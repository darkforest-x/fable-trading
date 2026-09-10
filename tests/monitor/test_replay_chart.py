"""Frozen replay chart contexts never borrow live OHLC or trade outcomes."""
from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.replay_chart import ReplayChartUnavailable, load_replay_chart
from yoyo.monitor.store import Store


def event(*, event_id="a" * 24, source="replay", venue="okx", symbol="PEPE-USDT-SWAP", minutes=60, open_ms=160 * 3_600_000):
    return {"id": event_id, "protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": source,
            "confirmation": "raw", "venue": venue, "symbol": symbol, "timeframe": "1H",
            "timeframe_min": minutes, "direction": "long", "side": "long", "bar_open_ms": open_ms,
            "bar_close_ms": open_ms + minutes * 60_000, "source_sha256": "frozen-v1-source"}


def write_frozen(root, *, symbol="PEPE-USDT-SWAP", mutate_after=None):
    index = pd.date_range("1970-01-01", periods=700, freq="30min", tz="UTC")
    close = pd.Series(range(700), dtype=float) / 100 + 10
    if mutate_after is not None:
        close.iloc[mutate_after] += 50
    frame = pd.DataFrame({"time": index, "open": close - .02, "high": close + .05,
                          "low": close - .06, "close": close, "volume": 100.})
    path = root / "okx" / f"{symbol}_30m.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    return path


def test_exact_frozen_ohlc_context_has_provenance_and_causal_mas(tmp_path):
    path = write_frozen(tmp_path)
    source = event()
    first = load_replay_chart(source, root=tmp_path)
    target = first["candles"][first["context_before_bars"]]
    assert target["t"] == source["bar_open_ms"]
    assert first["source"] == "replay" and first["future_context"] == "historical_review_only_not_model_input"
    assert first["context_before_bars"] == first["context_after_bars"] == 90
    assert first["provenance"]["ohlc_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first["provenance"]["source_sha256"] == "frozen-v1-source"
    assert all(key in target for key in ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120"))
    write_frozen(tmp_path, mutate_after=600)
    second = load_replay_chart(source, root=tmp_path)
    target_after = second["candles"][second["context_before_bars"]]
    assert (target["sma20"], target["ema20"], target["sma120"], target["ema120"]) == (
        target_after["sma20"], target_after["ema20"], target_after["sma120"], target_after["ema120"])


def test_missing_frozen_source_is_explicit_not_a_live_chart(tmp_path):
    with pytest.raises(ReplayChartUnavailable, match="frozen_ohlc_missing"):
        load_replay_chart(event(), root=tmp_path)


def test_gate_uses_its_frozen_direct_timeframe_file(tmp_path):
    source = event(venue="gate", symbol="PEPE_USDT", minutes=60)
    index = pd.date_range("1970-01-01", periods=400, freq="1h", tz="UTC")
    close = pd.Series(range(400), dtype=float) / 100 + 10
    frame = pd.DataFrame({"time": index, "open": close - .02, "high": close + .05,
                          "low": close - .06, "close": close, "volume": 100.})
    direct = tmp_path.parent / "normalized_gate_direct"
    direct.mkdir()
    path = direct / "PEPE_USDT_60m.csv.gz"
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    chart = load_replay_chart(source, root=tmp_path)
    assert chart["candles"][chart["context_before_bars"]]["t"] == source["bar_open_ms"]
    assert chart["provenance"]["ohlc_timeframe_min"] == 60


def test_replay_chart_api_uses_stored_event_identity_not_client_provenance(tmp_path, monkeypatch):
    from yoyo.monitor import server
    app = server.create_app(runtime=tmp_path, start_monitor=False)
    source = event()
    assert app.state.monitor.store.upsert_event(source)
    source["id"] = app.state.monitor.store.event_id(source)
    received = []
    monkeypatch.setattr(server, "load_replay_chart", lambda row: received.append(row) or {"source": "replay", "candles": []})
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/replay/chart")
    assert endpoint(source["id"]) == {"source": "replay", "candles": []}
    assert len(received) == 1
    assert {key: received[0][key] for key in ("id", "source", "venue", "symbol", "bar_open_ms")} == {
        key: source[key] for key in ("id", "source", "venue", "symbol", "bar_open_ms")}
