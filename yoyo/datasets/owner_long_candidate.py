"""Freeze the Owner-long population as a review-gated candidate manifest.

The only class fact used here is the owner's per-box direction decision in
``analysis/output/owner_side_review/review_sheet.csv``.  Core geometry is a
mechanical central-half derivation from that original box; it is not described
as sample-level boundary confirmation.  The six-MA score is joined only to
order human review and never changes a class label.

This module deliberately does not open OHLC files.  It records the already
resolved source path and pre-holdout row/time bounds, while causal source-window
hashes, rendered images, and YOLO labels are deferred until owner review is
complete.  Both pending and review-joined manifests remain
``training_eligible=false``; only the owner may change that in a later step.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START_ISO, assert_pre_holdout, is_pre_holdout


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BAR_MINUTES = 15
PURGE_BARS = 150
VAL_FRACTION = 0.15
YOLO_ROUNDING_TOLERANCE = 1e-6
EXPECTED_DIRECTION_ROWS = 2525
EXPECTED_LONG_ROWS = 1152
EXPECTED_UNIQUE_LONG = 1144
EXPECTED_SPLITS = {"train": 963, "val": 171, "drop": 10}
PACK_ID = "owner_short_gold_center_v1_ma_rope_prefilter_v1_owner_2525"
CANDIDATE_ID = "owner_long_gold_center_candidate_v2"
ALLOWED_DECISIONS = {"KEEP", "REMOVE", "UNCERTAIN"}

DEFAULT_REVIEW_SHEET = (
    PROJECT_ROOT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
)
DEFAULT_REVIEW_ROOT = PROJECT_ROOT / "analysis" / "output" / "owner_side_review"
DEFAULT_SCORES = (
    PROJECT_ROOT
    / "datasets"
    / "owner_short_gold_center_v1"
    / "review"
    / "ma_rope_prefilter_v1"
    / "admin"
    / "owner_2525_scores.jsonl"
)
DEFAULT_CALIBRATION = (
    PROJECT_ROOT
    / "datasets"
    / "owner_short_gold_center_v1"
    / "review"
    / "ma_rope_prefilter_v1"
    / "calibration.json"
)
DEFAULT_PUBLIC_MANIFEST = (
    PROJECT_ROOT
    / "datasets"
    / "owner_short_gold_center_v1"
    / "review"
    / "ma_rope_prefilter_v1"
    / "public"
    / "owner_2525_manifest.json"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "datasets" / CANDIDATE_ID


class OwnerLongCandidateError(RuntimeError):
    """Raised when the candidate population or an Owner receipt fails closed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_event_id(
    *,
    symbol: str,
    source_csv: str,
    win_start: int,
    win_end: int,
    core_start: int,
    core_end: int,
) -> str:
    payload = {
        "schema": "owner_long_target_v2",
        "symbol": symbol,
        "source_csv": source_csv,
        "win_start": win_start,
        "win_end": win_end,
        "core_start": core_start,
        "core_end": core_end,
    }
    return "olt_" + canonical_sha256(payload)[:24]


def _ensure_output_is_new(output_dir: Path, *, role: str) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite existing {role}: {output_dir}")


