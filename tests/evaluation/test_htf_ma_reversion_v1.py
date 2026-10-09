"""Mechanics of exp-htf-ma-reversion-20261009-v1."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import htf_ma_reversion_v1 as hr


def test_config_matches_the_builder_constants():
    cfg = json.loads(hr.CONFIG.read_text())
    assert cfg["targets_r"] == [2, 3, 5] and cfg["round_trip_cost"] == 0.002
    assert cfg["max_hold_bars"] == 576 and set(cfg["stops"]) == set(hr.STOPS)
    assert {k: v["htf_minutes"] for k, v in cfg["charts"].items()} == {"5": 15, "15": 60}
    assert cfg["vix_fix"]["params"] == {"pd": hr.VIX_PD, "bbl": hr.VIX_BBL, "mult": hr.VIX_MULT,
                                        "lb": hr.VIX_LB, "ph": hr.VIX_PH}


def test_chart_bar_reads_the_previous_completed_htf_bar():
    step = 900_000
    htf_index = np.array([0, step, 2 * step])
    got = hr.on_chart(np.array([0, step + 300_000, 2 * step, 3 * step]), htf_index, np.array([1.0, 2.0, 3.0]), step)
    assert np.isnan(got[0]) and got[1] == 1.0 and got[2] == 2.0 and got[3] == 3.0


def test_sma_line_waits_for_contiguous_bars_and_ema_reseeds_after_a_gap():
    n = 1400
    close = np.linspace(100, 130, n)
    frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    frame.iloc[100] = np.nan
    line, atr = hr.htf_line(frame, "sma60")
    assert np.isnan(line[100:160]).all() and np.isfinite(line[160])
    assert line[160] == pytest.approx(close[101:161].mean())
    ema, _ = hr.htf_line(frame, "ema120")
    assert np.isnan(ema[:101 + 1199]).all() and np.isfinite(ema[101 + 1199])
    seeded = pd.Series(close[101:]).ewm(span=120, adjust=False).mean().to_numpy()
    assert ema[n - 1] == pytest.approx(seeded[-1])
    assert np.isfinite(atr[101 + 13]) and np.isnan(atr[101 + 12])


def one(o, h, l, c, entry, side, stop, target, intrabar=True, cap=4):
    got = hr.simulate(*(np.array(x, float) for x in (o, h, l, c)), np.array([0]), np.array([entry], float),
                      np.array([side]), np.array([stop], float), np.array([target], float),
                      np.array([intrabar]), cap, 0.002)
    return {k: v[0] for k, v in got.items()}


def test_entry_bar_counts_the_stop_by_low_and_the_target_only_by_close():
    # long limit filled at 100 inside a bar that opened at 103; stop 98, target 104
    assert one([103], [105], [97], [99], 100, 1, 98, 104, cap=1)["kind"] == "stop"
    assert one([103], [105], [99.5], [104.5], 100, 1, 98, 104, cap=1)["kind"] == "target"
    early_high = one([103, 101], [110, 102], [99.5, 100], [101, 101.5], 100, 1, 98, 104, cap=2)
    assert early_high["kind"] == "timeout" and early_high["gross"] == pytest.approx(0.015)
    # an open entry (control) counts the bar's high normally
    assert one([100], [105], [99.5], [101], 100, 1, 98, 104, intrabar=False, cap=1)["kind"] == "target"


def test_later_bars_gap_fill_at_open_and_stop_first():
    gap = one([100, 96], [101, 97], [99.5, 95], [100.5, 96.5], 100, 1, 98, 104)
    assert gap["kind"] == "stop" and gap["gross"] == pytest.approx(-0.04)
    both = one([100, 100], [101, 105], [99.5, 97], [100.5, 100], 100, 1, 98, 104)
    assert both["kind"] == "stop" and both["net"] == pytest.approx(-0.02 - 0.002)
    short = one([100, 100], [100.5, 101], [99, 95], [99.5, 96], 100, -1, 102, 96)
    assert short["kind"] == "target" and short["gross"] == pytest.approx(0.04)
    missing = one([100, np.nan], [101, np.nan], [99.5, np.nan], [100.5, np.nan], 100, 1, 98, 104)
    assert not missing["valid"]


def test_excursion_ends_at_a_close_back_within_half_the_distance():
    touch = np.array([0, 1, 1, 0, 1, 0, 0, 1], bool)
    back = np.array([0, 0, 0, 0, 1, 0, 0, 0], bool)
    assert hr.excursions(touch, back, np.arange(8), 1) == [(1, 4), (7, 7)]
    # 15m chart over 5m path bars: chart bar 1 ends the first excursion, the next starts in chart bar 2
    touch = np.array([0, 1, 0, 1, 1, 0, 0, 1, 0], bool)
    assert hr.excursions(touch, np.array([0, 1, 0], bool), np.arange(9) // 3, 3) == [(1, 1), (7, 2)]


def test_entry_fills_at_the_open_when_already_beyond_and_one_trade_per_book():
    o, h, l = np.array([95.0]), np.array([96.0]), np.array([94.0])
    assert hr.find_entry(0, 0, 1, np.array([97.0]), o, h, l, np.array([0]), 1, None) == (0, 95.0)
    assert hr.find_entry(0, 0, -1, np.array([94.5]), o, h, l, np.array([0]), 1, None) == (0, 95.0)
    keep = hr.take_sequential(np.array([0, 5, 9, 12]), np.array([8, 10, 11, 20]), np.array([True, True, True, True]))
    assert keep.tolist() == [True, False, True, True]


def test_vix_fix_is_monotone_in_price_and_bisection_finds_the_turn():
    args = (100.0, 19 * 2.0, 19 * 4.0 + 19 * 1.0, 6.0)  # prior wvf mean 2, sd 1, max 6
    prices = np.linspace(99, 90, 50)
    on = hr.vix_on(prices, 1, *args)
    assert not on[0] and on[-1] and (np.diff(on.astype(int)) >= 0).all()
    turn = hr.bisect_on(1, 99.0, 90.0, args)
    assert hr.vix_on(turn, 1, *args) and not hr.vix_on(turn + 1e-6, 1, *args)
    assert not hr.vix_on(np.nan, 1, *args)


V2 = hr.EXP.parent / "exp-htf-ma-reversion-20261009-v2" / "config.json"


def test_v2_config_only_changes_stops_and_adds_reentry():
    v1, v2 = json.loads(hr.CONFIG.read_text()), json.loads(V2.read_text())
    changed = {k for k in v1.keys() | v2.keys() if v1.get(k) != v2.get(k)}
    assert changed <= {"experiment_id", "owner_request", "question", "prior_evidence", "stops", "reentry",
                       "reentry_rule", "output_dir", "selection", "data_note"}
    assert v2["reentry"] == [0, 3] and list(v2["stops"]) == ["pct0.5", "pct0.6", "pct0.8", "pct1.0", "pct1.2"]


def test_stop_risk_parses_percent_rules_and_the_line_third():
    fill, line = np.array([100.0]), np.array([110.0])
    assert hr.stop_risk("pct0.6", 1, fill, line)[0] == pytest.approx(0.6)
    assert hr.stop_risk("pct3", 1, fill, line)[0] == pytest.approx(3.0)
    assert hr.stop_risk("line3", 1, fill, line)[0] == pytest.approx(10 / 3)
    assert hr.stop_risk("line3", -1, np.array([120.0]), line)[0] == pytest.approx(10 / 3)


def toy_path():
    # long trigger at 100 on every bar; one excursion over bars 1..9 (ends at chart bar 9)
    o = np.array([105, 101, 100.5, 99.8, 100.2, 100.4, 101, 103, 106, 108.0])
    h = o + 0.5
    l = np.array([104, 99.9, 99.0, 99.5, 99.9, 100.1, 100.5, 102, 105, 107.0])
    c = o + 0.2
    trig = np.full(10, 100.0)
    touch = l <= trig
    back = np.zeros(10, bool)
    back[9] = True
    return (o, h, l, c), trig, touch, back


def test_reentry_after_a_stop_stays_inside_the_excursion_and_arm_zero_matches_v1():
    bars, trig, touch, back = toy_path()
    exc = hr.excursions(touch, back, np.arange(10), 1)
    assert exc == [(1, 9)]
    first = [hr.find_entry(p0, j, 1, trig, bars[0], bars[1], bars[2], np.arange(10), 1, None) for p0, j in exc]
    line = np.full(10, 110.0)
    args = (exc, first, np.flatnonzero(touch), 1, trig, bars, np.arange(10), 1, None, "pct0.6", line, 5.0, 6, 0.0)
    once = hr.chain_with_reentry(*args, 0)
    # fill 100 on bar 1, stop 99.4 hit by bar 2's low 99.0
    assert [(r[1], r[2], r[4]["kind"]) for r in once] == [(1, 100.0, "stop")]
    vec = hr.simulate(*bars, np.array([1]), np.array([100.0]), np.array([1]), np.array([99.4]), np.array([103.0]),
                      np.array([True]), 6, 0.0)
    assert once[0][4]["net"] == pytest.approx(vec["net"][0]) and once[0][4]["exit_i"] == vec["exit_i"][0]
    again = hr.chain_with_reentry(*args, 3)
    # re-entry at the first touch after the exit bar (bar 3, fills at its 99.8 open), then the target 102.794
    assert [(r[1], r[3], r[4]["kind"]) for r in again] == [(1, 0, "stop"), (3, 1, "target")]
    assert again[1][2] == pytest.approx(99.8)
