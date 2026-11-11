#!/usr/bin/env python3
"""Build an auditable five-minute observation from frozen monthly first-week events.

Inputs are the frozen event table and the twelve explicitly listed local OKX 5m
files in config.json. Touch roots use the 5m high/low relative to the frozen
outer rail; the full 5m time prefix from the UTC hour start to that root must be
present. A/B/C use only closes from the touch bar through the next five bars.
No simulated fills, costs, returns, or market decisions are computed.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CONFIG_PATH = HERE / "config.json"
BAR_MS = 5 * 60 * 1000
UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Expected timezone-aware timestamp: {value!r}")
    return parsed.astimezone(UTC)


def iso_utc(value: datetime | None) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds") if value else ""


def decimal(value: str) -> Decimal:
    return Decimal(value)


def comparison(close: Decimal, level: Decimal, side: str) -> tuple[str, bool]:
    """Return relation to rail and strict inside status for the event side."""
    if close == level:
        return "equal", False
    if side == "lower":
        return ("inside" if close > level else "outside"), close > level
    if side == "upper":
        return ("inside" if close < level else "outside"), close < level
    raise ValueError(f"Unknown side {side!r}")


def rail_touched(bar: dict[str, Any], side: str, level: Decimal) -> bool:
    if side == "lower":
        return decimal(bar["low"]) <= level
    if side == "upper":
        return decimal(bar["high"]) >= level
    raise ValueError(f"Unknown side {side!r}")


def boundary_touched(bar: dict[str, Any], side: str, boundary: Decimal) -> bool:
    if side == "lower":
        return decimal(bar["high"]) >= boundary
    return decimal(bar["low"]) <= boundary


def first_full_week_ready(month: str) -> datetime:
    """Return the first complete NY Monday-Sunday week's close (next Monday 00:00)."""
    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1)
    next_month = date(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1)
    day = month_start
    while day < next_month:
        if day.weekday() == 0 and day + timedelta(days=6) < next_month:
            return datetime.combine(day + timedelta(days=7), time.min, NY).astimezone(UTC)
        day += timedelta(days=1)
    raise ValueError(f"No complete Monday-Sunday week in NY month {month}")


def assert_boundary_direction_with_merl_values() -> None:
    """Guard near-edge direction with frozen MERL prices, not market outcomes."""
    # Frozen MERL|2024-09|upper: first-week high = 0.2763.
    upper_target = Decimal("0.2763")
    assert boundary_touched({"high": "0.2800", "low": "0.2763"}, "upper", upper_target)
    assert not boundary_touched({"high": "0.2800", "low": "0.2764"}, "upper", upper_target)
    # Frozen MERL|2024-12|lower: first-week low = 0.3612.
    lower_target = Decimal("0.3612")
    assert boundary_touched({"high": "0.3612", "low": "0.3500"}, "lower", lower_target)
    assert not boundary_touched({"high": "0.3611", "low": "0.3500"}, "lower", lower_target)


