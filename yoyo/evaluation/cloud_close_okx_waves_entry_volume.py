"""Recover the three missing WAVES 12H base-volume windows from local OKX trades.

The monthly trade exports are named by Asia/Shanghai calendar month. Their
``created_time`` values are UTC milliseconds. One contract is one WAVES, so a
confirmed print contributes ``size`` WAVES to its UTC 12H bar.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = ROOT / "data/research/cloud_close_okx_waves_trades/raw"
H1_REFERENCE = ROOT / "experiments/active/exp-cloud-close-okx-waves-trades-20261109-v1/bars1h.csv.gz"
INSTRUMENT = "WAVES-USDT-SWAP"
CONTRACT_SIZE_WAVES = 1.0
HOUR_MS = 3_600_000
STEP_12H_MS = 12 * HOUR_MS
ALLOWED_KEYS = {1_688_126_400_000, 1_688_558_400_000, 1_689_336_000_000}
TRADE_COLUMNS = ("instrument_name", "trade_id", "price", "size", "created_time")
OHLC = ("open", "high", "low", "close")


def _local_month(ms: int) -> tuple[int, int]:
    stamp = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
    )
    return stamp.year, stamp.month


def _months_between(start_ms: int, end_ms_exclusive: int) -> list[str]:
    first = _local_month(start_ms)
    last = _local_month(end_ms_exclusive - 1)
    year, month = first
    result = []
    while (year, month) <= last:
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


def _trade_archives(start_ms: int, end_ms_exclusive: int) -> pd.DataFrame:
    """Read only existing BJT-month ZIPs overlapping a UTC millisecond span."""
    frames = []
    months = _months_between(start_ms, end_ms_exclusive)
    if not months:
        return pd.DataFrame(columns=list(TRADE_COLUMNS))
    for month in months:
        path = ARCHIVE_ROOT / f"{INSTRUMENT}-trades-{month}.zip"
        if not path.is_file():
            raise FileNotFoundError(f"Required registered WAVES trade archive is missing: {path}")
        with ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"Expected one CSV in {path.name}, found {len(members)}")
            with archive.open(members[0]) as handle:
                for chunk in pd.read_csv(
                    handle,
                    usecols=list(TRADE_COLUMNS),
                    dtype={"instrument_name": "string"},
                    chunksize=500_000,
                ):
                    chunk["trade_id"] = pd.to_numeric(chunk.trade_id, errors="raise").astype("int64")
                    chunk["created_time"] = pd.to_numeric(chunk.created_time, errors="raise").astype("int64")
                    chunk["price"] = pd.to_numeric(chunk.price, errors="raise").astype("float64")
                    chunk["size"] = pd.to_numeric(chunk["size"], errors="raise").astype("float64")
                    chunk = chunk.loc[
                        chunk.created_time.ge(start_ms) & chunk.created_time.lt(end_ms_exclusive)
                    ]
                    if not chunk.empty:
                        chunk["archive_month"] = month
                        frames.append(chunk)
    if not frames:
        return pd.DataFrame(columns=[*TRADE_COLUMNS, "archive_month"])
    trades = pd.concat(frames, ignore_index=True)
    if not trades.instrument_name.astype(str).eq(INSTRUMENT).all():
        raise ValueError("WAVES trade archive contains a different instrument")
    numeric = trades[["trade_id", "created_time", "price", "size"]].to_numpy(dtype="float64")
    if (
        not np.isfinite(numeric).all()
        or trades.trade_id.lt(0).any()
        or trades.created_time.lt(0).any()
        or not trades.price.gt(0).all()
        or not trades["size"].gt(0).all()
        or trades.created_time.mod(HOUR_MS).lt(0).any()
    ):
        raise ValueError("Invalid WAVES trade ID, timestamp, price, or size")

    duplicated = trades.loc[trades.trade_id.duplicated(keep=False)]
    if not duplicated.empty:
        checks = duplicated.assign(hour=duplicated.created_time // HOUR_MS).groupby("trade_id").agg(
            price_n=("price", "nunique"), size_n=("size", "nunique"),
            hour_n=("hour", "nunique"), instrument_n=("instrument_name", "nunique"),
        )
        if checks.gt(1).any(axis=None):
            raise ValueError("Duplicate WAVES trade IDs disagree on instrument, price, size, or UTC hour")
    trades = (trades.sort_values(["created_time", "trade_id"], kind="stable")
              .drop_duplicates("trade_id", keep="first")
              .sort_values("trade_id", kind="stable")
              .reset_index(drop=True))
    if trades.trade_id.duplicated().any():
        raise ValueError("WAVES trade ID de-duplication failed")
    if trades.created_time.diff().dropna().lt(0).any():
        raise ValueError("WAVES trade IDs run backwards in UTC time")
    return trades


def _reference_h1(required_hours: np.ndarray) -> pd.DataFrame:
    if not H1_REFERENCE.is_file():
        raise FileNotFoundError(f"Frozen WAVES H1 price source is missing: {H1_REFERENCE}")
    frame = pd.read_csv(
        H1_REFERENCE,
        usecols=lambda column: column in {"time", *OHLC},
        float_precision="round_trip",
    )
    if not {"time", *OHLC}.issubset(frame.columns):
        raise ValueError("Frozen WAVES H1 source lacks timestamp or OHLC columns")
    frame = frame.set_index("time")
    frame.index = frame.index.astype("int64")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Frozen WAVES H1 source has duplicate or unordered timestamps")
    return frame.reindex(required_hours)


def waves_volume(needed_keys: list[int]) -> pd.DataFrame:
    """Return current and prior-20 base volumes for requested WAVES key opens.

    Every requested key is backed by its current 12H trade window and the
    preceding 20 complete UTC 12H windows. The trade IDs must be consecutive
    within each window and across neighboring required windows, and all 12
    per-window H1 OHLC aggregates must match the frozen WAVES H1 price path.
    The result is indexed by ``key_open_ms`` and contains the requested volume
    ratios plus compact source-validation fields.
    """
    keys = sorted({int(key) for key in needed_keys})
    index = pd.Index([], name="key_open_ms", dtype="int64")
    columns = ["current_base_volume", "prior20_median_base_volume", "relative_volume",
               "trade_count", "first_trade_id", "last_trade_id", "trade_ids_contiguous",
               "h1_price_aligned", "volume_unit"]
    if not keys:
        return pd.DataFrame(index=index, columns=columns)
    if any(key not in ALLOWED_KEYS for key in keys):
        raise ValueError("Only the three verified WAVES event key opens are supported")
    if any(key < 0 or key % STEP_12H_MS for key in keys):
        raise ValueError("WAVES key opens must align to UTC 12H candles")

    required_starts = sorted({
        stamp
        for key in keys
        for stamp in range(key - 20 * STEP_12H_MS, key + STEP_12H_MS, STEP_12H_MS)
    })
    start_ms = required_starts[0]
    end_ms = required_starts[-1] + STEP_12H_MS
    trades = _trade_archives(start_ms, end_ms)
    if trades.empty:
        raise ValueError("No WAVES trades were read for the requested 12H volume windows")

    # Check each monthly export against its Asia/Shanghai filename month. UTC
    # month-boundary prints may therefore be in the adjacent UTC month's ZIP.
    local_months = [
        f"{year:04d}-{month:02d}"
        for year, month in (_local_month(int(stamp)) for stamp in trades.created_time)
    ]
    if not np.array_equal(np.asarray(local_months, dtype=object),
                          trades.archive_month.astype(str).to_numpy(dtype=object)):
        raise ValueError("A WAVES print is stored outside its Asia/Shanghai archive month")

    ids = trades.trade_id.to_numpy(dtype="int64")
    times = trades.created_time.to_numpy(dtype="int64")
    if len(ids) > 1 and not np.equal(np.diff(ids), 1).all():
        raise ValueError("WAVES trade IDs are not continuous across the requested adjacent UTC windows")
    if len(times) > 1 and np.diff(times).min() < 0:
        raise ValueError("WAVES trade IDs are not monotonic in UTC time")

    hour_open = trades.created_time // HOUR_MS * HOUR_MS
    trades = trades.assign(hour_open=hour_open, candle_open=trades.created_time // STEP_12H_MS * STEP_12H_MS)
    hourly = trades.groupby("hour_open", sort=True).agg(
        open=("price", "first"), high=("price", "max"), low=("price", "min"), close=("price", "last"),
    )
    hourly.index = hourly.index.astype("int64")
    hourly.index.name = "time"
    required_hours = np.asarray(sorted({
        stamp + offset
        for stamp in required_starts
        for offset in range(0, STEP_12H_MS, HOUR_MS)
    }), dtype="int64")
    reference = _reference_h1(required_hours)
    candidate = hourly.reindex(required_hours)
    if candidate[list(OHLC)].isna().any().any() or reference[list(OHLC)].isna().any().any():
        raise ValueError("A requested WAVES 12H window lacks one of its 12 H1 trade/reference bars")
    matches = np.isclose(
        candidate[list(OHLC)].to_numpy(dtype="float64"),
        reference[list(OHLC)].to_numpy(dtype="float64"),
        rtol=1e-9,
        atol=1e-13,
    ).all(axis=1)
    if not matches.all():
        bad = required_hours[np.flatnonzero(~matches)]
        raise ValueError(f"WAVES trade-derived H1 OHLC disagrees with frozen H1 at {bad[:5].tolist()}")

    bars = {}
    for stamp, group in trades.groupby("candle_open", sort=True):
        group_ids = group.trade_id.to_numpy(dtype="int64")
        if len(group_ids) == 0 or (len(group_ids) > 1 and not np.equal(np.diff(group_ids), 1).all()):
            raise ValueError(f"WAVES trade IDs are not consecutive within 12H bar {stamp}")
        volume = math.fsum(group["size"].to_numpy(dtype="float64") * CONTRACT_SIZE_WAVES)
        if not math.isfinite(volume) or volume <= 0:
            raise ValueError(f"Invalid WAVES base volume in 12H bar {stamp}")
        bars[int(stamp)] = {
            "base_volume": float(volume),
            "trade_count": int(len(group_ids)),
            "first_trade_id": int(group_ids[0]),
            "last_trade_id": int(group_ids[-1]),
        }

    for left, right in zip(required_starts, required_starts[1:]):
        if right == left + STEP_12H_MS and bars[left]["last_trade_id"] + 1 != bars[right]["first_trade_id"]:
            raise ValueError(f"WAVES trade ID continuity breaks between 12H bars {left} and {right}")

    records = []
    for key in keys:
        window = [key - n * STEP_12H_MS for n in range(20, -1, -1)]
        if any(stamp not in bars for stamp in window):
            missing = [stamp for stamp in window if stamp not in bars]
            raise ValueError(f"WAVES trade archive does not cover full 21-bar window: {missing[:5]}")
        prior = np.asarray([bars[stamp]["base_volume"] for stamp in window[:-1]], dtype="float64")
        median = float(np.median(prior))
        current = float(bars[key]["base_volume"])
        if not math.isfinite(median) or median <= 0 or not math.isfinite(current):
            raise ValueError(f"WAVES key {key} has invalid current or prior-20 base volume")
        records.append({
            "key_open_ms": key,
            "current_base_volume": current,
            "prior20_median_base_volume": median,
            "relative_volume": current / median,
            "trade_count": bars[key]["trade_count"],
            "first_trade_id": bars[key]["first_trade_id"],
            "last_trade_id": bars[key]["last_trade_id"],
            "trade_ids_contiguous": True,
            "h1_price_aligned": True,
            "volume_unit": "WAVES_base_asset",
        })
    return pd.DataFrame.from_records(records).set_index("key_open_ms").reindex(columns=columns)

