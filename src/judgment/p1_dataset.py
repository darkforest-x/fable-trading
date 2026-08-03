"""Immutable P1 short-L2 dataset contract and fail-closed consumer.

P1 is research-only.  Candle inputs are 15m OKX USDT-SWAP rows and are read
only while ``open_time < 2026-05-04T00:00:00Z``.  Candidate features use the
28-column ``side_aligned_v1`` extractor at the mapped signal bar.  Labels use
the next-bar open, signal-bar ATR14, short TP5/SL2 over 72 bars, conservative
same-bar SL, and linear short return.  Cost is applied once through
``src.costs``.  No model, threshold, ACTIVE pointer, or execution state is read.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.costs import RETURN_SEMANTIC_COST, deduct_round_trip_cost_once
from src.judgment.features import (
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows_for_semantics,
)
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS
from src.judgment.outcomes import OutcomeContractError, resolve_barrier_outcome
from src.judgment.forward_types import SL_MULT, TP_MULT

PROJECT_DIR = Path(__file__).resolve().parents[2]
HOLDOUT_CUTOFF = pd.Timestamp("2026-05-04T00:00:00Z")
PROTOCOL_VERSION = "p1_short_l2_preholdout_v1"
FEATURE_SCHEMA = "judgment_28_v1"
FEATURE_SEMANTICS = "side_aligned_v1"
TIMEFRAME = "15m"
SIDE = "short"
ENTRY_MODE = "next_bar_open"
SAME_BAR_POLICY = "conservative_sl"
RETURN_CONVENTION = "linear_short"
BAR_DURATION = pd.Timedelta(minutes=15)

BASE_COLUMNS = [
    "build_id",
    "protocol_version",
    "candidate_id",
    "event_id",
    "source",
    "symbol",
    "timeframe",
    "side",
    "window_start_i",
    "window_end_i",
    "latest_closed_i",
    "mapped_signal_i",
    "signal_time",
    "signal_closed_at",
    "global_tip_age_bars",
    "box_x_center",
    "box_y_center",
    "box_width",
    "box_height",
    "box_confidence",
    "box_class_id",
    "detector_path",
    "detector_sha256",
    "feature_schema",
    "feature_semantics",
    "feature_as_of",
    "feature_source_max_i",
]
OUTCOME_COLUMNS = [
    "entry_mode_research",
    "entry_time_research",
    "entry_price_research",
    "atr_at_signal",
    "tp_price",
    "sl_price",
    "horizon_bars",
    "same_bar_policy",
    "exit_reason",
    "label_tp_before_sl",
    "exit_offset",
    "exit_time_research",
    "exit_price_research",
    "gross_ret",
    "fee_swap_taker",
    "net_ret_swap_taker",
    "interval_start",
    "interval_end",
    "event_group_id",
    "data_quality_flags",
]
DATASET_COLUMNS = BASE_COLUMNS + FEATURE_COLUMNS + OUTCOME_COLUMNS


class P1DatasetContractError(ValueError):
    """P1 input, manifest, schema, or immutable byte contract was violated."""


@dataclass(frozen=True)
class CandidateObservation:
    """One live-parity selected detector observation before feature/label work."""

    source: str
    symbol: str
    window_start_i: int
    window_end_i: int
    latest_closed_i: int
    mapped_signal_i: int
    global_tip_age_bars: int
    box_x_center: float
    box_y_center: float
    box_width: float
    box_height: float
    box_confidence: float
    box_class_id: int


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def schema_sha256() -> str:
    payload = json.dumps(
        {
            "columns": DATASET_COLUMNS,
            "feature_columns": FEATURE_COLUMNS,
            "feature_schema": FEATURE_SCHEMA,
            "feature_semantics": FEATURE_SEMANTICS,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_utc(value: str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed


def load_preholdout_candles(
    path: Path,
    *,
    cutoff: pd.Timestamp = HOLDOUT_CUTOFF,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a sorted CSV prefix and stop before materializing boundary OHLC.

    The boundary row's timestamp is inspected to fail closed; its OHLC fields
    are never converted or appended.  No later line is read.  Unsorted,
    malformed, duplicate, or non-finite pre-holdout rows are contract errors,
    not silent drops.
    """
    cutoff_utc = _parse_utc(str(cutoff))
    rows: list[dict[str, Any]] = []
    previous: pd.Timestamp | None = None
    boundary_timestamp_checked = False
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"open_time", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise P1DatasetContractError(f"{path}: missing OHLCV columns")
        for raw in reader:
            try:
                timestamp = _parse_utc(raw["open_time"])
            except (TypeError, ValueError) as exc:
                raise P1DatasetContractError(f"{path}: invalid open_time") from exc
            if timestamp >= cutoff_utc:
                boundary_timestamp_checked = True
                break
            if previous is not None and timestamp <= previous:
                raise P1DatasetContractError(f"{path}: candle times are not strictly increasing")
            previous = timestamp
            try:
                values = {name: float(raw[name]) for name in ("open", "high", "low", "close", "volume")}
            except (TypeError, ValueError) as exc:
                raise P1DatasetContractError(f"{path}: invalid numeric OHLCV before cutoff") from exc
            if not all(np.isfinite(values[name]) for name in ("open", "high", "low", "close")):
                raise P1DatasetContractError(f"{path}: non-finite OHLC before cutoff")
            if min(values[name] for name in ("open", "high", "low", "close")) <= 0:
                raise P1DatasetContractError(f"{path}: non-positive OHLC before cutoff")
            rows.append({"open_time": timestamp, **values})
    frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    return frame, {
        "preholdout_rows_materialized": len(frame),
        "post_cutoff_ohlcv_rows_materialized": 0,
        "boundary_timestamp_checked": boundary_timestamp_checked,
        "first_open_time": str(frame["open_time"].iloc[0]) if len(frame) else None,
        "last_open_time": str(frame["open_time"].iloc[-1]) if len(frame) else None,
    }


