#!/usr/bin/env python3
"""Build per-image geometry proposals for 61 short-pattern rebox candidates.

The first-pass review established only that these rows plausibly express the
Owner's short-platform semantics while their inherited legacy box includes too
much of the launch.  This script does not guess a batch offset and does not
promote any row.  It first adds a visible local bar index to each already-frozen,
pre-holdout review image, then records and re-renders exact 4--7 bar boundaries
chosen independently from those workboards.

The re-render reads only the minimum pre-holdout CSV prefix required by the new
window.  No validation image/label, holdout row, future return, or model score
participates.  All proposals remain blocked until Owner sample review.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_local_signal_v2_stageb import sha256_file  # noqa: E402
from scripts.build_owner_eth_shortdelay_calibration import (  # noqa: E402
    _utc,
    load_preholdout_prefix,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402
from scripts.review_owner_eth_shortdelay_review200 import (  # noqa: E402
    REBOX_INDICES,
    SHORT_REBOX_PENDING,
)


PROTOCOL = "owner_eth_shortdelay_review200_rebox_proposals_v1_20260811"
DEFAULT_SOURCE = (
    ROOT
    / "analysis/output/owner_eth_shortdelay_review200_codex_firstpass_v1/first_pass.jsonl"
)
DEFAULT_OUT = ROOT / "analysis/output/owner_eth_shortdelay_review200_rebox_v1"
CHART_LEFT = 12
CHART_RIGHT = 12
INDEX_STRIP_TOP = 690
INDEX_COLOR = (55, 55, 55)
LEGACY_COLOR = (45, 45, 230)
PROPOSAL_COLOR = (20, 145, 225)

# Local bar boundaries refer to the frozen indexed image, not the recropped
# proposal.  Every pair was selected independently from the seven indexed
# workboards: the end is the bar immediately before the visually identified
# launch, and the start covers the tightest 4--7 bar platform/transition that
# still carries the setup semantics.  This is deliberately data, not a formula.
REBOX_LOCAL_BOUNDS: dict[int, tuple[int, int]] = {
    6: (3, 7),
    8: (5, 9),
    12: (1, 5),
    13: (2, 6),
    19: (3, 7),
    21: (9, 14),
    24: (6, 10),
    34: (2, 6),
    35: (6, 10),
    38: (4, 8),
    39: (5, 9),
    46: (5, 9),
    47: (5, 9),
    50: (4, 8),
    51: (5, 9),
    54: (2, 6),
    56: (9, 12),
    57: (3, 7),
    64: (3, 7),
    67: (1, 5),
    69: (4, 8),
    72: (6, 10),
    73: (7, 11),
    75: (7, 11),
    76: (9, 12),
    82: (7, 11),
    83: (4, 8),
    84: (6, 10),
    85: (2, 6),
    94: (10, 15),
    97: (7, 11),
    98: (7, 11),
    99: (6, 10),
    104: (4, 8),
    105: (5, 9),
    106: (5, 9),
    115: (1, 5),
    121: (8, 12),
    122: (6, 10),
    129: (2, 6),
    133: (3, 7),
    139: (9, 13),
    142: (4, 8),
    143: (3, 7),
    145: (2, 6),
    151: (4, 8),
    159: (3, 7),
    160: (6, 10),
    164: (3, 7),
    165: (5, 9),
    167: (2, 6),
    168: (4, 8),
    171: (5, 9),
    180: (3, 7),
    189: (2, 6),
    194: (5, 9),
    195: (8, 12),
    196: (1, 5),
    197: (10, 14),
    198: (9, 13),
    199: (7, 13),
}


def load_rebox_rows(path: Path) -> list[dict[str, Any]]:
    """Load exactly the frozen 61-row rebox queue in ordinal order."""
    all_rows = [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    rows = [
        row
        for row in all_rows
        if row.get("codex_firstpass_status") == SHORT_REBOX_PENDING
    ]
    rows.sort(key=lambda row: int(str(row["calibration_id"])[1:4]))
    observed = tuple(int(str(row["calibration_id"])[1:4]) for row in rows)
    if observed != REBOX_INDICES:
        raise ValueError("rebox queue no longer matches the frozen 61 indices")
    if any(row.get("training_eligible") for row in rows):
        raise ValueError("a pending rebox row was unexpectedly promoted")
    return rows


def _x_at(local_index: int, win_len: int, width: int) -> int:
    if win_len <= 1:
        return CHART_LEFT
    plot_width = width - CHART_LEFT - CHART_RIGHT
    return int(CHART_LEFT + local_index / (win_len - 1) * plot_width)


def render_indexed_image(row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Overlay local 0..W-1 bar ids without changing the frozen chart geometry."""
    source = ROOT / str(row["image_path"])
    image = cv2.imread(str(source))
    if image is None:
        raise FileNotFoundError(source)
    height, width = image.shape[:2]
    if height < 720 or width < 1200:
        raise ValueError(f"unexpected review image shape: {image.shape}")

    win_len = int(row["win_len"])
    core_start, core_end = map(int, row["core_local"])
    cv2.rectangle(image, (0, INDEX_STRIP_TOP), (width - 1, height - 1), (248, 248, 248), -1)
    for local_index in range(win_len):
        x = _x_at(local_index, win_len, width)
        in_legacy = core_start <= local_index <= core_end
        color = LEGACY_COLOR if in_legacy else INDEX_COLOR
        cv2.line(image, (x, INDEX_STRIP_TOP), (x, INDEX_STRIP_TOP + 9), color, 1, cv2.LINE_AA)
        text = str(local_index)
        text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)[0]
        cv2.putText(
            image,
            text,
            (x - text_size[0] // 2, height - 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            color,
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        image,
        f"legacy={core_start}-{core_end} | choose NEW 4-7 bars; end immediately before launch",
        (10, INDEX_STRIP_TOP - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        LEGACY_COLOR,
        2,
        cv2.LINE_AA,
    )
    path = output_dir / "indexed" / f"{row['calibration_id']}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"failed to write {path}")
    output = dict(row)
    output.update(
        {
            "indexed_image_path": str(path.relative_to(ROOT)),
            "indexed_image_sha256": sha256_file(path),
            "geometry_review_status": "indexed_pending_per_image_boundary",
            "sample_owner_confirmed": False,
            "training_eligible": False,
            "production_eligible": False,
        }
    )
    return output


def build_workboard(rows: list[dict[str, Any]], path: Path, page_number: int) -> None:
    """Render at most nine readable cards in a 3x3 review sheet."""
    card_w = 900
    chart_h = int(round(card_w * 742 / 1280))
    card_h = chart_h + 38
    cols = 3
    rows_n = (len(rows) + cols - 1) // cols
    header_h = 76
    sheet = np.full((header_h + rows_n * card_h, cols * card_w, 3), 244, np.uint8)
    cv2.putText(
        sheet,
        f"REBOX WORKBOARD {page_number:02d} | RED=REJECTED LEGACY CORE | NUMBERS=LOCAL BAR INDEX",
        (20, 47),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (23, 35, 44),
        2,
        cv2.LINE_AA,
    )
    for position, row in enumerate(rows):
        image = cv2.imread(str(ROOT / row["indexed_image_path"]))
        if image is None:
            raise FileNotFoundError(row["indexed_image_path"])
        resized = cv2.resize(image, (card_w, chart_h), interpolation=cv2.INTER_AREA)
        row_i, col_i = divmod(position, cols)
        x0 = col_i * card_w
        y0 = header_h + row_i * card_h
        sheet[y0 : y0 + chart_h, x0 : x0 + card_w] = resized
        footer = (
            f"{row['calibration_id']} | {row['symbol']} | "
            f"legacy {row['core_local'][0]}-{row['core_local'][1]} | post {row['post_bars']}"
        )
        cv2.putText(
            sheet,
            footer,
            (x0 + 8, y0 + chart_h + 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (42, 53, 61),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(
            sheet,
            (x0, y0),
            (x0 + card_w - 1, y0 + card_h - 1),
            (202, 210, 216),
            1,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), sheet):
        raise OSError(f"failed to write {path}")


def _box_rect(image: np.ndarray, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    height, width = image.shape[:2]
    xc, yc, box_w, box_h = box
    return (
        int(round((xc - box_w / 2) * width)),
        int(round((yc - box_h / 2) * height)),
        int(round((xc + box_w / 2) * width)),
        int(round((yc + box_h / 2) * height)),
    )


def _dashed_rectangle(
    image: np.ndarray,
    rect: tuple[int, int, int, int],
    color: tuple[int, int, int],
    *,
    thickness: int = 3,
    dash: int = 14,
) -> None:
    x1, y1, x2, y2 = rect
    for x in range(x1, x2 + 1, dash * 2):
        cv2.line(image, (x, y1), (min(x + dash, x2), y1), color, thickness, cv2.LINE_AA)
        cv2.line(image, (x, y2), (min(x + dash, x2), y2), color, thickness, cv2.LINE_AA)
    for y in range(y1, y2 + 1, dash * 2):
        cv2.line(image, (x1, y), (x1, min(y + dash, y2)), color, thickness, cv2.LINE_AA)
        cv2.line(image, (x2, y), (x2, min(y + dash, y2)), color, thickness, cv2.LINE_AA)


def apply_manual_geometry(row: dict[str, Any]) -> dict[str, Any]:
    """Translate one manually selected indexed boundary into global geometry."""
    index = int(str(row["calibration_id"])[1:4])
    selected_start, selected_end = REBOX_LOCAL_BOUNDS[index]
    width = selected_end - selected_start + 1
    if not 4 <= width <= 7:
        raise ValueError(f"R{index:03d}: proposed width {width} is outside 4..7")
    if not 0 <= selected_start <= selected_end < int(row["win_len"]):
        raise ValueError(f"R{index:03d}: proposed local boundary is outside frozen window")

    legacy_global_start, legacy_global_end = map(int, row["core_global"])
    proposed_global_start = int(row["win_start"]) + selected_start
    proposed_global_end = int(row["win_start"]) + selected_end
    pre_bars = int(row["pre_bars"])
    post_bars = int(row["post_bars"])
    proposed_win_start = proposed_global_start - pre_bars
    proposed_win_end = proposed_global_end + post_bars
    proposed_win_len = proposed_win_end - proposed_win_start + 1
    proposed_core_local = (pre_bars, pre_bars + width - 1)
    if proposed_global_end >= legacy_global_end:
        raise ValueError(f"R{index:03d}: proposal did not move the core end earlier")

    output = dict(row)
    output.update(
        {
            "legacy_core_global": [legacy_global_start, legacy_global_end],
            "legacy_core_local_in_frozen_window": list(map(int, row["core_local"])),
            "proposal_selected_local_in_frozen_window": [selected_start, selected_end],
            "proposal_launch_local_in_frozen_window": selected_end + 1,
            "proposal_core_global": [proposed_global_start, proposed_global_end],
            "proposal_core_local": list(proposed_core_local),
            "proposal_core_bars": width,
            "proposal_win_start": proposed_win_start,
            "proposal_win_end": proposed_win_end,
            "proposal_win_len": proposed_win_len,
            "proposal_start_delta_vs_legacy": proposed_global_start - legacy_global_start,
            "proposal_end_delta_vs_legacy": proposed_global_end - legacy_global_end,
            "proposal_method": "per_image_visual_boundary_on_indexed_workboards",
            "proposal_semantics": "tight_short_platform_transition_ending_before_launch",
            "geometry_review_status": "codex_rebox_proposal_pending_owner_sample_review",
            "sample_owner_confirmed": False,
            "training_eligible": False,
            "production_eligible": False,
        }
    )
    return output


def render_rebox_proposal(row: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Rebuild the proposed dynamic crop and compare it with clipped legacy geometry."""
    required_end = max(int(row["proposal_win_end"]), int(row["mid_global"]))
    frame, read_audit = load_preholdout_prefix(ROOT / str(row["source_csv"]), required_end)
    enriched = add_mas(frame)
    win_start = int(row["proposal_win_start"])
    win_end = int(row["proposal_win_end"])
    window = enriched.iloc[win_start : win_end + 1].reset_index(drop=True)
    if len(window) != int(row["proposal_win_len"]):
        raise ValueError(f"short proposed window: {row['calibration_id']}")
    image, transform = render_chart(window, out_path=None)
    core_start, core_end = map(int, row["proposal_core_local"])
    proposed_box = yolo_box_from_bars(transform, window, core_start, core_end)
    if proposed_box is None:
        raise ValueError(f"empty proposed box: {row['calibration_id']}")

    legacy_start, legacy_end = map(int, row["legacy_core_global"])
    visible_start = max(legacy_start, win_start)
    visible_end = min(legacy_end, win_end)
    legacy_box = None
    if visible_start <= visible_end:
        legacy_box = yolo_box_from_bars(
            transform,
            window,
            visible_start - win_start,
            visible_end - win_start,
        )
    if legacy_box is not None:
        _dashed_rectangle(image, _box_rect(image, legacy_box), LEGACY_COLOR)

    proposed_rect = _box_rect(image, proposed_box)
    cv2.rectangle(
        image,
        (proposed_rect[0], proposed_rect[1]),
        (proposed_rect[2], proposed_rect[3]),
        PROPOSAL_COLOR,
        5,
        cv2.LINE_AA,
    )
    height, width_px = image.shape[:2]
    cv2.line(
        image,
        (proposed_rect[0], 42),
        (proposed_rect[0], height - 1),
        PROPOSAL_COLOR,
        2,
        cv2.LINE_AA,
    )
    cv2.line(
        image,
        (proposed_rect[2], 42),
        (proposed_rect[2], height - 1),
        PROPOSAL_COLOR,
        2,
        cv2.LINE_AA,
    )
    cv2.rectangle(image, (0, 0), (width_px, 42), (250, 250, 250), -1)
    old_start, old_end = map(int, row["legacy_core_local_in_frozen_window"])
    selected_start, selected_end = map(int, row["proposal_selected_local_in_frozen_window"])
    caption = (
        f"{row['calibration_id']} | ORANGE NEW {selected_start}-{selected_end} "
        f"({row['proposal_core_bars']} bars) | RED DASH OLD {old_start}-{old_end} | "
        f"POST {row['post_bars']}"
    )
    cv2.putText(
        image,
        caption,
        (10, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (22, 32, 39),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "ORANGE = PER-IMAGE PROPOSAL (OWNER SAMPLE CONFIRMATION PENDING)",
        (10, height - 14),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.53,
        PROPOSAL_COLOR,
        2,
        cv2.LINE_AA,
    )
    path = output_dir / "proposals" / f"{row['calibration_id']}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"failed to write {path}")

    output = dict(row)
    output.update(
        {
            "proposal_yolo_box": list(proposed_box),
            "legacy_visible_yolo_box": list(legacy_box) if legacy_box is not None else None,
            "proposal_actual_start_time": _utc(window.iloc[0]["open_time"]).isoformat(),
            "proposal_actual_end_time": _utc(window.iloc[-1]["open_time"]).isoformat(),
            "proposal_image_path": str(path.relative_to(ROOT)),
            "proposal_image_sha256": sha256_file(path),
            "proposal_read_audit": read_audit,
        }
    )
    return output


def build_proposal_board(rows: list[dict[str, Any]], path: Path, page_number: int) -> None:
    card_w = 900
    chart_h = int(round(card_w * 742 / 1280))
    card_h = chart_h + 38
    cols = 3
    rows_n = (len(rows) + cols - 1) // cols
    header_h = 76
    sheet = np.full((header_h + rows_n * card_h, cols * card_w, 3), 244, np.uint8)
    cv2.putText(
        sheet,
        f"PER-IMAGE REBOX PROPOSALS {page_number:02d} | ORANGE=NEW | RED DASH=VISIBLE LEGACY",
        (20, 47),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (23, 35, 44),
        2,
        cv2.LINE_AA,
    )
    for position, row in enumerate(rows):
        image = cv2.imread(str(ROOT / row["proposal_image_path"]))
        if image is None:
            raise FileNotFoundError(row["proposal_image_path"])
        resized = cv2.resize(image, (card_w, chart_h), interpolation=cv2.INTER_AREA)
        row_i, col_i = divmod(position, cols)
        x0 = col_i * card_w
        y0 = header_h + row_i * card_h
        sheet[y0 : y0 + chart_h, x0 : x0 + card_w] = resized
        footer = (
            f"{row['calibration_id']} | core {row['proposal_core_bars']} | "
            f"W{row['proposal_win_len']} | post {row['post_bars']} | owner_sample=false"
        )
        cv2.putText(
            sheet,
            footer,
            (x0 + 8, y0 + chart_h + 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (42, 53, 61),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(
            sheet,
            (x0, y0),
            (x0 + card_w - 1, y0 + card_h - 1),
            (202, 210, 216),
            1,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), sheet):
        raise OSError(f"failed to write {path}")


def run(source: Path, output_dir: Path) -> dict[str, Any]:
    rows = [render_indexed_image(row, output_dir) for row in load_rebox_rows(source)]
    sheets: list[str] = []
    for offset in range(0, len(rows), 9):
        page_number = offset // 9 + 1
        chunk = rows[offset : offset + 9]
        path = output_dir / "workboards" / f"indexed_rebox_{page_number:02d}.png"
        build_workboard(chunk, path, page_number)
        sheets.append(str(path.relative_to(ROOT)))

    if set(REBOX_LOCAL_BOUNDS) != set(REBOX_INDICES):
        raise ValueError("manual rebox geometry does not cover the frozen 61-row queue")
    proposals = [
        render_rebox_proposal(apply_manual_geometry(row), output_dir)
        for row in rows
    ]
    proposal_sheets: list[str] = []
    for offset in range(0, len(proposals), 9):
        page_number = offset // 9 + 1
        chunk = proposals[offset : offset + 9]
        path = output_dir / "proposal_boards" / f"rebox_proposals_{page_number:02d}.png"
        build_proposal_board(chunk, path, page_number)
        proposal_sheets.append(str(path.relative_to(ROOT)))

    widths = [int(row["proposal_core_bars"]) for row in proposals]
    window_lengths = [int(row["proposal_win_len"]) for row in proposals]
    geometry_deltas = {
        (
            int(row["proposal_start_delta_vs_legacy"]),
            int(row["proposal_end_delta_vs_legacy"]),
        )
        for row in proposals
    }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256_file(source),
        "counts": {
            "rebox_rows": len(rows),
            "workboards": len(sheets),
            "proposal_boards": len(proposal_sheets),
            "proposal_widths": {
                str(width): widths.count(width) for width in sorted(set(widths))
            },
        },
        "workboards": sheets,
        "proposal_boards": proposal_sheets,
        "proposal_window_len_observed": [min(window_lengths), max(window_lengths)],
        "geometry_delta_pairs": [list(pair) for pair in sorted(geometry_deltas)],
        "geometry_complete": True,
        "geometry_owner_confirmed": False,
        "sample_owner_confirmed": False,
        "training_eligible": False,
        "production_eligible": False,
        "holdout_read": False,
        "quality_gates": {
            "exactly_61": len(rows) == 61,
            "exactly_7_workboards": len(sheets) == 7,
            "exactly_7_proposal_boards": len(proposal_sheets) == 7,
            "frozen_rebox_partition": tuple(
                int(str(row["calibration_id"])[1:4]) for row in rows
            ) == REBOX_INDICES,
            "indexed_step_no_market_csv_read": True,
            "holdout_not_read": True,
            "manual_geometry_covers_exactly_61": set(REBOX_LOCAL_BOUNDS) == set(REBOX_INDICES),
            "proposal_widths_4_to_7": min(widths) >= 4 and max(widths) <= 7,
            "proposal_post_delays_3_to_5": all(
                3 <= int(row["post_bars"]) <= 5 for row in proposals
            ),
            "every_core_end_moved_before_legacy_end": all(
                int(row["proposal_end_delta_vs_legacy"]) < 0 for row in proposals
            ),
            "per_image_geometry_not_single_offset": len(geometry_deltas) > 1,
            "dynamic_windows_13_to_22": min(window_lengths) >= 13 and max(window_lengths) <= 22,
            "proposal_windows_end_no_later_than_frozen_windows": all(
                _utc(row["proposal_actual_end_time"]) <= _utc(row["actual_end_time"])
                for row in proposals
            ),
            "proposal_train_split_only": all(row["stage_split"] == "train" for row in proposals),
            "proposal_holdout_rows_materialized_zero": all(
                row["proposal_read_audit"]["holdout_rows_materialized"] == 0
                for row in proposals
            ),
            "all_rows_still_blocked": all(
                not row["training_eligible"] and not row["production_eligible"]
                for row in proposals
            ),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"indexed rebox gate failed: {summary['quality_gates']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "indexed_manifest.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (output_dir / "proposal_manifest.jsonl").open("w") as handle:
        for row in proposals:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
