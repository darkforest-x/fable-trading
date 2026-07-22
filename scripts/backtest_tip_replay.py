#!/usr/bin/env python3
"""Honest tip-replay backtest: bar-by-bar, the detector sees ONLY the past.

Iron rule 12 backtest (owner 2026-07-23): the old stage-3 backtest discovered
candidates by scanning full history — every detection carried its printed
future, so PF 6.61 measured hindsight, not live edge. This harness replays
history exactly as the live pulse experiences it:

  for each bar t (the tip):  render [t-199, t] via the live pipeline
  (full-series MAs -> slice -> render_chart) -> YOLO predict (conf 0.30)
  -> A' edge gate (box right edge maps to t or t-1) -> signal at t
  -> enter next bar open -> TP5/SL2 / 72-bar timeout -> maker cost.

No look-ahead anywhere: a trade exists only if the detector fired on the
truncated view. Same-symbol MIN_GAP dedup matches live.

Holdout discipline: the accept window (>= 2026-05-04) is refused unless
--allow-holdout is passed (owner approval + consumption accounting required).

Usage (after v16 passes the golden gate):
  PYTHONPATH=. .venv/bin/python scripts/backtest_tip_replay.py \\
      --weights models/owner_v16_tipuni_cold.pt \\
      --start 2026-04-01 --end 2026-05-03 --n-symbols 30 --tag v16_discovery
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

from src.costs import FORWARD_COST  # noqa: E402  (maker 0.06% round-trip route)
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS  # noqa: E402
from src.judgment.labeling import HORIZON_BARS  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT = 5.0, 2.0
PREDICT_BATCH = 16


def resolve_trade(df: pd.DataFrame, t: int) -> dict | None:
    """Entry next bar open; TP5/SL2 on ATR at signal bar; 72-bar timeout."""
    entry_i = t + 1
    if entry_i >= len(df):
        return None
    atr = float(df["atr14"].iloc[t]) if "atr14" in df else float("nan")
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(df["open"].iloc[entry_i])
    last_i = min(entry_i + HORIZON_BARS - 1, len(df) - 1)
    highs = df["high"].to_numpy()[entry_i : last_i + 1]
    lows = df["low"].to_numpy()[entry_i : last_i + 1]
    upper, lower = entry + TP_MULT * atr, entry - SL_MULT * atr
    hit_up = highs >= upper
    hit_dn = lows <= lower
    up1 = int(np.argmax(hit_up)) if hit_up.any() else len(highs)
    dn1 = int(np.argmax(hit_dn)) if hit_dn.any() else len(highs)
    if up1 < dn1:
        outcome, ret = "tp", upper / entry - 1
    elif dn1 <= up1 and hit_dn.any():
        outcome, ret = "sl", lower / entry - 1  # same-bar double touch -> conservative SL
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        outcome, ret = "timeout", float(df["close"].iloc[last_i]) / entry - 1
    else:
        return None  # horizon not complete at data end -> unresolved, skip
    return {"outcome": outcome, "gross_ret": ret, "entry_time": str(df["open_time"].iloc[entry_i])}


def replay_symbol(symbol: str, df: pd.DataFrame, model, start: pd.Timestamp, end: pd.Timestamp,
                  device: str) -> tuple[list[dict], int]:
    from src.judgment.candidates import add_indicators

    enriched = add_mas(df)
    enriched = add_indicators(enriched)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    lo = int(np.searchsorted(times, start))
    hi = int(np.searchsorted(times, end, side="right"))
    lo = max(lo, WINDOW)
    trades: list[dict] = []
    n_fired = 0
    last_signal = -(10**9)
    tmp = PROJECT / "data" / f"_tip_replay_{symbol}.png"
    batch: list[tuple[int, object, Path]] = []

    def flush(batch_items):
        nonlocal n_fired, last_signal
        if not batch_items:
            return
        res = model.predict([str(p) for _, _, p in batch_items], conf=DEFAULT_CONF,
                            verbose=False, device=device)
        for (t, tf, _), r in zip(batch_items, res):
            boxes = r.boxes
            if boxes is None or len(boxes) == 0:
                continue
            fired = False
            for b in boxes.xywhn.cpu().numpy():
                cx, _, w, _ = map(float, b[:4])
                bar = right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
                if bar >= WINDOW - TIP_EDGE_BARS:  # A' edge gate, live-identical
                    fired = True
                    break
            if not fired:
                continue
            n_fired += 1
            if t - last_signal < MIN_GAP_BARS:
                continue
            trade = resolve_trade(enriched, t)
            if trade is None:
                continue
            last_signal = t
            trade.update({"symbol": symbol, "signal_time": str(times.iloc[t]),
                          "net_ret": trade["gross_ret"] - FORWARD_COST})
            trades.append(trade)

    for t in range(lo, hi):
        sub = enriched.iloc[t - WINDOW + 1 : t + 1].reset_index(drop=True)
        p = tmp.with_name(f"{tmp.stem}_{t % PREDICT_BATCH}.png")
        try:
            _, tf = render_chart(sub, out_path=p)
        except Exception:
            continue
        batch.append((t, tf, p))
        if len(batch) >= PREDICT_BATCH:
            flush(batch)
            batch = []
    flush(batch)
    return trades, n_fired


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--start", default="2026-04-01")
    ap.add_argument("--end", default="2026-05-03")
    ap.add_argument("--n-symbols", type=int, default=30)
    ap.add_argument("--symbols", nargs="*", help="explicit list; overrides --n-symbols sample")
    ap.add_argument("--tag", default="tip_replay")
    ap.add_argument("--device", default=None)
    ap.add_argument("--allow-holdout", action="store_true",
                    help="permit bars >= 2026-05-04 (owner approval + ledger REQUIRED)")
    args = ap.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1)
    if end > HOLDOUT_START and not args.allow_holdout:
        raise SystemExit(
            f"window touches holdout (>= {HOLDOUT_START.date()}). Owner approval + "
            "consumption accounting required; rerun with --allow-holdout after both."
        )

    model = load_yolo_model(args.weights)
    device = args.device
    if device is None:
        import torch
        device = "0" if torch.cuda.is_available() else "cpu"

    pool = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=WINDOW + 500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue  # frozen-ruler symbols stay out of every experiment
        pool.append((symbol, frame))
    random.seed(20260723)
    if args.symbols:
        chosen = [(s, f) for s, f in pool if s in set(args.symbols)]
    else:
        chosen = random.sample(pool, min(args.n_symbols, len(pool)))

    all_trades: list[dict] = []
    total_fired = 0
    bars_scanned = 0
    for i, (symbol, frame) in enumerate(chosen, 1):
        trades, fired = replay_symbol(symbol, frame, model, start, end, device)
        all_trades.extend(trades)
        total_fired += fired
        times = pd.to_datetime(frame["open_time"], utc=True)
        bars_scanned += int(((times >= start) & (times < end)).sum())
        print(f"[{i}/{len(chosen)}] {symbol}: fired={fired} trades={len(trades)}", flush=True)

    net = np.array([t["net_ret"] for t in all_trades]) if all_trades else np.array([])
    wins, losses = net[net > 0].sum() if net.size else 0.0, net[net < 0].sum() if net.size else 0.0
    summary = {
        "tag": args.tag,
        "weights": args.weights,
        "window": f"{args.start}..{args.end}",
        "protocol": "tip_replay: detector saw only bars <= t; entry t+1 open; "
                    "TP5/SL2/72bar; maker cost; A' edge gate; MIN_GAP dedup",
        "n_symbols": len(chosen),
        "bars_scanned": bars_scanned,
        "fired_raw": total_fired,
        "fire_per_1k_bars": round(1000 * total_fired / max(bars_scanned, 1), 3),
        "n_trades": int(net.size),
        "win_rate": round(float((net > 0).mean()), 4) if net.size else None,
        "profit_factor": round(float(wins / -losses), 3) if losses < 0 else None,
        "mean_net_per_trade": round(float(net.mean()), 5) if net.size else None,
        "total_net_units": round(float(net.sum()), 5) if net.size else None,
        "cost": FORWARD_COST,
        "holdout_touched": bool(end > HOLDOUT_START),
    }
    out = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    out.write_text(json.dumps({"summary": summary, "trades": all_trades}, indent=2,
                              ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