def load_5m_file(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    ordered: list[int] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ts", "open", "high", "low", "close", "open_time"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            ts = int(row["ts"])
            if ts in rows:
                raise ValueError(f"{path}: duplicate timestamp {ts}")
            timestamp = parse_utc(row["open_time"])
            if int(timestamp.timestamp() * 1000) != ts:
                raise ValueError(f"{path}: ts/open_time mismatch at {row['open_time']}")
            row["_dt"] = timestamp
            rows[ts] = row
            ordered.append(ts)
    if not ordered:
        raise ValueError(f"Empty 5m file: {path}")
    if ordered != sorted(ordered):
        raise ValueError(f"{path}: timestamps are not ascending")
    gaps = sum(right - left != BAR_MS for left, right in zip(ordered, ordered[1:]))
    metadata = {
        "file": str(path.relative_to(REPO)),
        "rows": len(ordered),
        "first_utc": iso_utc(rows[ordered[0]]["_dt"]),
        "last_utc": iso_utc(rows[ordered[-1]]["_dt"]),
        "first_ts": ordered[0],
        "last_ts": ordered[-1],
        "non_5m_steps": gaps,
    }
    return rows, metadata


def read_events(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", newline="") as handle:
        events = list(csv.DictReader(handle))
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        key = (event["symbol"], event["month"], event["side"])
        if key in seen:
            raise ValueError(f"Duplicate frozen symbol/month/side event: {key}")
        seen.add(key)
    return events


def full_consecutive_window(
    bars: dict[int, dict[str, Any]], start_ts: int, count: int, month: str
) -> tuple[list[dict[str, Any]] | None, str]:
    window: list[dict[str, Any]] = []
    for offset in range(count):
        ts = start_ts + offset * BAR_MS
        bar = bars.get(ts)
        if bar is None:
            return None, "missing_bar"
        if bar["_dt"].astimezone(NY).strftime("%Y-%m") != month:
            return None, "ny_month_boundary"
        window.append(bar)
    return window, "complete"


def event_path(
    event: dict[str, str], bars: dict[int, dict[str, Any]] | None, metadata: dict[str, Any] | None
) -> dict[str, Any]:
    touch_dt = parse_utc(event["touch"])
    if touch_dt.minute != 0 or touch_dt.second != 0 or touch_dt.microsecond != 0:
        raise ValueError(f"Frozen touch is not on an hourly boundary: {event['event_id']} {event['touch']}")
    if touch_dt.astimezone(NY).strftime("%Y-%m") != event["month"]:
        raise ValueError(f"Frozen touch month disagrees with NY month: {event['event_id']}")

    level = decimal(event["level"])
    side = event["side"]
    near_boundary = decimal(event["low"] if side == "lower" else event["high"])
    ready_dt = first_full_week_ready(event["month"])
    ready_ts = int(ready_dt.timestamp() * 1000)
    hour_start = int(touch_dt.timestamp() * 1000)
    if ready_ts > hour_start:
        raise ValueError(f"Derived first-week ready time follows event touch: {event['event_id']}")
    if ready_ts % BAR_MS or hour_start % BAR_MS:
        raise ValueError(f"Ready/event time is off the 5m grid: {event['event_id']}")
    path: dict[str, Any] = {
        "event_id": event["event_id"],
        "symbol": event["symbol"],
        "month": event["month"],
        "cohort": event["cohort"],
        "side": side,
        "event_hour_utc": event["touch"],
        "first_week_ready_utc_derived": iso_utc(ready_dt),
        "first_week_ready_ms_derived": ready_ts,
        "first_week_high": event["high"],
        "first_week_low": event["low"],
        "outer_rail": event["level"],
        "near_side_boundary": str(near_boundary),
        "literal": event["literal"],
        "local_5m_file": metadata["file"] if metadata else "",
        "local_5m_first_utc": metadata["first_utc"] if metadata else "",
        "local_5m_last_utc": metadata["last_utc"] if metadata else "",
        "touch_5m_open_utc": "",
        "touch_5m_index_in_hour": "",
        "pre_touch_hour_bars": "",
        "pre_touch_prefix_contiguous": "",
        "touch_5m_open": "",
        "touch_5m_high": "",
        "touch_5m_low": "",
        "touch_5m_close": "",
        "pre_h1_expected_bars": (hour_start - ready_ts) // BAR_MS,
        "pre_h1_observed_bars": "",
        "pre_h1_same_side_touch_count": "",
        "pre_h1_first_same_side_touch_utc": "",
        "pre_h1_coverage_status": "",
        "touch_basis": "",
        "classification_status": "",
        "category": "",
        "first_strict_reclaim_index_0_to_5": "",
        "first_close_relation": "",
        "equal_rail_closes_6bars": "",
        "future12_status": "not_A_or_B",
        "future12_near_boundary_hit": "",
        "future12_complete_bar_count": 0,
    }
    if bars is None or metadata is None:
        path["classification_status"] = "missing_local_5m_file"
        path["pre_h1_coverage_status"] = "missing_local_5m_file"
        path["touch_basis"] = "H1_first_touch_only_no_local_5m_file"
        return path

    pre_h1_rows = [(ts, bars.get(ts)) for ts in range(ready_ts, hour_start, BAR_MS)]
    pre_h1_present = [(ts, bar) for ts, bar in pre_h1_rows if bar is not None]
    pre_h1_touches = [(ts, bar) for ts, bar in pre_h1_present if rail_touched(bar, side, level)]
    pre_h1_complete = len(pre_h1_present) == path["pre_h1_expected_bars"]
    path["pre_h1_observed_bars"] = len(pre_h1_present)
    path["pre_h1_same_side_touch_count"] = len(pre_h1_touches)
    path["pre_h1_first_same_side_touch_utc"] = iso_utc(bars[pre_h1_touches[0][0]]["_dt"]) if pre_h1_touches else ""
    if pre_h1_touches:
        path["pre_h1_coverage_status"] = "same_side_5m_touch_before_recorded_h1_hour"
        path["touch_basis"] = "excluded_prior_5m_touch_found"
        path["classification_status"] = "same_side_5m_touch_before_recorded_h1_hour"
        return path
    if pre_h1_complete:
        path["pre_h1_coverage_status"] = "complete_no_same_side_5m_touch"
        path["touch_basis"] = "pre_h1_5m_history_verified_no_touch"
    else:
        path["pre_h1_coverage_status"] = "incomplete_no_observed_same_side_5m_touch"
        path["touch_basis"] = "H1_first_touch_only_pre_h1_5m_history_incomplete"

    hour_timestamps = [hour_start + i * BAR_MS for i in range(12)]
    hour_rows = [bars.get(ts) for ts in hour_timestamps]
    path["touch_hour_rows_present"] = sum(row is not None for row in hour_rows)
    hour_candidates = [
        i for i, bar in enumerate(hour_rows)
        if bar is not None and rail_touched(bar, side, level)
    ]
    if not hour_candidates:
        if hour_start < metadata["first_ts"] or hour_start + 11 * BAR_MS > metadata["last_ts"]:
            path["classification_status"] = "outside_local_5m_time_range"
        elif all(bar is not None for bar in hour_rows):
            path["classification_status"] = "no_5m_touch_in_complete_event_hour"
        else:
            path["classification_status"] = "incomplete_event_hour_no_touch_root"
        return path

    root_index = hour_candidates[0]
    root_ts = hour_timestamps[root_index]
    root = hour_rows[root_index]
    path["touch_5m_open_utc"] = iso_utc(root["_dt"])
    path["touch_5m_index_in_hour"] = root_index
    path["pre_touch_hour_bars"] = root_index
    path["touch_5m_open"] = root["open"]
    path["touch_5m_high"] = root["high"]
    path["touch_5m_low"] = root["low"]
    path["touch_5m_close"] = root["close"]
    prefix_ok = all(hour_rows[i] is not None for i in range(root_index + 1))
    path["pre_touch_prefix_contiguous"] = prefix_ok
    if not prefix_ok:
        path["classification_status"] = "unverifiable_first_touch_hour_prefix"
        return path

    six, six_status = full_consecutive_window(bars, root_ts, 6, event["month"])
    if six is None:
        path["classification_status"] = (
            "six_bar_window_crosses_ny_month_end" if six_status == "ny_month_boundary"
            else "incomplete_six_bar_window"
        )
        return path

    comparisons = [comparison(decimal(bar["close"]), level, side) for bar in six]
    inside_indexes = [i for i, (_, is_inside) in enumerate(comparisons) if is_inside]
    first_relation, _ = comparisons[0]
    path["first_close_relation"] = first_relation
    path["equal_rail_closes_6bars"] = sum(relation == "equal" for relation, _ in comparisons)
    path["category"] = "A" if inside_indexes and inside_indexes[0] == 0 else (
        "B" if inside_indexes else "C"
    )
    if inside_indexes:
        path["first_strict_reclaim_index_0_to_5"] = inside_indexes[0]
    path["classification_status"] = "eligible_6bar_classification"

    if not inside_indexes:
        return path
    reclaim_index = inside_indexes[0]
    reclaim_bar = six[reclaim_index]
    if boundary_touched(reclaim_bar, side, near_boundary):
        path["future12_status"] = "reclaim_bar_already_touched_boundary"
        return path
    future_start = int(reclaim_bar["_dt"].timestamp() * 1000) + BAR_MS
    future, future_status = full_consecutive_window(bars, future_start, 12, event["month"])
    if future is None:
        path["future12_status"] = (
            "future12_crosses_ny_month_end" if future_status == "ny_month_boundary"
            else "incomplete_future12_window"
        )
        return path
    hit = any(boundary_touched(bar, side, near_boundary) for bar in future)
    path["future12_status"] = "complete_hit" if hit else "complete_no_hit"
    path["future12_near_boundary_hit"] = hit
    path["future12_complete_bar_count"] = len(future)
    return path


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty output: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    assert_boundary_direction_with_merl_values()
    config = json.loads(CONFIG_PATH.read_text())
    source_path = REPO / config["source_events"]
    data_dir = REPO / config["local_5m_source_directory"]
    events = read_events(source_path)

    bars_by_symbol: dict[str, dict[int, dict[str, Any]]] = {}
    metadata_by_symbol: dict[str, dict[str, Any]] = {}
    for filename in config["local_5m_files"]:
        match = re.fullmatch(r"okx_(.+)_USDT_SWAP_5m_\d+\.csv", filename)
        if not match:
            raise ValueError(f"Unexpected local data filename: {filename}")
        symbol = match.group(1)
        if symbol in bars_by_symbol:
            raise ValueError(f"More than one file configured for {symbol}")
        bars_by_symbol[symbol], metadata_by_symbol[symbol] = load_5m_file(data_dir / filename)

    paths = [
        event_path(event, bars_by_symbol.get(event["symbol"]), metadata_by_symbol.get(event["symbol"]))
        for event in events
    ]
    write_csv(HERE / "event_paths.csv", paths)

    source_events_by_symbol = Counter(event["symbol"] for event in events)
    path_status_by_symbol = Counter(row["classification_status"] for row in paths)
    category_counts = Counter(row["category"] for row in paths if row["category"])
    category_by_side: dict[str, dict[str, int]] = {}
    for side in ("lower", "upper"):
        category_by_side[side] = dict(Counter(
            row["category"] for row in paths if row["side"] == side and row["category"]
        ))
    future_counts = Counter(row["future12_status"] for row in paths)
    future_eligible = [row for row in paths if row["future12_status"] in ("complete_hit", "complete_no_hit")]
    future_by_category: dict[str, dict[str, int]] = {}
    for category in ("A", "B"):
        subset = [row for row in future_eligible if row["category"] == category]
        future_by_category[category] = {
            "complete_windows": len(subset),
            "hit_near_boundary": sum(row["future12_status"] == "complete_hit" for row in subset),
            "no_hit": sum(row["future12_status"] == "complete_no_hit" for row in subset),
        }

    common_first = max(parse_utc(m["first_utc"]) for m in metadata_by_symbol.values())
    common_last = min(parse_utc(m["last_utc"]) for m in metadata_by_symbol.values())
    coverage_rows = []
    for symbol in sorted(metadata_by_symbol):
        metadata = metadata_by_symbol[symbol]
        matching_paths = [row for row in paths if row["symbol"] == symbol]
        coverage_rows.append({
            "symbol": symbol,
            "file": metadata["file"],
            "rows": metadata["rows"],
            "first_utc": metadata["first_utc"],
            "last_utc": metadata["last_utc"],
            "non_5m_steps": metadata["non_5m_steps"],
            "source_events": source_events_by_symbol[symbol],
            "pre_h1_verified_no_touch": sum(row["pre_h1_coverage_status"] == "complete_no_same_side_5m_touch" for row in matching_paths),
            "pre_h1_incomplete_no_observed_touch": sum(row["pre_h1_coverage_status"] == "incomplete_no_observed_same_side_5m_touch" for row in matching_paths),
            "pre_h1_same_side_touch_found": sum(row["pre_h1_coverage_status"] == "same_side_5m_touch_before_recorded_h1_hour" for row in matching_paths),
            "event_hours_with_any_local_rows": sum(int(row.get("touch_hour_rows_present", 0)) > 0 for row in matching_paths),
            "first_touch_prefix_unverifiable": sum(row["classification_status"] == "unverifiable_first_touch_hour_prefix" for row in matching_paths),
            "eligible_6bar": sum(row["classification_status"] == "eligible_6bar_classification" for row in matching_paths),
            "A": sum(row["category"] == "A" for row in matching_paths),
            "B": sum(row["category"] == "B" for row in matching_paths),
            "C": sum(row["category"] == "C" for row in matching_paths),
        })
    write_csv(HERE / "coverage_by_symbol.csv", coverage_rows)

    event_months = sorted({event["month"] for event in events})
    summary = {
        "experiment_id": config["experiment_id"],
        "built_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "observation_only": True,
        "inputs": {
            "source_events": config["source_events"],
            "source_event_rows": len(events),
            "source_event_symbols": len({event["symbol"] for event in events}),
            "source_event_month_span_ny": [event_months[0], event_months[-1]],
            "source_event_side_counts": dict(Counter(event["side"] for event in events)),
            "local_5m_source_directory": config["local_5m_source_directory"],
            "local_5m_configured_file_count": len(metadata_by_symbol),
            "local_5m_configured_symbols": sorted(metadata_by_symbol),
            "local_5m_file_rows_total": sum(m["rows"] for m in metadata_by_symbol.values()),
            "local_5m_common_first_open_utc": iso_utc(common_first),
            "local_5m_common_last_open_utc": iso_utc(common_last),
            "local_5m_per_symbol": [
                {key: value for key, value in metadata.items() if key not in ("first_ts", "last_ts")}
                for _, metadata in sorted(metadata_by_symbol.items())
            ],
            "local_data_symbols_not_in_frozen_event_roster": sorted(set(metadata_by_symbol) - set(source_events_by_symbol)),
            "frozen_event_symbols_without_local_5m_file": sorted(set(source_events_by_symbol) - set(metadata_by_symbol)),
        },
        "method": {
            "touch_hour": "frozen event touch timestamp is the UTC hour open; within it the first 5m low <= lower rail or high >= upper rail is the touch root",
            "pre_h1_check": "derive ready_ms from the end of the first complete NY Monday-Sunday week; inspect ready_ms through recorded H1 touch-hour start. Any observed earlier same-side 5m touch excludes that event from A/B/C; if no touch is observed but the interval is incomplete, retain it with H1-first-touch-only basis.",
            "first_touch_prefix": "require each 5m timestamp from UTC hour start through the root, inclusive, with no missing 5m bars; all prior bars were checked and did not touch",
            "six_bar_window": "root plus five following consecutive 5m bars; require all six timestamps and require all bars to have the event NY month",
            "strict_reclaim": "lower close > outer rail; upper close < outer rail; equal is outside/not reclaimed",
            "categories": {"A": "root bar is first strict inside close", "B": "first strict inside close is among next five bars", "C": "no strict inside close across all six bars"},
            "near_boundary": "For A/B, after the reclaim bar, inspect the next 12 complete consecutive 5m bars in the same NY month for reaching first_week_low via high >= boundary (lower) or first_week_high via low <= boundary (upper); exclude reclaim bars that already reached the target.",
            "costs_or_pnl": "none; no trading backtest",
        },
        "coverage_funnel": {
            "source_events": len(events),
            "events_with_local_file": sum(event["symbol"] in metadata_by_symbol for event in events),
            "events_without_local_file": sum(event["symbol"] not in metadata_by_symbol for event in events),
            "classification_status_counts": dict(path_status_by_symbol),
            "pre_h1_coverage_status_counts": dict(Counter(row["pre_h1_coverage_status"] for row in paths)),
            "eligible_6bar_classifications": sum(row["classification_status"] == "eligible_6bar_classification" for row in paths),
        },
        "six_bar_classification": {
            "eligible_n": sum(category_counts.values()),
            "category_counts": dict(category_counts),
            "category_pct_of_eligible": {
                key: round(value * 100 / sum(category_counts.values()), 4) if category_counts else None
                for key, value in category_counts.items()
            },
            "category_counts_by_side": category_by_side,
            "category_counts_by_touch_basis": {
                basis: dict(Counter(row["category"] for row in paths if row["touch_basis"] == basis and row["category"]))
                for basis in sorted({row["touch_basis"] for row in paths})
            },
            "six_bar_close_equal_to_outer_rail_count": sum(int(row["equal_rail_closes_6bars"] or 0) for row in paths),
            "first_close_relation_counts": dict(Counter(row["first_close_relation"] for row in paths if row["first_close_relation"])),
        },
        "future_12_bar_near_boundary": {
            "status_counts": dict(future_counts),
            "complete_windows_n": len(future_eligible),
            "hit_near_boundary_n": sum(row["future12_status"] == "complete_hit" for row in future_eligible),
            "hit_pct_of_complete_windows": round(100 * sum(row["future12_status"] == "complete_hit" for row in future_eligible) / len(future_eligible), 4) if future_eligible else None,
            "counts_by_category": future_by_category,
        },
        "outputs": {
            "event_paths": "event_paths.csv",
            "coverage_by_symbol": "coverage_by_symbol.csv",
            "summary": "summary.json",
        },
    }
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "source_events": len(events),
        "local_symbols": len(metadata_by_symbol),
        "coverage_status_counts": dict(path_status_by_symbol),
        "categories": dict(category_counts),
        "future12_status_counts": dict(future_counts),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
