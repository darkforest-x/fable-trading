#!/usr/bin/env python3
"""Audit causal two-key-candle features without reading repository holdout.

Source columns are the frozen V2 causal event table. Every feature uses only
K1, K2 or bars ending at K2: candle geometry, exact K1-to-K2 distance, path,
volume, ATR, SMA40(HL2), the six-MA context rope, public-formula MA Shift state,
and the causally confirmed 10/10 Market Break state. Outcomes alone use bars
after K2. Feature families are tested one at a time; no interaction model is
fit. The source is rejected if any K2 timestamp reaches the 2026-05-04 holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_two_key_candle_ma_retest_1h import (
    clustered_ci,
    half_label,
    profit_factor,
    sha256_file,
    signflip_p,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-two-key-candle-feature-atlas-v3"
CONFIG_PATH = EXPERIMENT / "config.json"
OUT = EXPERIMENT / "results"

BLUE = "#315A7D"
GOLD = "#D6A249"
ORANGE = "#C56B37"
INK = "#26323A"
GREY = "#AAB3B8"
GRID = "#D9DEE1"


def stable_seed(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame[column]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False).astype(bool)
    return values.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def cut(
    values: pd.Series,
    edges: Iterable[float],
    labels: Iterable[str],
    *,
    include_lowest: bool = True,
) -> pd.Series:
    return pd.cut(
        values.astype(float),
        list(edges),
        labels=list(labels),
        include_lowest=include_lowest,
        ordered=True,
    )


def validate_source(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    required = {
        "event_id",
        "profile",
        "segment",
        "symbol",
        "direction",
        "side",
        "k1_i",
        "k2_i",
        "k2_time",
        "k2_high",
        "k2_low",
        "n_controls",
    }
    for horizon in (12, 24, 48):
        required |= {
            f"entry_i_{horizon}",
            f"entry_price_{horizon}",
            f"stop_price_{horizon}",
            f"gross_return_{horizon}",
            f"net_return_{horizon}",
            f"control_net_return_{horizon}",
            f"paired_excess_{horizon}",
        }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"source is missing required columns: {missing}")

    work = frame.copy()
    work["k2_time"] = pd.to_datetime(work["k2_time"], utc=True)
    safe_end = pd.Timestamp(config["time_splits"]["safe_end_exclusive"])
    holdout = pd.Timestamp(config["time_splits"]["holdout_start"])
    if safe_end != holdout:
        raise ValueError("safe boundary must equal repository holdout boundary")
    if work["k2_time"].ge(holdout).any():
        bad = work.loc[work["k2_time"].ge(holdout), "k2_time"].min()
        raise ValueError(f"holdout row detected at {bad}")
    if work["event_id"].duplicated().any():
        raise ValueError("source profile contains duplicate event_id rows")
    if not work["n_controls"].eq(int(config["matched_control"]["required_per_event"])).all():
        raise ValueError("not every event has the preregistered exact control count")
    if not work["side"].eq(np.where(work["direction"].eq(1), "long", "short")).all():
        raise ValueError("direction/side mismatch")

    max_abs_arithmetic_error = 0.0
    for horizon in (12, 24, 48):
        if not work[f"entry_i_{horizon}"].eq(work["k2_i"] + 1).all():
            raise ValueError(f"non-causal entry index at horizon {horizon}")
        expected_stop = np.where(work["direction"].eq(1), work["k2_low"], work["k2_high"])
        if not np.allclose(work[f"stop_price_{horizon}"], expected_stop, rtol=0, atol=1e-10):
            raise ValueError(f"stop is not the exact K2 extreme at horizon {horizon}")
        cost_error = (
            work[f"net_return_{horizon}"]
            - work[f"gross_return_{horizon}"]
            + float(config["round_trip_cost"])
        ).abs()
        excess_error = (
            work[f"paired_excess_{horizon}"]
            - work[f"net_return_{horizon}"]
            + work[f"control_net_return_{horizon}"]
        ).abs()
        max_abs_arithmetic_error = max(
            max_abs_arithmetic_error,
            float(cost_error.max()),
            float(excess_error.max()),
        )
        if float(cost_error.max()) > 1e-10 or float(excess_error.max()) > 1e-10:
            raise ValueError(f"return arithmetic mismatch at horizon {horizon}")

    return {
        "rows": int(len(work)),
        "symbols": int(work["symbol"].nunique()),
        "min_k2_time": work["k2_time"].min().isoformat(),
        "max_k2_time": work["k2_time"].max().isoformat(),
        "max_abs_arithmetic_error": max_abs_arithmetic_error,
        "holdout_rows": int(work["k2_time"].ge(holdout).sum()),
    }


def add_time_split(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    times = out["k2_time"]
    cuts = config["time_splits"]
    choices = [
        times.ge(pd.Timestamp(cuts["discovery_start"]))
        & times.lt(pd.Timestamp(cuts["discovery_end_exclusive"])),
        times.ge(pd.Timestamp(cuts["validation_start"]))
        & times.lt(pd.Timestamp(cuts["validation_end_exclusive"])),
        times.ge(pd.Timestamp(cuts["bridge_start"]))
        & times.lt(pd.Timestamp(cuts["bridge_end_exclusive"])),
        times.ge(pd.Timestamp(cuts["fresh_start"]))
        & times.lt(pd.Timestamp(cuts["safe_end_exclusive"])),
    ]
    out["analysis_split"] = np.select(
        choices,
        ["discovery", "validation", "bridge", "fresh_preholdout"],
        default="outside",
    )
    if out["analysis_split"].eq("outside").any():
        times_outside = out.loc[out["analysis_split"].eq("outside"), "k2_time"]
        raise ValueError(
            f"source row outside declared splits: {times_outside.min()} .. {times_outside.max()}"
        )
    out["half"] = half_label(out["k2_time"])
    out["analysis_period"] = np.select(
        [out["analysis_split"].eq("bridge"), out["analysis_split"].eq("fresh_preholdout")],
        ["2026 Jan-Feb bridge", "2026 Mar-Apr fresh"],
        default=out["half"],
    )
    return out


def make_dimensions(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Return fixed, causal bins covering morphology, path, state and context."""

    k1_ma = bool_series(frame, "k1_ma_colour_aligned")
    k2_ma = bool_series(frame, "k2_ma_colour_aligned")
    k1_accel = bool_series(frame, "k1_osc_accel_aligned")
    k2_sign = bool_series(frame, "k2_osc_sign_aligned")
    k2_accel = bool_series(frame, "k2_osc_accel_aligned")
    k1_struct = bool_series(frame, "k1_structure_aligned")
    k2_struct = bool_series(frame, "k2_structure_aligned")
    k2_not_opposite = bool_series(frame, "k2_structure_not_opposite")
    side_volume = np.where(
        frame["direction"].eq(1),
        frame["green_volume_share_20"],
        1.0 - frame["green_volume_share_20"],
    )

    dimensions: dict[str, pd.Series] = {
        "direction": frame["side"].astype(str),
        "gap_exact": frame["gap_bars"].astype(int).astype(str),
        "gap_band": cut(frame["gap_bars"], [1.5, 2.5, 4.5, 6.5, 8.5], ["2", "3-4", "5-6", "7-8"]),
        "k1_sma40_cross_depth": cut(frame["k1_sma40_cross_depth_atr"], [-0.051, 0.05, 0.20, 0.40, 0.70, np.inf], ["near/incomplete", "0.05-0.20", "0.20-0.40", "0.40-0.70", ">0.70"]),
        "k1_body_share": cut(frame["k1_body_ratio"], [0.499, 0.65, 0.80, 0.90, 1.001], ["0.50-0.65", "0.65-0.80", "0.80-0.90", ">0.90"]),
        "k1_range_atr": cut(frame["k1_range_atr"], [0.999, 1.25, 1.75, 2.50, np.inf], ["1.00-1.25", "1.25-1.75", "1.75-2.50", ">2.50"]),
        "k1_close_location": cut(frame["k1_close_location"], [0.749, 0.82, 0.88, 0.94, 1.001], ["0.75-0.82", "0.82-0.88", "0.88-0.94", ">0.94"]),
        "k1_volume_ratio": cut(frame["k1_volume_ratio"], [-np.inf, 0.8, 1.2, 1.5, 2.0, np.inf], ["<0.8", "0.8-1.2", "1.2-1.5", "1.5-2.0", ">2.0"]),
        "k1_ma_colour": np.where(k1_ma, "aligned", "opposite"),
        "k1_oscillator_acceleration": np.where(k1_accel, "aligned", "opposite"),
        "k1_structure_state": np.where(k1_struct, "aligned", "not aligned"),
        "k2_wick_share": cut(frame["k2_wick_share"], [0.449, 0.60, 0.75, 0.90, 1.001], ["0.45-0.60", "0.60-0.75", "0.75-0.90", ">0.90"]),
        "k2_body_share": cut(frame["k2_body_ratio"], [-0.001, 0.10, 0.25, 0.501], ["0-0.10", "0.10-0.25", "0.25-0.50"]),
        "k2_range_atr": cut(frame["k2_range_atr"], [-np.inf, 0.60, 0.90, 1.30, 1.80, np.inf], ["<0.60", "0.60-0.90", "0.90-1.30", "1.30-1.80", ">1.80"]),
        "k2_sma40_touch_depth": cut(frame["k2_sma40_touch_depth_atr"], [-0.051, 0.15, 0.40, 0.80, 1.501], ["near line", "0.15-0.40", "0.40-0.80", "0.80-1.50"]),
        "k2_sma40_close_reclaim": cut(frame["k2_sma40_close_side_atr"], [-0.001, 0.25, 0.50, 1.00, np.inf], ["0-0.25", "0.25-0.50", "0.50-1.00", ">1.00"]),
        "k2_native_body_colour": np.where(bool_series(frame, "k2_native_colour_aligned"), "aligned", "opposite"),
        "k2_ma_shift_colour": np.where(k2_ma, "aligned", "opposite"),
        "k2_oscillator_sign": np.where(k2_sign, "aligned", "opposite"),
        "k2_oscillator_acceleration": np.where(k2_accel, "aligned", "opposite/cooling"),
        "k2_structure_state": np.select([k2_struct, k2_not_opposite], ["aligned", "neutral"], default="opposite"),
        "oscillator_transition": np.select(
            [
                k1_accel & k2_sign & ~k2_accel,
                k1_accel & k2_sign & k2_accel,
                ~k1_accel & k2_sign,
                ~k2_sign,
            ],
            ["K1 impulse -> K2 aligned/cooling", "K1 impulse -> K2 accelerating", "K2 sign arrives late", "K2 sign opposite"],
            default="other",
        ),
        "ma_colour_transition": np.select([k1_ma & k2_ma, k1_ma & ~k2_ma, ~k1_ma & k2_ma], ["both aligned", "K1 only", "K2 only"], default="neither"),
        "structure_transition": np.select([k1_struct & k2_struct, k1_struct & ~k2_struct, ~k1_struct & k2_struct], ["both aligned", "K1 only", "K2 only"], default="neither"),
        "k2_vs_k1_extreme": cut(frame["extreme_distance_atr"], [-np.inf, -0.50, 0.0, 0.50, np.inf], ["deep sweep", "mild sweep", "holds within 0.5ATR", "holds >0.5ATR"]),
        "k2_vs_k1_close": cut(frame["close_distance_atr"], [-np.inf, -0.75, -0.25, 0.25, 0.75, np.inf], ["far retrace", "moderate retrace", "similar close", "extends 0.25-0.75", "extends >0.75"]),
        "k1_k2_body_overlap": cut(frame["k1_k2_body_overlap_share"], [-0.001, 0.001, 0.10, 0.25, 0.50, 1.001], ["none", "0-0.10", "0.10-0.25", "0.25-0.50", ">0.50"]),
        "k2_to_k1_volume": cut(frame["k2_to_k1_volume_ratio"], [-np.inf, 0.50, 0.80, 1.25, 2.00, np.inf], ["<0.50", "0.50-0.80", "0.80-1.25", "1.25-2.00", ">2.00"]),
        "pre_retest_extension": cut(frame["pre_retest_extension_atr"], [-np.inf, 0.25, 0.50, 1.00, 2.00, np.inf], ["<0.25", "0.25-0.50", "0.50-1.00", "1.00-2.00", ">2.00"]),
        "path_variation": cut(frame["path_variation_atr"], [-np.inf, 0.50, 1.00, 2.00, np.inf], ["<0.50", "0.50-1.00", "1.00-2.00", ">2.00"]),
        "path_efficiency": cut(frame["path_efficiency"], [-np.inf, -0.50, 0.0, 0.50, np.inf], ["<-0.50", "-0.50-0", "0-0.50", ">0.50"]),
        "wrong_sma40_closes": cut(frame["wrong_sma40_close_count"], [-0.5, 0.5, 1.5, np.inf], ["0", "1", "2+"]),
        "intermediate_ma_colour_share": cut(frame["intermediate_ma_colour_share"], [-0.01, 0.50, 0.999, 1.01], ["<50%", "50-99%", "100%"]),
        "six_ma_rope_width": cut(frame["rope_width_atr"], [-np.inf, 1.00, 2.00, 3.00, np.inf], ["<1ATR", "1-2ATR", "2-3ATR", ">3ATR"]),
        "six_ma_rope_slope": np.where(frame["rope_slope_side_atr"].ge(0), "aligned/nonnegative", "opposite"),
        "prior_rope_width": cut(frame["prior_rope_width_atr_20"], [-np.inf, 1.50, 2.50, 4.00, np.inf], ["<1.5ATR", "1.5-2.5ATR", "2.5-4ATR", ">4ATR"]),
        "atr_release": cut(frame["atr_release_24"], [-np.inf, 0.80, 1.00, 1.25, np.inf], ["<0.80", "0.80-1.00", "1.00-1.25", ">1.25"]),
        "atr_percent_of_price": cut(frame["atr_pct"], [-np.inf, 0.006, 0.010, 0.016, 0.025, np.inf], ["<0.6%", "0.6-1.0%", "1.0-1.6%", "1.6-2.5%", ">2.5%"]),
        "side_coloured_volume_share": cut(pd.Series(side_volume, index=frame.index), [0.0, 0.40, 0.50, 0.60, 1.0], ["<40%", "40-50%", "50-60%", ">60%"]),
        "utc_session": cut(frame["utc_hour"], [-1, 5, 11, 17, 23], ["00-05", "06-11", "12-17", "18-23"]),
        "weekpart": np.where(frame["weekday"].ge(5), "weekend", "weekday"),
        "anchor_score": cut(frame["anchor_score"], [-np.inf, 50, 60, 70, 80, 90, 101], ["<50", "50-60", "60-70", "70-80", "80-90", "90+"]),
    }
    return {name: pd.Series(values, index=frame.index).astype("string") for name, values in dimensions.items()}


