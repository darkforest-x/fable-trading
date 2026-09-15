"""Synthetic checks for frozen sampling, serial reversal and cash summaries."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import eth_bb_stoch_study as study


def test_control_draws_are_outcome_blind_distinct_and_matched():
    context = dict(month=np.array(["2026-01"]*20 + ["2026-02"]*10),
                   bucket=np.array([0, 1]*15), valid=np.ones(30, dtype=bool))
    cfg = dict(seed=9162026, controls_per_trade=5)
    draws = study.draw_controls(context, 2, 1, cfg)
    assert len(set(draws)) == 5 and 2 not in draws
    assert all(j < 20 and j % 2 == 0 for j in draws)
    # No outcomes enter this API; repeated runs and other caller state cannot
    # silently replace a losing or boundary-censored random sample.
    assert study.draw_controls(context, 2, 1, cfg) == draws


def test_control_censor_is_disclosed_without_redraw():
    cfg = dict(controls_per_trade=2, seed=9162026)
    actual = [dict(signal_i=5, month="2026-01", net_r=1., censored=False)]
    controls = [dict(actual_signal_i=5, net_r=0., censored=False),
                dict(actual_signal_i=5, net_r=-1., censored=True)]
    got = study.compare(actual, controls, cfg)
    assert got["paired_n"] == 0 and got["unpaired_n"] == 1
    assert got["controls_drawn"] == 2 and got["controls_censored"] == 1
    assert got["all_draw_boundary_mark_excess"] == pytest.approx(1.5)


def test_serial_ignores_held_signals_and_reopens_at_full_reverse(monkeypatch):
    index = pd.date_range("2026-01-01", periods=8, freq="5min", tz="UTC")
    frame = pd.DataFrame(dict(signal=[1, 0, 1, 0, -1, 0, 0, 0]), index=index)
    calls = []
    def replay(_, i, path_mode):
        calls.append(i)
        return dict(signal_i=i, entry_i=i+1, exit_i=4 if i == 0 else 7,
                    censored=i == 4, side=int(frame.signal.iloc[i]))
    monkeypatch.setattr(study, "replay_entry", replay)
    rows = study.run_serial(frame, dict(valid=np.ones(8, dtype=bool)), "tv")
    assert calls == [0, 4] and len(rows) == 2
    assert rows[1]["entry_i"] == 5


def test_exact_month_signflip_does_not_claim_many_independent_trades():
    pairs = [dict(month="2026-01", excess_net_r=1.),
             dict(month="2026-01", excess_net_r=1.),
             dict(month="2026-02", excess_net_r=1.)]
    got = study.signflip(pairs, dict(seed=9162026))
    assert got["months"] == 2 and got["null_draws"] == 4
    assert got["p"] == .25
