"""Causal OHLCV research factors adapted from pinned open-source definitions.

Source expressions, upstream commits, licenses and adaptation details live in
``open_factors.json``. Qlib Alpha158 uses partial rolling windows; this adapter
requires complete windows and keeps undefined values as NaN. No model feature
contract or strategy is changed. Values describe the closed candle at index t;
callers must supply only closed bars of ONE instrument and ONE timeframe.

Inputs: open/high/low/close/volume and an ordered, unique, equally spaced
DatetimeIndex. Candle ratios use t only. Rolling statistics use [t-19, t];
differences/returns additionally read t-20. Never backfill, center, or shift
negative. pandas 2.3 rolling std uses ddof=1, as Qlib's Std operator does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "1.0.0"
WINDOW = 20
EPSILON = 1e-12
INPUTS = ("open", "high", "low", "close", "volume")


def _validate(bars: pd.DataFrame) -> pd.DataFrame:
    """Reject ambiguous ordering, gaps and invalid market data instead of filling."""
    if not bars.columns.is_unique or any(key not in bars for key in INPUTS):
        raise ValueError("unique open/high/low/close/volume columns are required")
    index = bars.index
    if not isinstance(index, pd.DatetimeIndex) or index.hasnans:
        raise ValueError("a DatetimeIndex without NaT is required")
    if not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("bars must be strictly increasing with no duplicates")
    if len(index) > 2 and len(set(np.diff(index.asi8))) != 1:
        raise ValueError("bars must have one fixed interval without gaps; split streams first")
    for key in ("symbol", "timeframe", "timeframe_min"):
        if key in bars and bars[key].nunique(dropna=False) > 1:
            raise ValueError("compute one instrument/timeframe stream at a time")
    frame = bars.loc[:, INPUTS].astype(float)
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("OHLCV must be finite; missing data may not be filled implicitly")
    if (frame[list(INPUTS[:4])] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError("prices must be positive and volume nonnegative")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("invalid OHLC geometry")
    return frame


def _rolling(series: pd.Series):
    return series.rolling(WINDOW, min_periods=WINDOW)


def _qlib_corr(left: pd.Series, right: pd.Series) -> pd.Series:
    """Alpha158 Corr: trailing 20 pairs, NaN for std close to zero (atol=2e-5)."""
    result = _rolling(left).corr(right)
    constant = (np.isclose(_rolling(left).std(ddof=1), 0, atol=2e-5)
                | np.isclose(_rolling(right).std(ddof=1), 0, atol=2e-5))
    return result.mask(constant)


def compute_factors(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute library v1 on closed OHLCV bars, using at most 21 trailing bars.

    Returned IDs match the workbench catalog. NaN means warmup or undefined,
    never zero evidence. Direction is raw market direction (no short-side flip).
    The index labels the supplied bar; availability is that bar's close, not
    its open timestamp. No outcomes, labels, full-series normalization or future
    candles are used. This is computation, not an economic validation.
    """
    data = _validate(bars)
    o, h, l, c, v = (data[key] for key in INPUTS)
    candle_range = h - l
    delta = c.diff()
    volume_delta = v.diff()
    ratio = c / c.shift(1)
    price_volume = (ratio - 1).abs() * v
    low20 = _rolling(l).min()
    high20 = _rolling(h).max()
    # A zero previous volume makes Qlib's volume ratio undefined. Preserve NaN.
    volume_change_log = np.log(v / v.shift(1).replace(0, np.nan) + 1)
    # QTPyLib CHOP, with a complete prior close (15 raw bars for 14 TRs).
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    tr = tr.mask(c.shift(1).isna())
    channel14 = h.rolling(14).max() - l.rolling(14).min()
    # ta CMF's zero-range multiplier is zero; a zero-volume window stays NaN.
    multiplier = ((c - l) - (h - c)) / candle_range.replace(0, np.nan)
    multiplier = multiplier.fillna(0.0)
    output = {
        "oss.qlib.kmid2": (c - o) / (candle_range + EPSILON),
        "oss.qlib.kup2": (h - pd.concat([o, c], axis=1).max(axis=1)) / (candle_range + EPSILON),
        "oss.qlib.klow2": (pd.concat([o, c], axis=1).min(axis=1) - l) / (candle_range + EPSILON),
        "oss.qlib.ksft2": (2 * c - h - l) / (candle_range + EPSILON),
        "oss.qlib.std20": _rolling(c).std(ddof=1) / c,
        "oss.qlib.rsv20": (c - low20) / (high20 - low20 + EPSILON),
        "oss.qlib.corr20": _qlib_corr(c, np.log(v + 1)),
        "oss.qlib.cord20": _qlib_corr(ratio, volume_change_log),
        "oss.qlib.sumd20": (_rolling(delta.clip(lower=0)).sum()
                            - _rolling((-delta).clip(lower=0)).sum())
                           / (_rolling(delta.abs()).sum() + EPSILON),
        # Upstream VSTD divides by CURRENT volume, not mean volume.
        "oss.qlib.vstd20": _rolling(v).std(ddof=1) / (v + EPSILON),
        "oss.qlib.wvma20": _rolling(price_volume).std(ddof=1)
                           / (_rolling(price_volume).mean() + EPSILON),
        "oss.qlib.vsumd20": (_rolling(volume_delta.clip(lower=0)).sum()
                             - _rolling((-volume_delta).clip(lower=0)).sum())
                            / (_rolling(volume_delta.abs()).sum() + EPSILON),
        "oss.qtpylib.chop14": 100 * np.log10(tr.rolling(14).sum() / channel14.replace(0, np.nan)) / np.log10(14),
        "oss.ta.cmf20": _rolling(multiplier * v).sum() / _rolling(v).sum().replace(0, np.nan),
    }
    return pd.DataFrame(output, index=bars.index).replace([np.inf, -np.inf], np.nan)


