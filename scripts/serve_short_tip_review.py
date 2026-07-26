"""Keyboard review server for the short tip_v1b 1000-box gold pack (Phase S3).

Why: the pack ships an index.html that renders ~60 of the 1000 boxes and has no
keyboard handling and no write-back — its README asks the owner to hand-edit a
1000-row review_sheet.csv while looking at images elsewhere. That is hours of
error-prone row-matching for the one step iron rule 12 makes the sole promotion
gate (real-tip gold + tip-smoke; own val/mAP never decides). This turns it into
one keypress per box.

Design notes that matter for correctness:

* Review order is a FIXED-SEED SHUFFLE of the sheet. The sheet's order is only
  weakly related to confidence (Spearman 0.116), but shuffling makes "I stopped
  after N" an unbiased sample, so the running precision below is honest at any
  stopping point.
* A Wilson 95% interval on precision is shown live. Deciding whether this
  detector is good enough does not need all 1000 boxes — around 200-300 already
  pins precision to roughly +-5%. Stop when the interval clears your bar.
* Every keypress appends to reviews.jsonl AND rewrites review_sheet.csv
  atomically, so a crash or a closed laptop never loses labels, and re-running
  resumes exactly where you stopped.
* TRAINING stays tip-only per iron rule 12; the REVIEW view does not have to be
  the training picture, and it should not be. Owner, 2026-07-26: you cannot tell
  whether a box is correctly placed when it is jammed against the right border
  with nothing after it. Measured on a real pack PNG: 200 bars across 1280px
  leaves the box ~90px wide, below the resolution needed to see whether six MAs
  are converged, with the left 85% irrelevant history.
  So each card leads with a REVIEW render -- the box drawn where the detector
  put it, plus the bars that came AFTER it, and a grey line marking the tip so
  it is always clear what the detector could and could not see (keys Q/W/E/R for
  0/30/60/120 forward bars). A tip-only zoom sits below for reading the MA
  bundle closely (keys 1/2/3). Both render on demand and cache under _zoom/.

Seeing the aftermath is safe for THIS question and necessary for it: whether the
MAs were converged is fixed by bars at or before the tip, and no later bar can
change it -- the future only helps you see whether the box sits on the right
spot. What must not happen is the question drifting into "did this trade pay".
That is what spoiled the earlier 2525-box round, where labels made with the
outcome visible produced oracle PF 5.6-7.4 while causal rules stayed at 0.9-1.2.

Verdicts written to owner_keep:
  keep — 真的是盘口双均线密集(该检出)
  drop — 不是密集 / 框错位置(误检)
  skip — 拿不准(不计入精度分母)

Usage:
  PYTHONPATH=. .venv/bin/python scripts/serve_short_tip_review.py
  then open http://127.0.0.1:8770/

This tool only records the owner's eye. It never promotes, never touches
holdout, never trains.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import tempfile
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parents[1]
# Self-contained: zoom rendering imports src.*, so do not depend on PYTHONPATH
# being set by whoever launches this.
sys.path.insert(0, str(PROJECT))
DEFAULT_PACK = PROJECT / "analysis" / "output" / "owner_side_short_tip_v1b_detect1000"
SHUFFLE_SEED = 20260726
VERDICTS = ("keep", "drop", "skip")
ZOOM_BARS = (40, 60, 100)
FWD_BARS = (0, 30, 60, 120)   # bars shown AFTER the tip in the review view

_PACK: Path = DEFAULT_PACK
_ROWS: list[dict] = []
_ORDER: list[int] = []
_BY_STEM: dict[str, dict] = {}
_FRAMES: dict[str, object] = {}  # small LRU of loaded+MA'd series


def get_frame(symbol: str):
    """Load one symbol's series with MAs, keeping a few in memory."""
    if symbol in _FRAMES:
        return _FRAMES[symbol]
    from src.data.loader import list_series, load_series
    from src.detection.data import add_mas
    frame = add_mas(load_series(list_series(bar="15m")[("okx", symbol)]))
    if len(_FRAMES) >= 8:
        _FRAMES.pop(next(iter(_FRAMES)))
    _FRAMES[symbol] = frame
    return frame


