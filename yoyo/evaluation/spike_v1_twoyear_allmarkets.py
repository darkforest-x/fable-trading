"""Resumable, receipt-backed all-current-market SPIKE Burst V1 replay.

This experiment reads public, confirmed 30-minute OHLCV from Binance USD-M,
OKX SWAP, and Gate USDT futures, then derives only complete UTC 1H/4H/1D bars.
It freezes a current-catalog universe before collecting any outcome and retains
all page errors.  Current catalog coverage is not a historical listing census.

V1 itself is long-only and is replayed unchanged from ``spike_burst_replay``.
Signal-close R is diagnostic.  An executable event enters next open and uses
the already-frozen V1 stop; a gap through that stop exits at the same open.
Later stop touches use the conservative stop-first path.  Fees are the existing
20bp round trip.  Funding, exchange-specific fee tiers, market impact and
liquidity fills are deliberately unmodelled and never silently zero-filled.

Run phases in order after committing this file: ``catalog``, ``fetch``, then
``evaluate``.  Fetch is restart-safe per symbol and writes a full error ledger;
it never represents a partial run as all-market coverage.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256, features, replay

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1"
DATA = EXP / "data"
RESULTS = EXP / "results"
START = pd.Timestamp("2024-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
WARMUP_START = pd.Timestamp("2023-08-30T00:00:00Z")
PINE = ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine"
PINE_SHA = "18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2"
CONFIG = {"schema": "spike-v1-twoyear-allmarkets-v1", "evaluation_start": START.isoformat(),
          "exclusive_end": END.isoformat(), "warmup_start": WARMUP_START.isoformat(),
          "source_minutes": 30, "timeframes": [30, 60, 240, 1440], "direction": "long_only",
          "entry": "next_open", "round_trip_cost": 0.002, "funding": "unmodelled",
          "holdout_consumption": 1, "optimization": False, "production_eligible": False}
VENUES = {"binance": ("https://fapi.binance.com", 1500, 0.12),
          "okx": ("https://www.okx.com", 300, 0.25),
          "gate": ("https://api.gateio.ws/api/v4", 2000, 0.12)}
LEDGER_COLUMNS = ("event_id","venue","symbol","asset","timeframe_min","direction","signal_bar_open","signal_close_time","signal_close","entry_time","entry_price","exit_time","exit_price","exit_reason","fees_return","gross_return","net_return","return_pct","holding_minutes","reference_signal_risk","risk_fraction_at_entry","net_r","mae_return","mfe_return","drawdown_return","tail_capture","censored","volume_ratio","tr_atr_expansion","density_width_atr","density_duration","breakout_distance_atr","price_position","delayed_release_bars")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def stamp() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


class Client:
    """Small receipt cache; throttling is per process and every response is immutable."""
    def __init__(self, venue: str):
        self.venue, (self.base, self.limit, self.pause) = venue, VENUES[venue]
        self.next_at = 0.0
        self.session = requests.Session()

    def get(self, path: str, params: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        request = {"url": self.base + path, "params": params}
        key = digest(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())
        folder = DATA / "raw" / self.venue / key[:2]
        body_path, receipt_path = folder / (key + ".json.gz"), folder / (key + ".receipt.json")
        if body_path.exists() and receipt_path.exists():
            raw, receipt = gzip.decompress(body_path.read_bytes()), json.loads(receipt_path.read_text())
            if receipt.get("body_sha256") != digest(raw) or receipt.get("request") != request:
                raise ValueError("cached receipt mismatch " + key)
            return json.loads(raw), receipt
        time.sleep(max(0.0, self.next_at - time.monotonic()))
        self.next_at = time.monotonic() + self.pause
        response = self.session.get(request["url"], params=params, timeout=45)
        raw = response.content
        receipt = {"request": request, "resolved_url": response.url, "fetched_at": stamp(),
                   "status": response.status_code, "body_sha256": digest(raw), "bytes": len(raw)}
        if response.status_code != 200:
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(folder / (key + ".error.json"), dict(receipt, preview=raw[:500].decode("utf8", "replace")))
            response.raise_for_status()
        payload = response.json()
        if self.venue == "okx" and payload.get("code") != "0":
            raise ValueError("OKX error: " + str(payload)[:300])
        folder.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(gzip.compress(raw, mtime=0)); atomic_json(receipt_path, receipt)
        return payload, receipt


def catalog_rows(venue: str, payload: Any) -> list[dict[str, Any]]:
    raw = payload["symbols"] if venue == "binance" else payload["data"] if venue == "okx" else payload
    rows = []
    for item in raw:
        if venue == "binance":
            eligible = item.get("contractType") == "PERPETUAL" and item.get("quoteAsset") == "USDT" and item.get("marginAsset") == "USDT"
            symbol, asset, tick = item["symbol"], item.get("baseAsset", ""), next((x.get("tickSize") for x in item.get("filters", []) if x.get("filterType") == "PRICE_FILTER"), None)
            listed, delisted, status = int(item.get("onboardDate") or 0), int(item.get("deliveryDate") or 0), item.get("status")
        elif venue == "okx":
            eligible = item.get("instType") == "SWAP" and item.get("settleCcy") == "USDT" and item.get("ctType") == "linear" and item.get("ruleType") != "pre_market"
            symbol, asset, tick = item["instId"], item["instId"].split("-")[0], item.get("tickSz")
            listed, delisted, status = int(item.get("listTime") or 0), int(item.get("expTime") or 0), item.get("state")
        else:
            eligible = item.get("contract_type") == "" and item.get("type") == "direct" and item.get("name", "").endswith("_USDT") and not item.get("in_delisting", False)
            symbol, asset, tick = item["name"], item["name"].removesuffix("_USDT"), item.get("order_price_round")
            listed, delisted, status = int(item.get("launch_time") or item.get("create_time") or 0) * 1000, int(item.get("delisted_time") or 0) * 1000, item.get("status")
        try: tick = float(tick)
        except (ValueError, TypeError): tick = np.nan
        rows.append({"venue": venue, "symbol": symbol, "asset": asset, "eligible": bool(eligible), "tick": tick,
                     "listing_ms": listed, "delisting_ms": delisted, "catalog_status": status, "raw": item})
    return sorted(rows, key=lambda x: x["symbol"])


def catalog() -> None:
    if PINE_SHA != digest(PINE.read_bytes()) or SOURCE_SHA256 != PINE_SHA:
        raise ValueError("Pine/replay contract is not frozen V1")
    DATA.mkdir(parents=True, exist_ok=True)
    all_rows, receipts = [], []
    for venue in VENUES:
        client = Client(venue)
        if venue == "binance": payload, receipt = client.get("/fapi/v1/exchangeInfo", {})
        elif venue == "okx": payload, receipt = client.get("/api/v5/public/instruments", {"instType": "SWAP"})
        else: payload, receipt = client.get("/futures/usdt/contracts", {})
        receipts.append(receipt); all_rows.extend(catalog_rows(venue, payload))
    frame = pd.DataFrame(all_rows)
    if frame.duplicated(["venue", "symbol"]).any(): raise ValueError("duplicate catalog symbol")
    frame.to_json(DATA / "catalog.json", orient="records", indent=2)
    atomic_json(DATA / "catalog_manifest.json", {"generated_at": stamp(), "config": CONFIG, "receipts": receipts,
                "counts": frame.groupby(["venue", "eligible"]).size().rename("count").reset_index().to_dict("records"),
                "survivorship_warning": "Current catalogs are not an all-ever-listed/delisted census."})


def page_spec(venue: str, symbol: str, left: pd.Timestamp, right: pd.Timestamp) -> tuple[str, dict[str, Any]]:
    if venue == "binance": return "/fapi/v1/klines", {"symbol": symbol, "interval": "30m", "startTime": left.value // 10**6, "endTime": right.value // 10**6 - 1, "limit": 1500}
    if venue == "okx": return "/api/v5/market/history-candles", {"instId": symbol, "bar": "30m", "after": right.value // 10**6, "before": left.value // 10**6 - 1, "limit": 300}
    return "/futures/usdt/candlesticks", {"contract": symbol, "interval": "30m", "from": left.value // 10**9, "to": right.value // 10**9 - 1}


def normalize(venue: str, payload: Any, left: pd.Timestamp, right: pd.Timestamp) -> pd.DataFrame:
    raw = payload["data"] if venue == "okx" else payload
    rows = []
    for item in raw:
        if venue == "binance": ts, o, h, l, c, vol, quote, confirmed = item[0], item[1], item[2], item[3], item[4], item[5], item[7], True
        elif venue == "okx": ts, o, h, l, c, vol, quote, confirmed = item[0], item[1], item[2], item[3], item[4], item[5], item[7], str(item[8]) == "1"
        else: ts, o, h, l, c, vol, quote, confirmed = int(item["t"]) * 1000, item["o"], item["h"], item["l"], item["c"], item["v"], item["sum"], True
        ts = pd.Timestamp(int(ts), unit="ms", tz="UTC")
        if left <= ts and ts + pd.Timedelta(minutes=30) <= right and confirmed:
            rows.append((ts, float(o), float(h), float(l), float(c), float(vol), float(quote)))
    out = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "quote_volume"])
    if len(out):
        out = out.drop_duplicates("time").sort_values("time").set_index("time")
        values = out.to_numpy(float)
        if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4:] < 0).any(): raise ValueError("invalid source values")
        if (out.high < out[["open", "close", "low"]].max(axis=1)).any() or (out.low > out[["open", "close", "high"]].min(axis=1)).any(): raise ValueError("invalid OHLC")
    return out


def fetch_gate_timeframes(max_markets: int | None = None) -> None:
    """Collect Gate's maximum directly obtainable window for every requested TF.

    Gate rejects old 30m requests after about 10,000 bars.  Each timeframe is
    therefore requested directly (never a misleading resample of the short
    30m tail).  The receipt declares whether its own source reaches the frozen
    two-year start; short 30m/1H windows remain visible exclusions.
    """
    rows = pd.read_json(DATA / "catalog.json")
    wanted = rows.loc[rows.eligible & rows.venue.eq("gate")].sort_values("symbol").to_dict("records")
    if max_markets is not None: wanted = wanted[:max_markets]
    outputs = []
    for number, row in enumerate(wanted, 1):
        raw_listing = row.get("listing_ms")
        listed = int(raw_listing) if pd.notna(raw_listing) and float(raw_listing) > 0 else 0
        listed_at = pd.Timestamp(listed, unit="ms", tz="UTC").ceil("30min") if listed else WARMUP_START
        for minutes in CONFIG["timeframes"]:
            desired = max(WARMUP_START, listed_at)
            # Gate's own error declares a recent-10k-points horizon.  Do not
            # ask earlier and call rejection a candle gap.
            available = max(desired, END - pd.Timedelta(minutes=minutes * 10_000))
            dest = DATA / "normalized_gate_direct" / (row["symbol"] + f"_{minutes}m.csv.gz")
            receipt = DATA / "gate_timeframe_receipts" / (row["symbol"] + f"_{minutes}m.json")
            if dest.exists() and receipt.exists() and json.loads(receipt.read_text()).get("status") in ("complete", "partial"):
                outputs.append(json.loads(receipt.read_text())); continue
            dest.parent.mkdir(parents=True, exist_ok=True); client, pieces, pages, error = Client("gate"), [], [], ""
            cursor = available
            try:
                while cursor < END:
                    right = min(END, cursor + pd.Timedelta(minutes=minutes * 2000))
                    payload, page = client.get("/futures/usdt/candlesticks", {"contract": row["symbol"], "interval": {30:"30m",60:"1h",240:"4h",1440:"1d"}[minutes], "from": cursor.value // 10**9, "to": right.value // 10**9 - 1})
                    raw = []
                    for item in payload:
                        timestamp = pd.Timestamp(int(item["t"]), unit="s", tz="UTC")
                        if cursor <= timestamp and timestamp + pd.Timedelta(minutes=minutes) <= right:
                            raw.append((timestamp, float(item["o"]), float(item["h"]), float(item["l"]), float(item["c"]), float(item["v"]), float(item["sum"])))
                    pieces.append(pd.DataFrame(raw, columns=["time","open","high","low","close","volume","quote_volume"]).set_index("time"))
                    pages.append(page); cursor = right
                frame = pd.concat(pieces).sort_index() if pieces else pd.DataFrame()
                if len(frame) and frame.index.duplicated().any(): raise ValueError("duplicate Gate direct timestamps")
                expected = pd.date_range(available, END, freq=f"{minutes}min", inclusive="left")
                missing = expected.difference(frame.index).astype(str).tolist() if len(frame) else expected.astype(str).tolist()
                status = "complete" if not missing and available <= START else "partial" if not missing else "gapped"
                frame.to_csv(dest, compression={"method":"gzip","mtime":0})
            except Exception as exc:
                frame, missing, status, error = pd.DataFrame(), [], "error", repr(exc)
            record = {"venue":"gate", "symbol":row["symbol"], "asset":row["asset"], "minutes":minutes, "status":status,
                      "rows":len(frame), "pages":len(pages), "requested_from":available.isoformat(), "two_year_source_coverage":available <= START,
                      "missing":len(missing), "missing_examples":missing[:20], "error":error, "path":str(dest), "completed_at":stamp(), "page_receipts":pages}
            atomic_json(receipt, record); outputs.append(record); print(number, len(wanted), "gate", row["symbol"], minutes, status, len(frame), flush=True)
    pd.DataFrame(outputs).drop(columns=["page_receipts"], errors="ignore").to_csv(DATA / "gate_timeframe_coverage.csv", index=False)


def fetch(max_markets: int | None = None, venue: str | None = None) -> None:
    rows = pd.read_json(DATA / "catalog.json")
    wanted = rows.loc[rows.eligible].sort_values(["venue", "symbol"]).to_dict("records")
    if venue is not None:
        wanted = [row for row in wanted if row["venue"] == venue]
    if max_markets is not None: wanted = wanted[:max_markets]
    ledger = []
    for number, row in enumerate(wanted, 1):
        venue, symbol = row["venue"], row["symbol"]
        dest = DATA / "normalized" / venue / (symbol + "_30m.csv.gz")
        receipt = DATA / "market_receipts" / venue / (symbol + ".json")
        if dest.exists() and receipt.exists() and json.loads(receipt.read_text()).get("status") == "complete":
            ledger.append(json.loads(receipt.read_text())); continue
        client, chunks, pages, error = Client(venue), [], [], ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw_listing = row.get("listing_ms")
        listed = int(raw_listing) if pd.notna(raw_listing) and float(raw_listing) > 0 else 0
        listed_at = pd.Timestamp(listed, unit="ms", tz="UTC").ceil("30min") if listed > 0 else WARMUP_START
        requested_start = max(WARMUP_START, listed_at)
        expected = pd.date_range(requested_start, END, freq="30min", inclusive="left")
        cursor = requested_start
        try:
            while cursor < END:
                right = min(END, cursor + pd.Timedelta(minutes=30 * client.limit))
                path, params = page_spec(venue, symbol, cursor, right)
                payload, page = client.get(path, params); chunks.append(normalize(venue, payload, cursor, right)); pages.append(page); cursor = right
            frame = pd.concat(chunks).sort_index() if chunks else pd.DataFrame()
            if len(frame) and frame.index.duplicated().any(): raise ValueError("duplicate rows after page join")
            frame.to_csv(dest, compression={"method": "gzip", "mtime": 0})
            missing = expected.difference(frame.index).astype(str).tolist() if len(frame) else expected.astype(str).tolist()
            status = "complete" if not missing else "gapped"
        except Exception as exc: frame, pages, missing, status, error = pd.DataFrame(), pages, [], "error", repr(exc)
        record = {"venue": venue, "symbol": symbol, "asset": row["asset"], "tick": row["tick"], "status": status,
                  "rows": len(frame), "pages": len(pages), "missing_30m": len(missing), "missing_examples": missing[:20],
                  "error": error, "path": str(dest), "expected_from": expected[0].isoformat() if len(expected) else None,
                  "completed_at": stamp(), "page_receipts": pages}
        atomic_json(receipt, record); ledger.append(record); print(number, len(wanted), venue, symbol, status, len(frame), flush=True)
    pd.DataFrame(ledger).drop(columns=["page_receipts"], errors="ignore").to_csv(DATA / "error_ledger.csv", index=False)
    atomic_json(DATA / "fetch_manifest.json", {"config": CONFIG, "generated_at": stamp(), "requested_markets": len(wanted),
                "complete": sum(x["status"] == "complete" for x in ledger), "gapped": sum(x["status"] == "gapped" for x in ledger), "errors": sum(x["status"] == "error" for x in ledger)})


def aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    output = grouped.agg({"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum", "quote_volume":"sum"})
    return output.loc[grouped.size().eq(minutes // 30)]


def evaluate() -> None:
    manifest = json.loads((DATA / "fetch_manifest.json").read_text())
    if manifest["complete"] != manifest["requested_markets"] or manifest["gapped"] or manifest["errors"]:
        raise ValueError("Refusing partial/gapped universe as all-market backtest; inspect error_ledger.csv")
    records = []
    for receipt in sorted((DATA / "market_receipts").glob("*/*.json")):
        item = json.loads(receipt.read_text()); source = pd.read_csv(item["path"], index_col=0, parse_dates=True)
        source.index = pd.DatetimeIndex(source.index, tz="UTC") if source.index.tz is None else source.index.tz_convert("UTC")
        for minutes in CONFIG["timeframes"]:
            bars = aggregate(source, minutes); step = pd.Timedelta(minutes=minutes)
            split = np.flatnonzero(np.diff(bars.index.asi8) != step.value) + 1
            for segment in np.split(bars, split):
                if len(segment) < 341: continue
                replayed = replay(features(segment), float(item["tick"]))
                for i, signal in replayed.loc[replayed.burst].iterrows():
                    pos = segment.index.get_loc(i)
                    if pos + 1 >= len(segment): continue
                    entry_time, entry = segment.index[pos + 1], float(segment.open.iloc[pos + 1])
                    if not (START <= entry_time < END): continue
                    stop, exit_time, exit_price, reason = float(signal.initial_stop), None, None, "censored"
                    if entry <= stop: exit_time, exit_price, reason = entry_time, entry, "entry_gap_through_stop"
                    else:
                        for j in range(pos + 1, len(segment)):
                            bar = segment.iloc[j]
                            if float(bar.low) <= stop:
                                exit_time, exit_price, reason = segment.index[j], min(float(bar.open), stop), "protective_stop"
                                break
                            stop = float(replayed.protection.iloc[j]) if bool(replayed.trend_side.iloc[j]) else stop
                    if exit_time is None: exit_time, exit_price = min(END, segment.index[-1] + step), float(segment.close.iloc[-1])
                    gross = exit_price / entry - 1; net = gross - .002
                    records.append({"venue": item["venue"], "symbol": item["symbol"], "asset": item["asset"], "timeframe": minutes,
                        "direction": "long", "signal_close_time": i + step, "signal_close": float(segment.close.iloc[pos]), "entry_time": entry_time,
                        "entry_price": entry, "exit_time": exit_time, "exit_price": exit_price, "exit_reason": reason, "fees_return": .002,
                        "gross_return": gross, "net_return": net, "return_pct": net * 100, "holding_minutes": (exit_time-entry_time).total_seconds()/60,
                        "net_r": (exit_price-entry)/(float(signal.risk) or np.nan), "volume_ratio": float(replayed.rv.iloc[pos]),
                        "tr_atr_expansion": float(replayed.expansion.iloc[pos]), "density_width_atr": float(replayed.pastWidth.iloc[pos]),
                        "density_duration": int(replayed.quiet_bars.iloc[pos]), "breakout_distance_atr": (float(segment.close.iloc[pos])-float(replayed.launch_high.iloc[pos]))/float(replayed.atr.iloc[pos]),
                        "price_position": (float(segment.close.iloc[pos])-float(segment.low.iloc[pos]))/(float(segment.high.iloc[pos])-float(segment.low.iloc[pos]) or np.nan),
                        "reference_signal_risk": float(signal.risk), "censored": reason == "censored"})
    ledger = pd.DataFrame(records); RESULTS.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(RESULTS / "trade_ledger.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
    summary = ledger.groupby(["venue", "timeframe"], dropna=False).agg(trades=("net_return","size"), win_rate=("net_return",lambda x:(x>0).mean()), net_return=("net_return","sum"), expectancy=("net_return","mean"), censored=("censored","sum")).reset_index()
    summary.to_csv(RESULTS / "summary_by_venue_timeframe.csv", index=False)
    atomic_json(RESULTS / "evaluation_manifest.json", {"config": CONFIG, "completed_at": stamp(), "trade_count": len(ledger), "ledger": str(RESULTS / "trade_ledger.csv.gz"), "funding": "unmodelled"})


def _continuous(frame: pd.DataFrame, minutes: int) -> list[pd.DataFrame]:
    """Split rather than bridge gaps; every returned frame is UTC continuous."""
    if frame.empty:
        return []
    step = pd.Timedelta(minutes=minutes).value
    cuts = np.flatnonzero(np.diff(frame.index.asi8) != step) + 1
    return [part for part in np.split(frame, cuts) if len(part)]


def _trade_rows(item: dict[str, Any], bars: pd.DataFrame, minutes: int) -> list[dict[str, Any]]:
    """Run frozen long V1 on one continuous source segment, incrementally.

    The Pine signal's risk is a signal-close reference.  Trading begins only at
    the following open; stop checks use that fixed protection before a close
    ratchet becomes active.  On a stop candle the high is not counted as MFE,
    because the conservative intrabar ordering already selected the stop.
    """
    if len(bars) < 341 or not np.isfinite(float(item["tick"])) or float(item["tick"]) <= 0:
        return []
    feature_frame = features(bars)
    replayed = replay(feature_frame, float(item["tick"]))
    step, rows = pd.Timedelta(minutes=minutes), []
    for signal_time, signal in replayed.loc[replayed.burst].iterrows():
        position = bars.index.get_loc(signal_time)
        if position + 1 >= len(bars):
            continue
        entry_time, entry = bars.index[position + 1], float(bars.open.iloc[position + 1])
        if not (START <= entry_time < END) or not np.isfinite(float(signal.risk)) or float(signal.risk) <= 0:
            continue
        protection, exit_position, exit_price, reason = float(signal.initial_stop), None, np.nan, "censored"
        if entry <= protection:
            exit_position, exit_price, reason = position + 1, entry, "entry_gap_through_stop"
        else:
            for j in range(position + 1, len(bars)):
                bar = bars.iloc[j]
                if float(bar.low) <= protection:
                    exit_position, exit_price, reason = j, min(float(bar.open), protection), "protective_stop"
                    break
                if bool(replayed.trend_side.iloc[j]) and np.isfinite(float(replayed.protection.iloc[j])):
                    protection = float(replayed.protection.iloc[j])
        if exit_position is None:
            exit_position, exit_price = len(bars) - 1, float(bars.close.iloc[-1])
            exit_time = min(END, bars.index[-1] + step)
        else:
            exit_time = entry_time if reason == "entry_gap_through_stop" else bars.index[exit_position] + step
        # Stop-bar extrema are after an unknown stop touch and excluded from MFE.
        held_end = exit_position if reason == "protective_stop" else exit_position + 1
        held = bars.iloc[position + 1:held_end]
        highs = held.high.to_numpy(float) if len(held) else np.array([entry])
        lows = held.low.to_numpy(float) if len(held) else np.array([entry])
        mfe = float(highs.max() / entry - 1); mae = float(lows.min() / entry - 1)
        gross, net, risk_fraction = exit_price / entry - 1, exit_price / entry - 1 - .002, float(signal.risk) / entry
        row = {"event_id": digest(f"{item['venue']}|{item['symbol']}|{minutes}|{signal_time.isoformat()}".encode()),
               "venue":item["venue"], "symbol":item["symbol"], "asset":item["asset"], "timeframe_min":minutes,
               "direction":"long", "signal_bar_open":signal_time, "signal_close_time":signal_time + step,
               "signal_close":float(bars.close.iloc[position]), "entry_time":entry_time, "entry_price":entry,
               "exit_time":exit_time, "exit_price":exit_price, "exit_reason":reason, "fees_return":.002,
               "gross_return":gross, "net_return":net, "return_pct":net * 100, "holding_minutes":(exit_time-entry_time).total_seconds()/60,
               "reference_signal_risk":float(signal.risk), "risk_fraction_at_entry":risk_fraction,
               "net_r":net / risk_fraction, "mae_return":mae, "mfe_return":mfe,
               "drawdown_return":min(mae, 0.), "tail_capture":net / mfe if mfe > 0 else np.nan,
               "censored":reason == "censored", "volume_ratio":float(feature_frame.rv.iloc[position]),
               "tr_atr_expansion":float(feature_frame.expansion.iloc[position]), "density_width_atr":float(feature_frame.pastWidth.iloc[position]),
               "density_duration":int(replayed.quiet_bars.iloc[position]),
               "breakout_distance_atr":(float(bars.close.iloc[position])-float(replayed.launch_high.iloc[position]))/float(feature_frame.atr.iloc[position]),
               "price_position":(float(bars.close.iloc[position])-float(bars.low.iloc[position])) / max(float(bars.high.iloc[position])-float(bars.low.iloc[position]), np.finfo(float).eps),
               "delayed_release_bars":int(replayed.wait_bars.iloc[position]) if np.isfinite(float(replayed.wait_bars.iloc[position])) else 0}
        rows.append(row)
    return rows


def evaluate_covered() -> None:
    """Incrementally ledger every locally complete market/timeframe.

    Coverage begins as all frozen current-catalog venue/symbol/timeframe cells.
    A source becomes ``evaluated`` only after its own continuous segments have
    passed V1 warmup and its ledger receipt pins the source bytes.  Thus a
    growing covered universe never changes the 1,681×4 denominator.
    """
    catalog_frame = pd.read_json(DATA / "catalog.json")
    catalog_frame = catalog_frame.loc[catalog_frame.eligible].copy()
    base = pd.MultiIndex.from_product([CONFIG["timeframes"], range(len(catalog_frame))], names=["timeframe_min","catalog_i"]).to_frame(index=False)
    coverage = base.join(catalog_frame.reset_index(drop=True), on="catalog_i")[["venue","symbol","asset","timeframe_min"]]
    coverage["status"], coverage["detail"], coverage["trade_rows"] = "not_acquired", "", 0
    tick_lookup = {(r.venue,r.symbol):r.tick for r in catalog_frame.itertuples()}
    inputs: list[tuple[dict[str, Any], int, pd.DataFrame, str]] = []
    for path in sorted((DATA / "market_receipts").glob("*/*.json")):
        item = json.loads(path.read_text())
        mask = coverage.venue.eq(item["venue"]) & coverage.symbol.eq(item["symbol"])
        if item.get("status") != "complete":
            coverage.loc[mask, ["status","detail"]] = "source_" + item.get("status","unknown"), item.get("error","")
            continue
        source = pd.read_csv(item["path"], index_col=0, parse_dates=True)
        source.index = pd.DatetimeIndex(source.index, tz="UTC") if source.index.tz is None else source.index.tz_convert("UTC")
        for minutes in CONFIG["timeframes"]:
            inputs.append((item, minutes, aggregate(source, minutes), digest(Path(item["path"]).read_bytes())))
    for path in sorted((DATA / "gate_timeframe_receipts").glob("*.json")):
        item = json.loads(path.read_text()); minutes = int(item["minutes"])
        mask = coverage.venue.eq("gate") & coverage.symbol.eq(item["symbol"]) & coverage.timeframe_min.eq(minutes)
        if item.get("status") not in ("complete","partial"):
            coverage.loc[mask, ["status","detail"]] = "source_" + item.get("status","unknown"), item.get("error","")
            continue
        source = pd.read_csv(item["path"], index_col=0, parse_dates=True)
        source.index = pd.DatetimeIndex(source.index, tz="UTC") if source.index.tz is None else source.index.tz_convert("UTC")
        tick = tick_lookup.get(("gate", item["symbol"]))
        inputs.append((dict(item, tick=tick), minutes, source, digest(Path(item["path"]).read_bytes())))
    for item, minutes, frame, source_sha in inputs:
        mask = coverage.venue.eq(item["venue"]) & coverage.symbol.eq(item["symbol"]) & coverage.timeframe_min.eq(minutes)
        segments = _continuous(frame, minutes)
        # V1 needs at least 340 bars to stabilize its daily SMMA and other rolling state.
        # A fetched new listing is coverage evidence, but it is not a V1-eligible evaluation.
        if not any(len(segment) >= 341 for segment in segments):
            detail = f"{len(frame)} bars; no continuous segment reaches 341-bar V1 warmup"
            coverage.loc[mask, ["status", "detail"]] = "warmup_insufficient", detail
            continue
        dest = RESULTS / "covered_ledgers" / item["venue"] / (item["symbol"] + f"_{minutes}m.csv.gz")
        receipt = dest.with_suffix(".receipt.json")
        if receipt.exists() and dest.exists() and dest.stat().st_size > 50 and json.loads(receipt.read_text()).get("source_sha256") == source_sha:
            prior = json.loads(receipt.read_text()); coverage.loc[mask,["status","detail","trade_rows"]] = "evaluated", prior["source_window"], prior["trade_rows"]
            continue
        rows = []
        for segment in segments: rows.extend(_trade_rows(item, segment, minutes))
        dest.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=LEDGER_COLUMNS).to_csv(dest, index=False, compression={"method":"gzip","mtime":0})
        receipt_data = {"source_sha256":source_sha, "source_window":f"{frame.index.min() if len(frame) else None}..{frame.index.max() if len(frame) else None}", "trade_rows":len(rows), "completed_at":stamp()}
        atomic_json(receipt, receipt_data); coverage.loc[mask,["status","detail","trade_rows"]] = "evaluated", receipt_data["source_window"], len(rows)
        print("evaluated", item["venue"], item["symbol"], minutes, len(rows), flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(RESULTS / "coverage_limited.csv", index=False)
    ledgers = list((RESULTS / "covered_ledgers").glob("*/*.csv.gz"))
    # Early incremental builds wrote zero-event gzip files without a header. Repair
    # only those owned generated files so one valid zero-trade cell cannot block all ledgers.
    frames = []
    for path in ledgers:
        try:
            frames.append(pd.read_csv(path))
        except pd.errors.EmptyDataError:
            empty = pd.DataFrame(columns=LEDGER_COLUMNS)
            empty.to_csv(path, index=False, compression={"method":"gzip","mtime":0})
            frames.append(empty)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=LEDGER_COLUMNS)
    combined.to_csv(RESULTS / "covered_trade_ledger.csv.gz", index=False, compression={"method":"gzip","mtime":0})
    atomic_json(RESULTS / "coverage_progress.json", {"generated_at":stamp(), "denominator_cells":len(coverage),
                "statuses":coverage.status.value_counts().to_dict(), "evaluated_cells":int(coverage.status.eq("evaluated").sum()),
                "ledger_files":len(ledgers), "trade_rows":int(coverage.trade_rows.sum()),
                "combined_ledger":str(RESULTS / "covered_trade_ledger.csv.gz")})


def _performance_summary(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    """Summarize executable, equal-notional returns without inventing leverage."""
    rows = []
    for key, part in frame.groupby(groups, dropna=False):
        values = part.net_return.astype(float)
        profits, losses = values[values > 0].sum(), values[values < 0].sum()
        equity = values.cumsum()
        drawdown = (equity - equity.cummax()).min() if len(equity) else np.nan
        label = key if isinstance(key, tuple) else (key,)
        rows.append(dict(zip(groups, label), trades=len(part), wins=int((values > 0).sum()),
                         win_rate=float((values > 0).mean()), profit_factor=float(profits / abs(losses)) if losses else np.nan,
                         expectancy=float(values.mean()), net_return=float(values.sum()), max_drawdown=float(drawdown),
                         censored=int(part.censored.sum())))
    return pd.DataFrame(rows)


def report_covered() -> None:
    """Publish incremental summaries and a standalone searchable executable ledger.

    These are descriptive only: they use equal notional per independent signal;
    no capital allocation, leverage, funding, or capacity model is implied.
    """
    ledger_path = RESULTS / "covered_trade_ledger.csv.gz"
    coverage_path = RESULTS / "coverage_limited.csv"
    if not ledger_path.exists() or not coverage_path.exists():
        raise FileNotFoundError("run evaluate-covered before report-covered")
    ledger, coverage = pd.read_csv(ledger_path), pd.read_csv(coverage_path)
    if ledger.empty:
        ledger = pd.DataFrame(columns=LEDGER_COLUMNS)
    for name in ("entry_time", "exit_time", "signal_close_time"):
        if name in ledger:
            ledger[name] = pd.to_datetime(ledger[name], utc=True)
    ledger["year"] = ledger.entry_time.dt.year if len(ledger) else pd.Series(dtype="int64")
    ledger["month"] = ledger.entry_time.dt.strftime("%Y-%m") if len(ledger) else pd.Series(dtype="string")
    summary = _performance_summary(ledger, ["venue", "timeframe_min"]) if len(ledger) else pd.DataFrame()
    monthly = _performance_summary(ledger, ["venue", "timeframe_min", "year", "month"]) if len(ledger) else pd.DataFrame()
    summary.to_csv(RESULTS / "incremental_summary_by_venue_timeframe.csv", index=False)
    monthly.to_csv(RESULTS / "incremental_summary_by_month.csv", index=False)
    coverage.status.value_counts().rename_axis("status").reset_index(name="cells").to_csv(RESULTS / "incremental_coverage_status.csv", index=False)
    visible = ledger.copy()
    for name in ("entry_time", "exit_time", "signal_close_time", "signal_bar_open"):
        if name in visible:
            visible[name] = visible[name].astype(str)
    table = visible.to_html(index=False, escape=True, table_id="ledger")
    document = f'''<!doctype html><html><head><meta charset="utf-8"><title>SPIKE V1 coverage-limited trade drilldown</title>
<style>body{{font-family:system-ui;margin:20px}}input{{width:50%;padding:8px}}table{{border-collapse:collapse;font-size:12px}}th,td{{padding:4px 6px;border:1px solid #ddd;white-space:nowrap}}th{{position:sticky;top:0;background:#eee}}</style></head><body>
<h1>SPIKE Burst V1: executable trade drilldown</h1><p>Coverage-limited current catalog; {len(ledger)} executable trades. Search is literal across every displayed field. Cost is 0.2% round trip; funding and capacity are unmodelled.</p>
<input id="q" placeholder="Search venue, symbol, exit, date, feature…"><p id="count"></p>{table}
<script>const q=document.querySelector('#q'), rows=[...document.querySelectorAll('#ledger tbody tr')], c=document.querySelector('#count');function f(){{let n=0,x=q.value.toLowerCase();rows.forEach(r=>{{let ok=r.innerText.toLowerCase().includes(x);r.hidden=!ok;if(ok)n++}});c.textContent=n+' / '+rows.length+' trades'}}q.oninput=f;f()</script></body></html>'''
    (RESULTS / "covered_trade_drilldown.html").write_text(document, encoding="utf-8")
    atomic_json(RESULTS / "incremental_report_manifest.json", {"generated_at":stamp(), "trade_rows":len(ledger), "coverage_cells":len(coverage), "coverage_statuses":coverage.status.value_counts().to_dict(), "files":{"drilldown":str(RESULTS / "covered_trade_drilldown.html"), "summary":str(RESULTS / "incremental_summary_by_venue_timeframe.csv"), "monthly":str(RESULTS / "incremental_summary_by_month.csv")}})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("phase", choices=("catalog", "fetch", "gate-timeframes", "evaluate", "evaluate-covered", "report-covered")); parser.add_argument("--max-markets", type=int); parser.add_argument("--venue", choices=tuple(VENUES))
    args = parser.parse_args()
    if args.phase == "catalog": catalog()
    elif args.phase == "fetch": fetch(args.max_markets, args.venue)
    elif args.phase == "gate-timeframes": fetch_gate_timeframes(args.max_markets)
    elif args.phase == "evaluate": evaluate()
    elif args.phase == "evaluate-covered": evaluate_covered()
    else: report_covered()


if __name__ == "__main__": main()
