from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.plan_15m_candidate_dataset_release import (
    current_gate_status,
    write_new_json,
)
from yoyo.datasets.candidate_boundary_review import GEOMETRY_FIELDS, geometry_from_choices
from yoyo.datasets.candidate_dataset_release import (
    DatasetReleaseError,
    plan_dataset_release,
)


ROOT = Path(__file__).resolve().parents[1]
PREREG_PATH = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-dataset-release-gate9000-v1"
    / "preregistration.json"
)


def _fixture() -> tuple[
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
    str,
    str,
]:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    prereg["source"]["expected_source_rows"] = 20
    prereg["required_review_summary"].update(
        {"source_rows": 20, "answered_rows": 20, "missing_rows": 0}
    )
    joined: list[dict[str, object]] = []
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    short_keeps = 16
    for index in range(20):
        direction = "SHORT" if index < 18 else "LONG"
        decision = "KEEP" if index < short_keeps or index >= 18 else "DROP"
        anchor = base + timedelta(days=index)
        # The second window touches the first without sharing a bar.  It must
        # remain in the same dependency block under the frozen touch rule.
        if index == 1:
            anchor = base + timedelta(hours=3, minutes=30)
        row: dict[str, object] = {
            "event_id": f"event-{index:02d}",
            "symbol": "BTC-USDT-SWAP" if index < 18 else "ETH-USDT-SWAP",
            "direction": direction,
            "anchor_time": anchor.isoformat(),
            "review_sha256": f"{index + 1:064x}"[-64:],
            "rank": index + 1,
            "decision": decision,
            "answered": True,
            "sample_owner_confirmed": True,
            "geometry_owner_confirmed": decision == "KEEP",
            "direction_protocol_status": (
                "owner_short_protocol_frozen"
                if direction == "SHORT"
                else "mirror_unconfirmed"
            ),
            "eligible_for_later_owner_release_preview": (
                direction == "SHORT" and decision == "KEEP"
            ),
            "negative_eligible": False,
            "training_eligible": False,
            "production_eligible": False,
            "holdout_read": False,
            "reviewed_at": "2026-08-26T04:30:00+08:00",
            "note": None,
        }
        if decision == "KEEP":
            choices = geometry_from_choices(
                input_window_bars=14 + index % 9,
                core_width_bars=4 + index % 4,
                confirmation_bars=3 + index % 3,
            )
            row.update(choices)
        else:
            row.update({field: None for field in GEOMETRY_FIELDS})
        joined.append(row)
    preview = [
        dict(row)
        for row in joined
        if row["eligible_for_later_owner_release_preview"] is True
    ]
    summary: dict[str, object] = {
        "status": "complete_validated",
        "complete": True,
        "source_rows": 20,
        "answered_rows": 20,
        "missing_rows": 0,
        "position_degeneracy_audit": {"passed": True},
        "eligibility": {
            "training_eligible_true": 0,
            "negative_eligible_true": 0,
            "production_eligible_true": 0,
        },
    }
    summary_hash = "a" * 64
    preview_hash = "b" * 64
    receipt: dict[str, object] = {
        "schema_version": 1,
        "release_id": "owner-short-release-test",
        "review_experiment_id": prereg["source"]["review_experiment_id"],
        "review_summary_sha256": summary_hash,
        "short_keep_preview_sha256": preview_hash,
        "short_keep_rows": len(preview),
        "released_direction": "SHORT",
        "owner_dataset_release_received": True,
        "released_at": "2026-08-26T04:31:00+08:00",
        "scope": "P1 planning only",
        "training_authorized": False,
    }
    return prereg, summary, joined, preview, receipt, summary_hash, preview_hash


def _plan(**changes: object):
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    values: dict[str, object] = {
        "review_summary": summary,
        "joined_rows": joined,
        "preview_rows": preview,
        "release_receipt": receipt,
        "review_summary_sha256": summary_hash,
        "preview_sha256": preview_hash,
        "prereg": prereg,
    }
    values.update(changes)
    return plan_dataset_release(**values)


