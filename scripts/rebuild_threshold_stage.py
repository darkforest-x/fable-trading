"""Rebuild stage1 threshold selection, with a score-aware grid and a real sparsity guard.

The code that produced the fixed_w10 stage1 artefacts is not in this repo --
`max_trigger_density` appears only inside output json, in no .py file -- so the
parameter selection cannot currently be reproduced. This reconstructs it from
the pre-registered fold document and the scan's own signals_raw.

Three changes from the original stage:

  1. the grid comes from score quantiles, not a fixed 0.20..0.60 ladder. The
     classifier's scores span [0.269, 0.695], so the two lowest rungs of that
     ladder were the same configuration and produced byte-identical rows.

  2. the density ceiling is a parameter. At this cadence the structural maximum
     is 5.33 trades/symbol/day (every bar fires, 18-bar dedup) and the original
     guard sat at 4.0, so anything below ~75% fire passed unconditionally.

  3. a matched null. p_signal ranks realised TP at AUC 0.4588, below chance, so
     the question is not which rung wins but whether winning means anything.
     Shuffling scores within symbol preserves the score distribution, the trade
     cadence and the outcome time series, and destroys only the score-outcome
     link. If the real grid's best rung sits inside that null, the selection is
     reading noise.

Read-only over inputs. Consumes no holdout: signals_raw ends 2026-03-30.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

BAR_MINUTES = 15
QUANTILES = (50, 65, 75, 82.5, 88, 92, 95, 97.5, 99)


def load(path: Path) -> pd.DataFrame:
    rows = []
    with path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("status") != "closed" or d.get("net_maker") is None:
                continue
            rows.append((d["symbol"], d["decision_i"], d["decision_time"],
                         d["p_signal"], d["net_maker"]))
    df = pd.DataFrame(rows, columns=["symbol", "i", "t", "p", "net"])
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df.sort_values(["symbol", "i"]).reset_index(drop=True)


def dedup_mask(sym_codes: np.ndarray, idx: np.ndarray, gap: int) -> np.ndarray:
    """Earliest-first greedy dedup, min `gap` bars between kept rows per symbol."""
    keep = np.zeros(len(idx), dtype=bool)
    last_sym = -1
    last_i = -(10 ** 9)
    for k in range(len(idx)):
        s = sym_codes[k]
        if s != last_sym:
            last_sym = s
            last_i = -(10 ** 9)
        if idx[k] - last_i >= gap:
            keep[k] = True
            last_i = idx[k]
    return keep


def evaluate(df: pd.DataFrame, p: np.ndarray, thresholds, folds, gap, guards,
             n_sym: int) -> list[dict]:
    sym = df["symbol"].astype("category").cat.codes.to_numpy()
    idx = df["i"].to_numpy()
    net = df["net"].to_numpy()
    t = df["t"].dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    out = []
    for thr in thresholds:
        sel = p >= thr
        if sel.sum() == 0:
            continue
        keep = dedup_mask(sym[sel], idx[sel], gap)
        pos = np.flatnonzero(sel)[keep]
        fold_means, fold_trades, dens = [], [], []
        for f in folds:
            m = (t[pos] >= f["start"]) & (t[pos] < f["end"])
            n = int(m.sum())
            fold_trades.append(n)
            fold_means.append(float(net[pos][m].mean()) if n else np.nan)
            days = (f["end"] - f["start"]) / np.timedelta64(1, "D")
            dens.append(n / n_sym / days)
        fm = np.array(fold_means, dtype=float)
        valid = ~np.isnan(fm)
        worst_bp = float(np.nanmin(fm) * 1e4) if valid.any() else np.nan
        v = []
        if int(np.sum(fold_trades)) < guards["min_trades"]:
            v.append("min_trades")
        if int((fm[valid] > 0).sum()) < guards["min_positive_folds"]:
            v.append("min_positive_folds")
        if not (worst_bp >= guards["min_worst_fold_maker_bp"]):
            v.append("min_worst_fold_maker_bp")
        if max(dens) > guards["max_trigger_density_per_symbol_day"]:
            v.append("max_trigger_density")
        out.append({"threshold": float(thr), "total_trades": int(np.sum(fold_trades)),
                    "fold_trades": fold_trades,
                    "fold_bp": [None if np.isnan(x) else round(x * 1e4, 2) for x in fm],
                    "worst_fold_bp": None if np.isnan(worst_bp) else round(worst_bp, 2),
                    "max_density": round(max(dens), 3),
                    "positive_folds": int((fm[valid] > 0).sum()),
                    "eligible": not v, "guard_violations": v})
    return out


def best_worst_bp(rank: list[dict]) -> float:
    el = [r for r in rank if r["eligible"] and r["worst_fold_bp"] is not None]
    return max((r["worst_fold_bp"] for r in el), default=np.nan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("signals_raw", type=Path)
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--min-gap-bars", type=int, default=18)
    ap.add_argument("--max-density", type=float, default=4.0)
    ap.add_argument("--shuffles", type=int, default=200)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text())
    folds = [{"name": f["name"],
              "start": pd.Timestamp(f["validation_start"]).tz_convert(None).to_datetime64(),
              "end": pd.Timestamp(f["validation_end"]).tz_convert(None).to_datetime64()}
             for f in pre["fold_document"]["folds"]]
    guards = dict(pre["guards"])
    guards["max_trigger_density_per_symbol_day"] = args.max_density

    df = load(args.signals_raw)
    p = df["p"].to_numpy()
    n_sym = df["symbol"].nunique()
    thresholds = [float(np.percentile(p, q)) for q in QUANTILES]
    print(f"{len(df):,} closed rows · {n_sym} symbols · folds "
          f"{[f['name'] for f in folds]} · density ceiling {args.max_density}")
    print(f"score range [{p.min():.4f}, {p.max():.4f}]\n")

    rank = evaluate(df, p, thresholds, folds, args.min_gap_bars, guards, n_sym)
    print(f"{'quantile':>9}{'thr':>8}{'trades':>8}{'fold bp (1/2/3)':>26}"
          f"{'worst':>8}{'dens':>7}{'eligible':>10}  violations")
    for q, r in zip(QUANTILES, rank):
        fb = "/".join("  n/a" if x is None else f"{x:6.1f}" for x in r["fold_bp"])
        print(f"{q:>9.1f}{r['threshold']:>8.3f}{r['total_trades']:>8,}{fb:>26}"
              f"{r['worst_fold_bp'] if r['worst_fold_bp'] is not None else float('nan'):>8.1f}"
              f"{r['max_density']:>7.2f}{str(r['eligible']):>10}  "
              f"{','.join(r['guard_violations']) or '-'}")

    obs = best_worst_bp(rank)
    n_el = sum(r["eligible"] for r in rank)
    print(f"\n合格配置 {n_el} / {len(rank)}；其中最好的 worst-fold = "
          f"{'n/a' if np.isnan(obs) else f'{obs:.2f} bp'}")

    rng = np.random.default_rng(20260817)
    sym_codes = df["symbol"].astype("category").cat.codes.to_numpy()
    null = []
    for _ in range(args.shuffles):
        ps = p.copy()
        for s in np.unique(sym_codes):
            m = sym_codes == s
            ps[m] = rng.permutation(ps[m])
        r = evaluate(df, ps, [float(np.percentile(ps, q)) for q in QUANTILES],
                     folds, args.min_gap_bars, guards, n_sym)
        null.append(best_worst_bp(r))
    null = np.array([x for x in null if not np.isnan(x)])
    print(f"\n打乱分数的对照（{args.shuffles} 次，币内置换）：")
    if len(null):
        pval = float((null >= obs).mean()) if not np.isnan(obs) else np.nan
        print(f"  有合格配置的次数 {len(null)}/{args.shuffles}")
        print(f"  null 的最好 worst-fold: 中位 {np.median(null):.2f} bp  "
              f"p90 {np.percentile(null,90):.2f}  最大 {null.max():.2f}")
        print(f"  实测 {obs:.2f} bp 在 null 中的分位 -> p = {pval:.3f}")
    else:
        print("  没有一次产生合格配置")

    if args.out:
        args.out.write_text(json.dumps({
            "source": str(args.signals_raw), "prereg": str(args.prereg),
            "guards": guards, "min_gap_bars": args.min_gap_bars,
            "grid_source": "score quantiles", "quantiles": list(QUANTILES),
            "rankings": rank, "observed_best_worst_fold_bp": None if np.isnan(obs) else obs,
            "null_shuffles": args.shuffles,
            "null_best_worst_fold_bp": [float(x) for x in null],
        }, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
