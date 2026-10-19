#!/usr/bin/env python3
"""Build a local, filterable gallery of every original 4h YOLO event chart."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = args.output.resolve()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    events = list(summary["signals"])
    cards: list[str] = []
    for event in events:
        chart = str(event["chart"])
        chart_path = out / chart
        if not chart_path.is_file():
            raise FileNotFoundError(chart_path)
        symbol = str(event["symbol"]).replace("_USDT_SWAP", "USDT.P")
        side = "LONG" if int(event["class_id"]) == 0 else "SHORT"
        status = "CURRENT" if bool(event["is_current_latest_bar"]) else "HISTORICAL"
        first = pd.Timestamp(event["first_available_at"]).tz_convert("Asia/Shanghai")
        last = pd.Timestamp(event["last_available_at"]).tz_convert("Asia/Shanghai")
        confidence = float(event["confidence"])
        searchable = f"{symbol} {side} {status}".lower()
        cards.append(
            f'''<article class="card" data-search="{html.escape(searchable)}" data-conf="{confidence:.9f}">
  <header><strong>{html.escape(symbol)}</strong>
    <span class="{side.lower()}">{side}</span><span>{status}</span>
    <b>conf {confidence:.4f}</b><span>peak {float(event['event_peak_confidence']):.4f}</span>
    <span>{first:%m-%d %H:%M} → {last:%m-%d %H:%M} CST</span>
  </header>
  <a href="{html.escape(chart)}" target="_blank" title="打开原始 PNG">
    <img src="{html.escape(chart)}" loading="lazy" alt="{html.escape(symbol)} {side}">
  </a>
</article>'''
        )
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>4h YOLO 半个月全部原图（{len(events)} 张）</title>
<style>
body{{margin:0;background:#111;color:#eee;font:14px/1.5 -apple-system,"PingFang SC",sans-serif}}
.toolbar{{position:sticky;top:0;z-index:5;padding:12px 16px;background:#181818ee;border-bottom:1px solid #444;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
h1{{font-size:18px;margin:0 16px 0 0}} input{{background:#262626;color:#fff;border:1px solid #555;border-radius:6px;padding:8px 10px}}
button{{background:#333;color:#fff;border:1px solid #666;border-radius:6px;padding:7px 10px;cursor:pointer}}
#gallery{{display:grid;grid-template-columns:1fr;gap:18px;padding:18px}} .card{{background:#f7f7f7;color:#111;border-radius:8px;overflow:hidden}}
header{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 14px;background:#e9e9e9}}
header strong{{font-size:18px}} .long{{color:#087f5b;font-weight:800}} .short{{color:#c92a2a;font-weight:800}}
img{{display:block;width:100%;height:auto}} .hidden{{display:none}} .hint{{color:#bbb}}
@media(min-width:1500px){{#gallery.cols2{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
</style></head><body>
<div class="toolbar"><h1>4h YOLO 最近半个月全部原图：<span id="shown">{len(events)}</span> / {len(events)}</h1>
<input id="query" placeholder="筛选币种 / LONG / SHORT / CURRENT">
<input id="minconf" type="number" min="0.25" max="1" step="0.05" value="0.25" title="最低 confidence">
<button onclick="setCols(1)">单列大图</button><button onclick="setCols(2)">双列</button>
<span class="hint">点击任意图片打开原始 1920×1400 PNG</span></div>
<main id="gallery">{''.join(cards)}</main>
<script>
const q=document.getElementById('query'), m=document.getElementById('minconf'), cards=[...document.querySelectorAll('.card')];
function filter(){{const s=q.value.trim().toLowerCase(), c=parseFloat(m.value||'0.25');let n=0;cards.forEach(x=>{{const ok=x.dataset.search.includes(s)&&parseFloat(x.dataset.conf)>=c;x.classList.toggle('hidden',!ok);if(ok)n++;}});document.getElementById('shown').textContent=n;}}
function setCols(n){{document.getElementById('gallery').classList.toggle('cols2',n===2);}}
q.addEventListener('input',filter);m.addEventListener('input',filter);
</script></body></html>'''
    target = out / "all_original_charts.html"
    target.write_text(document, encoding="utf-8")
    print(f"gallery={target} images={len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
