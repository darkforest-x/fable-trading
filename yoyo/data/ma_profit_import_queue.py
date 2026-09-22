"""Fail-closed controller for sequential frozen 1m archive imports.

It only consumes immutable ``compressed_audits`` produced by the remote
extension.  A batch is eligible only when every named audit is ``complete``;
outcome data is never consulted.  The controller deliberately does not start
itself on import: callers invoke :func:`run` or the CLI after committing
this builder and the frozen batch plan.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import argparse
import base64
import os
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping

from yoyo.data.ma_profit_archive_import import ArchiveImportError, import_archives, sha256_file

ROOT = Path(__file__).resolve().parents[2]
MIN_FREE_BYTES = 20 * 1024**3


class QueueError(RuntimeError):
    """Raised when a frozen batch cannot be safely advanced."""


def terminal_statuses(audits: Mapping[str, Mapping[str, Any]], symbols: list[str]) -> dict[str, str]:
    """Return statuses, rejecting absent evidence rather than treating it as ready."""
    return {s: str(audits[s].get("status", "missing")) if s in audits else "missing" for s in symbols}


def require_ready(audits: Mapping[str, Mapping[str, Any]], symbols: list[str]) -> None:
    states = terminal_statuses(audits, symbols)
    bad = {s: v for s, v in states.items() if v != "complete"}
    if bad:
        raise QueueError("remote audits not all complete: " + json.dumps(bad, sort_keys=True))


def frozen_sources(*, experiment: Path, batch: Mapping[str, Any], audits: Mapping[str, Mapping[str, Any]], binding: Mapping[str, Any], config_sha256: str) -> dict[str, Any]:
    """Build the importer manifest from audited gzip identities only."""
    symbols = list(batch["symbols"]); require_ready(audits, symbols)
    sources = []
    for symbol in symbols:
        audit = audits[symbol]
        path = PureWindowsPath(str(audit["gzip_path"])).name
        if not path.endswith(".csv.gz"):
            raise QueueError(f"invalid gzip path for {symbol}")
        sources.append({"source_path": f"compressed/{path}", "sha256": audit["gzip_sha256"], "csv_sha256": audit["csv_sha256"], "rows": audit["rows"], "first_time": audit["first_time"], "last_time": audit["last_time"], "non_bar_gaps": audit["non_bar_gaps"], "bar_minutes": 1, "symbol": symbol, "venue": "binance_um", "batch_id": batch["batch_id"], "source_audit_sha256": audit["source_audit_sha256"], "upstream_gzip_path": audit["gzip_path"]})
    return {"schema_version": 1, "experiment_id": experiment.name, "batch_id": batch["batch_id"], "interval": "1m", "source_subset": True, "extension_config_sha256": config_sha256, "remote_run_binding": dict(binding), "sources": sources}


def verify_import(*, manifest: Mapping[str, Any], out: Path, receipt: Mapping[str, Any]) -> None:
    """Bind every published source to its frozen gzip and in-batch CSV bytes."""
    expected = {r["symbol"]: r for r in manifest["sources"]}
    if len(expected) != len(manifest["sources"]):
        raise QueueError("duplicate frozen symbol")
    if not receipt.get("gate_open") or receipt.get("sources_complete") != len(expected):
        raise QueueError("import gate closed")
    source_path = out / "sources_imported_1m.json"
    published = json.loads(source_path.read_text())
    if sha256_file(source_path) != receipt["source_manifest_sha256"]:
        raise QueueError("source manifest SHA drift")
    rows = {r["symbol"]: r for r in published["sources"]}
    if len(rows) != len(published["sources"]) or set(rows) != set(expected):
        raise QueueError("published source set drift")
    for symbol, source in expected.items():
        row = rows[symbol]
        path = (ROOT / row["source_path"]).resolve()
        if not path.is_relative_to(out.resolve()) or path.is_symlink():
            raise QueueError("published source escapes batch output")
        if row["sha256"] != source["csv_sha256"] or sha256_file(path) != source["csv_sha256"]:
            raise QueueError(f"CSV SHA drift: {symbol}")
        if row["gzip_sha256"] != source["sha256"] or row["rows"] != source["rows"]:
            raise QueueError(f"source lineage drift: {symbol}")


def import_frozen(*, manifest_path: Path, staging: Path, out: Path, receipt_path: Path) -> dict[str, Any]:
    """A verified completed receipt resumes without rewriting imported data."""
    manifest = json.loads(manifest_path.read_text())
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("gate_open"):
            if receipt["binding"]["archive_manifest_sha256"] != sha256_file(manifest_path):
                raise QueueError("import receipt manifest drift")
            verify_import(manifest=manifest, out=out, receipt=receipt)
            return receipt
        raise QueueError("prior failed import requires explicit recovery; preserving evidence")
    require_space(out.parent)
    receipt = import_archives(manifest=manifest_path, gzip_root=staging, out=out, receipt_out=receipt_path)
    verify_import(manifest=manifest, out=out, receipt=receipt)
    return receipt


def require_space(path: Path) -> None:
    while not path.exists():
        path = path.parent
    if shutil.disk_usage(path).free < MIN_FREE_BYTES:
        raise QueueError("less than 20 GiB free")


def write_new(path: Path, value: Mapping[str, Any]) -> None:
    """Resume identical metadata; never overwrite conflicting evidence."""
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise QueueError(f"existing metadata drift: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def git(*args: str) -> str:
    for attempt in range(6):
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            return result.stdout.strip()
        if "index.lock" not in result.stderr or attempt == 5:
            raise QueueError("Git failed: " + result.stderr[-1200:])
        time.sleep(2)
    raise QueueError("Git retries exhausted")


def frozen(paths: list[Path]) -> None:
    if git("branch", "--show-current") != "main":
        raise QueueError("main required")
    names = [str(p.resolve().relative_to(ROOT)) for p in paths]
    for name in names:
        git("ls-files", "--error-unmatch", "--", name)
    if git("status", "--porcelain", "--", *names):
        raise QueueError("uncommitted input or builder")


def freeze(paths: list[Path]) -> str:
    if git("branch", "--show-current") != "main":
        raise QueueError("main required")
    names = [str(p.resolve().relative_to(ROOT)) for p in paths]
    if any(p.stat().st_size > 95 * 1024**2 for p in paths):
        raise QueueError("metadata unexpectedly large")
    git("add", "--", *names)
    if git("diff", "--cached", "--name-only", "--", *names):
        if git("branch", "--show-current") != "main":
            raise QueueError("branch changed before commit")
        git("commit", "-m", "Freeze sequential 1m import batch", "--", *names)
    frozen(paths)
    return git("rev-parse", "HEAD")


def _remote_audits(host: str, remote_root: str, symbols: list[str], binding: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """One bounded SSH read; absent audits are ordinary pending downloads."""
    script = f"""import json,hashlib
