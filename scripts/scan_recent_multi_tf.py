"""Run v9 over the last N hours of BTC/ETH at 15m, 5m and 3m, and chart each fire.

The owner asked to see the current detector working on fresh data across three
timeframes. Two things have to be said plainly rather than buried in the output.

v9 IS A 15m MODEL. It was trained only on 15m renders of the owner's short gold
tips, and every constant downstream of it is calibrated for that bar: the MA set
(20/60/120), ATR14, the 5xATR/2xATR barriers and the 72-bar horizon. Running it
on 5m and 3m is out-of-distribution inference. The 5m/3m panels are worth looking
at, but they are a demonstration, not evidence -- no gold exists at those bars,
so nothing here can validate them.

CONTEXT IS NOT SCAN RANGE. A detection window is 200 bars, so scanning the last
20 hours needs 200 bars of history behind the first scanned bar, not 20 hours of
data. Each timeframe is loaded with its full file and only the requested tail is
scanned.

Scanning is causal at every bar: the window ends at t, the tip-edge gate applies,
and nothing to the right of the box is rendered -- the same discipline that
removed the look-ahead behind the old PF 6.61. The forward simulation that draws
entry/exit/TP/SL does look forward, because that is the outcome being shown; a
signal whose horizon has not finished yet is drawn as still open and excluded
from the summary statistics.

Read-only. No orders, no promote, no config change.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/scan_recent_multi_tf.py --hours 20
"""
from __future__ import annotations

import argparse
import json
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
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)
from scripts.live_signal_tg import L, MA_STYLE  # noqa: E402  (font-aware labels)

DATA_DIR = PROJECT / "analysis" / "output" / "btc_eth_scan"
OUT_DIR = PROJECT / "analysis" / "output" / "btc_eth_signals"
WEIGHT_CANDIDATES = (
    PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt",
    PROJECT / "models" / "owner_short_star_v9.pt",
)
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
BAR_MIN = {"15m": 15, "5m": 5, "3m": 3}
MIN_GAP = 8                    # bars between accepted fires, so one setup = one row


