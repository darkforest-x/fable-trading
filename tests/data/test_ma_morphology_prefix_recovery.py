from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yoyo.data.ma_morphology_prefix_recovery import (
    PrefixRecoveryError,
    build_recovery_request,
    render_remote_script,
    write_recovery_request,
    write_remote_script,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _synthetic_source(row_count: int = 1240) -> tuple[bytes, int, int]:
    start_ms = 1_750_000_000_000
    rows = [b"open_time,open,high,low,close,volume\r\n"]
    for index in range(row_count):
        stamp = start_ms + index * 60_000
        rows.append(f"{stamp},1,2,0.5,1.5,3\r\n".encode("ascii"))
    return b"".join(rows), start_ms, row_count


def _event_request(csv_bytes: bytes, gzip_bytes: bytes, gzip_path: Path, *, mismatch: str | None = None) -> dict:
    _, first_ms, source_rows = _synthetic_source()
    core_start_i = 1211
    core_bars = 4
    source_sha = _sha(csv_bytes)
    gzip_sha = _sha(gzip_bytes)
    if mismatch == "gzip":
        gzip_sha = "0" * 64
    if mismatch == "csv":
        source_sha = "f" * 64
    event_id = "cluster_test_event"
    body = {
        "schema_version": 1,
        "geometry": {"prefix_rows": 1211, "post_rows": 5},
        "output_prefix": "data/crypto/research/ma_prefix_test",
        "events": {
            event_id: {
                "event_id": event_id,
                "split": "val",
                "bar_minutes": 1,
                "core_bars": core_bars,
                # Deliberately local, not the absolute index in the full CSV.
                "source_core_start_i": 11,
                "source_core_end_i": 14,
                "core_start_time": _iso_ms(first_ms + core_start_i * 60_000),
                "core_end_time": _iso_ms(first_ms + (core_start_i + core_bars - 1) * 60_000),
                "source_path": "experiments/active/source.csv",
                "source_sha256": source_sha,
                "upstream_csv_sha256": source_sha,
                "upstream_gzip_path": str(gzip_path),
                "upstream_gzip_sha256": gzip_sha,
                "source_manifest_rows": source_rows,
                "source_first_time": _iso_ms(first_ms),
                "source_last_time": _iso_ms(first_ms + (source_rows - 1) * 60_000),
                "output_path": f"data/crypto/research/ma_prefix_test/val/{event_id}.csv",
            }
        },
    }
    body["request_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


def _run_remote(tmp_path: Path, request: dict) -> subprocess.CompletedProcess[str]:
    script_path = tmp_path / "recover.py"
    request_path = tmp_path / "request.json"
    write_remote_script(script_path)
    write_recovery_request(request, request_path)
    return subprocess.run(
        [sys.executable, str(script_path), "--request", str(request_path), "--repo-root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_remote_slice_keeps_raw_header_millisecond_timestamps_and_exact_window(tmp_path: Path) -> None:
    csv_bytes, first_ms, source_rows = _synthetic_source()
    gzip_path = tmp_path / "source.csv.gz"
    gzip_bytes = gzip.compress(csv_bytes, mtime=0)
    gzip_path.write_bytes(gzip_bytes)
    request = _event_request(csv_bytes, gzip_bytes, gzip_path)

    result = _run_remote(tmp_path, request)

    assert result.returncode == 0, result.stderr
    output_path = tmp_path / request["events"]["cluster_test_event"]["output_path"]
    recovered = output_path.read_bytes()
    lines = recovered.splitlines(keepends=True)
    assert lines[0] == b"open_time,open,high,low,close,volume\r\n"
    # 1,211 prefix + 4 core + 5 post rows, plus the unchanged header.
    assert len(lines) == 1 + 1211 + 4 + 5
    assert lines[1].split(b",", 1)[0] == str(first_ms).encode("ascii")
    assert lines[-1].split(b",", 1)[0] == str(first_ms + 1219 * 60_000).encode("ascii")
    manifest = json.loads((tmp_path / request["output_prefix"] / "recovery_manifest.json").read_text())
    entry = manifest["events"]["cluster_test_event"]
    assert entry["sha256"] == _sha(recovered)
    assert entry["source_sha256"] == _sha(csv_bytes)
    assert entry["resolved_source_core_start_i"] == 1211
    assert entry["resolved_source_core_end_i"] == 1214
    assert entry["slice_data_rows"] == 1220
    assert entry["first_open_time_raw"] == str(first_ms)
    assert entry["last_open_time_raw"] == str(first_ms + 1219 * 60_000)
    assert source_rows == 1240
    assert (tmp_path / request["output_prefix"] / "recovery_receipt.json").is_file()
    assert not (tmp_path / "recovery_manifest.json").exists()


@pytest.mark.parametrize("mismatch, message", [("gzip", "gzip SHA-256 mismatch"), ("csv", "CSV SHA-256 mismatch")])
def test_remote_fails_closed_on_full_source_hash_mismatch(tmp_path: Path, mismatch: str, message: str) -> None:
    csv_bytes, _, _ = _synthetic_source()
    gzip_path = tmp_path / "source.csv.gz"
    gzip_bytes = gzip.compress(csv_bytes, mtime=0)
    gzip_path.write_bytes(gzip_bytes)
    request = _event_request(csv_bytes, gzip_bytes, gzip_path, mismatch=mismatch)

    result = _run_remote(tmp_path, request)

    assert result.returncode != 0
    assert message in result.stderr
    output_prefix = tmp_path / request["output_prefix"]
    assert not (output_prefix / "recovery_manifest.json").exists()
    assert not (output_prefix / "recovery_receipt.json").exists()
    assert not (output_prefix / "val/cluster_test_event.csv").exists()


def test_freeze_selects_all_missing_eval_sources_and_top_two_pilots_from_all_ledger_events(tmp_path: Path) -> None:
    repo = tmp_path
    ledger_path = repo / "datasets/ledger.jsonl"
    ledger_path.parent.mkdir(parents=True)
    source_dir = repo / "imports"
    source_dir.mkdir()
    manifest_sources = []
    rows = [
        ("cluster_a", "train", "AAAUSDT", True),
        ("cluster_b", "train", "BBBUSDT", False),
        ("cluster_c", "train", "CCCUSDT", False),
        ("cluster_d", "val", "DDDUSDT", False),
        ("cluster_e", "val", "EEEUSDT", False),
        ("cluster_f", "val", "FFFUSDT", True),
        ("cluster_g", "test", "GGGUSDT", False),
        ("cluster_h", "test", "HHHUSDT", True),
    ]
    ledger = []
    for index, (event_id, split, symbol, exists) in enumerate(rows):
        source_path = f"experiments/active/exp/inputs/binance_1m_import/batch_01/series/{symbol}.csv"
        if exists:
            target = repo / source_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("existing\n")
        csv_sha = f"{index + 1:064x}"
        manifest_sources.append({
            "bar_minutes": 1,
            "batch_id": "batch_01",
            "csv_sha256": csv_sha,
            "first_time": "2024-01-01T00:00:00+00:00",
            "last_time": "2025-01-01T00:00:00+00:00",
            "non_bar_gaps": 0,
            "rows": 100000,
            "sha256": f"{index + 101:064x}",
            "source_path": f"compressed/{symbol}.csv.gz",
            "symbol": symbol,
            "upstream_gzip_path": f"C:\\fable\\archive\\{symbol}.csv.gz",
        })
        ledger.append({
            "dataset_kept": True,
            "event_id": event_id,
            "split": split,
            "bar_minutes": 1,
            "symbol": symbol,
            "source_path": source_path,
            "source_sha256": csv_sha,
            "source_core_start_i": 11,
            "source_core_end_i": 14,
            "core_bars": 4,
            "core_start_time": "2024-06-01T00:00:00+00:00",
            "core_end_time": "2024-06-01T00:03:00+00:00",
        })
    ledger_path.write_text("".join(json.dumps(row) + "\n" for row in ledger), encoding="utf-8")
    (source_dir / "batch_01_gzip_sources.json").write_text(
        json.dumps({"batch_id": "batch_01", "interval": "1m", "sources": manifest_sources}), encoding="utf-8"
    )

    request = build_recovery_request(
        ledger_path=ledger_path,
        gzip_manifest_dir=source_dir,
        repo_root=repo,
        pilot_per_split=2,
        output_prefix="data/crypto/research/test_prefixes",
    )

    assert set(request["selection"]["required_eval_event_ids"]) == {"cluster_d", "cluster_e", "cluster_g"}
    assert set(request["selection"]["pilot_event_ids"]) == {"cluster_a", "cluster_b", "cluster_d", "cluster_e", "cluster_g", "cluster_h"}
    assert request["selection"]["pilot_already_available_event_ids"] == ["cluster_a", "cluster_h"]
    assert set(request["events"]) == {"cluster_b", "cluster_d", "cluster_e", "cluster_g"}
    assert request["events"]["cluster_b"]["output_path"] == "data/crypto/research/test_prefixes/train/cluster_b.csv"
    assert request["request_sha256"] == hashlib.sha256(
        json.dumps({k: v for k, v in request.items() if k != "request_sha256"}, ensure_ascii=False,
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_frozen_request_rejects_modified_payload(tmp_path: Path) -> None:
    request = {"schema_version": 1, "events": {}, "request_sha256": "0" * 64}
    with pytest.raises(PrefixRecoveryError, match="request SHA"):
        write_recovery_request(request, tmp_path / "request.json")


def test_remote_script_is_standalone_python() -> None:
    compile(render_remote_script(), "<remote recovery script>", "exec")


def test_parallel_archives_preserve_raw_bytes_with_quoted_time_fallback(tmp_path: Path) -> None:
    plain, _, _ = _synthetic_source()
    lines = plain.splitlines(keepends=True)
    quoted = lines[0] + b"".join(b'"' + line.split(b",", 1)[0] + b'",' + line.split(b",", 1)[1]
                                  for line in lines[1:])
    merged = None
    expected = {}
    for index, payload in enumerate((plain, quoted)):
        archive = tmp_path / f"source_{index}.csv.gz"
        compressed = gzip.compress(payload, mtime=0)
        archive.write_bytes(compressed)
        part = _event_request(payload, compressed, archive)
        event = part['events'].pop('cluster_test_event')
        event_id = f'cluster_parallel_{index}'
        event['event_id'] = event_id
        event['output_path'] = f"{part['output_prefix']}/val/{event_id}.csv"
        part['events'][event_id] = event
        if merged is None:
            merged = part
        else:
            merged['events'].update(part['events'])
        expected[event['output_path']] = b''.join(payload.splitlines(keepends=True)[:1221])
    merged.pop('request_sha256')
    merged['request_sha256'] = hashlib.sha256(
        json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    result = _run_remote(tmp_path, merged)
    assert result.returncode == 0, result.stderr
    for path, payload in expected.items():
        assert (tmp_path / path).read_bytes() == payload
    receipt = json.loads((tmp_path / merged['output_prefix'] / 'recovery_receipt.json').read_text())
    assert receipt['archive_count'] == receipt['event_count'] == 2
