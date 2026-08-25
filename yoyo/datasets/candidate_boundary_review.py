"""Validate Owner review receipts for 15-minute MA-launch candidates.

The source rows come from the pre-holdout candidate manifest and the geometry
contract comes from ``docs/protocol/local_signal_v2.md``: an input of 14--22
bars ending at candidate bar ``t``, an Owner-selected 4--7 bar core, and 3--5
post-core confirmation bars.  This module reads only candidate identity and
review metadata; it never reads OHLCV, future labels, model outputs, holdout
data, forward state or order state.

The validator deliberately keeps ``training_eligible`` and
``production_eligible`` false.  A valid review receipt is evidence for a later
dataset-release decision, not permission to materialize labels or train.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
DECISIONS = frozenset({"KEEP", "DROP", "UNCERTAIN"})
IDENTITY_FIELDS = (
    "event_id",
    "symbol",
    "direction",
    "anchor_time",
    "review_sha256",
)
GEOMETRY_FIELDS = (
    "input_start_review_i",
    "input_end_review_i",
    "input_window_bars",
    "core_start_review_i",
    "core_end_review_i",
    "core_width_bars",
    "confirmation_bars",
    "box_center_ratio",
)
ANSWER_FIELDS = frozenset(
    (*IDENTITY_FIELDS, "decision", "reviewed_at", "note", *GEOMETRY_FIELDS)
)


class BoundaryReviewError(ValueError):
    """Raised when a source manifest or Owner export violates the frozen contract."""


@dataclass(frozen=True)
class ReviewContract:
    """Frozen review indices and allowed Local Signal V2 geometry ranges."""

    selection_i: int = 30
    review_bars: int = 48
    input_window_min: int = 14
    input_window_max: int = 22
    core_width_min: int = 4
    core_width_max: int = 7
    confirmation_min: int = 3
    confirmation_max: int = 5


@dataclass(frozen=True)
class ReviewValidation:
    """Normalized answers and a fail-closed audit summary."""

    answers: tuple[dict[str, Any], ...]
    joined_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def _aware_iso(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BoundaryReviewError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BoundaryReviewError(f"{field} is not an ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise BoundaryReviewError(f"{field} must include a timezone: {value}")
    return value


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BoundaryReviewError(f"{field} must be an integer")
    return value


def geometry_from_choices(
    *,
    input_window_bars: int,
    core_width_bars: int,
    confirmation_bars: int,
    contract: ReviewContract = ReviewContract(),
) -> dict[str, Any]:
    """Derive exact inclusive review indices from three explicit Owner choices."""

    window = _integer(input_window_bars, field="input_window_bars")
    width = _integer(core_width_bars, field="core_width_bars")
    confirmation = _integer(confirmation_bars, field="confirmation_bars")
    if not contract.input_window_min <= window <= contract.input_window_max:
        raise BoundaryReviewError(
            f"input_window_bars must be {contract.input_window_min}..{contract.input_window_max}"
        )
    if not contract.core_width_min <= width <= contract.core_width_max:
        raise BoundaryReviewError(
            f"core_width_bars must be {contract.core_width_min}..{contract.core_width_max}"
        )
    if not contract.confirmation_min <= confirmation <= contract.confirmation_max:
        raise BoundaryReviewError(
            f"confirmation_bars must be {contract.confirmation_min}..{contract.confirmation_max}"
        )

    input_end = contract.selection_i
    input_start = input_end - window + 1
    core_end = contract.selection_i - confirmation
    core_start = core_end - width + 1
    if input_start < 0 or input_end >= contract.review_bars:
        raise BoundaryReviewError("input window escaped the 48-bar review image")
    if not input_start <= core_start <= core_end <= input_end:
        raise BoundaryReviewError("core escaped the Owner-selected input window")
    denominator = input_end - input_start
    if denominator <= 0:
        raise BoundaryReviewError("input window has no bar-center span")
    center_ratio = round(
        (((core_start + core_end) / 2.0) - input_start) / denominator,
        6,
    )
    return {
        "input_start_review_i": input_start,
        "input_end_review_i": input_end,
        "input_window_bars": window,
        "core_start_review_i": core_start,
        "core_end_review_i": core_end,
        "core_width_bars": width,
        "confirmation_bars": confirmation,
        "box_center_ratio": center_ratio,
    }


def validate_source_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Validate immutable PENDING candidate identities and return an ID index."""

    if not rows:
        raise BoundaryReviewError("candidate source manifest is empty")
    by_id: dict[str, Mapping[str, Any]] = {}
    for number, row in enumerate(rows, 1):
        missing = [field for field in IDENTITY_FIELDS if field not in row]
        if missing:
            raise BoundaryReviewError(f"source row {number} misses identity fields: {missing}")
        event_id = str(row["event_id"])
        if event_id in by_id:
            raise BoundaryReviewError(f"duplicate source event_id: {event_id}")
        if str(row["direction"]) not in {"LONG", "SHORT"}:
            raise BoundaryReviewError(f"invalid source direction for {event_id}")
        if row.get("owner_verdict") != "PENDING":
            raise BoundaryReviewError(f"source candidate is not PENDING: {event_id}")
        if row.get("training_eligible") is not False:
            raise BoundaryReviewError(f"source candidate became training eligible: {event_id}")
        if row.get("production_eligible") is not False:
            raise BoundaryReviewError(f"source candidate became production eligible: {event_id}")
        digest = str(row["review_sha256"])
        if len(digest) != 64:
            raise BoundaryReviewError(f"invalid review_sha256 for {event_id}")
        _aware_iso(row["anchor_time"], field=f"source[{event_id}].anchor_time")
        by_id[event_id] = row
    return by_id


