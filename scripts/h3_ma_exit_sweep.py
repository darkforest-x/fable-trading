"""H3 structure exit: close below EMA21 vs TP5/SL2 baseline (SWAP, val-only).

Uses existing label_candidate_ma_exit (entry / atr floor / horizon same as
mainline; exit when close < ema21, else timeout at horizon). Does not modify
features.py or frozen models. Holdout untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.backtest.run import BAR, simulate, window_metrics
from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import (
    BarrierOutcome,
    label_candidate,
    label_candidate_ma_exit,
)
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

SWEEP_DIR = PROJECT_DIR / "data" / "sweep_h3_ma_exit"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "h3_ma_exit.json"
OUT_MD = PROJECT_DIR / "analysis" / "p15_h3_ma_exit.md"
SWAP_MAKER_COST = 0.0006
SWAP_TAKER_COST = 0.0010

ExitLabeler = Callable[[pd.DataFrame, int], Optional[BarrierOutcome]]

CONFIGS: dict[str, ExitLabeler] = {
    "tp5_sl2_base": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "ma_exit_ema21": lambda f, i: label_candidate_ma_exit(f, i, ma_col="ema21"),
}


def build_records() -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {k: [] for k in CONFIGS}
    n_series = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        n_series += 1
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        opens = enriched["open"].to_numpy()
        lows = enriched["low"].to_numpy()
        for row_pos, signal_i in enumerate(signal_indices):
            entry_i = signal_i + 1
            maker_filled = bool(entry_i < len(lows) and lows[entry_i] < opens[entry_i])
            feats = feature_rows.iloc[row_pos].to_dict()
            for name, labeler in CONFIGS.items():
                outcome = labeler(enriched, signal_i)
                if outcome is None:
                    continue
                records[name].append(
                    {
                        "source": source,
                        "symbol": symbol,
                        "signal_i": signal_i,
                        "signal_time": enriched["open_time"].iloc[signal_i],
                        "maker_filled": maker_filled,
                        "label": outcome.label,
                        "outcome": outcome.outcome,
                        "exit_offset": outcome.exit_offset,
                        "entry_price": outcome.entry_price,
                        "realized_ret": outcome.realized_ret,
                        **feats,
                    }
                )
    print(f"swap series scanned: {n_series}", flush=True)
    return records


def eval_variant(name: str, df: pd.DataFrame) -> dict:
    path = SWEEP_DIR / f"{name}.csv"
    df.to_csv(path, index=False)
    train, val, _ = load_splits(path, horizon_bars=72)
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    metrics = evaluate(y, prob, rets)
    k = max(1, len(prob) // 10)
    top_idx = np.argsort(prob)[-k:]
    filled = val["maker_filled"].to_numpy()[top_idx]
    top_rets = rets[top_idx]

    scored = val.copy()
    scored["score"] = prob
    scored["entry_time"] = scored["signal_time"] + BAR
    scored["exit_time"] = scored["entry_time"] + scored["exit_offset"] * BAR
    scored = scored.sort_values(["entry_time", "score"], ascending=[True, False])
    threshold = float(np.quantile(prob, 0.90))
    maker_pool = scored[scored["maker_filled"]]

    return {
        "config": name,
        "dataset": str(path),
        "n": int(len(df)),
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "positive_rate_val": round(float(val["label"].mean()), 4),
        "val_auc": metrics["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": metrics["top_decile"]["mean_realized_ret"],
        "top_net_taker_010": round(metrics["top_decile"]["mean_realized_ret"] - SWAP_TAKER_COST, 5),
        "top_net_maker_006": round(metrics["top_decile"]["mean_realized_ret"] - SWAP_MAKER_COST, 5),
        "top_win_rate": metrics["top_decile"]["win_rate"],
        "top_maker_fill_rate": round(float(filled.mean()), 3),
        "top_net_maker_filled_only": round(float(top_rets[filled].mean()) - SWAP_MAKER_COST, 5)
        if filled.any()
        else None,
        "threshold_val_q90": round(threshold, 5),
        "portfolio_maker": window_metrics(simulate(maker_pool, threshold), SWAP_MAKER_COST),
        "portfolio_taker": window_metrics(simulate(scored, threshold), SWAP_TAKER_COST),
        "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
        "outcomes": val["outcome"].value_counts().to_dict(),
    }


def write_report(results: list[dict]) -> None:
    by = {r["config"]: r for r in results}
    base = by["tp5_sl2_base"]
    ma = by["ma_exit_ema21"]
    pass_disc = (
        ma["top_net_maker_006"] >= base["top_net_maker_006"] and ma["perm_p"] < 0.01
    )
    md = f"""# P1.5 H3：结构出场（收盘跌破 EMA21）

