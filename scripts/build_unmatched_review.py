"""Blinded review pack for the detector's unmatched firings.

81% of the model's fresh firings have no teacher box near them. That number has
two readings that the scan cannot separate: the model is wrong, or v10 never
looked there — the teacher scanned at stride 20 with a 200-bar window, the model
scans every bar with a 16-bar one, so a gap in coverage is expected.

Only owner can separate them, and it takes a look, not a labelling campaign.

Matched firings are mixed in and the page does not say which is which. Without
that, a reviewer shown only suspected errors will find errors. The answer key
lives in a separate file the page never loads.

Each item shows two charts: the 16 bars the model actually saw with its own box,
and 200 bars of context around the same moment, because that is the view the
pattern was originally defined in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default="analysis/output/smallwin_scan_plain.json")
    ap.add_argument("--out", default="reports/unmatched_review")
    ap.add_argument("--n-unmatched", type=int, default=35)
    ap.add_argument("--n-matched", type=int, default=15)
    ap.add_argument("--window", type=int, default=16)
    ap.add_argument("--context", type=int, default=200)
    ap.add_argument("--future", type=int, default=48,
                    help="bars shown after the decision, behind a keypress")
    ap.add_argument("--seed", type=int, default=20260818)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    scan = json.loads(Path(args.scan).read_text())
    fires = scan["fires"]
    un = [f for f in fires if not f["matched"]]
    ma = [f for f in fires if f["matched"]]

    # stratify the unmatched by confidence: a pack of only low-confidence firings
    # would answer a different question than the one being asked
    un.sort(key=lambda f: f["conf"])
    k = args.n_unmatched
    idx = np.linspace(0, len(un) - 1, k).astype(int)
    pick_un = [un[i] for i in idx]
    pick_ma = rng.sample(ma, min(args.n_matched, len(ma)))
    items = [dict(f, _truth="unmatched") for f in pick_un] + \
            [dict(f, _truth="matched") for f in pick_ma]
    rng.shuffle(items)
    print(f"{len(pick_un)} unmatched + {len(pick_ma)} matched = {len(items)} items",
          flush=True)

    files = {}
    for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
        m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
        if m:
            files[m.group(1)] = p

    out = Path(args.out); (out / "img").mkdir(parents=True, exist_ok=True)
    cache: dict[str, pd.DataFrame] = {}
    cards, truth = [], {}

    for n, it in enumerate(items):
        sym = it["symbol"]
        if sym not in files:
            continue
        if sym not in cache:
            cache[sym] = add_mas(pd.read_csv(files[sym]).sort_values("ts")
                                 .reset_index(drop=True))
        fr = cache[sym]
        tip, br = int(it["tip"]), int(it["box_right"])
        rid = "u_" + hashlib.sha256(f"{sym}|{tip}|{args.seed}".encode()).hexdigest()[:10]

        ws = tip - args.window + 1
        if ws < 130 or tip >= len(fr):
            continue
        small, tf = render_chart(fr.iloc[ws:tip + 1], out_path=None)
        bl = max(0, br - 4 - ws); brr = min(args.window - 1, br - ws)
        seg = fr.iloc[ws + bl:ws + brr + 1]
        if seg.empty:
            continue
        x0, x1 = tf.x_at(bl), tf.x_at(brr)
        y0, y1 = tf.y_at(float(seg["high"].max())), tf.y_at(float(seg["low"].min()))
        pad = int(0.012 * small.shape[0])
        cv2.rectangle(small, (x0 - pad, y0 - pad), (x1 + pad, y1 + pad), (0, 200, 255), 3)

        cs = max(130, tip - args.context + 1)
        wide, tw = render_chart(fr.iloc[cs:tip + 1], out_path=None)
        wl, wr = max(0, br - 4 - cs), min(tip - cs, br - cs)
        wseg = fr.iloc[cs + wl:cs + wr + 1]
        if not wseg.empty:
            cv2.rectangle(wide, (tw.x_at(wl) - 3, tw.y_at(float(wseg["high"].max())) - 8),
                          (tw.x_at(wr) + 3, tw.y_at(float(wseg["low"].min())) + 8),
                          (0, 200, 255), 2)
        # future panel, and the realised move as a number so it is not eyeballed
        fe = min(len(fr) - 1, tip + args.future)
        fut, tfu = render_chart(fr.iloc[cs:fe + 1], out_path=None)
        fl, fr_ = max(0, br - 4 - cs), min(fe - cs, br - cs)
        fseg = fr.iloc[cs + fl:cs + fr_ + 1]
        if not fseg.empty:
            cv2.rectangle(fut, (tfu.x_at(fl) - 3, tfu.y_at(float(fseg["high"].max())) - 8),
                          (tfu.x_at(fr_) + 3, tfu.y_at(float(fseg["low"].min())) + 8),
                          (0, 200, 255), 2)
            cv2.line(fut, (tfu.x_at(tip - cs), 0), (tfu.x_at(tip - cs), fut.shape[0]),
                     (150, 150, 150), 1)
        c0 = float(fr["close"].iloc[tip])
        seg_f = fr.iloc[tip + 1:fe + 1]
        move = {}
        if len(seg_f):
            move = {"low_pct": round((float(seg_f["low"].min()) / c0 - 1) * 100, 2),
                    "high_pct": round((float(seg_f["high"].max()) / c0 - 1) * 100, 2),
                    "end_pct": round((float(seg_f["close"].iloc[-1]) / c0 - 1) * 100, 2),
                    "bars": int(len(seg_f))}
        for tag, im in (("s", small), ("w", wide), ("f", fut)):
            im2 = cv2.resize(im, (900, int(900 * im.shape[0] / im.shape[1])),
                             interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / "img" / f"{rid}_{tag}.jpg"), im2,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
        cards.append({"rid": rid, "symbol": sym,
                      "t": str(pd.to_datetime(fr["open_time"].iloc[br], utc=True)),
                      "move": move})
        truth[rid] = {"truth": it["_truth"], "conf": it["conf"],
                      "symbol": sym, "tip": tip, "box_right": br}
        if (n + 1) % 10 == 0:
            print(f"  {n+1}/{len(items)}", flush=True)

    (out / "_answers_do_not_open.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1))
    data = json.dumps(cards, ensure_ascii=False)
    (out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>未匹配开火复核</title><style>
:root{{color-scheme:dark}}
body{{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,system-ui,sans-serif}}
header{{position:sticky;top:0;background:#161b22;border-bottom:1px solid #30363d;padding:12px 16px;z-index:9}}
h1{{margin:0 0 2px;font-size:17px}} .q{{color:#58a6ff;font-size:15px;font-weight:600}}
img{{width:100%;max-width:900px;display:block;margin:8px auto;border:1px solid #30363d;border-radius:6px}}
.meta{{color:#8b949e;font-size:12px}} kbd{{background:#21262d;border:1px solid #30363d;border-radius:4px;padding:2px 8px}}
.keys{{margin-top:8px;font-size:14px}} .keys b{{color:#3fb950}}
button{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 12px;cursor:pointer}}
.lab{{text-align:center;color:#8b949e;font-size:12px;margin-top:6px}}
.done{{color:#3fb950;font-weight:600}}
</style>
<header>
<h1><span id="pos"></span> · 已判 <span class="done" id="done">0</span>/<span id="tot"></span>
&nbsp;<button onclick="dl()">导出 JSONL</button></h1>
<div class="q">模型在这里开了一枪。这是不是一个你要的形态？</div>
<div class="keys"><b><kbd>Y</kbd> 是</b>　<kbd>N</kbd> 不是　<kbd>S</kbd> 说不准　\
<kbd>空格</kbd> 揭开后续走势　<kbd>←</kbd> 上一个</div>
<div class="meta" id="info"></div>
</header>
<img id="s"><div class="lab">↑ 模型实际看到的 16 根（黄框 = 模型的检出）</div>
<img id="w"><div class="lab">↑ 同一时刻的 200 根上下文（决策点 = 图最右）</div>
<div id="fw" style="display:none">
<img id="f"><div class="lab" id="mv"></div>
</div>
<div class="lab" id="hint">按 <kbd>空格</kbd> 看后续 48 根走势（看过之后的判定会被单独标记）</div>
<script>
const IT={data};
const K='unmatched_review_v1';
let ans=JSON.parse(localStorage.getItem(K)||'{{}}'), i=0, seen=false;
const $=s=>document.querySelector(s);
function render(){{
  const it=IT[i]; if(!it) return;
  $('#s').src='img/'+it.rid+'_s.jpg'; $('#w').src='img/'+it.rid+'_w.jpg';
  $('#pos').textContent=(i+1)+' / '+IT.length; $('#tot').textContent=IT.length;
  $('#done').textContent=Object.keys(ans).length;
  $('#f').src='img/'+it.rid+'_f.jpg';
  $('#fw').style.display = seen ? 'block' : 'none';
  $('#hint').style.display = seen ? 'none' : 'block';
  const m=it.move||{{}};
  $('#mv').textContent = m.bars ? ('↑ 后续 '+m.bars+' 根：最低 '+m.low_pct+'%　最高 '+m.high_pct+'%　收于 '+m.end_pct+'%') : '';
  const a=ans[it.rid];
  $('#info').textContent=(a?('已判: '+a.v+(a.seen?'（看过未来）':'')+'　'):'')+it.symbol+' · '+it.t;
}}
function set(v){{ ans[IT[i].rid]={{v:v, seen:seen}}; localStorage.setItem(K,JSON.stringify(ans));
  if(i<IT.length-1){{i++; seen=false;}} render(); }}
function dl(){{
  const lines=IT.map(it=>{{const a=ans[it.rid]||{{}};
    return JSON.stringify({{rid:it.rid,symbol:it.symbol,t:it.t,
      verdict:a.v||null, future_seen:!!a.seen, move:it.move||null, reviewer:'owner'}});}});
  const b=new Blob([lines.join('\\n')+'\\n'],{{type:'application/x-ndjson'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='unmatched_verdicts.jsonl';a.click();
}}
addEventListener('keydown',e=>{{const k=e.key.toLowerCase();
  if(k===' '){{e.preventDefault(); seen=true; render(); return;}}
  if(k==='y')set('YES'); else if(k==='n')set('NO'); else if(k==='s')set('SKIP');
  else if(k==='arrowleft'){{if(i>0){{i--; seen=false;}} render();}}}});
render();
</script>""", encoding="utf-8")
    print(f"\n{len(cards)} items -> {out}/index.html")
    print(f"answer key: {out}/_answers_do_not_open.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
