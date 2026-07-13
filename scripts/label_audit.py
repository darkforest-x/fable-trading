"""Label-audit sample pack: stratified random images from the YOLO dataset
with their AUTO-GENERATED label boxes drawn on, for the owner's eyeball
review (owner request 2026-07-09: "打标的过程能抽样让我看到").

What to look for when reviewing:
- 绿框是否恰好罩住"多条均线收拢"的区段（宽度贴合、不过宽不过窄）；
- 有明显密集却没画框 = 漏标；框住了明显不密集的区段 = 误标；
- 背景图（无框样本）里是否混着该标的密集区。

Output: src/webapp/static/label_audit.html (self-contained, ~2MB) --
served by the dashboard at /label_audit.html. Rerun for a fresh sample
(--seed changes the draw).
"""
from __future__ import annotations

import argparse
import base64
import random
from pathlib import Path

import cv2

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATASET = None  # set from --dataset in main()
OUT = PROJECT_DIR / "src" / "webapp" / "static" / "label_audit.html"
GREEN = (60, 200, 120)  # BGR


def load_boxes(label_path: Path) -> list[tuple[float, float, float, float]]:
    if not label_path.exists():
        return []
    boxes = []
    for line in label_path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 5:
            _, cx, cy, w, h = map(float, parts)
            boxes.append((cx, cy, w, h))
    return boxes


def render(img_path: Path, boxes, max_w: int = 660) -> str:
    img = cv2.imread(str(img_path))
    ih, iw = img.shape[:2]
    for cx, cy, w, h in boxes:
        x1, y1 = int((cx - w / 2) * iw), int((cy - h / 2) * ih)
        x2, y2 = int((cx + w / 2) * iw), int((cy + h / 2) * ih)
        cv2.rectangle(img, (x1, y1), (x2, y2), GREEN, 3)
    scale = max_w / iw
    img = cv2.resize(img, (max_w, int(ih * scale)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return base64.b64encode(buf).decode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--per-cell", type=int, default=6, help="images per (split x has-box) cell")
    parser.add_argument("--dataset", default="dense_15m_full")
    args = parser.parse_args()
    global DATASET
    DATASET = PROJECT_DIR / "datasets" / args.dataset
    rng = random.Random(args.seed)

    cards = []
    for split in ("val", "train"):
        img_dir = DATASET / "images" / split
        lbl_dir = DATASET / "labels" / split
        items = []
        for p in sorted(img_dir.glob("*.png")):
            boxes = load_boxes(lbl_dir / (p.stem + ".txt"))
            items.append((p, boxes))
        with_box = [x for x in items if x[1]]
        background = [x for x in items if not x[1]]
        sample = rng.sample(with_box, min(args.per_cell, len(with_box))) + \
                 rng.sample(background, min(args.per_cell // 2, len(background)))
        for p, boxes in sample:
            b64 = render(p, boxes)
            tag = f"{len(boxes)} 框" if boxes else "背景图（规则判定：无密集）"
            cards.append(
                f'<figure><img src="data:image/jpeg;base64,{b64}" alt="{p.stem}">'
                f'<figcaption><b>{split}</b> · {p.stem} · {tag}</figcaption></figure>')

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>打标抽样审计 · dense_15m_full</title>
<style>
*,*::before,*::after{{box-sizing:border-box}}
body{{background:#131519;color:#e8e9eb;font-family:"PingFang SC",system-ui,sans-serif;
     margin:0;padding:24px;line-height:1.7}}
h1{{font-size:20px}} p{{color:#9aa0a8;max-width:56em;font-size:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(480px,100%),1fr));gap:16px;margin-top:16px}}
figure{{margin:0;background:#1b1e24;border:1px solid #2e3340;border-radius:10px;padding:10px}}
img{{width:100%;border-radius:6px;display:block}}
figcaption{{font-size:12.5px;color:#9aa0a8;margin-top:6px}}
b{{color:#e8e9eb}}
</style></head><body>
<h1>打标抽样审计 —— 规则自动标注 vs 你的眼睛</h1>
<p>绿框 = auto_label.py 规则画的"均线密集区"标注（YOLO 学习的标准答案）。审计要点：
① 框是否恰好罩住均线收拢段；② 有明显密集却没框 = 漏标；③ 框住不密集的区段 = 误标；
④ 背景图里是否混有该标的密集区。发现问题请记下图名，规则阈值可修（改动走 owner 审批）。
本页 seed：<code>{args.seed}</code>；换一批样本：
<code>PYTHONPATH=. .venv/bin/python scripts/label_audit.py --seed 42</code></p>
<div class="grid">{''.join(cards)}</div>
</body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB, {len(cards)} images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
