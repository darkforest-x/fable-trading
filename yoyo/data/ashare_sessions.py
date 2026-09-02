"""Causal A-share session aggregation primitives.

The source frame must contain completed, close-labelled 60-minute rows with
``raw_close_time``, ``open_time``, OHLC, volume, amount, secid and adjustment.
One output row uses exactly the four same-date source slots 10:30, 11:30,
14:00 and 15:00 Asia/Shanghai.  Its open_time is the first source row's 09:30
open, its raw_close_time/availability is 15:00, OHLC is first/max/min/last,
and volume/amount are sums.  No missing row is filled, no natural-day calendar
is inferred, and no source row later than ``cutoff_close`` is read.

This is a four-*trading-hour* session bar spanning the exchange lunch break,
not a continuous four-hour wall-clock resample.  The function is deliberately
below every business layer and imports no detector or strategy code.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

ASHARE_HOURLY_CLOSE_SLOTS: tuple[str, ...] = (
    "10:30",
    "11:30",
    "14:00",
    "15:00",
)

_REQUIRED_COLUMNS = frozenset(
    {
        "raw_close_time",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "secid",
        "adjustment",
    }
)


class AShareSessionError(ValueError):
    """Raised when an A-share source frame cannot be interpreted causally."""


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "raw_close_time",
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "secid",
            "adjustment",
            "source_rows",
            "session_date",
        ]
    )


def aggregate_complete_session_4h(
    frame: pd.DataFrame,
    *,
    cutoff_close: object,
    close_slots: Sequence[str] = ASHARE_HOURLY_CLOSE_SLOTS,
) -> pd.DataFrame:
    """Return exact complete four-trading-hour session bars through a cutoff."""

    missing = _REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise AShareSessionError(f"source frame missing columns: {sorted(missing)}")
    if tuple(close_slots) != ASHARE_HOURLY_CLOSE_SLOTS:
        raise AShareSessionError("A-share hourly close-slot contract drifted")
    source = frame.copy()
    closes = pd.to_datetime(source["raw_close_time"], utc=True).dt.tz_convert(
        "Asia/Shanghai"
    )
    cutoff = pd.Timestamp(cutoff_close)
    if cutoff.tzinfo is None:
        raise AShareSessionError("cutoff_close must be timezone-aware")
    cutoff = cutoff.tz_convert("Asia/Shanghai")
    source["raw_close_time"] = closes
    source = source[source["raw_close_time"] <= cutoff].copy()
    if source.empty:
        return _empty_result()
    source.sort_values("raw_close_time", inplace=True, ignore_index=True)
    if source["raw_close_time"].duplicated().any():
        raise AShareSessionError("duplicate source close-label")
    source["session_date"] = source["raw_close_time"].dt.date
    output: list[dict[str, Any]] = []
    for session_date, group in source.groupby("session_date", sort=True):
        group = group.sort_values("raw_close_time")
        slots = tuple(group["raw_close_time"].dt.strftime("%H:%M"))
        if len(group) != 4 or slots != ASHARE_HOURLY_CLOSE_SLOTS:
            continue
        first = group.iloc[0]
        last = group.iloc[-1]
        first_open = pd.Timestamp(first["open_time"])
        if first_open.tzinfo is None:
            raise AShareSessionError("source open_time must be timezone-aware")
        if first_open.tz_convert("Asia/Shanghai").strftime("%H:%M") != "09:30":
            raise AShareSessionError("first hourly row does not open at 09:30")
        output.append(
            {
                "raw_close_time": pd.Timestamp(last["raw_close_time"]),
                "open_time": first_open.tz_convert("UTC"),
                "open": float(first["open"]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(last["close"]),
                "volume": float(group["volume"].sum()),
                "amount": float(group["amount"].sum()),
                "secid": str(first["secid"]),
                "adjustment": str(first["adjustment"]),
                "source_rows": 4,
                "session_date": str(session_date),
            }
        )
    if not output:
        return _empty_result()
    result = pd.DataFrame(output)
    result.sort_values("raw_close_time", inplace=True, ignore_index=True)
    return result
