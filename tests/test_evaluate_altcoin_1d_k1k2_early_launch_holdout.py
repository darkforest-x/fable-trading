"""Contract tests for the one-shot altcoin daily V4 holdout evaluator."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts import evaluate_altcoin_1d_k1k2_early_launch_holdout as subject


EXPERIMENT = Path(
    "experiments/active/exp-altcoin-1d-k1k2-early-launch-holdout-20260905-v4"
)


def test_explicit_holdout_flag_is_required() -> None:
    with pytest.raises(SystemExit):
        subject.parse_args([])


def test_source_contract_uses_only_opened_partitions() -> None:
    config = subject.load_json(EXPERIMENT / "config.json")
    signal_config = subject.load_json(subject.ROOT / config["parents"]["signal_config_path"])
    manifest = subject.load_json(subject.ROOT / config["parents"]["universe_manifest_path"])

    records, sealed_paths = subject.source_records(signal_config, manifest)

    selected_paths = {
        path for record in records.values() for path in record["sources"]
    }
    assert len(records) == 161
    assert not ({"BTC", "ETH"} & set(records))
    assert not (selected_paths & sealed_paths)
    assert not (set(records) & set(manifest["sealed_confirmation_b"]))


def test_breadth_then_early_launch_cap_are_separate_rejections() -> None:
    setups = pd.DataFrame(
        [
            {
                "setup_id": "keep",
                "context_available": True,
                "context_breadth_change5": 0.03,
                "k1_signed_slow_side_atr": 0.74,
            },
            {
                "setup_id": "late",
                "context_available": True,
                "context_breadth_change5": 0.03,
                "k1_signed_slow_side_atr": 0.76,
            },
            {
                "setup_id": "flat-market",
                "context_available": True,
                "context_breadth_change5": 0.01,
                "k1_signed_slow_side_atr": 0.20,
            },
        ]
    )
    params = {
        "breadth_change5_min": 0.02,
        "breadth_level_min": -1.0,
        "context_mean_min": -1.0,
        "major_score_min": -1.0,
        "relative_score_min": -1.0,
    }

    kept, rejected = subject.filter_setups(
        {"TEST": setups}, params, extension_max=0.75
    )

    assert kept["TEST"]["setup_id"].tolist() == ["keep"]
    reasons = rejected.set_index("setup_id")["context_rejection_reason"].to_dict()
    assert reasons == {
        "flat-market": "breadth_change5_min",
        "late": "k1_signed_slow_side_atr_max",
    }


def test_acceptance_is_conjunctive_and_requires_winner_robustness() -> None:
    config = subject.load_json(EXPERIMENT / "config.json")
    result = {
        "summary": {
            "eligible": True,
            "mean_net_bp": 1.0,
            "mean_capped_net_r": 0.1,
            "profit_factor": 1.1,
            "positive_folds": 2,
            "total_folds": 2,
            "positive_symbol_share": 0.60,
            "week_cluster_signflip_p": 0.005,
        },
        "portfolio_summary": {
            "total_return": 0.01,
            "closed_equity_max_drawdown": -0.05,
        },
    }
    matched = {"excess_bp": 1.0, "week_cluster_signflip_p": 0.005}
    concentration = {"leave_largest_winner_out_mean_net_bp": 1.0}

    passing = subject.acceptance_checks(result, matched, concentration, config)
    assert all(passing.values())

    concentration["leave_largest_winner_out_mean_net_bp"] = -0.01
    failing = subject.acceptance_checks(result, matched, concentration, config)
    assert failing["leave_largest_winner_out_mean_net_positive"] is False
    assert not all(failing.values())
