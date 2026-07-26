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
* Boxes are drawn in the browser from the YOLO label, so the chart PNGs stay
  untouched originals.

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
import tempfile
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = PROJECT / "analysis" / "output" / "owner_side_short_tip_v1b_detect1000"
SHUFFLE_SEED = 20260726
VERDICTS = ("keep", "drop", "skip")

_PACK: Path = DEFAULT_PACK
_ROWS: list[dict] = []
_ORDER: list[int] = []


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
 #wrap{position:relative;margin:12px auto;width:min(1280px,96vw)}
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
<div id=wrap><img id=shot><div id=box hidden></div></div>
<div class=keys>
 <button onclick="mark('keep')">K 真密集(keep)</button>
 <button onclick="mark('drop')">D 误检(drop)</button>
 <button onclick="mark('skip')">S 拿不准</button>
 <span class=muted style="margin-left:14px">快捷键 <kbd>K</kbd><kbd>D</kbd><kbd>S</kbd>
   · <kbd>←</kbd> 撤销上一个</span>
 <div class=muted style="margin-top:8px" id=hint></div>
</div>
<script>
let cur=null, prev=[];
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
  const img=document.getElementById('shot'), box=document.getElementById('box');
  img.onload=()=>{ if(!c.box){box.hidden=true;return;}
    const [xc,yc,bw,bh]=c.box, W=img.clientWidth, H=img.clientHeight;
    box.style.left=((xc-bw/2)*W)+'px'; box.style.top=((yc-bh/2)*H)+'px';
    box.style.width=(bw*W)+'px'; box.style.height=(bh*H)+'px'; box.hidden=false; };
  img.src=c.image;
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
});
load();
</script>"""


def main() -> int:
    global _PACK, _ROWS, _ORDER
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()

    _PACK = args.pack
    if not (_PACK / "review_sheet.csv").exists():
        print(f"review_sheet.csv not found under {_PACK}")
        return 2
    _ROWS = load_rows(_PACK)
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
