"""Audit whether six-MA detector misses come from numeric roughness or pixel visibility.

This is a read-only, pre-holdout diagnostic for the frozen Grade-A validation
population.  It reads only each manifest row's declared causal CSV prefix
through ``window_end_i``.  The six input series are exactly the renderer's
``close``-derived SMA/EMA 20/60/120; no future column or label horizon is used.

The audit separates three hypotheses that must not be bundled:

* temporal roughness: mean absolute first/second pixel differences per MA;
* semantic geometry: six-line bundle width, core length and confirmation stage;
* raster visibility: survival of the six frozen BGR colors after the source
  1280-pixel chart is resized to the frozen model's 960-pixel input width.

No model is trained, no threshold is selected, no holdout row is read, and no
production or dataset file is changed.  Association estimates use event-cluster
robust covariance because one owner event contributes several causal variants.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import mannwhitneyu

from yoyo.datasets.window_render import enrich, load_prefix, window_slice
from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.render import MA_COLORS, make_chart_transform


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl"
DEFAULT_PREDICTIONS = (
    REPO_ROOT
    / "experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-train960-v1"
    / "results/frozen_val_predictions.jsonl"
)
DEFAULT_OUTPUT = REPO_ROOT / "analysis/output/p1_six_ma_smoothness_visibility_audit_20260902.json"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
MODEL_INPUT_WIDTH = 960
COLOR_TOLERANCE = 22
COLOR_TOLERANCE_SWEEP = (10, 16, 22, 28, 36, 48)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def finite_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite value: {value!r}")
    return number


def nested(row: dict[str, Any], section: str, key: str) -> float:
    return finite_float(row[section][key])


def proximity_distance(image: np.ndarray) -> np.ndarray:
    """Return each pixel's minimum L-infinity distance to a frozen MA color."""
    pixels = image.astype(np.int16)
    minimum = np.full(image.shape[:2], 256, dtype=np.int16)
    for color in MA_COLORS.values():
        delta = np.abs(pixels - np.asarray(color, dtype=np.int16))
        np.minimum(minimum, np.max(delta, axis=2), out=minimum)
    return minimum


def image_visibility(path: Path) -> dict[str, float]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(path)
    resized_height = int(round(image.shape[0] * MODEL_INPUT_WIDTH / image.shape[1]))
    resized = cv2.resize(
        image,
        (MODEL_INPUT_WIDTH, resized_height),
        interpolation=cv2.INTER_LINEAR,
    )
    native_distance = proximity_distance(image)
    resized_distance = proximity_distance(resized)
    result: dict[str, float] = {}
    for tolerance in COLOR_TOLERANCE_SWEEP:
        native_mask = native_distance <= tolerance
        resized_mask = resized_distance <= tolerance
        native_density = float(native_mask.mean())
        resized_density = float(resized_mask.mean())
        suffix = f"_tol{tolerance}"
        result[f"ma_color_density_native{suffix}"] = native_density
        result[f"ma_color_x_coverage_native{suffix}"] = float(native_mask.any(axis=0).mean())
        result[f"ma_color_density_960{suffix}"] = resized_density
        result[f"ma_color_x_coverage_960{suffix}"] = float(resized_mask.any(axis=0).mean())
        result[f"ma_color_density_retained_ratio{suffix}"] = (
            resized_density / native_density if native_density > 0 else float("nan")
        )
    # Stable aliases keep the primary diagnostic readable while the sweep makes
    # clear that tolerance=22 was not the only color-cube definition inspected.
    for stem in (
        "ma_color_density_native",
        "ma_color_x_coverage_native",
        "ma_color_density_960",
        "ma_color_x_coverage_960",
        "ma_color_density_retained_ratio",
    ):
        result[stem] = result[f"{stem}_tol{COLOR_TOLERANCE}"]
    return result


def xywh_to_xyxy(cx: float, cy: float, width: float, height: float) -> np.ndarray:
    return np.asarray(
        [cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0],
        dtype=float,
    )


