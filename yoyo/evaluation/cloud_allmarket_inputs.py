"""Freeze OKX crypto-perpetual inputs for the cloud V1.7 all-market screen.

The universe is the official OKX SWAP snapshot filtered to live USDT contracts
with ``instCategory=1`` at the capture time and ``listTime <= cutoff``.  The
screen's cutoff is fixed at 2026-11-08T12:00:00Z. Existing project OKX 15m/1h
archives are reused first; official history-candles pages fill older/newer
coverage. Delisted ICX/LRC are a separately identified archive cohort, backed
by OKX delisting notices and frozen OKX candles. Same-name current category-3
QNT/WEN instruments are excluded.

Outputs are resumable and immutable per raw page. CSV indices are integer UTC
milliseconds. 12H rows use native 12Hutc candles; a complete 1H aggregate is
used only where native 12Hutc is absent. 1H rows are real native bars or
aggregates of four complete 15m candles, never interpolated.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import gzip
import json
import math
import sqlite3
import threading
import time

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1"
TOP20_EXP = ROOT / "experiments/active/exp-cloud-top20-peak10r-20261108-v1"
TOP20_RANKING = ROOT / "experiments/active/exp-top20-1618-short-marks-20261031-v1/ranking.json"
TOP20_INPUTS = TOP20_EXP / "inputs"
H1_ARCHIVE = ROOT / "experiments/active/exp-eth-h1-gainers-mfe16-20261021-v1/inputs/h1"
INSTRUMENT_ARCHIVE = ROOT / "experiments/active/exp-eth-h1-gainers-mfe16-20261021-v1/inputs/instruments_swap_20261021.json"
LOCAL_KLINES = ROOT / "data/crypto/market/kline_fetched"
H1_HIGH = ROOT / "data/crypto/market/exp-ma-profit3r-20260922-v1-inputs/high"
PREHOLDOUT_15M = ROOT / "data/crypto/market/kline_preholdout_archive15m"
PREHOLDOUT_OLD_15M = ROOT / "data/crypto/market/kline_preholdout_archive15m_202109_202306"
CANDLE_CACHE = Path.home() / "Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3"
API = "https://www.okx.com/api/v5"
CUTOFF = int(pd.Timestamp("2026-11-08T12:00:00Z").timestamp() * 1000)
HOUR = 3_600_000
STEP_12H = 12 * HOUR
OHLC = ["open", "high", "low", "close"]
PAGE_LIMIT = 300
WORKERS = 10
MIN_REQUEST_INTERVAL = 0.13  # 7.7 req/s shared process budget; OKX history limit is higher.
TOP20_SYMBOLS = set(json.loads(TOP20_RANKING.read_text())["top20"][i]["symbol"]
                    for i in range(20))
DELISTED_CRYPTO = {
    "ICX": {
        "notice": "https://www.okx.com/help/okx-to-delist-perpetual-futures-for-icxusdt",
        "delist_time_ms": int(pd.Timestamp("2026-09-17T08:00:00Z").timestamp() * 1000),
        "basis": "OKX official delisting notice + archived OKX USDT-swap OHLC",
    },
    "LRC": {
        "notice": "https://www.okx.com/help/okx-to-delist-perpetual-futures-for-lrcusdt",
        "delist_time_ms": int(pd.Timestamp("2026-07-20T08:00:00Z").timestamp() * 1000),
        "basis": "OKX official delisting notice + archived OKX USDT-swap OHLC",
    },
}
EXCLUDED_SAME_NAME = {"QNT", "WEN"}

_rate_lock = threading.Lock()
_next_request = 0.0
_session_local = threading.local()


def dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def session() -> requests.Session:
    if not hasattr(_session_local, "session"):
        _session_local.session = requests.Session()
    return _session_local.session


def get_json(endpoint: str, params: dict) -> dict:
    global _next_request
    for attempt in range(6):
        with _rate_lock:
            now = time.monotonic()
            delay = max(0.0, _next_request - now)
            _next_request = max(now, _next_request) + MIN_REQUEST_INTERVAL
        if delay:
            time.sleep(delay)
        try:
            response = session().get(API + endpoint, params=params, timeout=35)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise RuntimeError(f"OKX {endpoint} {params}: {payload}")
            return payload
        except (requests.RequestException, ValueError, RuntimeError):
            if attempt == 5:
                raise
            time.sleep(min(8.0, 0.5 * (2 ** attempt)))
    raise AssertionError("unreachable")


def symbol_of(inst_id: str) -> str:
    return inst_id.split("-", 1)[0]


def raw_instruments() -> tuple[list[dict], str]:
    payload = get_json("/public/instruments", {"instType": "SWAP"})
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return payload["data"], fetched


def load_old_crypto_snapshot() -> list[dict]:
    payload = json.loads(INSTRUMENT_ARCHIVE.read_text())
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def current_universe(rows: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    by_id = {r.get("instId"): r for r in rows if r.get("instId")}
    live_crypto = [r for r in rows if r.get("instType") == "SWAP"
                   and r.get("instCategory") == "1" and r.get("state") == "live"
                   and str(r.get("instId", "")).endswith("-USDT-SWAP")
                   and r.get("listTime") and int(r["listTime"]) <= CUTOFF]
    return live_crypto, by_id


def build_universe(rows: list[dict], fetched_at: str) -> tuple[pd.DataFrame, dict]:
    live, by_id = current_universe(rows)
    old = load_old_crypto_snapshot()
    by_id_old = {r.get("instId"): r for r in old if r.get("instId")}
    # Preserve only historical category-1 rows that disappeared or changed class.
    live_ids = {r["instId"] for r in live}
    archived = []
    for inst_id, item in by_id_old.items():
        if (item.get("instType") == "SWAP" and item.get("instCategory") == "1"
                and str(inst_id).endswith("-USDT-SWAP") and inst_id not in live_ids
                and item.get("listTime") and int(item["listTime"]) <= CUTOFF):
            archived.append(item)

    records = []
    for item in sorted(live, key=lambda x: x["instId"]):
        symbol = symbol_of(item["instId"])
        is_top = symbol in TOP20_SYMBOLS
        records.append(dict(symbol=symbol, instId=item["instId"],
            list_time=int(item["listTime"]), is_current=True,
            cohort="top20_current" if is_top else "current_crypto_non_top20",
            top20=is_top, asset_class="crypto", category_evidence="OKX instCategory=1",
            archive_basis="", current_inst_category=item.get("instCategory", ""),
            rest_allowed=True, archive_end_ms=CUTOFF))

    for item in sorted(archived, key=lambda x: x["instId"]):
        symbol = symbol_of(item["instId"])
        if symbol in {r["symbol"] for r in records}:
            continue
        records.append(dict(symbol=symbol, instId=item["instId"],
            list_time=int(item["listTime"]), is_current=False,
            cohort="archived_crypto_official_snapshot", top20=False, asset_class="crypto",
            category_evidence="OKX archived instruments snapshot instCategory=1 (2026-10-21)",
            archive_basis="2026-10-21 official instrument snapshot; archive history required",
            current_inst_category=by_id.get(item["instId"], {}).get("instCategory", ""),
            rest_allowed=(not by_id.get(item["instId"]) or by_id[item["instId"]].get("instCategory") == "1"),
            archive_end_ms=(min(CUTOFF, int(by_id[item["instId"]]["listTime"]))
                            if by_id.get(item["instId"]) and by_id[item["instId"]].get("listTime")
                            and by_id[item["instId"]].get("instCategory") != "1" else CUTOFF)))

    # Two delisted crypto swaps have official notices and local OKX candles, but no
    # old instrument-listTime receipt. Use first observed bar as a lower bound and
    # label the distinction explicitly instead of inventing an exchange listing time.
    known_categories = {symbol_of(r["instId"]): str(r.get("instCategory", "")) for r in rows
                        if r.get("instId", "").endswith("-USDT-SWAP")}
    for symbol, meta in DELISTED_CRYPTO.items():
        if symbol in live_ids or symbol in known_categories and known_categories[symbol] != "1":
            continue
        observed = earliest_local_ms(symbol)
        if observed is None:
            continue
        records.append(dict(symbol=symbol, instId=f"{symbol}-USDT-SWAP", list_time=observed,
            is_current=False, cohort="archived_crypto_delisted_notice", top20=False,
            asset_class="crypto", category_evidence="OKX perpetual delisting notice; historical category snapshot unavailable",
            archive_basis=meta["basis"] + f"; notice={meta['notice']}; list_time is earliest observed archive bar, not official listing time",
            current_inst_category="", rest_allowed=False, archive_end_ms=meta["delist_time_ms"]))

    df = pd.DataFrame(records).drop_duplicates("instId", keep="first")
    df["top20"] = df["top20"].astype(bool)
    df = df.sort_values(["is_current", "top20", "symbol"], ascending=[False, False, True]).reset_index(drop=True)
    meta = {
        "cutoff_utc": pd.Timestamp(CUTOFF, unit="ms", tz="UTC").isoformat(),
        "official_instruments_fetched_at_utc": fetched_at,
        "official_endpoint": API + "/public/instruments?instType=SWAP",
        "official_swaps_returned": len(rows),
        "official_category_counts_usdt_swap": {},
        "current_crypto_usdt_swap_count": len(live),
        "current_top20_count": sum(symbol_of(r["instId"]) in TOP20_SYMBOLS for r in live),
        "current_non_top20_count": sum(symbol_of(r["instId"]) not in TOP20_SYMBOLS for r in live),
        "archived_category1_snapshot_candidates": len(archived),
        "archived_notice_crypto_candidates": ["ICX", "LRC"],
        "excluded_same_name_noncrypto": [],
        "delisted_category_snapshot": str(INSTRUMENT_ARCHIVE.relative_to(ROOT)),
        "top20_inputs_reused_from": str(TOP20_INPUTS.relative_to(ROOT)),
        "scope_note": "Current scope is official live instCategory=1 USDT swaps at capture, listTime<=cutoff; archive cohort uses only official category-1 snapshot candidates or ICX/LRC official delisting notices. Category-3 QNT/WEN are not admitted.",
    }
    cats = {}
    for r in rows:
        if str(r.get("instId", "")).endswith("-USDT-SWAP"):
            cat = str(r.get("instCategory", "missing"))
            cats[cat] = cats.get(cat, 0) + 1
    meta["official_category_counts_usdt_swap"] = cats
    for symbol in sorted(EXCLUDED_SAME_NAME):
        item = next((r for r in rows if r.get("instId") == f"{symbol}-USDT-SWAP"), None)
        if item:
            meta["excluded_same_name_noncrypto"].append({"symbol": symbol,
                "instId": item["instId"], "instCategory": item.get("instCategory"),
                "listTime": item.get("listTime"), "reason": "current OKX instrument is not category 1; no verified historical category-1 snapshot"})
    return df, meta


def local_files(symbol: str) -> tuple[list[Path], list[Path]]:
    h1 = []
    for directory, pattern in [
        (H1_ARCHIVE, f"okx_{symbol}_USDT_SWAP_1H_*.csv"),
        (H1_HIGH, f"okx_{symbol}-USDT-SWAP_60m.csv"),
    ]:
        h1.extend(sorted(directory.glob(pattern)) if directory.exists() else [])
    fifteen = []
    for directory, pattern in [
        (LOCAL_KLINES, f"okx_{symbol}_USDT_SWAP_15m_*.csv"),
        (PREHOLDOUT_15M, f"okx_{symbol}_USDT_SWAP_15m_*.csv"),
        (PREHOLDOUT_OLD_15M, f"okx_{symbol}_USDT_SWAP_15m_*.csv"),
    ]:
        candidates = sorted(directory.glob(pattern),
                            key=lambda p: int(p.stem.rsplit("_", 1)[-1]), reverse=True) if directory.exists() else []
        fifteen.extend(candidates[:1])
    return h1, fifteen


def cache_frame(inst_id: str, bar: str, step: int, cutoff: int) -> pd.DataFrame:
    if not CANDLE_CACHE.exists():
        return pd.DataFrame(columns=[*OHLC, "source"])
    key = f"https://www.okx.com|{inst_id}|{bar}"
    try:
        with sqlite3.connect(CANDLE_CACHE.as_uri() + "?mode=ro", uri=True, timeout=25) as db:
            raw_rows = [json.loads(row[0]) for row in db.execute(
                "SELECT raw FROM candles WHERE key=? AND t<? ORDER BY t", (key, cutoff))]
    except sqlite3.Error:
        return pd.DataFrame(columns=[*OHLC, "source"])
    rows = [[int(r[0]), *map(float, r[1:5]), f"shared_OKX_cache_{bar}"]
            for r in raw_rows if len(r) >= 9 and str(r[8]) == "1" and int(r[0]) + step <= cutoff]
    if not rows:
        return pd.DataFrame(columns=[*OHLC, "source"])
    out = pd.DataFrame(rows, columns=["time", *OHLC, "source"]).set_index("time")
    out.index = out.index.astype("int64")
    return out[~out.index.duplicated(keep="first")].sort_index(kind="stable")


def earliest_local_ms(symbol: str) -> int | None:
    h1, fifteen = local_files(symbol)
    values = []
    for path in [*h1, *fifteen]:
        try:
            frame = pd.read_csv(path, usecols=["ts"], dtype={"ts": "int64"})
            if len(frame):
                values.append(int(frame.ts.min()))
        except (OSError, ValueError, KeyError):
            continue
    return min(values) if values else None


def read_local_sources(symbol: str, inst_id: str, cutoff: int) -> tuple[pd.DataFrame, dict]:
    direct_files, fifteen_files = local_files(symbol)
    cached = cache_frame(inst_id, "1H", HOUR, cutoff)
    frames = [cached] if len(cached) else []
    source_info = {"h1_archive_files": [], "fifteen_minute_files": [],
                   "h1_archive_rows": 0, "shared_cache_rows": len(cached),
                   "complete_15m_aggregated_hours": 0,
                   "overlap_differences_vs_15m": 0}
    for path in direct_files:
        raw = pd.read_csv(path, float_precision="round_trip")
        if "ts" not in raw:
            continue
        frame = raw.set_index("ts").loc[:, OHLC].astype(float)
        frame.index = frame.index.astype("int64")
        frame = frame[(frame.index + HOUR <= cutoff)].sort_index()
        frame["source"] = "frozen_OKX_1H_20261021"
        frames.append(frame)
        source_info["h1_archive_files"].append(str(path.relative_to(ROOT)))
        source_info["h1_archive_rows"] += len(frame)
    for path in fifteen_files:
        raw = pd.read_csv(path, float_precision="round_trip")
        if not {"ts", *OHLC}.issubset(raw.columns):
            continue
        raw = raw.set_index("ts").loc[:, OHLC].astype(float)
        raw.index = raw.index.astype("int64")
        raw = raw[(raw.index + 15 * 60_000 <= cutoff)].sort_index()
        raw = raw[~raw.index.duplicated(keep="last")]
        groups = raw.groupby((raw.index // HOUR) * HOUR, sort=True)
        agg = groups.agg(open=("open", "first"), high=("high", "max"),
                         low=("low", "min"), close=("close", "last"))
        counts = groups.size()
        # Keep only hours with all four aligned 15m inputs.
        complete = []
        if (raw.index % (15 * 60_000)).any():
            raw = raw[(raw.index % (15 * 60_000)) == 0]
            groups = raw.groupby((raw.index // HOUR) * HOUR, sort=True)
            agg = groups.agg(open=("open", "first"), high=("high", "max"),
                             low=("low", "min"), close=("close", "last"))
            counts = groups.size()
        complete = [int(t) for t in agg.index if int(counts.loc[t]) == 4]
        agg = agg.loc[complete]
        agg["source"] = "complete_OKX_15m_aggregation"
        frames.append(agg)
        source_info["fifteen_minute_files"].append(str(path.relative_to(ROOT)))
        source_info["complete_15m_aggregated_hours"] += len(agg)
    if not frames:
        return pd.DataFrame(columns=[*OHLC, "source"]), source_info
    direct = pd.concat(frames).sort_index(kind="stable")
    direct = direct[~direct.index.duplicated(keep="first")]
    return direct, source_info


def read_cached_page(path: Path):
    if path.exists():
        with gzip.open(path, "rt") as handle:
            return json.load(handle)
    return None


def fetch_pages(folder: Path, inst_id: str, bar: str, step: int,
                start_ms: int, end_ms: int, known: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Fetch missing leading/trailing ranges, reusing a frozen known interval."""
    raw_dir = folder / "raw" / bar
    raw_dir.mkdir(parents=True, exist_ok=True)
    if start_ms > end_ms:
        return pd.DataFrame(columns=[*OHLC, "source"], dtype=float), {"pages": 0, "rows": 0}
    confirmed_known = known[(known.index >= start_ms) & (known.index <= end_ms)] if len(known) else known
    first_known = int(confirmed_known.index.min()) if len(confirmed_known) else None
    last_known = int(confirmed_known.index.max()) if len(confirmed_known) else None
    ranges = []
    if first_known is None:
        ranges.append((start_ms, end_ms))
    else:
        if start_ms < first_known:
            ranges.append((start_ms, first_known - step))
        if last_known < end_ms:
            ranges.append((last_known + step, end_ms))
        # Repair any frozen-source holes too. The final Monday completeness
        # receipt is based on actual hourly timestamps, never on a time span.
        known_times = np.sort(confirmed_known.index.to_numpy(dtype="int64"))
        for prev_t, next_t in zip(known_times[:-1], known_times[1:]):
            if next_t - prev_t > step:
                ranges.append((int(prev_t + step), int(next_t - step)))
    rows, pages = [], 0
    for left, right in ranges:
        if left > right:
            continue
        cursor = right + step
        while cursor > left:
            page_path = raw_dir / f"{cursor}.json.gz"
            page = read_cached_page(page_path)
            if page is None:
                payload = get_json("/market/history-candles", {
                    "instId": inst_id, "bar": bar, "limit": str(PAGE_LIMIT), "after": str(cursor)})
                page = payload.get("data", [])
                temp = page_path.with_suffix(page_path.suffix + ".tmp")
                with gzip.open(temp, "wt") as handle:
                    json.dump(page, handle, separators=(",", ":"))
                temp.replace(page_path)
            pages += 1
            if not page:
                break
            for row in page:
                if str(row[8]) != "1":
                    continue
                t = int(row[0])
                if t + step > CUTOFF or t < start_ms or t > end_ms:
                    continue
                rows.append([t, *map(float, row[1:5]), f"OKX_history_{bar}"])
            oldest = min(int(r[0]) for r in page)
            if oldest >= cursor:
                raise ValueError(f"{inst_id} {bar}: cursor did not move ({cursor} -> {oldest})")
            cursor = oldest
    if not rows:
        return pd.DataFrame(columns=[*OHLC, "source"], dtype=float), {"pages": pages, "rows": 0}
    result = pd.DataFrame(rows, columns=["time", *OHLC, "source"]).set_index("time")
    result.index = result.index.astype("int64")
    result = result[~result.index.duplicated(keep="first")].sort_index(kind="stable")
    return result, {"pages": pages, "rows": len(result)}


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[*OHLC, "source"])
    out = frame.loc[:, [*OHLC, "source"]].copy().sort_index(kind="stable")
    out.index = out.index.astype("int64")
    out = out[~out.index.duplicated(keep="first")]
    out = out[(out.index >= 0)]
    if out[OHLC].isna().any().any():
        raise ValueError("OHLC contains null")
    invalid = ((out.low <= 0) | (out.low > out[OHLC].min(axis=1))
               | (out.high < out[OHLC].max(axis=1)))
    if invalid.any():
        raise ValueError(f"invalid OHLC rows={int(invalid.sum())}")
    return out


