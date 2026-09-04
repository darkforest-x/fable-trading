import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-two-stage-k2-preholdout-20260904-v1"
)
RESULTS = EXPERIMENT / "results"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_diagnostic_receipt_keeps_audit_and_holdout_closed() -> None:
    receipt = json.loads(
        (RESULTS / "development_diagnostic_receipt.json").read_text()
    )
    assert receipt["source"]["audit_outcomes_read"] == 0
    assert receipt["source"]["holdout_rows_read"] == 0
    assert receipt["decision"] == {
        "candidate_passed": False,
        "audit_open_allowed": False,
        "tradingview_replacement_allowed": False,
        "holdout_rows_read": 0,
        "reason": (
            "registered two-stage K2 failed mean, robustness, fold and "
            "significance gates"
        ),
    }


def test_freqtrade_entry_and_lookahead_parity_are_exact() -> None:
    receipt = json.loads(
        (RESULTS / "development_diagnostic_receipt.json").read_text()
    )
    result = receipt["freqtrade"]
    archive = EXPERIMENT / result["archive"]
    assert result["native_entries"] == result["freqtrade_entries"] == 100
    assert result["exact_entry_keys"] == 100
    assert result["entry_price_max_abs_error"] == 0.0
    assert result["stop_price_max_abs_error"] == 0.0
    assert result["delay_mismatches"] == 0
    assert result["lookahead_has_bias"] is False
    assert result["biased_entry_signals"] == 0
    assert result["biased_exit_signals"] == 0
    assert _sha256(archive) == result["archive_sha256"]


def test_failure_contributions_reconcile_to_candidate_mean() -> None:
    modes = pd.read_csv(RESULTS / "development_failure_modes.csv")
    metrics = pd.read_csv(RESULTS / "development_metrics.csv")
    candidate = metrics.loc[metrics["arm"].eq("candidate_two_stage")].iloc[0]
    assert modes["events"].sum() == candidate["events"] == 100
    np.testing.assert_allclose(
        modes["contribution_to_all_trade_mean_bp"].sum(),
        candidate["mean_net_bp"],
        rtol=0.0,
        atol=1e-10,
    )


def test_posthoc_sweeps_cannot_be_misreported_as_selection() -> None:
    targets = pd.read_csv(RESULTS / "development_target_r_sensitivity.csv")
    rules = pd.read_csv(RESULTS / "development_rule_sensitivity.csv")
    assert targets["analysis_status"].eq(
        "posthoc_one_dimensional_sensitivity_not_selection"
    ).all()
    assert rules["analysis_status"].eq(
        "posthoc_single_coordinate_hypothesis_only"
    ).all()
    assert not rules["passes_original_full_gate"].any()
    assert targets.loc[targets["target_r"].eq(3.0), "events"].item() == 100


def test_freqtrade_tag_survives_framework_entry_reset_contract() -> None:
    source = (
        EXPERIMENT
        / "freqtrade/user_data/strategies/FableTwoStageK2.py"
    ).read_text()
    assert 'dataframe["fable_entry_tag"] = ""' in source
    assert 'dataframe["enter_tag"] = dataframe["fable_entry_tag"]' in source
    assert 'dataframe["enter_long"] = 0' in source
    assert 'dataframe["enter_short"] = 0' in source
