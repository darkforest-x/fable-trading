"""Collect venue-native funding and mark-price inputs for the frozen OKX wallet.

This module only prepares source inputs and coverage receipts. It does not
replay the account or estimate funding cash flows.
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_okx_funding_archives as archive
from yoyo.evaluation import cloud_close_target_expiry as expiry

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-okx-funding-sources-20261109-v1"
CONFIG = EXP / "config.json"
TRIM = ROOT / "experiments/active/exp-cloud-close-hourly-trim-20261109-v1"
RAW = ROOT / "data/research/cloud_close_okx_funding_sources_20261109"
API = "https://www.okx.com/api/v5"
DAILY_API = f"{API}/public/market-data-history"
MARK_API = f"{API}/market/history-mark-price-candles"
DAILY_BASE = "https://static.okx.com/cdn/okex/traderecords/swaprates/daily"
HOUR = 3_600_000
MAX_GAP = 8 * HOUR + 120_000
ZONE = "Asia/Shanghai"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _utc_ms(value: str | pd.Timestamp) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.value // 1_000_000)


def _request(url: str, params: dict | None = None, *, timeout: int = 30) -> bytes:
    request_url = url if params is None else f"{url}?{urlencode(params)}"
    request = Request(request_url, headers={"User-Agent": "fable-trading-research/1.0"})
    for attempt in range(5):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 4:
                raise
        except (URLError, TimeoutError):
            if attempt == 4:
                raise
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("unreachable request retry state")


def _api_json(url: str, params: dict) -> dict:
    payload = json.loads(_request(url, params).decode("utf-8"))
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX returned {payload.get('code')}: {payload.get('msg')}")
    return payload


def actual_legs() -> pd.DataFrame:
    """Return frozen funded parent children using exact 12H target expiry paths."""
    funded = pd.read_csv(TRIM / "funded_OKX.csv.gz", float_precision="round_trip")
    if len(funded) != 1569 or not funded.event_id.is_unique:
        raise ValueError("Unexpected frozen hourly-trim OKX roster")
    paths, _ = expiry.time_in_force(expiry.trend.thirds.okx_paths())
    legs = paths.loc[
        (~paths.is_control) & paths.event_id.astype(str).isin(set(funded.event_id.astype(str)))
    ].copy()
    if (len(legs) != 3138 or legs.duplicated(["event_id", "tp_r"]).any()
            or not legs.groupby("event_id").size().eq(2).all()):
        raise ValueError("Exact target-expiry paths do not contain two native children per funded parent")
    manifest = pd.read_csv(
        expiry.trend.thirds.wallet.SOURCE / "input_coverage.csv",
        usecols=["symbol", "instId"],
    ).drop_duplicates()
    if manifest.symbol.duplicated().any():
        raise ValueError("Ambiguous instrument map")
    legs["instrument"] = legs.symbol.map(manifest.set_index("symbol").instId)
    if legs.instrument.isna().any() or not legs.exit_phase.isin(["open", "intrabar", "close"]).all():
        raise ValueError("Missing native instrument or unsupported exit phase")
    legs = legs.merge(
        funded[["event_id", "cash_pnl", "notional", "net_return"]],
        on="event_id", how="left", validate="many_to_one",
    )
    if legs.cash_pnl.isna().any() or not np.isclose(
        1000.0 + funded.cash_pnl.sum(), 68115.33865050803, rtol=0, atol=0.01
    ):
        raise ValueError("Frozen cash account does not match the declared $68,115.34 source scope")
    legs["window_start_ms"] = legs.entry_ms.astype("int64") - 60_000
    legs["window_end_ms"] = legs.exit_bar_ms.astype("int64") + np.where(
        legs.exit_phase.eq("open"), 0, HOUR
    ) + 60_000
    return legs.sort_values(["entry_ms", "symbol", "event_id", "tp_r"]).reset_index(drop=True)


def base_sources() -> tuple[pd.DataFrame, pd.DataFrame, set[tuple[str, str]]]:
    receipts = pd.read_csv(archive.EXP / "archive_receipts.csv.gz")
    monthly = pd.read_csv(archive.EXP / "published_rates.csv.gz", float_precision="round_trip")
    recent = pd.read_csv(archive.EXP / "recent_realized_rates.csv.gz", float_precision="round_trip")
    tape = pd.concat([monthly, recent], ignore_index=True)
    if tape.duplicated(["instrument_name", "funding_time"]).any():
        raise ValueError("Frozen monthly and recent REST rates overlap exactly")
    coverage = _read_json(archive.EXP / "recent_rate_coverage.json")
    recent_ok = {
        (row["instrument"], row["month"])
        for row in coverage["audit"]
        if row["api_range_traversed"] and row["rows"] and not row["missing_rate_rows"]
        and row["maximum_gap_including_month_edges_ms"] <= MAX_GAP
    }
    monthly_ok = set(zip(
        receipts.loc[receipts.status.eq("observed"), "instrument"],
        receipts.loc[receipts.status.eq("observed"), "month"],
    ))
    return tape, receipts, monthly_ok | recent_ok


def _interval_days(start_ms: int, end_ms: int) -> list[str]:
    start = pd.Timestamp(start_ms, unit="ms", tz="UTC").tz_convert(ZONE).normalize()
    end = pd.Timestamp(end_ms, unit="ms", tz="UTC").tz_convert(ZONE).normalize()
    return [x.strftime("%Y-%m-%d") for x in pd.date_range(start, end, freq="D")]


def source_gaps(legs: pd.DataFrame, tape: pd.DataFrame, supported_months: set[tuple[str, str]]) -> tuple[pd.DataFrame, set[str]]:
    """Plan daily-source checks only over missing months and observed rate gaps."""
    panels = {
        inst: group.sort_values("funding_time").funding_time.to_numpy(dtype=np.int64)
        for inst, group in tape.groupby("instrument_name", sort=False)
    }
    rows: list[dict] = []
    days: set[str] = set()
    for leg in legs.itertuples(index=False):
        start, end = int(leg.window_start_ms), int(leg.window_end_ms)
        months = [str(m) for m in pd.period_range(
            archive.local_month(start), archive.local_month(end), freq="M"
        )]
        missing_months = [m for m in months if (leg.instrument, m) not in supported_months]
        if missing_months:
            for month in missing_months:
                mstart = pd.Timestamp(f"{month}-01", tz=ZONE)
                mend = mstart + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59, seconds=59)
                lo = max(start, int(mstart.tz_convert("UTC").value // 1_000_000))
                hi = min(end, int(mend.tz_convert("UTC").value // 1_000_000))
                days.update(_interval_days(lo, hi))
            rows.append(dict(event_id=leg.event_id, tp_r=leg.tp_r, instrument=leg.instrument,
                             gap_type="missing_month", gap_start_ms=start, gap_end_ms=end,
                             missing_months="|".join(missing_months)))
        ts = panels.get(leg.instrument)
        if ts is None or not len(ts):
            continue
        left = int(ts.searchsorted(start, side="right") - 1)
        right = int(ts.searchsorted(end, side="left"))
        if left < 0:
            lo, hi = start, min(end, int(ts[0]))
            if hi >= lo:
                days.update(_interval_days(lo, hi))
                rows.append(dict(event_id=leg.event_id, tp_r=leg.tp_r, instrument=leg.instrument,
                                 gap_type="left_edge", gap_start_ms=lo, gap_end_ms=hi, missing_months=""))
        if right >= len(ts):
            lo, hi = max(start, int(ts[-1])), end
            if hi >= lo:
                days.update(_interval_days(lo, hi))
                rows.append(dict(event_id=leg.event_id, tp_r=leg.tp_r, instrument=leg.instrument,
                                 gap_type="right_edge", gap_start_ms=lo, gap_end_ms=hi, missing_months=""))
        if left >= 0 and right < len(ts):
            section = ts[left:right + 1]
            bad = np.flatnonzero(np.diff(section) > MAX_GAP)
            for ix in bad:
                lo, hi = int(section[ix]), int(section[ix + 1])
                if lo <= end and hi >= start:
                    days.update(_interval_days(max(start, lo), min(end, hi)))
                    rows.append(dict(event_id=leg.event_id, tp_r=leg.tp_r, instrument=leg.instrument,
                                     gap_type="internal_gap", gap_start_ms=lo, gap_end_ms=hi,
                                     missing_months=""))
    return pd.DataFrame(rows), days


def _day_buckets(days: set[str]) -> list[tuple[str, str]]:
    buckets = set()
    for value in days:
        stamp = pd.Timestamp(value)
        first = ((stamp.day - 1) // 10) * 10 + 1
        last = min(first + 9, int((stamp + pd.offsets.MonthEnd(0)).day))
        buckets.add((f"{stamp.year:04d}-{stamp.month:02d}-{first:02d}",
                     f"{stamp.year:04d}-{stamp.month:02d}-{last:02d}"))
    return sorted(buckets)


def daily_manifest(days: set[str]) -> pd.DataFrame:
    wanted = set(days)
    rows: dict[str, dict] = {}
    for bucket_start, bucket_end in _day_buckets(days):
        start = pd.Timestamp(bucket_start, tz=ZONE)
        end = pd.Timestamp(bucket_end, tz=ZONE)
        params = dict(module="3", instType="SWAP", instFamilyList="ANY", dateAggrType="daily",
                      begin=str(start.tz_convert("UTC").value // 1_000_000),
                      end=str(end.tz_convert("UTC").value // 1_000_000))
        payload = _api_json(DAILY_API, params)
        for block in payload.get("data", []):
            for detail in block.get("details", []):
                for item in detail.get("groupDetails", []):
                    name = item["filename"]
                    date = name.removeprefix("allswap-fundingrates-").removesuffix(".zip")
                    if date not in wanted:
                        continue
                    rows[date] = dict(date=date, filename=name, url=item["url"], size_mb=float(item["sizeMB"]),
                                      query_begin=bucket_start, query_end=bucket_end)
        time.sleep(0.42)
    return pd.DataFrame(sorted(rows.values(), key=lambda r: r["date"]))


def _decode_daily_zip(data: bytes, filename: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        csv_names = [name for name in zf.namelist() if name.endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"Unexpected allswap ZIP members: {filename}")
        frame = pd.read_csv(zf.open(csv_names[0]), float_precision="round_trip")
    required = {"instrument_name", "funding_rate", "funding_time"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Unexpected official daily funding schema: {filename}")
    frame = frame[list(required)].copy()
    frame["funding_time"] = pd.to_numeric(frame.funding_time, errors="raise").astype("int64")
    frame["funding_rate"] = pd.to_numeric(frame.funding_rate, errors="coerce")
    if frame.instrument_name.isna().any() or np.isinf(frame.funding_rate).any():
        raise ValueError(f"Invalid official rate row: {filename}")
    stamp = filename.removeprefix("allswap-fundingrates-").removesuffix(".zip")
    local_dates = pd.to_datetime(frame.funding_time, unit="ms", utc=True).dt.tz_convert(ZONE).dt.strftime("%Y-%m-%d")
    if not local_dates.eq(stamp).all():
        raise ValueError(f"Funding timestamps do not match daily ZIP date: {filename}")
    return frame.sort_values(["instrument_name", "funding_time"]).reset_index(drop=True)


def fetch_daily_rates(legs: pd.DataFrame, gaps: pd.DataFrame, days: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not days:
        return pd.DataFrame(columns=["instrument_name", "funding_rate", "funding_time"]), pd.DataFrame()
    manifest = daily_manifest(days)
    cache = RAW / "allswap-daily"
    cache.mkdir(parents=True, exist_ok=True)
    relevant_symbols = set(legs.instrument)
    intervals = {
        inst: group[["window_start_ms", "window_end_ms"]].to_numpy(dtype=np.int64)
        for inst, group in legs.groupby("instrument")
    }
    rows, receipts = [], []
    for item in manifest.itertuples(index=False):
        path = cache / item.date / item.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            data = path.read_bytes()
            reused = True
        else:
            data = _request(item.url)
            _decode_daily_zip(data, item.filename)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            reused = False
        frame = _decode_daily_zip(data, item.filename)
        frame = frame[frame.instrument_name.isin(relevant_symbols)].copy()
        kept = []
        for inst, group in frame.groupby("instrument_name", sort=False):
            values = group.funding_time.to_numpy(dtype=np.int64)
            mask = np.zeros(len(group), dtype=bool)
            for start, end in intervals[inst]:
                mask |= (values >= start) & (values <= end)
            if mask.any():
                kept.append(group.loc[mask])
        if kept:
            rows.append(pd.concat(kept, ignore_index=True))
        receipts.append(dict(date=item.date, filename=item.filename, source=item.url, bytes=len(data),
                             reused_cache=reused, raw_rows=len(frame), relevant_rows=sum(len(x) for x in kept),
                             instrument_count=frame.instrument_name.nunique(), local_path=str(path)))
    result = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["instrument_name", "funding_rate", "funding_time"]
    )
    if result.duplicated(["instrument_name", "funding_time"]).any():
        duplicate = result[result.duplicated(["instrument_name", "funding_time"], keep=False)]
        if duplicate.groupby(["instrument_name", "funding_time"]).funding_rate.nunique(dropna=False).gt(1).any():
            raise ValueError("Official daily ZIPs disagree on duplicate settlement")
        result = result.drop_duplicates(["instrument_name", "funding_time"])
    return result.sort_values(["instrument_name", "funding_time"]).reset_index(drop=True), pd.DataFrame(receipts)


def _merge_rates(base: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Use daily rows as missing-event supplements, never double-count settlement rows."""
    if daily.empty:
        return base.assign(source="monthly_or_recent")
    base = base[["instrument_name", "funding_rate", "funding_time"]].copy()
    daily = daily[["instrument_name", "funding_rate", "funding_time"]].copy()
    panels = {inst: g.sort_values("funding_time") for inst, g in base.groupby("instrument_name", sort=False)}
    additions = []
    replace_null_indices: set[int] = set()
    for inst, group in daily.groupby("instrument_name", sort=False):
        old = panels.get(inst)
        if old is None or old.empty:
            additions.append(group)
            continue
        old_ts = old.funding_time.to_numpy(dtype=np.int64)
        old_rates = old.funding_rate.to_numpy(dtype=float)
        ts = group.funding_time.to_numpy(dtype=np.int64)
        rate = group.funding_rate.to_numpy(dtype=float)
        idx = np.searchsorted(old_ts, ts)
        near = np.full(len(ts), -1, dtype=np.int64)
        left = np.clip(idx - 1, 0, len(old_ts) - 1)
        right = np.clip(idx, 0, len(old_ts) - 1)
        choose_right = np.abs(old_ts[right] - ts) < np.abs(old_ts[left] - ts)
        near[:] = np.where(choose_right, right, left)
        dist = np.abs(old_ts[near] - ts)
        close = dist <= 120_000
        replace_null = close & ~np.isfinite(old_rates[near])
        if replace_null.any():
            replace_null_indices.update(old.index.to_numpy()[near[replace_null]].tolist())
        matched = close & np.isfinite(old_rates[near])
        if np.any(matched & (np.abs(old_rates[near] - rate) > 1e-12)):
            raise ValueError(f"Official monthly and daily settlement rates disagree for {inst}")
        additions.append(group.loc[~matched])
    append = pd.concat(additions, ignore_index=True) if additions else pd.DataFrame(columns=base.columns)
    if replace_null_indices:
        base = base.drop(index=list(replace_null_indices))
    combined = pd.concat([base.assign(source="monthly_or_recent"), append.assign(source="official_daily_all_swaps")],
                         ignore_index=True)
    exact_dup = combined.duplicated(["instrument_name", "funding_time"], keep=False)
    if exact_dup.any():
        dup = combined.loc[exact_dup]
        if dup.groupby(["instrument_name", "funding_time"]).funding_rate.nunique(dropna=False).gt(1).any():
            raise ValueError("Official rate sources disagree at an exact settlement timestamp")
        combined = combined.drop_duplicates(["instrument_name", "funding_time"], keep="first")
    return combined.sort_values(["instrument_name", "funding_time"]).reset_index(drop=True)


