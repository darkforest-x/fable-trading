"""H19: IC screening of external alpha factors on the SWAP judgment pool.

For each factor, compute it causally on every series, sample at the same
candidate signal bars used by the judgment layer, and measure:
  - IC  = Spearman corr(factor, realized_ret)  [rank IC, robust]
  - IC vs label = point-biserial corr(factor, label)
  - IR  = mean(IC) / std(IC) across per-symbol monthly buckets
  - alive/reversed/dead classification by |IC| and sign stability

TRAIN/VAL ONLY (no holdout). Output: analysis/output/factor_ic_screen.json
+ analysis/p2b_factor_ic_report.md. Survivors (|IC|>=0.03, stable sign) are
LISTED as candidate features -- adding them to features.py is a separate
single-variable step per the discipline, not automatic.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PROJECT_DIR = Path(__file__).resolve().parents[1]
import sys; sys.path.insert(0, str(PROJECT_DIR))
from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.judgment.candidates import add_indicators, scan_candidates
from src.factors.library import FACTORS

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
OUT_JSON = PROJECT_DIR / "analysis/output/factor_ic_screen.json"
OUT_MD = PROJECT_DIR / "analysis/p2b_factor_ic_report.md"


def collect() -> pd.DataFrame:
    rows = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not idxs:
            continue
        # factor values at signal bars
        fac_cols = {}
        for name, fn in FACTORS.items():
            try:
                fac_cols[name] = fn(enriched).to_numpy()
            except Exception:
                fac_cols[name] = np.full(len(enriched), np.nan)
        close = enriched["close"].to_numpy()
        for si in idxs:
            st = enriched["open_time"].iloc[si]
            if st >= HOLDOUT_START:  # discipline: never touch holdout window
                continue
            ei = si + 1
            if ei + 72 >= len(close):
                continue
            # crude realized_ret proxy = 72-bar forward MFE-neutral close ret
            fwd = close[min(ei + 72, len(close) - 1)] / close[ei] - 1
            row = {"symbol": symbol, "month": str(st)[:7], "fwd_ret": fwd}
            for name in FACTORS:
                row[name] = fac_cols[name][si]
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    df = collect()
    print(f"样本: {len(df)} 候选, {df['symbol'].nunique()} 币种", flush=True)
    results = []
    for name in FACTORS:
        sub = df[[name, "fwd_ret", "month"]].dropna()
        if len(sub) < 200:
            results.append({"factor": name, "n": len(sub), "note": "样本不足"})
            continue
        ic, _ = spearmanr(sub[name], sub["fwd_ret"])
        # monthly IC for IR
        monthly = []
        for _, g in sub.groupby("month"):
            if len(g) >= 30:
                m_ic, _ = spearmanr(g[name], g["fwd_ret"])
                if np.isfinite(m_ic):
                    monthly.append(m_ic)
        ir = (np.mean(monthly) / np.std(monthly)) if len(monthly) >= 3 and np.std(monthly) > 0 else 0.0
        sign_stable = len(monthly) >= 3 and (np.mean(np.sign(monthly) == np.sign(ic)) >= 0.7)
        cls = ("alive" if abs(ic) >= 0.03 and sign_stable
               else "reversed" if abs(ic) >= 0.03 and not sign_stable
               else "dead")
        results.append({"factor": name, "n": int(len(sub)), "ic": round(float(ic), 4),
                        "ir": round(float(ir), 3), "n_months": len(monthly),
                        "sign_stable": bool(sign_stable), "class": cls})
        print(f"  {name:16} IC {ic:+.4f}  IR {ir:+.2f}  {cls}", flush=True)
    results.sort(key=lambda r: -abs(r.get("ic", 0)))
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    alive = [r for r in results if r.get("class") == "alive"]
    md = ["# H19 外部 alpha 因子 IC 筛选（SWAP池, train/val, 未碰holdout）\n",
          f"样本 {len(df)} 候选 / {df['symbol'].nunique()} 币种。IC=Spearman(因子, 72bar前向收益)。\n",
          "| 因子 | IC | IR | 月数 | 符号稳定 | 分类 |", "|---|---|---|---|---|---|"]
    for r in results:
        if "ic" in r:
            md.append(f"| {r['factor']} | {r['ic']:+.4f} | {r['ir']:+.2f} | {r['n_months']} | {'✓' if r['sign_stable'] else '✗'} | {r['class']} |")
    md += ["", f"## 存活因子({len(alive)}个, |IC|≥0.03且符号稳定) → 候选新特征",
           "、".join(r["factor"] for r in alive) or "（无）",
           "", "**下一步**：存活因子逐个（单变量）加进 features.py 验证 top-decile 净收益增益，有增益才留。"]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"\n存活 {len(alive)}/{len(FACTORS)} 个因子 → {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
