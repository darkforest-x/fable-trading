#!/usr/bin/env python3
"""Select and audit decaying candle-color profit harvest for ETHUSDT.P 15m.

Excursions at +2/+4/+8/+12 signal ATR earn release slots. Adverse candle
colours release those slots at the next open using a frozen 4:2:1:1 size shape.
Selection changes only the maximum bank budget on 2023--2024. Profit releases
never change the disaster or SMA60/ATR runner stop. Repository holdout rows are
never parsed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import BAR_DELTA, _atr_buckets
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    utc,
    write_csv,
    write_json,
)
from scripts.research_ethusdtp_15m_bank_only_runner_v4 import _common_result
from scripts.research_ethusdtp_15m_color_harvest_v9 import color_exit_frame
from scripts.research_ethusdtp_15m_progressive_scaleout import (
    build_frozen_setups,
    load_eth_frame,
    resolve_baseline,
)
from scripts.research_ethusdtp_15m_progressive_scaleout_v2 import (
    fold_table,
    robust_summary,
    window,
)
from scripts.research_ethusdtp_15m_weakness_harvest_v7 import (
    _passes,
    resolve_weakness_harvest,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-decay-color-harvest-preholdout-20260905-v10"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _assert_head_frozen(path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    expected = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(expected).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_selection_committed() -> dict[str, Any]:
    _assert_head_frozen(SELECTION_PATH)
    receipt = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    if receipt.get("status") != "frozen_for_audit" or not receipt.get(
        "all_registered_gates_pass"
    ):
        raise RuntimeError(
            "selection is not committed or did not pass all audit-opening gates"
        )
    return receipt


def stage_fractions(
    config: Mapping[str, Any], bank_total_fraction: float
) -> list[float]:
    """Scale the frozen 4:2:1:1 release shape by the selected bank budget."""

    if not 0.0 < bank_total_fraction < 1.0:
        raise ValueError("bank_total_fraction must be strictly between zero and one")
    weights = np.asarray(
        config["decay_color_harvest"]["normalized_stage_weights"], dtype=float
    )
    if (
        len(weights) != 4
        or np.any(weights <= 0.0)
        or not np.isclose(weights.sum(), 1.0)
    ):
        raise ValueError(
            "normalized stage weights must contain four positive values summing to one"
        )
    return (bank_total_fraction * weights).tolist()


def resolve_decay_color_harvest(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    bank_total_fraction: float,
) -> dict[str, Any]:
    """Reweight a causal color-release path without changing its stop path.

    ``frame.reference_ma`` is an exit-only copy of current open, so the reused
    release predicate depends only on the completed candle body. The temporary
    equal fraction affects no trigger or stop state. Gross return is rebuilt
    from actual release opens and the unchanged final runner exit.
    """

    base = resolve_weakness_harvest(frame, event, config, fraction_per_stage=0.1)
    if not base.get("resolved"):
        return dict(base)
    fractions = stage_fractions(config, bank_total_fraction)
    prices = list(map(float, json.loads(str(base["release_prices_json"]))))
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    realized = sum(
        fractions[index] * direction * (price / entry - 1.0)
        for index, price in enumerate(prices)
    )
    released = sum(fractions[: len(prices)])
    remaining = 1.0 - released
    remainder_return = direction * (float(base["exit_price"]) / entry - 1.0)
    gross = realized + remaining * remainder_return
    signal_atr = float(event["signal_atr"])
    captured_atr = gross * entry / signal_atr
    result = {
        **base,
        "policy": "decay_adverse_color_harvest_sma60_runner",
        "gross_return": gross,
        "bank_total_fraction": bank_total_fraction,
        "fraction_per_stage": np.nan,
        "stage_fractions_json": json.dumps(fractions),
        "banked_gross_return": realized,
        "remaining_fraction": remaining,
        "capture_of_horizon_mfe": (
            captured_atr / float(base["horizon_mfe_atr"])
            if float(base["horizon_mfe_atr"]) > 0.0
            else np.nan
        ),
        "capture_of_exit_mfe": (
            captured_atr / float(base["mfe_at_exit_atr"])
            if float(base["mfe_at_exit_atr"]) > 0.0
            else np.nan
        ),
        "gave_back_atr": float(base["mfe_at_exit_atr"]) - captured_atr,
    }
    return _common_result(
        event, result, float(config["frozen_execution"]["round_trip_cost_fraction"])
    )


def replay(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    bank_total_fraction: float | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in setups.to_dict("records"):
        result = (
            resolve_baseline(frame, event, config)
            if bank_total_fraction is None
            else resolve_decay_color_harvest(
                frame, event, config, bank_total_fraction=bank_total_fraction
            )
        )
        if result.get("resolved"):
            rows.append(result)
    return pd.DataFrame(rows)


def _candidate_row(
    events: pd.DataFrame,
    baseline_summary: Mapping[str, Any],
    folds: list[str],
    config: Mapping[str, Any],
    bank: float,
) -> dict[str, Any]:
    summary = robust_summary(events, folds, config)
    baseline_loss = float(baseline_summary["runner_armed_to_nonpositive_share"])
    candidate_loss = float(summary["runner_armed_to_nonpositive_share"])
    reduction = (
        (baseline_loss - candidate_loss) / baseline_loss
        if baseline_loss > 0.0
        else np.nan
    )
    return {
        "maximum_bank_fraction": bank,
        "stage_fractions_json": events["stage_fractions_json"].iloc[0],
        **summary,
        "runner_loss_relative_reduction": reduction,
        "mean_net_delta_bp": float(
            summary["mean_net_bp"] - baseline_summary["mean_net_bp"]
        ),
        "worst_fold_degradation_bp": float(
            baseline_summary["worst_fold_net_bp"] - summary["worst_fold_net_bp"]
        ),
        "p95_net_retention": float(
            summary["p95_net_bp"] / baseline_summary["p95_net_bp"]
        ),
    }


def selection_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    split = config["splits"]
    frame, quality = load_eth_frame(
        config, end_exclusive=utc(split["development_end_exclusive"])
    )
    pairs, setups = build_frozen_setups(frame, config)
    setups = window(
        setups,
        split["development_start_inclusive"],
        split["development_end_exclusive"],
    )
    exit_frame = color_exit_frame(frame)
    baseline = replay(setups, exit_frame, config, bank_total_fraction=None)
    folds = list(map(str, split["development_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    rows: list[dict[str, Any]] = []
    ledgers: dict[float, pd.DataFrame] = {}
    for bank in map(float, config["selection"]["maximum_bank_fraction_candidates"]):
        events = replay(setups, exit_frame, config, bank_total_fraction=bank)
        ledgers[bank] = events
        rows.append(_candidate_row(events, baseline_summary, folds, config, bank))
    passing = [row for row in rows if _passes(row, config)]
    winner = (
        max(
            passing,
            key=lambda row: (
                float(row["robust_score_bp"]),
                -float(row["maximum_bank_fraction"]),
            ),
        )
        if passing
        else max(rows, key=lambda row: float(row["robust_score_bp"]))
    )
    bank = float(winner["maximum_bank_fraction"])
    selected = ledgers[bank]
    selected_summary = robust_summary(selected, folds, config)
    all_pass = _passes(winner, config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "selection_raw_pairs.csv.gz")
    write_csv(baseline, RESULTS / "selection_baseline_trades.csv.gz")
    write_csv(selected, RESULTS / "selection_candidate_trades.csv.gz")
    write_csv(pd.DataFrame(rows), RESULTS / "selection_bank_grid.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "selection_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(selected, folds).assign(policy="decay_color_harvest_v10"),
        RESULTS / "selection_candidate_fold_metrics.csv",
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_params": {
            "maximum_bank_fraction": bank,
            "stage_fractions": json.loads(str(winner["stage_fractions_json"])),
            "earned_slot_levels_atr": config["decay_color_harvest"][
                "earned_slot_levels_atr"
            ],
        },
        "source": quality,
        "raw_pairs": len(pairs),
        "frozen_setups": len(setups),
        "baseline": baseline_summary,
        "candidate": selected_summary,
        "selected_comparison": winner,
        "all_registered_gates_pass": all_pass,
        "audit_rows_read": 0,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(RESULTS / "selection_bank_grid.csv"),
        },
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def _matched_controls(
    candidate: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    bank: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    horizon = int(config["frozen_execution"]["horizon_bars"])
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= utc(frame.loc[entry_i, "open_time"]) < end
            and utc(frame.loc[last_i, "open_time"] + BAR_DELTA) <= end
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
            and np.isfinite(float(frame.loc[signal_i, "reference_ma"]))
            and np.isfinite(float(frame.loc[signal_i, "trend_ma"]))
        )
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    signals = set(candidate["signal_i"].astype(int))
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & (buckets >= 0)):
        if int(index) not in signals:
            pool.setdefault(
                (str(months[index]), int(blocks[index]), int(buckets[index])), []
            ).append(int(index))
    match = config["matched_control"]
    required = int(match["controls_per_event"])
    radius = int(match["exclude_radius_bars"])
    seed = str(match["seed"])
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in candidate.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            (index for index in pool.get(key, []) if abs(index - signal_i) > radius),
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        values: list[float] = []
        for assignment, control_i in enumerate(choices[:required]):
            control_event = {
                "setup_id": f"control-{control_i}",
                "signal_i": control_i,
                "entry_i": control_i + 1,
                "entry_time": frame.loc[control_i + 1, "open_time"],
                "entry_price": float(frame.loc[control_i + 1, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[control_i, "atr"]),
            }
            result = resolve_decay_color_harvest(
                frame, control_event, config, bank_total_fraction=bank
            )
            if not result.get("resolved"):
                continue
            values.append(float(result["net_return"]))
            controls.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "control_i": control_i,
                    "control_time": frame.loc[control_i, "open_time"],
                    "direction": int(event["direction"]),
                    "calendar_month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    "net_return": result["net_return"],
                }
            )
        if len(values) == required:
            mean = float(np.mean(values))
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "matched_exact",
                    "matched_control_count": required,
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": mean,
                    "paired_excess_return": event["net_return"] - mean,
                }
            )
        else:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched",
                    "matched_control_count": len(values),
                }
            )
    return pd.DataFrame(controls), pd.DataFrame(pairs)


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    selection = _assert_selection_committed()
    bank = float(selection["selected_params"]["maximum_bank_fraction"])
    split = config["splits"]
    start = utc(split["audit_start_inclusive"])
    end = utc(split["audit_end_exclusive"])
    frame, quality = load_eth_frame(config, end_exclusive=end)
    _, setups = build_frozen_setups(frame, config)
    setups = window(setups, start, end)
    exit_frame = color_exit_frame(frame)
    baseline = replay(setups, exit_frame, config, bank_total_fraction=None)
    candidate = replay(setups, exit_frame, config, bank_total_fraction=bank)
    folds = list(map(str, split["audit_folds"]))
    baseline_summary = robust_summary(baseline, folds, config)
    candidate_summary = robust_summary(candidate, folds, config)
    comparison = candidate[["setup_id", "net_return"]].merge(
        baseline[["setup_id", "net_return"]],
        on="setup_id",
        suffixes=("_candidate", "_baseline"),
    )
    comparison["delta"] = (
        comparison["net_return_candidate"] - comparison["net_return_baseline"]
    )
    controls, matched_pairs = _matched_controls(
        candidate, exit_frame, config, bank=bank, start=start, end=end
    )
    matched = matched_pairs[matched_pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "audit",
        "status": "research_only",
        "selected_params": selection["selected_params"],
        "source": quality,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "setups": len(setups),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "paired_candidate_minus_baseline": {
            "mean_delta_bp": float(comparison["delta"].mean() * 1e4),
            "median_delta_bp": float(comparison["delta"].median() * 1e4),
            "positive_delta_share": float(comparison["delta"].gt(0.0).mean()),
            "signflip_p": float(
                signflip_p(comparison["delta"], resamples=100_000, seed=90581)
            ),
        },
        "matched_random": {
            "matched_events": len(matched),
            "candidate_mean_net_bp": (
                float(matched["candidate_net_return"].mean() * 1e4)
                if len(matched)
                else np.nan
            ),
            "control_mean_net_bp": (
                float(matched["control_mean_net_return"].mean() * 1e4)
                if len(matched)
                else np.nan
            ),
            "excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
            "signflip_p": (
                float(signflip_p(excess, resamples=100_000, seed=90582))
                if len(excess)
                else np.nan
            ),
        },
        "production_or_live_changed": False,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(baseline, RESULTS / "audit_baseline_trades.csv.gz")
    write_csv(candidate, RESULTS / "audit_candidate_trades.csv.gz")
    write_csv(comparison, RESULTS / "audit_paired_exit_deltas.csv")
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(matched_pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(
        fold_table(baseline, folds).assign(policy="baseline"),
        RESULTS / "audit_baseline_fold_metrics.csv",
    )
    write_csv(
        fold_table(candidate, folds).assign(policy="decay_color_harvest_v10"),
        RESULTS / "audit_candidate_fold_metrics.csv",
    )
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("selection", "audit"))
    args = parser.parse_args()
    config = load_config()
    if args.phase == "selection":
        selection_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
