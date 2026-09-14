"""Read-only bridge from frozen Owner geometry annotations to indicator research.

The bridge joins the Owner's offline annotation export to the frozen
``owner_box_refinement`` manifest without reopening images or replacing a
declared OHLCV source.  A loaded source contains only the prefix ending at the
latest requested main-window close.  Its required source columns are
``open_time/open/high/low/close/volume``; :func:`add_six_mas` derives the
renderer-contract SMA/EMA 20, 60, and 120 columns from ``close``.

Human rectangles are evidence only when they are completed ``annotation``
records.  Predictions and the manifest's historical central-half baseline are
never converted into gold intervals.  This module does not write data, alter
eligibility flags, or consume holdout rows.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWERS = ROOT / "output/offline_tasks/owner_review_exports/20260908T130556806730Z/answers.jsonl"
DEFAULT_MANIFEST = ROOT / "datasets/owner_box_refinement_20260907_v1/manifest.jsonl"
DEFAULT_GOLD_V1 = ROOT / "datasets/annotations/gold_v1.jsonl"
BAR = pd.Timedelta(minutes=15)
HOLDOUT = pd.Timestamp(HOLDOUT_START)
INITIAL_DEVELOPMENT_END = pd.Timestamp("2025-07-01T00:00:00Z")
BOX_STATUS = "owner_boxes"
NO_TARGET_STATUS = "owner_no_target_with_inherited_proposal"
SIDE_BY_LABEL = {"多头": "long", "空头": "short"}


class OwnerGoldBridgeError(ValueError):
    """Raised when a frozen identity or bounded source check cannot be proved."""


def _stamp(value: object, field: str) -> pd.Timestamp:
    moment = pd.Timestamp(value)
    if pd.isna(moment) or moment.tzinfo is None:
        raise OwnerGoldBridgeError(f"{field} must be an explicit UTC timestamp")
    return moment.tz_convert("UTC")


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                raise OwnerGoldBridgeError(f"blank JSONL row: {path}:{number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise OwnerGoldBridgeError(f"invalid JSONL row: {path}:{number}") from exc
            if not isinstance(value, dict):
                raise OwnerGoldBridgeError(f"JSONL row must be an object: {path}:{number}")
            yield value


def _exclusion(record: Mapping[str, Any], reason: str, detail: str | None = None) -> dict[str, Any]:
    excluded = {
        "review_id": record.get("review_id"),
        "annotation_id": record.get("annotation_id"),
        "task_id": record.get("task_id"),
        "status": record.get("status"),
        "reason": reason,
    }
    if detail is not None:
        excluded["detail"] = detail
    return excluded


def _manifest_map(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _jsonl(path):
        review_id = row.get("review_id")
        if not isinstance(review_id, str) or not review_id:
            raise OwnerGoldBridgeError("manifest row has no review_id")
        if review_id in result:
            raise OwnerGoldBridgeError(f"manifest review_id is not unique: {review_id}")
        result[review_id] = row
    return result


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise OwnerGoldBridgeError(f"{field} must be an integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise OwnerGoldBridgeError(f"{field} must be an integer") from exc
    if integer != value:
        raise OwnerGoldBridgeError(f"{field} must be an integer")
    return integer


def _validated_manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        main_start_i = _integer(row["main_start_i"], "main_start_i")
        main_end_i = _integer(row["main_end_i"], "main_end_i")
        transform = row["chart_transform"]
        source_path = row["source_path"]
        digest = row["main_window_ohlcv_ma_sha256"]
    except KeyError as exc:
        raise OwnerGoldBridgeError(f"manifest missing {exc.args[0]}") from exc
    if not isinstance(transform, Mapping):
        raise OwnerGoldBridgeError("manifest chart_transform must be an object")
    if not isinstance(source_path, str) or not source_path or Path(source_path).is_absolute():
        raise OwnerGoldBridgeError("manifest source_path must be a declared relative path")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise OwnerGoldBridgeError("manifest main-window digest is invalid")
    try:
        width, height = int(transform["width"]), int(transform["height"])
        left, plot_w, n_bars = int(transform["left"]), int(transform["plot_w"]), int(transform["n_bars"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OwnerGoldBridgeError("manifest chart_transform is incomplete") from exc
    if (width, height) != (1280, 742) or n_bars < 2 or plot_w <= 0:
        raise OwnerGoldBridgeError("manifest chart_transform is not the fixed 1280x742 chart")
    if main_start_i < 0 or main_end_i < main_start_i or main_end_i - main_start_i + 1 != n_bars:
        raise OwnerGoldBridgeError("manifest main-window indices do not match its transform")
    main_start_time = _stamp(row.get("main_start_time"), "main_start_time")
    main_end_time = _stamp(row.get("main_end_time"), "main_end_time")
    if main_end_time + BAR > HOLDOUT:
        raise OwnerGoldBridgeError("manifest main window reaches the holdout boundary")
    if main_end_time - main_start_time != (n_bars - 1) * BAR:
        raise OwnerGoldBridgeError("manifest main-window times do not match its transform")
    return {
        "source_path": source_path,
        "main_start_i": main_start_i,
        "main_end_i": main_end_i,
        "main_start_time": main_start_time.isoformat(),
        "main_end_time": main_end_time.isoformat(),
        "chart_transform": {"width": width, "height": height, "left": left, "plot_w": plot_w, "n_bars": n_bars},
        "main_window_ohlcv_ma_sha256": digest,
    }


def _selected_centers(rectangle: Mapping[str, Any], transform: Mapping[str, int]) -> tuple[int, int]:
    try:
        x = float(rectangle["x"])
        width = float(rectangle["width"])
        rotation = float(rectangle["rotation"])
        image_rotation = float(rectangle["image_rotation"])
        original_width = int(rectangle["original_width"])
        original_height = int(rectangle["original_height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OwnerGoldBridgeError("rectangle geometry is incomplete") from exc
    if not all(math.isfinite(value) for value in (x, width, rotation, image_rotation)):
        raise OwnerGoldBridgeError("rectangle geometry must be finite")
    if (rotation, image_rotation, original_width, original_height) != (0.0, 0.0, 1280, 742):
        raise OwnerGoldBridgeError("rotated or non-1280x742 rectangles are not accepted")
    if width <= 0.0 or x < 0.0 or x + width > 100.0:
        raise OwnerGoldBridgeError("rectangle x interval is outside the source image")
    x0, x1 = 1280.0 * x / 100.0, 1280.0 * (x + width) / 100.0
    n_bars, left, plot_w = transform["n_bars"], transform["left"], transform["plot_w"]
    centers = [int(left + i / (n_bars - 1) * plot_w) for i in range(n_bars)]
    selected = [i for i, center in enumerate(centers) if x0 <= center <= x1]
    if not selected:
        raise OwnerGoldBridgeError("rectangle selects no candle center")
    return selected[0], selected[-1]


def _case(record: Mapping[str, Any], manifest: Mapping[str, Any], *, rectangle: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = _validated_manifest(manifest)
    annotation_metadata = {
        "protocol_id": record.get("protocol_id"),
        "source_identity": copy.deepcopy(record.get("source_identity")),
    }
    base = {
        "review_id": record["review_id"],
        "annotation_id": record["annotation_id"],
        "task_id": record["task_id"],
        "status": record["status"],
        "symbol": manifest.get("symbol"),
        "image_path": str(Path(record.get("source_identity", {}).get("pack", "datasets/owner_box_refinement_20260907_v1")) / manifest.get("asset_roles", {}).get("image", "")),
        **metadata,
        "split_phase": "development" if _stamp(metadata["main_end_time"], "main_end_time") + BAR <= INITIAL_DEVELOPMENT_END else "later",
        "raw_annotation_metadata": annotation_metadata,
        "training_eligible": False,
        "production_eligible": False,
    }
    if rectangle is None:
        return {
            **base,
            "case_kind": "owner_no_target",
            "side": None,
            "original_rectangle": None,
            "raw_human_box": None,
            "core_start_i": None,
            "core_end_i": None,
            "primary_start_i": metadata["main_start_i"],
            "primary_end_i": metadata["main_end_i"],
        }
    labels = rectangle.get("label")
    if labels not in SIDE_BY_LABEL:
        raise OwnerGoldBridgeError("rectangle label is not an Owner long/short label")
    local_start, local_end = _selected_centers(rectangle, metadata["chart_transform"])
    return {
        **base,
        "case_kind": "owner_box",
        "side": SIDE_BY_LABEL[labels],
        "original_rectangle": copy.deepcopy(dict(rectangle)),
        "raw_human_box": copy.deepcopy(dict(rectangle)),
        "core_start_i": metadata["main_start_i"] + local_start,
        "core_end_i": metadata["main_start_i"] + local_end,
        "primary_start_i": metadata["main_start_i"] + local_start,
        "primary_end_i": metadata["main_start_i"] + local_end,
    }


def load_cases(
    answers_path: Path = DEFAULT_ANSWERS,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return accepted Owner cases and all rejected-export denominators.

    Invalid or ambiguous records remain in ``exclusions`` rather than being
    silently dropped.  A completed explicit no-target answer ignores any raw
    inherited prediction boxes and deliberately has no core interval.
    """
    manifests = _manifest_map(Path(manifest_path))
    cases: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    for record in _jsonl(Path(answers_path)):
        if record.get("record_kind") != "annotation":
            exclusions.append(_exclusion(record, "not_annotation"))
            continue
        if record.get("event_conflict") or record.get("status") == "conflict_needs_review":
            exclusions.append(_exclusion(record, "conflict_needs_review"))
            continue
        if record.get("effective_answer") is not True:
            exclusions.append(_exclusion(record, "not_effective_answer"))
            continue
        if record.get("raw_answer", {}).get("was_cancelled") is not False:
            exclusions.append(_exclusion(record, "cancelled_or_unknown"))
            continue
        review_id = record.get("review_id")
        if not isinstance(review_id, str) or review_id not in manifests:
            exclusions.append(_exclusion(record, "missing_manifest_identity"))
            continue
        if review_id in seen_review_ids:
            exclusions.append(_exclusion(record, "duplicate_eligible_annotation"))
            continue
        status = record.get("status")
        try:
            accepted_for_record: list[dict[str, Any]] = []
            if status == BOX_STATUS:
                boxes = record.get("effective_boxes")
                if not isinstance(boxes, list) or not boxes:
                    raise OwnerGoldBridgeError("owner_boxes answer has no effective rectangle")
                for box in boxes:
                    if not isinstance(box, Mapping):
                        raise OwnerGoldBridgeError("effective rectangle must be an object")
                    accepted_for_record.append(_case(record, manifests[review_id], rectangle=box))
            elif status == NO_TARGET_STATUS:
                if record.get("effective_boxes") not in ([], None):
                    raise OwnerGoldBridgeError("explicit no-target answer has effective rectangles")
                accepted_for_record.append(_case(record, manifests[review_id], rectangle=None))
            else:
                exclusions.append(_exclusion(record, "unsupported_annotation_status"))
                continue
        except OwnerGoldBridgeError as exc:
            exclusions.append(_exclusion(record, "invalid_annotation_or_manifest", str(exc)))
            continue
        cases.extend(accepted_for_record)
        seen_review_ids.add(review_id)
    return cases, exclusions


