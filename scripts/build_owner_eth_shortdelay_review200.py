#!/usr/bin/env python3
"""Re-render the frozen 200-event owner review pack as dynamic short windows.

Owner confirmed the short-only green/orange/red calibration direction on
2026-08-11.  That confirmation authorizes expansion of the already frozen 200
Stage-A train events, not automatic sample labels or training.

The event set and delay quotas remain unchanged from
``owner_eth_target_review_v2_shortdelay``.  Every image is rebuilt from raw 15m
bars as 6--10 pre-core bars + the legacy 5/7-bar proposal + its frozen 3/4/5
post-core bars.  Legacy boxes remain unreviewed proposals until the image-level
semantic pass completes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, replace
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

from scripts.build_local_signal_v2_stageb import BAR_MINUTES, HOLDOUT_START, PURGE_BARS, sha256_file  # noqa: E402
from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    CalibrationPlan,
    _series_groups,
    _utc,
    load_preholdout_prefix,
    source_path_for_symbol,
    stable_key,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


PROTOCOL = "owner_eth_shortdelay_dynamic_review200_v1_20260811"
DEFAULT_REVIEW = ROOT / "analysis/output/owner_eth_target_review_v2_shortdelay/candidates.jsonl"
DEFAULT_STAGE = ROOT / "datasets/local_signal_v2_stagea_randomcrop_v1/w20_manifest.json"
DEFAULT_OWNER = ROOT / "datasets/dense_owner_w20_midbox/w20_manifest.json"
DEFAULT_RECEIPT = ROOT / "analysis/output/owner_eth_shortdelay_codex_firstpass_v1/owner_confirmation_receipt.json"
DEFAULT_OUT = ROOT / "analysis/output/owner_eth_shortdelay_dynamic_review200_v1"
PRE_CONTEXTS = (6, 7, 8, 9, 10)
EXPECTED_DELAY_COUNTS = Counter({3: 80, 4: 65, 5: 55})
EXPECTED_WIDTH_COUNTS = Counter({5: 100, 7: 100})


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text())
    required = {
        "direction_scope": "short_only",
        "green_orange_red_direction_confirmed": True,
        "mirror_policy": "exclude_from_current_short_training; do_not_use_as_negative",
        "individual_200_labels_owner_confirmed": False,
        "training_authorized": False,
        "holdout_authorized": False,
        "production_authorized": False,
    }
    for key, expected in required.items():
        if receipt.get(key) != expected:
            raise ValueError(f"owner receipt mismatch {key}: {receipt.get(key)!r} != {expected!r}")
    reference = ROOT / receipt["confirmed_reference"]
    if not reference.exists() or sha256_file(reference) != receipt["confirmed_reference_sha256"]:
        raise ValueError("confirmed representative board is missing or changed")
    return receipt


def assign_pre_contexts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Balance pre-context globally and within delay×width strata."""
    assignments: dict[str, int] = {}
    global_counts: Counter[int] = Counter()
    stratum_counts: dict[tuple[int, int], Counter[int]] = defaultdict(Counter)
    ordered = sorted(rows, key=lambda row: stable_key(PROTOCOL, "pre", row["event_id"]))
    for row in ordered:
        stratum = (int(row["post_bars"]), int(row["box_bars"]))
        options = sorted(
            PRE_CONTEXTS,
            key=lambda pre: (
                global_counts[pre],
                stratum_counts[stratum][pre],
                stable_key(PROTOCOL, "pre_tie", row["event_id"], pre),
            ),
        )
        chosen = options[0]
        assignments[str(row["event_id"])] = chosen
        global_counts[chosen] += 1
        stratum_counts[stratum][chosen] += 1
    if global_counts != Counter({pre: len(rows) // len(PRE_CONTEXTS) for pre in PRE_CONTEXTS}):
        raise ValueError(f"pre contexts are not exactly balanced: {global_counts}")
    return assignments


def build_plans(
    review_path: Path,
    stage_path: Path,
    owner_path: Path,
) -> tuple[list[CalibrationPlan], dict[str, Any]]:
    review_rows = load_jsonl(review_path)
    stage_rows = json.loads(stage_path.read_text())
    owner_rows = json.loads(owner_path.read_text())
    if len(review_rows) != 200 or len({row["event_id"] for row in review_rows}) != 200:
        raise ValueError("frozen review pack must contain 200 unique events")
    if Counter(int(row["post_bars"]) for row in review_rows) != EXPECTED_DELAY_COUNTS:
        raise ValueError("frozen delay quotas changed")
    if Counter(int(row["box_bars"]) for row in review_rows) != EXPECTED_WIDTH_COUNTS:
        raise ValueError("frozen width quotas changed")
    stage_by_event = {str(row["event_id"]): row for row in stage_rows}
    owner_by_stem = {str(row["stem"]): row for row in owner_rows}
    if len(stage_by_event) != len(stage_rows) or len(owner_by_stem) != len(owner_rows):
        raise ValueError("source manifest identity is not unique")
    val_rows = [row for row in stage_rows if row["split"] == "val"]
    val_start_min = min(_utc(row["start_time"]) for row in val_rows)
    train_allowed_end = val_start_min - timedelta(minutes=PURGE_BARS * BAR_MINUTES)
    pre_by_event = assign_pre_contexts(review_rows)
    groups = _series_groups()

    plans: list[CalibrationPlan] = []
    for ordinal, review in enumerate(review_rows, start=1):
        event_id = str(review["event_id"])
        stage = stage_by_event.get(event_id)
        if stage is None or stage["split"] != "train":
            raise ValueError(f"event missing from repaired train split: {event_id}")
        owner = owner_by_stem.get(str(stage["source_stem"]))
        if owner is None:
            raise ValueError(f"owner geometry missing: {event_id}")
        core_start, core_end = map(int, owner["small_bars"])
        core_bars = core_end - core_start + 1
        if core_bars != int(review["box_bars"]) or core_bars not in {5, 7}:
            raise ValueError(f"core width drift: {event_id}")
        post_bars = int(review["post_bars"])
        pre_bars = pre_by_event[event_id]
        win_start = core_start - pre_bars
        win_end = core_end + post_bars
        win_len = win_end - win_start + 1
        core_local = (pre_bars, pre_bars + core_bars - 1)
        center = ((core_local[0] + core_local[1]) / 2) / max(win_len - 1, 1)
        anchor = int(stage["mid_global"])
        anchor_time = _utc(stage["anchor_time"])
        start_time = anchor_time + timedelta(minutes=(win_start - anchor) * BAR_MINUTES)
        end_time = anchor_time + timedelta(minutes=(win_end - anchor) * BAR_MINUTES)
        if end_time > train_allowed_end or end_time >= HOLDOUT_START:
            raise ValueError(f"dynamic window crosses frozen train boundary: {event_id}")
        source_path = source_path_for_symbol(str(stage["symbol"]), groups)
        if source_path is None:
            raise ValueError(f"raw series path missing or ambiguous: {stage['symbol']}")
        plans.append(
            CalibrationPlan(
                calibration_id=f"R{ordinal:03d}_{event_id}",
                event_id=event_id,
                source_stem=str(stage["source_stem"]),
                symbol=str(stage["symbol"]),
                stage_split="train",
                source_csv=str(source_path.relative_to(ROOT)),
                mid_global=anchor,
                core_global=(core_start, core_end),
                core_local=core_local,
                core_bars=core_bars,
                pre_bars=pre_bars,
                post_bars=post_bars,
                win_start=win_start,
                win_end=win_end,
                win_len=win_len,
                box_center_ratio=center,
                expected_start_time=start_time.isoformat(),
                expected_anchor_time=anchor_time.isoformat(),
                expected_end_time=end_time.isoformat(),
                time_bucket=str(review["review_group"]),
                semantic_status="unreviewed_after_protocol_confirmation",
                geometry_status="unreviewed_legacy_core_proposal",
                training_eligible=False,
                production_eligible=False,
            )
        )
    profile = {
        "val_metadata_rows_for_boundary_only": len(val_rows),
        "val_images_read": 0,
        "val_labels_read": 0,
        "val_start_min": val_start_min.isoformat(),
        "purge_bars": PURGE_BARS,
        "train_allowed_end": train_allowed_end.isoformat(),
    }
    return plans, profile


def render_plan(plan: CalibrationPlan, output_dir: Path) -> dict[str, Any]:
    frame, read_audit = load_preholdout_prefix(ROOT / plan.source_csv, plan.win_end)
    actual_start = _utc(frame.iloc[plan.win_start]["open_time"])
    actual_anchor = _utc(frame.iloc[plan.mid_global]["open_time"])
    actual_end = _utc(frame.iloc[plan.win_end]["open_time"])
    if (
        actual_start != _utc(plan.expected_start_time)
        or actual_anchor != _utc(plan.expected_anchor_time)
        or actual_end != _utc(plan.expected_end_time)
    ):
        raise ValueError(f"global index/time mismatch: {plan.calibration_id}")
    enriched = add_mas(frame)
    window = enriched.iloc[plan.win_start : plan.win_end + 1].reset_index(drop=True)
    image, transform = render_chart(window, out_path=None)
    box = yolo_box_from_bars(transform, window, *plan.core_local)
    if box is None:
        raise ValueError(f"empty box: {plan.calibration_id}")
    height, width = image.shape[:2]
    xc, yc, bw, bh = box
    x1 = int(round((xc - bw / 2) * width))
    x2 = int(round((xc + bw / 2) * width))
    y1 = int(round((yc - bh / 2) * height))
    y2 = int(round((yc + bh / 2) * height))
    cv2.line(image, (x1, 42), (x1, height - 1), (180, 165, 0), 2, cv2.LINE_AA)
    cv2.line(image, (x2, 42), (x2, height - 1), (180, 165, 0), 2, cv2.LINE_AA)
    cv2.rectangle(image, (x1, y1), (x2, y2), (38, 38, 235), 4, cv2.LINE_AA)
    cv2.rectangle(image, (0, 0), (width, 42), (250, 250, 250), -1)
    caption = (
        f"{plan.calibration_id} | {plan.symbol} | PRE {plan.pre_bars} | "
        f"CORE {plan.core_bars} | POST {plan.post_bars} | W{plan.win_len}"
    )
    cv2.putText(image, caption, (10, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.61, (20, 30, 38), 2, cv2.LINE_AA)
    image_path = output_dir / "images" / f"{plan.calibration_id}.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), image)
    row = asdict(plan)
    row["core_global"] = list(plan.core_global)
    row["core_local"] = list(plan.core_local)
    row.update(
        {
            "yolo_box": list(box),
            "actual_start_time": actual_start.isoformat(),
            "actual_anchor_time": actual_anchor.isoformat(),
            "actual_end_time": actual_end.isoformat(),
            "image_path": str(image_path.relative_to(ROOT)),
            "image_sha256": sha256_file(image_path),
            "read_audit": read_audit,
            "protocol_owner_confirmed": True,
            "sample_owner_confirmed": False,
            "direction_scope": "short_only",
            "mirror_policy": "exclude_not_negative",
        }
    )
    return row


def build_sheet(rows: list[dict[str, Any]], path: Path, sheet_number: int) -> None:
    card_w = 512
    chart_h = int(round(card_w * 742 / 1280))
    card_h = chart_h + 31
    cols = 5
    rows_n = 5
    header_h = 62
    sheet = np.full((header_h + rows_n * card_h, cols * card_w, 3), 244, np.uint8)
    cv2.putText(
        sheet,
        f"DYNAMIC SHORT REVIEW 200 | SHEET {sheet_number:02d} | RED=LEGACY PROPOSAL | OWNER SAMPLE LABELS PENDING",
        (18, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.74,
        (22, 35, 44),
        2,
        cv2.LINE_AA,
    )
    for index, row in enumerate(rows):
        image = cv2.imread(str(ROOT / row["image_path"]))
        if image is None:
            raise FileNotFoundError(row["image_path"])
        resized = cv2.resize(image, (card_w, chart_h), interpolation=cv2.INTER_AREA)
        row_i, col_i = divmod(index, cols)
        x0 = col_i * card_w
        y0 = header_h + row_i * card_h
        sheet[y0 : y0 + chart_h, x0 : x0 + card_w] = resized
        footer = f"{row['calibration_id']} | owner_confirmed=false"
        cv2.putText(sheet, footer, (x0 + 8, y0 + chart_h + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (45, 55, 62), 1, cv2.LINE_AA)
        cv2.rectangle(sheet, (x0, y0), (x0 + card_w - 1, y0 + card_h - 1), (205, 212, 217), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), sheet)


def run(review: Path, stage: Path, owner: Path, receipt_path: Path, output_dir: Path) -> dict[str, Any]:
    receipt = validate_receipt(receipt_path)
    plans, profile = build_plans(review, stage, owner)
    rows = [render_plan(plan, output_dir) for plan in plans]
    sheets: list[str] = []
    for offset in range(0, len(rows), 25):
        sheet_number = offset // 25 + 1
        sheet_path = output_dir / "sheets" / f"sheet_{sheet_number:02d}_R{offset+1:03d}-R{min(offset+25, len(rows)):03d}.png"
        build_sheet(rows[offset : offset + 25], sheet_path, sheet_number)
        sheets.append(str(sheet_path.relative_to(ROOT)))
    delay_counts = Counter(int(row["post_bars"]) for row in rows)
    width_counts = Counter(int(row["core_bars"]) for row in rows)
    pre_counts = Counter(int(row["pre_bars"]) for row in rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "owner_confirmation_receipt": str(receipt_path.relative_to(ROOT)),
        "owner_confirmation_receipt_sha256": sha256_file(receipt_path),
        "confirmed_direction_scope": receipt["direction_scope"],
        "frozen_source_review": str(review.relative_to(ROOT)),
        "frozen_source_review_sha256": sha256_file(review),
        "counts": {
            "total": len(rows),
            "unique_events": len({row["event_id"] for row in rows}),
            "by_post": dict(sorted(delay_counts.items())),
            "by_core": dict(sorted(width_counts.items())),
            "by_pre": dict(sorted(pre_counts.items())),
        },
        "unique_symbols": len({row["symbol"] for row in rows}),
        "window_len_observed": [min(row["win_len"] for row in rows), max(row["win_len"] for row in rows)],
        "box_center_ratio_observed": [min(row["box_center_ratio"] for row in rows), max(row["box_center_ratio"] for row in rows)],
        "profile": profile,
        "sheets": sheets,
        "quality_gates": {
            "exactly_200_unique_events": len(rows) == 200 and len({row["event_id"] for row in rows}) == 200,
            "frozen_delay_counts": delay_counts == EXPECTED_DELAY_COUNTS,
            "frozen_width_counts": width_counts == EXPECTED_WIDTH_COUNTS,
            "pre_context_exactly_balanced": pre_counts == Counter({6: 40, 7: 40, 8: 40, 9: 40, 10: 40}),
            "dynamic_w14_to_22": min(row["win_len"] for row in rows) == 14 and max(row["win_len"] for row in rows) == 22,
            "train_only": all(row["stage_split"] == "train" for row in rows),
            "full_window_before_purge": all(_utc(row["actual_end_time"]) <= _utc(profile["train_allowed_end"]) for row in rows),
            "holdout_rows_materialized_zero": all(row["read_audit"]["holdout_rows_materialized"] == 0 for row in rows),
            "sample_labels_still_unconfirmed": all(not row["sample_owner_confirmed"] for row in rows),
            "training_still_blocked": all(not row["training_eligible"] for row in rows),
            "production_still_blocked": all(not row["production_eligible"] for row in rows),
            "eight_review_sheets": len(sheets) == 8,
        },
        "training_eligible": False,
        "production_eligible": False,
        "holdout_read": False,
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"review200 quality gate failed: {summary['quality_gates']}")
    with (output_dir / "manifest.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--owner", type=Path, default=DEFAULT_OWNER)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    summary = run(args.review, args.stage, args.owner, args.receipt, args.out)
    print(json.dumps({"output": str(args.out), "counts": summary["counts"], "gates": summary["quality_gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
