"""Fixed-wait and fixed-horizon outcomes for Monday breadth initial members.

Inputs are the frozen initial lower-touch membership and causal signal table
from ``exp-monday-breadth-optimization-20261108-v1/features``. Entries occur
at the 5m open at ``decision = known + wait_min``; timed exits occur at the
5m open exactly ``hold_hours`` later. Outcome paths use observed native
Binance USD-M 5m OHLC only. No candle is synthesized and missing path bars are
left unavailable.

The public ``load_full_ohlc`` API reads the same registered source lineage as
the earlier Monday breadth quote loader while retaining real lows. Its optional
REST repair is scoped to explicit ``needed_intervals`` and stores complete raw
OHLC in this study's data/research cache. It never borrows or infers a low from
close/high.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_constituent_prices as quote_sources


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
FEATURES = EXPERIMENT / "features"
PAYOFFS = EXPERIMENT / "payoffs"
RESEARCH = ROOT / "data/research/monday_breadth_optimization_20261108_v1/payoffs"
MEMBERS_PATH = FEATURES / "members.csv.gz"
SIGNALS_PATH = FEATURES / "signals.csv.gz"

MINUTES = (5, 10, 15, 30, 60, 240, 720)
WAIT_MINUTES = (0, 5, 10, 15, 20, 30, 45, 60, 90, 120)
HOLD_HOURS = (6, 12, 24, 48, 72, 144)
ASSETS = ("BTC", "ETH", "SOL")
BAR = pd.Timedelta(minutes=5)
BAR_MS = 300_000
TAKER_FEE = 0.0005
MAX_REST_PAGES_PER_GAP = 12
REST_URL = "https://fapi.binance.com/fapi/v1/klines"
REST_TIMEOUT_SECONDS = 20
REST_PAGE_LIMIT = 1000
REST_MIN_INTERVAL_SECONDS = 0.25
PARQUET_PART_ROWS = 100_000
RETURN_BATCH = 16_000
SIGNAL_FEATURE_COLUMNS = (
    "initial",
    "band",
    "new_coins",
    "first_half_new",
    "last_half_new",
    "monitor_fraction",
    "market_share",
    "market_share_status",
    "last_touch_age_min",
    "since_prev_lower_touch_min",
    "continued",
    "cooldown",
    "alarm",
)

_rest_gate = threading.Lock()
_last_rest_start = 0.0


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _ms(value: Any) -> int:
    return int(_utc(value).value // 1_000_000)


def _read_source_full_ohlc(path: Path, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, int, bool]:
    """Stream a registered 5m source and preserve low only when source has it."""
    header = pd.read_csv(path, compression="infer", nrows=0).columns
    required = {"ts", "open", "high", "close"}
    if not required.issubset(header):
        return _empty_ohlc(), 0, False
    has_low = "low" in header
    usecols = ["ts", "open", "high", "close"] + (["low"] if has_low else [])
    dtypes = {"ts": "int64", "open": "float64", "high": "float64", "close": "float64"}
    if has_low:
        dtypes["low"] = "float64"
    parts: list[pd.DataFrame] = []
    invalid_alignment = 0
    with pd.read_csv(path, compression="infer", usecols=usecols, dtype=dtypes, chunksize=100_000) as reader:
        for chunk in reader:
            if chunk.empty:
                continue
            first, last = int(chunk.ts.iloc[0]), int(chunk.ts.iloc[-1])
            if last < start_ms:
                continue
            if first > end_ms:
                break
            aligned = chunk.ts.mod(BAR_MS).eq(0)
            invalid_alignment += int((~aligned).sum())
            chunk = chunk.loc[aligned & chunk.ts.between(start_ms, end_ms)].copy()
            if chunk.empty:
                continue
            idx = pd.to_datetime(chunk.pop("ts"), unit="ms", utc=True)
            chunk.index = pd.DatetimeIndex(idx, name="timestamp")
            if not has_low:
                chunk["low"] = np.nan
            parts.append(chunk.loc[:, ["open", "high", "low", "close"]])
    if not parts:
        return _empty_ohlc(), invalid_alignment, has_low
    return pd.concat(parts).sort_index(kind="stable"), invalid_alignment, has_low


def _empty_ohlc() -> pd.DataFrame:
    return pd.DataFrame(
        {name: pd.Series(dtype="float64") for name in ("open", "high", "low", "close")},
        index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
    )


def _normalise_intervals(
    start: pd.Timestamp,
    end: pd.Timestamp,
    intervals: Sequence[Sequence[Any]] | None,
) -> list[tuple[int, int]]:
    if not intervals:
        return []
    left_bound, right_bound = _ms(start), _ms(end)
    windows: list[tuple[int, int]] = []
    for raw in intervals:
        if len(raw) != 2:
            raise ValueError(f"needed interval must have two bounds: {raw!r}")
        left, right = max(left_bound, _ms(raw[0])), min(right_bound, _ms(raw[1]))
        if right < left:
            continue
        first = ((left + BAR_MS - 1) // BAR_MS) * BAR_MS
        last = (right // BAR_MS) * BAR_MS
        if first <= last:
            windows.append((first, last))
    windows.sort()
    merged: list[list[int]] = []
    for left, right in windows:
        if merged and left <= merged[-1][1] + BAR_MS:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return [(left, right) for left, right in merged]


def _cache_paths(symbol: str, start_ms: int, end_ms: int) -> list[Path]:
    root = RESEARCH / "ohlc_rest" / symbol.upper()
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in root.glob("*.csv.gz"):
        try:
            left, right = (int(part) for part in path.name.removesuffix(".csv.gz").split("_", 1))
        except (TypeError, ValueError):
            continue
        if right >= start_ms and left <= end_ms:
            found.append(path)
    return sorted(found)


def _merge_full_sources(
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    candidates: Sequence[tuple[str, Path, int, Sequence[str]]],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    receipts: list[dict[str, Any]] = []
    for kind, path, priority, aliases in candidates:
        if not path.is_file():
            continue
        bounds = quote_sources._source_bounds(kind, path, symbol)
        if bounds is not None and (bounds[1] < start_ms or bounds[0] > end_ms):
            receipts.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "aliases": list(aliases),
                    "priority": int(priority),
                    "status": "outside_requested_range",
                }
            )
            continue
        try:
            frame, bad_alignment, has_low = _read_source_full_ohlc(path, start_ms, end_ms)
            if not frame.empty:
                frame["__priority"] = int(priority)
                frame["__source"] = str(path)
                frames.append(frame)
            stat = path.stat()
            receipts.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "resolved_path": str(path.resolve()),
                    "aliases": list(aliases),
                    "priority": int(priority),
                    "has_low": bool(has_low),
                    "rows": int(len(frame)),
                    "invalid_timestamp_alignment": int(bad_alignment),
                    "bytes": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
        except (OSError, ValueError, KeyError, pd.errors.ParserError, EOFError) as exc:
            receipts.append({"kind": kind, "path": str(path), "status": "read_error", "error": f"{type(exc).__name__}: {exc}"[:400]})
    if not frames:
        return _empty_ohlc(), receipts
    combined = pd.concat(frames).sort_index(kind="stable")
    # Match the established quote-reader priority. If its winning source dropped
    # low, reuse a lower-priority low only when all shared OHLC values agree.
    ordered = combined.sort_values("__priority", kind="stable")
    winner = ordered.loc[~ordered.index.duplicated(keep="last")].copy()
    missing_low = winner.low.isna()
    # Select the highest-priority available low once per timestamp, then match
    # shared OHLC columns vectorially. Avoid a timestamp-by-timestamp scan over
    # the full history for symbols with a large REST seam.
    low_options = ordered.loc[ordered.low.notna(), ["open", "high", "low", "close", "__priority"]]
    if not low_options.empty:
        low_options = low_options.sort_values("__priority", kind="stable")
        low_options = low_options.loc[~low_options.index.duplicated(keep="last")]
        alternatives = low_options.reindex(winner.index)
        shared = ["open", "high", "close"]
        equal = np.isclose(
            winner[shared].to_numpy(dtype=float),
            alternatives[shared].to_numpy(dtype=float),
            rtol=1e-12,
            atol=1e-12,
            equal_nan=False,
        ).all(axis=1)
        recover = missing_low.to_numpy() & alternatives.low.notna().to_numpy() & equal
        winner.loc[recover, "low"] = alternatives.loc[recover, "low"].to_numpy(dtype=float)
        low_recovered = int(recover.sum())
    else:
        low_recovered = 0
    winner = winner.loc[:, ["open", "high", "low", "close"]].sort_index()
    winner.index = pd.DatetimeIndex(winner.index, tz="UTC", name="timestamp")
    # Invalid low remains missing; invalid OHLC rows remain visible as NaN.
    values = winner[["open", "high", "close"]].to_numpy(dtype=float)
    body_ok = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
    body_ok &= winner.high.to_numpy() >= np.maximum(winner.open.to_numpy(), winner.close.to_numpy())
    for column in ("open", "high", "close"):
        winner.loc[~body_ok, column] = np.nan
    low = winner.low.to_numpy(dtype=float)
    low_ok = np.isfinite(low) & (low > 0) & (low <= np.minimum(winner.open.to_numpy(), winner.close.to_numpy()))
    low_ok &= low <= winner.high.to_numpy()
    winner.loc[~low_ok, "low"] = np.nan
    receipts.append({"low_recovered_from_matching_source": int(low_recovered)})
    return winner, receipts


def _pace_rest() -> None:
    global _last_rest_start
    with _rest_gate:
        delay = _last_rest_start + REST_MIN_INTERVAL_SECONDS - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last_rest_start = time.monotonic()


def _fetch_rest_full(symbol: str, left_ms: int, right_ms: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch complete public USD-M OHLC rows for one bounded missing run."""
    cursor = int(left_ms)
    rows: list[tuple[int, float, float, float, float]] = []
    pages: list[dict[str, Any]] = []
    stop = "range_complete"
    for page_no in range(MAX_REST_PAGES_PER_GAP):
        if cursor > right_ms:
            break
        query = urllib.parse.urlencode(
            {"symbol": symbol.upper(), "interval": "5m", "startTime": cursor, "endTime": int(right_ms), "limit": REST_PAGE_LIMIT}
        )
        request = urllib.request.Request(
            f"{REST_URL}?{query}",
            headers={"User-Agent": "fable-trading-monday-breadth-payoffs/1.0", "Accept": "application/json"},
        )
        payload: Any = None
        error: str | None = None
        for attempt in range(3):
            _pace_rest()
            try:
                with urllib.request.urlopen(request, timeout=REST_TIMEOUT_SECONDS) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                error = None
                break
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                error = f"HTTP {exc.code}: {body or exc.reason}"
                if exc.code not in (418, 429, 500, 502, 503, 504) or attempt == 2:
                    break
                time.sleep(1 + attempt)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                if attempt == 2:
                    break
                time.sleep(0.5 * (attempt + 1))
        if error:
            pages.append({"start_ms": cursor, "error": error})
            stop = "request_error"
            break
        if not isinstance(payload, list):
            pages.append({"start_ms": cursor, "error": f"unexpected response: {str(payload)[:300]}"})
            stop = "unexpected_response"
            break
        valid: list[tuple[int, float, float, float, float]] = []
        for candle in payload:
            try:
                stamp = int(candle[0])
                if stamp % BAR_MS != 0 or not cursor <= stamp <= right_ms:
                    continue
                values = tuple(float(candle[i]) for i in (1, 2, 3, 4))
                if not np.isfinite(values).all() or min(values) <= 0:
                    continue
                op, hi, lo, close = values
                if hi < max(op, close) or lo > min(op, close) or lo > hi:
                    continue
                valid.append((stamp, op, hi, lo, close))
            except (IndexError, TypeError, ValueError):
                continue
        valid.sort(key=lambda row: row[0])
        rows.extend(valid)
        pages.append(
            {
                "start_ms": cursor,
                "rows": len(valid),
                "first_ms": valid[0][0] if valid else None,
                "last_ms": valid[-1][0] if valid else None,
            }
        )
        if not valid:
            stop = "empty_response"
            break
        cursor = valid[-1][0] + BAR_MS
        if len(valid) < REST_PAGE_LIMIT or cursor > right_ms:
            break
    if cursor <= right_ms and len(pages) >= MAX_REST_PAGES_PER_GAP:
        stop = "page_cap_reached"
    if rows:
        out = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        out.index = pd.to_datetime(out.pop("ts"), unit="ms", utc=True)
        out.index.name = "timestamp"
        out = out.loc[~out.index.duplicated(keep="last")].sort_index()
    else:
        out = _empty_ohlc()
    return out, {"start_ms": int(left_ms), "end_ms": int(right_ms), "rows": int(len(out)), "pages": pages, "stop_reason": stop}