def _representative_identity(observation: CandidateObservation) -> tuple[object, ...]:
    return (
        observation.source,
        observation.symbol,
        observation.mapped_signal_i,
        observation.window_start_i,
        observation.window_end_i,
        observation.box_x_center,
        observation.box_y_center,
        observation.box_width,
        observation.box_height,
        observation.box_confidence,
        observation.box_class_id,
    )


def build_candidate_row(
    *,
    frame: pd.DataFrame,
    featured: pd.DataFrame,
    observation: CandidateObservation,
    build_id: str,
    detector_path: str,
    detector_sha256: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build one canonical P1 row, returning an explicit reject reason."""
    signal_i = int(observation.mapped_signal_i)
    if signal_i < 0 or signal_i >= len(frame):
        return None, "signal_out_of_bounds"
    signal_time = _parse_utc(str(frame["open_time"].iloc[signal_i]))
    if signal_time >= HOLDOUT_CUTOFF:
        raise P1DatasetContractError("candidate reached holdout boundary")
    if int(observation.global_tip_age_bars) > 2:
        return None, "global_tip_age_gt_2"
    entry_i = signal_i + 1
    if entry_i + HORIZON_BARS > len(frame):
        return None, "insufficient_preholdout_horizon"

    atr = float(featured["atr14"].iloc[signal_i])
    atr_pct = float(featured["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None, "invalid_atr"
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None, "atr_below_canonical_floor"
    entry_price = float(frame["open"].iloc[entry_i])
    if not np.isfinite(entry_price) or entry_price <= 0:
        return None, "invalid_entry_price"

    try:
        outcome = resolve_barrier_outcome(
            frame,
            side=SIDE,
            entry_i=entry_i,
            entry_price=entry_price,
            atr=atr,
            tp_atr_mult=TP_MULT,
            sl_atr_mult=SL_MULT,
            horizon_bars=HORIZON_BARS,
            same_bar_policy=SAME_BAR_POLICY,
            gap_policy="barrier_price",
            return_convention=RETURN_CONVENTION,
            allow_partial=False,
        )
    except OutcomeContractError as exc:
        return None, f"outcome_contract:{exc}"
    if outcome.status != "closed" or outcome.gross_ret is None or outcome.exit_price is None:
        return None, "outcome_not_closed"

    features = extract_feature_rows_for_semantics(
        featured,
        [signal_i],
        feature_semantics=FEATURE_SEMANTICS,
        side=SIDE,
    ).iloc[0]
    missing = [name for name in FEATURE_COLUMNS if not np.isfinite(float(features[name]))]
    quality_flags = [f"missing_feature:{name}" for name in missing]

    gross = float(outcome.gross_ret)
    fee = float(RETURN_SEMANTIC_COST["net_taker"])
    net = deduct_round_trip_cost_once(
        gross,
        input_semantics="gross",
        target_semantics="net_taker",
    )
    signal_closed_at = signal_time + BAR_DURATION
    entry_time = _parse_utc(str(frame["open_time"].iloc[entry_i]))
    if entry_time <= signal_time:
        raise P1DatasetContractError("research entry is not strictly after signal")
    exit_i = entry_i + int(outcome.exit_offset) - 1
    exit_bar_open = _parse_utc(str(frame["open_time"].iloc[exit_i]))
    exit_time = exit_bar_open + BAR_DURATION
    tp_price = entry_price - TP_MULT * atr
    sl_price = entry_price + SL_MULT * atr
    if tp_price <= 0:
        return None, "non_positive_tp"

    event_id = stable_hash(PROTOCOL_VERSION, observation.source, observation.symbol, signal_time.isoformat())
    candidate_id = stable_hash(PROTOCOL_VERSION, *_representative_identity(observation))
    row: dict[str, Any] = {
        "build_id": build_id,
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate_id,
        "event_id": event_id,
        "source": observation.source,
        "symbol": observation.symbol,
        "timeframe": TIMEFRAME,
        "side": SIDE,
        "window_start_i": observation.window_start_i,
        "window_end_i": observation.window_end_i,
        "latest_closed_i": observation.latest_closed_i,
        "mapped_signal_i": signal_i,
        "signal_time": signal_time.isoformat(),
        "signal_closed_at": signal_closed_at.isoformat(),
        "global_tip_age_bars": observation.global_tip_age_bars,
        "box_x_center": observation.box_x_center,
        "box_y_center": observation.box_y_center,
        "box_width": observation.box_width,
        "box_height": observation.box_height,
        "box_confidence": observation.box_confidence,
        "box_class_id": observation.box_class_id,
        "detector_path": detector_path,
        "detector_sha256": detector_sha256,
        "feature_schema": FEATURE_SCHEMA,
        "feature_semantics": FEATURE_SEMANTICS,
        "feature_as_of": signal_time.isoformat(),
        "feature_source_max_i": signal_i,
        "entry_mode_research": ENTRY_MODE,
        "entry_time_research": entry_time.isoformat(),
        "entry_price_research": entry_price,
        "atr_at_signal": atr,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "horizon_bars": HORIZON_BARS,
        "same_bar_policy": SAME_BAR_POLICY,
        "exit_reason": outcome.outcome,
        "label_tp_before_sl": int(outcome.label or 0),
        "exit_offset": int(outcome.exit_offset),
        "exit_time_research": exit_time.isoformat(),
        "exit_price_research": float(outcome.exit_price),
        "gross_ret": gross,
        "fee_swap_taker": fee,
        "net_ret_swap_taker": net,
        "interval_start": entry_time.isoformat(),
        "interval_end": exit_time.isoformat(),
        "event_group_id": "",
        "data_quality_flags": ";".join(quality_flags),
    }
    for name in FEATURE_COLUMNS:
        row[name] = float(features[name])
    return {name: row[name] for name in DATASET_COLUMNS}, None


def assign_event_groups(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign deterministic connected components of overlapping intervals."""
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (row["source"], row["symbol"], row["interval_start"], row["candidate_id"]),
    )
    state: dict[tuple[str, str], tuple[pd.Timestamp, str]] = {}
    for row in ordered:
        key = (str(row["source"]), str(row["symbol"]))
        start = _parse_utc(str(row["interval_start"]))
        end = _parse_utc(str(row["interval_end"]))
        active = state.get(key)
        if active is None or start > active[0]:
            group_id = stable_hash(PROTOCOL_VERSION, *key, row["candidate_id"])
            state[key] = (end, group_id)
        else:
            group_id = active[1]
            state[key] = (max(active[0], end), group_id)
        row["event_group_id"] = group_id
    return sorted(
        ordered,
        key=lambda row: (row["signal_time"], row["source"], row["symbol"], row["candidate_id"]),
    )


