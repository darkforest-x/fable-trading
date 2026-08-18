"""Label Studio pack for the perfect-pattern review — short side only.

Three things this fixes over the first attempt:

  the box is the one the model is actually trained on. v10's raw box is ~12 bars;
  the training label is a 5-bar crop anchored on the tightest bar in it. Reviewing
  the raw box would grade a different object than the one being learned.

  the top panel is the exact 16-bar window the model sees, not a wider zoom. If a
  pattern is only recognisable in 60 bars, the model does not have it.

  short only. This book is short-side; long candidates are kept out of the review
  rather than spending owner's attention on them.

Panels sit side by side. Stacked, the composite is taller than it is wide and the
viewer scales it down to a strip.
"""
from __future__ import annotations
import argparse, collections, hashlib, json, random, re, sys
from pathlib import Path
import cv2, numpy as np, pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT)); sys.path.insert(0, str(Path.home() / "yoyo-trading"))
from src.detection.data import add_mas          # noqa: E402
from src.detection.render import render_chart   # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=999999)
ap.add_argument("--out", default="reports/ls_pack")
ap.add_argument("--window", type=int, default=16)    # what the model sees
ap.add_argument("--box-bars", type=int, default=5)   # what the model is trained on
ap.add_argument("--context", type=int, default=200)
ap.add_argument("--future", type=int, default=48)
ap.add_argument("--side", default="short")
ap.add_argument("--seed", type=int, default=20260818)
a = ap.parse_args()
rng = random.Random(a.seed)

rows = [json.loads(l) for l in open("analysis/output/v10_mine_preholdout/detections.jsonl")]
rows = [r for r in rows if r.get("side") == a.side]
by = collections.defaultdict(list)
for r in rows: by[r["symbol"]].append(r)
ded = []
for s, rs in by.items():
    rs.sort(key=lambda r: -r["conf"]); taken = []
    for r in rs:
        c = (r["box_start_i"] + r["box_end_i"]) // 2
        if all(abs(c - t) >= 18 for t in taken):
            taken.append(c); ded.append(r)
ded.sort(key=lambda r: r["conf"])
idx = np.linspace(0, len(ded) - 1, min(a.n, len(ded))).astype(int)
pick = [dict(ded[i], _src="v10") for i in idx]

star = json.load(open("data/benchmark_exemplars.json"))["exemplars"]
gp = json.load(open("data/golden_pool.json"))
for k, v in star.items():
    m = re.match(r"(.+)_(\d{6})$", k)
    if not m or k not in gp or not gp[k]: continue
    end_i = int(m.group(2))
    for b in gp[k]:
        cx, _, w, _ = b
        b1 = end_i - 199 + int(round((cx + w / 2) * 199))
        b0 = end_i - 199 + int(round((cx - w / 2) * 199))
        pick.append({"symbol": m.group(1), "box_start_i": b0, "box_end_i": b1,
                     "tight_i": (b0 + b1) // 2, "conf": 1.0, "side": a.side,
                     "_src": "star"})
rng.shuffle(pick)
print(f"{len(pick):,} tasks  (v10 {sum(1 for p in pick if p['_src']=='v10'):,} "
      f"+ star {sum(1 for p in pick if p['_src']=='star')})  side={a.side}", flush=True)

files = {}
for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
    m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
    if m:
        files[m.group(1)] = p
        files.setdefault(m.group(1).replace("_SWAP", ""), p)

out = Path(a.out); (out / "img").mkdir(parents=True, exist_ok=True)
cache: dict[str, pd.DataFrame] = {}
tasks, truth = [], {}

def draw(img, tf, lo, hi, seg, col, th):
    if seg.empty: return
    cv2.rectangle(img, (tf.x_at(lo) - 4, tf.y_at(float(seg["high"].max())) - 9),
                  (tf.x_at(hi) + 4, tf.y_at(float(seg["low"].min())) + 9), col, th)

