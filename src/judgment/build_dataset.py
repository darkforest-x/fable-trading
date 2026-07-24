"""Build the judgment-layer dataset: candidates + triple-barrier labels + features.

Usage:
  python3 -m src.judgment.build_dataset [--mode strict|expanded] [--side long|short]
      [--bar 15m] [--out PATH]

Output: data/judgment_dataset.csv by default (long); short-only defaults to
data/judgment_dataset_v2_{mode}_short.csv so other pools are never overwritten.
Barrier structure comes from src.judgment.labeling defaults
(v2: TP 4xATR / SL 2xATR, atr_pct >= 0.0015). Do not change TP/SL/cost/
threshold presets here without owner approval.

Side path (2026-07-24 owner short-only pipeline):
  --side short → scan_short_candidates + label_short_candidate (rule pool only;
  YOLO short pool is scripts/yolo_candidate_source.py --side short after
  owner_side_short_v1 weights exist). Holdout is never touched by this module.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.data.bars import BAR_CHOICES, normalize_bar
from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates, scan_short_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import HORIZON_BARS, label_candidate, label_short_candidate

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_DIR / "data" / "judgment_dataset.csv"
MIN_BARS = 500


def default_out_path(mode: str, side: str) -> Path:
    """Pool-tagged defaults; short never shares the long v2 filenames."""
    if side == "short":
        return PROJECT_DIR / "data" / f"judgment_dataset_v2_{mode}_short.csv"
    return OUTPUT_PATH


def build(
    mode: str = "strict",
    *,
    bar: str = "15m",
    horizon_bars: int = HORIZON_BARS,
    side: str = "long",
) -> pd.DataFrame:
    if side not in ("long", "short"):
        raise ValueError(f"side must be long|short, got {side!r}")
    bar = normalize_bar(bar)
    records: list[dict] = []
    series_count = 0
    scan = scan_short_candidates if side == "short" else scan_candidates
    label_fn = label_short_candidate if side == "short" else label_candidate
    for source, symbol, frame in iter_series(bar=bar, min_bars=MIN_BARS):
        series_count += 1
        enriched = add_indicators(frame)
        signal_indices = scan(enriched, horizon_bars=horizon_bars, mode=mode)
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        for row_pos, signal_i in enumerate(signal_indices):
            outcome = label_fn(enriched, signal_i, horizon=horizon_bars)
            if outcome is None:
                continue
            record = {
                "source": source,
                "symbol": symbol,
                "bar": bar,
                "side": side,
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
    dataset.attrs["bar"] = bar
    dataset.attrs["horizon_bars"] = horizon_bars
    dataset.attrs["side"] = side
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
    parser.add_argument(
        "--side",
        choices=("long", "short"),
        default="long",
        help="long = historical dense-MA long pool; short = short-only rule pool.",
    )
    parser.add_argument("--bar", choices=BAR_CHOICES, default="15m")
    parser.add_argument("--horizon-bars", type=int, default=HORIZON_BARS)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="CSV path. Default: judgment_dataset.csv (long) or "
        "judgment_dataset_v2_{mode}_short.csv (short).",
    )
    args = parser.parse_args()
    out = args.out if args.out is not None else default_out_path(args.mode, args.side)
    dataset = build(
        mode=args.mode, bar=args.bar, horizon_bars=args.horizon_bars, side=args.side
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(out, index=False)
    n = len(dataset)
    summary = {
        "pool_mode": args.mode,
        "side": args.side,
        "bar": dataset.attrs.get("bar"),
        "horizon_bars": dataset.attrs.get("horizon_bars"),
        "series_scanned": dataset.attrs.get("series_count"),
        "candidates": n,
        "symbols_with_candidates": int(dataset["symbol"].nunique()) if n else 0,
        "time_range": [str(dataset["signal_time"].min()), str(dataset["signal_time"].max())] if n else None,
        "label_counts": dataset["label"].value_counts().to_dict() if n else {},
        "outcome_counts": dataset["outcome"].value_counts().to_dict() if n else {},
        "positive_rate": round(float(dataset["label"].mean()), 4) if n else None,
        "mean_realized_ret": round(float(dataset["realized_ret"].mean()), 5) if n else None,
        "feature_count": len(FEATURE_COLUMNS),
        "output": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
