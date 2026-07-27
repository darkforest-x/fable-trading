"""Does fractional differencing surface anything the existing features cannot?

Every feature the judgment layer has seen is a ratio or a short-window return --
memory-erased by construction. FFD keeps as much of the price level as
stationarity allows, so if these setups depend on WHERE price sits rather than
only on how it moved, no existing feature could have expressed it.

Two questions, in order:
  1. What d does each symbol actually need? If d comes out near 1.0 the series
     needs full differencing and FFD buys nothing over plain returns.
  2. Do FFD features separate the owner's 390 verdicts, and do they add anything
     on top of the volume signal already found (29.2%, CI [19.9, 40.5])?

Correctness notes: the weight vector is fixed before it touches the series and
the convolution is strictly backward-looking, so the transform is causal under
iron rule 3. d is fitted per symbol on the FULL series, which is acceptable for
a stationarity transform (it uses no labels) but is stated here rather than
buried -- a stricter version would fit d on train only.

Read-only, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_ffd_vs_owner.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.factors.ffd import ffd_weights, find_min_d, frac_diff  # noqa: E402

PACK = PROJECT / "analysis" / "output" / "owner_side_short_tip_v1b_detect1000"
VOL_WIN, VOL_MULT = 20, 1.5


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def main() -> int:
    sheet = pd.read_csv(PACK / "review_sheet.csv")
    graded = sheet[sheet["owner_keep"].isin(["keep", "drop"])]
    syms = sorted(graded["symbol"].unique())
    series = list_series(bar="15m")
    print(f"标注 {len(graded)}  涉及币 {len(syms)}")

    rows, dstats = [], []
    for sym in syms:
        key = ("okx", sym)
        if key not in series:
            continue
        try:
            base = load_series(series[key])
        except Exception:  # noqa: BLE001
            continue
        close = base["close"].astype(float)
        d, rep = find_min_d(close)
        if d is None:
            dstats.append({"symbol": sym, "d": None})
            continue
        dstats.append({"symbol": sym, "d": d, "adf_p": rep.adf_pvalue,
                       "corr": rep.corr_with_original, "n_w": rep.n_weights})
        ffd_close = frac_diff(np.log(close), d).to_numpy()
        vol = base["volume"].astype(float).to_numpy()
        volma = pd.Series(vol).rolling(VOL_WIN).mean().to_numpy()
        times = pd.to_datetime(base["open_time"], utc=True)
        sub = graded[graded["symbol"] == sym]
        for _, r in sub.iterrows():
            i = int(times.searchsorted(pd.Timestamp(r["tip_time"])))
            if i < VOL_WIN or i >= len(base) or not np.isfinite(ffd_close[i]):
                continue
            w = ffd_close[max(0, i - 24):i + 1]
            rows.append({
                "v": r["owner_keep"], "symbol": sym,
                "ffd": float(ffd_close[i]),
                "ffd_z": float((ffd_close[i] - np.nanmean(w)) / (np.nanstd(w) + 1e-12)),
                "ffd_chg8": float(ffd_close[i] - ffd_close[i - 8]) if i >= 8 else np.nan,
                "vol_ratio": float(vol[i] / volma[i]) if volma[i] > 0 else np.nan,
            })

    ds = pd.DataFrame(dstats).dropna(subset=["d"])
    d_col, corr_col, nw_col = ds["d"], ds["corr"], ds["n_w"]
    d_p25, d_p50, d_p75 = d_col.quantile(.25), d_col.median(), d_col.quantile(.75)
    print("\n=== 每个币需要的 d ===")
    print(f"  n={len(ds)}  d: p25={d_p25:.2f} p50={d_p50:.2f} p75={d_p75:.2f}  "
          f"权重长度中位 {nw_col.median():.0f}")
    print(f"  与原序列相关性 中位 {corr_col.median():.3f}")
    if d_p50 >= 0.9:
        print("  ⚠️ d 接近 1 → FFD 相对普通收益率没有额外记忆可留")
    else:
        print(f"  → d={d_p50:.2f} 明显小于 1,FFD 确实保留了记忆")

    d = pd.DataFrame(rows).dropna(subset=["ffd_z"])
    k, dr = d[d.v == "keep"], d[d.v == "drop"]
    base = float((d.v == "keep").mean())
    print(f"\n=== FFD 特征 vs owner 标注(n={len(d)},基础率 {base*100:.1f}%) ===")
    for col in ("ffd", "ffd_z", "ffd_chg8"):
        s = d.dropna(subset=[col])
        kk, dd = s[s.v == "keep"][col], s[s.v == "drop"][col]
        if len(kk) < 10:
            continue
        u, p = mannwhitneyu(kk, dd)
        star = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        print(f"  {col:10s} keep p50={kk.median():+.5f}  drop p50={dd.median():+.5f}  "
              f"p={p:.3e} {star}")

    print(f"\n=== 门槛精度(对照:放量>1.5x 已知 29.2% [19.9,40.5]) ===")
    print(f"{'条件':<32} {'命中':>6} {'精度':>8} {'95%CI':>16}")
    out = []
    q = d["ffd_z"].quantile
    conds = [
        ("ffd_z <= p25 (相对低位)", d.ffd_z <= q(0.25)),
        ("ffd_z >= p75 (相对高位)", d.ffd_z >= q(0.75)),
        ("放量>1.5x", d.vol_ratio > VOL_MULT),
        ("放量>1.5x 且 ffd_z<=p50", (d.vol_ratio > VOL_MULT) & (d.ffd_z <= q(0.50))),
        ("放量>1.5x 且 ffd_z<=p25", (d.vol_ratio > VOL_MULT) & (d.ffd_z <= q(0.25))),
    ]
    for name, m in conds:
        s = d[m]
        if len(s) < 10:
            print(f"{name:<32} {len(s):>6}  太少")
            continue
        kk = int((s.v == "keep").sum())
        lo, hi = wilson(kk, len(s))
        flag = " ←下沿过线" if lo > base else ""
        out.append({"cond": name, "n": len(s), "prec": round(kk / len(s), 4),
                    "ci": [round(lo, 4), round(hi, 4)]})
        print(f"{name:<32} {len(s):>6} {kk/len(s)*100:>7.1f}% "
              f"[{lo*100:>5.1f},{hi*100:>5.1f}]{flag}")

    best = max(out, key=lambda r: r["ci"][0], default=None)
    verdict = (f"最好条件 {best['cond']} 精度 {best['prec']*100:.1f}% 下沿 {best['ci'][0]*100:.1f}%"
               if best else "无可用条件")
    print(f"\n判读: {verdict}")
    (PROJECT / "analysis" / "output" / "diag_ffd_vs_owner.json").write_text(
        json.dumps({"n": len(d), "base_rate": round(base, 4),
                    "d_median": float(d_p50) if len(ds) else None,
                    "d_corr_median": float(corr_col.median()) if len(ds) else None,
                    "conditions": out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
