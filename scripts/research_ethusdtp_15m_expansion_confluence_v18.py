#!/usr/bin/env python3
"""Freeze and transport-test one ETHUSDT.P 15m expansion gate.

The single candidate uses only the completed K2 bar and earlier:
``min(ATR14 / prior-96 median ATR14, BB20 width / prior-96 median width)
>= 0.85``.  Entry events and all V16 execution fields are unchanged.

The 2023--2024 development window was already inspected in V17, so its result
is a hypothesis-freeze receipt rather than validation.  The 2025-through-
February-2026 phase is also known parent lineage and is labeled diagnostic.
Repository holdout begins 2026-05-04 and is never parsed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_ethusdtp_15m_bank_only_runner_v4 import _matched_controls
from scripts.research_ethusdtp_15m_causal_confluence_v17 import (
    _causality_receipt,
    _failure_table,
    _familywise_permutation_p,
    _fold_table,
    _tail_retention,
    build_feature_ledger,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-expansion-confluence-preholdout-20260905-v18"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_RECEIPT = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PARENT_V16_CONFIG = (
    ROOT
    / "experiments/active"
    / "exp-ethusdtp-15m-micro-profit-ladder-preholdout-20260905-v16"
    / "config.json"
)


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
    committed = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(committed).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_lineage(config: Mapping[str, Any]) -> dict[str, Any]:
    lineage = config["lineage"]
    for name in ("v17_script", "v17_config", "v17_selection_receipt"):
        path = ROOT / str(lineage[f"{name}_path"])
        _assert_head_frozen(path)
        if sha256_file(path) != str(lineage[f"{name}_sha256"]):
            raise RuntimeError(f"{name} lineage hash drift")
    return json.loads(
        (ROOT / str(lineage["v17_config_path"])).read_text(encoding="utf-8")
    )


def _assert_selection_committed() -> dict[str, Any]:
    _assert_head_frozen(SELECTION_RECEIPT)
    receipt = json.loads(SELECTION_RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("status") != "frozen_for_diagnostic_audit" or not receipt.get(
        "all_freeze_gates_pass"
    ):
        raise RuntimeError("V18 selection freeze did not pass or is not committed")
    return receipt


def _add_expansion_scores(events: pd.DataFrame) -> pd.DataFrame:
    output = events.copy()
    atr = output["eth_atr_ratio96"].astype(float)
    width = output["eth_bb_width_ratio96"].astype(float)
    output["expansion_floor"] = np.minimum(atr, width)
    output["expansion_geometric"] = np.sqrt(np.maximum(atr * width, 0.0))
    return output


def _score_diagnostic(events: pd.DataFrame, score_column: str) -> dict[str, Any]:
    target = events["net_return"].gt(0.0).astype(int)
    score = events[score_column].astype(float)
    auc = (
        float(roc_auc_score(target, score))
        if target.nunique() == 2 and score.nunique() > 1
        else np.nan
    )
    count = max(1, math.ceil(0.10 * len(events)))
    top = events.sort_values([score_column, "setup_id"], ascending=[False, True]).head(
        count
    )
    return {
        "score_column": score_column,
        "auc_profit": auc,
        "top_decile_events": len(top),
        "top_decile_mean_gross_bp": float(top["gross_return"].mean() * 1e4),
        "top_decile_mean_net_bp": float(top["net_return"].mean() * 1e4),
        "top_decile_win_rate": float(top["net_return"].gt(0.0).mean()),
        "tie_break": f"{score_column} descending then setup_id ascending",
    }


def _sensitivity_family(
    events: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Reproduce every threshold disclosed as inspected before V18 freeze."""

    family = config["inspected_family_for_multiplicity_receipt"]
    gate_ids: list[str] = []
    rows: list[dict[str, Any]] = []
    for score_name, score_column in (
        ("minimum", "expansion_floor"),
        ("geometric_mean", "expansion_geometric"),
    ):
        for threshold in map(float, family["thresholds"]):
            gate_id = f"{score_name}_{threshold:.2f}".replace(".", "p")
            gate_ids.append(gate_id)
            events[f"gate_{gate_id}"] = events[score_column].ge(threshold)
    p_values = _familywise_permutation_p(
        events,
        gate_ids,
        resamples=int(family["resamples"]),
        seed=int(family["seed"]),
    )
    baseline = metrics(events)
    folds = list(map(str, config["splits"]["development_folds"]))
    baseline_fold = _fold_table(events, folds, "v16_all").set_index("fold")
    for gate_id in gate_ids:
        selected = events.loc[events[f"gate_{gate_id}"]].copy()
        table = _fold_table(selected, folds, gate_id)
        summary = metrics(selected)
        rows.append(
            {
                "gate_id": gate_id,
                **summary,
                **_tail_retention(selected, events),
                "selection_rate": float(len(selected) / len(events)),
                "candidate_minus_v16_mean_net_bp": float(
                    summary["mean_net_bp"] - baseline["mean_net_bp"]
                ),
                "minimum_fold_events": int(table["events"].min()),
                "positive_folds": int(table["mean_net_bp"].gt(0.0).sum()),
                "folds_beating_v16": int(
                    sum(
                        float(row.mean_net_bp)
                        > float(baseline_fold.loc[row.fold, "mean_net_bp"])
                        for row in table.itertuples(index=False)
                    )
                ),
                "familywise_permutation_p": p_values[gate_id],
            }
        )
    return pd.DataFrame(rows), p_values


