"""SPIKE V10 (second definition): long-only V9 entries within 7 bars of a trendline break.

Source: the owner withdrew the first V10 on 2026-09-18 (「v10去掉 重新做」) and
restated it: 「用spike v9 和我这个趋势线指标 只有当我的趋势线指标出现 突破信号
然后v9也有信号才行 支持先突破 然后最多7根k出现v9信号 我们v10才做多 不需要做空信号」.

The rule, read literally:
  * the owner's "主下降趋势线 · 关键高点 V1" indicator records an upward close
    break of its descending line over pivot highs at bar b;
  * a V9 long confirmation at bar s is admitted only if 0 <= s - b <= 7, i.e.
    on the break bar itself or at most seven closed bars after it ("支持先突破"
    admits break-first; a V9 signal that arrives before the break is refused,
    because admitting it would mean entering on a bar the break had not yet
    confirmed);
  * V9 short confirmations never open a position ("不需要做空信号").

What does not change: V9's own entry bundle, every exit, and the raw opposite
confirmation. A raw V9 short confirmation still ends an open long exactly as it
does in V9 -- dropping it would change the exit, which is a second variable.

Break age is measured on the chart's own bar clock and is cleared by a data gap
(`trendline_break.trendline_breaks`): a break on the far side of an unobserved
interval is not "within 7 bars" of anything.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.trendline_break import TrendlineParams, trendline_breaks

VERSION = "spike-v10-long-break7-20260918-v2"
PARENT_VERSION = "spike-v9-entry-bundle-20260915-v1"
WITHDRAWN_VERSION = "spike-v10-trendline-break-20260918-v1"

# The owner's number, not a searched one.
MAX_BREAK_AGE = 7

# Pre-registered before any replay. `v9` is the published two-sided V9 and
# exists so truncation can be checked trade-for-trade against its ledger.
# `v9_long` removes short entries only, so the V10 change splits cleanly into
# "go long-only" (v9 -> v9_long) and "require the break" (v9_long -> v10).
ARMS = ("v9", "v9_long", "v10")


def long_break_ages(bars: pd.DataFrame, gap: np.ndarray, tick: float,
                    params: TrendlineParams | None = None) -> pd.DataFrame:
    """Upward breaks of the descending line and bars since the latest one.

    `bars` carries high/low/close/atr on the replay's bar clock; `gap` marks bars
    whose predecessor is missing. Only the owner's direction is computed: the
    mirrored support line of the withdrawn V10 has no role in a long-only rule.
    """
    params = params or TrendlineParams()
    high, low, close, atr = (bars[name].to_numpy(float) for name in ("high", "low", "close", "atr"))
    result = trendline_breaks(high, low, close, atr, gap, tick, direction=1, params=params)
    return pd.DataFrame({"long_break": result.break_event, "long_break_age": result.break_age,
                         "long_line_active": result.line_active, "long_line_price": result.line_price},
                        index=bars.index)


def arm_masks(v9_allowed: np.ndarray, raw_side: np.ndarray, break_age: np.ndarray,
              max_break_age: int = MAX_BREAK_AGE) -> dict[str, np.ndarray]:
    """Entry permission per arm; each arm is a subset of the one before it.

    `break_age` is -1 where no break is recorded in the current gap-free run.
    """
    if max_break_age < 0:
        raise ValueError("max_break_age must be zero or positive")
    v9_allowed = np.asarray(v9_allowed, dtype=bool)
    long_side = np.asarray(raw_side, dtype=int) == 1
    age = np.asarray(break_age, dtype=np.int64)
    in_window = (age >= 0) & (age <= max_break_age)
    v9_long = v9_allowed & long_side
    return {"v9": v9_allowed, "v9_long": v9_long, "v10": v9_long & in_window}


def gate_reason(side: int, v9: bool, age: int, max_break_age: int = MAX_BREAK_AGE) -> str:
    """Name why one raw V9 candidate was or was not a V10 entry."""
    if side != 1:
        return "short_not_traded"
    if not v9:
        return "v9_refused"
    if age < 0:
        return "no_trendline_break_in_segment"
    if age > max_break_age:
        return f"break_age_{age}_gt_{max_break_age}"
    return "break_on_this_bar" if age == 0 else f"break_age_{age}"
