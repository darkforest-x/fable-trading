"""SPIKE V12.8 monitor adapter: source identity, causality and event contract."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import spike_lines as legacy
from yoyo.monitor import v128_lines as adapter


def _h1_frame(n: int = 80) -> pd.DataFrame:
    close = np.arange(n, dtype=float) + 100.0
    open_ = close - 0.25
    return pd.DataFrame({
        "open": open_, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.full(n, 10.0),
    }, index=pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"))


def test_h1_gate_only_uses_completed_fresh_sma_at_chart_open_and_is_causal():
    higher = _h1_frame()
    chart_index = pd.DatetimeIndex([
        pd.Timestamp("2026-01-03 11:45", tz="UTC"),
        pd.Timestamp("2026-01-03 12:00", tz="UTC"),
        pd.Timestamp("2026-01-03 12:15", tz="UTC"),
        pd.Timestamp("2026-01-03 13:00", tz="UTC"),
    ])

    whole = adapter._h1_sma60_at_chart_open(higher, chart_index)
    prefix = adapter._h1_sma60_at_chart_open(higher.iloc[:73], chart_index[:3])

    assert np.isnan(whole[0])  # the H1 closing at 12:00 is not visible to the 11:45 bar
    assert whole[1] == pytest.approx(129.5)  # prior H1 close is available at 12:00
    assert whole[2] == pytest.approx(129.5)  # still fresh inside that H1 bucket
    assert np.array_equal(whole[:3], prefix, equal_nan=True)
    assert whole[3] == pytest.approx(130.5)


def test_h1_gap_resets_the_60_consecutive_bar_requirement():
    higher = _h1_frame().drop(pd.Timestamp("2026-01-03 00:00", tz="UTC"))
    chart_index = pd.date_range("2026-01-03 01:00", periods=3, freq="1h", tz="UTC")
    got = adapter._h1_sma60_at_chart_open(higher, chart_index)
    assert np.isnan(got).all()


def test_15m_facts_apply_the_confirmed_h1_gate_before_opening_the_v9_box(monkeypatch):
    index = pd.DatetimeIndex([
        pd.Timestamp("2026-01-03 12:00", tz="UTC"),
        pd.Timestamp("2026-01-03 12:15", tz="UTC"),
        pd.Timestamp("2026-01-03 12:30", tz="UTC"),
        pd.Timestamp("2026-01-03 12:45", tz="UTC"),
    ])
    close = np.array([160.0, 120.0, 140.0, 150.0])
    frame = pd.DataFrame({
        "open": close - 1, "high": close + 1, "low": close - 2,
        "close": close, "volume": 10.0, "atr": 1.0,
    }, index=index)
    base = {
        "frame": frame, "gap": np.zeros(4, dtype=bool), "side": np.array([1, -1, -1, 1]),
        "v9": np.ones(4, dtype=bool), "v9_long": np.array([True, False, False, True]),
        "parent_high": np.full(4, np.nan), "parent_low": np.full(4, np.nan),
        "ref_long_exit": np.zeros(4, dtype=bool), "box": {"box_entry": np.zeros(4, dtype=int)},
        "ready": np.ones(4, dtype=bool), "can_run": np.ones(4, dtype=bool),
        "long_alive": np.ones(4, dtype=bool),
    }
    monkeypatch.setattr(adapter.recent, "_generic_facts", lambda *args: base)
    captured: dict[str, object] = {}

    def exits(*args, state, **kwargs):
        captured["signal_side"] = np.asarray(kwargs["signal_side"]).copy()
        state["box_entry"] = np.arange(4)
        return np.zeros(4, dtype=bool)

    monkeypatch.setattr(adapter, "reference_long_exits", exits)
    higher = _h1_frame()
    got = adapter._facts(frame, "15m", tick=0.01, asset="TEST", higher=higher)

    assert got["v9"].tolist() == [True, True, False, True]
    assert captured["signal_side"].tolist() == [1, -1, 0, 1]
    assert got["box"]["box_entry"].tolist() == [0, 1, 2, 3]


def test_analyze_retains_break_and_joint_fields_under_the_new_identity(monkeypatch):
    index = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    close = np.arange(200, dtype=float) + 100.0
    frame = pd.DataFrame({
        "open": close - 0.1, "high": close + 1, "low": close - 1,
        "close": close, "volume": 10.0, "atr": 1.0,
    }, index=index)
    facts = {
        "frame": frame, "gap": np.zeros(200, dtype=bool), "side": np.zeros(200, dtype=int),
        "v9": np.zeros(200, dtype=bool), "v9_long": np.zeros(200, dtype=bool),
        "parent_high": np.full(200, np.nan), "parent_low": np.full(200, np.nan),
        "ref_long_exit": np.zeros(200, dtype=bool), "box": {"box_entry": np.full(200, -1)},
        "ready": np.ones(200, dtype=bool), "can_run": np.ones(200, dtype=bool),
        "long_alive": np.ones(200, dtype=bool),
    }
    line = {"i": 180, "break_i": 180, "born_i": 60, "ax": 10, "ap": 180.0,
            "bx": 30, "bp": 160.0, "cx": 50, "cp": 170.0, "source": "soft",
            "source_id": 1, "kind": "local_touch", "uid": 9, "score": 0.25}
    joint = {**line, "joint_i": 190, "box_entry_i": 150, "pair_source": "chart",
             "source": "chart", "order": "break-first", "structure_source": "soft"}
    monkeypatch.setattr(adapter, "_facts", lambda *args, **kwargs: facts)
    monkeypatch.setattr(adapter, "line_events", lambda *args, **kwargs: SimpleNamespace(
        events=[line], winner_events=[line], trace={"pivot_tie_status": "unverified_tradingview_native_semantics"}))

    def pair_events(*args, chart_breaks, **kwargs):
        assert chart_breaks[0]["structure_source"] == "soft"
        return [joint]

    monkeypatch.setattr(adapter, "pair_events", pair_events)
    monkeypatch.setattr(legacy, "positions", lambda *args, **kwargs: {
        190: {"status": "active", "basis": "old", "performance_version": "old", "current_r": 0.4}
    })

    got = adapter.analyze(frame, "1H", tick=0.01, asset="TEST")

    assert got["ready"] is True and len(got["breaks"]) == len(got["joints"]) == 1
    broken = got["breaks"][0]
    assert broken["protocol"] == adapter.PROTOCOL
    assert broken["source_version"] == "spike_burst_v12_8.pine"
    assert broken["source_sha256"] == adapter.SOURCE_SHA256
    assert broken["native_parity"] is False
    assert broken["pivot_tie_status"] == "unverified_tradingview_native_semantics"
    assert broken["line_kind"] == "local_touch" and broken["track"] == "削尖影线"
    paired = got["joints"][0]
    assert paired["source"] == "chart" and paired["pair_order"] == "break-first"
    assert paired["line_source"] == "soft" and paired["line_kind"] == "local_touch"
    assert paired["performance"]["basis"] == adapter.BASIS
    assert paired["performance"]["rsi_exit"] == "not_present_in_pine_v12_8"


def test_event_id_uses_versioned_namespace_and_rsi_override_is_refused():
    assert adapter.event_id("joint", "BTC-USDT-SWAP", "15m", 7) != legacy.event_id("joint", "BTC-USDT-SWAP", "15m", 7)
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    with pytest.raises(ValueError, match="does not define an RSI"):
        adapter.analyze(empty, "15m", tick=0.01, asset="TEST", rsi_exit_enabled=True)
