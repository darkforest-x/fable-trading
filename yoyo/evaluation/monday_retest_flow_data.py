"""Load frozen Binance minute bars with taker-flow fields for Monday studies.

``load_series`` returns float64 columns in this order:
``TS, OPEN, HIGH, LOW, CLOSE, VOL, COUNT, BUY``. ``TS`` is Unix
milliseconds at the bar open; ``BUY`` is Binance taker-buy base volume.

Raw monthly archives are preferred. Missing archive months are filled from
the original job's same-venue OHLCV series, with unknown ``COUNT`` and ``BUY``
left as NaN. If raw rows contain duplicates/conflicts or do not match the
frozen done receipt's row count and endpoints, the original series supplies
the canonical price timeline and raw flow is joined only on identical
timestamp and OHLCV. Gaps in that original timeline are retained.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

TS, OPEN, HIGH, LOW, CLOSE, VOL, COUNT, BUY = range(8)
BAR_COLUMNS = ("TS", "OPEN", "HIGH", "LOW", "CLOSE", "VOL", "COUNT", "BUY")
PRICE_COLUMNS = slice(OPEN, VOL + 1)
EMPTY = np.empty((0, len(BAR_COLUMNS)), dtype=np.float64)
_SERIES_COLUMNS = ("open", "high", "low", "close", "volume")
_CHUNK_ROWS = 250_000


def _utc_ms(value: object, label: str) -> int:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid expected {label}: {value!r}") from exc
    if pd.isna(stamp):
        raise ValueError(f"Invalid expected {label}: {value!r}")
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.value // 1_000_000


def _timeframe(job: dict) -> tuple[int, str]:
    minutes = int(job.get("execution_minutes", 0))
    if minutes not in (1, 5):
        raise ValueError(f"Unsupported execution_minutes: {minutes}")
    job_id = str(job.get("job_id", ""))
    suffix = f"-{minutes}m"
    if job_id and not job_id.endswith(suffix):
        raise ValueError(f"Job interval disagrees with job_id: {job_id}")
    return minutes, f"{minutes}m"


def _source_roots(job: dict, symbol: str) -> tuple[list[Path], list[Path]]:
    """Return data roots and readable merged-series paths from the frozen job."""
    raw_paths = job.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError(f"{symbol}: job.paths is empty")

    roots: list[Path] = []
    series_paths: list[Path] = []
    seen_roots: set[str] = set()
    seen_series: set[str] = set()
    for value in raw_paths:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if path.parent.name != "series":
            raise ValueError(f"{symbol}: source path is not under a series directory: {path}")
        root = path.parent.parent
        root_key = str(root.resolve())
        if root_key not in seen_roots:
            roots.append(root)
            seen_roots.add(root_key)
        series_key = str(path.resolve())
        if series_key not in seen_series:
            series_paths.append(path)
            seen_series.add(series_key)
    return roots, series_paths


def _months(start_ms: int, end_ms: int) -> list[str]:
    start = pd.Timestamp(start_ms, unit="ms", tz="UTC").tz_localize(None).to_period("M")
    end = pd.Timestamp(end_ms, unit="ms", tz="UTC").tz_localize(None).to_period("M")
    return [str(month) for month in pd.period_range(start, end, freq="M")]


def _csv_to_bars(stream: BytesIO, *, header: bool) -> np.ndarray:
    stream.seek(0)
    try:
        frame = pd.read_csv(
            stream,
            header=None,
            skiprows=int(header),
            usecols=[0, 1, 2, 3, 4, 5, 8, 9],
            dtype="float64",
            float_precision="round_trip",
        )
    except (ValueError, pd.errors.ParserError) as exc:
        raise ValueError("Monthly Binance kline has an unexpected CSV schema") from exc
    bars = frame.to_numpy(dtype=np.float64, copy=True)
    if len(bars) and bars[0, TS] > 1e14:
        bars[:, TS] //= 1_000
    return bars


def _read_month(path: Path, month: str, step_ms: int) -> np.ndarray:
    """Read one monthly archive through a single external-file read."""
    payload = path.read_bytes()
    try:
        with ZipFile(BytesIO(payload)) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            csv_members = [name for name in members if name.lower().endswith(".csv")]
            if len(csv_members) != 1:
                raise ValueError(f"{path}: expected one CSV member, found {len(csv_members)}")
            raw = BytesIO(archive.read(csv_members[0]))
    except BadZipFile as exc:
        raise ValueError(f"Invalid Binance monthly archive: {path}") from exc

    first = raw.readline().split(b",", 1)[0].strip()
    try:
        float(first)
        has_header = False
    except ValueError:
        has_header = True
    bars = _csv_to_bars(raw, header=has_header)
    if not len(bars):
        return EMPTY.copy()
    bars = _sanitize_raw_flow(bars)
    _validate_bars(bars, step_ms, f"{path}", allow_unknown_flow=True)
    month_start = int(pd.Timestamp(f"{month}-01", tz="UTC").value // 1_000_000)
    next_month_start = int((pd.Timestamp(f"{month}-01", tz="UTC") + pd.offsets.MonthBegin(1)).value // 1_000_000)
    if ((bars[:, TS] < month_start) | (bars[:, TS] >= next_month_start)).any():
        raise ValueError(f"{path}: archive contains a bar outside its named month")
    return bars


def _sanitize_raw_flow(bars: np.ndarray) -> np.ndarray:
    """Keep valid prices and invalidate both flow values on anomalous rows.

    Flow anomalies are isolated from OHLCV: do not clamp taker-buy volume or
    infer corrected counts. A bad or missing count/buy pair becomes NaN.
    """
    out = bars.copy()
    flow = out[:, COUNT:BUY + 1]
    count = out[:, COUNT]
    buy = out[:, BUY]
    volume = out[:, VOL]
    known = np.isfinite(count) & np.isfinite(buy)
    # Use exactly the original archive admission checks. Previously accepted
    # archives remain identical; only rows that formerly rejected a whole
    # archive switch to missing flow. Never change prices or invent a delta.
    tolerance = np.maximum(1e-12, volume * 1e-12)
    valid = known & (count >= 0) & (buy >= 0) & (buy - volume <= tolerance)
    flow[~valid] = np.nan
    return out


def _validate_bars(bars: np.ndarray, step_ms: int, label: str,
                   *, allow_unknown_flow: bool) -> None:
    if bars.ndim != 2 or bars.shape[1] != len(BAR_COLUMNS):
        raise ValueError(f"{label}: expected Nx8 bars, got {bars.shape}")
    if not len(bars):
        return
    ts = bars[:, TS]
    if not np.isfinite(ts).all() or not np.equal(ts, np.floor(ts)).all():
        raise ValueError(f"{label}: bar timestamps must be integer milliseconds")
    if (ts % step_ms != 0).any():
        raise ValueError(f"{label}: bar timestamp is off the {step_ms}ms grid")
    core = bars[:, OPEN:VOL + 1]
    if not np.isfinite(core).all() or (core[:, :4] <= 0).any() or (core[:, 4] < 0).any():
        raise ValueError(f"{label}: invalid or missing OHLCV")
    if (bars[:, HIGH] < np.maximum.reduce((bars[:, OPEN], bars[:, LOW], bars[:, CLOSE]))).any():
        raise ValueError(f"{label}: high is below an OHLC value")
    if (bars[:, LOW] > np.minimum.reduce((bars[:, OPEN], bars[:, HIGH], bars[:, CLOSE]))).any():
        raise ValueError(f"{label}: low is above an OHLC value")
    flow = bars[:, COUNT:BUY + 1]
    if not allow_unknown_flow and not np.isfinite(flow).all():
        raise ValueError(f"{label}: raw flow fields contain non-finite values")
    known = np.isfinite(flow).all(axis=1)
    if (bars[known, COUNT] < 0).any() or (bars[known, BUY] < 0).any():
        raise ValueError(f"{label}: negative trade count or taker-buy volume")
    tolerance = np.maximum(1e-12, bars[known, VOL] * 1e-12)
    if (bars[known, BUY] - bars[known, VOL] > tolerance).any():
        raise ValueError(f"{label}: taker-buy volume exceeds total volume")
    if allow_unknown_flow and (np.isinf(flow).any()):
        raise ValueError(f"{label}: flow fields may be finite or NaN, not infinite")


def _series_bounds(path: Path, symbol: str) -> tuple[int, int] | None:
    """Get cheap source bounds from its registered audit or plain CSV edges."""
    root = path.parent.parent
    audit = root / "audits" / f"{symbol}.json"
    if audit.is_file():
        try:
            metadata = json.loads(audit.read_text(encoding="utf-8"))
            first = metadata.get("first_time")
            last = metadata.get("last_time")
            if first is None and metadata.get("first_ms") is not None:
                first = pd.Timestamp(int(metadata["first_ms"]), unit="ms", tz="UTC")
            if last is None and metadata.get("last_ms") is not None:
                last = pd.Timestamp(int(metadata["last_ms"]), unit="ms", tz="UTC")
            if first is not None and last is not None:
                return _utc_ms(first, "source first"), _utc_ms(last, "source last")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A range hint only controls whether a source file can be skipped.
            # If it cannot be parsed, fall through to a local CSV edge check.
            pass

    if path.suffix == ".gz" or path.suffix == ".zip" or not path.is_file():
        return None

    def parse_timestamp(line: bytes) -> int | None:
        if not line.strip():
            return None
        try:
            stamp = int(float(line.split(b",", 1)[0].strip()))
        except (ValueError, OverflowError):
            return None
        return stamp // 1_000 if stamp > 1e14 else stamp

    try:
        with path.open("rb") as handle:
            first_ts = None
            for line in handle:
                first_ts = parse_timestamp(line)
                if first_ts is not None:
                    break
            size = path.stat().st_size
            offset = max(0, size - 65_536)
            handle.seek(offset)
            if offset:
                handle.readline()  # discard a possibly partial CSV row
            tail = handle.read()
        last_ts = None
        for line in reversed(tail.splitlines()):
            last_ts = parse_timestamp(line)
            if last_ts is not None:
                break
    except OSError:
        return None
    if first_ts is None or last_ts is None:
        return None
    return first_ts, last_ts


def _overlaps_ranges(bounds: tuple[int, int] | None,
                     ranges: list[tuple[int, int]]) -> bool:
    if bounds is None:
        return True
    first, last = bounds
    return any(last >= start and first <= end for start, end in ranges)


def _series_ranges(paths: list[Path], ranges: list[tuple[int, int]],
                   symbol: str) -> np.ndarray:
    """Read only requested time spans from original job series, in chunks."""
    ranges = [(int(start), int(end)) for start, end in ranges if end >= start]
    if not ranges:
        return EMPTY.copy()
    chunks_out: list[np.ndarray] = []
    for path in paths:
        if not path.is_file():
            continue
        if not _overlaps_ranges(_series_bounds(path, symbol), ranges):
            continue
        try:
            header = pd.read_csv(path, nrows=0, compression="infer")
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            raise ValueError(f"Cannot read frozen source header: {path}") from exc
        columns = set(header.columns)
        ts_name = "ts" if "ts" in columns else "open_time" if "open_time" in columns else None
        if ts_name is None or not set(_SERIES_COLUMNS).issubset(columns):
            raise ValueError(f"{path}: expected ts/open_time and OHLCV columns")
        try:
            reader = pd.read_csv(
                path,
                usecols=[ts_name, *_SERIES_COLUMNS],
                chunksize=_CHUNK_ROWS,
                compression="infer",
                float_precision="round_trip",
            )
            for frame in reader:
                stamps = pd.to_numeric(frame[ts_name], errors="raise").to_numpy(dtype=np.float64)
                if len(stamps) and stamps[0] > 1e14:
                    stamps //= 1_000
                keep = np.zeros(len(frame), dtype=bool)
                for start_ms, end_ms in ranges:
                    keep |= (stamps >= start_ms) & (stamps <= end_ms)
                if not keep.any():
                    continue
                price = frame.loc[keep, list(_SERIES_COLUMNS)].to_numpy(dtype=np.float64, copy=True)
                count_buy = np.full((len(price), 2), np.nan, dtype=np.float64)
                chunks_out.append(np.column_stack((stamps[keep], price, count_buy)))
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            raise ValueError(f"Cannot read frozen source series: {path}") from exc
    return np.concatenate(chunks_out, axis=0) if chunks_out else EMPTY.copy()


def _canonical_series(bars: np.ndarray, expected: dict, step_ms: int,
                      symbol: str) -> np.ndarray:
    """Validate and explicitly collapse only exact OHLCV source overlaps."""
    if not len(bars):
        raise ValueError(f"{symbol}: frozen source series is empty")
    bars = bars[np.argsort(bars[:, TS], kind="stable")]
    unique_rows: list[np.ndarray] = []
    i = 0
    while i < len(bars):
        j = i + 1
        while j < len(bars) and bars[j, TS] == bars[i, TS]:
            j += 1
        group = bars[i:j]
        if len(group) > 1 and not np.equal(group[:, OPEN:VOL + 1], group[0, OPEN:VOL + 1]).all():
            raise ValueError(f"{symbol}: conflicting OHLCV in original source overlap at {int(group[0, TS])}")
        unique_rows.append(group[0])
        i = j
    canonical = np.vstack(unique_rows)
    _validate_bars(canonical, step_ms, f"{symbol} original series", allow_unknown_flow=True)
    first = _utc_ms(expected["first"], "first")
    last = _utc_ms(expected["last"], "last")
    rows = int(expected["rows"])
    if (len(canonical) != rows or int(canonical[0, TS]) != first or int(canonical[-1, TS]) != last):
        raise ValueError(
            f"{symbol}: original series does not match done receipt "
            f"(got rows={len(canonical)} {int(canonical[0, TS])}..{int(canonical[-1, TS])}; "
            f"expected rows={rows} {first}..{last})"
        )
    return canonical


def _join_raw_flow(series: np.ndarray, raw: np.ndarray) -> np.ndarray:
    """Join raw count/buy only when timestamp and full OHLCV agree exactly."""
    out = series.copy()
    out[:, COUNT:BUY + 1] = np.nan
    if not len(raw):
        return out
    raw = raw[np.argsort(raw[:, TS], kind="stable")]
    unique_ts, starts, counts = np.unique(raw[:, TS], return_index=True, return_counts=True)
    locations = np.searchsorted(unique_ts, series[:, TS])
    valid = locations < len(unique_ts)
    found = np.zeros(len(series), dtype=bool)
    found[valid] = unique_ts[locations[valid]] == series[valid, TS]

    # Handle the overwhelmingly common unique raw timestamp path in bulk.
    row_indices = np.flatnonzero(found)
    group_indices = locations[row_indices]
    single = counts[group_indices] == 1
    single_rows = row_indices[single]
    if len(single_rows):
        raw_rows = starts[locations[single_rows]]
        same_price = np.equal(
            raw[raw_rows, OPEN:VOL + 1],
            series[single_rows, OPEN:VOL + 1],
        ).all(axis=1)
        known_flow = np.isfinite(raw[raw_rows, COUNT:BUY + 1]).all(axis=1)
        accepted = same_price & known_flow
        out[single_rows[accepted], COUNT:BUY + 1] = raw[raw_rows[accepted], COUNT:BUY + 1]

    # Only repeated timestamps need row-wise reconciliation.
    repeated_rows = row_indices[~single]
    for row_i in repeated_rows:
        group_i = locations[row_i]
        group = raw[starts[group_i]:starts[group_i] + counts[group_i]]
        same_price = np.equal(group[:, OPEN:VOL + 1], series[row_i, OPEN:VOL + 1]).all(axis=1)
        matching = group[same_price]
        if not len(matching) or not np.isfinite(matching[:, COUNT:BUY + 1]).all():
            continue
        if np.equal(matching[:, COUNT:BUY + 1], matching[0, COUNT:BUY + 1]).all():
            out[row_i, COUNT:BUY + 1] = matching[0, COUNT:BUY + 1]
    return out


def _drop_exact_rows(bars: np.ndarray) -> np.ndarray:
    """Drop only byte-identical rows, including rows with the same NaNs."""
    if len(bars) < 2:
        return bars
    contiguous = np.ascontiguousarray(bars, dtype=np.float64)
    row_dtype = np.dtype((np.void, contiguous.dtype.itemsize * contiguous.shape[1]))
    _, first_indices = np.unique(contiguous.view(row_dtype).ravel(), return_index=True)
    return contiguous[np.sort(first_indices)]


def load_series(symbol: str, job: dict, expected: dict) -> np.ndarray:
    """Load ``symbol`` from its frozen job using monthly archives and old OHLCV.

    Args:
        symbol: Instrument key, e.g. ``BTCUSDT``.
        job: ``identity.json["job"]`` from the frozen Nautilus source ledger.
        expected: Its ``done.json`` receipt, used to pin interval coverage.

    Returns:
        Chronologically sorted float64 array with columns ``TS, OPEN, HIGH,
        LOW, CLOSE, VOL, COUNT, BUY``. Missing/unverifiable flow fields are
        NaN; missing bars are not synthesized.
    """
    if str(job.get("symbol", "")) != symbol:
        raise ValueError(f"Job symbol {job.get('symbol')!r} does not match {symbol!r}")
    minutes, interval = _timeframe(job)
    try:
        first = _utc_ms(expected["first"], "first")
        last = _utc_ms(expected["last"], "last")
        expected_rows = int(expected["rows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{symbol}: done receipt lacks valid first/last/rows") from exc
    if expected_rows < 1 or last < first:
        raise ValueError(f"{symbol}: invalid done receipt range or row count")
    step_ms = minutes * 60_000
    if first % step_ms or last % step_ms:
        raise ValueError(f"{symbol}: done receipt endpoints are off the {interval} grid")

    roots, series_paths = _source_roots(job, symbol)
    raw_parts: list[np.ndarray] = []
    missing_month_ranges: list[tuple[int, int]] = []
    for month in _months(first, last):
        candidates = [
            root / "downloads" / symbol / f"{symbol}-{interval}-{month}.zip"
            for root in roots
        ]
        existing = [path for path in candidates if path.is_file()]
        month_rows: list[np.ndarray] = []
        for path in existing:
            bars = _read_month(path, month, step_ms)
            keep = (bars[:, TS] >= first) & (bars[:, TS] <= last)
            if keep.any():
                month_rows.append(bars[keep])
        if month_rows:
            raw_parts.extend(month_rows)
        else:
            month_start = int(pd.Timestamp(f"{month}-01", tz="UTC").value // 1_000_000)
            next_start = int((pd.Timestamp(f"{month}-01", tz="UTC") + pd.offsets.MonthBegin(1)).value // 1_000_000)
            missing_month_ranges.append((max(first, month_start), min(last, next_start - step_ms)))

    if missing_month_ranges:
        fallback = _series_ranges(series_paths, missing_month_ranges, symbol)
        if len(fallback):
            raw_parts.append(fallback)

    raw = np.concatenate(raw_parts, axis=0) if raw_parts else EMPTY.copy()
    if len(raw):
        raw = raw[np.argsort(raw[:, TS], kind="stable")]
        in_range = (raw[:, TS] >= first) & (raw[:, TS] <= last)
        raw = _drop_exact_rows(raw[in_range])
        _validate_bars(raw, step_ms, f"{symbol} monthly/fallback source", allow_unknown_flow=True)

    has_duplicates = len(raw) > 1 and bool((np.diff(raw[:, TS]) == 0).any())
    matches_receipt = (
        len(raw) == expected_rows
        and len(raw) > 0
        and int(raw[0, TS]) == first
        and int(raw[-1, TS]) == last
    )
    if matches_receipt and not has_duplicates:
        return raw.astype(np.float64, copy=False)

    # The archive timeline diverges from the historical source's frozen receipt.
    # Rebuild canonical prices from only this job's paths, then join flow on
    # timestamp + OHLCV equality. This path is deliberately uncommon.
    baseline_rows = _series_ranges(series_paths, [(first, last)], symbol)
    baseline = _canonical_series(baseline_rows, expected, step_ms, symbol)
    return _join_raw_flow(baseline, raw)


__all__ = ["BAR_COLUMNS", "TS", "OPEN", "HIGH", "LOW", "CLOSE", "VOL", "COUNT", "BUY", "load_series"]
