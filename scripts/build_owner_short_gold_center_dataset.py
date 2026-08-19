#!/usr/bin/env python3
"""Build the full Owner-short center-crop YOLO baseline dataset.

Positive lineage:

* exact independent Owner boxes from ``owner_side_review/review_sheet.csv``;
* only rows the Owner manually classified as ``short``;
* orange target = central half of that original box, clamped to 4--7 bars;
* compact context = 5--7 pre-core bars plus 3--5 post-core bars.

Time discipline:

* overlapping input windows on the same symbol form one dependency block;
* the last 15% dependency blocks are validation;
* train must end at least 150 15m bars before the earliest validation input;
* the complete input window must be before the holdout boundary.

Easy negatives are matched one-for-one to positives by symbol, split and window
length.  They are sampled from the same time block, outside every known Owner
box plus a 12-bar guard.  Later return, holdout, model scores and validation
labels are never used for selection.  This baseline pack is suitable only for
the first training pass needed to mine hard negatives; it is not production
eligible and is never promoted automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_local_signal_v2_stageb import (  # noqa: E402
    BAR_MINUTES,
    HOLDOUT_START,
    PURGE_BARS,
    sha256_file,
)
from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    _series_groups,
    load_preholdout_prefix,
    source_path_for_symbol,
)
from scripts.build_owner_gold_center_crop_review import (  # noqa: E402
    central_core,
    dynamic_context,
    yolo_iou,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


PROTOCOL = "owner_short_gold_center_dataset_v1_20260811"
DEFAULT_SHEET = ROOT / "analysis/output/owner_side_review/review_sheet.csv"
DEFAULT_REGISTRY = ROOT / "data/benchmark_exemplars.json"
DEFAULT_OUT = ROOT / "datasets/owner_short_gold_center_v1"
DEFAULT_AUDIT_HTML = ROOT / "analysis/html/p1_owner_short_gold_center_dataset_audit_20260811.html"
VAL_FRAC = 0.15
NEG_GUARD_BARS = 12
NEG_MAX_TRIES = 1000
MA_WARMUP_BARS = 120


def stable_seed(*parts: object) -> int:
    payload = "|".join(map(str, parts)).encode()
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


def event_id(symbol: str, first_start: int, last_end: int) -> str:
    payload = f"{PROTOCOL}|{symbol}|{first_start}|{last_end}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _star_boxes(path: Path) -> dict[str, list[tuple[float, float, float, float]]]:
    document = json.loads(path.read_text(encoding="utf-8"))["exemplars"]
    return {
        str(stem): [
            (float(box["cx"]), float(box["cy"]), float(box["w"]), float(box["h"]))
            for box in info.get("boxes", [])
        ]
        for stem, info in document.items()
    }


def plan_positives(
    sheet_path: Path,
    registry_path: Path,
    groups: dict[tuple[str, str], list[Path]],
) -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, Any]]:
    """Plan every Owner-short center crop without opening market rows."""
    sheet = pd.read_csv(sheet_path)
    sheet["cut_time"] = pd.to_datetime(sheet["cut_time"], utc=True, errors="raise")
    short = sheet[sheet["owner_side"].astype(str).str.lower().eq("short")].copy()
    if bool((short["cut_time"] >= HOLDOUT_START).any()):
        raise ValueError("Owner-short sheet contains holdout rows")
    stars = _star_boxes(registry_path)
    source_by_symbol: dict[str, Path] = {}
    plans: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for row in short.to_dict("records"):
        symbol = str(row["symbol"])
        if symbol not in source_by_symbol:
            source = source_path_for_symbol(symbol, groups)
            if source is None:
                skips["ambiguous_or_missing_source_csv"] += 1
                continue
            source_by_symbol[symbol] = source
        source_end = int(row["cut_global"])
        source_width = int(row["width_bars"])
        source_start = source_end - source_width + 1
        core_start, core_end = central_core(source_start, source_end)
        pre_bars, post_bars = dynamic_context(source_start, source_end, core_start, core_end)
        win_start = core_start - pre_bars
        win_end = core_end + post_bars
        cut_time = pd.Timestamp(row["cut_time"])
        start_time = cut_time + timedelta(minutes=(win_start - source_end) * BAR_MINUTES)
        end_time = cut_time + timedelta(minutes=(win_end - source_end) * BAR_MINUTES)
        if end_time >= HOLDOUT_START:
            skips["complete_window_touches_holdout"] += 1
            continue
        candidate_box = (
            float(row["yolo_xc"]),
            float(row["yolo_yc"]),
            float(row["yolo_w"]),
            float(row["yolo_h"]),
        )
        star_iou = max(
            (yolo_iou(candidate_box, box) for box in stars.get(str(row["stem"]), [])),
            default=0.0,
        )
        plans.append(
            {
                "sample_id": str(row["box_id"]),
                "symbol": symbol,
                "source_stem": str(row["stem"]),
                "source_csv": str(source_by_symbol[symbol].relative_to(ROOT)),
                "source_owner_global": [source_start, source_end],
                "source_owner_bars": source_width,
                "source_owner_cut_time": cut_time.isoformat(),
                "core_global": [core_start, core_end],
                "core_bars": core_end - core_start + 1,
                "pre_bars": pre_bars,
                "post_bars": post_bars,
                "win_start": win_start,
                "win_end": win_end,
                "win_len": win_end - win_start + 1,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "exact_star_box": bool(star_iou >= 0.999),
                "star_box_iou": star_iou,
                "source_owner_gold_confirmed": True,
                "center_crop_protocol_owner_directed": True,
                "production_eligible": False,
            }
        )
    if not plans:
        raise ValueError("no Owner-short plans")
    profile = {
        "sheet_rows": len(sheet),
        "owner_short_rows": len(short),
        "planned_rows": len(plans),
        "symbols": len({row["symbol"] for row in plans}),
        "exact_star_rows": sum(row["exact_star_box"] for row in plans),
        "skips": dict(skips),
        "holdout_rows": 0,
    }
    return plans, sheet, profile


def dependency_blocks(plans: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group overlapping same-symbol input windows into inseparable blocks."""
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for plan in plans:
        by_symbol[str(plan["symbol"])].append(plan)
    blocks: list[list[dict[str, Any]]] = []
    for symbol, rows in sorted(by_symbol.items()):
        rows.sort(key=lambda row: (int(row["win_start"]), int(row["win_end"]), row["sample_id"]))
        current: list[dict[str, Any]] = []
        current_end = -1
        for row in rows:
            if current and int(row["win_start"]) > current_end:
                blocks.append(current)
                current = []
                current_end = -1
            current.append(row)
            current_end = max(current_end, int(row["win_end"]))
        if current:
            blocks.append(current)
    return blocks


