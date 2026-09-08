"""Manual compatibility replay of three already published YOLO confirmations.

Run explicitly with `.venv/bin/python tests/monitor/replay_model_known_examples.py`.
This is not a collected pytest test or a new economic evaluation. It verifies
known 15m/1H/4H examples against the frozen live adapter and uses temporary
SQLite journals plus injected fake notification senders. No credentials,
network sends, future OHLC, outcomes, live state, or new dataset selection.
The 15m example is the second already-published example (one-bar delay).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from yoyo.monitor import MODEL_KIND, MODEL_PROTOCOL, MODEL_SHA256, SIGNAL_PROTOCOL
from yoyo.monitor.bark import BarkWorker
from yoyo.monitor.model_gate import ModelGate
from yoyo.monitor.policy import is_model_signal, is_tv_start
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker
from yoyo.monitor.yolo_detector import YoloDetector, prepare_windows


OUT = ROOT / "output/offline_tasks/spike_yolo_gate_20260908/known_positive_replay"
EXAMPLES = (
    ("ETH_15_1778263200", "ETH", "15m", 15,
     "data/imacd_yolo_confirmation_20260908_v1/ETH_decisions.csv",
     "data/imacd_yolo_confirmation_20260908_v1/ETH_proposals.csv.gz"),
    ("ETH_60_1778572800", "ETH", "1H", 60,
     "data/imacd_yolo_timeframes_20260908_v1/ETH_60_decisions.csv",
     "data/imacd_yolo_timeframes_20260908_v1/ETH_60_proposals.csv.gz"),
    ("ETC_240_1777910400", "ETC", "4H", 240,
     "data/imacd_yolo_expanded_20260908_v1/ETC_240_holdout_review_decisions.csv",
     "data/imacd_yolo_expanded_20260908_v1/ETC_240_holdout_review_proposals.csv.gz"),
)
DECISION_COLUMNS = ("event_id", "status", "side", "signal_i", "setup_start_i",
                    "setup_bars", "signal_open_at", "signal_available_at",
                    "signal_close", "confirmation_i", "confirmation_available_at",
                    "delay_bars", "model_detection_id", "core_start_i", "core_end_i")
PROPOSAL_COLUMNS = ("detection_id", "window_len", "input_pixel_sha256",
                    "window_end_i", "core_start_i", "core_end_i", "confidence")


def saved_row(path, key, value, columns):
    """Select existing metadata only; next-open/outcome columns are ignored."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        reader = csv.reader(handle)
        names = next(reader)
        indexes = {name: names.index(name) for name in columns}
        key_i = names.index(key)
        for row in reader:
            if row[key_i] == value:
                return {name: row[i] for name, i in indexes.items()}
    raise AssertionError("known metadata row missing: " + value)


