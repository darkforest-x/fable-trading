"""Is the coefficient of variation the normalisation that matches the owner's eye?

Today's result that forced this: on the owner's 390 verdicts the project's
mechanical density gate runs ANTI-correlated with them -- 31.0% of their keeps
count as dense against 47.0% of their drops. Two normalisations were already
falsified: raw percent spread (max MA - min MA) / close, and pixel share of the
rendered chart. Both put keeps slightly WIDER than drops.

The owner's Gemini research proposes a third: CV = std(6 MAs) / mean(6 MAs),
i.e. dispersion relative to price LEVEL rather than to the spread of extremes.
Mathematically distinct from both, so it is a genuine third try rather than a
restatement, and it costs minutes to test against labels that already exist.

Also tests the compound condition from the same source -- CV <= 1.0% AND volume
> 1.5x its 20-period mean -- which the project has never tried: density has
never been combined with a volume confirmation.

Judged the only way that means anything here: does it separate the owner's keeps
from their drops, and in which direction. A gate that fires MORE on drops is
worse than useless however good it looks in isolation.

Read-only. Pool ends 2026-05-03 so no holdout. Thresholds are owner decisions --
this measures against the proposed 1.0% and reports where the real split lies,
it does not adopt anything.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_cv_density_vs_owner.py
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
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402

PACK = PROJECT / "analysis" / "output" / "owner_side_short_tip_v1b_detect1000"
CV_MAX = 1.0          # percent, as proposed
VOL_MULT = 1.5
VOL_WIN = 20


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
    print(f"owner 标注: keep {(graded.owner_keep=='keep').sum()} / "
          f"drop {(graded.owner_keep=='drop').sum()}")

    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}
    rows = []
    for _, r in graded.iterrows():
        sym = str(r["symbol"])
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                base = load_series(series[key])
                framed = add_mas(base)
                ind = add_features(add_indicators(base))
                ma = np.vstack([framed[c].to_numpy(dtype=float)
                                for c in ALL_MA_COLS if c in framed.columns])
                vol = base["volume"].to_numpy(dtype=float)
                volma = pd.Series(vol).rolling(VOL_WIN).mean().to_numpy()
                cache[sym] = (framed, ma, ind, vol, volma)
            else:
                cache[sym] = None
        e = cache[sym]
        if e is None:
            continue
        framed, ma, ind, vol, volma = e
        times = pd.to_datetime(framed["open_time"], utc=True)
        i = int(times.searchsorted(pd.Timestamp(r["tip_time"])))
        if i < VOL_WIN or i >= len(framed):
            continue
        col = ma[:, i]
        if not np.all(np.isfinite(col)):
            continue
        mu = float(np.mean(col))
        if mu <= 0:
            continue
        cv = float(np.std(col, ddof=0)) / mu * 100.0     # percent
        vr = float(vol[i] / volma[i]) if np.isfinite(volma[i]) and volma[i] > 0 else np.nan
        # the project's current gate, for a like-for-like comparison
        fast = float(ind["fast_spread"].to_numpy(dtype=float)[i])
        full = float(ind["full_spread"].to_numpy(dtype=float)[i])
        rows.append({"v": r["owner_keep"], "cv": cv, "vol_ratio": vr,
                     "fast": fast, "full": full})

    d = pd.DataFrame(rows).dropna(subset=["cv"])
    k, dr = d[d.v == "keep"], d[d.v == "drop"]
    base = float((d.v == "keep").mean())
    print(f"可算 {len(d)}  基础率 {base*100:.1f}%\n")

    print("=== CV 分布(σ/μ %,越小越密集) ===")
    for name, s in (("keep", k), ("drop", dr)):
        q = np.percentile(s.cv, [25, 50, 75])
        print(f"  {name:5s} n={len(s):3d}  p25={q[0]:.3f}  p50={q[1]:.3f}  p75={q[2]:.3f}")
    u, p = mannwhitneyu(k.cv, dr.cv)
    direction = "keep 更密(更小)" if k.cv.median() < dr.cv.median() else "keep 更松(更大)"
    verdict_dir = "✅ 方向正确" if k.cv.median() < dr.cv.median() else "❌ 方向仍然反着"
    print(f"  Mann-Whitney p={p:.3e}   {direction}   {verdict_dir}")

    print("\n=== 各门槛下的精度(基础率 %.1f%%) ===" % (base * 100))
    print(f"{'条件':<34} {'命中':>6} {'keep':>6} {'精度':>8} {'95%CI':>16}")
    out_rows = []
    conds = [
        (f"CV <= {CV_MAX}%", d.cv <= CV_MAX),
        ("CV <= 0.5%", d.cv <= 0.5),
        ("CV <= p25", d.cv <= d.cv.quantile(0.25)),
        (f"放量 > {VOL_MULT}x", d.vol_ratio > VOL_MULT),
        (f"CV<={CV_MAX}% 且 放量>{VOL_MULT}x", (d.cv <= CV_MAX) & (d.vol_ratio > VOL_MULT)),
        ("CV<=p25 且 放量>1.5x", (d.cv <= d.cv.quantile(0.25)) & (d.vol_ratio > VOL_MULT)),
        ("[对照] 现用 expanded 门", (d.fast <= 0.0045) & (d.full <= 0.0088)),
    ]
    for name, m in conds:
        s = d[m]
        n = len(s)
        if n < 10:
            print(f"{name:<34} {n:>6}  样本太少")
            continue
        kk = int((s.v == "keep").sum())
        prec = kk / n
        lo, hi = wilson(kk, n)
        flag = " ←优于基础率" if lo > base else ""
        out_rows.append({"cond": name, "n": n, "keep": kk, "prec": round(prec, 4),
                         "ci": [round(lo, 4), round(hi, 4)]})
        print(f"{name:<34} {n:>6} {kk:>6} {prec*100:>7.1f}% "
              f"[{lo*100:>5.1f},{hi*100:>5.1f}]{flag}")

    best = max((r for r in out_rows), key=lambda r: r["ci"][0], default=None)
    ok = best and best["ci"][0] > base
    verdict = (f"CV 有效:{best['cond']} 精度 {best['prec']*100:.1f}% "
               f"区间下沿 {best['ci'][0]*100:.1f}% > 基础率 {base*100:.1f}%"
               if ok else
               "CV 无效:没有任何条件的置信区间下沿超过基础率 → 第三种归一化也不是钥匙")
    print(f"\n判读: {verdict}")
    (PROJECT / "analysis" / "output" / "diag_cv_density_vs_owner.json").write_text(
        json.dumps({"n": len(d), "base_rate": round(base, 4),
                    "cv_keep_p50": round(float(k.cv.median()), 4),
                    "cv_drop_p50": round(float(dr.cv.median()), 4),
                    "mannwhitney_p": float(p), "conditions": out_rows,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
