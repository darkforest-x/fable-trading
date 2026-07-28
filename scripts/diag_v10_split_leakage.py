"""Does anything cross the v10 train/val boundary? The split has already failed once.

The first v10 build put all 276 owner-reviewed hard negatives on the val side, so
the model trained on none of them and was merely tested on them. That was found by
counting files, not by any error. The same class of defect can run the other way --
material shared across the split, which inflates val and hides it.

Four ways it can leak here, checked separately because they fail differently:

  IDENTICAL   the same rendered image in both splits, by content hash. Would make
              val partly a memory test.
  SAME BAR    the same symbol and bar index on both sides under different tags. A
              positive in train and a negative in val on the same bar is worse
              than a duplicate: it is a contradiction.
  OVERLAP     windows are 200 bars, so two samples on the same symbol within 200
              bars share most of their pixels even when their anchor bars differ.
              Train/val pairs closer than that are near-duplicates.
  TIME        the split is by VAL_CUT, and a val window reaching back across it
              contains bars the model trained on. Unavoidable at the boundary,
              but its size should be known rather than assumed.

Read-only against the dataset. Reports; changes nothing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v10_split_leakage.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

DS = PROJECT / "datasets" / "dense_owner_short_star_tip_v10"
WINDOW = 200
TAGS = ("neghard", "negv9", "negrand")


def parse(stem: str) -> tuple[str, str, int] | None:
    """(tag, symbol, bar) from a filename; positives carry no tag prefix."""
    tag = ""
    rest = stem
    for t in TAGS:
        if stem.startswith(t + "_"):
            tag, rest = t, stem[len(t) + 1:]
            break
    m = re.match(r"^(.*)_(\d+)$", rest)
    if not m:
        return None
    return tag or "pos", m.group(1), int(m.group(2))


def main() -> int:
    if not DS.exists():
        print(f"数据集不存在: {DS}")
        return 2

    files = {}
    for split in ("train", "val"):
        for p in sorted((DS / "images" / split).glob("*.png")):
            files[(split, p.stem)] = p

    print(f"train {sum(1 for s,_ in files if s=='train')} · "
          f"val {sum(1 for s,_ in files if s=='val')}\n")

    # 1) identical content
    by_hash: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (split, stem), p in files.items():
        h = hashlib.md5(p.read_bytes()).hexdigest()
        by_hash[h].append((split, stem))
    dup_cross = [v for v in by_hash.values()
                 if len({s for s, _ in v}) > 1]
    dup_within = [v for v in by_hash.values()
                  if len(v) > 1 and len({s for s, _ in v}) == 1]
    print(f"① 完全相同的图(内容哈希)")
    print(f"   跨 train/val: {len(dup_cross)} 组"
          + (f"  例: {dup_cross[0][:2]}" if dup_cross else ""))
    print(f"   同一 split 内重复: {len(dup_within)} 组")

    # 2) same symbol+bar on both sides
    where: dict[tuple[str, int], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for (split, stem) in files:
        info = parse(stem)
        if info:
            tag, sym, bar = info
            where[(sym, bar)][split].append(tag)
    same_bar = {k: v for k, v in where.items() if len(v) > 1}
    contradict = {k: v for k, v in same_bar.items()
                  if ("pos" in v.get("train", []) + v.get("val", [])
                      and any(t.startswith("neg") for t in
                              v.get("train", []) + v.get("val", [])))}
    print(f"\n② 同一币同一 bar 出现在两侧: {len(same_bar)} 处")
    print(f"   其中正负矛盾(一侧正样本、另一侧负样本): {len(contradict)} 处")
    for k, v in list(contradict.items())[:3]:
        print(f"     {k[0]} bar {k[1]}: {dict(v)}")

    # 3) near-duplicate windows: same symbol, anchors within WINDOW bars
    tr = defaultdict(list)
    va = defaultdict(list)
    for (split, stem) in files:
        info = parse(stem)
        if not info:
            continue
        _tag, sym, bar = info
        (tr if split == "train" else va)[sym].append(bar)
    near = 0
    worst = []
    for sym, vbars in va.items():
        tb = sorted(tr.get(sym, []))
        if not tb:
            continue
        import bisect
        for b in vbars:
            i = bisect.bisect_left(tb, b)
            for j in (i - 1, i):
                if 0 <= j < len(tb):
                    d = abs(tb[j] - b)
                    if d < WINDOW:
                        near += 1
                        worst.append((d, sym, b, tb[j]))
                        break
    worst.sort()
    print(f"\n③ 窗口重叠(同币,train/val 锚点相距 <{WINDOW} 根)")
    print(f"   val 中有 {near} / {sum(len(v) for v in va.values())} 个样本与 train 重叠"
          f" = {100*near/max(sum(len(v) for v in va.values()),1):.1f}%")
    for d, sym, b, t in worst[:3]:
        print(f"     {sym}: val bar {b} 与 train bar {t} 相距 {d} 根")

    verdict_parts = []
    if dup_cross:
        verdict_parts.append(f"{len(dup_cross)} 组图片跨切分完全相同")
    if contradict:
        verdict_parts.append(f"{len(contradict)} 处同 bar 正负矛盾")
    ov = 100 * near / max(sum(len(v) for v in va.values()), 1)
    if ov > 5:
        verdict_parts.append(f"{ov:.1f}% 的 val 样本与 train 窗口重叠")
    verdict = ("未发现跨切分泄漏" if not verdict_parts
               else "发现泄漏:" + "、".join(verdict_parts))
    print(f"\n判读: {verdict}")

    (PROJECT / "analysis" / "output" / "diag_v10_split_leakage.json").write_text(
        json.dumps({"dataset": DS.name,
                    "identical_cross_split": len(dup_cross),
                    "identical_within_split": len(dup_within),
                    "same_bar_both_sides": len(same_bar),
                    "pos_neg_contradiction": len(contradict),
                    "val_overlapping_train_pct": round(ov, 2),
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
