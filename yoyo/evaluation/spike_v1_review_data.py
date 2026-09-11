"""Build the frozen 133-record SPIKE Burst V1 historical-review chart contract.

This is a display-only assembler.  It selects the fixed Beijing-time review
window from the immutable covered-trade ledger and six explicitly named
live-journal observations.  V1 features are always calculated on each full,
continuous source segment before the chart is sliced, so chart warmup cannot
silently turn into a future-looking short-window calculation.  Later candles
are labelled review context and are never execution, training, or P&L input.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features

ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXPERIMENT = "exp-spike-v1-twoyear-allmarkets-20260911-v1"
EXPERIMENT = "exp-spike-v1-okx-133-review-20260911"
OUTPUT = ROOT / "experiments" / "active" / EXPERIMENT / "data"
SOURCE_ROOT = ROOT / "experiments" / "active" / SOURCE_EXPERIMENT
LEDGER = SOURCE_ROOT / "results" / "immutable_replay_ledger" / (
    "covered_trade_ledger.b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578.csv.gz"
)
LEDGER_SHA256 = "b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578"
MONITOR_DB = Path("/Users/zhangzc/Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3")
START = pd.Timestamp("2026-08-26T16:00:00Z")  # 2026-08-27 00:00 BJT
END = pd.Timestamp("2026-09-10T16:00:00Z")    # 2026-09-11 00:00 BJT
TIMEFRAMES = {30: "30m", 60: "1H", 240: "4H"}
BEFORE_BARS, AFTER_BARS, MAX_BARS = 100, 144, 1500
FUTURE_CONTEXT = "historical_review_only_not_model_input"

# This list is a contract, rather than a query over an evolving journal.
LIVE_SPECS = (
    ("ISRG-USDT-SWAP", 30, "2026-09-03T13:00:00Z"),
    ("SOL-USD-SWAP", 30, "2026-09-03T15:00:00Z"),
    ("UNI-USD-SWAP", 30, "2026-09-05T16:30:00Z"),
    ("ON-USDT-SWAP", 30, "2026-09-08T00:30:00Z"),
    ("SOXS-USDT-SWAP", 30, "2026-09-10T13:00:00Z"),
    ("STABLE-USDT-SWAP", 60, "2026-09-10T14:00:00Z"),
)


class ReviewDataError(ValueError):
    """Fail closed when a frozen review record or its OHLC evidence drifts."""


@dataclass(frozen=True)
class ReviewRecord:
    """A frozen selection row with only display/provenance fields."""

    record_id: str
    source: str
    symbol: str
    timeframe_min: int
    signal_close_ms: int
    payload: dict[str, Any]


def sha256_file(path: Path) -> str:
    """Hash exactly the evidence bytes, including gzip encoding."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_ms(value: Any) -> int:
    """Normalize the ledger's ISO clock or monitor's millisecond clock."""
    if isinstance(value, (int, np.integer)):
        return int(value)
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return int(stamp.value // 1_000_000)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def _read_ohlcv(path: Path) -> tuple[pd.DataFrame, str]:
    """Read source time explicitly, allowing one unambiguous legacy index column.

    Some frozen CSVs retain their genuine timestamp in pandas' ``Unnamed: 0``
    index export while their nominal ``time`` column is entirely blank.  That
    representation is recoverable without rewriting or substituting evidence,
    but partial values or two competing time columns remain fail-closed.
    """
    frame = pd.read_csv(path, compression="gzip")
    required = ["time", "open", "high", "low", "close", "volume"]
    if set(required) - set(frame.columns):
        raise ReviewDataError("ohlcv_schema_missing")
    declared = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    candidate_name = "Unnamed: 0"
    candidate = pd.to_datetime(frame[candidate_name], utc=True, errors="coerce") if candidate_name in frame else None
    if declared.notna().all() and (candidate is None or candidate.isna().all()):
        timestamps, timestamp_source = declared, "time"
    elif declared.isna().all() and candidate is not None and candidate.notna().all():
        timestamps, timestamp_source = candidate, candidate_name
    else:
        raise ReviewDataError("ohlcv_timestamp_ambiguous_or_partial")
    frame = frame.loc[:, ["open", "high", "low", "close", "volume"]]
    frame.index = pd.DatetimeIndex(timestamps)
    return _validate_ohlcv(frame), timestamp_source


def _validate_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep invalid/missing OHLC explicit rather than making a visual approximation."""
    required = ["open", "high", "low", "close", "volume"]
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ReviewDataError("ohlcv_timeline_invalid")
    result = frame.loc[:, required].astype(float)
    prices = result[["open", "high", "low", "close"]]
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any() or (result.volume < 0).any():
        raise ReviewDataError("ohlcv_values_invalid")
    if (result.high < result[["open", "close", "low"]].max(axis=1)).any() or (result.low > result[["open", "close", "high"]].min(axis=1)).any():
        raise ReviewDataError("ohlcv_geometry_invalid")
    return result


def _aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Use the frozen V1 UTC epoch-aligned aggregation, retaining complete bins only."""
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    output = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return output.loc[grouped.size().eq(minutes // 30)]


def _segments(frame: pd.DataFrame, minutes: int) -> list[pd.DataFrame]:
    expected_ns = pd.Timedelta(minutes=minutes).value
    cuts = np.flatnonzero(np.diff(frame.index.asi8) != expected_ns) + 1
    edges = [0, *cuts.tolist(), len(frame)]
    return [frame.iloc[left:right] for left, right in zip(edges, edges[1:])]


def _load_ledger_records(ledger: Path) -> list[ReviewRecord]:
    if sha256_file(ledger) != LEDGER_SHA256:
        raise ReviewDataError("immutable_ledger_sha256_mismatch")
    with gzip.open(ledger, "rt", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    selected: list[ReviewRecord] = []
    for row in rows:
        close_ms, minutes = _utc_ms(row["signal_close_time"]), int(row["timeframe_min"])
        if (row.get("venue", "").lower() == "okx" and row.get("direction") == "long" and minutes in TIMEFRAMES
                and START.value // 1_000_000 <= close_ms < END.value // 1_000_000):
            selected.append(ReviewRecord(row["event_id"], "covered_ledger", row["symbol"], minutes, close_ms, row))
    selected.sort(key=lambda item: (item.signal_close_ms, item.timeframe_min, item.symbol, item.record_id))
    return selected


def _load_live_records(db_path: Path) -> list[ReviewRecord]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        output: list[ReviewRecord] = []
        for symbol, minutes, close_iso in LIVE_SPECS:
            close_ms = _utc_ms(close_iso)
            rows = connection.execute(
                "SELECT id,payload FROM events WHERE symbol=? AND timeframe=? AND kind='spike_burst_v1' "
                "AND side='long' AND close_ms=? AND json_extract(payload, '$.source')='live'",
                (symbol, TIMEFRAMES[minutes], close_ms),
            ).fetchall()
            if len(rows) != 1:
                raise ReviewDataError(f"live_frozen_event_count:{symbol}:{minutes}:{len(rows)}")
            event_id, raw = rows[0]
            payload = json.loads(raw)
            if payload.get("bar_close_ms") != close_ms or payload.get("timeframe_min") != minutes:
                raise ReviewDataError(f"live_frozen_event_clock_mismatch:{symbol}")
            output.append(ReviewRecord(event_id, "live_journal", symbol, minutes, close_ms, payload))
    finally:
        connection.close()
    return output


def select_records(ledger: Path = LEDGER, db_path: Path = MONITOR_DB) -> list[ReviewRecord]:
    """Return exactly the predeclared 127 ledger rows and six live observations."""
    records = [*_load_ledger_records(ledger), *_load_live_records(db_path)]
    identity = {(item.symbol, item.timeframe_min, item.signal_close_ms, "long") for item in records}
    counts = Counter(item.timeframe_min for item in records)
    if len(records) != 133 or len(identity) != 133 or counts != Counter({30: 75, 60: 42, 240: 16}):
        raise ReviewDataError(f"frozen_selection_drift:records={len(records)} identities={len(identity)} counts={dict(counts)}")
    return sorted(records, key=lambda item: (item.signal_close_ms, item.timeframe_min, item.symbol, item.record_id))


def _ledger_evidence_map(db_path: Path) -> dict[str, dict[str, Any]]:
    """Read the existing replay import's immutable OHLC attestations once."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT payload FROM events WHERE json_extract(payload, '$.source')='replay'").fetchall()
    finally:
        connection.close()
    output: dict[str, dict[str, Any]] = {}
    for (raw,) in rows:
        try:
            link = json.loads(raw).get("covered_ledger", {})
            ledger_id, evidence = link.get("ledger_event_id"), link.get("evidence")
        except json.JSONDecodeError:
            continue
        if not isinstance(ledger_id, str) or not isinstance(evidence, dict):
            continue
        prior = output.setdefault(ledger_id, evidence)
        if prior != evidence:
            raise ReviewDataError(f"covered_ledger_evidence_conflict:{ledger_id}")
    return output


def _checkpoint_path(output_root: Path, record: ReviewRecord) -> Path:
    return output_root / "live_checkpoint_sources" / f"{_slug(record.symbol)}_{record.timeframe_min}m_{record.signal_close_ms}.json.gz"


def _read_checkpoint(db_path: Path, record: ReviewRecord) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT payload FROM candle_checkpoints WHERE symbol=? AND timeframe=?", (record.symbol, TIMEFRAMES[record.timeframe_min])).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ReviewDataError(f"live_checkpoint_missing:{record.symbol}:{record.timeframe_min}")
    try:
        candles = json.loads(gzip.decompress(row[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ReviewDataError(f"live_checkpoint_unreadable:{record.symbol}") from exc
    if not isinstance(candles, list):
        raise ReviewDataError(f"live_checkpoint_schema:{record.symbol}")
    return candles


def _live_ohlcv(output_root: Path, db_path: Path, record: ReviewRecord) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Freeze a checkpoint once; reruns use its copied bytes rather than live monitor state."""
    path = _checkpoint_path(output_root, record)
    if path.exists():
        raw = gzip.decompress(path.read_bytes())
    else:
        raw = json.dumps(_read_checkpoint(db_path, record), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(raw, mtime=0))
    try:
        candles = json.loads(raw)
        frame = pd.DataFrame(candles).rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        frame["time"] = pd.to_datetime(frame["time"], unit="ms", utc=True)
        frame = frame.set_index("time")
        return _validate_ohlcv(frame), {"type": "frozen_live_checkpoint", "path": str(path.relative_to(output_root)), "sha256": sha256_file(path)}
    except (KeyError, TypeError, ValueError, pd.errors.ParserError) as exc:
        raise ReviewDataError(f"live_checkpoint_schema:{record.symbol}") from exc


def _source_ohlcv(output_root: Path, db_path: Path, record: ReviewRecord, evidence: dict[str, Any] | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    if record.source == "live_journal":
        return _live_ohlcv(output_root, db_path, record)
    path = SOURCE_ROOT / "data" / "normalized" / "okx" / f"{record.symbol}_30m.csv.gz"
    if not path.is_file():
        raise ReviewDataError("covered_ledger_frozen_ohlc_missing")
    if not isinstance(evidence, dict):
        raise ReviewDataError("covered_ledger_evidence_missing")
    actual_hash = sha256_file(path)
    if (evidence.get("ledger_sha256") != LEDGER_SHA256 or evidence.get("frozen_ohlc_sha256") != actual_hash
            or evidence.get("frozen_ohlc_timeframe_min") != 30):
        raise ReviewDataError("covered_ledger_frozen_ohlc_hash_mismatch")
    frame, timestamp_source = _read_ohlcv(path)
    if record.timeframe_min != 30:
        frame = _aggregate(frame, record.timeframe_min)
    return frame, {"type": "covered_ledger_frozen_ohlc", "path": str(path.relative_to(ROOT)), "sha256": actual_hash, "native_timeframe_min": 30, "timestamp_source": timestamp_source,
                   "covered_ledger_evidence": evidence}


def _chart(record: ReviewRecord, frame: pd.DataFrame, provenance: dict[str, Any], *, enriched_segments: list[pd.DataFrame] | None = None) -> dict[str, Any]:
    target = pd.Timestamp(record.signal_close_ms - record.timeframe_min * 60_000, unit="ms", tz="UTC")
    raw_segments = _segments(frame, record.timeframe_min)
    segment_index = next((number for number, part in enumerate(raw_segments) if target in part.index), None)
    segment = raw_segments[segment_index] if segment_index is not None else None
    if segment is None:
        raise ReviewDataError("signal_bar_missing_from_ohlcv")
    enriched = (enriched_segments or [features(part) for part in raw_segments])[segment_index]
    position = int(enriched.index.get_loc(target))
    exit_ms = _utc_ms(record.payload["exit_time"]) if record.source == "covered_ledger" and record.payload.get("exit_time") else None
    # The viewer may start focused on 100/144 nearby bars, but the review
    # record itself retains the complete known path through exit+72 or the
    # frozen OHLC cutoff.  A crop must never erase the actual ledger exit.
    end = len(enriched)
    if exit_ms is not None:
        exit_open = pd.Timestamp(exit_ms, unit="ms", tz="UTC")
        if exit_open in enriched.index:
            end = min(len(enriched), int(enriched.index.get_loc(exit_open)) + 73)
    start = max(0, position - BEFORE_BARS)
    if end - start > MAX_BARS:
        end = start + MAX_BARS
    display = enriched.iloc[start:end]
    candles = []
    for index, row in display.iterrows():
        candle = {"t": int(index.value // 1_000_000), "o": float(row.open), "h": float(row.high), "l": float(row.low), "c": float(row.close), "v": float(row.volume)}
        for source, name in (("s20", "sma20"), ("e20", "ema20"), ("s60", "sma60"), ("e60", "ema60"), ("s120", "sma120"), ("e120", "ema120"), ("md", "md"), ("sb", "sb")):
            candle[name] = _finite(row[source])
        candles.append(candle)
    payload = record.payload
    signal_close_price = _finite(payload.get("signal_close") if record.source == "covered_ledger" else payload.get("price"))
    if signal_close_price is not None and not np.isclose(float(enriched.loc[target, "close"]), signal_close_price, rtol=1e-10, atol=1e-12):
        raise ReviewDataError("signal_close_price_mismatch")
    initial_stop = _finite(payload.get("initial_stop"))
    stop_provenance = "live_event_explicit_initial_stop"
    if record.source == "covered_ledger":
        risk = _finite(payload.get("reference_signal_risk"))
        initial_stop = signal_close_price - risk if signal_close_price is not None and risk is not None else None
        stop_provenance = "derived_from_frozen_signal_close_reference_signal_risk_long"
    entry = None if record.source == "live_journal" else {"time_ms": _utc_ms(payload["entry_time"]), "price": _finite(payload["entry_price"]), "reference": "covered_ledger_next_open"}
    exit = None if record.source == "live_journal" or not payload.get("exit_time") else {"time_ms": _utc_ms(payload["exit_time"]), "price": _finite(payload["exit_price"]), "reason": payload.get("exit_reason")}
    return {
        "id": record.record_id, "source": record.source, "symbol": record.symbol, "venue": "okx", "side": "long",
        "kind": "spike_burst_v1", "timeframe": TIMEFRAMES[record.timeframe_min], "timeframe_min": record.timeframe_min,
        "signal_bar_open_ms": int(target.value // 1_000_000), "signal_close_ms": record.signal_close_ms,
        "display_time_bjt": (target + pd.Timedelta(minutes=record.timeframe_min)).tz_convert("Asia/Shanghai").isoformat(),
        "signal_close_price": signal_close_price, "reference_price": signal_close_price, "initial_stop": initial_stop,
        "signal": {"bar_open_ms": int(target.value // 1_000_000), "time_ms": record.signal_close_ms,
                   "display_time_bjt": (target + pd.Timedelta(minutes=record.timeframe_min)).tz_convert("Asia/Shanghai").isoformat(),
                   "close_price": signal_close_price},
        "entry": entry, "exit": exit, "live_status": "no_covered_trade_ledger" if record.source == "live_journal" else None,
        "candles": candles, "signal_candle_index": position - start,
        "coverage": {"available_before_bars": position, "available_after_bars": len(enriched) - position - 1,
                     "display_before_bars": position - start, "display_after_bars": end - position - 1,
                     "insufficient_before": position < BEFORE_BARS, "insufficient_after": len(enriched) - position - 1 < AFTER_BARS},
        "future_context": FUTURE_CONTEXT,
        "provenance": {**provenance, "source_sha256": payload.get("source_sha256") or provenance.get("covered_ledger_evidence", {}).get("source_sha256"),
                       "feature_source": "yoyo.evaluation.spike_burst_replay.features_full_continuous_segment_before_slice",
                       "initial_stop": stop_provenance, "active_stop": "not_emitted_no_tracking_replay"},
        "state": {"status": "available", "error": None},
    }


def build(output_root: Path = OUTPUT, ledger: Path = LEDGER, db_path: Path = MONITOR_DB, *, only_missing: bool = False) -> dict[str, Any]:
    """Build one JSON per immutable record and a deterministic index manifest."""
    records = select_records(ledger, db_path)
    evidence_by_ledger_id = _ledger_evidence_map(db_path)
    charts = output_root / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    manifest_records = []
    source_cache: dict[tuple[str, str, int, str], tuple[pd.DataFrame, dict[str, Any], list[pd.DataFrame]]] = {}
    prior_records: dict[str, dict[str, Any]] = {}
    prior_manifest = output_root / "manifest.json"
    if only_missing and prior_manifest.is_file():
        prior_records = {str(row.get("id")): row for row in json.loads(prior_manifest.read_text(encoding="utf-8")).get("records", [])}
    for sequence, record in enumerate(records, 1):
        filename = f"{sequence:03d}_{_slug(record.symbol)}_{record.timeframe_min}m_{record.signal_close_ms}.json"
        previous = prior_records.get(record.record_id)
        if previous is not None and previous.get("status") == "available" and (output_root / str(previous.get("chart_path", ""))).is_file():
            manifest_records.append(previous)
            continue
        try:
            evidence = evidence_by_ledger_id.get(record.record_id) if record.source == "covered_ledger" else None
            evidence_hash = evidence.get("frozen_ohlc_sha256", "") if isinstance(evidence, dict) else "live_checkpoint"
            cache_key = (record.source, record.symbol, record.timeframe_min, evidence_hash)
            cached = source_cache.get(cache_key)
            if cached is None:
                frame, provenance = _source_ohlcv(output_root, db_path, record, evidence)
                cached = (frame, provenance, [features(part) for part in _segments(frame, record.timeframe_min)])
                source_cache[cache_key] = cached
            frame, provenance, enriched_segments = cached
            chart = _chart(record, frame, provenance, enriched_segments=enriched_segments)
        except (ReviewDataError, ValueError) as exc:
            chart = {"id": record.record_id, "source": record.source, "symbol": record.symbol, "venue": "okx", "side": "long", "kind": "spike_burst_v1",
                     "timeframe": TIMEFRAMES[record.timeframe_min], "timeframe_min": record.timeframe_min, "signal_close_ms": record.signal_close_ms,
                     "candles": [], "future_context": FUTURE_CONTEXT, "state": {"status": "missing", "error": str(exc)}}
        path = charts / filename
        path.write_text(json.dumps(chart, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        manifest_records.append({"sequence": sequence, "id": record.record_id, "symbol": record.symbol, "timeframe": TIMEFRAMES[record.timeframe_min],
                                 "timeframe_min": record.timeframe_min, "signal_close_ms": record.signal_close_ms, "source": record.source,
                                 "chart_path": str(path.relative_to(output_root)), "status": chart["state"]["status"], "error": chart["state"]["error"]})
    manifest = {"schema_version": 1, "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "selection": {"window_bjt": "[2026-08-27 00:00, 2026-09-11 00:00)", "kind": "spike_burst_v1", "side": "long", "venue": "okx", "timeframes_min": [30, 60, 240], "live_specs": [list(item) for item in LIVE_SPECS]},
                "source_ledger": {"path": str(ledger.relative_to(ROOT)), "sha256": LEDGER_SHA256},
                "counts": {"total": len(manifest_records), "by_timeframe_min": {str(key): value for key, value in sorted(Counter(row["timeframe_min"] for row in manifest_records).items())},
                           "available": sum(row["status"] == "available" for row in manifest_records), "missing": sum(row["status"] == "missing" for row in manifest_records)},
                "records": manifest_records}
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--monitor-db", type=Path, default=MONITOR_DB)
    parser.add_argument("--only-missing", action="store_true", help="keep existing available charts and rebuild only missing records")
    arguments = parser.parse_args()
    manifest = build(arguments.output, arguments.ledger, arguments.monitor_db, only_missing=arguments.only_missing)
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
