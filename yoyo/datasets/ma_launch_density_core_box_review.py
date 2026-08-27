"""Build the Owner-corrected single-box 15-minute MA-density Review50 v3.

Each input row reuses the exact five-bar span already selected inside the causal
``[t-12, t-1]`` search in the frozen v1 manifest.  Box coordinates use only
``high``, ``low`` and ``sma/ema 20/60/120`` from those five bars.  No value after
the per-image core end contributes to x or y.  The box therefore stays at each
sample's own MA-density knot instead of being moved to one global ``t-3``
position, and it contains zero confirmation bars.

The builder writes review PNGs, manifests and HTML only.  It cannot write YOLO
labels/training images, start training, consume holdout OHLCV, change model
eligibility, ACTIVE/frozen state, forward state, deployment state or orders.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix, sha256_file
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-ma-launch-density-core-box-review50-v3"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
SOURCE_MANIFEST = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-ma-box-review50-v1"
    / "results"
    / "review_manifest.jsonl"
)
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
CORE_BARS = 5
PAD_FRACTION = 0.04
SOURCE_WIDTH = 1280
SOURCE_HEIGHT = 742
RED = (45, 45, 232)  # BGR


class DensityCoreReviewError(ValueError):
    """Raised when frozen identity, chronology or geometry drifts."""


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
    """Require the v3 behavior/config to land on main before materialization."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("density-core review builder must run on main")
    relatives = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"density-core builder inputs are not committed:\n{dirty}")
    return git_output("rev-parse", "HEAD")


def _clip(low: float, high: float, limit: float) -> tuple[float, float]:
    low = max(0.0, low)
    high = min(limit, high)
    if high <= low:
        raise DensityCoreReviewError("degenerate box coordinate")
    return float(low), float(high)


def density_core_box(
    transform: Any,
    window: pd.DataFrame,
    *,
    start_local: int,
    end_local: int,
    pad_fraction: float = PAD_FRACTION,
) -> dict[str, Any]:
    """Return one five-bar full-wick plus six-MA box with no post-core inputs."""

    if end_local - start_local + 1 != CORE_BARS:
        raise DensityCoreReviewError("v3 core must contain exactly five bars")
    if not 0 <= start_local <= end_local < len(window):
        raise DensityCoreReviewError("core falls outside frozen W20")
    if pad_fraction < 0:
        raise DensityCoreReviewError("padding must be non-negative")
    core = window.iloc[start_local : end_local + 1]
    values = np.concatenate(
        (
            core["high"].to_numpy(dtype=float),
            core["low"].to_numpy(dtype=float),
            core.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float).ravel(),
        )
    )
    if not np.isfinite(values).all():
        raise DensityCoreReviewError("non-finite core OHLC/MA value")
    raw_high, raw_low = float(values.max()), float(values.min())
    if raw_high <= raw_low:
        raise DensityCoreReviewError("core price extent is empty")
    pad = (raw_high - raw_low) * float(pad_fraction)
    box_high, box_low = raw_high + pad, raw_low - pad
    x0 = transform.x_at(start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(end_local) + transform.candle_half_w + 2
    y0, y1 = transform.y_at(box_high), transform.y_at(box_low)
    x0, x1 = _clip(min(x0, x1), max(x0, x1), transform.width)
    y0, y1 = _clip(min(y0, y1), max(y0, y1), transform.height)
    core_pixels = np.asarray([transform.y_at(float(value)) for value in values])
    contains = bool(core_pixels.min() >= y0 - 1e-6 and core_pixels.max() <= y1 + 1e-6)
    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "cx_norm": float((x0 + x1) / 2.0 / transform.width),
        "cy_norm": float((y0 + y1) / 2.0 / transform.height),
        "w_norm": float((x1 - x0) / transform.width),
        "h_norm": float((y1 - y0) / transform.height),
        "source_width_px": float(x1 - x0),
        "source_height_px": float(y1 - y0),
        "core_price_high_raw": raw_high,
        "core_price_low_raw": raw_low,
        "box_price_high": box_high,
        "box_price_low": box_low,
        "pad_fraction": float(pad_fraction),
        "contains_core_wicks_and_six_mas": contains,
        "core_bars": CORE_BARS,
        "confirmation_bars": 0,
        "post_core_values_used_for_any_coordinate": False,
    }


def _encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise OSError("OpenCV failed to encode density-core PNG")
    return encoded.tobytes()


