#!/usr/bin/env python3
"""Diagnose the development-only ETH 3m v10 prebox pack.

Inputs are restricted to bars before 2026-05-04 before indicator calculation.
The script measures whether fires occur before or after the short breakdown,
their three-hour outcome (kept separate from shape validity), confidence
calibration proxies, temporal repetition, and the 15m-to-3m clock mismatch.

This is a read-only diagnostic: it does not evaluate holdout, train, promote,
change thresholds, or import Label Studio tasks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import ALL_MA_COLS, add_mas, load_ohlcv_csv
from src.judgment.candidates import add_indicators
from src.judgment.features import add_features

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
MANIFEST = PROJECT / "datasets/eth_3m_v10_prebox200/manifest.csv"
CHECKPOINT = PROJECT / "datasets/eth_3m_v10_prebox200/scan_checkpoint.json"
TRAIN_LABELS = PROJECT / "datasets/dense_owner_short_star_tip_v10/labels"
NATIVE_SWEEP = PROJECT / "analysis/output/diag_precision_vs_recall_v10.json"
OUTPUT = PROJECT / "analysis/output/eth3m_v10_precision_diagnosis.json"


def cluster_count(times: pd.Series, gap_minutes: int) -> int:
    """Count chronological events, starting a new event after the given gap."""
    ordered = times.sort_values().reset_index(drop=True)
    if ordered.empty:
        return 0
    gaps = ordered.diff().dt.total_seconds().div(60)
    return 1 + int((gaps.iloc[1:] > gap_minutes).sum())


def training_box_widths() -> np.ndarray:
    """Return normalized positive-box widths from the v10 15m dataset."""
    values: list[float] = []
    for split in ("train", "val"):
        for path in sorted((TRAIN_LABELS / split).glob("*.txt")):
            for line in path.read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) >= 5:
                    values.append(float(fields[3]))
    return np.asarray(values, dtype=float)


def main() -> int:
    frame = load_ohlcv_csv(INPUT)
    frame = frame.loc[frame["open_time"] < HOLDOUT_START].reset_index(drop=True)
    ma_frame = add_mas(frame)
    features = add_features(add_indicators(frame))

    manifest = pd.read_csv(MANIFEST)
    manifest["candidate_time"] = pd.to_datetime(manifest["candidate_time"], utc=True)
    manifest["future_end"] = pd.to_datetime(manifest["future_end"], utc=True)
    if manifest["future_end"].max() >= HOLDOUT_START:
        raise RuntimeError("development diagnostic would cross holdout boundary")

    bar_by_time = pd.Series(np.arange(len(frame)), index=frame["open_time"])
    bars = bar_by_time.reindex(manifest["candidate_time"])
    if bars.isna().any():
        raise RuntimeError("candidate timestamp missing from source kline")
    bars_np = bars.astype(int).to_numpy()

    close = features["close"].to_numpy(dtype=float)
    atr_pct = features["atr_pct"].to_numpy(dtype=float)
    ret8_atr = np.asarray(
        [(close[i] / close[i - 8] - 1.0) / atr_pct[i] for i in bars_np],
        dtype=float,
    )
    below_all_mas = np.asarray(
        [close[i] < min(float(ma_frame.loc[i, col]) for col in ALL_MA_COLS) for i in bars_np],
        dtype=bool,
    )

    conf_bins = pd.cut(
        manifest["v10_conf"],
        bins=[0.30, 0.50, 0.70, np.inf],
        labels=["0.30-0.50", "0.50-0.70", ">=0.70"],
        include_lowest=True,
        right=False,
    )
    confidence_rows = []
    for label in conf_bins.cat.categories:
        mask = conf_bins == label
        confidence_rows.append(
            {
                "bin": str(label),
                "n": int(mask.sum()),
                "future_3h_down_rate": float(
                    (manifest.loc[mask, "outcome_return_3h"] < 0).mean()
                ),
            }
        )

    fine_conf_bins = pd.cut(
        manifest["v10_conf"],
        bins=[0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90],
        labels=[
            "0.30-0.40",
            "0.40-0.50",
            "0.50-0.60",
            "0.60-0.70",
            "0.70-0.80",
            "0.80-0.90",
        ],
        include_lowest=True,
        right=False,
    )
    fine_confidence_rows = []
    for label in fine_conf_bins.cat.categories:
        mask = fine_conf_bins == label
        fine_confidence_rows.append(
            {
                "bin": str(label),
                "n": int(mask.sum()),
                "future_3h_down_rate": float(
                    (manifest.loc[mask, "outcome_return_3h"] < 0).mean()
                ),
            }
        )

    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    train_widths = training_box_widths()
    native = json.loads(NATIVE_SWEEP.read_text(encoding="utf-8"))
    native_conf30 = next(row for row in native["sweep"] if row["conf"] == 0.3)
    # The original artifact divided total fires by one symbol's average month.
    # Recompute from the full 12 x 2,000-bar exposure using 30.44 days/month.
    total_native_bars = 12 * 2000
    bars_per_symbol_month_15m = 4 * 24 * 30.44
    native_symbol_months = total_native_bars / bars_per_symbol_month_15m
    native_density_conf30 = native_conf30["n_fire"] / native_symbol_months

    result = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "holdout_consumed": False,
        "population": {
            "n_tasks": int(len(manifest)),
            "candidate_min": manifest["candidate_time"].min().isoformat(),
            "future_end_max": manifest["future_end"].max().isoformat(),
            "anchors_scanned": int(checkpoint["next_pos"]),
            "raw_fire_rate_per_anchor": float(len(manifest) / checkpoint["next_pos"]),
            "bars_per_raw_fire": float(checkpoint["next_pos"] / len(manifest)),
        },
        "breakdown_state_at_fire": {
            "ret8_negative_n": int((ret8_atr < 0).sum()),
            "ret8_below_minus_1atr_n": int((ret8_atr < -1).sum()),
            "ret8_below_minus_2atr_n": int((ret8_atr < -2).sum()),
            "ret8_atr_median": float(np.median(ret8_atr)),
            "below_all_six_mas_n": int(below_all_mas.sum()),
        },
        "future_outcome_not_shape_precision": {
            "future_3h_down_n": int((manifest["outcome_return_3h"] < 0).sum()),
            "future_3h_down_rate": float((manifest["outcome_return_3h"] < 0).mean()),
            "future_3h_return_median": float(manifest["outcome_return_3h"].median()),
            "conf_vs_future_3h_return_spearman": float(
                manifest["v10_conf"].corr(manifest["outcome_return_3h"], method="spearman")
            ),
            "confidence_bins": confidence_rows,
            "confidence_fine_bins": fine_confidence_rows,
        },
        "temporal_repetition": {
            "events_gap_gt_3m": cluster_count(manifest["candidate_time"], 3),
            "events_gap_gt_30m": cluster_count(manifest["candidate_time"], 30),
            "events_gap_gt_60m": cluster_count(manifest["candidate_time"], 60),
            "events_gap_gt_180m": cluster_count(manifest["candidate_time"], 180),
        },
        "clock_mismatch": {
            "context_hours_training_15m": 200 * 15 / 60,
            "context_hours_current_3m": 200 * 3 / 60,
            "clock_compression_factor": 5.0,
            "train_box_width_norm_median": float(np.median(train_widths)),
            "train_box_width_bars_median": float(np.median(train_widths) * 199),
            "pred_box_width_norm_median": float(manifest["box_w"].median()),
            "pred_box_width_bars_median": float(manifest["box_w"].median() * 199),
            "pred_box_duration_minutes_on_3m": float(manifest["box_w"].median() * 199 * 3),
            "same_bar_width_duration_minutes_on_15m": float(
                manifest["box_w"].median() * 199 * 15
            ),
        },
        "native_15m_open_scan": {
            "conf": 0.3,
            "n_fire": int(native_conf30["n_fire"]),
            "total_bars": total_native_bars,
            "symbol_months_corrected": native_symbol_months,
            "fires_per_symbol_month_corrected": native_density_conf30,
            "owner_density_range": native["owner_density"],
            "note": "Corrected denominator; original density field is stale and 12x too high.",
        },
        "caveats": [
            "Owner's roughly 60% invalid estimate has not been recorded per task.",
            "Three-hour direction is an outcome label, not a detector-shape validity label.",
            "Confidence calibration against shape validity requires owner keep/drop labels.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