def test_release_plan_is_chronological_planning_only() -> None:
    plan = _plan()
    assert len(plan.positive_rows) == 16
    assert len(plan.guard_rows) == 18
    assert plan.summary["guard_direction_counts"] == {"SHORT": 16, "LONG": 2}
    split = plan.summary["split_profile"]
    assert split["dependency_blocks"] == 15
    assert split["actual_gap_bars"] >= 150
    assert {row["split"] for row in plan.positive_rows} == {"train", "val", "drop"}
    assert not (
        {row["dependency_id"] for row in plan.positive_rows if row["split"] == "train"}
        & {row["dependency_id"] for row in plan.positive_rows if row["split"] == "val"}
    )
    assert all(row["direction"] == "SHORT" for row in plan.positive_rows)
    assert all(row["training_eligible"] is False for row in plan.positive_rows)
    assert all(row["production_eligible"] is False for row in plan.guard_rows)
    long_guards = [row for row in plan.guard_rows if row["direction"] == "LONG"]
    assert all(row["protection_only"] is True for row in long_guards)
    targets = plan.negative_target_profile
    assert targets["total_target_rows"] == targets["train_positive_rows"] * 3
    assert targets["negative_rows_selected"] == 0
    assert targets["historical_owner_guard_union_complete"] is False
    assert plan.summary["outputs"] == {
        "training_images": 0,
        "yolo_labels": 0,
        "negative_rows": 0,
        "epochs": 0,
        "weights": 0,
    }


def test_release_must_bind_exact_summary_hash() -> None:
    prereg, summary, joined, preview, receipt, _summary_hash, preview_hash = _fixture()
    with pytest.raises(DatasetReleaseError, match="review_summary_sha256 drifted"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256="c" * 64,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_release_cannot_authorize_training() -> None:
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    receipt["training_authorized"] = True
    with pytest.raises(DatasetReleaseError, match="must not authorize training"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256=summary_hash,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_long_row_cannot_enter_short_release_preview() -> None:
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    long_row = next(row for row in joined if row["direction"] == "LONG")
    preview.append(copy.deepcopy(long_row))
    receipt["short_keep_rows"] = len(preview)
    with pytest.raises(DatasetReleaseError, match="non-SHORT-KEEP"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256=summary_hash,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_position_audit_failure_blocks_release() -> None:
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    summary["position_degeneracy_audit"] = {"passed": False}
    with pytest.raises(DatasetReleaseError, match="position-degeneracy"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256=summary_hash,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_incomplete_review_summary_blocks_release() -> None:
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    summary["complete"] = False
    with pytest.raises(DatasetReleaseError, match="complete is not release-ready"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256=summary_hash,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_release_preview_flag_is_recomputed_not_trusted() -> None:
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    joined[0]["eligible_for_later_owner_release_preview"] = False
    preview[0] = copy.deepcopy(joined[0])
    with pytest.raises(DatasetReleaseError, match="release-preview flag drifted"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256=summary_hash,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_guard_that_reaches_holdout_blocks_release() -> None:
    prereg, summary, joined, preview, receipt, summary_hash, preview_hash = _fixture()
    joined[0]["anchor_time"] = "2026-05-03T23:00:00Z"
    preview[0] = copy.deepcopy(joined[0])
    with pytest.raises(DatasetReleaseError, match="guard_end_time touches holdout"):
        plan_dataset_release(
            review_summary=summary,
            joined_rows=joined,
            preview_rows=preview,
            release_receipt=receipt,
            review_summary_sha256=summary_hash,
            preview_sha256=preview_hash,
            prereg=prereg,
        )


def test_current_status_records_both_real_blockers(tmp_path: Path) -> None:
    status = current_gate_status(
        prereg_path=PREREG_PATH,
        review_dir=tmp_path / "missing-review",
        release_path=tmp_path / "missing-release.json",
        planner_commit="f" * 40,
    )
    assert status["status"] == "blocked_pending_complete_owner_review_and_explicit_release"
    assert status["missing_requirements"] == [
        "complete_owner_review_summary",
        "explicit_hash_bound_owner_short_release",
    ]
    assert status["counts"]["repository_formal_owner_answers"] == 0
    assert status["counts"]["training_images"] == 0
    assert status["holdout"]["read"] is False
    assert status["remote"]["training_started"] is False


def test_status_receipt_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    write_new_json(path, {"status": "first"})
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_new_json(path, {"status": "second"})
