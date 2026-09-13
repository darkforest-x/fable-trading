"""Add an explicit, post-launch dependency audit to the V8 research receipt.

This supplements rather than rewrites the original runner's incomplete source
inventory. It compares current execution dependency bytes to the commit that
predated launch and checks source mtimes. It is not a process-memory attestation
and does not run any strategy, change outcomes or create a new parameter choice.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "spike_v8_early_exit_study.py", "spike_early_failure_exit_study.py",
    "spike_exit_policy_study.py", "spike_v8_replay.py", "spike_v6_wvf_study.py",
    "spike_v7_fast.py", "spike_exit_accounts.py", "spike_v7_episode_study.py",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default="7521699")
    parser.add_argument("--launch", default="2026-09-13T21:41:01+08:00")
    args = parser.parse_args()
    launched = datetime.fromisoformat(args.launch)
    rows = []
    for name in FILES:
        relative = "yoyo/evaluation/" + name
        path = ROOT / relative
        before = subprocess.check_output(["git", "show", f"{args.commit}:{relative}"], cwd=ROOT)
        current = path.read_bytes()
        rows.append({"path": relative, "sha256": hashlib.sha256(current).hexdigest(),
                     "equals_prelaunch_commit": current == before,
                     "source_mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                     "mtime_not_after_launch": path.stat().st_mtime <= launched.timestamp()})
    passed = all(row["equals_prelaunch_commit"] and row["mtime_not_after_launch"] for row in rows)
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "launch": args.launch,
              "prelaunch_commit": subprocess.check_output(["git", "rev-parse", args.commit], cwd=ROOT, text=True).strip(),
              "passed": passed, "dependencies": rows,
              "versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas")},
              "limitation": "Supplemental post-launch current-file audit; not pre-launch registration or process-memory attestation. Original manifests are preserved."}
    if args.output.exists():
        raise ValueError("audit output exists; preserve the prior receipt")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit("dependency source/time audit failed")
    print(json.dumps({"passed": passed, "dependencies": len(rows), "output": str(args.output)}))


if __name__ == "__main__":
    main()
