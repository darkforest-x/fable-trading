"""Install a reversible local LS browser shortcut without restarting its server.

The installed LS core.urls serves web/dist/apps/labelstudio/main.js from disk.
Append our scoped handler after the unmodified vendor bundle; retain a byte-exact
backup and refuse source/installation drift. No labels, accounts or DB writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

MARKER = b"\n;/* FABLE_LS_RIGHT_CLICK_SUBMIT_V1 */\n"
MARKER_PREFIX = b"FABLE_LS_RIGHT_CLICK_SUBMIT_"
SOURCE = Path(__file__).with_name("label_studio_right_click.js")


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(bundle: Path, state_dir: Path, restore: bool = False) -> dict:
    bundle = bundle.resolve(strict=True)
    current = bundle.read_bytes()
    receipt_path = state_dir / "installation.json"
    state_dir.mkdir(parents=True, exist_ok=True)
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if previous:
        if previous["bundle"] != str(bundle):
            raise ValueError("Receipt belongs to a different bundle")
        if sha(current) not in {previous["original_sha256"], previous["installed_sha256"]}:
            raise ValueError("Installed bundle drift; refusing to overwrite")
        backup = state_dir / previous["backup_name"]
        original = backup.read_bytes()
        if sha(original) != previous["original_sha256"]:
            raise ValueError("Backup hash mismatch")
    else:
        if restore:
            raise ValueError("No installation receipt to restore")
        if MARKER_PREFIX in current:
            raise ValueError("Existing hook has no matching receipt")
        original = current
        backup = state_dir / ("main.original." + sha(original) + ".js")
        if backup.exists() and backup.read_bytes() != original:
            raise ValueError("Existing backup differs")
        atomic_write(backup, original)
    if restore:
        target = original
        installed_sha = previous["installed_sha256"]
        source_sha = previous["source_sha256"]
    else:
        source = SOURCE.read_bytes()
        target = original + MARKER + source + b"\n"
        installed_sha, source_sha = sha(target), sha(source)
        if previous and not previous["restored"] and installed_sha != previous["installed_sha256"]:
            raise ValueError("Handler source changed; restore old installation before upgrading")
    # Detect a concurrent writer before replacement; do not overwrite its edits.
    if bundle.read_bytes() != current:
        raise ValueError("Bundle changed during installation")
    receipt = {
        "bundle": str(bundle), "backup_name": backup.name,
        "original_sha256": sha(original),
        "installed_sha256": installed_sha,
        "source_sha256": source_sha,
        "current_sha256": sha(target),
        "restored": restore, "server_restart": False, "annotation_writes": 0,
        "status": "prepared",
    }
    # Persist recovery identity before touching the live bundle. A crash here
    # leaves either known original or known installed bytes recoverable.
    atomic_write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode())
    if bundle.read_bytes() != current:
        raise ValueError("Bundle changed while preparing recovery receipt")
    if current != target:
        atomic_write(bundle, target)
    receipt["status"] = "restored" if restore else "installed"
    atomic_write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode())
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(args.bundle, args.state_dir, args.restore), indent=2))


if __name__ == "__main__":
    main()
