"""Run the project test suite and record the result as text plus machine JSON.

Used at C0 (baseline) and C7 (final) of the consolidation so that "no new
failures" is a comparison between two generated files rather than a claim.

The JSON keeps the per-test outcome list, not only the counts, because the
counts can stay equal while the *set* of failures changes. C7 compares sets.

Usage:
    python3 tools/consolidation/record_test_run.py --label baseline \
        --out-txt reports/consolidation/baseline_tests.txt \
        --out-json reports/consolidation/baseline_tests.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_LINE = re.compile(
    r"^(?:=+\s*)?(?P<body>(?:\d+\s+(?:failed|passed|skipped|error|errors|xfailed|xpassed|warnings|deselected)(?:,\s*)?)+)"
)
COUNT = re.compile(r"(\d+)\s+(failed|passed|skipped|errors?|xfailed|xpassed|warnings|deselected)")
OUTCOME_LINE = re.compile(r"^(?P<kind>FAILED|ERROR)\s+(?P<nodeid>\S+)")


def parse_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not SUMMARY_LINE.match(line.strip()):
            continue
        found = COUNT.findall(line)
        if not found:
            continue
        counts = {}
        for number, kind in found:
            key = "error" if kind.startswith("error") else kind
            counts[key] = counts.get(key, 0) + int(number)
    return counts


def parse_outcomes(text: str) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    outcomes: list[dict[str, str]] = []
    for line in text.splitlines():
        match = OUTCOME_LINE.match(line.strip())
        if not match:
            continue
        key = (match.group("kind"), match.group("nodeid"))
        if key in seen:
            continue
        seen.add(key)
        outcomes.append({"kind": match.group("kind"), "nodeid": match.group("nodeid")})
    return sorted(outcomes, key=lambda item: (item["kind"], item["nodeid"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", required=True, help="baseline | final | other tag")
    ap.add_argument("--out-txt", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--target", default="tests")
    ap.add_argument(
        "--python",
        default=os.environ.get("CONSOLIDATION_PYTHON", sys.executable),
        help="interpreter to run pytest with; the project venv, not necessarily this one",
    )
    ap.add_argument("--note", default="", help="free-text limitation recorded in the JSON")
    args = ap.parse_args()

    repo_root = Path.cwd()
    command = [args.python, "-m", "pytest", args.target, "-q", "-p", "no:cacheprovider"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )

    started = datetime.now(timezone.utc)
    proc = subprocess.run(command, capture_output=True, text=True, env=env)
    finished = datetime.now(timezone.utc)
    text = proc.stdout + proc.stderr

    out_txt = Path(args.out_txt)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(text + f"\nEXIT={proc.returncode}\n", encoding="utf-8")

    counts = parse_counts(text)
    payload = {
        "schema_version": 1,
        "label": args.label,
        "generated_at": finished.isoformat(timespec="seconds"),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "command": command,
        "cwd": str(repo_root),
        "python": args.python,
        "exit_code": proc.returncode,
        "counts": counts,
        "failing_outcomes": parse_outcomes(text),
        "note": args.note,
        "holdout_consumed_during_consolidation": False,
    }
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[{args.label}] {counts} exit={proc.returncode}")
    print(f"wrote {out_txt} and {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