def normalize_answer(
    answer: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    contract: ReviewContract = ReviewContract(),
) -> dict[str, Any]:
    """Bind one answer to source identity and verify all geometry arithmetic."""

    unknown = sorted(set(answer) - ANSWER_FIELDS)
    missing = sorted(ANSWER_FIELDS - set(answer))
    if unknown:
        raise BoundaryReviewError(f"answer has unknown fields: {unknown}")
    if missing:
        raise BoundaryReviewError(f"answer misses fields: {missing}")
    for field in IDENTITY_FIELDS:
        if str(answer[field]) != str(source[field]):
            raise BoundaryReviewError(
                f"identity drift for {source['event_id']} field {field}"
            )
    decision = str(answer["decision"])
    if decision not in DECISIONS:
        raise BoundaryReviewError(f"invalid decision for {source['event_id']}: {decision}")
    reviewed_at = _aware_iso(
        answer["reviewed_at"], field=f"answer[{source['event_id']}].reviewed_at"
    )
    note = answer["note"]
    if note is not None and not isinstance(note, str):
        raise BoundaryReviewError(f"note must be a string or null: {source['event_id']}")
    if isinstance(note, str) and len(note) > 2000:
        raise BoundaryReviewError(f"note exceeds 2000 characters: {source['event_id']}")

    normalized = {
        field: str(source[field]) for field in IDENTITY_FIELDS
    }
    normalized.update({"decision": decision, "reviewed_at": reviewed_at, "note": note})
    if decision == "KEEP":
        expected = geometry_from_choices(
            input_window_bars=_integer(
                answer["input_window_bars"], field="input_window_bars"
            ),
            core_width_bars=_integer(
                answer["core_width_bars"], field="core_width_bars"
            ),
            confirmation_bars=_integer(
                answer["confirmation_bars"], field="confirmation_bars"
            ),
            contract=contract,
        )
        for field, expected_value in expected.items():
            actual = answer[field]
            if field == "box_center_ratio":
                if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                    raise BoundaryReviewError(f"{field} must be numeric")
                if abs(float(actual) - float(expected_value)) > 1e-6:
                    raise BoundaryReviewError(
                        f"geometry arithmetic drift for {source['event_id']} field {field}"
                    )
            elif _integer(actual, field=field) != expected_value:
                raise BoundaryReviewError(
                    f"geometry arithmetic drift for {source['event_id']} field {field}"
                )
        normalized.update(expected)
    else:
        populated = [field for field in GEOMETRY_FIELDS if answer[field] is not None]
        if populated:
            raise BoundaryReviewError(
                f"{decision} must not carry geometry for {source['event_id']}: {populated}"
            )
        normalized.update({field: None for field in GEOMETRY_FIELDS})
    return normalized


