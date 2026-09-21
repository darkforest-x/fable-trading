"""No-network regression coverage for the transparent 1m archive importer."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

import pytest

import yoyo.data.ma_profit_archive_import as archive


CSV = b"open_time,open,high,low,close,volume\n2024-01-01T00:00:00Z,1,2,1,2,3\n2024-01-01T00:01:00Z,2,3,2,3,4\n"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, Path]:
    monkeypatch.setattr(archive, "ROOT", tmp_path)
    gzip_root = tmp_path / "gzips"
    source = gzip_root / "nested" / "binance_um_SYNUSDT_1m_2.csv.gz"
    source.parent.mkdir(parents=True)
    with gzip.open(source, "wb") as handle:
        handle.write(CSV)
    manifest = tmp_path / "sources_archive_1m.json"
    manifest.write_text(json.dumps({"schema_version": 1, "interval": "1m", "source_coverage_complete": False, "sources": [{
        "source_path": "nested\\binance_um_SYNUSDT_1m_2.csv.gz", "sha256": _sha(source.read_bytes()), "csv_sha256": _sha(CSV),
        "rows": 2, "first_time": "2024-01-01T00:00:00+00:00", "last_time": "2024-01-01T00:01:00+00:00", "non_bar_gaps": 0,
        "bar_minutes": 1, "symbol": "SYN_USDT_SWAP", "venue": "binance_um",
    }]}), encoding="utf-8")
    out, receipt = tmp_path / "inputs" / "binance_1m_import", tmp_path / "receipt.json"
    return manifest, gzip_root, out, receipt


def _mock_transparent(monkeypatch: pytest.MonkeyPatch) -> None:
    def copy(source: Path, destination: Path) -> None:
        shutil.copyfile(source, destination)
    monkeypatch.setattr(archive, "_run_ditto", copy)
    monkeypatch.setattr(archive, "_physical_bytes", lambda path: max(1, path.stat().st_size // 2))
    monkeypatch.setattr(archive, "_require_space", lambda *args, **kwargs: 123)


def test_imports_logically_identical_csv_with_windows_path_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    _mock_transparent(monkeypatch)
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert receipt["gate_open"] and receipt["sources_complete"] == 1
    source_manifest = json.loads((out / "sources_imported_1m.json").read_text())
    row = source_manifest["sources"][0]
    csv_path = tmp_path / row["source_path"]
    assert csv_path.read_bytes() == CSV and _sha(csv_path.read_bytes()) == row["sha256"]
    assert (gzip_root / "nested" / "binance_um_SYNUSDT_1m_2.csv.gz").exists()
    assert not list((out / "temporary").glob("*.csv"))
    second = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert second["gate_open"] and second["audits"][0]["status"] == "complete"


def test_bad_decompressed_sha_closes_gate_and_retains_original_gzip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    _mock_transparent(monkeypatch)
    payload = json.loads(manifest.read_text())
    payload["sources"][0]["csv_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert not receipt["gate_open"] and receipt["audits"][0]["status"] == "failed"
    assert (gzip_root / "nested" / "binance_um_SYNUSDT_1m_2.csv.gz").exists()
    assert not (out / "series").exists()


def test_corrupt_gzip_is_a_recorded_failure_without_deleting_staging_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    _mock_transparent(monkeypatch)
    gzip_path = gzip_root / "nested" / "binance_um_SYNUSDT_1m_2.csv.gz"
    gzip_path.write_bytes(b"not a gzip")
    payload = json.loads(manifest.read_text())
    payload["sources"][0]["sha256"] = _sha(gzip_path.read_bytes())
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert not receipt["gate_open"] and receipt["audits"][0]["status"] == "failed"
    assert gzip_path.read_bytes() == b"not a gzip"


def test_no_physical_storage_gain_closes_gate_and_keeps_verified_temporary_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(archive, "_run_ditto", lambda source, destination: shutil.copyfile(source, destination))
    monkeypatch.setattr(archive, "_physical_bytes", lambda path: path.stat().st_size)
    monkeypatch.setattr(archive, "_require_space", lambda *args, **kwargs: 123)
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert not receipt["gate_open"] and receipt["audits"][0]["status"] == "storage_compression_unsupported"
    assert list((out / "temporary").glob("*.csv"))
    assert not list((out / "series").glob("*.csv"))


def test_path_escape_is_rejected_before_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    payload = json.loads(manifest.read_text())
    payload["sources"][0]["source_path"] = "..\\outside.csv.gz"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(archive.ArchiveImportError, match="escapes gzip root"):
        archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)


def test_existing_unbound_csv_is_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    _mock_transparent(monkeypatch)
    destination = out / "series" / "binance_um_SYNUSDT_1m_2.csv"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(CSV)
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert not receipt["gate_open"] and destination.read_bytes() == CSV
    assert receipt["audits"][0]["status"] == "failed"


def test_bound_interrupted_post_replace_csv_is_adopted_without_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    _mock_transparent(monkeypatch)
    payload = json.loads(manifest.read_text())
    out.mkdir(parents=True)
    archive._write_json(out / "import_binding.json", archive._binding(manifest, payload))
    destination = out / "series" / "binance_um_SYNUSDT_1m_2.csv"
    destination.parent.mkdir()
    destination.write_bytes(CSV)
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert receipt["gate_open"] and receipt["audits"][0]["recovered_after_audit_interruption"] is True
    assert destination.read_bytes() == CSV


def test_completed_audit_rechecks_current_physical_compression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    _mock_transparent(monkeypatch)
    assert archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)["gate_open"]
    monkeypatch.setattr(archive, "_physical_bytes", lambda path: path.stat().st_size)
    receipt = archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
    assert not receipt["gate_open"] and receipt["audits"][0]["status"] == "storage_compression_unsupported"


def test_empty_manifest_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, gzip_root, out, receipt_path = _setup(tmp_path, monkeypatch)
    manifest.write_text(json.dumps({"schema_version": 1, "interval": "1m", "sources": []}), encoding="utf-8")
    with pytest.raises(archive.ArchiveImportError, match="must not be empty"):
        archive.import_archives(manifest=manifest, gzip_root=gzip_root, out=out, receipt_out=receipt_path)
