"""Python mirror of ``pine/ma_dense_launch_v1.pine`` for gate-parity checking.

The Pine indicator ports two frozen rule sets used by the Grade-A dense-launch
scanner: the autofill morphology gate (stage 1,
``exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json``) and the
perfect-filter hard gates (stage 2,
``exp-15m-ma-launch-owner-perfect-filter10000-v1/preregistration.json``).
This module recomputes the same quantities from OHLC so a test can assert that
frozen candidates still pass, keeping the Pine transcription honest.

The V1 indicator stops at those gates, so its signals are a superset of the
research candidate set. The functions below ``SEGMENTS`` add what V2 ports on
top: the stage-1 nearest-of-fifty similarity and the Grade-A quality score.
Never ported: the rendered ``box_height_norm`` gate, for which the scanner
itself writes 0.0.
Conventions follow the research code: six close-based SMA/EMA 20/60/120, Pine
RMA ATR14 read at core end + 2, core length 4 or 5, twelve pre-core bars and
five confirmation bars. Nothing here trades, trains or writes state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

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
        "body_touch_rate": float((((body_high[core] + 0.05 * atr >= ma_low[core])
                                   & (body_low[core] - 0.05 * atr <= ma_high[core]))).mean()),
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


# --------------------------------------------------------------------------
# Similarity and quality score (V2). Re-implemented the way the Pine port
# computes them — the tests compare this against the frozen research
# functions, so a drift in either side fails rather than passes silently.
# --------------------------------------------------------------------------

SEGMENTS = (("prelude", 0, 12), ("core", 12, 17), ("release", 17, 22))


def resample5(values: np.ndarray) -> np.ndarray:
    """Linear resample of a 4- or 5-point core onto five points (numpy.interp)."""
    n = len(values)
    if n == 5:
        return np.asarray(values, float)
    if n != 4:
        raise ValueError("core resampling needs four or five values")
    return np.interp(np.linspace(0.0, 1.0, 5), np.linspace(0.0, 1.0, n), values)


def znorm(block: np.ndarray) -> np.ndarray:
    """Per-channel z-normalization inside one segment; flat channels stay flat."""
    mean = block.mean(axis=1, keepdims=True)
    std = block.std(axis=1, keepdims=True)
    return (block - mean) / np.where(std <= 1e-12, 1.0, std)


def dtw_distance(left: np.ndarray, right: np.ndarray, radius: int) -> float:
    """Sakoe-Chiba constrained multivariate DTW, RMS-scaled like the research code."""
    n, m = left.shape[1], right.shape[1]
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - radius), min(m, i + radius) + 1):
            local = float(np.sum((left[:, i - 1] - right[:, j - 1]) ** 2))
            cost[i, j] = min(cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1]) + local
    return float(np.sqrt(cost[n, m] / (left.shape[0] * max(n, m))))


def derivative(block: np.ndarray) -> np.ndarray:
    """Keogh derivative used by derivative-DTW."""
    return 0.5 * ((block[:, 1:-1] - block[:, :-2]) + 0.5 * (block[:, 2:] - block[:, :-2]))


def segment_distance(left: np.ndarray, right: np.ndarray, radius: int, weights: Mapping[str, float]) -> float:
    a, b = znorm(left), znorm(right)
    lockstep = float(np.sqrt(np.mean((a - b) ** 2)))
    r = min(radius, left.shape[1] - 1)
    return (weights["lockstep"] * lockstep + weights["dtw"] * dtw_distance(a, b, r)
            + weights["ddtw"] * dtw_distance(derivative(a), derivative(b), r))


def segmented_distance(left: np.ndarray, right: np.ndarray, stage2: Mapping[str, object]) -> float:
    weights = stage2["segment_weights"]
    return sum(float(weights[name]) * segment_distance(left[:, a:b], right[:, a:b], int(stage2["radius"]),
                                                       stage2["component_weights"]) for name, a, b in SEGMENTS)


def segmented_lockstep(left: np.ndarray, right: np.ndarray, stage2: Mapping[str, object]) -> float:
    weights = stage2["segment_weights"]
    return sum(float(weights[name]) * float(np.sqrt(np.mean((znorm(left[:, a:b]) - znorm(right[:, a:b])) ** 2)))
               for name, a, b in SEGMENTS)


def nearest_distance(sequence: np.ndarray, pool: Sequence[np.ndarray], stage2: Mapping[str, object]) -> float:
    """Lock-step prefilter to k references, then the segmented DTW blend."""
    k = int(stage2["prefilter_k"])
    order = list(range(len(pool)))
    if len(order) > k:
        order = sorted(order, key=lambda i: (segmented_lockstep(sequence, pool[i], stage2), i))[:k]
    return min(segmented_distance(sequence, pool[i], stage2) for i in order)


def profiles(frame: pd.DataFrame, confirm_i: int, direction: str, core_bars: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stage-1 features (14) and 4x10 sequence, plus the stage-2 7x22 sequence."""
    sign = 1.0 if direction == "LONG" else -1.0
    end_i = confirm_i - 5
    start_i = end_i - core_bars + 1
    atr = float(frame["atr"].iloc[end_i + 2])
    columns = [f"{kind}{period}" for kind in ("sma", "ema") for period in MA_PERIODS]
    o, h, l, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    mas = frame[columns].to_numpy(float)
    body_high, body_low = np.maximum(o, c), np.minimum(o, c)
    ma_low, ma_high, ma_mid = mas.min(axis=1), mas.max(axis=1), mas.mean(axis=1)
    core = np.arange(start_i, end_i + 1)
    span = np.arange(start_i, end_i + 6)
    origin = float(mas[start_i].mean())
    # stage 1: core+release only
    close_path = sign * (c[span] - origin) / atr
    body = sign * (c[span] - o[span]) / atr
    centre = sign * (ma_mid[span] - origin) / atr
    spread = (ma_high[span] - ma_low[span]) / atr
    seq1 = np.stack([np.r_[resample5(ch[:core_bars]), ch[core_bars:]] for ch in (close_path, body, centre, spread)])
    slopes = (mas[end_i] - mas[start_i]) / atr
    close_env = np.maximum(np.maximum(ma_low[core] - c[core], c[core] - ma_high[core]), 0.0) / atr
    body_env = np.maximum(np.maximum(ma_low[core] - body_high[core], body_low[core] - ma_high[core]), 0.0) / atr
    features = np.array([
        (mas[core].max() - mas[core].min()) / atr,
        (mas[end_i].max() - mas[end_i].min()) / atr,
        (h[core].max() - l[core].min()) / atr,
        np.abs(c[core] - o[core]).max() / atr,
        sign * (c[end_i] - c[start_i]) / atr,
        sign * (c[end_i + 1] - c[end_i]) / atr,
        sign * (c[end_i + 2] - c[end_i]) / atr,
        sign * (c[end_i + 3] - c[end_i]) / atr,
        sign * (c[end_i + 5] - c[end_i]) / atr,
        sign * float(slopes.mean()),
        float(slopes.std()),
        float(np.abs(c[core, None] - mas[core]).min() / atr),
        float(close_env.max()),
        float(body_env.max()),
    ])
    # stage 2: 12 pre-core bars + resampled core + 5 release bars, seven channels
    wide = np.arange(start_i - 12, end_i + 6)
    favourable = np.where(sign > 0, h - body_high, body_low - l) / atr
    adverse = np.where(sign > 0, body_low - l, h - body_high) / atr
    channels = (sign * (c - origin) / atr, sign * (c - o) / atr, favourable, adverse,
                sign * (ma_mid - origin) / atr, (ma_high - ma_low) / atr, sign * (c - ma_mid) / atr)
    seq2 = np.stack([np.r_[ch[start_i - 12:start_i], resample5(ch[core]), ch[end_i + 1:end_i + 6]] for ch in channels])
    return features, seq1, seq2


