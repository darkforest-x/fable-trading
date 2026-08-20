"""Freeze the observable state of the five consolidation repositories.

Written for C0 of the single-repo consolidation task book. The point is not to
describe what the repositories *should* contain -- it is to record what they
actually contained at the moment migration started, so that every later claim
("this file came from yolo-xx at <sha>") can be checked against a frozen
reference rather than against memory.

Records per repository: origin, branch, HEAD sha, commit time, worktree
cleanliness, tracked-modification and untracked-file counts, tags, and the
detected test entry point. For the destination repository it additionally
records the runtime-safety hashes named in the task book section 3.2 -- ACTIVE
pointer, forward log, holdout ledger, cost contract, production config -- so
that C7 can prove they did not move.

Reads only. Never writes into a source repository, never runs `git stash`,
`reset` or `commit` anywhere.

Usage:
    python3 tools/consolidation/snapshot_repositories.py \
        --out-json reports/consolidation/source_repo_snapshots.json \
        --out-md   reports/consolidation/source_repo_snapshots.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# repo key -> (default local path, role)
REPOS: list[tuple[str, str, str]] = [
    ("fable-trading", "~/fable-trading", "destination"),
    ("darkforest-one", "~/darkforest-one", "source"),
    ("yolo-xx", "~/yolo-xx", "source"),
    ("yoyo-trading", "~/yoyo-trading", "source"),
    ("yoyo-eth", "~/yoyo-eth", "source"),
]

# Runtime-safety objects in the destination repo. Paths are relative to the
# destination repo root. Missing entries are recorded as null, not as an error:
# absence is itself a fact worth freezing.
SAFETY_PATHS: list[str] = [
    "models/ACTIVE",
    "models/ACTIVE_PREV",
    "models/owner_best.json",
    "models/active_bundle.example.json",
    "data/forward_log.csv",
    "data/forward_log_ma206.csv",
    "src/costs.py",
    "scripts/deploy_vps.sh",
    "scripts/deploy_vps_short_protocol.sh",
    "deploy/fable-forward.timer",
    "deploy/fable-live-health.service",
    "deploy/fable-live-health.timer",
]

TEST_ENTRY_CANDIDATES = [
    (".github/workflows/tests.yml", "python -m pytest tests -q"),
    ("Makefile", "make check"),
    ("pyproject.toml", "python -m pytest -q"),
]


def run_git(repo: Path, *args: str) -> str | None:
    """Return stripped stdout of a git command, or None if it fails."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_test_entry(repo: Path) -> str | None:
    for rel, command in TEST_ENTRY_CANDIDATES:
        if (repo / rel).exists():
            return command
    return None


def snapshot_repo(key: str, path: Path, role: str) -> dict[str, Any]:
    if not path.exists():
        return {"repo": key, "role": role, "path": str(path), "present": False}

    porcelain = run_git(path, "status", "--porcelain", "-uall") or ""
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    tracked_mods = [ln for ln in lines if not ln.startswith("??")]
    untracked = [ln for ln in lines if ln.startswith("??")]

    snap: dict[str, Any] = {
        "repo": key,
        "role": role,
        "path": str(path),
        "present": True,
        "origin_url": run_git(path, "remote", "get-url", "origin"),
        "branch": run_git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        "head_sha": run_git(path, "rev-parse", "HEAD"),
        "head_commit_time": run_git(path, "log", "-1", "--format=%cI"),
        "head_subject": run_git(path, "log", "-1", "--format=%s"),
        "clean": not lines,
        "tracked_modification_count": len(tracked_mods),
        "untracked_file_count": len(untracked),
        "tracked_file_count": len((run_git(path, "ls-files") or "").splitlines()),
        "tags": [t for t in (run_git(path, "tag") or "").splitlines() if t],
        "test_entry": detect_test_entry(path),
    }
    if role == "destination":
        snap["safety_hashes"] = {
            rel: {
                "sha256": sha256_file(path / rel),
                "size_bytes": (path / rel).stat().st_size if (path / rel).is_file() else None,
            }
            for rel in SAFETY_PATHS
        }
    return snap


def render_markdown(payload: dict[str, Any]) -> str:
    out: list[str] = []
    out.append("# Source repository snapshots (C0)")
    out.append("")
    out.append(f"Generated at: `{payload['generated_at']}`")
    out.append("")
    out.append(
        "Frozen reference for the single-repository consolidation. Every migration "
        "decision recorded later cites the `head_sha` values below as its source of truth."
    )
    out.append("")
    out.append("## Repositories")
    out.append("")
    out.append("| repo | role | branch | HEAD | clean | tracked files | untracked |")
    out.append("|---|---|---|---|---|---|---|")
    for snap in payload["repositories"]:
        if not snap.get("present"):
            out.append(f"| `{snap['repo']}` | {snap['role']} | — | **MISSING** | — | — | — |")
            continue
        out.append(
            "| `{repo}` | {role} | `{branch}` | `{sha}` | {clean} | {tf} | {ut} |".format(
                repo=snap["repo"],
                role=snap["role"],
                branch=snap["branch"],
                sha=(snap["head_sha"] or "")[:12],
                clean="yes" if snap["clean"] else "**no**",
                tf=snap["tracked_file_count"],
                ut=snap["untracked_file_count"],
            )
        )
    out.append("")
    out.append("## Head commits")
    out.append("")
    for snap in payload["repositories"]:
        if not snap.get("present"):
            continue
        out.append(f"- `{snap['repo']}` `{snap['head_sha']}` ({snap['head_commit_time']})")
        out.append(f"  - {snap['head_subject']}")
    out.append("")
    out.append("## Destination runtime-safety hashes")
    out.append("")
    out.append(
        "These are the objects task book section 3.2 forbids changing. C7 re-hashes "
        "them and any difference must be explained item by item."
    )
    out.append("")
    out.append("| path | sha256 | size |")
    out.append("|---|---|---|")
    for snap in payload["repositories"]:
        for rel, info in (snap.get("safety_hashes") or {}).items():
            sha = info["sha256"]
            out.append(
                "| `{p}` | {s} | {z} |".format(
                    p=rel,
                    s=f"`{sha}`" if sha else "_absent_",
                    z=info["size_bytes"] if info["size_bytes"] is not None else "—",
                )
            )
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-json", default="reports/consolidation/source_repo_snapshots.json")
    ap.add_argument("--out-md", default="reports/consolidation/source_repo_snapshots.md")
    args = ap.parse_args()

    repos = []
    for key, default_path, role in REPOS:
        env_key = "CONSOLIDATION_REPO_" + key.replace("-", "_").upper()
        raw = os.environ.get(env_key, default_path)
        repos.append(snapshot_repo(key, Path(raw).expanduser(), role))

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holdout_consumed_during_consolidation": False,
        "repositories": repos,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(payload), encoding="utf-8")

    missing = [r["repo"] for r in repos if not r.get("present")]
    dirty = [r["repo"] for r in repos if r.get("present") and not r["clean"]]
    print(f"wrote {out_json} and {out_md}")
    if missing:
        print(f"MISSING repositories: {', '.join(missing)}")
    if dirty:
        print(f"DIRTY worktrees (migration uses committed HEAD only): {', '.join(dirty)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
