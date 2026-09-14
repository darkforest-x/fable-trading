"""Read only timestamp-approved OKX ETH prefixes and complete UTC bar groups.

Source contract: the local OKX CSV begins with epoch-millisecond ``ts``;
OHLCV is parsed only after that timestamp and bar end pass the caller's
exclusive research endpoint and the central holdout boundary. No network,
source writes, forward filling, or fallback venue. This prevents an ordinary
whole-file read followed by filtering from exposing restricted observations.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START


def read_prefix(path: Path, end: str | pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Read approved 15m OHLCV; the first rejected row's prices are not read."""
    end = pd.Timestamp(end)
    if end.tzinfo is None or end > pd.Timestamp(HOLDOUT_START):
        raise ValueError("endpoint must be timezone-aware and no later than holdout")
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    records = []
    rejected_time = None
    with path.open("rb") as stream:
        header = stream.readline()
        names = next(csv.reader([header.decode("utf-8-sig").strip()]))
        if names[0] != "ts" or not set(("open", "high", "low", "close", "volume")) <= set(names):
            raise ValueError("expected timestamp-first OKX ts/OHLCV source")
        digest.update(header)
        while True:
            token = bytearray()
            while True:
                char = stream.read(1)
                if char in (b"", b","):
                    break
                if char in (b"\n", b"\r") or len(token) >= 24:
                    raise ValueError("invalid first timestamp field")
                token.extend(char)
            if not token and char == b"":
                break
            if char != b",":
                raise ValueError("truncated source row")
            stamp = pd.Timestamp(int(token), unit="ms", tz="UTC")
            if stamp + pd.Timedelta(minutes=15) > end or stamp >= pd.Timestamp(HOLDOUT_START):
                rejected_time = stamp.isoformat()
                break
            # Only approved timestamps reach any OHLC/volume bytes.
            suffix = stream.readline()
            row_bytes = bytes(token) + b"," + suffix
            row = next(csv.reader([row_bytes.decode().strip()]))
            if len(row) != len(names):
                raise ValueError("source row width changed")
            mapping = dict(zip(names, row))
            records.append((stamp, *(float(mapping[k]) for k in ("open", "high", "low", "close", "volume"))))
            digest.update(row_bytes)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("source changed during prefix read")
    frame = pd.DataFrame(records, columns=["open_time", "open", "high", "low", "close", "volume"])
    if frame.empty:
        raise ValueError("no permitted rows in source")
    validate_ohlcv(frame, 15)
    return frame, {
        "source_path": str(path.resolve()), "source_size_bytes_metadata_only": before.st_size,
        "source_mtime_ns": before.st_mtime_ns, "consumed_prefix_sha256": digest.hexdigest(),
        "prefix_includes_header": True, "rows": len(frame),
        "first_open": frame.open_time.iloc[0].isoformat(),
        "last_close": (frame.open_time.iloc[-1] + pd.Timedelta(minutes=15)).isoformat(),
        "end_exclusive": end.isoformat(), "first_excluded_timestamp_only": rejected_time,
        "restricted_price_rows_parsed": 0, "holdout_consumed": False,
        "gaps": 0, "duplicates": 0, "venue": "OKX", "instrument": "ETH-USDT-SWAP",
    }


def validate_ohlcv(frame: pd.DataFrame, minutes: int) -> None:
    """Check timestamp grid and geometry without correcting malformed data."""
    t = pd.to_datetime(frame.open_time, utc=True)
    step = pd.Timedelta(minutes=minutes)
    if t.duplicated().any() or not t.is_monotonic_increasing:
        raise ValueError("duplicate or unsorted timestamps")
    if not t.diff().iloc[1:].eq(step).all():
        raise ValueError("source contains missing candle intervals")
    if (t.astype("int64") % step.value != 0).any():
        raise ValueError("candles are not UTC epoch aligned")
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError("nonfinite or invalid OHLCV")
    if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high below another price")
    if (frame.low > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low above another price")


def aggregate(frame: pd.DataFrame, minutes: int) -> tuple[pd.DataFrame, dict]:
    """Use only all constituent15m rows; discard partial edge warmup groups."""
    if minutes not in (15, 60, 240):
        raise ValueError("allowed signal durations are15/60/240 minutes")
    validate_ohlcv(frame, 15)
    grouped = frame.set_index("open_time").resample(f"{minutes}min", origin="epoch")
    counts = grouped.size()
    complete = counts.eq(minutes // 15)
    incomplete = np.flatnonzero(~complete.to_numpy())
    if any(i not in (0, len(counts) - 1) for i in incomplete):
        raise ValueError("internal aggregate has incomplete constituents")
    result = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    result = result.loc[complete].reset_index()
    validate_ohlcv(result, minutes)
    return result, {"minutes": minutes, "rows": len(result), "constituents_per_bar": minutes // 15,
                    "discarded_partial_edge_groups": len(incomplete), "internal_incomplete_groups": 0}
