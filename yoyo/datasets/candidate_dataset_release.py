"""Plan a released 15-minute SHORT candidate dataset without reading OHLCV.

Inputs are the complete, hash-pinned Owner review artifacts produced by
``candidate_boundary_review`` plus an explicit Owner release receipt.  The
only time-indexed values used are ``anchor_time`` and the reviewed
``input_window_bars``, ``core_width_bars`` and ``confirmation_bars``.  Input
and core timestamps are derived at a fixed 15-minute cadence; no market row,
future outcome, model score, holdout value, forward state or order state is
read.

The result is planning evidence only: released SHORT positives receive
chronological train/val/drop assignments, while every confirmed KEEP core
(including ``mirror_unconfirmed`` LONG rows) becomes a negative-protection
interval.  No image, YOLO label or negative is materialized and all training
and production eligibility flags remain false.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from yoyo.datasets.candidate_boundary_review import GEOMETRY_FIELDS, geometry_from_choices


RELEASE_SCHEMA_VERSION = 1
RELEASE_FIELDS = frozenset(
    {
        "schema_version",
        "release_id",
        "review_experiment_id",
        "review_summary_sha256",
        "short_keep_preview_sha256",
        "short_keep_rows",
        "released_direction",
        "owner_dataset_release_received",
        "released_at",
        "scope",
        "training_authorized",
    }
)


class DatasetReleaseError(ValueError):
    """Raised when review, release or time-planning evidence fails closed."""


@dataclass(frozen=True)
class DatasetReleaseContract:
    """Frozen planning constants inherited from the Owner-short dataset lineage."""

    timeframe_minutes: int = 15
    validation_fraction: float = 0.15
    purge_bars: int = 150
    guard_bars: int = 12
    easy_per_train_positive: int = 1
    hard_per_train_positive: int = 2
    holdout_start: str = "2026-05-04T00:00:00Z"


@dataclass(frozen=True)
class DatasetReleasePlan:
    """Planning-only rows and audit summary; never a materialized dataset."""

    positive_rows: tuple[dict[str, Any], ...]
    guard_rows: tuple[dict[str, Any], ...]
    negative_target_profile: dict[str, Any]
    summary: dict[str, Any]


def _aware_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DatasetReleaseError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DatasetReleaseError(f"{field} is not an ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise DatasetReleaseError(f"{field} must include a timezone: {value}")
    return parsed


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DatasetReleaseError(f"{field} must be an integer")
    return value


def _false(value: Any, *, field: str) -> None:
    if value is not False:
        raise DatasetReleaseError(f"{field} must remain false")


def _stable_id(*parts: object) -> str:
    payload = "|".join(map(str, parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _iso(value: datetime) -> str:
    return value.isoformat()


def validate_release_receipt(
    receipt: Mapping[str, Any],
    *,
    prereg: Mapping[str, Any],
    review_summary_sha256: str,
    preview_sha256: str,
    preview_rows: int,
) -> dict[str, Any]:
    """Validate an explicit release bound to exact review and preview bytes."""

    unknown = sorted(set(receipt) - RELEASE_FIELDS)
    missing = sorted(RELEASE_FIELDS - set(receipt))
    if unknown:
        raise DatasetReleaseError(f"release receipt has unknown fields: {unknown}")
    if missing:
        raise DatasetReleaseError(f"release receipt misses fields: {missing}")
    if receipt["schema_version"] != RELEASE_SCHEMA_VERSION:
        raise DatasetReleaseError("release schema_version drifted")
    if not isinstance(receipt["release_id"], str) or not receipt["release_id"].strip():
        raise DatasetReleaseError("release_id must be a non-empty string")
    source = prereg["source"]
    if receipt["review_experiment_id"] != source["review_experiment_id"]:
        raise DatasetReleaseError("release review_experiment_id drifted")
    if receipt["review_summary_sha256"] != review_summary_sha256:
        raise DatasetReleaseError("release review_summary_sha256 drifted")
    if receipt["short_keep_preview_sha256"] != preview_sha256:
        raise DatasetReleaseError("release short_keep_preview_sha256 drifted")
    if _integer(receipt["short_keep_rows"], field="short_keep_rows") != preview_rows:
        raise DatasetReleaseError("release short_keep_rows differs from preview")
    release_contract = prereg["owner_release_receipt"]
    if receipt["released_direction"] != release_contract["released_direction"]:
        raise DatasetReleaseError("only the frozen SHORT direction may be released")
    if receipt["owner_dataset_release_received"] is not True:
        raise DatasetReleaseError("explicit Owner dataset release is missing")
    _aware_datetime(receipt["released_at"], field="released_at")
    if receipt["scope"] != release_contract["scope"]:
        raise DatasetReleaseError("release scope drifted")
    if receipt["training_authorized"] is not False:
        raise DatasetReleaseError("P1 planning release must not authorize training")
    return dict(receipt)


def _validate_review_bundle(
    *,
    summary: Mapping[str, Any],
    joined_rows: Sequence[Mapping[str, Any]],
    preview_rows: Sequence[Mapping[str, Any]],
    prereg: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    required = prereg["required_review_summary"]
    for field in ("status", "complete", "source_rows", "answered_rows", "missing_rows"):
        if summary.get(field) != required[field]:
            raise DatasetReleaseError(f"review summary {field} is not release-ready")
    position = summary.get("position_degeneracy_audit")
    if not isinstance(position, Mapping) or position.get("passed") is not True:
        raise DatasetReleaseError("position-degeneracy audit did not pass")
    eligibility = summary.get("eligibility")
    if not isinstance(eligibility, Mapping):
        raise DatasetReleaseError("review summary eligibility is missing")
    for field in (
        "training_eligible_true",
        "negative_eligible_true",
        "production_eligible_true",
    ):
        if eligibility.get(field) != 0:
            raise DatasetReleaseError(f"review summary {field} must be zero")

    expected_rows = int(prereg["source"]["expected_source_rows"])
    if len(joined_rows) != expected_rows:
        raise DatasetReleaseError(
            f"review joined rows differ from source: {len(joined_rows)}/{expected_rows}"
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    expected_preview: list[dict[str, Any]] = []
    for number, row in enumerate(joined_rows, 1):
        event_id = str(row.get("event_id", ""))
        if not event_id:
            raise DatasetReleaseError(f"joined row {number} has no event_id")
        if event_id in by_id:
            raise DatasetReleaseError(f"duplicate joined event_id: {event_id}")
        by_id[event_id] = row
        if row.get("answered") is not True or row.get("sample_owner_confirmed") is not True:
            raise DatasetReleaseError(f"joined row is not sample-confirmed: {event_id}")
        decision = row.get("decision")
        direction = row.get("direction")
        if decision not in {"KEEP", "DROP", "UNCERTAIN"}:
            raise DatasetReleaseError(f"joined row has invalid decision: {event_id}")
        if direction not in {"SHORT", "LONG"}:
            raise DatasetReleaseError(f"joined row has invalid direction: {event_id}")
        is_keep = decision == "KEEP"
        is_short_keep = is_keep and direction == "SHORT"
        if row.get("geometry_owner_confirmed") is not is_keep:
            raise DatasetReleaseError(
                f"geometry confirmation flag drifted from decision: {event_id}"
            )
        expected_direction_status = (
            "owner_short_protocol_frozen"
            if direction == "SHORT"
            else "mirror_unconfirmed"
        )
        if row.get("direction_protocol_status") != expected_direction_status:
            raise DatasetReleaseError(f"direction protocol status drifted: {event_id}")
        if row.get("eligible_for_later_owner_release_preview") is not is_short_keep:
            raise DatasetReleaseError(
                f"SHORT KEEP release-preview flag drifted: {event_id}"
            )
        if not is_keep and any(row.get(field) is not None for field in GEOMETRY_FIELDS):
            raise DatasetReleaseError(f"non-KEEP row carries geometry: {event_id}")
        _false(row.get("training_eligible"), field=f"{event_id}.training_eligible")
        _false(row.get("negative_eligible"), field=f"{event_id}.negative_eligible")
        _false(row.get("production_eligible"), field=f"{event_id}.production_eligible")
        _false(row.get("holdout_read"), field=f"{event_id}.holdout_read")
        if is_short_keep:
            expected_preview.append(dict(row))

    preview_by_id: dict[str, Mapping[str, Any]] = {}
    for row in preview_rows:
        event_id = str(row.get("event_id", ""))
        if event_id in preview_by_id:
            raise DatasetReleaseError(f"duplicate preview event_id: {event_id}")
        source = by_id.get(event_id)
        if source is None:
            raise DatasetReleaseError(f"unknown preview event_id: {event_id}")
        if dict(row) != dict(source):
            raise DatasetReleaseError(f"preview row drifted from joined row: {event_id}")
        if row.get("direction") != "SHORT" or row.get("decision") != "KEEP":
            raise DatasetReleaseError(f"preview contains non-SHORT-KEEP row: {event_id}")
        if row.get("geometry_owner_confirmed") is not True:
            raise DatasetReleaseError(f"preview geometry is not confirmed: {event_id}")
        preview_by_id[event_id] = row
    expected_ids = {row["event_id"] for row in expected_preview}
    if set(preview_by_id) != expected_ids:
        raise DatasetReleaseError("preview does not exactly equal all released SHORT KEEP rows")
    if not preview_rows:
        raise DatasetReleaseError("SHORT KEEP preview is empty")
    return by_id, expected_preview


def _derive_keep_geometry(
    row: Mapping[str, Any],
    *,
    contract: DatasetReleaseContract,
) -> dict[str, Any]:
    event_id = str(row["event_id"])
    try:
        expected = geometry_from_choices(
            input_window_bars=_integer(
                row.get("input_window_bars"), field=f"{event_id}.input_window_bars"
            ),
            core_width_bars=_integer(
                row.get("core_width_bars"), field=f"{event_id}.core_width_bars"
            ),
            confirmation_bars=_integer(
                row.get("confirmation_bars"), field=f"{event_id}.confirmation_bars"
            ),
        )
    except ValueError as exc:
        raise DatasetReleaseError(f"invalid reviewed geometry for {event_id}: {exc}") from exc
    for field, value in expected.items():
        actual = row.get(field)
        if field == "box_center_ratio":
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                raise DatasetReleaseError(f"{event_id}.{field} must be numeric")
            if abs(float(actual) - float(value)) > 1e-6:
                raise DatasetReleaseError(f"{event_id}.{field} arithmetic drifted")
        elif actual != value:
            raise DatasetReleaseError(f"{event_id}.{field} arithmetic drifted")

    anchor = _aware_datetime(row.get("anchor_time"), field=f"{event_id}.anchor_time")
    step = timedelta(minutes=contract.timeframe_minutes)
    input_start = anchor - step * (expected["input_window_bars"] - 1)
    input_end = anchor
    core_end = anchor - step * expected["confirmation_bars"]
    core_start = core_end - step * (expected["core_width_bars"] - 1)
    guard_start = core_start - step * contract.guard_bars
    guard_end = core_end + step * contract.guard_bars
    holdout = _aware_datetime(contract.holdout_start, field="holdout_start")
    for field, value in (
        ("anchor_time", anchor),
        ("input_start_time", input_start),
        ("input_end_time", input_end),
        ("core_start_time", core_start),
        ("core_end_time", core_end),
        ("guard_start_time", guard_start),
        ("guard_end_time", guard_end),
    ):
        if value >= holdout:
            raise DatasetReleaseError(f"{event_id}.{field} touches holdout")
    return {
        **expected,
        "anchor": anchor,
        "input_start": input_start,
        "input_end": input_end,
        "core_start": core_start,
        "core_end": core_end,
        "guard_start": guard_start,
        "guard_end": guard_end,
    }


def _dependency_blocks(
    rows: Sequence[dict[str, Any]], contract: DatasetReleaseContract
) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row["symbol"])].append(row)
    step = timedelta(minutes=contract.timeframe_minutes)
    blocks: list[dict[str, Any]] = []
    for symbol, symbol_rows in sorted(by_symbol.items()):
        symbol_rows.sort(
            key=lambda row: (
                row["_input_start"],
                row["_input_end"],
                str(row["event_id"]),
            )
        )
        current: list[dict[str, Any]] = []
        current_end: datetime | None = None
        for row in symbol_rows:
            if current and row["_input_start"] > current_end + step:
                blocks.append(_finish_block(symbol, current))
                current = []
                current_end = None
            current.append(row)
            current_end = (
                row["_input_end"]
                if current_end is None
                else max(current_end, row["_input_end"])
            )
        if current:
            blocks.append(_finish_block(symbol, current))
    return blocks


def _finish_block(symbol: str, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    start = min(row["_input_start"] for row in rows)
    end = max(row["_input_end"] for row in rows)
    return {
        "dependency_id": _stable_id("15m-release-v1", symbol, _iso(start), _iso(end)),
        "symbol": symbol,
        "start": start,
        "end": end,
        "rows": list(rows),
    }


def _assign_time_splits(
    rows: Sequence[dict[str, Any]], contract: DatasetReleaseContract
) -> dict[str, Any]:
    blocks = _dependency_blocks(rows, contract)
    if len(blocks) < 2:
        raise DatasetReleaseError("at least two dependency blocks are required")
    blocks.sort(key=lambda block: (block["end"], block["symbol"], block["dependency_id"]))
    n_val = max(1, int(round(len(blocks) * contract.validation_fraction)))
    if n_val >= len(blocks):
        raise DatasetReleaseError("validation assignment leaves no earlier blocks")
    val_ids = {block["dependency_id"] for block in blocks[-n_val:]}
    val_start = min(block["start"] for block in blocks if block["dependency_id"] in val_ids)
    train_cutoff = val_start - timedelta(
        minutes=contract.purge_bars * contract.timeframe_minutes
    )
    split_by_event: dict[str, str] = {}
    dependency_by_event: dict[str, str] = {}
    split_blocks: Counter[str] = Counter()
    for block in blocks:
        if block["dependency_id"] in val_ids:
            split = "val"
        elif block["end"] <= train_cutoff:
            split = "train"
        else:
            split = "drop"
        split_blocks[split] += 1
        for row in block["rows"]:
            event_id = str(row["event_id"])
            split_by_event[event_id] = split
            dependency_by_event[event_id] = block["dependency_id"]

    train_rows = [row for row in rows if split_by_event[str(row["event_id"])] == "train"]
    val_rows = [row for row in rows if split_by_event[str(row["event_id"])] == "val"]
    if not train_rows or not val_rows:
        raise DatasetReleaseError("chronological split produced an empty train or val")
    train_end = max(row["_input_end"] for row in train_rows)
    val_start_actual = min(row["_input_start"] for row in val_rows)
    gap_bars = (val_start_actual - train_end).total_seconds() / (
        contract.timeframe_minutes * 60
    )
    if gap_bars < contract.purge_bars:
        raise DatasetReleaseError(
            f"train/val purge {gap_bars} < {contract.purge_bars} bars"
        )
    train_dependencies = {
        dependency_by_event[str(row["event_id"])] for row in train_rows
    }
    val_dependencies = {
        dependency_by_event[str(row["event_id"])] for row in val_rows
    }
    if train_dependencies & val_dependencies:
        raise DatasetReleaseError("dependency block crosses train and val")
    return {
        "dependency_blocks": len(blocks),
        "dependency_block_counts": dict(split_blocks),
        "split_by_event": split_by_event,
        "dependency_by_event": dependency_by_event,
        "row_counts": dict(Counter(split_by_event.values())),
        "validation_blocks": n_val,
        "validation_fraction": contract.validation_fraction,
        "train_end_max": _iso(train_end),
        "val_start_min": _iso(val_start_actual),
        "purge_bars": contract.purge_bars,
        "actual_gap_bars": gap_bars,
    }


def plan_dataset_release(
    *,
    review_summary: Mapping[str, Any],
    joined_rows: Sequence[Mapping[str, Any]],
    preview_rows: Sequence[Mapping[str, Any]],
    release_receipt: Mapping[str, Any],
    review_summary_sha256: str,
    preview_sha256: str,
    prereg: Mapping[str, Any],
    contract: DatasetReleaseContract = DatasetReleaseContract(),
) -> DatasetReleasePlan:
    """Build a planning-only SHORT split and all-KEEP protection ledger."""

    by_id, expected_preview = _validate_review_bundle(
        summary=review_summary,
        joined_rows=joined_rows,
        preview_rows=preview_rows,
        prereg=prereg,
    )
    release = validate_release_receipt(
        release_receipt,
        prereg=prereg,
        review_summary_sha256=review_summary_sha256,
        preview_sha256=preview_sha256,
        preview_rows=len(preview_rows),
    )

    planning_rows: list[dict[str, Any]] = []
    geometry_by_id: dict[str, dict[str, Any]] = {}
    for source in expected_preview:
        geometry = _derive_keep_geometry(source, contract=contract)
        geometry_by_id[str(source["event_id"])] = geometry
        planning_rows.append(
            {
                "event_id": str(source["event_id"]),
                "symbol": str(source["symbol"]),
                "direction": "SHORT",
                "decision": "KEEP",
                "anchor_time": str(source["anchor_time"]),
                "review_sha256": str(source["review_sha256"]),
                "input_start_time": _iso(geometry["input_start"]),
                "input_end_time": _iso(geometry["input_end"]),
                "input_window_bars": int(geometry["input_window_bars"]),
                "core_start_time": _iso(geometry["core_start"]),
                "core_end_time": _iso(geometry["core_end"]),
                "core_width_bars": int(geometry["core_width_bars"]),
                "confirmation_bars": int(geometry["confirmation_bars"]),
                "box_center_ratio": float(geometry["box_center_ratio"]),
                "_input_start": geometry["input_start"],
                "_input_end": geometry["input_end"],
            }
        )
    split = _assign_time_splits(planning_rows, contract)
    positive_rows: list[dict[str, Any]] = []
    for row in planning_rows:
        event_id = str(row["event_id"])
        public = {key: value for key, value in row.items() if not key.startswith("_")}
        public.update(
            {
                "dependency_id": split["dependency_by_event"][event_id],
                "split": split["split_by_event"][event_id],
                "source_owner_release_id": release["release_id"],
                "dataset_plan_eligible": True,
                "training_eligible": False,
                "negative_eligible": False,
                "production_eligible": False,
                "holdout_read": False,
            }
        )
        positive_rows.append(public)
    positive_rows.sort(key=lambda row: (row["input_end_time"], row["symbol"], row["event_id"]))

    guard_rows: list[dict[str, Any]] = []
    keep_rows = [row for row in joined_rows if row.get("decision") == "KEEP"]
    for source in keep_rows:
        event_id = str(source["event_id"])
        geometry = geometry_by_id.get(event_id)
        if geometry is None:
            geometry = _derive_keep_geometry(source, contract=contract)
        direction = str(source["direction"])
        guard_rows.append(
            {
                "event_id": event_id,
                "symbol": str(source["symbol"]),
                "direction": direction,
                "core_start_time": _iso(geometry["core_start"]),
                "core_end_time": _iso(geometry["core_end"]),
                "guard_start_time": _iso(geometry["guard_start"]),
                "guard_end_time": _iso(geometry["guard_end"]),
                "guard_bars_each_side": contract.guard_bars,
                "released_short_positive": direction == "SHORT",
                "long_protocol_status": (
                    "not_applicable" if direction == "SHORT" else "mirror_unconfirmed"
                ),
                "protection_only": direction == "LONG",
                "negative_eligible": False,
                "training_eligible": False,
                "production_eligible": False,
                "holdout_read": False,
            }
        )
    guard_rows.sort(key=lambda row: (row["guard_start_time"], row["symbol"], row["event_id"]))

    train_rows = [row for row in positive_rows if row["split"] == "train"]
    train_by_window = Counter(int(row["input_window_bars"]) for row in train_rows)
    easy_by_window = {
        str(window): count * contract.easy_per_train_positive
        for window, count in sorted(train_by_window.items())
    }
    hard_by_window = {
        str(window): count * contract.hard_per_train_positive
        for window, count in sorted(train_by_window.items())
    }
    negative_profile = {
        "ratio_is_soft_target": True,
        "train_positive_rows": len(train_rows),
        "easy_target_rows": len(train_rows) * contract.easy_per_train_positive,
        "hard_target_rows": len(train_rows) * contract.hard_per_train_positive,
        "total_target_rows": len(train_rows)
        * (contract.easy_per_train_positive + contract.hard_per_train_positive),
        "easy_target_by_input_window": easy_by_window,
        "hard_target_by_input_window": hard_by_window,
        "negative_rows_selected": 0,
        "historical_owner_guard_union_complete": False,
        "selection_blocked": True,
        "next_requirement": "Union this ledger with every historical Owner-box guard, then sample same-symbol/same-split/same-W candidates without weakening any guard.",
        "training_eligible": False,
        "production_eligible": False,
    }
    summary = {
        "status": "release_plan_validated_not_materialized",
        "release_id": release["release_id"],
        "review_summary_sha256": review_summary_sha256,
        "short_keep_preview_sha256": preview_sha256,
        "source_rows": len(joined_rows),
        "released_short_keep_rows": len(positive_rows),
        "all_keep_guard_rows": len(guard_rows),
        "guard_direction_counts": dict(Counter(row["direction"] for row in guard_rows)),
        "split_profile": {key: value for key, value in split.items() if not key.endswith("_by_event")},
        "negative_target_profile": negative_profile,
        "outputs": {
            "training_images": 0,
            "yolo_labels": 0,
            "negative_rows": 0,
            "epochs": 0,
            "weights": 0,
        },
        "eligibility": {
            "dataset_plan_eligible": True,
            "dataset_materialization_eligible": False,
            "training_eligible": False,
            "production_eligible": False,
            "requires_separate_materialization_preregistration": True,
            "requires_historical_owner_guard_union": True,
        },
        "holdout": {
            "read": False,
            "ohlcv_rows_materialized": 0,
            "source_ohlcv_files_opened": 0,
        },
    }
    return DatasetReleasePlan(
        tuple(positive_rows), tuple(guard_rows), negative_profile, summary
    )
