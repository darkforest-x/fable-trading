"""Frozen-pipeline replication test on the OKX perpetual-swap universe.

Owner directive 2026-07-08: live trading targets swaps, so the validated
pipeline must replicate there. This runs the UNCHANGED expanded-pool rules,
features and training discipline on *_USDT_SWAP series only, labels with the
v2 anchor (TP4/SL2) and the v3 candidate (TP5/SL2), and reports val-only
metrics under swap economics:
  taker 0.05%/side -> 0.10% round trip; maker 0.02%/side plus either the
  legacy ~0.02% funding approximation or realized OKX funding history when
  the trade interval is covered by data/funding/.
No holdout access. Run offline via scripts/offline_pipeline.sh.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.funding import MAKER_FEE_ROUND_TRIP, funding_costs_for_trades
from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_DIR / "data" / "ma206"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "swap_replication_ma206.json"
TAKER_FEE_ROUND_TRIP = 0.0010
COSTS = {"taker_010": TAKER_FEE_ROUND_TRIP, "maker_006": 0.0006}
CONFIGS = {"tp4_sl2": (4.0, 2.0), "tp5_sl2": (5.0, 2.0)}


def build() -> dict[str, pd.DataFrame]:
    records: dict[str, list[dict]] = {name: [] for name in CONFIGS}
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
            for name, (tp, sl) in CONFIGS.items():
                outcome = label_candidate(enriched, signal_i, tp_mult=tp, sl_mult=sl)
                if outcome is None:
                    continue
                records[name].append({
                    "source": source, "symbol": symbol, "signal_i": signal_i,
                    "signal_time": enriched["open_time"].iloc[signal_i],
                    "maker_filled": maker_filled,
                    "label": outcome.label, "outcome": outcome.outcome,
                    "exit_offset": outcome.exit_offset, "entry_price": outcome.entry_price,
                    "realized_ret": outcome.realized_ret, **feats,
                })
    print(f"swap series scanned: {n_series}")
    return {k: pd.DataFrame(v).sort_values("signal_time").reset_index(drop=True)
            for k, v in records.items()}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for name, df in build().items():
        path = OUT_DIR / f"swap_{name}_ma206.csv"
        df.to_csv(path, index=False)
        train, val, _ = load_splits(path, horizon_bars=72)  # holdout unused
        model = train_model(train, val)
        prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        y, rets = val["label"].to_numpy(), val["realized_ret"].to_numpy()
        m = evaluate(y, prob, rets)
        k = max(1, len(prob) // 10)
        top_idx = np.argsort(prob)[-k:]
        filled = val["maker_filled"].to_numpy()[top_idx]
        top_rets = rets[top_idx]
        funding_cost = funding_costs_for_trades(val).to_numpy()
        top_funding_cost = funding_cost[top_idx]
        top_funding_available = np.isfinite(top_funding_cost)
        row = {
            "config": name, "n_candidates": int(len(df)),
            "n_train": int(len(train)), "n_val": int(len(val)),
            "val_auc": m["roc_auc"], "perm_p": round(permutation_pvalue(y, prob), 4),
            "top_gross": m["top_decile"]["mean_realized_ret"],
            "top_win_rate": m["top_decile"]["win_rate"],
            "maker_fill_rate": round(float(filled.mean()), 3),
            "funding_available_rate": round(float(np.isfinite(funding_cost).mean()), 3),
            "top_funding_available_rate": round(float(top_funding_available.mean()), 3),
        }
        for cname, c in COSTS.items():
            row[f"top_net_{cname}"] = round(m["top_decile"]["mean_realized_ret"] - c, 5)
        if filled.any():
            row["top_net_maker_filled_only"] = round(float(top_rets[filled].mean()) - COSTS["maker_006"], 5)
        if top_funding_available.any():
            covered_rets = top_rets[top_funding_available]
            covered_funding = top_funding_cost[top_funding_available]
            row["top_mean_real_funding_cost"] = round(float(covered_funding.mean()), 5)
            row["top_net_taker_real_funding_available"] = round(
                float((covered_rets - TAKER_FEE_ROUND_TRIP - covered_funding).mean()), 5
            )
            maker_real_net = covered_rets - MAKER_FEE_ROUND_TRIP - covered_funding
            row["top_net_maker_real_funding_available"] = round(float(maker_real_net.mean()), 5)
            maker_approx_net = covered_rets - COSTS["maker_006"]
            row["top_net_maker_real_vs_approx_delta"] = round(float(maker_real_net.mean() - maker_approx_net.mean()), 5)
            filled_available = filled & top_funding_available
            if filled_available.any():
                row["top_net_maker_real_funding_filled_only"] = round(
                    float((top_rets[filled_available] - MAKER_FEE_ROUND_TRIP - top_funding_cost[filled_available]).mean()),
                    5,
                )
        results.append(row)
        print(row, flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(pd.DataFrame(results).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
