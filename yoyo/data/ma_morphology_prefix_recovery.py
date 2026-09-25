"""Freeze and recover tiny timestamp-bound OHLC prefixes from archived 1m gzip files.

The frozen request is small enough to copy to the 3060.  Its standalone remote
script verifies both the gzip object and the entire decompressed CSV before it
publishes exact raw-line slices; source indices in the v6 ledger are retained as
provenance only because they are local to the previously imported source range.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "datasets/ma_launch_owner1500_morph_v6/dataset_ledger.jsonl"
DEFAULT_GZIP_MANIFEST_DIR = ROOT / "experiments/active/exp-ma-profit3r-20260922-v1/imports_1m"
DEFAULT_OUTPUT_PREFIX = "data/ma_morphology_prefix_recovery"
PREFIX_ROWS = 1211  # 1,200 EMA warm-up + 11 visible bars before core.
POST_ROWS = 5
RECOVERY_SCHEMA_VERSION = 1


class PrefixRecoveryError(ValueError):
    """Raised when source identity, event binding, or slice geometry drifts."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(payload)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise PrefixRecoveryError(f"cannot read JSONL {path}: {exc}") from exc


def _digest(value: Any, *, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise PrefixRecoveryError(f"{label} is not a SHA-256 digest")
    return text


def _load_gzip_sources(paths: Sequence[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    manifest_hashes: dict[str, str] = {}
    for path in sorted(paths):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PrefixRecoveryError(f"cannot read gzip source manifest {path}: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("sources"), list):
            raise PrefixRecoveryError(f"gzip source manifest has no sources list: {path}")
        manifest_hashes[path.as_posix()] = _sha256_file(path)
        for source in document["sources"]:
            csv_sha = _digest(source.get("csv_sha256"), label=f"{path}: csv_sha256")
            gzip_sha = _digest(source.get("sha256"), label=f"{path}: sha256")
            symbol = str(source.get("symbol") or "")
            upstream = str(source.get("upstream_gzip_path") or "")
            if not symbol or not upstream:
                raise PrefixRecoveryError(f"incomplete gzip identity in {path}")
            item = {
                "batch_id": str(source.get("batch_id") or document.get("batch_id") or ""),
                "bar_minutes": int(source.get("bar_minutes", document.get("interval", "1m").removesuffix("m"))),
                "csv_sha256": csv_sha,
                "first_time": source.get("first_time"),
                "last_time": source.get("last_time"),
                "non_bar_gaps": source.get("non_bar_gaps"),
                "rows": source.get("rows"),
                "sha256": gzip_sha,
                "source_path": source.get("source_path"),
                "symbol": symbol,
                "upstream_gzip_path": upstream,
                "manifest_path": path.as_posix(),
                "manifest_sha256": manifest_hashes[path.as_posix()],
            }
            by_identity.setdefault((csv_sha, symbol), []).append(item)

    unique: dict[str, dict[str, Any]] = {}
    for (csv_sha, symbol), candidates in by_identity.items():
        # Multiple manifests may repeat a source, but only identical gzip identity is safe.
        identities = {(item["upstream_gzip_path"], item["sha256"]) for item in candidates}
        if len(identities) != 1:
            raise PrefixRecoveryError(f"ambiguous gzip archive for {symbol} CSV SHA {csv_sha}")
        unique[f"{csv_sha}:{symbol}"] = sorted(candidates, key=lambda item: item["manifest_path"])[0]
    return unique, manifest_hashes


def _is_missing_1m_import(row: Mapping[str, Any], repo_root: Path) -> bool:
    source = str(row.get("source_path") or "")
    if "/inputs/binance_1m_import/" not in f"/{source.strip('/')}" or int(row.get("bar_minutes") or 0) != 1:
        return False
    source_path = Path(source)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise PrefixRecoveryError(f"ledger source_path must be repo-relative: {source}")
    return not (repo_root / source_path).is_file()


def build_recovery_request(
    *,
    ledger_path: Path = DEFAULT_LEDGER,
    gzip_manifest_dir: Path = DEFAULT_GZIP_MANIFEST_DIR,
    repo_root: Path = ROOT,
    pilot_per_split: int = 2,
    pilot_event_ids: Iterable[str] = (),
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> dict[str, Any]:
    """Build a deterministic request for missing val/test sources plus pilot events.

    Pilot ordering is lexicographic by event id within each split.  Explicit ids
    can be passed when a caller has already frozen a different pilot ordering.
    Only missing, ledger-kept Binance 1m imports are eligible for this tool.
    """

    if pilot_per_split < 0:
        raise PrefixRecoveryError("pilot_per_split must be non-negative")
    output_path = Path(output_prefix)
    if output_path.is_absolute() or ".." in output_path.parts or not output_path.parts:
        raise PrefixRecoveryError("output_prefix must be a safe repo-relative path")
    output_prefix = output_path.as_posix()
    repo_root = repo_root.resolve()
    ledger_rows = [row for row in _read_jsonl(ledger_path) if row.get("dataset_kept") is True]
    by_event: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        event_id = str(row.get("event_id") or "")
        if not event_id:
            continue
        if event_id in by_event and by_event[event_id] != row:
            raise PrefixRecoveryError(f"duplicate kept ledger event differs: {event_id}")
        by_event[event_id] = row
    missing = [row for row in by_event.values() if _is_missing_1m_import(row, repo_root)]
    missing.sort(key=lambda row: (str(row.get("split")), str(row.get("event_id"))))

    required_eval_ids = {str(row["event_id"]) for row in missing if row.get("split") in {"val", "test"}}
    pilot_ids = set(str(value) for value in pilot_event_ids)
    pilot_rank: dict[str, int] = {}
    if pilot_per_split:
        for split in sorted({str(row.get("split")) for row in by_event.values()}):
            ordered = sorted((row for row in by_event.values() if str(row.get("split")) == split), key=lambda row: str(row["event_id"]))
            for rank, row in enumerate(ordered[:pilot_per_split], start=1):
                event_id = str(row["event_id"])
                pilot_ids.add(event_id)
                pilot_rank[event_id] = rank
    unknown_pilots = pilot_ids.difference(by_event)
    if unknown_pilots:
        raise PrefixRecoveryError(f"pilot event ids are absent from kept ledger: {sorted(unknown_pilots)}")

    selected_ids = required_eval_ids | pilot_ids
    missing_ids = {str(row["event_id"]) for row in missing}
    already_available_pilots = sorted(event_id for event_id in pilot_ids if event_id not in missing_ids)
    source_manifests = sorted(gzip_manifest_dir.glob("batch_*_gzip_sources.json"))
    if not source_manifests:
        raise PrefixRecoveryError(f"no batch_*_gzip_sources.json files under {gzip_manifest_dir}")
    gzip_sources, manifest_hashes = _load_gzip_sources(source_manifests)

    events: dict[str, dict[str, Any]] = {}
    for event_id in sorted(selected_ids):
        row = by_event[event_id]
        if not _is_missing_1m_import(row, repo_root):
            # A pilot whose path is present needs no recovery; eval selections are missing by construction.
            continue
        if row.get("split") not in {"train", "val", "test"}:
            raise PrefixRecoveryError(f"unsupported split for {event_id}: {row.get('split')}")
        source_sha = _digest(row.get("source_sha256"), label=f"{event_id}.source_sha256")
        symbol = str(row.get("symbol") or "")
        archive = gzip_sources.get(f"{source_sha}:{symbol}")
        if archive is None:
            raise PrefixRecoveryError(f"no gzip source manifest matches {event_id} / {symbol} / {source_sha}")
        if archive["bar_minutes"] != 1 or archive["csv_sha256"] != source_sha:
            raise PrefixRecoveryError(f"gzip source manifest identity mismatch for {event_id}")
        try:
            local_start = int(row["source_core_start_i"])
            local_end = int(row["source_core_end_i"])
            core_bars = int(row.get("core_bars", local_end - local_start + 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise PrefixRecoveryError(f"missing core index geometry for {event_id}") from exc
        if local_end - local_start + 1 != core_bars or core_bars not in {4, 5}:
            raise PrefixRecoveryError(f"invalid core geometry for {event_id}")
        core_start_time = str(row.get("core_start_time") or "")
        core_end_time = str(row.get("core_end_time") or "")
        if not core_start_time or not core_end_time:
            raise PrefixRecoveryError(f"missing core timestamps for {event_id}")
        reasons = []
        if event_id in required_eval_ids:
            reasons.append("missing_eval_source")
        if event_id in pilot_ids:
            reasons.append("pilot_sorted_per_split")
        events[event_id] = {
            "bar_minutes": 1,
            "core_bars": core_bars,
            "core_end_time": core_end_time,
            "core_start_time": core_start_time,
            "event_id": event_id,
            "output_path": f"{output_prefix}/{row['split']}/{event_id}.csv",
            "pilot_rank_in_split": pilot_rank.get(event_id),
            "recovery_reason": reasons,
            "source_core_end_i": local_end,
            "source_core_start_i": local_start,
            "source_path": str(row["source_path"]),
            "source_sha256": source_sha,
            "split": str(row["split"]),
            "upstream_gzip_path": archive["upstream_gzip_path"],
            "upstream_gzip_sha256": archive["sha256"],
            "upstream_csv_sha256": archive["csv_sha256"],
            "source_manifest_path": archive["manifest_path"],
            "source_manifest_sha256": archive["manifest_sha256"],
            "source_manifest_rows": archive["rows"],
            "source_first_time": archive["first_time"],
            "source_last_time": archive["last_time"],
        }

    body: dict[str, Any] = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "ledger_path": ledger_path.resolve().relative_to(repo_root).as_posix(),
        "ledger_sha256": _sha256_file(ledger_path),
        "gzip_source_manifests": manifest_hashes,
        "selection": {
            "required_eval_event_ids": sorted(required_eval_ids),
            "pilot_event_ids": sorted(pilot_ids),
            "pilot_per_split": pilot_per_split,
            "pilot_order": "lexicographic_event_id_within_split",
            "pilot_already_available_event_ids": already_available_pilots,
        },
        "geometry": {"prefix_rows": PREFIX_ROWS, "post_rows": POST_ROWS, "row_index_binding": "core timestamps, not ledger local indices"},
        "output_prefix": output_prefix.strip("/\\"),
        "events": events,
    }
    body["request_sha256"] = _canonical_sha256(body)
    return body


def write_recovery_request(request: Mapping[str, Any], path: Path) -> None:
    """Atomically freeze a deterministic request JSON for remote execution."""

    value = dict(request)
    expected = value.pop("request_sha256", None)
    actual = _canonical_sha256(value)
    if expected != actual:
        raise PrefixRecoveryError("request SHA does not match frozen content")
    value["request_sha256"] = expected
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def render_remote_script() -> str:
    """Return a standalone standard-library script for the 3060 Windows host."""

    return _REMOTE_SCRIPT


def write_remote_script(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_remote_script())


def _cli_freeze(args: argparse.Namespace) -> int:
    request = build_recovery_request(
        ledger_path=Path(args.ledger),
        gzip_manifest_dir=Path(args.gzip_manifest_dir),
        repo_root=Path(args.repo_root),
        pilot_per_split=args.pilot_per_split,
        pilot_event_ids=args.pilot_event_id,
        output_prefix=args.output_prefix,
    )
    write_recovery_request(request, Path(args.out))
    print(json.dumps({"request_path": str(Path(args.out)), "request_sha256": request["request_sha256"],
                      "events": len(request["events"]),
                      "by_split": {split: sum(event["split"] == split for event in request["events"].values()) for split in ("train", "val", "test")},
                      "required_eval_events": len(request["selection"]["required_eval_event_ids"])}, ensure_ascii=False))
    return 0


def _cli_script(args: argparse.Namespace) -> int:
    write_remote_script(Path(args.out))
    print(str(Path(args.out)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="freeze a small recovery request from the v6 ledger")
    freeze.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    freeze.add_argument("--gzip-manifest-dir", default=str(DEFAULT_GZIP_MANIFEST_DIR))
    freeze.add_argument("--repo-root", default=str(ROOT))
    freeze.add_argument("--out", required=True)
    freeze.add_argument("--pilot-per-split", type=int, default=2)
    freeze.add_argument("--pilot-event-id", action="append", default=[])
    freeze.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    freeze.set_defaults(func=_cli_freeze)
    script = sub.add_parser("write-remote-script", help="write the standalone 3060 extraction script")
    script.add_argument("--out", required=True)
    script.set_defaults(func=_cli_script)
    args = parser.parse_args(argv)
    return args.func(args)


_REMOTE_SCRIPT = r'''#!/usr/bin/env python3
"""Standalone byte-preserving extractor for frozen 1m prefix-recovery requests."""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, os, re, shutil, tempfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

PREFIX_ROWS = 1211
POST_ROWS = 5

class RecoveryError(RuntimeError):
    pass

def canonical_sha(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def iso_to_ms(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise RecoveryError("event timestamp has no timezone: " + str(value))
    stamp = stamp.astimezone(timezone.utc)
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    if delta.microseconds % 1000:
        raise RecoveryError("timestamp is not millisecond aligned: " + str(value))
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000

def token_to_ms(value):
    text = value.strip()
    if not text:
        raise RecoveryError("empty timestamp token")
    try:
        number = int(text)
    except ValueError:
        return iso_to_ms(text)
    magnitude = abs(number)
    if magnitude >= 100000000000000000:  # nanoseconds
        scale = 1000000
    elif magnitude >= 100000000000000:  # microseconds
        scale = 1000
    elif magnitude >= 100000000000:  # milliseconds
        scale = 1
    else:  # seconds
        scale = -1000
    if scale < 0:
        return number * 1000
    if number % scale:
        raise RecoveryError("timestamp is not exactly representable in milliseconds: " + text)
    return number // scale

def parse_fields(raw_line):
    try:
        text = raw_line.decode("utf-8-sig")
        rows = list(csv.reader([text]))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RecoveryError("invalid UTF-8/CSV line: " + str(exc)) from exc
    if len(rows) != 1:
        raise RecoveryError("expected exactly one CSV record per raw line")
    return rows[0]

def time_column(header):
    normalized = [str(value).strip().lstrip("\ufeff").lower().replace(" ", "_") for value in header]
    for name in ("open_time", "open_time_ms", "timestamp", "time", "ts"):
        if name in normalized:
            return normalized.index(name)
    raise RecoveryError("CSV header lacks a recognized open-time column")

def safe_relative(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RecoveryError("output_path must be safe and repo-relative: " + value)
    return path

def process_archive(archive_path, events):
    archive = Path(archive_path)
    if not archive.is_file():
        raise RecoveryError("upstream gzip is missing: " + str(archive))
    expected_gzip = events[0]["upstream_gzip_sha256"]
    expected_csv = events[0]["upstream_csv_sha256"]
    if any(e["upstream_gzip_sha256"] != expected_gzip or e["upstream_csv_sha256"] != expected_csv for e in events):
        raise RecoveryError("events disagree on source archive identity")
    if sha_file(archive) != expected_gzip:
        raise RecoveryError("upstream gzip SHA-256 mismatch: " + str(archive))

    wanted = defaultdict(list)
    for event in events:
        if event["source_sha256"] != expected_csv:
            raise RecoveryError("ledger source SHA differs from gzip manifest for " + event["event_id"])
        bars = int(event["core_bars"])
        if bars not in (4, 5) or int(event["bar_minutes"]) != 1:
            raise RecoveryError("unsupported event geometry for " + event["event_id"])
        if int(event["source_core_end_i"]) - int(event["source_core_start_i"]) + 1 != bars:
            raise RecoveryError("ledger-local core index span disagrees with core_bars for " + event["event_id"])
        start_ms = iso_to_ms(event["core_start_time"])
        end_ms = iso_to_ms(event["core_end_time"])
        if end_ms - start_ms != (bars - 1) * 60000:
            raise RecoveryError("core timestamps disagree with 1m core length for " + event["event_id"])
        wanted[start_ms].append({"event": event, "start_ms": start_ms, "end_ms": end_ms, "core_bars": bars})

    csv_digest = hashlib.sha256()
    data_rows = 0
    header_line = None
    stamp_column = None
    first_token = last_token = None
    ring = deque(maxlen=PREFIX_ROWS)
    seen_start = defaultdict(int)
    states = {}
    active = set()
    results = {}
    try:
        with gzip.open(archive, "rb") as handle:
            for raw_line in handle:
                csv_digest.update(raw_line)
                if header_line is None:
                    header_line = raw_line
                    fields = parse_fields(raw_line)
                    stamp_column = time_column(fields)
                    continue
                fields = parse_fields(raw_line)
                if stamp_column >= len(fields):
                    raise RecoveryError("timestamp column missing from row " + str(data_rows))
                raw_time = fields[stamp_column].strip()
                stamp_ms = token_to_ms(raw_time)
                if data_rows == 0:
                    first_token = raw_time
                last_token = raw_time

                # Continue slices started on earlier rows. Each output keeps the
                # exact bytes, line endings, and millisecond timestamp spelling.
                for event_id in tuple(active):
                    state = states[event_id]
                    if data_rows > state["start_index"]:
                        state["payload"].extend(raw_line)
                        state["row_count"] += 1
                    if data_rows == state["core_end_index"] and stamp_ms != state["core_end_ms"]:
                        raise RecoveryError("timestamp-bound core end mismatch for " + event_id)
                    if data_rows == state["slice_end_index"]:
                        event = state["event"]
                        expected_rows = PREFIX_ROWS + int(event["core_bars"]) + POST_ROWS
                        if state["row_count"] != expected_rows:
                            raise RecoveryError("slice row count mismatch for " + event_id)
                        results[event_id] = {**state, "last_open_time_raw": raw_time}
                        active.remove(event_id)

                starts_here = wanted.get(stamp_ms, [])
                if starts_here:
                    seen_start[stamp_ms] += 1
                    if seen_start[stamp_ms] > 1:
                        raise RecoveryError("duplicate core-start timestamp in source: " + starts_here[0]["event"]["event_id"])
                for binding in starts_here:
                    event = binding["event"]
                    event_id = event["event_id"]
                    if len(ring) != PREFIX_ROWS:
                        raise RecoveryError("source lacks 1211 rows before core: " + event_id)
                    start_ms = binding["start_ms"]
                    end_ms = binding["end_ms"]
                    core_end_index = data_rows + binding["core_bars"] - 1
                    state = {
                        "event": event,
                        "start_index": data_rows,
                        "core_end_index": core_end_index,
                        "core_end_ms": end_ms,
                        "slice_end_index": core_end_index + POST_ROWS,
                        "payload": bytearray(header_line + b"".join(record[0] for record in ring) + raw_line),
                        "row_count": PREFIX_ROWS + 1,
                        "first_open_time_raw": ring[0][1],
                        "core_start_open_time_raw": raw_time,
                        "core_start_open_time_ms": start_ms,
                    }
                    states[event_id] = state
                    active.add(event_id)
                ring.append((raw_line, raw_time, stamp_ms))
                data_rows += 1
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise RecoveryError("failed while streaming gzip/CSV source: " + str(exc)) from exc

    if header_line is None:
        raise RecoveryError("decompressed source has no CSV header")
    if csv_digest.hexdigest() != expected_csv:
        raise RecoveryError("full decompressed CSV SHA-256 mismatch: " + str(archive))
    declared_rows = events[0].get("source_manifest_rows")
    if declared_rows is not None and int(declared_rows) != data_rows:
        raise RecoveryError("CSV data row count differs from source manifest")
    declared_first = events[0].get("source_first_time")
    declared_last = events[0].get("source_last_time")
    if declared_first and token_to_ms(str(declared_first)) != token_to_ms(str(first_token)):
        raise RecoveryError("CSV first timestamp differs from source manifest")
    if declared_last and token_to_ms(str(declared_last)) != token_to_ms(str(last_token)):
        raise RecoveryError("CSV last timestamp differs from source manifest")
    missing = sorted(set(event["event_id"] for event in events) - set(results))
    if missing:
        raise RecoveryError("timestamp-bound slice did not complete: " + ",".join(missing[:8]))
    for event in events:
        start_ms = iso_to_ms(event["core_start_time"])
        end_ms = iso_to_ms(event["core_end_time"])
        if seen_start[start_ms] != 1 or results[event["event_id"]]["core_start_open_time_ms"] != start_ms:
            raise RecoveryError("core-start timestamp is not unique and bound: " + event["event_id"])
        if results[event["event_id"]]["core_end_ms"] != end_ms:
            raise RecoveryError("core-end timestamp binding failed: " + event["event_id"])
        expected_rows = PREFIX_ROWS + int(event["core_bars"]) + POST_ROWS
        if results[event["event_id"]]["row_count"] != expected_rows:
            raise RecoveryError("wrong exact slice length: " + event["event_id"])
    return results, data_rows, csv_digest.hexdigest(), first_token, last_token

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

def run(request_path, repo_root):
    request_path = Path(request_path)
    repo_root = Path(repo_root).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    claimed_request_sha = request.get("request_sha256")
    unhashed = dict(request)
    unhashed.pop("request_sha256", None)
    if canonical_sha(unhashed) != claimed_request_sha:
        raise RecoveryError("frozen request SHA-256 mismatch")
    if request.get("schema_version") != 1:
        raise RecoveryError("unsupported request schema")
    events = request.get("events")
    if not isinstance(events, dict) or not events:
        raise RecoveryError("frozen request has no events")
    output_prefix = safe_relative(request.get("output_prefix", ""))
    for event_id, event in events.items():
        if event.get("event_id") != event_id:
            raise RecoveryError("event key/id mismatch: " + event_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", event_id):
            raise RecoveryError("unsafe event id: " + event_id)
        relative = safe_relative(event["output_path"])
        if relative.parts[:len(output_prefix.parts)] != output_prefix.parts:
            raise RecoveryError("event output_path is outside frozen output_prefix: " + event_id)

    groups = defaultdict(list)
    for event in events.values():
        key = (event["upstream_gzip_path"], event["upstream_gzip_sha256"], event["upstream_csv_sha256"])
        groups[key].append(event)
    tmp = Path(tempfile.mkdtemp(prefix=".ma-prefix-recovery-", dir=str(repo_root)))
    archive_results = {}
    event_states = {}
    try:
        for group_index, (key, group_events) in enumerate(sorted(groups.items()), start=1):
            result, row_count, csv_sha, first, last = process_archive(key[0], group_events)
            archive_results[key[0]] = {"gzip_sha256": key[1], "csv_sha256": csv_sha, "rows": row_count,
                                       "first_time": first, "last_time": last}
            for event_id, state in result.items():
                staged = tmp / ("event_" + str(len(archive_results)) + "_" + event_id + ".csv")
                staged.write_bytes(bytes(state["payload"]))
                state["staged_path"] = staged
                event_states[event_id] = state
                state.pop("payload", None)
            print("archive %d/%d verified; events=%d csv_sha256=%s" %
                  (group_index, len(groups), len(result), csv_sha), flush=True)

        manifest_events = {}
        receipt_events = {}
        # Validate all staged bytes and any existing immutable destinations before publishing.
        for event_id, event in events.items():
            state = event_states.get(event_id)
            if state is None:
                raise RecoveryError("staged slice is missing or ambiguous: " + event_id)
            staged = state["staged_path"]
            payload_sha = sha_file(staged)
            relative = safe_relative(event["output_path"])
            destination = repo_root / relative
            if destination.exists() and sha_file(destination) != payload_sha:
                raise RecoveryError("refusing to replace a different existing slice: " + str(destination))
            manifest_events[event_id] = {
                "path": relative.as_posix(),
                "sha256": payload_sha,
                "source_path": event["source_path"],
                "source_sha256": event["source_sha256"],
                "upstream_gzip_sha256": event["upstream_gzip_sha256"],
                "upstream_gzip_path": event["upstream_gzip_path"],
                "core_start_time": event["core_start_time"],
                "core_end_time": event["core_end_time"],
                "source_core_start_i_local": event["source_core_start_i"],
                "source_core_end_i_local": event["source_core_end_i"],
                "resolved_source_core_start_i": state["start_index"],
                "resolved_source_core_end_i": state["core_end_index"],
                "slice_start_i": state["start_index"] - PREFIX_ROWS,
                "slice_end_i": state["slice_end_index"],
                "slice_data_rows": state["row_count"],
                "prefix_rows": PREFIX_ROWS,
                "post_rows": POST_ROWS,
                "first_open_time_raw": state["first_open_time_raw"],
                "core_start_open_time_raw": state["core_start_open_time_raw"],
                "last_open_time_raw": state["last_open_time_raw"],
                "full_csv_sha256_verified": event["source_sha256"],
                "gzip_sha256_verified": event["upstream_gzip_sha256"],
            }
            receipt_events[event_id] = {"output_path": relative.as_posix(), "slice_sha256": payload_sha,
                                        "slice_data_rows": state["row_count"], "split": event["split"],
                                        "resolved_start_i": state["start_index"], "resolved_end_i": state["core_end_index"]}

        for event_id, event in events.items():
            relative = safe_relative(event["output_path"])
            destination = repo_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = event_states[event_id]["staged_path"]
            if not destination.exists():
                os.replace(staged, destination)
        manifest = {"schema_version": 1, "request_sha256": claimed_request_sha,
                    "events": manifest_events, "sources": archive_results}
        receipt = {"status": "passed", "request_sha256": claimed_request_sha,
                   "event_count": len(manifest_events), "archive_count": len(archive_results),
                   "events": receipt_events, "sources": archive_results}
        receipt_dir = repo_root / output_prefix
        atomic_json(receipt_dir / "recovery_manifest.json", manifest)
        atomic_json(receipt_dir / "recovery_receipt.json", receipt)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="frozen recovery_request.json copied to this host")
    parser.add_argument("--repo-root", default=".", help="repository root used for repo-relative output paths")
    args = parser.parse_args()
    run(args.request, args.repo_root)
    print("recovery passed")

if __name__ == "__main__":
    main()
'''


if __name__ == "__main__":  # pragma: no cover - covered by CLI integration tests.
    raise SystemExit(main())
