#!/usr/bin/env python3
"""Audit frozen H1 first touches against native OKX 5m event windows.

The H1 prefix from the end of the first complete NY week through the recorded
touch hour must be complete and contain no earlier same-side touch. The 5m
touch-hour OHLC must then aggregate to the frozen H1 OHLC before an event can
enter the descriptive A/B/C sample. This script does not simulate trades.
"""

from __future__ import annotations

import csv
import gzip
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CONFIG_PATH = HERE / "config.json"
UTC = timezone.utc
NY = ZoneInfo("America/New_York")
BAR_MS = 5 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
OHLC = ("open", "high", "low", "close")


def decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, UTC)


def iso_utc(value: datetime | None) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds") if value else ""


def read_csv(path: Path, compressed: bool = False) -> list[dict[str, str]]:
    opener = gzip.open if compressed else open
    with opener(path, "rt", newline="") as handle:
        return list(csv.DictReader(handle))


def first_full_week_ready(month: str) -> datetime:
    """Return next Monday 00:00 NY after the first full Monday-Sunday week."""
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)
    next_month = date(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1)
    day = month_start
    while day < next_month:
        if day.weekday() == 0 and day + timedelta(days=6) < next_month:
            return datetime.combine(day + timedelta(days=7), time.min, NY).astimezone(UTC)
        day += timedelta(days=1)
    raise ValueError(f"No complete NY Monday-Sunday week in {month}")


def rail_touched(bar: dict[str, Any], side: str, level: Decimal) -> bool:
    return decimal(bar["low"]) <= level if side == "lower" else decimal(bar["high"]) >= level


def near_touched(bar: dict[str, Any], side: str, boundary: Decimal) -> bool:
    return decimal(bar["high"]) >= boundary if side == "lower" else decimal(bar["low"]) <= boundary


def inside(close: Decimal, side: str, level: Decimal) -> bool:
    return close > level if side == "lower" else close < level


def outside(close: Decimal, side: str, level: Decimal) -> bool:
    return close < level if side == "lower" else close > level


def relation(close: Decimal, side: str, level: Decimal) -> str:
    if close == level:
        return "equal"
    return "inside" if inside(close, side, level) else "outside"


def decimal_match(a: Decimal, b: Decimal, rel_tol: Decimal, abs_tol: Decimal) -> tuple[bool, bool, Decimal]:
    difference = abs(a - b)
    exact = difference == 0
    within = difference <= max(abs_tol, abs(b) * rel_tol)
    return exact, within, difference


def read_h1(path: Path) -> tuple[dict[int, dict[str, str]], dict[str, Any]]:
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"time", *OHLC}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing H1 fields {sorted(missing)}")
        bars: dict[int, dict[str, str]] = {}
        ordered: list[int] = []
        for row in reader:
            ts = int(row["time"])
            if ts in bars:
                raise ValueError(f"{path}: duplicate H1 timestamp {ts}")
            if ts % HOUR_MS:
                raise ValueError(f"{path}: H1 timestamp off UTC hour grid {ts}")
            for key in OHLC:
                decimal(row[key])
            bars[ts] = row
            ordered.append(ts)
    if ordered != sorted(ordered):
        raise ValueError(f"{path}: H1 timestamps not ascending")
    return bars, {
        "rows": len(ordered),
        "first_utc": iso_utc(from_ms(ordered[0])) if ordered else "",
        "last_utc": iso_utc(from_ms(ordered[-1])) if ordered else "",
    }


def blank_h1_result() -> dict[str, Any]:
    return {
        "h1_source_file": "",
        "h1_source_rows": 0,
        "h1_source_first_utc": "",
        "h1_source_last_utc": "",
        "ready_utc_derived": "",
        "h1_expected_bars_inclusive": 0,
        "h1_observed_bars_inclusive": 0,
        "h1_missing_bars_inclusive": 0,
        "h1_prior_same_side_touch_count": 0,
        "h1_first_prior_same_side_touch_utc": "",
        "h1_event_open": "",
        "h1_event_high": "",
        "h1_event_low": "",
        "h1_event_close": "",
        "h1_event_hour_touched": "",
        "h1_first_touch_validation_status": "missing_h1_source",
    }


def audit_h1_event(event: dict[str, str], bars: dict[int, dict[str, str]] | None,
                   source_path: str, source_meta: dict[str, Any] | None) -> dict[str, Any]:
    result = blank_h1_result()
    result["h1_source_file"] = source_path
    if source_meta:
        result["h1_source_rows"] = source_meta["rows"]
        result["h1_source_first_utc"] = source_meta["first_utc"]
        result["h1_source_last_utc"] = source_meta["last_utc"]
    if bars is None:
        return result

    touch = parse_utc(event["touch"])
    if touch.minute or touch.second or touch.microsecond:
        raise ValueError(f"Event is not on an H1 open: {event['event_id']} {event['touch']}")
    if touch.astimezone(NY).strftime("%Y-%m") != event["month"]:
        raise ValueError(f"Touch time/month mismatch: {event['event_id']}")
    ready = first_full_week_ready(event["month"])
    ready_ms = int(ready.timestamp() * 1000)
    touch_ms = int(touch.timestamp() * 1000)
    if ready_ms > touch_ms:
        raise ValueError(f"Derived ready time is after touch: {event['event_id']}")

    expected_times = list(range(ready_ms, touch_ms + HOUR_MS, HOUR_MS))
    present = [ts for ts in expected_times if ts in bars]
    prior = [(ts, bars[ts]) for ts in expected_times[:-1] if ts in bars and rail_touched(bars[ts], event["side"], decimal(event["level"]))]
    current = bars.get(touch_ms)
    touched = bool(current and rail_touched(current, event["side"], decimal(event["level"])))
    result.update({
        "ready_utc_derived": iso_utc(ready),
        "h1_expected_bars_inclusive": len(expected_times),
        "h1_observed_bars_inclusive": len(present),
        "h1_missing_bars_inclusive": len(expected_times) - len(present),
        "h1_prior_same_side_touch_count": len(prior),
        "h1_first_prior_same_side_touch_utc": iso_utc(from_ms(prior[0][0])) if prior else "",
        "h1_event_open": current["open"] if current else "",
        "h1_event_high": current["high"] if current else "",
        "h1_event_low": current["low"] if current else "",
        "h1_event_close": current["close"] if current else "",
        "h1_event_hour_touched": str(touched).lower() if current else "",
    })
    if prior:
        status = "earlier_h1_same_side_touch"
    elif current is None:
        status = "missing_h1_touch_hour"
    elif len(present) != len(expected_times):
        status = "incomplete_h1_ready_to_touch_prefix"
    elif not touched:
        status = "frozen_touch_hour_does_not_touch_rail"
    else:
        status = "complete_h1_first_touch_no_prior_same_side_touch"
    result["h1_first_touch_validation_status"] = status
    return result


def read_h1_manifest(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows = read_csv(path, compressed=True)
    manifest: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["symbol"], row["cohort"])
        if key in manifest:
            raise ValueError(f"Duplicate H1 manifest key {key}")
        manifest[key] = row
    return manifest


def audit_h1(events: list[dict[str, str]], manifest: dict[tuple[str, str], dict[str, str]]) -> dict[str, dict[str, Any]]:
    by_source: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for event in events:
        by_source[(event["symbol"], event["cohort"])].append(event)
    result: dict[str, dict[str, Any]] = {}
    for key, source_events in by_source.items():
        manifest_row = manifest.get(key)
        if not manifest_row:
            for event in source_events:
                result[event["event_id"]] = audit_h1_event(event, None, "", None)
            continue
        relative = manifest_row["file"]
        path = REPO / relative
        if not path.is_file():
            for event in source_events:
                result[event["event_id"]] = audit_h1_event(event, None, relative, None)
            continue
        bars, meta = read_h1(path)
        for event in source_events:
            result[event["event_id"]] = audit_h1_event(event, bars, relative, meta)
    return result


