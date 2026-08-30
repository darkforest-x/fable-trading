"""Scan the 5m Binance archive for MA-launch patterns with the frozen gates.

The 15m pool is capped at 1,043 distinct patterns and the 8,000 training
images are those patterns re-rendered at seven or eight horizontal positions,
which is why the detector saturates by epoch 4-6. Relaxing the gates reaches
only ~4,330 and changes what the pattern means; a finer timeframe adds
patterns without touching the definition.

The morphology gate, reference family and hard gates are reused unchanged from
the 15m chain, so a 5m candidate is scored by exactly the same rule. What is
NOT claimed: that a 5m pattern is the same tradeable object as a 15m one. The
bar duration under every ATR, barrier and confirmation window is three times
shorter, so these are a separate population and must be trained and evaluated
as one until something shows they can be pooled.

Holdout is cut by prefix before any scan, and nothing here writes a training
dataset -- the output is a candidate manifest.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.datasets.ma_launch_owner_autofill10000 import (  # noqa: E402
    load_reference_profiles, scan_source,
)
from yoyo.datasets.ma_launch_owner_perfect_filter import (  # noqa: E402
    extract_profile, hard_gate_failures,
)
from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features, read_preholdout_prefix,
)
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402

AUTOFILL_PREREG = ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
PERFECT_PREREG = ROOT / "experiments/active/exp-15m-ma-launch-owner-perfect-filter10000-v1/preregistration.json"
SERIES = ROOT / "data/kline_preholdout_binance_um5m/series"
OUT = ROOT / "analysis/output/ma_launch_5m_candidates_20260830"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    prereg = json.loads(AUTOFILL_PREREG.read_text())
    gates = json.loads(PERFECT_PREREG.read_text())["hard_gates"]
    references, _ = load_reference_profiles(prereg)
    files = sorted(SERIES.glob("*.csv"))
    if args.limit:
        files = files[: args.limit]
    print(f"reference profiles {len(references)}   5m series {len(files)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    stats: Counter[str] = Counter()
    handle = (OUT / "candidates_5m.jsonl").open("w")
    kept = 0
    for i, path in enumerate(files, 1):
        symbol = path.name.split("binance_um_")[1].rsplit("_5m_", 1)[0]
        try:
            frame, _ = read_preholdout_prefix(path, end_exclusive=HOLDOUT_START, bar_minutes=5)
        except Exception as exc:  # noqa: BLE001 - one bad series must not stop the sweep
            stats[f"unreadable: {type(exc).__name__}"] += 1
            continue
        if len(frame) < 400:
            stats["too short"] += 1
            continue
        try:
            rows, _ = scan_source(frame, source_path=str(path.relative_to(ROOT)),
                                  symbol=symbol, prereg=prereg, references=references)
        except Exception as exc:  # noqa: BLE001
            stats[f"scan failed: {type(exc).__name__}"] += 1
            continue
        stats["scanned"] += 1
        stats["raw candidates"] += len(rows)
        # scan_source stops at the coarse morphology stage; the strict metrics
        # the hard gates read are produced by the perfect-filter profile, so
        # they must be extracted here rather than looked up on the row.
        enriched = add_candidate_features(frame)
        for row in rows:
            try:
                metrics = extract_profile(enriched, row, bar_minutes=5).metrics
            except Exception:  # noqa: BLE001 - a bad row must not stop the sweep
                stats["profile failed"] += 1
                continue
            if not hard_gate_failures(metrics, gates):
                handle.write(json.dumps({**row, "strict_metrics": metrics,
                                         "symbol": symbol, "venue": "binance_um",
                                         "timeframe": "5m"}, ensure_ascii=False, default=str) + "\n")
                kept += 1
        if i % 25 == 0:
            print(f"  {i}/{len(files)} symbols, {kept} passing", flush=True)
    handle.close()

    print("\n=== result ===")
    for key, value in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {key:30} {value}")
    print(f"  patterns passing every hard gate: {kept}")
    print(f"wrote {OUT / 'candidates_5m.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