def intersection_over_union(left: np.ndarray, right: np.ndarray) -> float:
    x0 = max(float(left[0]), float(right[0]))
    y0 = max(float(left[1]), float(right[1]))
    x1 = min(float(left[2]), float(right[2]))
    y1 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, float(left[2] - left[0])) * max(0.0, float(left[3] - left[1]))
    right_area = max(0.0, float(right[2] - right[0])) * max(0.0, float(right[3] - right[1]))
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def correct_detection_hit(label_path: Path, prediction: dict[str, Any]) -> bool:
    """Apply the frozen report contract: correct class and normalized IoU >= 0.5."""
    fields = label_path.read_text(encoding="utf-8").strip().split()
    if len(fields) != 5:
        raise ValueError(f"expected one YOLO box in {label_path}, found {len(fields)} fields")
    class_id = int(fields[0])
    truth = xywh_to_xyxy(*(float(value) for value in fields[1:]))
    for candidate in prediction["predictions"]:
        if int(candidate["class_id"]) != class_id:
            continue
        predicted = np.asarray(candidate["xyxy_norm"], dtype=float)
        if intersection_over_union(truth, predicted) >= 0.5:
            return True
    return False


def ma_pixel_geometry(row: dict[str, Any], enriched: pd.DataFrame) -> dict[str, float]:
    window = window_slice(enriched, int(row["window_start_i"]), int(row["window_end_i"]))
    transform = make_chart_transform(window)
    y = np.asarray(
        [
            [transform.y_at(finite_float(window.iloc[i][column])) for column in ALL_MA_COLS]
            for i in range(len(window))
        ],
        dtype=float,
    )
    first = np.diff(y, axis=0)
    second = np.diff(y, n=2, axis=0)
    collisions = []
    for values in y:
        for left in range(len(values)):
            for right in range(left + 1, len(values)):
                collisions.append(abs(values[left] - values[right]) <= 1.0)
    return {
        "ma_mean_abs_slope_px": float(np.abs(first).mean()),
        "ma_mean_abs_curvature_px": float(np.abs(second).mean()),
        "ma_bundle_mean_spread_px": float(np.ptp(y, axis=1).mean()),
        "ma_pair_collision_le1px_rate": float(np.mean(collisions)),
    }


def rank_association(frame: pd.DataFrame, column: str) -> dict[str, float]:
    clean = frame[["hit", column]].dropna()
    hit_values = clean.loc[clean["hit"], column].to_numpy(float)
    miss_values = clean.loc[~clean["hit"], column].to_numpy(float)
    test = mannwhitneyu(hit_values, miss_values, alternative="two-sided", method="asymptotic")
    return {
        "hit_median": float(np.median(hit_values)),
        "miss_median": float(np.median(miss_values)),
        "rank_auc_for_hit": float(test.statistic / (len(hit_values) * len(miss_values))),
        "two_sided_p": float(test.pvalue),
    }


def quartile_recall(frame: pd.DataFrame, column: str) -> list[dict[str, float | int]]:
    clean = frame[["hit", column]].dropna().copy()
    # Rank first so tied metrics do not collapse qcut bins or change sample count.
    clean["quartile"] = pd.qcut(
        clean[column].rank(method="first"), 4, labels=False
    ).astype(int)
    result = []
    for quartile, group in clean.groupby("quartile", sort=True):
        result.append(
            {
                "quartile": int(quartile) + 1,
                "n": int(len(group)),
                "recall": float(group["hit"].mean()),
                "value_min": float(group[column].min()),
                "value_max": float(group[column].max()),
            }
        )
    return result


def standardize(series: pd.Series) -> pd.Series:
    std = float(series.std(ddof=0))
    if not math.isfinite(std) or std <= 0:
        raise ValueError(f"cannot standardize {series.name}: std={std}")
    return (series - float(series.mean())) / std


