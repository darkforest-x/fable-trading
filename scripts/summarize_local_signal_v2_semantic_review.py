#!/usr/bin/env python3
"""Validate completed Owner verdicts and produce the blinded-source diagnosis."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.serve_local_signal_v2_semantic_review import load_verdicts


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_positive_semantic_review200_v2"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["owner_verdict"]) for row in rows)
    denominator = counts["YES"] + counts["NO"]
    return {
        "reviewed": len(rows),
        "YES": counts["YES"],
        "NO": counts["NO"],
        "SKIP": counts["SKIP"],
        "yes_rate_excluding_skip": counts["YES"] / denominator if denominator else None,
    }


def summarize(out_dir: Path) -> dict[str, Any]:
    manifest = read_jsonl(out_dir / "review_manifest.jsonl")
    pack_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    verdicts = load_verdicts(out_dir)
    if len(manifest) != 200 or len(verdicts) != 200:
        raise ValueError(f"review incomplete: manifest={len(manifest)} verdicts={len(verdicts)}")
    joined = []
    for row in manifest:
        item = dict(row)
        item["owner_verdict"] = verdicts[str(row["review_id"])]
        joined.append(item)
    positive = [row for row in joined if row["source_type"] == "positive_pool"]
    canary = [row for row in joined if row["source_type"] == "canary_candidate"]
    by_cohort = {
        cohort: metrics([row for row in canary if row["canary_cohort"] == cohort])
        for cohort in ("common_retained", "r2_new", "r1_suppressed")
    }
    result = {
        "protocol": pack_summary["protocol"],
        "positive_pool": metrics(positive),
        "canary_candidate": metrics(canary),
        "canary_by_internal_source_after_unblinding": by_cohort,
        "automatic_training_started": False,
        "holdout_read": False,
    }
    (out_dir / "owner_review_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(summarize(args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