def _draw_box(image: np.ndarray, box: Mapping[str, Any]) -> None:
    x0, y0, x1, y1 = (int(round(float(box[key]))) for key in ("x0", "y0", "x1", "y1"))
    cv2.rectangle(image, (x0, y0), (x1, y1), RED, 4, cv2.LINE_AA)


def _relative_final_path(path_in_building: Path, building: Path, final_dir: Path) -> str:
    return str((final_dir / path_in_building.relative_to(building)).relative_to(ROOT))


def _render_html(rows: Sequence[Mapping[str, Any]], manifest_sha: str) -> str:
    cards = []
    for order, row in enumerate(rows, 1):
        cards.append(
            "<article><h2>"
            + f"{order:02d}/50 · {html.escape(str(row['symbol']))} · {html.escape(str(row['direction']))}"
            + "</h2><p>"
            + f"{html.escape(str(row['anchor_time']))} · core offsets {row['core_start_offset']}…{row['core_end_offset']}"
            + f" · width {row['box']['source_width_px']:.1f}px</p>"
            + f"<img loading='lazy' src='{Path(str(row['image_path'])).name}' alt='{html.escape(str(row['sample_id']))}'>"
            + "</article>"
        )
    return """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>15m 密集核心单框 Review50 v3</title><style>
body{margin:0;background:#eef2f5;color:#18222c;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1420px;margin:auto;padding:18px}header{background:#fff7df;border-bottom:1px solid #d9c179}h1{margin:0 0 8px}.note{line-height:1.6}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}article{background:#fff;border-radius:12px;padding:12px;box-shadow:0 2px 10px #1b304018}h2{font-size:17px;margin:0 0 5px}p{color:#66717d;font-size:13px;margin:0 0 9px}img{display:block;width:100%;height:auto;border:1px solid #d5dde4}@media(max-width:820px){main{grid-template-columns:1fr}}
</style></head><body><header><h1>15m 密集核心单框 Review50 v3</h1><div class='note'><b>每张只有一个红框：</b>恢复该样本自己的六均线最密 5 根位置，不含任何确认 K；上下完整包含这 5 根的影线与六均线。仅供 P0 标注审核，没有 YOLO 标签或训练入口。<br>manifest SHA: """ + manifest_sha + "</div></header><main>" + "".join(cards) + "</main></body></html>"


def _describe(values: Sequence[float]) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    return {
        "min": float(data.min()),
        "p10": float(np.quantile(data, 0.10)),
        "median": float(np.median(data)),
        "p90": float(np.quantile(data, 0.90)),
        "max": float(data.max()),
    }