def clustered_logistic(
    frame: pd.DataFrame,
    primary: str,
    geometry_control: str,
) -> dict[str, Any]:
    model = frame[
        ["hit", "event_id", "core_bars", "direction", "post_bars", primary, geometry_control]
    ].dropna().copy()
    model["primary_z"] = standardize(model[primary].astype(float))
    model["geometry_z"] = standardize(model[geometry_control].astype(float))
    model["core5"] = (model["core_bars"].astype(int) == 5).astype(float)
    model["short"] = (model["direction"] == "SHORT").astype(float)
    post = pd.get_dummies(model["post_bars"].astype(int), prefix="post", dtype=float)
    if "post_2" not in post:
        raise ValueError("post=2 reference group is missing")
    post = post.drop(columns=["post_2"])
    x = pd.concat(
        [
            pd.Series(1.0, index=model.index, name="const"),
            model[["primary_z", "geometry_z", "core5", "short"]],
            post,
        ],
        axis=1,
    ).astype(float)
    fit = sm.GLM(model["hit"].astype(int), x, family=sm.families.Binomial()).fit(
        cov_type="cluster",
        cov_kwds={"groups": model["event_id"]},
        maxiter=200,
    )
    rows: dict[str, dict[str, float]] = {}
    for name in fit.params.index:
        coefficient = float(fit.params[name])
        standard_error = float(fit.bse[name])
        rows[name] = {
            "coefficient": coefficient,
            "cluster_robust_se": standard_error,
            "odds_ratio": float(math.exp(coefficient)),
            "ci95_low": float(math.exp(coefficient - 1.96 * standard_error)),
            "ci95_high": float(math.exp(coefficient + 1.96 * standard_error)),
            "two_sided_p": float(fit.pvalues[name]),
        }
    return {
        "n_rows": int(len(model)),
        "n_event_clusters": int(model["event_id"].nunique()),
        "reference_post_bars": 2,
        "primary_column": primary,
        "geometry_control_column": geometry_control,
        "converged": bool(fit.converged),
        "terms": rows,
    }


