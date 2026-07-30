"""Reconcile the tp5_sl2 SWAP discrepancy: swap_replication reported
+0.225%/trade @0.06% while swap_h1h9_stack reported +0.026%. Same claimed
config -- one of the pools or evaluations differs. Diff them mechanically.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import load_splits, train_model

P = Path("data")
POOLS = {
    "replication": P / "ma206" / "swap_tp5_sl2_ma206.csv",
    "stack": P / "sweep_swap_stack" / "tp5_sl2.csv",
    "portfolio": P / "sweep_v3_portfolio" / "tp5_sl2.csv",
}


def main() -> int:
    frames = {}
    for name, path in POOLS.items():
        if not path.exists():
            print(f"{name}: MISSING {path}")
            continue
        df = pd.read_csv(path, parse_dates=["signal_time"])
        frames[name] = df
        print(f"{name}: n={len(df)} symbols={df['symbol'].nunique()} "
              f"range={str(df['signal_time'].min())[:10]}..{str(df['signal_time'].max())[:10]} "
              f"mean_ret={df['realized_ret'].mean():.5f} pos_rate={df['label'].mean():.3f}")
    names = list(frames)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = frames[names[i]], frames[names[j]]
            m = a.merge(b, on=["symbol", "signal_time"], suffixes=("_a", "_b"))
            diff = (m["realized_ret_a"] - m["realized_ret_b"]).abs() > 1e-9
            print(f"{names[i]} vs {names[j]}: overlap={len(m)} "
                  f"only_a={len(a)-len(m)} only_b={len(b)-len(m)} ret_mismatch={int(diff.sum())}")
    # identical evaluation applied to every pool: fresh train, top decile by score
    for name, df in frames.items():
        train, val, _ = load_splits(POOLS[name], horizon_bars=72)
        model = train_model(train, val)
        prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        k = max(1, len(prob) // 10)
        top = val["realized_ret"].to_numpy()[np.argsort(prob)[-k:]]
        print(f"{name}: n_val={len(val)} top_decile_net@0.06%={top.mean()-0.0006:.5f} win={(top>0).mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
