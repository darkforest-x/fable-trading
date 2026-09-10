"""Import genuine SPIKE V1 ledger signals for read-only historical browsing.

The importer consumes only a ledger's signal and next-open clock fields.  It
does not copy PnL, exits, returns, drawdown, fees, or a trade outcome into the
monitor.  Imported rows are marked ``source=replay`` and cannot seed either
candidate inference or a notification outbox.
"""
from __future__ import annotations

import argparse
import csv
import gzip
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256
from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.store import Store


def _milliseconds(value: object) -> int:
    """Parse a ledger UTC clock; reject absent and non-integral timestamps."""
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("missing timestamp")
    if isinstance(value, (int, float)) or str(value).strip().lstrip("-").isdigit():
        result = int(value)
        if result < 0:
            raise ValueError("invalid timestamp")
        return result
    text = str(value).strip().replace("Z", "+00:00")
    try:
        result = int(datetime.fromisoformat(text).astimezone(timezone.utc).timestamp() * 1000)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if result < 0:
        raise ValueError("invalid timestamp")
    return result


def _number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid " + name) from exc
    if result <= 0:
        raise ValueError("invalid " + name)
    return result


def normalize_row(row: dict[str, object], *, import_id: str) -> dict:
    """Validate one long-only V1 ledger row without reading outcome fields."""
    venue = str(row.get("venue", "")).strip().lower()
    symbol = str(row.get("symbol", "")).strip().upper()
    timeframe_min = int(str(row.get("timeframe_min", "")))
    if venue not in {"binance", "okx", "gate"} or not symbol or timeframe_min not in (30, 60, 240):
        raise ValueError("unsupported replay instrument")
    if str(row.get("direction", "")).strip().lower() != "long":
        raise ValueError("SPIKE V1 replay is long-only")
    step = timeframe_min * 60_000
    close = _milliseconds(row.get("signal_close_time"))
    open_ms = _milliseconds(row.get("signal_bar_open", close - step))
    if open_ms + step != close or open_ms % step:
        raise ValueError("unaligned signal bar")
    price = _number(row.get("signal_close"), "signal close")
    risk = _number(row.get("reference_signal_risk", row.get("risk")), "risk")
    entry = row.get("entry_time")
    execution_clock = _milliseconds(entry) if entry not in (None, "") else None
    return {
        "protocol": SIGNAL_PROTOCOL,
        "kind": SIGNAL_KIND,
        "source": "replay",
        "confirmation": "raw",
        "replay_import_id": import_id,
        "replay_unverified": True,
        "performance_status": "unverified",
        "venue": venue,
        "symbol": symbol,
        "timeframe": {30: "30m", 60: "1H", 240: "4H"}[timeframe_min],
        "timeframe_min": timeframe_min,
        "direction": "long",
        "side": "long",
        "bar_open_ms": open_ms,
        "bar_close_ms": close,
        "signal_close_time": close,
        "is_closed": True,
        "price": price,
        "risk": risk,
        "initial_stop": price - risk,
        "source_sha256": SOURCE_SHA256,
        "entry_reference": "next_open",
        # This is a historical ledger clock, never a live fill or notification.
        "executable_entry_time": execution_clock,
        "execution_clock_status": "backtest_unverified",
        "detected_at_ms": close,
        "ready": True,
        "confirmed": True,
    }


def import_rows(store: Store, rows: Iterable[dict[str, object]], *, import_id: str) -> dict[str, int]:
    """Insert validated rows with no outbox/candidate side effect, idempotently."""
    if not import_id or len(import_id) > 160:
        raise ValueError("invalid import id")
    inserted = already_present = outside_v1_contract = 0
    for row in rows:
        try:
            event = normalize_row(row, import_id=import_id)
        except ValueError as exc:
            # A covered ledger may include daily rows; this monitor only owns
            # 30m/1H/4H. Do not turn that known boundary into a partial crash.
            if str(exc) == "unsupported replay instrument" and str(row.get("timeframe_min", "")) not in {"30", "60", "240"}:
                outside_v1_contract += 1
                continue
            raise
        if store.upsert_event(event, notify=False, bark_notify=False):
            inserted += 1
        else:
            already_present += 1
    return {"inserted": inserted, "already_present": already_present,
            "outside_v1_contract": outside_v1_contract}


def import_csv(store: Store, path: Path, *, import_id: str) -> dict[str, int]:
    """Read a plain or gzip CSV ledger; no data source is fetched by this tool."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return import_rows(store, csv.DictReader(handle), import_id=import_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import V1 replay signals without backtest outcomes")
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--import-id", required=True)
    args = parser.parse_args()
    print(import_csv(Store(args.database), args.ledger, import_id=args.import_id))


if __name__ == "__main__":
    main()
