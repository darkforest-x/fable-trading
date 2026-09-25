#!/usr/bin/env python3
"""Standalone byte-preserving extractor for frozen 1m prefix-recovery requests."""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, os, re, shutil, tempfile
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PREFIX_ROWS = 1211
POST_ROWS = 45  # Review-only: five training post-bars + forty future bars.

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
                # Avoid parsing every OHLC field across millions of archived
                # rows. Quoted or non-first time fields retain the CSV parser.
                first_field = raw_line.split(b",", 1)[0].strip() if stamp_column == 0 else b""
                if first_field.isdigit():
                    raw_time = first_field.decode("ascii")
                else:
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

def archive_job(item):
    key, events = item
    return process_archive(key[0], events)

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
        jobs = sorted(groups.items())
        with ProcessPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            for group_index, ((key, _), completed) in enumerate(zip(jobs, pool.map(archive_job, jobs)), start=1):
                result, row_count, csv_sha, first, last = completed
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
