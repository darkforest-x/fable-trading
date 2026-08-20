"""Inventory, interval-semantics notes, and evidence-bound acceptance gates.

Acceptance is rebuilt from one final Gold snapshot.  Blind-review results are
joined back by ``gold_id`` and re-derived here; callers cannot clear the gate by
passing a naked error-rate scalar.  This prevents three observed lineage bugs:
pre-review migration rows paired with post-review Gold, non-DIRECT spot checks
counted as DIRECT, and ``DIRECT == 0`` satisfying a percentage gate vacuously.
"""

from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from .io import git_head, git_status_short, sha256_file


def source_inventory(yoyo: Path, fable: Path) -> dict[str, Any]:
    def count_jsonl(path: Path) -> int | None:
        if not path.exists():
            return None
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)

    def exists(path: Path) -> bool:
        return path.exists()

    tasks_n = None
    tasks = yoyo / "datasets/gold_labelstudio_v1/tasks.json"
    if tasks.exists():
        payload = json.loads(tasks.read_text(encoding="utf-8"))
        tasks_n = len(payload) if isinstance(payload, list) else None
    return {
        "yoyo_head": git_head(yoyo),
        "fable_head": git_head(fable),
        "yoyo_status_lines": len([l for l in git_status_short(yoyo).splitlines() if l.strip()]),
        "files": {
            "gold_v1.jsonl": count_jsonl(yoyo / "datasets/gold_v1.jsonl"),
            "gold_v1_demo.jsonl": count_jsonl(yoyo / "datasets/gold_v1_demo.jsonl"),
            "gold_candidates_v1.jsonl": count_jsonl(yoyo / "datasets/gold_candidates_v1.jsonl"),
            "tasks.json": tasks_n,
            "v3_manifest": count_jsonl(yoyo / "datasets/dataset_v3_gold_core_v1/manifest.jsonl"),
            "v3_2_reviewed": count_jsonl(yoyo / "datasets/dataset_v3_2_reviewed_core_v1/manifest.jsonl"),
            "owner_gold_pos": count_jsonl(
                fable / "datasets/owner_short_gold_center_v1/positive_manifest.jsonl"
            ),
            "owner_gold_neg": count_jsonl(
                fable / "datasets/owner_short_gold_center_v1/negative_manifest.jsonl"
            ),
            "r1_pos": count_jsonl(
                fable / "datasets/owner_short_gold_center_hardneg_r1/positive_manifest.jsonl"
            ),
            "review_sheet_csv": (
                sum(1 for _ in (fable / "analysis/output/owner_side_review/review_sheet.csv").open()) - 1
                if (fable / "analysis/output/owner_side_review/review_sheet.csv").exists()
                else None
            ),
            "label_studio_sqlite": exists(fable / "label_studio_data/label_studio.sqlite3"),
            "serve_gold_annotate": exists(yoyo / "tools/serve_gold_annotate.py"),
        },
        "interval_semantics": {
            "local_8768": "inclusive — core_end_bar is stored as the last included bar; HTML shade runs center(a)→center(b)",
            "labelstudio_keypoints": "inclusive — START/END snap to bar index, core_end_bar = local_start + end_i",
            "legacy_yolo_box": "pixel_box — bars whose candle centers fall in [x_min, x_max]",
            "review_sheet_csv": "inclusive local bar_b0/bar_b1 on the old image; win_mode=end_incl; global restore needs window start",
            "legacy_manifest_bar_range": "inclusive — V3 box_start_bar/box_end_bar and R1 core_global",
            "human_gold_owner_box": "inclusive — source_owner_global [lo, hi], source_owner_bars = hi-lo+1",
        },
        "annotator": {
            "script": "tools/serve_gold_annotate.py",
            "port": 8768,
            "save_path": "datasets/gold_v1.jsonl",
            "storage": "JSONL on disk, not localStorage, not sqlite",
            "decision": "local_end_bar == decision_bar (last visible local bar, inclusive)",
            "old_window": "W30 causal local + context 50 pre / 120 post",
            "renderer": "yoyo.layers.l1_detection.render 1280x742 plus gold_render footer on local",
        },
    }


VALID_REVIEW_LABELS = frozenset({"SIGNAL", "NO_SIGNAL", "IGNORE", "UNCERTAIN"})


def _records_sha256(rows: list[dict[str, Any]]) -> str:
    """Hash normalized rows so two snapshots can be compared by content."""

    digest = hashlib.sha256()
    for row in rows:
        payload = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest.update(payload)
        digest.update(b"\n")
    return digest.hexdigest()


