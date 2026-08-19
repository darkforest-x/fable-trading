#!/usr/bin/env python3
"""Diagnose the frozen Local Signal V2 semantic decision boundary, read-only.

The unit of analysis is one of the 200 completed Owner review items.  The
outcome is ``owner_verdict``; every explanatory feature is rebuilt only from
OHLC and SMA/EMA 20/60/120 values at or before ``decision_bar``.  Positive
items read their source CSV only through ``win_end``.  Canary items read the
already-frozen pre-holdout snapshot only through ``decision_i``.  No future
review image, future OHLC row, holdout row, training label mutation, threshold
change, model fit, or deployment action is performed.

Feature windows are explicit:

* scale features use the full causal detector window;
* core features use ``box_start_bar..box_end_bar`` inclusive;
* release features use ``box_end_bar..decision_bar`` inclusive;
* pre-core return uses at most the three visible bars before the core;
* moving-average slopes use only the named causal endpoints.

This is exploratory boundary diagnosis, not a new classifier or a market
precision estimate.  Owner verdicts were made with a physically separate
future chart, so they are future-assisted semantic adjudications.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import cv2
import matplotlib
import numpy as np
import pandas as pd


matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import (  # noqa: E402
    IMG_HEIGHT,
    IMG_WIDTH,
    MIN_REL_SPAN,
    make_chart_transform,
)

from scripts.backtest_owner_short_gold_center_recent import (  # noqa: E402
    HOLDOUT_START,
)
from scripts.build_local_signal_v2_semantic_review import (  # noqa: E402
    DEFAULT_POSITIVE_MANIFEST,
    DEFAULT_R1_EVENTS,
    DEFAULT_R2_EVENTS,
    DEFAULT_SNAPSHOT,
    pair_canary_events,
)
from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    load_preholdout_prefix,
)


PROTOCOL = "local_signal_v2_semantic_boundary_diagnosis_v1_20260812"
DEFAULT_REVIEW_DIR = ROOT / "analysis/output/local_signal_v2_positive_semantic_review200_v2"
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_semantic_boundary_diagnosis_20260812"
DEFAULT_REPORT = ROOT / "analysis/p2_local_signal_v2_semantic_boundary_diagnosis_20260812.md"
RNG_SEED = 20260812
BOOTSTRAP_DRAWS = 3000
PERMUTATION_DRAWS = 10000

FAST20_COLS = ("sma20", "ema20")
SLOW120_COLS = ("sma120", "ema120")

# Predeclared before looking at verdict separation.  Redundant renderer-scale
# fields are retained for interpretation but only one enters the effect ranking.
FEATURES: dict[str, tuple[str, str]] = {
    "actual_span_pct": ("真实可见跨度", "%"),
    "model_vertical_occupancy_pct": ("模型纵轴占用", "%"),
    "median_candle_height_px": ("中位单根K高度", "px"),
    "core_center_pct": ("核心横向中心", "%窗宽"),
    "decision_delay_bars": ("框后确认延迟", "bars"),
    "box_height_norm_pct": ("预测框纵向高度", "%图高"),
    "core_full_spread_bps": ("核心六线密集度", "bp"),
    "decision_full_spread_bps": ("decision六线跨度", "bp"),
    "spread_release_ratio": ("六线释放倍数", "x"),
    "fast20_slope_bps_per_bar": ("20线框后斜率", "bp/bar"),
    "slow120_slope_bps_per_bar": ("120线全窗斜率", "bp/bar"),
    "close_vs_bundle_decision_bps": ("decision收盘相对六线", "bp"),
    "post_core_return_bps": ("框后至decision收益", "bp"),
    "core_return_bps": ("核心区间收益", "bp"),
    "decision_vs_core_low_bps": ("decision相对核心低点", "bp"),
    "raw_detection_count": ("事件原始重复触发", "count"),
    "windows_seen_count": ("触发窗口长度种数", "count"),
    "model_confidence": ("模型置信度", "score"),
}

RANKED_FEATURES = (
    "model_vertical_occupancy_pct",
    "median_candle_height_px",
    "core_center_pct",
    "decision_delay_bars",
    "box_height_norm_pct",
    "core_full_spread_bps",
    "decision_full_spread_bps",
    "spread_release_ratio",
    "fast20_slope_bps_per_bar",
    "slow120_slope_bps_per_bar",
    "close_vs_bundle_decision_bps",
    "post_core_return_bps",
    "core_return_bps",
    "decision_vs_core_low_bps",
    "raw_detection_count",
    "windows_seen_count",
    "model_confidence",
)

PLOT_LABELS = {
    "model_vertical_occupancy_pct": "Model vertical occupancy",
    "median_candle_height_px": "Median candle height",
    "core_center_pct": "Core horizontal center",
    "decision_delay_bars": "Decision delay",
    "box_height_norm_pct": "Predicted box height",
    "core_full_spread_bps": "Core MA-bundle spread",
    "decision_full_spread_bps": "Decision MA-bundle spread",
    "spread_release_ratio": "MA-bundle release ratio",
    "fast20_slope_bps_per_bar": "Post-core 20-MA slope",
    "slow120_slope_bps_per_bar": "Full-window 120-MA slope",
    "close_vs_bundle_decision_bps": "Decision close vs MA bundle",
    "post_core_return_bps": "Post-core return",
    "core_return_bps": "Core return",
    "decision_vs_core_low_bps": "Decision close vs core low",
    "raw_detection_count": "Repeated raw detections",
    "windows_seen_count": "Window lengths triggered",
    "model_confidence": "Model confidence",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(values: Iterable[object]) -> np.ndarray:
    result = np.asarray([float(value) for value in values], dtype=float)
    return result[np.isfinite(result)]


def safe_bps(new: float, old: float) -> float:
    return (float(new) / max(abs(float(old)), 1e-12) - 1.0) * 10000.0


def mean_at(frame: pd.DataFrame, index: int, columns: tuple[str, ...]) -> float:
    return float(pd.to_numeric(frame.loc[index, list(columns)], errors="coerce").mean())


def full_spread_bps(frame: pd.DataFrame, index: int) -> float:
    values = pd.to_numeric(frame.loc[index, list(ALL_MA_COLS)], errors="coerce").dropna()
    close = float(frame.loc[index, "close"])
    return float((values.max() - values.min()) / max(abs(close), 1e-12) * 10000.0)


def wilson95(yes: int, no: int) -> list[float | None]:
    total = yes + no
    if not total:
        return [None, None]
    rate = yes / total
    z = 1.959963984540054
    scale = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / scale
    half = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / scale
    return [center - half, center + half]


def verdict_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["owner_verdict"]) for row in rows)
    yes, no = counts["YES"], counts["NO"]
    return {
        "reviewed": len(rows),
        "YES": yes,
        "NO": no,
        "SKIP": counts["SKIP"],
        "yes_rate_excluding_skip": yes / (yes + no) if yes + no else None,
        "wilson95": wilson95(yes, no),
    }


def cliffs_delta(left: np.ndarray, right: np.ndarray) -> float:
    """Return P(left>right)-P(left<right), ignoring ties."""
    left = finite(left)
    right = finite(right)
    if not len(left) or not len(right):
        return float("nan")
    comparisons = left[:, None] - right[None, :]
    return float((np.sum(comparisons > 0) - np.sum(comparisons < 0)) / comparisons.size)


def effect_statistics(
    yes: np.ndarray,
    no: np.ndarray,
    *,
    seed: int,
) -> dict[str, Any]:
    yes = finite(yes)
    no = finite(no)
    if not len(yes) or not len(no):
        return {
            "n_yes": int(len(yes)),
            "n_no": int(len(no)),
            "median_yes": None,
            "median_no": None,
            "median_difference": None,
            "cliffs_delta": None,
            "cliffs_delta_bootstrap95": [None, None],
            "median_permutation_p": None,
        }
    rng = np.random.default_rng(seed)
    observed = float(np.median(yes) - np.median(no))
    delta = cliffs_delta(yes, no)
    boot = np.empty(BOOTSTRAP_DRAWS, dtype=float)
    for index in range(BOOTSTRAP_DRAWS):
        boot[index] = cliffs_delta(
            rng.choice(yes, size=len(yes), replace=True),
            rng.choice(no, size=len(no), replace=True),
        )
    combined = np.concatenate([yes, no])
    extreme = 0
    for _ in range(PERMUTATION_DRAWS):
        shuffled = rng.permutation(combined)
        difference = float(np.median(shuffled[: len(yes)]) - np.median(shuffled[len(yes) :]))
        extreme += int(abs(difference) >= abs(observed) - 1e-12)
    return {
        "n_yes": int(len(yes)),
        "n_no": int(len(no)),
        "median_yes": float(np.median(yes)),
        "median_no": float(np.median(no)),
        "median_difference": observed,
        "cliffs_delta": delta,
        "cliffs_delta_bootstrap95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
        "median_permutation_p": (extreme + 1) / (PERMUTATION_DRAWS + 1),
    }


def add_bh_qvalues(effects: dict[str, dict[str, Any]]) -> None:
    pairs = sorted(
        (float(value["median_permutation_p"]), key)
        for key, value in effects.items()
        if value.get("median_permutation_p") is not None
    )
    count = len(pairs)
    running = 1.0
    adjusted: dict[str, float] = {}
    for rank_from_end, (p_value, key) in enumerate(reversed(pairs), 1):
        rank = count - rank_from_end + 1
        running = min(running, p_value * count / rank)
        adjusted[key] = running
    for key, value in effects.items():
        value["bh_q"] = adjusted.get(key)


def load_original_maps() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    positives = {str(row["sample_id"]): row for row in read_jsonl(DEFAULT_POSITIVE_MANIFEST)}
    r1_rows = read_jsonl(DEFAULT_R1_EVENTS)
    r2_rows = read_jsonl(DEFAULT_R2_EVENTS)
    r1 = {str(row["event_id"]): row for row in r1_rows}
    r2 = {str(row["event_id"]): row for row in r2_rows}
    pairs, _r1_only, _r2_only = pair_canary_events(r1_rows, r2_rows, gap_bars=5)
    paired_r1_by_r2 = {str(right["event_id"]): left for left, right in pairs}
    if len(paired_r1_by_r2) != 163:
        raise ValueError(f"R1/R2 common-pair drift: {len(paired_r1_by_r2)}")
    return positives, r1, r2, paired_r1_by_r2


def box_norms(original: dict[str, Any], source_type: str) -> tuple[float, float, float]:
    if source_type == "positive_pool":
        _xc, yc, _width, height = map(float, original["yolo_box"])
        return yc, height, float(original["yolo_box"][0])
    y1, y2 = float(original["y1n"]), float(original["y2n"])
    return (y1 + y2) / 2, y2 - y1, (float(original["x1n"]) + float(original["x2n"])) / 2


def load_snapshot_prefix(path: Path, required_end: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Materialize a clean frozen snapshot only through ``required_end``.

    Required columns are ``open_time/open/high/low/close/volume``.  The caller
    supplies the causal decision index; ``nrows=required_end+1`` prevents later
    pre-holdout rows from entering memory merely because the CSV contains them.
    """
    requested_rows = required_end + 1
    raw = pd.read_csv(path, nrows=requested_rows)
    required = ("open_time", "open", "high", "low", "close", "volume")
    if not set(required).issubset(raw.columns):
        raise ValueError(f"snapshot schema mismatch: {path}")
    frame = raw[list(required)].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[list(required)].isna().any().any():
        raise ValueError(f"snapshot prefix contains invalid rows: {path}")
    if frame["open_time"].duplicated().any() or not frame["open_time"].is_monotonic_increasing:
        raise ValueError(f"snapshot prefix ordering mismatch: {path}")
    frame = frame.reset_index(drop=True)
    if len(frame) != requested_rows:
        raise ValueError(f"snapshot prefix too short: {path} {len(frame)} / {requested_rows}")
    max_time = pd.Timestamp(frame.loc[required_end, "open_time"])
    if max_time >= HOLDOUT_START:
        raise ValueError(f"snapshot prefix touches holdout: {path} {max_time}")
    try:
        source_csv = str(path.relative_to(ROOT))
    except ValueError:
        source_csv = str(path)
    return frame, {
        "source_csv": source_csv,
        "csv_rows_requested": requested_rows,
        "rows_materialized": len(frame),
        "max_materialized_time": max_time.isoformat(),
        "holdout_rows_materialized": 0,
    }


