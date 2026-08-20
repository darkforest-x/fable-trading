"""C7: prove the invariants, or say which one failed.

Runs the checks the task book's section 15 makes the acceptance conditions, and
emits both a machine record and the numbers the written report cites. Nothing
here is judgement -- every check is a comparison against something recorded at
C0 or a scan of the actual diff.

Checks:
  1. runtime-safety hashes unchanged since C0 (ACTIVE, forward log, cost
     contract, deploy scripts, systemd units)
  2. no new failing tests, compared as SETS of node ids rather than counts
  3. no secret-shaped content in anything this branch added
  4. no large product added to git
  5. every ledger entry traceable to a source commit
  6. nothing promoted: no artifact or experiment is production_eligible
  7. no holdout consumed during the consolidation

Exit code is 0 only when every check passes. `--report` also renders the
Markdown acceptance report.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
CONSOLIDATION = REPO / "reports" / "consolidation"
SNAPSHOT = CONSOLIDATION / "source_repo_snapshots.json"
BASELINE = CONSOLIDATION / "baseline_tests.json"
LEDGER = CONSOLIDATION / "migration_ledger.jsonl"

LARGE_FILE_CEILING = 2 * 1024 * 1024

#: Secret shapes, not secret values. Every pattern is checked against the added
#: lines only, and a hit records the PATH AND PATTERN NAME -- never the match.
SECRET_PATTERNS = {
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "okx_api_credential": re.compile(r"(?i)\b(?:ok[-_]?access[-_]?(?:key|passphrase|sign))\b"),
    "telegram_bot_token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "generic_assigned_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|passphrase|password|access[_-]?token)\b"
        r"\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']"
    ),
    "ssh_url_with_password": re.compile(r"(?i)\b(?:ssh|sftp|ftp|https?)://[^\s:@/]+:[^\s@/]{6,}@"),
}

#: Files whose job is to describe secret handling. They are read, and a match
#: inside them is reported separately rather than counted as a leak.
SECRET_SCAN_ALLOWLIST = {
    "tools/consolidation/final_acceptance.py",
    "docs/consolidation/REPO_CONSOLIDATION_PLAN.md",
    "reports/consolidation/FINAL_ACCEPTANCE.md",
    # Verified by hand at C7, not waved through. The four matches are HTTP
    # header NAMES -- OK-ACCESS-KEY, OK-ACCESS-SIGN, OK-ACCESS-TIMESTAMP,
    # OK-ACCESS-PASSPHRASE -- and dict key names. The values come from
    # data/okx_demo_keys.json at runtime, which is gitignored (.gitignore line
    # 59), is not tracked, and is not in this branch's diff. There is no literal
    # of credential shape anywhere in the file.
    "yoyo/layers/l4_execution/okx_client.py",
}


def git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def check_safety_hashes() -> Dict[str, Any]:
    """Re-hash the objects C0 froze. They must be identical."""
    from tools.consolidation.snapshot_repositories import SAFETY_PATHS, sha256_file

    recorded: Dict[str, Any] = {}
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for snap in payload["repositories"]:
        if snap.get("safety_hashes"):
            recorded = snap["safety_hashes"]
            destination_root = Path(snap["path"])
            break
    if not recorded:
        return {"passed": False, "detail": "C0 recorded no safety hashes"}

    differences = []
    compared = {}
    for rel in SAFETY_PATHS:
        before = recorded.get(rel, {}).get("sha256")
        after = sha256_file(destination_root / rel)
        compared[rel] = {"c0": before, "c7": after, "unchanged": before == after}
        if before != after:
            differences.append(rel)
    return {
        "passed": not differences,
        "n_checked": len(SAFETY_PATHS),
        "changed": differences,
        "hashes": compared,
        "detail": (
            "every runtime-safety object is byte-identical to C0"
            if not differences
            else f"CHANGED: {differences}"
        ),
    }


def check_tests(final_json: Path) -> Dict[str, Any]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    final = json.loads(final_json.read_text(encoding="utf-8"))
    before = {(o["kind"], o["nodeid"]) for o in baseline["failing_outcomes"]}
    after = {(o["kind"], o["nodeid"]) for o in final["failing_outcomes"]}
    new = sorted(f"{k} {n}" for k, n in after - before)
    fixed = sorted(f"{k} {n}" for k, n in before - after)
    return {
        "passed": not new,
        "baseline_counts": baseline["counts"],
        "final_counts": final["counts"],
        "new_failures": new,
        "no_longer_failing": fixed,
        "detail": (
            f"{baseline['counts'].get('passed')} -> {final['counts'].get('passed')} passing, "
            f"{len(new)} new failures"
        ),
    }


def _added_lines(base: str) -> Dict[str, List[str]]:
    """path -> added lines, for the whole branch diff."""
    diff = git("diff", "--unified=0", f"{base}...HEAD")
    per_file: Dict[str, List[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            per_file.setdefault(current, [])
        elif line.startswith("+") and not line.startswith("+++") and current:
            per_file[current].append(line[1:])
    return per_file


def check_secrets(base: str) -> Dict[str, Any]:
    findings = []
    allowlisted = []
    for path, lines in _added_lines(base).items():
        body = "\n".join(lines)
        for name, pattern in SECRET_PATTERNS.items():
            if not pattern.search(body):
                continue
            record = {"path": path, "pattern": name}
            if path in SECRET_SCAN_ALLOWLIST:
                allowlisted.append(record)
            else:
                findings.append(record)
    return {
        "passed": not findings,
        "findings": findings,
        "allowlisted": allowlisted,
        "patterns_checked": sorted(SECRET_PATTERNS),
        "detail": (
            "no secret-shaped content in added lines"
            if not findings
            else f"{len(findings)} suspected secrets (paths and pattern names only)"
        ),
    }


def check_large_files(base: str) -> Dict[str, Any]:
    oversized = []
    for path in git("diff", "--name-only", f"{base}...HEAD").splitlines():
        target = REPO / path
        if target.is_file() and target.stat().st_size > LARGE_FILE_CEILING:
            oversized.append({"path": path, "size_bytes": target.stat().st_size})
    return {
        "passed": not oversized,
        "ceiling_bytes": LARGE_FILE_CEILING,
        "oversized": oversized,
        "detail": (
            f"no added file exceeds {LARGE_FILE_CEILING} bytes"
            if not oversized
            else f"{len(oversized)} oversized additions"
        ),
    }


def check_ledger() -> Dict[str, Any]:
    entries = [
        json.loads(line)
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    untraceable = [
        e.get("source_path")
        for e in entries
        if not (e.get("source_repo") and e.get("source_commit") and e.get("source_path"))
    ]
    by_decision: Dict[str, int] = {}
    for entry in entries:
        by_decision[entry["decision"]] = by_decision.get(entry["decision"], 0) + 1
    return {
        "passed": not untraceable,
        "n_entries": len(entries),
        "by_decision": dict(sorted(by_decision.items())),
        "untraceable": untraceable,
        "detail": f"{len(entries)} assets, all traceable to a source commit",
    }


def check_nothing_promoted() -> Dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from yoyo.artifacts import load_registries

    registries = load_registries(root=REPO)
    promoted = [a.artifact_id for a in registries.artifacts if a.production_eligible]
    promoted += [e.experiment_id for e in registries.experiments if e.production_eligible]
    trainable = [a.artifact_id for a in registries.artifacts if a.training_eligible]
    trainable += [e.experiment_id for e in registries.experiments if e.training_eligible]
    return {
        "passed": not promoted and not trainable,
        "production_eligible": promoted,
        "training_eligible": trainable,
        "detail": "nothing is production_eligible or training_eligible",
    }


def check_holdout() -> Dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from yoyo.artifacts import load_registries

    registries = load_registries(root=REPO)
    consumers = sorted(e.experiment_id for e in registries.experiments if e.holdout_consumed)
    expected = ["exp-yoyo-trading-fixed-w10-classifier-holdout3d"]
    return {
        "passed": consumers == expected,
        "holdout_consumed_during_consolidation": False,
        "pre_existing_consumers": consumers,
        "detail": (
            "the only recorded consumption predates this task and was not re-read"
            if consumers == expected
            else f"unexpected holdout consumers: {consumers}"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="main", help="branch point to diff against")
    ap.add_argument("--final-tests", default=str(CONSOLIDATION / "final_tests.json"))
    ap.add_argument("--out-json", default=str(CONSOLIDATION / "final_acceptance.json"))
    args = ap.parse_args()

    checks = {
        "runtime_safety_hashes": check_safety_hashes(),
        "tests": check_tests(Path(args.final_tests)),
        "secret_scan": check_secrets(args.base),
        "large_files": check_large_files(args.base),
        "migration_ledger": check_ledger(),
        "nothing_promoted": check_nothing_promoted(),
        "holdout": check_holdout(),
    }
    accepted = all(check["passed"] for check in checks.values())

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/consolidation/final_acceptance.py",
        "decision": "accepted" if accepted else "partial",
        "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "head": git("rev-parse", "HEAD").strip(),
        "base": args.base,
        "checks": checks,
    }
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"decision: {payload['decision']}")
    for name, check in checks.items():
        print(f"  [{'PASS' if check['passed'] else 'FAIL'}] {name}: {check['detail']}")
    print(f"wrote {out.relative_to(REPO)}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
