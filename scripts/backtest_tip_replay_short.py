#!/usr/bin/env python3
"""Honest tip-replay backtest for the SHORT detector, priced at the real route.

Same replay discipline as backtest_tip_replay.py -- for each bar t the detector
sees only [t-199, t], fires under the A' edge gate, entry is the next bar's
open, and a trade exists only if the truncated view produced it. Two deliberate
differences:

  SIDE   short. Barriers mirror: TP is entry - TP_MULT*ATR, SL is entry +
         SL_MULT*ATR, and a same-bar double touch resolves to SL.
  COST   the long harness reports at FORWARD_COST (maker 0.06%), which this
         executor cannot reach: entry is ordType="market" and the OCO legs carry
         tpOrdPx/slOrdPx = "-1", i.e. market on trigger, so every leg is taker.
         SWAP_TAKER (0.10%, fees only, no slippage) is the floor. Results are
         reported across the whole ladder because the cost line is an owner
         decision and slippage on alt swaps is still unmeasured (the ledger has
         no paired fill/mark rows -- see p_execution_slippage.md).

Holdout (>= 2026-05-04) is refused without --allow-holdout, which needs owner
approval and consumption accounting.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/backtest_tip_replay_short.py \
      --weights runs/detect/runs/detect/owner_short_star_v6/weights/best.pt \
      --start 2026-02-03 --end 2026-05-03 --n-symbols 25 --tag v6_short
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import LEGACY_P0_ROUND_TRIP, SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    MIN_GAP_BARS,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT, HORIZON_BARS = 5.0, 2.0, 72
PREDICT_BATCH = 16
LADDER = [("SWAP_MAKER 0.06% (executor 拿不到)", SWAP_MAKER),
          ("SWAP_TAKER 0.10% (真实路由地板)", SWAP_TAKER),
          ("taker+0.05% 滑点 = 0.15%", 0.0015),
          ("LEGACY 0.20% (标注:不用于决策)", LEGACY_P0_ROUND_TRIP)]


def resolve_short(df: pd.DataFrame, t: int) -> dict | None:
    """Short: entry next open, TP below, SL above; same-bar double touch -> SL."""
    ei = t + 1
    if ei >= len(df):
        return None
    atr = float(df["atr14"].iloc[t]) if "atr14" in df else float("nan")
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(df["open"].iloc[ei])
    if entry <= 0:
        return None
    last_i = min(ei + HORIZON_BARS - 1, len(df) - 1)
    highs = df["high"].to_numpy()[ei:last_i + 1]
    lows = df["low"].to_numpy()[ei:last_i + 1]
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    hit_tp, hit_sl = lows <= tp, highs >= sl
    t1 = int(np.argmax(hit_tp)) if hit_tp.any() else len(lows)
    s1 = int(np.argmax(hit_sl)) if hit_sl.any() else len(highs)
    if t1 < s1:
        outcome, ret = "tp", 1 - tp / entry
    elif hit_sl.any():
        outcome, ret = "sl", 1 - sl / entry
    elif last_i - ei + 1 >= HORIZON_BARS:
        outcome, ret = "timeout", 1 - float(df["close"].iloc[last_i]) / entry
    else:
        return None
    return {"outcome": outcome, "gross_ret": float(ret),
            "entry_time": str(df["open_time"].iloc[ei])}


def replay(symbol: str, df, model, start, end, device):
    from src.judgment.candidates import add_indicators
    enriched = add_indicators(add_mas(df))
    times = pd.to_datetime(enriched["open_time"], utc=True)
    lo = max(int(np.searchsorted(times, start)), WINDOW)
    hi = int(np.searchsorted(times, end, side="right"))
    trades, n_fired, last_sig = [], 0, -(10 ** 9)
    tmp = PROJECT / "data" / f"_tipshort_{symbol}.png"
    batch = []

    def flush(items):
        nonlocal n_fired, last_sig
        if not items:
            return
        res = model.predict([str(p) for _, _, p in items], conf=DEFAULT_CONF,
                            verbose=False, device=device)
        for (t, tf, _), r in zip(items, res):
            b = r.boxes
            if b is None or len(b) == 0:
                continue
            fired = any(right_edge_to_bar(float(x[0]), float(x[2]), tf, n_bars=WINDOW)
                        >= WINDOW - TIP_EDGE_BARS for x in b.xywhn.cpu().numpy())
            if not fired:
                continue
            n_fired += 1
            if t - last_sig < MIN_GAP_BARS:
                continue
            tr = resolve_short(enriched, t)
            if tr is None:
                continue
            last_sig = t
            tr.update({"symbol": symbol, "signal_time": str(times.iloc[t])})
            trades.append(tr)

    for t in range(lo, hi):
        sub = enriched.iloc[t - WINDOW + 1:t + 1].reset_index(drop=True)
        p = tmp.with_name(f"{tmp.stem}_{t % PREDICT_BATCH}.png")
        try:
            _, tf = render_chart(sub, out_path=p)
        except Exception:  # noqa: BLE001
            continue
        batch.append((t, tf, p))
        if len(batch) >= PREDICT_BATCH:
            flush(batch)
            batch = []
    flush(batch)
    for k in range(PREDICT_BATCH):
        tmp.with_name(f"{tmp.stem}_{k}.png").unlink(missing_ok=True)
    return trades, n_fired


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--start", default="2026-02-03")
    ap.add_argument("--end", default="2026-05-03")
    ap.add_argument("--n-symbols", type=int, default=25)
    ap.add_argument("--symbols", nargs="*", help="explicit list; overrides the random sample")
    ap.add_argument("--tag", default="tip_replay_short")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--allow-holdout", action="store_true")
    args = ap.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    if end >= HOLDOUT_START and not args.allow_holdout:
        print(f"拒绝: end {end} 进入 holdout (>= {HOLDOUT_START})。需 owner 批准 + 记账。")
        return 2

    series = list_series(bar="15m")
    if args.symbols:
        syms = [s for s in args.symbols if ("okx", s) in series]
        missing = [s for s in args.symbols if ("okx", s) not in series]
        if missing:
            print(f"跳过(无数据): {missing}")
    else:
        syms = sorted({s for (_src, s) in series})
        random.Random(20260727).shuffle(syms)
        syms = syms[: args.n_symbols]
    model = load_yolo_model(args.weights)
    print(f"weights={args.weights}\n窗口 {start.date()} → {end.date()}  币 {len(syms)}")

    all_tr, fired = [], 0
    for i, sym in enumerate(syms, 1):
        try:
            df = load_series(series[("okx", sym)])
        except Exception:  # noqa: BLE001
            continue
        tr, nf = replay(sym, df, model, start, end, args.device)
        all_tr.extend(tr)
        fired += nf
        print(f"  [{i}/{len(syms)}] {sym}: 开火 {nf}  成交 {len(tr)}", flush=True)

    if not all_tr:
        print("没有任何交易")
        return 1
    g = np.array([t["gross_ret"] for t in all_tr])
    out = {"tag": args.tag, "weights": args.weights, "side": "short",
           "window": [str(start), str(end)], "n_symbols": len(syms),
           "n_fired": fired, "n_trades": len(all_tr),
           "barriers": f"TP{TP_MULT}/SL{SL_MULT}/{HORIZON_BARS}bar",
           "gross_mean": round(float(g.mean()), 5), "gross_PF": pf(g),
           "win_rate": round(float(np.mean([t["outcome"] == "tp" for t in all_tr])), 4),
           "by_cost": []}
    print(f"\n开火 {fired}  成交 {len(all_tr)}  毛均值 {g.mean():+.5f}  毛PF {pf(g)}  "
          f"胜率 {out['win_rate']*100:.1f}% (TP5/SL2 盈亏平衡 28.6%)")
    print(f"\n{'成本口径':<34} {'净均值':>10} {'PF':>7}")
    for name, c in LADDER:
        n = g - c
        row = {"cost_name": name, "cost": c, "net_mean": round(float(n.mean()), 5), "PF": pf(n)}
        out["by_cost"].append(row)
        print(f"{name:<34} {n.mean():>+10.5f} {str(pf(n)):>7}")

    dst = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    dst.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    pd.DataFrame(all_tr).to_csv(PROJECT / "analysis" / "output" / f"{args.tag}_trades.csv", index=False)
    print(f"\n-> {dst.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
