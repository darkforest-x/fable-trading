#!/usr/bin/env python3
"""Independent integrity checks for the ALLIN-V7 pre-holdout replay."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "experiments/active/exp-pine-allin-v7-preholdout-v1/results"
HOLDOUT = pd.Timestamp("2026-05-04T00:00:00Z")
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
COST = 0.002
EXPECTED_ARMS = {
    "sma_cross_only",
    "v7_fixed_boost",
    "v7_fixed_no_boost",
    "v7_risk_2pct",
    "v7_risk_2pct_no_skip",
}


def close(left: pd.Series, right: pd.Series, tolerance: float = 1e-11) -> bool:
    return bool(np.nanmax(np.abs(left.to_numpy(dtype=float) - right.to_numpy(dtype=float))) <= tolerance)


def main() -> int:
    trades = pd.read_csv(OUT / "trades.csv", parse_dates=["signal_time", "entry_time", "exit_time"])
    controls = pd.read_csv(
        OUT / "matched_controls.csv",
        parse_dates=["candidate_signal_time", "control_signal_time"],
    )
    quality = pd.read_csv(OUT / "data_quality.csv", parse_dates=["first_bar", "last_bar"])
    summary = pd.read_csv(OUT / "summary.csv")
    portfolio = pd.read_csv(OUT / "portfolio_daily.csv", parse_dates=["open_time"])
    payload = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))

    checks: dict[str, bool] = {}
    checks["holdout_not_consumed_flag"] = payload["holdout_consumed"] is False
    checks["all_source_bars_before_safe_end"] = bool(quality["last_bar"].max() < SAFE_END)
    checks["safe_end_before_holdout"] = bool(SAFE_END < HOLDOUT)
    checks["all_trade_times_before_safe_end"] = bool(trades["exit_time"].max() < SAFE_END)
    checks["all_control_times_before_safe_end"] = bool(controls["control_signal_time"].max() < SAFE_END)
    checks["universe_is_54_gapless_symbols"] = bool(
        quality["symbol"].nunique() == 54 and int(quality["gap_count"].sum()) == 0
    )
    checks["all_arms_and_splits_present"] = bool(
        set(summary["arm"]) == EXPECTED_ARMS and set(summary["split"]) == {"development", "validation"}
    )
    checks["trade_ids_unique"] = bool(trades["trade_id"].is_unique)
    checks["entry_is_next_bar"] = bool((trades["entry_i"] == trades["signal_i"] + 1).all())
    checks["holding_bars_consistent"] = bool(
        (trades["holding_bars"] == trades["exit_i"] - trades["entry_i"]).all()
    )
    recomputed_gross = np.where(
        trades["direction"].eq("long"),
        trades["exit_price"] / trades["entry_price"] - 1.0,
        1.0 - trades["exit_price"] / trades["entry_price"],
    )
    checks["trade_returns_recomputed"] = close(
        trades["gross_return"], pd.Series(recomputed_gross)
    ) and close(trades["net_return"], pd.Series(recomputed_gross - COST))
    checks["control_returns_recomputed"] = close(
        controls["control_net_return"], controls["control_gross_return"] - COST
    )
    checks["control_exact_strata_recorded"] = bool(
        controls["match_month"].notna().all()
        and controls["match_hk_6h_block"].between(0, 3).all()
        and controls["match_atr_quintile"].between(0, 4).all()
        and controls["control_rank"].between(0, 2).all()
    )
    checks["control_side_and_horizon_copied"] = bool(
        controls["direction"].isin(["long", "short"]).all()
        and (controls["holding_bars"] >= 0).all()
    )
    checks["no_candidate_used_as_own_control"] = bool(
        (controls["candidate_signal_i"] != controls["control_signal_i"]).all()
    )
    controls_per_trade = controls.groupby("trade_id").size()
    checks["at_most_three_controls_per_trade"] = bool(controls_per_trade.between(1, 3).all())
    checks["portfolio_finite_nonnegative"] = bool(
        np.isfinite(portfolio["portfolio_equity"]).all()
        and (portfolio["portfolio_equity"] >= 0.0).all()
    )
    checks["summary_net_matches_cost"] = close(
        summary["net_bp_per_trade"], summary["gross_bp_per_trade"] - COST * 1e4, tolerance=1e-8
    )
    checks["permutation_counts_frozen"] = bool(
        all(item["permutations"] == 10_000 for item in payload["ranking"].values())
    )

    failed = sorted(name for name, passed in checks.items() if not passed)
    validation = {
        "status": "pass" if not failed else "fail",
        "checks": checks,
        "failed": failed,
        "counts": {
            "symbols": int(quality["symbol"].nunique()),
            "trades": int(len(trades)),
            "controls": int(len(controls)),
            "summary_rows": int(len(summary)),
        },
        "assessment": "share_with_caveats" if not failed else "needs_revision",
        "required_caveats": payload["caveats"],
    }
    (OUT / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
