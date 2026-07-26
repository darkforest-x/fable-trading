"""IT-18: is the high-ATR "edge" real selection, or just bigger bets vs fixed cost?

IT-17 found the only all-folds-positive selector on the short pool is
atr_pct_HIGH (+0.54% mean net @0.2%), while Spearman(atr_pct, realized_ret) is
consistently NEGATIVE (-0.20..-0.30) -- high-ATR candidates are LESS likely to
win, yet the top ATR decile nets the most. Suspected mechanism: barriers are
TP5*ATR / SL2*ATR, so payoffs scale with ATR, while the cost is a FIXED 0.2%
round trip. Scaling every payoff up shrinks the fixed cost in relative terms --
that is leverage, not skill.

The discriminating statistic is PROFIT FACTOR, which is scale-invariant:
  - pure scaling  -> PF flat across ATR deciles, only net-after-fixed-cost rises
  - real selection -> PF itself rises with ATR

Also reports win rate (should stay ~flat or fall if it is scaling) and
gross/net per decile, plus PF at maker 0.06% for contrast.

Read-only. No holdout (pool ends 2026-05-03). No promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/it18_atr_edge_mechanism.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from scripts.it17_short_rule_vs_lgbm import load_pool  # noqa: E402

LEGACY_COST, MAKER_COST = 0.002, 0.0006


def pf(net: np.ndarray):
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    d, _ = load_pool()
    d = d.dropna(subset=["atr_pct"]).reset_index(drop=True)
    d["decile"] = pd.qcut(d["atr_pct"], 10, labels=False, duplicates="drop")
    print(f"pool={len(d)}  barriers=TP5xATR/SL2xATR  cost=FIXED 0.2% (and 0.06% maker)\n")

    print(f"{'dec':>3} {'n':>5} {'atr_pct中位':>10} {'胜率':>7} {'毛均值':>9} "
          f"{'净@0.2%':>9} {'PF@0.2%':>8} {'PF@0.06%':>9} {'PF毛':>7}")
    rows = []
    for dec, g in d.groupby("decile"):
        r = g["realized_ret"].to_numpy()
        n02, n006 = r - LEGACY_COST, r - MAKER_COST
        row = {
            "decile": int(dec), "n": int(len(g)),
            "atr_pct_median": round(float(g["atr_pct"].median()), 5),
            "win_rate": round(float((g["label"] == 1).mean()), 4),
            "gross_mean": round(float(r.mean()), 5),
            "net_mean_02": round(float(n02.mean()), 5),
            "PF_gross": pf(r), "PF_02": pf(n02), "PF_006": pf(n006),
        }
        rows.append(row)
        print(f"{row['decile']:>3} {row['n']:>5} {row['atr_pct_median']:>10.5f} "
              f"{row['win_rate']:>7.3f} {row['gross_mean']:>+9.5f} "
              f"{row['net_mean_02']:>+9.5f} {str(row['PF_02']):>8} "
              f"{str(row['PF_006']):>9} {str(row['PF_gross']):>7}")

    lo = [r for r in rows if r["decile"] <= 2]
    hi = [r for r in rows if r["decile"] >= 7]
    def avg(rs, k):
        v = [r[k] for r in rs if r[k] is not None]
        return round(float(np.mean(v)), 4) if v else None

    print("\n=== 低波动(decile 0-2) vs 高波动(decile 7-9) ===")
    print(f"  胜率      : {avg(lo,'win_rate')}  →  {avg(hi,'win_rate')}")
    print(f"  毛均值    : {avg(lo,'gross_mean')}  →  {avg(hi,'gross_mean')}")
    print(f"  净@0.2%   : {avg(lo,'net_mean_02')}  →  {avg(hi,'net_mean_02')}")
    print(f"  PF毛      : {avg(lo,'PF_gross')}  →  {avg(hi,'PF_gross')}   <-- 判据")
    print(f"  PF@0.2%   : {avg(lo,'PF_02')}  →  {avg(hi,'PF_02')}")

    # Direction matters, not just magnitude: gross PF is scale-invariant, so
    # only a RISE in gross PF means the ATR cut selects better trades.
    pf_lo, pf_hi = avg(lo, "PF_gross"), avg(hi, "PF_gross")
    wr_lo, wr_hi = avg(lo, "win_rate"), avg(hi, "win_rate")
    if pf_lo is None or pf_hi is None:
        verdict = "UNKNOWN(PF 不可计算)"
    elif pf_hi >= pf_lo + 0.08:
        verdict = "SELECTION(真选出更好的交易): 毛PF随波动率上升"
    elif pf_hi <= pf_lo - 0.08:
        verdict = ("ANTI-SELECTION(高波动其实是更差的交易): 毛PF与胜率双双下降;"
                   "净值变好只因 TP/SL 按 ATR 等比放大而成本固定 = 放大赌注,不是选股能力")
    else:
        verdict = "SCALING(放大赌注): 毛PF基本不变,只有扣固定成本后的净值变好"
    print(f"\n判决: {verdict}")
    print(f"  (毛PF {pf_lo} → {pf_hi};胜率 {wr_lo} → {wr_hi})")

    out = {"pool_n": len(d), "barriers": "TP5xATR/SL2xATR", "costs": [LEGACY_COST, MAKER_COST],
           "deciles": rows,
           "lo_vs_hi": {"win_rate": [avg(lo, "win_rate"), avg(hi, "win_rate")],
                        "gross_mean": [avg(lo, "gross_mean"), avg(hi, "gross_mean")],
                        "net_02": [avg(lo, "net_mean_02"), avg(hi, "net_mean_02")],
                        "PF_gross": [pf_lo, pf_hi],
                        "PF_02": [avg(lo, "PF_02"), avg(hi, "PF_02")]},
           "verdict": verdict}
    (PROJECT / "analysis" / "output" / "it18_atr_edge_mechanism.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
