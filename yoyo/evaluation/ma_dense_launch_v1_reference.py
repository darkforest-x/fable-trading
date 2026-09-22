"""Python mirror of ``pine/ma_dense_launch_v1.pine`` for gate-parity checking.

The Pine indicator ports two frozen rule sets used by the Grade-A dense-launch
scanner: the autofill morphology gate (stage 1,
``exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json``) and the
perfect-filter hard gates (stage 2,
``exp-15m-ma-launch-owner-perfect-filter10000-v1/preregistration.json``).
This module recomputes the same quantities from OHLC so a test can assert that
frozen candidates still pass, keeping the Pine transcription honest.

Not ported here or in Pine: the reference-similarity distance, the Grade-A
quality score and the rendered ``box_height_norm`` gate (the scanner writes
0.0 for it), so a pass here is a superset of the research candidate set.
Conventions follow the research code: six close-based SMA/EMA 20/60/120, Pine
RMA ATR14 read at core end + 2, core length 4 or 5, twelve pre-core bars and
five confirmation bars. Nothing here trades, trains or writes state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

MA_PERIODS = (20, 60, 120)
STAGE1 = {"max_ma_envelope_atr": 1.5, "max_ma_spread_end_atr": 1.1, "max_core_body_atr": 1.2,
          "min_core_progress_atr": -0.6, "max_core_progress_atr": 1.3, "min_post1_progress_atr": 0.0,
          "min_post2_progress_atr": 1.0, "min_post3_progress_atr": 1.25, "min_post5_progress_atr": 1.75,
          "min_aligned_ma_slope_atr": 0.03, "max_minimum_close_to_ma_atr": 1.0,
          "max_close_to_ma_envelope_atr": 1.9, "max_body_to_ma_envelope_atr": 1.5}
STAGE2 = {"max_six_ma_end_bandwidth_atr": 0.95, "max_six_ma_core_envelope_atr": 1.5,
          "min_core_directional_progress_atr": -0.6, "max_core_directional_progress_atr": 1.0,
          "min_aligned_ma_slope_atr_per_bar": 0.02, "contracting_max_end_start_ratio": 0.9,
          "contracting_min_decrease_steps": 2, "crossing_max_end_start_ratio": 1.15,
          "crossing_min_pairwise_order_flips": 3, "min_candle_bundle_touch_rate": 0.4,
          "max_close_to_bundle_q75_atr": 1.5, "max_pre_body_q90_atr": 1.1, "max_pre_abs_path_atr": 5.5,
          "max_pre_last3_directional_progress_atr": 1.0, "max_pre_favourable_excursion_atr": 3.0,
          "max_core_wick_q90_atr": 2.0, "max_core_reverse_body_count": 1, "max_core_body_atr": 1.2,
          "min_post1_progress_atr": 0.0, "min_post2_progress_atr": 1.0, "min_post3_progress_atr": 1.25,
          "min_post5_progress_atr": 1.75, "min_post_progress_floor_atr": 0.0,
          "min_positive_post_steps_out_of_5": 3, "max_post_retrace_atr": 0.75,
          "max_post_reverse_body_count": 1, "max_opposite_post_body_atr": 0.8}


def pine_rma(values: np.ndarray, length: int) -> np.ndarray:
    """Wilder smoothing seeded with the mean of the first ``length`` values."""
    out = np.full(len(values), np.nan)
    if len(values) < length:
        return out
    out[length - 1] = float(np.mean(values[:length]))
    for i in range(length, len(values)):
        out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Close SMA/EMA 20/60/120 plus the Pine ATR14 used by the scanner."""
    out = frame.copy()
    close = out["close"]
    for period in MA_PERIODS:
        out[f"sma{period}"] = close.rolling(period).mean()
        out[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    previous = close.shift()
    tr = pd.concat([out.high - out.low, (out.high - previous).abs(), (out.low - previous).abs()],
                   axis=1).max(axis=1)
    out["atr"] = pine_rma(tr.to_numpy(float), 14)
    return out


@dataclass(frozen=True)
class Decision:
    """One evaluation at a confirmation bar; ``metrics`` mirrors the Pine names."""
    stage1: bool
    stage2: bool
    metrics: Mapping[str, float]
    core_high: float
    core_low: float

    @property
    def signal(self) -> bool:
        return self.stage1 and self.stage2


def _carried_flips(values: np.ndarray) -> int:
    flips, previous = 0, 0
    for index, value in enumerate(values):
        sign = 1 if value > 0 else -1 if value < 0 else previous
        if index and sign and previous and sign != previous:
            flips += 1
        previous = sign
    return flips


def evaluate(frame: pd.DataFrame, confirm_i: int, direction: str, core_bars: int) -> Decision | None:
    """Evaluate one direction and core length at ``confirm_i`` (core end + 5).

    ``frame`` must already carry :func:`add_features` columns. Returns ``None``
    when the window is incomplete, gapped or the anchor ATR is unusable.
    """
    if core_bars not in (4, 5) or direction not in ("LONG", "SHORT"):
        raise ValueError("core_bars must be 4 or 5 and direction LONG or SHORT")
    sign = 1.0 if direction == "LONG" else -1.0
    end_i = confirm_i - 5
    start_i = end_i - core_bars + 1
    anchor_i = end_i + 2
    if start_i - 12 < 0 or confirm_i >= len(frame):
        return None
    columns = [f"{kind}{period}" for kind in ("sma", "ema") for period in MA_PERIODS]
    window = frame.iloc[start_i - 12: confirm_i + 1]
    if not np.isfinite(window[["open", "high", "low", "close", *columns]].to_numpy(float)).all():
        return None
    atr = float(frame["atr"].iloc[anchor_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    o, h, l, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    mas = frame[columns].to_numpy(float)
    core = np.arange(start_i, end_i + 1)
    pre = np.arange(start_i - 12, start_i)
    post = np.arange(end_i + 1, end_i + 6)
    ma_low, ma_high = mas.min(axis=1), mas.max(axis=1)
    body_high, body_low = np.maximum(o, c), np.minimum(o, c)
    close_dist = np.maximum(np.maximum(ma_low[core] - c[core], c[core] - ma_high[core]), 0.0) / atr
    body_dist = np.maximum(np.maximum(ma_low[core] - body_high[core], body_low[core] - ma_high[core]), 0.0) / atr
    progress = sign * (c[post] - c[end_i]) / atr
    path = np.r_[0.0, progress]
    bodies = sign * (c - o) / atr
    m = {
        "core_envelope_atr": float((mas[core].max() - mas[core].min()) / atr),
        "end_spread_atr": float((ma_high[end_i] - ma_low[end_i]) / atr),
        "core_max_body_atr": float(np.abs(c[core] - o[core]).max() / atr),
        "core_progress_atr": float(sign * (c[end_i] - c[start_i]) / atr),
        "post1": float(progress[0]), "post2": float(progress[1]), "post3": float(progress[2]),
        "post5": float(progress[4]),
        "slope_stage1_atr": float(sign * ((mas[end_i] - mas[start_i]) / atr).mean()),
        "slope_stage2_atr_per_bar": float(sign * (mas[end_i].mean() - mas[start_i].mean()) / atr / max(core_bars - 1, 1)),
        "min_close_to_ma_atr": float(np.abs(c[core, None] - mas[core]).min() / atr),
        "max_close_to_envelope_atr": float(close_dist.max()),
        "max_body_to_envelope_atr": float(body_dist.max()),
        "bundle_ratio": float((ma_high[end_i] - ma_low[end_i]) / max(ma_high[start_i] - ma_low[start_i], np.finfo(float).eps)),
        "bundle_decrease_steps": float((np.diff(ma_high[core] - ma_low[core]) < 0).sum()),
        "pairwise_flips": float(sum(_carried_flips(mas[start_i - 1: end_i + 1, a] - mas[start_i - 1: end_i + 1, b])
                                    for a in range(6) for b in range(a + 1, 6))),
        "touch_rate": float(((h[core] >= ma_low[core]) & (l[core] <= ma_high[core])).mean()),
        "close_to_bundle_q75_atr": float(np.quantile(close_dist, 0.75)),
        "core_wick_q90_atr": float(np.quantile(((h[core] - body_high[core]) + (body_low[core] - l[core])) / atr, 0.90)),
        "core_reverse_bodies": float((bodies[core] < -0.20).sum()),
        "pre_body_q90_atr": float(np.quantile(np.abs(c[pre] - o[pre]) / atr, 0.90)),
        "pre_abs_path_atr": float(np.abs(np.diff(c[pre])).sum() / atr),
        "pre_last3_atr": float(sign * (c[start_i - 1] - c[start_i - 4]) / atr),
        "pre_favourable_atr": float(max(0.0, (sign * (c[pre] - c[start_i - 12]) / atr).max())),
        "post_min_progress_atr": float(path.min()),
        "positive_post_steps": float((sign * np.diff(c[end_i: end_i + 6]) > 0).sum()),
        "post_retrace_atr": float(np.max(np.maximum.accumulate(path) - path)),
        "post_reverse_bodies": float((bodies[post] < -0.20).sum()),
        "max_opposite_post_body_atr": float(max(0.0, -bodies[post].min())),
    }
    g1, g2 = STAGE1, STAGE2
    stage1 = (m["core_envelope_atr"] <= g1["max_ma_envelope_atr"]
              and m["end_spread_atr"] <= g1["max_ma_spread_end_atr"]
              and m["core_max_body_atr"] <= g1["max_core_body_atr"]
              and g1["min_core_progress_atr"] <= m["core_progress_atr"] <= g1["max_core_progress_atr"]
              and m["post1"] >= g1["min_post1_progress_atr"] and m["post2"] >= g1["min_post2_progress_atr"]
              and m["post3"] >= g1["min_post3_progress_atr"] and m["post5"] >= g1["min_post5_progress_atr"]
              and m["slope_stage1_atr"] >= g1["min_aligned_ma_slope_atr"]
              and m["min_close_to_ma_atr"] <= g1["max_minimum_close_to_ma_atr"]
              and m["max_close_to_envelope_atr"] <= g1["max_close_to_ma_envelope_atr"]
              and m["max_body_to_envelope_atr"] <= g1["max_body_to_ma_envelope_atr"])
    topology = ((m["bundle_ratio"] <= g2["contracting_max_end_start_ratio"]
                 and m["bundle_decrease_steps"] >= g2["contracting_min_decrease_steps"])
                or (m["bundle_ratio"] <= g2["crossing_max_end_start_ratio"]
                    and m["pairwise_flips"] >= g2["crossing_min_pairwise_order_flips"]))
    stage2 = (m["end_spread_atr"] <= g2["max_six_ma_end_bandwidth_atr"]
              and m["core_envelope_atr"] <= g2["max_six_ma_core_envelope_atr"]
              and g2["min_core_directional_progress_atr"] <= m["core_progress_atr"] <= g2["max_core_directional_progress_atr"]
              and m["slope_stage2_atr_per_bar"] >= g2["min_aligned_ma_slope_atr_per_bar"]
              and topology
              and m["touch_rate"] >= g2["min_candle_bundle_touch_rate"]
              and m["close_to_bundle_q75_atr"] <= g2["max_close_to_bundle_q75_atr"]
              and m["pre_body_q90_atr"] <= g2["max_pre_body_q90_atr"]
              and m["pre_abs_path_atr"] <= g2["max_pre_abs_path_atr"]
              and m["pre_last3_atr"] <= g2["max_pre_last3_directional_progress_atr"]
              and m["pre_favourable_atr"] <= g2["max_pre_favourable_excursion_atr"]
              and m["core_wick_q90_atr"] <= g2["max_core_wick_q90_atr"]
              and m["core_reverse_bodies"] <= g2["max_core_reverse_body_count"]
              and m["core_max_body_atr"] <= g2["max_core_body_atr"]
              and m["post1"] >= g2["min_post1_progress_atr"] and m["post2"] >= g2["min_post2_progress_atr"]
              and m["post3"] >= g2["min_post3_progress_atr"] and m["post5"] >= g2["min_post5_progress_atr"]
              and m["post_min_progress_atr"] >= g2["min_post_progress_floor_atr"]
              and m["positive_post_steps"] >= g2["min_positive_post_steps_out_of_5"]
              and m["post_retrace_atr"] <= g2["max_post_retrace_atr"]
              and m["post_reverse_bodies"] <= g2["max_post_reverse_body_count"]
              and m["max_opposite_post_body_atr"] <= g2["max_opposite_post_body_atr"])
    return Decision(stage1, stage2, m, float(h[core].max()), float(l[core].min()))
