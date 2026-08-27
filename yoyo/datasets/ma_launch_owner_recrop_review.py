"""Build the Owner-directed 15-minute MA-launch recrop Review50 v4.

Source rows are the frozen pre-holdout Review50 identities.  Owner feedback is
applied as an explicit per-sample decision table: rejected rows receive no box,
directed rows receive their own 4-5 bar proposal, and unmentioned rows retain
the prior proposal but remain unconfirmed.  Clean model-input windows end at
the proposed core, core+1 or core+2; five later bars are rendered into a
physically separate review-only directory.

Columns used for box geometry are ``high``, ``low`` and ``sma/ema 20/60/120``
inside the proposed core only.  The chart transform sees the entire causal
model-input crop, including its deterministic 0-2 visible post-core bars.  No
holdout OHLCV, YOLO labels, training images, training process, model state,
forward state, deployment state or order path is touched.
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
from yoyo.layers.l1_detection.render import render_chart


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-recrop-review50-v4"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
SOURCE_WIDTH = 1280
SOURCE_HEIGHT = 742
MIN_CORE_BARS = 4
MAX_CORE_BARS = 7
PAD_FRACTION = 0.04
FUTURE_REVIEW_BARS = 5
RED = (45, 45, 232)  # BGR


class OwnerRecropReviewError(ValueError):
    """Raised when frozen identity, chronology or review geometry drifts."""


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
    """Require executable v4 behavior/config to land on main before rendering."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("owner-recrop review builder must run on main")
    relatives = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"owner-recrop builder inputs are not committed:\n{dirty}")
    return git_output("rev-parse", "HEAD")


def encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise OSError("OpenCV failed to encode owner-recrop PNG")
    return encoded.tobytes()


def stable_context(sample_id: str) -> tuple[int, int]:
    """Return deterministic pre/core-post context without a position shortcut."""

    digest = hashlib.sha256(sample_id.encode("utf-8")).digest()
    return 10 + digest[0] % 3, digest[1] % 3


def _clip(low: float, high: float, limit: float) -> tuple[float, float]:
    low = max(0.0, low)
    high = min(limit, high)
    if high <= low:
        raise OwnerRecropReviewError("degenerate box coordinate")
    return float(low), float(high)


def core_box(
    transform: Any,
    window: pd.DataFrame,
    *,
    start_local: int,
    end_local: int,
    pad_fraction: float = PAD_FRACTION,
) -> dict[str, Any]:
    """Return one 4-7 bar full-wick plus six-MA core box."""

    core_bars = end_local - start_local + 1
    if not MIN_CORE_BARS <= core_bars <= MAX_CORE_BARS:
        raise OwnerRecropReviewError("core must contain 4-7 bars")
    if not 0 <= start_local <= end_local < len(window):
        raise OwnerRecropReviewError("core falls outside rendered window")
    if pad_fraction < 0:
        raise OwnerRecropReviewError("padding must be non-negative")
    core = window.iloc[start_local : end_local + 1]
    values = np.concatenate(
        (
            core["high"].to_numpy(dtype=float),
            core["low"].to_numpy(dtype=float),
            core.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float).ravel(),
        )
    )
    if not np.isfinite(values).all():
        raise OwnerRecropReviewError("non-finite core OHLC/MA value")
    raw_high, raw_low = float(values.max()), float(values.min())
    if raw_high <= raw_low:
        raise OwnerRecropReviewError("core price extent is empty")
    pad = (raw_high - raw_low) * float(pad_fraction)
    box_high, box_low = raw_high + pad, raw_low - pad
    x0 = transform.x_at(start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(end_local) + transform.candle_half_w + 2
    y0, y1 = transform.y_at(box_high), transform.y_at(box_low)
    x0, x1 = _clip(min(x0, x1), max(x0, x1), transform.width)
    y0, y1 = _clip(min(y0, y1), max(y0, y1), transform.height)
    pixels = np.asarray([transform.y_at(float(value)) for value in values])
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
        "contains_core_wicks_and_six_mas": bool(
            pixels.min() >= y0 - 1e-6 and pixels.max() <= y1 + 1e-6
        ),
        "core_bars": core_bars,
        "confirmation_bars_inside_box": 0,
        "semantic_vertical_values_after_core": 0,
    }


