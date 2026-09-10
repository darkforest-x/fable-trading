"""Attach covered-ledger facts to imported SPIKE V1 replay events.

The monitor's replay importer intentionally starts signal-only: no outcome
field is allowed to affect browsing, candidates, or notification delivery.
This command is a later, explicit reconciliation.  It accepts only the frozen
covered-v2 ledger whose source/Pine hashes match the imported V1 event, joins
by the immutable ``venue/symbol/timeframe_min/signal_bar_open`` tuple, and
stores both the monitor journal id and the ledger source-event id.  Realized
and censored rows remain distinct.  A successful link is evidence of input
identity and ledger provenance, not an independent profitability validation.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from yoyo.evaluation.spike_v1_twoyear_allmarkets import METHOD_VERSION, PINE_SHA, RESULTS, SOURCE_SHA256
from yoyo.monitor.replay_chart import REPLAY_DATA_ROOT, ReplayChartUnavailable, _source_path
from yoyo.monitor.replay_import import _milliseconds
from yoyo.monitor.store import Store

V1_TIMEFRAME_MINUTES = (30, 60, 240)


def _sha256(path: Path) -> str:
    """Return the bytes hash of one immutable local artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clock(value: object) -> int:
    return _milliseconds(value)


def _finite(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid " + name) from exc
    if not math.isfinite(result):
        raise ValueError("invalid " + name)
    return result


def _censored(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    raise ValueError("invalid censored flag")


def _key(venue: object, symbol: object, timeframe_min: object, signal_bar_open: object) -> tuple[str, str, int, int]:
    try:
        minutes = int(timeframe_min)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid timeframe") from exc
    if minutes not in V1_TIMEFRAME_MINUTES:
        raise ValueError("outside V1 timeframe")
    venue_value, symbol_value = str(venue).lower(), str(symbol).upper()
    if venue_value not in {"binance", "okx", "gate"} or not symbol_value:
        raise ValueError("invalid replay provenance")
    return venue_value, symbol_value, minutes, _clock(signal_bar_open)


def _read_ledger(path: Path) -> dict[tuple[str, str, int, int], dict[str, object]]:
    opener = gzip.open if path.suffix == ".gz" else open
    result: dict[tuple[str, str, int, int], dict[str, object]] = {}
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                key = _key(row.get("venue"), row.get("symbol"), row.get("timeframe_min"), row.get("signal_bar_open"))
            except ValueError as exc:
                if str(exc) == "outside V1 timeframe":
                    continue
                raise
            if str(row.get("direction", "")).lower() != "long" or not row.get("event_id"):
                raise ValueError("invalid covered V1 ledger row")
            if key in result:
                raise ValueError("duplicate covered ledger tuple")
            result[key] = row
    return result


def _read_manifest(path: Path) -> dict[str, object]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("method_version") != METHOD_VERSION or manifest.get("source_sha256") != SOURCE_SHA256
            or manifest.get("pine_sha256") != PINE_SHA):
        raise ValueError("covered ledger provenance mismatch")
    return manifest


def _event_key(event: dict) -> tuple[str, str, int, int]:
    return _key(event.get("venue"), event.get("symbol"), event.get("timeframe_min"), event.get("bar_open_ms"))


def _outcome(row: dict[str, object]) -> tuple[str, dict[str, object]]:
    """Expose only a realized receipt, or explicitly retain censoring."""
    if _censored(row.get("censored")):
        return "covered_linked_censored_unverified", {"status": "censored"}
    return "covered_linked_realized_unverified", {
        "status": "realized",
        "entry_time_ms": _clock(row.get("entry_time")),
        "entry_price": _finite(row.get("entry_price"), "entry price"),
        "exit_time_ms": _clock(row.get("exit_time")),
        "exit_price": _finite(row.get("exit_price"), "exit price"),
        "exit_reason": str(row.get("exit_reason") or "unknown"),
        "net_r": _finite(row.get("net_r"), "net R"),
        "net_return": _finite(row.get("net_return"), "net return"),
        "fees_return": _finite(row.get("fees_return"), "fees return"),
    }


def build_updates(events: Iterable[dict], *, ledger_path: Path, manifest_path: Path,
                  ohlc_root: Path = REPLAY_DATA_ROOT) -> tuple[list[tuple[str, dict]], dict[str, int]]:
    """Prepare all reconciliation updates before writing a single journal row."""
    manifest = _read_manifest(manifest_path)
    ledger = _read_ledger(ledger_path)
    artifact_evidence = {
        "ledger_file": str(ledger_path), "ledger_sha256": _sha256(ledger_path),
        "coverage_receipt_file": str(manifest_path), "coverage_receipt_sha256": _sha256(manifest_path),
        "method_version": manifest["method_version"], "source_sha256": manifest["source_sha256"],
        "pine_sha256": manifest["pine_sha256"],
    }
    ohlc_hashes: dict[Path, str] = {}
    updates: list[tuple[str, dict]] = []
    stats = {"replay_events": 0, "matched_realized": 0, "matched_censored": 0,
             "unmatched": 0, "source_mismatch": 0, "ohlc_missing": 0}
    for event in events:
        if event.get("source") != "replay" or event.get("confirmation") != "raw":
            continue
        stats["replay_events"] += 1
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("replay event missing journal id")
        if event.get("source_sha256") != manifest["source_sha256"]:
            stats["source_mismatch"] += 1
            continue
        row = ledger.get(_event_key(event))
        if row is None:
            stats["unmatched"] += 1
            continue
        try:
            source_path, native_minutes = _source_path(event, Path(ohlc_root))
        except ReplayChartUnavailable:
            stats["ohlc_missing"] += 1
            continue
        if source_path not in ohlc_hashes:
            ohlc_hashes[source_path] = _sha256(source_path)
        status, outcome = _outcome(row)
        if outcome["status"] == "realized":
            stats["matched_realized"] += 1
        else:
            stats["matched_censored"] += 1
        updates.append((event_id, {
            "performance_status": status,
            "covered_ledger": {
                "link_status": outcome["status"],
                "monitor_event_id": event_id,
                "ledger_event_id": str(row["event_id"]),
                "match_key": {"venue": event["venue"], "symbol": event["symbol"],
                              "timeframe_min": int(event["timeframe_min"]), "signal_bar_open_ms": int(event["bar_open_ms"])},
                "evidence": {**artifact_evidence,
                             "frozen_ohlc_file": str(source_path), "frozen_ohlc_sha256": ohlc_hashes[source_path],
                             "frozen_ohlc_timeframe_min": native_minutes},
                "outcome": outcome,
            },
        }))
    return updates, stats


def link_replay_events(store: Store, *, ledger_path: Path = RESULTS / "covered_trade_ledger.csv.gz",
                       manifest_path: Path = RESULTS / "coverage_progress.json",
                       ohlc_root: Path = REPLAY_DATA_ROOT) -> dict[str, int]:
    """Link all current replay/raw events with no delivery or candidate side effect."""
    updates, stats = build_updates(store.list_events(limit=2000, source="replay", confirmation="raw"),
                                   ledger_path=Path(ledger_path), manifest_path=Path(manifest_path), ohlc_root=Path(ohlc_root))
    for event_id, update in updates:
        if not store.update_event_payload(event_id, update):
            raise RuntimeError("replay event disappeared during reconciliation")
    stats["linked"] = len(updates)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Link imported replay events to the covered V2 ledger")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--ledger", type=Path, default=RESULTS / "covered_trade_ledger.csv.gz")
    parser.add_argument("--coverage-manifest", type=Path, default=RESULTS / "coverage_progress.json")
    parser.add_argument("--ohlc-root", type=Path, default=REPLAY_DATA_ROOT)
    args = parser.parse_args()
    print(json.dumps(link_replay_events(Store(args.database), ledger_path=args.ledger,
                                        manifest_path=args.coverage_manifest, ohlc_root=args.ohlc_root), sort_keys=True))


if __name__ == "__main__":
    main()