def _persist_full_rest(symbol: str, frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    target_dir = RESEARCH / "ohlc_rest" / symbol.upper()
    target_dir.mkdir(parents=True, exist_ok=True)
    index_ms = frame.index.as_unit("ms").asi8
    target = target_dir / f"{int(index_ms.min())}_{int(index_ms.max())}.csv.gz"
    if target.is_file():
        return {"path": str(target), "status": "already_cached", "rows": int(len(frame)), "bytes": target.stat().st_size}
    fd, name = tempfile.mkstemp(prefix=".ohlc.", suffix=".csv.gz", dir=target_dir)
    os.close(fd)
    temp = Path(name)
    try:
        out = frame.loc[:, ["open", "high", "low", "close"]].copy()
        out.insert(0, "ts", index_ms)
        out.to_csv(temp, index=False, compression="gzip")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return {"path": str(target), "status": "written", "rows": int(len(frame)), "bytes": target.stat().st_size}


def _missing_full_runs(frame: pd.DataFrame, intervals: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    missing_runs: list[tuple[int, int]] = []
    for left, right in intervals:
        expected = pd.date_range(pd.to_datetime(left, unit="ms", utc=True), pd.to_datetime(right, unit="ms", utc=True), freq=BAR)
        observed = frame.reindex(expected)
        valid = observed[["open", "high", "low", "close"]].notna().all(axis=1).to_numpy()
        missing = expected[~valid]
        if not len(missing):
            continue
        ns = missing.as_unit("ns").asi8
        breaks = np.flatnonzero(np.diff(ns) != BAR.value) + 1
        chunks = np.split(ns, breaks)
        missing_runs.extend((int(chunk[0] // 1_000_000), int(chunk[-1] // 1_000_000)) for chunk in chunks if len(chunk))
    return missing_runs


def load_full_ohlc(
    symbol: str,
    start: Any,
    end: Any,
    *,
    needed_intervals: Sequence[Sequence[Any]] | None = None,
    repair_missing: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load observed full OHLC bars for one Binance USD-M symbol.

    Index is UTC candle-open time, the bar is 5m, and columns are ``open``,
    ``high``, ``low``, ``close``. Local paths/priority match
    ``monday_breadth_constituent_prices.load_quotes``. REST is considered only
    within explicit ``needed_intervals`` and only when ``repair_missing`` is
    true; its complete rows are cached under ``data/research``. Missing lows
    outside repaired intervals remain NaN. No close-as-low fallback is made.
    """
    symbol = str(symbol).strip().upper()
    if not symbol or any(not (char.isalnum() or char == "_") for char in symbol):
        raise ValueError(f"invalid Binance USD-M symbol: {symbol!r}")
    start_utc, end_utc = _utc(start), _utc(end)
    if end_utc < start_utc:
        raise ValueError("end must not precede start")
    start_ms, end_ms = _ms(start_utc), _ms(end_utc)
    windows = _normalise_intervals(start_utc, end_utc, needed_intervals)
    candidates, excluded = quote_sources._recorded_paths(symbol)
    local = [(kind, path, priority, aliases) for kind, path, priority, aliases in candidates]
    local.extend(("full_ohlc_rest", path, 60, [str(path)]) for path in _cache_paths(symbol, start_ms, end_ms))
    frame, source_receipts = _merge_full_sources(symbol, start_ms, end_ms, candidates=local)
    repair_receipts: list[dict[str, Any]] = []
    if repair_missing and windows:
        first_observed = frame.open.first_valid_index() if len(frame) else None
        if first_observed is not None:
            first_ms = _ms(first_observed)
            # Never request pre-listing history for instruments such as SOL.
            windows = [(max(left, first_ms), right) for left, right in windows if right >= first_ms]
        for left_ms, right_ms in _missing_full_runs(frame, windows):
            repaired, audit = _fetch_rest_full(symbol, left_ms, right_ms)
            audit["cache"] = _persist_full_rest(symbol, repaired)
            repair_receipts.append(audit)
            if not repaired.empty:
                frame, more = _merge_full_sources(
                    symbol,
                    start_ms,
                    end_ms,
                    candidates=local + [("full_ohlc_rest", Path(audit["cache"]["path"]), 60, [audit["cache"]["path"]])],
                )
                local.append(("full_ohlc_rest", Path(audit["cache"]["path"]), 60, [audit["cache"]["path"]]))
                source_receipts.extend(more)
    remaining = _missing_full_runs(frame, windows) if windows else []
    receipt = {
        "symbol": symbol,
        "venue": "Binance USD-M",
        "interval": "5m",
        "columns": ["open", "high", "low", "close"],
        "index": "UTC candle open time",
        "requested_range_utc": {"start": start_utc.isoformat(), "end": end_utc.isoformat(), "inclusive": True},
        "needed_intervals_utc": [[pd.to_datetime(a, unit="ms", utc=True).isoformat(), pd.to_datetime(b, unit="ms", utc=True).isoformat()] for a, b in windows],
        "local_sources": source_receipts,
        "excluded_source_lineage": excluded,
        "rows": int(len(frame)),
        "observed_range_utc": {"start": frame.index.min().isoformat() if len(frame) else None, "end": frame.index.max().isoformat() if len(frame) else None},
        "low_missing_rows_in_returned_frame": int(frame.low.isna().sum()),
        "rest_repairs": repair_receipts,
        "remaining_missing_full_ohlc_ranges": [[a, b] for a, b in remaining],
        "rest_endpoint": REST_URL if windows and repair_missing else None,
    }
    return frame, receipt


def _vbt_net_returns(entry: np.ndarray, exit_: np.ndarray) -> np.ndarray:
    """Run unique independent fixed-time trades through native VectorBT orders."""
    if len(entry) != len(exit_):
        raise ValueError("entry/exit lengths differ")
    if not len(entry):
        return np.empty(0, dtype=float)
    import vectorbt as vbt

    result = np.empty(len(entry), dtype=float)
    for start in range(0, len(entry), RETURN_BATCH):
        stop = min(len(entry), start + RETURN_BATCH)
        prices = np.stack([entry[start:stop], exit_[start:stop]])
        n = stop - start
        portfolio = vbt.Portfolio.from_orders(
            prices,
            size=np.vstack([np.ones(n, dtype=float), -np.ones(n, dtype=float)]),
            price=prices,
            fees=TAKER_FEE,
            init_cash=float(np.nanmax(prices) * 2),
            cash_sharing=False,
        )
        trades = portfolio.trades.records
        if len(trades) != n or not np.array_equal(trades.col.to_numpy(), np.arange(n)):
            raise AssertionError("VectorBT did not fill one entry and one timed exit per candidate")
        if not trades.status.eq(1).all() or not trades.entry_idx.eq(0).all() or not trades.exit_idx.eq(1).all():
            raise AssertionError("VectorBT trade fill sequence changed")
        if not np.allclose(trades.entry_fees.to_numpy(), entry[start:stop] * TAKER_FEE, rtol=1e-10, atol=1e-12):
            raise AssertionError("VectorBT entry fee mismatch")
        if not np.allclose(trades.exit_fees.to_numpy(), exit_[start:stop] * TAKER_FEE, rtol=1e-10, atol=1e-12):
            raise AssertionError("VectorBT exit fee mismatch")
        result[start:stop] = trades["return"].to_numpy() * 100
    return result


def _add_path_metrics(
    frame: pd.DataFrame,
    decision: pd.DatetimeIndex,
    hold_hours: int,
    *,
    entry_price: np.ndarray | None = None,
    exit_price: np.ndarray | None = None,
) -> pd.DataFrame:
    """Return per-distinct-entry path fields; excludes the exit candle's range."""
    steps = int(hold_hours * 12)
    grid_start = frame.index[0] if len(frame) else pd.Timestamp("1970-01-01", tz="UTC")
    if len(frame):
        grid_end = frame.index[-1]
        grid = pd.date_range(grid_start, grid_end, freq=BAR)
        dense = frame.reindex(grid)
        opens = dense.open.to_numpy(dtype=float)
        highs = dense.high.to_numpy(dtype=float)
        lows = dense.low.to_numpy(dtype=float)
        pos = grid.get_indexer(decision)
    else:
        grid = pd.DatetimeIndex([], tz="UTC")
        opens = highs = lows = np.empty(0, dtype=float)
        pos = np.full(len(decision), -1, dtype=int)
    n = len(decision)
    entry = np.full(n, np.nan) if entry_price is None else np.asarray(entry_price, dtype=float).copy()
    exit_ = np.full(n, np.nan) if exit_price is None else np.asarray(exit_price, dtype=float).copy()
    valid_pos = pos >= 0
    within = valid_pos & (pos + steps < len(grid))
    if entry_price is None:
        entry[within] = opens[pos[within]]
    if exit_price is None:
        exit_[within] = opens[pos[within] + steps]
    endpoint = np.isfinite(entry) & np.isfinite(exit_) & (entry > 0) & (exit_ > 0)
    net = np.full(n, np.nan)
    if endpoint.any():
        net[endpoint] = _vbt_net_returns(entry[endpoint], exit_[endpoint])
    gross = 100 * (exit_ / entry - 1)
    gross[~endpoint] = np.nan
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    path_complete = np.zeros(n, dtype=bool)
    hit5 = np.zeros(n, dtype=bool)
    hit10 = np.zeros(n, dtype=bool)
    first5 = np.full(n, "path_incomplete", dtype=object)
    first10 = np.full(n, "path_incomplete", dtype=object)
    prem5 = np.full(n, np.nan)
    prem10 = np.full(n, np.nan)
    for i in np.flatnonzero(endpoint & within):
        left, right = int(pos[i]), int(pos[i] + steps)
        high_window = highs[left:right]
        low_window = lows[left:right]
        if len(high_window) != steps or len(low_window) != steps or not np.isfinite(high_window).all() or not np.isfinite(low_window).all() or not np.isfinite(opens[right]):
            continue
        path_complete[i] = True
        h = np.maximum(high_window, exit_[i])
        l = np.minimum(low_window, exit_[i])
        mfe[i] = 100 * (float(np.max(h)) / entry[i] - 1)
        mae[i] = 100 * (float(np.min(l)) / entry[i] - 1)
        hit5[i] = bool(np.any(h >= entry[i] * 1.05))
        hit10[i] = bool(np.any(h >= entry[i] * 1.10))
        # OHLC cannot resolve high-vs-low order inside one 5m candle. For each
        # upside target, compare the first target bar with the first -5% bar.
        downside = np.flatnonzero(low_window <= entry[i] * 0.95)
        for target, status, pre in ((0.05, first5, prem5), (0.10, first10, prem10)):
            upside = np.flatnonzero(high_window >= entry[i] * (1 + target))
            up_i = int(upside[0]) if len(upside) else -1
            down_i = int(downside[0]) if len(downside) else -1
            if up_i < 0 and down_i < 0:
                status[i] = "neither"
            elif up_i >= 0 and down_i < 0:
                status[i] = "target_first"
            elif down_i >= 0 and up_i < 0:
                status[i] = "drawdown_first"
            elif up_i == down_i:
                status[i] = "same_bar_ambiguous"
            elif up_i < down_i:
                status[i] = "target_first"
            else:
                status[i] = "drawdown_first"
            if status[i] == "target_first" and up_i > 0:
                pre[i] = 100 * (float(np.min(low_window[:up_i])) / entry[i] - 1)
    return pd.DataFrame(
        {
            "entry_open": entry,
            "exit_open": exit_,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "path_complete": path_complete,
            "endpoint_available": endpoint,
            "hit5": hit5,
            "hit10": hit10,
            "first5_vs_drawdown5": first5,
            "first10_vs_drawdown5": first10,
            "pre_target_mae_5_pct": prem5,
            "pre_target_mae_10_pct": prem10,
        }
    )


def _member_outcomes_for_symbol(
    symbol: str,
    members: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    repair_missing: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    member_cols = ["minutes", "known", "symbol"] + [c for c in ("touch", "event_id", "lower_touch_level", "level_source", "age_at_known_min") if c in members]
    local_members = members.loc[members.symbol.eq(symbol), member_cols].copy()
    if local_members.empty:
        return pd.DataFrame(), pd.DataFrame(), {"symbol": symbol, "rows": 0}, []
    local_members["known"] = pd.to_datetime(local_members.known, utc=True)
    sig = signals[["minutes", "known", "wait_min", "decision"]].copy()
    sig["known"] = pd.to_datetime(sig.known, utc=True)
    work = local_members.merge(sig, on=["minutes", "known"], how="inner", validate="many_to_many")
    if work.empty:
        return pd.DataFrame(), pd.DataFrame(), {"symbol": symbol, "rows": 0, "status": "no_matching_signals"}, []
    if work.duplicated(["minutes", "known", "symbol", "wait_min"]).any():
        raise ValueError(f"duplicate member/signal key for {symbol}")
    if not (work.decision == work.known + pd.to_timedelta(work.wait_min, unit="m")).all():
        raise ValueError(f"signal decision does not equal known+wait for {symbol}")
    # Per-member causal observation and exact reclaim against its own first-touch level.
    unique_waits = work[["minutes", "known", "wait_min", "decision", "lower_touch_level"]].copy()
    unique_waits["known_close_time"] = unique_waits.known - BAR
    unique_waits["decision_close_time"] = unique_waits.decision - BAR
    first = min(unique_waits.known_close_time.min(), unique_waits.decision_close_time.min())
    last = max(unique_waits.decision.max() + pd.Timedelta(hours=max(HOLD_HOURS)), unique_waits.decision.max())
    intervals = [(row.decision, row.decision + pd.Timedelta(hours=max(HOLD_HOURS))) for row in unique_waits[["decision"]].itertuples(index=False)]
    frame, receipt = load_full_ohlc(symbol, first, last, needed_intervals=intervals, repair_missing=repair_missing)
    if len(frame):
        dense_idx = pd.date_range(frame.index[0], frame.index[-1], freq=BAR)
        dense = frame.reindex(dense_idx)
        close_lookup = dense.close
        decision_pos = dense_idx.get_indexer(pd.DatetimeIndex(unique_waits.decision_close_time))
        known_pos = dense_idx.get_indexer(pd.DatetimeIndex(unique_waits.known_close_time))
        known_close = np.full(len(unique_waits), np.nan)
        decision_close = np.full(len(unique_waits), np.nan)
        good = known_pos >= 0
        known_close[good] = close_lookup.to_numpy()[known_pos[good]]
        good = decision_pos >= 0
        decision_close[good] = close_lookup.to_numpy()[decision_pos[good]]
        unique_waits["observed_return_pct"] = 100 * (decision_close / known_close - 1)
        level = pd.to_numeric(unique_waits.lower_touch_level, errors="coerce").to_numpy(dtype=float)
        unique_waits["reclaimed"] = np.isfinite(decision_close) & np.isfinite(level) & (decision_close > level)
        unique_waits["quote_observed"] = np.isfinite(known_close) & np.isfinite(decision_close)
    else:
        unique_waits["observed_return_pct"] = np.nan
        unique_waits["reclaimed"] = False
        unique_waits["quote_observed"] = False
    # Exact lower-level observations are needed once per event/wait; retain the
    # member return list for an exact median after all symbol shards are reduced.
    observation_contrib = (
        unique_waits.groupby(["minutes", "known", "wait_min"], as_index=False, sort=False)
        .agg(
            quote_observed_n=("quote_observed", "sum"),
            reclaim_n=("reclaimed", "sum"),
            observed_values=("observed_return_pct", lambda s: s[np.isfinite(s)].tolist()),
        )
    )
    holds = pd.DataFrame({"hold_hours": HOLD_HOURS})
    work = work.merge(holds, how="cross")
    work["entry_time"] = work.decision
    work["exit_time"] = work.decision + pd.to_timedelta(work.hold_hours, unit="h")
    # One VectorBT trade is created per distinct actual (symbol, decision, hold)
    # even where several timeframe/event keys reuse that identical price path.
    distinct = work[["decision", "hold_hours"]].drop_duplicates().sort_values(["decision", "hold_hours"]).reset_index(drop=True)
    unique_metrics: list[pd.DataFrame] = []
    for hold, group in distinct.groupby("hold_hours", sort=True):
        metrics = _add_path_metrics(frame, pd.DatetimeIndex(group.decision), int(hold))
        metrics["decision"] = group.decision.to_numpy()
        metrics["hold_hours"] = int(hold)
        unique_metrics.append(metrics)
    prices = pd.concat(unique_metrics, ignore_index=True) if unique_metrics else pd.DataFrame()
    work = work.merge(prices, on=["decision", "hold_hours"], how="left", validate="many_to_one")
    obs_cols = ["minutes", "known", "wait_min", "observed_return_pct", "reclaimed", "quote_observed"]
    work = work.merge(unique_waits[obs_cols], on=["minutes", "known", "wait_min"], how="left", validate="many_to_one")
    work.insert(0, "symbol", symbol)
    work["trade_id"] = symbol + "|" + work.decision.dt.strftime("%Y%m%dT%H%M%SZ") + "|" + work.hold_hours.astype(str) + "h"
    raw_cols = [
        "minutes", "known", "wait_min", "hold_hours", "symbol", "event_id", "touch", "lower_touch_level", "level_source",
        "age_at_known_min", "decision", "entry_time", "exit_time", "trade_id", "entry_open", "exit_open",
        "gross_return_pct", "net_return_pct", "mfe_pct", "mae_pct", "path_complete", "endpoint_available", "hit5", "hit10",
        "first5_vs_drawdown5", "first10_vs_drawdown5", "pre_target_mae_5_pct", "pre_target_mae_10_pct",
        "observed_return_pct", "reclaimed", "quote_observed",
    ]
    raw = work.loc[:, [c for c in raw_cols if c in work.columns]].copy()
    return raw, observation_contrib, receipt, _symbol_partials(raw)


def _symbol_partials(raw: pd.DataFrame) -> list[dict[str, Any]]:
    if raw.empty:
        return []
    keys = ["minutes", "known", "wait_min", "hold_hours"]
    groups = raw.groupby(keys, sort=False, dropna=False)
    rows: list[dict[str, Any]] = []
    for key, part in groups:
        endpoint = part[part.endpoint_available.fillna(False)]
        path = part[part.path_complete.fillna(False)]
        t5 = part.first5_vs_drawdown5.value_counts()
        t10 = part.first10_vs_drawdown5.value_counts()
        row = dict(zip(keys, key))
        row.update(
            member_n=len(part),
            endpoint_n=len(endpoint),
            gross_sum=float(endpoint.gross_return_pct.sum()),
            net_sum=float(endpoint.net_return_pct.sum()),
            gross_win_n=int(endpoint.gross_return_pct.gt(0).sum()),
            net_win_n=int(endpoint.net_return_pct.gt(0).sum()),
            path_n=len(path),
            mfe_sum=float(path.mfe_pct.sum()),
            mae_sum=float(path.mae_pct.sum()),
            hit5_n=int(path.hit5.sum()),
            hit10_n=int(path.hit10.sum()),
            target5_first_n=int(t5.get("target_first", 0)),
            drawdown5_before_target5_n=int(t5.get("drawdown_first", 0)),
            ambiguous5_n=int(t5.get("same_bar_ambiguous", 0)),
            nohit5_n=int(t5.get("neither", 0)),
            target10_first_n=int(t10.get("target_first", 0)),
            drawdown5_before_target10_n=int(t10.get("drawdown_first", 0)),
            ambiguous10_n=int(t10.get("same_bar_ambiguous", 0)),
            nohit10_n=int(t10.get("neither", 0)),
            pre_target_mae5_sum=float(path.pre_target_mae_5_pct.sum()),
            pre_target_mae5_n=int(path.pre_target_mae_5_pct.notna().sum()),
            pre_target_mae10_sum=float(path.pre_target_mae_10_pct.sum()),
            pre_target_mae10_n=int(path.pre_target_mae_10_pct.notna().sum()),
        )
        rows.append(row)
    return rows


def _write_parquet_parts(raw: pd.DataFrame) -> list[dict[str, Any]]:
    if raw.empty:
        return []
    written: list[dict[str, Any]] = []
    for minutes, part in raw.groupby("minutes", sort=True):
        part = part.reset_index(drop=True)
        part_dir = RESEARCH / "constituents" / f"minutes={int(minutes)}" / f"symbol={str(part.symbol.iloc[0])}"
        part_dir.mkdir(parents=True, exist_ok=True)
        for offset in range(0, len(part), PARQUET_PART_ROWS):
            chunk = part.iloc[offset : offset + PARQUET_PART_ROWS]
            part_no = offset // PARQUET_PART_ROWS
            path = part_dir / f"part-{part_no:05d}.parquet"
            temp = path.with_suffix(".parquet.tmp")
            chunk.to_parquet(temp, index=False, compression="zstd", engine="pyarrow")
            os.replace(temp, path)
            written.append({"path": str(path), "rows": int(len(chunk)), "bytes": int(path.stat().st_size)})
    return written


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _process_symbol(
    symbol: str,
    members: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    repair_missing: bool,
    force: bool,
) -> dict[str, Any]:
    cache_root = RESEARCH / "symbol_receipts"
    receipt_path = cache_root / f"{symbol}.json"
    contrib_path = RESEARCH / "contributions" / f"{symbol}.parquet"
    observations_path = RESEARCH / "observations" / f"{symbol}.parquet"
    if not force and receipt_path.is_file() and contrib_path.is_file() and observations_path.is_file():
        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        if saved.get("input_contract") == _input_contract():
            return {"symbol": symbol, "cached": True, "receipt": saved}
    raw, obs, quote_receipt, partial_rows = _member_outcomes_for_symbol(
        symbol, members, signals, repair_missing=repair_missing
    )
    files = _write_parquet_parts(raw)
    contrib = pd.DataFrame(partial_rows)
    contrib_path.parent.mkdir(parents=True, exist_ok=True)
    contrib_temp = contrib_path.with_suffix(".parquet.tmp")
    contrib.to_parquet(contrib_temp, index=False, compression="zstd", engine="pyarrow")
    os.replace(contrib_temp, contrib_path)
    observations_path.parent.mkdir(parents=True, exist_ok=True)
    obs_temp = observations_path.with_suffix(".parquet.tmp")
    obs.to_parquet(obs_temp, index=False, compression="zstd", engine="pyarrow")
    os.replace(obs_temp, observations_path)
    receipt = {
        "symbol": symbol,
        "input_contract": _input_contract(),
        "member_outcome_rows": int(len(raw)),
        "event_contribution_rows": int(len(contrib)),
        "observation_contribution_rows": int(len(obs)),
        "constituent_files": files,
        "quote_receipt": quote_receipt,
    }
    _write_json(receipt_path, receipt)
    return {"symbol": symbol, "cached": False, "receipt": receipt}


def _input_contract() -> dict[str, Any]:
    return {
        "minutes": list(MINUTES),
        "wait_min": list(WAIT_MINUTES),
        "hold_hours": list(HOLD_HOURS),
        "fee_taker_per_fill": TAKER_FEE,
        "entry": "5m open at known+wait_min",
        "exit": "5m open at entry+hold_hours",
        "path": "high/low for entry bar through bar before exit; exit open included",
        "first_hit": "target +5/+10% versus -5%; same five-minute bar labelled ambiguous",
    }


def _aggregate_contributions(symbols: Sequence[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    contributions: list[pd.DataFrame] = []
    observations: list[pd.DataFrame] = []
    for symbol in symbols:
        cpath = RESEARCH / "contributions" / f"{symbol}.parquet"
        opath = RESEARCH / "observations" / f"{symbol}.parquet"
        if cpath.is_file():
            contributions.append(pd.read_parquet(cpath))
        if opath.is_file():
            observations.append(pd.read_parquet(opath))
    if not contributions:
        return pd.DataFrame(), pd.DataFrame()
    raw = pd.concat(contributions, ignore_index=True)
    keys = ["minutes", "known", "wait_min", "hold_hours"]
    sums = [c for c in raw.columns if c not in keys]
    total = raw.groupby(keys, as_index=False, sort=False)[sums].sum(numeric_only=True)
    if not observations:
        return total, pd.DataFrame()
    obs = pd.concat(observations, ignore_index=True)
    obs_keys = ["minutes", "known", "wait_min"]
    obs_rows: list[dict[str, Any]] = []
    for key, part in obs.groupby(obs_keys, sort=False):
        vals = [np.asarray(x, dtype=float) for x in part.observed_values if isinstance(x, (list, tuple, np.ndarray)) and len(x)]
        finite = np.concatenate(vals) if vals else np.empty(0, dtype=float)
        row = dict(zip(obs_keys, key))
        row["quote_observed_n"] = int(part.quote_observed_n.sum())
        row["reclaim_n"] = int(part.reclaim_n.sum())
        row["price_n"] = int(len(finite))
        row["alt_member_median_observed_return_pct"] = float(np.median(finite)) if len(finite) else np.nan
        obs_rows.append(row)
    return total, pd.DataFrame(obs_rows)


def _benchmark_outcomes(signals: pd.DataFrame, *, repair_missing: bool) -> tuple[pd.DataFrame, dict[str, Any]]:
    keys = ["minutes", "known", "wait_min", "decision"]
    unique = signals[keys].drop_duplicates().copy()
    unique["known_close_time"] = unique.known - BAR
    unique["decision_close_time"] = unique.decision - BAR
    output = unique[["minutes", "known", "wait_min", "decision"]].copy()
    metadata: dict[str, Any] = {}
    for asset in ASSETS:
        symbol = asset + "USDT"
        start = min(unique.known_close_time.min(), unique.decision_close_time.min())
        end = unique.decision.max() + pd.Timedelta(hours=max(HOLD_HOURS))
        intervals = [(row.decision, row.decision + pd.Timedelta(hours=max(HOLD_HOURS))) for row in unique[["decision"]].itertuples(index=False)]
        frame, receipt = load_full_ohlc(symbol, start, end, needed_intervals=intervals, repair_missing=repair_missing)
        metadata[asset] = receipt
        if len(frame):
            dense_idx = pd.date_range(frame.index[0], frame.index[-1], freq=BAR)
            dense = frame.reindex(dense_idx)
            close = dense.close.to_numpy(dtype=float)
            known_pos = dense_idx.get_indexer(pd.DatetimeIndex(unique.known_close_time))
            decision_pos = dense_idx.get_indexer(pd.DatetimeIndex(unique.decision_close_time))
            before = np.full(len(unique), np.nan)
            after = np.full(len(unique), np.nan)
            ok = known_pos >= 0
            before[ok] = close[known_pos[ok]]
            ok = decision_pos >= 0
            after[ok] = close[decision_pos[ok]]
            output[f"{asset.lower()}_observed_return_pct"] = 100 * (after / before - 1)
        else:
            output[f"{asset.lower()}_observed_return_pct"] = np.nan
        metric_rows: list[pd.DataFrame] = []
        for hold in HOLD_HOURS:
            metrics = _add_path_metrics(frame, pd.DatetimeIndex(unique.decision), hold)
            metrics["hold_hours"] = hold
            metrics["minutes"] = unique.minutes.to_numpy()
            metrics["known"] = unique.known.to_numpy()
            metrics["wait_min"] = unique.wait_min.to_numpy()
            metric_rows.append(metrics)
        long = pd.concat(metric_rows, ignore_index=True)
        output = output.merge(
            long[["minutes", "known", "wait_min", "hold_hours", "gross_return_pct", "net_return_pct", "mfe_pct", "mae_pct", "endpoint_available", "path_complete"]]
            .rename(columns={
                "gross_return_pct": f"{asset.lower()}_gross_return_pct",
                "net_return_pct": f"{asset.lower()}_net_return_pct",
                "mfe_pct": f"{asset.lower()}_mfe_pct",
                "mae_pct": f"{asset.lower()}_mae_pct",
                "endpoint_available": f"{asset.lower()}_endpoint_available",
                "path_complete": f"{asset.lower()}_path_complete",
            }),
            on=["minutes", "known", "wait_min"],
            how="left",
            validate="one_to_many",
        )
    return output, metadata


def _final_event_table(signals: pd.DataFrame, members: pd.DataFrame, symbols: Sequence[str], *, repair_missing: bool) -> tuple[pd.DataFrame, dict[str, Any]]:
    totals, observations = _aggregate_contributions(symbols)
    if totals.empty:
        raise RuntimeError("no payoff contribution cache found")
    signal_cols = [c for c in SIGNAL_FEATURE_COLUMNS if c in signals.columns]
    base = signals[["minutes", "known", "wait_min", "decision", *signal_cols]].drop_duplicates(
        ["minutes", "known", "wait_min"]
    ).copy()
    base["known"] = pd.to_datetime(base.known, utc=True)
    base = base.merge(observations, on=["minutes", "known", "wait_min"], how="left", validate="one_to_one")
    # If all initial members have a usable quote, fraction has the original
    # constituent denominator. Missing prices remain visible in price_n.
    expected = members.groupby(["minutes", "known"]).size().rename("initial_member_n").reset_index()
    expected["known"] = pd.to_datetime(expected.known, utc=True)
    base = base.merge(expected, on=["minutes", "known"], how="left", validate="many_to_one")
    base["reclaim_fraction"] = base.reclaim_n / base.price_n.replace(0, np.nan)
    holds = pd.DataFrame({"hold_hours": HOLD_HOURS})
    event = base.merge(holds, how="cross")
    event = event.merge(totals, on=["minutes", "known", "wait_min", "hold_hours"], how="left", validate="one_to_one")
    count_cols = [
        "member_n", "endpoint_n", "gross_win_n", "net_win_n", "path_n", "hit5_n", "hit10_n",
        "target5_first_n", "drawdown5_before_target5_n", "ambiguous5_n", "nohit5_n",
        "target10_first_n", "drawdown5_before_target10_n", "ambiguous10_n", "nohit10_n",
        "pre_target_mae5_n", "pre_target_mae10_n",
    ]
    for column in count_cols:
        if column in event:
            event[column] = event[column].fillna(0).astype("int64")
    event["gross_coverage_pct"] = 100 * event.endpoint_n / event.initial_member_n.replace(0, np.nan)
    event["path_coverage_pct"] = 100 * event.path_n / event.initial_member_n.replace(0, np.nan)
    event["basket_gross_return_pct"] = event.gross_sum / event.endpoint_n.replace(0, np.nan)
    event["basket_net_return_pct"] = event.net_sum / event.endpoint_n.replace(0, np.nan)
    event["member_win_pct"] = 100 * event.gross_win_n / event.endpoint_n.replace(0, np.nan)
    event["member_net_win_pct"] = 100 * event.net_win_n / event.endpoint_n.replace(0, np.nan)
    event["mean_mfe_pct"] = event.mfe_sum / event.path_n.replace(0, np.nan)
    event["mean_mae_pct"] = event.mae_sum / event.path_n.replace(0, np.nan)
    event["hit5_pct"] = 100 * event.hit5_n / event.path_n.replace(0, np.nan)
    event["hit10_pct"] = 100 * event.hit10_n / event.path_n.replace(0, np.nan)
    resolved5 = event.target5_first_n + event.drawdown5_before_target5_n
    resolved10 = event.target10_first_n + event.drawdown5_before_target10_n
    event["target5_before_drawdown5_pct"] = 100 * event.target5_first_n / resolved5.replace(0, np.nan)
    event["target10_before_drawdown5_pct"] = 100 * event.target10_first_n / resolved10.replace(0, np.nan)
    event["pre_target_mae_5_pct"] = event.pre_target_mae5_sum / event.pre_target_mae5_n.replace(0, np.nan)
    event["pre_target_mae_10_pct"] = event.pre_target_mae10_sum / event.pre_target_mae10_n.replace(0, np.nan)
    benchmark, quote_receipts = _benchmark_outcomes(signals, repair_missing=repair_missing)
    event = event.merge(
        benchmark,
        on=["minutes", "known", "wait_min", "hold_hours"],
        how="left",
        validate="one_to_one",
    )
    event["members_expected_n"] = event.initial_member_n
    wanted = [
        "minutes", "known", "wait_min", "hold_hours", "decision", *signal_cols,
        "members_expected_n", "endpoint_n", "gross_coverage_pct", "path_n", "path_coverage_pct",
        "basket_gross_return_pct", "basket_net_return_pct", "member_win_pct", "member_net_win_pct",
        "mean_mfe_pct", "mean_mae_pct", "hit5_pct", "hit10_pct",
        "target5_before_drawdown5_pct", "target10_before_drawdown5_pct",
        "target5_first_n", "drawdown5_before_target5_n", "ambiguous5_n", "nohit5_n",
        "target10_first_n", "drawdown5_before_target10_n", "ambiguous10_n", "nohit10_n",
        "pre_target_mae_5_pct", "pre_target_mae_10_pct",
        "btc_observed_return_pct", "eth_observed_return_pct", "sol_observed_return_pct",
        "alt_member_median_observed_return_pct", "reclaim_fraction", "reclaim_n", "price_n",
        "btc_gross_return_pct", "btc_net_return_pct", "btc_mfe_pct", "btc_mae_pct",
        "eth_gross_return_pct", "eth_net_return_pct", "eth_mfe_pct", "eth_mae_pct",
        "sol_gross_return_pct", "sol_net_return_pct", "sol_mfe_pct", "sol_mae_pct",
    ]
    event = event.loc[:, [c for c in wanted if c in event.columns]].sort_values(["minutes", "known", "wait_min", "hold_hours"])
    if event.duplicated(["minutes", "known", "wait_min", "hold_hours"]).any():
        raise AssertionError("event outcome join keys are not unique")
    return event, quote_receipts


def _load_inputs(minutes: Sequence[int] | None, waits: Sequence[int] | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not MEMBERS_PATH.is_file() or not SIGNALS_PATH.is_file():
        raise FileNotFoundError(f"Expected feature inputs from opt_features: {MEMBERS_PATH} and {SIGNALS_PATH}")
    signals = pd.read_csv(SIGNALS_PATH, compression="infer", parse_dates=["known", "decision"])
    members = pd.read_csv(MEMBERS_PATH, compression="infer", parse_dates=["known", "touch"])
    signals["known"] = pd.to_datetime(signals.known, utc=True)
    signals["decision"] = pd.to_datetime(signals.decision, utc=True)
    members["known"] = pd.to_datetime(members.known, utc=True)
    if "touch" in members:
        members["touch"] = pd.to_datetime(members.touch, utc=True)
    if minutes:
        signals = signals[signals.minutes.isin(minutes)].copy()
        members = members[members.minutes.isin(minutes)].copy()
    if waits:
        signals = signals[signals.wait_min.isin(waits)].copy()
    if signals.duplicated(["minutes", "known", "wait_min"]).any():
        raise ValueError("duplicate feature signal keys")
    if members.duplicated(["minutes", "known", "symbol"]).any():
        raise ValueError("duplicate initial member keys")
    if not members.symbol.astype(str).str.endswith("USDT").all():
        raise ValueError("member symbols must be Binance USD-M contracts")
    if not signals.minutes.isin(MINUTES).all() or not signals.wait_min.isin(WAIT_MINUTES).all():
        raise ValueError("feature rows contain unauthorized timeframe/wait values")
    return signals.sort_values(["minutes", "known", "wait_min"]).reset_index(drop=True), members.sort_values(["minutes", "known", "symbol"]).reset_index(drop=True)


def _write_event_partitions(event: pd.DataFrame) -> list[dict[str, Any]]:
    PAYOFFS.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for (minutes, wait_min), part in event.groupby(["minutes", "wait_min"], sort=True):
        path = PAYOFFS / f"event_outcomes_minutes{int(minutes)}_wait{int(wait_min)}.csv.gz"
        temp = path.with_suffix(path.suffix + ".tmp")
        part.to_csv(temp, index=False, compression="gzip")
        os.replace(temp, path)
        written.append({"path": str(path.relative_to(ROOT)), "minutes": int(minutes), "wait_min": int(wait_min), "rows": int(len(part)), "bytes": int(path.stat().st_size)})
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--minutes", type=int, nargs="*", choices=MINUTES)
    parser.add_argument("--wait-min", type=int, nargs="*", choices=WAIT_MINUTES)
    parser.add_argument("--symbols", nargs="*", help="optional symbol subset for a bounded run")
    parser.add_argument("--no-repair", action="store_true", help="read only registered local OHLC/cache")
    parser.add_argument("--force", action="store_true", help="recompute per-symbol payoff cache")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8:
        raise ValueError("workers must be between 1 and 8 to bound memory/API concurrency")
    signals, members = _load_inputs(args.minutes, args.wait_min)
    symbols = sorted(set(members.symbol.astype(str)))
    if args.symbols:
        selected = {str(x).strip().upper() for x in args.symbols}
        symbols = [s for s in symbols if s in selected]
        if not symbols:
            raise ValueError("no requested symbols are in the initial member inputs")
        members = members[members.symbol.isin(symbols)].copy()
    RESEARCH.mkdir(parents=True, exist_ok=True)
    PAYOFFS.mkdir(parents=True, exist_ok=True)
    contract = _input_contract()
    _write_json(PAYOFFS / "contract.json", {**contract, "member_input": str(MEMBERS_PATH.relative_to(ROOT)), "signal_input": str(SIGNALS_PATH.relative_to(ROOT)), "join_keys": ["minutes", "known", "wait_min", "hold_hours"]})
    print(f"signals={len(signals):,} initial_members={len(members):,} symbols={len(symbols):,} workers={args.workers}", flush=True)
    finished: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _process_symbol,
                symbol,
                members[members.symbol.eq(symbol)],
                signals,
                repair_missing=not args.no_repair,
                force=args.force,
            ): symbol
            for symbol in symbols
        }
        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                finished.append(future.result())
            except Exception as exc:
                for item in futures:
                    item.cancel()
                raise RuntimeError(f"payoff generation failed for {symbol}") from exc
            if i % 25 == 0 or i == len(futures):
                print(f"{i}/{len(futures)} symbols complete; last={symbol}", flush=True)
    final, benchmark_receipts = _final_event_table(signals, members, symbols, repair_missing=not args.no_repair)
    files = _write_event_partitions(final)
    manifest = {
        "experiment_id": "exp-monday-breadth-optimization-20261108-v1",
        "input_contract": contract,
        "input_paths": {"members": str(MEMBERS_PATH.relative_to(ROOT)), "signals": str(SIGNALS_PATH.relative_to(ROOT))},
        "symbols": len(symbols),
        "signals": int(len(signals)),
        "initial_member_rows": int(len(members)),
        "event_outcome_rows": int(len(final)),
        "event_partitions": files,
        "constituent_cache_root": str((RESEARCH / "constituents").relative_to(ROOT)),
        "constituent_cache_files": sum(len(row["receipt"].get("constituent_files", [])) for row in finished if not row.get("cached")),
        "source_receipt_root": str((RESEARCH / "symbol_receipts").relative_to(ROOT)),
        "benchmark_quote_receipts": benchmark_receipts,
        "vectorbt": "Portfolio.from_orders; one independent unit-size market entry/exit per unique (symbol, decision, hold_hours); 0.05% taker per fill; cash_sharing=False",
        "fees": {"entry": TAKER_FEE, "exit": TAKER_FEE, "funding": "not modeled", "slippage": "not modeled"},
        "cash_account_interpretation": "independent unit-size trade returns and equal-weight constituent means; no shared funded multi-symbol account",
        "path_limitations": "5m OHLC does not establish intrabar order; same-bar +target/-5% is ambiguous; missing low/path bars are not inferred",
    }
    _write_json(PAYOFFS / "manifest.json", manifest)
    print(f"wrote {len(files)} event partitions, {len(final):,} joined keys; research cache={RESEARCH}", flush=True)


if __name__ == "__main__":
    main()
