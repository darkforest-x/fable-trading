"""Single-variable feature-gain screen: for each H19-survivor factor, add it
ALONE as a 29th feature, retrain, and measure val top-decile net-return gain
vs the 28-feature baseline. Pure measurement -- never edits features.py.

This automates the EVIDENCE. Merging a winner into the mainline (which changes
the frozen production model the forward clock depends on) stays a manual owner
decision. Discipline: train/val only, holdout untouched, one factor at a time.

Input: analysis/output/factor_ic_screen.json (survivors), a SWAP judgment pool.
Output: analysis/output/single_var_gain.json + analysis/p2b_feature_gain_report.md
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
import sys; sys.path.insert(0, str(PROJECT_DIR))
from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import load_splits, train_model, evaluate, permutation_pvalue
from src.factors.library import FACTORS
import lightgbm as lgb

IC_JSON = PROJECT_DIR / "analysis/output/factor_ic_screen.json"
OUT_JSON = PROJECT_DIR / "analysis/output/single_var_gain.json"
OUT_MD = PROJECT_DIR / "analysis/p2b_feature_gain_report.md"
POOL_CSV = PROJECT_DIR / "data/single_var_pool.csv"


def build_pool_with_factors(survivors: list[str]) -> pd.DataFrame:
    """One scan: rule candidates + 28 base features + each survivor factor value."""
    records = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        enriched = add_indicators(frame)
        idxs = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not idxs:
            continue
        featured = add_features(enriched)
        feat_rows = extract_feature_rows(featured, idxs)
        fac_vals = {f: FACTORS[f](enriched).to_numpy() for f in survivors}
        for pos, si in enumerate(idxs):
            o = label_candidate(enriched, si, tp_mult=5.0, sl_mult=2.0)
            if o is None:
                continue
            rec = {"signal_time": enriched["open_time"].iloc[si], "label": o.label,
                   "realized_ret": o.realized_ret}
            rec.update(feat_rows.iloc[pos].to_dict())
            for f in survivors:
                rec[f"fac_{f}"] = fac_vals[f][si]
            records.append(rec)
    return pd.DataFrame(records).sort_values("signal_time").reset_index(drop=True)


def eval_featureset(train, val, cols) -> dict:
    dtr = lgb.Dataset(train[cols], label=train["label"])
    dva = lgb.Dataset(val[cols], label=val["label"], reference=dtr)
    from src.judgment.train import LGB_PARAMS
    model = lgb.train(LGB_PARAMS, dtr, num_boost_round=600, valid_sets=[dva],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
    prob = model.predict(val[cols], num_iteration=model.best_iteration)
    m = evaluate(val["label"].to_numpy(), prob, val["realized_ret"].to_numpy())
    return {"auc": m["roc_auc"], "top_net_02": m["top_decile"]["mean_net_ret"],
            "p": round(permutation_pvalue(val["label"].to_numpy(), prob), 4)}


def main() -> int:
    ic = json.loads(IC_JSON.read_text())
    survivors = [r["factor"] for r in ic if r.get("class") == "alive"]
    if not survivors:
        print("H19 无存活因子，跳过单变量增益筛选"); return 0
    print(f"存活因子 {survivors} -> 逐个单变量增益测试", flush=True)
    pool = build_pool_with_factors(survivors)
    pool.to_csv(POOL_CSV, index=False)
    # reuse load_splits' time discipline via a temp path
    train, val, _ = load_splits(POOL_CSV, horizon_bars=72)
    base = eval_featureset(train, val, FEATURE_COLUMNS)
    results = [{"featureset": "baseline_28", **base}]
    for f in survivors:
        r = eval_featureset(train, val, FEATURE_COLUMNS + [f"fac_{f}"])
        r["gain_net"] = round(r["top_net_02"] - base["top_net_02"], 5)
        results.append({"featureset": f"+{f}", **r})
        print(f"  +{f}: top净 {r['top_net_02']:+.5f} (增益 {r['gain_net']:+.5f})", flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    winners = [r for r in results[1:] if r["gain_net"] > 0 and r["p"] < 0.01]
    md = ["# 单变量特征增益（H19存活因子逐个加入，val only）\n",
          f"基线28维 top净@0.2%: {base['top_net_02']:+.5f} (AUC {base['auc']})\n",
          "| 特征集 | top净@0.2% | 增益 | AUC | p |", "|---|---|---|---|---|"]
    for r in results:
        g = f"{r.get('gain_net',0):+.5f}" if 'gain_net' in r else "—"
        md.append(f"| {r['featureset']} | {r['top_net_02']:+.5f} | {g} | {r['auc']} | {r['p']} |")
    md += ["", f"## 值得合并的因子({len(winners)}个, 增益>0且p<0.01) → **待owner拍板加进features.py**",
           "、".join(r["featureset"] for r in winners) or "（无——现有28维已够，不加）"]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"\n值得合并 {len(winners)} 个（需owner确认）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
