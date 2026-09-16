"""Gate semantics, arm wiring, ledger audit and permutation null checks."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import bb_stoch_rsi_study as study
from yoyo.evaluation.ma_stoch_rsi_filter import zone_admission
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder


def frame_fixture(signal, gap_at=None):
    """Flat 100-ish bars: no target, no stop, so only signals end a trade."""
    index = pd.date_range("2026-01-01", periods=len(signal), freq="5min", tz="UTC")
    body = dict(open=100., high=100.6, low=99.4, close=100., signal=signal,
                target_upper=200., target_lower=1., rsi=50.)
    frame = pd.DataFrame(body, index=index)
    frame["_data_gap"] = False
    if gap_at is not None:
        frame.iloc[gap_at, frame.columns.get_loc("_data_gap")] = True
    return frame


def test_gate_only_removes_and_never_flips_a_side():
    signal = np.array([1, 1, -1, -1, 0, 1])
    rsi = np.array([29.9, 30., 70.1, 70., 5., np.nan])
    assert zone_admission(signal, rsi, 30., 70.).tolist() == [1, 0, -1, 0, 0, 0]


def test_rsi_restarts_at_a_data_gap_instead_of_joining_across_it():
    closes = 100 + np.sin(np.arange(60) / 3)
    frame = frame_fixture(np.zeros(60, dtype=int), gap_at=30)
    frame["close"] = closes
    got = study.rsi_series(frame, 14)
    np.testing.assert_allclose(got[:30], _rsi_wilder(pd.Series(closes[:30]), 14).to_numpy(), equal_nan=True)
    np.testing.assert_allclose(got[30:], _rsi_wilder(pd.Series(closes[30:]), 14).to_numpy(), equal_nan=True)
    # A restart must cost a fresh warmup; the joined series would have none.
    assert np.isnan(got[30:44]).all() and np.isfinite(got[44])


def test_entry_gate_alone_keeps_the_unfiltered_opposite_exit():
    raw = np.array([1, 0, 0, -1, 0, 0, 0, 0])
    gate = zone_admission(raw, np.array([29., 50., 50., 50., 50., 50., 50., 50.]), 30., 70.)
    assert gate.tolist() == [1, 0, 0, 0, 0, 0, 0, 0]
    frame = frame_fixture(raw)
    context = dict(valid=np.ones(len(raw), dtype=bool))
    entry_only = study.run_arm(frame, context, "tv", gate, frame)
    both = study.run_arm(frame, context, "tv", gate, frame.assign(signal=gate))
    assert len(entry_only) == len(both) == 1
    assert entry_only[0]["exit_reason"] == "opposite_signal_close" and entry_only[0]["exit_i"] == 3
    # Gating the exit as well is a different rule, and it shows up as a trade
    # that the filter is now holding through the opposite composite signal.
    assert both[0]["exit_reason"] == "boundary_censor" and both[0]["censored"]


def test_blocked_entry_is_not_replaced_by_a_later_bar_of_the_same_signal():
    raw = np.array([1, 1, 0, 0, 0, 0])
    gate = zone_admission(raw, np.array([50., 29., 50., 50., 50., 50.]), 30., 70.)
    frame = frame_fixture(raw)
    context = dict(valid=np.ones(len(raw), dtype=bool))
    rows = study.run_arm(frame, context, "tv", gate, frame)
    assert [r["signal_i"] for r in rows] == [1]
    assert [r["signal_i"] for r in study.run_arm(frame, context, "tv", raw, frame)] == [0]


def test_audit_recomputes_the_ledger_and_rejects_a_tampered_row():
    row = dict(side=1, entry_price=100., initial_risk=3., censored=False,
               gross_pnl=6., fees=0.206, net_pnl=5.794, net_r=5.794 / 3,
               fills=[dict(price=100., qty=1., phase="entry"), dict(price=106., qty=1., phase="full")])
    study.audit([row])
    with pytest.raises(AssertionError):
        study.audit([dict(row, net_pnl=6.)])


def test_event_level_permutation_is_one_sided_seeded_and_label_only(monkeypatch):
    outcomes = {0: 1., 1: 1., 2: -1., 3: -1.}
    def replay(_frame, i, path_mode=None, side_override=None):
        return dict(signal_i=i, entry_i=i, exit_i=i, side=1, net_r=outcomes[i], censored=False)
    monkeypatch.setattr(study, "replay_entry", replay)
    monkeypatch.setattr(study, "audit", lambda rows: None)
    frame = frame_fixture(np.array([1, 1, 1, 1]))
    frame["rsi"] = [29., 29., 50., 50.]
    gate = zone_admission(frame.signal.to_numpy(int), frame.rsi.to_numpy(float), 30., 70.)
    cfg = dict(primary_path="tv", seed=9162026, event_label_permutations=199)
    got, rows = study.event_level(frame, dict(valid=np.ones(4, dtype=bool)), gate, cfg)
    assert [r["gate_passed"] for r in rows] == [True, True, False, False]
    assert got["passed_mean_net_r"] == 1. and got["blocked_mean_net_r"] == -1.
    assert got["mean_difference"] == pytest.approx(2.)
    # Only 1 of the 6 label assignments is this extreme, so a 199-draw seeded
    # null cannot report significance from four events.
    assert 0 < got["p"] < .3 and got["permutations"] == 199
    assert study.event_level(frame, dict(valid=np.ones(4, dtype=bool)), gate, cfg)[0]["p"] == got["p"]
