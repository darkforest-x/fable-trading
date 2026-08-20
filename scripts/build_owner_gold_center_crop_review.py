#!/usr/bin/env python3
"""Build an Owner-review pack from the original starred short gold boxes.

The source geometry is never redrawn by Codex.  Each row must satisfy both:

* the Owner marked the exact Label Studio box as a ``⭐`` exemplar; and
* the Owner later classified that exact independent box as ``short``.

The proposed orange core is the central part of the original Owner box.  Its
width is half of the original span, clamped to 4--7 bars.  The surrounding
training crop uses 5--7 pre-core bars and 3--5 post-core bars, derived only from
the original box margins.  The optional 48-bar future panel is rendered from a
separate prefix and is review-only; it never enters the training image or label.

Raw OHLCV reads are prefix-limited and rejected before the holdout boundary.
This script creates a review artifact only.  Every row remains training-ineligible
until the Owner confirms the source-to-crop contract.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_local_signal_v2_stageb import HOLDOUT_START, sha256_file  # noqa: E402
from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    _series_groups,
    load_preholdout_prefix,
    source_path_for_symbol,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


PROTOCOL = "owner_star_short_gold_center_crop_v1_20260811"
DEFAULT_SHEET = ROOT / "analysis/output/owner_side_review/review_sheet.csv"
DEFAULT_REGISTRY = ROOT / "data/benchmark_exemplars.json"
DEFAULT_STAR_GALLERY = ROOT / "analysis/output/star_benchmark_originals/manifest.json"
DEFAULT_OUT = ROOT / "analysis/output/owner_gold_center_crop_review_v1"
DEFAULT_HTML = ROOT / "analysis/html/p1_owner_gold_center_crop_owner_gate_20260811.html"
FUTURE_BARS = 48
ORIGINAL_COLOR = (35, 35, 230)
CORE_COLOR = (20, 145, 225)
BOUNDARY_COLOR = (20, 145, 225)
FUTURE_BOUNDARY_COLOR = (180, 60, 180)


def yolo_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Return IoU for two normalized ``cx, cy, width, height`` boxes."""

    def rect(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        cx, cy, width, height = box
        return cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2

    ax1, ay1, ax2, ay2 = rect(a)
    bx1, by1, bx2, by2 = rect(b)
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    return intersection / union if union > 0 else 0.0


def central_core(source_start: int, source_end: int) -> tuple[int, int]:
    """Take the central half of an Owner box, bounded to 4--7 visible bars."""
    source_width = source_end - source_start + 1
    if source_width < 4:
        raise ValueError(f"Owner box is too narrow: {source_width}")
    core_width = min(source_width, max(4, min(7, math.ceil(source_width / 2))))
    core_start = source_start + (source_width - core_width) // 2
    return core_start, core_start + core_width - 1


def dynamic_context(
    source_start: int,
    source_end: int,
    core_start: int,
    core_end: int,
) -> tuple[int, int]:
    """Derive compact context from source-box margins without later returns.

    The left side gets 5--7 bars of setup context.  The right side gets only
    3--5 bars, preserving the Owner's short-delay ceiling.
    """
    left_margin = core_start - source_start
    right_margin = source_end - core_end
    pre_bars = min(7, max(5, left_margin + 2))
    post_bars = min(5, max(3, right_margin))
    return pre_bars, post_bars


def _box_rect(image: Any, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    cx, cy, box_width, box_height = box
    return (
        int(round((cx - box_width / 2) * width)),
        int(round((cy - box_height / 2) * height)),
        int(round((cx + box_width / 2) * width)),
        int(round((cy + box_height / 2) * height)),
    )


def _caption(image: Any, text: str) -> Any:
    cv2.rectangle(image, (0, 0), (image.shape[1] - 1, 42), (250, 250, 250), -1)
    cv2.putText(
        image,
        text,
        (10, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (20, 30, 36),
        2,
        cv2.LINE_AA,
    )
    return image


def load_gold_rows(sheet_path: Path, registry_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join exact starred boxes to the Owner's later manual short decisions."""
    sheet = pd.read_csv(sheet_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))["exemplars"]
    gallery_items: dict[str, dict[str, Any]] = {}
    if DEFAULT_STAR_GALLERY.exists():
        gallery = json.loads(DEFAULT_STAR_GALLERY.read_text(encoding="utf-8"))
        gallery_items = {str(item["stem"]): item for item in gallery.get("items", [])}
    short = sheet[sheet["owner_side"].astype(str).str.lower().eq("short")].copy()
    short["cut_time"] = pd.to_datetime(short["cut_time"], utc=True, errors="raise")
    if bool((short["cut_time"] >= HOLDOUT_START).any()):
        raise ValueError("short Owner sheet unexpectedly contains holdout rows")

    rows: list[dict[str, Any]] = []
    for item in short.to_dict("records"):
        star = registry.get(str(item["stem"]))
        if star is None:
            continue
        candidate = (
            float(item["yolo_xc"]),
            float(item["yolo_yc"]),
            float(item["yolo_w"]),
            float(item["yolo_h"]),
        )
        benchmark_boxes = [
            (float(box["cx"]), float(box["cy"]), float(box["w"]), float(box["h"]))
            for box in star.get("boxes", [])
        ]
        best_iou = max((yolo_iou(candidate, box) for box in benchmark_boxes), default=0.0)
        if best_iou < 0.999:
            continue
        item["star_box_iou"] = best_iou
        item["source_export"] = str(star.get("source_export", ""))
        gallery_item = gallery_items.get(str(item["stem"]))
        if gallery_item is not None:
            item["original_image_path"] = str(
                (DEFAULT_STAR_GALLERY.parent / str(gallery_item["raw"])).relative_to(ROOT)
            )
        else:
            item["original_image_path"] = str(item["image_path"])
        rows.append(item)

    rows.sort(key=lambda row: (str(row["cut_time"]), str(row["box_id"])))
    profile = {
        "sheet_rows": int(len(sheet)),
        "owner_short_rows": int(len(short)),
        "owner_short_images": int(short["stem"].nunique()),
        "registry_stems": len(registry),
        "exact_star_short_rows": len(rows),
        "exact_star_short_images": len({str(row["stem"]) for row in rows}),
        "max_cut_time": max(str(row["cut_time"]) for row in rows) if rows else None,
        "holdout_rows": 0,
    }
    return rows, profile


def render_original(row: dict[str, Any], path: Path) -> None:
    source = ROOT / str(row["original_image_path"])
    image = cv2.imread(str(source))
    if image is None:
        raise FileNotFoundError(source)
    owner_box = (
        float(row["yolo_xc"]),
        float(row["yolo_yc"]),
        float(row["yolo_w"]),
        float(row["yolo_h"]),
    )
    rect = _box_rect(image, owner_box)
    cv2.rectangle(image, rect[:2], rect[2:], ORIGINAL_COLOR, 5, cv2.LINE_AA)
    _caption(image, f"ORIGINAL OWNER GOLD | {row['box_id']} | RED=EXACT HAND BOX")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(path)


def render_crop(
    row: dict[str, Any],
    source_csv: Path,
    crop_path: Path,
    future_path: Path,
) -> dict[str, Any]:
    source_width = int(row["width_bars"])
    source_end = int(row["cut_global"])
    source_start = source_end - source_width + 1
    core_start, core_end = central_core(source_start, source_end)
    pre_bars, post_bars = dynamic_context(source_start, source_end, core_start, core_end)
    win_start = core_start - pre_bars
    win_end = core_end + post_bars
    future_end = win_end + FUTURE_BARS

    train_frame, train_audit = load_preholdout_prefix(source_csv, win_end)
    train_enriched = add_mas(train_frame)
    train_window = train_enriched.iloc[win_start : win_end + 1].reset_index(drop=True)
    image, transform = render_chart(train_window, out_path=None)
    core_local = (core_start - win_start, core_end - win_start)
    box = yolo_box_from_bars(transform, train_window, *core_local)
    if box is None:
        raise ValueError(f"empty core box: {row['box_id']}")
    rect = _box_rect(image, box)
    cv2.rectangle(image, rect[:2], rect[2:], CORE_COLOR, 5, cv2.LINE_AA)
    cv2.line(image, (rect[0], 42), (rect[0], image.shape[0] - 1), BOUNDARY_COLOR, 2, cv2.LINE_AA)
    cv2.line(image, (rect[2], 42), (rect[2], image.shape[0] - 1), BOUNDARY_COLOR, 2, cv2.LINE_AA)
    _caption(
        image,
        f"TRAIN CROP W{len(train_window)} | ORANGE=CENTER {core_local[0]}-{core_local[1]} "
        f"({core_end-core_start+1} bars) | POST {post_bars}",
    )
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(crop_path), image):
        raise OSError(crop_path)

    future_frame, future_audit = load_preholdout_prefix(source_csv, future_end)
    future_enriched = add_mas(future_frame)
    future_window = future_enriched.iloc[win_start : future_end + 1].reset_index(drop=True)
    future_image, future_transform = render_chart(future_window, out_path=None)
    cutoff_local = win_end - win_start
    cutoff_x = int(round(future_transform.x_at(cutoff_local)))
    cv2.line(
        future_image,
        (cutoff_x, 42),
        (cutoff_x, future_image.shape[0] - 1),
        FUTURE_BOUNDARY_COLOR,
        4,
        cv2.LINE_AA,
    )
    _caption(future_image, "REVIEW ONLY | PURPLE=TRAIN CUTOFF | +48 FUTURE BARS")
    future_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(future_path), future_image):
        raise OSError(future_path)

    return {
        **row,
        "source_owner_global": [source_start, source_end],
        "source_owner_bars": source_width,
        "core_global": [core_start, core_end],
        "core_local": list(core_local),
        "core_bars": core_end - core_start + 1,
        "pre_bars": pre_bars,
        "post_bars": post_bars,
        "win_start": win_start,
        "win_end": win_end,
        "win_len": len(train_window),
        "future_bars": FUTURE_BARS,
        "source_csv": str(source_csv.relative_to(ROOT)),
        "owner_original_path": str((DEFAULT_OUT / "originals" / f"{row['box_id']}.png").relative_to(ROOT)),
        "training_crop_path": str(crop_path.relative_to(ROOT)),
        "future_review_path": str(future_path.relative_to(ROOT)),
        "training_image_sha256": sha256_file(crop_path),
        "future_review_sha256": sha256_file(future_path),
        "train_read_audit": train_audit,
        "future_read_audit": future_audit,
        "geometry_method": "central_half_of_exact_owner_gold_clamped_4_7",
        "source_owner_gold_confirmed": True,
        "center_crop_protocol_owner_directed": True,
        "owner_sample_confirmed": False,
        "training_eligible": False,
        "production_eligible": False,
    }


def relative_from_html(path: Path) -> str:
    return Path("../output/owner_gold_center_crop_review_v1").joinpath(
        path.relative_to(DEFAULT_OUT)
    ).as_posix()


def build_html(rows: list[dict[str, Any]], profile: dict[str, Any]) -> str:
    cards: list[str] = []
    for index, row in enumerate(rows, 1):
        original = relative_from_html(ROOT / str(row["owner_original_path"]))
        crop = relative_from_html(ROOT / str(row["training_crop_path"]))
        future = relative_from_html(ROOT / str(row["future_review_path"]))
        cards.append(
            f"""
<article class="card">
  <h2>#{index:02d} {html.escape(str(row['symbol']))}</h2>
  <div class="facts">原手框 {row['source_owner_bars']}根 → 中心橙框 {row['core_bars']}根 · 训练W{row['win_len']} · 前文{row['pre_bars']} · 后文{row['post_bars']} · {html.escape(str(row['cut_time']))}</div>
  <div class="panels">
    <figure><figcaption>① 原始金标：红框是Owner原手框</figcaption><img loading="lazy" src="{html.escape(original)}"></figure>
    <figure><figcaption>② 训练输入：橙框取原框正中心</figcaption><img loading="lazy" src="{html.escape(crop)}"></figure>
    <figure><figcaption>③ 仅人工审核：额外未来48根</figcaption><img loading="lazy" src="{html.escape(future)}"></figure>
  </div>
  <small>{html.escape(str(row['box_id']))} · exact ⭐ IoU={row['star_box_iou']:.3f}</small>
</article>"""
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>原始空头金标中心裁切审核</title>
<style>
body{{margin:0;background:#eef2f5;color:#17232d;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
header{{padding:24px 30px;background:#14212c;color:white}}h1{{margin:0 0 8px}}header p{{margin:4px 0;color:#cfdae2;line-height:1.55}}
main{{max-width:1800px;margin:18px auto;padding:0 18px}}.card{{background:white;margin:0 0 18px;padding:14px;border-radius:12px;box-shadow:0 2px 8px #0002}}
h2{{margin:0 0 6px;font-size:18px}}.facts{{color:#51616e;margin-bottom:10px}}.panels{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}
figure{{margin:0;border:1px solid #d5dde3;border-radius:8px;overflow:hidden;background:#fff}}figcaption{{padding:8px 10px;background:#f7f9fa;font-weight:650}}img{{display:block;width:100%}}small{{display:block;margin-top:8px;color:#75828c}}
@media(max-width:1000px){{.panels{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>原始空头金标 → 中心橙框</h1>
<p>{len(rows)} 个可恢复原图的精确交集：Owner原始⭐框 ∩ Owner后来亲自确认做空。橙框不是Codex目测，也不是模型预测。</p>
<p>精确联结共{profile['exact_star_short_rows']}框；其中{profile['exact_star_short_rows']-len(rows)}框的历史原始PNG当前不在本机，已诚实跳过，没有用训练裁图冒充。</p>
<p>训练图只含W12–17左右短窗；右侧48根只供人工审核，目录和哈希与训练图隔离。当前仍未成为训练标签。</p></header><main>{''.join(cards)}</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows, profile = load_gold_rows(args.sheet, args.registry)
    if args.limit:
        rows = rows[: args.limit]
    groups = _series_groups()
    rendered: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for row in rows:
        source_csv = source_path_for_symbol(str(row["symbol"]), groups)
        if source_csv is None:
            skips["ambiguous_or_missing_source_csv"] += 1
            continue
        try:
            original_path = args.out / "originals" / f"{row['box_id']}.png"
            crop_path = args.out / "training_crops" / f"{row['box_id']}.png"
            future_path = args.out / "review_future_only" / f"{row['box_id']}_future48.png"
            render_original(row, original_path)
            item = render_crop(row, source_csv, crop_path, future_path)
            item["owner_original_path"] = str(original_path.relative_to(ROOT))
            rendered.append(item)
        except (FileNotFoundError, ValueError) as error:
            skips[type(error).__name__] += 1
            print(f"skip {row['box_id']}: {error}")

    if not rendered:
        raise SystemExit("no gold rows rendered")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.jsonl"
    manifest_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rendered),
        encoding="utf-8",
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "profile": profile,
        "rendered": len(rendered),
        "skips": dict(skips),
        "source_widths": dict(Counter(int(row["source_owner_bars"]) for row in rendered)),
        "core_widths": dict(Counter(int(row["core_bars"]) for row in rendered)),
        "window_lengths": dict(Counter(int(row["win_len"]) for row in rendered)),
        "post_bars": dict(Counter(int(row["post_bars"]) for row in rendered)),
        "holdout_read": False,
        "owner_gold_geometry_reused": True,
        "center_crop_protocol_owner_directed": True,
        "codex_manual_rebox_used": False,
        "model_prediction_used": False,
        "later_return_used_for_geometry": False,
        "future_data_in_training_image": False,
        "future_data_in_training_label": False,
        "owner_decisions_preselected": 0,
        "training_eligible_rows": 0,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(build_html(rendered, profile), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(args.html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