def build(prereg_path: Path = DEFAULT_PREREG, output_dir: Path | None = None) -> dict[str, Any]:
    """Materialize the exact 50 single-box review images fail-closed."""

    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise DensityCoreReviewError("experiment ID drift")
    source_manifest = ROOT / str(prereg["source"]["review_manifest"])
    if sha256_file(source_manifest) != prereg["source"]["review_manifest_sha256"]:
        raise DensityCoreReviewError("frozen v1 manifest hash drift")
    if prereg["geometry"].get("core_bars") != CORE_BARS or prereg["geometry"].get("confirmation_bars") != 0:
        raise DensityCoreReviewError("single five-bar geometry contract drift")
    builder_commit = verify_builder_committed(
        [Path(__file__), ROOT / "scripts" / "build_15m_ma_launch_density_core_box_review50.py", prereg_path]
    )
    source_rows = read_jsonl(source_manifest)
    if len(source_rows) != 50 or len({row["sample_id"] for row in source_rows}) != 50:
        raise DensityCoreReviewError("source review is not 50 unique rows")

    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite density-core review: {final_dir}")
    image_dir = building / "public" / "images"
    image_dir.mkdir(parents=True)
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for order, row in enumerate(source_rows, 1):
        grouped[str(row["source_path"])].append((order, row))

    output_rows: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    for source_path, items in sorted(grouped.items()):
        frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV materialized")
        source_audits.append(audit)
        enriched = add_six_mas(frame)
        for order, row in items:
            window = enriched.iloc[int(row["window_start_i"]) : int(row["window_end_i"]) + 1].reset_index(drop=True)
            if len(window) != 20:
                raise DensityCoreReviewError(f"frozen W20 missing: {row['sample_id']}")
            raw, transform = render_chart(window, out_path=None)
            clean_sha = hashlib.sha256(_encode_png(raw)).hexdigest()
            if clean_sha != row["image_sha256"]:
                raise DensityCoreReviewError(f"clean render SHA drift: {row['sample_id']}")
            span = row["variants"]["L5_min24"]["span"]
            start_local, end_local = int(span["start_local"]), int(span["end_local"])
            anchor_local = int(row["source_anchor_i"]) - int(row["window_start_i"])
            if end_local >= anchor_local:
                raise DensityCoreReviewError(f"core reaches anchor/future: {row['sample_id']}")
            box = density_core_box(
                transform,
                window,
                start_local=start_local,
                end_local=end_local,
            )
            mutated = window.copy()
            after = list(range(end_local + 1, len(mutated)))
            mutated.loc[after, ["high", "low", *SIX_MA_COLUMNS]] *= 7.0
            mutated_box = density_core_box(
                transform,
                mutated,
                start_local=start_local,
                end_local=end_local,
            )
            delta = max(abs(float(box[key]) - float(mutated_box[key])) for key in ("x0", "y0", "x1", "y1"))
            canvas = raw.copy()
            _draw_box(canvas, box)
            filename = f"{order:02d}_{row['symbol']}_{row['direction']}_{row['sample_id']}_density5.png"
            path = image_dir / filename
            png = _encode_png(canvas)
            path.write_bytes(png)
            output_rows.append(
                {
                    "source_order": order,
                    "sample_id": str(row["sample_id"]),
                    "symbol": str(row["symbol"]),
                    "direction": str(row["direction"]),
                    "split": str(row["split"]),
                    "anchor_time": str(row["anchor_time"]),
                    "source_path": str(row["source_path"]),
                    "window_start_i": int(row["window_start_i"]),
                    "window_end_i": int(row["window_end_i"]),
                    "clean_image_sha256": clean_sha,
                    "image_path": _relative_final_path(path, building, final_dir),
                    "image_sha256": hashlib.sha256(png).hexdigest(),
                    "core_start_local": start_local,
                    "core_end_local": end_local,
                    "core_start_offset": int(span["start_offset"]),
                    "core_end_offset": int(span["end_offset"]),
                    "core_bars": CORE_BARS,
                    "confirmation_bars": 0,
                    "box": box,
                    "post_core_intervention_coordinate_max_abs_delta": float(delta),
                    "sample_owner_confirmed": False,
                    "training_eligible": False,
                    "production_eligible": False,
                }
            )
    output_rows.sort(key=lambda row: int(row["source_order"]))
    manifest_path = building / "review_manifest.jsonl"
    write_jsonl(manifest_path, output_rows)
    manifest_sha = sha256_file(manifest_path)
    index_path = building / "public" / "index.html"
    index_path.write_text(_render_html(output_rows, manifest_sha), encoding="utf-8")
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "n_rows": len(output_rows),
        "n_boxes_per_image": 1,
        "core_bars": CORE_BARS,
        "confirmation_bars": 0,
        "clean_image_sha_parity": sum(row["clean_image_sha256"] == source_rows[i]["image_sha256"] for i, row in enumerate(output_rows)),
        "core_containment_pass": sum(bool(row["box"]["contains_core_wicks_and_six_mas"]) for row in output_rows),
        "post_core_intervention_zero_delta": sum(float(row["post_core_intervention_coordinate_max_abs_delta"]) == 0.0 for row in output_rows),
        "core_end_offset_distribution": dict(sorted(Counter(int(row["core_end_offset"]) for row in output_rows).items())),
        "center_fraction": _describe([float(row["box"]["cx_norm"]) for row in output_rows]),
        "source_width_px": _describe([float(row["box"]["source_width_px"]) for row in output_rows]),
        "source_height_px": _describe([float(row["box"]["source_height_px"]) for row in output_rows]),
        "holdout_ohlcv_rows_materialized": sum(int(audit["holdout_ohlcv_rows_materialized"]) for audit in source_audits),
        "yolo_labels_written": 0,
        "training_images_written": 0,
        "training_started": False,
        "active_or_frozen_modified": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    if any(summary[key] != 50 for key in ("clean_image_sha_parity", "core_containment_pass", "post_core_intervention_zero_delta")):
        raise DensityCoreReviewError(f"v3 QA failed: {summary}")
    write_json(building / "summary.json", summary)
    write_jsonl(building / "source_audit.jsonl", source_audits)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "source_manifest_sha256": sha256_file(source_manifest),
        "review_manifest_sha256": manifest_sha,
        "review_html_sha256": sha256_file(index_path),
        "n_rows": 50,
        "holdout_ohlcv_rows_materialized": 0,
        "yolo_labels_written": 0,
        "training_started": False,
        "active_or_frozen_modified": False,
    }
    write_json(building / "build_receipt.json", receipt)
    os.replace(building, final_dir)
    return receipt

