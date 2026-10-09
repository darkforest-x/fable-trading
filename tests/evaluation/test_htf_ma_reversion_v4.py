"""Mechanics of exp-htf-ma-reversion-20261009-v4 (confirmed entries, stop under the swing extreme)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from yoyo.evaluation import htf_ma_reversion_v4 as v4

TRIG = np.full(12, 100.0)


def test_configs_share_the_rule_and_differ_only_in_data():
    m = json.loads((v4.EXP / "config_market.json").read_text())
    e = json.loads((v4.EXP / "config_eth.json").read_text())
    differ = {k for k in m.keys() | e.keys() if m.get(k) != e.get(k)}
    assert differ <= {"run", "data", "series_dir", "symbols", "start", "select_before", "end", "warmup_start",
                      "selection", "output_dir"}
    assert m["confirm"] == ["reclaim", "prev_bar"] and m["stop_buffer"] == 0.001 and m["reentry"] == [0, 3]


def test_confirmations_reclaim_and_previous_bar():
    close = np.array([99.0, 100.5, 99.5, 101.0])
    high = np.array([99.5, 101.0, 100.8, 101.5])
    low = np.array([98.0, 99.0, 99.0, 100.0])
    trig = np.array([100.0, 100.0, np.nan, 100.0])
    assert v4.confirmations("reclaim", 1, close, high, low, trig).tolist() == [False, True, False, True]
    assert v4.confirmations("prev_bar", 1, close, high, low, np.full(4, 100.0)).tolist() == [False, True, False, True]
    assert v4.confirmations("prev_bar", -1, close, high, low, np.full(4, 100.0)).tolist() == [False, False, False, False]


def toy():
    o = np.array([105, 101, 99.5, 98.0, 98.5, 99.2, 101.0, 99.0, 97.0, 99.5, 101.0, 103])
    h = np.array([106, 101.5, 99.9, 98.8, 99.5, 101.5, 101.5, 99.5, 99.8, 101.2, 103.5, 108])
    l = np.array([104, 99.8, 98.0, 97.0, 98.2, 99.0, 98.0, 96.5, 96.0, 99.0, 100.5, 102.5])
    c = np.array([105, 100.2, 98.2, 98.4, 99.3, 101.2, 99.2, 97.1, 99.0, 101.0, 103.2, 107.5])
    return (o, h, l, c)


def test_waits_past_a_fill_beyond_the_stop_then_stops_under_the_low_and_reenters_on_a_new_low():
    bars = toy()
    exc = [(1, 11)]
    conf = np.flatnonzero(v4.confirmations("reclaim", 1, bars[3], bars[1], bars[2], TRIG))
    assert conf.tolist() == [0, 1, 5, 9, 10, 11]  # bar 0 precedes the touch and is never used
    once = v4.chain_confirmed(exc, conf, 1, bars, 1, 0.001, 2.0, 2, 0.0, 0)
    # bar 1 confirms but bar 2 opens at 99.5 below the 99.80 low -> wait; bar 5 confirms -> fill 101.0 at bar 6
    assert [(g[1], g[2], g[4], g[5]["kind"]) for g in once] == [(6, 101.0, 97.0, "stop")]
    assert once[0][5]["exit_i"] == 7 and once[0][5]["gross"] == pytest.approx(97.0 * 0.999 / 101.0 - 1)
    again = v4.chain_confirmed(exc, conf, 1, bars, 1, 0.001, 2.0, 2, 0.0, 3)
    # after the stop on bar 7 the next confirmation is bar 9; the extreme is the new 96.0 low
    assert [(g[1], g[3], g[4], g[5]["kind"]) for g in again] == [(6, 0, 97.0, "stop"), (10, 1, 96.0, "timeout")]
    assert again[1][5]["gross"] == pytest.approx(107.5 / 101.0 - 1)


def test_no_confirmation_after_the_excursion_ends():
    bars = toy()
    conf = np.array([1, 5, 9])
    assert v4.chain_confirmed([(1, 4)], conf, 1, bars, 1, 0.001, 2.0, 2, 0.0, 3) == []


def test_daily_trend_reads_the_previous_completed_day():
    import pandas as pd
    closes = [10.0, 12.0, 8.0, 9.0]  # SMA2: nan, 11, 10, 8.5 -> states 0, +1, -1, +1
    ts = np.arange(4 * 288) * 300_000
    px = np.repeat(closes, 288)
    raw = pd.DataFrame({"ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1.0})
    probe = np.array([0, 288, 2 * 288, 3 * 288 + 5]) * 300_000  # first bar of days 0..3 (+ a later bar)
    assert v4.daily_trend(raw, probe, 2).tolist() == [0, 0, 1, -1]


def test_v5_configs_add_only_the_trend_block():
    v5 = v4.EXP.parent / "exp-htf-ma-reversion-20261010-v5"
    for name in ("config_market.json", "config_eth.json"):
        a = json.loads((v4.EXP / name).read_text())
        b = json.loads((v5 / name).read_text())
        changed = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
        assert changed <= {"experiment_id", "owner_request", "question", "prior_evidence", "trend", "single_variable",
                           "primary_read", "selection"}
        assert b["trend"]["sma_days"] == 50 and b["trend"]["arms"] == ["with", "against"]