def basic_metrics(frame: pd.DataFrame, horizon: int) -> dict[str, float | int]:
    if frame.empty:
        return {
            "n": 0,
            "n_long": 0,
            "n_short": 0,
            "gross_bp": float("nan"),
            "net_bp": float("nan"),
            "control_net_bp": float("nan"),
            "paired_excess_bp": float("nan"),
            "profit_factor": float("nan"),
            "win_rate": float("nan"),
            "stop_rate": float("nan"),
            "median_mfe_r": float("nan"),
            "hit_2r_rate": float("nan"),
        }
    return {
        "n": int(len(frame)),
        "n_long": int(frame["side"].eq("long").sum()),
        "n_short": int(frame["side"].eq("short").sum()),
        "gross_bp": float(frame[f"gross_return_{horizon}"].mean() * 1e4),
        "net_bp": float(frame[f"net_return_{horizon}"].mean() * 1e4),
        "control_net_bp": float(frame[f"control_net_return_{horizon}"].mean() * 1e4),
        "paired_excess_bp": float(frame[f"paired_excess_{horizon}"].mean() * 1e4),
        "profit_factor": float(profit_factor(frame[f"net_return_{horizon}"])),
        "win_rate": float(frame[f"net_return_{horizon}"].gt(0).mean()),
        "stop_rate": float(bool_series(frame, f"stopped_{horizon}").mean()),
        "median_mfe_r": float(frame[f"mfe_r_{horizon}"].median()),
        "hit_2r_rate": float(bool_series(frame, f"hit_2r_{horizon}").mean()),
    }


