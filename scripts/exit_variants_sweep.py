"""SWAP val-only H1/H2 exit-variant sweep for the research agenda.

Compares the TP5/SL2 mainline against H1 scaled take-profit and H2 breakeven
shift on the current perpetual-swap universe. This is discovery-tier only:
train/val split is used, holdout is not evaluated.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from src.backtest.run import BAR, simulate, window_metrics
from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import (
    BarrierOutcome,
    label_candidate,
    label_candidate_breakeven,
    label_candidate_scaled,
)
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
SWEEP_DIR = PROJECT_DIR / "data" / "sweep_exits_swap"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "exit_variants_swap.json"
SWAP_MAKER_COST = 0.0006
SWAP_TAKER_COST = 0.0010

ExitLabeler = Callable[[pd.DataFrame, int], Optional[BarrierOutcome]]


def _tp5_sl2_base(frame: pd.DataFrame, signal_i: int) -> BarrierOutcome | None:
    return label_candidate(frame, signal_i, tp_mult=5.0, sl_mult=2.0)


def _scaled_25_t3(frame: pd.DataFrame, signal_i: int) -> BarrierOutcome | None:
    return label_candidate_scaled(frame, signal_i, tp1_mult=2.5, trail_mult=3.0)


def _breakeven_15(frame: pd.DataFrame, signal_i: int) -> BarrierOutcome | None:
    return label_candidate_breakeven(frame, signal_i, tp_mult=5.0, be_trigger=1.5)


CONFIGS: dict[str, ExitLabeler] = {
    "tp5_sl2_base": _tp5_sl2_base,
    "scaled_25_t3": _scaled_25_t3,
    "breakeven_15": _breakeven_15,
}


def should_include_symbol(symbol: str) -> bool:
    return symbol.endswith("_USDT_SWAP")


def build_records() -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {k: [] for k in CONFIGS}
    n_series = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not should_include_symbol(symbol):
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
                records[name].append({
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
                })
    print(f"swap series scanned: {n_series}")
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


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    records = build_records()
    results = []
    for name, rows in records.items():
        df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
        results.append(eval_variant(name, df))
        print(results[-1], flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    flat = []
    for result in results:
        flat.append({
            "config": result["config"],
            "val_auc": result["val_auc"],
            "perm_p": result["perm_p"],
            "top_net_maker_006": result["top_net_maker_006"],
            "top_win_rate": result["top_win_rate"],
            "pf_maker": result["portfolio_maker"].get("profit_factor"),
            "maxdd_maker": result["portfolio_maker"].get("max_drawdown_pct"),
        })
    print(pd.DataFrame(flat).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
