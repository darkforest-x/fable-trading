"""Golden-set round-1 disagreement report: owner annotations vs rule prelabels.

Input: output/label_studio/export_round1.json (LS export; each task carries
the rule prelabels under predictions[] and the owner's verdict under
annotations[]). Boxes are LS percent rects (top-left origin) -> normalized
center format for IoU.

Classification per rule box / owner box pair (greedy IoU):
  IoU >= 0.80        accepted   (owner agrees, maybe cosmetic nudge)
  0.30 <= IoU < 0.80 reshaped   (same region, geometry corrected)
  rule box unmatched deleted    (rule false positive per human)
  owner box unmatched added     (rule miss per human)

Output: analysis/output/golden_round1.json + analysis/p2a_golden_round1.md
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
EXPORT = PROJECT_DIR / "output" / "label_studio" / "export_round1.json"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "golden_round1.json"
OUT_MD = PROJECT_DIR / "analysis" / "p2a_golden_round1.md"


def rects(entry) -> list[tuple[float, float, float, float]]:
    out = []
    for r in entry.get("result", []):
        v = r.get("value", {})
        if {"x", "y", "width", "height"} <= set(v):
            cx = (v["x"] + v["width"] / 2) / 100
            cy = (v["y"] + v["height"] / 2) / 100
            out.append((cx, cy, v["width"] / 100, v["height"] / 100))
    return out


def iou(a, b):
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / union if union > 0 else 0.0


def main() -> int:
    tasks = json.loads(EXPORT.read_text())
    # export carries prediction IDs only; take rule boxes from the import pack
    source = json.loads((PROJECT_DIR / "output/label_studio/tasks_val.json").read_text())
    rule_by_stem = {t["data"].get("stem"): rects(t["predictions"][0])
                    for t in source if t.get("predictions")}
    counts = Counter()
    width_ratio, x_shift = [], []
    per_image = []
    for t in tasks:
        stem = t.get("data", {}).get("stem", "?")
        rule = rule_by_stem.get(stem, [])
        owner = rects(t["annotations"][0]) if t.get("annotations") else []
        used_owner = set()
        img_events = []
        for rb in rule:
            best_j, best_i = None, 0.0
            for j, ob in enumerate(owner):
                if j in used_owner:
                    continue
                v = iou(rb, ob)
                if v > best_i:
                    best_j, best_i = j, v
            if best_j is not None and best_i >= 0.80:
                used_owner.add(best_j)
                counts["accepted"] += 1
            elif best_j is not None and best_i >= 0.30:
                used_owner.add(best_j)
                counts["reshaped"] += 1
                ob = owner[best_j]
                width_ratio.append(ob[2] / rb[2] if rb[2] > 0 else 1)
                x_shift.append(ob[0] - rb[0])
                img_events.append("reshape")
            else:
                counts["deleted"] += 1
                img_events.append("delete")
        added = len(owner) - len(used_owner)
        counts["added"] += added
        if added:
            img_events.append(f"add x{added}")
        if img_events:
            per_image.append({"stem": stem, "events": img_events})
        counts["rule_boxes"] += len(rule)
        counts["owner_boxes"] += len(owner)

    n_rule = counts["rule_boxes"] or 1
    summary = {
        "tasks": len(tasks),
        "rule_boxes": counts["rule_boxes"], "owner_boxes": counts["owner_boxes"],
        "accepted": counts["accepted"], "reshaped": counts["reshaped"],
        "deleted_rule_fp": counts["deleted"], "added_rule_fn": counts["added"],
        "accept_rate": round(counts["accepted"] / n_rule, 4),
        "delete_rate": round(counts["deleted"] / n_rule, 4),
        "reshape_stats": {
            "n": len(width_ratio),
            "median_width_ratio_owner_over_rule": round(
                sorted(width_ratio)[len(width_ratio)//2], 3) if width_ratio else None,
            "mean_x_shift_norm": round(sum(x_shift)/len(x_shift), 4) if x_shift else None,
        },
        "images_with_changes": len(per_image),
    }
    OUT_JSON.write_text(json.dumps({"summary": summary, "per_image": per_image},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 金标准 Round-1：owner vs 规则 分歧报告\n",
          f"任务 {summary['tasks']}，规则框 {summary['rule_boxes']}，owner 框 {summary['owner_boxes']}\n",
          f"- 直接认可 accepted: {summary['accepted']} ({summary['accept_rate']:.0%})",
          f"- 修形 reshaped: {summary['reshaped']}",
          f"- 删除（规则误标）deleted: {summary['deleted_rule_fp']} ({summary['delete_rate']:.0%})",
          f"- 新增（规则漏标）added: {summary['added_rule_fn']}",
          f"- 修形统计: {summary['reshape_stats']}",
          f"- 有改动的图: {summary['images_with_changes']}/{summary['tasks']}\n",
          "明细见 analysis/output/golden_round1.json"]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
