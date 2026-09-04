#!/usr/bin/env python3
"""Independently validate the frozen BTCUSDT.P Pine-v8 1h holdout replay."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts import backtest_two_key_candle_pine_v8_btc_1h as replay


PROJECT = Path(__file__).resolve().parents[1]
RESULTS = replay.RESULTS


def _close(left: Any, right: Any, tolerance: float = 1e-10) -> bool:
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    return bool(
        left_array.shape == right_array.shape
        and np.allclose(left_array, right_array, rtol=0.0, atol=tolerance, equal_nan=True)
    )


def _same_frame(left: pd.DataFrame, right: pd.DataFrame, columns: list[str]) -> bool:
    if len(left) != len(right) or list(left["event_id"]) != list(right["event_id"]):
        return False
    for column in columns:
        if column not in left or column not in right:
            return False
        if pd.api.types.is_numeric_dtype(left[column]) and pd.api.types.is_numeric_dtype(right[column]):
            if not _close(left[column], right[column]):
                return False
        elif list(left[column].astype(str)) != list(right[column].astype(str)):
            return False
    return True


def _future_mutation_checks(
    raw: pd.DataFrame,
    events: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[bool, int]:
    """Change only information unavailable at entry and require earlier signals to stay fixed."""

    if events.empty:
        return True, 0
    positions = sorted({0, len(events) // 2, len(events) - 1})
    for position in positions:
        event = events.iloc[position]
        entry_i = int(event["entry_i"])
        mutated = raw.copy()
        future = mutated.index >= entry_i
        mutated.loc[future, "high"] = mutated.loc[future, "high"] * 1.031
        mutated.loc[future, "low"] = mutated.loc[future, "low"] * 0.969
        mutated.loc[future, "close"] = mutated.loc[future, "close"] * 1.011
        mutated.loc[future, "volume"] = mutated.loc[future, "volume"] * 2.37
        # The entry-bar open is the last datum the signal contract is allowed to use.
        mutated.loc[mutated.index > entry_i, "open"] = mutated.loc[mutated.index > entry_i, "open"] * 1.017
        featured = replay.add_features(mutated)
        candidates = replay.detect_raw_candidates(featured, config)
        accepted = replay.accept_pine_events(candidates, featured, config)
        earlier = accepted[accepted["entry_i"].le(entry_i)].reset_index(drop=True)
        original = events[events["entry_i"].le(entry_i)].reset_index(drop=True)
        if not _same_frame(
            original,
            earlier,
            ["event_id", "k1_i", "k2_i", "entry_i", "direction", "entry_price", "risk_price"],
        ):
            return False, len(positions)
    return True, len(positions)


def main() -> int:
    config = replay.load_config()
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    receipt = json.loads((RESULTS / "source_receipt.json").read_text(encoding="utf-8"))
    raw, quality = replay.load_hourly_source(replay.SOURCE_PATH, config)
    events = pd.read_csv(
        RESULTS / "trade_ledger.csv",
        parse_dates=["k1_time", "k2_time", "entry_time", "exit_bar_time", "exit_available_at"],
    )
    controls = pd.read_csv(
        RESULTS / "matched_controls.csv",
        parse_dates=["control_signal_time", "control_entry_time", "exit_bar_time", "exit_available_at"],
    )
    pairs = pd.read_csv(RESULTS / "matched_pairs.csv")

    featured = replay.add_features(raw)
    candidates = replay.detect_raw_candidates(featured, config)
    accepted = replay.accept_pine_events(candidates, featured, config)
    start = replay._utc(config["window"]["analysis_start_inclusive"])
    end = replay._utc(config["window"]["snapshot_end_exclusive"])
    replayed = accepted[
        accepted["entry_time"].ge(start) & accepted["entry_time"].lt(end)
    ].reset_index(drop=True)
    replayed = replay.attach_outcomes(replayed, featured, config)
    replayed_controls, replayed_pairs = replay.build_matched_controls(replayed, featured, config)

    checks: dict[str, bool] = {}
    checks["owner_authorization_exact"] = bool(
        config["owner_authorization"]["verbatim"] == "批准读取 2026-05-04 之后的价格"
        and config["owner_authorization"]["configuration_holdout_use"] == 1
    )
    checks["configuration_frozen_before_outcomes"] = config["diagnostics"]["frozen_before_outcomes"] is True
    checks["pine_source_hash_exact"] = bool(
        replay.sha256_file(replay.PINE_PATH) == config["signal"]["pine_source_sha256"]
        == summary["pine_source_sha256"]
    )
    checks["source_hash_exact"] = bool(
        replay.sha256_file(replay.SOURCE_PATH) == receipt["snapshot_sha256"] == summary["source_sha256"]
    )
    checks["source_is_gapless_valid_full_window"] = bool(
        quality["gap_count"] == 0
        and quality["invalid_ohlc_rows"] == 0
        and quality["negative_volume_rows"] == 0
        and quality["covers_frozen_window"]
    )
    checks["crosscheck_ohlc_exact_where_overlapping"] = bool(
        summary["source_crosschecks"]
        and all(item["overlap_rows"] > 0 and item["ohlc_exact_match"] for item in summary["source_crosschecks"])
    )
    checks["event_ids_unique"] = bool(events["event_id"].is_unique)
    checks["entry_is_immediately_after_k2"] = bool((events["entry_i"] == events["k2_i"] + 1).all())
    checks["recorded_gap_matches_indices"] = bool((events["gap_bars"] == events["k2_i"] - events["k1_i"]).all())
    checks["events_inside_preregistered_window"] = bool(
        events["entry_time"].ge(start).all() and events["entry_time"].lt(end).all()
    )
    checks["six_bar_global_cooldown"] = bool(
        events["entry_i"].sort_values().diff().dropna().ge(config["signal"]["cooldown_bars"]).all()
    )
    expected_stop = np.where(events["direction"].eq(1), events["k2_low"], events["k2_high"])
    expected_risk = events["direction"] * (events["entry_price"] - expected_stop)
    expected_target = events["entry_price"] + events["direction"] * expected_risk * config["execution"]["target_r"]
    checks["exact_k2_stop_and_target"] = bool(
        _close(events["stop_price"], expected_stop)
        and _close(events["risk_price"], expected_risk)
        and _close(events["target_price"], expected_target)
    )
    checks["risk_positive_and_inside_frozen_band"] = bool(
        events["risk_price"].gt(0.0).all()
        and events["stop_distance_atr"].between(
            config["signal"]["next_open_risk_atr_min"], config["signal"]["next_open_risk_atr_max"]
        ).all()
    )
    checks["full_trade_replay_exact"] = _same_frame(
        events,
        replayed,
        [
            "event_id", "k1_i", "k2_i", "entry_i", "direction", "entry_price", "stop_price",
            "target_price", "outcome", "resolved", "exit_i", "exit_price", "gross_return", "net_return",
            "mfe_r", "mae_r", "path_class", "verdict", "causal_flags",
        ],
    )
    checks["returns_recompute_with_frozen_cost"] = bool(
        _close(
            events.loc[events["resolved"], "net_return"],
            events.loc[events["resolved"], "gross_return"] - config["execution"]["round_trip_cost_fraction"],
        )
    )
    resolved_ids = set(events.loc[events["resolved"], "event_id"])
    control_counts = controls.groupby("candidate_event_id").size()
    checks["three_controls_for_every_resolved_trade"] = bool(
        set(control_counts.index) == resolved_ids
        and control_counts.eq(config["matched_control"]["controls_per_trade"]).all()
    )
    checks["controls_exact_replay"] = bool(
        len(controls) == len(replayed_controls)
        and list(controls["candidate_event_id"]) == list(replayed_controls["candidate_event_id"])
        and _close(controls["control_signal_i"], replayed_controls["control_signal_i"])
        and _close(controls["net_return"], replayed_controls["net_return"])
    )
    checks["paired_excess_recomputes"] = bool(
        len(pairs) == len(replayed_pairs)
        and list(pairs["event_id"]) == list(replayed_pairs["event_id"])
        and _close(pairs["paired_excess_return"], replayed_pairs["paired_excess_return"])
        and _close(pairs["paired_excess_return"], pairs["candidate_net_return"] - pairs["control_mean_net_return"])
    )
    radius = int(config["matched_control"]["exclude_within_bars_of_signal"])
    all_signal_i = events["k2_i"].to_numpy(dtype=int)
    checks["controls_outside_signal_exclusion_radius"] = bool(
        all(
            np.min(np.abs(all_signal_i - int(control_i))) > radius
            for control_i in controls["control_signal_i"]
        )
    )
    mutation_passed, mutation_count = _future_mutation_checks(raw, events, config)
    checks["sampled_future_mutation_causality"] = mutation_passed
    checks["summary_counts_match_ledgers"] = bool(
        summary["analysis_signals"] == len(events)
        and summary["resolved_signals"] == int(events["resolved"].sum())
        and summary["unresolved_signals"] == int((~events["resolved"]).sum())
        and len(controls) == int(events["resolved"].sum()) * config["matched_control"]["controls_per_trade"]
    )
    checks["no_training_tuning_promotion_or_live_eligibility"] = bool(
        summary["thresholds_tuned_after_holdout"] is False
        and summary["training_eligible"] is False
        and summary["production_eligible"] is False
        and config["eligibility"]["live_orders_allowed"] is False
    )

    failed = sorted(name for name, passed in checks.items() if not passed)
    payload = {
        "status": "pass" if not failed else "fail",
        "checks": checks,
        "failed": failed,
        "counts": {
            "source_bars": len(raw),
            "raw_best_k1_candidates": len(candidates),
            "accepted_analysis_signals": len(events),
            "resolved_signals": int(events["resolved"].sum()),
            "matched_controls": len(controls),
            "future_mutations": mutation_count,
        },
        "assessment": "share_with_caveats" if not failed else "needs_revision",
        "caveats": [
            "Python replay is pinned to the Pine source hash but is not a TradingView Strategy Tester export.",
            "OHLC bars cannot order an intrabar stop/target collision; the frozen rule assigns it to stop.",
            "Funding and slippage beyond the frozen 0.2% round-trip cost are excluded.",
            "Pre-entry flags are descriptive associations and must not be called causes or tuned on this snapshot.",
        ],
    }
    replay.write_json(RESULTS / "validation.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