def ctx_png(stem: str, back: int, fwd: int) -> bytes | None:
    """Render the review view: box in place, plus the bars that came AFTER it.

    Training stays tip-only (iron rule 12) -- this is the REVIEW view only.
    Owner's point, 2026-07-26: you cannot tell whether a box is correctly placed
    without seeing what surrounds it; the tip-only training PNG is the wrong
    picture for that job. So the box is drawn where the detector put it, and a
    vertical line marks the tip -- everything right of that line is future the
    detector never saw. Judging box PLACEMENT with hindsight visible is fine;
    the MA convergence it marks is fixed by bars at or before the tip and no
    future bar can change it. Just do not let the aftermath turn the question
    into "did it pay", which is what spoiled the 2525-box round.
    """
    cache = _PACK / "_zoom" / f"ctx_{stem}_{back}_{fwd}.png"
    if cache.exists():
        return cache.read_bytes()
    row = _BY_STEM.get(stem)
    if row is None:
        return None
    import cv2
    import pandas as pd
    from src.detection.render import make_chart_transform, render_chart
    from src.judgment.yolo_candidates import WINDOW as SCAN_WINDOW
    from src.judgment.yolo_candidates import right_edge_to_bar

    label = load_label_box(_PACK, stem)
    if label is None:
        return None
    try:
        frame = get_frame(row["symbol"])
        times = pd.to_datetime(frame["open_time"], utc=True)
        tip = int(times.searchsorted(pd.Timestamp(row["tip_time"])))
        if tip <= 0 or tip >= len(frame):
            return None

        # 1) Rebuild the transform of the window the detector actually saw
        #    (SCAN_WINDOW bars ending at the tip) to turn the normalized box
        #    back into absolute bar indices and a price range.
        w0 = max(0, tip - SCAN_WINDOW + 1)
        tf_scan = make_chart_transform(frame.iloc[w0:tip + 1])
        cx, yc, bw, bh = label
        r_bar = right_edge_to_bar(cx, bw, tf_scan, n_bars=tf_scan.n_bars)
        l_bar = right_edge_to_bar(cx - bw, bw, tf_scan, n_bars=tf_scan.n_bars)
        span = max(tf_scan.price_max - tf_scan.price_min, 1e-12)

        def px_to_price(y_norm: float) -> float:
            y = y_norm * tf_scan.height
            return tf_scan.price_max - (y - tf_scan.top) / max(tf_scan.plot_h, 1) * span

        p_hi = px_to_price(yc - bh / 2)
        p_lo = px_to_price(yc + bh / 2)
        abs_l, abs_r = w0 + l_bar, w0 + r_bar

        # 2) Render the review window, which extends PAST the tip.
        lo = max(0, tip - back + 1)
        hi = min(len(frame) - 1, tip + fwd)
        sub = frame.iloc[lo:hi + 1]
        cache.parent.mkdir(parents=True, exist_ok=True)
        img, tf = render_chart(sub, out_path=None)
        img = img.copy()

        # 3) Draw the detection box and mark where the detector's view ended.
        x0, x1 = tf.x_at(abs_l - lo), tf.x_at(abs_r - lo)
        y0, y1 = tf.y_at(p_hi), tf.y_at(p_lo)
        # render_chart's array is already in the layout cv2.imwrite expects
        # (CANDLE_RED=(69,54,242) is BGR), so colours here are BGR and the
        # array must be written WITHOUT a channel swap.
        cv2.rectangle(img, (x0 - tf.candle_half_w, y0),
                      (x1 + tf.candle_half_w, y1), (60, 60, 255), 2)
        if fwd > 0 and hi > tip:
            xt = tf.x_at(tip - lo)
            cv2.line(img, (xt, tf.top), (xt, tf.top + tf.plot_h), (150, 150, 150), 1)
            cv2.putText(img, "tip (detector saw up to here)", (xt + 6, tf.top + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 120), 1, cv2.LINE_AA)
        cv2.imwrite(str(cache), img)
    except Exception as exc:  # noqa: BLE001 — a bad symbol must not kill review
        print(f"  ctx failed {stem}: {type(exc).__name__}: {exc}")
        return None
    return cache.read_bytes() if cache.exists() else None