def deduplicate_positive_plans(
    plans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Collapse duplicate Owner annotations that produce the same training target.

    Some historical review rows are aliases of the same underlying market window
    and resolve to identical center-crop geometry.  Keeping both would silently
    overweight that target.  Prefer an exact-star lineage when available, retain
    every aliased Owner id on the canonical row, and split only after deduplication.
    """
    groups: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for plan in plans:
        key = (
            str(plan["symbol"]),
            int(plan["win_start"]),
            int(plan["win_end"]),
            tuple(map(int, plan["core_global"])),
        )
        groups[key].append(plan)

    unique: list[dict[str, Any]] = []
    duplicate_groups = 0
    duplicate_rows_removed = 0
    for rows in groups.values():
        rows.sort(key=lambda row: (not bool(row["exact_star_box"]), str(row["sample_id"])))
        canonical = dict(rows[0])
        owner_ids = sorted(str(row["sample_id"]) for row in rows)
        canonical["owner_annotation_ids"] = owner_ids
        canonical["owner_annotation_count"] = len(owner_ids)
        unique.append(canonical)
        if len(rows) > 1:
            duplicate_groups += 1
            duplicate_rows_removed += len(rows) - 1
    unique.sort(key=lambda row: (str(row["symbol"]), int(row["win_start"]), str(row["sample_id"])))
    return unique, {
        "duplicate_target_groups": duplicate_groups,
        "duplicate_annotation_rows_removed": duplicate_rows_removed,
        "unique_positive_targets": len(unique),
    }


def assign_time_splits(
    plans: list[dict[str, Any]],
    *,
    val_frac: float = VAL_FRAC,
    purge_bars: int = PURGE_BARS,
) -> dict[str, Any]:
    """Assign dependency blocks to chronological train/val with a purge gap."""
    blocks = dependency_blocks(plans)
    blocks.sort(
        key=lambda block: (
            max(pd.Timestamp(row["end_time"]) for row in block),
            str(block[0]["symbol"]),
            str(block[0]["sample_id"]),
        )
    )
    n_val = max(1, int(round(len(blocks) * val_frac)))
    val_blocks = blocks[-n_val:]
    earlier = blocks[:-n_val]
    val_start = min(pd.Timestamp(row["start_time"]) for block in val_blocks for row in block)
    train_cutoff = val_start - timedelta(minutes=purge_bars * BAR_MINUTES)
    split_by_sample: dict[str, str] = {}
    block_ids: dict[str, str] = {}
    block_counts: Counter[str] = Counter()
    for block in blocks:
        first_start = min(int(row["win_start"]) for row in block)
        last_end = max(int(row["win_end"]) for row in block)
        block_id = event_id(str(block[0]["symbol"]), first_start, last_end)
        if block in val_blocks:
            split = "val"
        elif block in earlier and max(pd.Timestamp(row["end_time"]) for row in block) <= train_cutoff:
            split = "train"
        else:
            split = "drop"
        block_counts[split] += 1
        for row in block:
            split_by_sample[str(row["sample_id"])] = split
            block_ids[str(row["sample_id"])] = block_id
    for row in plans:
        row["split"] = split_by_sample[str(row["sample_id"])]
        row["dependency_id"] = block_ids[str(row["sample_id"])]
    kept = [row for row in plans if row["split"] in {"train", "val"}]
    train_end = max(pd.Timestamp(row["end_time"]) for row in kept if row["split"] == "train")
    val_start_actual = min(pd.Timestamp(row["start_time"]) for row in kept if row["split"] == "val")
    gap_bars = (val_start_actual - train_end).total_seconds() / (BAR_MINUTES * 60)
    if gap_bars < purge_bars:
        raise ValueError(f"purge gap {gap_bars} < {purge_bars}")
    if {row["dependency_id"] for row in kept if row["split"] == "train"} & {
        row["dependency_id"] for row in kept if row["split"] == "val"
    }:
        raise ValueError("dependency block crosses split")
    return {
        "dependency_blocks": len(blocks),
        "dependency_block_counts": dict(block_counts),
        "row_counts": dict(Counter(row["split"] for row in plans)),
        "val_start_min": val_start_actual.isoformat(),
        "train_end_max": train_end.isoformat(),
        "purge_bars": purge_bars,
        "actual_gap_bars": gap_bars,
    }


def owner_forbidden_intervals(sheet: pd.DataFrame) -> dict[str, list[tuple[int, int]]]:
    """Return all known Owner boxes, both directions, expanded by a guard."""
    forbidden: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in sheet.to_dict("records"):
        if pd.Timestamp(row["cut_time"]) >= HOLDOUT_START:
            continue
        end = int(row["cut_global"])
        start = end - int(row["width_bars"]) + 1
        forbidden[str(row["symbol"])].append(
            (max(0, start - NEG_GUARD_BARS), end + NEG_GUARD_BARS)
        )
    for symbol in forbidden:
        forbidden[symbol].sort()
    return forbidden


def overlaps(interval: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    start, end = interval
    return any(start <= other_end and end >= other_start for other_start, other_end in others)


def plan_easy_negatives(
    positives: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    forbidden: dict[str, list[tuple[int, int]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Match one empty real-market window to each positive without future scoring."""
    time_bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for split in ("train", "val"):
        rows = [row for row in positives if row["split"] == split]
        time_bounds[split] = (
            min(pd.Timestamp(row["start_time"]) for row in rows),
            max(pd.Timestamp(row["end_time"]) for row in rows),
        )
    selected_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    negative_rows: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for positive in sorted(positives, key=lambda row: (row["split"], row["symbol"], row["sample_id"])):
        symbol = str(positive["symbol"])
        split = str(positive["split"])
        frame = frames[symbol]
        times = pd.to_datetime(frame["open_time"], utc=True)
        lower_time, upper_time = time_bounds[split]
        low = max(MA_WARMUP_BARS, int(times.searchsorted(lower_time, side="left")))
        window_len = int(positive["win_len"])
        high = min(len(frame) - window_len, int(times.searchsorted(upper_time, side="right")) - window_len)
        if high < low:
            skips["no_time_room"] += 1
            continue
        rng = np.random.default_rng(stable_seed(PROTOCOL, "easy_neg", positive["sample_id"]))
        chosen: tuple[int, int] | None = None
        for _attempt in range(NEG_MAX_TRIES):
            start = int(rng.integers(low, high + 1))
            end = start + window_len - 1
            interval = (start, end)
            if overlaps(interval, forbidden.get(symbol, [])):
                continue
            if overlaps(interval, selected_intervals[symbol]):
                continue
            chosen = interval
            break
        if chosen is None:
            # Dense symbols can exhaust random non-overlapping placements.  Keep
            # the stronger all-Owner-box guard, but allow overlap with another
            # already-selected background as long as the interval is not an
            # exact duplicate.  This preserves same-symbol/time/window matching
            # without crossing into another market or reading later rows.
            used_exact = set(selected_intervals[symbol])
            for start in range(low, high + 1):
                interval = (start, start + window_len - 1)
                if interval in used_exact:
                    continue
                if overlaps(interval, forbidden.get(symbol, [])):
                    continue
                chosen = interval
                break
        if chosen is None:
            skips["sampling_exhausted"] += 1
            continue
        selected_intervals[symbol].append(chosen)
        start, end = chosen
        negative_rows.append(
            {
                "sample_id": f"neg_{positive['sample_id']}",
                "matched_positive_id": positive["sample_id"],
                "symbol": symbol,
                "source_csv": positive["source_csv"],
                "split": split,
                "win_start": start,
                "win_end": end,
                "win_len": window_len,
                "start_time": pd.Timestamp(times.iloc[start]).isoformat(),
                "end_time": pd.Timestamp(times.iloc[end]).isoformat(),
                "selection_method": "same_symbol_split_window_random_outside_all_owner_boxes",
                "owner_guard_bars": NEG_GUARD_BARS,
                "later_return_used": False,
                "model_score_used": False,
                "production_eligible": False,
            }
        )
    return negative_rows, dict(skips)


def render_dataset(
    positives: list[dict[str, Any]],
    negatives: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render positive and empty negative labels into one YOLO directory."""
    rendered_positive: list[dict[str, Any]] = []
    rendered_negative: list[dict[str, Any]] = []
    enriched = {symbol: add_mas(frame) for symbol, frame in frames.items()}
    for positive in positives:
        split = str(positive["split"])
        image_path = output_dir / "images" / split / f"{positive['sample_id']}.png"
        label_path = output_dir / "labels" / split / f"{positive['sample_id']}.txt"
        window = enriched[str(positive["symbol"])].iloc[
            int(positive["win_start"]) : int(positive["win_end"]) + 1
        ].reset_index(drop=True)
        image, transform = render_chart(window, out_path=None)
        core_start, core_end = map(int, positive["core_global"])
        local_start = core_start - int(positive["win_start"])
        local_end = core_end - int(positive["win_start"])
        box = yolo_box_from_bars(transform, window, local_start, local_end)
        if box is None:
            raise ValueError(f"empty positive box: {positive['sample_id']}")
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(image_path), image):
            raise OSError(image_path)
        label_path.write_text(
            f"0 {box[0]:.6f} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f}\n",
            encoding="utf-8",
        )
        item = dict(positive)
        item.update(
            {
                "class": "positive",
                "yolo_box": list(box),
                "image_path": str(image_path.relative_to(ROOT)),
                "label_path": str(label_path.relative_to(ROOT)),
                "image_sha256": sha256_file(image_path),
                "label_sha256": sha256_file(label_path),
            }
        )
        rendered_positive.append(item)
    for negative in negatives:
        split = str(negative["split"])
        image_path = output_dir / "images" / split / f"{negative['sample_id']}.png"
        label_path = output_dir / "labels" / split / f"{negative['sample_id']}.txt"
        window = enriched[str(negative["symbol"])].iloc[
            int(negative["win_start"]) : int(negative["win_end"]) + 1
        ].reset_index(drop=True)
        image, _transform = render_chart(window, out_path=None)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(image_path), image):
            raise OSError(image_path)
        label_path.write_text("", encoding="utf-8")
        item = dict(negative)
        item.update(
            {
                "class": "easy_negative",
                "image_path": str(image_path.relative_to(ROOT)),
                "label_path": str(label_path.relative_to(ROOT)),
                "image_sha256": sha256_file(image_path),
                "label_sha256": sha256_file(label_path),
            }
        )
        rendered_negative.append(item)
    return rendered_positive, rendered_negative


def assert_unique_training_examples(rows: list[dict[str, Any]]) -> None:
    """Reject byte-identical image/label pairs before they reach YOLO."""
    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (str(row["image_sha256"]), str(row["label_sha256"]))
        previous = seen.get(key)
        if previous is not None:
            raise ValueError(
                f"duplicate training example: {previous} and {row['sample_id']}"
            )
        seen[key] = str(row["sample_id"])


def build_audit_html(
    positives: list[dict[str, Any]],
    negatives: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    """Return a file://-safe paired visual audit over width and split strata."""
    negative_by_positive = {str(row["matched_positive_id"]): row for row in negatives}
    selected: list[dict[str, Any]] = []
    for split in ("train", "val"):
        for width in (4, 5, 6, 7):
            cohort = [
                row
                for row in positives
                if row["split"] == split and int(row["core_bars"]) == width
            ]
            cohort.sort(key=lambda row: stable_seed(PROTOCOL, "audit", row["sample_id"]))
            selected.extend(cohort[:8])
    cards: list[str] = []
    for index, positive in enumerate(selected, 1):
        negative = negative_by_positive.get(str(positive["sample_id"]))
        xc, yc, box_width, box_height = map(float, positive["yolo_box"])
        left = (xc - box_width / 2) * 100
        top = (yc - box_height / 2) * 100
        pos_src = Path("../../").joinpath(positive["image_path"]).as_posix()
        neg_panel = "<div class='missing'>无安全同币背景（未放宽Owner保护区）</div>"
        if negative is not None:
            neg_src = Path("../../").joinpath(negative["image_path"]).as_posix()
            neg_panel = f"<img loading='lazy' src='{neg_src}' alt='matched easy negative'>"
        cards.append(
            f"""<article><h2>#{index:02d} {positive['symbol']} · {positive['split']}</h2>
<div class="facts">原框{positive['source_owner_bars']}根 → 中心{positive['core_bars']}根 · W{positive['win_len']} · 前{positive['pre_bars']} / 后{positive['post_bars']} · dependency {positive['dependency_id']}</div>
<div class="pair"><figure><figcaption>正例：Owner原框中心橙框</figcaption><div class="chart"><img loading="lazy" src="{pos_src}" alt="positive"><i style="left:{left:.4f}%;top:{top:.4f}%;width:{box_width*100:.4f}%;height:{box_height*100:.4f}%"></i></div></figure>
<figure><figcaption>同币 · 同split · 同W真实空背景</figcaption>{neg_panel}</figure></div></article>"""
        )
    counts = summary["counts"]
    split = summary["split_profile"]
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Owner空头金标中心裁切全量审计</title>
<style>body{{margin:0;background:#edf2f5;color:#172631;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header{{background:#14212c;color:white;padding:25px 30px}}h1{{margin:0 0 8px}}header p{{margin:5px 0;color:#cfdae2}}main{{max-width:1550px;margin:18px auto;padding:0 18px}}article{{background:white;border-radius:11px;padding:12px;margin-bottom:16px;box-shadow:0 2px 8px #0002}}h2{{margin:0 0 5px;font-size:17px}}.facts{{color:#60707c;margin-bottom:9px;font-size:13px}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}figure{{margin:0;border:1px solid #d5dde3;border-radius:7px;overflow:hidden}}figcaption{{padding:7px 9px;background:#f7f9fa;font-weight:650}}figure>img,.chart>img{{width:100%;display:block}}.chart{{position:relative}}.chart i{{position:absolute;border:4px solid #e08a00;box-sizing:border-box}}.missing{{min-height:190px;display:grid;place-items:center;color:#8a5b00;background:#fff7df}}@media(max-width:850px){{.pair{{grid-template-columns:1fr}}}}</style></head>
<body><header><h1>Owner空头金标中心裁切 · 全量配对审计</h1><p>正例 train {counts['train_positive']} / val {counts['val_positive']}；easy-negative train {counts['train_easy_negative']} / val {counts['val_easy_negative']}。</p><p>依赖块 {split['dependency_blocks']}；150根purge，实际间隔 {split['actual_gap_bars']:.0f}根。橙框来自Owner原框中心，不是Codex重画。</p></header><main>{''.join(cards)}</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    groups = _series_groups()
    raw_plans, sheet, source_profile = plan_positives(args.sheet, args.registry, groups)
    plans, dedup_profile = deduplicate_positive_plans(raw_plans)
    source_profile.update(dedup_profile)
    source_profile["planned_rows_before_dedup"] = source_profile.pop("planned_rows")
    source_profile["planned_rows"] = len(plans)
    source_profile["exact_star_rows"] = sum(row["exact_star_box"] for row in plans)
    split_profile = assign_time_splits(plans)
    positives = [row for row in plans if row["split"] in {"train", "val"}]

    max_end_by_symbol: dict[str, int] = defaultdict(int)
    source_by_symbol: dict[str, Path] = {}
    for row in positives:
        symbol = str(row["symbol"])
        max_end_by_symbol[symbol] = max(max_end_by_symbol[symbol], int(row["win_end"]))
        source_by_symbol[symbol] = ROOT / str(row["source_csv"])
    frames: dict[str, pd.DataFrame] = {}
    read_audits: dict[str, dict[str, Any]] = {}
    for symbol in sorted(max_end_by_symbol):
        frame, audit = load_preholdout_prefix(source_by_symbol[symbol], max_end_by_symbol[symbol])
        frames[symbol] = frame
        read_audits[symbol] = audit

    forbidden = owner_forbidden_intervals(sheet)
    negatives, negative_skips = plan_easy_negatives(positives, frames, forbidden)
    rendered_positive, rendered_negative = render_dataset(positives, negatives, frames, args.out)
    assert_unique_training_examples(rendered_positive + rendered_negative)

    positive_manifest = args.out / "positive_manifest.jsonl"
    negative_manifest = args.out / "negative_manifest.jsonl"
    positive_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rendered_positive),
        encoding="utf-8",
    )
    negative_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rendered_negative),
        encoding="utf-8",
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "data.yaml").write_text(
        f"path: {args.out.resolve()}\ntrain: images/train\nval: images/val\n"
        "names:\n  0: owner_short_platform\n",
        encoding="utf-8",
    )
    counts = {
        "train_positive": sum(row["split"] == "train" for row in rendered_positive),
        "val_positive": sum(row["split"] == "val" for row in rendered_positive),
        "train_easy_negative": sum(row["split"] == "train" for row in rendered_negative),
        "val_easy_negative": sum(row["split"] == "val" for row in rendered_negative),
    }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "dataset": str(args.out.relative_to(ROOT)),
        "source_profile": source_profile,
        "split_profile": split_profile,
        "counts": counts,
        "positive_core_widths": dict(Counter(int(row["core_bars"]) for row in rendered_positive)),
        "positive_window_lengths": dict(Counter(int(row["win_len"]) for row in rendered_positive)),
        "positive_post_bars": dict(Counter(int(row["post_bars"]) for row in rendered_positive)),
        "negative_skips": negative_skips,
        "negative_owner_guard_bars": NEG_GUARD_BARS,
        "source_read_audits": read_audits,
        "holdout_read": False,
        "later_return_used": False,
        "model_score_used": False,
        "owner_gold_geometry_reused": True,
        "codex_manual_rebox_used": False,
        "hard_negative_status": "pending_after_baseline_training",
        "training_scope": "baseline_for_hard_negative_mining_only",
        "production_eligible": False,
        "auto_promote": False,
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    DEFAULT_AUDIT_HTML.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_AUDIT_HTML.write_text(
        build_audit_html(rendered_positive, rendered_negative, summary), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "source_read_audits"}, ensure_ascii=False, indent=2))
    print(DEFAULT_AUDIT_HTML)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
