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
    "N",
    "initial",
    "band",
    "new_coins",
    "first_half_new",
    "last_half_new",
    "median_member_touch_history_age_days",
    "trailing28_universe",
    "share28_pct",
    "monitor_fraction",
    "initial_monitor_fraction",
    "wait_monitor_fraction",
    "monitor_bars",
    "market_share",
    "market_share_status",
    "last_touch_age_min",
    "since_prev_lower_touch_min",
    "since_prev_25_lower_shock_min",
    "since_prev_25_any_side_shock_min",
    "lower_shock_history_status",
    "any_side_shock_history_status",
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
    source_starts = [
        int(bounds[0])
        for kind, path, _, _ in candidates
        if (bounds := quote_sources._source_bounds(kind, path, symbol)) is not None
    ]
    earliest_registered_ms = min(source_starts) if source_starts else None
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
        elif earliest_registered_ms is not None:
            # When the requested slice predates every registered candle, avoid
            # asking REST for a market that had not begun trading locally.
            windows = [(max(left, earliest_registered_ms), right) for left, right in windows if right >= earliest_registered_ms]
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
        "earliest_registered_source_ms": earliest_registered_ms,
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


_NUMBA_PATH_SCAN = None


def _scan_paths_numba(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    starts: np.ndarray,
    hold_steps: np.ndarray,
) -> tuple[np.ndarray, ...]:
    """Single compiled pass per unique trade path, including explicit exit open."""
    global _NUMBA_PATH_SCAN
    if _NUMBA_PATH_SCAN is None:
        from numba import njit

        @njit
        def scan(open_, high_, low_, start_, steps_):
            n = len(start_)
            entry = np.full(n, np.nan)
            exit_ = np.full(n, np.nan)
            gross = np.full(n, np.nan)
            mfe = np.full(n, np.nan)
            mae = np.full(n, np.nan)
            complete = np.zeros(n, dtype=np.bool_)
            hit5 = np.zeros(n, dtype=np.bool_)
            hit10 = np.zeros(n, dtype=np.bool_)
            first5 = np.zeros(n, dtype=np.int8)  # 0=incomplete, 1=neither, 2=target, 3=drawdown, 4=ambiguous
            first10 = np.zeros(n, dtype=np.int8)
            pre5 = np.full(n, np.nan)
            pre10 = np.full(n, np.nan)
            for i in range(n):
                p = start_[i]
                steps = steps_[i]
                if p < 0 or p + steps >= len(open_):
                    continue
                e = open_[p]
                x = open_[p + steps]
                if not np.isfinite(e) or not np.isfinite(x) or e <= 0.0 or x <= 0.0:
                    continue
                entry[i] = e
                exit_[i] = x
                gross[i] = 100.0 * (x / e - 1.0)
                complete_path = True
                max_price = e
                min_price = e
                down_i = -1
                up5_i = -1
                up10_i = -1
                pre5_price = e
                pre10_price = e
                for j in range(steps):
                    bar = p + j
                    hi = high_[bar]
                    lo = low_[bar]
                    if not np.isfinite(hi) or not np.isfinite(lo) or hi <= 0.0 or lo <= 0.0:
                        complete_path = False
                        break
                    min_before_bar = min_price
                    if hi > max_price:
                        max_price = hi
                    if up5_i < 0 and hi >= e * 1.05 - e * 1e-12:
                        up5_i = j
                        pre5_price = min_before_bar
                    if up10_i < 0 and hi >= e * 1.10 - e * 1e-12:
                        up10_i = j
                        pre10_price = min_before_bar
                    if down_i < 0 and lo <= e * 0.95 + e * 1e-12:
                        down_i = j
                    if lo < min_price:
                        min_price = lo
                    if up5_i < 0:
                        pre5_price = min(pre5_price, lo)
                    if up10_i < 0:
                        pre10_price = min(pre10_price, lo)
                if not complete_path:
                    continue
                # The exact timed exit is a single open print; do not broadcast
                # it over earlier highs/lows or inspect the exit candle's range.
                if x > max_price:
                    max_price = x
                if x < min_price:
                    min_price = x
                if x >= e * 1.05 - e * 1e-12 and up5_i < 0:
                    up5_i = steps
                    pre5_price = min_price
                if x >= e * 1.10 - e * 1e-12 and up10_i < 0:
                    up10_i = steps
                    pre10_price = min_price
                if x <= e * 0.95 + e * 1e-12 and down_i < 0:
                    down_i = steps
                complete[i] = True
                mfe[i] = 100.0 * (max_price / e - 1.0)
                mae[i] = 100.0 * (min_price / e - 1.0)
                hit5[i] = up5_i >= 0
                hit10[i] = up10_i >= 0
                if up5_i < 0 and down_i < 0:
                    first5[i] = 1
                elif up5_i >= 0 and down_i < 0:
                    first5[i] = 2
                    pre5[i] = 100.0 * (pre5_price / e - 1.0)
                elif down_i >= 0 and up5_i < 0:
                    first5[i] = 3
                elif up5_i == down_i:
                    first5[i] = 4
                elif up5_i < down_i:
                    first5[i] = 2
                    pre5[i] = 100.0 * (pre5_price / e - 1.0)
                else:
                    first5[i] = 3
                if up10_i < 0 and down_i < 0:
                    first10[i] = 1
                elif up10_i >= 0 and down_i < 0:
                    first10[i] = 2
                    pre10[i] = 100.0 * (pre10_price / e - 1.0)
                elif down_i >= 0 and up10_i < 0:
                    first10[i] = 3
                elif up10_i == down_i:
                    first10[i] = 4
                elif up10_i < down_i:
                    first10[i] = 2
                    pre10[i] = 100.0 * (pre10_price / e - 1.0)
                else:
                    first10[i] = 3
            return entry, exit_, gross, mfe, mae, complete, hit5, hit10, first5, first10, pre5, pre10

        _NUMBA_PATH_SCAN = scan
    return _NUMBA_PATH_SCAN(opens, highs, lows, starts, hold_steps)