def coverage(legs: pd.DataFrame, tape: pd.DataFrame) -> pd.DataFrame:
    panels = {
        inst: (g.funding_time.to_numpy(dtype=np.int64), g.funding_rate.to_numpy(dtype=float))
        for inst, g in tape.groupby("instrument_name", sort=False)
    }
    rows = []
    for leg in legs.itertuples(index=False):
        ts, rates = panels.get(leg.instrument, (np.array([], dtype=np.int64), np.array([], dtype=float)))
        start, end = int(leg.window_start_ms), int(leg.window_end_ms)
        lo = int(ts.searchsorted(start, side="right") - 1) if len(ts) else -1
        hi = int(ts.searchsorted(end, side="left")) if len(ts) else 0
        status, max_gap_ms, held_nulls = "unbracketed", None, 0
        if lo >= 0 and hi < len(ts):
            section = ts[lo:hi + 1]
            max_gap_ms = int(np.diff(section).max()) if len(section) > 1 else 0
            held = (ts >= start) & (ts <= end)
            held_nulls = int((~np.isfinite(rates[held])).sum())
            if held_nulls:
                status = "null_rate_in_window"
            elif max_gap_ms > MAX_GAP:
                status = "source_gap_over_8h2m"
            else:
                status = "bracketed_native_rates"
        rows.append(dict(event_id=leg.event_id, tp_r=leg.tp_r, symbol=leg.symbol, instrument=leg.instrument,
                         entry_ms=leg.entry_ms, exit_bar_ms=leg.exit_bar_ms, exit_phase=leg.exit_phase,
                         window_start_ms=start, window_end_ms=end, rate_status=status,
                         max_gap_ms=max_gap_ms, null_rate_rows=held_nulls,
                         parent_cash_pnl=leg.cash_pnl, parent_notional=leg.notional))
    return pd.DataFrame(rows)


