"""H5: vol-adaptive TP/SL mults by atr_pct tertile vs fixed TP5 (SWAP val-only).

Tertile edges from train-period candidate atr_pct only (no val/holdout leak).
Low vol → TP4/SL1.6; mid → TP5/SL2; high → TP6/SL2.4.
Also reports top-decile net@maker sliced by val atr_pct tertile.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate, label_candidate_vol_adaptive, vol_adaptive_mults
from src.judgment.train import (
    HOLDOUT_START,
    evaluate,
    load_splits,
    permutation_pvalue,
    train_model,
)

SWEEP_DIR = PROJECT_DIR / "data" / "sweep_h5_vol_adaptive"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "h5_vol_adaptive.json"
OUT_MD = PROJECT_DIR / "analysis" / "p15_h5_vol_adaptive.md"
SWAP_MAKER_COST = 0.0006
TP_BY = (4.0, 5.0, 6.0)
SL_BY = (1.6, 2.0, 2.4)


def collect_raw() -> pd.DataFrame:
    rows = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        enriched = add_indicators(frame)
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
            atr_pct = float(enriched["atr_pct"].iloc[si])
            base = label_candidate(enriched, si, tp_mult=5.0, sl_mult=2.0)
            if base is None:
                continue
            rec = {
                "source": source,
                "symbol": symbol,
                "signal_i": si,
                "signal_time": st,
                "atr_pct": atr_pct,
                "label_fixed": base.label,
                "realized_ret_fixed": base.realized_ret,
                "exit_offset_fixed": base.exit_offset,
                "outcome_fixed": base.outcome,
            }
            rec.update(feats.iloc[pos].to_dict())
            rows.append(rec)
            # stash frame ref via symbol+si only; re-label adaptive after edges known
    return pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)


def add_adaptive_labels(df: pd.DataFrame, q33: float, q66: float) -> pd.DataFrame:
    """Re-scan series to attach adaptive outcomes (needs OHLC again)."""
    # index candidates by (symbol, signal_i)
    need = {(r.symbol, int(r.signal_i)): i for i, r in df.iterrows()}
    labels = {}
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        enriched = add_indicators(frame)
        # only signal indices present in df for this symbol
        sis = [si for (sym, si) in need if sym == symbol]
        for si in sis:
            o = label_candidate_vol_adaptive(
                enriched, si, q33=q33, q66=q66, tp_by_tertile=TP_BY, sl_by_tertile=SL_BY
            )
            if o is None:
                continue
            tp, sl, tier = vol_adaptive_mults(float(enriched["atr_pct"].iloc[si]), q33, q66, tp_by_tertile=TP_BY, sl_by_tertile=SL_BY)
            labels[(symbol, si)] = {
                "label": o.label,
                "realized_ret": o.realized_ret,
                "exit_offset": o.exit_offset,
                "outcome": o.outcome,
                "tp_mult": tp,
                "sl_mult": sl,
                "vol_tier": tier,
            }
    out = df.copy()
    for col in ("label", "realized_ret", "exit_offset", "outcome", "tp_mult", "sl_mult", "vol_tier"):
        out[col] = [
            labels.get((r.symbol, int(r.signal_i)), {}).get(col, np.nan) for r in out.itertuples()
        ]
    out = out.dropna(subset=["label", "realized_ret"])
    out["label"] = out["label"].astype(int)
    return out


def eval_df(name: str, df: pd.DataFrame, label_col: str, ret_col: str) -> dict:
    path = SWEEP_DIR / f"{name}.csv"
    work = df.copy()
    work["label"] = work[label_col]
    work["realized_ret"] = work[ret_col]
    work.to_csv(path, index=False)
    train, val, _ = load_splits(path, horizon_bars=72)
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    # per-tier on val using atr_pct tertiles of val itself for reporting
    val = val.copy()
    val["score"] = prob
    q33v, q66v = val["atr_pct"].quantile([1 / 3, 2 / 3]).to_list()
    tiers = []
    for t, lo, hi in (
        (0, -np.inf, q33v),
        (1, q33v, q66v),
        (2, q66v, np.inf),
    ):
        mask = (val["atr_pct"] > lo) & (val["atr_pct"] <= hi) if t > 0 else val["atr_pct"] <= hi
        sub = val[mask]
        if len(sub) < 30:
            tiers.append({"tier": t, "n": int(len(sub)), "note": "too few"})
            continue
        k = max(1, len(sub) // 10)
        top = sub.nlargest(k, "score")
        tiers.append(
            {
                "tier": t,
                "n": int(len(sub)),
                "top_n": int(len(top)),
                "top_gross": round(float(top["realized_ret"].mean()), 5),
                "top_net_maker": round(float(top["realized_ret"].mean()) - SWAP_MAKER_COST, 5),
                "top_win_rate": round(float((top["realized_ret"] > 0).mean()), 4),
            }
        )
    return {
        "config": name,
        "n": int(len(df)),
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "positive_rate_val": round(float(val["label"].mean()), 4),
        "val_auc": m["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": m["top_decile"]["mean_realized_ret"],
        "top_net_maker_006": round(m["top_decile"]["mean_realized_ret"] - SWAP_MAKER_COST, 5),
        "top_win_rate": m["top_decile"]["win_rate"],
        "mean_exit_bars": round(float(val["exit_offset"].mean()) if "exit_offset" in val else float("nan"), 1)
        if "exit_offset" in val.columns
        else None,
        "by_vol_tier_val": tiers,
    }


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    print("collect candidates…", flush=True)
    raw = collect_raw()
    # tertile edges from train portion of dev only (first 80% by time)
    split_i = int(len(raw) * 0.8)
    train_atr = raw.iloc[:split_i]["atr_pct"]
    q33, q66 = train_atr.quantile([1 / 3, 2 / 3]).to_list()
    print(f"train atr_pct tertile edges: q33={q33:.6f} q66={q66:.6f}", flush=True)

    print("label adaptive…", flush=True)
    full = add_adaptive_labels(raw, q33, q66)
    # fixed uses precomputed columns
    fixed = full.copy()
    fixed["label"] = fixed["label_fixed"]
    fixed["realized_ret"] = fixed["realized_ret_fixed"]
    fixed["exit_offset"] = fixed["exit_offset_fixed"]

    fixed["exit_offset"] = fixed["exit_offset_fixed"]
    r_fixed = eval_df("tp5_sl2_fixed", fixed, "label_fixed", "realized_ret_fixed")
    r_adapt = eval_df("vol_adaptive", full, "label", "realized_ret")

    payload = {
        "q33": q33,
        "q66": q66,
        "tp_by_tertile": list(TP_BY),
        "sl_by_tertile": list(SL_BY),
        "results": [r_fixed, r_adapt],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = (
        r_adapt["top_net_maker_006"] >= r_fixed["top_net_maker_006"]
        and r_adapt["perm_p"] < 0.01
    )
    def tier_table(r):
        lines = []
        for t in r["by_vol_tier_val"]:
            if "top_net_maker" in t:
                lines.append(
                    f"| {t['tier']} | {t['n']} | {t['top_net_maker']:+.5f} | {t['top_win_rate']:.2%} |"
                )
            else:
                lines.append(f"| {t['tier']} | {t['n']} | — | — |")
        return "\n".join(lines)

    md = f"""# P1.5 H5：波动率自适应障碍

