"""TradingView-style trade charts: what the model actually did, trade by trade.

Aggregates hide the thing the owner wants to see. A win rate says the pool is
1.5 points above breakeven; it does not show whether the SL sits somewhere
absurd, whether entry lands after the move is spent, or whether the 72-bar
timeout keeps cutting winners short. Those are visible in one glance per trade
and invisible in any summary.

Each chart carries everything needed to judge a single trade without reading the
code: candles and the six MAs, the detection box where the model fired, the tip
line separating what it saw from what followed, entry / TP / SL as priced
horizontal levels, the exit marker, and every timestamp on the axis.

Scope note: the window is inside holdout, but this is the same 2026-05-04 to
07-16 span already consumed by holdout #9. Re-rendering trades from an
already-spent window is not a fresh consumption -- no new metric is computed
here and no threshold is chosen from it.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/render_trade_charts.py \
      --symbols-file /tmp/top20.txt --start 2026-07-09 --end 2026-07-16
"""
from __future__ import annotations

import argparse
import sys
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

from src.costs import SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    MIN_GAP_BARS,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v6/weights/best.pt"
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
CONF = 0.30              # scan at the default so nothing is hidden by a filter
MA_STYLE = {"sma20": "#2196f3", "ema20": "#ff9800", "sma60": "#00bcd4",
            "ema60": "#e91e63", "sma120": "#9c27b0", "ema120": "#607d8b"}


def resolve_short(ind: pd.DataFrame, t: int) -> dict | None:
    ei = t + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[t])
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(ind["open"].iloc[ei])
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    ht, hs = lo <= tp, hi >= sl
    t1 = int(np.argmax(ht)) if ht.any() else 10 ** 9
    s1 = int(np.argmax(hs)) if hs.any() else 10 ** 9
    if t1 < s1:
        out, px, off = "TP", tp, t1
    elif s1 < 10 ** 9:
        out, px, off = "SL", sl, s1
    else:
        off = last - ei
        out, px = "TIMEOUT", float(ind["close"].iloc[last])
    return {"entry_i": ei, "entry": entry, "tp": tp, "sl": sl, "atr": atr,
            "outcome": out, "exit_i": ei + off, "exit_px": px,
            "gross": 1 - px / entry}


