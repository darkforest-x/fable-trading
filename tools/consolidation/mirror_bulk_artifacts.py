"""Bring the source repositories' bulk artifacts into this tree, without git.

Owner instruction, 2026-08-20: move everything over and keep the large files out
of git. So the 20.5 GB of rendered frames, scan outputs, run directories and raw
bars land under archive/consolidated/<repo>/, and .gitignore excludes the tree
except for its README and this script's manifest.

Uses APFS clonefile (`cp -c`). On a copy-on-write filesystem the clone is
near-instant and costs almost no additional space, so the source repositories
stay intact -- which matters, because several REFERENCE_ONLY ledger entries
point back at them by commit and hash.

Skips what is already here. The four datasets yolo-xx shares with this
repository were verified byte-identical apart from data.yaml, whose difference
is only the repository path prefix baked into it; copying them would add 1.6 GB
of duplicate for nothing.

Writes MANIFEST.json: per-repo file counts, byte totals and the source commit,
so 20 GB of otherwise anonymous files can say where each came from.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

DEST_ROOT = Path.home() / "fable-trading" / "archive" / "consolidated"

#: repo -> directories to mirror. Chosen from the coverage audit's exclusion
#: classes: everything the ledger did not migrate, minus noise.
PLAN: Dict[str, List[str]] = {
    "yolo-xx": ["datasets", "reports", "data", "runs", "weights", "build"],
    "yoyo-trading": ["datasets", "runs", "reviews"],
    "yoyo-eth": ["reports", "artifacts"],
    "darkforest-one": ["data"],
}

#: Present in this repository already, verified byte-identical except data.yaml.
SKIP_DATASETS = {
    "dense_owner_short_star_tip_v10",
    "dense_owner_side_short_tip_v3",
    "eth_3m_short_pilot_v1",
    "eth_short_tip_label2000",
}

SKIP_NAMES = {".DS_Store"}


def source_commit(root: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def clone_file(src: Path, dst: Path) -> None:
    """APFS clone when available, plain copy otherwise."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        subprocess.run(["cp", "-c", str(src), str(dst)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        shutil.copy2(src, dst)


def mirror(repo: str, subdirs: List[str], dry_run: bool) -> Dict[str, object]:
    root = Path.home() / repo
    if not root.is_dir():
        return {"present": False}

    files = 0
    total_bytes = 0
    skipped_duplicate = 0
    per_dir: Dict[str, Dict[str, int]] = {}

    for subdir in subdirs:
        src_root = root / subdir
        if not src_root.is_dir():
            continue
        count = 0
        size = 0
        for src in src_root.rglob("*"):
            if not src.is_file() or src.is_symlink():
                continue
            if src.name in SKIP_NAMES:
                continue
            rel = src.relative_to(src_root)
            if repo == "yolo-xx" and subdir == "datasets" and rel.parts[0] in SKIP_DATASETS:
                skipped_duplicate += 1
                continue
            dst = DEST_ROOT / repo / subdir / rel
            if not dry_run:
                clone_file(src, dst)
            count += 1
            size += src.stat().st_size
        if count:
            per_dir[subdir] = {"files": count, "bytes": size}
        files += count
        total_bytes += size

    return {
        "present": True,
        "source_commit": source_commit(root),
        "files": files,
        "bytes": total_bytes,
        "skipped_already_present": skipped_duplicate,
        "per_directory": per_dir,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run:
        DEST_ROOT.mkdir(parents=True, exist_ok=True)

    repos = {}
    for repo, subdirs in PLAN.items():
        print(f"mirroring {repo} ...", flush=True)
        repos[repo] = mirror(repo, subdirs, args.dry_run)
        r = repos[repo]
        if r.get("present"):
            print(
                f"  {r['files']:,} files, {r['bytes'] / 2**30:.2f} GiB"
                f" (skipped {r['skipped_already_present']:,} already present)"
            )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/consolidation/mirror_bulk_artifacts.py",
        "note": (
            "Bulk artifacts cloned from the four archived source repositories. Not in "
            "git: .gitignore excludes archive/consolidated/** apart from README.md and "
            "this manifest. Curated text from these repositories was migrated "
            "separately with provenance -- see reports/consolidation/migration_ledger.jsonl."
        ),
        "totals": {
            "files": sum(r.get("files", 0) for r in repos.values()),
            "bytes": sum(r.get("bytes", 0) for r in repos.values()),
        },
        "skipped_because_already_present": sorted(SKIP_DATASETS),
        "repositories": repos,
    }
    if not args.dry_run:
        (DEST_ROOT / "MANIFEST.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(
        f"\ntotal {payload['totals']['files']:,} files, "
        f"{payload['totals']['bytes'] / 2**30:.2f} GiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
