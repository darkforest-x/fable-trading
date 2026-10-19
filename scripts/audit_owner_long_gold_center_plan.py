#!/usr/bin/env python3
"""Audit a mirrored Owner-long compact-YOLO dataset plan without building it.

Inputs are the Owner direction sheet, the already-frozen six-MA review scores,
and (only for explaining the BTC/ETH count coincidence) the completed recent
scan artifacts.  Long positive geometry is derived solely from each original
Owner box: the central half is clamped to 4--7 bars, with 5--7 visible bars
before it and 3--5 after it.  No market row after a sample's visible window is
read here; no return, outcome, model score, or 2026-05-04+ OHLC is used to form
the dataset plan.

The script is intentionally read-only with respect to datasets and model
weights.  It writes one audit JSON and never renders labels, starts training,
changes ``training_eligible``, promotes a model, or places an order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
BAR_MINUTES = 15
PURGE_BARS = 150
VAL_FRAC = 0.15
NEG_GUARD_BARS = 12
HARD_PER_POSITIVE = 2

DEFAULT_SHEET = ROOT / "analysis/output/owner_side_review/review_sheet.csv"
DEFAULT_SCORE_ROWS = (
    ROOT
    / "datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1"
    / "admin/owner_2525_scores.jsonl"
)
DEFAULT_CALIBRATION = (
    ROOT
    / "datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1"
    / "calibration.json"
)
DEFAULT_SCAN_DIR = (
    ROOT
    / "analysis/output/owner_short_gold_center_recent15d_core10_20260821"
    / "hardneg_fp32"
)
DEFAULT_OUT = (
    ROOT
    / "analysis/output/owner_long_dataset_lineage_audit_20260821/summary.json"
)


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
        if line
    ]


def central_core(source_start: int, source_end: int) -> tuple[int, int]:
    """Derive the frozen central-half core from one original Owner box."""
    source_width = source_end - source_start + 1
    if source_width < 4:
        raise ValueError(f"Owner box is too narrow: {source_width}")
    core_width = min(source_width, max(4, min(7, -(-source_width // 2))))
    core_start = source_start + (source_width - core_width) // 2
    return core_start, core_start + core_width - 1


def dynamic_context(
    source_start: int,
    source_end: int,
    core_start: int,
    core_end: int,
) -> tuple[int, int]:
    """Use only margins inside the original box to fix visible context."""
    pre_bars = min(7, max(5, core_start - source_start + 2))
    post_bars = min(5, max(3, source_end - core_end))
    return pre_bars, post_bars


def tier_for_score(score: float, calibration: dict[str, Any]) -> str:
    if score >= float(calibration["core_threshold_star_p50_lower"]):
        return "A_CORE"
    if score >= float(calibration["broad_threshold_star_p10_lower"]):
        return "B_BROAD"
    return "C_REST"


def plan_side(
    sheet: pd.DataFrame,
    score_by_id: dict[str, dict[str, Any]],
    calibration: dict[str, Any],
    side: str,
) -> list[dict[str, Any]]:
    """Create geometry/time plans for one explicit Owner direction."""
    plans: list[dict[str, Any]] = []
    selected = sheet[sheet["owner_side"].astype(str).str.lower().eq(side)]
    for row in selected.to_dict("records"):
        box_id = str(row["box_id"])
        score = score_by_id[box_id]
        source_end = int(row["cut_global"])
        source_start = source_end - int(row["width_bars"]) + 1
        core_start, core_end = central_core(source_start, source_end)
        pre_bars, post_bars = dynamic_context(
            source_start, source_end, core_start, core_end
        )
        window_start = core_start - pre_bars
        window_end = core_end + post_bars
        cut_time = pd.Timestamp(row["cut_time"])
        start_time = cut_time + timedelta(
            minutes=(window_start - source_end) * BAR_MINUTES
        )
        end_time = cut_time + timedelta(
            minutes=(window_end - source_end) * BAR_MINUTES
        )
        plans.append(
            {
                "sample_id": box_id,
                "owner_side": side,
                "symbol": str(row["symbol"]),
                "source_csv": str(score["resolved_source_csv"]),
                "source_owner_start": source_start,
                "source_owner_end": source_end,
                "source_owner_bars": int(row["width_bars"]),
                "core_start": core_start,
                "core_end": core_end,
                "core_bars": core_end - core_start + 1,
                "pre_bars": pre_bars,
                "post_bars": post_bars,
                "win_start": window_start,
                "win_end": window_end,
                "win_len": window_end - window_start + 1,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "rope_score": float(score["rope_score"]),
                "rope_tier": tier_for_score(float(score["rope_score"]), calibration),
                "direction_owner_confirmed": True,
                "core_boundary_confirmation": "rule_verified_not_sample_confirmed",
                "future_outcome_used": False,
                "model_score_used": False,
                "training_eligible": False,
            }
        )
    return plans


def deduplicate_plans(
    plans: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for plan in plans:
        key = (
            str(plan["symbol"]),
            int(plan["win_start"]),
            int(plan["win_end"]),
            int(plan["core_start"]),
            int(plan["core_end"]),
        )
        groups[key].append(plan)
    unique: list[dict[str, Any]] = []
    duplicate_groups = 0
    duplicate_rows_removed = 0
    for rows in groups.values():
        rows.sort(key=lambda row: str(row["sample_id"]))
        canonical = dict(rows[0])
        canonical["owner_annotation_ids"] = sorted(
            str(row["sample_id"]) for row in rows
        )
        canonical["owner_annotation_count"] = len(rows)
        unique.append(canonical)
        if len(rows) > 1:
            duplicate_groups += 1
            duplicate_rows_removed += len(rows) - 1
    unique.sort(
        key=lambda row: (
            str(row["symbol"]),
            int(row["win_start"]),
            str(row["sample_id"]),
        )
    )
    return unique, {
        "duplicate_target_groups": duplicate_groups,
        "duplicate_annotation_rows_removed": duplicate_rows_removed,
        "unique_positive_targets": len(unique),
    }


def dependency_blocks(plans: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for plan in plans:
        by_symbol[str(plan["symbol"])].append(plan)
    blocks: list[list[dict[str, Any]]] = []
    for rows in by_symbol.values():
        rows.sort(
            key=lambda row: (
                int(row["win_start"]),
                int(row["win_end"]),
                str(row["sample_id"]),
            )
        )
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


def block_id(block: list[dict[str, Any]]) -> str:
    payload = "|".join(
        [
            str(block[0]["symbol"]),
            str(min(int(row["win_start"]) for row in block)),
            str(max(int(row["win_end"]) for row in block)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def assign_time_splits(plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Chronologically split inseparable windows and enforce a 150-bar purge."""
    blocks = dependency_blocks(plans)
    blocks.sort(
        key=lambda block: (
            max(pd.Timestamp(row["end_time"]) for row in block),
            str(block[0]["symbol"]),
            str(block[0]["sample_id"]),
        )
    )
    n_val = max(1, round(len(blocks) * VAL_FRAC))
    val_block_ids = {id(block) for block in blocks[-n_val:]}
    val_start = min(
        pd.Timestamp(row["start_time"])
        for block in blocks[-n_val:]
        for row in block
    )
    train_cutoff = val_start - timedelta(minutes=PURGE_BARS * BAR_MINUTES)
    counts: Counter[str] = Counter()
    for block in blocks:
        if id(block) in val_block_ids:
            split = "val"
        elif max(pd.Timestamp(row["end_time"]) for row in block) <= train_cutoff:
            split = "train"
        else:
            split = "drop"
        counts[split] += 1
        dependency_id = block_id(block)
        for row in block:
            row["split"] = split
            row["dependency_id"] = dependency_id
    kept = [row for row in plans if row["split"] in {"train", "val"}]
    train_end = max(
        pd.Timestamp(row["end_time"]) for row in kept if row["split"] == "train"
    )
    val_start_actual = min(
        pd.Timestamp(row["start_time"]) for row in kept if row["split"] == "val"
    )
    actual_gap = (
        (val_start_actual - train_end).total_seconds() / (BAR_MINUTES * 60)
    )
    train_dependencies = {
        row["dependency_id"] for row in kept if row["split"] == "train"
    }
    val_dependencies = {
        row["dependency_id"] for row in kept if row["split"] == "val"
    }
    crossing = train_dependencies & val_dependencies
    if actual_gap < PURGE_BARS or crossing:
        raise ValueError("time split or dependency purge failed")
    return {
        "dependency_blocks": len(blocks),
        "dependency_block_counts": dict(counts),
        "row_counts": dict(Counter(str(row["split"]) for row in plans)),
        "train_end_max": train_end.isoformat(),
        "val_start_min": val_start_actual.isoformat(),
        "purge_bars": PURGE_BARS,
        "actual_gap_bars": actual_gap,
        "dependency_cross_split": len(crossing),
    }