def zoom_png(stem: str, n_bars: int) -> bytes | None:
    """Render (and cache) the last n_bars ending at this stem's tip bar.

    The tip bar is located by TIME, not by the sheet's stored index, so a
    re-fetched series with different length cannot silently shift the window.
    """
    cache = _PACK / "_zoom" / f"{stem}_{n_bars}.png"
    if cache.exists():
        return cache.read_bytes()
    row = _BY_STEM.get(stem)
    if row is None:
        return None
    import pandas as pd
    from src.detection.render import render_chart
    try:
        frame = get_frame(row["symbol"])
        times = pd.to_datetime(frame["open_time"], utc=True)
        i = int(times.searchsorted(pd.Timestamp(row["tip_time"])))
        if i <= 0 or i >= len(frame):
            return None
        lo = max(0, i - n_bars + 1)
        cache.parent.mkdir(parents=True, exist_ok=True)
        render_chart(frame.iloc[lo:i + 1], out_path=cache)
    except Exception as exc:  # noqa: BLE001 — a bad symbol must not kill review
        print(f"  zoom failed {stem}: {type(exc).__name__}: {exc}")
        return None
    return cache.read_bytes() if cache.exists() else None


def wilson(k: int, n: int) -> tuple[float, float, float]:
    """Point estimate and Wilson 95% interval for k successes in n trials."""
    if n == 0:
        return 0.0, 0.0, 1.0
    z = 1.959963985
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def load_rows(pack: Path) -> list[dict]:
    sheet = pack / "review_sheet.csv"
    with sheet.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r.setdefault("owner_keep", "")
        r.setdefault("owner_note", "")
    return rows


def load_label_box(pack: Path, stem: str) -> list[float] | None:
    """Highest-area YOLO box for this stem, as [xc, yc, w, h] normalized."""
    p = pack / "labels" / "train" / f"{stem}.txt"
    if not p.exists():
        return None
    best = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            xc, yc, w, h = (float(v) for v in parts[1:5])
        except ValueError:
            continue
        if best is None or w * h > best[2] * best[3]:
            best = [xc, yc, w, h]
    return best


def save_sheet(pack: Path, rows: list[dict]) -> None:
    """Atomic rewrite so an interrupted write cannot corrupt the sheet."""
    sheet = pack / "review_sheet.csv"
    fields = list(rows[0].keys())
    fd, tmp = tempfile.mkstemp(dir=str(pack), suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, sheet)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def stats(rows: list[dict]) -> dict:
    keep = sum(1 for r in rows if r["owner_keep"] == "keep")
    drop = sum(1 for r in rows if r["owner_keep"] == "drop")
    skip = sum(1 for r in rows if r["owner_keep"] == "skip")
    done = keep + drop + skip
    p, lo, hi = wilson(keep, keep + drop)
    return {"keep": keep, "drop": drop, "skip": skip, "done": done,
            "total": len(rows), "precision": round(p, 4),
            "ci_lo": round(lo, 4), "ci_hi": round(hi, 4), "graded": keep + drop}


def next_index(rows: list[dict], order: list[int]) -> int:
    for i in order:
        if not rows[i]["owner_keep"]:
            return i
    return -1


