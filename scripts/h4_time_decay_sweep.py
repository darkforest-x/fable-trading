"""H4 time-decay stop: SL tightens 0.25xATR every 12 bars vs TP5/SL2 (SWAP val)."""
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
    label_candidate_time_decay,
)
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

SWEEP_DIR = PROJECT_DIR / "data" / "sweep_h4_time_decay"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "h4_time_decay.json"
OUT_MD = PROJECT_DIR / "analysis" / "p15_h4_time_decay.md"
SWAP_MAKER_COST = 0.0006
SWAP_TAKER_COST = 0.0010

ExitLabeler = Callable[[pd.DataFrame, int], Optional[BarrierOutcome]]
CONFIGS: dict[str, ExitLabeler] = {
    "tp5_sl2_base": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "time_decay_12x025": lambda f, i: label_candidate_time_decay(
        f, i, tp_mult=5.0, sl_mult=2.0, tighten_every=12, tighten_step=0.25
    ),
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
        "portfolio_maker": window_metrics(simulate(maker_pool, threshold), SWAP_MAKER_COST),
        "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
        "outcomes": val["outcome"].value_counts().to_dict(),
    }


def write_report(results: list[dict]) -> None:
    by = {r["config"]: r for r in results}
    base, td = by["tp5_sl2_base"], by["time_decay_12x025"]
    ok = td["top_net_maker_006"] >= base["top_net_maker_006"] and td["perm_p"] < 0.01
    md = f"""# P1.5 H4：时间衰减紧缩出场

**日期**：2026-07-15  
**纪律**：发现级 val-only；SWAP；未碰 holdout。  
**规则**：TP 固定 5×ATR；SL 初始 2×ATR，持仓每 12 根收紧 0.25×ATR（止损上移，距离地板 0）。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/h4_time_decay_sweep.py
```

## 数据统计

| 配置 | 候选 | train | val | 正类率 | 均持仓根数 |
|---|---:|---:|---:|---:|---:|
| TP5/SL2 | {base['n']} | {base['n_train']} | {base['n_val']} | {base['positive_rate_val']:.2%} | {base['mean_exit_bars']} |
| time_decay 12×0.25 | {td['n']} | {td['n_train']} | {td['n_val']} | {td['positive_rate_val']:.2%} | {td['mean_exit_bars']} |

## top-decile

| 配置 | AUC | p | top gross | 净@maker0.06% | top 胜率 | 均持仓 |
|---|---:|---:|---:|---:|---:|---:|
| TP5/SL2 | {base['val_auc']} | {base['perm_p']} | {base['top_gross']:+.5f} | {base['top_net_maker_006']:+.5f} | {base['top_win_rate']:.2%} | {base['mean_exit_bars']} |
| time_decay | {td['val_auc']} | {td['perm_p']} | {td['top_gross']:+.5f} | {td['top_net_maker_006']:+.5f} | {td['top_win_rate']:.2%} | {td['mean_exit_bars']} |

outcomes val：baseline `{base['outcomes']}`；decay `{td['outcomes']}`

## 判定

净@maker ≥ TP5 且 p<0.01 → **{'发现级通过' if ok else '未通过'}**  
（maker 净：基线 {base['top_net_maker_006']:+.5f} vs decay {td['top_net_maker_006']:+.5f}；p={td['perm_p']}）

## 解读

时间衰减压缩长时间横盘的左尾，但也更易在回调中提前出场。若净弱于 TP5，说明本池脉冲时长常越过收紧点后才到 TP，收紧伤害赢家多于保护。

## 风险与诚实声明

- val 多次选型；无前视；未改冻结主线；
- SL 距离可收到 0（等价保本），极端同 bar 仍保守记 SL。

## 下一步

未通过则归档；通过则与 H1/H3 并列发现级候选。继续 H5 波动率自适应。
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
