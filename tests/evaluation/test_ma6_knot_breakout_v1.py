"""Mechanics of exp-ma6-knot-breakout-20261011-v1."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import ma6_knot_breakout_v1 as kb


def test_config_matches_the_builder_constants():
    cfg = json.loads(kb.CONFIG.read_text())
    assert cfg["variants"] == list(kb.VARIANTS)
    assert cfg["targets_r"] == [3, 5] and cfg["round_trip_cost"] == 0.002 and cfg["max_hold_bars"] == 192
    assert cfg["timeframes_5m_source"] == [5, 15, 30, 60] and set(kb.HTF) == {1, 5, 15, 30, 60}
    assert kb.KNOTS["tight"] == (0.7, 10) and kb.KNOTS["v131"] == (3.0, 2)


def raw_1m(close: np.ndarray, start: str = "2026-01-01") -> pd.DataFrame:
    ts = pd.Timestamp(start, tz="UTC").value // 10**6 + 60_000 * np.arange(len(close))
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": o, "high": np.maximum(o, close) + 0.05,
                         "low": np.minimum(o, close) - 0.05, "close": close, "volume": 100.0})


def test_bars_from_keeps_complete_buckets_only():
    raw = raw_1m(np.linspace(100, 101, 15)).drop(index=7)  # minute 7 missing -> bucket 5..9 incomplete
    bars = kb.bars_from(raw, 1, 5)
    assert len(bars) == 3 and np.isnan(bars.close.iloc[1]) and bars.close.iloc[0] == raw.close.iloc[4]


def test_higher_timeframe_bar_is_read_only_after_it_closes():
    index = np.arange(10) * 60_000
    htf = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": np.arange(1.0, 3.0)}, index=[0, 300_000])
    closes = htf.index.to_numpy() + 300_000
    k = np.searchsorted(closes, index + 60_000, side="right") - 1
    assert list(k) == [-1, -1, -1, -1, 0, 0, 0, 0, 0, 1]  # 5m bar 0..4 usable from the 1m bar opening at 4
    up, down = kb.htf_stack(index, 1, htf, 5)
    assert not up[:4].any() and not down[:4].any()  # nothing before the first 5m close


def test_break_is_the_first_close_beyond_all_six_and_features_are_causal():
    close = 100 + 0.05 * np.sin(np.arange(900) * 2 * np.pi / 7)  # flat, tangled MAs
    close[899] = 100.2  # last close above the rope
    close = np.r_[close, close[-1] - np.arange(1, 6) * 0.5]  # then five falling closes
    bars = kb.bars_from(raw_1m(close), 1, 1)
    f = kb.features(bars, np.zeros(len(bars), bool), np.zeros(len(bars), bool))
    first = np.flatnonzero(f.break_short.to_numpy())
    tail = first[first >= 900]
    assert list(tail) == [900]  # only the first close below the rope breaks; the next four do not
    # changing a later bar never changes the fields at the break bar
    later = close.copy()
    later[902:] += 50
    g = kb.features(kb.bars_from(raw_1m(later), 1, 1), np.zeros(len(bars), bool), np.zeros(len(bars), bool))
    cols = ["past_width", "past_flips", "rv", "close_pos", "rope_hi", "rope_lo", "fake_for_short", "lh_run"]
    pd.testing.assert_frame_equal(f[cols].iloc[:901], g[cols].iloc[:901])


def test_confirm_waits_for_ema20_outermost_and_drops_reentries():
    n = 10
    f = pd.DataFrame({"below": [True] * n, "above": [False] * n, "e20": [5.0] * n, "rope_lo": [4.0] * n,
                      "rope_hi": [6.0] * n, "seg": 1000, "atr": 1.0, "prev_atr": 1.0, "frozen_share": 0.0})
    f.loc[3, "e20"] = 4.0  # EMA20 becomes the lowest MA three bars after a break at 0
    assert list(kb.confirm_bars(f, np.array([0]), -1)) == [3]
    g = f.copy()
    g.loc[2, "below"] = False  # a close back inside the rope before confirmation cancels it
    assert list(kb.confirm_bars(g, np.array([0]), -1)) == [-1]
    h = f.copy()
    h.loc[3, "e20"] = 5.0
    h.loc[5, "e20"] = 4.0  # outside the 4-bar window
    assert list(kb.confirm_bars(h, np.array([0]), -1)) == [-1]


@pytest.mark.skipif(not kb.EXAMPLE_OKX.exists(), reason="local OKX ETH 1m file not present")
def test_the_owner_example_fires_every_variant():
    got = kb.example_check()
    hits = got["short_hits_within_4_bars"]
    assert hits["confirm"] == ["23:05"]
    assert all(hits[v] == ["23:02"] for v in kb.VARIANTS if v != "confirm")
    assert got["past_flips"] == 10 and got["past_width"] == pytest.approx(0.672, abs=1e-3)


def test_scan_output_feeds_the_shared_summary():
    cfg = json.loads(kb.CONFIG.read_text())
    rng = np.random.default_rng(1)
    pieces = []
    for k in range(40):  # tangled flat stretches, each ended by a sharp drop or rise
        flat = 100 + 0.05 * np.sin(np.arange(60) * 2 * np.pi / 7) + rng.normal(0, 0.01, 60)
        pieces.append(flat)
        pieces.append(flat[-1] + (-1) ** k * np.arange(1, 21) * 0.3)
        pieces.append(np.full(40, flat[-1] + (-1) ** k * 6.0) + rng.normal(0, 0.02, 40))
    close = np.concatenate([100 + 0.05 * np.sin(np.arange(800) * 2 * np.pi / 7), *pieces])
    raw = raw_1m(close, "2024-12-25")
    bars = kb.bars_from(raw, 1, 1)
    months = sorted(set(pd.to_datetime(bars.index, unit="ms").to_period("M").astype(str)))
    frames = kb.scan_frame("TESTUSDT", 1, bars, kb.bars_from(raw, 1, 5), set(months), {**cfg, "start": "2024-12-25T00:00:00Z"})
    trades = pd.concat(frames, ignore_index=True)
    assert set(trades.variant) >= {"primary", "knot_v131"} and set(trades.target_r) == {3, 5}
    summary = kb.sb.summarize(trades, cfg)
    assert {"events", "mean_net_r", "excess_r", "p_excess"} <= set(summary.columns) and len(summary)