**日期**：2026-07-15  
**纪律**：发现级 val-only；SWAP 主线宇宙；未评价 holdout；未改候选阈值 / 入场 / 成本假设。  
**标签**：`label_candidate_ma_exit`（入场=次根开盘、ATR 地板、horizon=72 同 TP5；出场=收盘 < ema21，否则 timeout）。无固定 TP/SL 障碍。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/h3_ma_exit_sweep.py
```

## 数据统计

| 配置 | 候选数 | train | val | val 正类率 | 均持仓根数 |
|---|---:|---:|---:|---:|---:|
| TP5/SL2 baseline | {base['n']} | {base['n_train']} | {base['n_val']} | {base['positive_rate_val']:.2%} | {base['mean_exit_bars']} |
| MA-exit EMA21 | {ma['n']} | {ma['n_train']} | {ma['n_val']} | {ma['positive_rate_val']:.2%} | {ma['mean_exit_bars']} |

## top-decile 结果

| 配置 | val AUC | p | top gross | 净@taker0.10% | 净@maker0.06% | top 胜率 | 均持仓 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TP5/SL2 baseline | {base['val_auc']} | {base['perm_p']} | {base['top_gross']:+.5f} | {base['top_net_taker_010']:+.5f} | {base['top_net_maker_006']:+.5f} | {base['top_win_rate']:.2%} | {base['mean_exit_bars']} |
| MA-exit EMA21 | {ma['val_auc']} | {ma['perm_p']} | {ma['top_gross']:+.5f} | {ma['top_net_taker_010']:+.5f} | {ma['top_net_maker_006']:+.5f} | {ma['top_win_rate']:.2%} | {ma['mean_exit_bars']} |

outcome 分布（val）：  
- baseline：`{base['outcomes']}`  
- ma_exit：`{ma['outcomes']}`

## 判定（发现级）

门槛：净@maker ≥ TP5 基线 **且** p < 0.01。

| 项 | 基线 | MA-exit | 是否达标 |
|---|---:|---:|---|
| 净@maker0.06% | {base['top_net_maker_006']:+.5f} | {ma['top_net_maker_006']:+.5f} | {'✓' if ma['top_net_maker_006'] >= base['top_net_maker_006'] else '✗'} |
| perm p | {base['perm_p']} | {ma['perm_p']} | {'✓' if ma['perm_p'] < 0.01 else '✗'} |

**总判定：{'发现级通过' if pass_disc else '未通过'}**

## 解读

- 结构出场用形态自身定义脉冲结束；预期持仓更短、正类率定义变为「出场时仍盈利」。
- 若净@maker 弱于 TP5：说明 EMA21 过早截断赢家，或过晚在亏损侧离场，不如固定障碍的风险收益比。
- 均持仓根数变化直接反映出场节奏差异。

## 风险与诚实声明

- val 已多次选型，数字仅排序；
- MA-exit 无硬止损，极端单笔左尾可能大于 TP5/SL2；
- 未碰 holdout / 冻结模型 / forward_log；
- `label_candidate_ma_exit` 已在库中，本任务补齐 SWAP 对照实验与报告。

## 下一步

1. 通过 → 记录为发现级候选，前向确认前不替换冻结主线。
2. 未通过 → 归档负结果；H1 scaled 仍为最强出场挑战者。
3. 继续 H4 时间衰减紧缩。
"""
    OUT_MD.write_text(md, encoding="utf-8")


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    records = build_records()
    results = []
    for name, rows in records.items():
        df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
        r = eval_variant(name, df)
        results.append(r)
        print(
            f"{name}: auc={r['val_auc']} p={r['perm_p']} "
            f"net_maker={r['top_net_maker_006']:+.5f} bars={r['mean_exit_bars']}",
            flush=True,
        )
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(results)
    print(f"wrote {OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
