"""Resumable OKX history fetcher (public API, no key needed).

Fills the data gap identified in p2b: the old cache has only one full-year
series (ETH_USDT_SWAP); everything else is <6 months. This script pulls
`DAYS` days of candles for a curated list of liquid symbols into
data/kline_fetched/, in files the loader merges with the old cache.

Run ON A MACHINE WITH OKX ACCESS (the Cowork sandbox cannot reach okx.com):

    python3 -m src.data.fetch_okx            # all default symbols
    python3 -m src.data.fetch_okx --symbols BTC_USDT ETH_USDT
    python3 -m src.data.fetch_okx --days 400

Resumable: progress is kept in {SYMBOL}_{bar}.part.csv (ignored by the loader);
finished symbols are skipped on rerun. Safe to Ctrl-C and restart.

Rate limit: history-candles allows 20 req / 2 s; a global throttle spaces
requests >=0.12 s apart across all workers (<=8.3 req/s). Symbols are fetched
in parallel (--workers, default 8) so per-request network latency overlaps;
expected runtime for ~55 symbols x 400 days is under 2 hours even at ~1.5 s
per request.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.data.bars import BAR_CHOICES, normalize_bar

FETCH_DIR = Path(__file__).resolve().parents[2] / "data" / "kline_fetched"
API = "https://www.okx.com/api/v5/market/history-candles"
ARCHIVE_BASE = "https://static.okx.com/cdn/okex/traderecords/candlesticks/monthly"
DEFAULT_BAR = "15m"
PAGE_LIMIT = 100
MAX_RETRIES = 5
DEFAULT_WORKERS = 8
# Global request spacing shared by all workers: <=8.3 req/s, safely under
# OKX's 20 req / 2 s limit for history-candles.
MIN_REQUEST_INTERVAL_S = 0.12

# Curated liquid symbols expected to have >=1 year of OKX history (spot,
# matching the old cache's symbol keys), plus the one legacy swap series.
# Stablecoins/gold are excluded (loader blocks them anyway).
DEFAULT_SYMBOLS = [
    "ETH_USDT_SWAP",
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "DOGE_USDT", "ADA_USDT", "TRX_USDT", "LTC_USDT", "BCH_USDT",
    "LINK_USDT", "AVAX_USDT", "DOT_USDT", "UNI_USDT", "AAVE_USDT",
    "ATOM_USDT", "NEAR_USDT", "APT_USDT", "SUI_USDT", "FIL_USDT",
    "ICP_USDT", "XLM_USDT", "HBAR_USDT", "OP_USDT", "INJ_USDT",
    "TIA_USDT", "ORDI_USDT", "PEPE_USDT", "SHIB_USDT", "WLD_USDT",
    "ENA_USDT", "ETHFI_USDT", "JTO_USDT", "ONDO_USDT", "CRV_USDT",
    "APE_USDT", "CHZ_USDT", "CFX_USDT", "ZRO_USDT", "ID_USDT",
    "GALA_USDT", "EIGEN_USDT", "VIRTUAL_USDT", "PENGU_USDT",
    "TRUMP_USDT", "PI_USDT", "HYPE_USDT", "OKB_USDT", "ZEC_USDT",
    "TON_USDT", "ARB_USDT", "POL_USDT", "ETC_USDT", "SAND_USDT",
    "GRT_USDT", "ZK_USDT",
]


REQUEST_HEADERS = {
    # OKX's WAF rejects the default Python-urllib user agent with 403.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}


class ArchiveFetchError(RuntimeError):
    """Raised when an official archive violates the frozen time/schema contract."""


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _month(value: str) -> pd.Timestamp:
    """Parse one YYYY-MM token as an inclusive UTC month start."""

    try:
        stamp = pd.Timestamp(f"{value}-01T00:00:00Z")
    except ValueError as exc:
        raise ArchiveFetchError(f"invalid archive month: {value}") from exc
    if stamp.strftime("%Y-%m") != value:
        raise ArchiveFetchError(f"invalid archive month: {value}")
    return stamp


def archive_months(
    start: str,
    end: str,
    *,
    max_exclusive: object,
) -> list[pd.Timestamp]:
    """Return complete OKX UTC+8 archive months below ``max_exclusive``."""

    first = _month(start)
    last = _month(end)
    boundary = _utc(max_exclusive)
    if last < first:
        raise ArchiveFetchError("archive month range is descending")
    months: list[pd.Timestamp] = []
    cursor = first
    while cursor <= last:
        next_month = cursor + pd.offsets.MonthBegin(1)
        archive_end_utc = next_month - pd.Timedelta(hours=8)
        if archive_end_utc > boundary:
            raise ArchiveFetchError(
                f"archive month {cursor:%Y-%m} crosses exclusive boundary {boundary.isoformat()}"
            )
        months.append(cursor)
        cursor = next_month
    return months


def archive_url(symbol: str, month: pd.Timestamp) -> str:
    """Return the official OKX monthly 1m-candlestick archive URL."""

    instrument = symbol.replace("_", "-")
    ym = month.strftime("%Y%m")
    label = month.strftime("%Y-%m")
    filename = f"{instrument}-candlesticks-{label}.zip"
    return f"{ARCHIVE_BASE}/{ym}/{filename}?v=999"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def aggregate_archive_bytes(
    payload: bytes,
    *,
    symbol: str,
    month: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Aggregate one official monthly 1m ZIP into complete UTC 15m candles."""

    month = _utc(month)
    # Official monthly files use the exchange's UTC+8 calendar.  For example,
    # the file named 2024-01 spans 2023-12-31 16:00Z through
    # 2024-01-31 15:59Z.  Output candles remain aligned to UTC 15-minute bins.
    archive_start = month - pd.Timedelta(hours=8)
    archive_end = month + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=8)
    expected_instrument = symbol.replace("_", "-")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ArchiveFetchError("monthly archive must contain exactly one CSV")
            member = members[0]
            csv_bytes = archive.read(member)
    except zipfile.BadZipFile as exc:
        raise ArchiveFetchError("official archive is not a valid ZIP") from exc

    frame = pd.read_csv(io.BytesIO(csv_bytes))
    required = {
        "instrument_name",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "open_time",
        "confirm",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ArchiveFetchError(f"archive schema missing columns: {missing}")
    if frame.empty:
        raise ArchiveFetchError("archive CSV is empty")
    instruments = set(frame["instrument_name"].astype(str))
    if instruments != {expected_instrument}:
        raise ArchiveFetchError(
            f"archive instrument drift: expected {expected_instrument}, got {sorted(instruments)}"
        )

    numeric_columns = ("open", "high", "low", "close", "vol", "open_time")
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.sort_values("open_time", kind="mergesort").reset_index(drop=True)
    if frame["open_time"].duplicated().any():
        raise ArchiveFetchError("archive contains duplicate one-minute timestamps")
    archive_start_ms = int(archive_start.value // 1_000_000)
    archive_end_ms = int(archive_end.value // 1_000_000)
    if (
        int(frame["open_time"].min()) < archive_start_ms
        or int(frame["open_time"].max()) >= archive_end_ms
    ):
        raise ArchiveFetchError("archive contains a timestamp outside its named UTC+8 month")
    if bool((frame[["open", "high", "low", "close"]] <= 0.0).any().any()):
        raise ArchiveFetchError("archive contains non-positive OHLC")
    if bool((frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
        raise ArchiveFetchError("archive high is below a candle body")
    if bool((frame["low"] > frame[["open", "close"]].min(axis=1)).any()):
        raise ArchiveFetchError("archive low is above a candle body")

    frame["bucket_ms"] = (frame["open_time"].astype("int64") // 900_000) * 900_000
    grouped = frame.groupby("bucket_ms", sort=True)
    counts = grouped.size()
    first_ts = grouped["open_time"].min()
    last_ts = grouped["open_time"].max()
    complete = (counts == 15) & ((last_ts - first_ts) == 14 * 60_000)
    complete_buckets = set(int(value) for value in counts.index[complete])
    kept = frame[frame["bucket_ms"].isin(complete_buckets)]
    aggregated = (
        kept.groupby("bucket_ms", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("vol", "sum"),
        )
        .reset_index()
        .rename(columns={"bucket_ms": "ts"})
    )
    aggregated["open_time"] = pd.to_datetime(aggregated["ts"], unit="ms", utc=True)
    aggregated = aggregated[["ts", "open", "high", "low", "close", "volume", "open_time"]]
    audit: dict[str, object] = {
        "month": month.strftime("%Y-%m"),
        "archive_calendar_timezone": "UTC+08:00",
        "archive_window_start_utc": archive_start.isoformat(),
        "archive_window_end_exclusive_utc": archive_end.isoformat(),
        "zip_sha256": _sha256_bytes(payload),
        "csv_member": member,
        "csv_sha256": _sha256_bytes(csv_bytes),
        "raw_1m_rows": int(len(frame)),
        "complete_15m_rows": int(len(aggregated)),
        "incomplete_15m_groups_dropped": int((~complete).sum()),
        "confirm_values": sorted(str(value) for value in frame["confirm"].unique()),
        "first_raw_ts": int(frame["open_time"].min()),
        "last_raw_ts": int(frame["open_time"].max()),
    }
    return aggregated, audit


def _request_archive(url: str) -> bytes | None:
    """Download one public archive; return ``None`` only for a real 404."""

    for attempt in range(MAX_RETRIES):
        try:
            request = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt + 1 == MAX_RETRIES:
                raise ArchiveFetchError(f"archive HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 == MAX_RETRIES:
                raise ArchiveFetchError(f"archive download failed: {url}") from exc
        time.sleep(2**attempt)
    raise AssertionError("unreachable archive retry state")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def fetch_archive_symbol(
    symbol: str,
    *,
    months: list[pd.Timestamp],
    output_dir: Path,
    max_exclusive: object,
) -> dict[str, object]:
    """Fetch, aggregate and atomically publish one symbol's safe monthly prefix."""

    output_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "symbol": symbol,
        "months_requested": [month.strftime("%Y-%m") for month in months],
        "max_exclusive": _utc(max_exclusive).isoformat(),
        "archive_calendar_timezone": "UTC+08:00",
    }
    audit_path = output_dir / f"archive_{symbol}.json"
    if audit_path.exists():
        prior = json.loads(audit_path.read_text(encoding="utf-8"))
        legacy_contract = dict(contract)
        legacy_contract.pop("archive_calendar_timezone")
        if prior.get("contract") == legacy_contract:
            # Early task-local receipts were written before the official
            # archive's UTC+8 calendar boundary was made explicit.  Their
            # output rows are already UTC-aligned and strictly pre-boundary;
            # promote only the receipt schema after verifying the file below.
            prior["contract"] = contract
            output = prior.get("output_path")
            if output is not None:
                output_path = output_dir / Path(str(output)).name
                if not output_path.exists() or hashlib.sha256(output_path.read_bytes()).hexdigest() != str(
                    prior.get("output_sha256")
                ):
                    raise ArchiveFetchError(f"legacy archive output drift for {symbol}")
            _write_json(audit_path, prior)
        if prior.get("contract") == contract and prior.get("status") in {"complete", "no_data"}:
            output = prior.get("output_path")
            if output is None or (output_dir / Path(str(output)).name).exists():
                return prior
        raise ArchiveFetchError(f"archive resume contract drift for {symbol}")

    monthly_frames: list[pd.DataFrame] = []
    monthly_audits: list[dict[str, object]] = []
    missing_months: list[str] = []
    for month in months:
        url = archive_url(symbol, month)
        payload = _request_archive(url)
        if payload is None:
            missing_months.append(month.strftime("%Y-%m"))
            continue
        frame, audit = aggregate_archive_bytes(payload, symbol=symbol, month=month)
        audit["url"] = url
        monthly_frames.append(frame)
        monthly_audits.append(audit)

    result: dict[str, object] = {
        "contract": contract,
        "status": "no_data" if not monthly_frames else "complete",
        "months_available": [str(row["month"]) for row in monthly_audits],
        "months_missing": missing_months,
        "monthly_audits": monthly_audits,
        "holdout_ohlcv_rows_materialized": 0,
        "output_path": None,
    }
    if monthly_frames:
        combined = pd.concat(monthly_frames, ignore_index=True)
        combined = combined.sort_values("ts", kind="mergesort").drop_duplicates("ts", keep="first")
        boundary_ms = int(_utc(max_exclusive).value // 1_000_000)
        if int(combined["ts"].max()) >= boundary_ms:
            raise ArchiveFetchError(f"aggregated archive crossed exclusive boundary for {symbol}")
        filename = f"okx_{symbol}_15m_{len(combined)}.csv"
        output_path = output_dir / filename
        temporary = output_path.with_suffix(".csv.part")
        combined.to_csv(temporary, index=False)
        os.replace(temporary, output_path)
        result.update(
            {
                "output_path": str(output_path),
                "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "rows": int(len(combined)),
                "first_time": str(combined["open_time"].iloc[0]),
                "last_time": str(combined["open_time"].iloc[-1]),
            }
        )
    _write_json(audit_path, result)
    return result


def fetch_archive_universe(
    symbols: list[str],
    *,
    months: list[pd.Timestamp],
    output_dir: Path,
    max_exclusive: object,
    workers: int,
) -> dict[str, object]:
    """Fetch multiple symbols with symbol-level resumability."""

    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                fetch_archive_symbol,
                symbol,
                months=months,
                output_dir=output_dir,
                max_exclusive=max_exclusive,
            ): symbol
            for symbol in symbols
        }
        for index, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            result = future.result()
            results.append(result)
            print(
                f"archive [{index}/{len(futures)}] {symbol}: "
                f"{result['status']} rows={result.get('rows', 0)}",
                flush=True,
            )
    summary = {
        "symbols_requested": len(symbols),
        "symbols_complete": sum(row["status"] == "complete" for row in results),
        "symbols_no_data": sum(row["status"] == "no_data" for row in results),
        "rows": sum(int(row.get("rows", 0)) for row in results),
        "months_downloaded": sum(len(row["months_available"]) for row in results),
        "holdout_ohlcv_rows_materialized": 0,
        "results": sorted(results, key=lambda row: str(row["contract"]["symbol"])),
    }
    _write_json(output_dir / "archive_fetch_summary.json", summary)
    return summary


_rate_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    while True:
        with _rate_lock:
            now = time.monotonic()
            wait = _last_request_at + MIN_REQUEST_INTERVAL_S - now
            if wait <= 0:
                _last_request_at = now
                return
        time.sleep(wait)


def _request(url: str) -> dict:
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            print(f"    retry {attempt + 1}/{MAX_RETRIES} in {wait}s ({exc})", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def _finished_file(symbol: str, bar: str) -> Path | None:
    hits = sorted(FETCH_DIR.glob(f"okx_{symbol}_{bar}_*.csv"))
    return hits[-1] if hits else None


def fetch_symbol(symbol: str, start_ms: int, bar: str = DEFAULT_BAR) -> None:
    bar = normalize_bar(bar)
    inst_id = symbol.replace("_", "-")
    part = FETCH_DIR / f"{symbol}_{bar}.part.csv"
    rows: list[list] = []
    oldest_ms: int | None = None
    if part.exists():  # resume: reload progress, continue from oldest ts
        with part.open() as fh:
            rows = [r for r in csv.reader(fh)][1:]
        if rows:
            oldest_ms = min(int(r[0]) for r in rows)
            print(f"  {symbol}: resuming at {datetime.fromtimestamp(oldest_ms / 1e3, tz=timezone.utc):%Y-%m-%d}", flush=True)

    header = ["ts", "open", "high", "low", "close", "volume", "open_time"]
    if not part.exists():
        part.write_text(",".join(header) + "\n")

    while oldest_ms is None or oldest_ms > start_ms:
        url = f"{API}?instId={inst_id}&bar={bar}&limit={PAGE_LIMIT}"
        if oldest_ms is not None:
            url += f"&after={oldest_ms}"
        payload = _request(url)
        if payload.get("code") != "0":
            print(f"  {symbol}: API error: {payload.get('msg')} -- skipping", flush=True)
            break
        page = payload.get("data") or []
        if not page:
            break  # listed later than start date: no more history
        new_rows = []
        for r in page:  # [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
            ts = int(r[0])
            if len(r) > 8 and r[8] == "0":
                continue  # unconfirmed candle
            ot = datetime.fromtimestamp(ts / 1e3, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
            new_rows.append([ts, r[1], r[2], r[3], r[4], r[5], ot])
        with part.open("a", newline="") as fh:
            csv.writer(fh).writerows(new_rows)
        rows.extend(new_rows)
        oldest_ms = int(page[-1][0])

    if not rows:
        part.unlink(missing_ok=True)
        print(f"  {symbol}: no data", flush=True)
        return
    # dedupe + sort, write final file named to match the loader's pattern
    uniq = {int(r[0]): r for r in rows}
    final_rows = [uniq[k] for k in sorted(uniq)]
    out = FETCH_DIR / f"okx_{symbol}_{bar}_{len(final_rows)}.csv"
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(final_rows)
    part.unlink(missing_ok=True)
    first = datetime.fromtimestamp(sorted(uniq)[0] / 1e3, tz=timezone.utc)
    print(f"  {symbol}: done, {len(final_rows)} bars from {first:%Y-%m-%d} -> {out.name}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--bar", default=DEFAULT_BAR, choices=BAR_CHOICES,
                        help="candle timeframe (filenames and API both follow it)")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="write to a different directory (e.g. data/kline_deep for "
                             "deep-history pulls kept apart from the live universe)")
    parser.add_argument(
        "--archive-monthly-start",
        help="inclusive YYYY-MM; enables official monthly 1m archive aggregation to 15m",
    )
    parser.add_argument("--archive-monthly-end", help="inclusive YYYY-MM")
    parser.add_argument(
        "--archive-max-exclusive",
        help="hard UTC timestamp boundary; every requested archive month must end before it",
    )
    parser.add_argument(
        "--archive-source-audit",
        type=Path,
        help="derive non-deep, non-empty symbols from a frozen candidate source_audit.json",
    )
    args = parser.parse_args()
    bar = normalize_bar(args.bar)
    archive_mode = args.archive_monthly_start is not None
    if archive_mode:
        if bar != "15m":
            parser.error("official monthly archive mode is fixed to 15m output")
        if not args.archive_monthly_end or not args.archive_max_exclusive:
            parser.error(
                "archive mode requires --archive-monthly-end and --archive-max-exclusive"
            )
        if args.out_dir is None:
            parser.error("archive mode requires an explicit --out-dir")
        symbols = list(args.symbols or [])
        if args.archive_source_audit is not None:
            source_rows = json.loads(args.archive_source_audit.read_text(encoding="utf-8"))
            derived = [
                str(row["symbol"])
                for row in source_rows
                if int(row.get("rows_materialized", 0)) > 0
                and "/kline_deep/" not in str(row.get("source_path", ""))
            ]
            symbols.extend(derived)
        symbols = sorted(set(symbols))
        if not symbols:
            parser.error("archive mode requires --symbols or --archive-source-audit")
        months = archive_months(
            args.archive_monthly_start,
            args.archive_monthly_end,
            max_exclusive=args.archive_max_exclusive,
        )
        summary = fetch_archive_universe(
            symbols,
            months=months,
            output_dir=args.out_dir.resolve(),
            max_exclusive=args.archive_max_exclusive,
            workers=args.workers,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    symbols = list(args.symbols or DEFAULT_SYMBOLS)
    if args.out_dir is not None:
        global FETCH_DIR
        FETCH_DIR = args.out_dir
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
    pending: list[str] = []
    for symbol in symbols:
        done = _finished_file(symbol, bar)
        if done is not None:
            print(f"{symbol}: already fetched ({done.name})", flush=True)
        else:
            pending.append(symbol)
    print(f"fetching {len(pending)} symbols with {args.workers} workers", flush=True)
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_symbol, s, start_ms, bar): s for s in pending}
        for n, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                future.result()
            except RuntimeError as exc:
                failed += 1
                print(f"  {symbol}: FAILED: {exc} (rerun to resume)", flush=True)
            print(f"[{n}/{len(pending)} finished]", flush=True)
    if failed:
        print(f"{failed} symbols failed -- rerun to resume them", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
