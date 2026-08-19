"""Render the machine-readable views of what was decided about each source asset.

Two outputs, both derived rather than typed, so neither can drift from the
ledger and the registries they summarise:

  docs/consolidation/source_asset_registry.json
      every asset that crossed (or deliberately did not cross) a repository
      boundary, with its decision, provenance and hashes. Task book section 7.

  experiments/historical/<repo>/summary.json
      the machine summary the task book requires next to each historical
      README: the experiments registered from that repository, their verdicts,
      the reports migrated from it, and its holdout position.

Derived, not authored: the ledger and the two registries are the sources of
truth. If this file and they disagree, they win, and re-running fixes it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "reports" / "consolidation" / "migration_ledger.jsonl"
SNAPSHOTS = REPO / "reports" / "consolidation" / "source_repo_snapshots.json"

#: repo slug -> directory name under experiments/historical/
HISTORICAL_DIRS = {
    "darkforest-x/darkforest-one": "darkforest_one",
    "darkforest-x/yolo-xx": "yolo_xx",
    "darkforest-x/yoyo-trading": "yoyo_trading",
    "darkforest-x/yoyo-eth": "yoyo_eth",
}


def load_ledger() -> List[Dict[str, Any]]:
    if not LEDGER.is_file():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_registries() -> Dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from yoyo.artifacts import load_registries as _load

    registries = _load(root=REPO)
    return {
        "artifacts": [record.__dict__ for record in registries.artifacts],
        "experiments": [record.__dict__ for record in registries.experiments],
    }


def build_asset_registry(entries: List[Dict[str, Any]], registries: Dict[str, Any]) -> Dict[str, Any]:
    by_decision: Dict[str, int] = defaultdict(int)
    by_repo: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for entry in entries:
        by_decision[entry["decision"]] += 1
        by_repo[entry["source_repo"]][entry["decision"]] += 1

    snapshots = {}
    if SNAPSHOTS.is_file():
        payload = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
        snapshots = {
            snap["repo"]: snap.get("head_sha")
            for snap in payload.get("repositories", [])
            if snap.get("present")
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/consolidation/build_source_asset_registry.py",
        "holdout_consumed_during_consolidation": False,
        "source_head_shas": snapshots,
        "totals": {
            "assets_recorded": len(entries),
            "by_decision": dict(sorted(by_decision.items())),
            "by_repo": {repo: dict(sorted(counts.items())) for repo, counts in sorted(by_repo.items())},
            "artifacts_registered": len(registries["artifacts"]),
            "experiments_registered": len(registries["experiments"]),
        },
        "assets": sorted(
            entries, key=lambda e: (e["source_repo"], e["source_path"])
        ),
    }


def build_repo_summary(
    repo_slug: str, entries: List[Dict[str, Any]], registries: Dict[str, Any]
) -> Dict[str, Any]:
    mine = [e for e in entries if e["source_repo"] == repo_slug]
    experiments = [e for e in registries["experiments"] if e["source_repo"] == repo_slug]
    artifacts = [a for a in registries["artifacts"] if a["source_repo"] == repo_slug]
    head = ""
    for entry in mine:
        head = entry["source_commit"]
        break

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/consolidation/build_source_asset_registry.py",
        "source_repo": repo_slug,
        "source_commit": head,
        "experiments": [
            {
                "experiment_id": e["experiment_id"],
                "status": e["status"],
                "question": e["question"],
                "result": e["result"],
                "single_variable": e.get("single_variable"),
                "holdout_consumed": e["holdout_consumed"],
                "training_eligible": e["training_eligible"],
                "production_eligible": e["production_eligible"],
                "canonical_report": e.get("canonical_report"),
                "reuse_allowed": e.get("reuse_allowed"),
            }
            for e in sorted(experiments, key=lambda x: x["experiment_id"])
        ],
        "artifacts": [
            {
                "artifact_id": a["artifact_id"],
                "artifact_type": a["artifact_type"],
                "role": a["role"],
                "sha256": a["sha256"],
                "holdout_status": a["holdout_status"],
                "training_eligible": a["training_eligible"],
                "production_eligible": a["production_eligible"],
            }
            for a in sorted(artifacts, key=lambda x: x["artifact_id"])
        ],
        "migrated_files": {
            decision: sorted(
                e["destination_path"] or e["source_path"]
                for e in mine
                if e["decision"] == decision
            )
            for decision in sorted({e["decision"] for e in mine})
        },
        "holdout_consumed_by_this_repo": sorted(
            e["experiment_id"] for e in experiments if e["holdout_consumed"]
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the outputs are stale")
    args = ap.parse_args()

    entries = load_ledger()
    registries = load_registries()

    outputs: Dict[Path, Dict[str, Any]] = {
        REPO / "docs" / "consolidation" / "source_asset_registry.json": build_asset_registry(
            entries, registries
        )
    }
    for repo_slug, directory in HISTORICAL_DIRS.items():
        target = REPO / "experiments" / "historical" / directory / "summary.json"
        outputs[target] = build_repo_summary(repo_slug, entries, registries)

    stale = []
    for path, payload in outputs.items():
        rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        if args.check:
            if not path.is_file():
                stale.append(f"{path.relative_to(REPO)} (missing)")
                continue
            existing = json.loads(path.read_text(encoding="utf-8"))
            fresh = json.loads(rendered)
            # generated_at always differs; compare everything else
            existing.pop("generated_at", None)
            fresh.pop("generated_at", None)
            if existing != fresh:
                stale.append(str(path.relative_to(REPO)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")

    if args.check:
        if stale:
            print("STALE (re-run without --check):", ", ".join(stale), file=sys.stderr)
            return 1
        print(f"all {len(outputs)} generated views are current")
        return 0

    for path in outputs:
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