def load(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    if "open_time" in d.columns:
        d["open_time"] = pd.to_datetime(d["open_time"], utc=True)
    else:
        d["open_time"] = pd.to_datetime(d.iloc[:, 0], utc=True, unit="ms")
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.sort_values("open_time").reset_index(drop=True)


def simulate(ind, i: int) -> dict:
    """Forward outcome of a short from bar i. May still be open at the file's end."""
    ei = i + 1
    atr = float(ind["atr14"].iloc[i])
    if ei >= len(ind) or not np.isfinite(atr) or atr <= 0:
        return {"status": "no_entry"}
    entry = float(ind["open"].iloc[ei])
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    for j in range(len(cl)):
        if lo[j] <= tp:
            return {"status": "closed", "why": "TP", "entry_i": ei, "exit_i": ei + j,
                    "entry": entry, "tp": tp, "sl": sl, "atr": atr,
                    "exit_px": tp, "gross": 1 - tp / entry}
        if hi[j] >= sl:
            return {"status": "closed", "why": "SL", "entry_i": ei, "exit_i": ei + j,
                    "entry": entry, "tp": tp, "sl": sl, "atr": atr,
                    "exit_px": sl, "gross": 1 - sl / entry}
    done = (last - ei + 1) >= HORIZON
    px = float(cl[-1])
    return {"status": "closed" if done else "open",
            "why": "TIMEOUT" if done else "RUNNING",
            "entry_i": ei, "exit_i": last, "entry": entry, "tp": tp, "sl": sl,
            "atr": atr, "exit_px": px, "gross": 1 - px / entry}


def draw(ind, sym: str, tf: str, sig_i: int, box: tuple, conf: float,
         tr: dict, out_path: Path) -> None:
    lo_i = max(0, sig_i - 110)
    hi_i = min(len(ind) - 1, tr.get("exit_i", sig_i) + 15)
    seg = ind.iloc[lo_i:hi_i + 1]
    x = mdates.date2num(pd.to_datetime(seg["open_time"], utc=True).dt.tz_localize(None))
    o, h, l, c = (seg[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(16, 10), dpi=110, sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04})
    w = (x[1] - x[0]) * 0.7 if len(x) > 1 else 0.005
    up = c >= o
    ax.vlines(x, l, h, color="#888", lw=0.8, zorder=2)
    ax.bar(x[up], (c - o)[up], w, bottom=o[up], color="#26a69a", zorder=3)
    ax.bar(x[~up], (o - c)[~up], w, bottom=c[~up], color="#ef5350", zorder=3)

    mas = [seg[m].to_numpy(dtype=float) for m in ALL_MA_COLS if m in seg]
    if mas:
        st = np.vstack(mas)
        ax.fill_between(x, np.nanmin(st, 0), np.nanmax(st, 0), color="#607d8b",
                        alpha=0.16, zorder=1, label=L("均线束", "MA bundle"))
    for m, col in MA_STYLE.items():
        if m in seg:
            ax.plot(x, seg[m].astype(float), color=col, lw=1.0, label=m, zorder=4)

    b0, b1 = box
    if b0 is not None and lo_i <= b0 <= b1 <= hi_i:
        xs, xe = x[b0 - lo_i], x[b1 - lo_i]
        sub = seg.iloc[b0 - lo_i:b1 - lo_i + 1]
        ys, ye = float(sub["low"].min()), float(sub["high"].max())
        pad = (ye - ys) * 0.06
        ax.add_patch(Rectangle((xs, ys - pad), max(xe - xs, w), ye - ys + 2 * pad,
                               fill=False, edgecolor="#d32f2f", lw=2.2, zorder=6))
        ax.text(xs, ye + pad, f" v9 conf {conf:.2f}", fontsize=9,
                color="#d32f2f", va="bottom")

    xsig = x[sig_i - lo_i]
    ax.axvline(xsig, color="#455a64", ls="--", lw=1.3, zorder=5)
    ax.text(xsig, ax.get_ylim()[1], L(" 信号", " signal"), va="top",
            fontsize=9, color="#455a64")

    xent = x[tr["entry_i"] - lo_i]
    for px, col, lab, ls in ((tr["entry"], "#1976d2", L("入场", "Entry"), "-"),
                             (tr["tp"], "#2e7d32", f"{L('止盈','TP')} {TP_MULT:g}xATR", "--"),
                             (tr["sl"], "#c62828", f"{L('止损','SL')} {SL_MULT:g}xATR", "--")):
        ax.hlines(px, xent, x[-1], color=col, lw=1.5, ls=ls, zorder=5)
        ax.text(x[-1], px, f"  {lab} {px:.6g}", va="center", fontsize=9, color=col)
    ax.plot([xent], [tr["entry"]], marker="v", ms=14, color="#1976d2", zorder=7)

    col = {"TP": "#2e7d32", "SL": "#c62828"}.get(tr["why"], "#f9a825")
    if tr["exit_i"] - lo_i < len(x):
        ax.plot([x[tr["exit_i"] - lo_i]], [tr["exit_px"]], marker="o", ms=12,
                color=col, zorder=7)

    ylo = min(float(np.nanmin(l)), tr["tp"])
    yhi = max(float(np.nanmax(h)), tr["sl"])
    pad = (yhi - ylo) * 0.06
    ax.set_ylim(ylo - pad, yhi + pad)

    vol = seg["volume"].to_numpy(dtype=float)
    axv.bar(x[up], vol[up], w, color="#26a69a", alpha=0.75)
    axv.bar(x[~up], vol[~up], w, color="#ef5350", alpha=0.75)
    if len(vol) > 20:
        axv.plot(x, pd.Series(vol).rolling(20).mean(), color="#455a64", lw=1.0)
    axv.set_ylabel(L("成交量", "Volume"), fontsize=9)
    axv.grid(alpha=0.15)

    t_sig = pd.to_datetime(ind["open_time"].iloc[sig_i], utc=True)
    t_ent = pd.to_datetime(ind["open_time"].iloc[tr["entry_i"]], utc=True)
    t_exit = pd.to_datetime(ind["open_time"].iloc[tr["exit_i"]], utc=True)
    held = tr["exit_i"] - tr["entry_i"]
    net = tr["gross"] - SWAP_TAKER
    oos = "" if tf == "15m" else L("  [分布外:v9 只在 15m 训过]",
                                   "  [OUT-OF-DISTRIBUTION: v9 trained on 15m only]")
    state = tr["why"] if tr["status"] == "closed" else L("持仓中", "RUNNING")
    ax.set_title(
        f"{sym}  {tf}  {L('做空','SHORT')}  {state}   "
        f"{L('毛','gross')} {tr['gross']*100:+.2f}%  "
        f"{L('净@taker','net@taker')} {net*100:+.2f}%   conf {conf:.2f}{oos}\n"
        f"{L('信号','signal')} {t_sig:%m-%d %H:%M}  "
        f"{L('入场','entry')} {t_ent:%m-%d %H:%M} @ {tr['entry']:.6g}  "
        f"{L('出场','exit')} {t_exit:%m-%d %H:%M} @ {tr['exit_px']:.6g}  "
        f"{L('持仓','held')} {held} bar ({held*BAR_MIN[tf]/60:.1f}h)  "
        f"ATR {tr['atr']/tr['entry']*100:.2f}%",
        fontsize=11, loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.legend(loc="upper left", fontsize=8, ncol=7, framealpha=0.9)
    ax.grid(alpha=0.15)
    ax.set_ylabel(L("价格", "Price"))
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=20.0)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    args = ap.parse_args()

    weights = next((w for w in WEIGHT_CANDIDATES if w.exists()), None)
    if weights is None:
        print("v9 权重缺失")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model = load_yolo_model(str(weights))
    print(f"权重 {weights.name}   扫描窗口 最近 {args.hours:g} 小时   conf {args.conf}\n")

    tmp = OUT_DIR / "_w.png"
    rows = []
    for path in sorted(DATA_DIR.glob("okx_*_*.csv")):
        parts = path.stem.split("_")
        tf = next((p for p in parts if p in BAR_MIN), None)
        if tf is None:
            continue
        sym = "_".join(parts[1:parts.index(tf)])
        fr = add_mas(load(path))
        ind = add_indicators(fr)
        if len(fr) < WINDOW + 2:
            print(f"{sym} {tf}: 只有 {len(fr)} 根,不足 {WINDOW}+2,跳过")
            continue
        cutoff = fr["open_time"].iloc[-1] - pd.Timedelta(hours=args.hours)
        start = max(WINDOW, int((fr["open_time"] < cutoff).sum()))
        fires, last = [], -10 ** 9
        for t in range(start, len(fr)):
            try:
                _, tform = render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
                res = model.predict(str(tmp), conf=args.conf, verbose=False, device="cpu")
            except Exception:  # noqa: BLE001
                continue
            r0 = res[0] if res else None
            if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
                continue
            for row, cf in zip(r0.boxes.xywhn.cpu().numpy(), r0.boxes.conf.cpu().numpy()):
                cx, w = float(row[0]), float(row[2])
                b1 = right_edge_to_bar(cx, w, tform, n_bars=WINDOW)
                if b1 < WINDOW - 1 - TIP_EDGE_BARS:
                    continue
                sig_i = t - (WINDOW - 1 - b1)
                if sig_i - last < MIN_GAP:
                    break
                b0 = right_edge_to_bar(cx - w / 2, 0.0, tform, n_bars=WINDOW)
                fires.append((sig_i, (t - (WINDOW - 1 - max(0, b0)), sig_i), float(cf)))
                last = sig_i
                break
        print(f"{sym} {tf}: 扫 {len(fr)-start} 根, 开火 {len(fires)}", flush=True)

        for sig_i, box, conf in fires:
            tr = simulate(ind, sig_i)
            if tr["status"] == "no_entry":
                continue
            t_sig = pd.to_datetime(ind["open_time"].iloc[sig_i], utc=True)
            png = OUT_DIR / f"{sym}_{tf}_{t_sig:%m%d_%H%M}.png"
            draw(ind, sym, tf, sig_i, box, conf, tr, png)
            rows.append({"symbol": sym, "tf": tf, "signal_time": str(t_sig),
                         "conf": round(conf, 3), "status": tr["status"],
                         "outcome": tr["why"], "entry": tr["entry"],
                         "tp": tr["tp"], "sl": tr["sl"],
                         "held_bars": tr["exit_i"] - tr["entry_i"],
                         "gross": round(tr["gross"], 5),
                         "net_taker": round(tr["gross"] - SWAP_TAKER, 5),
                         "png": png.name,
                         "in_distribution": tf == "15m"})
    tmp.unlink(missing_ok=True)

    if not rows:
        print("\n无信号")
        return 0
    d = pd.DataFrame(rows)
    d.to_csv(OUT_DIR / "signals.csv", index=False)
    print(f"\n{'周期':>5} {'信号':>5} {'已了结':>7} {'TP':>4} {'SL':>4} {'超时':>5} "
          f"{'净@taker均值':>13}  说明")
    for tf in ("15m", "5m", "3m"):
        g = d[d["tf"] == tf]
        if g.empty:
            continue
        cl = g[g["status"] == "closed"]
        note = "" if tf == "15m" else "← 分布外,不构成验证"
        mean = f"{cl['net_taker'].mean()*100:+.3f}%" if len(cl) else "—"
        print(f"{tf:>5} {len(g):>5} {len(cl):>7} "
              f"{int((cl['outcome']=='TP').sum()):>4} {int((cl['outcome']=='SL').sum()):>4} "
              f"{int((cl['outcome']=='TIMEOUT').sum()):>5} {mean:>13}  {note}")
    print(f"\n图 {len(rows)} 张 -> {OUT_DIR}")
    (OUT_DIR / "summary.json").write_text(json.dumps(
        {"weights": weights.name, "hours": args.hours, "conf": args.conf,
         "n_signals": len(rows), "rows": rows}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
