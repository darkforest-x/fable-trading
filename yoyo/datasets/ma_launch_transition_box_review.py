"""Build an Owner review pack for the two-span MA-launch box protocol.

The rejected v1 review used one span for both axes: it searched for a 4--7 bar
six-MA knot and boxed only the MA envelope.  The Owner's three 2026-08-27 red
reference boxes show a different object.  A compact pre-release core supplies
the vertical price zone, while the horizontal extent continues through three
early confirmation bars.  Large confirmation candles may therefore leave the
box vertically without changing the price-zone label.

For source anchor ``t`` this review fixes the core end at ``t-3``.  Candidate
core lengths 4--7 yield total horizontal spans of 7--10 bars through ``t``.
Vertical bounds are the union of core candle wicks and SMA/EMA 20/60/120, plus
4% symmetric price padding.  Confirmation OHLC and MAs never set vertical
bounds.  The exact 50 pre-holdout identities, W20 crops and clean-image hashes
come from the hash-pinned rejected v1 Review50 so box semantics are the only
review-surface change.

This module writes review evidence only.  It cannot create YOLO labels,
training data, weights, eligibility, ACTIVE/frozen state, forward state,
deployment state or order state.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    read_preholdout_prefix,
    sha256_file,
)
from yoyo.datasets.gold_box import EXTREME_WICK_RATIO, extreme_wick
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-ma-launch-transition-box-review50-v2"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
CORE_LENGTHS = (4, 5, 6, 7)
CORE_END_OFFSET = -3
CONFIRMATION_BARS = 3
PAD_FRACTION = 0.04
SOURCE_WIDTH = 1280
SOURCE_HEIGHT = 742
BASELINE_IMGSZ = 960
SOURCE_TO_MODEL_SCALE = BASELINE_IMGSZ / SOURCE_WIDTH

RED = (45, 45, 232)
CYAN = (214, 176, 24)
GOLD = (0, 165, 255)
INK = (35, 42, 48)
LIGHT = (247, 249, 251)


class TransitionBoxReviewError(ValueError):
    """Raised when frozen identity, chronology or two-span geometry drifts."""


@dataclass(frozen=True)
class TransitionSpan:
    """Core price span plus horizontal confirmation extent in local indices."""

    core_start_local: int
    core_end_local: int
    confirmation_end_local: int
    core_start_offset: int
    core_end_offset: int
    confirmation_end_offset: int
    core_len: int
    total_box_bars: int


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_builder_committed(paths: Sequence[Path]) -> str:
    """Require behavior and preregistration to land on main before building."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("transition-box review builder must run on main")
    relatives = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"transition-box builder inputs are not committed:\n{dirty}")
    commits = [git_output("log", "-1", "--format=%H", "--", relative) for relative in relatives]
    if any(len(commit) != 40 for commit in commits):
        raise RuntimeError("could not resolve transition-box builder/config commits")
    return git_output("rev-parse", "HEAD")


def transition_span(anchor_local: int, core_len: int, window_len: int) -> TransitionSpan:
    """Return ``core(t-L-2..t-3)`` plus horizontal confirmation through ``t``."""

    if core_len not in CORE_LENGTHS:
        raise TransitionBoxReviewError(f"unsupported core length: {core_len}")
    core_end = anchor_local + CORE_END_OFFSET
    core_start = core_end - core_len + 1
    confirmation_end = anchor_local
    if not 0 <= core_start <= core_end < confirmation_end < window_len:
        raise TransitionBoxReviewError("two-span geometry falls outside W20")
    total = confirmation_end - core_start + 1
    if total != core_len + CONFIRMATION_BARS:
        raise TransitionBoxReviewError("horizontal confirmation count drifted")
    return TransitionSpan(
        core_start_local=core_start,
        core_end_local=core_end,
        confirmation_end_local=confirmation_end,
        core_start_offset=core_start - anchor_local,
        core_end_offset=core_end - anchor_local,
        confirmation_end_offset=0,
        core_len=core_len,
        total_box_bars=total,
    )


