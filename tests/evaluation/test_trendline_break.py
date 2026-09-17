"""Guards for the trendline replay: Pine parity, causality, mirror, gaps.

`_reference` is a literal transcription of the owner's Pine script, loops and
all. The shipped `trendline_breaks` replaces those loops with matrix algebra,
so the parity test is what makes that rewrite auditable rather than trusted.
"""
from __future__ import annotations

import numpy as np
import pytest

from yoyo.evaluation.trendline_break import TrendlineParams, trendline_breaks


def _reference(high, low, close, atr, gap, tick, direction=1, params=None):
    """Bar-by-bar transcription of the Pine source; slow on purpose."""
    p = params or TrendlineParams()
    u = np.asarray(high, float) if direction == 1 else -np.asarray(low, float)
    c = np.asarray(close, float) * direction
    atr = np.asarray(atr, float)
    n = len(c)
    finite = np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
    breaks = np.zeros(n, bool)
    born = np.zeros(n, bool)
    px, py, pa = [], [], []
    active, x1, y1, x2, y2, slope, born_bar, above_count = False, -1, np.nan, -1, np.nan, np.nan, -1, 0
    segment_start = 0
    for i in range(n):
        if gap[i] or not finite[i]:
            px, py, pa = [], [], []
            active, above_count = False, 0
            segment_start = i + 1 if not finite[i] else i
            continue
        broke = False
        if active:
            projected = y1 + slope * (i - x1)
            if i > born_bar:
                above = np.isfinite(atr[i]) and c[i] > projected + atr[i] * p.break_buf_atr
                above_count = above_count + 1 if above else 0
                if above_count >= p.break_bars:
                    broke, breaks[i], active, above_count = True, True, False, 0
            if active and i - x1 > p.lookback:
                active, above_count = False, 0
        new_pivot = False
        pivot_bar = i - p.right_bars
        if pivot_bar - p.left_bars >= segment_start and np.isfinite(atr[pivot_bar]):
            window_l = u[pivot_bar - p.left_bars:pivot_bar]
            window_r = u[pivot_bar + 1:i + 1]
            if finite[pivot_bar - p.left_bars:i + 1].all() and u[pivot_bar] > window_l.max() and u[pivot_bar] > window_r.max():
                px.append(pivot_bar)
                py.append(float(u[pivot_bar]))
                pa.append(max(float(atr[pivot_bar]), tick))
                new_pivot = True
        while px and (i - px[0] > p.lookback or len(px) > p.max_candidates):
            px.pop(0), py.pop(0), pa.pop(0)
        if not active and not broke and new_pivot and len(px) >= 2:
            k = len(px) - 1
            best_score, best_x1, best_y1 = -1.0, -1, np.nan
            for j in range(k):
                span = px[k] - px[j]
                if span < p.min_span or py[j] <= py[k] or py[j] - py[k] < pa[k] * p.min_drop_atr:
                    continue
                cand_slope = (py[k] - py[j]) / span
                if c[i] > py[j] + cand_slope * (i - px[j]):
                    continue
                touches = sum(1 for m in range(j, len(px))
                              if abs(py[m] - (py[j] + cand_slope * (px[m] - px[j]))) <= pa[m] * p.touch_tol_atr)
                if touches < p.min_touches:
                    continue
                score = span + max(touches - 2, 0) * p.min_span * 2.0
                if score <= best_score:
                    continue
                clean = True
                for offset in range(0, i - px[j] + 1):
                    ceiling = py[j] + cand_slope * (i - offset - px[j])
                    past = atr[i - offset] if np.isfinite(atr[i - offset]) else tick
                    if u[i - offset] > ceiling + past * p.wick_tol_atr:
                        clean = False
                        break
                if clean:
                    best_score, best_x1, best_y1 = score, px[j], py[j]
            if best_x1 >= 0:
                x1, y1, x2, y2 = best_x1, best_y1, px[k], py[k]
                slope = (y2 - y1) / (x2 - x1)
                active, born_bar, above_count, born[i] = True, i, 0, True
    return breaks, born


def _series(seed, n=4000, vol=0.004):
    rng = np.random.default_rng(seed)
    price = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    high = price * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = price * (1 - np.abs(rng.normal(0, vol / 2, n)))
    tr = high - low
    atr = np.concatenate([np.full(13, np.nan), np.convolve(tr, np.ones(14) / 14, "valid")])
    return high, low, price, atr


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("direction", [1, -1])
def test_matches_literal_pine_transcription(seed, direction):
    high, low, close, atr = _series(seed)
    gap = np.zeros(len(close), bool)
    fast = trendline_breaks(high, low, close, atr, gap, 0.001, direction=direction)
    breaks, born = _reference(high, low, close, atr, gap, 0.001, direction=direction)
    assert fast.break_event.tolist() == breaks.tolist()
    assert fast.born_event.tolist() == born.tolist()
    assert breaks.sum() > 0


