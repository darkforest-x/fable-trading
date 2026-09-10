"""Authenticate local Binance archives and prepare native-amount SPIKE history.

Reads only already collected public USD-M monthly 15m ZIPs and the frozen
multivenue native 1H tail. The ZIP's original quote_volume is retained; the
legacy seven-column series is not used to invent turnover. Each output bar
uses only complete UTC constituent bars, with gaps split rather than filled.
No signal, label, return, fitting, API request, or live-service operation is
performed here. Archive/current metadata are snapshots, not a historical
listing census or historical tick schedule. Historical delivery is unknown.

The three source hashes below freeze already existing manifests before this
builder is run. The CLI refuses to materialize artifacts until this builder
and its parser dependency exactly match committed HEAD. New research output
must use its own empty directory; original sources are always read-only.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.data.binance_um_archives import KLINE_COLUMNS, parse_month_zip

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data/kline_preholdout_binance_um15m"
RECENT = ROOT / "experiments/active/exp-altseason-multivenue-20260910-v1"
ARCHIVE_SHA = "de0b6b64550d4a5cc7cd7f61a8f76750e3819bc8fb316d723c9a693befbd6546"
COVERAGE_SHA = "90c1dae2b3ae4d0fe133b475e1cd130a58423491024c3de536bcf94d6ef08a0d"
CATALOG_SHA = "f9ebc62751284bb69caf308d2799db3910a943e2e688120a6d5e5a62b0a37ecf"
JOIN = pd.Timestamp("2026-05-01T00:00:00Z")
COLS = ["open", "high", "low", "close", "volume", "quote_volume"]
STABLES = {"BUSD", "DAI", "EURC", "EURI", "FDUSD", "FRAX", "GHO", "GUSD",
           "LUSD", "PYUSD", "RLUSD", "SUSDE", "TUSD", "USD0", "USD1", "USDC",
           "USDD", "USDE", "USDF", "USDP", "USDS", "USDT"}


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def checked(path, expected):
    path = Path(path)
    payload = path.read_bytes()
    if not expected or digest(payload) != expected:
        raise ValueError("Source SHA mismatch: " + str(path))
    return payload


def artifact(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=digest(path.read_bytes()), size_bytes=path.stat().st_size)


def utc(value):
    value = pd.Timestamp(value)
    if value.tzinfo is None:
        raise ValueError("Explicit UTC-aware boundary required")
    return value.tz_convert("UTC")


def empty_frame(minutes):
    frame = pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
    frame.attrs.update(minutes=minutes, period_seconds=60 * minutes)
    return frame


def validate_frame(frame, minutes):
    """Fail closed on malformed prices, native amounts, epochs, or duplicates."""
    if list(frame.columns) != COLS:
        raise ValueError("Expected only native OHLCV plus quote_volume")
    idx = frame.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None or str(idx.tz) != "UTC":
        raise ValueError("Expected UTC open-stamped index")
    idx = idx.as_unit("ns")
    if idx.hasnans or not idx.is_unique or not idx.is_monotonic_increasing:
        raise ValueError("Duplicate, invalid, or descending timestamps")
    if np.any(idx.asi8 % pd.Timedelta(minutes=minutes).value):
        raise ValueError("Misaligned native candle timestamps")
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Non-finite native price or volume")
    if (frame[COLS[:4]] <= 0).any().any() or (frame[COLS[4:]] < 0).any().any():
        raise ValueError("Invalid native price or amount")
    if ((frame.volume == 0) != (frame.quote_volume == 0)).any():
        raise ValueError("Inconsistent zero native base/quote volume")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("Invalid OHLC geometry")
    frame.index = idx
    frame.attrs.update(minutes=minutes, period_seconds=minutes * 60)
    return frame


def parse_native_month(payload, symbol, month, expected_sha256, expected_csv_sha256):
    """Preserve actual per-bar base/USDT amounts from the same validated ZIP.

    Uses each row's 12 native fields, with no rolling window. The existing
    parser authenticates bytes, member name, OHLC, epoch unit and close clock.
    This additional vectorized projection validates the original quote total,
    explicit column order, integer open alignment and zero-volume semantics.
    """
    base, audit = parse_month_zip(payload, symbol=symbol, month=month,
                                   expected_sha256=expected_sha256, interval="15m")
    if audit["csv_sha256"] != expected_csv_sha256:
        raise ValueError("Source CSV SHA mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        csv_payload = archive.read(symbol + "-15m-" + month + ".csv")
    raw = pd.read_csv(io.BytesIO(csv_payload), header=None, dtype=str)
    if str(raw.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        aliases = {"count": "trade_count", "taker_buy_volume": "taker_buy_base_volume"}
        header = [str(x).strip().lower().replace(" ", "_") for x in raw.iloc[0]]
        if tuple(aliases.get(x, x) for x in header) != KLINE_COLUMNS:
            raise ValueError("Native archive header order changed")
        raw = raw.iloc[1:].reset_index(drop=True)
    quote = pd.to_numeric(raw.iloc[:, 7], errors="raise").to_numpy(dtype=float)
    open_epoch = pd.to_numeric(raw.iloc[:, 0], errors="raise").to_numpy(dtype=float)
    close_epoch = pd.to_numeric(raw.iloc[:, 6], errors="raise").to_numpy(dtype=float)
    divisor = 1000 if audit["epoch_unit"] == "microseconds" else 1
    if (not np.isfinite(open_epoch).all() or not np.isfinite(close_epoch).all()
            or np.any(open_epoch != np.floor(open_epoch)) or np.any(close_epoch != np.floor(close_epoch))
            or np.any(open_epoch % (900000 * divisor))):
        raise ValueError("Fractional or unaligned original open clock")
    frame = base[COLS[:-1]].copy()
    frame["quote_volume"] = quote
    frame.index = pd.DatetimeIndex(base.open_time)
    frame.index.name = "open_time"
    return validate_frame(frame, 15), audit


def resample_complete(frame, minutes, source_minutes=15):
    """Aggregate OHLC and native amount sums only from complete UTC groups."""
    validate_frame(frame, source_minutes)
    if minutes < source_minutes or minutes % source_minutes:
        raise ValueError("Target must be an integer multiple of source interval")
    if frame.empty:
        return empty_frame(minutes)
    rule = str(minutes) + "min"
    group = frame.resample(rule, origin="epoch", closed="left", label="left")
    counts = group.size()
    out = group.agg(dict(open="first", high="max", low="min", close="last",
                         volume="sum", quote_volume="sum"))
    out = out.loc[counts == minutes // source_minutes, COLS].copy()
    return validate_frame(out, minutes)


def contiguous_segments(frame, minutes=60):
    """Return disjoint continuous frames; indicators must restart at each gap."""
    validate_frame(frame, minutes)
    if frame.empty:
        return []
    cuts = np.flatnonzero(np.diff(frame.index.asi8) != pd.Timedelta(minutes=minutes).value) + 1
    return [validate_frame(frame.iloc[a:b].copy(), minutes)
            for a, b in zip(np.r_[0, cuts], np.r_[cuts, len(frame)])]


def merge_hourly(archive, recent, start, end):
    """Verify any overlapping bars, then join unique hours without filling gaps."""
    for frame in (archive, recent):
        validate_frame(frame, 60)
    overlap = archive.index.intersection(recent.index)
    if len(overlap) and not np.allclose(archive.loc[overlap].to_numpy(),
                                      recent.loc[overlap].to_numpy(), rtol=1e-10, atol=0):
        raise ValueError("Archive/native-hourly overlap mismatch")
    result = pd.concat([archive, recent.loc[~recent.index.isin(archive.index)]]).sort_index()
    result = result.loc[(result.index >= utc(start)) &
                        (result.index + pd.Timedelta(hours=1) <= utc(end))].copy()
    result = validate_frame(result, 60)
    gaps = np.diff(result.index.asi8) // pd.Timedelta(hours=1).value - 1
    return result, dict(overlap_hours_checked=len(overlap), gap_count=int((gaps > 0).sum()),
                        missing_hours_between_rows=int(gaps[gaps > 0].sum()))


def load_months(root, record, start, end):
    """Read authenticated local months intersecting [start,end), no network."""
    start, end = utc(start), utc(end)
    frames, sources, missing = [], [], []
    if record is None or start >= end:
        return empty_frame(15), dict(sources=[], missing_months=[])
    seen = set()
    for row in record.get("month_audits", []):
        month = row["month"]
        left = pd.Timestamp(month + "-01", tz="UTC")
        right = left + pd.offsets.MonthBegin(1)
        if right <= start or left >= end:
            continue
        if month in seen:
            raise ValueError("Duplicate archive month")
        seen.add(month)
        if row.get("status") != "complete":
            missing.append(dict(month=month, reason=row.get("reason", row.get("status"))))
            continue
        path = Path(root) / "downloads" / record["symbol"] / (record["symbol"] + "-15m-" + month + ".zip")
        payload = checked(path, row["zip_sha256"])
        frame, audit = parse_native_month(payload, record["symbol"], month,
                                          row["zip_sha256"], row["csv_sha256"])
        for key in ("rows", "first_time", "last_time", "epoch_unit"):
            if audit[key] != row[key]:
                raise ValueError("Archive manifest row metadata mismatch: " + key)
        frames.append(frame)
        sources.append(dict(path=str(path.resolve()), sha256=row["zip_sha256"],
                            csv_sha256=row["csv_sha256"], month=month, rows=len(frame)))
    frame = pd.concat(frames).sort_index() if frames else empty_frame(15)
    frame = frame.loc[(frame.index >= start) & (frame.index + pd.Timedelta(minutes=15) <= end)].copy()
    return validate_frame(frame, 15), dict(sources=sources, missing_months=missing)


def load_recent(path, source, start, end):
    """Authenticate native Binance 1H bytes; confirmed must be exchange-closed."""
    checked(path, source["sha256"])
    raw = pd.read_csv(path)
    if len(raw) != int(source["rows"]):
        raise ValueError("Recent manifest row count mismatch")
    if not set(["ts", "confirmed"] + COLS).issubset(raw.columns):
        raise ValueError("Recent native amount schema missing")
    if not pd.to_numeric(raw.confirmed, errors="raise").eq(1).all():
        raise ValueError("Recent source contains unconfirmed candles")
    stamps = pd.to_numeric(raw.ts, errors="raise")
    if not np.isfinite(stamps).all() or not (stamps == np.floor(stamps)).all():
        raise ValueError("Invalid recent native clock")
    idx = pd.to_datetime(stamps.astype("int64"), unit="ms", utc=True)
    frame = raw[COLS].astype(float).copy()
    frame.index = pd.DatetimeIndex(idx)
    frame.index.name = "open_time"
    validate_frame(frame, 60)
    frame = frame.loc[(frame.index >= utc(start)) & (frame.index + pd.Timedelta(hours=1) <= utc(end))].copy()
    return validate_frame(frame, 60)


def load_inputs(archive_root=ARCHIVE, recent_root=RECENT, *, archive_sha=ARCHIVE_SHA,
                coverage_sha=COVERAGE_SHA, catalog_sha=CATALOG_SHA):
    """Freeze universe as archive admissions union current snapshot, not winners."""
    archive_root, recent_root = Path(archive_root), Path(recent_root)
    summary_path = archive_root / "archive_fetch_summary.json"
    summary = json.loads(checked(summary_path, archive_sha))
    old_catalog_path = archive_root / "exchange_info.json"
    old_catalog = json.loads(checked(old_catalog_path, summary["exchange_info_sha256"]))
    recent_catalog_path = recent_root / "data/catalog/binance.json"
    recent_catalog = json.loads(checked(recent_catalog_path, catalog_sha))
    coverage_path = recent_root / "results/coverage.csv"
    checked(coverage_path, coverage_sha)
    coverage = pd.read_csv(coverage_path, keep_default_na=False,
                           usecols=["venue", "symbol", "identity", "source_manifest"])
    coverage = coverage.loc[coverage.venue.eq("binance")]
    if coverage.symbol.duplicated().any():
        raise ValueError("Duplicate recent symbol")
    records = {r["symbol"]: r for r in summary["results"]}
    old_raw = {r["symbol"]: r for r in old_catalog["symbols"]}
    markets = {r["symbol"]: r for r in recent_catalog["markets"]}
    if len(records) != len(summary["results"]) or len(markets) != len(recent_catalog["markets"]):
        raise ValueError("Duplicate catalog or archive symbol")
    recent = {}
    for row in coverage.to_dict("records"):
        identity, source = ast.literal_eval(row["identity"]), ast.literal_eval(row["source_manifest"])
        market = markets.get(row["symbol"])
        market_sha = digest(json.dumps(market, sort_keys=True, ensure_ascii=False, default=str).encode())
        if market is None or market_sha != identity.get("market_hash"):
            raise ValueError("Recent catalog/coverage identity mismatch")
        if source.get("sha256") != identity.get("source_sha256"):
            raise ValueError("Recent source/coverage identity mismatch")
        if source.get("native_volume_unit") != "base_asset" or source.get("quote_volume_unit") != "USDT":
            raise ValueError("Recent native quantity units changed")
        recent[row["symbol"]] = source
    jobs = []
    for symbol in sorted(set(records) | set(markets)):
        if any(x in symbol for x in ("/", "\\", "\x00")):
            raise ValueError("Invalid native symbol path")
        market = markets.get(symbol)
        raw = market["raw"] if market else old_raw.get(symbol, {})
        asset = raw.get("baseAsset", symbol.removesuffix("USDT"))
        exclusion = ""
        if not (raw.get("contractType") == "PERPETUAL" and raw.get("underlyingType") == "COIN"
                and raw.get("quoteAsset") == "USDT" and raw.get("marginAsset") == "USDT"):
            exclusion = "catalog_ineligible_non_crypto_usdt_perpetual"
        elif asset in STABLES:
            exclusion = "stablecoin"
        filters = [f for f in raw.get("filters", []) if f.get("filterType") == "PRICE_FILTER"]
        tick, tick_error = None, ""
        try:
            if len(filters) != 1:
                raise ValueError("Missing unique native PRICE_FILTER")
            value = Decimal(str(filters[0].get("tickSize")))
            if not value.is_finite() or value <= 0 or not np.isfinite(float(value)) or float(value) <= 0:
                raise ValueError("Invalid native tick")
            tick = str(value)
        except (InvalidOperation, ValueError) as exc:
            tick_error = str(exc)
            exclusion = exclusion or "invalid_native_tick"
        jobs.append(dict(symbol=symbol, venue="binance", asset=asset, major=asset in {"BTC", "ETH"},
                         archive_record=records.get(symbol), recent_source=recent.get(symbol), tick=tick,
                         tick_origin="raw.filters.PRICE_FILTER.tickSize", tick_error=tick_error,
                         tick_snapshot="recent_catalog" if market else "archive_exchange_info",
                         catalog_status=raw.get("status"), listing_ms=raw.get("onboardDate"),
                         delisting_ms=raw.get("deliveryDate"), exclude_reason=exclusion,
                         archive_admitted=symbol in records, recent_catalog_present=market is not None))
    return jobs, [artifact(p) for p in [summary_path, old_catalog_path, recent_catalog_path, coverage_path]]


def materialize_market(job, output, archive_root, recent_root, start, end):
    """Produce only this symbol's immutable continuous 1H source frames."""
    metadata = {k: v for k, v in job.items() if k not in ("archive_record", "recent_source")}
    metadata.update(segments=[], sources=[], missing_months=[])
    if metadata["exclude_reason"]:
        return dict(metadata, status="excluded")
    try:
        old15, audit = load_months(archive_root, job["archive_record"], start, min(end, JOIN))
        old = resample_complete(old15, 60)
        metadata.update(audit)
        fresh = empty_frame(60)
        if job["recent_source"] is not None and end > JOIN:
            path = Path(recent_root) / "data/normalized/binance" / (job["symbol"] + "_1h.csv.gz")
            fresh = load_recent(path, job["recent_source"], max(start, JOIN), end)
            metadata["sources"].append(artifact(path))
        frame, seam = merge_hourly(old, fresh, start, end)
        metadata.update(seam, archive_rows15m=len(old15), archive_rows1h=len(old), recent_rows1h=len(fresh))
        metadata["boundary_adjacent"] = bool(len(old) and len(fresh) and old.index[-1] + pd.Timedelta(hours=1) == fresh.index[0])
        if frame.empty:
            return dict(metadata, status="empty", exclude_reason="no_authenticated_candles")
        for i, part in enumerate(contiguous_segments(frame)):
            path = Path(output) / "segments" / (hashlib.sha256(job["symbol"].encode()).hexdigest()[:16] + "_%d.pkl.gz" % i)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise ValueError("Refusing to overwrite segment: " + str(path))
            part.to_pickle(path, compression="gzip")
            evidence = artifact(path)
            metadata["segments"].append(dict(venue="binance", symbol=job["symbol"], asset=job["asset"], major=job["major"],
                instrument="binance:" + job["symbol"] + ":segment%d" % i, segment=i, minutes=60,
                tick=job["tick"], tick_origin=job["tick_origin"], tick_snapshot=job["tick_snapshot"],
                source_features_path=evidence["path"], source_features_sha256=evidence["sha256"],
                actual_start=part.index[0].isoformat(), actual_end=(part.index[-1] + pd.Timedelta(hours=1)).isoformat(),
                rows=len(part), exclude_reason="", tick_error="", young_source_allowed=False))
        return dict(metadata, status="complete", rows=len(frame), actual_start=frame.index[0].isoformat(),
                    actual_end=(frame.index[-1] + pd.Timedelta(hours=1)).isoformat())
    except Exception as exc:
        # Source errors reject the whole symbol; never quietly skip bad months.
        return dict(metadata, status="rejected", segments=[], exclude_reason="source_integrity_error", error=str(exc))