def recall_by(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows = []
    for value, group in frame.groupby(column, sort=True):
        rows.append({column: value, "n": int(len(group)), "recall": float(group["hit"].mean())})
    return rows


def source_cache(rows: Iterable[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    max_ends: dict[str, int] = defaultdict(int)
    for row in rows:
        max_ends[str(row["source_path"])] = max(
            max_ends[str(row["source_path"])], int(row["window_end_i"])
        )
    cache: dict[str, pd.DataFrame] = {}
    for relative_path, required_end in sorted(max_ends.items()):
        path = REPO_ROOT / relative_path
        cache[relative_path] = enrich(load_prefix(path, required_end))
    return cache


def run(manifest_path: Path, prediction_path: Path) -> dict[str, Any]:
    manifest = read_jsonl(manifest_path)
    predictions = read_jsonl(prediction_path)
    prediction_by_id = {str(row["dataset_sample_id"]): row for row in predictions}

    val_rows = [row for row in manifest if row.get("split") == "val"]
    positive_rows = [row for row in val_rows if row.get("sample_kind") == "positive"]
    negative_rows = [row for row in val_rows if row.get("sample_kind") == "negative"]
    if not positive_rows or not negative_rows:
        raise ValueError("expected both positive and negative val rows")
    missing = [
        str(row["dataset_sample_id"])
        for row in val_rows
        if str(row["dataset_sample_id"]) not in prediction_by_id
    ]
    if missing:
        raise ValueError(f"predictions missing {len(missing)} val rows; first={missing[0]}")

    visible_start = pd.to_datetime(
        [row["window_start_time"] for row in positive_rows], utc=True, errors="raise"
    )
    visible_end = pd.to_datetime(
        [row["window_end_time"] for row in positive_rows], utc=True, errors="raise"
    )
    if visible_end.max() >= HOLDOUT_START:
        raise RuntimeError(
            f"holdout boundary violated: max visible time {visible_end.max()} >= {HOLDOUT_START}"
        )

    sources = source_cache(positive_rows)
    dataset_root = manifest_path.parent
    records = []
    for row in positive_rows:
        sample_id = str(row["dataset_sample_id"])
        pred = prediction_by_id[sample_id]
        label_path = dataset_root / str(row["label_path"])
        record: dict[str, Any] = {
            "dataset_sample_id": sample_id,
            "event_id": str(row["event_id"]),
            "hit": correct_detection_hit(label_path, pred),
            "core_bars": int(row["core_bars"]),
            "post_bars": int(row["post_bars"]),
            "direction": str(row["direction"]),
            "ma_slope_std_atr": nested(row, "features", "ma_slope_std_atr"),
            "pairwise_order_flips": nested(row, "strict_metrics", "pairwise_order_flips"),
            "six_ma_core_mean_bandwidth_bps": nested(
                row, "strict_metrics", "six_ma_core_mean_bandwidth_bps"
            ),
            "six_ma_end_bandwidth_bps": nested(
                row, "strict_metrics", "six_ma_end_bandwidth_bps"
            ),
            "tightening_ratio_vs_pre12": nested(
                row, "strict_metrics", "tightening_ratio_vs_pre12"
            ),
            "core_progress_atr": nested(row, "features", "core_progress_atr"),
            "post2_progress_atr": nested(row, "features", "post2_progress_atr"),
            "box_width_norm": nested(row, "box", "w_norm"),
            "box_height_norm": nested(row, "box", "h_norm"),
        }
        record.update(ma_pixel_geometry(row, sources[str(row["source_path"])]))
        record.update(image_visibility(dataset_root / str(row["image_path"])))
        records.append(record)
    frame = pd.DataFrame.from_records(records)

    manifest_columns = [
        "ma_slope_std_atr",
        "pairwise_order_flips",
        "six_ma_core_mean_bandwidth_bps",
        "six_ma_end_bandwidth_bps",
        "tightening_ratio_vs_pre12",
        "core_progress_atr",
        "post2_progress_atr",
        "box_width_norm",
        "box_height_norm",
    ]
    pixel_columns = [
        "ma_mean_abs_slope_px",
        "ma_mean_abs_curvature_px",
        "ma_bundle_mean_spread_px",
        "ma_pair_collision_le1px_rate",
        "ma_color_density_native",
        "ma_color_x_coverage_native",
        "ma_color_density_960",
        "ma_color_x_coverage_960",
        "ma_color_density_retained_ratio",
    ]

    negative_predictions = [prediction_by_id[str(row["dataset_sample_id"])] for row in negative_rows]
    fired_negative = sum(int(row["boxes"]) > 0 for row in negative_predictions)
    event_hit = frame.groupby("event_id")["hit"].any()
    return {
        "scope": {
            "manifest": str(manifest_path.relative_to(REPO_ROOT)),
            "predictions": str(prediction_path.relative_to(REPO_ROOT)),
            "holdout_start_exclusive": HOLDOUT_START.isoformat(),
            "min_visible_window_start": visible_start.min().isoformat(),
            "max_visible_window_end": visible_end.max().isoformat(),
            "positive_rows": int(len(frame)),
            "negative_rows": int(len(negative_rows)),
            "event_clusters": int(frame["event_id"].nunique()),
            "source_files": int(len(sources)),
            "color_tolerance_linf": COLOR_TOLERANCE,
            "color_tolerance_sweep_linf": list(COLOR_TOLERANCE_SWEEP),
            "source_width": int(cv2.imread(str(dataset_root / positive_rows[0]["image_path"])).shape[1]),
            "model_input_width": MODEL_INPUT_WIDTH,
        },
        "frozen_model": {
            "positive_image_recall": float(frame["hit"].mean()),
            "positive_hits": int(frame["hit"].sum()),
            "positive_misses": int((~frame["hit"]).sum()),
            "event_any_variant_recall": float(event_hit.mean()),
            "event_any_variant_hits": int(event_hit.sum()),
            "event_count": int(len(event_hit)),
            "negative_images_fired": int(fired_negative),
            "negative_image_fire_rate": float(fired_negative / len(negative_rows)),
        },
        "manifest_rank_associations": {
            column: rank_association(frame, column) for column in manifest_columns
        },
        "pixel_rank_associations": {
            column: rank_association(frame, column) for column in pixel_columns
        },
        "color_tolerance_sensitivity": {
            str(tolerance): rank_association(
                frame, f"ma_color_x_coverage_960_tol{tolerance}"
            )
            for tolerance in COLOR_TOLERANCE_SWEEP
        },
        "quartile_recall": {
            column: quartile_recall(frame, column)
            for column in (
                "ma_slope_std_atr",
                "six_ma_core_mean_bandwidth_bps",
                "ma_mean_abs_curvature_px",
                "ma_bundle_mean_spread_px",
                "ma_color_x_coverage_960",
            )
        },
        "recall_by_core_bars": recall_by(frame, "core_bars"),
        "recall_by_post_bars": recall_by(frame, "post_bars"),
        "clustered_logistic": {
            "numeric_roughness": clustered_logistic(
                frame,
                primary="ma_mean_abs_curvature_px",
                geometry_control="ma_bundle_mean_spread_px",
            ),
            "pixel_visibility": clustered_logistic(
                frame,
                primary="ma_color_x_coverage_960",
                geometry_control="six_ma_end_bandwidth_bps",
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run(args.manifest.resolve(), args.predictions.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["scope"], indent=2, sort_keys=True))
    print(json.dumps(result["frozen_model"], indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