for n, it in enumerate(pick):
    sym = it["symbol"]
    if sym not in files: continue
    if sym not in cache:
        cache[sym] = add_mas(pd.read_csv(files[sym]).sort_values("ts").reset_index(drop=True))
    fr = cache[sym]
    rb0, rb1 = int(it["box_start_i"]), int(it["box_end_i"])
    # the training label: 5 bars centred on the tightest bar, clamped to v10's box
    k = int(it.get("tight_i", (rb0 + rb1) // 2)); half = a.box_bars // 2
    b0 = max(rb0, k - half); b1 = min(rb1, k - half + a.box_bars - 1)
    if b1 - b0 + 1 < 3 or b0 < 140 or b1 >= len(fr) - 5: continue
    rid = hashlib.sha256(f"{sym}|{b0}|{b1}|{a.seed}".encode()).hexdigest()[:12]

    ws = b1 - a.window + 1 + 2                      # box near, not at, the right edge
    ws = max(140, min(ws, b0 - 2))
    we = ws + a.window - 1
    if we >= len(fr): continue
    z, tz = render_chart(fr.iloc[ws:we + 1], out_path=None)
    draw(z, tz, b0 - ws, b1 - ws, fr.iloc[b0:b1 + 1], (0, 200, 255), 4)

    cs = max(130, b1 - a.context + 1); fe = min(len(fr) - 1, b1 + a.future)
    w, tw = render_chart(fr.iloc[cs:fe + 1], out_path=None)
    draw(w, tw, b0 - cs, b1 - cs, fr.iloc[b0:b1 + 1], (0, 200, 255), 2)
    cv2.line(w, (tw.x_at(b1 - cs), 0), (tw.x_at(b1 - cs), w.shape[0]), (140, 140, 140), 2)

    H = 620
    z = cv2.resize(z, (int(H * z.shape[1] / z.shape[0]), H), interpolation=cv2.INTER_AREA)
    w = cv2.resize(w, (int(H * w.shape[1] / w.shape[0]), H), interpolation=cv2.INTER_AREA)
    sep = np.full((H, 4, 3), 190, np.uint8)
    comp = np.hstack([z, sep, w])                    # side by side, wide not tall
    bar = np.full((30, comp.shape[1], 3), 245, np.uint8)
    c0 = float(fr["close"].iloc[b1]); sf = fr.iloc[b1 + 1:fe + 1]
    txt = f"{a.window}-bar model view (box = {b1-b0+1} bars)   |   {a.context}-bar context + {a.future}-bar future"
    if len(sf):
        txt += (f"   |   after {len(sf)}: low {(float(sf['low'].min())/c0-1)*100:+.1f}%  "
                f"high {(float(sf['high'].max())/c0-1)*100:+.1f}%  "
                f"close {(float(sf['close'].iloc[-1])/c0-1)*100:+.1f}%")
    cv2.putText(bar, txt, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, .48, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.imwrite(str(out / "img" / f"{rid}.jpg"), np.vstack([comp, bar]),
                [cv2.IMWRITE_JPEG_QUALITY, 80])

    tasks.append({"data": {"image": f"http://127.0.0.1:8792/img/{rid}.jpg", "rid": rid,
                           "symbol": sym,
                           "t": str(pd.to_datetime(fr["open_time"].iloc[b1], utc=True))}})
    truth[rid] = {"src": it["_src"], "conf": it["conf"], "symbol": sym,
                  "box_start_i": b0, "box_end_i": b1, "raw_box": [rb0, rb1]}
    if (n + 1) % 1000 == 0: print(f"  {n+1}/{len(pick)}", flush=True)

json.dump(tasks, open(out / "tasks.json", "w"), ensure_ascii=False)
json.dump(truth, open(out / "_truth.json", "w"), ensure_ascii=False)
print(f"\n{len(tasks):,} tasks -> {out}/tasks.json")
