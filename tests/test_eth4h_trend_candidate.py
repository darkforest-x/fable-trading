"""Counterexamples for the candidate's single-variable safety mechanisms."""
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.eth4h_trend_candidate import CHAIN, CandidatePolicy, CandidateReplay
from yoyo.layers.l3_backtest.pine_allin_eth4h import Policy, Replay


def frame(n=8):
    f = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
                      "open": 100., "high": 100., "low": 100., "close": 100., "atr": 1.,
                      "v7_long": False, "v7_short": False, "cross_long": False, "cross_short": False,
                      "entry_allowed": True, "osc": 1., "hk_dayofweek": 0, "hk_hour": 0})
    f.loc[0, "v7_long"] = True
    return f


def test_half_percent_risk_is_budgeted_from_known_signal_distance():
    f = frame()
    f.loc[2, "low"] = 96.
    t = CandidateReplay(f, CHAIN[1]).run(0, len(f))["trades"].iloc[0]
    assert t.qty * 3 == pytest.approx(500 * .005)
    assert t.leverage <= 1
    assert t.net_pnl < -2.5  # Commission is additional, never hidden in risk.


def test_only_the_declared_dimension_changes_at_each_stage():
    from dataclasses import asdict
    expected = ["risk_pct", "ratchet_only", "isolate_stops", "fresh_cooldown", "close_only"]
    for before, after, field in zip(CHAIN, CHAIN[1:], expected):
        a, b = asdict(before), asdict(after)
        assert [k for k in a if k != "name" and a[k] != b[k]] == [field]


def test_same_side_signal_cannot_loosen_protection():
    f = frame()
    f.loc[2, "v7_long"] = True
    f.loc[2, ["open", "high", "low", "close"]] = [100., 100., 98., 98.]
    f.loc[3, ["open", "high", "low", "close"]] = [98., 98., 96., 98.]
    old = CandidateReplay(f, CHAIN[1]).run(0, len(f))
    new = CandidateReplay(f, CHAIN[2]).run(0, len(f))
    assert old["trades"].empty
    assert new["trades"].iloc[0].exit_price == 97.


def test_stop_isolation_does_not_replace_old_position_stop():
    r = CandidateReplay(frame(), CHAIN[3])
    assert r.reset_stop(97., 103., {"direction": 1}, -1) == 97.
    assert r.entry_stop_state(97., 103.) == 103.


def test_fresh_cooldown_sees_new_profit_immediately():
    r = CandidateReplay(frame(), CHAIN[4])
    assert r.cooling_state(7, False) is True


def test_opposite_signal_closes_without_reversing():
    f = frame()
    f.loc[2, "v7_short"] = True
    out = CandidateReplay(f, CHAIN[-1]).run(0, len(f))
    assert len(out["trades"]) == 1 and out["open_position"] is None


def test_initial_stage_matches_parent_immediate_one_x():
    f = frame()
    f.loc[2, "low"] = 96.
    a = Replay(f, Policy("S0_immediate_1x", fee=.001, immediate_stop=True, unit_leverage=True)).run(0, len(f))
    b = CandidateReplay(f, CHAIN[0]).run(0, len(f))
    pd.testing.assert_frame_equal(a["trades"], b["trades"])
    pd.testing.assert_frame_equal(a["equity"], b["equity"])


def test_boundary_exit_reconciles_cash_and_charges_both_fills():
    from yoyo.evaluation.eth4h_trend_candidate import BoundaryReplay
    f = frame()
    out = BoundaryReplay(f, CHAIN[-1]).run(0, len(f))
    t = out["trades"].iloc[0]
    assert t.exit_reason == "research_end"
    assert t.fees == pytest.approx(t.qty * 100 * .002)
    assert out["final_equity"] == pytest.approx(500 + t.net_pnl)
    assert out["open_position"] is None
    assert out["equity"].iloc[-1].position == 0
