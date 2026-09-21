"""Fetch a separately frozen Binance 1m archive and publish deterministic gzip inputs.

This wrapper intentionally loads an absolute snapshot of ``binance_um_archives``
instead of importing the working-tree module.  It preserves the upstream ZIP and
per-symbol audit, then replaces only its temporary aggregate CSV with a
deterministic ``.csv.gz`` whose decompressed SHA is recorded for resumption.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


MIN_FREE_BYTES = 20 * 1024**3


class ArchiveExtensionError(RuntimeError):
    """Raised when the frozen archive extension cannot be safely resumed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_archiver(path: Path) -> ModuleType:
    """Load one explicit archiver snapshot without consulting ``sys.path``."""

    snapshot = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("ma_profit_archiver_snapshot", snapshot)
    if spec is None or spec.loader is None:
        raise ArchiveExtensionError(f"cannot load archiver snapshot: {snapshot}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("fetch_symbol", "admitted_symbols", "sha256_file"):
        if not hasattr(module, name):
            raise ArchiveExtensionError(f"archiver snapshot lacks {name}")
    return module


def _load_config(path: Path, archiver_path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "interval", "archive_start", "archive_end_inclusive", "archive_max_exclusive", "archiver_sha256", "exchange_info_path", "exchange_info_sha256", "expected_admitted_symbols", "source_plan_sha256"}
    if not required.issubset(config):
        raise ArchiveExtensionError(f"extension config missing {sorted(required - set(config))}")
    if config["schema_version"] != 1 or config["interval"] != "1m":
        raise ArchiveExtensionError("extension config must pin schema 1 and interval 1m")
    if config["archiver_sha256"] != sha256_file(archiver_path):
        raise ArchiveExtensionError("archiver snapshot SHA drift")
    snapshot = Path(config["exchange_info_path"])
    if not snapshot.is_absolute():
        snapshot = (path.parent / snapshot).resolve()
    if not snapshot.exists() or config["exchange_info_sha256"] != sha256_file(snapshot):
        raise ArchiveExtensionError("exchange-info snapshot SHA drift")
    config["_exchange_info_snapshot"] = snapshot
    return config


def _gzip_csv(source: Path, destination: Path) -> tuple[str, str]:
    """Write a reproducible gzip stream and return decompressed/gzip SHA-256."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with source.open("rb") as raw, temporary.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as packed:
            shutil.copyfileobj(raw, packed, length=1024 * 1024)
    os.replace(temporary, destination)
    return sha256_file(source), sha256_file(destination)


def _require_free_space(path: Path) -> None:
    if shutil.disk_usage(path).free < MIN_FREE_BYTES:
        raise ArchiveExtensionError("less than 20 GiB free before archive download")


def _run_binding(*, archiver_path: Path, config_path: Path, config: Mapping[str, Any]) -> dict[str, str]:
    return {
        "archiver_sha256": sha256_file(archiver_path),
        "config_sha256": sha256_file(config_path),
        "exchange_info_sha256": str(config["exchange_info_sha256"]),
        "wrapper_sha256": sha256_file(Path(__file__)),
    }


def _valid_compressed(audit: Mapping[str, Any]) -> bool:
    path = Path(str(audit.get("gzip_path", "")))
    if not path.exists() or sha256_file(path) != audit.get("gzip_sha256"):
        return False
    digest = hashlib.sha256()
    try:
        with gzip.open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest() == audit.get("csv_sha256")


def _compress_symbol(row: Mapping[str, Any], *, archiver: ModuleType, config: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    symbol = str(row["symbol"])
    compressed_audit = output_dir / "compressed_audits" / f"{symbol}.json"
    if compressed_audit.exists():
        prior = json.loads(compressed_audit.read_text(encoding="utf-8"))
        if prior.get("status") == "complete" and _valid_compressed(prior):
            return prior
    _require_free_space(output_dir)
    source = archiver.fetch_symbol(row, output_dir=output_dir, archive_start=config["archive_start"], archive_end_inclusive=config["archive_end_inclusive"], archive_max_exclusive=config["archive_max_exclusive"], interval="1m")
    result: dict[str, Any] = {"symbol": symbol, "status": source.get("status"), "source_audit_path": str(output_dir / "audits" / f"{symbol}.json"), "source_audit_sha256": sha256_file(output_dir / "audits" / f"{symbol}.json")}
    if source.get("status") != "complete":
        result["error"] = "upstream_no_complete_series"
        write_json(compressed_audit, result)
        return result
    csv_path = Path(str(source["output_path"]))
    series_dir = (output_dir / "series").resolve()
    if csv_path.resolve().parent != series_dir:
        raise ArchiveExtensionError(f"refusing to delete CSV outside this run's series directory: {csv_path}")
    gzip_path = output_dir / "compressed" / f"{csv_path.name}.gz"
    csv_sha, gzip_sha = _gzip_csv(csv_path, gzip_path)
    if csv_sha != source.get("output_sha256"):
        raise ArchiveExtensionError(f"upstream CSV SHA drift: {symbol}")
    result.update({"status": "complete", "csv_sha256": csv_sha, "gzip_path": str(gzip_path), "gzip_sha256": gzip_sha, "rows": int(source["rows"]), "first_time": source["first_time"], "last_time": source["last_time"], "non_bar_gaps": int(source["non_bar_gaps"]), "interval": "1m", "coverage": {"months_requested": source["months_requested"], "months_complete": source["months_complete"], "months_missing": source["months_missing"]}})
    if not _valid_compressed(result):
        raise ArchiveExtensionError(f"compressed SHA verification failed: {symbol}")
    csv_path.unlink()
    write_json(compressed_audit, result)
    return result


def run(*, archiver_path: Path, config_path: Path, output_dir: Path, workers: int = 4) -> dict[str, Any]:
    """Fetch/resume the frozen extension; any missing or failed source closes its gate."""

    if not 1 <= int(workers) <= 4:
        raise ArchiveExtensionError("workers must be between 1 and 4")
    if output_dir.exists() and not output_dir.is_dir():
        raise ArchiveExtensionError(f"output is not a directory: {output_dir}")
    archiver_path, config_path, output_dir = Path(archiver_path).resolve(), Path(config_path).resolve(), Path(output_dir).resolve()
    config = _load_config(config_path, archiver_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    binding = _run_binding(archiver_path=archiver_path, config_path=config_path, config=config)
    binding_path = output_dir / "run_binding.json"
    if binding_path.exists() and json.loads(binding_path.read_text(encoding="utf-8")) != binding:
        raise ArchiveExtensionError("existing output run binding drift")
    if not binding_path.exists():
        write_json(binding_path, binding)
    _require_free_space(output_dir)
    archiver = load_archiver(archiver_path)
    exchange_info = json.loads(Path(config["_exchange_info_snapshot"]).read_text(encoding="utf-8"))
    symbols = archiver.admitted_symbols(exchange_info, before=config["archive_max_exclusive"])
    if len(symbols) != int(config["expected_admitted_symbols"]):
        raise ArchiveExtensionError("admitted-symbol snapshot drift")
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=int(workers)) as pool:
        future_map = {pool.submit(_compress_symbol, row, archiver=archiver, config=config, output_dir=output_dir): row["symbol"] for row in symbols}
        for index, future in enumerate(as_completed(future_map), 1):
            symbol = str(future_map[future])
            try:
                result = future.result()
            except Exception as exc:  # preserve every source failure and finish the batch audit.
                result = {"symbol": symbol, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                write_json(output_dir / "compressed_audits" / f"{symbol}.json", result)
            results.append(result)
            print(f"archive extension {index}/{len(future_map)} {symbol:<18} {result['status']:<8} rows={int(result.get('rows', 0)):>7}", flush=True)
    results.sort(key=lambda row: str(row["symbol"]))
    complete = [row for row in results if row.get("status") == "complete"]
    failed = [row for row in results if row.get("status") != "complete"]
    sources = [{"source_path": str(Path(row["gzip_path"]).relative_to(output_dir)), "sha256": row["gzip_sha256"], "csv_sha256": row["csv_sha256"], "rows": row["rows"], "first_time": row["first_time"], "last_time": row["last_time"], "non_bar_gaps": row["non_bar_gaps"], "bar_minutes": 1, "symbol": row["symbol"], "venue": "binance_um"} for row in complete]
    manifest = {"schema_version": 1, "interval": "1m", "source_coverage_complete": not failed and len(complete) == len(symbols), "sources": sources}
    write_json(output_dir / "sources_archive_1m.json", manifest)
    summary = {"schema_version": 1, "status": "completed" if manifest["source_coverage_complete"] else "failed", "source_coverage_complete": manifest["source_coverage_complete"], "run_binding": binding, "archiver_path": str(archiver_path), "archiver_sha256": sha256_file(archiver_path), "wrapper_sha256": binding["wrapper_sha256"], "config_path": str(config_path), "config_sha256": sha256_file(config_path), "exchange_info_sha256": config["exchange_info_sha256"], "source_plan_sha256": config["source_plan_sha256"], "builder_commit": config.get("builder_commit"), "workers": int(workers), "symbols_admitted": len(symbols), "symbols_complete": len(complete), "symbols_failed": len(failed), "rows": sum(int(row["rows"]) for row in complete), "failures": failed, "sources_manifest_sha256": sha256_file(output_dir / "sources_archive_1m.json")}
    write_json(output_dir / "archive1m_receipt.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archiver", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(run(archiver_path=args.archiver, config_path=args.config, output_dir=args.out, workers=args.workers), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
