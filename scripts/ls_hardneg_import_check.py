#!/usr/bin/env python3
"""Validate hardneg Label Studio import pack + print one-shot import commands.

Does NOT start Label Studio UI. Does NOT require credentials.
Optionally probes LS HTTP if --probe-url is given.

Usage:
  python3 scripts/ls_hardneg_import_check.py
  python3 scripts/ls_hardneg_import_check.py --probe-url http://127.0.0.1:8081
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS = PROJECT / "output/label_studio/tasks_hardneg_discovery.json"
DEFAULT_CFG = PROJECT / "output/label_studio/label_config.xml"
DEFAULT_README = PROJECT / "output/label_studio/tasks_hardneg_discovery_README.md"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    ap.add_argument("--config", type=Path, default=DEFAULT_CFG)
    ap.add_argument("--probe-url", default="")
    args = ap.parse_args()

    ok = True
    if not args.tasks.is_file():
        print(f"FAIL missing tasks: {args.tasks}")
        return 1
    tasks = json.loads(args.tasks.read_text())
    if not isinstance(tasks, list) or not tasks:
        print("FAIL tasks JSON is empty or not a list")
        return 1
    missing_keys = [i for i, t in enumerate(tasks) if "data" not in t or "image" not in t.get("data", {})]
    if missing_keys:
        print(f"FAIL {len(missing_keys)} tasks missing data.image (e.g. idx {missing_keys[:3]})")
        ok = False
    print(f"OK tasks n={len(tasks)} path={args.tasks.relative_to(PROJECT)}")
    print(f"  first image: {tasks[0]['data']['image'][:120]}")

    if args.config.is_file():
        cfg = args.config.read_text()
        print(f"OK label_config bytes={len(cfg)} path={args.config.relative_to(PROJECT)}")
        if "dense_cluster" not in cfg:
            print("WARN label_config has no dense_cluster tag name")
    else:
        print(f"WARN missing config {args.config}")
        ok = False

    if DEFAULT_README.is_file():
        print(f"OK readme {DEFAULT_README.relative_to(PROJECT)}")

    print("\n--- one-shot import (Owner runs when ready; UI not required for this check) ---")
    print("1) Start LS (optional):")
    print("   docker compose -f scripts/label_studio_compose.yml up -d")
    print("2) Create project → paste label_config.xml → Import tasks_hardneg_discovery.json")
    print("3) Or API (needs scripts/.label_studio.env + running LS):")
    print(
        "   PYTHONPATH=. python3 scripts/ls_auto_import.py "
        "'hardneg_discovery' output/label_studio/tasks_hardneg_discovery.json "
        "output/label_studio/label_config.xml"
    )
    print("4) Rebuild pack if needed:")
    print("   PYTHONPATH=. .venv/bin/python scripts/hardneg_to_labelstudio.py --limit 24")

    if args.probe_url:
        url = args.probe_url.rstrip("/") + "/"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                print(f"\nPROBE {url} -> HTTP {resp.status}")
        except urllib.error.URLError as exc:
            print(f"\nPROBE {url} unreachable (expected if LS not started): {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"\nPROBE {url} error: {exc}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
