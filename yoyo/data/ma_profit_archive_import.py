"""Import frozen local Binance 1m gzip archives as APFS-transparent CSV files.

The source gzip files remain read-only staging inputs.  For every file this
tool verifies the compressed SHA, streams and hashes the decompressed CSV, then
uses macOS ``ditto --hfsCompression`` to create an ordinary CSV with identical
logical bytes.  The existing source readers therefore require no changes.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
MIN_FREE_BYTES = 10 * 1024**3
BYTES_PER_ROW_ESTIMATE = 512


class ArchiveImportError(RuntimeError):
    """Raised when frozen archive lineage or transparent storage is unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError as exc:
        raise ArchiveImportError(f"output must be under repository root: {path}") from exc


def _safe_relative_gzip(value: object) -> PurePosixPath:
    raw = str(value).replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ArchiveImportError(f"gzip source_path must be a relative path: {value!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveImportError(f"gzip source_path escapes gzip root: {value!r}")
    return path


def _gzip_path(root: Path, value: object) -> Path:
    relative = _safe_relative_gzip(value)
    path = (root / Path(*relative.parts)).resolve()
    if root not in path.parents:
        raise ArchiveImportError(f"gzip source_path escapes gzip root: {value!r}")
    return path


def _physical_bytes(path: Path) -> int:
    stat = path.stat()
    blocks = getattr(stat, "st_blocks", None)
    if blocks is None:
        return int(stat.st_size)
    return int(blocks) * 512


def _require_space(path: Path, *, rows: int, gzip_size: int) -> int:
    expected = max(1024 * 1024, int(rows) * BYTES_PER_ROW_ESTIMATE, int(gzip_size) * 4)
    required = MIN_FREE_BYTES + expected
    if shutil.disk_usage(path).free < required:
        raise ArchiveImportError(f"insufficient free space: need at least {required} bytes before one import")
    return expected


def _stream_decompress(source: Path, destination: Path, expected_csv_sha: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with gzip.open(source, "rb") as packed, destination.open("wb") as output:
            for block in iter(lambda: packed.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
                output.write(block)
    except (OSError, EOFError) as exc:
        destination.unlink(missing_ok=True)
        raise ArchiveImportError(f"invalid gzip stream: {source}") from exc
    actual = digest.hexdigest()
    if actual != expected_csv_sha:
        destination.unlink(missing_ok=True)
        raise ArchiveImportError(f"decompressed CSV SHA drift: {source}")
    return actual, size


def _run_ditto(source: Path, destination: Path) -> None:
    tool = Path("/usr/bin/ditto")
    if not tool.exists():
        raise ArchiveImportError("APFS transparent compression unavailable: /usr/bin/ditto is missing")
    result = subprocess.run([str(tool), "--hfsCompression", str(source), str(destination)], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ArchiveImportError(f"ditto transparent compression failed: {result.stderr.strip()}")


def _validate_manifest(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") != 1 or payload.get("interval") != "1m" or not isinstance(payload.get("sources"), list):
        raise ArchiveImportError("archive manifest must be schema 1, interval 1m, and contain sources")
    if not payload["sources"]:
        raise ArchiveImportError("archive manifest sources must not be empty")
    seen_paths: set[str] = set()
    seen_destinations: set[str] = set()
    result: list[dict[str, Any]] = []
    required = {"source_path", "sha256", "csv_sha256", "rows", "first_time", "last_time", "non_bar_gaps", "bar_minutes", "symbol", "venue"}
    for raw in payload["sources"]:
        if not isinstance(raw, Mapping) or not required.issubset(raw):
            raise ArchiveImportError("archive manifest source misses required fields")
        relative = _safe_relative_gzip(raw["source_path"])
        key = str(relative)
        basename = relative.name.removesuffix(".gz")
        if not basename.endswith(".csv") or key in seen_paths or basename in seen_destinations:
            raise ArchiveImportError("archive manifest has duplicate/invalid gzip or CSV identity")
        if int(raw["bar_minutes"]) != 1 or int(raw["rows"]) <= 0:
            raise ArchiveImportError("archive manifest source is not a positive-row 1m series")
        seen_paths.add(key)
        seen_destinations.add(basename)
        result.append({**dict(raw), "source_path": key, "csv_basename": basename})
    return result


def _binding(manifest: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "importer_sha256": sha256_file(Path(__file__)),
        "archive_manifest_path": str(manifest.resolve()),
        "archive_manifest_sha256": sha256_file(manifest),
        "archive_manifest_sources_sha256": hashlib.sha256(json.dumps(payload["sources"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def _audit_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("source_path", "sha256", "csv_sha256", "rows", "first_time", "last_time", "non_bar_gaps", "symbol", "venue", "bar_minutes")}


def _existing_complete(audit_path: Path, identity: Mapping[str, Any], destination: Path) -> dict[str, Any] | None:
    if not audit_path.exists():
        return None
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("identity") != dict(identity):
        raise ArchiveImportError(f"existing import audit binding differs: {audit_path}")
    if audit.get("status") != "complete":
        return None
    if not destination.exists() or sha256_file(destination) != identity["csv_sha256"]:
        raise ArchiveImportError(f"existing completed CSV failed SHA verification: {destination}")
    logical_bytes = destination.stat().st_size
    physical_bytes = _physical_bytes(destination)
    if not bool(audit.get("transparent_compression_supported")) or physical_bytes >= logical_bytes:
        return {
            **audit, "status": "storage_compression_unsupported", "logical_bytes": logical_bytes,
            "physical_bytes": physical_bytes, "transparent_compression_supported": False,
        }
    return {**audit, "logical_bytes": logical_bytes, "physical_bytes": physical_bytes}


def _import_one(row: Mapping[str, Any], *, gzip_root: Path, out: Path, binding_existed: bool) -> dict[str, Any]:
    identity = _audit_identity(row)
    gzip_path = _gzip_path(gzip_root, row["source_path"])
    destination = out / "series" / str(row["csv_basename"])
    audit_path = out / "audits" / f"{row['csv_basename']}.json"
    prior = _existing_complete(audit_path, identity, destination)
    if prior is not None:
        return prior
    if destination.exists():
        if binding_existed and not audit_path.exists() and sha256_file(destination) == row["csv_sha256"]:
            logical_bytes = destination.stat().st_size
            physical_bytes = _physical_bytes(destination)
            if physical_bytes < logical_bytes:
                return {
                    "status": "complete", "identity": identity, "gzip_path": str(gzip_path), "gzip_sha256": row["sha256"],
                    "csv_path": _relative(destination), "logical_csv_sha256": row["csv_sha256"],
                    "logical_bytes": logical_bytes, "physical_bytes": physical_bytes,
                    "expected_decompression_bytes": None, "transparent_compression_supported": True,
                    "recovered_after_audit_interruption": True,
                }
        raise ArchiveImportError(f"refusing to overwrite existing CSV without a matching completed audit: {destination}")
    if not gzip_path.is_file() or sha256_file(gzip_path) != row["sha256"]:
        raise ArchiveImportError(f"gzip SHA drift or missing source: {row['source_path']}")
    expected_space = _require_space(out, rows=int(row["rows"]), gzip_size=gzip_path.stat().st_size)
    staging = out / "temporary" / f"{row['csv_basename']}.part.csv"
    if staging.exists():
        if sha256_file(staging) != row["csv_sha256"]:
            raise ArchiveImportError(f"existing temporary CSV SHA differs: {staging}")
    else:
        staging.parent.mkdir(parents=True, exist_ok=True)
        _stream_decompress(gzip_path, staging, str(row["csv_sha256"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    compressed_temp = destination.with_suffix(destination.suffix + ".part")
    compressed_temp.unlink(missing_ok=True)
    _run_ditto(staging, compressed_temp)
    logical_sha = sha256_file(compressed_temp)
    logical_bytes = compressed_temp.stat().st_size
    physical_bytes = _physical_bytes(compressed_temp)
    if logical_sha != row["csv_sha256"]:
        compressed_temp.unlink(missing_ok=True)
        raise ArchiveImportError(f"post-ditto logical CSV SHA drift: {destination}")
    if physical_bytes >= logical_bytes:
        compressed_temp.unlink(missing_ok=True)
        return {
            "status": "storage_compression_unsupported", "identity": identity,
            "gzip_path": str(gzip_path), "gzip_sha256": row["sha256"], "temporary_csv": str(staging),
            "logical_csv_sha256": logical_sha, "logical_bytes": logical_bytes, "physical_bytes": physical_bytes,
            "expected_decompression_bytes": expected_space, "transparent_compression_supported": False,
        }
    os.replace(compressed_temp, destination)
    if sha256_file(destination) != row["csv_sha256"]:
        raise ArchiveImportError(f"post-ditto CSV SHA drift: {destination}")
    destination_physical = _physical_bytes(destination)
    if destination_physical >= logical_bytes:
        destination.unlink()
        return {
            "status": "storage_compression_unsupported", "identity": identity,
            "gzip_path": str(gzip_path), "gzip_sha256": row["sha256"], "temporary_csv": str(staging),
            "logical_csv_sha256": logical_sha, "logical_bytes": logical_bytes, "physical_bytes": destination_physical,
            "expected_decompression_bytes": expected_space, "transparent_compression_supported": False,
        }
    audit = {
        "status": "complete", "identity": identity, "gzip_path": str(gzip_path), "gzip_sha256": row["sha256"],
        "csv_path": _relative(destination), "logical_csv_sha256": logical_sha, "logical_bytes": logical_bytes,
        "physical_bytes": destination_physical, "expected_decompression_bytes": expected_space,
        "transparent_compression_supported": True,
    }
    staging.unlink()  # Only this run's verified temporary file is removable.
    return audit


def import_archives(*, manifest: Path, gzip_root: Path, out: Path, receipt_out: Path) -> dict[str, Any]:
    """Import a frozen manifest; failures close the storage gate but retain evidence."""

    manifest, gzip_root, out, receipt_out = (Path(value).resolve() for value in (manifest, gzip_root, out, receipt_out))
    if not manifest.is_file() or not gzip_root.is_dir():
        raise ArchiveImportError("manifest and gzip root must exist")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    rows = _validate_manifest(payload)
    _relative(out)
    binding = _binding(manifest, payload)
    binding_path = out / "import_binding.json"
    binding_existed = binding_path.exists()
    if binding_existed and json.loads(binding_path.read_text(encoding="utf-8")) != binding:
        raise ArchiveImportError("existing output manifest identity binding differs")
    if receipt_out.exists():
        old = json.loads(receipt_out.read_text(encoding="utf-8"))
        if old.get("binding") != binding:
            raise ArchiveImportError("existing receipt manifest identity binding differs")
    out.mkdir(parents=True, exist_ok=True)
    if not binding_path.exists():
        _write_json(binding_path, binding)
    audits: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        audit_path = out / "audits" / f"{row['csv_basename']}.json"
        try:
            audit = _import_one(row, gzip_root=gzip_root, out=out, binding_existed=binding_existed)
        except Exception as exc:
            audit = {"status": "failed", "identity": _audit_identity(row), "error": f"{type(exc).__name__}: {exc}", "transparent_compression_supported": False}
        _write_json(audit_path, audit)
        audits.append(audit)
        print(f"archive import {index}/{len(rows)} {row['symbol']:<18} {audit['status']}", flush=True)
    complete = [audit for audit in audits if audit["status"] == "complete"]
    supported = len(complete) == len(rows) and all(audit["transparent_compression_supported"] for audit in complete)
    source_rows = []
    for audit in complete:
        identity = audit["identity"]
        source_rows.append({
            "source_path": audit["csv_path"], "sha256": identity["csv_sha256"], "csv_sha256": identity["csv_sha256"],
            "gzip_source_path": identity["source_path"], "gzip_sha256": identity["sha256"], "rows": identity["rows"],
            "first_time": identity["first_time"], "last_time": identity["last_time"], "non_bar_gaps": identity["non_bar_gaps"], "bar_minutes": 1,
            "symbol": identity["symbol"], "venue": identity["venue"],
        })
    source_manifest = {
        "schema_version": 1, "interval": "1m", "sources": source_rows,
        "storage_compression_supported": supported, "source_coverage_complete": supported,
        "coverage_scope": "manifest_subset_only", "source_manifest_is_ready_for_miner": supported,
    }
    source_manifest_path = out / "sources_imported_1m.json"
    _write_json(source_manifest_path, source_manifest)
    receipt = {
        "schema_version": 1, "binding": binding, "sources_requested": len(rows), "sources_complete": len(complete),
        "storage_compression_supported": supported, "gate_open": supported,
        "subset_note": "This import covers only the frozen input manifest; it does not claim all archive symbols are present.",
        "audits": audits, "source_manifest_path": _relative(source_manifest_path),
        "source_manifest_sha256": sha256_file(source_manifest_path), "original_gzip_deleted": False,
    }
    _write_json(receipt_out, receipt)
    return receipt


def _formal_guard(paths: Sequence[Path]) -> None:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ArchiveImportError("main branch required")
    names = [_relative(path) for path in paths]
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *names], cwd=ROOT, text=True)
    if dirty.strip():
        raise ArchiveImportError("commit importer and frozen manifest before formal import")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--gzip-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    args = parser.parse_args()
    _formal_guard([Path(__file__), args.manifest])
    print(json.dumps(import_archives(manifest=args.manifest, gzip_root=args.gzip_root, out=args.out, receipt_out=args.receipt_out), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
