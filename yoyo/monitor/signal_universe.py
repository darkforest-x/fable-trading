"""Separate new-signal admission from required closed-bar maintenance.

Only the default local execution ledger is discovered, read-only and without
credentials. Unavailable ownership keeps excluded live instruments refreshing;
it never re-enables their entries. Historical reference positions also retain
their candles until their saved projection has resolved.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from yoyo.contracts.signal_universe import instrument_exclusion, public_policy
from yoyo.monitor.v130_policy import SIGNAL_KIND, SIGNAL_PROTOCOL

MONITOR_RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"
EXECUTION_RUNTIME = Path.home() / "Library/Application Support/Fable/OKXExecution"


def execution_symbols(monitor_runtime):
    if Path(monitor_runtime).resolve() != MONITOR_RUNTIME.resolve():
        return set()
    try:
        config = EXECUTION_RUNTIME / "config.json"
        if not config.exists():
            return set()
        if config.is_symlink():
            return None
        if Path(json.loads(config.read_text())["source"]).resolve() != Path(monitor_runtime).resolve():
            return set()
        path = EXECUTION_RUNTIME / "execution.sqlite3"
        if not path.is_file() or path.is_symlink():
            return None
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT value FROM trades")]
        result = set()
        for row in rows:
            if row["state"] not in {"closed", "rejected", "skipped"}:
                symbol = row.get("symbol") or (row.get("event") or {}).get("symbol")
                if not isinstance(symbol, str):
                    return None
                result.add(symbol)
        return result
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return None


def plan_scan(store, instruments, now_ms):
    eligible = {row["instId"] for row in instruments if instrument_exclusion(row) is None}
    with store.connect() as db:
        references = {r[0] for r in db.execute(
            "SELECT DISTINCT symbol FROM events WHERE kind=? AND json_extract(payload,'$.protocol')=? "
            "AND (json_extract(payload,'$.performance.status')='active' OR "
            "json_extract(payload,'$.performance.reason')='awaiting_next_closed_bar')",
            (SIGNAL_KIND, SIGNAL_PROTOCOL))}
    owned = execution_symbols(store.path.parent)
    excluded = {row["instId"]: instrument_exclusion(row) for row in instruments if row["instId"] not in eligible}
    maintenance = set(excluded) if owned is None else (references | owned) & set(excluded)
    selected = [row for row in instruments if row["instId"] in eligible | maintenance]
    store.sync_market_admission(eligible, excluded)
    store.set_meta("universe", dict(public_policy(), count=len(eligible), available_count=len(instruments),
                   excluded_count=len(excluded), excluded_symbols=sorted(excluded),
                   maintenance_count=len(maintenance), maintenance_symbols=sorted(maintenance),
                   management_ownership_known=owned is not None, scan_count=len(selected), updated_at_ms=now_ms))
    return selected, eligible
