"""Why is the high-confidence edge decaying — regime, or structural death?

The one statistically solid positive this project has produced is v6 at
conf>=0.5: 42.9% win over the first third against a 28.6% breakeven, Wilson
[34.1, 52.1], entirely above the line. It then decays monotonically --
44.0% -> 28.8% -> 24.5% across thirds -- and the last third sits below breakeven.

Two explanations with very different consequences:

  REGIME      the market in 2025 H2 simply suited this setup (high volatility,
              trending, alt-heavy) and 2026 does not. The edge would return when
              conditions do, and the right move is to gate on the condition.
  STRUCTURAL  the setup stopped working regardless of conditions -- crowding, or
              a change in how these moves resolve. The chain should be closed.

They are separable: measure the market environment in each third and check
whether the environment moved as much as the win rate did. If the thirds look
alike and only the win rate fell, "regime" cannot carry the explanation.

Environment is measured on BTC plus the candidate universe itself, at the signal
bars only, so it describes the conditions the strategy actually traded in rather
than the calendar average.

Read-only. Pool ends 2026-05-03, no holdout. No promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_edge_decay_attribution.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402

CONF_CSV = PROJECT / "analysis" / "output" / "v6_conf_per_candidate.csv"
POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
CONF_HI = 0.50
BREAKEVEN = 2.0 / 7.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def btc_environment() -> pd.DataFrame:
    """Per-bar BTC conditions: volatility, trend strength, drawdown."""
    series = list_series(bar="15m")
    btc = load_series(series[("okx", "BTC_USDT_SWAP")])
    t = pd.to_datetime(btc["open_time"], utc=True)
    c = btc["close"].astype(float)
    h, lo = btc["high"].astype(float), btc["low"].astype(float)
    atr = ((h - lo) / c).rolling(14).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    ret96 = c / c.shift(96) - 1
    # trend strength: |96-bar return| relative to typical bar range
    trend = (ret96.abs() / (atr * math.sqrt(96) + 1e-12))
    roll_max = c.rolling(672, min_periods=96).max()
    return pd.DataFrame({
        "t": t, "btc_atr_pct": atr, "btc_above_ema200": (c > ema200).astype(float),
        "btc_ret96": ret96, "btc_trend_strength": trend,
        "btc_dd": c / roll_max - 1,
    })


def main() -> int:
    conf = pd.read_csv(CONF_CSV)
    conf["t"] = pd.to_datetime(conf["signal_time"], utc=True)
    conf = conf.sort_values("t").reset_index(drop=True)
    pool = pd.read_csv(POOL)
    pool["t"] = pd.to_datetime(pool["signal_time"], utc=True)

    # candidate-side conditions come from the pool's own features
    keep = ["t", "symbol", "atr_pct", "volume_ratio", "ma_spread_pct", "dense_run_len"]
    have = [c for c in keep if c in pool.columns]
    conf = conf.merge(pool[have].drop_duplicates(subset=["t", "symbol"]),
                      on=["t", "symbol"], how="left")

    env = btc_environment()
    conf = pd.merge_asof(conf.sort_values("t"), env.sort_values("t"),
                         on="t", direction="backward")

    n = len(conf)
    thirds = [(0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n)]
    print(f"候选 {n}   高置信门槛 conf>={CONF_HI}   盈亏平衡 {BREAKEVEN*100:.1f}%\n")

    rows = []
    for i, (a, b) in enumerate(thirds, 1):
        seg = conf.iloc[a:b]
        hi = seg[seg["conf"] >= CONF_HI]
        k, m = int((hi["label"] == 1).sum()), len(hi)
        lo_ci, up_ci = wilson(k, m)
        rows.append({
            "third": i, "start": str(seg["t"].iloc[0].date()),
            "end": str(seg["t"].iloc[-1].date()),
            "n_hi": m, "win": round(k / m, 4) if m else None,
            "ci": [round(lo_ci, 4), round(up_ci, 4)],
            "btc_atr_pct": round(float(seg["btc_atr_pct"].median()), 5),
            "btc_trend": round(float(seg["btc_trend_strength"].median()), 3),
            "btc_above_ema200": round(float(seg["btc_above_ema200"].mean()), 3),
            "btc_dd": round(float(seg["btc_dd"].median()), 4),
            "cand_atr_pct": round(float(seg["atr_pct"].median()), 5) if "atr_pct" in seg else None,
            "cand_vol_ratio": round(float(seg["volume_ratio"].median()), 3) if "volume_ratio" in seg else None,
            "n_symbols": int(seg["symbol"].nunique()),
        })

    print(f"{'段':>3} {'区间':<24} {'高置信胜率':>18} {'BTC波动':>9} {'BTC趋势':>9} "
          f"{'BTC>EMA200':>11} {'BTC回撤':>9} {'候选波动':>9} {'候选量比':>9}")
    for r in rows:
        ci = f"[{r['ci'][0]*100:.0f},{r['ci'][1]*100:.0f}]"
        print(f"{r['third']:>3} {r['start']}~{r['end']:<12} "
              f"{r['win']*100:>6.1f}% n={r['n_hi']:<3} {ci:>9} "
              f"{r['btc_atr_pct']:>9.5f} {r['btc_trend']:>9.3f} "
              f"{r['btc_above_ema200']:>11.2f} {r['btc_dd']:>9.4f} "
              f"{str(r['cand_atr_pct']):>9} {str(r['cand_vol_ratio']):>9}")

    w = np.array([r["win"] for r in rows if r["win"] is not None])
    def spread(key):
        v = np.array([r[key] for r in rows if r[key] is not None], dtype=float)
        return float(v.max() / max(v.min(), 1e-12)) if len(v) else np.nan

    win_ratio = float(w.max() / max(w.min(), 1e-12))
    print(f"\n=== 变化幅度(最大/最小) ===")
    print(f"  高置信胜率      : {win_ratio:.2f}x   ({w.min()*100:.1f}% → {w.max()*100:.1f}%)")
    for key, label in (("btc_atr_pct", "BTC 波动率"), ("btc_trend", "BTC 趋势强度"),
                       ("cand_atr_pct", "候选波动率"), ("cand_vol_ratio", "候选量比")):
        print(f"  {label:14s}: {spread(key):.2f}x")

    env_moves = max(spread(k) for k in ("btc_atr_pct", "btc_trend", "cand_atr_pct"))
    if env_moves >= win_ratio * 0.6:
        verdict = ("REGIME:环境变动幅度与胜率变动同量级 → 边可能随条件回来,"
                   "应当对条件设门而不是关掉链路")
    else:
        verdict = ("STRUCTURAL:环境几乎没变而胜率大幅下滑 → 不是行情不对,"
                   "是这个 setup 本身失效了")
    print(f"\n判读: {verdict}")

    (PROJECT / "analysis" / "output" / "diag_edge_decay_attribution.json").write_text(
        json.dumps({"conf_hi": CONF_HI, "breakeven": round(BREAKEVEN, 4),
                    "thirds": rows, "win_ratio": round(win_ratio, 3),
                    "env_max_ratio": round(env_moves, 3), "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