def card(rows: list[dict], order: list[int], pack: Path, idx: int | None = None) -> dict:
    i = next_index(rows, order) if idx is None else idx
    if i < 0:
        return {"done": True, "stats": stats(rows)}
    r = rows[i]
    pos = order.index(i) + 1
    return {
        "done": False, "row": i, "pos": pos, "stem": r["stem"],
        "symbol": r["symbol"], "tip_time": r.get("tip_time", ""),
        "conf": r.get("max_conf", ""), "spread": r.get("tip_spread", ""),
        "image": f"/img/{r['stem']}.png", "box": load_label_box(pack, r["stem"]),
        "zoom": f"/zoom/{r['stem']}", "zoom_bars": list(ZOOM_BARS),
        "ctx": f"/ctx/{r['stem']}",
        "current": r["owner_keep"], "stats": stats(rows),
    }


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/card":
            self._json(200, card(_ROWS, _ORDER, _PACK))
        elif path.startswith("/img/"):
            img = _PACK / "images" / "train" / Path(path[5:]).name
            if img.exists() and img.suffix == ".png":
                self._send(200, img.read_bytes(), "image/png")
            else:
                self._json(404, {"error": "not found"})
        elif path.startswith("/ctx/"):
            stem = Path(path[5:]).name
            q = dict(p.split("=", 1) for p in urlparse(self.path).query.split("&") if "=" in p)
            try:
                back, fwd = int(q.get("back", 120)), int(q.get("fwd", 60))
            except ValueError:
                back, fwd = 120, 60
            back = min(max(back, 40), 300)
            fwd = fwd if fwd in FWD_BARS else 60
            png = ctx_png(stem, back, fwd)
            if png:
                self._send(200, png, "image/png")
            else:
                self._json(404, {"error": "ctx unavailable"})
        elif path.startswith("/zoom/"):
            stem = Path(path[6:]).name
            try:
                n = int(urlparse(self.path).query.split("n=")[-1])
            except (ValueError, IndexError):
                n = 60
            n = n if n in ZOOM_BARS else 60
            png = zoom_png(stem, n)
            if png:
                self._send(200, png, "image/png")
            else:
                self._json(404, {"error": "zoom unavailable"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/mark":
            self._json(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return
        row, verdict = data.get("row"), str(data.get("verdict", "")).strip().lower()
        note = str(data.get("note", "")).strip()
        if not isinstance(row, int) or not 0 <= row < len(_ROWS):
            self._json(400, {"error": "bad row"})
            return
        if verdict not in VERDICTS and verdict != "":
            self._json(400, {"error": f"verdict must be one of {VERDICTS} or empty"})
            return
        _ROWS[row]["owner_keep"] = verdict
        if note:
            _ROWS[row]["owner_note"] = note
        with (_PACK / "reviews.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"stem": _ROWS[row]["stem"], "verdict": verdict,
                                 "note": note}, ensure_ascii=False) + "\n")
        save_sheet(_PACK, _ROWS)
        self._json(200, card(_ROWS, _ORDER, _PACK))


PAGE = r"""<!doctype html><meta charset=utf-8>
<title>short tip_v1b 金标评审</title>
<style>
 body{margin:0;background:#111;color:#eee;font:14px/1.5 -apple-system,system-ui,sans-serif}
 header{position:sticky;top:0;background:#1a1a1a;padding:8px 14px;border-bottom:1px solid #333;
        display:flex;gap:18px;align-items:center;flex-wrap:wrap;z-index:5}
 .big{font-size:19px;font-weight:600}
 .ci{color:#8bc34a} .muted{color:#888} .warn{color:#ffb74d}
 .lbl{padding:10px 14px 2px;color:#9e9e9e;font-size:13px;width:min(1280px,96vw);margin:0 auto}
 #zwrap{margin:4px auto 0;width:min(1280px,96vw)}
 #zoom{width:100%;display:block;border-radius:4px;background:#fff}
 #wrap{position:relative;margin:4px auto 0;width:min(1280px,96vw);opacity:.85}
 #shot{width:100%;display:block;border-radius:4px}
 #box{position:absolute;border:2px solid #ff3b30;box-shadow:0 0 0 1px #000;pointer-events:none}
 .keys{padding:0 14px 20px;text-align:center}
 kbd{background:#333;border-radius:4px;padding:2px 7px;margin:0 3px;font-family:ui-monospace}
 button{background:#2a2a2a;color:#eee;border:1px solid #444;border-radius:6px;
        padding:9px 18px;font-size:15px;cursor:pointer;margin:0 5px}
 button:hover{background:#3a3a3a}
 #done{text-align:center;padding:60px;font-size:20px}
</style>
<header>
 <span class=big id=prog>…</span>
 <span>精度 <b id=prec>—</b> <span class=ci id=ci></span></span>
 <span class=muted id=counts></span>
 <span class=muted id=meta></span>
</header>
<div class=lbl><b>评审图:框 + 框之后的走势</b>（灰竖线=tip,检测器只看到线左边）
 —— 后续 <b id=nfwd>60</b> 根（<kbd>Q</kbd>0 <kbd>W</kbd>30 <kbd>E</kbd>60 <kbd>R</kbd>120）</div>
<div id=zwrap><img id=ctx></div>
<div class=lbl>tip 放大 <b id=nbars>60</b> 根(无未来,看均线收敛细节)
 （<kbd>1</kbd>40 <kbd>2</kbd>60 <kbd>3</kbd>100）</div>
<div id=wrap><img id=zoom></div>
<div class=keys>
 <button onclick="mark('keep')">K 真密集(keep)</button>
 <button onclick="mark('drop')">D 误检(drop)</button>
 <button onclick="mark('skip')">S 拿不准</button>
 <span class=muted style="margin-left:14px">快捷键 <kbd>K</kbd><kbd>D</kbd><kbd>S</kbd>
   · <kbd>←</kbd> 撤销上一个</span>
 <div class=muted style="margin-top:8px" id=hint></div>
 <div class=warn style="margin-top:6px">判的是<b>此刻的形态</b>(均线有没有聚拢),
   <b>不是</b>"这单赚没赚"。看后续走势是为了确认<b>框的位置对不对</b>;
   均线收没收敛由 tip 及之前决定,未来改不了它。</div>
</div>
<script>
let cur=null, prev=[], nbars=60, nfwd=60;
async function load(){ render(await (await fetch('/api/card')).json()); }
function render(c){
  if(c.done){ document.body.innerHTML='<div id=done>✅ 全部评完<br><br>'
      +'keep '+c.stats.keep+' · drop '+c.stats.drop+' · skip '+c.stats.skip
      +'<br>精度 '+(c.stats.precision*100).toFixed(1)+'%</div>'; return; }
  cur=c;
  document.getElementById('prog').textContent=c.pos+' / '+c.stats.total;
  const s=c.stats;
  document.getElementById('prec').textContent =
      s.graded? (s.precision*100).toFixed(1)+'%' : '—';
  document.getElementById('ci').textContent =
      s.graded>=20? '95%CI ['+(s.ci_lo*100).toFixed(1)+', '+(s.ci_hi*100).toFixed(1)+']' : '';
  document.getElementById('counts').textContent =
      'keep '+s.keep+' · drop '+s.drop+' · skip '+s.skip;
  document.getElementById('meta').textContent = c.symbol+'  '+c.tip_time+'  conf='+c.conf;
  const w=(s.ci_hi-s.ci_lo);
  document.getElementById('hint').textContent = s.graded>=20
     ? (w<=0.10 ? '区间已收窄到 ±'+(w*50).toFixed(1)+'%,够裁决了,可以随时停。'
                : '再看一些,区间还宽(±'+(w*50).toFixed(1)+'%)。')
     : '前 20 个之后开始显示置信区间。';
  document.getElementById('ctx').src=c.ctx+'?back=120&fwd='+nfwd;
  document.getElementById('nfwd').textContent=nfwd;
  document.getElementById('zoom').src=c.zoom+'?n='+nbars;
  document.getElementById('nbars').textContent=nbars;
}
async function mark(v){
  if(!cur) return; const row=cur.row; prev.push(row);
  render(await (await fetch('/api/mark',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({row:row,verdict:v})})).json());
}
async function undo(){
  if(!prev.length) return; const row=prev.pop();
  render(await (await fetch('/api/mark',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({row:row,verdict:''})})).json());
}
addEventListener('keydown',e=>{
  const k=e.key.toLowerCase();
  if(k==='k')mark('keep'); else if(k==='d')mark('drop');
  else if(k==='s')mark('skip'); else if(e.key==='ArrowLeft')undo();
  else if(k==='1'||k==='2'||k==='3'){ nbars=[40,60,100][+k-1]; if(cur)render(cur); }
  else if('qwer'.includes(k)){ nfwd=[0,30,60,120]['qwer'.indexOf(k)]; if(cur)render(cur); }
});
load();
</script>"""


def main() -> int:
    global _PACK, _ROWS, _ORDER, _BY_STEM
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()

    _PACK = args.pack
    if not (_PACK / "review_sheet.csv").exists():
        print(f"review_sheet.csv not found under {_PACK}")
        return 2
    _ROWS = load_rows(_PACK)
    _BY_STEM = {r["stem"]: r for r in _ROWS}
    _ORDER = list(range(len(_ROWS)))
    random.Random(SHUFFLE_SEED).shuffle(_ORDER)

    s = stats(_ROWS)
    print(f"pack   : {_PACK}")
    print(f"boxes  : {s['total']}  已评 {s['done']}  (keep {s['keep']} / drop {s['drop']} / skip {s['skip']})")
    if s["graded"]:
        print(f"精度   : {s['precision']*100:.1f}%  95%CI [{s['ci_lo']*100:.1f}, {s['ci_hi']*100:.1f}]")
    print(f"\n打开 http://127.0.0.1:{args.port}/    (K=keep D=drop S=skip ←=撤销)")
    print("每次按键即时写回 review_sheet.csv,可随时关掉再续。\n")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
