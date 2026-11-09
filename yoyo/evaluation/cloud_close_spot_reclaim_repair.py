"""Retain valid Spot 1h rows around isolated malformed close_time records.

This is an input-coverage repair only. Candidate identity, NY-week assignment,
and reclaim geometry come from ``cloud_close_spot_reclaim_inputs``. The only
row-level change is that a malformed source row no longer invalidates its
entire monthly ZIP; that row is excluded unchanged and any event requiring its
hour remains unknown. The source cache and frozen panel are read-only.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import io
import json
import os
from pathlib import Path
import zipfile

from yoyo.evaluation import cloud_close_spot_reclaim_inputs as source


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/active/exp-cloud-close-spot-repair-20261109-v1/config.json"
HOUR_MS = source.HOUR_MS
EXPECTED_CLOSE_DELTA_MS = HOUR_MS - 1
UTC = dt.timezone.utc
ROW_AUDIT_COLUMNS = [
    "spot_symbol", "month", "archive_path", "source_row_number", "open_ms",
    "close_ms", "close_delta_ms", "timestamp_unit", "reason", "action",
]
ARCHIVE_AUDIT_COLUMNS = [
    "spot_symbol", "month", "archive_path", "archive_url", "status",
    "compressed_bytes", "member", "rows_seen", "valid_rows", "rejected_rows",
    "bad_close_rows", "bad_open_rows", "bad_ohlcv_rows", "bad_zero_trade_rows",
    "duplicate_open_rows", "zero_trade_rows", "first_open_ms", "last_open_ms",
    "missing_h1_between_first_last", "timestamp_units", "error",
]
PRESERVE_COLUMNS = [
    "covered", "spot_reclaim", "spot_confirms", "spot_h1_expected",
    "spot_h1_observed", "spot_h1_missing", "spot_zero_trade_hours",
    "spot_monday_high", "spot_monday_low", "spot_key_high", "spot_key_low",
    "spot_key_close", "spot_lower_1618", "spot_upper_1618",
]


def _load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_gz_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    _atomic_bytes(path, payload)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with tmp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _write_csv_gz(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({column: row.get(column) for column in columns} for row in rows)
    os.replace(tmp, path)


def _read_csv_gz(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = row["event_id"]
            if key in rows:
                raise ValueError(f"duplicate event_id in {path}: {key}")
            rows[key] = row
    return rows


def _month_path(cache_root: Path, spot_symbol: str, month: str) -> Path:
    return cache_root / "raw" / spot_symbol / "1h" / f"{spot_symbol}-1h-{month}.zip"


def _normalize_timestamp(raw: int) -> tuple[int, str]:
    unit = "us" if raw >= 100_000_000_000_000 else "ms"
    return (raw // 1000 if unit == "us" else raw), unit


def _quality_row(spot_symbol: str, month: str, path: Path, row_no: int,
                 open_ms: int | None, close_ms: int | None, delta: int | None,
                 unit: str, reason: str) -> dict:
    return {
        "spot_symbol": spot_symbol, "month": month, "archive_path": str(path),
        "source_row_number": row_no, "open_ms": open_ms, "close_ms": close_ms,
        "close_delta_ms": delta, "timestamp_unit": unit, "reason": reason,
        "action": "excluded_source_row_no_timestamp_repair",
    }


def _parse_archive(path: Path, spot_symbol: str, month: str) -> tuple[dict[int, dict], dict, list[dict]]:
    """Read valid original rows; omit and audit only rows failing exact checks."""
    candidates: dict[int, dict] = {}
    open_rows: dict[int, list[int]] = collections.defaultdict(list)
    invalid_rows: set[int] = set()
    audit = {
        "spot_symbol": spot_symbol, "month": month, "archive_path": str(path),
        "archive_url": source._archive_url(spot_symbol, month), "status": "read",
        "compressed_bytes": path.stat().st_size, "member": "", "rows_seen": 0,
        "valid_rows": 0, "rejected_rows": 0, "bad_close_rows": 0,
        "bad_open_rows": 0, "bad_ohlcv_rows": 0, "bad_zero_trade_rows": 0,
        "duplicate_open_rows": 0, "zero_trade_rows": 0,
        "first_open_ms": "", "last_open_ms": "", "missing_h1_between_first_last": 0,
        "timestamp_units": "", "error": "",
    }
    issues: list[dict] = []
    records: dict[int, dict] = {}
    units: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            expected = f"{spot_symbol}-1h-{month}.csv"
            if members != [expected]:
                raise ValueError(f"expected one exact CSV member {expected!r}, got {members!r}")
            audit["member"] = members[0]
            with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
                for row_no, row in enumerate(csv.reader(text), 1):
                    audit["rows_seen"] += 1
                    open_ms = close_ms = delta = None
                    unit = "unknown"
                    reason = ""
                    try:
                        if len(row) != 12:
                            raise ValueError(f"expected 12 source fields, got {len(row)}")
                        open_raw, close_raw = int(row[0]), int(row[6])
                        open_ms, unit = _normalize_timestamp(open_raw)
                        open_rows[open_ms].append(row_no)
                        # Interpret close_time in the same native unit as the original open_time.
                        close_ms = close_raw // 1000 if unit == "us" else close_raw
                        units.add(unit)
                        delta = close_ms - (open_ms + EXPECTED_CLOSE_DELTA_MS)
                        stamp = dt.datetime.fromtimestamp(open_ms / 1000, UTC)
                        if open_ms % 1000 or stamp.minute or stamp.second or stamp.strftime("%Y-%m") != month:
                            reason = "open_time_not_exact_utc_hour_in_named_month"
                            audit["bad_open_rows"] += 1
                        elif delta != 0:
                            reason = "close_time_not_exact_hour_end"
                            audit["bad_close_rows"] += 1
                        else:
                            o, high, low, close, volume = map(Decimal, row[1:6])
                            trades = int(row[8])
                            values = (o, high, low, close, volume)
                            if (not all(value.is_finite() for value in values)
                                    or min(o, high, low, close) <= 0
                                    or low > min(o, close) or high < max(o, close)
                                    or low > high or volume < 0 or trades < 0):
                                reason = "invalid_ohlcv_or_trade_count_bounds"
                                audit["bad_ohlcv_rows"] += 1
                            elif (trades == 0) != (volume == 0):
                                reason = "zero_volume_trade_count_inconsistent"
                                audit["bad_zero_trade_rows"] += 1
                            else:
                                candidates[open_ms] = {
                                    "open": o, "high": high, "low": low, "close": close,
                                    "volume": volume, "trades": trades,
                                    "zero_trade": trades == 0,
                                }
                                audit["zero_trade_rows"] += int(trades == 0)
                        if reason:
                            invalid_rows.add(row_no)
                            issues.append(_quality_row(spot_symbol, month, path, row_no,
                                                       open_ms, close_ms, delta, unit, reason))
                    except (ValueError, InvalidOperation, OverflowError, OSError) as exc:
                        if not reason:
                            reason = f"row_parse_or_timestamp_error:{type(exc).__name__}:{exc}"
                            invalid_rows.add(row_no)
                            issues.append(_quality_row(spot_symbol, month, path, row_no,
                                                       open_ms, close_ms, delta, unit, reason))
            for open_ms, row_numbers in open_rows.items():
                if len(row_numbers) > 1:
                    candidates.pop(open_ms, None)
                    audit["duplicate_open_rows"] += len(row_numbers)
                    for row_no in row_numbers:
                        invalid_rows.add(row_no)
                        # Keep the independent close/OHLC finding too when present.
                        issues.append(_quality_row(spot_symbol, month, path, row_no,
                                                   open_ms, None, None, "mixed",
                                                   "duplicate_open_timestamp_all_copies_excluded"))
            audit["valid_rows"] = len(candidates)
            audit["rejected_rows"] = len(invalid_rows)
            audit["zero_trade_rows"] = sum(int(bar["zero_trade"]) for bar in candidates.values())
    except Exception as exc:
        records.clear()
        audit["status"] = "archive_unreadable"
        audit["error"] = f"{type(exc).__name__}: {exc}"
        audit["rejected_rows"] = audit["rows_seen"]
        return records, audit, issues
    audit["timestamp_units"] = ";".join(sorted(units))
    records = candidates
    if records:
        first_open_ms = min(records)
        last_open_ms = max(records)
        audit["first_open_ms"] = first_open_ms
        audit["last_open_ms"] = last_open_ms
        span = last_open_ms - first_open_ms
        if span >= 0 and span % HOUR_MS == 0:
            expected_count = span // HOUR_MS + 1
            audit["missing_h1_between_first_last"] = max(0, expected_count - len(records))
    return records, audit, issues


def _empty_archive_audit(spot_symbol: str, month: str, path: Path, status: str,
                         error: str = "") -> dict:
    return {
        "spot_symbol": spot_symbol, "month": month, "archive_path": str(path),
        "archive_url": source._archive_url(spot_symbol, month), "status": status,
        "compressed_bytes": path.stat().st_size if path.is_file() else 0,
        "member": "", "rows_seen": 0, "valid_rows": 0, "rejected_rows": 0,
        "bad_close_rows": 0, "bad_open_rows": 0, "bad_ohlcv_rows": 0,
        "bad_zero_trade_rows": 0, "duplicate_open_rows": 0,
        "zero_trade_rows": 0, "first_open_ms": "", "last_open_ms": "",
        "missing_h1_between_first_last": 0, "timestamp_units": "", "error": error,
    }


def _set_missing_archive(row: dict, status: str, reason: str) -> dict:
    row["covered"] = False
    row["spot_reclaim"] = None
    row["spot_confirms"] = None
    row["spot_status"] = status
    row["unknown_reason"] = reason
    return row


def _same_value(left: str | None, right: str | None) -> bool:
    if left in (None, "") and right in (None, ""):
        return True
    return str(left).lower() == str(right).lower()


def build(*, mini_check: bool = False) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Build output in memory only; caller chooses whether to persist it."""
    config = _load_config()
    source_config = _read_json(ROOT / config["inputs"]["source_config"])
    cache_root = ROOT / config["inputs"]["cache_root"]
    original_panel = ROOT / config["inputs"]["original_panel"]
    pair_index_path = source._pair_index_path(cache_root)
    if not pair_index_path.is_file() or not original_panel.is_file():
        raise FileNotFoundError("Frozen pair index or original event panel is missing")
    pair_index = _read_gz_json(pair_index_path)
    exact_pairs = set(pair_index.get("pairs", []))
    events, week_stats = source._events_to_process(source_config, False)
    baseline = _read_csv_gz(original_panel)
    if len(baseline) != len(events) or set(baseline) != {event["event_id"] for event in events}:
        raise ValueError("Frozen panel event IDs differ from the frozen candidate source")

    if mini_check:
        # Real-data check: in one of the seven damaged months, a later event
        # must recover while an event whose required window crosses the exact
        # malformed hour stays unknown. No source row or timestamp is changed.
        malformed = [
            ("2020-12", 1_608_559_200_000, 495),
            ("2021-02", 1_613_012_400_000, 244),
            ("2021-04", 1_619_323_200_000, 579),
            ("2021-08", 1_628_816_400_000, 290),
            ("2021-12", 1_640_318_400_000, 557),
            ("2022-11", 1_668_484_800_000, 341),
            ("2023-03", 1_679_659_200_000, 565),
        ]
        recovered = gap = None
        malformed_month = malformed_row = None
        for month, bad_open_ms, row_number in malformed:
            eligible = [e for e in events
                        if month in e["required_months"]
                        and baseline[e["event_id"]]["spot_status"] == "unknown_archive_download_or_parse"]
            after = next((e for e in eligible if e["week_start_ms"] > bad_open_ms), None)
            crossing = next((e for e in eligible if e["week_start_ms"] <= bad_open_ms < e["entry_ms"]), None)
            if after is not None and crossing is not None:
                recovered, gap = after, crossing
                malformed_month, malformed_row = month, row_number
                break
        if recovered is None or gap is None:
            raise AssertionError("No real source month has both recoverable and gap-dependent frozen events")
        mini_events = [recovered, gap]
        mini_ids = {event["event_id"] for event in mini_events}
    else:
        mini_events = events
        mini_ids = None

    events_by_symbol: dict[str, list[dict]] = {}
    for event in mini_events:
        events_by_symbol.setdefault(event["symbol"], []).append(event)

    event_rows: list[dict] = []
    row_quality: list[dict] = []
    archive_audit: list[dict] = []
    for symbol_index, (symbol, group) in enumerate(sorted(events_by_symbol.items()), 1):
        spot_symbol = symbol + "USDT"
        if spot_symbol not in exact_pairs:
            for event in group:
                row = source._base_event_row(event)
                source._mark_unknown(row, "unknown_no_exact_spot_pair",
                                     "no exact BASEUSDT Spot monthly kline prefix; aliases are not inferred")
                event_rows.append(row)
            continue

        index_path = source._monthly_index_path(cache_root, spot_symbol)
        if not index_path.is_file():
            for event in group:
                row = source._base_event_row(event)
                _set_missing_archive(row, "unknown_local_month_index_unavailable",
                                     "official month index is absent from the read-only local cache")
                event_rows.append(row)
            continue
        month_index = _read_gz_json(index_path)
        indexed_months = set(month_index.get("months", []))
        required_months = sorted({month for event in group for month in event["required_months"]})
        no_archive_months = {month for month in required_months if month not in indexed_months}
        month_failures: dict[str, tuple[str, str]] = {}
        bars: dict[int, dict] = {}
        units: set[str] = set()
        for month in required_months:
            path = _month_path(cache_root, spot_symbol, month)
            if month in no_archive_months:
                archive_audit.append(_empty_archive_audit(
                    spot_symbol, month, path, "absent_from_official_month_index",
                    "exact pair month key is absent from cached official index"))
                continue
            if not path.is_file():
                month_failures[month] = (
                    "unknown_local_archive_unavailable",
                    "official month is indexed but its exact cached ZIP is absent; repair performs no download",
                )
                archive_audit.append(_empty_archive_audit(spot_symbol, month, path, "local_archive_unavailable"))
                continue
            month_bars, audit, issues = _parse_archive(path, spot_symbol, month)
            archive_audit.append(audit)
            row_quality.extend(issues)
            units.update(filter(None, audit["timestamp_units"].split(";")))
            if audit["status"] != "read":
                month_failures[month] = ("unknown_archive_unreadable", audit["error"])
                continue
            bars.update(month_bars)
        inspection = {"timestamp_unit": ";".join(sorted(units)) if units else "unknown"}
        for event in group:
            row = source._event_geometry(event, bars, inspection,
                                         sorted(set(event["required_months"]) & no_archive_months))
            failed_month = next((month for month in event["required_months"] if month in month_failures), None)
            if failed_month is not None and row["spot_status"] != "unknown_no_month_archive":
                status, reason = month_failures[failed_month]
                _set_missing_archive(row, status, f"{failed_month}: {reason}")
            event_rows.append(row)
        if symbol_index % 25 == 0 or symbol_index == len(events_by_symbol):
            print(f"repair panel symbols: {symbol_index}/{len(events_by_symbol)}", flush=True)

    event_rows.sort(key=lambda row: (int(row["entry_ms"]), row["symbol"], row["event_id"]))
    if mini_check:
        if {row["event_id"] for row in event_rows} != mini_ids:
            raise AssertionError("Real-data mini emitted an unexpected event set")
        rows_by_id = {row["event_id"]: row for row in event_rows}
        if not rows_by_id[recovered["event_id"]]["covered"]:
            raise AssertionError("Event after the malformed source hour failed to recover")
        if rows_by_id[gap["event_id"]]["covered"] or rows_by_id[gap["event_id"]]["spot_h1_missing"] < 1:
            raise AssertionError("Event requiring the malformed source hour was not left unknown")
        if not any(q["month"] == malformed_month
                   and q["source_row_number"] == malformed_row
                   and q["reason"] == "close_time_not_exact_hour_end"
                   for q in row_quality):
            raise AssertionError("Mini did not audit the known malformed original CSV row")
        return event_rows, row_quality, archive_audit, {
            "mini": True, "recovered_event_id": recovered["event_id"],
            "gap_event_id": gap["event_id"], "recovered_status": rows_by_id[recovered["event_id"]]["spot_status"],
            "gap_status": rows_by_id[gap["event_id"]]["spot_status"],
            "gap_missing_h1": rows_by_id[gap["event_id"]]["spot_h1_missing"],
            "malformed_month": malformed_month,
            "audit_row": next(q for q in row_quality if q["month"] == malformed_month
                              and q["source_row_number"] == malformed_row),
        }

    output_by_id = {row["event_id"]: row for row in event_rows}
    if len(output_by_id) != len(events) or set(output_by_id) != {event["event_id"] for event in events}:
        raise ValueError("Output event IDs do not exactly match the frozen candidate set")
    old_covered = 0
    preserved = 0
    recovered = collections.Counter()
    for event_id, old in baseline.items():
        new = output_by_id[event_id]
        if old["covered"].lower() == "true":
            old_covered += 1
            if new["covered"] is not True:
                raise ValueError(f"Previously covered event became unknown: {event_id}")
            for field in PRESERVE_COLUMNS:
                if not _same_value(old.get(field), new.get(field)):
                    raise ValueError(f"Previously covered event geometry changed: {event_id} field={field}")
            preserved += 1
        if old["spot_status"] == "unknown_archive_download_or_parse":
            recovered[new["spot_status"]] += 1

    status_counts = collections.Counter(row["spot_status"] for row in event_rows)
    summary = {
        "schema": 1,
        "experiment_id": config["id"],
        "source_experiment": source_config["id"],
        "candidate_count": len(events),
        "output_count": len(event_rows),
        "source_week_audit": week_stats,
        "source_cache_read_only": True,
        "network_requests": 0,
        "source_cache_writes": 0,
        "original_covered_preserved": preserved,
        "original_covered_count": old_covered,
        "status_counts": dict(sorted(status_counts.items())),
        "prior_archive_parse_unknown_transition": dict(sorted(recovered.items())),
        "prior_archive_parse_unknown_recovered_observed": sum(
            count for status, count in recovered.items() if status in {"observed", "observed_zero_trade_hours"}
        ),
        "archive_count": len(archive_audit),
        "archive_status_counts": dict(sorted(collections.Counter(a["status"] for a in archive_audit).items())),
        "archives_read": sum(a["status"] == "read" for a in archive_audit),
        "archives_with_rejected_rows": sum(int(a["rejected_rows"]) > 0 for a in archive_audit),
        "source_rows_rejected": sum(int(a["rejected_rows"]) for a in archive_audit),
        "row_quality_reasons": dict(sorted(collections.Counter(q["reason"] for q in row_quality).items())),
        "row_quality_count": len(row_quality),
        "unknown_reason_counts": dict(sorted(collections.Counter(
            row["unknown_reason"] for row in event_rows if not row["covered"]
        ).items())),
        "limitations": [
            "Events whose exact required H1 window crosses a rejected source row remain unknown.",
            "Exact-pair archives missing from local cache or unreadable remain unknown; this builder does not download.",
            "Non-ASCII pair months without a locally cached exact archive remain unknown; no alias is inferred.",
        ],
    }
    return event_rows, row_quality, archive_audit, summary