def _candidate_summary(
    events: pd.DataFrame,
    candidate: pd.DataFrame,
    folds: list[str],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    baseline_metrics = metrics(events)
    baseline_folds = _fold_table(events, folds, "v16_all")
    candidate_folds = _fold_table(candidate, folds, "expansion_floor_0p85")
    baseline_map = baseline_folds.set_index("fold")["mean_net_bp"].to_dict()
    summary = {
        **metrics(candidate),
        **_tail_retention(candidate, events),
        "selection_rate": float(len(candidate) / len(events)),
        "candidate_minus_v16_mean_net_bp": float(
            candidate["net_return"].mean() * 1e4 - baseline_metrics["mean_net_bp"]
        ),
        "minimum_fold_events": int(candidate_folds["events"].min()),
        "positive_folds": int(candidate_folds["mean_net_bp"].gt(0.0).sum()),
        "folds_beating_v16": int(
            sum(
                float(row.mean_net_bp) > float(baseline_map[row.fold])
                for row in candidate_folds.itertuples(index=False)
            )
        ),
    }
    return summary, baseline_folds, candidate_folds


def selection_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    v17_config = _assert_lineage(config)
    _, events, source = build_feature_ledger(v17_config, "selection")
    events = _add_expansion_scores(events)
    threshold = float(config["fixed_candidate"]["threshold"])
    candidate = events.loc[events["expansion_floor"].ge(threshold)].copy()
    folds = list(map(str, config["splits"]["development_folds"]))
    summary, baseline_folds, candidate_folds = _candidate_summary(
        events, candidate, folds
    )
    sensitivity, p_values = _sensitivity_family(events, config)
    selected_family_id = "minimum_0p85"
    summary["retrospective_familywise_permutation_p"] = p_values[selected_family_id]
    gate = config["selection_freeze_gate"]
    checks = {
        "events_total": len(candidate) >= int(gate["minimum_events_total"]),
        "events_per_fold": summary["minimum_fold_events"]
        >= int(gate["minimum_events_per_fold"]),
        "selection_rate": float(gate["selection_rate_min"])
        <= summary["selection_rate"]
        <= float(gate["selection_rate_max"]),
        "mean_net": summary["mean_net_bp"] > float(gate["mean_net_bp_gt"]),
        "profit_factor": summary["profit_factor"] > float(gate["profit_factor_gt"]),
        "positive_folds": summary["positive_folds"] >= int(gate["positive_folds_min"]),
        "folds_beating_v16": summary["folds_beating_v16"]
        >= int(gate["folds_beating_v16_min"]),
        "right_tail_pnl": summary["baseline_top_decile_positive_pnl_capture"]
        >= float(gate["baseline_top_decile_positive_pnl_capture_min"]),
        "p95_retention": summary["candidate_p95_net_retention"]
        >= float(gate["candidate_p95_net_retention_min"]),
    }
    causality = _causality_receipt(v17_config, events, phase="selection")
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(candidate, RESULTS / "development_candidate_trades.csv.gz")
    write_csv(sensitivity, RESULTS / "disclosed_sensitivity_family.csv")
    write_csv(
        pd.concat([baseline_folds, candidate_folds], ignore_index=True),
        RESULTS / "development_fold_metrics.csv",
    )
    write_json(RESULTS / "causality_receipt.json", causality)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "retrospective_development_freeze",
        "status": (
            "frozen_for_diagnostic_audit"
            if all(checks.values())
            else "rejected_before_audit"
        ),
        "fixed_candidate": config["fixed_candidate"],
        "baseline": metrics(events),
        "candidate": summary,
        "score_diagnostic": _score_diagnostic(events, "expansion_floor"),
        "freeze_checks": checks,
        "all_freeze_gates_pass": bool(all(checks.values())),
        "interpretation": "retrospective hypothesis freeze, not validation",
        "source": source,
        "causality": causality,
        "audit_rows_read": 0,
        "repository_holdout_rows_read": source["repository_holdout_rows_read"],
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "sensitivity_sha256": sha256_file(
                RESULTS / "disclosed_sensitivity_family.csv"
            ),
        },
    }
    write_json(SELECTION_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    _assert_selection_committed()
    v17_config = _assert_lineage(config)
    eth, events, source = build_feature_ledger(v17_config, "audit")
    events = _add_expansion_scores(events)
    threshold = float(config["fixed_candidate"]["threshold"])
    candidate = events.loc[events["expansion_floor"].ge(threshold)].copy()
    folds = list(map(str, config["splits"]["audit_folds"]))
    summary, baseline_folds, candidate_folds = _candidate_summary(
        events, candidate, folds
    )
    causality = _causality_receipt(v17_config, events, phase="audit")
    parent = json.loads(PARENT_V16_CONFIG.read_text(encoding="utf-8"))
    parent["matched_control"] = dict(config["matched_control"])
    controls, pairs = _matched_controls(
        candidate,
        eth,
        parent,
        bank=0.10,
        start=utc(config["splits"]["audit_start_inclusive"]),
        end=utc(config["splits"]["audit_end_exclusive"]),
    )
    matched = pairs.loc[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    matched_summary = {
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
        "signflip_p_one_sided": (
            float(signflip_p(excess, resamples=100_000, seed=2026090519))
            if len(excess)
            else np.nan
        ),
    }
    gate = config["audit_gate"]
    checks = {
        "events_total": len(candidate) >= int(gate["minimum_events_total"]),
        "events_per_fold": summary["minimum_fold_events"]
        >= int(gate["minimum_events_per_fold"]),
        "mean_net": summary["mean_net_bp"] > float(gate["mean_net_bp_gt"]),
        "profit_factor": summary["profit_factor"] > float(gate["profit_factor_gt"]),
        "mean_improvement": summary["candidate_minus_v16_mean_net_bp"]
        >= float(gate["candidate_minus_v16_mean_net_bp_min"]),
        "positive_folds": summary["positive_folds"] >= int(gate["positive_folds_min"]),
        "matched_random_excess": matched_summary["excess_bp"]
        > float(gate["matched_random_excess_bp_gt"]),
        "matched_random_p": matched_summary["signflip_p_one_sided"]
        < float(gate["matched_random_signflip_p_lt"]),
        "right_tail_pnl": summary["baseline_top_decile_positive_pnl_capture"]
        >= float(gate["baseline_top_decile_positive_pnl_capture_min"]),
    }
    failure = _failure_table(candidate)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(candidate, RESULTS / "audit_candidate_trades.csv.gz")
    write_csv(
        pd.concat([baseline_folds, candidate_folds], ignore_index=True),
        RESULTS / "audit_fold_metrics.csv",
    )
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(failure, RESULTS / "audit_failure_mechanics.csv")
    write_json(RESULTS / "audit_causality_receipt.json", causality)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "already_seen_transport_audit",
        "status": "research_gate_pass"
        if all(checks.values())
        else "research_gate_fail",
        "fixed_candidate": config["fixed_candidate"],
        "baseline": metrics(events),
        "candidate": summary,
        "score_diagnostic": _score_diagnostic(events, "expansion_floor"),
        "matched_random": matched_summary,
        "audit_checks": checks,
        "all_audit_gates_pass": bool(all(checks.values())),
        "interpretation": "already-seen parent lineage; transport diagnostic only",
        "source": source,
        "causality": causality,
        "repository_holdout_rows_read": source["repository_holdout_rows_read"],
        "production_eligible": False,
        "active_forward_tradingview_or_live_changed": False,
        "selection_receipt_sha256": sha256_file(SELECTION_RECEIPT),
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
    }
    write_json(RESULTS / "audit_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


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
