"""Mechanics of exp-owner-squeeze-breakout-20261008-v1."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import squeeze_breakout_v1 as sb


def test_config_matches_the_builder_constants():
    cfg = json.loads(sb.CONFIG.read_text())
    assert cfg["targets_r"] == [3, 5] and cfg["round_trip_cost"] == 0.002
    assert cfg["max_hold_bars"] == sb.MAX_HOLD and cfg["rule"]["cooldown_bars"] == sb.COOLDOWN
    assert cfg["ablation"]["variants"] == [f"no_{c}" for c in ("bb", "dense", "engulf", "volume", "big_body")]
    assert set(sb.CONDITIONS) == {"bb", "dense", "big_body", "engulf", "volume"}


def run_one(o, h, l, c, side, stop, target_r=3.0, cap=5):
    got = sb.simulate_many(np.array(o, float), np.array(h, float), np.array(l, float), np.array(c, float),
                           np.array([0]), np.array([side]), np.array([stop], float), target_r, cap, 0.002)
    return {k: v[0] for k, v in got.items()}


def test_long_target_stop_gap_and_same_bar():
    # entry 100, stop 98 -> risk 2, 3R target 106
    hit = run_one([100, 101, 103], [101, 104, 107], [99, 100, 102], [100.5, 103, 106.5], 1, 98)
    assert hit["kind"] == "target" and hit["bars"] == 3
    assert hit["gross_r"] == pytest.approx(3.0) and hit["net_r"] == pytest.approx(3.0 - 0.002 / 0.02)
    stop = run_one([100, 99], [101, 99.5], [99, 97], [99.5, 97.5], 1, 98)
    assert stop["kind"] == "stop" and stop["gross_r"] == pytest.approx(-1.0)
    gap = run_one([100, 96], [101, 97], [99, 95], [99.5, 96.5], 1, 98)
    assert gap["kind"] == "stop" and gap["gross_r"] == pytest.approx(-2.0)  # filled at the 96 open
    both = run_one([100, 100], [101, 107], [99, 97], [99.5, 100], 1, 98)
    assert both["kind"] == "stop"  # stop first when both print in one bar
    gap_up = run_one([100, 108], [101, 109], [99, 97], [99.5, 108], 1, 98)
    assert gap_up["kind"] == "target" and gap_up["gross_r"] == pytest.approx(4.0)  # open beyond the target


def test_timeout_missing_bar_and_short_mirror():
    flat = run_one([100] * 5, [100.5] * 5, [99.5] * 5, [100, 100.2, 100.4, 100.1, 101], 1, 98, cap=5)
    assert flat["kind"] == "timeout" and flat["gross_r"] == pytest.approx(0.5)
    assert flat["never_closed_below"]
    gap = run_one([100, np.nan, 100], [100.5, np.nan, 107], [99.5, np.nan, 99], [100, np.nan, 106], 1, 98)
    assert not gap["valid"] and np.isnan(gap["net_r"])
    short = run_one([100, 99, 97], [101, 99.5, 97.5], [99, 96, 93], [99.5, 97, 93.5], -1, 102)
    assert short["kind"] == "target" and short["gross_r"] == pytest.approx(3.0)
    late = sb.simulate_many(np.array([100.0, 100]), np.array([101.0, 101]), np.array([99.0, 99]),
                            np.array([100.0, 100]), np.array([0]), np.array([1]), np.array([98.0]), 3.0, 5, 0.002)
    assert not late["valid"][0]  # the path runs past the data before any exit


def test_bb_run_must_end_inside_the_twelve_bars_before():
    comp = pd.Series([False] * 30)
    comp[[5, 6, 7]] = True  # run ends at bar 7
    got = sb.recent_run(comp)
    assert not got[7] and got[8] and got[17] and not got[18]  # ends at t-1 .. t-10 only
    broken = pd.Series([False] * 30)
    broken[[5, 6, 8, 9]] = True
    assert not sb.recent_run(broken).any()


def frame(**over):
    base = dict(open=100.0, high=103.2, low=99.9, close=103.0, atr=1.0, prev_atr=1.0, rope_hi=101.0, rope_lo=100.0, frozen_share=0.0,
                past_width=1.0, past_flips=3.0, bb_recent=True, rv=2.0, prior_high=102.0, prior_low=99.0,
                prev_open=101.0, prev_close=100.5, seg=900)
    base.update(over)
    return pd.DataFrame([base])


def test_conditions_long_and_short():
    base, conds = sb.conditions(frame(), 1)
    assert base[0] and all(v[0] for v in conds.values())
    base, conds = sb.conditions(frame(close=101.5, high=101.6), 1)
    assert not base[0]  # below the 12-bar high
    _, conds = sb.conditions(frame(prev_open=99.5, prev_close=103.5), 1)
    assert not conds["engulf"][0]
    _, conds = sb.conditions(frame(close=100.8, high=101.0, prior_high=100.5, rope_hi=100.6), 1)
    assert not conds["big_body"][0]
    short = frame(open=100.3, close=97.0, high=100.4, low=96.8, rope_hi=100.5, rope_lo=99.0, prior_low=98.0,
                  prev_open=99.5, prev_close=100.2)
    base, conds = sb.conditions(short, -1)
    assert base[0] and conds["engulf"][0] and conds["big_body"][0]
    assert not sb.conditions(frame(seg=500), 1)[0][0]


def test_cooldown_spans_both_sides():
    assert sb.with_cooldown([(10, 1), (15, -1), (22, 1), (23, -1)]) == [(10, 1), (23, -1)]


def test_features_are_causal():
    rng = np.random.default_rng(1)
    n = 1500
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    bars = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "close": c, "volume": rng.lognormal(3, 0.4, n)})
    bars["high"] = bars[["open", "close"]].max(axis=1) * 1.001
    bars["low"] = bars[["open", "close"]].min(axis=1) * 0.999
    full = sb.features(bars)
    cut = sb.features(bars.iloc[:1200])
    cols = ["atr", "rope_hi", "rope_lo", "past_width", "past_flips", "bb_recent", "rv", "prior_high"]
    pd.testing.assert_frame_equal(full.iloc[:1200][cols], cut[cols])


def test_controls_exclude_frozen_bars_and_other_regimes():
    atr_pct = np.array([0.01, 0.0, 0.004, 0.006, 0.02, 0.021, 0.01])
    pool = np.arange(7)
    assert sb.control_candidates(pool, atr_pct, 0).tolist() == [3, 4, 6]  # 0.5x-2x of 1%, not itself
    cfg = json.loads(sb.CONFIG.read_text())
    assert tuple(cfg["controls"]["vol_band"]) == sb.VOL_BAND


def test_frozen_price_history_blocks_an_event():
    assert not sb.conditions(frame(frozen_share=0.4), 1)[0][0]
    assert sb.conditions(frame(frozen_share=0.0), 1)[0][0]
    n = 1000
    c = np.r_[np.full(800, 0.6), 0.6 * np.exp(np.cumsum(np.full(200, 0.002)))]
    bars = pd.DataFrame({"open": c, "close": c, "high": c, "low": c, "volume": 1.0})
    f = sb.features(bars)
    assert f.frozen_share.iloc[799] == 1.0 and f.frozen_share.iloc[n - 1] == 1.0  # open == close bars still have zero range
