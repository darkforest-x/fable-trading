#!/usr/bin/env python3
"""Evaluate causal dynamic stop managers on the frozen BTCUSDT.P 15m cohort.

Signal construction is not repeated or tuned here.  The input decision ledger
was produced by the committed two-stage K2 experiment and contains only values
known at the next-open entry.  Each stop manager reads OHLC, SMA40(HL2), and
ATR14 sequentially from ``entry_i`` through the registered 48-bar horizon.
Updates made from a completed candle become active on the following candle.

The physical OHLCV source ends on 2026-02-28, before the repository holdout at
2026-05-04.  Development reads 2023-2024 only.  Post-exit recovery fields are
diagnostics and are never inputs to a stop decision or model prediction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from scripts.optimize_btcusdtp_k1k2_independent_timeframes import (
    ranking_metrics,
)
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    BAR_DELTAS,
    add_control_metrics,
    atr_quintiles,
    fold_table,
    load_featured,
    robust_metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_btcusdtp_k1k2_15m_two_stage_k2 import (
    build_k2_event_candidates,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-dynamic-stop-preholdout-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SCRIPT_PATH = Path(__file__).resolve()
BAR = "15m"
EPSILON = 1e-12

BLUE = "#2563EB"
BLUE_LIGHT = "#BFDBFE"
ORANGE = "#D97706"
ORANGE_LIGHT = "#FED7AA"
INK = "#172033"
GRID = "#D9DEE8"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _monotone_stop(current: float, proposed: float, direction: int) -> float:
    return max(current, proposed) if direction > 0 else min(current, proposed)


def _stop_hit(low: float, high: float, stop: float, direction: int) -> bool:
    return low <= stop if direction > 0 else high >= stop


def _target_hit(low: float, high: float, target: float, direction: int) -> bool:
    return high >= target if direction > 0 else low <= target


def _lock_price(
    entry: float,
    risk: float,
    direction: int,
    cost: float,
    locked_r: float | str,
) -> float:
    if locked_r == "fee_cover":
        return entry * (1.0 + direction * cost)
    return entry + direction * risk * float(locked_r)


def resolve_stop_policy(
    frame: pd.DataFrame,
    event: dict[str, Any],
    config: dict[str, Any],
    arm: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one stop policy using only completed information at each step.

    Source columns are open/high/low/close, causal SMA40(HL2), causal ATR14,
    and the frozen entry/stop values.  A stop update derived from row ``i`` is
    assigned only after row ``i`` survives both barriers, so it first applies
    to row ``i + 1``.  The original 3R target and 20bp total cost never change.
    """

    execution = config["execution_frozen"]
    horizon = int(execution["horizon_bars"])
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    original_stop = float(event["stop_price"])
    risk = float(event["risk_price"])
    risk_fraction = float(event["risk_fraction"])
    target = entry + direction * risk * float(execution["target_r"])
    cost = float(execution["round_trip_cost_fraction"])
    fee_cover = entry * (1.0 + direction * cost)
    policy = str(arm["label"])
    kind = str(arm["kind"])

    last_i = entry_i + horizon - 1
    path = frame.loc[entry_i:last_i]
    favourable_all = (
        path["high"].to_numpy(dtype=float) - entry
        if direction > 0
        else entry - path["low"].to_numpy(dtype=float)
    )
    adverse_all = (
        entry - path["low"].to_numpy(dtype=float)
        if direction > 0
        else path["high"].to_numpy(dtype=float) - entry
    )
    horizon_mfe_r = float(np.max(favourable_all) / risk)
    horizon_mae_r = float(np.max(adverse_all) / risk)

    active_stop = original_stop
    active_stop_source = "original_structure"
    if kind == "conditional_soft_structure_stop":
        confirmation_atr = risk / float(event["stop_distance_atr"])
        active_stop = original_stop - direction * (
            float(arm["catastrophe_buffer_atr"]) * confirmation_atr
        )
        active_stop_source = "catastrophe"

    scheduled_reason: str | None = None
    protection_armed_i: int | None = None
    chandelier_active = False
    mfe_price = 0.0
    mae_price = 0.0
    best_close_r = -math.inf
    favourable_water = entry
    stop_updates = 0
    structural_touches = 0
    valid_reclaims = 0
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    exit_stop_source = ""

    for i in range(entry_i, last_i + 1):
        open_ = float(frame.loc[i, "open"])
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        close = float(frame.loc[i, "close"])
        sma = float(frame.loc[i, "sma40_hl2"])
        atr = float(frame.loc[i, "atr"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        mfe_price = max(mfe_price, favourable)
        mae_price = max(mae_price, adverse)
        favourable_water = (
            max(favourable_water, high)
            if direction > 0
            else min(favourable_water, low)
        )

        if scheduled_reason is not None:
            exit_i = i
            exit_price = open_
            outcome = scheduled_reason
            exit_stop_source = "scheduled_next_open"
            break

        hit_stop = _stop_hit(low, high, active_stop, direction)
        hit_target = _target_hit(low, high, target, direction)
        if hit_stop:
            exit_i = i
            exit_price = active_stop
            if active_stop_source == "original_structure":
                outcome = "sl_ambiguous" if hit_target else "sl"
            elif active_stop_source == "catastrophe":
                outcome = (
                    "catastrophe_stop_ambiguous" if hit_target else "catastrophe_stop"
                )
            elif active_stop_source == "fee_cover":
                outcome = (
                    "protected_stop_ambiguous" if hit_target else "protected_stop"
                )
            else:
                outcome = (
                    "dynamic_stop_ambiguous" if hit_target else "dynamic_stop"
                )
            exit_stop_source = active_stop_source
            break
        if hit_target:
            exit_i = i
            exit_price = target
            outcome = "tp"
            exit_stop_source = "target"
            break

        close_r = direction * (close - entry) / risk
        best_close_r = max(best_close_r, close_r)

        if kind == "conditional_soft_structure_stop":
            touched_original = _stop_hit(low, high, original_stop, direction)
            if touched_original:
                structural_touches += 1
                reclaimed_stop = direction * (close - original_stop) > 0.0
                intended_sma_side = direction * (close - sma) >= 0.0
                if reclaimed_stop and intended_sma_side:
                    valid_reclaims += 1
                else:
                    scheduled_reason = "structure_invalid_next_open"

        proposed_stop: float | None = None
        proposed_source = ""
        if kind in {"baseline", "automatic_structure_exit", "conditional_soft_structure_stop"}:
            if close_r >= float(execution["baseline_profit_protection_trigger_close_r"]):
                proposed_stop = fee_cover
                proposed_source = "fee_cover"
                if protection_armed_i is None:
                    protection_armed_i = i
        elif kind == "dynamic_r_ladder":
            trigger_value = mfe_price / risk if arm["trigger_source"].startswith("running") else best_close_r
            for level in arm["levels"]:
                if trigger_value >= float(level["trigger_r"]):
                    candidate = _lock_price(
                        entry, risk, direction, cost, level["locked_r"]
                    )
                    proposed_stop = (
                        candidate
                        if proposed_stop is None
                        else _monotone_stop(proposed_stop, candidate, direction)
                    )
                    proposed_source = "wick_r_ladder" if arm["trigger_source"].startswith("running") else "close_r_ladder"
                    if protection_armed_i is None:
                        protection_armed_i = i
        elif kind == "atr_chandelier":
            if close_r >= float(arm["activation_close_r"]):
                chandelier_active = True
                if protection_armed_i is None:
                    protection_armed_i = i
            if chandelier_active:
                trail = (
                    favourable_water - float(arm["atr_multiple"]) * atr
                    if direction > 0
                    else favourable_water + float(arm["atr_multiple"]) * atr
                )
                proposed_stop = _monotone_stop(fee_cover, trail, direction)
                proposed_source = "atr_chandelier"
        else:
            raise ValueError(f"unknown stop manager kind: {kind}")

        if kind == "automatic_structure_exit" and direction * (close - sma) < 0.0:
            scheduled_reason = "sma40_invalid_next_open"

        if proposed_stop is not None:
            updated = _monotone_stop(active_stop, proposed_stop, direction)
            if abs(updated - active_stop) > EPSILON:
                active_stop = updated
                active_stop_source = proposed_source
                stop_updates += 1

    if exit_i is None:
        exit_i = last_i
        exit_price = float(frame.loc[last_i, "close"])
        outcome = "timeout"
        exit_stop_source = "timeout_close"

    gross = direction * (float(exit_price) / entry - 1.0)
    final_stop_r = direction * (active_stop - entry) / risk
    return {
        "resolved": True,
        "stop_policy": policy,
        "outcome": outcome,
        "exit_stop_source": exit_stop_source,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTAS[BAR],
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": float(gross),
        "net_return": float(gross - cost),
        "return_r": float(direction * (float(exit_price) - entry) / risk),
        "net_return_r": float((gross - cost) / risk_fraction),
        "mfe_r": float(mfe_price / risk),
        "mae_r": float(mae_price / risk),
        "horizon_mfe_r": horizon_mfe_r,
        "horizon_mae_r": horizon_mae_r,
        "horizon_hit_4r": bool(horizon_mfe_r >= 4.0),
        "horizon_hit_5r": bool(horizon_mfe_r >= 5.0),
        "horizon_hit_6r": bool(horizon_mfe_r >= 6.0),
        "protection_armed": protection_armed_i is not None,
        "protection_armed_i": protection_armed_i,
        "stop_updates": int(stop_updates),
        "final_active_stop": float(active_stop),
        "final_active_stop_r": float(final_stop_r),
        "structural_touches": int(structural_touches),
        "valid_structure_reclaims": int(valid_reclaims),
    }


def load_frozen_decisions(
    config: dict[str, Any], frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    predecessor = config["predecessor"]
    for path_key, hash_key in (
        ("config_path", "config_sha256"),
        ("decision_ledger_path", "decision_ledger_sha256"),
        ("reference_trade_ledger_path", "reference_trade_ledger_sha256"),
    ):
        path = PROJECT / str(predecessor[path_key])
        if sha256_file(path) != str(predecessor[hash_key]):
            raise RuntimeError(f"frozen predecessor artifact drift: {path_key}")
    decisions = pd.read_csv(PROJECT / predecessor["decision_ledger_path"])
    decisions["entry_time"] = pd.to_datetime(decisions["entry_time"], utc=True)
    horizon = int(config["execution_frozen"]["horizon_bars"])
    keep: list[bool] = []
    for row in decisions.itertuples(index=False):
        last = int(row.entry_i) + horizon - 1
        keep.append(
            bool(
                row.entry_time >= start
                and row.entry_time < end
                and last < len(frame)
                and int(frame.loc[last, "segment_id"])
                == int(frame.loc[int(row.entry_i), "segment_id"])
                and frame.loc[last, "open_time"] + BAR_DELTAS[BAR] <= end
            )
        )
    selected = decisions.loc[keep].copy().reset_index(drop=True)
    expected = int(config["signal_and_entry_frozen"]["entries_expected"])
    if len(selected) != expected:
        raise RuntimeError(f"expected {expected} frozen entries, got {len(selected)}")
    return selected


def predecessor_signal_indices(
    config: dict[str, Any], frame: pd.DataFrame
) -> set[int]:
    """Return every predecessor signal endpoint, not just accepted entries."""

    predecessor_config = json.loads(
        (PROJECT / config["predecessor"]["config_path"]).read_text(encoding="utf-8")
    )
    same_bar = build_k2_event_candidates(
        frame, predecessor_config, maximum_confirmation_delay_bars=0
    )
    two_stage = build_k2_event_candidates(
        frame, predecessor_config, maximum_confirmation_delay_bars=2
    )
    return set(same_bar["k2_i"].astype(int)) | set(two_stage["k2_i"].astype(int))


def run_policy(
    decisions: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    arm: dict[str, Any],
) -> pd.DataFrame:
    outcomes = [
        resolve_stop_policy(frame, event, config, arm)
        for event in decisions.to_dict("records")
    ]
    return pd.DataFrame(
        [
            {**event, **outcome}
            for event, outcome in zip(decisions.to_dict("records"), outcomes)
        ]
    )


def build_control_specs(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
    excluded_signal_indices: set[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build fixed exact-stratum control events before applying any policy."""

    horizon = int(config["execution_frozen"]["horizon_bars"])
    n = len(frame)
    eligible = np.zeros(n, dtype=bool)
    for signal_i in range(n - horizon - 1):
        entry_i = signal_i + 1
        last = entry_i + horizon - 1
        eligible[signal_i] = bool(
            frame.loc[entry_i, "open_time"] >= start
            and frame.loc[entry_i, "open_time"] < end
            and frame.loc[last, "open_time"] + BAR_DELTAS[BAR] <= end
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
        )
    excluded = np.zeros(n, dtype=bool)
    radius = horizon + 1
    for index in excluded_signal_indices:
        excluded[max(0, index - radius) : min(n, index + radius + 1)] = True
    buckets = atr_quintiles(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & ~excluded & (buckets >= 0)):
        pool.setdefault(
            (str(months[index]), int(blocks[index]), int(buckets[index])), []
        ).append(int(index))

    required = int(config["matched_control"]["controls_per_trade"])
    seed = str(config["matched_control"]["seed"])
    specs: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        signal_i = int(event["k2_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            pool.get(key, []),
            key=lambda index: hashlib.sha256(
                f"{seed}|{BAR}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            matches.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched_insufficient_exact_stratum",
                    "matched_control_count": len(choices),
                }
            )
            continue
        for rank, control_i in enumerate(choices[:required]):
            entry_i = control_i + 1
            entry = float(frame.loc[entry_i, "open"])
            atr = float(frame.loc[control_i, "atr"])
            stop_distance_atr = float(event["stop_distance_atr"])
            risk = stop_distance_atr * atr
            direction = int(event["direction"])
            specs.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "control_rank": rank,
                    "control_i": control_i,
                    "k2_i": control_i,
                    "entry_i": entry_i,
                    "entry_time": frame.loc[entry_i, "open_time"],
                    "entry_price": entry,
                    "direction": direction,
                    "risk_price": risk,
                    "risk_fraction": risk / entry,
                    "stop_price": entry - direction * risk,
                    "stop_distance_atr": stop_distance_atr,
                    "secondary_score": event["secondary_score"],
                    "k1_range_atr": event["k1_range_atr"],
                    "month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                }
            )
        matches.append(
            {
                "setup_id": event["setup_id"],
                "match_status": "matched_exact",
                "matched_control_count": required,
            }
        )
    return pd.DataFrame(specs), pd.DataFrame(matches)


def resolve_controls(
    specs: pd.DataFrame,
    candidate_events: pd.DataFrame,
    matches: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    arm: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    controls = run_policy(specs, frame, config, arm) if len(specs) else specs.copy()
    rows: list[dict[str, Any]] = []
    event_net = candidate_events.set_index("setup_id")["net_return"]
    for match in matches.to_dict("records"):
        setup_id = str(match["setup_id"])
        if match["match_status"] != "matched_exact":
            rows.append(
                {
                    **match,
                    "candidate_net_return": float(event_net.loc[setup_id]),
                    "control_mean_net_return": np.nan,
                    "paired_excess_return": np.nan,
                }
            )
            continue
        subset = controls.loc[controls["candidate_setup_id"].eq(setup_id)]
        mean = float(subset["net_return"].mean())
        candidate = float(event_net.loc[setup_id])
        rows.append(
            {
                **match,
                "candidate_net_return": candidate,
                "control_mean_net_return": mean,
                "paired_excess_return": candidate - mean,
            }
        )
    return controls, pd.DataFrame(rows)


def paired_familywise_signflip(
    ledgers: dict[str, pd.DataFrame], config: dict[str, Any]
) -> pd.DataFrame:
    """Return max-statistic adjusted p-values for candidates versus baseline."""

    baseline_label = str(config["factor"]["baseline_label"])
    baseline = ledgers[baseline_label].sort_values("setup_id")
    labels = [
        str(arm["label"])
        for arm in config["factor"]["arms"]
        if str(arm["label"]) != baseline_label
    ]
    differences = []
    for label in labels:
        candidate = ledgers[label].sort_values("setup_id")
        if not candidate["setup_id"].reset_index(drop=True).equals(
            baseline["setup_id"].reset_index(drop=True)
        ):
            raise RuntimeError(f"entry cohort drift for {label}")
        differences.append(
            (candidate["net_return"].to_numpy(dtype=float) - baseline["net_return"].to_numpy(dtype=float))
            * 1e4
        )
    matrix = np.vstack(differences).T
    observed = matrix.mean(axis=0)
    resamples = int(config["multiple_comparison"]["resamples"])
    rng = np.random.default_rng(int(config["multiple_comparison"]["seed"]))
    exceed = np.zeros(len(labels), dtype=int)
    batch_size = 1000
    done = 0
    while done < resamples:
        batch = min(batch_size, resamples - done)
        signs = rng.choice((-1.0, 1.0), size=(batch, len(matrix)))
        # NumPy linked to macOS Accelerate can emit spurious overflow warnings
        # for tiny finite matmuls.  Explicit einsum keeps the same arithmetic
        # on the stable code path used elsewhere in this repository.
        null = np.einsum("bi,ij->bj", signs, matrix, optimize=False) / len(matrix)
        maxima = null.max(axis=1)
        exceed += np.count_nonzero(
            maxima[:, None] >= observed[None, :] - EPSILON, axis=0
        )
        done += batch
    return pd.DataFrame(
        {
            "stop_policy": labels,
            "paired_mean_improvement_bp": observed,
            "familywise_signflip_p_one_sided": (exceed + 1) / (resamples + 1),
            "resamples": resamples,
        }
    )


def baseline_failure_bucket(row: pd.Series) -> str:
    outcome = str(row["outcome"])
    if outcome.startswith("sl"):
        if float(row["mfe_r"]) < 0.5:
            return "SL before 0.5R"
        if float(row["mfe_r"]) < 1.5:
            return "SL after 0.5R, before 1.5R"
        return "wick >=1.5R, no close protection"
    if outcome.startswith(("protected", "dynamic")):
        return "protected / dynamic stop"
    if outcome == "tp":
        return "3R target"
    return "timeout"


def failure_contributions(ledgers: dict[str, pd.DataFrame], baseline_label: str) -> pd.DataFrame:
    baseline = ledgers[baseline_label][["setup_id", "net_return", "outcome", "mfe_r"]].copy()
    baseline["failure_path"] = baseline.apply(baseline_failure_bucket, axis=1)
    rows: list[dict[str, Any]] = []
    total = len(baseline)
    for path, group in baseline.groupby("failure_path", sort=False):
        keys = set(group["setup_id"])
        base_mean = float(group["net_return"].mean() * 1e4)
        base_contribution = float(group["net_return"].sum() * 1e4 / total)
        for label, ledger in ledgers.items():
            subset = ledger.loc[ledger["setup_id"].isin(keys)]
            policy_mean = float(subset["net_return"].mean() * 1e4)
            rows.append(
                {
                    "failure_path": path,
                    "events": len(group),
                    "share": len(group) / total,
                    "stop_policy": label,
                    "baseline_group_mean_net_bp": base_mean,
                    "baseline_contribution_to_all_mean_bp": base_contribution,
                    "policy_group_mean_net_bp": policy_mean,
                    "policy_improvement_within_group_bp": policy_mean - base_mean,
                    "policy_improvement_contribution_to_all_mean_bp": float(
                        (subset["net_return"].sum() - group["net_return"].sum())
                        * 1e4
                        / total
                    ),
                }
            )
    return pd.DataFrame(rows)


def stop_bar_diagnostics(
    baseline: pd.DataFrame, frame: pd.DataFrame, config: dict[str, Any]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    horizon = int(config["execution_frozen"]["horizon_bars"])
    stopped = baseline.loc[baseline["outcome"].astype(str).str.startswith("sl")]
    for event in stopped.to_dict("records"):
        i = int(event["exit_i"])
        direction = int(event["direction"])
        entry = float(event["entry_price"])
        stop = float(event["stop_price"])
        risk = float(event["risk_price"])
        close = float(frame.loc[i, "close"])
        sma = float(frame.loc[i, "sma40_hl2"])
        reclaimed_stop = direction * (close - stop) > 0.0
        intended_sma_side = direction * (close - sma) >= 0.0
        future_first = i + 1
        future_last = int(event["entry_i"]) + horizon - 1
        if future_first <= future_last:
            future = frame.loc[future_first:future_last]
            post_favourable = (
                future["high"].max() - entry
                if direction > 0
                else entry - future["low"].min()
            )
            post_adverse = (
                entry - future["low"].min()
                if direction > 0
                else future["high"].max() - entry
            )
        else:
            post_favourable = math.nan
            post_adverse = math.nan
        rows.append(
            {
                "setup_id": event["setup_id"],
                "entry_time": event["entry_time"],
                "direction": direction,
                "stop_bar_i": i,
                "bars_to_stop": int(event["hold_bars"]),
                "same_entry_bar": int(event["hold_bars"]) == 1,
                "within_two_bars": int(event["hold_bars"]) <= 2,
                "baseline_mfe_r": event["mfe_r"],
                "baseline_mae_r": event["mae_r"],
                "stop_bar_close_reclaimed_original_stop": reclaimed_stop,
                "stop_bar_close_on_intended_sma40_side": intended_sma_side,
                "valid_soft_reclaim_close": reclaimed_stop and intended_sma_side,
                "close_through_original_stop": not reclaimed_stop,
                "close_lost_intended_sma40_side": not intended_sma_side,
                "post_exit_max_favourable_r": float(post_favourable / risk)
                if np.isfinite(post_favourable)
                else np.nan,
                "post_exit_max_adverse_r": float(post_adverse / risk)
                if np.isfinite(post_adverse)
                else np.nan,
                "post_exit_hit_1r": bool(post_favourable >= risk)
                if np.isfinite(post_favourable)
                else False,
                "post_exit_hit_3r": bool(post_favourable >= 3.0 * risk)
                if np.isfinite(post_favourable)
                else False,
            }
        )
    return pd.DataFrame(rows)


def oracle_ceiling(baseline: pd.DataFrame) -> pd.DataFrame:
    work = baseline.copy()
    work["failure_path"] = work.apply(baseline_failure_bucket, axis=1)
    base = float(work["net_return"].mean() * 1e4)
    rows = [{"counterfactual": "observed baseline", "mean_net_bp": base, "improvement_bp": 0.0}]
    groups: list[tuple[str, Iterable[str]]] = [
        ("perfectly flatten SL before 0.5R", ["SL before 0.5R"]),
        (
            "perfectly flatten every baseline SL",
            [
                "SL before 0.5R",
                "SL after 0.5R, before 1.5R",
                "wick >=1.5R, no close protection",
            ],
        ),
    ]
    for label, paths in groups:
        values = work["net_return"].copy()
        values.loc[work["failure_path"].isin(paths)] = 0.0
        result = float(values.mean() * 1e4)
        rows.append(
            {"counterfactual": label, "mean_net_bp": result, "improvement_bp": result - base}
        )
    return pd.DataFrame(rows)


def attach_classifier_features(events: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """Attach only values available by the confirmation close or entry open.

    Rolling inputs use frame columns whose windows end at ``confirmation_i``.
    ``directional_sma_slope_6_atr`` is direction times the six-bar SMA change
    divided by confirmation ATR14.  ``entry_gap_directional_atr`` uses the
    already-known next-open entry minus the completed confirmation close.
    """

    out = events.copy()
    index = out["confirmation_i"].astype(int).to_numpy()
    direction = out["direction"].astype(float).to_numpy()
    atr = frame.loc[index, "atr"].to_numpy(dtype=float)
    sma = frame.loc[index, "sma40_hl2"].to_numpy(dtype=float)
    prior_sma = frame.loc[index - 6, "sma40_hl2"].to_numpy(dtype=float)
    confirm_close = frame.loc[index, "close"].to_numpy(dtype=float)
    out["confirmation_volume_ratio_20"] = frame.loc[
        index, "volume_ratio_20"
    ].to_numpy(dtype=float)
    out["confirmation_atr_release_24"] = frame.loc[
        index, "atr_release_24"
    ].to_numpy(dtype=float)
    out["directional_sma_slope_6_atr"] = direction * (sma - prior_sma) / atr
    out["entry_gap_directional_atr"] = (
        direction * (out["entry_price"].to_numpy(dtype=float) - confirm_close) / atr
    )
    out["early_failure"] = out.apply(
        lambda row: str(row["outcome"]).startswith("sl") and float(row["mfe_r"]) < 0.5,
        axis=1,
    ).astype(int)
    return out


def _classifier_pipeline(name: str, feature_count: int) -> Any:
    preprocess = ColumnTransformer(
        [("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), list(range(feature_count)))],
        remainder="drop",
    )
    if name == "logistic_l2":
        model: Any = LogisticRegression(
            C=1.0,
            class_weight="balanced",
            max_iter=2000,
            random_state=20260904,
            solver="liblinear",
        )
    elif name == "tree_depth2":
        model = DecisionTreeClassifier(
            max_depth=2,
            min_samples_leaf=10,
            class_weight="balanced",
            random_state=20260904,
        )
    elif name == "training_prior_dummy":
        model = DummyClassifier(strategy="prior", random_state=20260904)
    else:
        raise ValueError(name)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def walkforward_classifier(
    baseline: pd.DataFrame, frame: pd.DataFrame, config: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    enriched = attach_classifier_features(baseline, frame)
    features = list(config["failure_diagnostics"]["causal_classifier"]["features"])
    X = enriched[features].replace([np.inf, -np.inf], np.nan)
    y = enriched["early_failure"].astype(int)
    stamps = pd.to_datetime(enriched["entry_time"], utc=True)
    folds = [
        ("2023H2", utc("2023-07-01"), utc("2024-01-01")),
        ("2024H1", utc("2024-01-01"), utc("2024-07-01")),
        ("2024H2", utc("2024-07-01"), utc("2025-01-01")),
    ]
    predictions: list[dict[str, Any]] = []
    models = ["logistic_l2", "tree_depth2", "training_prior_dummy"]
    for fold, test_start, test_end in folds:
        train = stamps < test_start
        test = (stamps >= test_start) & (stamps < test_end)
        for name in models:
            model = _classifier_pipeline(name, len(features))
            model.fit(X.loc[train].to_numpy(dtype=float), y.loc[train].to_numpy(dtype=int))
            probability = model.predict_proba(X.loc[test].to_numpy(dtype=float))[:, 1]
            for row_index, prob in zip(enriched.index[test], probability):
                predictions.append(
                    {
                        "model": name,
                        "test_fold": fold,
                        "setup_id": enriched.loc[row_index, "setup_id"],
                        "entry_time": enriched.loc[row_index, "entry_time"],
                        "early_failure": int(y.loc[row_index]),
                        "predicted_early_failure_probability": float(prob),
                        "training_events": int(train.sum()),
                        "training_early_failures": int(y.loc[train].sum()),
                    }
                )
    pred = pd.DataFrame(predictions)
    metrics: list[dict[str, Any]] = []
    for name, group in pred.groupby("model", sort=False):
        truth = group["early_failure"].to_numpy(dtype=int)
        probability = group["predicted_early_failure_probability"].to_numpy(dtype=float)
        top_n = max(1, math.ceil(0.30 * len(group)))
        top = group.nlargest(top_n, "predicted_early_failure_probability")
        metrics.append(
            {
                "model": name,
                "test_events": len(group),
                "early_failure_prevalence": float(truth.mean()),
                "roc_auc": float(roc_auc_score(truth, probability))
                if len(np.unique(truth)) == 2
                else np.nan,
                "brier_score": float(brier_score_loss(truth, probability)),
                "top_30pct_events": top_n,
                "top_30pct_early_failure_rate": float(top["early_failure"].mean()),
                "lift_vs_prevalence": float(top["early_failure"].mean() / truth.mean())
                if truth.mean() > 0.0
                else np.nan,
            }
        )
    metric_table = pd.DataFrame(metrics)

    coefficient_rows: list[dict[str, Any]] = []
    full_model = _classifier_pipeline("logistic_l2", len(features))
    full_model.fit(X.to_numpy(dtype=float), y.to_numpy(dtype=int))
    coefficients = full_model.named_steps["model"].coef_[0]
    for feature, value in zip(features, coefficients):
        coefficient_rows.append(
            {
                "feature": feature,
                "standardized_logistic_coefficient": float(value),
                "absolute_coefficient": abs(float(value)),
                "analysis_status": "descriptive_full_development_fit_not_rule_selection",
            }
        )
    coefficient_table = pd.DataFrame(coefficient_rows).sort_values(
        ["absolute_coefficient", "feature"], ascending=[False, True]
    )
    return pred, metric_table, coefficient_table


def _all_halfyears_gross_positive(events: pd.DataFrame, folds: list[str]) -> bool:
    table = fold_table(events, folds)
    return bool(table["mean_gross_bp"].gt(0.0).all())


def select_policy(
    metrics: pd.DataFrame, familywise: pd.DataFrame, config: dict[str, Any]
) -> tuple[str, str]:
    baseline_label = str(config["factor"]["baseline_label"])
    baseline = metrics.loc[metrics["stop_policy"].eq(baseline_label)].iloc[0]
    joined = metrics.merge(familywise, on="stop_policy", how="left")
    passing = joined.loc[
        joined["stop_policy"].ne(baseline_label)
        & joined["eligible"].astype(bool)
        & joined["mean_net_bp"].gt(0.0)
        & joined["robust_score_bp"].gt(0.0)
        & joined["worst_fold_net_bp"].gt(-5.0)
        & joined["all_halfyears_gross_positive"].astype(bool)
        & joined["robust_score_bp"].ge(float(baseline["robust_score_bp"]) + 5.0)
        & joined["matched_control_excess_bp"].gt(0.0)
        & joined["paired_signflip_p_one_sided"].lt(0.01)
        & joined["familywise_signflip_p_one_sided"].lt(0.05)
    ].copy()
    if passing.empty:
        return baseline_label, "retain_baseline_no_candidate_passed_every_registered_gate"
    passing = passing.sort_values(
        ["robust_score_bp", "worst_fold_net_bp", "mean_net_bp", "stop_policy"],
        ascending=[False, False, False, True],
        kind="mergesort",
    )
    return str(passing.iloc[0]["stop_policy"]), "candidate_passed_every_registered_gate"


def make_charts(
    metrics: pd.DataFrame,
    folds: pd.DataFrame,
    contributions: pd.DataFrame,
    selected_label: str,
) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    ordered = metrics.sort_values("mean_net_bp")
    y = np.arange(len(ordered))
    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    ax.barh(y - 0.18, ordered["mean_net_bp"], height=0.34, color=BLUE, label="mean net")
    ax.barh(y + 0.18, ordered["robust_score_bp"], height=0.34, color=BLUE_LIGHT, edgecolor=BLUE, label="robust score")
    ax.axvline(0.0, color=INK, linewidth=0.9)
    ax.set_yticks(y, ordered["stop_policy"])
    ax.set_xlabel("bp per trade")
    ax.set_title("BTCUSDT.P 15m stop-manager performance")
    ax.text(0.0, 1.01, "2023-2024 development; identical 100-entry cohort; 20bp round-trip cost", transform=ax.transAxes, color="#4B5563")
    ax.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.65)
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(RESULTS / "chart_stop_policy_performance.png", dpi=180)
    plt.close(fig)

    matrix = folds.pivot(index="stop_policy", columns="fold", values="mean_net_bp")
    matrix = matrix.loc[metrics.sort_values("mean_net_bp", ascending=False)["stop_policy"]]
    limit = float(np.nanmax(np.abs(matrix.to_numpy(dtype=float))))
    fig, ax = plt.subplots(figsize=(9.8, 6.4))
    image = ax.imshow(matrix, cmap="PuOr_r", vmin=-limit, vmax=limit, aspect="auto")
    ax.set_xticks(np.arange(len(matrix.columns)), matrix.columns)
    ax.set_yticks(np.arange(len(matrix.index)), matrix.index)
    for i in range(len(matrix.index)):
        for j in range(len(matrix.columns)):
            value = float(matrix.iloc[i, j])
            ax.text(j, i, f"{value:+.1f}", ha="center", va="center", color=INK, fontsize=9)
    ax.set_title("BTCUSDT.P 15m stop-manager half-year net expectancy")
    ax.text(0.0, 1.01, "bp per trade; chronological folds; orange is negative and purple-blue is positive", transform=ax.transAxes, color="#4B5563")
    fig.colorbar(image, ax=ax, shrink=0.78, label="net bp / trade")
    fig.tight_layout()
    fig.savefig(RESULTS / "chart_policy_halfyear_heatmap.png", dpi=180)
    plt.close(fig)

    base = contributions.loc[
        contributions["stop_policy"].eq(str(metrics.iloc[0]["stop_policy"]))
    ]
    # The baseline contribution is identical on every policy row; select one copy.
    base = contributions.drop_duplicates("failure_path")
    candidate = contributions.loc[contributions["stop_policy"].eq(selected_label)]
    order = base.sort_values("baseline_contribution_to_all_mean_bp")["failure_path"]
    base = base.set_index("failure_path").loc[order].reset_index()
    candidate = candidate.set_index("failure_path").loc[order].reset_index()
    y = np.arange(len(order))
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 6.2), sharey=True)
    axes[0].barh(y, base["baseline_contribution_to_all_mean_bp"], color=ORANGE, edgecolor="#9A5A05")
    axes[0].axvline(0.0, color=INK, linewidth=0.8)
    axes[0].set_yticks(y, order)
    axes[0].set_xlabel("contribution to all-trade mean, bp")
    axes[0].set_title("Baseline loss contribution")
    axes[1].barh(y, candidate["policy_improvement_contribution_to_all_mean_bp"], color=BLUE_LIGHT, edgecolor=BLUE)
    axes[1].axvline(0.0, color=INK, linewidth=0.8)
    axes[1].set_xlabel("improvement versus baseline, bp")
    axes[1].set_title(f"{selected_label} path effect")
    for ax in axes:
        ax.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.65)
    fig.suptitle("BTCUSDT.P 15m failure-path contribution and attempted rescue")
    fig.text(0.5, 0.01, "2023-2024; path labels are defined by the baseline outcome; signed values reconcile to each policy mean", ha="center", color="#4B5563")
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 0.95))
    fig.savefig(RESULTS / "chart_failure_contribution_and_rescue.png", dpi=180)
    plt.close(fig)


def development_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    folds = list(config["window"]["development_folds"])
    frame, quality = load_featured(config, BAR)
    decisions = load_frozen_decisions(config, frame, start, end)
    arms = list(config["factor"]["arms"])
    baseline_label = str(config["factor"]["baseline_label"])

    ledgers: dict[str, pd.DataFrame] = {}
    for arm in arms:
        label = str(arm["label"])
        events = run_policy(decisions, frame, config, arm)
        ledgers[label] = events
        write_csv(events, RESULTS / f"development_{label}_trades.csv.gz")
        print(f"[{label}] net={events['net_return'].mean()*1e4:+.2f}bp", flush=True)

    reference = pd.read_csv(PROJECT / config["predecessor"]["reference_trade_ledger_path"])
    reference = reference.sort_values("setup_id").reset_index(drop=True)
    baseline = ledgers[baseline_label].sort_values("setup_id").reset_index(drop=True)
    if not reference["setup_id"].equals(baseline["setup_id"]):
        raise RuntimeError("baseline setup keys differ from predecessor reference")
    max_net_error = float(
        np.max(np.abs(reference["net_return"].to_numpy(dtype=float) - baseline["net_return"].to_numpy(dtype=float)))
    )
    max_exit_error = float(
        np.max(np.abs(reference["exit_price"].to_numpy(dtype=float) - baseline["exit_price"].to_numpy(dtype=float)))
    )
    outcome_mismatches = int(
        reference["outcome"].astype(str).ne(baseline["outcome"].astype(str)).sum()
    )
    if max_net_error > 1e-12 or max_exit_error > 1e-9 or outcome_mismatches:
        raise RuntimeError(
            f"baseline parity failed: net={max_net_error} exit={max_exit_error} outcomes={outcome_mismatches}"
        )

    control_specs, matches = build_control_specs(
        baseline,
        frame,
        config,
        start,
        end,
        predecessor_signal_indices(config, frame),
    )
    write_csv(control_specs, RESULTS / "development_control_specs.csv.gz")
    write_csv(matches, RESULTS / "development_control_matches.csv")
    metric_rows: list[dict[str, Any]] = []
    fold_rows: list[pd.DataFrame] = []
    for arm in arms:
        label = str(arm["label"])
        events = ledgers[label]
        controls, pairs = resolve_controls(
            control_specs, events, matches, frame, config, arm
        )
        write_csv(controls, RESULTS / f"development_{label}_matched_controls.csv.gz")
        write_csv(pairs, RESULTS / f"development_{label}_matched_pairs.csv")
        metrics = robust_metrics(
            events,
            folds,
            int(config["development_gate"]["minimum_events_total"]),
            int(config["development_gate"]["minimum_events_per_fold"]),
        )
        metrics = {
            "stop_policy": label,
            **metrics,
            "all_halfyears_gross_positive": _all_halfyears_gross_positive(events, folds),
            **add_control_metrics({}, pairs),
            **ranking_metrics(events, resamples=20_000),
        }
        metric_rows.append(metrics)
        fold = fold_table(events, folds)
        fold.insert(0, "stop_policy", label)
        fold_rows.append(fold)

    metrics_table = pd.DataFrame(metric_rows)
    folds_table = pd.concat(fold_rows, ignore_index=True)
    familywise = paired_familywise_signflip(ledgers, config)
    selected_label, selection_reason = select_policy(metrics_table, familywise, config)
    best_observed = str(
        metrics_table.sort_values(
            ["robust_score_bp", "worst_fold_net_bp", "mean_net_bp", "stop_policy"],
            ascending=[False, False, False, True],
            kind="mergesort",
        ).iloc[0]["stop_policy"]
    )

    contributions = failure_contributions(ledgers, baseline_label)
    stop_bars = stop_bar_diagnostics(ledgers[baseline_label], frame, config)
    ceilings = oracle_ceiling(ledgers[baseline_label])
    predictions, classifier_metrics, coefficients = walkforward_classifier(
        ledgers[baseline_label], frame, config
    )
    make_charts(metrics_table, folds_table, contributions, best_observed)

    write_csv(metrics_table, RESULTS / "development_policy_metrics.csv")
    write_csv(folds_table, RESULTS / "development_policy_folds.csv")
    write_csv(familywise, RESULTS / "development_policy_familywise_tests.csv")
    write_csv(contributions, RESULTS / "development_failure_contributions.csv")
    write_csv(stop_bars, RESULTS / "development_baseline_stop_bar_diagnostics.csv.gz")
    write_csv(ceilings, RESULTS / "development_oracle_ceiling.csv")
    write_csv(predictions, RESULTS / "development_early_failure_predictions.csv.gz")
    write_csv(classifier_metrics, RESULTS / "development_early_failure_classifier_metrics.csv")
    write_csv(coefficients, RESULTS / "development_early_failure_logistic_coefficients.csv")
    write_csv(pd.DataFrame([{**quality, "audit_rows_read": 0, "holdout_rows_read": 0}]), RESULTS / "source_receipt.csv")

    baseline_metrics = metrics_table.loc[
        metrics_table["stop_policy"].eq(baseline_label)
    ].iloc[0].to_dict()
    best_metrics = metrics_table.loc[
        metrics_table["stop_policy"].eq(best_observed)
    ].iloc[0].to_dict()
    selected_metrics = metrics_table.loc[
        metrics_table["stop_policy"].eq(selected_label)
    ].iloc[0].to_dict()
    candidate_passed = selected_label != baseline_label
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "development_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "predecessor_baseline_parity": {
            "entries": len(baseline),
            "setup_keys_exact": True,
            "net_return_max_abs_error": max_net_error,
            "exit_price_max_abs_error": max_exit_error,
            "outcome_mismatches": outcome_mismatches,
        },
        "source": {**quality, "audit_rows_read": 0, "holdout_rows_read": 0},
        "policy_count": len(arms),
        "baseline_metrics": baseline_metrics,
        "best_observed_policy": best_observed,
        "best_observed_metrics": best_metrics,
        "selected_policy": selected_label,
        "selected_metrics": selected_metrics,
        "selection_reason": selection_reason,
        "development_gate_passed": candidate_passed,
        "audit_open_allowed": candidate_passed,
        "audit_rows_read": 0,
        "holdout_rows_read": 0,
        "matched_events_each_policy": int(metrics_table["matched_events"].min()),
        "familywise_tests": familywise.to_dict("records"),
        "baseline_stop_diagnostics": {
            "stops": len(stop_bars),
            "same_entry_bar": int(stop_bars["same_entry_bar"].sum()),
            "within_two_bars": int(stop_bars["within_two_bars"].sum()),
            "valid_soft_reclaim_close": int(stop_bars["valid_soft_reclaim_close"].sum()),
            "close_through_original_stop": int(stop_bars["close_through_original_stop"].sum()),
            "close_lost_intended_sma40_side": int(stop_bars["close_lost_intended_sma40_side"].sum()),
            "post_exit_hit_1r": int(stop_bars["post_exit_hit_1r"].sum()),
            "post_exit_hit_3r": int(stop_bars["post_exit_hit_3r"].sum()),
        },
        "early_failure_classifier": classifier_metrics.to_dict("records"),
        "tradingview_replacement_allowed": False,
        "production_mutation_allowed": False,
        "live_orders_placed": 0,
    }
    write_json(RESULTS / "development_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["development"], default="development")
    args = parser.parse_args()
    if args.phase == "development":
        development_phase(load_config())


if __name__ == "__main__":
    main()
