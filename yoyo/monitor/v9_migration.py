"""Explicit owner-authorized V9 reset for the independent monitor database.

Stop the monitor before running this module. The same exclusive service lock
prevents clearing a live sender's journal. A private SQLite backup precedes
the transactional reset; no credentials, research artifacts, execution state,
or separate shadow book are changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3

from yoyo.monitor import V9_RESET_KEY
from yoyo.monitor.okx import OKX
from yoyo.monitor.store import Store


def migrate(runtime: Path) -> dict:
    runtime = Path(runtime)
    database = runtime / "monitor.sqlite3"
    if not database.is_file():
        raise ValueError("existing monitor database required")
    with (runtime / "service.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(database)
        existing = store.get_meta(V9_RESET_KEY)
        if existing is not None:
            return existing
        client = OKX()
        client.synchronize()
        cutoff = client.clock()
        folder = runtime / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = folder / ("before-v9-" + suffix + ".sqlite3")
        with backup.open("xb"):
            pass
        backup.chmod(0o600)
        with sqlite3.connect(str(database)) as source, sqlite3.connect(str(backup)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
        receipt = store.reset_for_v9(cutoff)
        receipt = dict(receipt, backup=str(backup))
        path = runtime / "v9-reset-receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path,
                        default=Path.home() / "Library/Application Support/Fable/ImpulseMonitor")
    parser.add_argument("--apply", action="store_true", required=True,
                        help="Apply the explicitly requested monitor signal-history reset")
    args = parser.parse_args()
    print(json.dumps(migrate(args.runtime), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
