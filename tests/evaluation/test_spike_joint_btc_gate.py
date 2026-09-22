"""Causality and admission contracts for the joint BTC gate."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_joint_btc_gate import btc_reference, gate_decision, gate_mask, serial_candidates


def _five_minute_bars(hours: int = 245) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=hours * 12, freq="5min", tz="UTC")
    close = 100. + np.linspace(0, 20, len(index))
    return pd.DataFrame({"open": close, "high": close + .1, "low": close - .1, "close": close, "volume": 1.}, index=index)


def test_h1_uses_the_close_at_the_decision_boundary_and_same_equals_h1():
    reference = btc_reference(_five_minute_bars())
    signal_close = reference[60].index[240] + pd.Timedelta(hours=1)
    same = gate_decision(reference, signal_close, "1h", "same_sma120")
    h1 = gate_decision(reference, signal_close, "1h", "h1_sma120")
    assert same["btc_source_close_time"] == signal_close
    for key in ("gate_pass", "gate_known", "gate_reason", "btc_source_bar_open", "btc_source_close", "btc_sma"):
        assert same[key] == h1[key]


def test_warmup_gap_and_stale_btc_are_explicit_unknowns():
    bars = _five_minute_bars()
    warm = btc_reference(bars)
    assert gate_decision(warm, warm[15].index[50] + pd.Timedelta(minutes=15), "15m", "same_sma60")["gate_reason"] == "btc_warmup_or_gap"
    # One missing five-minute bar makes its complete 1h bucket unavailable and
    # prevents a 120-bar SMA from silently bridging that hole.
    broken = bars.drop(bars.index[200 * 12 + 3])
    ref = btc_reference(broken)
    decision = gate_decision(ref, ref[60].index[200] + pd.Timedelta(hours=1), "1h", "h1_sma120")
    assert not decision["gate_known"] and decision["gate_reason"] == "btc_warmup_or_gap"
    end = ref[60].index[-1] + pd.Timedelta(hours=1)
    stale = gate_decision(ref, end + pd.Timedelta(hours=1, minutes=1), "1h", "h1_sma120")
    assert not stale["gate_known"] and stale["gate_reason"] == "btc_stale"


def test_future_btc_changes_cannot_revise_a_prior_permission():
    bars = _five_minute_bars()
    before = btc_reference(bars)
    close = before[15].index[180] + pd.Timedelta(minutes=15)
    expected = gate_decision(before, close, "15m", "same_sma120")
    changed = bars.copy()
    changed.loc[changed.index >= close, "close"] += 10_000.
    actual = gate_decision(btc_reference(changed), close, "15m", "same_sma120")
    for key in ("gate_pass", "gate_known", "gate_reason", "btc_source_close", "btc_sma"):
        assert actual[key] == expected[key]


def test_batch_permission_matches_scalar_asof_decisions():
    reference = btc_reference(_five_minute_bars())
    opens = reference[15].index[40:220]
    closes = opens + pd.Timedelta(minutes=15)
    for gate in ("same_sma60", "same_sma120", "h1_sma60", "h1_sma120"):
        actual = gate_mask(reference, closes, "15m", gate)
        expected = np.array([gate_decision(reference, stamp, "15m", gate)["gate_pass"] for stamp in closes])
        np.testing.assert_array_equal(actual, expected)


def test_gate_rejection_releases_occupancy_but_never_changes_an_exit():
    events = [{"signal_i": 10, "trade_key": "a"}, {"signal_i": 11, "trade_key": "b"}]
    calls = []
    def evaluate(exit_rule, i):
        calls.append((exit_rule, i))
        return "closed", {"exit_i": i + 5, "net_r": float(i), "censored": False}
    pass_all = {10: {"gate_pass": True, "gate_known": True, "gate_reason": "above_sma"},
                11: {"gate_pass": True, "gate_known": True, "gate_reason": "above_sma"}}
    filtered = {10: {"gate_pass": False, "gate_known": True, "gate_reason": "below_sma"},
                11: pass_all[11]}
    baseline, _ = serial_candidates(events, "price", "none", pass_all, evaluate, 30)
    gated, statuses = serial_candidates(events, "price", "same_sma120", filtered, evaluate, 30)
    assert baseline[0]["net_r"] == 10.  # Same event's exit is the evaluator's frozen outcome.
    assert [row["trade_key"] for row in gated] == ["b"]
    assert statuses[0]["status"] == "rejected_btc_gate" and calls.count(("price", 11)) == 1
