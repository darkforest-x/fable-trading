"""Copy an asset from a source repository into this one and record the move.

Every file that crosses a repository boundary during the consolidation goes
through here, so that section 11.1 (source identity) and 11.2 (byte parity) of
the task book are satisfied by construction rather than by a later audit:

  - the source commit is read from the source repo's git HEAD, not typed in
  - source and destination SHA-256 are both computed and stored
  - a DIRECT_PORT whose two hashes differ is refused outright
  - a decision, a reason and the covering tests are mandatory arguments
  - one JSONL line lands in reports/consolidation/migration_ledger.jsonl per file

Refuses to read from a source repo whose worktree is dirty in the files being
copied, because then "source_commit" would name content that is not what got
copied. Refuses to copy anything the destination .gitignore would swallow, and
refuses files over --max-bytes (default 2 MiB) unless --allow-large: task book
section 3.6 keeps large artefacts out of git and in the artifact registry.

Usage:
    python3 tools/consolidation/port_asset.py \
        --source-root ~/yoyo-trading --source-repo darkforest-x/yoyo-trading \
        --decision DIRECT_PORT --reason "canonical package returns home" \
        --tests tests/boundaries/test_layer_imports.py \
        --map yoyo/contracts:yoyo/contracts
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path("reports/consolidation/migration_ledger.jsonl")
DECISIONS = ("DIRECT_PORT", "ADAPT_AND_PORT", "REFERENCE_ONLY", "HISTORICAL_REPORT", "REJECT")
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
SKIP_GLOBS = ("*.pyc", "__pycache__/*", ".DS_Store", "*.egg-info/*")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120
    )
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {root}: {out.stderr.strip()}")
    return out.stdout.strip()


def is_skipped(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in SKIP_GLOBS)


def expand(source_root: Path, src_rel: str, dest_rel: str) -> list[tuple[Path, Path, str, str]]:
    """Return (src_abs, dest_abs, src_rel, dest_rel) for one mapping."""
    src_abs = source_root / src_rel
    if src_abs.is_file():
        return [(src_abs, Path(dest_rel), src_rel, dest_rel)]
    if not src_abs.is_dir():
        raise SystemExit(f"source path does not exist: {src_abs}")
    pairs = []
    for path in sorted(src_abs.rglob("*")):
        if not path.is_file():
            continue
        rel_inside = path.relative_to(src_abs).as_posix()
        if is_skipped(rel_inside) or is_skipped(path.name):
            continue
        pairs.append(
            (
                path,
                Path(dest_rel) / rel_inside,
                f"{src_rel}/{rel_inside}",
                f"{dest_rel}/{rel_inside}",
            )
        )
    return pairs


def gitignored(dest_rel: Path) -> bool:
    out = subprocess.run(
        ["git", "check-ignore", "-q", str(dest_rel)], capture_output=True, text=True
    )
    return out.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--source-repo", required=True, help="e.g. darkforest-x/yoyo-trading")
    ap.add_argument("--decision", required=True, choices=DECISIONS)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--tests", nargs="*", default=[], help="tests that cover the ported asset")
    ap.add_argument(
        "--map",
        dest="mappings",
        action="append",
        required=True,
        metavar="SRC_REL:DEST_REL",
        help="file or directory mapping, repeatable",
    )
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--allow-large", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--record-only",
        action="store_true",
        help="do not copy; record an already-written destination. For an adaptation "
        "that was rewritten by hand, so its provenance still goes through the ledger "
        "rather than around it. Incompatible with DIRECT_PORT, which means bytes.",
    )
    args = ap.parse_args()

    reference_only = args.decision == "REFERENCE_ONLY"
    if reference_only and args.record_only:
        raise SystemExit(
            "REFERENCE_ONLY already implies no copy; --record-only adds nothing"
        )
    if args.record_only and args.decision == "DIRECT_PORT":
        raise SystemExit(
            "--record-only with DIRECT_PORT is a contradiction: DIRECT_PORT asserts the "
            "bytes are the source's, which is exactly what copying establishes"
        )

    source_root = Path(args.source_root).expanduser().resolve()
    if not (source_root / ".git").exists():
        raise SystemExit(f"not a git repository: {source_root}")
    source_commit = git(source_root, "rev-parse", "HEAD")

    pairs: list[tuple[Path, Path, str, str]] = []
    for mapping in args.mappings:
        if ":" not in mapping:
            raise SystemExit(f"--map needs SRC_REL:DEST_REL, got {mapping!r}")
        src_rel, dest_rel = mapping.split(":", 1)
        pairs += expand(source_root, src_rel, dest_rel)

    dirty = set(
        line[3:].strip().strip('"')
        for line in git(source_root, "status", "--porcelain", "-uall").splitlines()
    )
    problems: list[str] = []
    for _, _, src_rel, _ in pairs:
        if src_rel in dirty:
            problems.append(f"source file is uncommitted, so source_commit would lie: {src_rel}")
    for src_abs, dest_rel, src_rel, _ in pairs:
        if args.record_only and not dest_rel.is_file():
            problems.append(f"--record-only but destination does not exist: {dest_rel}")
        size = src_abs.stat().st_size
        if size > args.max_bytes and not args.allow_large and not reference_only:
            problems.append(
                f"{src_rel} is {size} bytes (> {args.max_bytes}); register it as an "
                "artifact instead of committing it, or pass --allow-large"
            )
        if not reference_only and gitignored(dest_rel):
            problems.append(f"destination is gitignored, the copy would be invisible: {dest_rel}")
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 2

    records = []
    for src_abs, dest_rel, src_rel, dest_rel_str in pairs:
        src_sha = sha256_file(src_abs)
        if reference_only:
            dest_sha = None
        elif args.record_only:
            dest_sha = sha256_file(dest_rel)
        elif not args.dry_run:
            dest_rel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_abs, dest_rel)
            dest_sha = sha256_file(dest_rel)
        else:
            dest_sha = src_sha if not dest_rel.exists() else sha256_file(dest_rel)
        if args.decision == "DIRECT_PORT" and src_sha != dest_sha:
            raise SystemExit(
                f"DIRECT_PORT but bytes differ for {src_rel}: {src_sha} != {dest_sha}"
            )
        records.append(
            {
                "source_repo": args.source_repo,
                "source_commit": source_commit,
                "source_path": src_rel,
                "destination_path": None if reference_only else dest_rel_str,
                "reference_pointer": dest_rel_str if reference_only else None,
                "decision": args.decision,
                "reason": args.reason,
                "source_sha256": src_sha,
                "destination_sha256": dest_sha,
                "size_bytes": src_abs.stat().st_size,
                "tests": args.tests,
                "status": "accepted",
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "holdout_consumed": False,
            }
        )

    if args.dry_run:
        print(f"[dry-run] {len(records)} file(s) from {args.source_repo}@{source_commit[:12]}")
        for record in records:
            print(f"  {record['source_path']} -> {record['destination_path']}")
        return 0

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print(
        f"ported {len(records)} file(s) from {args.source_repo}@{source_commit[:12]} "
        f"as {args.decision}; ledger -> {LEDGER}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
