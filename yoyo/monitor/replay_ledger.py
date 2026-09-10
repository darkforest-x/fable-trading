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
import os
import shutil
import tempfile
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


def _stale_audit(event: dict) -> dict[str, object] | None:
    """Carry a prior invalidated link forward without restoring its outcome."""
    link = event.get("covered_ledger")
    if (event.get("performance_status") == "stale_evidence_unverified" and isinstance(link, dict)
            and link.get("link_status") == "stale_evidence"):
        return link
    return None


def _unlinked_update(event: dict, *, artifact_evidence: dict[str, object], link_status: str,
                     reason: str, row: dict[str, object] | None = None) -> tuple[str, dict]:
    """Record a failed audit link without exposing an outcome field."""
    event_id = event["id"]
    link = {
        "link_status": link_status, "reason": reason, "monitor_event_id": event_id,
        "match_key": {"venue": event["venue"], "symbol": event["symbol"],
                      "timeframe_min": int(event["timeframe_min"]), "signal_bar_open_ms": int(event["bar_open_ms"])},
        "evidence": artifact_evidence,
    }
    if row is not None:
        link["ledger_event_id"] = str(row["event_id"])
    return event_id, {"performance_status": "unverified", "covered_ledger": link}


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
             "unmatched": 0, "source_mismatch": 0, "ohlc_missing": 0, "source_path_error": 0}
    for event in events:
        if event.get("source") != "replay" or event.get("confirmation") != "raw":
            continue
        stats["replay_events"] += 1
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("replay event missing journal id")
        if event.get("source_sha256") != manifest["source_sha256"]:
            stats["source_mismatch"] += 1
            updates.append(_unlinked_update(event, artifact_evidence=artifact_evidence,
                                            link_status="source_mismatch", reason="replay_source_sha256_mismatch"))
            continue
        row = ledger.get(_event_key(event))
        if row is None:
            stats["unmatched"] += 1
            updates.append(_unlinked_update(event, artifact_evidence=artifact_evidence,
                                            link_status="unmatched", reason="covered_ledger_tuple_missing"))
            continue
        try:
            source_path, native_minutes = _source_path(event, Path(ohlc_root))
        except ReplayChartUnavailable as error:
            if error.code == "frozen_ohlc_missing":
                stats["ohlc_missing"] += 1
                status = "ohlc_missing"
            else:
                stats["source_path_error"] += 1
                status = "source_path_error"
            updates.append(_unlinked_update(event, artifact_evidence=artifact_evidence,
                                            link_status=status, reason=error.code, row=row))
            continue
        if source_path not in ohlc_hashes:
            ohlc_hashes[source_path] = _sha256(source_path)
        status, outcome = _outcome(row)
        if outcome["status"] == "realized":
            stats["matched_realized"] += 1
        else:
            stats["matched_censored"] += 1
        link = {
            "link_status": outcome["status"],
            "monitor_event_id": event_id,
            "ledger_event_id": str(row["event_id"]),
            "match_key": {"venue": event["venue"], "symbol": event["symbol"],
                          "timeframe_min": int(event["timeframe_min"]), "signal_bar_open_ms": int(event["bar_open_ms"])},
            "evidence": {**artifact_evidence,
                         "frozen_ohlc_file": str(source_path), "frozen_ohlc_sha256": ohlc_hashes[source_path],
                         "frozen_ohlc_timeframe_min": native_minutes},
            "outcome": outcome,
        }
        prior_stale = _stale_audit(event)
        if prior_stale is not None:
            link["superseded_stale_evidence"] = prior_stale
        updates.append((event_id, {"performance_status": status, "covered_ledger": link}))
    return updates, stats


