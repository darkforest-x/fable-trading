"""Causal port of the owner's "主下降趋势线 · 关键高点 V1" Pine v6 indicator.

Source: the Pine script the owner pasted on 2026-09-18, asking for a V2 that is
a *strategy* with searched take-profit / stop-loss. This module is only the
first half of that: it reproduces where the indicator fires. Nothing here knows
what a trade is.

The translation is line-for-line, including the order of operations inside one
confirmed bar, because that order is what keeps the result causal:

  1. test the line that already existed for a close-confirmed break, then for
     age expiry (expiry is deliberately NOT a break),
  2. push a pivot that has just finished its right-side confirmation,
  3. prune the candidate store,
  4. only on a bar that confirmed a pivot and did not just break, search for a
     replacement line.

Causality: a pivot at bar p is unknown until bar p + right_bars, so a line born
at bar b reads nothing after b; `_is_clean` walks x1..b; the break test reads
the current close and the current ATR. No future bar is read anywhere. The
right_bars lag is the drawing method's own latency and the reason the line
looks "already drawn" when scrolling history -- that is not look-ahead, but it
does mean a live chart sees each line 8 bars later than the picture suggests.

Two documented departures from the pasted script, both of which only ever
remove signals:

  * `ta.pivothigh` tie handling is not published by TradingView. A pivot here
    needs a strictly greater value than every bar on both sides. Bars that
    would qualify only under a non-strict comparison are counted and returned
    as `pivot_ties` rather than being silently kept or dropped.
  * Pine runs on a continuous chart. This replay is handed bar arrays that must
    already be gap-free on their own grid (the callers use the frozen
    `release_eth_prefix` validator), so there is no gap branch; a caller that
    splices discontinuous data would be certifying a line across bars nobody
    observed, and that is the caller's bug to not have.

A second, independent port of the same Pine exists in the working tree as
`yoyo/evaluation/trendline_break.py`, written by a parallel session for a
different question (gating SPIKE V9). The two were compared bar-for-bar; see
the report's parity section. They are deliberately not merged: that file was
uncommitted and under active edit while this study ran, and a study whose
builder can change underneath it has no reproducibility claim at all.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass(frozen=True)
class TrendlineParams:
    """The owner's Pine input defaults, unchanged.

    These are the *indicator*'s parameters and this study does not search them.
    CLAUDE.md rule 4 allows one variable per experiment and the variable here is
    the exit (take-profit / stop-loss), so every one of these stays frozen at
    what the owner shipped.
    """

    left_bars: int = 12          # 高点左侧比较 K 数
    right_bars: int = 8          # 高点右侧确认 K 数
    lookback: int = 600          # 搜索范围 / 主线最长年龄
    min_span: int = 48           # 两个锚点最小间隔
    min_drop_atr: float = 2.0    # 两个高点最小落差 (ATR)
    min_touches: int = 2         # 至少贴线的高点数
    wick_tol_atr: float = 0.35   # 建线时允许越线的幅度 (ATR)
    touch_tol_atr: float = 0.50  # 高点贴线容差 (ATR)
    break_buf_atr: float = 0.20  # 突破需超出趋势线的幅度 (ATR)
    break_bars: int = 2          # 突破需要连续收盘确认的 K 数
    max_candidates: int = 40     # Pine's `array.size(pivotXs) > 40` prune

    def __post_init__(self) -> None:
        # The Pine script's own runtime.error, kept so a bad config fails here
        # rather than producing a quietly emptier signal set.
        if self.lookback < self.min_span + self.right_bars:
            raise ValueError("lookback must be >= min_span + right_bars")
        if min(self.left_bars, self.right_bars, self.break_bars, self.min_touches) < 1:
            raise ValueError("bar counts must be positive")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TrendlineSignals:
    """Per-bar outputs aligned to the input bar clock."""

    break_event: np.ndarray   # bool: close-confirmed upward break on this bar
    born_event: np.ndarray    # bool: a new main line was drawn on this bar
    line_active: np.ndarray   # bool: a line was live at the end of this bar
    line_price: np.ndarray    # float: the line's value on this bar, else NaN
    lines: list               # one dict per drawn line, for auditing
    pivot_ties: int           # pivots admitted only under non-strict comparison


def pine_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    """`ta.atr(length)` exactly: RMA of true range, seeded with an SMA.

    Pine's `ta.tr(true)` uses high-low on the first bar, where close[1] is na.
    `ta.rma` emits its first value at index length-1 as the simple mean of the
    first `length` true ranges, then rolls with the 1/length weight. Earlier
    bars are NaN, which is why a caller must not trade the warmup.

    This repository already contains two ATR implementations that disagree at
    bar 14 because they seed the warmup differently
    (docs/consolidation/DUPLICATE_SEMANTICS.md section 4). This one is the
    Pine-seeded branch, stated here so the report can say which it is instead of
    leaving the reader to guess.
    """
    high, low, close = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(close)
    tr = np.empty(n)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    prev = close[:-1]
    tr[1:] = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - prev), np.abs(low[1:] - prev)))
    atr = np.full(n, np.nan)
    if n < length:
        return atr
    atr[length - 1] = tr[:length].mean()
    alpha = 1.0 / length
    running = atr[length - 1]
    for i in range(length, n):
        running = running + alpha * (tr[i] - running)
        atr[i] = running
    return atr


def _rolling_max(values: np.ndarray, width: int) -> np.ndarray:
    """out[k] == max(values[k:k+width]); NaN never wins a comparison."""
    filled = np.where(np.isfinite(values), values, -np.inf)
    return np.lib.stride_tricks.sliding_window_view(filled, width).max(axis=1)


def confirmed_pivot_highs(high: np.ndarray, left: int, right: int) -> tuple[np.ndarray, int]:
    """`ta.pivothigh(high, left, right)`, indexed by its confirmation bar.

    Returns `pivot_of`, where `pivot_of[i]` is the bar carrying the pivot that
    bar `i` confirms, or -1. Bar `i` confirms the pivot at `i - right`, which is
    exactly the `pivotHigh` / `bar_index - rightBars` pairing in the Pine.

    The second return value counts bars that are pivots only if ties are
    allowed. TradingView does not document the comparison, so the strict rule is
    used and the ambiguity is reported instead of hidden.
    """
    high = np.asarray(high, dtype=float)
    n = len(high)
    pivot_of = np.full(n, -1, dtype=np.int64)
    if n < left + right + 1:
        return pivot_of, 0
    centers = np.arange(left, n - right)
    left_max = _rolling_max(high, left)[centers - left]
    right_max = _rolling_max(high, right)[centers + 1]
    value = high[centers]
    strict = (value > left_max) & (value > right_max)
    loose = (value >= left_max) & (value >= right_max)
    pivot_of[centers[strict] + right] = centers[strict]
    return pivot_of, int((loose & ~strict).sum())


def detect(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray,
           tick: float, params: TrendlineParams | None = None) -> TrendlineSignals:
    """Replay the main descending line and its close-confirmed upward break.

    `atr` is supplied by the caller rather than computed here so that the study,
    the Pine script and the exit engine provably share one series.
    """
    p = params or TrendlineParams()
    high, low, close, atr = (np.asarray(x, dtype=float) for x in (high, low, close, atr))
    n = len(close)
    if not (len(high) == len(low) == len(atr) == n):
        raise ValueError("misaligned bar arrays")
    if not np.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")

    pivot_of, ties = confirmed_pivot_highs(high, p.left_bars, p.right_bars)

    break_event = np.zeros(n, dtype=bool)
    born_event = np.zeros(n, dtype=bool)
    line_active_out = np.zeros(n, dtype=bool)
    line_price = np.full(n, np.nan)

    # Scalar lists for the per-bar hot path; numpy is kept for the window
    # arithmetic. This is a speed choice, the arithmetic is unchanged.
    high_l = high.tolist()
    close_l = close.tolist()
    atr_l = atr.tolist()

    px: list[int] = []        # pivotXs
    py: list[float] = []      # pivotYs
    pa: list[float] = []      # pivotATRs
    lines: list[dict] = []

    active = False
    x1 = x2 = born_bar = -1
    y1 = y2 = slope = float("nan")
    above_count = 0

    for i in range(n):
        # ---- 1. the line that already exists ------------------------------
        broke_here = False
        if active:
            projected = y1 + slope * (i - x1)
            line_price[i] = projected
            if i > born_bar:
                a_now = atr_l[i]
                above = a_now == a_now and close_l[i] > projected + a_now * p.break_buf_atr
                above_count = above_count + 1 if above else 0
                if above_count >= p.break_bars:
                    broke_here = True
                    break_event[i] = True
                    active = False
                    above_count = 0
            if active and i - x1 > p.lookback:
                # 太旧的主线退出监测；不把"到期"当作突破。
                active = False
                above_count = 0

        # ---- 2. a pivot finishing its right-side confirmation --------------
        pivot_bar = int(pivot_of[i])
        new_pivot = pivot_bar >= 0 and atr_l[pivot_bar] == atr_l[pivot_bar]
        if new_pivot:
            px.append(pivot_bar)
            py.append(high_l[pivot_bar])
            pa.append(max(atr_l[pivot_bar], tick))

        # ---- 3. prune the candidate store ---------------------------------
        while px and (i - px[0] > p.lookback or len(px) > p.max_candidates):
            px.pop(0)
            py.pop(0)
            pa.pop(0)

        # ---- 4. look for a replacement line -------------------------------
        if not active and not broke_here and new_pivot and len(px) >= 2:
            k = len(px) - 1
            cx2, cy2, ca2 = px[k], py[k], pa[k]
            X = np.asarray(px, dtype=float)
            Y = np.asarray(py, dtype=float)
            A = np.asarray(pa, dtype=float)
            span = cx2 - X[:k]
            with np.errstate(divide="ignore", invalid="ignore"):
                slopes = (cy2 - Y[:k]) / span
            ok = (span >= p.min_span) & (Y[:k] > cy2) & (Y[:k] - cy2 >= ca2 * p.min_drop_atr)
            # 建线时收盘价必须仍在线下，不补画一个已经发生的突破。
            ok &= close_l[i] <= Y[:k] + slopes * (i - X[:k])
            # Pine's touch loop `for j = i to n-1` as one matrix: row j is the
            # candidate line, column m the pivot it is measured against, and the
            # mask keeps only pivots at or after the candidate's own anchor.
            expected = Y[:k, None] + slopes[:, None] * (X[None, :] - X[:k, None])
            hit = np.abs(Y[None, :] - expected) <= (A[None, :] * p.touch_tol_atr)
            hit &= np.arange(len(px))[None, :] >= np.arange(k)[:, None]
            touches = hit.sum(axis=1)
            # 跨度较大、额外贴线高点较多的候选优先。
            scores = span + np.maximum(touches - 2, 0) * p.min_span * 2.0
            viable = ok & (touches >= p.min_touches)

            best_score, best_x1, best_y1, best_touch = -1.0, -1, float("nan"), 0
            for j in np.flatnonzero(viable):
                score = float(scores[j])
                # Strict `>` keeps Pine's behaviour that the earliest candidate
                # wins a tie, and keeps the expensive clean scan off ties too.
                if score <= best_score:
                    continue
                cx1, cy1, cand_slope = px[j], py[j], float(slopes[j])
                idx = np.arange(cx1, i + 1)
                ceiling = cy1 + cand_slope * (idx - cx1)
                # Pine: math.max(nz(atrValue[offset], mintick), mintick)
                window_atr = np.maximum(np.nan_to_num(atr[idx], nan=tick), tick)
                tol = window_atr * p.wick_tol_atr
                if np.any(high[idx] > ceiling + tol):
                    continue
                best_score, best_x1, best_y1, best_touch = score, cx1, cy1, int(touches[j])

            if best_x1 >= 0:
                x1, y1, x2, y2 = best_x1, best_y1, cx2, cy2
                slope = (y2 - y1) / (x2 - x1)
                active = True
                born_bar = i
                above_count = 0
                born_event[i] = True
                line_price[i] = y1 + slope * (i - x1)
                lines.append({"born_i": i, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                              "slope": slope, "score": best_score, "touches": best_touch})
        line_active_out[i] = active

    return TrendlineSignals(break_event, born_event, line_active_out, line_price, lines, ties)