def run(*, mini_check: bool = False) -> dict:
    rows, quality, archives, summary = build(mini_check=mini_check)
    if mini_check:
        print(json.dumps(summary, ensure_ascii=False, default=str, sort_keys=True))
        return summary
    config = _load_config()
    outputs = config["outputs"]
    existing_outputs = [str(ROOT / relative) for relative in outputs.values() if (ROOT / relative).exists()]
    if existing_outputs:
        raise FileExistsError(f"Refusing to overwrite repair outputs: {existing_outputs}")
    _write_csv_gz(ROOT / outputs["event_panel"], rows, source.PANEL_COLUMNS)
    _write_csv_gz(ROOT / outputs["row_quality"], quality, ROW_AUDIT_COLUMNS)
    _write_csv_gz(ROOT / outputs["archive_audit"], archives, ARCHIVE_AUDIT_COLUMNS)
    summary["generated_at_utc"] = dt.datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    _write_json(ROOT / outputs["summary"], summary)
    receipt = {
        "schema": 1, "experiment_id": config["id"],
        "source_config": config["inputs"]["source_config"],
        "source_panel": config["inputs"]["original_panel"],
        "source_cache": config["inputs"]["cache_root"],
        "source_cache_mutated": False, "network_requests": 0,
        "candidate_count": summary["candidate_count"],
        "output_count": summary["output_count"],
        "original_covered_preserved": summary["original_covered_preserved"],
        "original_covered_count": summary["original_covered_count"],
        "outputs": outputs,
    }
    _write_json(ROOT / outputs["source_receipt"], receipt)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Build and persist the new repair panel")
    group.add_argument("--mini-check", action="store_true", help="Run real-data mini checks without writing outputs")
    args = parser.parse_args()
    run(mini_check=args.mini_check)


if __name__ == "__main__":
    main()
