"""P0/P1 acceptance must be evidence-bound and fail closed."""

from __future__ import annotations

from copy import deepcopy

from yoyo.datasets.legacy_gold_migration.audit import acceptance


GOLD_SHA = "a" * 64


def _event(index: int, label: str, status: str = "DIRECT") -> dict:
    positive = label == "SIGNAL"
    return {
        "gold_id": f"g{index:03d}",
        "event_group_id": f"group{index:03d}",
        "shape_label": label,
        "migration_status": status,
        "source_annotation_type": "human_gold_owner_box",
        "window_length": 10,
        "core_length": 4 if positive else None,
        "local_core_start": 5,
        "local_core_end_exclusive": 9,
        "local_confirmation_position": 9,
        "contains_other_core": False,
        "future_used_in_model_input": False,
        "holdout_read": False,
        "split": "train",
    }


def _images(**extra) -> dict:
    out = {
        "holdout_rows": 0,
        "duplicate_image_sha_across_splits": 0,
        "gold_events_sha256": GOLD_SHA,
    }
    out.update(extra)
    return out


def _events() -> list[dict]:
    return [_event(i, "SIGNAL" if i < 10 else "NO_SIGNAL") for i in range(20)]


def _evidence(rows, *, approved=False) -> dict:
    return {
        "gold_events_sha256": GOLD_SHA,
        "planned_primary_count": len(rows),
        "planned_total_count": len(rows) + 1,
        "n_answered_total": len(rows) + 1,
        "pack_complete": True,
        "primary_reviews": rows,
        "repeat_metrics": {"n_pairs": 1, "raw_agreement": 1.0, "cohen_kappa": 1.0},
        "boundary_metrics": {"n_signal_pairs": 1, "exact_agreement": 1.0},
        "owner_approval": (
            {
                "approved": True,
                "approved_at": "2026-08-20T00:00:00+00:00",
                "conversation_reference": "owner explicit approval",
            }
            if approved
            else {}
        ),
    }


def test_zero_direct_never_passes_the_percentage_gate() -> None:
    rows = [_event(0, "SIGNAL", "IGNORE"), _event(1, "NO_SIGNAL", "IGNORE")]
    result = acceptance(rows, rows, _images(direct_error_rate=0.0))
    assert result["n_direct"] == 0
    assert result["gates"]["direct_population_nonzero"] is False
    assert result["gates"]["direct_spot_frac"] is False
    assert result["gates"]["direct_error_rate"] is False
    assert result["training_eligible"] is False


def test_naked_error_rate_scalar_is_not_evidence() -> None:
    rows = _events()
    result = acceptance(rows, rows, _images(direct_error_rate=0.0))
    assert result["direct_error_rate"] is None
    assert result["gates"]["direct_review_lineage"] is False


def test_non_direct_row_cannot_claim_direct_spot_check_credit() -> None:
    rows = _events() + [_event(20, "NO_SIGNAL", "IGNORE")]
    reviews = [
        {
            "gold_id": "g020",
            "review_label": "NO_SIGNAL",
            "counts_toward_direct": True,
        }
    ]
    result = acceptance(rows, rows, _images(), _evidence(reviews))
    assert result["gates"]["direct_spot_ownership"] is False
    assert result["gates"]["direct_review_lineage"] is False
    assert result["n_spot"] == 0


def test_pre_review_and_final_gold_snapshots_cannot_be_mixed() -> None:
    final = _events()
    stale = deepcopy(final)
    stale[0]["migration_status"] = "ONE_CLICK_REVIEW"
    result = acceptance(stale, final, _images())
    assert result["gates"]["single_final_gold_snapshot"] is False


def test_complete_direct_rows_recompute_rate_but_still_need_owner_approval() -> None:
    rows = _events()
    reviews = [
        {
            "gold_id": f"g{i:03d}",
            "review_label": rows[i]["shape_label"],
            "counts_toward_direct": True,
        }
        for i in range(3)
    ]
    result = acceptance(rows, rows, _images(), _evidence(reviews))
    assert result["n_direct"] == 20
    assert result["n_spot"] == 3
    assert result["spot_frac"] == 0.15
    assert result["direct_error_rate"] == 0.0
    assert result["gates"]["direct_spot_frac"] is True
    assert result["gates"]["direct_error_rate"] is True
    assert result["gates"]["owner_training_approval"] is False
    assert result["training_eligible"] is False


def test_explicit_owner_approval_is_separate_from_measurement() -> None:
    rows = _events()
    reviews = [
        {
            "gold_id": f"g{i:03d}",
            "review_label": rows[i]["shape_label"],
            "counts_toward_direct": True,
        }
        for i in range(3)
    ]
    result = acceptance(rows, rows, _images(), _evidence(reviews, approved=True))
    assert all(result["gates"].values())
    assert result["training_eligible"] is True
