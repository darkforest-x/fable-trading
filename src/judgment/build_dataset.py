"""Build the judgment-layer dataset: candidates + triple-barrier labels + features.

Usage: python3 -m src.judgment.build_dataset [--mode strict|expanded] [--out PATH]
Output: data/judgment_dataset.csv by default (data/ is gitignored) and a JSON
summary on stdout. Barrier structure comes from src.judgment.labeling defaults
(v2: TP 4xATR / SL 2xATR, atr_pct >= 0.0015).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import HORIZON_BARS, label_candidate

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_DIR / "data" / "judgment_dataset.csv"
MIN_BARS = 500


def build(mode: str = "strict") -> pd.DataFrame:
    records: list[dict] = []
    series_count = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=MIN_BARS):
        series_count += 1
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=HORIZON_BARS, mode=mode)
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        for row_pos, signal_i in enumerate(signal_indices):
            outcome = label_candidate(enriched, signal_i)
            if outcome is None:
                continue
            record = {
                "source": source,
                "symbol": symbol,
                "signal_i": signal_i,
                "signal_time": enriched["open_time"].iloc[signal_i],
                "label": outcome.label,
                "outcome": outcome.outcome,
                "exit_offset": outcome.exit_offset,
                "entry_price": outcome.entry_price,
                "realized_ret": outcome.realized_ret,
            }
            record.update(feature_rows.iloc[row_pos].to_dict())
            records.append(record)
    dataset = pd.DataFrame(records)
    if not dataset.empty:
        dataset = _dedupe_cross_source(dataset)
        dataset = dataset.sort_values("signal_time").reset_index(drop=True)
    dataset.attrs["series_count"] = series_count
    return dataset


def _dedupe_cross_source(dataset: pd.DataFrame, *, min_gap_hours: float = 4.5) -> pd.DataFrame:
    """Drop duplicate events listed on both okx and gate for the same symbol.

    Exact same (symbol, signal_time) keeps the okx row; additionally a gate
    candidate within `min_gap_hours` of an okx candidate on the same symbol is
    treated as the same market event and dropped (mirrors the per-series
    18-bar min gap).
    """
    dataset = dataset.sort_values(["symbol", "signal_time"])
    keep = pd.Series(True, index=dataset.index)
    for symbol, group in dataset.groupby("symbol"):
        okx_times = group.loc[group["source"] == "okx", "signal_time"]
        if okx_times.empty:
            continue
        gate_rows = group[group["source"] == "gate"]
        for idx, ts in gate_rows["signal_time"].items():
            nearest = (okx_times - ts).abs().min()
            if nearest <= pd.Timedelta(hours=min_gap_hours):
                keep.loc[idx] = False
    return dataset[keep]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("strict", "expanded"), default="strict")
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    dataset = build(mode=args.mode)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(args.out, index=False)
    n = len(dataset)
    summary = {
        "pool_mode": args.mode,
        "series_scanned": dataset.attrs.get("series_count"),
        "candidates": n,
        "symbols_with_candidates": int(dataset["symbol"].nunique()) if n else 0,
        "time_range": [str(dataset["signal_time"].min()), str(dataset["signal_time"].max())] if n else None,
        "label_counts": dataset["label"].value_counts().to_dict() if n else {},
        "outcome_counts": dataset["outcome"].value_counts().to_dict() if n else {},
        "positive_rate": round(float(dataset["label"].mean()), 4) if n else None,
        "mean_realized_ret": round(float(dataset["realized_ret"].mean()), 5) if n else None,
        "feature_count": len(FEATURE_COLUMNS),
        "output": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