def overlaps(interval: tuple[int, int], others: Iterable[tuple[int, int]]) -> bool:
    start, end = interval
    return any(start <= other_end and end >= other_start for other_start, other_end in others)


def opposite_short_candidates(
    sheet: pd.DataFrame,
    score_by_id: dict[str, dict[str, Any]],
    calibration: dict[str, Any],
    long_plans: list[dict[str, Any]],
    train_end: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Find safe Owner-short direction negatives for a long-only detector."""
    forbidden: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in sheet[
        sheet["owner_side"].astype(str).str.lower().eq("long")
    ].to_dict("records"):
        end = int(row["cut_global"])
        start = end - int(row["width_bars"]) + 1
        forbidden[str(row["symbol"])].append(
            (max(0, start - NEG_GUARD_BARS), end + NEG_GUARD_BARS)
        )
    long_symbols = {str(row["symbol"]) for row in long_plans}
    candidates: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    seen: set[tuple[Any, ...]] = set()
    for plan in plan_side(sheet, score_by_id, calibration, "short"):
        symbol = str(plan["symbol"])
        if symbol not in long_symbols:
            skips["symbol_outside_long_training_universe"] += 1
            continue
        if pd.Timestamp(plan["end_time"]) > train_end:
            skips["after_train_end"] += 1
            continue
        interval = (int(plan["win_start"]), int(plan["win_end"]))
        if overlaps(interval, forbidden.get(symbol, [])):
            skips["overlaps_owner_long_guard"] += 1
            continue
        key = (
            symbol,
            *interval,
            int(plan["core_start"]),
            int(plan["core_end"]),
        )
        if key in seen:
            skips["duplicate_target"] += 1
            continue
        seen.add(key)
        candidates.append(plan)
    return candidates, dict(skips)


def confidence_band(confidence: float) -> str:
    if confidence >= 0.8:
        return "ge_0_8"
    if confidence >= 0.5:
        return "ge_0_5_lt_0_8"
    return "ge_0_25_lt_0_5"


def scan_count_audit(scan_dir: Path) -> dict[str, Any]:
    summary = json.loads((scan_dir / "scan_summary.json").read_text(encoding="utf-8"))
    success = json.loads((scan_dir / "_SUCCESS.json").read_text(encoding="utf-8"))
    events = read_jsonl(scan_dir / "events.jsonl")
    raw = read_jsonl(scan_dir / "raw_detections.jsonl")
    if len(events) != int(summary["deduplicated_events"]) or len(events) != int(
        success["events"]
    ):
        raise ValueError("scan completion counts do not close")
    if len(raw) != int(summary["raw_detections"]):
        raise ValueError("raw scan count does not close")
    profile: dict[str, Any] = {}
    for symbol in ("BTC_USDT_SWAP", "ETH_USDT_SWAP", "ADA_USDT_SWAP"):
        symbol_events = [row for row in events if row["symbol"] == symbol]
        symbol_raw = [row for row in raw if row["symbol"] == symbol]
        profile[symbol] = {
            "raw_detections": len(symbol_raw),
            "deduplicated_events": len(symbol_events),
            "confidence_bands": dict(
                Counter(
                    confidence_band(float(row["event_conf_max"]))
                    for row in symbol_events
                )
            ),
            "daily_event_counts": dict(
                sorted(Counter(str(row["decision_time"])[:10] for row in symbol_events).items())
            ),
        }
    btc_times = {
        str(row["decision_time"])
        for row in events
        if row["symbol"] == "BTC_USDT_SWAP"
    }
    eth_times = {
        str(row["decision_time"])
        for row in events
        if row["symbol"] == "ETH_USDT_SWAP"
    }
    return {
        "complete": bool(success.get("ok")),
        "scan_summary_sha256": sha256_file(scan_dir / "scan_summary.json"),
        "events_sha256": sha256_file(scan_dir / "events.jsonl"),
        "raw_detections_sha256": sha256_file(scan_dir / "raw_detections.jsonl"),
        "event_gap_bars": int(summary["event_gap_bars"]),
        "bar_endpoints": int(summary["bar_endpoints"]),
        "raw_detections": len(raw),
        "deduplicated_events": len(events),
        "symbols": profile,
        "btc_eth_shared_decision_times": len(btc_times & eth_times),
        "all_confidence_bands": dict(
            Counter(confidence_band(float(row["event_conf_max"])) for row in events)
        ),
        "evaluation_scope": str(summary["evaluation_scope"]),
        "holdout_use_number": int(summary["holdout_use_number"]),
        "threshold_selection_from_this_scan_allowed": False,
    }


def build_audit(
    sheet_path: Path,
    score_path: Path,
    calibration_path: Path,
    scan_dir: Path,
) -> dict[str, Any]:
    sheet = pd.read_csv(sheet_path)
    sheet["cut_time"] = pd.to_datetime(sheet["cut_time"], utc=True, errors="raise")
    scores = read_jsonl(score_path)
    score_by_id = {str(row["box_id"]): row for row in scores}
    if len(score_by_id) != len(scores):
        raise ValueError("duplicate box_id in score rows")
    sheet_ids = set(sheet["box_id"].astype(str))
    if sheet_ids != set(score_by_id):
        raise ValueError("direction sheet and score rows do not join one-to-one")
    side_mismatch = 0
    time_mismatch = 0
    index_mismatch = 0
    missing_sources = 0
    for row in sheet.to_dict("records"):
        score = score_by_id[str(row["box_id"])]
        side_mismatch += str(row["owner_side"]).lower() != str(score["owner_side"]).lower()
        time_mismatch += pd.Timestamp(row["cut_time"]) != pd.Timestamp(score["decision_time"])
        index_mismatch += int(row["cut_global"]) != int(score["decision_bar"])
        missing_sources += not (ROOT / str(score["resolved_source_csv"])).is_file()
    if side_mismatch or time_mismatch or index_mismatch or missing_sources:
        raise ValueError("Owner/score/source lineage mismatch")

    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    long_raw = plan_side(sheet, score_by_id, calibration, "long")
    long_unique, dedup = deduplicate_plans(long_raw)
    split = assign_time_splits(long_unique)
    holdout_touches = sum(
        pd.Timestamp(row["end_time"]) >= HOLDOUT_START for row in long_unique
    )
    if holdout_touches:
        raise ValueError("Owner-long visible window touches holdout")
    train_rows = [row for row in long_unique if row["split"] == "train"]
    val_rows = [row for row in long_unique if row["split"] == "val"]
    train_end = max(pd.Timestamp(row["end_time"]) for row in train_rows)
    short_candidates, short_skips = opposite_short_candidates(
        sheet,
        score_by_id,
        calibration,
        long_unique,
        train_end,
    )
    train_widths = Counter(int(row["win_len"]) for row in train_rows)
    short_widths = Counter(int(row["win_len"]) for row in short_candidates)
    hard_target = {width: HARD_PER_POSITIVE * count for width, count in train_widths.items()}
    model_background_needed = {
        width: max(0, hard_target[width] - short_widths.get(width, 0))
        for width in sorted(hard_target)
    }
    direction_counts = Counter(sheet["owner_side"].astype(str).str.lower())
    long_tiers = Counter(str(row["rope_tier"]) for row in long_raw)
    return {
        "schema_version": 1,
        "audit_id": "owner_long_gold_center_lineage_audit_20260821",
        "source": {
            "owner_direction_sheet": str(sheet_path.relative_to(ROOT)),
            "owner_direction_sheet_sha256": sha256_file(sheet_path),
            "rope_score_rows": str(score_path.relative_to(ROOT)),
            "rope_score_rows_sha256": sha256_file(score_path),
            "calibration": str(calibration_path.relative_to(ROOT)),
            "calibration_sha256": sha256_file(calibration_path),
        },
        "direction_population": dict(direction_counts),
        "source_join": {
            "sheet_rows": len(sheet),
            "score_rows": len(scores),
            "box_id_missing_or_extra": 0,
            "side_mismatch": side_mismatch,
            "decision_time_mismatch": time_mismatch,
            "decision_index_mismatch": index_mismatch,
            "missing_resolved_source": missing_sources,
        },
        "long_quality_tiers": dict(long_tiers),
        "long_positive_plan": {
            "raw_owner_long_rows": len(long_raw),
            **dedup,
            "split": split,
            "train_positive": len(train_rows),
            "val_positive": len(val_rows),
            "drop_for_purge": sum(row["split"] == "drop" for row in long_unique),
            "train_window_lengths": dict(sorted(train_widths.items())),
            "val_window_lengths": dict(
                sorted(Counter(int(row["win_len"]) for row in val_rows).items())
            ),
            "core_widths_kept": dict(
                sorted(
                    Counter(
                        int(row["core_bars"])
                        for row in long_unique
                        if row["split"] in {"train", "val"}
                    ).items()
                )
            ),
            "complete_window_holdout_touches": holdout_touches,
            "direction_confirmation": "owner_sample_level",
            "core_geometry_confirmation": "mechanical_contract_not_sample_level",
            "training_eligible": False,
        },
        "mirrored_hard_negative_plan": {
            "safe_owner_short_candidates": len(short_candidates),
            "safe_owner_short_by_window": dict(sorted(short_widths.items())),
            "owner_short_skips": short_skips,
            "hard_target_two_per_train_positive": sum(hard_target.values()),
            "hard_target_by_window": dict(sorted(hard_target.items())),
            "model_ranked_background_needed": sum(model_background_needed.values()),
            "model_ranked_background_needed_by_window": model_background_needed,
            "selection_must_be_recomputed_after_owner_filter": True,
        },
        "btc_eth_equal_count_audit": scan_count_audit(scan_dir),
        "discipline": {
            "holdout_market_rows_read_for_long_plan": False,
            "future_outcome_used_for_long_plan": False,
            "training_started": False,
            "training_eligible_changed": False,
            "model_promoted": False,
            "orders_placed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORE_ROWS)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--scan-dir", type=Path, default=DEFAULT_SCAN_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    audit = build_audit(args.sheet, args.scores, args.calibration, args.scan_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
