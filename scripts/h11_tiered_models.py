"""H11: tiered LightGBM by 24h notional median (large vs alt) vs pooled SWAP.

Tier edge = median of train-period candidate 24h notional (sum volume*close
over 96x15m bars). Val metrics only; holdout untouched. No features.py edit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import (
    HOLDOUT_START,
    LGB_PARAMS,
    evaluate,
    load_splits,
    permutation_pvalue,
)

OUT_JSON = PROJECT_DIR / "analysis/output/h11_tiered.json"
OUT_MD = PROJECT_DIR / "analysis/p2b_h11_tiered.md"
POOL_CSV = PROJECT_DIR / "data/h11_tiered_pool.csv"
SWAP_MAKER_COST = 0.0006


def collect() -> pd.DataFrame:
    rows = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        enriched = add_indicators(frame)
        notional = (enriched["volume"] * enriched["close"]).rolling(96, min_periods=48).sum()
        idxs = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not idxs:
            continue
        featured = add_features(enriched)
        feats = extract_feature_rows(featured, idxs)
        for pos, si in enumerate(idxs):
            st = pd.Timestamp(enriched["open_time"].iloc[si])
            if st.tzinfo is None:
                st = st.tz_localize("UTC")
            else:
                st = st.tz_convert("UTC")
            if st >= HOLDOUT_START:
                continue
            o = label_candidate(enriched, si, tp_mult=5.0, sl_mult=2.0)
            if o is None:
                continue
            rec = {
                "source": source,
                "symbol": symbol,
                "signal_time": st,
                "label": o.label,
                "realized_ret": o.realized_ret,
                "notional_24h": float(notional.iloc[si]),
            }
            rec.update(feats.iloc[pos].to_dict())
            rows.append(rec)
    return pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)


def train_eval(train: pd.DataFrame, val: pd.DataFrame, tag: str) -> dict:
    if len(train) < 200 or len(val) < 50:
        return {"tag": tag, "n_train": len(train), "n_val": len(val), "note": "样本不足"}
    dtr = lgb.Dataset(train[FEATURE_COLUMNS], label=train["label"])
    dva = lgb.Dataset(val[FEATURE_COLUMNS], label=val["label"], reference=dtr)
    model = lgb.train(
        LGB_PARAMS,
        dtr,
        num_boost_round=600,
        valid_sets=[dva],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    return {
        "tag": tag,
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "positive_rate_val": round(float(val["label"].mean()), 4),
        "val_auc": m["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": m["top_decile"]["mean_realized_ret"],
        "top_net_maker_006": round(m["top_decile"]["mean_realized_ret"] - SWAP_MAKER_COST, 5),
        "top_win_rate": m["top_decile"]["win_rate"],
    }


def main() -> int:
    print("building pool…", flush=True)
    pool = collect()
    pool = pool.dropna(subset=list(FEATURE_COLUMNS) + ["notional_24h", "label", "realized_ret"])
    pool.to_csv(POOL_CSV, index=False)
    train, val, _ = load_splits(POOL_CSV, horizon_bars=72)
    median = float(train["notional_24h"].median())
    print(f"train 24h notional median: {median:.2f}", flush=True)

    results = []
    # pooled
    results.append(train_eval(train, val, "pooled"))

    # tiered: train/eval within tier
    for name, mask_tr, mask_va in (
        ("large", train["notional_24h"] >= median, val["notional_24h"] >= median),
        ("alt", train["notional_24h"] < median, val["notional_24h"] < median),
    ):
        results.append(train_eval(train[mask_tr], val[mask_va], f"tier_{name}"))

    # stacked: score each val row with its tier model, compare overall metrics
    models = {}
    for name, mask_tr in (
        ("large", train["notional_24h"] >= median),
        ("alt", train["notional_24h"] < median),
    ):
        tr = train[mask_tr]
        va_ref = val[val["notional_24h"] >= median] if name == "large" else val[val["notional_24h"] < median]
        if len(tr) < 200 or len(va_ref) < 30:
            continue
        dtr = lgb.Dataset(tr[FEATURE_COLUMNS], label=tr["label"])
        dva = lgb.Dataset(va_ref[FEATURE_COLUMNS], label=va_ref["label"], reference=dtr)
        models[name] = lgb.train(
            LGB_PARAMS,
            dtr,
            num_boost_round=600,
            valid_sets=[dva],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )

    if len(models) == 2:
        scores = np.zeros(len(val))
        for i, row in enumerate(val.itertuples()):
            name = "large" if row.notional_24h >= median else "alt"
            scores[i] = models[name].predict(
                val.iloc[[i]][FEATURE_COLUMNS], num_iteration=models[name].best_iteration
            )[0]
        y = val["label"].to_numpy()
        rets = val["realized_ret"].to_numpy()
        m = evaluate(y, scores, rets)
        results.append(
            {
                "tag": "tiered_stacked",
                "n_train": int(len(train)),
                "n_val": int(len(val)),
                "positive_rate_val": round(float(val["label"].mean()), 4),
                "val_auc": m["roc_auc"],
                "perm_p": round(permutation_pvalue(y, scores), 4),
                "top_gross": m["top_decile"]["mean_realized_ret"],
                "top_net_maker_006": round(m["top_decile"]["mean_realized_ret"] - SWAP_MAKER_COST, 5),
                "top_win_rate": m["top_decile"]["win_rate"],
            }
        )

    for r in results:
        print(r, flush=True)

    payload = {"median_notional_24h": median, "results": results}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    by = {r["tag"]: r for r in results if "val_auc" in r}
    pooled = by.get("pooled", {})
    stacked = by.get("tiered_stacked", {})
    better = (
        stacked
        and stacked.get("top_net_maker_006", -9) > pooled.get("top_net_maker_006", 0)
        and stacked.get("val_auc", 0) >= pooled.get("val_auc", 0)
        and stacked.get("perm_p", 1) < 0.01
    )

    lines = [
        "# H11 市值/流动性分层模型（SWAP 24h 成交额中位数二分）\n",
        "**日期**：2026-07-15  ",
        "**纪律**：val-only；holdout 未碰；分层边界 = **train** 候选 24h 名义成交额中位数。  ",
        f"**边界**：median notional_24h = {median:.4g}\n",
        "## 复现命令\n",
        "```bash",
        "PYTHONPATH=. python3 scripts/h11_tiered_models.py",
        "```\n",
        "## 结果\n",
        "| 模型 | n_train | n_val | AUC | p | top 净@maker | top 胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        if "val_auc" not in r:
            lines.append(f"| {r['tag']} | {r.get('n_train')} | {r.get('n_val')} | — | — | — | {r.get('note','')} |")
            continue
        lines.append(
            f"| {r['tag']} | {r['n_train']} | {r['n_val']} | {r['val_auc']} | {r['perm_p']} | "
            f"{r['top_net_maker_006']:+.5f} | {r['top_win_rate']:.2%} |"
        )
    lines += [
        "",
        "## 判定\n",
        f"分层堆叠 vs 合池：AUC {stacked.get('val_auc','—')} vs {pooled.get('val_auc','—')}；"
        f"净@maker {stacked.get('top_net_maker_006', float('nan')):+.5f} vs "
        f"{pooled.get('top_net_maker_006', float('nan')):+.5f}。\n",
        f"**总判定：{'分层稳定优于合池（发现级）' if better else '分层未稳定优于合池'}**\n",
        "## 解读\n",
        "- large/alt 分训检验异质边际；stacked 才是可部署的对照（每笔用对应层模型）。",
        "- 若分 tier 样本过小，AUC 波动大，不得宣称分层胜利。\n",
        "## 风险与诚实声明\n",
        "- 24h 名义成交额是流动性代理，非流通市值；",
        "- 中位数边界会随时间漂移，实盘需滚动重估；",
        "- val 多次看数；未改主线。\n",
        "## 下一步\n",
        "未优则维持合池冻结模型；若 owner 要分层，需独立前向账本。",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
