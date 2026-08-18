"""Two-anchor phase labelling, 20-sample feel test.

Every round so far asked a state question — is this a pattern, is it an A, which
way does it break — and every state model has topped out around 0.6. The one
question that scored 0.7417 asked about a process. The six anchors in the event
schema were built for exactly that and have never been filled.

This asks for two of them per event, by clicking on the chart:

  1  where the six lines start converging
  2  where the retest fails and price breaks down

Owner's own boxes only, A-grade and starred first: their base rate is 43.2% A
against v10's 14.3%, so this spends attention on material that is already clean.

The window deliberately extends past the box. A phase boundary cannot be marked
without seeing the phase, and these anchors are structural labels, not causal
predictions — the model gets a causal prefix at training time, per spec §8.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
import cv2, pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT)); sys.path.insert(0, str(Path.home() / "yoyo-trading"))
from src.detection.data import add_mas          # noqa: E402
from src.detection.render import render_chart   # noqa: E402

N, LEAD, TRAIL = 20, 45, 30
OUT = Path("reports/phase_pilot"); (OUT / "img").mkdir(parents=True, exist_ok=True)
EV = Path("/Users/zhangzc/yolo-xx/reports/pattern_event_v3/pattern_events.jsonl")

star_keys = set(json.load(open("data/benchmark_exemplars.json"))["exemplars"])
lib = json.load(open("/Users/zhangzc/yolo-xx/reports/pattern_library_candidate.json"))
items = lib if isinstance(lib, list) else (lib.get("patterns") or lib.get("candidates") or [])
stem_of = {(it.get("pattern_id") or it.get("id")): (it.get("stem") or it.get("source_stem"))
           for it in items}

ev = [json.loads(l) for l in EV.read_text().splitlines() if l.strip()]
def rank(e):
    s = stem_of.get(e["source_pattern_id"]) in star_keys
    return (0 if (e.get("quality_label") == "A" and s) else
            1 if s else 2 if e.get("quality_label") == "A" else 3)
cand = sorted([e for e in ev if e["source"] == "golden_pool"], key=rank)

files = {}
for p in Path("data/kline_fetched").glob("okx_*_15m_*.csv"):
    m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
    if m:
        files[m.group(1)] = p; files.setdefault(m.group(1).replace("_SWAP", ""), p)

cache, tasks = {}, []
for e in cand:
    if len(tasks) >= N: break
    sym = e["symbol"]
    if sym not in files: continue
    if sym not in cache:
        cache[sym] = add_mas(pd.read_csv(files[sym]).sort_values("ts").reset_index(drop=True))
    fr = cache[sym]
    b0, b1 = e["original_box"]["box_start_i"], e["original_box"]["box_end_i"]
    if b0 is None or b0 - LEAD < 130 or b1 + TRAIL >= len(fr): continue
    ws, we = b0 - LEAD, b1 + TRAIL
    img, tf = render_chart(fr.iloc[ws:we + 1], out_path=None)
    seg = fr.iloc[b0:b1 + 1]
    cv2.rectangle(img, (tf.x_at(b0 - ws) - 3, tf.y_at(float(seg["high"].max())) - 8),
                  (tf.x_at(b1 - ws) + 3, tf.y_at(float(seg["low"].min())) + 8),
                  (0, 200, 255), 2)
    W = 1600
    img = cv2.resize(img, (W, int(W * img.shape[0] / img.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "img" / f"{e['event_id']}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    tasks.append({"event_id": e["event_id"], "symbol": sym, "grade": e.get("quality_label"),
                  "star": stem_of.get(e["source_pattern_id"]) in star_keys,
                  "ws": ws, "we": we, "n_bars": we - ws + 1,
                  "box": [b0 - ws, b1 - ws],
                  "t0": str(pd.to_datetime(fr["open_time"].iloc[ws], utc=True)),
                  "img": f"img/{e['event_id']}.jpg"})

json.dump(tasks, open(OUT / "tasks.json", "w"), ensure_ascii=False, indent=1)
print(f"{len(tasks)} 条  (A级 {sum(1 for t in tasks if t['grade']=='A')}, "
      f"⭐ {sum(1 for t in tasks if t['star'])})")
