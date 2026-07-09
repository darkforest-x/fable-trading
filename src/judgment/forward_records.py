"""Forward-log persistence and idempotent row merging."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.judgment.forward_types import FORWARD_COLUMNS, OUTCOME_COLUMNS, ForwardRecord, MergeResult


def read_forward_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=FORWARD_COLUMNS)
    return normalize_log(pd.read_csv(path))


def write_forward_log(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


def merge_forward_log(existing: pd.DataFrame, new_records: list[ForwardRecord]) -> MergeResult:
    current = normalize_log(existing)
    rows = {}
    for record in current.to_dict("records"):
        rows[row_key(record)] = record
    new_signals = 0
    closed_updates = 0
    for record in new_records:
        key = forward_key(record["source"], record["symbol"], pd.Timestamp(record["signal_time"]))
        previous = rows.get(key)
        if previous is None:
            rows[key] = record
            new_signals += 1
            continue
        if str(previous["status"]) != "closed" and record["status"] == "closed":
            merged = dict(previous)
            for column in OUTCOME_COLUMNS:
                merged[column] = record[column]
            rows[key] = merged
            closed_updates += 1
    if not rows:
        return MergeResult(pd.DataFrame(columns=FORWARD_COLUMNS), new_signals, closed_updates)
    frame = pd.DataFrame(rows.values())
    frame = normalize_log(frame).sort_values(["signal_time", "symbol"]).reset_index(drop=True)
    return MergeResult(frame, new_signals, closed_updates)


def normalize_log(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in FORWARD_COLUMNS:
        if column not in out.columns:
            out[column] = np.nan
    return out[list(FORWARD_COLUMNS)]


def open_keys(frame: pd.DataFrame) -> set[tuple[str, str, str]]:
    if frame.empty:
        return set()
    active = frame[frame["status"] != "closed"]
    return {row_key(record) for record in active.to_dict("records")}


def row_key(record) -> tuple[str, str, str]:
    return forward_key(str(record["source"]), str(record["symbol"]), pd.Timestamp(record["signal_time"]))


def forward_key(source: str, symbol: str, signal_time: pd.Timestamp) -> tuple[str, str, str]:
    return source, symbol, str(signal_time)
