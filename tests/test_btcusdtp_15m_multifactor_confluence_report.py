"""Regression tests for BTC 15m multifactor report diagnostics."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_btcusdtp_15m_multifactor_confluence_report import (
    HOLDOUT_START,
    corrected_failure_mechanics,
)


def test_intrabar_mfe_does_not_arm_runner() -> None:
    trades = pd.DataFrame(
        [
            {
                "setup_id": "intrabar-only",
                "net_return": -0.01,
                "gross_return": -0.008,
                "entry_price": 100.0,
                "signal_atr": 1.0,
                "runner_armed": False,
                "outcome": "hard_stop",
                "mfe_at_exit_atr": 2.5,
                "horizon_mfe_atr": 3.0,
            },
            {
                "setup_id": "close-armed-loss",
                "net_return": -0.005,
                "gross_return": -0.003,
                "entry_price": 100.0,
                "signal_atr": 1.0,
                "runner_armed": True,
                "outcome": "trend_ma_trail1_stop",
                "mfe_at_exit_atr": 4.0,
                "horizon_mfe_atr": 8.0,
            },
        ]
    )

    result = corrected_failure_mechanics(trades).set_index("setup_id")

    assert result.loc["intrabar-only", "diagnostic_category"] == "failed_before_arm_other"
    assert result.loc["close-armed-loss", "diagnostic_category"] == "armed_then_loss"


def test_exit_giveback_excludes_post_exit_horizon() -> None:
    trades = pd.DataFrame(
        [
            {
                "setup_id": "winner",
                "net_return": 0.008,
                "gross_return": 0.01,
                "entry_price": 100.0,
                "signal_atr": 1.0,
                "runner_armed": True,
                "outcome": "trend_ma_trail1_stop",
                "mfe_at_exit_atr": 1.5,
                "horizon_mfe_atr": 10.0,
            }
        ]
    )

    result = corrected_failure_mechanics(trades).iloc[0]

    assert result["realized_atr"] == 1.0
    assert result["exit_giveback_atr"] == 0.5
    assert result["horizon_opportunity_gap_atr"] == 9.0
    assert result["diagnostic_category"] == "winner_retained"


def test_registered_ledgers_stop_before_holdout() -> None:
    root = Path(__file__).resolve().parents[1] / "experiments" / "active"
    root /= "exp-btcusdtp-15m-multifactor-confluence-preholdout-20260904-v1"
    root /= "results"
    for filename in (
        "development_feature_ledger.csv.gz",
        "confirmation_feature_ledger.csv.gz",
        "audit_feature_ledger.csv.gz",
    ):
        times = pd.to_datetime(pd.read_csv(root / filename, usecols=["entry_time"])["entry_time"], utc=True)
        assert times.max() < HOLDOUT_START
