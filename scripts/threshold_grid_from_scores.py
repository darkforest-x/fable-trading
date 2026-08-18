"""Propose a threshold grid from the model's own score distribution.

The fixed_w10 stage1 grid is a fixed 0.20..0.60 ladder. This classifier's scores
span [0.269, 0.695], so the two lowest rungs sit below the minimum score and are
the same configuration as each other:

    r1  threshold 0.20 -> 1383 trades, worst fold +6.13bp
        threshold 0.25 -> 1383 trades, worst fold +6.13bp
    r2  threshold 0.20 -> 2766 trades, worst fold +4.15bp
        threshold 0.25 -> 2766 trades, worst fold +4.15bp

Identical rows, because everything <= 0.269 selects every bar. Two of nine grid
points measure nothing, and both are then rejected by max_trigger_density -- the
guard works, but it is spending itself on configurations that were never
distinguishable in the first place.

A grid placed on score quantiles spends every point on a different selection.
This prints one, with the fire rate and post-dedup trigger density each rung
would produce, so a rung that cannot pass the density guard is visible before
the fold work rather than after.

Read-only: reads a predictions file, writes nothing unless --out is given.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

BAR_MINUTES = 15
DEFAULT_QUANTILES = (50, 65, 75, 82.5, 88, 92, 95, 97.5, 99)


def load_scores(path: Path) -> pd.DataFrame:
    rows = []
    with path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            rows.append((d["symbol"], d["decision_i"], d["p_signal"]))
    return pd.DataFrame(rows, columns=["symbol", "i", "p"])


def dedup_count(df: pd.DataFrame, gap: int) -> int:
    n = 0
    for _, g in df.sort_values(["symbol", "i"]).groupby("symbol", sort=False):
        last = -10**9
        for i in g["i"].to_numpy():
            if i - last >= gap:
                n += 1
                last = i
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions", type=Path)
    ap.add_argument("--min-gap-bars", type=int, default=18)
    ap.add_argument("--max-density", type=float, default=4.0,
                    help="max_trigger_density_per_symbol_day from the stage prereg")
    ap.add_argument("--points", type=int, default=len(DEFAULT_QUANTILES))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    df = load_scores(args.predictions)
    p = df["p"].to_numpy()
    n_sym = df["symbol"].nunique()
    bars_per_sym = len(df) / n_sym
    days = bars_per_sym * BAR_MINUTES / 60 / 24
    print(f"{len(df):,} scored bars · {n_sym} symbols · {bars_per_sym:,.0f} bars/symbol "
          f"· {days:.1f} days/symbol")
    print(f"score range [{p.min():.4f}, {p.max():.4f}]  median {np.median(p):.4f}")
    print(f"density ceiling with this cadence: "
          f"{bars_per_sym / args.min_gap_bars / days:.2f} trades/symbol/day at 100% fire "
          f"(guard is {args.max_density})\n")

    qs = np.linspace(50, 99, args.points) if args.points != len(DEFAULT_QUANTILES) \
        else np.array(DEFAULT_QUANTILES)
    grid = []
    print(f"{'quantile':>9}{'threshold':>11}{'fire%':>9}{'dedup trades':>14}"
          f"{'trades/sym/day':>16}{'density guard':>15}")
    for q in qs:
        thr = float(np.percentile(p, q))
        sub = df[df["p"] >= thr]
        n = dedup_count(sub, args.min_gap_bars)
        dens = n / n_sym / days
        ok = dens <= args.max_density
        grid.append({"quantile": float(q), "threshold": round(thr, 6),
                     "fire_rate": round(len(sub) / len(df), 6),
                     "dedup_trades": n, "trades_per_symbol_day": round(dens, 4),
                     "passes_density_guard": bool(ok)})
        print(f"{q:>9.1f}{thr:>11.4f}{len(sub)/len(df)*100:>8.1f}%{n:>14,}"
              f"{dens:>16.2f}{'  pass' if ok else '  FAIL':>15}")

    n_pass = sum(g["passes_density_guard"] for g in grid)
    print(f"\n{n_pass} / {len(grid)} rungs can pass the density guard; "
          f"all {len(grid)} select a different set of bars")

    if args.out:
        args.out.write_text(json.dumps({
            "source": str(args.predictions),
            "min_gap_bars": args.min_gap_bars,
            "max_trigger_density_per_symbol_day": args.max_density,
            "score_min": float(p.min()), "score_max": float(p.max()),
            "n_scored_bars": len(df), "n_symbols": n_sym,
            "days_per_symbol": round(days, 3),
            "grid": grid,
        }, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
