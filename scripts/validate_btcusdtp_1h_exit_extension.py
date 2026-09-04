#!/usr/bin/env python3
"""Validate the frozen BTCUSDT.P 1h exit-only diagnostic artifacts.

This validator reads the saved arm ledger and original frozen candidate/control
ledgers.  It independently checks identity stability, 3R parity, fixed/split
payoff arithmetic, 20bp cost application, summaries, continuation counts, and
the post-hoc/non-promotable contract.  It does not read bars beyond the already
authorized 12-bar paths or alter any trading state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import write_json
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    PROJECT
    / "experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1"
)
RESULTS = EXPERIMENT / "results"
PROTOCOL = EXPERIMENT / "protocol_amendment_02_exit_extension.json"
ORIGINAL_EVENTS = RESULTS / "trade_ledger.csv"
ORIGINAL_CONTROLS = RESULTS / "matched_controls.csv"
TRADES = RESULTS / "exit_target_trade_ledger.csv"
SUMMARY = RESULTS / "exit_target_summary.csv"
COMPARISONS = RESULTS / "exit_target_comparisons.csv"
PERIODS = RESULTS / "exit_target_periods.csv"
CONTINUATION = RESULTS / "exit_target_continuation.csv"
DIAGNOSTICS = RESULTS / "exit_target_diagnostics.json"
CHART = RESULTS / "exit_target_diagnostics.png"
OUTPUT = RESULTS / "exit_target_validation.json"
ARMS = {
    "fixed_3R",
    "fixed_4R",
    "fixed_5R",
    "fixed_6R",
    "split_3R_6R",
    "split_3R_runner",
}


def _original_family(value: pd.Series) -> pd.Series:
    return pd.Series(
        np.where(
            value.eq("tp"),
            "target",
            np.where(value.astype(str).str.startswith("sl"), "stop", "timeout"),
        ),
        index=value.index,
    )


def _close(left: pd.Series, right: pd.Series, tolerance: float = 1e-11) -> bool:
    return bool(np.allclose(left.to_numpy(dtype=float), right.to_numpy(dtype=float), atol=tolerance))


def validate() -> dict[str, Any]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    diagnostics = json.loads(DIAGNOSTICS.read_text(encoding="utf-8"))
    trades = pd.read_csv(TRADES, parse_dates=["entry_time"])
    summary = pd.read_csv(SUMMARY)
    comparisons = pd.read_csv(COMPARISONS)
    periods = pd.read_csv(PERIODS)
    continuation = pd.read_csv(CONTINUATION)
    original_events = pd.read_csv(ORIGINAL_EVENTS)
    original_controls = pd.read_csv(ORIGINAL_CONTROLS)
    candidate = trades[trades["subject_type"].eq("candidate")].copy()
    controls = trades[trades["subject_type"].eq("control")].copy()
    baseline_candidate = candidate[candidate["arm"].eq("fixed_3R")].sort_values(
        "candidate_event_id"
    )
    original_events = original_events.sort_values("event_id")
    merged_candidate = baseline_candidate.merge(
        original_events,
        left_on="candidate_event_id",
        right_on="event_id",
        suffixes=("_new", "_old"),
        validate="one_to_one",
    )
    baseline_control = controls[controls["arm"].eq("fixed_3R")].sort_values(
        ["candidate_event_id", "control_rank"]
    )
    original_controls = original_controls.sort_values(["candidate_event_id", "control_rank"])
    merged_control = baseline_control.merge(
        original_controls,
        on=["candidate_event_id", "control_rank"],
        suffixes=("_new", "_old"),
        validate="one_to_one",
    )

    candidate_identity_spread = candidate.groupby("candidate_event_id").agg(
        entry_i=("entry_i", "nunique"),
        direction=("direction", "nunique"),
        entry_price=("entry_price", "nunique"),
        risk_price=("risk_price", "nunique"),
    )
    control_identity_spread = controls.groupby(["candidate_event_id", "control_rank"]).agg(
        entry_i=("entry_i", "nunique"),
        direction=("direction", "nunique"),
        entry_price=("entry_price", "nunique"),
        risk_price=("risk_price", "nunique"),
    )
    fixed = candidate[candidate["arm"].str.startswith("fixed_")]
    fixed_target_r = fixed["arm"].str.extract(r"fixed_(\d+)R")[0].astype(float)
    split_6 = candidate[candidate["arm"].eq("split_3R_6R")]
    split_runner = candidate[candidate["arm"].eq("split_3R_runner")]

    summary_recomputed = candidate.groupby("arm").agg(
        n=("net_return", "size"),
        mean_net_bp=("net_return", lambda values: values.mean() * 10_000.0),
        target_count=("exit_family", lambda values: int(values.eq("target").sum())),
        stop_count=("exit_family", lambda values: int(values.eq("stop").sum())),
        timeout_count=("exit_family", lambda values: int(values.eq("timeout").sum())),
    )
    summary_indexed = summary.set_index("arm")

    pivot = candidate.pivot(index="candidate_event_id", columns="arm", values="net_return")
    comparison_recomputed = pd.Series(
        {
            arm: float((pivot[arm] - pivot["fixed_3R"]).mean() * 10_000.0)
            for arm in ARMS - {"fixed_3R"}
        }
    )
    comparison_saved = comparisons.set_index("arm")["mean_delta_bp_vs_3r"]

    checks = {
        "protocol_is_holdout_use_2_and_exit_only": (
            int(protocol["owner_authorization"]["configuration_holdout_use"]) == 2
            and bool(protocol["decision_rule"]["diagnostic_only"])
            and bool(protocol["decision_rule"]["no_arm_can_be_promoted_from_this_snapshot"])
        ),
        "declared_input_hashes_exact": all(
            [
                sha256_file(EXPERIMENT / protocol["fixed_inputs"]["source"])
                == protocol["fixed_inputs"]["source_sha256"],
                sha256_file(EXPERIMENT / protocol["fixed_inputs"]["signal_ledger"])
                == protocol["fixed_inputs"]["signal_ledger_sha256"],
                sha256_file(EXPERIMENT / protocol["fixed_inputs"]["matched_controls"])
                == protocol["fixed_inputs"]["matched_controls_sha256"],
            ]
        ),
        "strict_json_and_nonpromotable": (
            diagnostics["configuration_holdout_use"] == 2
            and diagnostics["posthoc"] is True
            and diagnostics["promotable"] is False
        ),
        "all_predeclared_arms_present": set(trades["arm"].unique()) == ARMS,
        "candidate_and_control_row_counts": len(candidate) == 49 * 6 and len(controls) == 141 * 6,
        "candidate_keys_unique": not candidate.duplicated(["candidate_event_id", "arm"]).any(),
        "control_keys_unique": not controls.duplicated(
            ["candidate_event_id", "control_rank", "arm"]
        ).any(),
        "candidate_identity_unchanged_across_arms": bool(candidate_identity_spread.eq(1).all().all()),
        "control_identity_unchanged_across_arms": bool(control_identity_spread.eq(1).all().all()),
        "fixed_3r_candidate_net_parity": _close(
            merged_candidate["net_return_new"], merged_candidate["net_return_old"]
        ),
        "fixed_3r_candidate_outcome_parity": bool(
            merged_candidate["exit_family"].eq(_original_family(merged_candidate["outcome_old"])).all()
        ),
        "fixed_3r_control_net_parity": _close(
            merged_control["net_return_new"], merged_control["net_return_old"]
        ),
        "cost_applied_once": _close(trades["net_return"], trades["gross_return"] - 0.002),
        "net_r_recomputed": _close(
            trades["net_return_r"], trades["net_return"] / trades["risk_pct"]
        ),
        "fixed_target_and_stop_payoffs_exact": bool(
            np.allclose(
                fixed.loc[fixed["exit_family"].eq("target"), "return_r"],
                fixed_target_r[fixed["exit_family"].eq("target")],
            )
            and np.allclose(fixed.loc[fixed["exit_family"].eq("stop"), "return_r"], -1.0)
        ),
        "split_3r_6r_payoffs_exact": bool(
            np.allclose(split_6.loc[split_6["outcome"].eq("full_target"), "return_r"], 4.5)
            and np.allclose(split_6.loc[split_6["outcome"].eq("scale_then_stop"), "return_r"], 1.0)
            and np.allclose(
                split_6.loc[split_6["outcome"].eq("stop_before_scale"), "return_r"], -1.0
            )
        ),
        "runner_stop_payoffs_exact": bool(
            np.allclose(split_runner.loc[split_runner["outcome"].eq("scale_then_stop"), "return_r"], 1.0)
            and np.allclose(
                split_runner.loc[
                    split_runner["outcome"].eq("stop_before_scale"), "return_r"
                ],
                -1.0,
            )
        ),
        "summary_counts_and_means_recompute": bool(
            summary_indexed["n"].eq(summary_recomputed["n"]).all()
            and summary_indexed["target_count"].eq(summary_recomputed["target_count"]).all()
            and summary_indexed["stop_count"].eq(summary_recomputed["stop_count"]).all()
            and summary_indexed["timeout_count"].eq(summary_recomputed["timeout_count"]).all()
            and np.allclose(summary_indexed["mean_net_bp"], summary_recomputed["mean_net_bp"])
        ),
        "paired_deltas_recompute": bool(
            np.allclose(
                comparison_saved.sort_index(),
                comparison_recomputed.sort_index(),
            )
        ),
        "continuation_population_is_original_3r_targets": (
            len(continuation) == 16
            and int(continuation["fixed_4R_exit_family"].eq("target").sum()) == 13
            and int(continuation["fixed_5R_exit_family"].eq("target").sum()) == 9
            and int(continuation["fixed_6R_exit_family"].eq("target").sum()) == 7
        ),
        "period_table_complete": len(periods) == 6 * 3,
        "target_hits_monotone": summary_indexed.loc[
            ["fixed_3R", "fixed_4R", "fixed_5R", "fixed_6R"], "target_count"
        ].tolist()
        == [16, 13, 9, 7],
        "chart_exists": CHART.exists() and CHART.stat().st_size > 100_000,
    }
    failed = [name for name, passed in checks.items() if not bool(passed)]
    payload = {
        "status": "pass" if not failed else "fail",
        "checks": {name: bool(value) for name, value in checks.items()},
        "failed": failed,
        "counts": {
            "checks": len(checks),
            "candidate_entries": 49,
            "matched_control_entries": 141,
            "arms": 6,
            "trade_arm_rows": len(trades),
            "continuation_rows": len(continuation),
        },
        "artifact_hashes": {
            path.name: sha256_file(path)
            for path in [
                TRADES,
                SUMMARY,
                COMPARISONS,
                PERIODS,
                CONTINUATION,
                DIAGNOSTICS,
                CHART,
            ]
        },
        "assessment": "exploratory_exit_hypothesis_only",
    }
    write_json(OUTPUT, payload)
    if failed:
        raise RuntimeError(f"exit-extension validation failed: {failed}")
    return payload


def main() -> int:
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
