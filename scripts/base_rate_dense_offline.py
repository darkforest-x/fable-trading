#!/usr/bin/env python3
"""Base rate of the dense-cluster geometry itself — NO detector, NO judgment.

Why (owner 2026-07-23): every backtest so far conditioned on some YOLO detector
(v12/v14/v15/v16), all trained on hindsight semantics, so a negative like v16's
PF 0.78 cannot be cleanly blamed on "dense geometry has no alpha" — the detector
mixes 'launching-now' and 'already-launched' candidates. The judgment layer can
only purify if the raw geometry has positive expectancy. That base rate has
NEVER been measured in isolation.

This measures it cleanly: mark dense tips with the pure RULE (find_dense_segments
= fast_spread<=0.0028 & full_spread<=0.0055, run>=5 bars — causal MA thresholds,
zero "box before/after launch" semantics), enter next bar, resolve TP5/SL2/72bar
triple-barrier, subtract maker cost. Compare against a random-entry baseline to
test whether dense beats trading at random.

Discipline: <2026-05-04 only (never touches holdout), excludes frozen-eval
symbols, causal throughout. Rule thresholds are owner constants, not fit here.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/base_rate_dense_offline.py
  PYTHONPATH=. .venv/bin/python scripts/base_rate_dense_offline.py --n-symbols 0  # all
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from src.costs import FORWARD_COST  # noqa: E402  maker 0.06% round-trip
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FAST_MAX, FULL_MAX = 0.0028, 0.0055   # find_dense_segments thresholds
MIN_DENSE_BARS = 5
MIN_GAP_BARS = 18                     # same-episode dedup, matches live
TP_MULT, SL_MULT = 5.0, 2.0
WARMUP = 210                          # need MAs (EMA120/200) warmed before signals


def dense_tip_indices(enriched: pd.DataFrame) -> list[int]:
    """Causal: bar i where a run of >=MIN_DENSE_BARS dense bars *just* reached 5
    (emergence of the cluster), then MIN_GAP-deduped. Uses only data up to i."""
    fast = pd.to_numeric(enriched["fast_spread"], errors="coerce").to_numpy()
    full = pd.to_numeric(enriched["full_spread"], errors="coerce").to_numpy()
    dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
    # rolling count of consecutive dense bars ending at i
    run = np.zeros(len(dense), dtype=int)
    for i in range(len(dense)):
        run[i] = run[i - 1] + 1 if dense[i] and i > 0 else (1 if dense[i] else 0)
    # tip = the bar the run first hits MIN_DENSE_BARS (cluster just qualified)
    fires = [i for i in range(WARMUP, len(dense) - 1) if run[i] == MIN_DENSE_BARS]
    deduped: list[int] = []
    for i in fires:
        if not deduped or i - deduped[-1] >= MIN_GAP_BARS:
            deduped.append(i)
    return deduped


def resolve_net(enriched: pd.DataFrame, i: int, cost: float) -> float | None:
    """TP5/SL2/72bar triple-barrier from next-bar open, minus cost. None=skip."""
    entry_i = i + 1
    if entry_i >= len(enriched):
        return None
    atr = float(enriched["atr14"].iloc[i])
    atr_pct = float(enriched["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last_i = min(entry_i + HORIZON_BARS - 1, len(enriched) - 1)
    if last_i < entry_i:
        return None
    highs = enriched["high"].to_numpy()[entry_i : last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : last_i + 1]
    upper, lower = entry + TP_MULT * atr, entry - SL_MULT * atr
    up = np.argmax(highs >= upper) if (highs >= upper).any() else len(highs)
    dn = np.argmax(lows <= lower) if (lows <= lower).any() else len(highs)
    if up < dn:
        gross = upper / entry - 1
    elif dn < up:
        gross = lower / entry - 1
    elif (lows <= lower).any():   # same-bar double touch -> conservative SL
        gross = lower / entry - 1
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        gross = float(enriched["close"].iloc[last_i]) / entry - 1
    else:
        return None                # horizon incomplete at data end
    return gross - cost


def stats(net: np.ndarray) -> dict:
    if not len(net):
        return {"n": 0, "win_rate": None, "profit_factor": None, "mean_net": None, "total_net": 0.0}
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return {
        "n": int(len(net)),
        "win_rate": round(float((net > 0).mean()), 4),
        "profit_factor": round(float(w / -l), 3) if l < 0 else None,
        "mean_net": round(float(net.mean()), 5),
        "total_net": round(float(net.sum()), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=60, help="0 = all SWAP")
    ap.add_argument("--tag", default="base_rate_dense")
    args = ap.parse_args()
    rng = np.random.default_rng(20260723)

    dense_net: list[float] = []
    rand_net: list[float] = []
    dense_by_time: dict[str, list[float]] = {}
    n_sym = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        if is_eval_symbol(symbol):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)  # never touch holdout
        if len(frame) < WARMUP + 200:
            continue
        enriched = add_indicators(add_mas(frame))
        tips = dense_tip_indices(enriched)
        if not tips:
            continue
        t = pd.to_datetime(enriched["open_time"], utc=True)
        for i in tips:
            net = resolve_net(enriched, i, FORWARD_COST)
            if net is None:
                continue
            dense_net.append(net)
            half = str(t.iloc[i])[:7]  # YYYY-MM bucket
            dense_by_time.setdefault(half, []).append(net)
        # random baseline: same count of eligible random bars in same range
        pool = list(range(WARMUP, len(enriched) - HORIZON_BARS - 2))
        if pool:
            for i in rng.choice(pool, size=min(len(tips), len(pool)), replace=False):
                net = resolve_net(enriched, int(i), FORWARD_COST)
                if net is not None:
                    rand_net.append(net)
        n_sym += 1
        if args.n_symbols and n_sym >= args.n_symbols:
            break

    d = np.array(dense_net)
    r = np.array(rand_net)
    monthly = {k: stats(np.array(v)) for k, v in sorted(dense_by_time.items())}
    out = {
        "tag": args.tag,
        "window": f"<{HOLDOUT_START.date()} (holdout untouched)",
        "n_symbols": n_sym,
        "rule": f"fast_spread<={FAST_MAX} & full_spread<={FULL_MAX}, run>={MIN_DENSE_BARS}, no detector",
        "exit": f"TP{TP_MULT:g}/SL{SL_MULT:g}/{HORIZON_BARS}bar, maker cost {FORWARD_COST}",
        "dense": stats(d),
        "random_baseline": stats(r),
        "dense_minus_random_mean_net": (
            round(float(d.mean() - r.mean()), 5) if len(d) and len(r) else None),
        "monthly_dense": monthly,
    }
    p = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "monthly_dense"}, indent=2, ensure_ascii=False))
    print(f"\nmonthly dense PF: " + " ".join(
        f"{k}:{m['profit_factor']}" for k, m in monthly.items() if m["n"] >= 20))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
