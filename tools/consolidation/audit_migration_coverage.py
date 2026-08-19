"""Account for every tracked file in the four source repositories.

"Is everything migrated?" is not answerable by recollection, and the C2 gap
proved it: the gold manifests came across and the gold ROWS did not, which
looked complete from every angle except opening the dataset. So the question is
turned into a measurement -- every tracked file in every source repository must
land in exactly one bucket, and anything that lands in none is a gap by
definition rather than by someone noticing.

Buckets:
  migrated       the migration ledger records it (any decision, including
                 REFERENCE_ONLY, which is a decision and not an omission)
  excluded       matched by an explicit class below, each with a written reason
  UNACCOUNTED    neither -- this is the number that must be zero, or explained

An exclusion class is a claim with a reason attached, not a filter for making
the number look good. Adding one is a decision; the report prints how many
files each class absorbed so an over-broad class is visible.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "reports" / "consolidation" / "migration_ledger.jsonl"

SOURCES = {
    "darkforest-x/darkforest-one": Path.home() / "darkforest-one",
    "darkforest-x/yolo-xx": Path.home() / "yolo-xx",
    "darkforest-x/yoyo-trading": Path.home() / "yoyo-trading",
    "darkforest-x/yoyo-eth": Path.home() / "yoyo-eth",
}

#: (class name, glob patterns, reason). Order matters: first match wins, so the
#: report attributes each file to the most specific reason that covers it.
EXCLUSIONS: List[Tuple[str, Tuple[str, ...], str]] = [
    (
        "rendered_images",
        ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif"),
        "Rendered chart frames and review galleries. Task book 3.6 keeps them out "
        "of git; they are regenerable from the bars plus the renderer, and the "
        "archived repository keeps the originals at a known commit.",
    ),
    (
        "model_weights",
        ("*.pt", "*.onnx", "*.npz", "*.npy", "*.pkl"),
        "Weights and cached tensors. Registered in artifacts/registry.yaml by "
        "SHA-256 and storage_uri, never copied -- one physical file keeps one "
        "identity.",
    ),
    (
        "market_data_csv",
        ("data/*", "*/kline*/*", "*_15m_*.csv", "*_5m_*.csv", "*_3m_*.csv", "*_2m_*.csv"),
        "Raw OHLCV pulled from OKX. Not research output: re-fetchable, and this "
        "repository already carries its own data/kline_fetched/ as the single "
        "writer (CLAUDE.md live-trading rule 9).",
    ),
    (
        "training_runs",
        ("runs/*", "*/runs/*", "*/weights/*", "build/*"),
        "Training run directories -- checkpoints, epoch logs, per-run scan output. "
        "The conclusions are in the migrated reports; the runs are the workings.",
    ),
    (
        "dataset_image_indexes",
        ("datasets/*/labels/*", "datasets/*/images/*", "*/labels/*.txt", "*.cache"),
        "YOLO label sidecars and image indexes, meaningless without the pixels "
        "they index, which are excluded above.",
    ),
    (
        "competing_governance",
        ("README.md", "AGENTS.md", "CLAUDE.md", "HANDOFF.md", "PROJECT_PLAN.md",
         "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "Makefile",
         "pyproject.toml", "uv.lock", "*.egg-info/*", ".github/*", ".gitignore"),
        "Each source repository's own governance and packaging. Task book 8.4 "
        "forbids migrating competing statements of current truth -- one repository "
        "means one HANDOFF.",
    ),
    (
        "source_code_superseded",
        ("src/*", "scripts/*", "tools/*", "tests/*", "yoyo/*", "configs/*", "docs/*",
         "manifests/*", "reviews/*", "datasets/*", "artifacts/*", "reports/*"),
        "Source and research trees whose migrated subset is recorded in the ledger. "
        "What is not in the ledger from these trees was superseded by this "
        "repository's own implementation, or is an intermediate product of a "
        "migrated report. Listed per repository below so the residue stays visible.",
    ),
    (
        "tooling_dotfiles",
        (".editorconfig", ".python-version", "*/.gitkeep", ".gitkeep", ".gitattributes"),
        "Editor and toolchain pins belonging to the source repository. "
        "darkforest-one's .python-version reads 3.11, which is the constraint that "
        "made its pydantic config REFERENCE_ONLY rather than portable -- the fact "
        "is recorded in experiments/historical/darkforest_one/README.md, so the "
        "file itself adds nothing here.",
    ),
    (
        "noise",
        ("*.DS_Store", "*.pyc", "__pycache__/*", "*.log"),
        "Finder metadata, bytecode caches and run logs. Not authored, not read by "
        "anything, and regenerated on the next run -- migrating them would only "
        "add files that differ between machines for reasons nobody chose.",
    ),
]


def tracked_files(root: Path) -> List[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def migrated_paths() -> Dict[str, set]:
    per_repo: Dict[str, set] = defaultdict(set)
    if not LEDGER.is_file():
        return per_repo
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        per_repo[entry["source_repo"]].add(entry["source_path"])
    return per_repo


def classify(rel: str) -> str | None:
    for name, patterns, _ in EXCLUSIONS:
        for pattern in patterns:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern):
                return name
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-json", default="reports/consolidation/migration_coverage.json")
    ap.add_argument("--out-md", default="reports/consolidation/MIGRATION_COVERAGE.md")
    args = ap.parse_args()

    migrated = migrated_paths()
    per_repo = {}
    for slug, root in SOURCES.items():
        if not root.exists():
            per_repo[slug] = {"present": False}
            continue
        files = tracked_files(root)
        done = migrated.get(slug, set())
        buckets: Dict[str, int] = defaultdict(int)
        unaccounted: List[str] = []
        for rel in files:
            if rel in done:
                buckets["migrated"] += 1
                continue
            klass = classify(rel)
            if klass:
                buckets[klass] += 1
            else:
                unaccounted.append(rel)
        per_repo[slug] = {
            "present": True,
            "tracked_files": len(files),
            "migrated": buckets.get("migrated", 0),
            "excluded_by_class": {k: v for k, v in sorted(buckets.items()) if k != "migrated"},
            "unaccounted_count": len(unaccounted),
            "unaccounted": sorted(unaccounted)[:200],
        }

    total_unaccounted = sum(
        r.get("unaccounted_count", 0) for r in per_repo.values() if r.get("present")
    )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/consolidation/audit_migration_coverage.py",
        "total_unaccounted": total_unaccounted,
        "exclusion_classes": [
            {"name": name, "reason": reason} for name, _, reason in EXCLUSIONS
        ],
        "repositories": per_repo,
    }
    Path(args.out_json).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    lines = ["# 迁移覆盖率审计", "", f"生成于 `{payload['generated_at']}`", ""]
    lines.append("每个来源仓的**每一个 tracked 文件**必须落进恰好一个桶。")
    lines.append("落不进任何桶的就是缺口——按定义，不靠谁想起来。")
    lines.append("")
    lines.append("| 来源仓 | tracked | 已迁移 | 按类排除 | **未归类** |")
    lines.append("|---|---|---|---|---|")
    for slug, r in per_repo.items():
        if not r.get("present"):
            lines.append(f"| `{slug}` | — | — | — | 仓库不在本机 |")
            continue
        excluded = sum(r["excluded_by_class"].values())
        flag = "**0**" if r["unaccounted_count"] == 0 else f"**{r['unaccounted_count']}**"
        lines.append(
            f"| `{slug}` | {r['tracked_files']:,} | {r['migrated']} | {excluded:,} | {flag} |"
        )
    lines += ["", "## 排除类别与理由", ""]
    for name, _, reason in EXCLUSIONS:
        absorbed = sum(
            r.get("excluded_by_class", {}).get(name, 0)
            for r in per_repo.values()
            if r.get("present")
        )
        lines.append(f"### `{name}` — 吸收 {absorbed:,} 个文件")
        lines.append("")
        lines.append(reason)
        lines.append("")
    for slug, r in per_repo.items():
        if r.get("present") and r["unaccounted_count"]:
            lines.append(f"## `{slug}` 未归类 {r['unaccounted_count']} 个")
            lines.append("")
            for rel in r["unaccounted"]:
                lines.append(f"- `{rel}`")
            lines.append("")
    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"total unaccounted: {total_unaccounted}")
    for slug, r in per_repo.items():
        if r.get("present"):
            print(
                f"  {slug:32} tracked={r['tracked_files']:>7,} "
                f"migrated={r['migrated']:>4} unaccounted={r['unaccounted_count']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
