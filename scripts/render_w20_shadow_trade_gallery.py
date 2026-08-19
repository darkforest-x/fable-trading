#!/usr/bin/env python3
"""Render K-line gallery for w20 midbox shadow book (closed + open).

Review chart: signal centered ±100 bars (same layout as tip galleries).
Detection was causal W=24; this page is for owner inspection only.

  PYTHONPATH=.:$HOME/yoyo-trading .venv/bin/python \\
    scripts/render_w20_shadow_trade_gallery.py
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("YOYO_DATA_ROOT", str(PROJECT))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

# Same series resolver as w20 builders (YOLO_DATA_ROOT / kline_cache quirks).
from scripts.build_w20_midbox_dataset import resolve_series  # noqa: E402

LOOKBACK = 100
LOOKAHEAD = 100
WINDOW = 24
TIP_EDGE = 2
DEFAULT_LOG = PROJECT / "analysis" / "output" / "forward_log_w20_midbox_shadow.csv"
DEFAULT_OUT = PROJECT / "analysis" / "output" / "w20_shadow_trade_gallery"


def _flat_pad_rows(row: pd.Series, n: int, *, side: str) -> pd.DataFrame:
    if n <= 0:
        return pd.DataFrame(columns=row.index)
    base = row.to_dict()
    px = float(row.get("close", row.get("open", 0.0)) or 0.0)
    for k in ("open", "high", "low", "close"):
        if k in base:
            base[k] = px
    rows = [base.copy() for _ in range(n)]
    return pd.DataFrame(rows)


def resolve_signal_i(fr: pd.DataFrame, row: pd.Series) -> int | None:
    if "signal_i" in row and pd.notna(row["signal_i"]):
        i = int(row["signal_i"])
        if 0 <= i < len(fr):
            return i
    t = pd.to_datetime(row.get("signal_time"), utc=True, errors="coerce")
    if pd.isna(t):
        return None
    ot = pd.to_datetime(fr["open_time"], utc=True, errors="coerce")
    mask = ot == t
    if bool(mask.any()):
        return int(np.flatnonzero(mask.to_numpy())[0])
    if ot.isna().all():
        return None
    # nearest bar by absolute time delta
    deltas = (ot - t).abs().to_numpy()
    return int(np.nanargmin(deltas))


def draw_trade_chart(
    fr: pd.DataFrame,
    *,
    sig_i: int,
    symbol: str,
    score: float,
    side: str,
    outcome: str,
    status: str,
    net_bp: float | None,
    out_path: Path,
) -> None:
    if "sma20" not in fr.columns:
        fr = add_mas(fr)
    n = len(fr)
    half_l, half_r = LOOKBACK, LOOKAHEAD
    i0 = max(0, sig_i - half_l)
    i1 = min(n - 1, sig_i + half_r)
    before_real = sig_i - i0
    after_real = i1 - sig_i
    pad_left = half_l - before_real
    pad_right = half_r - after_real
    view = fr.iloc[i0 : i1 + 1].copy()
    if pad_left > 0:
        view = pd.concat(
            [_flat_pad_rows(view.iloc[0], pad_left, side="left"), view],
            ignore_index=True,
        )
    if pad_right > 0:
        view = pd.concat(
            [view, _flat_pad_rows(view.iloc[-1], pad_right, side="right")],
            ignore_index=True,
        )
    view = add_mas(view)
    img, tf = render_chart(view, out_path=None)
    n_local = len(view)
    loc_sig = half_l

    # tip W=24 ending at signal (decision bar = tip)
    win_end_abs = sig_i
    win_start_abs = max(0, win_end_abs - WINDOW + 1)
    # small tip box: last TIP_EDGE+1 bars of window
    box0_abs = max(win_start_abs, win_end_abs - TIP_EDGE)
    box1_abs = win_end_abs
    loc0 = int(box0_abs - i0 + pad_left)
    loc1 = int(box1_abs - i0 + pad_left)
    loc0 = max(0, min(loc0, n_local - 1))
    loc1 = max(loc0, min(loc1, n_local - 1))
    hi = float(fr["high"].iloc[box0_abs : box1_abs + 1].max())
    lo = float(fr["low"].iloc[box0_abs : box1_abs + 1].min())
    y1, y2 = tf.y_at(hi), tf.y_at(lo)
    x1, x2 = tf.x_at(loc0), tf.x_at(loc1)
    if x2 < x1:
        x1, x2 = x2, x1
    if x2 - x1 < 6:
        x2 = x1 + 6
    # outcome color
    if status == "open":
        color = (200, 200, 80)  # cyan-ish
    elif str(outcome).lower() == "tp":
        color = (80, 200, 80)
    elif str(outcome).lower() == "sl":
        color = (60, 60, 220)
    else:
        color = (0, 180, 255)
    cv2.rectangle(img, (x1, min(y1, y2)), (x2, max(y1, y2)), color, 2, cv2.LINE_AA)

    xs = tf.x_at(min(max(loc_sig, 0), n_local - 1))
    cv2.line(img, (xs, 0), (xs, img.shape[0] - 1), (220, 220, 220), 1, cv2.LINE_AA)
    if loc_sig < n_local - 1:
        x_after = tf.x_at(min(loc_sig + 1, n_local - 1))
        x_end = tf.x_at(n_local - 1)
        overlay = img.copy()
        cv2.rectangle(overlay, (x_after, 0), (x_end, img.shape[0] - 1), (40, 40, 20), -1)
        cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)

    # caption bar
    bp_s = f"{net_bp:+.1f}bp" if net_bp is not None and np.isfinite(net_bp) else "—"
    cap = f"{symbol}  conf={score:.3f}  {side}  {status}/{outcome}  {bp_s}"
    cv2.rectangle(img, (0, 0), (min(img.shape[1], 980), 36), (255, 255, 255), -1)
    cv2.putText(
        img, cap, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (20, 20, 20), 2, cv2.LINE_AA
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def write_html(cards: list[dict], out_html: Path, *, summary: dict) -> None:
    # sort: closed first by |bp| then open
    def key(c):
        st = 0 if c["status"] == "closed" else 1
        bp = c.get("net_bp")
        bpv = abs(bp) if bp is not None and np.isfinite(bp) else -1
        return (st, -bpv, c.get("signal_time", ""))

    cards = sorted(cards, key=key)
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>",
        f"<title>w20 shadow K线 · {summary.get('n_closed',0)} closed</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;background:#0e1116;color:#e6edf3;margin:16px 20px 48px}",
        "h1{font-size:1.35rem;margin:0 0 6px}",
        ".meta{color:#8b949e;margin:0 0 14px;font-size:13.5px;line-height:1.5}",
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:8px;margin:0 0 16px}",
        ".stat{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:8px 10px}",
        ".stat .k{font-size:11px;color:#8b949e}.stat .v{font-size:1.15rem;font-weight:700}",
        ".dock{position:sticky;top:0;z-index:5;background:#0e1116f0;padding:8px 0 10px;"
        "backdrop-filter:blur(6px);border-bottom:1px solid #30363d;margin:0 0 14px;"
        "display:flex;flex-wrap:wrap;gap:6px}",
        ".dock button{border:1px solid #30363d;background:#21262d;color:#e6edf3;"
        "border-radius:999px;padding:4px 11px;font-size:12.5px;cursor:pointer}",
        ".dock button.active{background:#1f6feb;border-color:#1f6feb}",
        ".grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1800px}",
        "@media(max-width:1100px){.grid{grid-template-columns:1fr}}",
        ".card{border:1px solid #30363d;border-radius:12px;overflow:hidden;background:#161b22}",
        ".card.hidden{display:none}",
        ".card img{width:100%;display:block;background:#000}",
        ".cap{padding:9px 11px;font-size:12.5px;line-height:1.4}",
        ".tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;margin-right:4px}",
        ".tag.tp{background:#3fb95033;color:#3fb950}",
        ".tag.sl{background:#f8514933;color:#f85149}",
        ".tag.to{background:#d2992233;color:#d29922}",
        ".tag.open{background:#58a6ff33;color:#58a6ff}",
        "a{color:#58a6ff}",
        "</style></head><body>",
        f"<h1>w20 shadow · K 线画廊（{summary.get('n_closed',0)} closed + {summary.get('n_open',0)} open）</h1>",
        f"<p class='meta'>log=<code>{html.escape(summary.get('log',''))}</code> · "
        f"review 图：信号居中 ±{LOOKBACK}/±{LOOKAHEAD} · 检测几何 W={WINDOW} tip-edge≤{TIP_EDGE}<br>"
        f"execution_eligible=false · 不写主线 forward_log · 生成 {html.escape(summary.get('generated_at',''))}</p>",
        "<div class='cards'>",
        f"<div class='stat'><div class='k'>closed</div><div class='v'>{summary.get('n_closed')}</div></div>",
        f"<div class='stat'><div class='k'>open</div><div class='v'>{summary.get('n_open')}</div></div>",
        f"<div class='stat'><div class='k'>胜率</div><div class='v'>{summary.get('win_rate','—')}</div></div>",
        f"<div class='stat'><div class='k'>mean bp</div><div class='v'>{summary.get('mean_bp','—')}</div></div>",
        f"<div class='stat'><div class='k'>PF</div><div class='v'>{summary.get('pf','—')}</div></div>",
        "</div>",
        "<div class='dock' id='filt'>",
        "<button type='button' class='active' data-f='all'>全部</button>",
        "<button type='button' data-f='tp'>TP</button>",
        "<button type='button' data-f='sl'>SL</button>",
        "<button type='button' data-f='timeout'>timeout</button>",
        "<button type='button' data-f='open'>open</button>",
        "<button type='button' data-f='win'>赢</button>",
        "<button type='button' data-f='loss'>亏</button>",
        "</div>",
        "<div class='grid' id='grid'>",
    ]
    for c in cards:
        oc = str(c.get("outcome") or "").lower()
        st = c["status"]
        tags = []
        if st == "open":
            tags.append("<span class='tag open'>open</span>")
            filt = "open"
        else:
            tags.append(f"<span class='tag {oc if oc in ('tp','sl') else 'to'}'>{html.escape(oc or '?')}</span>")
            filt = oc if oc in ("tp", "sl", "timeout") else "closed"
        bp = c.get("net_bp")
        winloss = "win" if bp is not None and bp > 0 else ("loss" if bp is not None else "")
        bp_s = f"{bp:+.1f} bp" if bp is not None and np.isfinite(bp) else "—"
        rel = html.escape(c["img_rel"])
        parts.append(
            f"<div class='card' data-f='{filt}' data-wl='{winloss}'>"
            f"<a href='{rel}' target='_blank'><img src='{rel}' loading='lazy' alt=''/></a>"
            f"<div class='cap'>{''.join(tags)}"
            f"<b>{html.escape(c['symbol'])}</b> · conf {c['score']:.3f} · {html.escape(str(c.get('side') or ''))}"
            f" · <b>{bp_s}</b><br>"
            f"<span style='color:#8b949e'>{html.escape(str(c.get('signal_time','')))} → "
            f"{html.escape(str(c.get('entry_time','')))}</span>"
            f" <a href='{rel}' target='_blank'>原图</a></div></div>"
        )
    parts.append("</div>")
    parts.append(
        """<script>
