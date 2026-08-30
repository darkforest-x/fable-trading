"""Scan the OKX symbols the Grade-A pool never covered.

The pool holds 1,043 distinct patterns, and the 8,000 training images are
those 1,043 rendered at seven or eight horizontal positions each. Relaxing the
shape gates only reaches ~4,330 while changing what the pattern means, so more
source coverage is the honest way to add patterns.

Of the 456 OKX 15m series on disk, 229 were already scanned through the
autofill/perfect-filter chain. This scans the rest with the same frozen
morphology gate and the same reference family, reusing
ma_launch_owner_autofill10000.scan_source rather than a second scanner, so a
candidate found here is the same object as a candidate found there.

Honest limits, stated before the run rather than after:
  * these series start around 2025-06, so each contributes roughly eleven
    usable months once the holdout prefix cut removes everything from
    2026-05-04 onward;
  * shallow history means few complete patterns per symbol, so the expected
    yield is on the order of a hundred, not thousands.

Nothing is written into any training dataset. The output is a candidate
manifest for review.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.datasets.ma_launch_owner_autofill10000 import (  # noqa: E402
    load_reference_profiles,
    scan_source,
)
from yoyo.datasets.ma_launch_owner_perfect_filter import hard_gate_failures  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402

AUTOFILL_PREREG = ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
PERFECT_PREREG = ROOT / "experiments/active/exp-15m-ma-launch-owner-perfect-filter10000-v1/preregistration.json"
GRADE_A_MANIFEST = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl"
OKX_DIR = ROOT / "data/kline_fetched"
OUT = ROOT / "analysis/output/okx_extra_scan_20260830"


def already_scanned() -> set[str]:
    """Symbols the frozen pool already drew from, in either venue."""
    seen: set[str] = set()
    ranked = ROOT / "experiments/active/exp-15m-ma-launch-owner-perfect-filter10000-v1/results/ranked_manifest.jsonl"
    for line in ranked.read_text().splitlines():
        if line.strip():
            seen.add(str(json.loads(line).get("symbol")))
    for line in GRADE_A_MANIFEST.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("sample_kind") == "positive":
                seen.add(str(row.get("symbol")))
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    prereg = json.loads(AUTOFILL_PREREG.read_text())
    gates = json.loads(PERFECT_PREREG.read_text())["hard_gates"]
    references, _ = load_reference_profiles(prereg)
    print(f"reference profiles: {len(references)}")

    skip = already_scanned()
    files = sorted(OKX_DIR.glob("okx_*_15m_*.csv"))
    todo = []
    for path in files:
        symbol = path.name.split("okx_")[1].rsplit("_15m_", 1)[0]
        if symbol not in skip:
            todo.append((symbol, path))
    print(f"OKX series on disk {len(files)}, already covered {len(files)-len(todo)}, to scan {len(todo)}")
    if args.limit:
        todo = todo[: args.limit]

    OUT.mkdir(parents=True, exist_ok=True)
    candidates: list[dict] = []
    stats: Counter[str] = Counter()
    for i, (symbol, path) in enumerate(todo, 1):
        try:
            frame, _ = read_preholdout_prefix(path, end_exclusive=HOLDOUT_START)
        except Exception as exc:  # noqa: BLE001 - a bad series must not kill the sweep
            stats[f"unreadable: {type(exc).__name__}"] += 1
            continue
        if len(frame) < 400:
            stats["too short after holdout cut"] += 1
            continue
        try:
            rows, _ = scan_source(frame, source_path=str(path.relative_to(ROOT)),
                                  symbol=symbol, prereg=prereg, references=references)
        except Exception as exc:  # noqa: BLE001
            stats[f"scan failed: {type(exc).__name__}"] += 1
            continue
        stats["scanned"] += 1
        for row in rows:
            metrics = row.get("strict_metrics")
            if not metrics:
                continue
            failures = hard_gate_failures(metrics, gates)
            if not failures:
                candidates.append({**row, "symbol": symbol, "venue": "okx"})
        if i % 50 == 0:
            print(f"  {i}/{len(todo)} symbols, {len(candidates)} passing so far", flush=True)

    with (OUT / "okx_extra_candidates.jsonl").open("w") as handle:
        for row in candidates:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    print("\n=== result ===")
    for key, value in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {key:34} {value}")
    print(f"  new patterns passing every hard gate: {len(candidates)}")
    print(f"  distinct symbols contributing: {len({c['symbol'] for c in candidates})}")
    print(f"wrote {OUT / 'okx_extra_candidates.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
