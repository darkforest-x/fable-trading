"""Visual scout: the owner-taste detector watches the RIGHT EDGE of every
crypto swap chart and pushes fresh dense-cluster sightings to Telegram.

- model: models/owner_best.pt if present (updated by training queues),
  else the newest runs/detect/.../owner_v*/weights/best.pt;
- a sighting = predicted box whose right edge reaches the last ~10% of the
  window (i.e., the cluster is live NOW), conf >= CONF;
- dedupe: one alert per (symbol, box-right bar-time); state in
  data/visual_scout_state.json; log appended to data/visual_scout_log.csv;
- annotated chart PNGs land in src/webapp/static/scout/ and a gallery page
  is regenerated for the dashboard (/scout.html).

Run once per invocation (loop via scripts/scout_loop.sh).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
from src.data.loader import FETCHED_DIR  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas, list_cache_files, load_ohlcv_csv  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.notify import send  # noqa: E402

WINDOW = 200
CONF = 0.25
RIGHT_EDGE = 0.90  # box right edge must reach this normalized x
SCOUT_DIR = PROJECT_DIR / "src/webapp/static/scout"
STATE = PROJECT_DIR / "data/visual_scout_state.json"
LOG = PROJECT_DIR / "data/visual_scout_log.csv"
TMP = PROJECT_DIR / "data/_scout_tmp.png"


def pick_model() -> Path:
    fixed = PROJECT_DIR / "models/owner_best.pt"
    if fixed.exists():
        return fixed
    runs = sorted(PROJECT_DIR.glob("runs/detect/runs/detect/owner_v*/weights/best.pt"),
                  key=lambda p: p.stat().st_mtime)
    if not runs:
        raise SystemExit("no owner detector weights found")
    return runs[-1]


def main() -> int:
    from ultralytics import YOLO
    weights = pick_model()
    model = YOLO(str(weights))
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    SCOUT_DIR.mkdir(parents=True, exist_ok=True)
    hits = []
    for path in list_cache_files(FETCHED_DIR, min_rows=5000):
        symbol = path.stem.rsplit("_", 2)[0].replace("okx_", "")
        if not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        df = add_mas(load_ohlcv_csv(path))
        if len(df) < WINDOW:
            continue
        sub = df.iloc[-WINDOW:]
        last_ts = str(sub["open_time"].iloc[-1])
        render_chart(sub, out_path=TMP)
        res = model.predict(str(TMP), conf=CONF, verbose=False)[0]
        if res.boxes is None:
            continue
        live = []
        for b, c in zip(res.boxes.xywhn.cpu().numpy(), res.boxes.conf.cpu().numpy()):
            cx, _, w, _ = map(float, b[:4])
            if cx + w / 2 >= RIGHT_EDGE:
                live.append((float(c), b))
        if not live:
            continue
        key = f"{symbol}@{last_ts[:13]}"  # dedupe per symbol-hour
        if state.get(symbol) == key.split("@")[1]:
            continue
        state[symbol] = key.split("@")[1]
        conf = max(c for c, _ in live)
        img = cv2.imread(str(TMP))
        ih, iw = img.shape[:2]
        for c, b in live:
            cx, cy, w, h = map(float, b[:4])
            cv2.rectangle(img, (int((cx - w/2) * iw), int((cy - h/2) * ih)),
                          (int((cx + w/2) * iw), int((cy + h/2) * ih)), (60, 200, 120), 3)
        cv2.imwrite(str(SCOUT_DIR / f"{symbol}.png"), img,
                    [cv2.IMWRITE_PNG_COMPRESSION, 6])
        hits.append({"symbol": symbol, "conf": round(conf, 3), "last_bar": last_ts})

    if hits:
        LOG.parent.mkdir(exist_ok=True)
        new_file = not LOG.exists()
        with LOG.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["scanned_at", "symbol", "conf", "last_bar"])
            if new_file:
                w.writeheader()
            for h in hits:
                w.writerow({"scanned_at": datetime.now(timezone.utc).isoformat(), **h})
        hits.sort(key=lambda h: -h["conf"])
        lines = [f"👁 视觉侦察：{len(hits)} 个币种右缘出现密集（模型 {weights.name}）"]
        lines += [f"· {h['symbol']}  conf {h['conf']}" for h in hits[:10]]
        lines.append("图: http://103.214.174.58:8642/scout.html")
        send("\n".join(lines))
    STATE.write_text(json.dumps(state))

    pngs = sorted(SCOUT_DIR.glob("*.png"), key=lambda p: -p.stat().st_mtime)[:40]
    cards = "".join(
        f'<figure><img src="scout/{p.name}?v={int(p.stat().st_mtime)}">'
        f'<figcaption>{p.stem} · {datetime.fromtimestamp(p.stat().st_mtime):%m-%d %H:%M}</figcaption></figure>'
        for p in pngs)
    (PROJECT_DIR / "src/webapp/static/scout.html").write_text(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>视觉侦察 · owner 口味检测器实时哨</title><style>
body{{background:#131519;color:#e8e9eb;font-family:"PingFang SC",system-ui,sans-serif;padding:24px}}
h1{{font-size:20px}} p{{color:#9aa0a8;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(460px,100%),1fr));gap:14px;margin-top:14px}}
figure{{margin:0;background:#1b1e24;border:1px solid #2e3340;border-radius:10px;padding:8px}}
img{{width:100%;border-radius:6px;display:block}} figcaption{{font-size:12px;color:#9aa0a8;margin-top:4px}}
</style></head><body>
<h1>视觉侦察 —— 检测器盯着每张合约图的右边缘</h1>
<p>绿框 = owner 口味模型在最新 200 根上的实时检出（右缘 = 正在形成）。侦察不等于交易信号：
入场仍由判断层 + 趋势闸决定。TG 已同步推送。</p>
<div class="grid">{cards}</div></body></html>""", encoding="utf-8")
    print(f"scout done: {len(hits)} new sightings, model={weights.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
