"""Re-label the Grade-A positives by what the trade actually did.

The frozen dataset calls 1,043 events positive because each one passed a
release gate -- post1/post2/post3/post5 progress above a floor -- which only
says the price started moving, never that the move paid. Resolving the frozen
TP5/SL2/72 barrier from the earliest honest entry shows 389 reached take
profit and 516 hit the stop. The stopped-out majority is currently taught to
the detector as the thing to find.

Box geometry does not explain the difference: winners and losers sit at the
same offset from every candidate MA-convergence anchor (all |rho| < 0.07
against net return in ATR units), so moving boxes cannot fix this and only the
label can.

New label:  tp -> positive, sl -> negative (empty label, image kept),
            timeout -> dropped, because 72 bars without touching either
            barrier is neither the pattern working nor failing and guessing
            would put noise on both sides.

Images are copied byte-for-byte. Only labels move, so a model trained here
differs from the frozen baseline in exactly one variable.
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
DST = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_outcome_v1"
OUTCOMES = ROOT / "analysis/output/grade_a_label_optimisation_20260830/event_outcomes_geometry.csv"


def main() -> int:
    outcome = pd.read_csv(OUTCOMES).set_index("event_id")["outcome"].to_dict()
    records = [json.loads(l) for l in (SRC / "manifest.jsonl").read_text().splitlines() if l.strip()]

    for split in ("train", "val"):
        (DST / "images" / split).mkdir(parents=True, exist_ok=True)
        (DST / "labels" / split).mkdir(parents=True, exist_ok=True)

    stats: Counter[str] = Counter()
    manifest = []
    for rec in records:
        img, lab = rec["image_path"], rec["label_path"]
        if rec.get("sample_kind") != "positive":
            shutil.copyfile(SRC / img, DST / img)
            (DST / lab).write_text("")
            stats["negative kept"] += 1
            manifest.append({**rec, "outcome_label": "negative", "barrier_outcome": None})
            continue

        result = outcome.get(rec["event_id"])
        if result == "tp":
            shutil.copyfile(SRC / img, DST / img)
            shutil.copyfile(SRC / lab, DST / lab)
            stats["positive kept (tp)"] += 1
            manifest.append({**rec, "outcome_label": "positive", "barrier_outcome": "tp"})
        elif result == "sl":
            shutil.copyfile(SRC / img, DST / img)
            (DST / lab).write_text("")
            stats["flipped to negative (sl)"] += 1
            manifest.append({**rec, "outcome_label": "negative", "barrier_outcome": "sl"})
        else:
            stats[f"dropped ({result or 'unsimulated'})"] += 1

    (DST / "manifest.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in manifest))
    yaml = (SRC / "data.yaml").read_text().replace(SRC.name, DST.name)
    (DST / "data.yaml").write_text(yaml)
    (DST / "build_receipt.json").write_text(json.dumps({
        "source_dataset": SRC.name,
        "output_dataset": DST.name,
        "rule": "tp -> positive, sl -> negative, timeout -> dropped",
        "barriers": {"tp_atr": 5.0, "sl_atr": 2.0, "horizon_bars": 72,
                     "entry": "close of core_end + 2 bars", "cost": 0.002},
        "images_byte_identical_to_source": True,
        "stats": dict(stats),
        "holdout_read": False,
        "training_eligible": False,
        "production_eligible": False,
    }, indent=2, ensure_ascii=False) + "\n")

    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:34} {v}")
    pos = stats["positive kept (tp)"]
    neg = stats["negative kept"] + stats["flipped to negative (sl)"]
    print(f"\n  正 {pos}  负 {neg}  比例 1:{neg/max(pos,1):.1f}")
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