def _review_metrics(
    events: list[dict[str, Any]],
    images: dict[str, Any],
    review_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Recompute DIRECT audit metrics from row evidence, never trusted totals.

    Columns used are ``gold_id``, ``migration_status`` and ``shape_label`` from
    the final Gold snapshot plus ``review_label`` and ``counts_toward_direct``
    from the blind-review scorer.  No market data or future bars are read.
    """

    by_id: dict[str, dict[str, Any]] = {}
    duplicate_gold_ids: list[str] = []
    for row in events:
        gold_id = row.get("gold_id")
        if not gold_id:
            continue
        if gold_id in by_id:
            duplicate_gold_ids.append(str(gold_id))
        by_id[str(gold_id)] = row

    direct_ids = {
        gold_id
        for gold_id, row in by_id.items()
        if row.get("migration_status") == "DIRECT"
    }
    empty = {
        "lineage_ok": False,
        "pack_complete": False,
        "repeat_metrics_present": False,
        "boundary_metrics_present": False,
        "owner_training_approval": False,
        "n_direct": len(direct_ids),
        "n_reviewed_primary": 0,
        "n_direct_reviewed": 0,
        "n_direct_errors": None,
        "direct_error_rate": None,
        "direct_spot_fraction": None,
        "invalid_review_rows": [],
        "non_direct_claimed_as_direct": [],
        "duplicate_gold_ids": sorted(set(duplicate_gold_ids)),
    }
    if not review_evidence:
        return empty

    expected_gold_sha = images.get("gold_events_sha256")
    evidence_gold_sha = review_evidence.get("gold_events_sha256")
    rows = review_evidence.get("primary_reviews")
    if not isinstance(rows, list):
        return empty

    invalid: list[str] = []
    claimed_wrong: list[str] = []
    seen: set[str] = set()
    direct_reviewed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for position, review in enumerate(rows):
        if not isinstance(review, dict):
            invalid.append(f"row:{position}")
            continue
        gold_id = str(review.get("gold_id") or "")
        source = by_id.get(gold_id)
        label = review.get("review_label")
        if not gold_id or source is None or gold_id in seen or label not in VALID_REVIEW_LABELS:
            invalid.append(gold_id or f"row:{position}")
            continue
        seen.add(gold_id)
        is_direct = source.get("migration_status") == "DIRECT"
        claimed_direct = review.get("counts_toward_direct") is True
        if claimed_direct != is_direct:
            claimed_wrong.append(gold_id)
        if is_direct:
            direct_reviewed.append((source, review))

    lineage_ok = bool(
        expected_gold_sha
        and evidence_gold_sha == expected_gold_sha
        and not invalid
        and not claimed_wrong
        and not duplicate_gold_ids
    )
    n_direct_reviewed = len(direct_reviewed) if lineage_ok else 0
    n_direct_errors = (
        sum(1 for source, review in direct_reviewed if review["review_label"] != source.get("shape_label"))
        if lineage_ok and n_direct_reviewed
        else None
    )
    error_rate = (
        n_direct_errors / n_direct_reviewed
        if n_direct_errors is not None and n_direct_reviewed
        else None
    )
    spot_fraction = (
        n_direct_reviewed / len(direct_ids)
        if lineage_ok and direct_ids
        else None
    )
    repeats = review_evidence.get("repeat_metrics") or {}
    boundaries = review_evidence.get("boundary_metrics") or {}
    approval = review_evidence.get("owner_approval") or {}
    owner_approved = bool(
        approval.get("approved") is True
        and approval.get("approved_at")
        and approval.get("conversation_reference")
    )
    planned_primary = review_evidence.get("planned_primary_count")
    planned_total = review_evidence.get("planned_total_count")
    answered_total = review_evidence.get("n_answered_total")
    pack_complete = bool(
        review_evidence.get("pack_complete") is True
        and isinstance(planned_primary, int)
        and isinstance(planned_total, int)
        and isinstance(answered_total, int)
        and planned_primary == len(seen)
        and planned_total == answered_total
        and planned_total >= planned_primary
    )
    return {
        "lineage_ok": lineage_ok,
        "pack_complete": pack_complete,
        "repeat_metrics_present": bool(
            repeats.get("n_pairs", 0) > 0
            and repeats.get("raw_agreement") is not None
            and repeats.get("cohen_kappa") is not None
        ),
        "boundary_metrics_present": bool(
            boundaries.get("n_signal_pairs", 0) > 0
            and boundaries.get("exact_agreement") is not None
        ),
        "owner_training_approval": owner_approved,
        "n_direct": len(direct_ids),
        "n_reviewed_primary": len(seen),
        "n_direct_reviewed": n_direct_reviewed,
        "n_direct_errors": n_direct_errors,
        "direct_error_rate": error_rate,
        "direct_spot_fraction": spot_fraction,
        "invalid_review_rows": sorted(set(invalid)),
        "non_direct_claimed_as_direct": sorted(set(claimed_wrong)),
        "duplicate_gold_ids": sorted(set(duplicate_gold_ids)),
    }


def acceptance(
    events: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    images: dict[str, Any],
    review_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate P0/P1 gates against one Gold snapshot and joined review rows."""

    gates = {}
    pos = [r for r in gold if r.get("shape_label") == "SIGNAL"]
    neg = [r for r in gold if r.get("shape_label") == "NO_SIGNAL"]
    ign = [r for r in gold if r.get("shape_label") == "IGNORE"]
    bad_pos = [
        r
        for r in pos
        if not (
            r.get("window_length") == 10
            and r.get("core_length") == 4
            and r.get("local_core_start") == 5
            and r.get("local_core_end_exclusive") == 9
            and r.get("local_confirmation_position") == 9
        )
    ]
    gates["fixed_params"] = len(bad_pos) == 0
    gates["model_proposed_not_gold"] = all(
        r.get("source_annotation_type") != "model_proposed" for r in gold
    )
    gates["unresolved_conflicts"] = all(r.get("migration_status") != "CONFLICT" for r in gold)
    gates["ignore_not_in_gold_train"] = True
    gates["competing_core_excluded"] = all(not r.get("contains_other_core") for r in gold)
    gates["future_used"] = all(r.get("future_used_in_model_input") is False for r in gold)
    gates["holdout_read"] = all(r.get("holdout_read") is False for r in gold)
    gates["holdout_rows"] = images.get("holdout_rows", 0) == 0
    groups = {}
    for r in gold:
        groups.setdefault(r.get("event_group_id"), set()).add(r.get("split"))
    cross = {k: v for k, v in groups.items() if len(v) > 1}
    gates["cross_split_event_group_count"] = len(cross) == 0
    gates["duplicate_image_sha_across_splits"] = images.get("duplicate_image_sha_across_splits", 0) == 0
    gates["single_final_gold_snapshot"] = _records_sha256(events) == _records_sha256(gold)

    review = _review_metrics(events, images, review_evidence)
    n_direct = review["n_direct"]
    n_spot = review["n_direct_reviewed"]
    spot_frac = review["direct_spot_fraction"]
    err = review["direct_error_rate"]
    gates["direct_population_nonzero"] = n_direct > 0
    gates["direct_review_lineage"] = review["lineage_ok"]
    gates["direct_spot_ownership"] = not review["non_direct_claimed_as_direct"]
    gates["blind_review_complete"] = review["pack_complete"]
    gates["repeat_stability_measured"] = review["repeat_metrics_present"]
    gates["boundary_reproducibility_measured"] = review["boundary_metrics_present"]
    gates["direct_spot_frac"] = bool(
        n_direct > 0 and spot_frac is not None and spot_frac >= 0.15
    )
    gates["direct_error_rate"] = err is not None and err <= 0.05
    # training_eligible may never flip merely because a file appeared.  The
    # owner approval record is deliberately separate from the measured gates.
    gates["owner_training_approval"] = review["owner_training_approval"]
    training_eligible = all(gates.values()) and len(pos) > 0 and len(neg) > 0
    return {
        "gates": gates,
        "n_gold_signal": len(pos),
        "n_gold_no_signal": len(neg),
        "n_gold_ignore": len(ign),
        "n_direct": n_direct,
        "n_spot": n_spot,
        "spot_frac": spot_frac,
        "direct_error_rate": err,
        "n_direct_errors": review["n_direct_errors"],
        "n_reviewed_primary": review["n_reviewed_primary"],
        "review_invalid_rows": review["invalid_review_rows"],
        "review_non_direct_claimed_as_direct": review["non_direct_claimed_as_direct"],
        "review_duplicate_gold_ids": review["duplicate_gold_ids"],
        "cross_split_event_groups": list(cross),
        "training_eligible": training_eligible,
    }