def main() -> None:
    """Export features from a local, closed-bar CSV; never fetch or place orders."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--time-column", default="timestamp")
    parser.add_argument("--time-unit", choices=("s", "ms", "us", "ns"))
    parser.add_argument("--receipt", type=Path, help="Optional small provenance/coverage JSON, exclusive creation")
    args = parser.parse_args()
    if args.receipt and args.receipt.resolve() in {args.input.resolve(), args.output.resolve()}:
        parser.error("receipt must have a distinct path")
    if args.output.exists() or (args.receipt and args.receipt.exists()):
        parser.error("output/receipt already exists; use new paths to preserve prior results")
    if not args.output.parent.is_dir() or (args.receipt and not args.receipt.parent.is_dir()):
        parser.error("output and receipt parent directories must already exist")
    frame = pd.read_csv(args.input)
    if args.time_column not in frame:
        parser.error("input must contain --time-column")
    times = frame.pop(args.time_column)
    if pd.api.types.is_numeric_dtype(times) and args.time_unit is None:
        parser.error("numeric timestamps require explicit --time-unit")
    frame.index = pd.DatetimeIndex(pd.to_datetime(times, unit=args.time_unit, utc=True))
    frame.index.name = args.time_column
    features = compute_factors(frame)
    # Exclusive creation prevents accidental replacement of a prior result.
    with args.output.open("x", encoding="utf-8") as stream:
        features.to_csv(stream)
    if args.receipt:
        root = Path(__file__).resolve().parents[2]
        implementation = Path(__file__)
        metadata = implementation.with_suffix(".json")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", str(implementation), str(metadata)], cwd=root, text=True)
        receipt = {
            "generated_at": datetime.now(timezone.utc).isoformat(), "implementation_version": VERSION,
            "source_commit": commit, "implementation_dirty": bool(dirty.strip()),
            "implementation_sha256": digest(implementation), "metadata_sha256": digest(metadata),
            "input": str(args.input), "input_sha256": digest(args.input),
            "output": str(args.output), "output_sha256": digest(args.output),
            "time_column": args.time_column, "time_unit": args.time_unit, "rows": len(features),
            "first_bar": str(frame.index.min()), "last_bar": str(frame.index.max()),
            "coverage": [{"factor_id": key, "valid": int(features[key].notna().sum()),
                          "missing": int(features[key].isna().sum())} for key in features],
            "validation": "calculation only; no outcomes, profitability or production validation",
            "training_eligible": False, "production_eligible": False,
        }
        with args.receipt.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    print(f"Computed {len(features)} bars × {len(features.columns)} factors; missing values remain blank.")


if __name__ == "__main__":
    main()