def build_atlas(
    frame: pd.DataFrame,
    dimensions: dict[str, pd.Series],
    horizons: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    atlas_rows: list[dict[str, Any]] = []
    half_rows: list[dict[str, Any]] = []
    for dimension, values in dimensions.items():
        local = frame.assign(_level=values)
        for level, group in local.groupby("_level", observed=True, dropna=False, sort=False):
            level_name = str(level)
            for split, split_group in group.groupby("analysis_split", sort=False):
                for horizon in horizons:
                    atlas_rows.append(
                        {
                            "dimension": dimension,
                            "level": level_name,
                            "split": str(split),
                            "horizon_bars": horizon,
                            **basic_metrics(split_group, horizon),
                        }
                    )
            for half, half_group in group.groupby("analysis_period", sort=True):
                half_rows.append(
                    {
                        "dimension": dimension,
                        "level": level_name,
                        "half": str(half),
                        "analysis_split": str(half_group["analysis_split"].iloc[0]),
                        **basic_metrics(half_group, 24),
                    }
                )
    return pd.DataFrame(atlas_rows), pd.DataFrame(half_rows)


def discovery_score(
    group: pd.DataFrame,
    config: dict[str, Any],
    *,
    dimension: str,
) -> tuple[bool, float, dict[str, float | int]]:
    requirements = config["selection"]
    metrics = basic_metrics(group, 24)
    half_metrics = [basic_metrics(part, 24) for _, part in group.groupby("half")]
    expected_halves = {"2023H1", "2023H2", "2024H1", "2024H2"}
    available_halves = set(group["half"].astype(str))
    min_half_n = min((int(item["n"]) for item in half_metrics), default=0)
    side_ok = dimension == "direction" or (
        int(metrics["n_long"]) >= int(requirements["minimum_discovery_events_per_side"])
        and int(metrics["n_short"]) >= int(requirements["minimum_discovery_events_per_side"])
    )
    eligible = bool(
        int(metrics["n"]) >= int(requirements["minimum_discovery_events"])
        and available_halves == expected_halves
        and min_half_n >= int(requirements["minimum_discovery_events_per_half"])
        and side_ok
    )
    half_net = [float(item["net_bp"]) for item in half_metrics]
    half_excess = [float(item["paired_excess_bp"]) for item in half_metrics]
    score = min(
        float(metrics["net_bp"]),
        float(metrics["paired_excess_bp"]),
        float(np.median(half_net)) if half_net else float("-inf"),
        float(np.median(half_excess)) if half_excess else float("-inf"),
    )
    return eligible, score, {
        **metrics,
        "half_count": int(len(half_metrics)),
        "min_half_n": min_half_n,
        "positive_net_halves": int(sum(value > 0 for value in half_net)),
        "positive_excess_halves": int(sum(value > 0 for value in half_excess)),
        "median_half_net_bp": float(np.median(half_net)) if half_net else float("nan"),
        "median_half_excess_bp": float(np.median(half_excess)) if half_excess else float("nan"),
    }


def bh_adjust(values: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values.dropna().astype(float)
    if valid.empty:
        return result
    order = valid.sort_values().index
    m = len(order)
    raw = np.asarray([float(valid.loc[index]) * m / rank for rank, index in enumerate(order, 1)])
    adjusted = np.minimum.accumulate(raw[::-1])[::-1].clip(0.0, 1.0)
    result.loc[order] = adjusted
    return result


def inferential_metrics(frame: pd.DataFrame, horizon: int, key: str) -> dict[str, float]:
    if len(frame) < 2:
        return {
            "net_signflip_p": float("nan"),
            "excess_signflip_p": float("nan"),
            "net_ci95_low_bp": float("nan"),
            "net_ci95_high_bp": float("nan"),
            "excess_ci95_low_bp": float("nan"),
            "excess_ci95_high_bp": float("nan"),
        }
    seed = stable_seed(key)
    net_low, net_high = clustered_ci(frame, f"net_return_{horizon}", seed + 1)
    excess_low, excess_high = clustered_ci(frame, f"paired_excess_{horizon}", seed + 2)
    return {
        "net_signflip_p": float(signflip_p(frame[f"net_return_{horizon}"], seed + 3)),
        "excess_signflip_p": float(signflip_p(frame[f"paired_excess_{horizon}"], seed + 4)),
        "net_ci95_low_bp": net_low * 1e4,
        "net_ci95_high_bp": net_high * 1e4,
        "excess_ci95_low_bp": excess_low * 1e4,
        "excess_ci95_high_bp": excess_high * 1e4,
    }


def select_and_replay(
    frame: pd.DataFrame,
    dimensions: dict[str, pd.Series],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selection_rows: list[dict[str, Any]] = []
    replay_rows: list[dict[str, Any]] = []
    discovery = frame[frame["analysis_split"].eq("discovery")]
    for dimension, values in dimensions.items():
        local = frame.assign(_level=values)
        local_discovery = local.loc[discovery.index]
        candidates: list[tuple[float, int, str, pd.DataFrame, dict[str, Any]]] = []
        for level, group in local_discovery.groupby("_level", observed=True, dropna=False, sort=False):
            level_name = str(level)
            eligible, score, metrics = discovery_score(group, config, dimension=dimension)
            selection_rows.append(
                {
                    "dimension": dimension,
                    "level": level_name,
                    "eligible": eligible,
                    "selected": False,
                    "discovery_score": score,
                    **metrics,
                }
            )
            if eligible:
                candidates.append((score, len(group), level_name, group, metrics))
        if not candidates:
            continue
        score, _, selected_level, _, selected_metrics = max(
            candidates,
            key=lambda item: (item[0], item[1], item[2]),
        )
        for row in reversed(selection_rows):
            if row["dimension"] == dimension and row["level"] == selected_level:
                row["selected"] = True
                break
        for split in ("discovery", "validation", "bridge", "fresh_preholdout"):
            group = local[
                local["analysis_split"].eq(split)
                & local["_level"].astype(str).eq(selected_level)
            ]
            replay_rows.append(
                {
                    "dimension": dimension,
                    "selected_level": selected_level,
                    "discovery_score": score,
                    "split": split,
                    **basic_metrics(group, 24),
                    **inferential_metrics(group, 24, f"{dimension}|{selected_level}|{split}"),
                }
            )
    selection = pd.DataFrame(selection_rows)
    replay = pd.DataFrame(replay_rows)
    validation = replay["split"].eq("validation")
    replay.loc[validation, "net_fdr_q"] = bh_adjust(replay.loc[validation, "net_signflip_p"])
    replay.loc[validation, "excess_fdr_q"] = bh_adjust(replay.loc[validation, "excess_signflip_p"])
    required_n = int(config["selection"]["minimum_validation_events"])
    replay["passes_validation_gate"] = False
    replay.loc[validation, "passes_validation_gate"] = (
        replay.loc[validation, "n"].ge(required_n)
        & replay.loc[validation, "net_bp"].gt(0)
        & replay.loc[validation, "paired_excess_bp"].gt(0)
        & replay.loc[validation, "profit_factor"].gt(1)
        & replay.loc[validation, "net_fdr_q"].lt(0.05)
        & replay.loc[validation, "excess_fdr_q"].lt(0.05)
        & replay.loc[validation, "net_ci95_low_bp"].gt(0)
        & replay.loc[validation, "excess_ci95_low_bp"].gt(0)
    )
    return selection, replay


def fixed_target_sensitivity(
    profiles: dict[str, pd.DataFrame],
    config: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cost = float(config["round_trip_cost"])
    horizons = [int(config["primary_horizon_bars"]), *map(int, config["sensitivity_horizon_bars"])]
    for profile, current in profiles.items():
        for split, group in current.groupby("analysis_split", sort=False):
            for horizon in horizons:
                risk_pct = (
                    group["direction"]
                    * (group[f"entry_price_{horizon}"] - group[f"stop_price_{horizon}"])
                    / group[f"entry_price_{horizon}"]
                )
                for target_r in map(int, config["fixed_target_r_sensitivity"]):
                    hit = bool_series(group, f"hit_{target_r}r_{horizon}")
                    fixed = pd.Series(
                        np.where(
                            hit,
                            target_r * risk_pct - cost,
                            group[f"net_return_{horizon}"],
                        ),
                        index=group.index,
                    )
                    rows.append(
                        {
                            "profile": profile,
                            "split": str(split),
                            "horizon_bars": horizon,
                            "target_r": target_r,
                            "n": int(len(group)),
                            "hit_rate": float(hit.mean()) if len(hit) else float("nan"),
                            "mean_net_bp": float(fixed.mean() * 1e4) if len(fixed) else float("nan"),
                            "profit_factor": float(profit_factor(fixed)),
                        }
                    )
    return pd.DataFrame(rows)


def cost_sensitivity(
    profiles: dict[str, pd.DataFrame],
    config: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for profile, current in profiles.items():
        for split, group in current.groupby("analysis_split", sort=False):
            for cost_bp in map(float, config["cost_sensitivity_bp"]):
                net = group["gross_return_24"] - cost_bp / 1e4
                rows.append(
                    {
                        "profile": profile,
                        "split": str(split),
                        "cost_bp": cost_bp,
                        "n": int(len(group)),
                        "gross_bp": float(group["gross_return_24"].mean() * 1e4),
                        "net_bp": float(net.mean() * 1e4),
                        "profit_factor": float(profit_factor(net)),
                    }
                )
    return pd.DataFrame(rows)


def btc_summary(profiles: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for profile, current in profiles.items():
        for split, group in current[current["symbol"].eq("BTC")].groupby("analysis_split", sort=False):
            for horizon in (12, 24, 48):
                rows.append(
                    {
                        "profile": profile,
                        "split": str(split),
                        "horizon_bars": horizon,
                        **basic_metrics(group, horizon),
                    }
                )
    return pd.DataFrame(rows)


def base_by_half(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for half, group in frame.groupby("analysis_period", sort=True):
        rows.append(
            {
                "half": str(half),
                "analysis_split": str(group["analysis_split"].iloc[0]),
                **basic_metrics(group, 24),
            }
        )
    return pd.DataFrame(rows)


def style_axis(axis: plt.Axes) -> None:
    axis.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
    axis.spines[["top", "right"]].set_visible(False)
    axis.tick_params(colors=INK)
    axis.xaxis.label.set_color(INK)
    axis.yaxis.label.set_color(INK)


def plot_halfyear(data: pd.DataFrame, output: Path) -> None:
    x = np.arange(len(data))
    width = 0.36
    fig, axis = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    axis.bar(x - width / 2, data["net_bp"], width, color=BLUE, edgecolor=INK, linewidth=0.5, label="signal net")
    axis.bar(x + width / 2, data["control_net_bp"], width, color=GOLD, edgecolor=INK, linewidth=0.5, label="matched control net")
    axis.axhline(0, color=INK, linewidth=1)
    axis.set_xticks(x, data["half"])
    axis.set_ylabel("bp per event after 20bp round-trip cost")
    axis.set_title("SMA40 core performance by half-year", loc="left", fontsize=15, color=INK)
    axis.text(0, 1.02, "Exact next-open entry, exact K2-extreme stop; n shown above paired bars", transform=axis.transAxes, color=INK)
    for i, n in enumerate(data["n"]):
        top = max(data.loc[i, "net_bp"], data.loc[i, "control_net_bp"], 0)
        axis.text(i, top + 5, f"n={int(n)}", ha="center", va="bottom", fontsize=8, color=INK)
    axis.legend(frameon=False, ncol=2, loc="lower right")
    style_axis(axis)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def plot_generalization(replay: pd.DataFrame, output: Path) -> None:
    discovery = replay[replay["split"].eq("discovery")][["dimension", "selected_level", "discovery_score"]]
    validation = replay[replay["split"].eq("validation")][["dimension", "net_bp", "paired_excess_bp", "n"]]
    data = discovery.merge(validation, on="dimension", validate="one_to_one")
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), constrained_layout=True)
    for axis, column, label in (
        (axes[0], "net_bp", "validation net bp"),
        (axes[1], "paired_excess_bp", "validation excess vs control bp"),
    ):
        axis.scatter(data["discovery_score"], data[column], s=np.sqrt(data["n"]) * 8, color=BLUE, alpha=0.78, edgecolor=INK, linewidth=0.5)
        axis.axhline(0, color=INK, linewidth=1)
        axis.axvline(0, color=INK, linewidth=1, linestyle="--")
        axis.set_xlabel("discovery robust score (bp)")
        axis.set_ylabel(label)
        style_axis(axis)
        labels = data.nlargest(3, column)._append(data.nsmallest(2, column)).drop_duplicates("dimension")
        for row in labels.itertuples():
            axis.annotate(row.dimension, (row.discovery_score, getattr(row, column)), xytext=(4, 4), textcoords="offset points", fontsize=7, color=INK)
    fig.suptitle("One-dimensional discovery choices replayed unchanged in 2025", x=0.01, ha="left", fontsize=15, color=INK)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def plot_validation_rank(replay: pd.DataFrame, output: Path) -> None:
    data = replay[replay["split"].eq("validation")].copy()
    data["joint"] = data[["net_bp", "paired_excess_bp"]].min(axis=1)
    data = data.sort_values("joint").tail(18)
    y = np.arange(len(data))
    fig, axis = plt.subplots(figsize=(12, 8.5), constrained_layout=True)
    axis.barh(y - 0.18, data["net_bp"], 0.36, color=BLUE, edgecolor=INK, linewidth=0.4, label="signal net")
    axis.barh(y + 0.18, data["paired_excess_bp"], 0.36, color=GOLD, edgecolor=INK, linewidth=0.4, label="excess vs matched control")
    axis.axvline(0, color=INK, linewidth=1)
    axis.set_yticks(y, [f"{d}: {l}" for d, l in zip(data["dimension"], data["selected_level"])])
    axis.set_xlabel("2025 bp per event")
    axis.set_title("Best 2023-2024 bin from each dimension: 2025 replay", loc="left", fontsize=15, color=INK)
    axis.text(0, 1.015, "Top 18 by the weaker of absolute net and matched-control excess; no threshold was refit on 2025", transform=axis.transAxes, color=INK)
    axis.legend(frameon=False, ncol=2, loc="lower right")
    style_axis(axis)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def plot_fixed_targets(data: pd.DataFrame, output: Path) -> None:
    selected = data[
        data["profile"].eq("anchor score >=70")
        & data["horizon_bars"].eq(24)
        & data["split"].isin(["discovery", "validation", "bridge", "fresh_preholdout"])
    ].copy()
    split_order = ["discovery", "validation", "bridge", "fresh_preholdout"]
    colours = [BLUE, GOLD, ORANGE, GREY]
    fig, axis = plt.subplots(figsize=(11, 6.5), constrained_layout=True)
    width = 0.18
    x = np.arange(4)
    for offset, (split, colour) in enumerate(zip(split_order, colours)):
        group = selected[selected["split"].eq(split)].set_index("target_r").reindex([1, 2, 3, 5])
        axis.bar(x + (offset - 1.5) * width, group["mean_net_bp"], width, label=split, color=colour, edgecolor=INK, linewidth=0.4)
    axis.axhline(0, color=INK, linewidth=1)
    axis.set_xticks(x, ["1R", "2R", "3R", "5R"])
    axis.set_ylabel("mean net bp per event")
    axis.set_title("Fixed-target sensitivity for anchor score >=70", loc="left", fontsize=15, color=INK)
    axis.text(0, 1.02, "24h opportunity window, stop-first intrabar convention, exact K2 stop and 20bp cost", transform=axis.transAxes, color=INK)
    axis.legend(frameon=False, ncol=2)
    style_axis(axis)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def chart_map() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"report_segment": "time stability", "question": "Does the frozen core beat costs and matched controls through time?", "family": "comparison", "chart_type": "grouped bar", "fields": "half, net_bp, control_net_bp, n", "supported_claim": "half-year sign stability or failure", "palette": "blue + gold + neutrals", "output": "core_halfyear.png"},
            {"report_segment": "feature generalization", "question": "Do discovery-selected one-dimensional bins generalize to 2025?", "family": "relationship", "chart_type": "two-panel scatter", "fields": "discovery_score, validation net/excess, n", "supported_claim": "selection-to-validation decay", "palette": "single blue root + neutrals", "output": "walkforward_generalization.png"},
            {"report_segment": "feature ranking", "question": "Which selected bins are least bad in 2025 on both absolute and relative outcomes?", "family": "comparison", "chart_type": "horizontal grouped bar", "fields": "dimension, level, net_bp, paired_excess_bp", "supported_claim": "no dimension passes the joint gate", "palette": "blue + gold + neutrals", "output": "validation_rank.png"},
            {"report_segment": "exit sensitivity", "question": "Does a fixed 1R/2R/3R/5R target rescue the anchor score?", "family": "comparison", "chart_type": "grouped bar", "fields": "split, target_r, mean_net_bp", "supported_claim": "fixed targets do or do not fix expectancy", "palette": "blue + gold + orange + neutral context", "output": "fixed_target_sensitivity.png"},
        ]
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    source = args.source or PROJECT / config["source_events"]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    all_rows = pd.read_csv(source)
    source_profile = str(config["source_profile"])
    frame = all_rows[all_rows["profile"].eq(source_profile)].copy()
    frame["k2_time"] = pd.to_datetime(frame["k2_time"], utc=True)
    source_receipt = validate_source(frame, config)
    frame = add_time_split(frame, config)
    exact_profile_names = {
        "SMA40 core": "single_sma40_core_2_8",
        "anchor score >=70": "sma40_anchor_score_70",
        "anchor score >=80": "sma40_anchor_score_80",
    }
    exact_profiles: dict[str, pd.DataFrame] = {}
    profile_receipts: dict[str, dict[str, Any]] = {}
    for label, profile_name in exact_profile_names.items():
        current = all_rows[all_rows["profile"].eq(profile_name)].copy()
        current["k2_time"] = pd.to_datetime(current["k2_time"], utc=True)
        profile_receipts[profile_name] = validate_source(current, config)
        exact_profiles[label] = add_time_split(current, config)
    dimensions = make_dimensions(frame)
    if list(dimensions) != list(config["feature_families"]):
        raise ValueError("implemented feature family order differs from preregistered config")

    horizons = [int(config["primary_horizon_bars"]), *map(int, config["sensitivity_horizon_bars"])]
    atlas, half_metrics = build_atlas(frame, dimensions, horizons)
    selection, replay = select_and_replay(frame, dimensions, config)
    fixed_targets = fixed_target_sensitivity(exact_profiles, config)
    costs = cost_sensitivity(exact_profiles, config)
    btc = btc_summary(exact_profiles)
    base_half = base_by_half(frame)
    maps = chart_map()

    atlas.to_csv(output / "dimension_atlas.csv", index=False)
    half_metrics.to_csv(output / "dimension_halfyear_metrics.csv", index=False)
    selection.to_csv(output / "discovery_selection.csv", index=False)
    replay.to_csv(output / "walkforward_replay.csv", index=False)
    fixed_targets.to_csv(output / "fixed_target_sensitivity.csv", index=False)
    costs.to_csv(output / "cost_sensitivity.csv", index=False)
    btc.to_csv(output / "btc_subgroup.csv", index=False)
    base_half.to_csv(output / "core_halfyear.csv", index=False)
    maps.to_csv(output / "chart_map.csv", index=False)

    if not args.no_plots:
        plot_halfyear(base_half, output / "core_halfyear.png")
        plot_generalization(replay, output / "walkforward_generalization.png")
        plot_validation_rank(replay, output / "validation_rank.png")
        plot_fixed_targets(fixed_targets, output / "fixed_target_sensitivity.png")

    validation = replay[replay["split"].eq("validation")].copy()
    validation["joint_metric_bp"] = validation[["net_bp", "paired_excess_bp"]].min(axis=1)
    best_rows = validation.nlargest(10, "joint_metric_bp")
    summary = {
        "experiment_id": config["experiment_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "diagnostic_complete_no_model_fit",
        "holdout_consumed": False,
        "training_eligible": False,
        "production_eligible": False,
        "source": {
            "path": str(source.relative_to(PROJECT)),
            "sha256": sha256_file(source),
            "profile": source_profile,
            **source_receipt,
        },
        "exact_profile_receipts": profile_receipts,
        "script": {"path": str(Path(__file__).resolve().relative_to(PROJECT)), "sha256": sha256_file(Path(__file__).resolve())},
        "config": {"path": str(args.config.resolve().relative_to(PROJECT)), "sha256": sha256_file(args.config)},
        "counts_by_split": {key: int(value) for key, value in frame["analysis_split"].value_counts().to_dict().items()},
        "feature_families": len(dimensions),
        "discovery_selected_families": int(selection["selected"].sum()),
        "validation_pass_count": int(validation["passes_validation_gate"].sum()),
        "best_validation_joint_rows": best_rows[["dimension", "selected_level", "n", "net_bp", "control_net_bp", "paired_excess_bp", "profit_factor", "net_fdr_q", "excess_fdr_q", "joint_metric_bp"]].to_dict("records"),
        "limitations": [
            "The current 54-symbol cache is a survivor universe, not a point-in-time listing universe.",
            "V3 reuses V2 events and controls; it audits features but does not independently reconstruct the market data.",
            "The 2026-03-01..2026-05-03 window was already opened by V2 and is descriptive only for newly selected V3 bins.",
            "Feature families are examined one at a time; interactions are intentionally not mined after the fresh window was seen.",
            "No holdout row at or after 2026-05-04 was read.",
        ],
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
