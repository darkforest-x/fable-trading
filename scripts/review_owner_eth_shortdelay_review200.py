#!/usr/bin/env python3
"""Freeze the Codex visual first-pass partition for dynamic review200.

This file records an image-by-image review, not a numeric morphology rule.
Owner confirmed the short-only protocol and green/orange/red direction, but did
not individually confirm these 200 samples.  Therefore all rows remain blocked
from training.  ``short_rebox_pending`` is intentionally separate from
``short_keep`` so a plausible short pattern cannot inherit a legacy box that
already contains the launch.
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


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_local_signal_v2_stageb import sha256_file  # noqa: E402


PROTOCOL = "owner_eth_shortdelay_review200_codex_firstpass_v1_20260811"
DEFAULT_SOURCE = ROOT / "analysis/output/owner_eth_shortdelay_dynamic_review200_v1"
DEFAULT_OUT = ROOT / "analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1"

SHORT_KEEP = "short_keep"
SHORT_REBOX_PENDING = "short_rebox_pending"
SHORT_HARD_NEGATIVE = "short_hard_negative"
MIRROR_EXCLUDED = "mirror_excluded"
STATUSES = (SHORT_KEEP, SHORT_REBOX_PENDING, SHORT_HARD_NEGATIVE, MIRROR_EXCLUDED)

MIRROR_INDICES = (
    1, 3, 10, 11, 14, 20, 22, 23, 25, 26, 27, 30, 31, 36, 40, 42, 48, 49,
    52, 55, 59, 60, 62, 63, 66, 68, 74, 77, 78, 79, 87, 88, 89, 90, 92, 95,
    100, 101, 103, 107, 108, 110, 113, 114, 117, 119, 120, 126, 134, 138, 140,
    146, 148, 149, 150, 152, 153, 154, 155, 157, 158, 161, 163, 166, 172, 173,
    174, 178, 179, 181, 182, 188, 191, 193,
)
KEEP_INDICES = (
    2, 4, 5, 7, 16, 28, 29, 32, 33, 37, 41, 45, 53, 61, 65, 70, 71, 80, 81,
    86, 91, 93, 102, 109, 112, 128, 132, 135, 141, 156, 162, 169, 170, 176,
    183, 184, 187, 190, 192, 200,
)
HARD_NEGATIVE_INDICES = (
    9, 15, 17, 18, 43, 44, 58, 96, 111, 116, 118, 123, 124, 125, 127, 130,
    131, 136, 137, 144, 147, 175, 177, 185, 186,
)
REBOX_INDICES = (
    6, 8, 12, 13, 19, 21, 24, 34, 35, 38, 39, 46, 47, 50, 51, 54, 56, 57,
    64, 67, 69, 72, 73, 75, 76, 82, 83, 84, 85, 94, 97, 98, 99, 104, 105,
    106, 115, 121, 122, 129, 133, 139, 142, 143, 145, 151, 159, 160, 164,
    165, 167, 168, 171, 180, 189, 194, 195, 196, 197, 198, 199,
)

INDEX_STATUS = {
    **{index: MIRROR_EXCLUDED for index in MIRROR_INDICES},
    **{index: SHORT_KEEP for index in KEEP_INDICES},
    **{index: SHORT_HARD_NEGATIVE for index in HARD_NEGATIVE_INDICES},
    **{index: SHORT_REBOX_PENDING for index in REBOX_INDICES},
}

STATUS_REASON = {
    SHORT_KEEP: "clean_short_platform_legacy_box_plausible",
    SHORT_REBOX_PENDING: "short_semantics_plausible_but_launch_inside_legacy_box",
    SHORT_HARD_NEGATIVE: "insufficiently_clean_short_platform_for_precision_first_target",
    MIRROR_EXCLUDED: "long_mirror_excluded_from_short_training_and_not_a_negative",
}

STATUS_COLOR = {
    SHORT_KEEP: (44, 154, 39),
    SHORT_REBOX_PENDING: (20, 145, 225),
    SHORT_HARD_NEGATIVE: (45, 45, 230),
    MIRROR_EXCLUDED: (190, 90, 150),
}

REPRESENTATIVES = {
    SHORT_KEEP: (2, 29, 80, 190),
    SHORT_REBOX_PENDING: (6, 34, 104, 195),
    SHORT_HARD_NEGATIVE: (9, 43, 118, 185),
    MIRROR_EXCLUDED: (1, 30, 110, 178),
}


def load_rows(source_dir: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in (source_dir / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len(rows) != 200:
        raise ValueError(f"expected 200 source rows, got {len(rows)}")
    observed = {int(str(row["calibration_id"])[1:4]) for row in rows}
    if observed != set(range(1, 201)) or set(INDEX_STATUS) != observed:
        raise ValueError("first-pass index partition is incomplete")
    return rows


def apply_first_pass(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        index = int(str(row["calibration_id"])[1:4])
        status = INDEX_STATUS[index]
        row.update(
            {
                "codex_firstpass_status": status,
                "codex_firstpass_reason": STATUS_REASON[status],
                "review_method": "manual_visual_review_of_8_frozen_contact_sheets",
                "owner_protocol_confirmed": True,
                "sample_owner_confirmed": False,
                "geometry_review_status": (
                    "rebox_required_pending_per_image_geometry"
                    if status == SHORT_REBOX_PENDING
                    else "legacy_proposal_plausible" if status == SHORT_KEEP
                    else "not_applicable"
                ),
                "training_eligible": False,
                "production_eligible": False,
            }
        )
        reviewed.append(row)
    return reviewed


def _box_rect(image: np.ndarray, yolo_box: list[float]) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    xc, yc, bw, bh = map(float, yolo_box)
    return (
        int(round((xc - bw / 2) * width)),
        int(round((yc - bh / 2) * height)),
        int(round((xc + bw / 2) * width)),
        int(round((yc + bh / 2) * height)),
    )


def render_status_image(row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    status = str(row["codex_firstpass_status"])
    image = cv2.imread(str(ROOT / row["image_path"]))
    if image is None:
        raise FileNotFoundError(row["image_path"])
    color = STATUS_COLOR[status]
    height, width = image.shape[:2]
    rect = _box_rect(image, row["yolo_box"])
    if status == SHORT_HARD_NEGATIVE:
        cv2.line(image, (65, 70), (width - 65, height - 35), color, 6, cv2.LINE_AA)
        cv2.line(image, (width - 65, 70), (65, height - 35), color, 6, cv2.LINE_AA)
    else:
        cv2.rectangle(image, (rect[0], rect[1]), (rect[2], rect[3]), color, 5, cv2.LINE_AA)
    cv2.rectangle(image, (0, 0), (width, 44), (250, 250, 250), -1)
    cv2.putText(
        image,
        f"{status.upper()} | {row['calibration_id']} | {row['symbol']}",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.64,
        color,
        2,
        cv2.LINE_AA,
    )
    if status == SHORT_REBOX_PENDING:
        cv2.putText(
            image,
            "LEGACY RED BOX REJECTED - PER-IMAGE REBOX REQUIRED",
            (10, height - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )
    output_path = output_dir / "images" / status / f"{row['calibration_id']}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), image)
    output = dict(row)
    output["status_image_path"] = str(output_path.relative_to(ROOT))
    output["status_image_sha256"] = sha256_file(output_path)
    return output


def build_contact_sheet(rows: list[dict[str, Any]], path: Path, title: str) -> None:
    card_w = 640
    chart_h = int(round(card_w * 742 / 1280))
    card_h = chart_h + 34
    cols = 4
    rows_n = (len(rows) + cols - 1) // cols
    header_h = 70
    sheet = np.full((header_h + rows_n * card_h, cols * card_w, 3), 244, np.uint8)
    cv2.putText(sheet, title, (18, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (22, 35, 44), 2, cv2.LINE_AA)
    for index, row in enumerate(rows):
        image = cv2.imread(str(ROOT / row["status_image_path"]))
        if image is None:
            raise FileNotFoundError(row["status_image_path"])
        resized = cv2.resize(image, (card_w, chart_h), interpolation=cv2.INTER_AREA)
        row_i, col_i = divmod(index, cols)
        x0 = col_i * card_w
        y0 = header_h + row_i * card_h
        sheet[y0 : y0 + chart_h, x0 : x0 + card_w] = resized
        cv2.putText(
            sheet,
            f"{row['calibration_id']} | owner_sample_confirmed=false",
            (x0 + 8, y0 + chart_h + 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (45, 55, 62),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(sheet, (x0, y0), (x0 + card_w - 1, y0 + card_h - 1), (205, 212, 217), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), sheet)


def build_representative_board(rows: list[dict[str, Any]], path: Path) -> None:
    by_index = {int(str(row["calibration_id"])[1:4]): row for row in rows}
    ordered = [by_index[index] for status in STATUSES for index in REPRESENTATIVES[status]]
    card_w = 650
    chart_h = int(round(card_w * 742 / 1280))
    card_h = chart_h + 34
    header_h = 105
    sheet = np.full((header_h + 4 * card_h, 4 * card_w, 3), 242, np.uint8)
    cv2.putText(
        sheet,
        "DYNAMIC REVIEW200 - CODEX FIRST PASS - SAMPLE OWNER CONFIRMATION PENDING",
        (22, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.82,
        (23, 35, 44),
        2,
        cv2.LINE_AA,
    )
    for col, status in enumerate(STATUSES):
        color = STATUS_COLOR[status]
        cv2.rectangle(sheet, (col * card_w, 55), ((col + 1) * card_w - 1, 103), color, -1)
        cv2.putText(sheet, status.upper(), (col * card_w + 14, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.63, (255, 255, 255), 2, cv2.LINE_AA)
        for row_i, index in enumerate(REPRESENTATIVES[status]):
            row = by_index[index]
            image = cv2.imread(str(ROOT / row["status_image_path"]))
            if image is None:
                raise FileNotFoundError(row["status_image_path"])
            resized = cv2.resize(image, (card_w, chart_h), interpolation=cv2.INTER_AREA)
            x0 = col * card_w
            y0 = header_h + row_i * card_h
            sheet[y0 : y0 + chart_h, x0 : x0 + card_w] = resized
            cv2.putText(sheet, row["calibration_id"], (x0 + 8, y0 + chart_h + 23), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (45, 55, 62), 1, cv2.LINE_AA)
            cv2.rectangle(sheet, (x0, y0), (x0 + card_w - 1, y0 + card_h - 1), (205, 212, 217), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), sheet)


def run(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    rows = apply_first_pass(load_rows(source_dir))
    rendered = [render_status_image(row, output_dir) for row in rows]
    counts = Counter(row["codex_firstpass_status"] for row in rendered)
    queue_sheets: list[str] = []
    for status in STATUSES:
        cohort = [row for row in rendered if row["codex_firstpass_status"] == status]
        for offset in range(0, len(cohort), 24):
            chunk = cohort[offset : offset + 24]
            number = offset // 24 + 1
            path = output_dir / "queues" / f"{status}_{number:02d}.png"
            build_contact_sheet(chunk, path, f"{status.upper()} | {offset+1}-{offset+len(chunk)} OF {len(cohort)}")
            queue_sheets.append(str(path.relative_to(ROOT)))
    representative = output_dir / "representative_review200_firstpass16.png"
    build_representative_board(rendered, representative)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "source": str(source_dir.relative_to(ROOT)),
        "source_manifest_sha256": sha256_file(source_dir / "manifest.jsonl"),
        "review_method": "manual_visual_review_of_8_frozen_contact_sheets",
        "counts": dict(counts),
        "representative_board": str(representative.relative_to(ROOT)),
        "queue_sheets": queue_sheets,
        "owner_protocol_confirmed": True,
        "sample_owner_confirmed": False,
        "rebox_geometry_complete": False,
        "quality_gates": {
            "all_200_partitioned": len(rendered) == 200 and sum(counts.values()) == 200,
            "four_statuses_present": set(counts) == set(STATUSES),
            "partition_indices_unique": len(INDEX_STATUS) == 200 and set(INDEX_STATUS) == set(range(1, 201)),
            "mirror_excluded_not_negative": all(
                row["codex_firstpass_status"] != MIRROR_EXCLUDED
                or row["codex_firstpass_reason"] == STATUS_REASON[MIRROR_EXCLUDED]
                for row in rendered
            ),
            "rebox_queue_not_promoted": all(
                row["codex_firstpass_status"] != SHORT_REBOX_PENDING
                or row["geometry_review_status"] == "rebox_required_pending_per_image_geometry"
                for row in rendered
            ),
            "sample_owner_confirmation_not_faked": all(not row["sample_owner_confirmed"] for row in rendered),
            "training_blocked": all(not row["training_eligible"] for row in rendered),
            "production_blocked": all(not row["production_eligible"] for row in rendered),
        },
        "training_eligible": False,
        "production_eligible": False,
        "holdout_read": False,
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"review200 first-pass gate failed: {summary['quality_gates']}")
    with (output_dir / "first_pass.jsonl").open("w") as handle:
        for row in rendered:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    summary = run(args.source, args.out)
    print(json.dumps({"output": str(args.out), "counts": summary["counts"], "gates": summary["quality_gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
