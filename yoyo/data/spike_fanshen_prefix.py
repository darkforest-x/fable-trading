"""Strict pre-May OKX prefix reading for owner-authorized Fanshen research.

Only a timestamp token is read before endpoint approval; OHLCV bytes at or
after the restricted boundary are never parsed. Features use complete source
bars; aggregation uses complete UTC constituents and no price filling.
"""
import csv
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.data.release_eth_prefix import validate_ohlcv


def read_prefix(path, minutes, end):
    """Read timestamp-first OHLCV only where source bar closes by end."""
    end = pd.Timestamp(end)
    if end.tzinfo is None or end > min(pd.Timestamp(HOLDOUT_START), pd.Timestamp("2026-05-01T00:00Z")):
        raise ValueError("research endpoint cannot expose holdout")
    if minutes not in (3, 5, 15):
        raise ValueError("unsupported native source duration")
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    rows = []
    excluded = None
    with path.open("rb") as handle:
        header = handle.readline()
        names = next(csv.reader([header.decode("utf-8-sig").strip()]))
        if names[0] != "ts":
            raise ValueError("timestamp must be first field")
        if not set(["open", "high", "low", "close", "volume"]).issubset(names):
            raise ValueError("missing OHLCV source fields")
        digest.update(header)
        while True:
            token = bytearray()
            while True:
                char = handle.read(1)
                if char in (b"", b","):
                    break
                if char in (b"\n", b"\r") or len(token) >= 24:
                    raise ValueError("malformed timestamp")
                token.extend(char)
            if not token and char == b"":
                break
            if char != b",":
                raise ValueError("truncated timestamp")
            stamp = pd.Timestamp(int(token), unit="ms", tz="UTC")
            if stamp + pd.Timedelta(minutes=minutes) > end:
                excluded = stamp.isoformat()
                break
            suffix = handle.readline()
            raw = bytes(token) + b"," + suffix
            fields = next(csv.reader([raw.decode().strip()]))
            if len(fields) != len(names):
                raise ValueError("source row width changed")
            mapping = dict(zip(names, fields))
            rows.append((stamp, *[float(mapping[k]) for k in ("open", "high", "low", "close", "volume")]))
            digest.update(raw)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("source changed while reading")
    frame = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    if frame.empty:
        raise ValueError("empty permitted prefix")
    validate_ohlcv(frame, minutes)
    frame = frame.set_index("open_time")
    frame.attrs["minutes"] = minutes
    return frame, dict(path=str(path), source_size_metadata_only=before.st_size,
                      rows=len(frame), first_open=str(frame.index[0]),
                      last_close=str(frame.index[-1]+pd.Timedelta(minutes=minutes)),
                      prefix_sha256=digest.hexdigest(), first_excluded_timestamp_only=excluded,
                      holdout_consumed=False, restricted_price_rows_parsed=0, gaps=0, duplicates=0)


def aggregate(frame, source_minutes, target_minutes):
    """Group complete closed UTC buckets; only partial boundary buckets drop."""
    if target_minutes % source_minutes or target_minutes < source_minutes:
        raise ValueError("target must be an integer multiple of source")
    validate_ohlcv(frame.reset_index(), source_minutes)
    groups = frame.resample(f"{target_minutes}min", origin="epoch")
    counts = groups.size()
    complete = counts.eq(target_minutes // source_minutes)
    partial = np.flatnonzero(~complete.to_numpy())
    if any(i not in (0, len(counts)-1) for i in partial):
        raise ValueError("internal incomplete aggregate bucket")
    result = groups.agg(dict(open="first", high="max", low="min", close="last", volume="sum")).loc[complete]
    validate_ohlcv(result.reset_index(), target_minutes)
    result.attrs["minutes"] = target_minutes
    return result, dict(source_minutes=source_minutes, minutes=target_minutes, rows=len(result),
                        discarded_partial_edge_groups=len(partial), internal_incomplete_groups=0)