@pytest.mark.parametrize("cut", [900, 1500, 2600])
def test_no_lookahead_when_history_is_truncated(cut):
    """Every output up to bar t must survive deleting every bar after t."""
    high, low, close, atr = _series(11)
    gap = np.zeros(len(close), bool)
    whole = trendline_breaks(high, low, close, atr, gap, 0.001)
    part = trendline_breaks(high[:cut], low[:cut], close[:cut], atr[:cut], gap[:cut], 0.001)
    assert whole.break_event[:cut].tolist() == part.break_event.tolist()
    assert whole.born_event[:cut].tolist() == part.born_event.tolist()
    assert whole.break_age[:cut].tolist() == part.break_age.tolist()


def test_mirror_is_the_sign_flip_of_the_original():
    high, low, close, atr = _series(5)
    gap = np.zeros(len(close), bool)
    down = trendline_breaks(high, low, close, atr, gap, 0.001, direction=-1)
    flipped = trendline_breaks(-low, -high, -close, atr, gap, 0.001, direction=1)
    assert down.break_event.tolist() == flipped.break_event.tolist()


def test_constructed_descending_line_breaks_on_the_second_close_above():
    """A hand-built descending line: two anchors, then a two-bar close break."""
    n = 320
    close = np.full(n, 95.0)
    high = np.full(n, 95.5)
    low = np.full(n, 94.5)
    for bar, peak in ((40, 112.0), (140, 106.0)):
        high[bar] = peak
        close[bar] = peak - 0.5
    # The line through those two highs projects to about 99.4 at bar 250, so a
    # 95 close stays under it the whole way; lift two closes clearly through.
    close[250] = 103.0
    high[250] = 103.2
    close[251] = 103.4
    high[251] = 103.6
    atr = np.full(n, 0.5)
    gap = np.zeros(n, bool)
    result = trendline_breaks(high, low, close, atr, gap, 0.01)
    assert result.born_event.any(), "two separated pivot highs must build a line"
    assert result.break_event[251], "two consecutive closes through the line confirm the break"
    assert not result.break_event[250], "one close through the line is not yet a break"
    assert result.break_age[251] == 0 and result.break_age[255] == 4


def test_gap_clears_line_and_break_age():
    high, low, close, atr = _series(9)
    gap = np.zeros(len(close), bool)
    base = trendline_breaks(high, low, close, atr, gap, 0.001)
    first = int(np.flatnonzero(base.break_event)[0])
    gap[first + 3] = True
    gapped = trendline_breaks(high, low, close, atr, gap, 0.001, params=TrendlineParams())
    assert base.break_age[first + 3] == 3, "without a gap the age keeps counting"
    assert gapped.break_age[first + 3] == -1, "a gap erases the earlier break"
    assert not gapped.line_active[first + 3]


def test_rejects_impossible_parameters():
    with pytest.raises(ValueError):
        TrendlineParams(lookback=40, min_span=48)
    high, low, close, atr = _series(3, n=200)
    with pytest.raises(ValueError):
        trendline_breaks(high, low, close, atr, np.zeros(200, bool), 0.0)
    with pytest.raises(ValueError):
        trendline_breaks(high, low, close, atr, np.zeros(200, bool), 0.001, direction=0)


@pytest.mark.parametrize("seed", range(6))
def test_agrees_with_the_independent_sibling_port(seed):
    """The two ports of one Pine script must not drift apart silently."""
    sibling = pytest.importorskip("yoyo.evaluation.trendline_v2_signals")
    rng = np.random.default_rng(seed)
    n = 6000
    price = 100 * np.exp(np.cumsum(rng.normal(0, .004, n)))
    high = price * (1 + np.abs(rng.normal(0, .002, n)))
    low = price * (1 - np.abs(rng.normal(0, .002, n)))
    atr = sibling.pine_atr(high, low, price, 14)
    theirs = sibling.detect(high, low, price, atr, .001, sibling.TrendlineParams())
    mine = trendline_breaks(high, low, price, atr, np.zeros(n, bool), .001, direction=1)
    assert theirs.break_event.tolist() == mine.break_event.tolist()
    assert theirs.born_event.tolist() == mine.born_event.tolist()
    assert theirs.line_active.tolist() == mine.line_active.tolist()
    assert theirs.break_event.sum() > 0