def aggregate(frame: pd.DataFrame, step: int, n: int, source: str) -> pd.DataFrame:
    rows = frame[OHLC].copy()
    groups = rows.groupby((rows.index // step) * step, sort=True)
    agg = groups.agg(open=("open", "first"), high=("high", "max"),
                     low=("low", "min"), close=("close", "last"))
    times = groups.size()
    complete = []
    available = set(rows.index.tolist())
    sub_step = step // n
    for t in agg.index:
        if int(times.loc[t]) == n and all(int(t + k * sub_step) in available for k in range(n)):
            complete.append(int(t))
    agg = agg.loc[complete]
    agg["source"] = source
    return agg


def monday_coverage(hour: pd.DataFrame, first_ms: int, data_cutoff: int) -> tuple[int, int, list[str]]:
    available = set(hour.index.astype("int64").tolist()) if not hour.empty else set()
    ny = "America/New_York"
    first = pd.Timestamp(first_ms, unit="ms", tz="UTC").tz_convert(ny).tz_localize(None)
    last = pd.Timestamp(data_cutoff, unit="ms", tz="UTC").tz_convert(ny).tz_localize(None)
    monday = (first - pd.Timedelta(days=first.weekday())).normalize()
    ready, total, missing = 0, 0, []
    while monday <= last:
        start = int(monday.tz_localize(ny).tz_convert("UTC").timestamp() * 1000)
        end = int((monday + pd.Timedelta(days=1)).tz_localize(ny).tz_convert("UTC").timestamp() * 1000)
        expected = range(start, end, HOUR)
        if start >= first_ms and end <= data_cutoff:
            total += 1
            if all(t in available for t in expected):
                ready += 1
            else:
                missing.append(monday.date().isoformat())
        monday += pd.Timedelta(days=7)
    return ready, total, missing


def write_symbol(spec: dict) -> dict:
    symbol, inst_id = spec["symbol"], spec["instId"]
    folder = EXP / "inputs" / symbol
    folder.mkdir(parents=True, exist_ok=True)
    receipt_path = folder / "receipt.json"
    if receipt_path.exists():
        existing = json.loads(receipt_path.read_text())
        if existing.get("status") == "complete" and all((folder / f).exists()
                for f in ("bars12h.csv.gz", "bars1h.csv.gz")):
            return existing

    first_ms = int(spec["list_time"])
    data_cutoff = min(CUTOFF, int(spec.get("archive_end_ms", CUTOFF)))
    archived_notice = spec["cohort"] == "archived_crypto_delisted_notice"
    step_start_h = first_ms // HOUR * HOUR
    step_start_12 = first_ms // STEP_12H * STEP_12H
    try:
        hour, local_info = read_local_sources(symbol, inst_id, data_cutoff)
        hour = hour[(hour.index >= step_start_h) & (hour.index + HOUR <= data_cutoff)]
        hour = normalize_frame(hour)
        # ICX/LRC have no current public instrument. Never request an identically
        # named live contract: only their preserved pre-delisting archive is used.
        rest_h, h_info = (pd.DataFrame(columns=[*OHLC, "source"]), {"pages": 0, "rows": 0})
        rest_12, b_info = (pd.DataFrame(columns=[*OHLC, "source"]), {"pages": 0, "rows": 0})
        native12 = pd.DataFrame(columns=[*OHLC, "source"])
        if spec.get("rest_allowed", True):
            rest_h, h_info = fetch_pages(folder, inst_id, "1H", HOUR,
                step_start_h, data_cutoff - HOUR, hour)
            hour = pd.concat([rest_h, hour]).sort_index(kind="stable")
            hour = hour[~hour.index.duplicated(keep="first")]
            hour = normalize_frame(hour)
            cache12 = cache_frame(inst_id, "12Hutc", STEP_12H, data_cutoff)
            rest_12, b_info = fetch_pages(folder, inst_id, "12Hutc", STEP_12H,
                step_start_12, data_cutoff - STEP_12H, cache12)
            native12 = pd.concat([rest_12, cache12]).sort_index(kind="stable")
            native12 = native12[~native12.index.duplicated(keep="first")]
            b_info["shared_cache_rows"] = len(cache12)

        # For historical notices, only archived 15m/H1 data qualifies; the 15m
        # aggregate must be complete. Current contracts are fetched as native 12H.
        if archived_notice:
            native12 = pd.DataFrame(columns=[*OHLC, "source"])
        agg12 = aggregate(hour, STEP_12H, 12, "complete_1H_aggregation")
        bars12 = pd.concat([native12, agg12]).sort_index(kind="stable")
        bars12 = bars12[~bars12.index.duplicated(keep="first")]
        bars12 = normalize_frame(bars12)
        # Filter only on candle close, retain official bars that opened at/before
        # listing (first candle may be a partial listing-period bar).
        hour = hour[(hour.index >= step_start_h) & (hour.index + HOUR <= data_cutoff)]
        bars12 = bars12[(bars12.index >= step_start_12) & (bars12.index + STEP_12H <= data_cutoff)]

        hour.to_csv(folder / "bars1h.csv.gz", compression="gzip", index_label="time",
                    float_format="%.12g")
        bars12.to_csv(folder / "bars12h.csv.gz", compression="gzip", index_label="time",
                      float_format="%.12g")
        ready, total, missing_mondays = monday_coverage(hour, first_ms, data_cutoff)
        status = "complete" if len(bars12) > 0 and ready == total else "partial"
        receipt = {
            "symbol": symbol, "instId": inst_id, "status": status,
            "cohort": spec["cohort"], "is_current": bool(spec["is_current"]),
            "list_time_ms": first_ms, "list_time_basis": "OKX public instruments listTime" if spec["is_current"] or spec["cohort"] == "archived_crypto_official_snapshot" else "earliest archived OHLC observation; actual listTime unavailable",
            "cutoff_utc": pd.Timestamp(CUTOFF, unit="ms", tz="UTC").isoformat(),
            "data_cutoff_ms": data_cutoff,
            "bar_files": {"12h": "bars12h.csv.gz", "1h": "bars1h.csv.gz"},
            "bars12h": {"rows": len(bars12), "first_ms": int(bars12.index.min()) if len(bars12) else None,
                         "last_open_ms": int(bars12.index.max()) if len(bars12) else None,
                         "sources": bars12.source.value_counts().to_dict() if len(bars12) else {}},
            "bars1h": {"rows": len(hour), "first_ms": int(hour.index.min()) if len(hour) else None,
                       "last_open_ms": int(hour.index.max()) if len(hour) else None,
                       "complete_ny_mondays": ready, "closed_ny_mondays": total,
                       "missing_ny_mondays": missing_mondays,
                       "sources": hour.source.value_counts().to_dict() if len(hour) else {}},
            "local_sources": local_info, "history_candles": {"1H": h_info, "12Hutc": b_info},
            "archive_basis": spec.get("archive_basis", ""),
            "coverage_complete": status == "complete",
            "usable": len(bars12) > 0,
            "audit_state": "audited_partial" if status == "partial" else "complete",
            "transport_error": False,
        }
        dump_json(receipt_path, receipt)
        return receipt
    except Exception as exc:
        receipt = {"symbol": symbol, "instId": inst_id,
                   "status": "transport_error" if isinstance(exc, requests.RequestException) else "error",
                   "usable": False, "transport_error": isinstance(exc, requests.RequestException),
                   "cohort": spec["cohort"], "error": f"{type(exc).__name__}: {exc}",
                   "cutoff_utc": pd.Timestamp(CUTOFF, unit="ms", tz="UTC").isoformat()}
        dump_json(receipt_path, receipt)
        return receipt


def write_top20_receipt(spec: dict) -> dict:
    folder = TOP20_INPUTS / spec["symbol"]
    receipt_file = folder / "receipt.json"
    meta = json.loads(receipt_file.read_text()) if receipt_file.exists() else {}
    return {"symbol": spec["symbol"], "instId": spec["instId"], "cohort": "top20_reused",
            "status": "reused" if (folder / "bars12h.csv.gz").exists() and (folder / "bars1h.csv.gz").exists() else "missing",
            "source_path": str(folder.relative_to(ROOT)),
            "bars12h_rows": meta.get("bars", None), "bars1h_rows": meta.get("hours", None),
            "error": ""}


def run() -> None:
    EXP.mkdir(parents=True, exist_ok=True)
    snapshot = EXP / "instruments_snapshot.json.gz"
    # Reruns must use the original instrument universe, even after delistings or
    # same-symbol category changes. Query live instruments only for a fresh run.
    if snapshot.exists():
        with gzip.open(snapshot, "rt") as handle:
            frozen = json.load(handle)
        all_rows, fetched_at = frozen["data"], frozen["fetched_at_utc"]
    else:
        all_rows, fetched_at = raw_instruments()
        with gzip.open(snapshot, "wt") as handle:
            json.dump({"fetched_at_utc": fetched_at,
                       "endpoint": API + "/public/instruments?instType=SWAP",
                       "data": all_rows}, handle, separators=(",", ":"))
    universe, meta = build_universe(all_rows, fetched_at)
    universe.to_csv(EXP / "universe.csv", index=False)
    dump_json(EXP / "market_meta.json", meta)

    top20_specs = json.loads(TOP20_RANKING.read_text())["top20"]
    top20 = {s["symbol"]: s for s in top20_specs}
    current_specs = universe[universe.is_current & ~universe.top20].to_dict("records")
    archive_specs = universe[~universe.is_current].to_dict("records")
    specs_by_symbol = {s["symbol"]: s for s in current_specs}
    for s in archive_specs:
        specs_by_symbol.setdefault(s["symbol"], s)
    work = list(specs_by_symbol.values())
    coverage = [write_top20_receipt(spec) for spec in top20_specs]
    print(f"Frozen current crypto: {int(universe.is_current.sum())}; non-Top20 inputs to build: {len(work)}; archive rows: {len(archive_specs)}", flush=True)
    receipts = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(write_symbol, spec): spec for spec in work}
        done = 0
        for future in as_completed(futures):
            done += 1
            receipt = future.result()
            receipts.append(receipt)
            if done % 10 == 0 or done == len(work):
                ncomplete = sum(r.get("status") == "complete" for r in receipts)
                print(f"inputs {done}/{len(work)}; complete={ncomplete}; latest={receipt.get('symbol')}:{receipt.get('status')}", flush=True)
    coverage.extend({"symbol": r.get("symbol"), "instId": r.get("instId"),
                     "cohort": r.get("cohort"), "status": r.get("status"),
                     "bars12h_rows": r.get("bars12h", {}).get("rows"),
                     "bars1h_rows": r.get("bars1h", {}).get("rows"),
                     "first12h_ms": r.get("bars12h", {}).get("first_ms"),
                     "last12h_open_ms": r.get("bars12h", {}).get("last_open_ms"),
                     "first1h_ms": r.get("bars1h", {}).get("first_ms"),
                     "last1h_open_ms": r.get("bars1h", {}).get("last_open_ms"),
                     "ready_ny_mondays": r.get("bars1h", {}).get("complete_ny_mondays"),
                     "closed_ny_mondays": r.get("bars1h", {}).get("closed_ny_mondays"),
                     "missing_ny_mondays": json.dumps(r.get("bars1h", {}).get("missing_ny_mondays", []), separators=(",", ":")),
                     "data_cutoff_ms": r.get("data_cutoff_ms", CUTOFF),
                     "usable": r.get("usable", False),
                     "transport_error": r.get("transport_error", False),
                     "audit_state": r.get("audit_state", ""),
                     "archive_basis": r.get("archive_basis", ""),
                     "error": r.get("error", ""), "receipt_path": f"inputs/{r.get('symbol')}/receipt.json"}
                    for r in receipts)
    pd.DataFrame(coverage).sort_values(["cohort", "symbol"]).to_csv(EXP / "input_coverage.csv", index=False)
    meta["input_counts"] = pd.DataFrame(coverage).status.value_counts().to_dict()
    meta["input_finished"] = True
    meta["usable_symbols"] = sum(r.get("status") in ("complete", "partial", "reused") for r in coverage)
    meta["transport_errors"] = sum(r.get("status") == "transport_error" for r in receipts)
    meta["finished_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dump_json(EXP / "market_meta.json", meta)


if __name__ == "__main__":
    run()
