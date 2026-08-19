"""Count the models that actually exist, by hash rather than by filename.

"How many models have we trained" has three different answers depending on what
you count, and quoting the wrong one is how a repository comes to believe it has
more evidence than it does:

    files       318 -- counts every copy, including stale worktrees
    distinct    111 -- deduplicated by SHA-256
    trained     107 -- distinct, minus the stock COCO bases

The gap between the first two is not clutter: one model is stored in fourteen
places, and a filename is not an identity. Only the hash is.

Excludes .claude/worktrees and vps_rescue by default: both hold copies of the
same weights, and counting them inflates every number.

Usage:
    python3 tools/consolidation/audit_models.py
    python3 tools/consolidation/audit_models.py --json reports/model_inventory.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parents[2]
DATA_ROOT = Path.home() / "fable-trading"

EXCLUDE_PARTS = (".claude", "vps_rescue", ".git")

#: Ultralytics ships these; they are nobody's training output.
STOCK_BASES = {"yolo11n.pt", "yolo11s.pt", "yolo11n-cls.pt", "yolo26n.pt", "base.pt"}

#: (family, pattern) tested against the lowercased path, first match wins.
FAMILIES = [
    ("COCO base", re.compile(r"yolo11|yolo26|/base\.pt$")),
    ("smallwin", re.compile(r"smallwin")),
    ("hardneg", re.compile(r"hardneg")),
    ("local_signal_v2", re.compile(r"local_signal_v2")),
    ("classifier", re.compile(r"classify|_cls")),
    ("side_short_tip", re.compile(r"side_short_tip")),
    ("owner_short_star", re.compile(r"short_star")),
    ("owner_v* main chain", re.compile(r"owner_v\d")),
    ("R3 gold finetune", re.compile(r"r3[ab]|v3gold")),
    ("paired A/B", re.compile(r"paired_ab|_ab_")),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def family_of(path: str) -> str:
    lowered = path.lower()
    for name, pattern in FAMILIES:
        if pattern.search(lowered):
            return name
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(DATA_ROOT))
    ap.add_argument("--json", dest="out_json")
    ap.add_argument(
        "--include-copies",
        action="store_true",
        help="do not exclude worktrees and vps_rescue (inflates every count)",
    )
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    weights = [
        p
        for p in root.rglob("*.pt")
        if args.include_copies or not any(part in EXCLUDE_PARTS for part in p.parts)
    ]

    by_hash: Dict[str, List[str]] = collections.defaultdict(list)
    for path in weights:
        try:
            by_hash[sha256(path)].append(str(path.relative_to(root)))
        except OSError:
            continue

    trained = {
        digest: paths
        for digest, paths in by_hash.items()
        if not any(Path(p).name in STOCK_BASES for p in paths)
    }
    families = collections.Counter(family_of(paths[0]) for paths in by_hash.values())

    # LightGBM: frozen artifacts plus per-experiment models
    lgb = sorted(
        str(p.relative_to(root))
        for p in list(root.glob("models/frozen_*.txt"))
        + list(root.glob("experiments/*/model.txt"))
        + list(root.glob("archive/consolidated/*/artifacts/**/*model.txt"))
    )

    bundle = root / "models" / "active_bundle.json"
    active_pointer = (root / "models" / "ACTIVE")

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/consolidation/audit_models.py",
        "root": str(root),
        "weight_files": len(weights),
        "distinct_weights": len(by_hash),
        "trained_weights": len(trained),
        "stock_bases": len(by_hash) - len(trained),
        "by_family": dict(families.most_common()),
        "most_duplicated": [
            {"sha256": d, "copies": len(p), "paths": p[:5]}
            for d, p in sorted(by_hash.items(), key=lambda kv: -len(kv[1]))[:5]
        ],
        "lightgbm_models": lgb,
        "lightgbm_count": len(lgb),
        "production": {
            "active_bundle_exists": bundle.is_file(),
            "active_pointer": active_pointer.read_text(encoding="utf-8").strip()
            if active_pointer.is_file()
            else None,
            "note": (
                "models/ACTIVE is a research pointer. Production authority is "
                "models/active_bundle.json via require_active_bundle(); when it is "
                "absent the executor fails closed and nothing trades."
            ),
        },
    }

    print(f"weight files       {payload['weight_files']:>5}")
    print(f"distinct (sha256)  {payload['distinct_weights']:>5}")
    print(f"  trained          {payload['trained_weights']:>5}")
    print(f"  stock COCO base  {payload['stock_bases']:>5}")
    print("\nby family:")
    for name, count in families.most_common():
        print(f"  {name:<22} {count:>4}")
    print(f"\nLightGBM models    {payload['lightgbm_count']:>5}")
    print(
        f"production bundle  {'present' if payload['production']['active_bundle_exists'] else 'ABSENT -> executor fails closed, nothing trades'}"
    )

    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