def _clip_box(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    x0 = float(np.clip(x0, 0, width - 1))
    x1 = float(np.clip(x1, 1, width))
    y0 = float(np.clip(y0, 0, height - 1))
    y1 = float(np.clip(y1, 1, height))
    if x1 <= x0 or y1 <= y0:
        raise TransitionBoxReviewError("degenerate transition box")
    return x0, y0, x1, y1


def transition_box_for_span(
    transform: Any,
    window: pd.DataFrame,
    span: TransitionSpan,
    *,
    pad_fraction: float = PAD_FRACTION,
    allow_extreme_wick: bool = False,
) -> dict[str, Any]:
    """Return the two-span box; confirmation values never affect vertical price bounds."""

    if pad_fraction < 0.0:
        raise TransitionBoxReviewError("pad fraction must be non-negative")
    if extreme_wick(window, span.core_start_local, span.core_end_local) and not allow_extreme_wick:
        raise TransitionBoxReviewError(
            f"core wick ratio >= {EXTREME_WICK_RATIO:g}; mark IGNORE instead of silently clipping"
        )
    core = window.iloc[span.core_start_local : span.core_end_local + 1]
    ma_values = core.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    wick_highs = core["high"].to_numpy(dtype=float)
    wick_lows = core["low"].to_numpy(dtype=float)
    values = np.concatenate((wick_highs, wick_lows, ma_values.ravel()))
    if not np.isfinite(values).all():
        raise TransitionBoxReviewError("non-finite core wick or six-MA value")
    raw_high = float(values.max())
    raw_low = float(values.min())
    if raw_high <= raw_low:
        raise TransitionBoxReviewError("core price zone has no vertical extent")
    pad = (raw_high - raw_low) * float(pad_fraction)
    box_high = raw_high + pad
    box_low = raw_low - pad
    x0 = transform.x_at(span.core_start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(span.confirmation_end_local) + transform.candle_half_w + 2
    y0 = transform.y_at(box_high)
    y1 = transform.y_at(box_low)
    x0, y0, x1, y1 = _clip_box(
        min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1),
        width=transform.width,
        height=transform.height,
    )
    core_pixels = [transform.y_at(float(value)) for value in values]
    contains_core = min(core_pixels) >= y0 - 1e-6 and max(core_pixels) <= y1 + 1e-6
    confirmation = window.iloc[span.core_end_local + 1 : span.confirmation_end_local + 1]
    confirmation_extremes = np.concatenate(
        (confirmation["high"].to_numpy(dtype=float), confirmation["low"].to_numpy(dtype=float))
    )
    confirmation_outside = int(np.sum((confirmation_extremes > box_high) | (confirmation_extremes < box_low)))
    source_width = x1 - x0
    source_height = y1 - y0
    return {
        "x0": float(x0),
        "y0": float(y0),
        "x1": float(x1),
        "y1": float(y1),
        "cx_norm": float((x0 + x1) / 2.0 / transform.width),
        "cy_norm": float((y0 + y1) / 2.0 / transform.height),
        "w_norm": float(source_width / transform.width),
        "h_norm": float(source_height / transform.height),
        "source_width_px": float(source_width),
        "source_height_px": float(source_height),
        "baseline_model_width_px": float(source_width * SOURCE_TO_MODEL_SCALE),
        "baseline_model_height_px": float(source_height * SOURCE_TO_MODEL_SCALE),
        "core_price_high_raw": raw_high,
        "core_price_low_raw": raw_low,
        "box_price_high": box_high,
        "box_price_low": box_low,
        "pad_fraction": float(pad_fraction),
        "contains_core_wicks_and_six_mas": bool(contains_core),
        "confirmation_bars_in_horizontal_extent": CONFIRMATION_BARS,
        "confirmation_extremes_outside_vertical_zone": confirmation_outside,
        "confirmation_values_used_for_vertical_bounds": False,
        "center_fraction": float((x0 + x1) / 2.0 / transform.width),
    }


def _pixel_box(box: Mapping[str, Any]) -> tuple[int, int, int, int]:
    return tuple(int(round(float(box[key]))) for key in ("x0", "y0", "x1", "y1"))  # type: ignore[return-value]


def _draw_dashed_box(image: np.ndarray, box: Mapping[str, Any], color: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = _pixel_box(box)
    dash = 13
    for x in range(x0, x1 + 1, dash * 2):
        cv2.line(image, (x, y0), (min(x + dash, x1), y0), color, 3, cv2.LINE_AA)
        cv2.line(image, (x, y1), (min(x + dash, x1), y1), color, 3, cv2.LINE_AA)
    for y in range(y0, y1 + 1, dash * 2):
        cv2.line(image, (x0, y), (x0, min(y + dash, y1)), color, 3, cv2.LINE_AA)
        cv2.line(image, (x1, y), (x1, min(y + dash, y1)), color, 3, cv2.LINE_AA)


def _draw_transition_box(
    image: np.ndarray,
    box: Mapping[str, Any],
    *,
    core_end_x: int,
    label: str,
) -> None:
    x0, y0, x1, y1 = _pixel_box(box)
    cv2.rectangle(image, (x0, y0), (x1, y1), RED, 4, cv2.LINE_AA)
    cv2.line(image, (core_end_x, y0), (core_end_x, y1), GOLD, 3, cv2.LINE_AA)
    (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
    ty = max(th + 6, y0 - 6)
    cv2.rectangle(image, (x0, ty - th - 5), (min(image.shape[1] - 1, x0 + tw + 8), ty + baseline + 2), (255, 255, 255), -1)
    cv2.putText(image, label, (x0 + 3, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.58, RED, 2, cv2.LINE_AA)


def _panel(image: np.ndarray, title: str, subtitle: str, width: int = 470) -> np.ndarray:
    scale = width / image.shape[1]
    resized = cv2.resize(image, (width, int(round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    header = np.full((64, width, 3), LIGHT, dtype=np.uint8)
    cv2.putText(header, title, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.57, INK, 2, cv2.LINE_AA)
    cv2.putText(header, subtitle, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.41, (78, 86, 94), 1, cv2.LINE_AA)
    return np.vstack((header, resized))


def _encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise OSError("OpenCV failed to encode PNG")
    return encoded.tobytes()


def _describe(values: Sequence[float]) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    return {
        "min": float(data.min()),
        "p10": float(np.quantile(data, 0.10)),
        "p25": float(np.quantile(data, 0.25)),
        "median": float(np.median(data)),
        "p75": float(np.quantile(data, 0.75)),
        "p90": float(np.quantile(data, 0.90)),
        "max": float(data.max()),
        "mean": float(data.mean()),
    }


def _relative_asset(path: Path, final_dir: Path) -> str:
    return str((final_dir / path.relative_to(final_dir.with_name(f"{final_dir.name}.building"))).relative_to(ROOT))


def _old_box(row: Mapping[str, Any]) -> dict[str, Any]:
    box = row.get("variants", {}).get("L5_min24", {}).get("box")
    if not isinstance(box, dict):
        raise TransitionBoxReviewError(f"v1 row lacks frozen L5/min24 box: {row.get('sample_id')}")
    return dict(box)


def build_metric(row: Mapping[str, Any], window: pd.DataFrame) -> tuple[dict[str, Any], Any]:
    """Build all four v2 candidates on one exact frozen W20 chart."""

    if len(window) != 20:
        raise TransitionBoxReviewError("frozen review window is not W20")
    transform = make_chart_transform(window)
    anchor_local = int(row["source_anchor_i"]) - int(row["window_start_i"])
    variants: dict[str, Any] = {}
    for core_len in CORE_LENGTHS:
        span = transition_span(anchor_local, core_len, len(window))
        box = transition_box_for_span(transform, window, span)
        variants[f"L{core_len}_C3"] = {"span": span.__dict__, "box": box}
    return {
        "sample_id": str(row["sample_id"]),
        "symbol": str(row["symbol"]),
        "direction": str(row["direction"]),
        "split": str(row["split"]),
        "anchor_time": str(row["anchor_time"]),
        "source_path": str(row["source_path"]),
        "source_anchor_i": int(row["source_anchor_i"]),
        "window_start_i": int(row["window_start_i"]),
        "window_end_i": int(row["window_end_i"]),
        "window_end_offset": int(row["window_end_offset"]),
        "window_bars": 20,
        "time_bin": int(row["time_bin"]),
        "old_rejected_l5_min24_box": _old_box(row),
        "old_rejected_image_sha256": str(row["image_sha256"]),
        "variants": variants,
        "sample_owner_confirmed": False,
        "training_eligible": False,
        "production_eligible": False,
    }, transform


def render_assets(
    metric: Mapping[str, Any],
    window: pd.DataFrame,
    image_dir: Path,
    *,
    final_dir: Path,
) -> dict[str, Any]:
    """Write the clean parity image and one four-variant comparison sheet."""

    raw, transform = render_chart(window, out_path=None)
    raw_bytes = _encode_png(raw)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if raw_sha != metric["old_rejected_image_sha256"]:
        raise TransitionBoxReviewError(f"clean W20 parity drift: {metric['sample_id']}")
    raw_path = image_dir / f"{metric['sample_id']}.png"
    raw_path.write_bytes(raw_bytes)
    panels: list[np.ndarray] = []
    old = metric["old_rejected_l5_min24_box"]
    for core_len in CORE_LENGTHS:
        variant = metric["variants"][f"L{core_len}_C3"]
        canvas = raw.copy()
        _draw_dashed_box(canvas, old, CYAN)
        _draw_transition_box(
            canvas,
            variant["box"],
            core_end_x=transform.x_at(int(variant["span"]["core_end_local"])),
            label=f"core{core_len}+confirm3",
        )
        panels.append(
            _panel(
                canvas,
                f"Core {core_len} + confirmation 3 = {core_len + 3} bars",
                (
                    f"RED=new | CYAN dashed=rejected | model "
                    f"{variant['box']['baseline_model_width_px']:.0f}x"
                    f"{variant['box']['baseline_model_height_px']:.0f}px"
                ),
            )
        )
    comparison = np.hstack(panels)
    comparison_path = image_dir / f"{metric['sample_id']}_comparison.png"
    comparison_bytes = _encode_png(comparison)
    comparison_path.write_bytes(comparison_bytes)
    return {
        "image_path": _relative_asset(raw_path, final_dir),
        "image_sha256": raw_sha,
        "comparison_path": _relative_asset(comparison_path, final_dir),
        "comparison_sha256": hashlib.sha256(comparison_bytes).hexdigest(),
    }


def _box_style(box: Mapping[str, Any]) -> str:
    return (
        f"left:{100 * float(box['x0']) / SOURCE_WIDTH:.6f}%;"
        f"top:{100 * float(box['y0']) / SOURCE_HEIGHT:.6f}%;"
        f"width:{100 * (float(box['x1']) - float(box['x0'])) / SOURCE_WIDTH:.6f}%;"
        f"height:{100 * (float(box['y1']) - float(box['y0'])) / SOURCE_HEIGHT:.6f}%"
    )


def _core_divider_style(row: Mapping[str, Any], core_len: int) -> str:
    variant = row["variants"][f"L{core_len}_C3"]
    span = variant["span"]
    box = variant["box"]
    step = (float(box["x1"]) - float(box["x0"])) / max(int(span["total_box_bars"]), 1)
    divider = float(box["x1"]) - CONFIRMATION_BARS * step
    return f"left:{100 * divider / SOURCE_WIDTH:.6f}%;top:{100 * float(box['y0']) / SOURCE_HEIGHT:.6f}%;height:{100 * (float(box['y1']) - float(box['y0'])) / SOURCE_HEIGHT:.6f}%"


def build_review_html(rows: Sequence[Mapping[str, Any]], prereg_sha: str, manifest_sha: str) -> str:
    """Build an offline one-sample review page with zero preselected answers."""

    items: list[dict[str, Any]] = []
    html_parent = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results" / "public"
    for order, row in enumerate(rows, start=1):
        image = Path(os.path.relpath(ROOT / str(row["image_path"]), html_parent)).as_posix()
        items.append(
            {
                "sample_id": row["sample_id"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "split": row["split"],
                "anchor_time": row["anchor_time"],
                "time_bin": row["time_bin"],
                "source_order": order,
                "image": image,
                "image_sha256": row["image_sha256"],
                "old_style": _box_style(row["old_rejected_l5_min24_box"]),
                "variants": {
                    str(length): {
                        "style": _box_style(row["variants"][f"L{length}_C3"]["box"]),
                        "divider_style": _core_divider_style(row, length),
                        "height": row["variants"][f"L{length}_C3"]["box"]["baseline_model_height_px"],
                        "outside": row["variants"][f"L{length}_C3"]["box"]["confirmation_extremes_outside_vertical_zone"],
                    }
                    for length in CORE_LENGTHS
                },
            }
        )
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    config = json.dumps(
        {"experiment_id": EXPERIMENT_ID, "prereg_sha256": prereg_sha, "review_manifest_sha256": manifest_sha},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return REVIEW_HTML.replace("__ITEMS__", payload).replace("__CONFIG__", config).replace("__TOTAL__", str(len(items)))


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize only geometry; this protocol review has no economic metric."""

    result: dict[str, Any] = {
        "rows": len(rows),
        "direction_counts": dict(sorted(Counter(str(row["direction"]) for row in rows).items())),
        "split_counts": dict(sorted(Counter(str(row["split"]) for row in rows).items())),
        "time_range": [min(str(row["anchor_time"]) for row in rows), max(str(row["anchor_time"]) for row in rows)],
        "owner_answers_preselected": 0,
        "holdout_ohlcv_rows_materialized": 0,
        "yolo_labels_written": 0,
        "training_runs_started": 0,
    }
    for length in CORE_LENGTHS:
        boxes = [row["variants"][f"L{length}_C3"]["box"] for row in rows]
        result[f"core{length}_confirm3"] = {
            "total_box_bars": length + CONFIRMATION_BARS,
            "model_width_px": _describe([float(box["baseline_model_width_px"]) for box in boxes]),
            "model_height_px": _describe([float(box["baseline_model_height_px"]) for box in boxes]),
            "confirmation_extremes_outside_vertical_zone_total": sum(
                int(box["confirmation_extremes_outside_vertical_zone"]) for box in boxes
            ),
            "all_core_wicks_and_six_mas_contained": sum(bool(box["contains_core_wicks_and_six_mas"]) for box in boxes),
        }
    return result


def validate_owner_review_payload(
    payload: Mapping[str, Any],
    review_rows: Sequence[Mapping[str, Any]],
    *,
    prereg_sha256: str,
    review_manifest_sha256: str,
) -> dict[str, Any]:
    """Validate a complete review export without converting it into labels."""

    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise TransitionBoxReviewError("owner review experiment id drifted")
    if payload.get("prereg_sha256") != prereg_sha256 or payload.get("review_manifest_sha256") != review_manifest_sha256:
        raise TransitionBoxReviewError("owner review frozen hash drifted")
    expected = {str(row["sample_id"]): row for row in review_rows}
    answers = list(payload.get("answers") or [])
    if len(expected) != len(review_rows) or len(answers) != len(expected):
        raise TransitionBoxReviewError("owner review is incomplete")
    if {str(answer.get("sample_id")) for answer in answers} != set(expected):
        raise TransitionBoxReviewError("owner review identity set drifted")
    counts: Counter[str] = Counter()
    lengths: Counter[int] = Counter()
    for answer in answers:
        row = expected[str(answer["sample_id"])]
        for field in ("symbol", "direction", "anchor_time", "image_sha256"):
            if answer.get(field) != row.get(field):
                raise TransitionBoxReviewError(f"owner review identity drift: {field}")
        decision = str(answer.get("decision"))
        if decision not in {"ACCEPT", "ADJUST", "UNCERTAIN"}:
            raise TransitionBoxReviewError("invalid owner decision")
        preferred = answer.get("preferred_core_len")
        if decision == "ACCEPT" and preferred is None:
            raise TransitionBoxReviewError("accepted row lacks core length")
        if preferred is not None and int(preferred) not in CORE_LENGTHS:
            raise TransitionBoxReviewError("invalid preferred core length")
        counts[decision] += 1
        if preferred is not None:
            lengths[int(preferred)] += 1
    if payload.get("complete") is not True or int(payload.get("n_answered", -1)) != len(expected):
        raise TransitionBoxReviewError("owner export is not declared complete")
    return {
        "experiment_id": EXPERIMENT_ID,
        "status": "owner_review_complete_pending_per_sample_gold_boundary",
        "n_reviewed": len(answers),
        "decision_counts": dict(sorted(counts.items())),
        "preferred_core_length_counts": {str(key): value for key, value in sorted(lengths.items())},
        "sample_owner_confirmed": False,
        "training_eligible": False,
        "production_eligible": False,
        "next_gate": "Use indexed START/END review per sample; never assign core length by hash or batch offset.",
    }


def build(prereg_path: Path = DEFAULT_PREREG, output_dir: Path | None = None) -> dict[str, Any]:
    """Build the official v2 transition-zone Review50 atomically."""

    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise TransitionBoxReviewError("unexpected transition-box experiment id")
    builder_commit = verify_builder_committed(
        [
            Path(__file__).resolve(),
            prereg_path,
            ROOT / "scripts" / "build_15m_ma_launch_transition_box_review50.py",
        ]
    )
    source_manifest = (ROOT / str(prereg["source"]["review_manifest_path"])).resolve()
    if sha256_file(source_manifest) != str(prereg["source"]["review_manifest_sha256"]):
        raise TransitionBoxReviewError("hash-pinned v1 Review50 manifest drifted")
    rejected_prereg = (ROOT / str(prereg["source"]["rejected_preregistration_path"])).resolve()
    if sha256_file(rejected_prereg) != str(prereg["source"]["rejected_preregistration_sha256"]):
        raise TransitionBoxReviewError("hash-pinned rejected v1 preregistration drifted")
    rows = read_jsonl(source_manifest)
    if len(rows) != 50 or len({str(row["sample_id"]) for row in rows}) != 50:
        raise TransitionBoxReviewError("v1 Review50 identity count drifted")
    direction_counts = Counter(str(row["direction"]) for row in rows)
    split_counts = Counter(str(row["split"]) for row in rows)
    if direction_counts != Counter({key: int(value) for key, value in prereg["source"]["expected_direction_counts"].items()}):
        raise TransitionBoxReviewError("v1 Review50 direction counts drifted")
    if split_counts != Counter({key: int(value) for key, value in prereg["source"]["expected_split_counts"].items()}):
        raise TransitionBoxReviewError("v1 Review50 split counts drifted")
    if any(pd.Timestamp(row["anchor_time"]) >= HOLDOUT_START for row in rows):
        raise TransitionBoxReviewError("review identity touches holdout")

    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building = final_dir.with_name(f"{final_dir.name}.building")
    try:
        final_dir.relative_to(ROOT)
    except ValueError as exc:
        raise TransitionBoxReviewError("review output must stay inside the repository") from exc
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite transition-box artifact: {final_dir}")
    image_dir = building / "public" / "images"
    image_dir.mkdir(parents=True)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_path"])].append(row)
    metrics: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    try:
        for source_path, source_rows in sorted(grouped.items()):
            frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
            if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
                raise AssertionError("holdout OHLCV materialized")
            source_audits.append(audit)
            enriched = add_six_mas(frame)
            for row in source_rows:
                start = int(row["window_start_i"])
                end = int(row["window_end_i"])
                window = enriched.iloc[start : end + 1].reset_index(drop=True)
                metric, _ = build_metric(row, window)
                metric.update(render_assets(metric, window, image_dir, final_dir=final_dir))
                metric["review_html_path"] = str((final_dir / "public" / "index.html").relative_to(ROOT))
                metrics.append(metric)
        metrics.sort(
            key=lambda row: (
                str(row["direction"]), str(row["split"]), int(row["time_bin"]),
                str(row["anchor_time"]), str(row["sample_id"]),
            )
        )
        if len(metrics) != 50:
            raise TransitionBoxReviewError("v2 rendered row count drifted")
        manifest_path = building / "review_manifest.jsonl"
        write_jsonl(manifest_path, metrics)
        manifest_sha = sha256_file(manifest_path)
        prereg_sha = sha256_file(prereg_path)
        public_html = building / "public" / "index.html"
        public_html.write_text(build_review_html(metrics, prereg_sha, manifest_sha), encoding="utf-8")
        summary = summarize(metrics)
        write_json(building / "summary.json", summary)
        write_jsonl(building / "source_audit.jsonl", source_audits)
        qa = {
            "experiment_id": EXPERIMENT_ID,
            "review_rows": len(metrics),
            "unique_sample_ids": len({row["sample_id"] for row in metrics}),
            "clean_image_sha_parity": sum(row["image_sha256"] == row["old_rejected_image_sha256"] for row in metrics),
            "all_core_wicks_and_six_mas_contained": all(
                variant["box"]["contains_core_wicks_and_six_mas"]
                for row in metrics for variant in row["variants"].values()
            ),
            "all_total_box_bars_7_to_10": all(
                7 <= int(variant["span"]["total_box_bars"]) <= 10
                for row in metrics for variant in row["variants"].values()
            ),
            "answers_preselected": 0,
            "yolo_labels_written": 0,
            "training_started": False,
            "holdout_ohlcv_rows_materialized": 0,
        }
        if not all(value for key, value in qa.items() if key.startswith("all_") or key.endswith("_parity")):
            raise TransitionBoxReviewError("static QA failed")
        write_json(building / "html_qa_receipt.json", qa)
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "status": "review50_ready_pending_owner",
            "builder_commit": builder_commit,
            "preregistration_sha256": prereg_sha,
            "source_review_manifest_sha256": sha256_file(source_manifest),
            "review_manifest_sha256": manifest_sha,
            "review_html_sha256": sha256_file(public_html),
            "summary_sha256": sha256_file(building / "summary.json"),
            "review_rows": len(metrics),
            "owner_answers_preselected": 0,
            "holdout_ohlcv_rows_materialized": 0,
            "yolo_labels_written": 0,
            "training_started": False,
            "training_eligible": False,
            "production_eligible": False,
            "active_or_frozen_changed": False,
        }
        write_json(building / "build_receipt.json", receipt)
        building.rename(final_dir)
        return receipt
    except Exception:
        if building.exists():
            shutil.rmtree(building)
        raise


REVIEW_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><link rel="icon" href="data:,"><title>15m 启动源区框 Review50 v2</title>
<style>
:root{--ink:#18222c;--muted:#657382;--line:#d4dde5;--bg:#eef2f5;--card:#fff;--red:#e23239;--cyan:#18a8c4;--gold:#e0a020;--green:#19845c;--amber:#ac771e}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}header{position:sticky;top:0;z-index:10;background:#fffffff5;border-bottom:1px solid var(--line)}.top,main{max-width:1540px;margin:auto;padding:14px 18px}.title{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}h1{font-size:24px;margin:0}.muted{color:var(--muted)}.progress{height:7px;background:#e1e7ec;border-radius:99px;margin:9px 0;overflow:hidden}.progress span{display:block;height:100%;background:var(--green);width:0}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toolbar button,.toolbar select{border:1px solid #b8c4ce;background:#fff;border-radius:8px;padding:7px 10px;font:inherit;cursor:pointer}.toolbar .primary{background:#275f7a;border-color:#275f7a;color:#fff;font-weight:800}.stats{margin-left:auto;font-weight:800}.notice{background:#fff7df;border:1px solid #e6c56e;border-radius:10px;padding:11px 13px;line-height:1.6;margin-bottom:13px}.card{background:var(--card);border-radius:13px;box-shadow:0 2px 13px #1b304018;overflow:hidden}.head{display:flex;gap:10px;align-items:center;padding:11px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}.badge{border-radius:99px;padding:4px 9px;font-weight:800}.LONG{background:#dff1ff;color:#126899}.SHORT{background:#ffe4e7;color:#a32734}.identity{font-weight:800}.meta{font-size:13px;color:var(--muted)}.current{margin-left:auto;font-weight:800}.section{padding:12px 14px;border-bottom:1px solid var(--line)}.section h2{font-size:17px;margin:0 0 5px}.hint{color:var(--muted);font-size:13px;margin-bottom:9px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}.variant{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}.vtitle{padding:7px 9px;font-weight:800;font-size:13px;background:#f5f7f9}.stage{position:relative;aspect-ratio:1280/742;background:#fff}.stage img{position:absolute;inset:0;width:100%;height:100%;display:block}.old{position:absolute;border:3px dashed var(--cyan);background:#18a8c408;pointer-events:none}.new{position:absolute;border:4px solid var(--red);background:#e2323908;pointer-events:none}.divider{position:absolute;border-left:3px solid var(--gold);pointer-events:none}.choicearea{display:grid;grid-template-columns:1fr 1.2fr;gap:12px;padding:13px 14px}.group b{display:block;margin-bottom:7px}.options{display:flex;gap:7px;flex-wrap:wrap}.options button{border:1px solid #b8c4ce;background:#fff;border-radius:8px;padding:9px 12px;font-weight:800;cursor:pointer}.options button.active{background:#193f56;color:#fff;border-color:#193f56}.actions [data-decision]{color:#fff;border:0}.actions [data-decision=ACCEPT]{background:var(--green)}.actions [data-decision=ADJUST]{background:var(--red)}.actions [data-decision=UNCERTAIN]{background:var(--amber)}textarea{width:100%;margin-top:9px;border:1px solid #b8c4ce;border-radius:8px;padding:8px;font:inherit}.nav{display:flex;gap:8px;margin-top:10px}.nav button{border:1px solid #b8c4ce;background:#fff;border-radius:8px;padding:8px 11px;cursor:pointer}footer{max-width:1540px;margin:auto;padding:13px 18px 50px;color:var(--muted);font-size:13px;line-height:1.55}@media(max-width:980px){header{position:static}.grid{grid-template-columns:repeat(2,1fr)}.choicearea{grid-template-columns:1fr}.stats{width:100%;margin-left:0}}@media(max-width:560px){.grid{grid-template-columns:1fr}}
</style></head><body><header><div class="top"><div class="title"><h1>15m 启动源区框 Review50 v2</h1><span class="muted" id="position"></span></div><div class="progress"><span id="bar"></span></div><div class="toolbar"><select id="side"><option value="ALL">全部方向</option><option value="LONG">LONG</option><option value="SHORT">SHORT</option></select><select id="status"><option value="ALL">全部状态</option><option value="PENDING" selected>未审核</option><option value="ACCEPT">接受</option><option value="ADJUST">需调整</option><option value="UNCERTAIN">待定</option></select><button id="prev">上一张</button><button id="next">下一张</button><button id="export" class="primary">导出审核 JSON</button><span class="stats" id="stats"></span></div></div></header><main><div class="notice"><strong>红框是新版，青色虚线是被否决的旧框，黄色竖线分开核心与3根确认K。</strong>新版纵向只由黄色线左侧的核心 K 线影线和六均线决定；黄色线右侧确认 K 只延长横向，不会把框拉高/拉低。四档总宽度分别为 7/8/9/10 根。页面没有默认答案、不写 YOLO 标签、不能启动训练。</div><section class="card"><div class="head"><span id="badge" class="badge"></span><span class="identity" id="identity"></span><span class="meta" id="meta"></span><span class="current" id="current"></span></div><div class="section"><h2>哪一档覆盖了完整启动源区，又没有把无关前文带进来？</h2><div class="hint">注意大确认 K 可以穿出红框上下边界，这是规则本身，不是漏框；需要比较的是红框覆盖的源区与横向启动过程。</div><div class="grid" id="variants"></div></div><div class="choicearea"><div class="group"><b>本张更合适的核心长度</b><div class="options" id="lengthChoices"></div><div class="nav"><button id="clear">清空本张</button></div></div><div class="group actions"><b>裁决</b><div class="options"><button data-decision="ACCEPT">接受此语义</button><button data-decision="ADJUST">仍需调整</button><button data-decision="UNCERTAIN">待定</button></div><textarea id="note" rows="3" placeholder="可写应向左/右几根，或哪根K不该算进核心"></textarea></div></div></section></main><footer>浏览器只把进度写入 localStorage。导出 JSON 后仍只是协议/样本审核回执；禁止用哈希随机给全量样本分配核心长度，也不会自动物化训练集。</footer><script>
const ITEMS=__ITEMS__,CONFIG=__CONFIG__,BY=new Map(ITEMS.map(x=>[x.sample_id,x])),KEY=`transition-box-review::${CONFIG.experiment_id}::${CONFIG.review_manifest_sha256}`;let state={answers:{},cursor:null,side:'ALL',status:'PENDING'};try{state={...state,...JSON.parse(localStorage.getItem(KEY)||'{}')}}catch(_){ }const $=id=>document.getElementById(id);const decision=i=>state.answers[i.sample_id]?.decision||'PENDING';function filtered(){return ITEMS.filter(i=>(state.side==='ALL'||i.direction===state.side)&&(state.status==='ALL'||decision(i)===state.status))}function current(){return BY.get(state.cursor)||filtered()[0]||ITEMS[0]}function save(){localStorage.setItem(KEY,JSON.stringify(state))}function panel(item,L){const s=item.variants[String(L)];return `<div class="variant"><div class="vtitle">Core ${L} + Confirm 3 = ${L+3}根 · h=${s.height.toFixed(0)}px · 确认极值穿框 ${s.outside}</div><div class="stage"><img src="${item.image}"><div class="old" style="${item.old_style}"></div><div class="new" style="${s.style}"></div><div class="divider" style="${s.divider_style}"></div></div></div>`}function stats(){const c={PENDING:0,ACCEPT:0,ADJUST:0,UNCERTAIN:0};ITEMS.forEach(i=>c[decision(i)]++);const done=ITEMS.length-c.PENDING;$('stats').textContent=`已审 ${done}/${ITEMS.length} · 接受 ${c.ACCEPT} · 调整 ${c.ADJUST} · 待定 ${c.UNCERTAIN}`;$('bar').style.width=`${100*done/ITEMS.length}%`}function render(){let list=filtered();if(!list.length){state.status='ALL';$('status').value='ALL';list=filtered()}let item=current();if(!list.some(i=>i.sample_id===item.sample_id)){item=list[0];state.cursor=item.sample_id}const a=state.answers[item.sample_id]||{},idx=list.findIndex(i=>i.sample_id===item.sample_id);$('position').textContent=`筛选内 ${idx+1}/${list.length} · 全局 ${item.source_order}/__TOTAL__`;$('badge').textContent=item.direction;$('badge').className=`badge ${item.direction}`;$('identity').textContent=`${item.symbol} · ${item.sample_id}`;$('meta').textContent=`${item.anchor_time} · ${item.split} · time-bin ${item.time_bin}`;$('current').textContent=a.decision||'PENDING';$('variants').innerHTML=[4,5,6,7].map(L=>panel(item,L)).join('');$('lengthChoices').innerHTML=[4,5,6,7].map(v=>`<button data-length="${v}" class="${Number(a.preferred_core_len)===v?'active':''}">Core ${v} / 总${v+3}根</button>`).join('');document.querySelectorAll('[data-length]').forEach(b=>b.onclick=()=>setField('preferred_core_len',Number(b.dataset.length)));document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===a.decision));$('note').value=a.note||'';stats();save()}function base(i){return {sample_id:i.sample_id,symbol:i.symbol,direction:i.direction,anchor_time:i.anchor_time,image_sha256:i.image_sha256}}function setField(k,v){const i=current();state.answers[i.sample_id]={...base(i),...(state.answers[i.sample_id]||{}),[k]:v,reviewed_at:new Date().toISOString()};save();render()}function decide(v){const i=current(),a=state.answers[i.sample_id]||{};if(v==='ACCEPT'&&!a.preferred_core_len){alert('接受前必须选核心长度。');return}state.answers[i.sample_id]={...base(i),...a,decision:v,note:$('note').value||null,reviewed_at:new Date().toISOString()};save();step(1)}function step(d){const list=filtered();if(!list.length)return;let n=list.findIndex(i=>i.sample_id===current().sample_id);if(n<0)n=0;state.cursor=list[Math.max(0,Math.min(list.length-1,n+d))].sample_id;save();render()}$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('side').onchange=e=>{state.side=e.target.value;state.cursor=null;save();render()};$('status').onchange=e=>{state.status=e.target.value;state.cursor=null;save();render()};document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));$('note').oninput=e=>{const i=current();state.answers[i.sample_id]={...base(i),...(state.answers[i.sample_id]||{}),note:e.target.value||null};save()};$('clear').onclick=()=>{delete state.answers[current().sample_id];save();render()};$('export').onclick=()=>{const answers=ITEMS.map(i=>state.answers[i.sample_id]).filter(a=>a?.decision),out={schema_version:1,...CONFIG,exported_at:new Date().toISOString(),n_total:ITEMS.length,n_answered:answers.length,complete:answers.length===ITEMS.length,answers},blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${CONFIG.experiment_id}_answers_${out.n_answered}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};if(!state.cursor)state.cursor=filtered()[0]?.sample_id||ITEMS[0].sample_id;$('side').value=state.side;$('status').value=state.status;render();
</script></body></html>'''
