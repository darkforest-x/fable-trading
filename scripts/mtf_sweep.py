"""R4 multi-timeframe SWAP sweep for H7/H8.

Discovery-tier only: scan expanded dense-MA candidates on 5m/30m/1H bars,
label with fixed TP5/SL2 exits, train each pool independently, and report
train/val metrics without evaluating holdout.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
SWEEP_DIR = PROJECT_DIR / "data" / "mtf_sweep"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "mtf_sweep.json"
SWAP_MAKER_COST = 0.0006
SWAP_TAKER_COST = 0.0010
BASELINE_15M_VAL_N = 1510
MAJOR_5M_SYMBOLS = frozenset(
    {
        "BTC_USDT_SWAP",
        "ETH_USDT_SWAP",
        "SOL_USDT_SWAP",
        "BNB_USDT_SWAP",
        "XRP_USDT_SWAP",
        "DOGE_USDT_SWAP",
        "ADA_USDT_SWAP",
        "LINK_USDT_SWAP",
        "AVAX_USDT_SWAP",
        "TRX_USDT_SWAP",
        "LTC_USDT_SWAP",
        "DOT_USDT_SWAP",
        "TON_USDT_SWAP",
        "ARB_USDT_SWAP",
        "OP_USDT_SWAP",
    }
)


@dataclass(frozen=True)
class MtfConfig:
    name: str
    hypothesis: str
    bar: str
    horizon_bars: int


CONFIGS = (
    MtfConfig("h7_5m_h96", "H7", "5m", 96),
    MtfConfig("h7_5m_h144", "H7", "5m", 144),
    MtfConfig("h7_5m_h216", "H7", "5m", 216),
    MtfConfig("h8_30m_h24", "H8", "30m", 24),
    MtfConfig("h8_30m_h48", "H8", "30m", 48),
    MtfConfig("h8_30m_h72", "H8", "30m", 72),
    MtfConfig("h8_1h_h24", "H8", "1H", 24),
    MtfConfig("h8_1h_h48", "H8", "1H", 48),
    MtfConfig("h8_1h_h72", "H8", "1H", 72),
)


def include_symbol(symbol: str, bar: str) -> bool:
    if not symbol.endswith("_USDT_SWAP"):
        return False
    if bar == "5m":
        return symbol in MAJOR_5M_SYMBOLS
    return bar in {"30m", "1H"}


def _configs_by_bar() -> dict[str, list[MtfConfig]]:
    grouped: dict[str, list[MtfConfig]] = {}
    for cfg in CONFIGS:
        grouped.setdefault(cfg.bar, []).append(cfg)
    return grouped


def _scan_bar(bar: str, configs: list[MtfConfig]) -> tuple[dict[str, list[dict]], dict]:
    max_horizon = max(cfg.horizon_bars for cfg in configs)
    records: dict[str, list[dict]] = {cfg.name: [] for cfg in configs}
    series_scanned = 0
    series_with_candidates = 0
    included_symbols: set[str] = set()
    for source, symbol, frame in iter_series(bar=bar, min_bars=500):
        if not include_symbol(symbol, bar):
            continue
        series_scanned += 1
        included_symbols.add(symbol)
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=max_horizon, mode="expanded")
        if not signal_indices:
            continue
        series_with_candidates += 1
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        opens = enriched["open"].to_numpy()
        lows = enriched["low"].to_numpy()
        for row_pos, signal_i in enumerate(signal_indices):
            entry_i = signal_i + 1
            maker_filled = bool(entry_i < len(lows) and lows[entry_i] < opens[entry_i])
            feats = feature_rows.iloc[row_pos].to_dict()
            for cfg in configs:
                outcome = label_candidate(enriched, signal_i, tp_mult=5.0, sl_mult=2.0, horizon=cfg.horizon_bars)
                if outcome is None:
                    continue
                records[cfg.name].append({
                    "source": source,
                    "symbol": symbol,
                    "bar": bar,
                    "horizon_bars": cfg.horizon_bars,
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
    coverage = {
        "bar": bar,
        "max_horizon_bars": max_horizon,
        "series_scanned": series_scanned,
        "series_with_candidates": series_with_candidates,
        "symbols": sorted(included_symbols),
    }
    if bar == "5m":
        coverage["missing_major_symbols"] = sorted(MAJOR_5M_SYMBOLS - included_symbols)
    return records, coverage


def _empty_result(cfg: MtfConfig, path: Path, n: int) -> dict:
    return {
        "config": cfg.name,
        "hypothesis": cfg.hypothesis,
        "bar": cfg.bar,
        "horizon_bars": cfg.horizon_bars,
        "dataset": str(path),
        "n_candidates": int(n),
        "status": "skipped_insufficient_classes",
    }


def _eval_config(cfg: MtfConfig, rows: list[dict]) -> dict:
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    path = SWEEP_DIR / f"{cfg.name}.csv"
    df.to_csv(path, index=False)
    train, val, _ = load_splits(path, horizon_bars=cfg.horizon_bars, bar=cfg.bar)
    if train["label"].nunique() < 2 or val["label"].nunique() < 2:
        return _empty_result(cfg, path, len(df))
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    metrics = evaluate(y, prob, rets)
    k = max(1, len(prob) // 10)
    top_idx = np.argsort(prob)[-k:]
    filled = val["maker_filled"].to_numpy()[top_idx]
    top_rets = rets[top_idx]
    top_gross = metrics["top_decile"]["mean_realized_ret"]
    return {
        "config": cfg.name,
        "hypothesis": cfg.hypothesis,
        "bar": cfg.bar,
        "horizon_bars": cfg.horizon_bars,
        "dataset": str(path),
        "n_candidates": int(len(df)),
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "val_range": [str(val["signal_time"].min()), str(val["signal_time"].max())],
        "positive_rate_val": round(float(val["label"].mean()), 4),
        "val_auc": metrics["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": top_gross,
        "top_net_taker_010": round(top_gross - SWAP_TAKER_COST, 5),
        "top_net_maker_006": round(top_gross - SWAP_MAKER_COST, 5),
        "top_win_rate": metrics["top_decile"]["win_rate"],
        "top_maker_fill_rate": round(float(filled.mean()), 3),
        "top_net_maker_filled_only": round(float(top_rets[filled].mean()) - SWAP_MAKER_COST, 5)
        if filled.any()
        else None,
        "n_val_vs_15m_baseline": round(float(len(val) / BASELINE_15M_VAL_N), 3),
        "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
        "outcomes": val["outcome"].value_counts().to_dict(),
    }


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    all_records: dict[str, list[dict]] = {}
    coverage = []
    by_bar = _configs_by_bar()
    for bar, configs in by_bar.items():
        records, bar_coverage = _scan_bar(bar, configs)
        all_records.update(records)
        coverage.append(bar_coverage)
        print(f"{bar}: scanned {bar_coverage['series_scanned']} series")
    cfg_by_name = {cfg.name: cfg for cfg in CONFIGS}
    results = [_eval_config(cfg_by_name[name], rows) for name, rows in all_records.items()]
    payload = {
        "costs": {"maker": SWAP_MAKER_COST, "taker": SWAP_TAKER_COST},
        "baseline_15m_val_n": BASELINE_15M_VAL_N,
        "coverage": coverage,
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(pd.DataFrame(results).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
