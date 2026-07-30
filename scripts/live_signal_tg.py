"""Live paper signals from the v10 detector, pushed to Telegram + Bark with a chart.

PAPER / SIM ONLY. Sends notifications (TG + Bark) and writes paper logs under
analysis/output/live_signals_v10/; places **no** orders, does **not** touch
models/ACTIVE, does **not** append forward_log.csv (see live disciplines 9-11).

Detection is restricted to tip / tip-1 / tip-2 windows (live discipline 12).
Use --tip-only for strict tip-only fires (age ~0). Freshness gate is derived
from pipeline arithmetic and kept in sync with executor / dashboard (30 min).

Chart shows projected entry/TP/SL off the signal bar close. No real exits.

Exit policy for v10 paper: USE_STOP=True (TP5 / SL2). Per v10 measurement,
"TP only, no stop" was -4.64 bp excess; stop-inclusive is the documented default.

Usage (local/VPS):
  PYTHONPATH=. python3 scripts/live_signal_tg.py --dry-run
  PYTHONPATH=. python3 scripts/live_signal_tg.py --tip-only --send
  # cron/launchd every 15min on VPS (no --n-symbols to sweep full universe)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src import notify  # noqa: E402
from src.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, WINDOW, load_yolo_model, right_edge_to_bar,
)

# training layout on the Mac/3060, deployed flat under models/ on the VPS
WEIGHT_CANDIDATES = (
    # v10 primary (owner short star v10)
    PROJECT / "models" / "owner_short_star_v10.pt",
    PROJECT / "runs/detect/runs/detect/owner_short_star_v10/weights/best.pt",
    # fallback v9 (explicitly not promoted)
    PROJECT / "models" / "owner_short_star_v9.pt",
    PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt",
)
OUT_DIR = PROJECT / "analysis" / "output" / "live_signals_v10"
LOG_CSV = OUT_DIR / "paper_signals.csv"
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
# Paper exit for v10: TP 5xATR / SL 2xATR (USE_STOP=True).
# HANDOFF 2026-07-30: on v10 pool, "TP only, no stop" was -4.64 bp excess.
# Keep stop-inclusive to match production TP5/SL2 and documented v10 measurement.
USE_STOP = True
FRESH_GATE_MIN = 30.0            # live discipline 7 -- owner-set, checked not changed
MA_STYLE = {"sma20": "#2196f3", "ema20": "#ff9800", "sma60": "#00bcd4",
            "ema60": "#8bc34a", "sma120": "#9c27b0", "ema120": "#e91e63"}

# The Mac has CJK fonts, the VPS ships none, and matplotlib silently renders
# missing glyphs as tofu boxes -- a chart nobody can read is worse than an English
# one. Pick a CJK face if the host has one, otherwise label in English.
_CJK = ("PingFang SC", "Hiragino Sans GB", "Heiti TC", "STHeiti", "Songti SC",
        "Arial Unicode MS", "Noto Sans CJK SC", "Noto Sans CJK JP",
        "Source Han Sans SC", "WenQuanYi Zen Hei", "Droid Sans Fallback")


def _pick_font() -> bool:
    from matplotlib import font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    for name in _CJK:
        if name in have:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return True
    return False


CJK_OK = _pick_font()


def L(zh: str, en: str) -> str:
    """Chinese when the host can draw it, English when it would be tofu."""
    return zh if CJK_OK else en


def scan_symbol(fr: pd.DataFrame, model, tip_edge: int, conf: float) -> tuple | None:
    """Newest tip-aligned fire in this frame, or None. Causal: window ends at t."""
    tip = len(fr) - 1
    tmp = OUT_DIR / "_scan.png"
    for back in range(0, tip_edge + 1):
        t = tip - back
        if t < WINDOW:
            continue
        try:
            _, tf = render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
            res = model.predict(str(tmp), conf=conf, verbose=False, device="cpu")
        except Exception:  # noqa: BLE001
            continue
        r0 = res[0] if res else None
        if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
            continue
        for row, cf in zip(r0.boxes.xywhn.cpu().numpy(), r0.boxes.conf.cpu().numpy()):
            cx, w = float(row[0]), float(row[2])
            b1 = right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
            if b1 < WINDOW - 1 - tip_edge:
                continue
            b0 = right_edge_to_bar(cx - w / 2, 0.0, tf, n_bars=WINDOW)
            sig_i = t - (WINDOW - 1 - b1)
            return sig_i, (t - (WINDOW - 1 - max(0, b0)), sig_i), float(cf)
    return None


def draw(ind, sym: str, sig_i: int, box: tuple, conf: float,
         age_min: float, out_path: Path) -> dict:
    """Setup chart for a signal that has not been entered yet."""
    lo = max(0, sig_i - 110)
    hi = len(ind) - 1
    seg = ind.iloc[lo:hi + 1]
    x = mdates.date2num(pd.to_datetime(seg["open_time"], utc=True).dt.tz_localize(None))
    o, h, l, c = (seg[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))

    atr = float(ind["atr14"].iloc[sig_i])
    entry = float(ind["close"].iloc[sig_i])          # projection: next open ~ last close
    tp = entry - TP_MULT * atr
    sl = entry + SL_MULT * atr if USE_STOP else None

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(16, 10), dpi=110, sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04})
    w = (x[1] - x[0]) * 0.7 if len(x) > 1 else 0.005
    up = c >= o
    ax.vlines(x, l, h, color="#888", linewidth=0.8, zorder=2)
    ax.bar(x[up], (c - o)[up], w, bottom=o[up], color="#26a69a", zorder=3)
    ax.bar(x[~up], (o - c)[~up], w, bottom=c[~up], color="#ef5350", zorder=3)

    # shade the MA bundle so the compression that triggered the fire is visible
    mas = [seg[m].astype(float).to_numpy() for m in ALL_MA_COLS if m in seg]
    if mas:
        stack = np.vstack(mas)
        ax.fill_between(x, np.nanmin(stack, 0), np.nanmax(stack, 0),
                        color="#607d8b", alpha=0.16, zorder=1, label=L("均线束", "MA bundle"))
    for m, col in MA_STYLE.items():
        if m in seg:
            ax.plot(x, seg[m].astype(float), color=col, lw=1.0, label=m, zorder=4)

    b0, b1 = box
    if b0 is not None and lo <= b0 <= b1 <= hi:
        xs, xe = x[b0 - lo], x[b1 - lo]
        sub = seg.iloc[b0 - lo:b1 - lo + 1]
        ys, ye = float(sub["low"].min()), float(sub["high"].max())
        pad = (ye - ys) * 0.06
        ax.add_patch(Rectangle((xs, ys - pad), max(xe - xs, w), ye - ys + 2 * pad,
                               fill=False, edgecolor="#d32f2f", lw=2.2, zorder=6))
        ax.text(xs, ye + pad, f" v10 {L('检测', 'detect')} conf {conf:.2f}", fontsize=9,
                color="#d32f2f", va="bottom")

    xsig = x[sig_i - lo]
    ax.axvline(xsig, color="#455a64", ls="--", lw=1.3, zorder=5)
    levels = [(entry, "#1976d2", L("入场(预估=信号收盘)", "Entry (est. = signal close)"), "-"),
              (tp, "#2e7d32", f"{L('止盈', 'TP')} {TP_MULT:g}xATR", "--")]
    if sl is not None:
        levels.append((sl, "#c62828", f"{L('止损', 'SL')} {SL_MULT:g}xATR", "--"))
    for px, col, lab, ls in levels:
        ax.hlines(px, xsig, x[-1], color=col, lw=1.5, ls=ls, zorder=5)
        ax.text(x[-1], px, f"  {lab} {px:.6g}", va="center", fontsize=9, color=col)
    ax.plot([xsig], [entry], marker="v", ms=14, color="#1976d2", zorder=7)
    # the TP sits 5*ATR below entry and autoscale ignores hlines, so widen the
    # view or the take-profit level is drawn outside the axes and never seen
    ylo = min(float(np.nanmin(l)), tp)
    yhi = max(float(np.nanmax(h)), sl if sl is not None else float(np.nanmax(h)))
    pad = (yhi - ylo) * 0.06
    ax.set_ylim(ylo - pad, yhi + pad)

    vol = seg["volume"].astype(float).to_numpy() if "volume" in seg else np.zeros(len(x))
    axv.bar(x[up], vol[up], w, color="#26a69a", alpha=0.75)
    axv.bar(x[~up], vol[~up], w, color="#ef5350", alpha=0.75)
    if len(vol) > 20:
        axv.plot(x, pd.Series(vol).rolling(20).mean(), color="#455a64", lw=1.0)
    axv.set_ylabel(L("成交量", "Volume"), fontsize=9)
    axv.grid(alpha=0.15)

    t_sig = pd.to_datetime(ind["open_time"].iloc[sig_i], utc=True)
    rr = (entry - tp) / (sl - entry) if (sl and sl > entry) else float("nan")
    fresh = L("新鲜", "FRESH") if age_min <= FRESH_GATE_MIN else L("超门", "STALE")
    ax.set_title(
        f"{sym}  {L('做空信号', 'SHORT signal')}   conf {conf:.2f}   "
        f"{L('无止损·超时', 'no stop, timeout') if sl is None else f'R:R {rr:.1f}:1'}   "
        f"ATR {atr/entry*100:.2f}%\n"
        f"{L('信号bar', 'signal bar')} {t_sig:%m-%d %H:%M} UTC   "
        f"{L('距今', 'age')} {age_min:.0f} min "
        f"({fresh}, {L('门', 'gate')} {FRESH_GATE_MIN:.0f}min)   "
        f"{L('成本', 'cost')} taker {SWAP_TAKER*1e4:.0f}bp / maker {SWAP_MAKER*1e4:.0f}bp",
        fontsize=11, loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.legend(loc="upper left", fontsize=8, ncol=7, framealpha=0.9)
    ax.grid(alpha=0.15)
    ax.set_ylabel(L("价格", "Price"))
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return {"entry": entry, "tp": tp, "sl": sl, "atr": atr, "rr": rr,
            "horizon_bars": HORIZON, "use_stop": USE_STOP,
            "signal_time": str(t_sig)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=0, help="0 = whole universe")
    ap.add_argument("--tip-only", action="store_true",
                    help="accept only a fire on the tip bar (age 0 instead of ~15min)")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--send", action="store_true", help="push to Telegram")
    ap.add_argument("--dry-run", action="store_true", help="render only, no send")
    ap.add_argument("--max-send", type=int, default=8)
    ap.add_argument("--weights", default=None)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    weights = Path(args.weights) if args.weights else next(
        (w for w in WEIGHT_CANDIDATES if w.exists()), None)
    if weights is None or not weights.exists():
        print(f"权重缺失,试过: {[str(w) for w in WEIGHT_CANDIDATES]}")
        return 2
    print(f"权重: {weights}")
    tip_edge = 0 if args.tip_only else 2
    model = load_yolo_model(str(weights))
    series = list_series(bar="15m")
    syms = sorted({s for (_x, s) in series
                   if s.endswith("_USDT_SWAP") and not is_stockish(s)})
    if args.n_symbols:
        syms = syms[: args.n_symbols]
    now = datetime.now(timezone.utc)
    print(f"扫描 {len(syms)} 币   tip_edge={tip_edge}   conf={args.conf}   "
          f"现在 {now:%Y-%m-%d %H:%M} UTC", flush=True)

    hits, t0 = [], time.perf_counter()
    for k, sym in enumerate(syms, 1):
        try:
            fr = add_mas(load_series(series[("okx", sym)]))
        except Exception:  # noqa: BLE001
            continue
        if len(fr) < WINDOW + 5:
            continue
        got = scan_symbol(fr, model, tip_edge, args.conf)
        if got is None:
            continue
        sig_i, box, conf = got
        ind = add_indicators(fr)
        t_sig = pd.Timestamp(pd.to_datetime(ind["open_time"], utc=True).iloc[sig_i])
        age = (now - t_sig).total_seconds() / 60.0
        png = OUT_DIR / f"{sym}_{t_sig:%Y%m%d_%H%M}.png"
        meta = draw(ind, sym, sig_i, box, conf, age, png)
        hits.append({"symbol": sym, "conf": round(conf, 3),
                     "age_min": round(age, 1), "fresh": age <= FRESH_GATE_MIN,
                     "png": str(png), **meta})
        print(f"  [{k}/{len(syms)}] {sym}  conf {conf:.2f}  距今 {age:.0f}min", flush=True)
    wall = time.perf_counter() - t0

    hits.sort(key=lambda r: (not r["fresh"], -r["conf"]))
    fresh = [h for h in hits if h["fresh"]]
    print(f"\n扫描 {len(syms)} 币用时 {wall/60:.1f} 分钟   开火 {len(hits)}   "
          f"其中新鲜(<={FRESH_GATE_MIN:.0f}min) {len(fresh)}")
    if hits:
        ages = np.array([h["age_min"] for h in hits])
        print(f"信号年龄: p10={np.percentile(ages,10):.0f} p50={np.median(ages):.0f} "
              f"p90={np.percentile(ages,90):.0f} 分钟")

    if hits:
        df = pd.DataFrame(hits)
        df.insert(0, "scanned_at", now.isoformat())
        df.to_csv(LOG_CSV, mode="a", header=not LOG_CSV.exists(), index=False)
        print(f"纸面记录 -> {LOG_CSV}")

    if args.send and not args.dry_run:
        sent_tg = 0
        sent_bark = 0
        for h in fresh[: args.max_send]:
            # TG caption (HTML)
            cap = (f"<b>{h['symbol']}</b> 做空信号 (v10 纸面盘)\n"
                   f"conf {h['conf']:.2f} · R:R {h['rr']:.1f}:1 · "
                   f"ATR {h['atr']/h['entry']*100:.2f}%\n"
                   f"入场≈{h['entry']:.6g} · TP {h['tp']:.6g} · SL {h['sl']:.6g}\n"
                   f"信号 {h['signal_time'][:16]} UTC · 距今 {h['age_min']:.0f} 分钟\n"
                   f"<i>纸面信号,未下单</i>")
            if notify.send_photo(Path(h["png"]), cap):
                sent_tg += 1
            # Bark (plain title + body)
            btitle = f"{h['symbol']} 做空 v10"
            bbody = (f"conf {h['conf']:.2f}  ATR {h['atr']/h['entry']*100:.2f}%\n"
                     f"入场 {h['entry']:.6g}  TP {h['tp']:.6g}  SL {h['sl']:.6g}\n"
                     f"距今 {h['age_min']:.0f}min  {h['signal_time'][:16]} UTC")
            if notify.bark_send(btitle, bbody, group="fable-live", level="timeSensitive"):
                sent_bark += 1
        print(f"已推送 TG {sent_tg}/{len(fresh[:args.max_send])}  Bark {sent_bark}/{len(fresh[:args.max_send])}")
    elif args.send:
        print("--dry-run 与 --send 同时给出:只渲染,不推送")

    (OUT_DIR / "last_scan.json").write_text(json.dumps(
        {"scanned_at": now.isoformat(), "n_symbols": len(syms),
         "tip_edge": tip_edge, "conf": args.conf, "wall_min": round(wall / 60, 2),
         "n_fired": len(hits), "n_fresh": len(fresh),
         "gate_min": FRESH_GATE_MIN, "hits": hits}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
