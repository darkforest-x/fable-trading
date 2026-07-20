"""Route-C analysis: score v12 val-window candidates with the frozen v11 model
and compare score distributions against the v11 baseline pool (same model).

Inputs
- data/judgment_yolo_swap_v12_valwin.csv  (scripts/scan_v12_valwin.py output)
- data/judgment_yolo_swap_v11.csv         (frozen v11 pool, read-only)
- models/frozen_tp5_sl2_swap_yolo_v11_reg_20260718 via latest_artifact(default_config())

Both sides are restricted to the common comparison window: the v12 rescan's
effective candidate span (warmup 288 bars after the tail-3200 start, horizon
72 bars before the 2026-05-04 cutoff). All timestamps < HOLDOUT_START — no
holdout is read anywhere.

Outputs analysis/output/p_v12_score_shift.json with candidate counts, score
quantiles, KS distance, threshold pass rates, and top-decile gross/net
(0.2% round-trip) return + win-rate tables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.judgment.frozen import default_config, latest_artifact
from src.judgment.train import HOLDOUT_START

ROUND_TRIP_COST = 0.002
V12_PATH = PROJECT_DIR / "data" / "judgment_yolo_swap_v12_valwin.csv"
OUT_PATH = PROJECT_DIR / "analysis" / "output" / "p_v12_score_shift.json"


def side_stats(df: pd.DataFrame, threshold: float, window_days: float) -> dict:
    scores = df["score"].to_numpy()
    top = df[df["score"] >= np.quantile(scores, 0.9)]
    passed = df[df["score"] >= threshold]

    def ret_block(sub: pd.DataFrame) -> dict:
        if not len(sub):
            return {"n": 0}
        gross = float(sub["realized_ret"].mean())
        return {
            "n": int(len(sub)),
            "gross_mean": round(gross, 5),
            "net_mean": round(gross - ROUND_TRIP_COST, 5),
            "win_rate": round(float((sub["realized_ret"] > 0).mean()), 4),
            "net_win_rate": round(float((sub["realized_ret"] > ROUND_TRIP_COST).mean()), 4),
        }

    return {
        "n_candidates": int(len(df)),
        "n_symbols": int(df["symbol"].nunique()),
        "candidates_per_day": round(len(df) / window_days, 2),
        "pos_rate": round(float(df["label"].mean()), 4),
        "score_quantiles": {
            f"p{int(q * 100)}": round(float(np.quantile(scores, q)), 5)
            for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.99)
        },
        "score_mean": round(float(scores.mean()), 5),
        "pass_threshold": {
            "threshold": threshold,
            "n_passed": int(len(passed)),
            "pass_rate": round(float(len(passed) / len(df)), 4),
            "passed_returns": ret_block(passed),
        },
        "top_decile": ret_block(top),
        "all_returns": ret_block(df),
    }


def main() -> int:
    artifact = latest_artifact(default_config())
    assert artifact is not None, "v11 frozen artifact missing"
    model = lgb.Booster(model_file=str(artifact.model_path))
    threshold = artifact.threshold
    feats = list(artifact.feature_columns)

    v12 = pd.read_csv(V12_PATH, parse_dates=["signal_time"])
    v11 = pd.read_csv(artifact.dataset_path, parse_dates=["signal_time"])
    assert v12["signal_time"].max() < HOLDOUT_START
    # The v11 pool CSV physically contains holdout-period rows (split happens
    # in load_splits). Drop them by timestamp BEFORE any stats — never scored.
    v11 = v11[v11["signal_time"] < HOLDOUT_START]

    # Common window = v12 rescan's effective span (same bars offered to both
    # detectors; v11 pool covers it as a sub-range of its val split).
    win_lo, win_hi = v12["signal_time"].min(), v12["signal_time"].max()
    window_days = (win_hi - win_lo).total_seconds() / 86400
    v11w = v11[(v11["signal_time"] >= win_lo) & (v11["signal_time"] <= win_hi)].copy()
    v12w = v12.copy()

    for df in (v11w, v12w):
        df["score"] = model.predict(df[feats], num_iteration=artifact.best_iteration)

    ks = ks_2samp(v11w["score"], v12w["score"])
    out = {
        "model": artifact.relative_model_path,
        "threshold_val_q90": threshold,
        "round_trip_cost": ROUND_TRIP_COST,
        "common_window": [str(win_lo), str(win_hi)],
        "window_days": round(window_days, 2),
        "holdout_touched": False,
        "v11_baseline": side_stats(v11w, threshold, window_days),
        "v12_rescan": side_stats(v12w, threshold, window_days),
        "ks": {"statistic": round(float(ks.statistic), 4), "pvalue": float(ks.pvalue)},
        "v11_full_val_pass_rate_note": (
            "v11 pass rate is ~10% by construction only over the FULL val split; "
            "within this sub-window it may differ."
        ),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