def _position_audit(
    answers: Sequence[Mapping[str, Any]], prereg: Mapping[str, Any]
) -> dict[str, Any]:
    keeps = [answer for answer in answers if answer["decision"] == "KEEP"]
    rules = prereg["protocol"]["position_degeneracy_audit_after_complete_review"]
    if not keeps:
        return {
            "keep_rows": 0,
            "status": "not_applicable_no_keeps",
            "passed": False,
            "training_blocked": True,
        }
    triples = {
        (
            int(answer["input_window_bars"]),
            int(answer["core_width_bars"]),
            int(answer["confirmation_bars"]),
        )
        for answer in keeps
    }
    windows = {int(answer["input_window_bars"]) for answer in keeps}
    widths = {int(answer["core_width_bars"]) for answer in keeps}
    confirmations = {int(answer["confirmation_bars"]) for answer in keeps}
    centers = {float(answer["box_center_ratio"]) for answer in keeps}
    checks = {
        "distinct_geometry_triples": len(triples)
        >= int(rules["minimum_distinct_geometry_triples"]),
        "distinct_input_lengths": len(windows)
        >= int(rules["minimum_distinct_input_lengths"]),
        "distinct_core_widths": len(widths)
        >= int(rules["minimum_distinct_core_widths"]),
        "distinct_confirmation_delays": len(confirmations)
        >= int(rules["minimum_distinct_confirmation_delays"]),
        "distinct_box_center_ratios": len(centers)
        >= int(rules["minimum_distinct_box_center_ratios"]),
    }
    passed = all(checks.values())
    return {
        "keep_rows": len(keeps),
        "status": "passed" if passed else "failed_degenerate_geometry",
        "passed": passed,
        "training_blocked": not passed,
        "checks": checks,
        "distinct": {
            "geometry_triples": len(triples),
            "input_lengths": sorted(windows),
            "core_widths": sorted(widths),
            "confirmation_delays": sorted(confirmations),
            "box_center_ratios": len(centers),
            "box_center_ratio_min": min(centers),
            "box_center_ratio_max": max(centers),
        },
    }


