"""Sample the v10 training set into an HTML the owner can actually inspect.

The v9 round shipped three artefacts the owner had to debug by eye -- floating
boxes, long setups in a short-only pack, a control rendered differently from the
thing it controlled. Every one of them would have been caught by looking at a
sample before training, and none of them raised an error.

So this samples 5% of each population and draws what the model will actually be
fed: the image as it goes into YOLO, with its label box on top, grouped so the
three populations can be judged against each other rather than one at a time.

  POSITIVES       what the model is told to fire on
  HARD NEGATIVES  owner rejections and v9's own mistakes -- the cases that decide
                  whether it learns "looks like it but is not"
  EASY NEGATIVES  random non-dense bars, which teach almost nothing and are here
                  only so their share is visible

Hard and easy negatives are separated by filename tag rather than guessed from
the picture, since the whole point is to see how many of each the model gets.

Read-only. Writes HTML plus the drawn PNGs; touches no dataset file.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/audit_v10_dataset_html.py --pct 5
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import cv2

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

DS = PROJECT / "datasets" / "dense_owner_short_star_tip_v10"
OUT = PROJECT / "analysis" / "output" / "v10_dataset_audit"
SEED = 20260728


def classify(stem: str, meta: dict) -> str:
    """Which population a file belongs to.

    build_meta.json records only aggregate counts, but emit_negative names every
    file "{tag}_{symbol}_{bar}", so the source is in the filename. Reading it
    there beats inferring the population from the picture -- the whole point of
    this audit is to see how many of each the model actually gets.
    """
    for tag in ("neghard", "negv9", "negrand"):
        if stem.startswith(tag + "_"):
            return tag
    return meta.get(stem, "unknown")


def load_meta() -> dict:
    """build_meta.json records the tag each emitted file was written under."""
    p = DS / "build_meta.json"
    if not p.exists():
        return {}
    try:
        m = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for key in ("rows", "items", "files"):
        for r in (m.get(key) or []):
            if isinstance(r, dict) and "stem" in r and "tag" in r:
                out[r["stem"]] = r["tag"]
    return out


def draw(img_path: Path, lbl_path: Path, dst: Path) -> tuple[int, bool]:
    img = cv2.imread(str(img_path))
    if img is None:
        return 0, False
    h, w = img.shape[:2]
    n = 0
    ok = True
    txt = lbl_path.read_text().strip() if lbl_path.exists() else ""
    for line in txt.splitlines():
        f = line.split()
        if len(f) != 5:
            ok = False
            continue
        xc, yc, bw, bh = (float(v) for v in f[1:])
        x1, y1 = int((xc-bw/2)*w), int((yc-bh/2)*h)
        x2, y2 = int((xc+bw/2)*w), int((yc+bh/2)*h)
        roi = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        # a box drawn where there are no candles is the failure that shipped
        # three times in the v9 round; check it here rather than by eye
        if roi.size:
            b, g, r = roi[:, :, 0].astype(int), roi[:, :, 1].astype(int), roi[:, :, 2].astype(int)
            cand = ((r-b > 60) & (r-g > 60)) | ((g-b > 60) & (g-r > 60))
            if cand.mean() < 0.03:
                ok = False
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 220), 2)
        n += 1
    cv2.imwrite(str(dst), img)
    return n, ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pct", type=float, default=5.0)
    args = ap.parse_args()
    if not DS.exists():
        print(f"数据集不存在: {DS}")
        return 2
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    meta = load_meta()
    rng = random.Random(SEED)

    pops: dict[str, list[tuple[Path, Path]]] = {"正样本": [], "困难负样本": [], "简单负样本": []}
    for split in ("train", "val"):
        for img in sorted((DS/"images"/split).glob("*.png")):
            lbl = DS/"labels"/split/f"{img.stem}.txt"
            has_box = lbl.exists() and bool(lbl.read_text().strip())
            tag = classify(img.stem, meta)
            if has_box:
                pops["正样本"].append((img, lbl))
            elif tag in ("neghard", "negv9"):
                pops["困难负样本"].append((img, lbl))
            else:
                pops["简单负样本"].append((img, lbl))

    print(f"总体: " + " · ".join(f"{k} {len(v)}" for k, v in pops.items()))
    if not meta:
        print("注意:build_meta.json 未记录每张图的来源标签,"
              "困难/简单负样本无法区分,已全部归入简单负样本")

    sections = []
    problems = 0
    for name, items in pops.items():
        if not items:
            continue
        k = max(1, round(len(items) * args.pct / 100))
        pick = rng.sample(items, min(k, len(items)))
        cards = []
        for img, lbl in pick:
            dst = OUT/"img"/f"{img.stem}.png"
            n_box, ok = draw(img, lbl, dst)
            if not ok:
                problems += 1
            flag = "" if ok else ' <b style="color:#ff5252">⚠ 框内无K线</b>'
            cards.append(
                f'<figure><figcaption>{img.stem} · 框 {n_box}{flag}</figcaption>'
                f'<img src="img/{img.stem}.png" loading="lazy"></figure>')
        sections.append(
            f'<h2>{name} <span class="c">抽样 {len(pick)} / 总 {len(items)}'
            f'({args.pct:g}%)</span></h2>\n' + "\n".join(cards))
        print(f"  {name}: 抽 {len(pick)} / {len(items)}")

    (OUT/"index.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>v10 数据集抽样审查</title><style>
body{{background:#101214;color:#e8e8e8;font:15px/1.6 -apple-system,"PingFang SC",sans-serif;margin:0;padding:18px}}
h1{{font-size:19px}} h2{{font-size:16px;margin:34px 0 12px;border-bottom:1px solid #2a2f35;padding-bottom:6px}}
.c{{color:#8b949e;font-weight:400;font-size:14px}}
figure{{margin:0 0 22px}} figcaption{{color:#9aa4ae;padding:5px 2px;font-size:13px}}
img{{width:100%;border-radius:6px;background:#fff}}
.s{{background:#171b1f;padding:12px 14px;border-radius:8px;margin:12px 0}}</style>
<h1>v10 训练集抽样审查 · 每类 {args.pct:g}%</h1>
<div class="s">红框 = 模型被告知"在这里开火"。<b>空标签图不画框</b>(负样本)。<br>
自动校验:框内必须含 K 线像素;不合格标 <b style="color:#ff5252">⚠</b>。本次不合格 <b>{problems}</b> 张。</div>
{"".join(sections)}""", encoding="utf-8")
    print(f"\n不合格 {problems} 张")
    print(f"输出 {OUT/'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