def write_dataset_csv(rows: Iterable[dict[str, Any]], path: Path) -> str:
    """Write stable UTF-8 CSV bytes atomically and return their SHA256."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".partial")
    frame = pd.DataFrame(list(rows), columns=DATASET_COLUMNS)
    frame.to_csv(
        temp,
        index=False,
        columns=DATASET_COLUMNS,
        lineterminator="\n",
        float_format="%.17g",
        na_rep="",
    )
    temp.replace(output)
    return file_sha256(output)


def load_immutable_dataset(
    manifest_path: Path,
    *,
    expected_protocol: str = PROTOCOL_VERSION,
    require_training_eligible: bool = True,
) -> pd.DataFrame:
    """Load one explicit manifest; hash/schema/protocol mismatch fails closed."""
    manifest_file = Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise P1DatasetContractError(f"cannot read manifest {manifest_file}") from exc
    if manifest.get("protocol_version") != expected_protocol:
        raise P1DatasetContractError("manifest protocol mismatch")
    if manifest.get("schema_sha256") != schema_sha256():
        raise P1DatasetContractError("manifest schema hash mismatch")
    if require_training_eligible and manifest.get("training_eligible") is not True:
        raise P1DatasetContractError("dataset is not marked training_eligible=true")
    raw_path = Path(str(manifest.get("dataset_path", "")))
    dataset_path = raw_path if raw_path.is_absolute() else PROJECT_DIR / raw_path
    if not dataset_path.is_file():
        raise P1DatasetContractError(f"dataset missing: {dataset_path}")
    actual = file_sha256(dataset_path)
    if actual != manifest.get("dataset_sha256"):
        raise P1DatasetContractError("dataset bytes hash mismatch")
    frame = pd.read_csv(dataset_path)
    if list(frame.columns) != DATASET_COLUMNS:
        raise P1DatasetContractError("dataset column order mismatch")
    if len(frame) != int(manifest.get("row_count", -1)):
        raise P1DatasetContractError("dataset row count mismatch")
    if set(frame["protocol_version"].astype(str)) != {expected_protocol}:
        raise P1DatasetContractError("row protocol mismatch")
    signal_times = pd.to_datetime(frame["signal_time"], utc=True, errors="raise")
    if (signal_times >= HOLDOUT_CUTOFF).any():
        raise P1DatasetContractError("dataset contains holdout signal time")
    return frame


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
