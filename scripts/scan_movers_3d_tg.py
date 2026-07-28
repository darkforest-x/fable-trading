"""Scan majors plus the biggest 3-day movers with v9, chart every fire, push to Telegram.

What this can and cannot say has to be stated up front, because the charts look
authoritative and only half of the stack behind them is validated.

The detector is. v9 matches the owner's short gold at 84.0% recall, 0.596 median
IoU, width ratio 1.00 and zero right-edge offset, so a box drawn here is a box
the owner would plausibly have drawn.

The rest is not. The judgment layer cannot be shown to rank at all on the pool
built so far -- top-decile lift sits inside the noise at this sample size -- and
the honest tip-replay holdout of the whole chain came out at PF 0.784, -0.234%
per trade. So each chart is "v9 saw the pattern here", never "this trade makes
money", and the caption says so.

Symbol selection is two lists, kept separate so neither hides the other:
  MAJORS   the liquid names, always scanned whether or not they moved
  MOVERS   ranked by |3-day return|, since a short setup needs something that
           ran first, and ranking by absolute move keeps both directions visible

Three timeframes on request, but only one of them means anything. v9 was trained
on 15m renders and every constant downstream -- the MA set, ATR14, the 5x/2x
barriers, the 72-bar horizon -- is calibrated for that bar. 5m and 3m are
out-of-distribution inference: worth looking at, not evidence, and stamped as
such on every figure and in the summary.

Inference runs on Apple MPS, measured at 36ms per window against 306ms on CPU.
Three timeframes over 28 symbols is ~72k windows, which is 43 minutes on the GPU
and six hours on the CPU.

Scanning is causal per bar with the tip-edge gate applied; only the forward
simulation that draws the exit looks ahead, and a trade whose horizon has not
finished is drawn as still open.

PAPER ONLY: no orders, no ACTIVE change, no forward_log write.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/scan_movers_3d_tg.py --dry-run
  PYTHONPATH=. .venv/bin/python scripts/scan_movers_3d_tg.py --send
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src import notify  # noqa: E402
from src.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)
from scripts.scan_recent_multi_tf import WEIGHT_CANDIDATES, draw, simulate  # noqa: E402

OUT_DIR = PROJECT / "analysis" / "output" / "movers_3d"
DATA_DIR = PROJECT / "analysis" / "output" / "movers_3d_data"
# 200 bars of context sit behind the first scanned bar, so each timeframe needs
# more history than the scan window itself: 200x3min is 10h, 200x15min is 50h.
TF_FETCH_DAYS = {"15m": 8, "5m": 5, "3m": 5}
TF_BARS_PER_DAY = {"15m": 96, "5m": 288, "3m": 480}
MAJORS = ["BTC_USDT_SWAP", "ETH_USDT_SWAP", "SOL_USDT_SWAP", "BNB_USDT_SWAP",
          "XRP_USDT_SWAP", "DOGE_USDT_SWAP", "ADA_USDT_SWAP", "AVAX_USDT_SWAP",
          "LINK_USDT_SWAP", "LTC_USDT_SWAP"]
N_MOVERS = 20
DAYS = 3
BARS_PER_DAY = 96          # 15m
MIN_GAP = 8


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=DAYS)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--n-movers", type=int, default=N_MOVERS)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-send", type=int, default=25)
    ap.add_argument("--timeframes", default="15m,5m,3m")
    ap.add_argument("--device", default=None, help="default: mps if available")
    args = ap.parse_args()
    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    device = args.device
    if device is None:
        try:
            import torch
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"

    weights = next((w for w in WEIGHT_CANDIDATES if w.exists()), None)
    if weights is None:
        print("v9 权重缺失")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    series = list_series(bar="15m")
    syms = sorted({s for (_x, s) in series
                   if s.endswith("_USDT_SWAP") and not is_stockish(s)})

    # rank by |3-day return|: a short setup needs something that ran first, and
    # taking the absolute move keeps fallers visible beside risers
    span = int(args.days * BARS_PER_DAY)
    ranked = []
    frames: dict[str, pd.DataFrame] = {}
    for s in syms:
        try:
            fr = load_series(series[("okx", s)])
        except Exception:  # noqa: BLE001
            continue
        if len(fr) < WINDOW + span + 2:
            continue
        c = fr["close"].astype(float).to_numpy()
        chg = c[-1] / c[-span - 1] - 1
        if not np.isfinite(chg):
            continue
        frames[s] = fr
        ranked.append((s, float(chg)))
    ranked.sort(key=lambda kv: -abs(kv[1]))
    movers = [s for s, _ in ranked[: args.n_movers]]
    chg_of = dict(ranked)
    targets = [s for s in MAJORS if s in frames] + [s for s in movers if s not in MAJORS]

    print(f"权重 {weights.name}   窗口 最近 {args.days:g} 天   conf {args.conf}   "
          f"周期 {','.join(tfs)}   推理设备 {device}")
    print(f"主流币 {len([s for s in MAJORS if s in frames])} 个 + "
          f"涨跌幅前 {len(movers)} → 合计 {len(targets)} 个\n")
    print("涨跌幅榜前 10:")
    for s, c in ranked[:10]:
        print(f"  {s.replace('_USDT_SWAP',''):<10} {c*100:+7.1f}%")
    print()

    # 15m already sits in data/kline_fetched; 3m and 5m have to be pulled for the
    # chosen names only, with enough history for the 200-bar context behind the
    # first scanned bar rather than only the scan window itself
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for tf in tfs:
        if tf == "15m":
            continue
        import subprocess
        print(f"拉取 {tf} 数据({len(targets)} 币 x {TF_FETCH_DAYS.get(tf,5)} 天)…",
              flush=True)
        subprocess.run(
            [sys.executable, "-m", "src.data.fetch_okx", "--symbols", *targets,
             "--bar", tf, "--days", str(TF_FETCH_DAYS.get(tf, 5)),
             "--workers", "6", "--out-dir", str(DATA_DIR)],
            cwd=PROJECT, capture_output=True, text=True)

    def load_tf(sym: str, tf: str) -> pd.DataFrame | None:
        if tf == "15m":
            return frames.get(sym)
        hits = sorted(DATA_DIR.glob(f"okx_{sym}_{tf}_*.csv"))
        if not hits:
            return None
        d = pd.read_csv(hits[-1])
        d["open_time"] = pd.to_datetime(d["open_time"], utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        return d.sort_values("open_time").reset_index(drop=True)

    model = load_yolo_model(str(weights))
    tmp = OUT_DIR / "_w.png"
    rows, t0 = [], time.perf_counter()

    for tf in tfs:
        span_tf = int(args.days * TF_BARS_PER_DAY[tf])
        oos = "" if tf == "15m" else "  [分布外]"
        print(f"\n===== {tf}{oos} =====", flush=True)
        for k, sym in enumerate(targets, 1):
            base = load_tf(sym, tf)
            if base is None or len(base) < WINDOW + 4:
                print(f"[{k}/{len(targets)}] {sym:<20} {tf}  数据不足,跳过", flush=True)
                continue
            fr = add_mas(base)
            ind = add_indicators(fr)
            start = max(WINDOW, len(fr) - span_tf)
            fires, last = [], -10 ** 9
            for t in range(start, len(fr)):
                try:
                    _, tform = render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
                    res = model.predict(str(tmp), conf=args.conf, verbose=False,
                                        device=device)
                except Exception:  # noqa: BLE001
                    continue
                r0 = res[0] if res else None
                if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
                    continue
                for row, cf in zip(r0.boxes.xywhn.cpu().numpy(),
                                   r0.boxes.conf.cpu().numpy()):
                    cx, w = float(row[0]), float(row[2])
                    b1 = right_edge_to_bar(cx, w, tform, n_bars=WINDOW)
                    if b1 < WINDOW - 1 - TIP_EDGE_BARS:
                        continue
                    sig_i = t - (WINDOW - 1 - b1)
                    if sig_i - last < MIN_GAP:
                        break
                    b0 = right_edge_to_bar(cx - w / 2, 0.0, tform, n_bars=WINDOW)
                    fires.append((sig_i, (t - (WINDOW - 1 - max(0, b0)), sig_i),
                                  float(cf)))
                    last = sig_i
                    break
            tag = "主流" if sym in MAJORS else "榜单"
            print(f"[{k}/{len(targets)}] {sym:<20} {tf:>4} {tag}  "
                  f"3日 {chg_of.get(sym,0)*100:+6.1f}%  扫 {len(fr)-start}  "
                  f"开火 {len(fires)}", flush=True)

            for sig_i, box, conf in fires:
                tr = simulate(ind, sig_i)
                if tr.get("status") == "no_entry":
                    continue
                t_sig = pd.to_datetime(ind["open_time"].iloc[sig_i], utc=True)
                png = OUT_DIR / f"{sym}_{tf}_{t_sig:%m%d_%H%M}.png"
                draw(ind, sym, tf, sig_i, box, conf, tr, png)
                rows.append({"symbol": sym, "tf": tf, "group": tag,
                             "chg_3d": round(chg_of.get(sym, 0), 4),
                             "signal_time": str(t_sig), "conf": round(conf, 3),
                             "status": tr["status"], "outcome": tr["why"],
                             "entry": tr["entry"], "tp": tr["tp"], "sl": tr["sl"],
                             "atr": tr["atr"],
                             "held_bars": tr["exit_i"] - tr["entry_i"],
                             "gross": round(tr["gross"], 5),
                             "net_taker": round(tr["gross"] - SWAP_TAKER, 5),
                             "in_distribution": tf == "15m",
                             "png": str(png)})
    tmp.unlink(missing_ok=True)
    wall = time.perf_counter() - t0

    if not rows:
        print("\n无信号")
        return 0
    d = pd.DataFrame(rows)
    d.to_csv(OUT_DIR / "signals.csv", index=False)
    closed = d[d["status"] == "closed"]
    print(f"\n{'周期':>5} {'信号':>5} {'已了结':>7} {'TP':>4} {'SL':>4} {'超时':>5} "
          f"{'净@taker':>11}  说明")
    for tf in tfs:
        g = d[d["tf"] == tf]
        if g.empty:
            continue
        gc = g[g["status"] == "closed"]
        mean = f"{gc['net_taker'].mean()*100:+.3f}%" if len(gc) else "—"
        note = "" if tf == "15m" else "← 分布外,不构成验证"
        print(f"{tf:>5} {len(g):>5} {len(gc):>7} "
              f"{int((gc['outcome']=='TP').sum()):>4} {int((gc['outcome']=='SL').sum()):>4} "
              f"{int((gc['outcome']=='TIMEOUT').sum()):>5} {mean:>11}  {note}")
    print(f"\n扫 {len(targets)} 币 x {len(tfs)} 周期用时 {wall/60:.1f} 分钟   信号 {len(d)} 条"
          f"(已了结 {len(closed)},持仓中 {len(d)-len(closed)})")
    if len(closed):
        print(f"已了结:TP {int((closed['outcome']=='TP').sum())} · "
              f"SL {int((closed['outcome']=='SL').sum())} · "
              f"超时 {int((closed['outcome']=='TIMEOUT').sum())} · "
              f"净@taker 均值 {closed['net_taker'].mean()*100:+.3f}%")

    if args.send and not args.dry_run:
        head = (f"<b>v9 扫描 · 最近 {args.days:g} 天 · {'/'.join(tfs)}</b>\n"
                f"主流币 {len([s for s in MAJORS if s in frames])} + 涨跌幅榜前 "
                f"{len(movers)} = {len(targets)} 个币\n"
                f"信号 <b>{len(d)}</b> 条(已了结 {len(closed)},持仓中 "
                f"{len(d)-len(closed)})\n")
        if len(closed):
            head += (f"已了结:TP {int((closed['outcome']=='TP').sum())} · "
                     f"SL {int((closed['outcome']=='SL').sum())} · "
                     f"超时 {int((closed['outcome']=='TIMEOUT').sum())} · "
                     f"净@taker {closed['net_taker'].mean()*100:+.3f}%\n")
        head += ("\n<b>怎么看这批图</b>\n"
                 "检测层 v9 已验证(对 owner 金标召回 84.0%,IoU 0.596)——"
                 "<b>框画在哪是可信的</b>。\n"
                 "但判断层还测不出排序能力,整条链路 tip-replay 终审是 "
                 "<b>PF 0.784 / 每笔 -0.234%</b>。\n"
                 "<b>所以每张图只说「v9 在这里看见了形态」,不说「这笔会赚钱」。</b>\n\n"
                 "另:v9 只在 <b>15m</b> 上训练过,5m/3m 属分布外推理,"
                 "图上和标题都有标注 —— 那两组可以看,但不构成任何验证。")
        print("摘要:", notify.send(head))
        sent = 0
        for _, r in d.sort_values("conf", ascending=False).head(args.max_send).iterrows():
            state = r["outcome"] if r["status"] == "closed" else "持仓中"
            oos = "" if r["tf"] == "15m" else " · ⚠️分布外"
            cap = (f"<b>{r['symbol']}</b> {r['tf']} 做空信号 · {r['group']} · "
                   f"3日 {r['chg_3d']*100:+.1f}%{oos}\n"
                   f"conf {r['conf']:.2f} · ATR {r['atr']/r['entry']*100:.2f}% · "
                   f"{state}\n"
                   f"入场 {r['entry']:.6g} · TP {r['tp']:.6g} · SL {r['sl']:.6g}\n"
                   f"信号 {r['signal_time'][:16]} UTC · "
                   f"毛 {r['gross']*100:+.2f}% · 净@taker {r['net_taker']*100:+.2f}%\n"
                   f"<i>纸面信号,未下单</i>")
            if notify.send_photo(Path(r["png"]), cap):
                sent += 1
        print(f"已推送 {sent}/{min(len(d), args.max_send)} 张到 TG")

    (OUT_DIR / "summary.json").write_text(json.dumps(
        {"weights": weights.name, "days": args.days, "conf": args.conf,
         "majors": [s for s in MAJORS if s in frames], "movers": movers,
         "timeframes": tfs, "device": device,
         "n_signals": len(rows), "wall_min": round(wall / 60, 2),
         "rows": rows}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
