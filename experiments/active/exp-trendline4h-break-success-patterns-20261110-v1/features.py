"""Causal features of every V1.2 4H closing break, for success-vs-failure pattern mining.

All features use bars <= x (the break bar, known at its close) plus the same-bar
cross-section of other symbols (also closed at x). Outcome columns are joined
from exp-trendline4h-breakout-runs-20261109-v1/events.csv and never used as inputs.
Success = peak gain >= 30% before a 1-ATR stop within 180 4H bars (frozen there).
Dev = breaks before 2026-01-01 UTC, check = 2026; full-horizon breaks only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CENSUS = HERE.parent / "exp-trendline4h-breakout-runs-20261109-v1"
sys.path.insert(0, str(CENSUS))
import trendline_v12  # noqa: E402

SRC = HERE.parents[2] / "data/crypto/market/okx_swap_4h_20261109"


def ema(c, n):
    out = np.empty_like(c); a = 2 / (n + 1); out[0] = c[0]
    for i in range(1, len(c)):
        out[i] = out[i - 1] + a * (c[i] - out[i - 1])
    return out


def main() -> int:
    rows = []
    btc = None
    for f in sorted(SRC.glob("okx_*_4H_*.csv")):
        sym = f.name[4:].split("_4H_")[0]
        df = pd.read_csv(f)
        if len(df) < 200:
            continue
        o, h, l, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        ts = df.ts.to_numpy(np.int64)
        atr = trendline_v12.pine_atr(h, l, c)
        e20, e50 = ema(c, 20), ema(c, 50)
        if sym == "BTC_USDT_SWAP":
            btc = pd.DataFrame({"ts": ts, "btc_ret42": pd.Series(c).pct_change(42).to_numpy() * 100,
                                "btc_cloud": (e20 > e50).astype(int)})
        born = None
        for e in trendline_v12.run(o, h, l, c).events:
            if e.kind == "born":
                born = e.x
                continue
            if e.kind != "break" or e.x < 200:
                continue
            x = e.x
            a = atr[x]
            rows.append(dict(
                symbol=sym, x=x, ts=int(ts[x]),
                touches=e.touches, span=e.b_x - e.a_x, drop_pct=(e.a_y / e.b_y - 1) * 100,
                slope_atr=(e.a_y - e.b_y) / (e.b_x - e.a_x) / a,
                born_to_break=x - born if born is not None else np.nan, line_age=x - e.a_x,
                over_line_atr=(c[x] - e.level) / a, bar_body_atr=(c[x] - o[x]) / a,
                move2_atr=(c[x] - c[x - 2]) / a,
                vol_ratio=v[x] / max(v[x - 20:x].mean(), 1e-12), vol2_ratio=v[x - 1:x + 1].mean() / max(v[x - 22:x - 1].mean(), 1e-12),
                cloud_bull=int(e20[x] > e50[x]), cloud_atr=(e20[x] - e50[x]) / a, over_e50_atr=(c[x] - e50[x]) / a,
                atr_pct=a / c[x] * 100, atr_expand=a / atr[x - 30],
                range30_pct=(h[x - 29:x + 1].max() / l[x - 29:x + 1].min() - 1) * 100,
                dd180_pct=(c[x] / h[max(0, x - 179):x + 1].max() - 1) * 100,
                ret42_pct=(c[x] / c[x - 42] - 1) * 100, age_bars=x,
            ))
    ft = pd.DataFrame(rows)
    ft["breaks_same_bar"] = ft.groupby("ts").x.transform("size") - 1
    t = np.sort(ft.ts.to_numpy())
    ft["breaks_prev24h"] = [int(np.searchsorted(t, s, "right") - np.searchsorted(t, s - 6 * 4 * 3600_000 + 1, "left")) - 1 for s in ft.ts]
    ft = ft.merge(btc, on="ts", how="left")
    ev = pd.read_csv(CENSUS / "events.csv")[["symbol", "x", "full_horizon", "peak_gain_pct_atr1", "peak_r_atr1"]]
    ft = ft.merge(ev, on=["symbol", "x"], how="inner")
    ft["win30"] = (ft.peak_gain_pct_atr1 >= 30).astype(int)
    ft["win10r"] = (ft.peak_r_atr1 >= 10).astype(int)
    ft["period"] = np.where(ft.ts < pd.Timestamp("2026-01-01", tz="UTC").value // 1_000_000, "dev", "check")
    ft.to_csv(HERE / "features.csv", index=False)
    print(len(ft), ft.groupby("period").agg(n=("x", "size"), win30=("win30", "mean"), full=("full_horizon", "mean")).round(3).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
