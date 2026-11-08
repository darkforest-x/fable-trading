#!/usr/bin/env python3
"""Build lazy, compressed Binance candle blocks for the breadth strategy booklet.

The normalized event ledger supplies event/member identity and saved fill times
and prices. This builder only projects local, already-registered Binance USD-M
5m OHLC sources into complete 1h BTC and 15m ALT chart bars. It does not fetch
data, calculate strategy outcomes, fill missing candles, or render images.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS = ROOT / "yoyo/monitor/static/strategy-booklet/breadth-ema20-data.json"
DEFAULT_OUTPUT = ROOT / "yoyo/monitor/static/strategy-booklet/breadth-charts"
BTC_RECEIPT = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1/btc/price_receipt.json"
ALT_RECEIPTS = ROOT / "data/research/breadth_constituent_sixday_20261108/checkpoints-v1"
BASE_INTERVAL_MS = 5 * 60 * 1000
CONTEXT_MS = 3 * 60 * 60 * 1000
PERIOD_MS = {"BTC": 60 * 60 * 1000, "ALT": 15 * 60 * 1000}
EXPECTED_EVENTS = 398
EXPECTED_2026_EVENTS = 48
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,64}USDT$")
FILE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
OHLC = ("open", "high", "low", "close")


class ChartBuildError(RuntimeError):
    """Raised when source identity or the normalized chart contract is invalid."""


def utc_ms(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        # ISO timestamps are the stable input contract; epoch values are accepted
        # only to keep the builder usable with locally derived canonical ledgers.
        return int(value)
    if not isinstance(value, str):
        raise ChartBuildError(f"{field} must be a UTC ISO timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ChartBuildError(f"invalid {field}: {value!r}") from exc
    if stamp.tzinfo is None:
        raise ChartBuildError(f"{field} must include a UTC offset")
    return int(stamp.astimezone(timezone.utc).timestamp() * 1000)


def iso_utc(stamp_ms: int | None) -> str | None:
    if stamp_ms is None:
        return None
    return datetime.fromtimestamp(stamp_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def finite_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def gzip_bytes(value: bytes) -> bytes:
    return gzip.compress(value, compresslevel=9, mtime=0)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def event_timestamp_id(asset: str, stamp_ms: int) -> str:
    dt = datetime.fromtimestamp(stamp_ms / 1000, tz=timezone.utc)
    return f"{asset.lower()}-{dt.strftime('%Y%m%dT%H%M%SZ')}"


def event_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("events")
    else:
        rows = None
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ChartBuildError("normalized ledger must contain a non-empty events[] list")
    return rows


def normalized_event(row: dict[str, Any], index: int) -> dict[str, Any]:
    asset = str(row.get("asset", "")).upper()
    if asset not in ("BTC", "ALT"):
        raise ChartBuildError(f"events[{index}].asset must be BTC or ALT")
    entry_ms = utc_ms(row.get("entry_time"), f"events[{index}].entry_time")
    if entry_ms is None or entry_ms % BASE_INTERVAL_MS:
        raise ChartBuildError(f"events[{index}].entry_time must align to a Binance 5m candle open")
    raw_id = row.get("event_id")
    event_id = str(raw_id) if raw_id not in (None, "") else f"{asset}|{iso_utc(entry_ms)}"
    exit_ms = utc_ms(row.get("exit_time"), f"events[{index}].exit_time")
    first_exit_ms = utc_ms(row.get("first_exit_time"), f"events[{index}].first_exit_time")
    if exit_ms is not None and exit_ms < entry_ms:
        raise ChartBuildError(f"events[{index}].exit_time precedes entry")
    if first_exit_ms is not None and first_exit_ms < entry_ms:
        raise ChartBuildError(f"events[{index}].first_exit_time precedes entry")
    members = row.get("members", [])
    if not isinstance(members, list) or any(not isinstance(member, dict) for member in members):
        raise ChartBuildError(f"events[{index}].members must be a list of objects")
    if asset == "BTC" and members:
        raise ChartBuildError(f"events[{index}] BTC block must not contain synthetic basket members")
    if asset == "ALT" and (row.get("entry_price") is not None or row.get("exit_price") is not None):
        raise ChartBuildError(f"events[{index}] ALT event-level prices must be null; prices belong to members")
    return {
        "input": row,
        "index": index,
        "event_id": event_id,
        "asset": asset,
        "entry_ms": entry_ms,
        "exit_ms": exit_ms,
        "first_exit_ms": first_exit_ms,
        "entry_price": finite_number(row.get("entry_price")),
        "exit_price": finite_number(row.get("exit_price")),
        "first_exit_price": finite_number(row.get("first_exit_price")),
        "exit_legs": max(0, int(finite_number(row.get("exit_legs")) or 0)),
        "members": members,
    }


def member_times(member: dict[str, Any], event: dict[str, Any], index: int) -> tuple[int, int | None]:
    entry_ms = utc_ms(member.get("entry_time") or iso_utc(event["entry_ms"]), f"member[{index}].entry_time")
    exit_ms = utc_ms(member.get("selected_exit_time") or member.get("exit_time"), f"member[{index}].exit_time")
    if entry_ms is None or entry_ms % BASE_INTERVAL_MS:
        raise ChartBuildError(f"member[{index}].entry_time must align to a Binance 5m candle open")
    if exit_ms is not None and exit_ms < entry_ms:
        raise ChartBuildError(f"member[{index}].exit_time precedes entry")
    return entry_ms, exit_ms


def requested_window(entry_ms: int, last_ms: int | None, period_ms: int, context_ms: int) -> tuple[int, int]:
    start = (entry_ms // period_ms) * period_ms - context_ms
    last = last_ms if last_ms is not None else entry_ms
    end_bucket = (last // period_ms) * period_ms
    end = end_bucket + period_ms + context_ms
    return start, end


def merge_windows(windows: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def receipt_path(symbol: str, asset: str) -> Path:
    if asset == "BTC":
        return BTC_RECEIPT
    return ALT_RECEIPTS / f"{symbol}.json"


def safe_repo_path(path: str | Path) -> str:
    """Return a repo-relative source path without publishing external mount roots."""
    value = str(path)
    try:
        return Path(value).resolve(strict=False).relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        marker = "/fable-trading/"
        if marker in value:
            return value.rsplit(marker, 1)[-1]
        return Path(value).name


def load_receipt(symbol: str, asset: str) -> tuple[dict[str, Any] | None, Path]:
    path = receipt_path(symbol, asset)
    if not path.is_file():
        return None, path
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChartBuildError(f"could not read source receipt {path}: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("venue") not in (None, "Binance USD-M"):
        raise ChartBuildError(f"source receipt is not Binance USD-M: {path}")
    if receipt.get("interval") not in (None, "5m"):
        raise ChartBuildError(f"source receipt is not 5m: {path}")
    return receipt, path


def source_entries(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    raw = receipt.get("local_sources", [])
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted((x for x in raw if isinstance(x, dict)),
                       key=lambda x: (int(x.get("priority") or 999), str(x.get("kind", "")))):
        candidate_values = [item.get("resolved_path"), item.get("path"), *(item.get("aliases") or [])]
        resolved: Path | None = None
        chosen: str | None = None
        for candidate in candidate_values:
            if not candidate:
                continue
            path = Path(str(candidate)).expanduser()
            if path.is_file():
                try:
                    resolved = path.resolve(strict=True)
                except OSError:
                    resolved = path
                chosen = str(candidate)
                break
        identity = str(resolved) if resolved else str(item.get("resolved_path") or item.get("path") or "")
        if identity in seen:
            continue
        seen.add(identity)
        entries.append({"meta": item, "resolved": resolved, "chosen": chosen})
    return entries


def source_file_record(symbol: str, item: dict[str, Any]) -> dict[str, Any]:
    meta, path = item["meta"], item["resolved"]
    current = None
    if path and path.is_file():
        st = path.stat()
        current = {"bytes": st.st_size, "mtime_ns": st.st_mtime_ns}
    return {
        "kind": meta.get("kind"),
        "path": safe_repo_path(item["chosen"] or meta.get("path") or meta.get("resolved_path") or ""),
        "receipt_bytes": meta.get("bytes"),
        "receipt_mtime_ns": meta.get("mtime_ns"),
        "current": current,
        "priority": meta.get("priority"),
        "has_low": meta.get("has_low", True),
    }


def read_symbol_bars(symbol: str, asset: str, windows: list[tuple[int, int]]) -> tuple[Any, dict[str, Any]]:
    """Read only requested UTC windows from the existing 5m OHLC source files."""
    import numpy as np
    import pandas as pd

    receipt, path = load_receipt(symbol, asset)
    if receipt is None:
        return pd.DataFrame(columns=["ts", *OHLC]), {
            "symbol": symbol, "status": "missing_receipt", "receipt": safe_repo_path(path), "files": [], "conflict_bars": 0,
        }
    sources = source_entries(receipt)
    windows = merge_windows(windows)
    file_records = [source_file_record(symbol, item) for item in sources]
    frames = []
    missing_source_files = []
    wanted_start = windows[0][0] if windows else 0
    wanted_end = windows[-1][1] if windows else 0
    source_paths: list[Path] = []
    for item in sources:
        meta, source = item["meta"], item["resolved"]
        if not source or not source.is_file():
            if item["chosen"] is None:
                missing_source_files.append(safe_repo_path(meta.get("path") or meta.get("resolved_path") or ""))
            continue
        if meta.get("has_low") is False:
            continue
        if source in source_paths:
            continue
        source_paths.append(source)
        try:
            reader = pd.read_csv(source, usecols=lambda name: name in {"ts", *OHLC}, chunksize=100_000,
                                 float_precision="round_trip")
            for chunk in reader:
                if "ts" not in chunk or any(name not in chunk for name in OHLC):
                    raise ChartBuildError(f"OHLC 5m source lacks required columns: {safe_repo_path(source)}")
                ts = pd.to_numeric(chunk["ts"], errors="coerce").to_numpy(dtype="float64")
                finite_ts = np.isfinite(ts)
                wanted = np.zeros(len(chunk), dtype=bool)
                for start, end in windows:
                    wanted |= finite_ts & (ts >= start) & (ts < end)
                if not wanted.any():
                    continue
                selected = chunk.loc[wanted, ["ts", *OHLC]].copy()
                selected["ts"] = pd.to_numeric(selected["ts"], errors="raise").astype("int64")
                for column in OHLC:
                    selected[column] = pd.to_numeric(selected[column], errors="raise")
                selected = selected[(selected.ts >= wanted_start) & (selected.ts < wanted_end)]
                if not selected.empty:
                    frames.append(selected)
        except ChartBuildError:
            raise
        except Exception as exc:
            raise ChartBuildError(f"failed reading {safe_repo_path(source)} for {symbol}: {exc}") from exc
    if not frames:
        return pd.DataFrame(columns=["ts", *OHLC]), {
            "symbol": symbol, "status": "no_window_bars", "receipt": safe_repo_path(path),
            "files": file_records, "missing_source_files": missing_source_files, "conflict_bars": 0,
        }
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.drop_duplicates()
    frame = frame.sort_values("ts", kind="stable").reset_index(drop=True)
    duplicates = frame[frame.duplicated("ts", keep=False)]
    conflicts: set[int] = set()
    if not duplicates.empty:
        distinct = duplicates.groupby("ts", sort=False)[list(OHLC)].nunique(dropna=False).gt(1).any(axis=1)
        conflicts = {int(stamp) for stamp, is_conflict in distinct.items() if is_conflict}
        if conflicts:
            frame = frame[~frame.ts.isin(conflicts)]
        frame = frame.drop_duplicates("ts", keep="first")
    if not frame.empty:
        valid = (
            (frame.ts % BASE_INTERVAL_MS == 0)
            & np.isfinite(frame[list(OHLC)].to_numpy(dtype="float64")).all(axis=1)
            & (frame[list(OHLC)] > 0).all(axis=1)
            & (frame.high >= frame[["open", "low", "close"]].max(axis=1))
            & (frame.low <= frame[["open", "high", "close"]].min(axis=1))
        )
        frame = frame.loc[valid].reset_index(drop=True)
    return frame, {
        "symbol": symbol,
        "status": "ready" if not frame.empty else "no_valid_window_bars",
        "receipt": safe_repo_path(path),
        "interval": receipt.get("interval", "5m"),
        "venue": receipt.get("venue", "Binance USD-M"),
        "files": file_records,
        "missing_source_files": missing_source_files,
        "conflict_bars": len(conflicts),
        "requested_windows": len(windows),
        "requested_start_ms": wanted_start,
        "requested_end_ms": wanted_end,
    }


def aggregate_window(frame: Any, start_ms: int, end_ms: int, period_ms: int) -> dict[str, Any]:
    import pandas as pd

    expected = (end_ms - start_ms) // period_ms
    if frame.empty:
        return {"bars": [], "expected_bars": expected, "missing_bars": expected, "availability": "missing", "conflict_bars": 0}
    selected = frame[(frame.ts >= start_ms) & (frame.ts < end_ms)].copy()
    if selected.empty:
        return {"bars": [], "expected_bars": expected, "missing_bars": expected, "availability": "missing", "conflict_bars": 0}
    selected["bucket"] = selected.ts // period_ms * period_ms
    grouped = selected.groupby("bucket", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
        source_bars=("ts", "size"),
    )
    needed = period_ms // BASE_INTERVAL_MS
    grouped = grouped[grouped.source_bars == needed]
    bars = [[int(ts), *(float(row[key]) for key in OHLC)] for ts, row in grouped.iterrows()]
    n = len(bars)
    return {
        "bars": bars,
        "expected_bars": expected,
        "missing_bars": max(0, expected - n),
        "availability": "complete" if n == expected else ("partial" if n else "missing"),
        "conflict_bars": 0,
    }


def exact_open(frame: Any, stamp_ms: int | None) -> float | None:
    if stamp_ms is None or frame.empty:
        return None
    rows = frame.loc[frame.ts == stamp_ms, "open"]
    if len(rows):
        return float(rows.iloc[0])
    return None


def marker(kind: str, stamp_ms: int | None, price: float | None, source: str) -> dict[str, Any]:
    return {"kind": kind, "time_ms": stamp_ms, "time": iso_utc(stamp_ms), "price": price, "price_source": source}


def chart_windows(events: list[dict[str, Any]], context_ms: int) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[int, int | None], dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    descriptors: dict[tuple[int, int | None], dict[str, Any]] = {}
    for event in events:
        asset = event["asset"]
        event_index = event["index"]
        period_ms = PERIOD_MS[asset]
        if asset == "BTC":
            markers: list[tuple[str, int | None, float | None]] = []
            if event["exit_legs"] >= 2:
                if event["first_exit_ms"] is not None:
                    markers.append(("exit_1", event["first_exit_ms"], event["first_exit_price"]))
                if event["exit_ms"] is not None and event["exit_ms"] != event["first_exit_ms"]:
                    markers.append(("exit_2", event["exit_ms"], event["exit_price"]))
            elif event["exit_ms"] is not None:
                markers.append(("exit_1", event["exit_ms"], event["exit_price"]))
            elif event["first_exit_ms"] is not None:
                markers.append(("exit_1", event["first_exit_ms"], event["first_exit_price"]))
            last_ms = max((stamp for _, stamp, _ in markers if stamp is not None), default=event["entry_ms"])
            start_ms, end_ms = requested_window(event["entry_ms"], last_ms, period_ms, context_ms)
            descriptor = {"event": event, "member": None, "symbol": "BTCUSDT", "asset": asset,
                          "period_ms": period_ms, "start_ms": start_ms, "end_ms": end_ms, "markers": markers}
            descriptors[(event_index, None)] = descriptor
            by_symbol.setdefault("BTCUSDT", []).append(descriptor)
            continue
        for member_index, member in enumerate(event["members"]):
            symbol = str(member.get("symbol", "")).upper()
            if not SYMBOL_RE.fullmatch(symbol):
                raise ChartBuildError(f"invalid ALT member symbol in {event['event_id']}: {symbol!r}")
            entry_ms, exit_ms = member_times(member, event, member_index)
            start_ms, end_ms = requested_window(entry_ms, exit_ms, period_ms, context_ms)
            descriptor = {"event": event, "member": member, "member_index": member_index,
                          "symbol": symbol, "asset": asset, "period_ms": period_ms,
                          "entry_ms": entry_ms, "exit_ms": exit_ms, "start_ms": start_ms, "end_ms": end_ms}
            descriptors[(event_index, member_index)] = descriptor
            by_symbol.setdefault(symbol, []).append(descriptor)
    return by_symbol, descriptors


def build_chart(descriptor: dict[str, Any], frame: Any, source_status: str) -> dict[str, Any]:
    asset = descriptor["asset"]
    period_ms = descriptor["period_ms"]
    start_ms, end_ms = descriptor["start_ms"], descriptor["end_ms"]
    result = aggregate_window(frame, start_ms, end_ms, period_ms)
    if asset == "BTC":
        event = descriptor["event"]
        markers = [marker("entry", event["entry_ms"], event["entry_price"], "saved_event_entry")]
        markers.extend(marker(kind, stamp, price, "saved_fill" if price is not None else "unavailable")
                       for kind, stamp, price in descriptor["markers"])
        for mark in markers:
            if mark["price"] is None and mark["kind"] != "entry":
                bar_price = exact_open(frame, mark["time_ms"])
                if bar_price is not None:
                    mark["price"] = bar_price
                    mark["price_source"] = "binance_5m_open_fallback"
        return {
            "symbol": "BTCUSDT", "bar_minutes": 60, "source_status": source_status,
            "window_start_ms": start_ms, "window_end_ms": end_ms, "markers": markers,
            **result,
        }
    member = descriptor["member"]
    entry_price = finite_number(member.get("entry_price"))
    exit_price = finite_number(member.get("exit_price"))
    if exit_price is None:
        exit_price = finite_number(member.get("selected_exit_open"))
    if entry_price is None:
        entry_price = exact_open(frame, descriptor["entry_ms"])
        entry_source = "binance_5m_open_fallback"
    else:
        entry_source = "saved_member_entry"
    if exit_price is None:
        exit_price = exact_open(frame, descriptor["exit_ms"])
        exit_source = "binance_5m_open_fallback" if exit_price is not None else "unavailable"
    else:
        exit_source = "saved_member_exit"
    markers = [marker("entry", descriptor["entry_ms"], entry_price, entry_source)]
    if descriptor["exit_ms"] is not None:
        markers.append(marker("exit_1", descriptor["exit_ms"], exit_price, exit_source))
    return {
        "symbol": descriptor["symbol"], "bar_minutes": 15, "source_status": source_status,
        "window_start_ms": start_ms, "window_end_ms": end_ms, "markers": markers,
        **result,
    }


def event_block(event: dict[str, Any], charts: dict[tuple[int, int | None], dict[str, Any]]) -> dict[str, Any]:
    row = event["input"]
    base = {
        "schema": 1,
        "event_id": event["event_id"],
        "asset": event["asset"],
        "source": "Binance USD-M",
        "entry_time": iso_utc(event["entry_ms"]),
        "entry_price": event["entry_price"] if event["asset"] == "BTC" else None,
        "net_pct": finite_number(row.get("net_pct")),
        "funded_pct": finite_number(row.get("funded_pct")),
        "bar_minutes": PERIOD_MS[event["asset"]] // 60000,
        "chart_note": "OHLC aggregated only from complete Binance USD-M 5m source candles; missing buckets are omitted, never interpolated.",
    }
    if event["asset"] == "BTC":
        chart = charts.get((event["index"], None))
        if chart is None:
            raise ChartBuildError(f"missing chart job for BTC event {event['event_id']}")
        base.update({
            "exit_legs": event["exit_legs"],
            "exit_policy": row.get("exit_policy"),
            "event_source": row.get("source"),
            "markers": chart["markers"],
            "chart": chart,
            "members": [],
            "member_coverage": None,
        })
    else:
        members = []
        for member_index, source_member in enumerate(event["members"]):
            chart = charts.get((event["index"], member_index))
            if chart is None:
                raise ChartBuildError(f"missing member chart job for {event['event_id']} member {member_index}")
            exit_ms = chart["markers"][-1]["time_ms"] if len(chart["markers"]) > 1 else None
            member_entry_ms = chart["markers"][0]["time_ms"] if chart["markers"] else event["entry_ms"]
            members.append({
                "member_id": source_member.get("member_id"),
                "symbol": chart["symbol"],
                "entry_time": iso_utc(member_entry_ms),
                "entry_price": chart["markers"][0]["price"] if chart["markers"] else None,
                "exit_time": iso_utc(exit_ms),
                "exit_price": chart["markers"][-1]["price"] if len(chart["markers"]) > 1 else None,
                "net_pct": finite_number(source_member.get("selected_net_pct", source_member.get("net_pct"))),
                "funded_net_pct": finite_number(source_member.get("selected_funded_net_pct", source_member.get("funded_net_pct"))),
                "outcome_complete": source_member.get("outcome_complete"),
                "funding_complete": source_member.get("funding_complete"),
                "markers": chart["markers"],
                "chart": chart,
            })
        coverage = row.get("member_coverage")
        base.update({
            "entry_price": None,
            "exit_price": None,
            "markers": [],
            "chart": None,
            "members": members,
            "member_coverage": coverage,
        })
    return base


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def input_receipt(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"path": safe_repo_path(path), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}


def build(events_path: Path = DEFAULT_EVENTS, output_dir: Path = DEFAULT_OUTPUT,
          expected_events: int = EXPECTED_EVENTS, expected_2026: int = EXPECTED_2026_EVENTS,
          pre_context_ms: int = CONTEXT_MS, post_context_ms: int = CONTEXT_MS) -> dict[str, Any]:
    if pre_context_ms != post_context_ms:
        raise ChartBuildError("pre/post context must currently be the same duration")
    if not events_path.is_file():
        raise ChartBuildError(f"normalized event ledger not found: {events_path}")
    try:
        raw = json.loads(events_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChartBuildError(f"could not read normalized event ledger: {exc}") from exc
    events = [normalized_event(row, n) for n, row in enumerate(event_rows(raw))]
    ids = [event["event_id"] for event in events]
    if len(ids) != len(set(ids)):
        raise ChartBuildError("event_id must be unique in normalized events[]")
    count_2026 = sum(datetime.fromtimestamp(event["entry_ms"] / 1000, tz=timezone.utc).year == 2026 for event in events)
    if expected_events >= 0 and len(events) != expected_events:
        raise ChartBuildError(f"expected {expected_events} events, received {len(events)}")
    if expected_2026 >= 0 and count_2026 != expected_2026:
        raise ChartBuildError(f"expected {expected_2026} 2026 events, received {count_2026}")

    by_symbol, _ = chart_windows(events, pre_context_ms)
    charts: dict[tuple[int, int | None], dict[str, Any]] = {}
    source_inventory: dict[str, Any] = {}
    source_symbol_status: dict[str, str] = {}
    for n, (symbol, jobs) in enumerate(sorted(by_symbol.items()), start=1):
        asset = "BTC" if symbol == "BTCUSDT" else "ALT"
        frame, receipt_info = read_symbol_bars(symbol, asset, [(job["start_ms"], job["end_ms"]) for job in jobs])
        source_inventory[symbol] = receipt_info
        source_symbol_status[symbol] = receipt_info["status"]
        for job in jobs:
            key = (job["event"]["index"], job.get("member_index"))
            chart = build_chart(job, frame, receipt_info["status"])
            if receipt_info.get("conflict_bars"):
                chart["source_conflict_bars_for_symbol"] = receipt_info["conflict_bars"]
            charts[key] = chart
        if n == 1 or n % 25 == 0 or n == len(by_symbol):
            print(f"[charts] symbols {n}/{len(by_symbol)}; bars={sum(len(x.get('bars', [])) for x in charts.values())}", file=sys.stderr)

    event_files: list[dict[str, Any]] = []
    expected_block_names: set[str] = set()
    totals = {"BTC_events": 0, "ALT_events": 0, "BTC_bars": 0, "ALT_member_charts": 0,
              "ALT_member_charts_with_bars": 0, "ALT_bars": 0, "missing_charts": 0}
    for event in events:
        block = event_block(event, charts)
        event_key = event_timestamp_id(event["asset"], event["entry_ms"])
        if not FILE_KEY_RE.fullmatch(event_key):
            raise ChartBuildError(f"unsafe event file key: {event_key!r}")
        relpath = Path("events") / f"{event_key}.json.gz"
        packed = gzip_bytes(compact_json(block))
        target = output_dir / relpath
        atomic_write(target, packed)
        expected_block_names.add(target.name)
        available_charts = 0
        total_bars = 0
        if event["asset"] == "BTC":
            totals["BTC_events"] += 1
            chart = block["chart"]
            available_charts = int(bool(chart["bars"]))
            total_bars = len(chart["bars"])
            totals["BTC_bars"] += total_bars
            if not chart["bars"]:
                totals["missing_charts"] += 1
        else:
            totals["ALT_events"] += 1
            totals["ALT_member_charts"] += len(block["members"])
            available_charts = sum(bool(member["chart"]["bars"]) for member in block["members"])
            total_bars = sum(len(member["chart"]["bars"]) for member in block["members"])
            totals["ALT_member_charts_with_bars"] += available_charts
            totals["ALT_bars"] += total_bars
            totals["missing_charts"] += len(block["members"]) - available_charts
        event_files.append({
            "event_id": event["event_id"],
            "asset": event["asset"],
            "entry_time": iso_utc(event["entry_ms"]),
            "path": relpath.as_posix(),
            "bytes": len(packed),
            "uncompressed_bytes": len(gzip.decompress(packed)),
            "sha256": sha256_bytes(packed),
            "charts": 1 if event["asset"] == "BTC" else len(block["members"]),
            "available_charts": available_charts,
            "bars": total_bars,
        })

    source_payload = {
        "schema": 1,
        "source": "Binance USD-M",
        "interval": "5m",
        "input_data_hashes": "not computed; local source path, byte size, and mtime receipts only",
        "symbols": source_inventory,
    }
    source_packed = gzip_bytes(compact_json(source_payload))
    source_relpath = "sources.json.gz"
    atomic_write(output_dir / source_relpath, source_packed)

    manifest = {
        "schema": 1,
        "strategy": "breadth-ema20",
        "source": "Binance USD-M",
        "source_interval": "5m",
        "timezone": "UTC",
        "bar_time": "UTC candle open milliseconds",
        "bars_encoding": "[open_time_ms, open, high, low, close]",
        "chart_period_minutes": {"BTC": 60, "ALT": 15},
        "context_hours_before_and_after": pre_context_ms / 3_600_000,
        "chart_policy": "Only complete aggregate buckets are emitted. Missing Binance 5m bars are omitted, never interpolated or replaced by another symbol.",
        "input": input_receipt(events_path),
        "source_receipts": {
            "path": source_relpath, "bytes": len(source_packed),
            "uncompressed_bytes": len(gzip.decompress(source_packed)), "sha256": sha256_bytes(source_packed),
        },
        "totals": totals,
        "source_symbols": len(source_inventory),
        "event_count": len(events),
        "events_2026": count_2026,
        "events": event_files,
    }
    manifest_bytes = compact_json(manifest)
    atomic_write(output_dir / "manifest.json", manifest_bytes)

    # Remove stale chart blocks only after all replacement blocks and the new
    # manifest have been written successfully. This directory is builder-owned.
    event_dir = output_dir / "events"
    if event_dir.exists():
        for stale in event_dir.glob("*.json.gz"):
            if stale.name not in expected_block_names:
                stale.unlink()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS, help="normalized events[] JSON source")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="builder-owned static chart directory")
    parser.add_argument("--expected-events", type=int, default=EXPECTED_EVENTS, help="set -1 to skip total count check")
    parser.add_argument("--expected-2026", type=int, default=EXPECTED_2026_EVENTS, help="set -1 to skip 2026 count check")
    parser.add_argument("--context-hours", type=float, default=3.0, help="symmetric chart context before entry and after last exit")
    args = parser.parse_args()
    if not math.isfinite(args.context_hours) or args.context_hours < 0:
        parser.error("--context-hours must be a finite non-negative number")
    context_ms = int(args.context_hours * 3_600_000)
    try:
        manifest = build(args.events, args.output_dir, args.expected_events, args.expected_2026,
                         pre_context_ms=context_ms, post_context_ms=context_ms)
    except (ChartBuildError, OSError) as exc:
        parser.exit(2, f"build failed: {exc}\n")
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "events": manifest["event_count"],
        "events_2026": manifest["events_2026"],
        "source_symbols": manifest["source_symbols"],
        "totals": manifest["totals"],
        "manifest": str(args.output_dir / "manifest.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