def committed_sources():
    records = []
    for path in [Path(__file__), ROOT / "yoyo/data/binance_um_archives.py"]:
        relative = str(path.resolve().relative_to(ROOT))
        old = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
        if old != path.read_bytes():
            raise ValueError("Builder must match committed HEAD before materialization: " + relative)
        records.append(artifact(path))
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), records


def build(output, start, end, workers=4, archive_root=ARCHIVE, recent_root=RECENT):
    start, end = utc(start), utc(end)
    hour = pd.Timedelta(hours=1).value
    if start >= end or start.value % hour or end.value % hour:
        raise ValueError("Expected increasing whole-hour UTC boundaries")
    commit, code = committed_sources()
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Research output directory must be empty")
    jobs, inputs = load_inputs(archive_root, recent_root)
    output.mkdir(parents=True, exist_ok=True)
    markets = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(materialize_market, job, output, archive_root, recent_root, start, end) for job in jobs]
        for future in as_completed(pending):
            markets.append(future.result())
            if len(markets) % 20 == 0:
                print(json.dumps(dict(completed=len(markets), total=len(jobs))), flush=True)
    markets.sort(key=lambda r: r["symbol"])
    manifest = dict(schema="spike-burst-history-v1", status="complete", generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        builder_commit=commit, builder_sources=code, source_manifests=inputs,
        start=start.isoformat(), exclusive_end=end.isoformat(), join=JOIN.isoformat(),
        markets=markets, segments=[s for r in markets for s in r.get("segments", [])],
        status_counts={status: sum(r["status"] == status for r in markets)
                       for status in ("complete", "excluded", "empty", "rejected")},
        training_eligible=False, production_eligible=False, quote_volume="native USDT amount; never estimated",
        universe="archive admissions union recent catalog; includes observed nontrading statuses, not complete delisted census",
        actual_historical_publication_latency="unknown", historical_price_tick_schedule="unknown; frozen snapshot approximation",
        seam="adjacency audited; archive ends exactly where recent begins; no overlapping source window available",
        rules="no gap filling; no candle interpolation; per-segment indicator warmup required")
    path = output / "history_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2023-05-01T00:00:00Z")
    parser.add_argument("--end", default="2026-09-09T00:00:00Z")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    manifest = build(args.output, args.start, args.end, args.workers)
    print(json.dumps(dict(manifest=str(args.output / "history_manifest.json"), markets=len(manifest["markets"]),
                          segments=len(manifest["segments"]))), flush=True)


if __name__ == "__main__":
    main()