def draw(ind, sym, sig_i, tr, box_bars, out_path: Path) -> None:
    lo = max(0, sig_i - 90)
    hi = min(len(ind) - 1, tr["exit_i"] + 20)
    seg = ind.iloc[lo:hi + 1]
    x = mdates.date2num(pd.to_datetime(seg["open_time"], utc=True).dt.tz_localize(None))
    o, h, l, c = (seg[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))

    fig, ax = plt.subplots(figsize=(16, 9), dpi=110)
    w = (x[1] - x[0]) * 0.7 if len(x) > 1 else 0.005
    up = c >= o
    ax.vlines(x, l, h, color="#888", linewidth=0.8, zorder=2)
    ax.bar(x[up], (c - o)[up], w, bottom=o[up], color="#26a69a", zorder=3)
    ax.bar(x[~up], (o - c)[~up], w, bottom=c[~up], color="#ef5350", zorder=3)
    for m, col in MA_STYLE.items():
        if m in seg:
            ax.plot(x, seg[m].astype(float), color=col, lw=1.1, label=m, zorder=4)

    # detection box, in the coordinates the model produced it
    b0, b1 = box_bars
    if b0 is not None and lo <= b0 and b1 <= hi:
        xs, xe = x[b0 - lo], x[b1 - lo]
        ys = float(seg["low"].iloc[max(0, b0 - lo):b1 - lo + 1].min())
        ye = float(seg["high"].iloc[max(0, b0 - lo):b1 - lo + 1].max())
        ax.add_patch(Rectangle((xs, ys), xe - xs, ye - ys, fill=False,
                               edgecolor="#d32f2f", lw=2.0, zorder=6))

    xs_sig = x[sig_i - lo]
    xe_ent = x[tr["entry_i"] - lo]
    xe_exit = x[tr["exit_i"] - lo]
    ax.axvline(xs_sig, color="#9e9e9e", ls="--", lw=1.2, zorder=5)
    ax.text(xs_sig, ax.get_ylim()[1], " 检测 tip", va="top", fontsize=9, color="#666")

    for px, col, lab in ((tr["entry"], "#1976d2", "入场"),
                         (tr["tp"], "#2e7d32", f"止盈 TP {TP_MULT}xATR"),
                         (tr["sl"], "#c62828", f"止损 SL {SL_MULT}xATR")):
        ax.hlines(px, xe_ent, x[-1], color=col, lw=1.4,
                  ls="-" if lab == "入场" else "--", zorder=5)
        ax.text(x[-1], px, f"  {lab} {px:.6g}", va="center", fontsize=9, color=col)

    ax.plot([xe_ent], [tr["entry"]], marker="v", ms=13, color="#1976d2", zorder=7)
    ok = tr["outcome"] == "TP"
    ax.plot([xe_exit], [tr["exit_px"]], marker="o", ms=11, zorder=7,
            color="#2e7d32" if ok else ("#c62828" if tr["outcome"] == "SL" else "#f9a825"))

    t_sig = pd.to_datetime(ind["open_time"].iloc[sig_i], utc=True)
    t_ent = pd.to_datetime(ind["open_time"].iloc[tr["entry_i"]], utc=True)
    t_exit = pd.to_datetime(ind["open_time"].iloc[tr["exit_i"]], utc=True)
    held = tr["exit_i"] - tr["entry_i"]
    net = tr["gross"] - SWAP_TAKER
    ax.set_title(
        f"{sym}  做空  {tr['outcome']}   毛 {tr['gross']*100:+.2f}%  净@taker {net*100:+.2f}%\n"
        f"检测 {t_sig:%m-%d %H:%M}  入场 {t_ent:%m-%d %H:%M} @ {tr['entry']:.6g}  "
        f"出场 {t_exit:%m-%d %H:%M} @ {tr['exit_px']:.6g}  持仓 {held} 根 ({held*15/60:.1f}h)  "
        f"ATR {tr['atr']/tr['entry']*100:.2f}%",
        fontsize=11, loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate(rotation=30)
    ax.legend(loc="upper left", fontsize=8, ncol=6, framealpha=0.9)
    ax.grid(alpha=0.15)
    ax.set_ylabel("价格")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--start", default="2026-07-09")
    ap.add_argument("--end", default="2026-07-16")
    ap.add_argument("--max-per-symbol", type=int, default=1)
    ap.add_argument("--out", type=Path,
                    default=PROJECT / "analysis" / "output" / "trade_charts")
    args = ap.parse_args()

    syms = [s.strip() for s in Path(args.symbols_file).read_text().split() if s.strip()]
    start, end = (pd.Timestamp(x, tz="UTC") for x in (args.start, args.end))
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for p in out.glob("*.png"):
        p.unlink()

    model = load_yolo_model(str(WEIGHTS))
    series = list_series(bar="15m")
    tmp = PROJECT / "data" / "_tc.png"
    made, rows = 0, []

    for sym in syms:
        key = ("okx", sym)
        if key not in series:
            print(f"  {sym}: 无数据")
            continue
        ind = add_indicators(add_mas(load_series(series[key])))
        times = pd.to_datetime(ind["open_time"], utc=True)
        a = max(int(np.searchsorted(times, start)), WINDOW)
        b = int(np.searchsorted(times, end, side="right"))
        picked, last = 0, -(10 ** 9)
        for t in range(a, b):
            if picked >= args.max_per_symbol:
                break
            if t - last < MIN_GAP_BARS:
                continue
            try:
                _, tf = render_chart(ind.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
                res = model.predict([str(tmp)], conf=CONF, verbose=False, device="cpu")[0]
            except Exception:  # noqa: BLE001
                continue
            bx = res.boxes
            if bx is None or len(bx) == 0:
                continue
            best = None
            for row, cf in zip(bx.xywhn.cpu().numpy(), bx.conf.cpu().numpy()):
                cx, bw = float(row[0]), float(row[2])
                rb = right_edge_to_bar(cx + bw / 2, 0.0, tf, n_bars=WINDOW)
                if (WINDOW - 1) - rb <= TIP_EDGE_BARS and (best is None or cf > best[0]):
                    lb = right_edge_to_bar(cx - bw / 2, 0.0, tf, n_bars=WINDOW)
                    best = (float(cf), t - (WINDOW - 1) + lb, t - (WINDOW - 1) + rb)
            if best is None:
                continue
            tr = resolve_short(ind, t)
            if tr is None:
                continue
            last = t
            picked += 1
            made += 1
            fn = out / f"{made:02d}_{sym}_{tr['outcome']}.png"
            draw(ind, sym, t, tr, (best[1], best[2]), fn)
            rows.append({"symbol": sym, "conf": round(best[0], 3),
                         "signal": str(times.iloc[t]), "outcome": tr["outcome"],
                         "gross": round(tr["gross"], 5),
                         "net_taker": round(tr["gross"] - SWAP_TAKER, 5)})
            print(f"  [{made:2d}] {sym:22s} conf={best[0]:.3f} {tr['outcome']:8s} "
                  f"毛 {tr['gross']*100:+6.2f}%")
    tmp.unlink(missing_ok=True)

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(out / "trades.csv", index=False)
        w = int((df.outcome == "TP").sum())
        print(f"\n{made} 张图 -> {out}")
        print(f"  TP {w}  SL {int((df.outcome=='SL').sum())}  "
              f"TIMEOUT {int((df.outcome=='TIMEOUT').sum())}  "
              f"毛均值 {df.gross.mean()*100:+.2f}%  净@taker {df.net_taker.mean()*100:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