def central_core(source_start: int, source_end: int) -> tuple[int, int]:
    """Return a 4--7 bar central core derived only from the Owner box."""
    source_width = source_end - source_start + 1
    if source_width < 4:
        raise OwnerLongCandidateError(f"Owner box is too narrow: {source_width}")
    core_width = min(source_width, max(4, min(7, -(-source_width // 2))))
    core_start = source_start + (source_width - core_width) // 2
    return core_start, core_start + core_width - 1


def dynamic_context(
    source_start: int,
    source_end: int,
    core_start: int,
    core_end: int,
) -> tuple[int, int]:
    """Derive 5--7 pre bars and 3--5 post bars without market outcomes."""
    pre_bars = min(7, max(5, core_start - source_start + 2))
    post_bars = min(5, max(3, source_end - core_end))
    return pre_bars, post_bars


def tier_for_score(score: float, calibration: Mapping[str, Any]) -> str:
    if score >= float(calibration["core_threshold_star_p50_lower"]):
        return "A_CORE"
    if score >= float(calibration["broad_threshold_star_p10_lower"]):
        return "B_BROAD"
    return "C_REST"


def _read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _validate_review_public_manifest(
    path: Path,
    *,
    expected_rows: int,
    expected_sample_ids: set[str] | None = None,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("pack_id") != PACK_ID:
        raise OwnerLongCandidateError("review public manifest pack id mismatch")
    items = payload.get("items") or []
    if len(items) != expected_rows:
        raise OwnerLongCandidateError(
            f"review public item count changed: {len(items)} != {expected_rows}"
        )
    by_review: dict[str, Mapping[str, Any]] = {}
    by_sample: dict[str, Mapping[str, Any]] = {}
    for item in items:
        review_id = str(item.get("review_id") or "")
        sample_id = str(item.get("sample_id") or "")
        if not review_id or review_id in by_review:
            raise OwnerLongCandidateError(
                f"blank or duplicate review_id in public manifest: {review_id!r}"
            )
        if not sample_id or sample_id in by_sample:
            raise OwnerLongCandidateError(
                f"blank or duplicate sample_id in public manifest: {sample_id!r}"
            )
        by_review[review_id] = item
        by_sample[sample_id] = item
    if expected_sample_ids is not None and set(by_sample) != expected_sample_ids:
        raise OwnerLongCandidateError(
            "review public sample_id population does not equal direction sheet"
        )
    return by_review, by_sample


def _validate_source_join(
    sheet_rows: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
    *,
    expected_direction_rows: int,
    expected_long_rows: int,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    if len(sheet_rows) != expected_direction_rows:
        raise OwnerLongCandidateError(
            f"direction sheet count changed: {len(sheet_rows)} != {expected_direction_rows}"
        )
    if len(score_rows) != expected_direction_rows:
        raise OwnerLongCandidateError(
            f"score row count changed: {len(score_rows)} != {expected_direction_rows}"
        )
    sheet_by_id = {str(row.get("box_id") or ""): row for row in sheet_rows}
    score_by_id = {str(row.get("sample_id") or ""): row for row in score_rows}
    if "" in sheet_by_id or len(sheet_by_id) != len(sheet_rows):
        raise OwnerLongCandidateError("blank or duplicate box_id in direction sheet")
    if "" in score_by_id or len(score_by_id) != len(score_rows):
        raise OwnerLongCandidateError("blank or duplicate sample_id in score rows")
    if set(sheet_by_id) != set(score_by_id):
        raise OwnerLongCandidateError("direction sheet and score rows do not join one-to-one")
    long_count = sum(
        str(row.get("owner_side") or "").lower() == "long" for row in sheet_rows
    )
    if long_count != expected_long_rows:
        raise OwnerLongCandidateError(
            f"Owner-long count changed: {long_count} != {expected_long_rows}"
        )
    for sample_id, sheet in sheet_by_id.items():
        score = score_by_id[sample_id]
        if str(score.get("owner_side") or "").lower() != str(
            sheet.get("owner_side") or ""
        ).lower():
            raise OwnerLongCandidateError(f"{sample_id}: owner side mismatch")
        if str(score.get("symbol") or "") != str(sheet.get("symbol") or ""):
            raise OwnerLongCandidateError(f"{sample_id}: symbol mismatch")
        if int(score.get("decision_bar")) != int(sheet.get("cut_global")):
            raise OwnerLongCandidateError(f"{sample_id}: decision index mismatch")
        if pd.Timestamp(score.get("decision_time")) != pd.Timestamp(sheet.get("cut_time")):
            raise OwnerLongCandidateError(f"{sample_id}: decision time mismatch")
        if not str(score.get("resolved_source_csv") or ""):
            raise OwnerLongCandidateError(f"{sample_id}: unresolved source path")
        bar_b0 = int(sheet.get("bar_b0"))
        bar_b1 = int(sheet.get("bar_b1"))
        width_bars = int(sheet.get("width_bars"))
        if not (0 <= bar_b0 <= bar_b1 < 200):
            raise OwnerLongCandidateError(f"{sample_id}: invalid original bar_b0/bar_b1")
        if bar_b1 - bar_b0 + 1 != width_bars:
            raise OwnerLongCandidateError(f"{sample_id}: original box width mismatch")
        box_index = int(sheet.get("box_index"))
        n_boxes = int(sheet.get("n_boxes_on_image"))
        if n_boxes < 1 or not 0 <= box_index < n_boxes:
            raise OwnerLongCandidateError(f"{sample_id}: invalid box index/count")
        yolo = [
            float(sheet.get("yolo_xc")),
            float(sheet.get("yolo_yc")),
            float(sheet.get("yolo_w")),
            float(sheet.get("yolo_h")),
        ]
        if not all(math.isfinite(value) for value in yolo):
            raise OwnerLongCandidateError(f"{sample_id}: non-finite original YOLO geometry")
        xc, yc, width, height = yolo
        if not (
            0 <= xc <= 1
            and 0 <= yc <= 1
            and 0 < width <= 1
            and 0 < height <= 1
            and xc - width / 2 >= -YOLO_ROUNDING_TOLERANCE
            and xc + width / 2 <= 1 + YOLO_ROUNDING_TOLERANCE
            and yc - height / 2 >= -YOLO_ROUNDING_TOLERANCE
            and yc + height / 2 <= 1 + YOLO_ROUNDING_TOLERANCE
        ):
            raise OwnerLongCandidateError(f"{sample_id}: original YOLO box outside canvas")
        source_path = PROJECT_ROOT / str(score["resolved_source_csv"])
        if not source_path.is_file():
            raise OwnerLongCandidateError(f"{sample_id}: resolved source path is missing")
    return sheet_by_id, score_by_id


def _derive_long_rows(
    sheet_by_id: Mapping[str, Mapping[str, Any]],
    score_by_id: Mapping[str, Mapping[str, Any]],
    calibration: Mapping[str, Any],
    *,
    review_root: Path,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for sample_id in sorted(sheet_by_id):
        sheet = sheet_by_id[sample_id]
        if str(sheet.get("owner_side") or "").lower() != "long":
            continue
        score = score_by_id[sample_id]
        source_end = int(sheet["cut_global"])
        source_start = source_end - int(sheet["width_bars"]) + 1
        core_start, core_end = central_core(source_start, source_end)
        pre_bars, post_bars = dynamic_context(
            source_start, source_end, core_start, core_end
        )
        win_start = core_start - pre_bars
        win_end = core_end + post_bars
        cut_time = pd.Timestamp(sheet["cut_time"])
        start_time = cut_time + timedelta(
            minutes=(win_start - source_end) * BAR_MINUTES
        )
        end_time = cut_time + timedelta(
            minutes=(win_end - source_end) * BAR_MINUTES
        )
        assert_pre_holdout(start_time, what=f"{sample_id} visible window start")
        assert_pre_holdout(end_time, what=f"{sample_id} visible window end")
        preview_path = review_root / str(sheet["preview_path"])
        if not preview_path.is_file():
            raise OwnerLongCandidateError(f"{sample_id}: missing Owner preview {preview_path}")
        rope_score = float(score["rope_score"])
        owner_row = {str(key): value for key, value in sheet.items()}
        owner_row_sha256 = canonical_sha256(owner_row)
        source_csv = str(score["resolved_source_csv"])
        event_id = stable_event_id(
            symbol=str(sheet["symbol"]),
            source_csv=source_csv,
            win_start=win_start,
            win_end=win_end,
            core_start=core_start,
            core_end=core_end,
        )
        plans.append(
            {
                "event_id": event_id,
                "sample_id": sample_id,
                "symbol": str(sheet["symbol"]),
                "owner_side": "long",
                "source_csv": source_csv,
                "source_csv_sha256": None,
                "source_csv_hash_status": "forbidden_eof_hash_defer_preholdout_prefix_hash",
                "source_owner_global": [source_start, source_end],
                "source_owner_bars": int(sheet["width_bars"]),
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
                "rope_score": rope_score,
                "rope_tier": tier_for_score(rope_score, calibration),
                "owner_preview_path": str(preview_path.relative_to(PROJECT_ROOT)),
                "owner_preview_sha256": sha256_file(preview_path),
                "owner_row_sha256": owner_row_sha256,
                "owner_original_geometry": {
                    "bar_b0": int(sheet["bar_b0"]),
                    "bar_b1": int(sheet["bar_b1"]),
                    "width_bars": int(sheet["width_bars"]),
                    "yolo_box": [
                        float(sheet["yolo_xc"]),
                        float(sheet["yolo_yc"]),
                        float(sheet["yolo_w"]),
                        float(sheet["yolo_h"]),
                    ],
                    "box_index": int(sheet["box_index"]),
                    "n_boxes_on_image": int(sheet["n_boxes_on_image"]),
                },
                "direction_confirmation": "owner_sample_level",
                "core_boundary_confirmation": "mechanical_contract_not_sample_level",
                "source_window_sha256": None,
                "source_window_hash_status": "deferred_until_causal_materialization",
                "max_materialized_time": None,
                "holdout_rows_materialized": None,
                "actual_ohlc_gap_bars": None,
                "future_outcome_used": False,
                "model_score_used_for_label": False,
                "review_image_used_as_model_input": False,
                "owner_filter_decision": "PENDING",
                "review_required": True,
                "class": "candidate_positive",
                "training_eligible": False,
                "production_eligible": False,
            }
        )
    return plans


def deduplicate_targets(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row["symbol"]),
            int(row["win_start"]),
            int(row["win_end"]),
            int(row["core_global"][0]),
            int(row["core_global"][1]),
        )
        groups[key].append(row)
    unique: list[dict[str, Any]] = []
    duplicate_groups = 0
    aliases_removed = 0
    for grouped in groups.values():
        ordered = sorted(grouped, key=lambda row: str(row["sample_id"]))
        tiers = {str(row["rope_tier"]) for row in ordered}
        if len(tiers) != 1:
            raise OwnerLongCandidateError("duplicate target aliases disagree on rope tier")
        canonical = dict(ordered[0])
        event_ids = {str(row["event_id"]) for row in ordered}
        source_paths = {str(row["source_csv"]) for row in ordered}
        decision_times = {str(row["source_owner_cut_time"]) for row in ordered}
        if len(event_ids) != 1 or len(source_paths) != 1 or len(decision_times) != 1:
            raise OwnerLongCandidateError("target aliases disagree on event lineage")
        canonical["owner_annotation_ids"] = [str(row["sample_id"]) for row in ordered]
        canonical["owner_annotation_count"] = len(ordered)
        canonical["owner_preview_paths"] = [str(row["owner_preview_path"]) for row in ordered]
        canonical["owner_preview_sha256s"] = [
            str(row["owner_preview_sha256"]) for row in ordered
        ]
        canonical["owner_annotation_lineage"] = [
            {
                "annotation_id": str(row["sample_id"]),
                "owner_row_sha256": str(row["owner_row_sha256"]),
                "source_owner_global": list(row["source_owner_global"]),
                "source_owner_bars": int(row["source_owner_bars"]),
                "owner_original_geometry": dict(row["owner_original_geometry"]),
                "owner_preview_path": str(row["owner_preview_path"]),
                "owner_preview_sha256": str(row["owner_preview_sha256"]),
            }
            for row in ordered
        ]
        unique.append(canonical)
        if len(ordered) > 1:
            duplicate_groups += 1
            aliases_removed += len(ordered) - 1
    unique.sort(
        key=lambda row: (
            str(row["symbol"]),
            int(row["win_start"]),
            str(row["sample_id"]),
        )
    )
    return unique, {
        "duplicate_target_groups": duplicate_groups,
        "duplicate_annotation_aliases_removed": aliases_removed,
        "unique_targets": len(unique),
    }


def _dependency_blocks(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row["symbol"])].append(row)
    blocks: list[list[dict[str, Any]]] = []
    for symbol_rows in by_symbol.values():
        symbol_rows.sort(
            key=lambda row: (
                int(row["win_start"]),
                int(row["win_end"]),
                str(row["sample_id"]),
            )
        )
        current: list[dict[str, Any]] = []
        current_end = -1
        for row in symbol_rows:
            if current and int(row["win_start"]) > current_end:
                blocks.append(current)
                current = []
                current_end = -1
            current.append(row)
            current_end = max(current_end, int(row["win_end"]))
        if current:
            blocks.append(current)
    return blocks


def _dependency_id(block: Sequence[Mapping[str, Any]]) -> str:
    payload = "|".join(
        (
            str(block[0]["symbol"]),
            str(min(int(row["win_start"]) for row in block)),
            str(max(int(row["win_end"]) for row in block)),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def assign_time_splits(
    rows: list[dict[str, Any]],
    *,
    val_fraction: float = VAL_FRACTION,
    purge_bars: int = PURGE_BARS,
) -> dict[str, Any]:
    """Split dependency blocks chronologically and enforce a bar purge."""
    if not 0 < val_fraction < 1:
        raise ValueError("val_fraction must be between zero and one")
    blocks = _dependency_blocks(rows)
    if len(blocks) < 2:
        raise OwnerLongCandidateError("need at least two dependency blocks")
    blocks.sort(
        key=lambda block: (
            max(pd.Timestamp(row["end_time"]) for row in block),
            str(block[0]["symbol"]),
            str(block[0]["sample_id"]),
        )
    )
    n_val = max(1, round(len(blocks) * val_fraction))
    val_blocks = blocks[-n_val:]
    val_ids = {id(block) for block in val_blocks}
    val_start = min(
        pd.Timestamp(row["start_time"]) for block in val_blocks for row in block
    )
    train_cutoff = val_start - timedelta(minutes=purge_bars * BAR_MINUTES)
    block_counts: Counter[str] = Counter()
    for block in blocks:
        if id(block) in val_ids:
            split = "val"
        elif max(pd.Timestamp(row["end_time"]) for row in block) <= train_cutoff:
            split = "train"
        else:
            split = "drop"
        block_counts[split] += 1
        dependency_id = _dependency_id(block)
        for row in block:
            row["split"] = split
            row["dependency_id"] = dependency_id

    train_rows = [row for row in rows if row["split"] == "train"]
    val_rows = [row for row in rows if row["split"] == "val"]
    if not train_rows or not val_rows:
        raise OwnerLongCandidateError("time split produced an empty train or val")
    train_end = max(pd.Timestamp(row["end_time"]) for row in train_rows)
    val_start_actual = min(pd.Timestamp(row["start_time"]) for row in val_rows)
    actual_gap_bars = (val_start_actual - train_end).total_seconds() / (
        BAR_MINUTES * 60
    )
    train_dependencies = {row["dependency_id"] for row in train_rows}
    val_dependencies = {row["dependency_id"] for row in val_rows}
    dependency_cross_split = len(train_dependencies & val_dependencies)
    if actual_gap_bars < purge_bars or dependency_cross_split:
        raise OwnerLongCandidateError("time purge or dependency isolation failed")
    event_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        event_splits[str(row["event_id"])].add(str(row["split"]))
    event_cross_split = sum(len(splits) > 1 for splits in event_splits.values())
    if event_cross_split:
        raise OwnerLongCandidateError("one event_id appears in more than one split")
    return {
        "dependency_blocks": len(blocks),
        "dependency_block_counts": dict(sorted(block_counts.items())),
        "row_counts": dict(sorted(Counter(str(row["split"]) for row in rows).items())),
        "train_end_max": train_end.isoformat(),
        "val_start_min": val_start_actual.isoformat(),
        "purge_bars": purge_bars,
        "nominal_timestamp_grid_gap_bars": actual_gap_bars,
        "actual_ohlc_gap_bars": None,
        "purge_proof_status": "pending_bounded_ohlc_materialization",
        "dependency_cross_split": dependency_cross_split,
        "event_cross_split": event_cross_split,
    }


def build_pending_manifest(
    *,
    review_sheet: Path = DEFAULT_REVIEW_SHEET,
    score_rows_path: Path = DEFAULT_SCORES,
    calibration_path: Path = DEFAULT_CALIBRATION,
    public_manifest_path: Path = DEFAULT_PUBLIC_MANIFEST,
    review_root: Path = DEFAULT_REVIEW_ROOT,
    output_dir: Path = DEFAULT_OUTPUT,
    generator_commit: str,
    expected_direction_rows: int = EXPECTED_DIRECTION_ROWS,
    expected_long_rows: int = EXPECTED_LONG_ROWS,
    expected_unique_long: int = EXPECTED_UNIQUE_LONG,
    expected_splits: Mapping[str, int] | None = EXPECTED_SPLITS,
) -> dict[str, Any]:
    """Write the deterministic, review-pending Owner-long target ledger."""
    _ensure_output_is_new(output_dir, role=CANDIDATE_ID)
    sheet_rows = _read_sheet(review_sheet)
    score_rows = read_jsonl(score_rows_path)
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    sheet_by_id, score_by_id = _validate_source_join(
        sheet_rows,
        score_rows,
        expected_direction_rows=expected_direction_rows,
        expected_long_rows=expected_long_rows,
    )
    _public_by_review, public_by_sample = _validate_review_public_manifest(
        public_manifest_path,
        expected_rows=expected_direction_rows,
        expected_sample_ids=set(sheet_by_id),
    )
    public_manifest_sha256 = sha256_file(public_manifest_path)
    raw_long = _derive_long_rows(
        sheet_by_id, score_by_id, calibration, review_root=review_root
    )
    unique, dedup = deduplicate_targets(raw_long)
    if len(unique) != expected_unique_long:
        raise OwnerLongCandidateError(
            f"unique Owner-long count changed: {len(unique)} != {expected_unique_long}"
        )
    split = assign_time_splits(unique)
    if expected_splits is not None and split["row_counts"] != dict(expected_splits):
        raise OwnerLongCandidateError(
            f"split counts changed: {split['row_counts']} != {dict(expected_splits)}"
        )
    if any(row["training_eligible"] or row["production_eligible"] for row in unique):
        raise OwnerLongCandidateError("pending targets must not be eligible")
    if any(not is_pre_holdout(row["end_time"]) for row in unique):
        raise OwnerLongCandidateError("pending target touches holdout")
    if len({str(row["event_id"]) for row in unique}) != len(unique):
        raise OwnerLongCandidateError("stable event_id collision")
    for row in unique:
        aliases = {str(value) for value in row["owner_annotation_ids"]}
        if not aliases.issubset(public_by_sample):
            raise OwnerLongCandidateError(
                f"{row['event_id']}: Owner aliases missing from review public manifest"
            )
        row["review_pack_id"] = PACK_ID
        row["review_public_manifest_sha256"] = public_manifest_sha256

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "candidate_manifest.jsonl"
    write_jsonl(manifest_path, unique)
    source_contract = {
        "schema_version": 1,
        "candidate_id": CANDIDATE_ID,
        "class_fact": "Owner per-box long direction",
        "geometry": "central half of original Owner box, clamped to 4--7 bars",
        "review_order_only": "six-MA rope A/B/C",
        "causal_materialization": "deferred until Owner filter receipt is validated",
        "holdout_start": HOLDOUT_START_ISO,
        "materialization_loader": (
            "scripts.build_owner_eth_shortdelay_calibration.load_preholdout_prefix"
        ),
        "required_materialization_receipt_fields": [
            "source_preholdout_prefix_sha256",
            "source_window_sha256",
            "rendered_image_sha256",
            "label_sha256",
            "max_materialized_time",
            "holdout_rows_materialized",
            "actual_ohlc_gap_bars",
        ],
        "full_source_csv_eof_hash_forbidden": True,
        "training_eligible": False,
        "production_eligible": False,
        "owner_approval_required_to_change_training_eligible": True,
    }
    write_json(output_dir / "source_contract.json", source_contract)
    tier_counts = Counter(str(row["rope_tier"]) for row in unique)
    summary = {
        "schema_version": 1,
        "candidate_id": CANDIDATE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator_commit": generator_commit,
        "source": {
            "owner_direction_sheet": str(review_sheet.relative_to(PROJECT_ROOT)),
            "owner_direction_sheet_sha256": sha256_file(review_sheet),
            "rope_scores": str(score_rows_path.relative_to(PROJECT_ROOT)),
            "rope_scores_sha256": sha256_file(score_rows_path),
            "calibration": str(calibration_path.relative_to(PROJECT_ROOT)),
            "calibration_sha256": sha256_file(calibration_path),
            "review_public_manifest": str(public_manifest_path.relative_to(PROJECT_ROOT)),
            "review_public_manifest_sha256": public_manifest_sha256,
        },
        "raw_owner_long_annotations": len(raw_long),
        "deduplication": dedup,
        "unique_targets": len(unique),
        "rope_tier_counts_unique": dict(sorted(tier_counts.items())),
        "split": split,
        "data_cutoff": max(str(row["end_time"]) for row in unique),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_size_bytes": manifest_path.stat().st_size,
        "review_status_counts": {"PENDING": len(unique)},
        "source_ohlc_opened": False,
        "holdout_read": False,
        "future_outcome_used": False,
        "labels_created": 0,
        "images_created": 0,
        "training_eligible": False,
        "production_eligible": False,
        "quality_gates": {
            "source_join_one_to_one": True,
            "unique_target_count_frozen": len(unique) == expected_unique_long,
            "strictly_pre_holdout": all(
                is_pre_holdout(row["end_time"]) for row in unique
            ),
            "dependency_cross_split_zero": split["dependency_cross_split"] == 0,
            "nominal_grid_purge_at_least_150_bars": split[
                "nominal_timestamp_grid_gap_bars"
            ]
            >= PURGE_BARS,
            "actual_ohlc_purge_deferred": split["actual_ohlc_gap_bars"] is None,
            "event_cross_split_zero": split["event_cross_split"] == 0,
            "all_pending_review": all(
                row["owner_filter_decision"] == "PENDING" for row in unique
            ),
            "nothing_training_eligible": all(
                not row["training_eligible"] for row in unique
            ),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise OwnerLongCandidateError(f"quality gate failed: {summary['quality_gates']}")
    write_json(output_dir / "summary.json", summary)
    return summary


def _load_review_answers(
    review_export: Path,
    public_manifest: Path,
    *,
    expected_public_rows: int,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Mapping[str, Any]]]:
    payload = json.loads(review_export.read_text(encoding="utf-8"))
    if payload.get("pack_id") != PACK_ID:
        raise OwnerLongCandidateError("review pack id mismatch")
    exported_at = payload.get("exported_at")
    if not exported_at:
        raise OwnerLongCandidateError("review export is missing exported_at")
    exported_stamp = pd.Timestamp(exported_at)
    if exported_stamp.tzinfo is None:
        raise OwnerLongCandidateError("review exported_at must be timezone-aware")
    public_by_review, public_by_sample = _validate_review_public_manifest(
        public_manifest, expected_rows=expected_public_rows
    )
    decisions: dict[str, str] = {}
    answer_ids: set[str] = set()
    for answer in payload.get("answers") or []:
        review_id = str(answer.get("review_id") or "")
        if not review_id or review_id in answer_ids:
            raise OwnerLongCandidateError(f"blank or duplicate review answer: {review_id!r}")
        answer_ids.add(review_id)
        item = public_by_review.get(review_id)
        if item is None:
            raise OwnerLongCandidateError(f"unknown review_id: {review_id}")
        sample_id = str(item["sample_id"])
        if answer.get("sample_id") not in (None, sample_id):
            raise OwnerLongCandidateError(f"{review_id}: sample_id mismatch")
        decision = str(answer.get("decision") or "")
        if decision not in ALLOWED_DECISIONS:
            raise OwnerLongCandidateError(f"{review_id}: invalid decision {decision!r}")
        if sample_id in decisions:
            raise OwnerLongCandidateError(
                f"duplicate sample_id across review answers: {sample_id}"
            )
        decisions[sample_id] = decision
    return decisions, payload, public_by_sample


def join_review_export(
    *,
    review_export: Path,
    pending_manifest: Path = DEFAULT_OUTPUT / "candidate_manifest.jsonl",
    public_manifest: Path = DEFAULT_PUBLIC_MANIFEST,
    output_dir: Path,
    reviewer: str,
    expected_public_rows: int = EXPECTED_DIRECTION_ROWS,
) -> dict[str, Any]:
    """Join Owner decisions without granting training eligibility or rendering."""
    if not reviewer.strip():
        raise OwnerLongCandidateError("reviewer identity is required")
    _ensure_output_is_new(output_dir, role="Owner-long review receipt")
    candidates = read_jsonl(pending_manifest)
    decisions, payload, public_by_sample = _load_review_answers(
        review_export,
        public_manifest,
        expected_public_rows=expected_public_rows,
    )
    candidate_aliases = {
        str(alias)
        for candidate in candidates
        for alias in candidate["owner_annotation_ids"]
    }
    if not candidate_aliases.issubset(public_by_sample):
        raise OwnerLongCandidateError("pending Owner aliases are missing from public manifest")
    expected_public_hashes = {
        str(candidate.get("review_public_manifest_sha256") or "")
        for candidate in candidates
    }
    if expected_public_hashes != {sha256_file(public_manifest)}:
        raise OwnerLongCandidateError("public review manifest SHA differs from pending ledger")
    if len({str(row["event_id"]) for row in candidates}) != len(candidates):
        raise OwnerLongCandidateError("pending manifest has duplicate event_id")
    joined: list[dict[str, Any]] = []
    for candidate in candidates:
        alias_ids = [str(value) for value in candidate["owner_annotation_ids"]]
        observed = {decisions[alias] for alias in alias_ids if alias in decisions}
        if len(observed) > 1:
            raise OwnerLongCandidateError(
                f"{candidate['sample_id']}: duplicate aliases have conflicting decisions"
            )
        reviewed_aliases = sum(alias in decisions for alias in alias_ids)
        review_complete = reviewed_aliases == len(alias_ids)
        decision = next(iter(observed)) if review_complete and observed else "PENDING"
        row = dict(candidate)
        row["owner_filter_decision"] = decision
        row["owner_filter_review_complete"] = review_complete
        row["owner_filter_reviewed_aliases"] = reviewed_aliases
        row["owner_filter_unreviewed_aliases"] = len(alias_ids) - reviewed_aliases
        row["owner_filter_receipt_sha256"] = sha256_file(review_export)
        row["owner_filter_reviewer"] = reviewer.strip()
        row["owner_filter_exported_at"] = str(payload["exported_at"])
        row["class"] = "candidate_positive" if decision == "KEEP" else "excluded_or_pending"
        row["training_eligible"] = False
        row["production_eligible"] = False
        joined.append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    joined_path = output_dir / "review_joined_manifest.jsonl"
    kept_path = output_dir / "kept_candidate_manifest.jsonl"
    kept = [row for row in joined if row["owner_filter_decision"] == "KEEP"]
    write_jsonl(joined_path, joined)
    write_jsonl(kept_path, kept)
    status_counts = Counter(str(row["owner_filter_decision"]) for row in joined)
    tier_status = {
        tier: dict(
            sorted(
                Counter(
                    str(row["owner_filter_decision"])
                    for row in joined
                    if row["rope_tier"] == tier
                ).items()
            )
        )
        for tier in ("A_CORE", "B_BROAD", "C_REST")
    }
    summary = {
        "schema_version": 1,
        "candidate_id": CANDIDATE_ID,
        "review_pack_id": payload.get("pack_id"),
        "review_export_sha256": sha256_file(review_export),
        "pending_manifest_sha256": sha256_file(pending_manifest),
        "public_manifest_sha256": sha256_file(public_manifest),
        "answers_in_full_page_export": len(payload.get("answers") or []),
        "long_alias_answers": sum(alias in decisions for alias in candidate_aliases),
        "reviewer": reviewer.strip(),
        "exported_at": payload["exported_at"],
        "long_unique_targets": len(joined),
        "status_counts": dict(sorted(status_counts.items())),
        "tier_status_counts": tier_status,
        "kept_candidates": len(kept),
        "complete_all_long_targets": status_counts.get("PENDING", 0) == 0,
        "complete_A_CORE": tier_status["A_CORE"].get("PENDING", 0) == 0,
        "training_eligible_changed": False,
        "labels_created": 0,
        "images_created": 0,
        "holdout_read": False,
        "joined_manifest_sha256": sha256_file(joined_path),
        "kept_manifest_sha256": sha256_file(kept_path),
    }
    write_json(output_dir / "review_summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-pending", help="freeze the pending Owner-long ledger")
    build.add_argument("--review-sheet", type=Path, default=DEFAULT_REVIEW_SHEET)
    build.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    build.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    build.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC_MANIFEST)
    build.add_argument("--review-root", type=Path, default=DEFAULT_REVIEW_ROOT)
    build.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    build.add_argument("--generator-commit", default=None)
    join = sub.add_parser("join-review", help="validate and join an Owner export")
    join.add_argument("review_export", type=Path)
    join.add_argument(
        "--pending-manifest",
        type=Path,
        default=DEFAULT_OUTPUT / "candidate_manifest.jsonl",
    )
    join.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC_MANIFEST)
    join.add_argument("--reviewer", required=True)
    join.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build-pending":
        result = build_pending_manifest(
            review_sheet=args.review_sheet,
            score_rows_path=args.scores,
            calibration_path=args.calibration,
            public_manifest_path=args.public_manifest,
            review_root=args.review_root,
            output_dir=args.out,
            generator_commit=args.generator_commit or current_commit(),
        )
    else:
        result = join_review_export(
            review_export=args.review_export,
            pending_manifest=args.pending_manifest,
            public_manifest=args.public_manifest,
            output_dir=args.out,
            reviewer=args.reviewer,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
