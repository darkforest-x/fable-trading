"""Forward-log persistence and idempotent row merging."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.judgment.forward_types import (
    FORWARD_COLUMNS,
    LEGACY_PROTOCOL,
    LEGACY_SEMANTICS,
    LEGACY_STRATEGY,
    OUTCOME_COLUMNS,
    ForwardRecord,
    MergeResult,
)

# side is part of the key, so it needs its own marker for rows that predate it
LEGACY_SIDE = "legacy_unknown_side"
ForwardKey = tuple[str, str, str, str, str]


def read_forward_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=FORWARD_COLUMNS)
    return normalize_log(pd.read_csv(path))


def write_forward_log(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


def _entry_pending(record: dict) -> bool:
    """True while a tip-recorded row still carries proxy entry fields."""
    value = record.get("maker_filled")
    return value is None or (isinstance(value, float) and np.isnan(value))


def merge_forward_log(existing: pd.DataFrame, new_records: list[ForwardRecord]) -> MergeResult:
    current = normalize_log(existing)
    rows = {}
    for record in current.to_dict("records"):
        rows[row_key(record)] = record
    new_signals = 0
    closed_updates = 0
    for record in new_records:
        key = forward_key(
            record["source"], record["symbol"], pd.Timestamp(record["signal_time"]),
            record.get("side"), record.get("protocol_version"),
        )
        previous = rows.get(key)
        if previous is None:
            rows[key] = record
            new_signals += 1
            continue
        if str(previous["status"]) == "closed":
            continue
        merged = dict(previous)
        changed = False
        # Legacy-only entry backfill (2026-07-20 real-time tip path): a row recorded at the
        # tip carries a PROXY entry (signal-bar close) and empty maker_filled.
        # Once the true entry bar has printed, overwrite entry fields with the
        # real next-bar values. detected_at stays first-seen (lag accounting).
        if (
            str(previous.get("protocol_version")) == LEGACY_PROTOCOL
            and _entry_pending(previous)
            and not _entry_pending(record)
        ):
            for column in ("entry_time", "entry_price", "maker_filled"):
                merged[column] = record[column]
            changed = True
        if record["status"] == "closed":
            for column in OUTCOME_COLUMNS:
                # Old-schema compatibility: legacy updates do not carry the
                # appended P0.6 research/actual columns.
                merged[column] = record.get(column, merged.get(column, np.nan))
            closed_updates += 1
            changed = True
        if changed:
            rows[key] = merged
    if not rows:
        return MergeResult(pd.DataFrame(columns=FORWARD_COLUMNS), new_signals, closed_updates)
    frame = pd.DataFrame(rows.values())
    frame = normalize_log(frame).sort_values(["signal_time", "symbol"]).reset_index(drop=True)
    return MergeResult(frame, new_signals, closed_updates)


def normalize_log(frame: pd.DataFrame) -> pd.DataFrame:
    """Read any vintage of the log without crashing, and without lying about it.

    A row from before provenance existed gets LEGACY_* markers rather than the
    current protocol's values. Inheriting today's protocol_version would silently
    fold old long-resolver rows into the repaired short book, which is the exact
    contamination acceptance H-01/H-02 forbids.
    """
    out = frame.copy()
    for column in FORWARD_COLUMNS:
        if column not in out.columns:
            out[column] = np.nan
    for column, marker in (
        ("protocol_version", LEGACY_PROTOCOL),
        ("strategy_id", LEGACY_STRATEGY),
        ("feature_semantics", LEGACY_SEMANTICS),
    ):
        out[column] = out[column].fillna(marker).replace("", marker)
    # Absent eligibility is not eligibility. Built from a truth test rather than
    # fillna+astype: an object column of NaN downcasts with a FutureWarning, and
    # more to the point the string "False" read back from CSV is truthy under
    # astype(bool) -- which would flip an ineligible row into an actionable one.
    out["execution_eligible"] = [
        str(v).strip().lower() in ("true", "1", "1.0") for v in out["execution_eligible"]
    ]
    return out[list(FORWARD_COLUMNS)]


def rows_for_protocol(frame: pd.DataFrame, protocol_version: str) -> pd.DataFrame:
    """One protocol's rows only. Summaries must never span two (H-01/H-02)."""
    if frame.empty:
        return frame
    return normalize_log(frame).pipe(
        lambda f: f[f["protocol_version"].astype(str) == str(protocol_version)]
    )


def actionable_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Rows an executor may act on: execution-eligible only (H-03).

    Scored-but-ineligible rows stay visible in the log on purpose -- they are
    evidence -- but they are not an order queue.
    """
    if frame.empty:
        return frame
    out = normalize_log(frame)
    return out[out["execution_eligible"]]


def actual_closed_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Only causally filled, closed trades; legacy proxies never qualify."""
    if frame.empty:
        return frame
    out = normalize_log(frame)
    filled = out["entry_status"].astype(str).isin({"paper_filled", "broker_filled"})
    closed = out["status"].astype(str) == "closed"
    pnl = pd.to_numeric(out["actual_realized_ret"], errors="coerce").notna()
    semantics = out["actual_return_semantics"].astype(str).isin(
        {"gross", "net_taker", "net_maker"}
    )
    return out[filled & closed & pnl & semantics]


def open_keys(frame: pd.DataFrame) -> set[ForwardKey]:
    if frame.empty:
        return set()
    active = frame[frame["status"] != "closed"]
    return {row_key(record) for record in active.to_dict("records")}


def row_key(record) -> ForwardKey:
    return forward_key(
        str(record["source"]),
        str(record["symbol"]),
        pd.Timestamp(record["signal_time"]),
        record.get("side"),
        record.get("protocol_version"),
    )


def forward_key(
    source: str,
    symbol: str,
    signal_time: pd.Timestamp,
    side: object = None,
    protocol_version: object = None,
) -> ForwardKey:
    """Identity of one signal event, including which contract produced it.

    side and protocol_version are part of the key (acceptance B-03/B-04): the same
    bar under two protocols is two events, and merging them would let a legacy
    long-resolver row absorb the outcome of a repaired short one. Both default to
    their LEGACY_* marker rather than to a live value, so an old row keeps its own
    identity instead of being adopted by whatever is running now.
    """
    side_s = LEGACY_SIDE if side is None or _blank(side) else str(side).strip().lower()
    proto_s = LEGACY_PROTOCOL if protocol_version is None or _blank(protocol_version) \
        else str(protocol_version).strip()
    return source, symbol, str(signal_time), side_s, proto_s


def _blank(value: object) -> bool:
    if isinstance(value, float) and np.isnan(value):
        return True
    return str(value).strip() == "" or str(value).lower() == "nan"