def read_event_ledger(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    rows = read_csv(path, compressed=True)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        event_id = row["event_id"]
        if event_id in result:
            raise ValueError(f"Duplicate native event ledger row {event_id}")
        result[event_id] = row
    return result


def read_v1_paths(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    rows = read_csv(path)
    return {row["event_id"]: row for row in rows}


def event_window_path(directory: Path, event: dict[str, str]) -> Path:
    return directory / event["symbol"] / f"{event['month']}-{event['side']}.json.gz"


def read_archive_window(path: Path) -> tuple[dict[str, Any] | None, dict[int, dict[str, Any]]]:
    if not path.is_file():
        return None, {}
    with gzip.open(path, "rt") as handle:
        data = json.load(handle)
    bars: dict[int, dict[str, Any]] = {}
    for values in data.get("candles", []):
        if len(values) < 5:
            raise ValueError(f"Malformed archive candle in {path}")
        ts = int(values[0])
        if ts in bars:
            raise ValueError(f"Duplicate archive candle timestamp {ts} in {path}")
        bars[ts] = {
            "ts_ms": ts,
            "open": str(values[1]),
            "high": str(values[2]),
            "low": str(values[3]),
            "close": str(values[4]),
            "confirm": "1",
            "source": data.get("source", "okx_month_archive"),
        }
    return data, bars


def parse_native_rows(event: dict[str, str], rows: list[dict[str, str]]) -> dict[int, dict[str, Any]]:
    event_hour_ms = int(parse_utc(event["touch"]).timestamp() * 1000)
    bars: dict[int, dict[str, Any]] = {}
    ordered_ts: list[int] = []
    for row in rows:
        ts = int(row["ts_ms"])
        if ts % BAR_MS:
            raise ValueError(f"Native candle off 5m grid: {event['event_id']} {ts}")
        if ts in bars:
            raise ValueError(f"Duplicate native candle timestamp: {event['event_id']} {ts}")
        if row.get("ts_utc"):
            parsed_ts = int(parse_utc(row["ts_utc"]).timestamp() * 1000)
            if parsed_ts != ts:
                raise ValueError(f"Native ts_ms/ts_utc mismatch: {event['event_id']} {ts}")
        if row.get("bar_offset_from_touch_hour") not in (None, ""):
            expected_offset = (ts - event_hour_ms) // BAR_MS
            if int(row["bar_offset_from_touch_hour"]) != expected_offset:
                raise ValueError(f"Native offset/timestamp mismatch: {event['event_id']} {ts}")
        bar = {**row, "ts_ms": ts}
        for key in OHLC:
            decimal(bar[key])
        if decimal(bar["low"]) > min(decimal(bar["open"]), decimal(bar["close"])):
            raise ValueError(f"Invalid native low/OHLC: {event['event_id']} {ts}")
        if decimal(bar["high"]) < max(decimal(bar["open"]), decimal(bar["close"])):
            raise ValueError(f"Invalid native high/OHLC: {event['event_id']} {ts}")
        bars[ts] = bar
        ordered_ts.append(ts)
    if ordered_ts != sorted(ordered_ts):
        raise ValueError(f"Native event candles are not ascending by ts_ms: {event['event_id']}")
    return bars


def iter_native_groups(path: Path) -> Iterator[tuple[str, list[dict[str, str]]]]:
    """Stream the event-clustered candle file without retaining all 5m rows."""
    if not path.is_file():
        return
    seen: set[str] = set()
    current_id: str | None = None
    current: list[dict[str, str]] = []
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"event_id", "ts_ms", "open", "high", "low", "close", "confirm", "source"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Native candles missing fields {sorted(missing)}")
        for row in reader:
            event_id = row["event_id"]
            if current_id is None:
                current_id = event_id
            if event_id != current_id:
                if event_id in seen:
                    raise ValueError(f"Native candles are not clustered by event_id: {event_id}")
                seen.add(current_id)
                yield current_id, current
                current_id, current = event_id, []
            current.append(row)
        if current_id is not None:
            yield current_id, current


def window_rows(bars: dict[int, dict[str, Any]], start_ms: int, count: int,
                month: str) -> tuple[list[dict[str, Any]] | None, str]:
    window: list[dict[str, Any]] = []
    for index in range(count):
        ts = start_ms + index * BAR_MS
        bar = bars.get(ts)
        if bar is None:
            return None, "missing_bar"
        if from_ms(ts).astimezone(NY).strftime("%Y-%m") != month:
            return None, "ny_month_boundary"
        if str(bar.get("confirm", "")).strip() == "0":
            return None, "unconfirmed_bar"
        window.append(bar)
    return window, "complete"


def confirm_basis(bars: Iterable[dict[str, Any]], as_of_ms: int) -> str:
    flags = [str(bar.get("confirm", "")).strip() for bar in bars]
    if any(flag == "0" for flag in flags):
        return "contains_unconfirmed_bar"
    if flags and all(flag == "1" for flag in flags):
        return "exchange_confirmed"
    if flags and all(flag in ("", "1") for flag in flags):
        return "historical_closed_no_exchange_confirm_flag" if all(
            int(bar["ts_ms"]) + BAR_MS <= as_of_ms for bar in bars
        ) else "confirm_flag_missing_before_bar_close"
    return "mixed_confirm_flags"


def match_hour_ohlc(event_hour: list[dict[str, Any]] | None, h1: dict[str, Any] | None,
                    rel_tol: Decimal, abs_tol: Decimal) -> tuple[str, dict[str, str], dict[str, str]]:
    if event_hour is None:
        return "incomplete_native_touch_hour", {}, {}
    if h1 is None:
        return "missing_original_h1_touch_bar", {}, {}
    aggregated = {
        "open": decimal(event_hour[0]["open"]),
        "high": max(decimal(row["high"]) for row in event_hour),
        "low": min(decimal(row["low"]) for row in event_hour),
        "close": decimal(event_hour[-1]["close"]),
    }
    differences: dict[str, str] = {}
    exact_all = True
    within_all = True
    for key in OHLC:
        target = decimal(h1[key])
        exact, within, diff = decimal_match(aggregated[key], target, rel_tol, abs_tol)
        exact_all &= exact
        within_all &= within
        differences[key] = str(diff)
    status = "exact" if exact_all else "within_tolerance" if within_all else "mismatch"
    return status, {key: str(value) for key, value in aggregated.items()}, differences


def classify_path(event: dict[str, str], bars: dict[int, dict[str, Any]],
                  h1: dict[str, Any] | None, rel_tol: Decimal, abs_tol: Decimal,
                  as_of_ms: int) -> dict[str, Any]:
    touch_ms = int(parse_utc(event["touch"]).timestamp() * 1000)
    side, month = event["side"], event["month"]
    level = decimal(event["level"])
    boundary = decimal(event["low"] if side == "lower" else event["high"])
    output: dict[str, Any] = {
        "native_5m_bar_count": len(bars),
        "native_5m_first_utc": iso_utc(from_ms(min(bars))) if bars else "",
        "native_5m_last_utc": iso_utc(from_ms(max(bars))) if bars else "",
        "native_5m_source_values": "|".join(sorted({str(bar.get("source", "")) for bar in bars.values()})),
        "native_confirm_basis_touch_hour": "",
        "touch_hour_5m_bars_present": 0,
        "touch_hour_native_status": "missing_native_candles" if not bars else "incomplete_native_touch_hour",
        "h1_5m_ohlc_match_status": "incomplete_native_touch_hour",
        "h1_5m_aggregate_open": "",
        "h1_5m_aggregate_high": "",
        "h1_5m_aggregate_low": "",
        "h1_5m_aggregate_close": "",
        "h1_5m_open_abs_diff": "",
        "h1_5m_high_abs_diff": "",
        "h1_5m_low_abs_diff": "",
        "h1_5m_close_abs_diff": "",
        "first_touch_5m_utc": "",
        "first_touch_5m_index_in_hour": "",
        "first_touch_5m_open": "",
        "first_touch_5m_high": "",
        "first_touch_5m_low": "",
        "first_touch_5m_close": "",
        "first_touch_prefix_status": "not_checked",
        "candidate_classification_status": "not_checked",
        "candidate_category": "",
        "first_reclaim_5m_utc": "",
        "first_reclaim_index_0_to_5": "",
        "first_close_relation": "",
        "equal_rail_closes_6bars": "",
        "near_boundary_before_reclaim": "",
        "near_boundary_on_reclaim_bar": "",
        "post_reclaim_1h_status": "not_A_or_B",
        "post_reclaim_1h_retouch_outer": "",
        "post_reclaim_1h_any_close_outside": "",
        "post_reclaim_1h_end_close_outside": "",
        "post_reclaim_1h_equal_close_count": "",
        "near_boundary_post_reclaim_1h": "",
        "near_boundary_first_hit_phase": "",
        "near_boundary_first_hit_utc": "",
        "next_bar_open": "",
        "mfe_from_next_open_pct": "",
        "mae_from_next_open_pct": "",
        "one_hour_end_close_inward": "",
        "one_hour_end_close": "",
    }
    event_hour, hour_status = window_rows(bars, touch_ms, 12, month)
    output["touch_hour_5m_bars_present"] = sum(touch_ms + i * BAR_MS in bars for i in range(12))
    output["touch_hour_native_status"] = hour_status
    if event_hour is None:
        return output

    output["native_confirm_basis_touch_hour"] = confirm_basis(event_hour, as_of_ms)
    match, aggregated, differences = match_hour_ohlc(event_hour, h1, rel_tol, abs_tol)
    output["h1_5m_ohlc_match_status"] = match
    for key in OHLC:
        output[f"h1_5m_aggregate_{key}"] = aggregated.get(key, "")
        output[f"h1_5m_{key}_abs_diff"] = differences.get(key, "")

    root_index = next((index for index, bar in enumerate(event_hour) if rail_touched(bar, side, level)), None)
    if root_index is None:
        output["first_touch_prefix_status"] = "no_5m_touch_in_h1_touch_hour"
        output["candidate_classification_status"] = "no_5m_touch_in_h1_touch_hour"
        return output
    root_ms = touch_ms + root_index * BAR_MS
    root = event_hour[root_index]
    output.update({
        "first_touch_5m_utc": iso_utc(from_ms(root_ms)),
        "first_touch_5m_index_in_hour": root_index,
        "first_touch_5m_open": root["open"],
        "first_touch_5m_high": root["high"],
        "first_touch_5m_low": root["low"],
        "first_touch_5m_close": root["close"],
        "first_touch_prefix_status": "complete_no_earlier_touch_in_hour",
    })
    six, six_status = window_rows(bars, root_ms, 6, month)
    if six is None:
        output["candidate_classification_status"] = "six_bar_window_crosses_ny_month_end" if six_status == "ny_month_boundary" else f"incomplete_six_bar_window_{six_status}"
        return output
    closes = [decimal(bar["close"]) for bar in six]
    inside_indexes = [index for index, close in enumerate(closes) if inside(close, side, level)]
    first_relation = relation(closes[0], side, level)
    category = "A" if inside_indexes and inside_indexes[0] == 0 else "B" if inside_indexes else "C"
    reclaim_index = inside_indexes[0] if inside_indexes else None
    output.update({
        "candidate_classification_status": "eligible_6bar_classification",
        "candidate_category": category,
        "first_close_relation": first_relation,
        "equal_rail_closes_6bars": sum(close == level for close in closes),
    })
    if reclaim_index is None:
        output["post_reclaim_1h_status"] = "not_A_or_B"
        return output

    reclaim_bar = six[reclaim_index]
    reclaim_ms = root_ms + reclaim_index * BAR_MS
    output["first_reclaim_5m_utc"] = iso_utc(from_ms(reclaim_ms))
    output["first_reclaim_index_0_to_5"] = reclaim_index
    pre_reclaim_hit = any(near_touched(bar, side, boundary) for bar in six[:reclaim_index])
    reclaim_hit = near_touched(reclaim_bar, side, boundary)
    output["near_boundary_before_reclaim"] = str(pre_reclaim_hit).lower()
    output["near_boundary_on_reclaim_bar"] = str(reclaim_hit).lower()

    first_hit: tuple[str, int] | None = None
    for bar in six[:reclaim_index]:
        if near_touched(bar, side, boundary):
            first_hit = ("before_reclaim", int(bar["ts_ms"]))
            break
    if first_hit is None and reclaim_hit:
        first_hit = ("reclaim_bar", reclaim_ms)

    future, future_status = window_rows(bars, reclaim_ms + BAR_MS, 12, month)
    if future is None:
        output["post_reclaim_1h_status"] = f"incomplete_{future_status}"
        if first_hit:
            output["near_boundary_first_hit_phase"] = first_hit[0]
            output["near_boundary_first_hit_utc"] = iso_utc(from_ms(first_hit[1]))
        return output

    output["post_reclaim_1h_status"] = "complete"
    retouch = any(rail_touched(bar, side, level) for bar in future)
    close_outside = any(outside(decimal(bar["close"]), side, level) for bar in future)
    end_close_outside = outside(decimal(future[-1]["close"]), side, level)
    equal_closes = sum(decimal(bar["close"]) == level for bar in future)
    post_boundary_hit = any(near_touched(bar, side, boundary) for bar in future)
    output["post_reclaim_1h_retouch_outer"] = str(retouch).lower()
    output["post_reclaim_1h_any_close_outside"] = str(close_outside).lower()
    output["post_reclaim_1h_end_close_outside"] = str(end_close_outside).lower()
    output["post_reclaim_1h_equal_close_count"] = equal_closes
    output["near_boundary_post_reclaim_1h"] = str(post_boundary_hit).lower()
    if first_hit is None and post_boundary_hit:
        hit_bar = next(bar for bar in future if near_touched(bar, side, boundary))
        first_hit = ("post_reclaim_1h", int(hit_bar["ts_ms"]))
    if first_hit:
        output["near_boundary_first_hit_phase"] = first_hit[0]
        output["near_boundary_first_hit_utc"] = iso_utc(from_ms(first_hit[1]))

    entry = decimal(future[0]["open"])
    highs = [decimal(bar["high"]) for bar in future]
    lows = [decimal(bar["low"]) for bar in future]
    end_close = decimal(future[-1]["close"])
    if side == "lower":
        mfe = (max(highs) / entry - 1) * 100
        mae = (min(lows) / entry - 1) * 100
        inward_end = end_close > entry
    else:
        mfe = (entry - min(lows)) / entry * 100
        mae = (entry - max(highs)) / entry * 100
        inward_end = end_close < entry
    output.update({
        "next_bar_open": str(entry),
        "mfe_from_next_open_pct": str(mfe),
        "mae_from_next_open_pct": str(mae),
        "one_hour_end_close_inward": str(inward_end).lower(),
        "one_hour_end_close": str(end_close),
    })
    return output


def audit_window_prefix(event: dict[str, str], bars: dict[int, dict[str, Any]]) -> dict[str, Any]:
    ready_ms = int(first_full_week_ready(event["month"]).timestamp() * 1000)
    touch_ms = int(parse_utc(event["touch"]).timestamp() * 1000)
    start_ms = max(ready_ms, touch_ms - 120 * BAR_MS)
    scan_times = list(range(start_ms, touch_ms, BAR_MS))
    observed = [(ts, bars[ts]) for ts in scan_times if ts in bars]
    level = decimal(event["level"])
    earlier = [(ts, bar) for ts, bar in observed if rail_touched(bar, event["side"], level)]
    full_prefix_expected = (touch_ms - ready_ms) // BAR_MS
    prefix_fits_window = ready_ms >= touch_ms - 120 * BAR_MS
    complete = prefix_fits_window and len(observed) == len(scan_times) and all(
        str(bar.get("confirm", "")).strip() != "0" for _, bar in observed
    )
    status = "earlier_touch" if earlier else "verified" if complete else "incomplete"
    return {
        "native_window_prior_5m_status": status,
        "native_window_prior_5m_expected_bars": full_prefix_expected,
        "native_window_prior_5m_scan_expected_bars": len(scan_times),
        "native_window_prior_5m_scan_observed_bars": len(observed),
        "native_window_prior_5m_full_prefix_fits_window": str(prefix_fits_window).lower(),
        "native_window_prior_5m_first_utc": iso_utc(from_ms(start_ms)),
        "native_window_prior_5m_first_same_side_touch_utc": iso_utc(from_ms(earlier[0][0])) if earlier else "",
    }


def compare_bar_maps(left: dict[int, dict[str, Any]], right: dict[int, dict[str, Any]],
                     rel_tol: Decimal, abs_tol: Decimal) -> dict[str, Any]:
    common = sorted(set(left) & set(right))
    exact = within = mismatch = 0
    field_mismatches = Counter()
    max_abs_difference = {key: Decimal(0) for key in OHLC}
    for ts in common:
        exact_bar, within_bar = True, True
        for key in OHLC:
            a, b = decimal(left[ts][key]), decimal(right[ts][key])
            e, w, difference = decimal_match(a, b, rel_tol, abs_tol)
            exact_bar &= e
            within_bar &= w
            if not w:
                field_mismatches[key] += 1
            max_abs_difference[key] = max(max_abs_difference[key], difference)
        exact += exact_bar
        within += (not exact_bar and within_bar)
        mismatch += not within_bar
    return {
        "common_bars": len(common),
        "exact_ohlc_bars": exact,
        "within_tolerance_ohlc_bars": within,
        "mismatched_ohlc_bars": mismatch,
        "mismatched_fields": dict(field_mismatches),
        "max_abs_difference": {key: str(value) for key, value in max_abs_difference.items()},
    }


def compare_v1(event: dict[str, str], native: dict[str, Any], v1: dict[str, str] | None,
               rel_tol: Decimal, abs_tol: Decimal) -> dict[str, Any]:
    result = {
        "v1_pre_h1_coverage_status": "",
        "v1_category": "",
        "v1_touch_5m_utc": "",
        "v1_future12_status": "",
        "v1_future12_near_boundary_hit": "",
        "v1_strict_pre_h1_verified": "false",
        "v1_native_root_match": "not_comparable",
        "v1_native_category_match": "not_comparable",
        "v1_native_future12_match": "not_comparable",
    }
    if not v1:
        return result
    result.update({
        "v1_pre_h1_coverage_status": v1.get("pre_h1_coverage_status", ""),
        "v1_category": v1.get("category", ""),
        "v1_touch_5m_utc": v1.get("touch_5m_open_utc", ""),
        "v1_future12_status": v1.get("future12_status", ""),
        "v1_future12_near_boundary_hit": v1.get("future12_near_boundary_hit", ""),
        "v1_strict_pre_h1_verified": str(v1.get("pre_h1_coverage_status") == "complete_no_same_side_5m_touch").lower(),
    })
    v1_touch, new_touch = result["v1_touch_5m_utc"], native.get("first_touch_5m_utc", "")
    if v1_touch and new_touch:
        same_time = parse_utc(v1_touch) == parse_utc(new_touch)
        v1_index = v1.get("touch_5m_index_in_hour", "")
        same_index = not v1_index or str(v1_index) == str(native.get("first_touch_5m_index_in_hour", ""))
        same_ohlc = True
        for key in OHLC:
            left, right = v1.get(f"touch_5m_{key}", ""), native.get(f"first_touch_5m_{key}", "")
            if not left or not right or not decimal_match(decimal(left), decimal(right), rel_tol, abs_tol)[1]:
                same_ohlc = False
        result["v1_native_root_match"] = "match" if same_time and same_index and same_ohlc else "mismatch"
    if result["v1_category"] and native.get("candidate_category"):
        result["v1_native_category_match"] = str(result["v1_category"] == native["candidate_category"]).lower()
    if v1.get("future12_status") in ("complete_hit", "complete_no_hit") and native.get("post_reclaim_1h_status") == "complete":
        v1_hit = v1.get("future12_status") == "complete_hit"
        result["v1_native_future12_match"] = str(v1_hit == (native.get("near_boundary_post_reclaim_1h") == "true")).lower()
    return result


def truth(value: Any) -> bool:
    return str(value).lower() == "true"


def median(values: list[Decimal]) -> str:
    if not values:
        return ""
    values = sorted(values)
    n = len(values)
    middle = n // 2
    value = values[middle] if n % 2 else (values[middle - 1] + values[middle]) / 2
    return str(value)


def summary_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [row for row in rows if row.get("primary_post_reclaim_1h_status") == "complete"]
    def rate(field: str) -> tuple[int, str]:
        yes = sum(truth(row.get(field, "")) for row in complete)
        return yes, f"{yes / len(complete):.6f}" if complete else ""
    retouch_n, retouch_rate = rate("primary_post_reclaim_1h_retouch_outer")
    close_n, close_rate = rate("primary_post_reclaim_1h_any_close_outside")
    end_close_n, end_close_rate = rate("primary_post_reclaim_1h_end_close_outside")
    target_n, target_rate = rate("primary_near_boundary_post_reclaim_1h")
    inward_n, inward_rate = rate("primary_one_hour_end_close_inward")
    pre_n = sum(truth(row.get("primary_near_boundary_before_reclaim", "")) for row in rows)
    reclaim_n = sum(truth(row.get("primary_near_boundary_on_reclaim_bar", "")) for row in rows)
    return {
        "A_or_B_n": len(rows),
        "post_reclaim_1h_complete_n": len(complete),
        "post_reclaim_1h_retouch_outer_n": retouch_n,
        "post_reclaim_1h_retouch_outer_rate": retouch_rate,
        "post_reclaim_1h_any_close_outside_n": close_n,
        "post_reclaim_1h_any_close_outside_rate": close_rate,
        "post_reclaim_1h_end_close_outside_n": end_close_n,
        "post_reclaim_1h_end_close_outside_rate": end_close_rate,
        "near_boundary_pre_reclaim_n": pre_n,
        "near_boundary_pre_reclaim_rate": f"{pre_n / len(rows):.6f}" if rows else "",
        "near_boundary_on_reclaim_bar_n": reclaim_n,
        "near_boundary_on_reclaim_bar_rate": f"{reclaim_n / len(rows):.6f}" if rows else "",
        "near_boundary_post_reclaim_1h_n": target_n,
        "near_boundary_post_reclaim_1h_rate": target_rate,
        "mfe_from_next_open_median_pct": median([decimal(row["primary_mfe_from_next_open_pct"]) for row in complete if row.get("primary_mfe_from_next_open_pct")]),
        "mae_from_next_open_median_pct": median([decimal(row["primary_mae_from_next_open_pct"]) for row in complete if row.get("primary_mae_from_next_open_pct")]),
        "one_hour_end_close_inward_n": inward_n,
        "one_hour_end_close_inward_rate": inward_rate,
    }


def group_specs(rows: list[dict[str, Any]]) -> list[tuple[str, tuple[str, ...]]]:
    return [
        ("overall", ()),
        ("side", ("side",)),
        ("period", ("period",)),
        ("side_period", ("side", "period")),
        ("symbol", ("symbol",)),
        ("symbol_side", ("symbol", "side")),
        ("symbol_period", ("symbol", "period")),
        ("symbol_side_period", ("symbol", "side", "period")),
    ]


def summarize_groups(rows: list[dict[str, Any]], category_mode: bool) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for group_type, keys in group_specs(rows):
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[tuple(str(row.get(key, "all")) for key in keys)].append(row)
        for key, subset in sorted(groups.items()):
            item: dict[str, Any] = {
                "group_type": group_type,
                "symbol": "all",
                "side": "all",
                "period": "all",
            }
            item.update(dict(zip(keys, key)))
            if category_mode:
                counts = Counter(row["category"] for row in subset)
                n = len(subset)
                item.update({
                    "eligible_n": n,
                    "A_n": counts["A"],
                    "B_n": counts["B"],
                    "C_n": counts["C"],
                    "A_pct": f"{100 * counts['A'] / n:.6f}" if n else "",
                    "B_pct": f"{100 * counts['B'] / n:.6f}" if n else "",
                    "C_pct": f"{100 * counts['C'] / n:.6f}" if n else "",
                })
            else:
                item.update(summary_metrics(subset))
            output.append(item)
    return output


def coverage_by_symbol(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    symbols = sorted({row["symbol"] for row in rows})
    for symbol in symbols:
        subset = [row for row in rows if row["symbol"] == symbol]
        categories = Counter(row["category"] for row in subset if row.get("category"))
        statuses = Counter(row["analysis_status"] for row in subset)
        result.append({
            "symbol": symbol,
            "cohort": "|".join(sorted({row["cohort"] for row in subset})),
            "source_events": len(subset),
            "h1_valid_first_touch": sum(row["h1_first_touch_validation_status"] == "complete_h1_first_touch_no_prior_same_side_touch" for row in subset),
            "native_touch_hour_complete": sum(row["touch_hour_native_status"] == "complete" for row in subset),
            "h1_native_ohlc_match": sum(row["h1_5m_ohlc_match_status"] in ("exact", "within_tolerance") for row in subset),
            "primary_source_h1_ohlc_match": sum(row["primary_h1_5m_ohlc_match_status"] in ("exact", "within_tolerance") for row in subset),
            "native_window_prior_5m_verified": sum(row["native_window_prior_5m_status"] == "verified" for row in subset),
            "native_window_prior_5m_earlier_touch": sum(row["native_window_prior_5m_status"] == "earlier_touch" for row in subset),
            "archive_prior_5m_verified": sum(row["archive_prior_5m_status"] == "verified" for row in subset),
            "archive_prior_5m_earlier_touch": sum(row["archive_prior_5m_status"] == "earlier_touch" for row in subset),
            "v1_strict_pre_h1_verified": sum(truth(row["v1_strict_pre_h1_verified"]) for row in subset),
            "primary_5m_source_counts": json.dumps(dict(Counter(row["primary_5m_source"] for row in subset)), ensure_ascii=False, separators=(",", ":")),
            "eligible_ABC": sum(row["analysis_status"] == "eligible_ABC" for row in subset),
            "A": categories["A"], "B": categories["B"], "C": categories["C"],
            "analysis_status_counts": json.dumps(dict(sorted(statuses.items())), ensure_ascii=False, separators=(",", ":")),
        })
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("\n")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_gzip_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        with gzip.open(path, "wt") as handle:
            handle.write("\n")
        return
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def enrich_event(event: dict[str, str], h1: dict[str, Any], fetch: dict[str, str] | None,
                 native_rows: list[dict[str, str]], archive_dir: Path,
                 v1: dict[str, str] | None, rel_tol: Decimal, abs_tol: Decimal,
                 as_of_ms: int) -> dict[str, Any]:
    bars = parse_native_rows(event, native_rows)
    window_prefix = audit_window_prefix(event, bars)
    h1_bar = None
    if h1.get("h1_event_open"):
        h1_bar = {key: h1[f"h1_event_{key}"] for key in OHLC}
    observed = classify_path(event, bars, h1_bar, rel_tol, abs_tol, as_of_ms)

    archive_path = event_window_path(archive_dir, event)
    archive_data, archive_bars = read_archive_window(archive_path)
    if archive_data and (archive_data.get("event_id") != event["event_id"] or archive_data.get("symbol") != event["symbol"]):
        raise ValueError(f"Archive window event identity mismatch: {event['event_id']}")
    archive_prior_status = str(archive_data.get("prior_5m_status", "unknown")) if archive_data else "missing_archive_window"
    archive_class = classify_path(event, archive_bars, h1_bar, rel_tol, abs_tol, as_of_ms) if archive_data else {}
    comparison = compare_bar_maps(bars, archive_bars, rel_tol, abs_tol) if archive_data else {
        "common_bars": 0, "exact_ohlc_bars": 0, "within_tolerance_ohlc_bars": 0,
        "mismatched_ohlc_bars": 0, "mismatched_fields": {},
        "max_abs_difference": {key: "" for key in OHLC},
    }
    v1_fields = compare_v1(event, observed, v1, rel_tol, abs_tol)

    h1_valid = h1["h1_first_touch_validation_status"] == "complete_h1_first_touch_no_prior_same_side_touch"
    direct_hour_match = observed["touch_hour_native_status"] == "complete" and observed["h1_5m_ohlc_match_status"] in ("exact", "within_tolerance")
    direct_hour_conflict = observed["touch_hour_native_status"] == "complete" and observed["h1_5m_ohlc_match_status"] == "mismatch"
    archive_hour_match = bool(archive_data) and archive_class.get("touch_hour_native_status") == "complete" and archive_class.get("h1_5m_ohlc_match_status") in ("exact", "within_tolerance")
    archive_prior_touch = archive_prior_status == "earlier_touch"
    native_prior_touch = window_prefix["native_window_prior_5m_status"] == "earlier_touch"
    v1_prior_touch = v1_fields["v1_pre_h1_coverage_status"] == "same_side_5m_touch_before_recorded_h1_hour"
    direct_prior_touch = archive_prior_touch or native_prior_touch or v1_prior_touch

    selected: dict[str, Any] = {}
    selected_source = "none"
    if direct_hour_match:
        selected, selected_source = observed, "okx_native_5m"
        archive_classification_complete = archive_class.get("candidate_classification_status") == "eligible_6bar_classification"
        same_archive_root = (
            observed.get("first_touch_5m_utc")
            and archive_class.get("first_touch_5m_utc")
            and parse_utc(observed["first_touch_5m_utc"]) == parse_utc(archive_class["first_touch_5m_utc"])
        )
        needs_archive_followup = (
            observed.get("candidate_category") in ("A", "B")
            and observed.get("post_reclaim_1h_status") != "complete"
            and archive_class.get("post_reclaim_1h_status") == "complete"
            and archive_class.get("candidate_category") == observed.get("candidate_category")
        )
        needs_archive_classification = (
            observed.get("candidate_classification_status") != "eligible_6bar_classification"
            and archive_classification_complete
        )
        if (needs_archive_followup or needs_archive_classification) and same_archive_root and archive_hour_match and not archive_prior_touch:
            selected, selected_source = archive_class, "okx_month_archive_1m_aggregated_5m_fallback"
    elif not direct_hour_conflict and archive_hour_match and not archive_prior_touch:
        selected, selected_source = archive_class, "okx_month_archive_1m_aggregated_5m_fallback"

    event_hour_ms = int(parse_utc(event["touch"]).timestamp() * 1000)
    period = "before_2025" if event_hour_ms < SPLIT_MS else "2025_or_later"
    ledger_count_match = "unavailable"
    if fetch and fetch.get("returned_bars", "") != "":
        ledger_count_match = str(int(fetch["returned_bars"]) == len(bars)).lower()
    output: dict[str, Any] = {
        **event,
        **h1,
        "period": period,
        "native_fetch_status": fetch.get("status", "missing_event_ledger_row") if fetch else "missing_event_ledger_row",
        "native_fetch_source": fetch.get("source", "") if fetch else "",
        "native_fetch_detail": fetch.get("detail", "") if fetch else "",
        "native_fetch_requested_bars": fetch.get("requested_bars", "") if fetch else "",
        "native_fetch_returned_bars": fetch.get("returned_bars", "") if fetch else "",
        "native_fetch_contiguous_confirmed_bars": fetch.get("contiguous_confirmed_bars", "") if fetch else "",
        "native_ledger_return_count_matches_candles": ledger_count_match,
        **window_prefix,
        **observed,
        "archive_prior_5m_status": archive_prior_status,
        "archive_source": str(archive_data.get("source", "")) if archive_data else "",
        "archive_native_common_bars": comparison["common_bars"],
        "archive_native_exact_ohlc_bars": comparison["exact_ohlc_bars"],
        "archive_native_within_tolerance_ohlc_bars": comparison["within_tolerance_ohlc_bars"],
        "archive_native_mismatched_ohlc_bars": comparison["mismatched_ohlc_bars"],
        "archive_native_open_mismatched_bars": comparison["mismatched_fields"].get("open", 0),
        "archive_native_high_mismatched_bars": comparison["mismatched_fields"].get("high", 0),
        "archive_native_low_mismatched_bars": comparison["mismatched_fields"].get("low", 0),
        "archive_native_close_mismatched_bars": comparison["mismatched_fields"].get("close", 0),
        **{f"archive_native_max_abs_diff_{key}": value for key, value in comparison["max_abs_difference"].items()},
        "archive_first_touch_5m_utc": archive_class.get("first_touch_5m_utc", ""),
        "archive_first_touch_5m_index_in_hour": archive_class.get("first_touch_5m_index_in_hour", ""),
        "archive_h1_5m_ohlc_match_status": archive_class.get("h1_5m_ohlc_match_status", ""),
        "archive_touch_hour_status": archive_class.get("touch_hour_native_status", ""),
        "archive_candidate_classification_status": archive_class.get("candidate_classification_status", ""),
        "archive_candidate_category": archive_class.get("candidate_category", ""),
        "archive_post_reclaim_1h_status": archive_class.get("post_reclaim_1h_status", ""),
        "archive_post_reclaim_1h_retouch_outer": archive_class.get("post_reclaim_1h_retouch_outer", ""),
        "archive_post_reclaim_1h_any_close_outside": archive_class.get("post_reclaim_1h_any_close_outside", ""),
        "archive_post_reclaim_1h_end_close_outside": archive_class.get("post_reclaim_1h_end_close_outside", ""),
        "archive_near_boundary_before_reclaim": archive_class.get("near_boundary_before_reclaim", ""),
        "archive_near_boundary_on_reclaim_bar": archive_class.get("near_boundary_on_reclaim_bar", ""),
        "archive_near_boundary_post_reclaim_1h": archive_class.get("near_boundary_post_reclaim_1h", ""),
        "archive_mfe_from_next_open_pct": archive_class.get("mfe_from_next_open_pct", ""),
        "archive_mae_from_next_open_pct": archive_class.get("mae_from_next_open_pct", ""),
        "archive_one_hour_end_close_inward": archive_class.get("one_hour_end_close_inward", ""),
        "archive_native_root_match": "not_comparable",
        "archive_native_root_time_match": "not_comparable",
        "archive_native_root_ohlc_match": "not_comparable",
        "archive_native_category_match": "not_comparable",
        **v1_fields,
    }
    output.update({f"direct5m_{key}": value for key, value in observed.items()})
    output.update({
        "primary_5m_source": selected_source,
        "primary_touch_hour_status": selected.get("touch_hour_native_status", ""),
        "primary_h1_5m_ohlc_match_status": selected.get("h1_5m_ohlc_match_status", ""),
        "primary_candidate_classification_status": selected.get("candidate_classification_status", ""),
        "primary_candidate_category": selected.get("candidate_category", ""),
        "primary_first_touch_5m_utc": selected.get("first_touch_5m_utc", ""),
        "primary_first_touch_5m_index_in_hour": selected.get("first_touch_5m_index_in_hour", ""),
        "primary_first_reclaim_5m_utc": selected.get("first_reclaim_5m_utc", ""),
        "primary_first_reclaim_index_0_to_5": selected.get("first_reclaim_index_0_to_5", ""),
        "primary_first_touch_prefix_status": selected.get("first_touch_prefix_status", ""),
        "primary_near_boundary_before_reclaim": selected.get("near_boundary_before_reclaim", ""),
        "primary_near_boundary_on_reclaim_bar": selected.get("near_boundary_on_reclaim_bar", ""),
        "primary_post_reclaim_1h_status": selected.get("post_reclaim_1h_status", ""),
        "primary_post_reclaim_1h_retouch_outer": selected.get("post_reclaim_1h_retouch_outer", ""),
        "primary_post_reclaim_1h_any_close_outside": selected.get("post_reclaim_1h_any_close_outside", ""),
        "primary_post_reclaim_1h_end_close_outside": selected.get("post_reclaim_1h_end_close_outside", ""),
        "primary_post_reclaim_1h_equal_close_count": selected.get("post_reclaim_1h_equal_close_count", ""),
        "primary_near_boundary_post_reclaim_1h": selected.get("near_boundary_post_reclaim_1h", ""),
        "primary_near_boundary_first_hit_phase": selected.get("near_boundary_first_hit_phase", ""),
        "primary_near_boundary_first_hit_utc": selected.get("near_boundary_first_hit_utc", ""),
        "primary_next_bar_open": selected.get("next_bar_open", ""),
        "primary_mfe_from_next_open_pct": selected.get("mfe_from_next_open_pct", ""),
        "primary_mae_from_next_open_pct": selected.get("mae_from_next_open_pct", ""),
        "primary_one_hour_end_close_inward": selected.get("one_hour_end_close_inward", ""),
        "primary_one_hour_end_close": selected.get("one_hour_end_close", ""),
    })
    if observed.get("first_touch_5m_utc") and archive_class.get("first_touch_5m_utc"):
        time_match = (
            parse_utc(observed["first_touch_5m_utc"]) == parse_utc(archive_class["first_touch_5m_utc"])
            and observed.get("first_touch_5m_index_in_hour") == archive_class.get("first_touch_5m_index_in_hour")
        )
        output["archive_native_root_time_match"] = str(time_match).lower()
        ohlc_match = bool(time_match)
        if time_match:
            for key in OHLC:
                left = observed.get(f"first_touch_5m_{key}", "")
                right = archive_class.get(f"first_touch_5m_{key}", "")
                if not left or not right or not decimal_match(decimal(left), decimal(right), rel_tol, abs_tol)[1]:
                    ohlc_match = False
        else:
            ohlc_match = False
        output["archive_native_root_ohlc_match"] = str(ohlc_match).lower()
        output["archive_native_root_match"] = str(time_match and ohlc_match).lower()
    if observed.get("candidate_category") and archive_class.get("candidate_category"):
        output["archive_native_category_match"] = str(observed["candidate_category"] == archive_class["candidate_category"]).lower()

    if not h1_valid:
        analysis_status = f"excluded_h1_{h1['h1_first_touch_validation_status']}"
    elif direct_prior_touch:
        analysis_status = "excluded_prior_same_side_5m_touch_found"
    elif ledger_count_match == "false" and selected_source == "okx_native_5m":
        analysis_status = "excluded_native_ledger_candle_count_mismatch"
    elif not selected and direct_hour_conflict:
        analysis_status = "direct_native_h1_5m_ohlc_mismatch_source_conflict"
    elif not selected:
        analysis_status = "no_complete_h1_matching_5m_touch_hour"
    elif selected["candidate_classification_status"] != "eligible_6bar_classification":
        analysis_status = selected["candidate_classification_status"]
    else:
        analysis_status = "eligible_ABC"
    output["analysis_status"] = analysis_status
    output["primary_classification_included"] = str(analysis_status == "eligible_ABC").lower()
    output["category"] = selected.get("candidate_category", "") if analysis_status == "eligible_ABC" else ""
    if direct_prior_touch:
        evidence_basis = "prior_same_side_5m_touch_found_excluded"
    elif window_prefix["native_window_prior_5m_status"] == "verified" or archive_prior_status == "verified" or v1_fields["v1_strict_pre_h1_verified"] == "true":
        evidence_basis = f"h1_complete_no_prior_touch;5m_prefix_verified;primary={selected_source}"
    else:
        evidence_basis = f"h1_complete_no_prior_touch;5m_prefix_incomplete_or_unavailable;primary={selected_source}"
    output["first_touch_evidence_basis"] = evidence_basis
    return output


def main() -> None:
    global SPLIT_MS
    config = json.loads(CONFIG_PATH.read_text())
    split_ms = int(parse_utc(config["split_utc"]).timestamp() * 1000)
    SPLIT_MS = split_ms
    rel_tol = decimal(config["h1_5m_match_tolerance"]["relative"])
    abs_tol = decimal(config["h1_5m_match_tolerance"]["absolute"])
    source_events_path = REPO / config["source_events"]
    h1_manifest_path = REPO / config["source_h1_manifest"]
    native_ledger_path = REPO / config["native_event_ledger"]
    native_candles_path = REPO / config["native_candles"]
    archive_dir = REPO / config["archive_windows_directory"]
    v1_path = REPO / config["v1_event_paths"]

    events = read_csv(source_events_path, compressed=True)
    event_by_id = {event["event_id"]: event for event in events}
    if len(event_by_id) != len(events):
        raise ValueError("Frozen source events contain duplicate event_id")
    manifest = read_h1_manifest(h1_manifest_path)
    h1_results = audit_h1(events, manifest)
    fetch_rows = read_event_ledger(native_ledger_path)
    v1_rows = read_v1_paths(v1_path)
    as_of_ms = int(datetime.now(UTC).timestamp() * 1000)

    paths: list[dict[str, Any]] = []
    seen_candle_events: set[str] = set()
    if native_candles_path.is_file():
        for event_id, candle_rows in iter_native_groups(native_candles_path):
            event = event_by_id.get(event_id)
            if event is None:
                raise ValueError(f"Native candles contain unknown event_id {event_id}")
            seen_candle_events.add(event_id)
            paths.append(enrich_event(
                event, h1_results[event_id], fetch_rows.get(event_id), candle_rows,
                archive_dir, v1_rows.get(event_id), rel_tol, abs_tol, as_of_ms,
            ))
    else:
        print(f"Native candle file is missing: {native_candles_path}")

    for event in events:
        event_id = event["event_id"]
        if event_id not in seen_candle_events:
            paths.append(enrich_event(
                event, h1_results[event_id], fetch_rows.get(event_id), [],
                archive_dir, v1_rows.get(event_id), rel_tol, abs_tol, as_of_ms,
            ))
    paths.sort(key=lambda row: (row["touch"], row["symbol"], row["side"]))

    eligible_candidates = [row for row in paths if row["analysis_status"] == "eligible_ABC"]
    touch_counts = Counter(row["primary_first_touch_5m_utc"] for row in eligible_candidates if row.get("primary_first_touch_5m_utc"))
    reclaim_counts = Counter(row["primary_first_reclaim_5m_utc"] for row in eligible_candidates if row.get("primary_first_reclaim_5m_utc"))
    for row in paths:
        row["same_5m_touch_timestamp_event_count"] = touch_counts.get(row.get("primary_first_touch_5m_utc", ""), 0)
        row["same_5m_reclaim_timestamp_event_count"] = reclaim_counts.get(row.get("primary_first_reclaim_5m_utc", ""), 0)

    eligible = [row for row in paths if row["analysis_status"] == "eligible_ABC"]
    ab_rows = [row for row in eligible if row["category"] in ("A", "B")]
    category_rows = summarize_groups(eligible, category_mode=True)
    outcome_rows = summarize_groups(ab_rows, category_mode=False)
    coverage_rows = coverage_by_symbol(paths)
    time_groups = []
    for ts, count in sorted(touch_counts.items()):
        if count > 1:
            subset = [row for row in eligible_candidates if row.get("primary_first_touch_5m_utc") == ts]
            time_groups.append({
                "first_touch_5m_utc": ts,
                "event_count": count,
                "symbols": "|".join(sorted(row["symbol"] for row in subset)),
                "side_counts": json.dumps(dict(Counter(row["side"] for row in subset)), separators=(",", ":")),
                "period_counts": json.dumps(dict(Counter(row["period"] for row in subset)), separators=(",", ":")),
            })

    out_path = HERE / "event_paths.csv.gz"
    write_gzip_csv(out_path, paths)
    write_csv(HERE / "coverage_by_symbol.csv", coverage_rows)
    write_csv(HERE / "category_summary.csv", category_rows)
    write_csv(HERE / "outcome_summary.csv", outcome_rows)
    write_csv(HERE / "same_5m_touch_times.csv", time_groups)

    h1_status_counts = Counter(row["h1_first_touch_validation_status"] for row in paths)
    native_status_counts = Counter(row["native_fetch_status"] for row in paths)
    event_hour_status_counts = Counter(row["direct5m_touch_hour_native_status"] for row in paths)
    primary_event_hour_status_counts = Counter(row["primary_touch_hour_status"] or "none" for row in paths)
    match_counts = Counter(row["direct5m_h1_5m_ohlc_match_status"] for row in paths)
    primary_match_counts = Counter(row["primary_h1_5m_ohlc_match_status"] or "none" for row in paths)
    analysis_status_counts = Counter(row["analysis_status"] for row in paths)
    category_counts = Counter(row["category"] for row in eligible)
    categories_by_side = {
        side: dict(Counter(row["category"] for row in eligible if row["side"] == side))
        for side in ("lower", "upper")
    }
    categories_by_period = {
        period: dict(Counter(row["category"] for row in eligible if row["period"] == period))
        for period in ("before_2025", "2025_or_later")
    }
    categories_by_side_period = {
        f"{side}|{period}": dict(Counter(row["category"] for row in eligible if row["side"] == side and row["period"] == period))
        for side in ("lower", "upper") for period in ("before_2025", "2025_or_later")
    }
    outcome_by_side_category = {
        f"{side}|{category}": summary_metrics([row for row in ab_rows if row["side"] == side and row["category"] == category])
        for side in ("lower", "upper") for category in ("A", "B")
    }
    outcome_by_side_period_category = {
        f"{side}|{period}|{category}": summary_metrics([
            row for row in ab_rows if row["side"] == side and row["period"] == period and row["category"] == category
        ])
        for side in ("lower", "upper") for period in ("before_2025", "2025_or_later") for category in ("A", "B")
    }
    archive_status_counts = Counter(row["archive_prior_5m_status"] for row in paths)
    native_prefix_status_counts = Counter(row["native_window_prior_5m_status"] for row in paths)
    evidence_basis_counts = Counter(row["first_touch_evidence_basis"] for row in paths)
    primary_source_counts = Counter(row["primary_5m_source"] for row in paths)
    strict_5m_rows = [row for row in eligible if (
        row["native_window_prior_5m_status"] == "verified"
        or row["archive_prior_5m_status"] == "verified"
        or truth(row["v1_strict_pre_h1_verified"])
    )]
    archive_compare_counts = {
        "events_with_archive_window": sum(row["archive_prior_5m_status"] != "missing_archive_window" for row in paths),
        "shared_candles": sum(int(row["archive_native_common_bars"]) for row in paths),
        "exact_ohlc_candles": sum(int(row["archive_native_exact_ohlc_bars"]) for row in paths),
        "within_tolerance_ohlc_candles": sum(int(row["archive_native_within_tolerance_ohlc_bars"]) for row in paths),
        "mismatched_ohlc_candles": sum(int(row["archive_native_mismatched_ohlc_bars"]) for row in paths),
        "mismatched_open_fields": sum(int(row["archive_native_open_mismatched_bars"]) for row in paths),
        "mismatched_high_fields": sum(int(row["archive_native_high_mismatched_bars"]) for row in paths),
        "mismatched_low_fields": sum(int(row["archive_native_low_mismatched_bars"]) for row in paths),
        "mismatched_close_fields": sum(int(row["archive_native_close_mismatched_bars"]) for row in paths),
        "max_abs_difference_by_field": {
            key: str(max((decimal(row[f"archive_native_max_abs_diff_{key}"]) for row in paths if row[f"archive_native_max_abs_diff_{key}"]), default=Decimal(0)))
            for key in OHLC
        },
        "root_match_events": sum(row["archive_native_root_match"] == "true" for row in paths),
        "root_mismatch_events": sum(row["archive_native_root_match"] == "false" for row in paths),
        "root_time_match_events": sum(row["archive_native_root_time_match"] == "true" for row in paths),
        "root_ohlc_match_events": sum(row["archive_native_root_ohlc_match"] == "true" for row in paths),
        "category_match_events": sum(row["archive_native_category_match"] == "true" for row in paths),
        "category_mismatch_events": sum(row["archive_native_category_match"] == "false" for row in paths),
    }
    v1_comparable = [row for row in paths if row["v1_native_root_match"] != "not_comparable"]
    v1_strict = [row for row in paths if truth(row["v1_strict_pre_h1_verified"])]
    summary = {
        "experiment_id": config["experiment_id"],
        "built_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "observation_only": True,
        "inputs": {
            "frozen_events": config["source_events"],
            "frozen_event_rows": len(events),
            "frozen_event_symbols": len({row["symbol"] for row in events}),
            "frozen_event_side_counts": dict(Counter(row["side"] for row in events)),
            "frozen_event_cohort_counts": dict(Counter(row["cohort"] for row in events)),
            "h1_source_manifest": config["source_h1_manifest"],
            "h1_manifest_rows": len(manifest),
            "native_event_ledger": config["native_event_ledger"],
            "native_event_ledger_rows": len(fetch_rows),
            "native_candles": config["native_candles"],
            "native_candle_event_groups_seen": len(seen_candle_events),
            "v1_event_paths": config["v1_event_paths"],
            "v1_rows": len(v1_rows),
            "archive_windows_directory": config["archive_windows_directory"],
            "analysis_as_of_utc": datetime.fromtimestamp(as_of_ms / 1000, UTC).isoformat(timespec="seconds"),
        },
        "method": {
            "h1_prefix": "Require each original frozen H1 timestamp from derived ready_utc through the event H1 open inclusive. No earlier same-side rail touch is permitted, and the recorded H1 bar must touch the rail.",
            "event_hour_match": "Aggregate the 12 event-hour 5m bars as first open, max high, min low, last close; compare to original H1 using Decimal with rel_tol=1e-10 and abs_tol=1e-12.",
            "first_5m_touch": "Within the full frozen H1 touch hour, first lower low<=rail or upper high>=rail; all event-hour 5m bars must be present, sequential, and not explicitly unconfirmed.",
            "category_window": "First touch bar plus the next five consecutive 5m bars in the same NY month. A=first close strictly inside, B=first strict inside close in bars 1-5, C=no strict inside close.",
            "post_reclaim_hour": "For A/B, the next 12 consecutive 5m bars after the first strict reclaim bar. Report any outer-rail retouch, any strict close outside (lower close<rail; upper close>rail), equal closes separately, end close outside, near-boundary reach before/on/after reclaim, directional MFE/MAE from the next bar open, and one-hour end close direction.",
            "near_boundary_direction": "Lower side reaches first-week low when high>=low boundary; upper side reaches first-week high when low<=high boundary.",
            "primary_5m_source": "Use complete direct native 5m when it matches original H1. If its touch hour is missing/incomplete, use complete matching OKX month-archive 1m-derived 5m data as a labeled fallback. A complete native H1/5m mismatch is a source conflict and is not hidden by archive fallback.",
            "pre_touch_5m_audit": "Check native event-window bars from max(ready_utc, H1 touch open minus 120 five-minute bars) through the H1 open exclusive. An observed earlier same-side touch excludes the event; verified requires the entire interval, including zero expected bars where ready equals touch. Archive and v1 full-prefix evidence remain separate.",
            "primary_exclusions": "Exclude incomplete or contradictory original H1 prefixes, any earlier same-side 5m touch found by native-window/archive/v1 prefix audit, incomplete event-hour windows, and H1/5m OHLC mismatches. Archive 5m prefix incompleteness is retained with an explicit evidence-basis label.",
            "v1_comparison": "v1 strict 5m-prefix subset is pre_h1_coverage_status=complete_no_same_side_5m_touch; it is separately counted and never substituted for H1 proof.",
            "costs_or_pnl": "none; no simulated trades or fills",
        },
        "coverage_funnel": {
            "source_events": len(events),
            "h1_first_touch_validation_status_counts": dict(h1_status_counts),
            "native_fetch_status_counts": dict(native_status_counts),
            "direct_native_touch_hour_status_counts": dict(event_hour_status_counts),
            "direct_native_h1_5m_ohlc_match_status_counts": dict(match_counts),
            "primary_touch_hour_status_counts": dict(primary_event_hour_status_counts),
            "primary_h1_5m_ohlc_match_status_counts": dict(primary_match_counts),
            "archive_prior_5m_status_counts": dict(archive_status_counts),
            "native_event_window_prior_5m_status_counts": dict(native_prefix_status_counts),
            "first_touch_evidence_basis_counts": dict(evidence_basis_counts),
            "analysis_status_counts": dict(analysis_status_counts),
            "primary_5m_source_counts": dict(primary_source_counts),
            "events_in_primary_ABC_denominator": len(eligible),
            "events_with_A_or_B": len(ab_rows),
        },
        "six_bar_classification": {
            "eligible_n": len(eligible),
            "category_counts": dict(category_counts),
            "category_pct_of_eligible": {
                key: round(100 * count / len(eligible), 6) for key, count in category_counts.items()
            } if eligible else {},
            "category_counts_by_side": categories_by_side,
            "category_counts_by_period": categories_by_period,
            "category_counts_by_side_period": categories_by_side_period,
        },
        "A_B_post_reclaim_one_hour": {
            "by_side_category": outcome_by_side_category,
            "by_side_period_category": outcome_by_side_period_category,
        },
        "strict_5m_prefix_subset": {
            "verified_n_in_primary_ABC": len(strict_5m_rows),
            "categories": dict(Counter(row["category"] for row in strict_5m_rows)),
            "native_window_verified_n": sum(row["native_window_prior_5m_status"] == "verified" for row in eligible),
            "archive_window_verified_n": sum(row["archive_prior_5m_status"] == "verified" for row in eligible),
            "v1_full_prefix_verified_n": sum(truth(row["v1_strict_pre_h1_verified"]) for row in eligible),
            "v1_subset_is_reported_separately": True,
        },
        "time_concentration": {
            "unique_first_touch_5m_timestamps": len(touch_counts),
            "multi_event_first_touch_timestamps": sum(count > 1 for count in touch_counts.values()),
            "events_in_multi_event_first_touch_timestamps": sum(count for count in touch_counts.values() if count > 1),
            "max_events_at_one_first_touch_5m_timestamp": max(touch_counts.values(), default=0),
            "unique_first_reclaim_5m_timestamps": len(reclaim_counts),
            "multi_event_first_reclaim_timestamps": sum(count > 1 for count in reclaim_counts.values()),
            "max_events_at_one_first_reclaim_5m_timestamp": max(reclaim_counts.values(), default=0),
        },
        "archive_rest_source_comparison": archive_compare_counts,
        "v1_overlap_consistency": {
            "v1_rows": len(v1_rows),
            "native_root_comparable_n": len(v1_comparable),
            "root_matches": sum(row["v1_native_root_match"] == "match" for row in v1_comparable),
            "root_mismatches": sum(row["v1_native_root_match"] == "mismatch" for row in v1_comparable),
            "category_matches": sum(row["v1_native_category_match"] == "true" for row in paths),
            "category_mismatches": sum(row["v1_native_category_match"] == "false" for row in paths),
            "future12_matches": sum(row["v1_native_future12_match"] == "true" for row in paths),
            "future12_mismatches": sum(row["v1_native_future12_match"] == "false" for row in paths),
            "v1_strict_pre_h1_verified_n": len(v1_strict),
            "v1_strict_subset_primary_eligible_n": sum(row["analysis_status"] == "eligible_ABC" for row in v1_strict),
            "v1_strict_subset_categories": dict(Counter(row["category"] for row in v1_strict if row["category"])),
        },
        "outputs": {
            "event_paths": "event_paths.csv.gz",
            "coverage_by_symbol": "coverage_by_symbol.csv",
            "category_summary": "category_summary.csv",
            "outcome_summary": "outcome_summary.csv",
            "same_5m_touch_times": "same_5m_touch_times.csv",
            "summary": "summary.json",
        },
    }
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "source_events": len(events),
        "h1_status_counts": dict(h1_status_counts),
        "native_status_counts": dict(native_status_counts),
        "analysis_status_counts": dict(analysis_status_counts),
        "categories": dict(category_counts),
        "v1_strict_n": len(v1_strict),
    }, ensure_ascii=False, indent=2))


SPLIT_MS = 0


if __name__ == "__main__":
    main()
