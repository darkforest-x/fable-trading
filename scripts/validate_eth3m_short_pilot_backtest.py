#!/usr/bin/env python3
"""Independent integrity checks for the ETH 3m pilot causal replay outputs.

This validator intentionally does not import the backtest implementation.  It
rebuilds the high-impact counts, time guards, 3h short returns, dedup gaps, and
matched-control cells directly from the saved CSVs and source OHLC.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/eth3m_short_pilot_v1_backtest"
DATA = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
MANIFEST = PROJECT / "datasets/eth_3m_short_pilot_v1/manifest.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
WINDOW = 200
FUTURE = 60
COST = 0.002
MIN_GAP = 18


def close_enough(left: float, right: float, tol: float = 1e-12) -> bool:
    return bool(np.isfinite(left) and np.isfinite(right) and abs(left - right) <= tol)


def main() -> int:
    eligible = pd.read_csv(OUT / "eligible.csv")
    scan = pd.read_csv(OUT / "scan_rows.csv")
    signals = pd.read_csv(OUT / "signals.csv")
    controls = pd.read_csv(OUT / "matched_controls.csv")
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    frame = pd.read_csv(DATA)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame[frame["open_time"] < HOLDOUT].sort_values("open_time").reset_index(drop=True)
    manifest = pd.read_csv(MANIFEST)
    manifest["causal_start_time"] = pd.to_datetime(manifest["causal_start_time"], utc=True)
    manifest["anchor_time"] = pd.to_datetime(manifest["anchor_time"], utc=True)

    checks: dict[str, bool] = {}
    checks["holdout_physically_absent"] = bool(frame["open_time"].max() < HOLDOUT)
    checks["manifest_pre_holdout"] = bool(manifest["anchor_time"].max() < HOLDOUT)
    checks["eligible_expected_counts"] = bool(
        len(eligible) == 5398 and int(eligible["strict_oos"].sum()) == 774
    )
    checks["scan_one_to_one"] = bool(
        len(scan) == len(eligible)
        and scan["bar_i"].nunique() == len(scan)
        and set(scan["bar_i"].astype(int)) == set(eligible["bar_i"].astype(int))
    )
    checks["scan_no_errors"] = bool(scan["scan_error"].fillna("").astype(str).eq("").all())
    checks["mapped_boxes_are_decision_tip"] = bool(
        (scan.loc[scan["raw_fire"].astype(bool), "mapped_box_bar_i"]
         == scan.loc[scan["raw_fire"].astype(bool), "bar_i"]).all()
    )

    used = np.zeros(len(frame), dtype=bool)
    times = frame["open_time"]
    for row in manifest.itertuples(index=False):
        lo = int(times.searchsorted(row.causal_start_time, side="left"))
        hi = int(times.searchsorted(row.anchor_time, side="right"))
        used[lo:hi] = True
    overlap_counts = []
    for bar_i in eligible["bar_i"].astype(int):
        overlap_counts.append(int(used[bar_i - WINDOW + 1 : bar_i + 1].sum()))
    checks["zero_training_pixel_overlap"] = bool(max(overlap_counts, default=-1) == 0)
    checks["strict_after_last_anchor"] = bool(
        pd.to_datetime(
            eligible.loc[eligible["strict_oos"].astype(bool), "signal_time"], utc=True
        ).min()
        > manifest["anchor_time"].max()
    )

    joined = eligible[["bar_i", "strict_oos"]].merge(
        scan[["bar_i", "raw_fire"]], on="bar_i", validate="one_to_one"
    )
    raw_gap = int(joined["raw_fire"].astype(bool).sum())
    raw_strict = int(joined.loc[joined["strict_oos"].astype(bool), "raw_fire"].astype(bool).sum())
    checks["raw_counts"] = bool(raw_gap == 5071 and raw_strict == 772)

    checks["signal_unique"] = bool(
        not signals.duplicated(["scope", "bar_i"]).any()
        and signals.groupby("scope").size().to_dict()
        == {"gap_replay": 304, "strict_oos": 43}
    )
    gap_ok = True
    for _, group in signals.groupby("scope"):
        diffs = np.diff(np.sort(group["bar_i"].astype(int).unique()))
        gap_ok &= bool((diffs >= MIN_GAP).all())
    checks["dedup_gap_at_least_18"] = gap_ok

    source_index = frame.set_index("open_time", drop=False)
    return_errors: list[float] = []
    for row in signals.itertuples(index=False):
        i = int(row.bar_i)
        entry = float(frame["open"].iloc[i + 1])
        exit_close = float(frame["close"].iloc[i + FUTURE])
        gross = 1.0 - exit_close / entry
        return_errors.append(abs(gross - float(row.gross_ret_3h)))
        return_errors.append(abs((gross - COST) - float(row.net_ret_3h)))
        assert pd.Timestamp(row.signal_time) == source_index.loc[pd.Timestamp(row.signal_time), "open_time"]
        assert pd.Timestamp(row.exit_time) < HOLDOUT
    checks["all_3h_returns_recomputed"] = bool(max(return_errors, default=1.0) <= 1e-12)

    checks["controls_exact_cell"] = bool(
        controls["match_tier"].eq("same_run_atr_quintile").all()
        and controls["signal_gap_run_id"].eq(controls["control_gap_run_id"]).all()
        and controls["signal_atr_bucket"].eq(controls["control_atr_bucket"]).all()
        and controls.groupby(["scope", "signal_bar_i"]).size().between(1, 3).all()
    )
    checks["matched_signal_coverage_100pct"] = bool(
        controls[["scope", "signal_bar_i"]].drop_duplicates().shape[0] == len(signals)
    )

    recomputed: dict[str, dict[str, float | int]] = {}
    for scope in ("strict_oos", "gap_replay"):
        sig = signals[signals["scope"] == scope]
        elig = eligible if scope == "gap_replay" else eligible[eligible["strict_oos"].astype(bool)]
        fire = joined if scope == "gap_replay" else joined[joined["strict_oos"].astype(bool)]
        values = {
            "eligible_bars": int(len(elig)),
            "raw_fires": int(fire["raw_fire"].astype(bool).sum()),
            "dedup_signals": int(len(sig)),
            "raw_fire_rate": float(fire["raw_fire"].astype(bool).mean()),
            "net_mean_at_20bp": float(sig["net_ret_3h"].mean()),
            "matched_control_net_mean_at_20bp": float(sig["control_net_mean"].mean()),
            "paired_excess_mean": float(sig["paired_excess"].mean()),
        }
        recomputed[scope] = values
        reported = summary["replay"][scope]
        checks[f"summary_{scope}_counts"] = all(
            int(reported[key]) == int(values[key])
            for key in ("eligible_bars", "raw_fires", "dedup_signals")
        )
        checks[f"summary_{scope}_means"] = all(
            close_enough(float(reported[key]), float(values[key]))
            for key in (
                "raw_fire_rate",
                "net_mean_at_20bp",
                "matched_control_net_mean_at_20bp",
                "paired_excess_mean",
            )
        )

    failed = sorted(key for key, passed in checks.items() if not passed)
    validation = {
        "status": "pass" if not failed else "fail",
        "checks": checks,
        "failed": failed,
        "recomputed": recomputed,
        "caveats": [
            "strict OOS has only 774 bars / 43 dedup signals / two run-day blocks",
            "3h outcomes overlap, so signal-level t statistics are optimistic",
            "gap replay is pixel-disjoint but interleaved with the training calendar",
        ],
    }
    (OUT / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
