#!/usr/bin/env python3
"""Create a conservative Codex first-pass review of the 30 calibration crops.

The only confirmed semantic reference is currently the Owner's ETH short-side
example.  Long-side mirror shapes are therefore isolated as
``mirror_unconfirmed`` rather than silently merged into the positive class or
misused as negatives.  This review is a calibration proposal, never an Owner
gold label: every row remains ``training_eligible=false``.

Review outcomes:

- ``short_keep``: plausible short platform and the legacy core excludes launch;
- ``short_rebox``: plausible short platform, but the legacy box contains launch;
- ``short_hard_negative``: no sufficiently clean short-platform morphology;
- ``mirror_unconfirmed``: plausible long mirror whose class policy is unresolved.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
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

from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    HOLDOUT_START,
    load_preholdout_prefix,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


PROTOCOL = "owner_eth_shortdelay_codex_firstpass_v1_20260811"
DEFAULT_CALIBRATION = ROOT / "analysis/output/owner_eth_shortdelay_calibration30_v1"
DEFAULT_OUT = ROOT / "analysis/output/owner_eth_shortdelay_codex_firstpass_v1"

SHORT_KEEP = "short_keep"
SHORT_REBOX = "short_rebox"
SHORT_HARD_NEGATIVE = "short_hard_negative"
MIRROR_UNCONFIRMED = "mirror_unconfirmed"
STATUSES = (SHORT_KEEP, SHORT_REBOX, SHORT_HARD_NEGATIVE, MIRROR_UNCONFIRMED)

# Manual, image-by-image Codex first pass against the Owner ETH short reference.
# ``revised_local`` is measured in the original calibration crop and is used
# only for SHORT_REBOX rows.  It is not a rule or an automatic label generator.
FIRST_PASS: dict[str, dict[str, Any]] = {
    "post3_01_fbf4cf2dce81c15f": {"status": SHORT_HARD_NEGATIVE, "reason": "no_clear_departure_after_compact_core"},
    "post3_02_f299289efbc86811": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post3_03_defc9fab8c5c3f67": {"status": MIRROR_UNCONFIRMED, "reason": "long_launch_inside_legacy_core"},
    "post3_04_b3795a34234f0111": {"status": MIRROR_UNCONFIRMED, "reason": "long_launch_inside_legacy_core"},
    "post3_05_d469b18635744b8a": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post3_06_9960fc90ec866841": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post3_07_b074852f51b01da3": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post3_08_e2367b3d9a81db14": {"status": SHORT_REBOX, "reason": "legacy_core_begins_with_short_launch", "revised_local": [3, 8]},
    "post3_09_2de27e350f3719f2": {"status": SHORT_HARD_NEGATIVE, "reason": "core_too_volatile_for_precision_first_target"},
    "post3_10_b6f07110764d1736": {"status": SHORT_REBOX, "reason": "legacy_core_contains_short_launch", "revised_local": [4, 9]},
    "post4_01_5dbc11e9c63e14fc": {"status": SHORT_HARD_NEGATIVE, "reason": "two_sided_large_bars_remain_after_rebox_attempt"},
    "post4_02_63cd543bdb6cea8e": {"status": SHORT_KEEP, "reason": "tight_ma_platform_then_short_departure"},
    "post4_03_e97e5f378680edf0": {"status": MIRROR_UNCONFIRMED, "reason": "long_launch_inside_legacy_core"},
    "post4_04_d7f7f70ba5fc672c": {"status": SHORT_HARD_NEGATIVE, "reason": "large_two_sided_bars_inside_core"},
    "post4_05_cc9b036d7dac5b7d": {"status": MIRROR_UNCONFIRMED, "reason": "long_launch_inside_legacy_core"},
    "post4_06_22e69852730a27fe": {"status": MIRROR_UNCONFIRMED, "reason": "long_launch_inside_legacy_core"},
    "post4_07_66a6f6b28ea012dd": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post4_08_927e4ea9792c594b": {"status": SHORT_KEEP, "reason": "ma_rejection_then_short_departure"},
    "post4_09_46341661b893ad17": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post4_10_6e5583ec7ca9643f": {"status": SHORT_KEEP, "reason": "tight_sideways_core_then_short_departure"},
    "post5_01_6e4c77c178d33381": {"status": SHORT_KEEP, "reason": "small_top_platform_then_short_departure"},
    "post5_02_ae39397bfa9c4f75": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post5_03_7765e4ba74ef9415": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post5_04_1ee4075f2dbf9df6": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post5_05_5d24615540d10ec8": {"status": SHORT_KEEP, "reason": "compressed_rollover_then_short_departure"},
    "post5_06_5e8b8747807fd831": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post5_07_97b69842bb563adc": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post5_08_0856615c9978775b": {"status": MIRROR_UNCONFIRMED, "reason": "long_side_departure"},
    "post5_09_7c8fd08046a8bbfb": {"status": SHORT_REBOX, "reason": "legacy_core_contains_short_launch", "revised_local": [4, 9]},
    "post5_10_63a1f55cd00168c5": {"status": SHORT_REBOX, "reason": "legacy_core_contains_short_launch", "revised_local": [4, 9]},
}

REPRESENTATIVES = {
    SHORT_KEEP: (
        "post4_02_63cd543bdb6cea8e",
        "post4_08_927e4ea9792c594b",
        "post4_10_6e5583ec7ca9643f",
        "post5_01_6e4c77c178d33381",
    ),
    SHORT_REBOX: (
        "post3_08_e2367b3d9a81db14",
        "post3_10_b6f07110764d1736",
        "post5_09_7c8fd08046a8bbfb",
        "post5_10_63a1f55cd00168c5",
    ),
    SHORT_HARD_NEGATIVE: (
        "post3_01_fbf4cf2dce81c15f",
        "post3_09_2de27e350f3719f2",
        "post4_01_5dbc11e9c63e14fc",
        "post4_04_d7f7f70ba5fc672c",
    ),
    MIRROR_UNCONFIRMED: (
        "post3_02_f299289efbc86811",
        "post3_05_d469b18635744b8a",
        "post4_03_e97e5f378680edf0",
        "post5_02_ae39397bfa9c4f75",
    ),
}

STATUS_COLOR = {
    SHORT_KEEP: (50, 150, 30),
    SHORT_REBOX: (20, 145, 220),
    SHORT_HARD_NEGATIVE: (45, 45, 225),
    MIRROR_UNCONFIRMED: (190, 90, 150),
}


def load_rows(calibration_dir: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in (calibration_dir / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    ids = {str(row["calibration_id"]) for row in rows}
    if ids != set(FIRST_PASS):
        missing = sorted(ids - set(FIRST_PASS))
        stale = sorted(set(FIRST_PASS) - ids)
        raise ValueError(f"first-pass coverage mismatch missing={missing} stale={stale}")
    return rows


def apply_first_pass(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        decision = FIRST_PASS[str(row["calibration_id"])]
        status = str(decision["status"])
        if status not in STATUSES:
            raise ValueError(f"invalid status {status}")
        old_start, old_end = map(int, row["core_global"])
        revised_global: list[int] | None = None
        revised_local_source: list[int] | None = None
        if status == SHORT_REBOX:
            revised_local_source = list(map(int, decision["revised_local"]))
            revised_global = [
                int(row["win_start"]) + revised_local_source[0],
                int(row["win_start"]) + revised_local_source[1],
            ]
            width = revised_global[1] - revised_global[0] + 1
            if not 4 <= width <= 7:
                raise ValueError(f"revised core width outside 4-7: {row['calibration_id']}")
            if revised_global[1] >= old_end:
                raise ValueError(f"rebox must remove launch-side bars: {row['calibration_id']}")
        elif "revised_local" in decision:
            raise ValueError(f"non-rebox has revised geometry: {row['calibration_id']}")
        row.update(
            {
                "codex_firstpass_status": status,
                "codex_firstpass_reason": str(decision["reason"]),
                "legacy_core_global": [old_start, old_end],
                "revised_core_global": revised_global,
                "revised_local_in_source_crop": revised_local_source,
                "reviewer": "codex_visual_first_pass_against_owner_eth_short_reference",
                "owner_confirmed": False,
                "semantic_status": "codex_firstpass_only",
                "geometry_status": (
                    "codex_rebox_proposal" if status == SHORT_REBOX else row["geometry_status"]
                ),
                "training_eligible": False,
                "production_eligible": False,
            }
        )
        reviewed.append(row)
    return reviewed


def _rect_from_box(image: np.ndarray, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    xc, yc, bw, bh = box
    return (
        int(round((xc - bw / 2) * width)),
        int(round((yc - bh / 2) * height)),
        int(round((xc + bw / 2) * width)),
        int(round((yc + bh / 2) * height)),
    )


def _dashed_rectangle(
    image: np.ndarray,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    *,
    dash: int = 14,
    thickness: int = 3,
) -> None:
    x1, y1, x2, y2 = rect
    for start in range(x1, x2, dash * 2):
        cv2.line(image, (start, y1), (min(start + dash, x2), y1), color, thickness, cv2.LINE_AA)
        cv2.line(image, (start, y2), (min(start + dash, x2), y2), color, thickness, cv2.LINE_AA)
    for start in range(y1, y2, dash * 2):
        cv2.line(image, (x1, start), (x1, min(start + dash, y2)), color, thickness, cv2.LINE_AA)
        cv2.line(image, (x2, start), (x2, min(start + dash, y2)), color, thickness, cv2.LINE_AA)


def render_review_row(row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    status = str(row["codex_firstpass_status"])
    if status == SHORT_REBOX:
        new_core = list(map(int, row["revised_core_global"]))
        win_start = new_core[0] - int(row["pre_bars"])
        win_end = new_core[1] + int(row["post_bars"])
        active_core = new_core
    else:
        win_start = int(row["win_start"])
        win_end = int(row["win_end"])
        active_core = list(map(int, row["legacy_core_global"]))
    frame, read_audit = load_preholdout_prefix(ROOT / row["source_csv"], win_end)
    if pd.Timestamp(frame.iloc[win_end]["open_time"]) >= HOLDOUT_START:
        raise ValueError("review render touched holdout")
    enriched = add_mas(frame)
    window = enriched.iloc[win_start : win_end + 1].reset_index(drop=True)
    image, transform = render_chart(window, out_path=None)
    active_local = (active_core[0] - win_start, active_core[1] - win_start)
    active_box = yolo_box_from_bars(transform, window, *active_local)
    if active_box is None:
        raise ValueError(f"empty active box: {row['calibration_id']}")
    vis = image.copy()
    height, width = vis.shape[:2]
    color = STATUS_COLOR[status]

    if status == SHORT_HARD_NEGATIVE:
        old_box = yolo_box_from_bars(transform, window, *active_local)
        if old_box is not None:
            _dashed_rectangle(vis, _rect_from_box(vis, old_box), color)
        cv2.line(vis, (70, 80), (width - 70, height - 40), color, 6, cv2.LINE_AA)
        cv2.line(vis, (width - 70, 80), (70, height - 40), color, 6, cv2.LINE_AA)
    else:
        rect = _rect_from_box(vis, active_box)
        cv2.rectangle(vis, (rect[0], rect[1]), (rect[2], rect[3]), color, 5, cv2.LINE_AA)
        cv2.line(vis, (rect[0], 42), (rect[0], height - 1), color, 2, cv2.LINE_AA)
        cv2.line(vis, (rect[2], 42), (rect[2], height - 1), color, 2, cv2.LINE_AA)

    if status == SHORT_REBOX:
        old_start, old_end = map(int, row["legacy_core_global"])
        clipped_start = max(old_start, win_start)
        clipped_end = min(old_end, win_end)
        if clipped_start <= clipped_end:
            old_box = yolo_box_from_bars(
                transform,
                window,
                clipped_start - win_start,
                clipped_end - win_start,
            )
            if old_box is not None:
                _dashed_rectangle(vis, _rect_from_box(vis, old_box), (40, 40, 230))

    cv2.rectangle(vis, (0, 0), (width, 44), (250, 250, 250), -1)
    title = (
        f"{status.upper()} | {row['symbol']} | PRE {row['pre_bars']} | "
        f"CORE {active_core[1]-active_core[0]+1} | POST {row['post_bars']}"
    )
    cv2.putText(vis, title, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.67, color, 2, cv2.LINE_AA)
    cv2.rectangle(vis, (0, height - 34), (width, height), (250, 250, 250), -1)
    cv2.putText(
        vis,
        str(row["codex_firstpass_reason"]),
        (12, height - 11),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (45, 55, 62),
        1,
        cv2.LINE_AA,
    )
    image_path = output_dir / "images" / status / f"{row['calibration_id']}.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), vis)
    output = dict(row)
    output.update(
        {
            "review_win_start": win_start,
            "review_win_end": win_end,
            "review_win_len": win_end - win_start + 1,
            "review_core_global": active_core,
            "review_core_local": list(active_local),
            "review_image_path": str(image_path.relative_to(ROOT)),
            "review_read_audit": read_audit,
        }
    )
    return output


def build_representative_board(rows: list[dict[str, Any]], output_path: Path) -> None:
    by_id = {str(row["calibration_id"]): row for row in rows}
    card_w = 650
    chart_h = int(round(card_w * 742 / 1280))
    card_h = chart_h + 38
    columns = list(STATUSES)
    rows_n = max(len(REPRESENTATIVES[status]) for status in columns)
    header_h = 105
    sheet = np.full((header_h + rows_n * card_h, len(columns) * card_w, 3), 242, np.uint8)
    cv2.putText(
        sheet,
        "CODEX FIRST PASS - OWNER CONFIRMATION REQUIRED - NO TRAINING LABELS",
        (24, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.84,
        (25, 36, 44),
        2,
        cv2.LINE_AA,
    )
    for col, status in enumerate(columns):
        color = STATUS_COLOR[status]
        cv2.rectangle(sheet, (col * card_w, 55), ((col + 1) * card_w - 1, 103), color, -1)
        cv2.putText(
            sheet,
            status.upper(),
            (col * card_w + 15, 88),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.66,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        for row_i, calibration_id in enumerate(REPRESENTATIVES[status]):
            row = by_id[calibration_id]
            image = cv2.imread(str(ROOT / row["review_image_path"]))
            if image is None:
                raise FileNotFoundError(row["review_image_path"])
            resized = cv2.resize(image, (card_w, chart_h), interpolation=cv2.INTER_AREA)
            y0 = header_h + row_i * card_h
            x0 = col * card_w
            sheet[y0 : y0 + chart_h, x0 : x0 + card_w] = resized
            footer = f"{row['calibration_id']} | owner_confirmed=false"
            cv2.putText(
                sheet,
                footer,
                (x0 + 10, y0 + chart_h + 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.43,
                (50, 60, 68),
                1,
                cv2.LINE_AA,
            )
            cv2.rectangle(sheet, (x0, y0), (x0 + card_w - 1, y0 + card_h - 1), (205, 211, 216), 2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), sheet)


def run(calibration_dir: Path, output_dir: Path) -> dict[str, Any]:
    source_rows = load_rows(calibration_dir)
    reviewed = apply_first_pass(source_rows)
    rendered = [render_review_row(row, output_dir) for row in reviewed]
    board_path = output_dir / "representative_semantic_calibration16.png"
    build_representative_board(rendered, board_path)
    counts = Counter(str(row["codex_firstpass_status"]) for row in rendered)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "source_calibration": str(calibration_dir.relative_to(ROOT)),
        "reviewer": "codex_visual_first_pass_against_owner_eth_short_reference",
        "owner_confirmed": False,
        "confirmed_direction_scope": "short_only_reference",
        "mirror_policy": "isolate_as_mirror_unconfirmed; neither positive nor negative",
        "counts": dict(counts),
        "total": len(rendered),
        "representative_board": str(board_path.relative_to(ROOT)),
        "quality_gates": {
            "all_30_reviewed": len(rendered) == 30,
            "status_partition_complete": sum(counts.values()) == 30 and set(counts) == set(STATUSES),
            "rebox_width_4_to_7": all(
                row["codex_firstpass_status"] != SHORT_REBOX
                or 4 <= row["review_core_global"][1] - row["review_core_global"][0] + 1 <= 7
                for row in rendered
            ),
            "rebox_removes_launch_side_bars": all(
                row["codex_firstpass_status"] != SHORT_REBOX
                or row["review_core_global"][1] < row["legacy_core_global"][1]
                for row in rendered
            ),
            "post_3_to_5_only": all(3 <= int(row["post_bars"]) <= 5 for row in rendered),
            "holdout_rows_materialized_zero": all(
                row["review_read_audit"]["holdout_rows_materialized"] == 0 for row in rendered
            ),
            "owner_confirmation_not_faked": all(not row["owner_confirmed"] for row in rendered),
            "training_still_blocked": all(not row["training_eligible"] for row in rendered),
            "production_still_blocked": all(not row["production_eligible"] for row in rendered),
        },
        "training_eligible": False,
        "production_eligible": False,
        "holdout_read": False,
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"first-pass quality gate failed: {summary['quality_gates']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "first_pass.jsonl").open("w") as handle:
        for row in rendered:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    summary = run(args.calibration, args.out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
