"""Remap directional FEATURE_COLUMNS on an existing short YOLO judgment CSV.

Single-variable helper for short-only judgment experiments: keep the same
candidates / short barrier labels / realized_ret, recompute features from
klines with align_short_feature_rows (ext_up←ext_down, …). Does not rescan
YOLO and does not touch holdout / TP/SL / costs.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/remap_yolo_short_features.py \
    --in data/judgment_yolo_owner_side_short_5_6m.csv \
    --out data/judgment_yolo_owner_side_short_5_6m_feat_mirror.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows_for_side


def remap(dataset: pd.DataFrame, *, bar: str = "15m") -> pd.DataFrame:
    if "side" in dataset.columns:
        sides = set(dataset["side"].astype(str).str.lower().unique())
        if sides != {"short"}:
            raise SystemExit(f"refuse non-short-only pool: {sorted(sides)}")
    need = {"source", "symbol", "signal_i"}
    missing = need - set(dataset.columns)
    if missing:
        raise SystemExit(f"dataset missing columns: {sorted(missing)}")

    series_map = {(s, sym): frame for s, sym, frame in iter_series(bar=bar, min_bars=1)}
    feat_by_key: dict[tuple[str, str, int], pd.Series] = {}
    for (source, symbol), group in dataset.groupby(["source", "symbol"], sort=False):
        frame = series_map.get((source, symbol))
        if frame is None or frame.empty:
            raise SystemExit(f"missing klines for {source}/{symbol}")
        enriched = add_indicators(frame)
        featured = add_features(enriched)
        indices = [int(i) for i in group["signal_i"].tolist()]
        for i in indices:
            if i < 0 or i >= len(featured):
                raise SystemExit(
                    f"signal_i {i} out of range for {source}/{symbol} (n={len(featured)})"
                )
        feat_rows = extract_feature_rows_for_side(featured, indices, "short")
        for pos, signal_i in enumerate(indices):
            feat_by_key[(source, symbol, signal_i)] = feat_rows.iloc[pos]

    out = dataset.copy()
    for idx, row in out.iterrows():
        key = (row["source"], row["symbol"], int(row["signal_i"]))
        feats = feat_by_key[key]
        for col in FEATURE_COLUMNS:
            out.at[idx, col] = feats[col]
    return out.reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bar", default="15m")
    args = parser.parse_args()
    raw = pd.read_csv(args.inp, parse_dates=["signal_time"])
    remapped = remap(raw, bar=args.bar)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    remapped.to_csv(args.out, index=False)
    changed = {
        c: float((remapped[c] - raw[c]).abs().mean())
        for c in ("ext_up", "order_score", "drawdown24", "ret_4", "slow_slope_12")
        if c in raw.columns
    }
    summary = {
        "in": str(args.inp),
        "out": str(args.out),
        "n": len(remapped),
        "side": "short",
        "mean_abs_delta_directional": changed,
        "labels_unchanged": bool((remapped["label"].to_numpy() == raw["label"].to_numpy()).all()),
        "realized_ret_unchanged": bool(
            (remapped["realized_ret"].to_numpy() == raw["realized_ret"].to_numpy()).all()
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
