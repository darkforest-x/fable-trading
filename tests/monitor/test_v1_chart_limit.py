"""Display truncation cannot change frozen V1 replay, state, or raw events."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pandas as pd
import pytest

from yoyo.evaluation.spike_burst_replay import features
from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.service import Monitor
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store


def candles(count=720, step=3_600_000):
    return [{"t": index * step, "o": 100. + index / 1000, "h": 101. + index / 1000,
             "l": 99. + index / 1000, "c": 100. + index / 1000, "v": 10.}
            for index in range(count)]


def test_chart_limit_only_skips_discarded_display_rows():
    full = analyze(candles(), [], "1H", tick=.01)
    compact = analyze(candles(), [], "1H", tick=.01, chart_limit=240)
    assert compact["state"] == full["state"]
    assert compact["events"] == full["events"]
    assert compact["chart"] == full["chart"][-240:]
    source = candles()
    frame = pd.DataFrame(
        [{"open": row["o"], "high": row["h"], "low": row["l"], "close": row["c"], "volume": row["v"]}
         for row in source],
        index=pd.to_datetime([row["t"] for row in source], unit="ms", utc=True),
    )
    expected = features(frame).iloc[-240:]
    for chart_row, (_, feature_row) in zip(compact["chart"], expected.iterrows()):
        assert chart_row["md"] == pytest.approx(float(feature_row.md))
        assert chart_row["sb"] == pytest.approx(float(feature_row.sb))


def test_chart_md_sb_are_prefix_causal_without_changing_events_or_state():
    source = candles()
    prefix = analyze(source[:500], [], "1H", tick=.01, chart_limit=240)
    altered = deepcopy(source)
    for row in altered[500:]:
        row.update(o=900., h=1000., l=1., c=999., v=99_999.)
    full = analyze(altered, [], "1H", tick=.01)
    assert full["chart"][:500] == analyze(source[:500], [], "1H", tick=.01)["chart"]
    cutoff = source[500]["t"]
    assert [event for event in full["events"] if event["bar_open_ms"] < cutoff] == prefix["events"]


def test_live_chart_repairs_missing_sb_from_full_checkpoint_without_writes(tmp_path, monkeypatch):
    """A selected legacy chart may gain display fields without replaying history."""
    store = Store(tmp_path / "monitor.sqlite")
    seed = candles()
    expected = analyze(seed, [], "1H", tick=.01, chart_limit=240)
    legacy_chart = [{key: value for key, value in row.items() if key != "sb"} for row in expected["chart"]]
    event = {"protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": "live", "confirmation": "raw",
             "symbol": "PEPE-USDT-SWAP", "timeframe": "1H", "side": "long", "direction": "long",
             "bar_open_ms": seed[-1]["t"], "bar_close_ms": seed[-1]["t"] + 3_600_000,
             "detected_at_ms": seed[-1]["t"] + 3_600_000}
    assert store.upsert_event(event)
    event["id"] = store.event_id(event)
    market = {"symbol": event["symbol"], "timeframe": "1H", "phase": "ready", "ready": True,
              "tick_size": ".01", "chart": legacy_chart, "events": [event]}
    store.upsert_market(market)
    store.save_candle_checkpoint(event["symbol"], "1H", seed)
    with store.connect() as db:
        before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
                  for table in ("events", "meta", "outbox", "bark_outbox", "model_candidates")}
    for name in ("upsert_market", "set_meta", "upsert_event", "register_candidate", "save_candle_checkpoint"):
        monkeypatch.setattr(store, name, lambda *args, _name=name, **kwargs: pytest.fail(f"display read wrote {_name}"))

    result = Monitor(store).chart(event["symbol"], "1H")

    assert result["candles"] == expected["chart"]
    assert result["events"] == [event]
    assert result["state"]["chart_features_complete"] is True
    with store.connect() as db:
        after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
                 for table in before}
    assert after == before


def test_live_chart_keeps_legacy_candles_when_full_checkpoint_is_unavailable(tmp_path):
    store = Store(tmp_path / "monitor.sqlite")
    legacy = [{"t": 0, "o": 1., "h": 2., "l": .5, "c": 1.5, "v": 1., "md": 0.}]
    store.upsert_market({"symbol": "PEPE-USDT-SWAP", "timeframe": "1H", "tick_size": ".01",
                         "chart": legacy, "events": [], "phase": "ready"})

    result = Monitor(store).chart("PEPE-USDT-SWAP", "1H")

    assert result["candles"] == legacy
    assert result["state"]["chart_features_complete"] is False
    assert result["state"]["error"] == "cached_chart_features_unavailable"


def test_frozen_full_history_output_hash_survives_display_materialization_change():
    """Pin the complete 720-bar display contract, including causal MD/SB values."""
    result = analyze(candles(), [], "1H", tick=.01, chart_limit=240)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == (
        "35212be0ccb01378b1d3587a9cf1b47656ed165a7b06dbe1f4783c6e98062c13"
    )


def test_adding_sb_is_the_only_change_to_the_prior_frozen_display_contract():
    """Old chart bytes return exactly when the new per-row display field is removed."""
    result = analyze(candles(), [], "1H", tick=.01, chart_limit=240)
    prior = dict(result)
    prior["chart"] = [{key: value for key, value in row.items() if key != "sb"} for row in result["chart"]]
    payload = json.dumps(prior, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == (
        "2ecb21f572e9403dd00fd8733626e9621285e63a978d37e891a2b1da2d135bec"
    )