def causal_window(
    review: dict[str, Any],
    original: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    start = int(review["window_start_bar"])
    decision = int(review["decision_bar"])
    if review["source_type"] == "positive_pool":
        raw, read_audit = load_preholdout_prefix(ROOT / str(original["source_csv"]), decision)
    else:
        symbol = str(review["symbol"])
        raw, read_audit = load_snapshot_prefix(
            DEFAULT_SNAPSHOT / f"{symbol}.csv", decision
        )
    if read_audit["holdout_rows_materialized"] != 0:
        raise ValueError(f"holdout row materialized: {review['review_id']}")
    enriched = add_mas(raw)
    window = enriched.iloc[start : decision + 1].reset_index(drop=True)
    if len(window) != int(review["window_length"]):
        raise ValueError(f"causal window mismatch: {review['review_id']}")
    visible_time = pd.Timestamp(window["open_time"].iloc[-1])
    if visible_time >= HOLDOUT_START:
        raise ValueError(f"causal window touches holdout: {review['review_id']}")
    if visible_time != pd.Timestamp(review["decision_time"]):
        raise ValueError(f"decision time mismatch: {review['review_id']}")
    return window, read_audit


def extract_feature_row(
    review: dict[str, Any],
    original: dict[str, Any],
    window: pd.DataFrame,
    paired_r1: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract only causal scale, geometry, MA, and price-release features."""
    start = int(review["window_start_bar"])
    core_start = int(review["box_start_bar"]) - start
    core_end = int(review["box_end_bar"]) - start
    decision = len(window) - 1
    if not (0 <= core_start <= core_end <= decision):
        raise ValueError(f"invalid core bounds: {review['review_id']}")

    values = [window["low"], window["high"]]
    values.extend(window[column] for column in ALL_MA_COLS)
    bounds = pd.concat(values).dropna()
    actual_low, actual_high = float(bounds.min()), float(bounds.max())
    midpoint = (actual_low + actual_high) / 2
    actual_span_pct = (actual_high - actual_low) / max(abs(midpoint), 1e-12) * 100
    transform = make_chart_transform(window)
    rendered_span = transform.price_max - transform.price_min
    vertical_occupancy = (actual_high - actual_low) / max(rendered_span, 1e-12) * 100
    candle_pixels = (
        (pd.to_numeric(window["high"]) - pd.to_numeric(window["low"]))
        / max(rendered_span, 1e-12)
        * transform.plot_h
    )

    core_spreads = [full_spread_bps(window, index) for index in range(core_start, core_end + 1)]
    core_spread = float(np.median(core_spreads))
    decision_spread = full_spread_bps(window, decision)
    delay = decision - core_end
    fast20_core_end = mean_at(window, core_end, FAST20_COLS)
    fast20_decision = mean_at(window, decision, FAST20_COLS)
    slow120_start = mean_at(window, 0, SLOW120_COLS)
    slow120_decision = mean_at(window, decision, SLOW120_COLS)
    bundle_decision = mean_at(window, decision, tuple(ALL_MA_COLS))
    core_low = float(pd.to_numeric(window.loc[core_start:core_end, "low"]).min())
    yc, box_height, source_box_center = box_norms(original, str(review["source_type"]))

    pre_index = max(0, core_start - 3)
    row: dict[str, Any] = {
        "review_id": str(review["review_id"]),
        "event_id": str(review["event_id"]),
        "symbol": str(review["symbol"]),
        "source_type": str(review["source_type"]),
        "source_model": str(review["source_model"]),
        "canary_cohort": review.get("canary_cohort"),
        "owner_verdict": str(review["owner_verdict"]),
        "decision_time": str(review["decision_time"]),
        "window_length": int(review["window_length"]),
        "core_bars": core_end - core_start + 1,
        "pre_bars": core_start,
        "decision_delay_bars": delay,
        "core_center_pct": ((core_start + core_end) / 2) / max(decision, 1) * 100,
        "core_width_pct": (core_end - core_start + 1) / len(window) * 100,
        "source_box_center_x_pct": source_box_center * 100,
        "box_center_y_norm_pct": yc * 100,
        "box_height_norm_pct": box_height * 100,
        "actual_span_pct": actual_span_pct,
        "renderer_floor_active": actual_span_pct < MIN_REL_SPAN * 100,
        "renderer_floor_compression_factor": max(MIN_REL_SPAN * 100 / max(actual_span_pct, 1e-12), 1.0),
        "model_vertical_occupancy_pct": vertical_occupancy,
        "median_candle_height_px": float(np.median(candle_pixels)),
        "core_full_spread_bps": core_spread,
        "decision_full_spread_bps": decision_spread,
        "spread_release_ratio": decision_spread / max(core_spread, 1e-12),
        "fast20_slope_bps_per_bar": safe_bps(fast20_decision, fast20_core_end) / max(delay, 1),
        "slow120_slope_bps_per_bar": safe_bps(slow120_decision, slow120_start) / max(decision, 1),
        "close_vs_bundle_decision_bps": safe_bps(float(window.loc[decision, "close"]), bundle_decision),
        "pre_core_return_bps": safe_bps(float(window.loc[core_start, "close"]), float(window.loc[pre_index, "close"])),
        "core_return_bps": safe_bps(float(window.loc[core_end, "close"]), float(window.loc[core_start, "open"])),
        "post_core_return_bps": safe_bps(float(window.loc[decision, "close"]), float(window.loc[core_end, "close"])),
        "decision_vs_core_low_bps": safe_bps(float(window.loc[decision, "close"]), core_low),
        "model_confidence": float(review["model_confidence"]),
        "raw_detection_count": float(original.get("raw_detection_count", "nan")),
        "windows_seen_count": float(len(original.get("window_lengths_seen", []))) if "window_lengths_seen" in original else float("nan"),
    }
    if paired_r1 is not None:
        row.update(
            {
                "paired_r2_minus_r1_core_mid_bars": float(original["core_mid_i"]) - float(paired_r1["core_mid_i"]),
                "paired_r2_minus_r1_decision_bars": int(original["decision_i"]) - int(paired_r1["decision_i"]),
                "paired_r2_minus_r1_core_bars": int(original["predicted_core_bars"]) - int(paired_r1["predicted_core_bars"]),
                "paired_r2_minus_r1_delay_bars": int(original["decision_delay_bars"]) - int(paired_r1["decision_delay_bars"]),
                "paired_r2_minus_r1_peak_confidence": float(original["event_conf_max"]) - float(paired_r1["event_conf_max"]),
                "paired_r2_minus_r1_raw_detection_count": int(original["raw_detection_count"]) - int(paired_r1["raw_detection_count"]),
            }
        )
    return row


def bucket_metrics(
    rows: list[dict[str, Any]],
    field: str,
    bins: tuple[tuple[str, float, float], ...],
) -> dict[str, Any]:
    return {
        label: verdict_metrics(
            [row for row in rows if low <= float(row[field]) < high]
        )
        for label, low, high in bins
    }


def median_table(rows: list[dict[str, Any]], group_field: str, fields: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for group in sorted({str(row.get(group_field)) for row in rows}):
        cohort = [row for row in rows if str(row.get(group_field)) == group]
        result[group] = {
            "n": len(cohort),
            **{
                field: (float(np.median(finite(row[field] for row in cohort))) if len(finite(row[field] for row in cohort)) else None)
                for field in fields
            },
        }
    return result


def build_effects(rows: list[dict[str, Any]]) -> dict[str, Any]:
    yes = [row for row in rows if row["owner_verdict"] == "YES"]
    no = [row for row in rows if row["owner_verdict"] == "NO"]
    effects: dict[str, Any] = {}
    for index, field in enumerate(RANKED_FEATURES):
        effects[field] = {
            "label": FEATURES[field][0],
            "unit": FEATURES[field][1],
            **effect_statistics(
                finite(row[field] for row in yes),
                finite(row[field] for row in no),
                seed=RNG_SEED + index * 1009,
            ),
        }
    add_bh_qvalues(effects)
    return effects


def plot_cohort_rates(summary: dict[str, Any], output: Path) -> None:
    labels = ["Common retained", "R2 new", "R1 suppressed"]
    keys = ["common_retained", "r2_new", "r1_suppressed"]
    metrics = [summary["canary_by_cohort"][key] for key in keys]
    rates = [float(item["yes_rate_excluding_skip"] or 0) * 100 for item in metrics]
    lower = [max(0.0, (rate / 100 - float(item["wilson95"][0])) * 100) for rate, item in zip(rates, metrics)]
    upper = [max(0.0, (float(item["wilson95"][1]) - rate / 100) * 100) for rate, item in zip(rates, metrics)]
    fig, ax = plt.subplots(figsize=(8.8, 4.8), dpi=150)
    bars = ax.bar(labels, rates, color=["#2463a6", "#9fb8d0", "#d69b2d"], edgecolor="#263746")
    ax.errorbar(range(3), rates, yerr=[lower, upper], fmt="none", ecolor="#263746", capsize=5, lw=1.5)
    for bar, rate, item in zip(bars, rates, metrics):
        ax.text(bar.get_x() + bar.get_width() / 2, rate + 2.0, f"{item['YES']}/{item['YES'] + item['NO']} = {rate:.0f}%", ha="center", va="bottom", fontsize=10)
    highest_interval = max(
        float(item["wilson95"][1] or 0) * 100 for item in metrics
    )
    ax.set_ylim(0, max(35, highest_interval + 5))
    ax.set_ylabel("Owner YES rate (%)")
    ax.set_title("Canary semantic acceptance by R1/R2 cohort")
    ax.grid(axis="y", color="#d9e0e5", lw=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_span_rates(summary: dict[str, Any], output: Path) -> None:
    keys = ["lt1", "1to2", "2to4", "ge4"]
    labels = ["<1%", "1–2%", "2–4%", "≥4%"]
    metrics = [summary["canary_by_actual_span_pct"][key] for key in keys]
    rates = [float(item["yes_rate_excluding_skip"] or 0) * 100 for item in metrics]
    fig, ax = plt.subplots(figsize=(8.8, 4.8), dpi=150)
    bars = ax.bar(labels, rates, color="#3f76a8", edgecolor="#263746")
    for bar, rate, item in zip(bars, rates, metrics):
        ax.text(bar.get_x() + bar.get_width() / 2, rate + 1.2, f"{item['YES']}/{item['YES'] + item['NO']}", ha="center", va="bottom", fontsize=10)
    ax.set_ylim(0, max(35, max(rates) + 10))
    ax.set_ylabel("Owner YES rate (%)")
    ax.set_xlabel("Actual causal chart span")
    ax.set_title("Canary acceptance by causal price span")
    ax.grid(axis="y", color="#d9e0e5", lw=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_effects(effects: dict[str, Any], output: Path) -> None:
    usable = [
        (key, value)
        for key, value in effects.items()
        if value.get("cliffs_delta") is not None
    ]
    usable.sort(key=lambda item: abs(float(item[1]["cliffs_delta"])), reverse=True)
    usable = usable[:10]
    usable.reverse()
    labels = [PLOT_LABELS[key] for key, _value in usable]
    points = np.asarray([float(value["cliffs_delta"]) for _key, value in usable])
    lows = np.asarray([float(value["cliffs_delta_bootstrap95"][0]) for _key, value in usable])
    highs = np.asarray([float(value["cliffs_delta_bootstrap95"][1]) for _key, value in usable])
    colors = ["#2463a6" if point >= 0 else "#9aa7b2" for point in points]
    fig, ax = plt.subplots(figsize=(9.6, 6.6), dpi=150)
    y = np.arange(len(usable))
    ax.hlines(y, lows, highs, color="#536473", lw=2)
    ax.scatter(points, y, s=64, c=colors, edgecolors="#263746", zorder=3)
    ax.axvline(0, color="#263746", ls="--", lw=1)
    ax.set_yticks(y, labels)
    ax.set_xlim(-1, 1)
    ax.set_xlabel("Cliff's delta (positive = higher among Owner YES)")
    ax.set_title("Univariate YES/NO effect sizes in the Canary sample")
    ax.grid(axis="x", color="#d9e0e5", lw=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def diagnose(review_dir: Path, output: Path) -> dict[str, Any]:
    joined_path = review_dir / "owner_review_joined.jsonl"
    review_rows = read_jsonl(joined_path)
    if len(review_rows) != 200 or len({row["review_id"] for row in review_rows}) != 200:
        raise ValueError("expected exactly 200 unique frozen review rows")
    if any(row["owner_verdict"] not in {"YES", "NO", "SKIP"} for row in review_rows):
        raise ValueError("invalid Owner verdict")
    if any(int(row["visible_end_bar"]) != int(row["decision_bar"]) for row in review_rows):
        raise ValueError("non-causal review row")

    positives, r1, r2, paired_r1_by_r2 = load_original_maps()
    extracted: list[dict[str, Any]] = []
    read_audits: list[dict[str, Any]] = []
    image_sizes: Counter[str] = Counter()
    for review in review_rows:
        if review["source_type"] == "positive_pool":
            sample_id = str(review["event_id"]).removeprefix("positive:")
            original = positives.get(sample_id)
        else:
            source_map = r1 if review["source_model"] == "R1" else r2
            original = source_map.get(str(review["event_id"]))
        if original is None:
            raise ValueError(f"source lineage missing: {review['review_id']}")
        window, read_audit = causal_window(review, original)
        paired = paired_r1_by_r2.get(str(review["event_id"])) if review.get("canary_cohort") == "common_retained" else None
        extracted.append(extract_feature_row(review, original, window, paired))
        read_audits.append({"review_id": review["review_id"], **read_audit})

        image_path = ROOT / str(review["model_input_path"])
        if not image_path.is_file() or sha256_file(image_path) != str(review["model_input_sha256"]):
            raise ValueError(f"model input lineage mismatch: {review['review_id']}")
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"model input unreadable: {review['review_id']}")
        image_sizes[f"{image.shape[1]}x{image.shape[0]}"] += 1

    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "boundary_features.jsonl", extracted)
    write_jsonl(output / "source_read_audit.jsonl", read_audits)
    canary = [row for row in extracted if row["source_type"] == "canary_candidate"]
    positive = [row for row in extracted if row["source_type"] == "positive_pool"]
    span_bins = (
        ("lt1", float("-inf"), 1.0),
        ("1to2", 1.0, 2.0),
        ("2to4", 2.0, 4.0),
        ("ge4", 4.0, float("inf")),
    )
    occupancy_bins = (
        ("lt15", float("-inf"), 15.0),
        ("15to30", 15.0, 30.0),
        ("30to60", 30.0, 60.0),
        ("ge60", 60.0, float("inf")),
    )
    delay_bins = (
        ("0to2", float("-inf"), 3.0),
        ("3", 3.0, 4.0),
        ("4", 4.0, 5.0),
        ("5", 5.0, 6.0),
        ("gt5", 6.0, float("inf")),
    )
    common = [row for row in canary if row["canary_cohort"] == "common_retained"]
    pair_fields = (
        "paired_r2_minus_r1_core_mid_bars",
        "paired_r2_minus_r1_decision_bars",
        "paired_r2_minus_r1_core_bars",
        "paired_r2_minus_r1_delay_bars",
        "paired_r2_minus_r1_peak_confidence",
        "paired_r2_minus_r1_raw_detection_count",
    )
    result: dict[str, Any] = {
        "protocol": PROTOCOL,
        "source_review_protocol": json.loads((review_dir / "summary.json").read_text(encoding="utf-8"))["protocol"],
        "data_quality": {
            "review_rows": len(review_rows),
            "unique_review_ids": len({row["review_id"] for row in review_rows}),
            "source_lineage_joined": len(extracted),
            "model_input_sha_verified": len(extracted),
            "model_input_sizes": dict(image_sizes),
            "causal_visible_end_equals_decision": True,
            "max_materialized_time": max(str(row["max_materialized_time"]) for row in read_audits),
            "holdout_rows_materialized": sum(int(row["holdout_rows_materialized"]) for row in read_audits),
            "future_review_files_read": 0,
            "future_ohlc_rows_read": 0,
            "joined_sha256": sha256_file(joined_path),
            "feature_rows_sha256": sha256_file(output / "boundary_features.jsonl"),
            "renderer_source_sha256": sha256_file(YOYO_REPO / "yoyo/layers/l1_detection/render.py"),
            "renderer_min_relative_span": MIN_REL_SPAN,
        },
        "population": {
            "positive_pool": verdict_metrics(positive),
            "canary_candidate": verdict_metrics(canary),
        },
        "renderer_scale": {
            "positive_floor_active": verdict_metrics([row for row in positive if row["renderer_floor_active"]]),
            "positive_floor_inactive": verdict_metrics([row for row in positive if not row["renderer_floor_active"]]),
            "canary_floor_active": verdict_metrics([row for row in canary if row["renderer_floor_active"]]),
            "canary_floor_inactive": verdict_metrics([row for row in canary if not row["renderer_floor_active"]]),
            "canary_median_vertical_occupancy_pct": float(np.median([row["model_vertical_occupancy_pct"] for row in canary])),
            "canary_median_candle_height_px": float(np.median([row["median_candle_height_px"] for row in canary])),
            "positive_median_vertical_occupancy_pct": float(np.median([row["model_vertical_occupancy_pct"] for row in positive])),
            "positive_median_candle_height_px": float(np.median([row["median_candle_height_px"] for row in positive])),
        },
        "canary_by_actual_span_pct": bucket_metrics(canary, "actual_span_pct", span_bins),
        "canary_by_vertical_occupancy_pct": bucket_metrics(canary, "model_vertical_occupancy_pct", occupancy_bins),
        "canary_by_decision_delay": bucket_metrics(canary, "decision_delay_bars", delay_bins),
        "canary_by_cohort": {
            cohort: verdict_metrics([row for row in canary if row["canary_cohort"] == cohort])
            for cohort in ("common_retained", "r2_new", "r1_suppressed")
        },
        "canary_cohort_medians": median_table(
            canary,
            "canary_cohort",
            (
                "model_vertical_occupancy_pct",
                "median_candle_height_px",
                "decision_delay_bars",
                "core_full_spread_bps",
                "spread_release_ratio",
                "post_core_return_bps",
                "raw_detection_count",
            ),
        ),
        "positive_vs_canary_medians": {
            scope: {
                field: float(np.median(finite(row[field] for row in cohort)))
                for field in (
                    "actual_span_pct",
                    "model_vertical_occupancy_pct",
                    "median_candle_height_px",
                    "core_full_spread_bps",
                    "decision_full_spread_bps",
                    "spread_release_ratio",
                    "fast20_slope_bps_per_bar",
                    "close_vs_bundle_decision_bps",
                    "core_return_bps",
                    "post_core_return_bps",
                    "box_height_norm_pct",
                    "decision_delay_bars",
                )
            }
            for scope, cohort in {
                "positive_all": positive,
                "positive_yes": [row for row in positive if row["owner_verdict"] == "YES"],
                "canary_yes": [row for row in canary if row["owner_verdict"] == "YES"],
                "canary_no": [row for row in canary if row["owner_verdict"] == "NO"],
            }.items()
        },
        "common_retained_paired_r2_minus_r1_medians": median_table(common, "owner_verdict", pair_fields),
        "canary_univariate_effects": build_effects(canary),
        "positive_univariate_effects": build_effects(positive),
        "interpretation_limits": {
            "future_assisted_owner_verdicts": True,
            "stratified_review_sample": True,
            "not_market_precision_estimate": True,
            "univariate_not_causal": True,
            "classifier_trained": False,
            "threshold_changed": False,
            "labels_changed": False,
            "holdout_read": False,
        },
    }
    (output / "boundary_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    causality = {
        "protocol": PROTOCOL,
        "pass": result["data_quality"]["holdout_rows_materialized"] == 0
        and result["data_quality"]["future_review_files_read"] == 0
        and result["data_quality"]["future_ohlc_rows_read"] == 0
        and result["data_quality"]["causal_visible_end_equals_decision"],
        **result["data_quality"],
    }
    (output / "causality_audit.json").write_text(
        json.dumps(causality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    plot_cohort_rates(result, output / "canary_cohort_yes_rate.png")
    plot_span_rates(result, output / "canary_span_yes_rate.png")
    plot_effects(result["canary_univariate_effects"], output / "canary_feature_effects.png")
    result["artifacts"] = {
        name: {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
        for name, path in {
            "features": output / "boundary_features.jsonl",
            "source_read_audit": output / "source_read_audit.jsonl",
            "causality_audit": output / "causality_audit.json",
            "cohort_chart": output / "canary_cohort_yes_rate.png",
            "span_chart": output / "canary_span_yes_rate.png",
            "effect_chart": output / "canary_feature_effects.png",
        }.items()
    }
    (output / "boundary_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = diagnose(args.review_dir, args.out)
    print(
        json.dumps(
            {
                "protocol": result["protocol"],
                "population": result["population"],
                "renderer_scale": result["renderer_scale"],
                "causality_pass": json.loads((args.out / "causality_audit.json").read_text())["pass"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
