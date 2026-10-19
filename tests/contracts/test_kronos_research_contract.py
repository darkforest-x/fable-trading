"""Kronos may be researched only when its identity and evidence bar are explicit."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from yoyo.contracts.holdout import HoldoutBoundaryError
from yoyo.contracts.kronos_research import (
    KronosResearchContract,
    KronosResearchContractError,
    load_kronos_research_contract,
    minimum_permutations_for_alpha,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/research/kronos_small_forecast_v1.yaml"


def _raw() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_canonical_research_contract_is_valid_and_research_only() -> None:
    contract = load_kronos_research_contract(CONFIG)
    assert contract.source_commit == "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
    assert contract.temperature == 0.6
    assert contract.sample_count == 10
    assert contract.explicit_eval is True
    assert contract.n_permutations == 10_000
    assert contract.training_eligible is False
    assert contract.production_eligible is False
    assert contract.active_bundle_eligible is False


def test_unpinned_model_revision_is_refused() -> None:
    raw = _raw()
    raw["model"]["revision"] = "0" * 40
    with pytest.raises(KronosResearchContractError, match="unpinned Kronos identity"):
        KronosResearchContract.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("explicit_eval", False, "explicit_eval"),
        ("temperature", 1.0, "temperature"),
        ("sample_count", 1, "sample_count"),
        ("timestamp_timezone", "local", "UTC"),
    ),
)
def test_readme_style_or_ambiguous_inference_is_refused(
    field: str, value: object, message: str
) -> None:
    raw = _raw()
    raw["inference"][field] = value
    with pytest.raises(KronosResearchContractError, match=message):
        KronosResearchContract.from_mapping(raw)


def test_permutation_count_must_be_able_to_resolve_the_project_alpha() -> None:
    assert minimum_permutations_for_alpha(0.01) == 100
    raw = _raw()
    raw["evaluation"]["n_permutations"] = 30
    with pytest.raises(KronosResearchContractError, match="cannot resolve"):
        KronosResearchContract.from_mapping(raw)


def test_all_five_matched_control_axes_are_mandatory() -> None:
    raw = _raw()
    raw["evaluation"]["matched_control_axes"] = ["volatility_bucket"]
    with pytest.raises(KronosResearchContractError, match="symbol, time, volatility"):
        KronosResearchContract.from_mapping(raw)


def test_contract_cannot_authorize_training_production_or_holdout() -> None:
    for section, field in (
        ("eligibility", "training"),
        ("eligibility", "production"),
        ("eligibility", "active_bundle"),
        ("holdout", "read_allowed"),
    ):
        raw = _raw()
        raw[section][field] = True
        with pytest.raises(KronosResearchContractError):
            KronosResearchContract.from_mapping(raw)


def test_context_end_is_checked_against_the_single_holdout_boundary() -> None:
    contract = load_kronos_research_contract(CONFIG)
    assert contract.assert_context_end("2026-05-03T23:45:00+00:00") is None
    with pytest.raises(HoldoutBoundaryError, match="Kronos context end"):
        contract.assert_context_end("2026-05-04T00:00:00+00:00")
