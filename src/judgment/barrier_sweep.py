"""Exit-structure sweep (v3 exploration, owner-requested 2026-07-08).

One pass over the expanded pool: candidates + features are computed once,
then every candidate is labeled under each exit config; each config gets its
own dataset CSV and a fresh LightGBM train/val evaluation via the unchanged
src.judgment.train pipeline (load_splits purge follows each config's horizon).

Round 2 adds:
- longer horizons (the round-1 lesson: TP beyond ~5xATR dies within 72 bars;
  trend exits need TIME as well as room);
- a maker-fill model: a resting limit at the entry bar's open is counted
  filled only if the bar trades strictly below the open (low < open). This
  encodes maker adverse selection honestly -- runaway winners that never
  tick back are MISSED, not filled. Maker round-trip cost 0.16% (0.08%/side)
  vs taker 0.30%.

Discipline: selection happens on VAL ONLY -- holdout untouched. Every config
evaluated here is another look at val; confirmation belongs to forward data.

Usage: python3 -m src.judgment.barrier_sweep
Output: data/sweep_v3/*.csv, analysis/output/p2b_v3_sweep2.json + stdout table.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.build_dataset import _dedupe_cross_source
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import (
    BarrierOutcome,
    label_candidate,
    label_candidate_breakeven,
    label_candidate_ma_exit,
    label_candidate_scaled,
    label_candidate_trailing,
)
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[2]
SWEEP_DIR = PROJECT_DIR / "data" / "sweep_v3"
OUTPUT_JSON = PROJECT_DIR / "analysis" / "output" / "p2b_v3_sweep2.json"
MIN_BARS = 500
TAKER_COST = 0.003   # 0.15%/side incl. slippage (stage-3 base case)
MAKER_COST = 0.0016  # 0.08%/side limit orders, owner route D

# Round-2 grid: the two round-1 leaders anchored at h72, then time-extended
# structures. Candidates are scanned once with the max horizon so every
# config shares the same signal set.
CONFIGS: dict[str, dict] = {
    "tp5_sl2_h72": {"exit": "fixed", "tp": 5.0, "sl": 2.0, "horizon": 72},    # round-1 winner (anchor)
    "tp5_sl2_h144": {"exit": "fixed", "tp": 5.0, "sl": 2.0, "horizon": 144},
    "tp6_sl2_h144": {"exit": "fixed", "tp": 6.0, "sl": 2.0, "horizon": 144},
    "tp8_sl2_h192": {"exit": "fixed", "tp": 8.0, "sl": 2.0, "horizon": 192},
    "trail3_h144": {"exit": "trailing", "trail": 3.0, "horizon": 144},
    "trail4_h192": {"exit": "trailing", "trail": 4.0, "horizon": 192},
}
MAX_HORIZON = max(c["horizon"] for c in CONFIGS.values())


ExitPlugin = Callable[[pd.DataFrame, int, dict], Optional[BarrierOutcome]]


def _fixed_exit(enriched: pd.DataFrame, signal_i: int, cfg: dict) -> BarrierOutcome | None:
    return label_candidate(
        enriched, signal_i, tp_mult=cfg["tp"], sl_mult=cfg["sl"], horizon=cfg["horizon"])


def _trailing_exit(enriched: pd.DataFrame, signal_i: int, cfg: dict) -> BarrierOutcome | None:
    return label_candidate_trailing(
        enriched, signal_i, trail_mult=cfg["trail"], horizon=cfg["horizon"])


def _scaled_exit(enriched: pd.DataFrame, signal_i: int, cfg: dict) -> BarrierOutcome | None:
    return label_candidate_scaled(
        enriched,
        signal_i,
        tp1_mult=cfg.get("tp1", 2.5),
        trail_mult=cfg.get("trail", 3.0),
        sl_mult=cfg.get("sl", 2.0),
        horizon=cfg["horizon"],
    )


def _breakeven_exit(enriched: pd.DataFrame, signal_i: int, cfg: dict) -> BarrierOutcome | None:
    return label_candidate_breakeven(
        enriched,
        signal_i,
        tp_mult=cfg.get("tp", 5.0),
        sl_mult=cfg.get("sl", 2.0),
        be_trigger=cfg.get("be_trigger", 1.5),
        horizon=cfg["horizon"],
    )


def _ma_exit(enriched: pd.DataFrame, signal_i: int, cfg: dict) -> BarrierOutcome | None:
    return label_candidate_ma_exit(
        enriched,
        signal_i,
        ma_col=cfg.get("ma_col", "ema20"),
        horizon=cfg["horizon"],
    )


EXIT_PLUGINS: dict[str, ExitPlugin] = {
    "fixed": _fixed_exit,
    "trailing": _trailing_exit,
    "scaled": _scaled_exit,
    "breakeven": _breakeven_exit,
    "ma-exit": _ma_exit,
}


def _exit_name(cfg: dict) -> str:
    if "exit" in cfg:
        return str(cfg["exit"])
    if "trail" in cfg:
        return "trailing"
    return "fixed"


def label_with_config(enriched: pd.DataFrame, signal_i: int, cfg: dict) -> BarrierOutcome | None:
    exit_name = _exit_name(cfg)
    try:
        plugin = EXIT_PLUGINS[exit_name]
    except KeyError as exc:
        raise ValueError(f"unknown exit plugin {exit_name!r}") from exc
    return plugin(enriched, signal_i, cfg)


def build_all() -> dict[str, pd.DataFrame]:
    records: dict[str, list[dict]] = {name: [] for name in CONFIGS}
    for source, symbol, frame in iter_series(bar="15m", min_bars=MIN_BARS):
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=MAX_HORIZON, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        opens = enriched["open"].to_numpy()
        lows = enriched["low"].to_numpy()
        for row_pos, signal_i in enumerate(signal_indices):
            entry_i = signal_i + 1
            # maker fill: resting limit at entry-bar open fills only if the
            # bar trades strictly below it (adverse-selection-honest)
            maker_filled = bool(entry_i < len(lows) and lows[entry_i] < opens[entry_i])
            base = {
                "source": source, "symbol": symbol, "signal_i": signal_i,
                "signal_time": enriched["open_time"].iloc[signal_i],
                "maker_filled": maker_filled,
            }
            feats = feature_rows.iloc[row_pos].to_dict()
            for name, cfg in CONFIGS.items():
                outcome = label_with_config(enriched, signal_i, cfg)
                if outcome is None:
                    continue
                records[name].append({
                    **base, "label": outcome.label, "outcome": outcome.outcome,
                    "exit_offset": outcome.exit_offset, "entry_price": outcome.entry_price,
                    "realized_ret": outcome.realized_ret, **feats,
                })
    out = {}
    for name, rows in records.items():
        df = pd.DataFrame(rows)
        # same okx/gate cross-source dedupe as the official build_dataset path
        df = _dedupe_cross_source(df).sort_values("signal_time").reset_index(drop=True)
        out[name] = df
    return out


def eval_config(name: str, csv_path: Path, horizon: int) -> dict:
    train, val, _holdout = load_splits(csv_path, horizon_bars=horizon)  # holdout unused
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    top = m["top_decile"]

    # maker economics on the top decile: unfilled = missed trade (dropped)
    k = max(1, len(prob) // 10)
    top_idx = np.argsort(prob)[-k:]
    filled = val["maker_filled"].to_numpy()[top_idx]
    top_rets = rets[top_idx]
    maker = {
        "fill_rate": round(float(filled.mean()), 3),
        "gross_filled": round(float(top_rets[filled].mean()), 5) if filled.any() else None,
        "gross_missed": round(float(top_rets[~filled].mean()), 5) if (~filled).any() else None,
        "net_maker": round(float(top_rets[filled].mean() - MAKER_COST), 5) if filled.any() else None,
    }
    return {
        "config": name, "horizon": horizon,
        "n_train": int(len(train)), "n_val": int(len(val)),
        "val_auc": m["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": top["mean_realized_ret"],
        "top_net_taker03": round(top["mean_realized_ret"] - TAKER_COST, 5),
        "top_win_rate": top["win_rate"],
        "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
        "timeout_share": round(float((val["outcome"] == "timeout").mean()), 3),
        "maker": maker,
    }


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    print(f"building {len(CONFIGS)} label variants in one scan (max horizon {MAX_HORIZON})...", flush=True)
    datasets = build_all()
    results = []
    for name, df in datasets.items():
        path = SWEEP_DIR / f"judgment_v3_{name}.csv"
        df.to_csv(path, index=False)
        print(f"{name}: {len(df)} candidates, pos_rate {df['label'].mean():.3f} -> training", flush=True)
        results.append(eval_config(name, path, CONFIGS[name]["horizon"]))
    OUTPUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    flat = []
    for r in results:
        row = {k: v for k, v in r.items() if k != "maker"}
        row["maker_fill"] = r["maker"]["fill_rate"]
        row["net_maker"] = r["maker"]["net_maker"]
        flat.append(row)
    cols = ["config", "val_auc", "perm_p", "top_gross", "top_net_taker03",
            "net_maker", "maker_fill", "top_win_rate", "mean_exit_bars", "timeout_share"]
    print(pd.DataFrame(flat)[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
