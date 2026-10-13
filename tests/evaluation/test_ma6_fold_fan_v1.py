"""Mechanics of exp-ma6-fold-fan-1h-20261013-v1."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import ma6_fold_fan_v1 as ff


def test_config_matches_the_builder_constants():
    cfg = json.loads(ff.CONFIG.read_text())
    assert cfg["variants"] == list(ff.VARIANTS) and cfg["minutes"] == ff.MINUTES == 60
    assert cfg["targets_r"] == [3, 5] and cfg["round_trip_cost"] == 0.002 and cfg["max_hold_bars"] == 192
    assert (ff.CONVERGE, ff.TURN_RATIO, ff.FAN_RATIO, ff.ANGLE_VOTES) == (1.0, 3.0, 1.3, 4)


def raw_1h(close: np.ndarray, start: str = "2026-01-01") -> pd.DataFrame:
    ts = pd.Timestamp(start, tz="UTC").value // 10**6 + 3_600_000 * np.arange(len(close))
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": o, "high": np.maximum(o, close) + 0.05,
                         "low": np.minimum(o, close) - 0.05, "close": close, "volume": 100.0})


def frame(close: np.ndarray) -> pd.DataFrame:
    return ff.features(ff.kb.bars_from(raw_1h(close), 60, 60))


def hits_near(f: pd.DataFrame, i: int) -> dict[str, list[tuple[int, int]]]:
    allowed = np.ones(len(f), bool)
    allowed[-1] = False
    return {v: [(j - i, s) for j, s in got if abs(j - i) <= 3] for v, got in ff.variant_hits(f, allowed).items()}


def test_flat_knot_then_sharp_drop_is_an_obtuse_short_fold_at_k3():
    close = 100 - 0.001 * np.arange(900)  # converged lines drifting down, then one bar drops hard
    close = np.r_[close, close[-1] - 1.0, close[-1] - 1.0]
    f = frame(close)
    i = 900
    assert hits_near(f, i)["primary"] == [(0, -1)]
    assert ff.angle_of(f, np.array([i]), np.array([-1]))[0] == "obtuse"


def test_rising_lines_reversed_by_one_bar_are_acute():
    close = 100 + 0.001 * np.arange(900)
    # a reversal must first carry EMA20 through the ordered bundle before it can fan out, so the
    # drop is larger than in the obtuse case (with 1.0 the width only grows 1.17x)
    close = np.r_[close, close[-1] - 3.0, close[-1] - 3.0]
    f = frame(close)
    i = 900
    assert (0, -1) in hits_near(f, i)["primary"]
    assert ff.angle_of(f, np.array([i]), np.array([-1]))[0] == "acute"


def test_a_line_that_keeps_its_slope_is_not_a_fold():
    close = 100 - 0.05 * np.arange(902)  # steady fall: every slope stays the same, no corner anywhere
    f = frame(close)
    assert not (f.all_down & (f.turn_ratio >= ff.TURN_RATIO)).iloc[800:].any()


def test_fields_at_k3_ignore_later_bars():
    close = np.r_[100 + 0.01 * np.sin(np.arange(900) * 2 * np.pi / 7), 99.0, 99.0, 99.0, 99.0]
    later = close.copy()
    later[901:] += 30
    cols = ["width_k2_atr", "fan_ratio", "turn_ratio", "all_down", "e20_front_short", "k2_up", "k2_down", "dc_atr"]
    pd.testing.assert_frame_equal(frame(close)[cols].iloc[:901], frame(later)[cols].iloc[:901])


@pytest.mark.skipif(not ff.EXAMPLE_OKX.exists(), reason="local OKX ETH 1H file not present")
def test_the_owner_example_fires_primary_obtuse_short_at_k3():
    got = ff.example_check()
    assert all(got["hits_within_2_bars"][v][0] == (0, -1) for v in ff.VARIANTS)  # later bars fall in the cooldown
    assert got["angle"] == "obtuse"
    assert got["width_k2_atr"] == pytest.approx(0.642, abs=1e-3) and got["fan_ratio"] == pytest.approx(1.5, abs=1e-3)


def test_scan_output_feeds_the_summary():
    cfg = json.loads(ff.CONFIG.read_text())
    rng = np.random.default_rng(1)
    pieces = [100 + 0.01 * np.sin(np.arange(800) * 2 * np.pi / 7)]
    for k in range(40):  # flat stretches, each ended by a one-bar jump that folds every MA
        base = pieces[-1][-1]
        pieces.append(base + 0.01 * np.sin(np.arange(150) * 2 * np.pi / 7) + rng.normal(0, 0.002, 150))
        pieces.append(pieces[-1][-1] + (-1) ** k * (1.0 + np.arange(30) * 0.05))
    raw = raw_1h(np.concatenate(pieces), "2024-09-01")
    bars = ff.kb.bars_from(raw, 60, 60)
    months = sorted(set(pd.to_datetime(bars.index, unit="ms").to_period("M").astype(str)))
    frames = ff.scan_frame("TESTUSDT", bars, set(months), {**cfg, "start": "2024-09-01T00:00:00Z"})
    trades = pd.concat(frames, ignore_index=True)
    assert set(trades.variant) >= {"primary"} and set(trades.target_r) == {3, 5}
    summary = ff.summarize(trades, cfg)
    assert {"events", "mean_net_r", "excess_r", "p_excess", "angle"} <= set(summary.columns)
    assert "all" in set(summary.angle)