def validate_export(
    payload: Mapping[str, Any],
    *,
    source_rows: Sequence[Mapping[str, Any]],
    prereg: Mapping[str, Any],
    require_complete: bool = True,
    contract: ReviewContract = ReviewContract(),
) -> ReviewValidation:
    """Validate a browser export and retain false eligibility on every row."""

    required_top = {
        "schema_version",
        "pack_id",
        "source_manifest_sha256",
        "protocol_sha256",
        "exported_at",
        "complete",
        "n_total",
        "n_answered",
        "answers",
    }
    unknown_top = sorted(set(payload) - required_top)
    missing_top = sorted(required_top - set(payload))
    if unknown_top:
        raise BoundaryReviewError(f"export has unknown top-level fields: {unknown_top}")
    if missing_top:
        raise BoundaryReviewError(f"export misses top-level fields: {missing_top}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise BoundaryReviewError("export schema_version drifted")
    if payload["pack_id"] != prereg["experiment_id"]:
        raise BoundaryReviewError("export pack_id drifted")
    if payload["source_manifest_sha256"] != prereg["source"]["candidate_manifest_sha256"]:
        raise BoundaryReviewError("export source manifest hash drifted")
    if payload["protocol_sha256"] != prereg["protocol"]["sha256"]:
        raise BoundaryReviewError("export protocol hash drifted")
    _aware_iso(payload["exported_at"], field="exported_at")
    if not isinstance(payload["answers"], list):
        raise BoundaryReviewError("answers must be a list")

    source_by_id = validate_source_rows(source_rows)
    expected_total = len(source_rows)
    if _integer(payload["n_total"], field="n_total") != expected_total:
        raise BoundaryReviewError("export n_total differs from source manifest")
    if _integer(payload["n_answered"], field="n_answered") != len(payload["answers"]):
        raise BoundaryReviewError("export n_answered differs from answers length")
    computed_complete = len(payload["answers"]) == expected_total
    if not isinstance(payload["complete"], bool) or payload["complete"] != computed_complete:
        raise BoundaryReviewError("export complete flag is not derived from answer coverage")
    if require_complete and not computed_complete:
        raise BoundaryReviewError(
            f"review is incomplete: {len(payload['answers'])}/{expected_total}"
        )

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload["answers"]:
        if not isinstance(raw, Mapping):
            raise BoundaryReviewError("each answer must be an object")
        event_id = str(raw.get("event_id", ""))
        if event_id in seen:
            raise BoundaryReviewError(f"duplicate answer event_id: {event_id}")
        if event_id not in source_by_id:
            raise BoundaryReviewError(f"unknown answer event_id: {event_id}")
        seen.add(event_id)
        normalized.append(
            normalize_answer(raw, source=source_by_id[event_id], contract=contract)
        )
    if computed_complete and seen != set(source_by_id):
        raise BoundaryReviewError("complete export does not exactly cover source IDs")

    answer_by_id = {answer["event_id"]: answer for answer in normalized}
    joined: list[dict[str, Any]] = []
    for source in source_rows:
        event_id = str(source["event_id"])
        answer = answer_by_id.get(event_id)
        decision = answer["decision"] if answer else "PENDING"
        is_keep = decision == "KEEP"
        direction = str(source["direction"])
        joined_row = {
            field: str(source[field]) for field in IDENTITY_FIELDS
        }
        joined_row.update(
            {
                "rank": int(source["rank"]),
                "decision": decision,
                "answered": answer is not None,
                "sample_owner_confirmed": answer is not None,
                "geometry_owner_confirmed": bool(answer and is_keep),
                "direction_protocol_status": (
                    "owner_short_protocol_frozen"
                    if direction == "SHORT"
                    else "mirror_unconfirmed"
                ),
                "eligible_for_later_owner_release_preview": bool(
                    answer and is_keep and direction == "SHORT"
                ),
                "negative_eligible": False,
                "training_eligible": False,
                "production_eligible": False,
                "holdout_read": False,
            }
        )
        for field in (*GEOMETRY_FIELDS, "reviewed_at", "note"):
            joined_row[field] = answer[field] if answer else None
        joined.append(joined_row)

    counts = Counter(answer["decision"] for answer in normalized)
    side_decisions = {
        side: dict(
            Counter(
                answer["decision"]
                for answer in normalized
                if answer["direction"] == side
            )
        )
        for side in ("LONG", "SHORT")
    }
    position = _position_audit(normalized, prereg)
    summary = {
        "status": "complete_validated" if computed_complete else "incomplete_validated",
        "complete": computed_complete,
        "source_rows": expected_total,
        "answered_rows": len(normalized),
        "missing_rows": expected_total - len(normalized),
        "decision_counts": dict(counts),
        "side_decision_counts": side_decisions,
        "short_keep_release_preview_rows": sum(
            answer["decision"] == "KEEP" and answer["direction"] == "SHORT"
            for answer in normalized
        ),
        "long_keep_mirror_unconfirmed_rows": sum(
            answer["decision"] == "KEEP" and answer["direction"] == "LONG"
            for answer in normalized
        ),
        "position_degeneracy_audit": position,
        "eligibility": {
            "training_eligible_true": 0,
            "production_eligible_true": 0,
            "negative_eligible_true": 0,
            "requires_later_owner_dataset_release": True,
        },
        "holdout": {"read": False, "ohlcv_rows_materialized": 0},
    }
    return ReviewValidation(tuple(normalized), tuple(joined), summary)