def prefix_bars(path, close_ms, minutes):
    """Read only timestamp before parsing price fields; stop at decision close.

    Source rows are ascending ts-first CSV. OHLCV is parsed only for bars
    opening strictly before close_ms; final 15m bars must also be complete.
    Complete UTC groups reproduce the original native-timeframe aggregation.
    """
    lines = []
    with path.open() as handle:
        header = handle.readline()
        assert header.startswith("ts,")
        for line in handle:
            stamp = int(line.partition(",")[0])
            if stamp >= close_ms:
                break
            lines.append(line)
    raw = pd.read_csv(io.StringIO(header + "".join(lines)),
                      usecols=["ts", "open", "high", "low", "close", "volume"])
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop("ts"), unit="ms", utc=True))
    assert raw.index.is_unique and raw.index.is_monotonic_increasing
    assert raw.index.equals(raw.index.floor("15min"))
    assert raw.index.to_series().diff().iloc[1:].eq(pd.Timedelta(minutes=15)).all()
    assert int(raw.index[-1].timestamp()*1000) + 900_000 <= close_ms
    grouped = raw.groupby(raw.index.floor(f"{minutes}min"))
    bars = grouped.agg(dict(open="first", high="max", low="min", close="last", volume="sum"))
    bars = bars.loc[grouped.size().eq(minutes // 15)]
    assert int(bars.index[-1].timestamp()*1000) + minutes*60_000 == close_ms
    assert list(bars) == ["open", "high", "low", "close", "volume"]
    return bars


def as_monitor(bars):
    return [dict(t=int(t.timestamp()*1000), o=float(r.open), h=float(r.high),
                 l=float(r.low), c=float(r.close), v=float(r.volume))
            for t, r in bars.iterrows()]


class MockResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def run_one(spec, detector):
    event_id, base, timeframe, minutes, decisions, proposals = spec
    expected = saved_row(ROOT/decisions, "event_id", event_id, DECISION_COLUMNS)
    assert expected["status"] == "confirmed"
    proposal = saved_row(ROOT/proposals, "detection_id", expected["model_detection_id"], PROPOSAL_COLUMNS)
    arrow_open = int(pd.Timestamp(expected["signal_open_at"]).timestamp()*1000)
    arrow_close = int(pd.Timestamp(expected["signal_available_at"]).timestamp()*1000)
    confirm_close = int(pd.Timestamp(expected["confirmation_available_at"]).timestamp()*1000)
    step = minutes * 60_000
    symbol = base + "-USDT-SWAP"
    source = ROOT / f"data/kline_deep/okx_{base}_USDT_SWAP_15m_158499.csv"
    clock = [arrow_close + 1000]
    calls, traces = [], []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="spike-known-replay-") as temp:
        store = Store(Path(temp)/"isolated.sqlite3")
        for activate in (store.activate_notification_policy, store.activate_bark_policy):
            activate(arrow_close-1, protocol=MODEL_PROTOCOL)
        store.activate_timeframe_policy(timeframe, arrow_close-1, protocol=MODEL_PROTOCOL)
        gate = ModelGate(store, lambda: clock[0], threading.Event(), detector)
        frozen_arrow = None
        for close_ms in range(arrow_close, confirm_close + 1, step):
            clock[0] = close_ms + 1000
            # Recompute each actual endpoint from its own strictly bounded source.
            bars = prefix_bars(source, close_ms, minutes)
            result = analyze(as_monitor(bars), [], timeframe)
            chart = result["chart"]
            assert chart[-1]["t"] + step == close_ms
            assert all(r["t"] + step <= close_ms for r in chart)
            assert not any("return" in key or "outcome" in key or "next_open" in key
                           for row in chart[-19:] for key in row)
            if frozen_arrow is None:
                matches = [r for r in result["events"]
                           if r["kind"] == "tv_start" and r["bar_open_ms"] == arrow_open]
                assert len(matches) == 1, "known original arrow no longer matches monitor"
                frozen_arrow = dict(matches[0], symbol=symbol, protocol=SIGNAL_PROTOCOL,
                                    detected_at_ms=clock[0])
                assert is_tv_start(frozen_arrow)
                assert frozen_arrow["near_zero_bars"] == int(float(expected["setup_bars"]))
                assert frozen_arrow["side"] == ("long" if expected["side"] == "1" else "short")
                assert frozen_arrow["price"] == float(expected["signal_close"])
                assert gate.register(frozen_arrow)
            windows = prepare_windows(chart, timeframe, close_ms-step)
            if close_ms == confirm_close:
                original_window = next(w for w in windows if len(w.times) == int(proposal["window_len"]))
                assert original_window.input_pixel_sha256 == proposal["input_pixel_sha256"], "original input pixel drift"
            gate.process(symbol, timeframe, chart)
            candidates = store.list_candidates(symbol=symbol, timeframe=timeframe)
            assert len(candidates) == 1
            traces.append(dict(endpoint_close_ms=close_ms, source_rows=len(bars),
                               status=candidates[0]["model"]["status"],
                               input_pixel_hashes={str(len(w.times)): w.input_pixel_sha256 for w in windows}))
            if close_ms < confirm_close:
                assert candidates[0]["model"]["status"] == "pending", "confirmed earlier than saved known example"
                assert not store.list_events(kind=MODEL_KIND, protocol=MODEL_PROTOCOL)
        rows = store.list_events(kind=MODEL_KIND, protocol=MODEL_PROTOCOL)
        assert len(rows) == 1, "known real positive failed current live gate"
        event = rows[0]
        assert is_model_signal(event)
        assert event["bar_close_ms"] == confirm_close
        assert event["indicator"]["bar_close_ms"] == arrow_close
        assert event["model"]["wait_bars"] == int(float(expected["delay_bars"]))
        assert event["model"]["model_sha256"] == MODEL_SHA256
        assert event["model"]["input_pixel_sha256"] == proposal["input_pixel_sha256"]
        assert event["model"]["core_start_ms"] == int(bars.index[int(float(expected["core_start_i"]))].timestamp()*1000)
        assert event["model"]["core_end_ms"] == int(bars.index[int(float(expected["core_end_i"]))].timestamp()*1000)

        def telegram_sender(url, **kwargs):
            calls.append(dict(channel="telegram", api_method=url.rsplit("/", 1)[-1],
                              caption=kwargs.get("data", kwargs.get("json", {}))))
            result = {"message_id": 1}
            if "files" in kwargs:
                result["photo"] = [{"file_id": "mock-photo", "width": 1600, "height": 900}]
            return MockResponse({"ok": True, "result": result})

        def bark_sender(url, **kwargs):
            calls.append(dict(channel="bark", title=kwargs["json"]["title"],
                              body=kwargs["json"]["body"]))
            return MockResponse({"code": 200, "timestamp": 1})

        tg = TelegramWorker(store, creds=("synthetic-token", "synthetic-chat"), sender=telegram_sender)
        bark = BarkWorker(store, creds="synthetic-device", sender=bark_sender)
        assert tg.deliver_once(clock[0]) and bark.deliver_once(clock[0])
        assert not tg.deliver_once(clock[0]) and not bark.deliver_once(clock[0])
        assert [c["channel"] for c in calls] == ["telegram", "bark"]
        assert tg.status()["sent"] == bark.status()["sent"] == 1
        # Terminal replay cannot requeue a delivered event.
        gate.process(symbol, timeframe, chart)
        assert not tg.deliver_once(clock[0]) and not bark.deliver_once(clock[0])
        return dict(event_id=event_id, symbol=symbol, timeframe=timeframe,
                    source=str(source.relative_to(ROOT)), old_decision_source=decisions,
                    old_proposal_source=proposals, old_detection_id=proposal["detection_id"],
                    original_pixel_sha256=proposal["input_pixel_sha256"],
                    passed=True, elapsed_seconds=round(time.monotonic()-started, 3),
                    trace=traces, confirmation=event, mock_calls=calls,
                    no_network_sends=True, temporary_database_removed=True,
                    future_ohlcv_parsed=False)


def main():
    own = str(Path(__file__).resolve().relative_to(ROOT))
    assert not subprocess.check_output(["git", "status", "--porcelain", "--", own], cwd=ROOT, text=True).strip(), "commit replay runner first"
    OUT.mkdir(parents=True, exist_ok=True)
    detector = YoloDetector()
    detector.warmup()
    rows = []
    for spec in EXAMPLES:
        print(json.dumps(dict(event_id=spec[0], phase="replay")), flush=True)
        row = run_one(spec, detector)
        rows.append(row)
        (OUT/(spec[0]+".json")).write_text(json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False))
        print(json.dumps(dict(event_id=spec[0], passed=True, elapsed_seconds=row["elapsed_seconds"])), flush=True)
    receipt = dict(scope="three preselected already-published positives; engineering compatibility only, no returns",
                   generated_at=datetime.now(timezone.utc).isoformat(),
                   source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   detector_status=detector.status(), examples=rows, passed=True)
    (OUT/"receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False))
    print(str(OUT/"receipt.json"), flush=True)


if __name__ == "__main__":
    main()