from pathlib import Path
root=Path({remote_root!r})
expected={dict(binding)!r}
actual=json.loads((root/'run_binding.json').read_text(encoding='utf-8'))
assert actual==expected, 'remote run binding drift'
answer={{}}
for symbol in {symbols!r}:
 p=root/'compressed_audits'/(symbol+'.json')
 if not p.exists(): continue
 row=json.loads(p.read_text(encoding='utf-8'))
 assert row['symbol']==symbol, 'remote symbol drift'
 if row.get('status')=='complete':
  source=root/'audits'/(symbol+'.json')
  assert hashlib.sha256(source.read_bytes()).hexdigest()==row['source_audit_sha256'], 'source audit drift'
 answer[symbol]=row
print(json.dumps(answer))
"""
    encoded = base64.b64encode(script.encode()).decode()
    remote = 'C:/fable/.venv/Scripts/python.exe -c "import base64;exec(base64.b64decode(\'' + encoded + '\'))"'
    for attempt in range(3):
        try:
            result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, remote], capture_output=True, timeout=50)
        except subprocess.TimeoutExpired:
            if attempt == 2: raise QueueError("SSH audit timeout")
            time.sleep(5); continue
        if result.returncode == 0:
            return json.loads(result.stdout)
        if result.returncode != 255 or attempt == 2:
            raise QueueError("remote audit failed: " + result.stderr.decode(errors="replace")[-1200:])
        time.sleep(5)
    raise QueueError("SSH retries exhausted")


def wait_ready(fetch: Callable[[], Mapping[str, Mapping[str, Any]]], symbols: list[str], *, interval_seconds: int = 50) -> Mapping[str, Mapping[str, Any]]:
    last = None
    while True:
        audits = fetch(); states = terminal_statuses(audits, symbols)
        terminal = {s: v for s, v in states.items() if v not in {"complete", "missing"}}
        if terminal: raise QueueError("remote terminal failure: " + json.dumps(terminal, sort_keys=True))
        count = sum(v == "complete" for v in states.values())
        if count != last:
            print(f"import queue remote ready {count}/{len(symbols)}", flush=True); last = count
        if count == len(symbols): return audits
        time.sleep(min(60, max(1, interval_seconds)))


def transfer(host: str, remote_root: str, manifest: Mapping[str, Any], staging: Path) -> None:
    root = PureWindowsPath(remote_root)
    for row in manifest["sources"]:
        require_space(staging)
        target = staging / row["source_path"]
        if not target.resolve().is_relative_to(staging.resolve()):
            raise QueueError("staging path escapes batch")
        upstream = PureWindowsPath(row["upstream_gzip_path"])
        if upstream.parent != root / "compressed" or upstream.name != target.name:
            raise QueueError("unexpected upstream gzip path")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_file(target) != row["sha256"]: raise QueueError("existing gzip SHA drift")
            continue
        temporary = target.with_suffix(target.suffix + ".part")
        for attempt in range(3):
            result = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{host}:{upstream.as_posix()}", str(temporary)], timeout=600)
            if result.returncode == 0: break
            if attempt == 2: raise QueueError("gzip transfer failed")
            time.sleep(5)
        if sha256_file(temporary) != row["sha256"]: raise QueueError("downloaded gzip SHA drift")
        os.replace(temporary, target)


def finish_batch(experiment: Path, batch: Mapping[str, Any], manifest_path: Path, staging: Path, out: Path) -> None:
    name = batch["batch_id"]
    meta = experiment / "imports_1m"
    receipt_path = meta / f"{name}_import_receipt.json"
    manifest = json.loads(manifest_path.read_text())
    receipt = import_frozen(manifest_path=manifest_path, staging=staging, out=out, receipt_path=receipt_path)
    binding_path = meta / f"{name}_import_binding.json"
    published = experiment / f"sources_archive_1m_{name.replace('_', '')}.json"
    write_new(binding_path, receipt["binding"])
    write_new(published, json.loads((out / "sources_imported_1m.json").read_text()))
    freeze([receipt_path, binding_path, published])
    cleanup = meta / f"{name}_cleanup_receipt.json"
    if not cleanup.exists():
        removed = []
        for row in manifest["sources"]:
            target = staging / row["source_path"]
            if not target.resolve().is_relative_to(staging.resolve()): raise QueueError("cleanup escapes staging")
            if target.exists():
                if sha256_file(target) != row["sha256"]: raise QueueError("cleanup gzip drift")
                size = target.stat().st_size; target.unlink()
                removed.append({"path": str(target.relative_to(ROOT)), "sha256": row["sha256"], "bytes": size})
        write_new(cleanup, {"batch_id": name, "source_manifest_sha256": sha256_file(published), "removed_local_staging": removed, "remote_files_changed": False})
    cleanup_record = json.loads(cleanup.read_text())
    if (cleanup_record.get("batch_id") != name or cleanup_record.get("source_manifest_sha256") != sha256_file(published)
            or cleanup_record.get("remote_files_changed") is not False):
        raise QueueError("cleanup receipt binding drift")
    # A prior process may have written the receipt but exited before its commit.
    freeze([cleanup])
    print(json.dumps({"batch": name, "status": "complete", "sources": receipt["sources_complete"], "source_manifest_sha256": sha256_file(published)}), flush=True)


def run(plan_path: Path, host: str, remote_root: str, from_batch: str, poll_seconds: int) -> None:
    plan_path = plan_path.resolve(); experiment = plan_path.parent
    reference = experiment / "imports_1m/batch_05_gzip_sources.json"
    config_path = experiment / "extension_1m.json"
    pinned = [Path(__file__), ROOT / "yoyo/data/ma_profit_archive_import.py", plan_path, reference, config_path]
    frozen(pinned)
    pins = {str(path): sha256_file(path) for path in pinned}
    plan = json.loads(plan_path.read_text()); previous = json.loads(reference.read_text())
    binding = previous["remote_run_binding"]
    if plan["extension_config_sha256"] != sha256_file(config_path) or binding["config_sha256"] != sha256_file(config_path):
        raise QueueError("extension config drift")
    batches = plan["batches"]; flattened = [s for b in batches for s in b["symbols"]]
    if len(flattened) != plan["symbols_total"] or flattened != sorted(set(flattened)):
        raise QueueError("fixed batch membership/order drift")
    start = next((i for i,b in enumerate(batches) if b["batch_id"] == from_batch), None)
    if start is None: raise QueueError("unknown from-batch")
    lock = experiment / "import_queue.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(descriptor, json.dumps({"pid": os.getpid(), "pins": pins}).encode()); os.close(descriptor)
    try:
        for batch in batches[start:]:
            if any(sha256_file(Path(path)) != value for path,value in pins.items()): raise QueueError("controller/input drift during run")
            require_space(experiment)
            name = batch["batch_id"]
            manifest_path = experiment / f"imports_1m/{name}_gzip_sources.json"
            snapshot = experiment / f"imports_1m/{name}_upstream_audits_snapshot.json"
            staging = experiment / f"staging_1m/{name}"
            out = experiment / f"inputs/binance_1m_import/{name}"
            if manifest_path.exists():
                frozen([manifest_path, snapshot])
                manifest = json.loads(manifest_path.read_text())
                if [s["symbol"] for s in manifest["sources"]] != batch["symbols"] or manifest["remote_run_binding"] != binding:
                    raise QueueError("resumed manifest drift")
            else:
                audits = wait_ready(lambda: _remote_audits(host, remote_root, batch["symbols"], binding), batch["symbols"], interval_seconds=poll_seconds)
                manifest = frozen_sources(experiment=experiment, batch=batch, audits=audits, binding=binding, config_sha256=sha256_file(config_path))
                write_new(snapshot, {"schema_version": 1, "batch_id": name, "symbols": batch["symbols"], "audits": [audits[s] for s in batch["symbols"]], "remote_run_binding": binding, "import_batches_sha256": sha256_file(plan_path)})
                write_new(manifest_path, manifest)
                freeze([snapshot, manifest_path])
            receipt_path = experiment / f"imports_1m/{name}_import_receipt.json"
            if not receipt_path.exists(): transfer(host, remote_root, manifest, staging)
            staging.mkdir(parents=True, exist_ok=True)
            finish_batch(experiment, batch, manifest_path, staging, out)
    except Exception as exc:
        error = experiment / f"import_queue_error_{int(time.time())}.json"
        write_new(error, {"error": f"{type(exc).__name__}: {exc}", "pins": pins})
        raise
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--host", default="Administrator@192.168.1.2")
    parser.add_argument("--remote-root", default="C:/fable/experiments/active/exp-ma-profit3r-20260922-v1/inputs/binance_1m_full")
    parser.add_argument("--from-batch", default="batch_06")
    parser.add_argument("--poll-seconds", type=int, default=50)
    args = parser.parse_args()
    run(args.plan, args.host, args.remote_root, args.from_batch, args.poll_seconds)


if __name__ == "__main__":
    main()
