"""Bounded data and causal market context for the altseason trend study.

Source: the immutable ``history_development/manifest.json`` from the 2026-09-09
altcoin study. Membership is its original 54-symbol pool, never a later price,
return, completeness or current-rank screen. Owner illustrations remain out.
Only timestamp-first OHLCV prefixes ending by 2026-01-01 are materialized;
the canonical holdout boundary is an additional upper bound. File hashing is
source identity, not a price evaluation. This module never fetches or writes.

Four-hour bars require all sixteen consecutive 15-minute observations. Daily
market context consumes the engine's already-lagged daily EMA booleans, using
only a daily source whose close is no later than the current decision close.
It does not select periods or members from any future price or outcome.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Mapping

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START


ROOT = Path(__file__).resolve().parents[2]
RESEARCH_END = pd.Timestamp("2026-01-01T00:00:00Z")
OHLCV = ("open", "high", "low", "close", "volume")
ILLUSTRATIONS = frozenset(("SOPH", "USELESS"))
RAW_STEP = pd.Timedelta(minutes=15)
BAR_STEP = pd.Timedelta(hours=4)


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("An explicit UTC timestamp is required")
    return stamp.tz_convert("UTC")


def _path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def load_universe(manifest_path: str | Path, *, expected_count: int = 54) -> list[dict]:
    """Read the frozen membership and provenance; do not read price files.

    ``expected_count`` is exposed for synthetic fixtures. Research callers use
    54. A rejected member rejects the pool rather than changing its denominator.
    A later-cutoff manifest is rejected because its tail quality may select the
    earlier universe. The historical builder receipt is preserved verbatim.
    """
    path = Path(manifest_path).resolve()
    payload = path.read_bytes()
    manifest = json.loads(payload)
    if (not isinstance(expected_count, int) or isinstance(expected_count, bool)
            or expected_count < 1):
        raise ValueError("expected_count must be a positive integer")
    if manifest.get("schema_version") not in (1, 2):
        raise ValueError("Unsupported frozen history manifest schema")
    if _utc(manifest["end_exclusive"]) > min(RESEARCH_END, pd.Timestamp(HOLDOUT_START)):
        raise ValueError("Use the independently bounded development manifest")
    rows = manifest.get("symbols")
    if not isinstance(rows, list):
        raise ValueError("Missing frozen history records")
    pool = [row for row in rows if row.get("cohort") == "frozen_pool"]
    symbols = [row.get("symbol") for row in pool]
    if len(pool) != expected_count or len(set(symbols)) != len(symbols):
        raise ValueError("Frozen pool membership count or uniqueness changed")
    if (set(symbols) & ILLUSTRATIONS or any(not isinstance(s, str)
            or re.fullmatch(r"[A-Z0-9]{1,30}", s) is None for s in symbols)):
        raise ValueError("Invalid frozen pool identity or owner illustration")
    if any(row.get("status") != "complete" for row in pool):
        raise ValueError("Frozen pool is incomplete; do not drop members")
    if not all(manifest.get(key) for key in ("builder_path", "builder_sha256", "builder_commit")):
        raise ValueError("Frozen history is missing its builder receipt")
    receipt = dict(manifest_path=str(path), manifest_sha256=hashlib.sha256(payload).hexdigest(),
                   frozen_end_exclusive=manifest["end_exclusive"],
                   history_builder_path=manifest["builder_path"],
                   history_builder_sha256=manifest["builder_sha256"],
                   history_builder_commit=manifest["builder_commit"])
    result = []
    for original in pool:
        row = dict(original)
        if not row.get("output_path") or re.fullmatch(r"[a-f0-9]{64}", str(row.get("output_sha256", ""))) is None:
            raise ValueError("Frozen record is missing its output identity")
        row["output_path"] = str(_path(row["output_path"]))
        row["source_receipt"] = dict(receipt)
        result.append(row)
    return result


def load_bars(record: Mapping, end: object = RESEARCH_END) -> pd.DataFrame:
    """Read a verified 15m prefix, validate it, and return complete 4H OHLCV.

    Only ts, open, high, low, close and volume before the effective cutoff are
    used; optional confirm/open_time are validated there too. The first excluded
    row is inspected only for its timestamp, and its prices are never parsed.
    Requested cutoffs later than research/holdout bounds are clamped. Missing
    intervals raise; partial edge groups are discarded rather than filled.
    """
    requested = _utc(end)
    cutoff = min(requested, RESEARCH_END, pd.Timestamp(HOLDOUT_START))
    if cutoff != cutoff.floor("15min"):
        raise ValueError("Cutoff must be aligned to a closed 15m boundary")
    path = _path(record["output_path"])
    actual_sha = _sha(path)
    if actual_sha != record["output_sha256"]:
        raise ValueError("Frozen history SHA256 changed: " + str(path))
    cutoff_ms = cutoff.value // 1_000_000
    prefix = hashlib.sha256()
    lines = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        header = handle.readline()
        names = next(csv.reader([header]))
        if (not header.startswith("ts,") or len(names) != len(set(names))
                or not {"ts", *OHLCV}.issubset(names)):
            raise ValueError("Expected unique ts-first OHLCV CSV columns")
        prefix.update(header.encode())
        for line in handle:
            stamp = line.partition(",")[0]
            if re.fullmatch(r"[0-9]+", stamp) is None:
                raise ValueError("Invalid source timestamp")
            if int(stamp) >= cutoff_ms:
                break
            lines.append(line)
            prefix.update(line.encode())
    if not lines:
        raise ValueError("No pre-cutoff history")
    raw = pd.read_csv(io.StringIO(header + "".join(lines)), dtype=str, keep_default_na=False)
    if not raw.ts.str.fullmatch(r"[0-9]+").all():
        raise ValueError("Invalid source timestamp")
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.astype("int64"), unit="ms", utc=True))
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Duplicate or nonmonotonic source prefix")
    if not index.equals(index.floor("15min")):
        raise ValueError("Misaligned source timestamp")
    if len(index) > 1 and not np.all(np.diff(index.as_unit("ns").asi8) == RAW_STEP.value):
        raise ValueError("Source gap: explicit separate segments are required")
    if not (index + RAW_STEP <= cutoff).all():
        raise ValueError("Incomplete source bar crosses cutoff")
    if "confirm" in raw and not raw.confirm.eq("1").all():
        raise ValueError("Unconfirmed source bar")
    if "open_time" in raw:
        clocks = pd.DatetimeIndex(pd.to_datetime(raw.open_time, utc=True, errors="raise"))
        if not clocks.equals(index):
            raise ValueError("open_time disagrees with source timestamp")
    values = raw.loc[:, OHLCV].apply(pd.to_numeric, errors="raise").to_numpy(dtype=float)
    o, h, l, c, v = values.T
    if (not np.isfinite(values).all() or np.any(np.minimum.reduce([o, h, l, c]) <= 0)
            or np.any(v < 0) or np.any(h < np.maximum.reduce([o, l, c]))
            or np.any(l > np.minimum.reduce([o, h, c]))):
        raise ValueError("Invalid OHLCV geometry or nonfinite values")
    frame = pd.DataFrame(values, columns=OHLCV, index=index)
    grouped = frame.groupby(frame.index.floor("4h"))
    bars = grouped.agg(dict(open="first", high="max", low="min", close="last", volume="sum"))
    bars = bars.loc[grouped.size().eq(16)]
    if bars.empty:
        raise ValueError("No complete 4H groups")
    bars.attrs.update(minutes=240, period_seconds=14_400, symbol=record.get("symbol"),
                      source_receipt={**dict(record.get("source_receipt", {})),
                          "output_path": str(path), "output_sha256": actual_sha,
                          "prefix_sha256": prefix.hexdigest(),
                          "loader_path": str(Path(__file__).resolve()),
                          "loader_sha256": _sha(Path(__file__)),
                          "requested_end": requested.isoformat(), "effective_end": cutoff.isoformat(),
                          "source_rows": len(frame), "source_start": index[0].isoformat(),
                          "source_end_close": (index[-1] + RAW_STEP).isoformat(),
                          "bar_rows": len(bars), "bar_start": bars.index[0].isoformat(),
                          "bar_end_close": (bars.index[-1] + BAR_STEP).isoformat(),
                          "partial_edge_rows_dropped": len(frame) - len(bars) * 16,
                          "gap_count": 0, "holdout_price_rows_materialized": 0})
    return bars


def freeze_metadata(records: list[dict]) -> dict:
    """Summarize existing receipts only; never infer coverage by reading prices."""
    keys = ("symbol", "cohort", "status", "start", "end_close", "rows",
            "gap_count", "missing_bars", "output_path", "output_sha256", "source_receipt")
    return dict(symbol_count=len(records), altcoin_count=sum(r["symbol"] not in ("BTC", "ETH") for r in records),
                research_end=RESEARCH_END.isoformat(), holdout_start=pd.Timestamp(HOLDOUT_START).isoformat(),
                universe_kind="Frozen convenience/survivor pool; not point-in-time all-market coverage",
                members=[{key: r.get(key) for key in keys} for r in records])


def _feature_bools(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        raise ValueError("Missing daily market context column: " + column)
    values = frame[column]
    if not values.dropna().map(lambda v: isinstance(v, (bool, np.bool_))).all():
        raise ValueError("Market context columns must contain booleans or missing values")
    return values.astype("boolean")


def market_regime(features_by_symbol: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Classify each 4H decision from the last complete daily market context.

    BTC must be ready and above its daily EMA200. Breadth uses ready altcoins
    with a known daily EMA50 comparison, excludes BTC/ETH/owner illustrations,
    and requires at least ten eligible symbols. Strictly more than half must
    be above EMA50. Unknown BTC or insufficient breadth remains pd.NA rather
    than being mislabeled a weak market. No future membership fill is used.
    """
    indexes = []
    validated = {}
    for symbol, frame in features_by_symbol.items():
        index = frame.index
        if (not isinstance(index, pd.DatetimeIndex) or index.tz is None or index.hasnans
                or str(index.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT")
                or not index.is_unique or not index.is_monotonic_increasing
                or not index.equals(index.floor("4h"))):
            raise ValueError("Market features require unique chronological UTC 4H opens")
        ready = _feature_bools(frame, "ready")
        column = "daily_above_ema200" if symbol == "BTC" else "daily_above_ema50"
        above = _feature_bools(frame, column)
        if "daily_source_close_time" not in frame:
            raise ValueError("Daily context requires its source close time")
        source = pd.to_datetime(frame.daily_source_close_time, utc=True, errors="raise")
        known = source.notna()
        if ((source[known] > index[known] + BAR_STEP).any()
                or not (source[known] == source[known].dt.floor("D")).all()):
            raise ValueError("Daily context source is unclosed or not a complete UTC day")
        # Missing/stale source dates cannot silently reuse a much older regime.
        expected = (index + BAR_STEP).floor("D")
        available = known & source.eq(expected)
        validated[symbol] = (ready.fillna(False) & above.notna() & available, above)
        indexes.append(index)
    union = pd.DatetimeIndex([], tz="UTC")
    for index in indexes:
        union = union.union(index)
    union = union.sort_values()
    eligible_count = pd.Series(0, index=union, dtype="int64")
    above_count = pd.Series(0, index=union, dtype="int64")
    for symbol, (eligible, above) in validated.items():
        if symbol in {"BTC", "ETH", *ILLUSTRATIONS}:
            continue
        known = eligible.reindex(union, fill_value=False).astype(bool)
        eligible_count += known.astype(int)
        above_count += (known & above.reindex(union).fillna(False)).astype(int)
    breadth = (above_count / eligible_count.replace(0, np.nan)).astype("Float64")
    btc_up = pd.Series(pd.NA, index=union, dtype="boolean")
    if "BTC" in validated:
        eligible, above = validated["BTC"]
        btc_up = above.where(eligible, pd.NA).reindex(union).astype("boolean")
    strong = pd.Series(pd.NA, index=union, dtype="boolean")
    known = btc_up.notna() & eligible_count.ge(10)
    strong.loc[known] = btc_up.loc[known] & breadth.loc[known].gt(.5)
    result = pd.DataFrame(dict(strong=strong, breadth=breadth, eligible_count=eligible_count, btc_up=btc_up))
    result.attrs.update(period_seconds=14_400, decision_clock="index + 4H", daily_context="last completed UTC day",
                        unknown_policy="Retained as nullable booleans; never classified as weak")
    return result