def fetch_funding() -> dict:
    EXP.mkdir(parents=True, exist_ok=True)
    legs = actual_legs()
    base, receipts, supported = base_sources()
    gaps, days = source_gaps(legs, base, supported)
    daily, daily_receipts = fetch_daily_rates(legs, gaps, days)
    merged = _merge_rates(base, daily)
    checks = coverage(legs, merged)
    legs.to_csv(EXP / "frozen_actual_legs.csv.gz", index=False)
    gaps.to_csv(EXP / "funding_source_gap_candidates.csv.gz", index=False)
    daily.to_csv(EXP / "daily_funding_rates.csv.gz", index=False)
    daily_receipts.to_csv(EXP / "daily_funding_receipts.csv.gz", index=False)
    checks.to_csv(EXP / "funding_coverage.csv.gz", index=False)
    merged.to_csv(EXP / "merged_funding_rates.csv.gz", index=False)
    parents = checks.groupby("event_id").rate_status.apply(lambda s: s.eq("bracketed_native_rates").all())
    parent_pnl = legs[["event_id", "cash_pnl"]].drop_duplicates("event_id").set_index("event_id").cash_pnl
    bad = parent_pnl.loc[~parents.reindex(parent_pnl.index).fillna(False)]
    summary = dict(
        funded_parents=len(parent_pnl), funded_children=len(legs),
        actual_account_end_usdt=float(1000 + parent_pnl.sum()),
        original_monthly_records=len(base), daily_candidate_dates=len(days),
        daily_zip_files=len(daily_receipts), daily_relevant_rate_rows=len(daily), merged_rate_rows=len(merged),
        child_rate_status=checks.rate_status.value_counts().to_dict(),
        fully_supported_parents=int(parents.sum()), unsupported_parents=len(bad),
        unsupported_frozen_cash_pnl_usdt=float(bad.sum()),
        unsupported_share_of_frozen_net_pnl=float(bad.sum() / parent_pnl.sum()),
        max_gap_threshold_ms=MAX_GAP,
        note="Funding-rate coverage only. No funding cash flow is imputed; H1 mark quotes are a separate source step.",
    )
    (EXP / "funding_source_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)
    return summary


def _mark_rows(inst: str, start_ms: int, end_ms: int) -> tuple[list[dict], dict]:
    """Fetch native H1 mark OHLC bars, walking backward with endpoint pagination."""
    cache = RAW / "mark-h1" / inst
    cache.mkdir(parents=True, exist_ok=True)
    chunks = []
    cursor = int(end_ms) + HOUR
    attempts = 0
    while cursor > start_ms and attempts < 100_000:
        params = dict(instId=inst, bar="1H", after=str(cursor), limit="100")
        payload = _api_json(MARK_API, params)
        data = payload.get("data", [])
        attempts += 1
        if not data:
            break
        rows = []
        for item in data:
            if len(item) < 6:
                raise ValueError(f"Malformed mark H1 row for {inst}")
            ts = int(item[0])
            values = [float(v) for v in item[1:5]]
            if not np.isfinite(values).all() or min(values) <= 0:
                raise ValueError(f"Invalid native mark OHLC for {inst} at {ts}")
            rows.append(dict(instrument=inst, bar_open_ms=ts, open=values[0], high=values[1],
                             low=values[2], close=values[3], confirm=int(item[5])))
        frame = pd.DataFrame(rows).drop_duplicates("bar_open_ms")
        chunks.append(frame.loc[frame.bar_open_ms.ge(start_ms) & frame.bar_open_ms.le(end_ms)])
        oldest = int(frame.bar_open_ms.min())
        if oldest >= cursor or len(data) < 100:
            break
        cursor = oldest
        time.sleep(0.11)
    collected = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(
        columns=["instrument", "bar_open_ms", "open", "high", "low", "close", "confirm"]
    )
    collected = collected.drop_duplicates(["instrument", "bar_open_ms"]).sort_values("bar_open_ms")
    path = cache / f"{start_ms}_{end_ms}.csv.gz"
    collected.to_csv(path, index=False)
    expected = max(0, (int(end_ms) - int(start_ms)) // HOUR + 1)
    receipt = dict(instrument=inst, start_ms=int(start_ms), end_ms=int(end_ms), expected_h1_bars=expected,
                   observed_h1_bars=len(collected), missing_h1_bars=max(0, expected - len(collected)),
                   first_bar_ms=int(collected.bar_open_ms.min()) if len(collected) else None,
                   last_bar_ms=int(collected.bar_open_ms.max()) if len(collected) else None,
                   local_path=str(path), api="/api/v5/market/history-mark-price-candles", bar="1H")
    return collected.to_dict("records"), receipt


def fetch_marks(mark_start: str, mark_end: str | None = None) -> dict:
    EXP.mkdir(parents=True, exist_ok=True)
    legs = actual_legs()
    start = _utc_ms(mark_start)
    end = _utc_ms(mark_end) if mark_end else int(legs.window_end_ms.max())
    selected = legs[(legs.entry_ms >= start) & (legs.window_end_ms <= end)].copy()
    if selected.empty:
        raise ValueError("No frozen actual legs fall within the requested mark interval")
    intervals = []
    for inst, group in selected.groupby("instrument"):
        ranges = sorted((int((r.entry_ms // HOUR) * HOUR), int((r.exit_bar_ms // HOUR) * HOUR))
                        for r in group.itertuples())
        merged = []
        for lo, hi in ranges:
            if merged and lo <= merged[-1][1] + HOUR:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        intervals.extend((inst, lo, hi) for lo, hi in merged)
    marks, receipts = [], []
    for inst, lo, hi in intervals:
        rows, receipt = _mark_rows(inst, lo, hi)
        marks.extend(rows)
        receipts.append(receipt)
        print(inst, receipt["observed_h1_bars"], "/", receipt["expected_h1_bars"], flush=True)
    frame = pd.DataFrame(marks).drop_duplicates(["instrument", "bar_open_ms"]).sort_values(
        ["instrument", "bar_open_ms"]
    )
    frame.to_csv(EXP / "mark_price_h1.csv.gz", index=False)
    receipts = pd.DataFrame(receipts)
    receipts.to_csv(EXP / "mark_price_h1_receipts.csv.gz", index=False)
    expected = int(receipts.expected_h1_bars.sum())
    observed = int(receipts.observed_h1_bars.sum())
    summary = dict(mark_start_utc=pd.Timestamp(start, unit="ms", tz="UTC").isoformat(),
                   mark_end_utc=pd.Timestamp(end, unit="ms", tz="UTC").isoformat(),
                   instruments=len(receipts), expected_h1_bars=expected, observed_h1_bars=observed,
                   missing_h1_bars=max(0, expected - observed),
                   source="OKX native derivative mark-price H1 candles; not last-price or index candles",
                   api="/api/v5/market/history-mark-price-candles")
    (EXP / "mark_price_h1_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)
    return summary


def preflight() -> None:
    legs = actual_legs()
    base, receipts, supported = base_sources()
    gaps, days = source_gaps(legs, base, supported)
    current_keys = {
        (leg.instrument, str(month))
        for leg in legs.itertuples(index=False)
        for month in pd.period_range(archive.local_month(int(leg.window_start_ms)),
                                     archive.local_month(int(leg.window_end_ms)), freq="M")
    }
    frozen_keys = set(zip(receipts.instrument, receipts.month))
    if current_keys != frozen_keys:
        raise ValueError("Exact target-expiry paths changed the frozen monthly-key set")
    old = archive.funded_legs()[["event_id", "tp_r", "exit_bar_ms", "exit_phase"]]
    new = legs[["event_id", "tp_r", "exit_bar_ms", "exit_phase"]]
    compare = old.merge(new, on=["event_id", "tp_r"], suffixes=("_old", "_new"), validate="one_to_one")
    if len(compare) != len(legs):
        raise ValueError("Old and current native child event sets differ")
    exits_differ = compare.exit_bar_ms_old.ne(compare.exit_bar_ms_new) | compare.exit_phase_old.ne(compare.exit_phase_new)
    target = compare.tp_r.eq(2)
    runner = compare.tp_r.eq(0)
    target_exit_differences = int(exits_differ[target].sum())
    runner_exit_differences = int(exits_differ[runner].sum())
    target_phase_differences = int(compare.loc[target, "exit_phase_old"].ne(compare.loc[target, "exit_phase_new"]).sum())
    if target_exit_differences != 409 or runner_exit_differences != 0:
        raise ValueError("Exact source path comparison changed from its inspected baseline")
    summary = dict(parents=legs.event_id.nunique(), children=len(legs),
                   instruments=legs.instrument.nunique(),
                   target_exit_changed_vs_old=target_exit_differences,
                   target_phase_changed_vs_old=target_phase_differences,
                   runner_exit_changed_vs_old=runner_exit_differences,
                   required_month_keys=len(current_keys),
                   already_published_month_or_recent_keys=len(supported),
                   candidate_daily_dates=len(days), gap_rows=len(gaps),
                   source_rates=len(base), original_end_usdt=float(1000 + legs[["event_id", "cash_pnl"]].drop_duplicates("event_id").cash_pnl.sum()),
                   writes=False)
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--preflight", action="store_true")
    actions.add_argument("--fetch-funding", action="store_true")
    actions.add_argument("--fetch-marks", action="store_true")
    parser.add_argument("--mark-start", help="UTC start date for marks, e.g. 2022-03-01")
    parser.add_argument("--mark-end", help="Optional UTC end date for mark bars")
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.fetch_funding:
        fetch_funding()
    else:
        if not args.mark_start:
            parser.error("--fetch-marks requires --mark-start")
        fetch_marks(args.mark_start, args.mark_end)


if __name__ == "__main__":
    main()