def invalidate_replay_links(store: Store, *, reason: str) -> int:
    """Hide mutable-ledger outcomes while retaining the exact prior evidence.

    The original payload remains under ``previous_link`` for audit.  The public
    link intentionally contains no ``outcome`` so a stale receipt cannot be
    displayed as a current ledger fact while recovery is incomplete.
    """
    if not isinstance(reason, str) or not reason:
        raise ValueError("invalid stale evidence reason")
    count = 0
    for event in _replay_events(store):
        prior = event.get("covered_ledger")
        if not isinstance(prior, dict):
            continue
        if event.get("performance_status") == "stale_evidence_unverified" and prior.get("link_status") == "stale_evidence":
            continue
        audit = {
            "link_status": "stale_evidence",
            "reason": reason,
            "previous_performance_status": event.get("performance_status"),
            "previous_link": prior,
        }
        if not store.update_event_payload(event["id"], {
            "performance_status": "stale_evidence_unverified", "covered_ledger": audit,
        }):
            raise RuntimeError("replay event disappeared during invalidation")
        count += 1
    return count


def _immutable_copy(source: Path, destination: Path) -> str:
    """Copy one artifact atomically and fail if its source changes mid-copy."""
    before = _sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256(destination) != before:
            raise ValueError("immutable artifact name collision")
        return before
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        with source.open("rb") as input_handle:
            shutil.copyfileobj(input_handle, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if _sha256(source) != before or _sha256(temporary) != before:
            raise ValueError("artifact changed while freezing")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return before


def freeze_ledger_snapshot(*, ledger_path: Path, manifest_path: Path, snapshot_dir: Path) -> tuple[Path, Path]:
    """Create content-addressed copies before linking monitor events to them."""
    ledger_path, manifest_path, snapshot_dir = Path(ledger_path), Path(manifest_path), Path(snapshot_dir)
    _read_manifest(manifest_path)
    ledger_sha, manifest_sha = _sha256(ledger_path), _sha256(manifest_path)
    ledger_destination = snapshot_dir / f"covered_trade_ledger.{ledger_sha}.csv.gz"
    manifest_destination = snapshot_dir / f"coverage_progress.{manifest_sha}.json"
    _immutable_copy(ledger_path, ledger_destination)
    _immutable_copy(manifest_path, manifest_destination)
    receipt = snapshot_dir / f"snapshot.{ledger_sha}.{manifest_sha}.json"
    if not receipt.exists():
        payload = {
            "ledger_file": str(ledger_destination), "ledger_sha256": ledger_sha,
            "coverage_receipt_file": str(manifest_destination), "coverage_receipt_sha256": manifest_sha,
            "source_ledger_file": str(ledger_path), "source_coverage_receipt_file": str(manifest_path),
        }
        temporary = receipt.with_suffix(receipt.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, receipt)
    return ledger_destination, manifest_destination


def reconcile_replay_events(store: Store, *, ledger_path: Path, manifest_path: Path, snapshot_dir: Path,
                            ohlc_root: Path = REPLAY_DATA_ROOT, stale_reason: str) -> dict[str, int]:
    """Invalidate old evidence, freeze bytes, then re-link only exact matches."""
    invalidated = invalidate_replay_links(store, reason=stale_reason)
    immutable_ledger, immutable_manifest = freeze_ledger_snapshot(
        ledger_path=ledger_path, manifest_path=manifest_path, snapshot_dir=snapshot_dir,
    )
    result = link_replay_events(store, ledger_path=immutable_ledger, manifest_path=immutable_manifest,
                                ohlc_root=ohlc_root)
    result["invalidated"] = invalidated
    return result


def _replay_events(store: Store, *, page_size: int = 2000) -> Iterable[dict]:
    """Yield every replay/raw journal row in stable cursor order.

    Reconciliation is an audit over the whole imported replay corpus.  It must
    not inherit the browsing API's first-page limit.
    """
    cursor: tuple[int, str] | None = None
    while True:
        kwargs = {"limit": page_size, "source": "replay", "confirmation": "raw"}
        if cursor is not None:
            kwargs.update(before_close_ms=cursor[0], before_id=cursor[1])
        page = store.list_events(**kwargs)
        if not page:
            return
        for event in page:
            yield event
        if len(page) < page_size:
            return
        last = page[-1]
        close, event_id = last.get("bar_close_ms"), last.get("id")
        if isinstance(close, bool) or not isinstance(close, int) or not isinstance(event_id, str) or not event_id:
            raise ValueError("invalid replay event cursor")
        cursor = close, event_id


def link_replay_events(store: Store, *, ledger_path: Path = RESULTS / "covered_trade_ledger.csv.gz",
                       manifest_path: Path = RESULTS / "coverage_progress.json",
                       ohlc_root: Path = REPLAY_DATA_ROOT, page_size: int = 2000) -> dict[str, int]:
    """Link all current replay/raw events with no delivery or candidate side effect."""
    updates, stats = build_updates(_replay_events(store, page_size=page_size),
                                   ledger_path=Path(ledger_path), manifest_path=Path(manifest_path), ohlc_root=Path(ohlc_root))
    for event_id, update in updates:
        if not store.update_event_payload(event_id, update):
            raise RuntimeError("replay event disappeared during reconciliation")
    stats["linked"] = stats["matched_realized"] + stats["matched_censored"]
    stats["reconciled"] = len(updates)
    return stats


def write_link_receipt(store: Store, path: Path) -> int:
    """Write one auditable row per replay reconciliation state without aggregation."""
    rows = []
    for event in _replay_events(store):
        link = event.get("covered_ledger")
        if not isinstance(link, dict) or link.get("monitor_event_id") != event.get("id"):
            continue
        evidence, outcome = link.get("evidence", {}), link.get("outcome", {})
        rows.append({
            "monitor_event_id": event["id"], "ledger_event_id": link.get("ledger_event_id"),
            "venue": event.get("venue"), "symbol": event.get("symbol"), "timeframe_min": event.get("timeframe_min"),
            "signal_bar_open_ms": event.get("bar_open_ms"), "link_status": link.get("link_status"),
            "link_reason": link.get("reason"), "performance_status": event.get("performance_status"), "source_sha256": evidence.get("source_sha256"),
            "pine_sha256": evidence.get("pine_sha256"), "ledger_sha256": evidence.get("ledger_sha256"),
            "coverage_receipt_sha256": evidence.get("coverage_receipt_sha256"),
            "frozen_ohlc_file": evidence.get("frozen_ohlc_file"), "frozen_ohlc_sha256": evidence.get("frozen_ohlc_sha256"),
            "frozen_ohlc_timeframe_min": evidence.get("frozen_ohlc_timeframe_min"),
            "exit_reason": outcome.get("exit_reason") if outcome.get("status") == "realized" else None,
            "net_r": outcome.get("net_r") if outcome.get("status") == "realized" else None,
        })
    rows.sort(key=lambda row: (row["venue"], row["symbol"], int(row["timeframe_min"]), int(row["signal_bar_open_ms"])))
    fields = ["monitor_event_id", "ledger_event_id", "venue", "symbol", "timeframe_min", "signal_bar_open_ms",
              "link_status", "link_reason", "performance_status", "source_sha256", "pine_sha256", "ledger_sha256",
              "coverage_receipt_sha256", "frozen_ohlc_file", "frozen_ohlc_sha256", "frozen_ohlc_timeframe_min",
              "exit_reason", "net_r"]
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Link imported replay events to the covered V2 ledger")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--ledger", type=Path, default=RESULTS / "covered_trade_ledger.csv.gz")
    parser.add_argument("--coverage-manifest", type=Path, default=RESULTS / "coverage_progress.json")
    parser.add_argument("--ohlc-root", type=Path, default=REPLAY_DATA_ROOT)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--stale-reason", default="mutable_ledger_artifact_replaced")
    args = parser.parse_args()
    store = Store(args.database)
    if args.snapshot_dir:
        result = reconcile_replay_events(store, ledger_path=args.ledger, manifest_path=args.coverage_manifest,
                                         snapshot_dir=args.snapshot_dir, ohlc_root=args.ohlc_root,
                                         stale_reason=args.stale_reason)
    else:
        result = link_replay_events(store, ledger_path=args.ledger, manifest_path=args.coverage_manifest,
                                    ohlc_root=args.ohlc_root)
    if args.receipt:
        result["receipt_rows"] = write_link_receipt(store, args.receipt)
        result["receipt_file"] = str(args.receipt)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