def _path_metrics_many(frame: pd.DataFrame, decisions: Sequence[Any], holds: Sequence[int]) -> pd.DataFrame:
    """Calculate endpoint and OHLC path statistics for unique decision/horizon rows."""
    decision = pd.DatetimeIndex(pd.to_datetime(decisions, utc=True))
    hold_array = np.asarray(holds, dtype=np.int64)
    if len(decision) != len(hold_array):
        raise ValueError("decision and hold arrays differ in length")
    if len(frame):
        grid = pd.date_range(frame.index[0], frame.index[-1], freq=BAR)
        dense = frame.reindex(grid)
        opens = dense.open.to_numpy(dtype=np.float64)
        highs = dense.high.to_numpy(dtype=np.float64)
        lows = dense.low.to_numpy(dtype=np.float64)
        starts = grid.get_indexer(decision).astype(np.int64)
    else:
        opens = highs = lows = np.empty(0, dtype=np.float64)
        starts = np.full(len(decision), -1, dtype=np.int64)
    metrics = _scan_paths_numba(opens, highs, lows, starts, hold_array * 12)
    entry, exit_, gross, mfe, mae, complete, hit5, hit10, first5, first10, pre5, pre10 = metrics
    endpoint = np.isfinite(entry) & np.isfinite(exit_) & (entry > 0) & (exit_ > 0)
    net = np.full(len(entry), np.nan)
    if endpoint.any():
        net[endpoint] = _vbt_net_returns(entry[endpoint], exit_[endpoint])
    status = {0: "path_incomplete", 1: "neither", 2: "target_first", 3: "drawdown_first", 4: "same_bar_ambiguous"}
    return pd.DataFrame(
        {
            "entry_open": entry,
            "exit_open": exit_,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "path_complete": complete,
            "endpoint_available": endpoint,
            "hit5": hit5,
            "hit10": hit10,
            "first5_vs_drawdown5": [status[int(x)] for x in first5],
            "first10_vs_drawdown5": [status[int(x)] for x in first10],
            "pre_target_mae_5_pct": pre5,
            "pre_target_mae_10_pct": pre10,
        }
    )