**日期**：2026-07-15  
**纪律**：发现级 val-only；SWAP；三分位边界只用 **train 时段** 候选 `atr_pct`（防泄漏）。  
**缩放**：低波动 TP4/SL1.6，中 TP5/SL2，高 TP6/SL2.4。对照固定 TP5/SL2。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/h5_vol_adaptive.py
```

## 数据统计

- 三分位边界（train）：q33={q33:.6f}，q66={q66:.6f}
- 固定：n={r_fixed['n']} train={r_fixed['n_train']} val={r_fixed['n_val']} 正类率={r_fixed['positive_rate_val']:.2%}
- 自适应：n={r_adapt['n']} train={r_adapt['n_train']} val={r_adapt['n_val']} 正类率={r_adapt['positive_rate_val']:.2%}

## 总体 top-decile

| 配置 | AUC | p | top gross | 净@maker0.06% | top 胜率 |
|---|---:|---:|---:|---:|---:|
| 固定 TP5/SL2 | {r_fixed['val_auc']} | {r_fixed['perm_p']} | {r_fixed['top_gross']:+.5f} | {r_fixed['top_net_maker_006']:+.5f} | {r_fixed['top_win_rate']:.2%} |
| 波动率自适应 | {r_adapt['val_auc']} | {r_adapt['perm_p']} | {r_adapt['top_gross']:+.5f} | {r_adapt['top_net_maker_006']:+.5f} | {r_adapt['top_win_rate']:.2%} |

## 分波动率层（val 内三分位，top10% 净@maker）

### 固定 TP5
| tier | n | top 净@maker | top 胜率 |
|---|---:|---:|---:|
{tier_table(r_fixed)}

### 自适应
| tier | n | top 净@maker | top 胜率 |
|---|---:|---:|---:|
{tier_table(r_adapt)}

## 判定

净@maker ≥ 固定 TP5 且 p<0.01 → **{'发现级通过' if ok else '未通过'}**

## 解读

低波动收窄障碍可降低成本摩擦占比；高波动放宽给趋势空间。若分层显示仅某一 tier 受益而总体持平/变差，说明缩放比未校准或标签噪声主导。

## 风险与诚实声明

- 三分位边界来自本池 train，换宇宙需重估；
- val 多次看数；未碰 holdout / 冻结模型；
- 未改主线 barrier 默认值。

## 下一步

未通过则保留固定 TP5；通过则与 H1/H3 一并前向影子。
"""
    OUT_MD.write_text(md, encoding="utf-8")
    print(
        f"fixed net={r_fixed['top_net_maker_006']:+.5f} adaptive={r_adapt['top_net_maker_006']:+.5f} "
        f"→ {'PASS' if ok else 'FAIL'}",
        flush=True,
    )
    print(f"wrote {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