def _clip(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def quality_score(metrics: Mapping[str, float], good: float, bad: float, family: float,
                  stage2: Mapping[str, object]) -> dict[str, float]:
    """Six-axis Grade-A quality score (mirror of ``_axis_scores``)."""
    scale = max(float(stage2["distance_scale"]), 1e-12)
    contraction = _clip((1.15 - metrics["bundle_ratio"]) / 0.50)
    topology = max((contraction + _clip(metrics["bundle_decrease_steps"] / 4.0)) / 2.0,
                   (contraction + _clip(metrics["pairwise_flips"] / 6.0)) / 2.0)
    density = float(np.mean([_clip((1.10 - metrics["end_spread_atr"]) / 0.85),
                             _clip((1.60 - metrics["core_envelope_atr"]) / 1.35), topology,
                             _clip(metrics["slope_stage2_atr_per_bar"] / 0.08)]))
    quietness = float(np.mean([_clip(1.0 - metrics["pre_body_q90_atr"] / 1.10),
                               _clip(1.0 - metrics["pre_abs_path_atr"] / 5.50),
                               _clip(1.0 - max(0.0, metrics["pre_last3_atr"])),
                               _clip(1.0 - metrics["pre_favourable_atr"] / 3.00)]))
    contact = float(np.mean([_clip((metrics["touch_rate"] - 0.40) / 0.60),
                             _clip(1.0 - metrics["close_to_bundle_q75_atr"] / 1.50),
                             metrics["body_touch_rate"]]))
    release = float(np.mean([_clip(metrics["post1"] / 1.50), _clip(metrics["post2"] / 2.00),
                             _clip(metrics["post3"] / 2.50), _clip(metrics["post5"] / 3.50),
                             _clip(metrics["positive_post_steps"] / 5.0),
                             _clip(1.0 - metrics["post_retrace_atr"] / 0.75)]))
    cleanliness = float(np.mean([_clip(1.0 - metrics["core_wick_q90_atr"] / 2.00),
                                 _clip(1.0 - metrics["core_reverse_bodies"] / 2.0),
                                 _clip(1.0 - metrics["core_max_body_atr"] / 1.20),
                                 _clip(1.0 - metrics["max_opposite_post_body_atr"] / 0.80)]))
    similarity = (0.50 * float(np.exp(-good / scale)) + 0.25 * float(np.exp(-family / scale))
                  + 0.25 * float(1.0 / (1.0 + np.exp(-(bad - good) / scale))))
    axes = {"density_topology": density, "prelude_quietness": quietness, "price_bundle_contact": contact,
            "release_cleanliness": release, "wick_reverse_cleanliness": cleanliness,
            "reference_similarity": similarity}
    weights = stage2["axis_weights"]
    worst = float(stage2["worst_axis_weight"])
    weighted = sum(float(weights[name]) * value for name, value in axes.items())
    return {**axes, "quality_score": (1.0 - worst) * weighted + worst * min(axes.values())}


def evaluate_full(frame: pd.DataFrame, confirm_i: int, direction: str, core_bars: int,
                  pack: Mapping[str, object]) -> dict[str, object] | None:
    """Hard gates, stage-1 similarity and the Grade-A quality score for one candidate."""
    decision = evaluate(frame, confirm_i, direction, core_bars)
    if decision is None:
        return None
    features, seq1, seq2 = profiles(frame, confirm_i, direction, core_bars)
    stage1, stage2 = pack["stage1"], pack["stage2"]
    scales = np.asarray(stage1["feature_scales"], float)
    best = min(float(stage1["feature_weight"]) * float(np.sqrt(np.mean(((features - np.asarray(f, float)) / scales) ** 2)))
               + float(stage1["sequence_weight"]) * float(np.sqrt(np.mean((seq1 - np.asarray(s, float)) ** 2)))
               for f, s in zip(stage1["features"], stage1["sequences"]))
    good = nearest_distance(seq2, [np.asarray(x, float) for x in stage2["anchors"]], stage2)
    bad = nearest_distance(seq2, [np.asarray(x, float) for x in stage2["bad"]], stage2)
    family = nearest_distance(seq2, [np.asarray(x, float) for x in stage2["family"]], stage2)
    metrics = dict(decision.metrics)
    scored = quality_score(metrics, good, bad, family, stage2)
    return {"hard_gates": decision.signal, "stage1_distance": best,
            "stage1_similarity": best <= float(stage1["max_distance"]),
            "good_distance": good, "bad_distance": bad, "family_distance": family, **scored,
            "grade_a": bool(decision.signal and best <= float(stage1["max_distance"])
                            and scored["quality_score"] >= float(stage2["perfect_threshold"])),
            "core_high": decision.core_high, "core_low": decision.core_low}
