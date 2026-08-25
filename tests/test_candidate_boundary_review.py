"""Contract tests for the 9,000-candidate Owner boundary review surface."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.build_15m_candidate_boundary_review import render_html
from yoyo.datasets.candidate_boundary_review import (
    BoundaryReviewError,
    geometry_from_choices,
    validate_export,
    validate_source_rows,
)


ROOT = Path(__file__).resolve().parents[1]
PREREG = json.loads(
    (
        ROOT
        / "experiments/active/exp-15m-ma-launch-boundary-review9000-v1/preregistration.json"
    ).read_text(encoding="utf-8")
)


def source_row(event_id: str, direction: str, rank: int) -> dict:
    return {
        "event_id": event_id,
        "symbol": f"{direction}_TEST_USDT_SWAP",
        "direction": direction,
        "rank": rank,
        "anchor_time": f"2024-01-0{rank}T00:00:00+00:00",
        "review_sha256": ("a" if direction == "SHORT" else "b") * 64,
        "owner_verdict": "PENDING",
        "training_eligible": False,
        "production_eligible": False,
    }


def answer(row: dict, decision: str = "KEEP") -> dict:
    common = {
        field: row[field]
        for field in (
            "event_id",
            "symbol",
            "direction",
            "anchor_time",
            "review_sha256",
        )
    }
    common.update(
        {
            "decision": decision,
            "reviewed_at": "2026-08-26T01:02:03+00:00",
            "note": None,
        }
    )
    if decision == "KEEP":
        common.update(
            geometry_from_choices(
                input_window_bars=18,
                core_width_bars=5,
                confirmation_bars=3,
            )
        )
    else:
        common.update(
            {
                "input_start_review_i": None,
                "input_end_review_i": None,
                "input_window_bars": None,
                "core_start_review_i": None,
                "core_end_review_i": None,
                "core_width_bars": None,
                "confirmation_bars": None,
                "box_center_ratio": None,
            }
        )
    return common


def payload(rows: list[dict], answers: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "pack_id": PREREG["experiment_id"],
        "source_manifest_sha256": PREREG["source"]["candidate_manifest_sha256"],
        "protocol_sha256": PREREG["protocol"]["sha256"],
        "exported_at": "2026-08-26T01:03:00+00:00",
        "complete": len(answers) == len(rows),
        "n_total": len(rows),
        "n_answered": len(answers),
        "answers": answers,
    }


def test_geometry_is_exact_inclusive_bar_arithmetic() -> None:
    geometry = geometry_from_choices(
        input_window_bars=18,
        core_width_bars=5,
        confirmation_bars=3,
    )
    assert geometry == {
        "input_start_review_i": 13,
        "input_end_review_i": 30,
        "input_window_bars": 18,
        "core_start_review_i": 23,
        "core_end_review_i": 27,
        "core_width_bars": 5,
        "confirmation_bars": 3,
        "box_center_ratio": 0.705882,
    }


@pytest.mark.parametrize(
    "choices",
    [
        {"input_window_bars": 13, "core_width_bars": 5, "confirmation_bars": 3},
        {"input_window_bars": 23, "core_width_bars": 5, "confirmation_bars": 3},
        {"input_window_bars": 18, "core_width_bars": 3, "confirmation_bars": 3},
        {"input_window_bars": 18, "core_width_bars": 8, "confirmation_bars": 3},
        {"input_window_bars": 18, "core_width_bars": 5, "confirmation_bars": 2},
        {"input_window_bars": 18, "core_width_bars": 5, "confirmation_bars": 6},
    ],
)
def test_geometry_rejects_values_outside_frozen_ranges(choices: dict) -> None:
    with pytest.raises(BoundaryReviewError):
        geometry_from_choices(**choices)


def test_complete_export_is_identity_bound_but_stays_ineligible() -> None:
    rows = [source_row("short-1", "SHORT", 1), source_row("long-1", "LONG", 1)]
    result = validate_export(
        payload(rows, [answer(rows[0]), answer(rows[1], "DROP")]),
        source_rows=rows,
        prereg=PREREG,
    )
    assert result.summary["status"] == "complete_validated"
    assert result.summary["short_keep_release_preview_rows"] == 1
    assert result.summary["long_keep_mirror_unconfirmed_rows"] == 0
    assert result.summary["eligibility"]["training_eligible_true"] == 0
    assert all(row["training_eligible"] is False for row in result.joined_rows)
    assert all(row["negative_eligible"] is False for row in result.joined_rows)


def test_long_keep_remains_mirror_unconfirmed() -> None:
    row = source_row("long-1", "LONG", 1)
    result = validate_export(
        payload([row], [answer(row)]),
        source_rows=[row],
        prereg=PREREG,
    )
    joined = result.joined_rows[0]
    assert joined["direction_protocol_status"] == "mirror_unconfirmed"
    assert joined["eligible_for_later_owner_release_preview"] is False
    assert result.summary["long_keep_mirror_unconfirmed_rows"] == 1


def test_incomplete_export_requires_explicit_progress_mode() -> None:
    rows = [source_row("short-1", "SHORT", 1), source_row("long-1", "LONG", 1)]
    progress = payload(rows, [answer(rows[0])])
    with pytest.raises(BoundaryReviewError, match="incomplete"):
        validate_export(progress, source_rows=rows, prereg=PREREG)
    result = validate_export(
        progress,
        source_rows=rows,
        prereg=PREREG,
        require_complete=False,
    )
    assert result.summary["status"] == "incomplete_validated"
    assert result.summary["missing_rows"] == 1


def test_duplicate_unknown_and_identity_drift_fail_closed() -> None:
    row = source_row("short-1", "SHORT", 1)
    duplicate = payload([row], [answer(row), answer(row)])
    duplicate["n_answered"] = 2
    duplicate["complete"] = False
    with pytest.raises(BoundaryReviewError, match="duplicate"):
        validate_export(
            duplicate,
            source_rows=[row],
            prereg=PREREG,
            require_complete=False,
        )

    unknown_answer = answer(row)
    unknown_answer["event_id"] = "unknown"
    with pytest.raises(BoundaryReviewError, match="unknown"):
        validate_export(
            payload([row], [unknown_answer]),
            source_rows=[row],
            prereg=PREREG,
        )

    drifted = answer(row)
    drifted["review_sha256"] = "c" * 64
    with pytest.raises(BoundaryReviewError, match="identity drift"):
        validate_export(
            payload([row], [drifted]),
            source_rows=[row],
            prereg=PREREG,
        )


def test_keep_arithmetic_and_nonkeep_geometry_fail_closed() -> None:
    row = source_row("short-1", "SHORT", 1)
    drifted = answer(row)
    drifted["core_start_review_i"] -= 1
    with pytest.raises(BoundaryReviewError, match="geometry arithmetic drift"):
        validate_export(
            payload([row], [drifted]),
            source_rows=[row],
            prereg=PREREG,
        )

    drop = answer(row, "DROP")
    drop["core_width_bars"] = 5
    with pytest.raises(BoundaryReviewError, match="must not carry geometry"):
        validate_export(
            payload([row], [drop]),
            source_rows=[row],
            prereg=PREREG,
        )


def test_source_candidates_must_stay_pending_and_ineligible() -> None:
    row = source_row("short-1", "SHORT", 1)
    validate_source_rows([row])
    promoted = copy.deepcopy(row)
    promoted["training_eligible"] = True
    with pytest.raises(BoundaryReviewError, match="training eligible"):
        validate_source_rows([promoted])


def test_html_has_no_bulk_accept_or_prefilled_geometry() -> None:
    items = [
        {
            "event_id": "short-1",
            "symbol": "SHORT_TEST_USDT_SWAP",
            "direction": "SHORT",
            "rank": 1,
            "anchor_time": "2024-01-01T00:00:00+00:00",
            "review_sha256": "a" * 64,
            "image": "../../../candidate/review.png",
            "source_order": 1,
        }
    ]
    page = render_html(items, PREREG)
    assert "state={answers:{},drafts:{}" in page
    assert "setAllAccepted" not in page
    assert "全部认可" not in page
    assert "KEEP 前必须逐项选择" in page
    assert PREREG["source"]["candidate_manifest_sha256"] in page
    assert '"event_id":"short-1"' in page