const dock=document.getElementById('filt');
const cards=[...document.querySelectorAll('.card')];
dock.addEventListener('click',e=>{
  const b=e.target.closest('button'); if(!b) return;
  dock.querySelectorAll('button').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  const f=b.dataset.f;
  cards.forEach(c=>{
    let ok=true;
    if(f==='all') ok=true;
    else if(f==='win'||f==='loss') ok=c.dataset.wl===f;
    else ok=c.dataset.f===f;
    c.classList.toggle('hidden', !ok);
  });
});
</script></body></html>"""
    )
    out_html.write_text("".join(parts), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=0, help="0=all")
    args = ap.parse_args()

    df = pd.read_csv(args.log)
    if args.limit:
        df = df.head(args.limit)
    img_dir = args.out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    cards: list[dict] = []
    skips: dict[str, int] = {}
    series_cache: dict[str, pd.DataFrame] = {}

    for idx, row in df.iterrows():
        sym = str(row["symbol"])
        try:
            if sym not in series_cache:
                s = resolve_series(sym)
                if s is None or len(s) == 0:
                    skips["no_series"] = skips.get("no_series", 0) + 1
                    continue
                series_cache[sym] = add_mas(s)
            fr = series_cache[sym]
            sig_i = resolve_signal_i(fr, row)
            if sig_i is None:
                skips["no_sig_i"] = skips.get("no_sig_i", 0) + 1
                continue
            score = float(row["score"]) if pd.notna(row.get("score")) else 0.0
            ret = float(row["realized_ret"]) if pd.notna(row.get("realized_ret")) else None
            net_bp = ret * 10000 if ret is not None else None
            stem = f"{idx:04d}_{sym}_{sig_i}_{row['status']}"
            img_path = img_dir / f"{stem}.png"
            if not img_path.exists():
                draw_trade_chart(
                    fr,
                    sig_i=sig_i,
                    symbol=sym,
                    score=score,
                    side=str(row.get("side") or ""),
                    outcome=str(row.get("outcome") or ""),
                    status=str(row["status"]),
                    net_bp=net_bp,
                    out_path=img_path,
                )
            cards.append(
                {
                    "symbol": sym,
                    "signal_time": str(row.get("signal_time") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "score": score,
                    "side": str(row.get("side") or ""),
                    "outcome": str(row.get("outcome") or ""),
                    "status": str(row["status"]),
                    "net_bp": net_bp,
                    "img_rel": f"images/{img_path.name}",
                }
            )
            if len(cards) % 10 == 0:
                print(f"... {len(cards)} charts  skips={skips}", flush=True)
        except Exception as e:
            skips[type(e).__name__] = skips.get(type(e).__name__, 0) + 1
            continue

    closed = [c for c in cards if c["status"] == "closed"]
    open_ = [c for c in cards if c["status"] == "open"]
    wins = sum(1 for c in closed if c.get("net_bp") is not None and c["net_bp"] > 0)
    mean_bp = float(np.mean([c["net_bp"] for c in closed if c.get("net_bp") is not None])) if closed else float("nan")
    g = sum(c["net_bp"] for c in closed if c.get("net_bp") and c["net_bp"] > 0)
    l = -sum(c["net_bp"] for c in closed if c.get("net_bp") is not None and c["net_bp"] <= 0)
    pf = g / l if l > 0 else None

    summary = {
        "log": str(args.log),
        "n_closed": len(closed),
        "n_open": len(open_),
        "win_rate": f"{wins / len(closed):.1%}" if closed else "—",
        "mean_bp": f"{mean_bp:.1f}" if closed else "—",
        "pf": f"{pf:.3f}" if pf else "—",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skips": skips,
    }
    out_html = args.out / "index.html"
    write_html(cards, out_html, summary=summary)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # convenience copy into analysis/html for open
    html_copy = PROJECT / "analysis" / "html" / "w20_shadow_75_charts.html"
    # relative images from html/ would break — write redirect instead
    html_copy.write_text(
        f"""<!doctype html><meta charset=utf-8>
<meta http-equiv="refresh" content="0;url=../output/w20_shadow_trade_gallery/index.html">
<p><a href="../output/w20_shadow_trade_gallery/index.html">打开 shadow K 线画廊</a></p>
""",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    print(f"gallery → {out_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