def window_sha256(window: pd.DataFrame) -> str:
    """Match the frozen owner-box-refinement main-window digest contract."""
    columns = ["open_time", "open", "high", "low", "close", "volume", *SIX_MA_COLUMNS]
    view = window.loc[:, columns].copy()
    view["open_time"] = pd.to_datetime(view["open_time"], utc=True).map(lambda t: t.isoformat())
    content = view.to_csv(index=False, float_format="%.17g", lineterminator="\n").encode()
    return hashlib.sha256(content).hexdigest()


def _declared_source(path: str, root: Path) -> Path:
    source = (root / path).resolve()
    try:
        source.relative_to(root.resolve())
    except ValueError as exc:
        raise OwnerGoldBridgeError("declared source path escapes repository root") from exc
    if not source.is_file():
        raise OwnerGoldBridgeError(f"declared source does not exist: {path}")
    return source


def load_source(group: Sequence[Mapping[str, Any]], *, root: Path = ROOT) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load one declared source's bounded pre-holdout prefix and verify every case.

    ``group`` must contain cases from exactly one declared source.  Any index,
    timestamp, continuity, or frozen-digest mismatch raises before a frame is
    returned, so downstream indicator research cannot continue on a partial or
    substituted source.
    """
    if not group:
        raise OwnerGoldBridgeError("source group is empty")
    source_paths = {case.get("source_path") for case in group}
    if len(source_paths) != 1 or not isinstance(next(iter(source_paths)), str):
        raise OwnerGoldBridgeError("source group must have exactly one declared source path")
    for case in group:
        end = _stamp(case.get("main_end_time"), "main_end_time")
        if end + BAR > HOLDOUT:
            raise OwnerGoldBridgeError("case reaches the holdout boundary before source load")
    source_path = next(iter(source_paths))
    source = _declared_source(source_path, Path(root))
    end_exclusive = min(HOLDOUT, max(_stamp(c["main_end_time"], "main_end_time") for c in group) + BAR)
    raw, prefix_audit = read_preholdout_prefix(source, end_exclusive=end_exclusive)
    frame = add_six_mas(raw)
    statuses: list[dict[str, Any]] = []
    for case in group:
        try:
            start, end = _integer(case["main_start_i"], "main_start_i"), _integer(case["main_end_i"], "main_end_i")
            if start < 0 or end < start or end >= len(frame):
                raise OwnerGoldBridgeError("main-window source indices are unavailable")
            window = frame.iloc[start:end + 1]
            expected_start = _stamp(case["main_start_time"], "main_start_time")
            expected_end = _stamp(case["main_end_time"], "main_end_time")
            times = pd.to_datetime(window["open_time"], utc=True)
            if (len(window) != end - start + 1 or times.iloc[0] != expected_start or times.iloc[-1] != expected_end
                    or not times.diff().iloc[1:].eq(BAR).all()):
                raise OwnerGoldBridgeError("main-window index/time mapping is incomplete or non-contiguous")
            if expected_end + BAR > HOLDOUT:
                raise OwnerGoldBridgeError("main window reaches the holdout boundary")
            if window_sha256(window) != case.get("main_window_ohlcv_ma_sha256"):
                raise OwnerGoldBridgeError("main-window OHLCV/MA digest mismatch")
        except (KeyError, OwnerGoldBridgeError) as exc:
            raise OwnerGoldBridgeError(f"unsafe case {case.get('review_id')}: {exc}") from exc
        statuses.append({"review_id": case.get("review_id"), "annotation_id": case.get("annotation_id"),
                         "status": "safe", "reason": None})
    return frame, {**prefix_audit, "end_exclusive": end_exclusive.isoformat(), "case_statuses": statuses}


def load_gold_v1_metadata(path: Path = DEFAULT_GOLD_V1) -> list[dict[str, Any]]:
    """Return known canonical bar intervals without assigning an unrecorded side."""
    result: list[dict[str, Any]] = []
    for row in _jsonl(Path(path)):
        result.append({
            "gold_id": row.get("gold_id"), "symbol": row.get("symbol"), "source_path": row.get("source_path"),
            "decision_bar": row.get("decision_bar"), "decision_time": row.get("decision_time"),
            "core_start_i": row.get("core_start_bar"), "core_end_i": row.get("core_end_bar"),
            "shape_label": row.get("shape_label"), "side": None,
        })
    return result