def draw_box(image: np.ndarray, box: Mapping[str, Any]) -> None:
    x0, y0, x1, y1 = (int(round(float(box[key]))) for key in ("x0", "y0", "x1", "y1"))
    cv2.rectangle(image, (x0, y0), (x1, y1), RED, 4, cv2.LINE_AA)


def relative_final_path(path_in_building: Path, building: Path, final_dir: Path) -> str:
    return str((final_dir / path_in_building.relative_to(building)).relative_to(ROOT))


def resolve_decision(source_row: Mapping[str, Any], overrides: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve one explicit Owner decision or an honest unmentioned proposal."""

    sample_id = str(source_row["sample_id"])
    override = overrides.get(sample_id)
    if override is not None and override.get("action") == "reject":
        return {
            "status": "OWNER_REJECT",
            "core_start_offset": None,
            "core_end_offset": None,
            "core_bars": 0,
            "reason": str(override["reason"]),
            "owner_semantic_verdict": True,
            "sample_owner_geometry_confirmed": False,
        }
    if override is not None:
        start = int(override["core_start_offset"])
        end = int(override["core_end_offset"])
        action = str(override["action"])
        if action not in {"rebox", "keep_reference"}:
            raise OwnerRecropReviewError(f"unknown override action for {sample_id}: {action}")
        status = "OWNER_DIRECTED_REBOX_PROPOSAL" if action == "rebox" else "OWNER_REFERENCE_RECROP"
        reason = str(override["reason"])
        owner_semantic_verdict = action == "keep_reference"
    else:
        span = source_row["variants"]["L5_min24"]["span"]
        start, end = int(span["start_offset"]), int(span["end_offset"])
        status = "PENDING_UNMENTIONED"
        reason = "Owner did not explicitly judge this row; prior L5 span is retained only as a review proposal."
        owner_semantic_verdict = False
    core_bars = end - start + 1
    if not MIN_CORE_BARS <= core_bars <= MAX_CORE_BARS:
        raise OwnerRecropReviewError(f"invalid proposed core length for {sample_id}: {core_bars}")
    if end > -1:
        raise OwnerRecropReviewError(f"proposed core reaches signal/future for {sample_id}")
    return {
        "status": status,
        "core_start_offset": start,
        "core_end_offset": end,
        "core_bars": core_bars,
        "reason": reason,
        "owner_semantic_verdict": owner_semantic_verdict,
        "sample_owner_geometry_confirmed": False,
    }


def validate_preregistration(prereg: Mapping[str, Any], source_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise OwnerRecropReviewError("experiment ID drift")
    if int(prereg["source"]["n_rows"]) != 50 or len(source_rows) != 50:
        raise OwnerRecropReviewError("source review is not exactly 50 rows")
    if len({str(row["sample_id"]) for row in source_rows}) != 50:
        raise OwnerRecropReviewError("source review sample IDs are not unique")
    overrides = {str(row["sample_id"]): dict(row) for row in prereg["owner_decisions"]}
    if len(overrides) != len(prereg["owner_decisions"]):
        raise OwnerRecropReviewError("duplicate Owner decision")
    source_ids = {str(row["sample_id"]) for row in source_rows}
    if not set(overrides).issubset(source_ids):
        raise OwnerRecropReviewError("Owner decision references a sample outside frozen Review50")
    decisions = [resolve_decision(row, overrides) for row in source_rows]
    counts = Counter(str(row["status"]) for row in decisions)
    expected = {
        "OWNER_REJECT": 6,
        "OWNER_DIRECTED_REBOX_PROPOSAL": 5,
        "OWNER_REFERENCE_RECROP": 3,
        "PENDING_UNMENTIONED": 36,
    }
    if dict(counts) != expected:
        raise OwnerRecropReviewError(f"Owner decision count drift: {dict(counts)}")
    return overrides


def assert_contiguous(window: pd.DataFrame, *, sample_id: str, kind: str) -> None:
    times = pd.to_datetime(window["open_time"], utc=True)
    if len(window) < 2 or bool((times.diff().dropna() != pd.Timedelta(minutes=15)).any()):
        raise OwnerRecropReviewError(f"non-contiguous {kind} window: {sample_id}")


def describe(values: Sequence[float]) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    return {
        "min": float(data.min()),
        "p10": float(np.quantile(data, 0.10)),
        "median": float(np.median(data)),
        "p90": float(np.quantile(data, 0.90)),
        "max": float(data.max()),
    }


def render_html(rows: Sequence[Mapping[str, Any]], manifest_sha: str) -> str:
    cards: list[str] = []
    for row in rows:
        order = int(row["source_order"])
        title = f"{order:02d}/50 · {html.escape(str(row['symbol']))} · {html.escape(str(row['direction']))}"
        status = html.escape(str(row["status"]))
        reason = html.escape(str(row["reason"]))
        if status == "OWNER_REJECT":
            cards.append(
                f"<article class='reject'><h2>{title}</h2><div class='badge reject-b'>无框剔除</div>"
                f"<p>{reason}</p><img loading='lazy' src='{html.escape(str(row['review_src']))}'></article>"
            )
            continue
        meta = (
            f"core t{row['core_start_offset']}…t{row['core_end_offset']} · {row['core_bars']}根 · "
            f"输入右端=core+{row['post_core_visible_bars']} · 总窗 {row['model_window_bars']}根"
        )
        cards.append(
            f"<article><h2>{title}</h2><div class='badge'>{status}</div><p>{html.escape(meta)}<br>{reason}</p>"
            "<div class='pair'><figure><figcaption>模型输入审核叠框（实际输入另存为无框 PNG）</figcaption>"
            f"<img loading='lazy' src='{html.escape(str(row['review_src']))}'></figure>"
            "<figure><figcaption>未来 +5 根，仅人工复核，物理隔离且无 labels</figcaption>"
            f"<img loading='lazy' src='{html.escape(str(row['future_src']))}'></figure></div></article>"
        )
    return """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>15m Owner 逐图重框 Review50 v4</title><style>
body{margin:0;background:#eef2f5;color:#18222c;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1500px;margin:auto;padding:18px}header{background:#fff7df;border-bottom:1px solid #d9c179}.note{line-height:1.65}.legend{background:#fff;padding:10px;border-left:4px solid #d44;margin-top:10px}article{background:#fff;border-radius:12px;padding:14px;margin-bottom:18px;box-shadow:0 2px 10px #1b304018}article.reject{border:2px solid #999}h1{margin:0 0 8px}h2{font-size:18px;margin:0 0 6px}.badge{display:inline-block;background:#fff0cd;color:#6b4b00;border-radius:999px;padding:3px 9px;font-size:12px}.reject-b{background:#eceff1;color:#4e5962}p{color:#596572;line-height:1.55;font-size:13px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}figure{margin:0}figcaption{font-size:12px;color:#5c6670;margin-bottom:5px}img{display:block;width:100%;height:auto;border:1px solid #d5dde4}@media(max-width:900px){.pair{grid-template-columns:1fr}}
</style></head><body><header><h1>15m Owner 逐图重框 Review50 v4</h1><div class='note'><b>本页是待审核提案，不是训练集：</b>明确错误的 6 张不画框；其余每张最多一个红框。模型窗右侧只留核心后 0–2 根 K，人工未来 +5 根另存，禁止进入 labels/训练。未点名的 36 张仍标为 PENDING，不冒充 Owner Gold。</div><div class='legend'>44/42 是合格参考；48 保留形态但修复旧图 8 根延迟。manifest SHA: """ + manifest_sha + "</div></header><main>" + "".join(cards) + "</main></body></html>"


def build(prereg_path: Path = DEFAULT_PREREG, output_dir: Path | None = None) -> dict[str, Any]:
    """Materialize the exact frozen Review50 with Owner-directed v4 proposals."""

    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    source_manifest = ROOT / str(prereg["source"]["review_manifest"])
    if sha256_file(source_manifest) != str(prereg["source"]["review_manifest_sha256"]):
        raise OwnerRecropReviewError("frozen v1 manifest hash drift")
    source_rows = read_jsonl(source_manifest)
    overrides = validate_preregistration(prereg, source_rows)
    builder_commit = verify_builder_committed(
        [Path(__file__), ROOT / "scripts" / "build_15m_ma_launch_owner_recrop_review50.py", prereg_path]
    )

    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite owner-recrop review: {final_dir}")
    public_images = building / "public" / "images"
    model_inputs = building / "model_inputs_clean"
    future_reviews = building / "future_review_only"
    rejected_images = building / "public" / "rejected"
    for directory in (public_images, model_inputs, future_reviews, rejected_images):
        directory.mkdir(parents=True)

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
        for order, source_row in items:
            sample_id = str(source_row["sample_id"])
            decision = resolve_decision(source_row, overrides)
            common = {
                "source_order": order,
                "sample_id": sample_id,
                "symbol": str(source_row["symbol"]),
                "direction": str(source_row["direction"]),
                "split": str(source_row["split"]),
                "anchor_time": str(source_row["anchor_time"]),
                "source_path": str(source_row["source_path"]),
                **decision,
                "training_eligible": False,
                "production_eligible": False,
                "yolo_label_path": None,
            }
            if decision["status"] == "OWNER_REJECT":
                source_clean = ROOT / str(source_row["image_path"])
                if sha256_file(source_clean) != str(source_row["image_sha256"]):
                    raise OwnerRecropReviewError(f"rejected clean image SHA drift: {sample_id}")
                filename = f"{order:02d}_{source_row['symbol']}_{source_row['direction']}_{sample_id}_REJECT.png"
                target = rejected_images / filename
                shutil.copyfile(source_clean, target)
                common.update(
                    {
                        "review_image_path": relative_final_path(target, building, final_dir),
                        "review_image_sha256": sha256_file(target),
                        "review_src": f"rejected/{filename}",
                        "model_input_path": None,
                        "model_input_sha256": None,
                        "future_review_path": None,
                        "future_review_sha256": None,
                        "future_src": None,
                        "model_window_bars": 0,
                        "pre_core_context_bars": 0,
                        "post_core_visible_bars": 0,
                        "core_to_model_right_gap_bars": None,
                        "box": None,
                    }
                )
                output_rows.append(common)
                continue

            anchor_i = int(source_row["source_anchor_i"])
            core_start_i = anchor_i + int(decision["core_start_offset"])
            core_end_i = anchor_i + int(decision["core_end_offset"])
            pre_bars, post_bars = stable_context(sample_id)
            model_start_i = core_start_i - pre_bars
            model_end_i = core_end_i + post_bars
            future_end_i = core_end_i + FUTURE_REVIEW_BARS
            if model_start_i < 0 or future_end_i >= len(enriched):
                raise OwnerRecropReviewError(f"recrop outside source bounds: {sample_id}")
            model_window = enriched.iloc[model_start_i : model_end_i + 1].reset_index(drop=True)
            future_window = enriched.iloc[model_start_i : future_end_i + 1].reset_index(drop=True)
            assert_contiguous(model_window, sample_id=sample_id, kind="model")
            assert_contiguous(future_window, sample_id=sample_id, kind="future-review")
            if pd.Timestamp(future_window["open_time"].iloc[-1]) >= HOLDOUT_START:
                raise OwnerRecropReviewError(f"future review touches holdout: {sample_id}")
            core_start_local = core_start_i - model_start_i
            core_end_local = core_end_i - model_start_i
            clean, transform = render_chart(model_window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None)
            box = core_box(transform, model_window, start_local=core_start_local, end_local=core_end_local)
            clean_png = encode_png(clean)
            base_name = f"{order:02d}_{source_row['symbol']}_{source_row['direction']}_{sample_id}"
            model_path = model_inputs / f"{base_name}_MODEL_CLEAN.png"
            model_path.write_bytes(clean_png)
            overlay = clean.copy()
            draw_box(overlay, box)
            overlay_path = public_images / f"{base_name}_MODEL_REVIEW.png"
            overlay_path.write_bytes(encode_png(overlay))

            future_clean, future_transform = render_chart(
                future_window, width=SOURCE_WIDTH, height=SOURCE_HEIGHT, out_path=None
            )
            future_box = core_box(
                future_transform,
                future_window,
                start_local=core_start_i - model_start_i,
                end_local=core_end_i - model_start_i,
            )
            draw_box(future_clean, future_box)
            future_path = future_reviews / f"{base_name}_FUTURE_PLUS5_REVIEW_ONLY.png"
            future_path.write_bytes(encode_png(future_clean))

            common.update(
                {
                    "core_start_source_i": core_start_i,
                    "core_end_source_i": core_end_i,
                    "core_start_local": core_start_local,
                    "core_end_local": core_end_local,
                    "model_window_start_i": model_start_i,
                    "model_window_end_i": model_end_i,
                    "model_window_bars": len(model_window),
                    "pre_core_context_bars": pre_bars,
                    "post_core_visible_bars": post_bars,
                    "core_to_model_right_gap_bars": model_end_i - core_end_i,
                    "model_input_path": relative_final_path(model_path, building, final_dir),
                    "model_input_sha256": sha256_file(model_path),
                    "review_image_path": relative_final_path(overlay_path, building, final_dir),
                    "review_image_sha256": sha256_file(overlay_path),
                    "review_src": f"images/{overlay_path.name}",
                    "future_review_path": relative_final_path(future_path, building, final_dir),
                    "future_review_sha256": sha256_file(future_path),
                    "future_src": f"../future_review_only/{future_path.name}",
                    "future_review_bars_after_core": FUTURE_REVIEW_BARS,
                    "future_review_has_label_file": False,
                    "box": box,
                    "future_review_box": future_box,
                }
            )
            output_rows.append(common)

    output_rows.sort(key=lambda row: int(row["source_order"]))
    if [int(row["source_order"]) for row in output_rows] != list(range(1, 51)):
        raise OwnerRecropReviewError("output order drift")
    manifest_path = building / "review_manifest.jsonl"
    write_jsonl(manifest_path, output_rows)
    manifest_sha = sha256_file(manifest_path)
    index_path = building / "public" / "index.html"
    index_path.write_text(render_html(output_rows, manifest_sha), encoding="utf-8")

    active = [row for row in output_rows if row["status"] != "OWNER_REJECT"]
    rejected = [row for row in output_rows if row["status"] == "OWNER_REJECT"]
    label_files = list(building.rglob("*.txt"))
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "n_source_rows": len(output_rows),
        "n_review_box_proposals": len(active),
        "n_owner_reject_no_box": len(rejected),
        "status_counts": dict(Counter(str(row["status"]) for row in output_rows)),
        "boxes_per_active_image": 1,
        "boxes_per_rejected_image": 0,
        "core_bars_distribution": dict(Counter(int(row["core_bars"]) for row in active)),
        "model_window_bars": describe([float(row["model_window_bars"]) for row in active]),
        "core_to_model_right_gap_bars": describe(
            [float(row["core_to_model_right_gap_bars"]) for row in active]
        ),
        "all_model_right_gaps_tip_tip1_tip2": all(
            0 <= int(row["core_to_model_right_gap_bars"]) <= 2 for row in active
        ),
        "core_containment_pass": sum(bool(row["box"]["contains_core_wicks_and_six_mas"]) for row in active),
        "source_width_px": describe([float(row["box"]["source_width_px"]) for row in active]),
        "source_height_px": describe([float(row["box"]["source_height_px"]) for row in active]),
        "holdout_ohlcv_rows_materialized": sum(
            int(audit["holdout_ohlcv_rows_materialized"]) for audit in source_audits
        ),
        "future_review_directory_physically_separate": True,
        "future_review_label_files": len(label_files),
        "yolo_labels_written": 0,
        "training_started": False,
        "active_or_frozen_modified": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    if len(active) != 44 or len(rejected) != 6:
        raise OwnerRecropReviewError(f"proposal/reject count drift: {len(active)}/{len(rejected)}")
    if summary["core_containment_pass"] != 44 or not summary["all_model_right_gaps_tip_tip1_tip2"]:
        raise OwnerRecropReviewError(f"v4 geometry/freshness QA failed: {summary}")
    if summary["holdout_ohlcv_rows_materialized"] != 0 or label_files:
        raise OwnerRecropReviewError(f"v4 safety QA failed: {summary}")
    write_json(building / "summary.json", summary)
    write_jsonl(building / "source_audit.jsonl", source_audits)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "source_manifest_sha256": sha256_file(source_manifest),
        "review_manifest_sha256": manifest_sha,
        "review_html_sha256": sha256_file(index_path),
        "n_source_rows": 50,
        "n_review_box_proposals": 44,
        "n_owner_reject_no_box": 6,
        "holdout_ohlcv_rows_materialized": 0,
        "yolo_labels_written": 0,
        "training_started": False,
        "active_or_frozen_modified": False,
    }
    write_json(building / "build_receipt.json", receipt)
    os.replace(building, final_dir)
    return receipt
