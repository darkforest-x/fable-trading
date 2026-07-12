"""Round-2 golden analysis: owner self-consistency (20 repeats), fresh-image
disagreement vs rules, and the consolidated golden pool for training.

Outputs:
  analysis/output/golden_round2.json    (all three sections)
  data/golden_pool.json                 (stem -> owner boxes, round2 wins on repeats)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from golden_disagreement import iou, rects  # noqa: E402

R1 = PROJECT_DIR / "output/label_studio/export_round1.json"
R2 = PROJECT_DIR / "output/label_studio/export_round2.json"
R2_TASKS = PROJECT_DIR / "output/label_studio/tasks_val_round2.json"
OUT = PROJECT_DIR / "analysis/output/golden_round2.json"
POOL = PROJECT_DIR / "data/golden_pool.json"


def owner_boxes(export_path: Path) -> dict[str, list]:
    out: dict[str, list] = {}
    for t in json.loads(export_path.read_text()):
        stem = t.get("data", {}).get("stem")
        if stem and t.get("annotations"):
            key = stem
            if t["data"].get("repeat_of_round1"):
                key = f"{stem}@repeat"
            out[key] = rects(t["annotations"][0])
    return out


def match_f1(a: list, b: list, thr: float) -> tuple[int, int, int]:
    used = set()
    tp = 0
    for x in a:
        m = next((k for k, y in enumerate(b) if k not in used and iou(x, y) >= thr), None)
        if m is not None:
            used.add(m)
            tp += 1
    return tp, len(a) - tp, len(b) - tp


def main() -> int:
    r1 = owner_boxes(R1)
    r2 = owner_boxes(R2)
    repeats = {k.split("@")[0]: v for k, v in r2.items() if k.endswith("@repeat")}
    fresh = {k: v for k, v in r2.items() if not k.endswith("@repeat")}

    cons = {}
    for thr in (0.5, 0.3):
        tp = fn = fp = 0
        for stem, b2 in repeats.items():
            b1 = r1.get(stem, [])
            t, f1_, f2_ = match_f1(b1, b2, thr)
            tp += t; fn += f1_; fp += f2_
        prec = tp / max(tp + fp, 1); rec = tp / max(tp + fn, 1)
        cons[f"iou{int(thr*100)}"] = {
            "f1": round(2*prec*rec/max(prec+rec, 1e-9), 3),
            "r1_boxes": tp + fn, "r2_boxes": tp + fp, "matched": tp}

    rule_by_stem = {t["data"].get("stem"): rects(t["predictions"][0])
                    for t in json.loads(R2_TASKS.read_text()) if t.get("predictions")}
    c = Counter()
    for stem, ob in fresh.items():
        rb = rule_by_stem.get(stem, [])
        tp, fn_owner_extra, _ = match_f1(rb, ob, 0.3)
        c["rule_boxes"] += len(rb)
        c["kept"] += tp
        c["deleted"] += len(rb) - tp
        c["added"] += len(ob) - tp
        c["owner_boxes"] += len(ob)

    pool = {**{k: v for k, v in r1.items()}, **fresh}
    pool.update(repeats)  # round-2 verdict wins on repeated stems
    POOL.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")

    result = {
        "self_consistency_20_repeats": cons,
        "round2_fresh_vs_rules": {
            "images": len(fresh), **dict(c),
            "delete_rate": round(c["deleted"] / max(c["rule_boxes"], 1), 3),
        },
        "golden_pool": {"images": len(pool),
                        "boxes": sum(len(v) for v in pool.values()),
                        "backgrounds": sum(1 for v in pool.values() if not v)},
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
