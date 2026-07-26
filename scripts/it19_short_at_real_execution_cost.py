"""IT-19: re-price the short chain at the cost the EXECUTOR actually pays.

The 100-coin short walk-forward was judged at 0.2% (LEGACY_P0_ROUND_TRIP), but
src/costs.py says that number is a P0 spot-era blanket assumption kept only for
reporting continuity -- "Not used for decisions". The mainline universe is SWAP.

And the maker route is not reachable by the current executor either:
  src/execution/executor.py  -> "place market + TP/SL bracket"
  okx_client.place_market    -> ordType="market"            (entry  = TAKER)
  okx_client OCO             -> tpOrdPx="-1", slOrdPx="-1"  (exits  = MARKET
                                on trigger, i.e. TAKER)
Every leg is taker, so SWAP_MAKER (0.0006, which also bundles a slippage
allowance) is unreachable; SWAP_TAKER (0.0010 = 0.05%/side, fees only, NO
slippage allowance) is the floor, and real slippage on alt swaps sits on top.

This reports the pool's economics across the honest cost ladder, per ATR decile
and walk-forward, so the owner can decide the cost line with numbers in hand.
Gross PF is unchanged by cost and is shown as the scale-invariant reference.

Read-only. No holdout. No promote. Cost assumptions are owner decisions -- this
script only MEASURES sensitivity, it does not adopt a number.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/it19_short_at_real_execution_cost.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.costs import LEGACY_P0_ROUND_TRIP, SWAP_MAKER, SWAP_TAKER  # noqa: E402
from scripts.it17_short_rule_vs_lgbm import folds, load_pool, top_net  # noqa: E402

# taker floor, then taker + realistic slippage allowances
LADDER = [
    ("SWAP_MAKER 0.06% (executor 拿不到)", SWAP_MAKER),
    ("SWAP_TAKER 0.10% (纯手续费,无滑点)", SWAP_TAKER),
    ("taker+0.05% 滑点 = 0.15%", 0.0015),
    ("taker+0.10% 滑点 = 0.20%", 0.0020),
    ("LEGACY_P0 0.20% (仓库标注:不用于决策)", LEGACY_P0_ROUND_TRIP),
]


def pf(net: np.ndarray):
    w, l = net[net > 0].sum(), net[net < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    d, _ = load_pool()
    r = d["realized_ret"].to_numpy()
    print(f"pool={len(d)}  毛均值={r.mean():+.5f}  毛PF={pf(r)}  "
          f"胜率={(d['label']==1).mean():.3f}\n")

    print("=== 全池(不做任何选择)在各成本下 ===")
    print(f"{'成本口径':<38} {'净均值':>10} {'PF':>7}")
    whole = []
    for name, c in LADDER:
        n = r - c
        whole.append({"cost_name": name, "cost": c,
                      "net_mean": round(float(n.mean()), 5), "PF": pf(n)})
        print(f"{name:<38} {n.mean():>+10.5f} {str(pf(n)):>7}")

    print("\n=== 高ATR分位(decile 9)在各成本下 ===")
    d9 = d[d["atr_pct"] >= d["atr_pct"].quantile(0.9)]
    r9 = d9["realized_ret"].to_numpy()
    print(f"(n={len(d9)} 毛PF={pf(r9)} 胜率={(d9['label']==1).mean():.3f} "
          f"— 毛PF低于全池,见 IT-18)")
    hi = []
    for name, c in LADDER:
        n = r9 - c
        hi.append({"cost_name": name, "cost": c,
                   "net_mean": round(float(n.mean()), 5), "PF": pf(n)})
        print(f"{name:<38} {n.mean():>+10.5f} {str(pf(n)):>7}")

    print("\n=== walk-forward: atr_pct_HIGH top-decile 每折净值 ===")
    wf = {}
    for name, c in LADDER:
        per = [top_net(v, v["atr_pct"].to_numpy(), c)[0] for _, v in folds(d)]
        a = np.array(per)
        wf[name] = {"cost": c, "folds": per, "mean": round(float(a.mean()), 5),
                    "n_pos": int((a > 0).sum()), "n_folds": len(a)}
        print(f"{name:<38} {[f'{v:+.4f}' for v in per]} "
              f"mean={a.mean():+.5f} {int((a>0).sum())}/{len(a)}+")

    out = {
        "executor_route": {
            "entry": 'okx_client.place_market ordType="market" -> TAKER',
            "exits": 'OCO tpOrdPx="-1"/slOrdPx="-1" -> market on trigger -> TAKER',
            "conclusion": "全部腿 taker;SWAP_MAKER 0.0006 不可达,SWAP_TAKER 0.0010 是地板(不含滑点)",
        },
        "pool_n": len(d), "gross_mean": round(float(r.mean()), 5), "gross_PF": pf(r),
        "whole_pool_by_cost": whole,
        "atr_decile9_by_cost": hi,
        "walkforward_atr_high_topdecile": wf,
        "note": "成本假设是 owner 决策(CLAUDE.md);本脚本只测敏感度,不采纳任何数字。",
    }
    (PROJECT / "analysis" / "output" / "it19_short_at_real_execution_cost.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