def _member_outcomes_for_symbol(
    symbol: str,
    members: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    repair_missing: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    member_cols = ["minutes", "known", "symbol"] + [c for c in ("touch", "event", "event_id", "lower_touch_level", "level_source", "age_at_known_min") if c in members]
    local_members = members.loc[members.symbol.eq(symbol), member_cols].copy()
    if local_members.empty:
        return pd.DataFrame(), {"symbol": symbol, "rows": 0}
    if "event" in local_members and "event_id" not in local_members:
        local_members = local_members.rename(columns={"event": "event_id"})
    local_members["known"] = pd.to_datetime(local_members.known, utc=True)
    sig = signals[["minutes", "known", "wait_min", "decision"]].copy()
    sig["known"] = pd.to_datetime(sig.known, utc=True)
    work = local_members.merge(sig, on=["minutes", "known"], how="inner", validate="many_to_many")
    if work.empty:
        return pd.DataFrame(), {"symbol": symbol, "rows": 0, "status": "no_matching_signals"}
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
        unique_waits["reclaim_eligible"] = np.isfinite(decision_close) & np.isfinite(level)
        unique_waits["reclaimed"] = unique_waits.reclaim_eligible & (decision_close > level)
        unique_waits["quote_observed"] = np.isfinite(known_close) & np.isfinite(decision_close)
    else:
        unique_waits["observed_return_pct"] = np.nan
        unique_waits["reclaimed"] = False
        unique_waits["reclaim_eligible"] = False
        unique_waits["quote_observed"] = False
    holds = pd.DataFrame({"hold_hours": HOLD_HOURS})
    work = work.merge(holds, how="cross")
    work["entry_time"] = work.decision
    work["exit_time"] = work.decision + pd.to_timedelta(work.hold_hours, unit="h")
    # One VectorBT trade is created per distinct actual (symbol, decision, hold)
    # even where several timeframe/event keys reuse that identical price path.
    distinct = work[["decision", "hold_hours"]].drop_duplicates().sort_values(["decision", "hold_hours"]).reset_index(drop=True)
    prices = _path_metrics_many(frame, distinct.decision, distinct.hold_hours)
    prices["decision"] = distinct.decision.to_numpy()
    prices["hold_hours"] = distinct.hold_hours.to_numpy()
    work = work.merge(prices, on=["decision", "hold_hours"], how="left", validate="many_to_one")
    obs_cols = ["minutes", "known", "wait_min", "observed_return_pct", "reclaimed", "reclaim_eligible", "quote_observed"]
    work = work.merge(unique_waits[obs_cols], on=["minutes", "known", "wait_min"], how="left", validate="many_to_one")
    work["symbol"] = symbol
    work["trade_id"] = symbol + "|" + work.decision.dt.strftime("%Y%m%dT%H%M%SZ") + "|" + work.hold_hours.astype(str) + "h"
    raw_cols = [
        "minutes", "known", "wait_min", "hold_hours", "symbol", "event_id", "touch", "lower_touch_level", "level_source",
        "age_at_known_min", "decision", "entry_time", "exit_time", "trade_id", "entry_open", "exit_open",
        "gross_return_pct", "net_return_pct", "mfe_pct", "mae_pct", "path_complete", "endpoint_available", "hit5", "hit10",
        "first5_vs_drawdown5", "first10_vs_drawdown5", "pre_target_mae_5_pct", "pre_target_mae_10_pct",
        "observed_return_pct", "reclaimed", "reclaim_eligible", "quote_observed",
    ]
    raw = work.loc[:, [c for c in raw_cols if c in work.columns]].copy()
    return raw, receipt


def _write_parquet_parts(raw: pd.DataFrame) -> list[dict[str, Any]]:
    if raw.empty:
        return []
    written: list[dict[str, Any]] = []
    for minutes, part in raw.groupby("minutes", sort=True):
        part = part.sort_values(["wait_min", "known", "hold_hours"], kind="stable").reset_index(drop=True)
        part_dir = RESEARCH / "constituents" / f"minutes={int(minutes)}" / f"symbol={str(part.symbol.iloc[0])}"
        part_dir.mkdir(parents=True, exist_ok=True)
        for stale in part_dir.glob("part-*.parquet"):
            stale.unlink()
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
    scope_minutes: Sequence[int],
    scope_wait_min: Sequence[int],
) -> dict[str, Any]:
    cache_root = RESEARCH / "symbol_receipts"
    receipt_path = cache_root / f"{symbol}.json"
    if not force and receipt_path.is_file():
        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        saved_files = [Path(row["path"]) for row in saved.get("constituent_files", [])]
        covered_minutes = set(saved.get("scope_minutes", []))
        covered_waits = set(saved.get("scope_wait_min", []))
        if (
            saved.get("input_contract") == _input_contract()
            and set(scope_minutes).issubset(covered_minutes)
            and set(scope_wait_min).issubset(covered_waits)
            and saved_files
            and all(path.is_file() for path in saved_files)
        ):
            return {"symbol": symbol, "cached": True, "receipt": saved}
    old_receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
    raw, quote_receipt = _member_outcomes_for_symbol(
        symbol, members, signals, repair_missing=repair_missing
    )
    files = _write_parquet_parts(raw)
    old_files = old_receipt.get("constituent_files", []) if old_receipt.get("input_contract") == _input_contract() else []
    merged_files = {row["path"]: row for row in [*old_files, *files] if Path(row["path"]).is_file()}
    receipt = {
        "symbol": symbol,
        "input_contract": _input_contract(),
        "scope_minutes": sorted(set(old_receipt.get("scope_minutes", [])) | set(scope_minutes)),
        "scope_wait_min": sorted(set(old_receipt.get("scope_wait_min", [])) | set(scope_wait_min)),
        "member_outcome_rows": int(len(raw)),
        "constituent_files": list(merged_files.values()),
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
        "constituent_scope": "exclude BTC/ETH/SOL benchmarks and USTCUSDT from alt return/reclaim; preserve total initial breadth in signal feature",
        "schema_version": 2,
        "feature_input_stats": {
            path.name: {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for path in (MEMBERS_PATH, SIGNALS_PATH)
            if path.is_file()
        },
    }


def _merge_decision_windows(decisions: Iterable[Any], hold: pd.Timedelta) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    starts = pd.DatetimeIndex(pd.to_datetime(list(decisions), utc=True)).drop_duplicates().sort_values()
    windows: list[list[pd.Timestamp]] = []
    for start in starts:
        end = start + hold
        if windows and start <= windows[-1][1] + BAR:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return [(left, right) for left, right in windows]


def _load_benchmark_frames(signals: pd.DataFrame, *, repair_missing: bool) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    unique = signals[["known", "decision"]].drop_duplicates()
    if unique.empty:
        return {}, {}
    windows = _merge_decision_windows(unique.decision, pd.Timedelta(hours=max(HOLD_HOURS)))
    first = min(unique.known.min() - BAR, unique.decision.min() - BAR)
    last = max(end for _, end in windows)
    frames: dict[str, pd.DataFrame] = {}
    receipts: dict[str, Any] = {}
    for asset in ASSETS:
        frame, receipt = load_full_ohlc(asset + "USDT", first, last, needed_intervals=windows, repair_missing=repair_missing)
        frames[asset] = frame
        receipts[asset] = receipt
        print(f"benchmark {asset}: {len(frame):,} bars; low missing={receipt['low_missing_rows_in_returned_frame']:,}", flush=True)
    return frames, receipts


def _benchmark_partition(signals: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    keys = ["minutes", "known", "wait_min", "decision"]
    base = signals[keys].drop_duplicates().sort_values(["minutes", "known", "wait_min"]).reset_index(drop=True)
    base["known"] = pd.to_datetime(base.known, utc=True)
    base["decision"] = pd.to_datetime(base.decision, utc=True)
    event = base.assign(known_close_time=base.known - BAR, decision_close_time=base.decision - BAR)
    output = event[["minutes", "known", "wait_min", "decision", "known_close_time", "decision_close_time"]].merge(
        pd.DataFrame({"hold_hours": HOLD_HOURS}), how="cross"
    )
    for asset, frame in frames.items():
        if len(frame):
            grid = pd.date_range(frame.index[0], frame.index[-1], freq=BAR)
            dense = frame.reindex(grid)
            close = dense.close.to_numpy(dtype=float)
            before_pos = grid.get_indexer(pd.DatetimeIndex(event.known_close_time))
            after_pos = grid.get_indexer(pd.DatetimeIndex(event.decision_close_time))
            before = np.full(len(event), np.nan)
            after = np.full(len(event), np.nan)
            ok = before_pos >= 0
            before[ok] = close[before_pos[ok]]
            ok = after_pos >= 0
            after[ok] = close[after_pos[ok]]
            event[f"{asset.lower()}_observed_return_pct"] = 100 * (after / before - 1)
        else:
            event[f"{asset.lower()}_observed_return_pct"] = np.nan
        metrics = _path_metrics_many(frame, output.decision, output.hold_hours)
        names = {
            "entry_open": f"{asset.lower()}_entry_open",
            "exit_open": f"{asset.lower()}_exit_open",
            "gross_return_pct": f"{asset.lower()}_gross_return_pct",
            "net_return_pct": f"{asset.lower()}_net_return_pct",
            "mfe_pct": f"{asset.lower()}_mfe_pct",
            "mae_pct": f"{asset.lower()}_mae_pct",
            "endpoint_available": f"{asset.lower()}_endpoint_available",
            "path_complete": f"{asset.lower()}_path_complete",
            "hit5": f"{asset.lower()}_hit5",
            "hit10": f"{asset.lower()}_hit10",
            "first5_vs_drawdown5": f"{asset.lower()}_first5_vs_drawdown5",
            "first10_vs_drawdown5": f"{asset.lower()}_first10_vs_drawdown5",
            "pre_target_mae_5_pct": f"{asset.lower()}_pre_target_mae_5_pct",
            "pre_target_mae_10_pct": f"{asset.lower()}_pre_target_mae_10_pct",
        }
        metrics.rename(columns=names, inplace=True)
        output = pd.concat([output.reset_index(drop=True), metrics.reset_index(drop=True)], axis=1)
    obs_cols = [f"{asset.lower()}_observed_return_pct" for asset in frames]
    output = output.merge(event[["minutes", "known", "wait_min", *obs_cols]], on=["minutes", "known", "wait_min"], how="left", validate="many_to_one")
    output.drop(columns=["known_close_time", "decision_close_time"], inplace=True)
    if output.duplicated(["minutes", "known", "wait_min", "hold_hours"]).any():
        raise AssertionError("benchmark W/X/H join keys are not unique")
    return output


def _aggregate_wait_partition(
    minutes: int,
    wait_min: int,
    signals: pd.DataFrame,
    eligible_members: pd.DataFrame,
    symbols: Sequence[str],
    benchmark_frames: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, int]:
    """Read and aggregate one W/X slice; never hold all 15m constituent rows."""
    columns = [
        "minutes", "known", "wait_min", "hold_hours", "symbol", "gross_return_pct", "net_return_pct",
        "mfe_pct", "mae_pct", "path_complete", "endpoint_available", "hit5", "hit10",
        "first5_vs_drawdown5", "first10_vs_drawdown5", "pre_target_mae_5_pct", "pre_target_mae_10_pct",
        "observed_return_pct", "reclaimed", "reclaim_eligible", "quote_observed",
    ]
    chunks: list[pd.DataFrame] = []
    for symbol in symbols:
        folder = RESEARCH / "constituents" / f"minutes={minutes}" / f"symbol={symbol}"
        for path in sorted(folder.glob("part-*.parquet")):
            rows = pd.read_parquet(path, columns=columns, filters=[("wait_min", "==", int(wait_min))], engine="pyarrow")
            if not rows.empty:
                chunks.append(rows)
    raw = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=columns)
    raw["known"] = pd.to_datetime(raw.known, utc=True)
    keys = ["minutes", "known", "wait_min", "hold_hours"]
    if not raw.empty and raw.duplicated([*keys, "symbol"]).any():
        raise AssertionError(f"duplicate member result in W/X={minutes}/{wait_min}")
    raw["endpoint_ok"] = raw.endpoint_available.fillna(False).astype(bool)
    raw["path_ok"] = raw.path_complete.fillna(False).astype(bool)
    raw["gross_sum_item"] = raw.gross_return_pct.where(raw.endpoint_ok, 0.0)
    raw["net_sum_item"] = raw.net_return_pct.where(raw.endpoint_ok, 0.0)
    raw["gross_win_item"] = raw.endpoint_ok & raw.gross_return_pct.gt(0)
    raw["net_win_item"] = raw.endpoint_ok & raw.net_return_pct.gt(0)
    raw["mfe_sum_item"] = raw.mfe_pct.where(raw.path_ok, 0.0)
    raw["mae_sum_item"] = raw.mae_pct.where(raw.path_ok, 0.0)
    raw["hit5_item"] = raw.path_ok & raw.hit5.fillna(False).astype(bool)
    raw["hit10_item"] = raw.path_ok & raw.hit10.fillna(False).astype(bool)
    raw["first5"] = raw.first5_vs_drawdown5.eq("target_first")
    raw["down5_before5"] = raw.first5_vs_drawdown5.eq("drawdown_first")
    raw["ambiguous5"] = raw.first5_vs_drawdown5.eq("same_bar_ambiguous")
    raw["nohit5"] = raw.first5_vs_drawdown5.eq("neither")
    raw["first10"] = raw.first10_vs_drawdown5.eq("target_first")
    raw["down5_before10"] = raw.first10_vs_drawdown5.eq("drawdown_first")
    raw["ambiguous10"] = raw.first10_vs_drawdown5.eq("same_bar_ambiguous")
    raw["nohit10"] = raw.first10_vs_drawdown5.eq("neither")
    raw["pre5_sum_item"] = raw.pre_target_mae_5_pct.fillna(0.0)
    raw["pre10_sum_item"] = raw.pre_target_mae_10_pct.fillna(0.0)
    raw["pre5_count_item"] = raw.pre_target_mae_5_pct.notna()
    raw["pre10_count_item"] = raw.pre_target_mae_10_pct.notna()
    agg = raw.groupby(["known", "hold_hours"], sort=False).agg(
        member_n=("symbol", "size"), endpoint_n=("endpoint_ok", "sum"),
        gross_sum=("gross_sum_item", "sum"), net_sum=("net_sum_item", "sum"),
        gross_win_n=("gross_win_item", "sum"), net_win_n=("net_win_item", "sum"),
        path_n=("path_ok", "sum"), mfe_sum=("mfe_sum_item", "sum"), mae_sum=("mae_sum_item", "sum"),
        hit5_n=("hit5_item", "sum"), hit10_n=("hit10_item", "sum"),
        target5_first_n=("first5", "sum"), drawdown5_before_target5_n=("down5_before5", "sum"),
        ambiguous5_n=("ambiguous5", "sum"), nohit5_n=("nohit5", "sum"),
        target10_first_n=("first10", "sum"), drawdown5_before_target10_n=("down5_before10", "sum"),
        ambiguous10_n=("ambiguous10", "sum"), nohit10_n=("nohit10", "sum"),
        pre5_sum=("pre5_sum_item", "sum"), pre5_n=("pre5_count_item", "sum"),
        pre10_sum=("pre10_sum_item", "sum"), pre10_n=("pre10_count_item", "sum"),
    ).reset_index()
    agg["minutes"] = int(minutes)
    agg["wait_min"] = int(wait_min)
    expected = eligible_members.loc[eligible_members.minutes.eq(minutes)].groupby("known").size().rename("members_expected_n").reset_index()
    expected["known"] = pd.to_datetime(expected.known, utc=True)
    signals_x = signals[(signals.minutes == minutes) & (signals.wait_min == wait_min)].copy()
    feature_cols = [c for c in SIGNAL_FEATURE_COLUMNS if c in signals_x.columns]
    base = signals_x[["minutes", "known", "wait_min", "decision", *feature_cols]].drop_duplicates(["minutes", "known", "wait_min"])
    base["known"] = pd.to_datetime(base.known, utc=True)
    base = base.merge(expected, on="known", how="left", validate="one_to_one")
    base["members_expected_n"] = base.members_expected_n.fillna(0).astype("int64")
    if not agg.empty:
        check = agg.merge(base[["known", "members_expected_n"]], on="known", how="left", validate="many_to_one")
        if not check.member_n.eq(check.members_expected_n).all():
            raise AssertionError(f"member denominator mismatch in W/X={minutes}/{wait_min}")
    event = base.merge(pd.DataFrame({"hold_hours": HOLD_HOURS}), how="cross")
    event = event.merge(agg, on=["minutes", "known", "wait_min", "hold_hours"], how="left", validate="one_to_one")
    obs = raw[raw.hold_hours.eq(HOLD_HOURS[0])].groupby("known", as_index=False, sort=False).agg(
        alt_member_median_observed_return_pct=("observed_return_pct", "median"),
        price_n=("observed_return_pct", "count"), quote_observed_n=("quote_observed", "sum"),
        reclaim_n=("reclaimed", "sum"), reclaim_eligible_n=("reclaim_eligible", "sum"),
    )
    event = event.merge(obs, on="known", how="left", validate="many_to_one")
    for col in [c for c in agg.columns if c.endswith("_n") or c in {"member_n", "endpoint_n", "path_n"}]:
        if col in event:
            event[col] = event[col].fillna(0).astype("int64")
    for col in ("members_expected_n", "endpoint_n", "path_n", "reclaim_n", "reclaim_eligible_n", "quote_observed_n", "price_n"):
        if col in event:
            event[col] = event[col].fillna(0).astype("int64")
    event["gross_coverage_pct"] = 100 * event.endpoint_n / event.members_expected_n.replace(0, np.nan)
    event["path_coverage_pct"] = 100 * event.path_n / event.members_expected_n.replace(0, np.nan)
    event["basket_gross_return_pct"] = event.gross_sum / event.endpoint_n.replace(0, np.nan)
    event["basket_net_return_pct"] = event.net_sum / event.endpoint_n.replace(0, np.nan)
    event["member_win_pct"] = 100 * event.gross_win_n / event.endpoint_n.replace(0, np.nan)
    event["member_net_win_pct"] = 100 * event.net_win_n / event.endpoint_n.replace(0, np.nan)
    event["mean_mfe_pct"] = event.mfe_sum / event.path_n.replace(0, np.nan)
    event["mean_mae_pct"] = event.mae_sum / event.path_n.replace(0, np.nan)
    event["hit5_pct"] = 100 * event.hit5_n / event.path_n.replace(0, np.nan)
    event["hit10_pct"] = 100 * event.hit10_n / event.path_n.replace(0, np.nan)
    event["target5_before_drawdown5_pct"] = 100 * event.target5_first_n / (event.target5_first_n + event.drawdown5_before_target5_n).replace(0, np.nan)
    event["target10_before_drawdown5_pct"] = 100 * event.target10_first_n / (event.target10_first_n + event.drawdown5_before_target10_n).replace(0, np.nan)
    event["pre_target_mae_5_pct"] = event.pre5_sum / event.pre5_n.replace(0, np.nan)
    event["pre_target_mae_10_pct"] = event.pre10_sum / event.pre10_n.replace(0, np.nan)
    event["reclaim_fraction"] = event.reclaim_n / event.reclaim_eligible_n.replace(0, np.nan)
    event["reclaim_fraction_all_eligible"] = event.reclaim_n / event.members_expected_n.replace(0, np.nan)
    event["reclaim_quote_coverage_pct"] = 100 * event.quote_observed_n / event.members_expected_n.replace(0, np.nan)
    bench = _benchmark_partition(signals_x, benchmark_frames)
    event = event.merge(bench, on=["minutes", "known", "wait_min", "hold_hours"], how="left", validate="one_to_one")
    if event.duplicated(["minutes", "known", "wait_min", "hold_hours"]).any():
        raise AssertionError("event outcome join keys are not unique")
    return event.sort_values(["minutes", "known", "wait_min", "hold_hours"]), int(len(raw))


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
        written.append(_write_event_partition(int(minutes), int(wait_min), part))
    return written


def _write_event_partition(minutes: int, wait_min: int, event: pd.DataFrame) -> dict[str, Any]:
    PAYOFFS.mkdir(parents=True, exist_ok=True)
    path = PAYOFFS / f"event_outcomes_minutes{minutes}_wait{wait_min}.csv.gz"
    temp = path.with_suffix(path.suffix + ".tmp")
    event.to_csv(temp, index=False, compression="gzip")
    os.replace(temp, path)
    return {"path": str(path.relative_to(ROOT)), "minutes": minutes, "wait_min": wait_min, "rows": int(len(event)), "bytes": int(path.stat().st_size)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--minutes", type=int, nargs="*", choices=MINUTES)
    parser.add_argument("--wait-min", type=int, nargs="*", choices=WAIT_MINUTES)
    parser.add_argument("--symbols", nargs="*", help="optional symbol subset for a bounded run")
    parser.add_argument("--no-repair", action="store_true", help="read only registered local OHLC/cache")
    parser.add_argument("--force", action="store_true", help="recompute per-symbol payoff cache")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8:
        raise ValueError("workers must be between 1 and 8 to bound memory/API concurrency")
    if args.wait_min and set(args.wait_min) != set(WAIT_MINUTES):
        raise ValueError("resume by complete timeframe; keep all wait_min variants together")
    signals, all_members = _load_inputs(args.minutes, args.wait_min)
    excluded = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "USTCUSDT"}
    eligible_members = all_members.loc[~all_members.symbol.astype(str).isin(excluded)].copy()
    symbols = sorted(set(eligible_members.symbol.astype(str)))
    if args.symbols:
        selected = {str(x).strip().upper() for x in args.symbols}
        symbols = [s for s in symbols if s in selected]
        if not symbols:
            raise ValueError("no requested symbols are in the initial member inputs")
        eligible_members = eligible_members[eligible_members.symbol.isin(symbols)].copy()
    scope_minutes = sorted(int(v) for v in signals.minutes.unique())
    scope_wait_min = sorted(int(v) for v in signals.wait_min.unique())
    RESEARCH.mkdir(parents=True, exist_ok=True)
    PAYOFFS.mkdir(parents=True, exist_ok=True)
    contract = _input_contract()
    _write_json(PAYOFFS / "contract.json", {**contract, "member_input": str(MEMBERS_PATH.relative_to(ROOT)), "signal_input": str(SIGNALS_PATH.relative_to(ROOT)), "join_keys": ["minutes", "known", "wait_min", "hold_hours"]})
    print(f"signals={len(signals):,} initial_members={len(all_members):,} eligible_alt_members={len(eligible_members):,} symbols={len(symbols):,} workers={args.workers}", flush=True)
    finished: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _process_symbol,
                symbol,
                eligible_members[eligible_members.symbol.eq(symbol)],
                signals,
                repair_missing=not args.no_repair,
                force=args.force,
                scope_minutes=scope_minutes,
                scope_wait_min=scope_wait_min,
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
    if args.symbols:
        print("symbol subset complete; event aggregation skipped so partial member coverage is not mislabeled", flush=True)
        return
    benchmark_frames, benchmark_receipts = _load_benchmark_frames(signals, repair_missing=not args.no_repair)
    previous_manifest_path = PAYOFFS / "manifest.json"
    previous_manifest = {}
    if previous_manifest_path.is_file():
        try:
            previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            previous_manifest = {}
    current_pairs = {
        (int(row.minutes), int(row.wait_min))
        for row in signals[["minutes", "wait_min"]].drop_duplicates().itertuples(index=False)
    }
    preserved = [
        row for row in previous_manifest.get("event_partitions", [])
        if (int(row.get("minutes", -1)), int(row.get("wait_min", -1))) not in current_pairs
        and (ROOT / str(row.get("path", ""))).is_file()
    ]
    manifest = {
        "experiment_id": "exp-monday-breadth-optimization-20261108-v1",
        "input_contract": contract,
        "input_paths": {"members": str(MEMBERS_PATH.relative_to(ROOT)), "signals": str(SIGNALS_PATH.relative_to(ROOT))},
        "symbols": len(symbols),
        "signals": int(len(signals)),
        "initial_member_rows_including_benchmarks_and_USTC": int(len(all_members)),
        "eligible_alt_member_rows": int(len(eligible_members)),
        "scope_minutes_completed": sorted(set(previous_manifest.get("scope_minutes_completed", [])) | set(scope_minutes)),
        "event_outcome_rows": sum(int(row.get("rows", 0)) for row in preserved),
        "event_partitions": preserved,
        "constituent_cache_root": str((RESEARCH / "constituents").relative_to(ROOT)),
        "constituent_cache_files": sum(len(row["receipt"].get("constituent_files", [])) for row in finished if not row.get("cached")),
        "source_receipt_root": str((RESEARCH / "symbol_receipts").relative_to(ROOT)),
        "benchmark_quote_receipts_by_scope": previous_manifest.get("benchmark_quote_receipts_by_scope", []),
        "vectorbt": "Portfolio.from_orders; one independent unit-size market entry/exit per unique (symbol, decision, hold_hours); 0.05% taker per fill; cash_sharing=False",
        "fees": {"entry": TAKER_FEE, "exit": TAKER_FEE, "funding": "not modeled", "slippage": "not modeled"},
        "cash_account_interpretation": "independent unit-size trade returns and equal-weight constituent means; no shared funded multi-symbol account",
        "path_limitations": "5m OHLC does not establish intrabar order; same-bar +target/-5% is ambiguous; missing low/path bars are not inferred",
    }
    manifest["benchmark_quote_receipts_by_scope"].append({"minutes": scope_minutes, "receipts": benchmark_receipts})
    _write_json(PAYOFFS / "manifest.json", manifest)
    for minutes in sorted(signals.minutes.unique()):
        for wait_min in sorted(signals.loc[signals.minutes.eq(minutes), "wait_min"].unique()):
            event, raw_rows = _aggregate_wait_partition(
                int(minutes), int(wait_min), signals, eligible_members, symbols, benchmark_frames
            )
            if "event_columns" not in manifest:
                manifest["event_columns"] = list(event.columns)
            elif manifest["event_columns"] != list(event.columns):
                raise AssertionError("event outcome columns changed between W/X partitions")
            record = _write_event_partition(int(minutes), int(wait_min), event)
            record["constituent_raw_rows_scanned"] = int(raw_rows)
            manifest["event_partitions"].append(record)
            manifest["event_outcome_rows"] += int(len(event))
            _write_json(PAYOFFS / "manifest.json", manifest)
            print(f"wrote W={minutes} X={wait_min}: {len(event):,} event-horizon rows", flush=True)
    print(f"wrote {len(manifest['event_partitions'])} event partitions, {manifest['event_outcome_rows']:,} joined keys; research cache={RESEARCH}", flush=True)


if __name__ == "__main__":
    main()
