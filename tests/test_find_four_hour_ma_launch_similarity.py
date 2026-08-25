"""Contract tests for versioned 4h similarity-builder configurations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.find_four_hour_ma_launch_similarity import (
    audit_expansion_rank_prefix,
    validate_expansion_contract,
    validate_preregistration,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PREREG = (
    ROOT
    / "experiments/active/exp-btc-4h-ma-launch-similarity-v1/preregistration.json"
)
TOP20_PREREG = (
    ROOT
    / "experiments/active/exp-btc-4h-ma-launch-similarity-top20-v2/preregistration.json"
)
BASELINE_SUMMARY = (
    ROOT
    / "experiments/active/exp-btc-4h-ma-launch-similarity-v1/results/scan_summary.json"
)


def test_baseline_preregistration_still_loads_exactly() -> None:
    payload, spec, experiment_id, universe_size, use_number = (
        validate_preregistration(BASELINE_PREREG)
    )
    assert payload["spec"] == spec.to_jsonable()
    assert experiment_id == "exp-btc-4h-ma-launch-similarity-v1"
    assert universe_size == 54
    assert use_number == 1


def test_top20_expansion_changes_only_top_n() -> None:
    payload, spec, experiment_id, universe_size, use_number = (
        validate_preregistration(TOP20_PREREG)
    )
    audit = validate_expansion_contract(payload, spec)
    assert experiment_id == "exp-btc-4h-ma-launch-similarity-top20-v2"
    assert universe_size == 54
    assert use_number == 2
    assert audit["enabled"] is True
    assert audit["single_changed_field"] == "top_per_side"
    assert audit["previous_top_per_side"] == 8
    assert audit["expanded_top_per_side"] == 20


def test_top20_prefix_audit_accepts_the_frozen_baseline_rows() -> None:
    prereg = json.loads(TOP20_PREREG.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_SUMMARY.read_text(encoding="utf-8"))
    selected = {
        side: copy.deepcopy(baseline["selected"][side])
        for side in ("LONG", "SHORT")
    }
    audit = audit_expansion_rank_prefix(prereg, selected)
    assert audit["required"] is True
    assert audit["passed"] is True
    assert audit["sides"]["LONG"]["prefix_count"] == 8
    assert audit["sides"]["SHORT"]["exact_identity_and_distance_match"] is True


def test_top20_prefix_audit_refuses_rank_drift() -> None:
    prereg = json.loads(TOP20_PREREG.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_SUMMARY.read_text(encoding="utf-8"))
    selected = {
        side: copy.deepcopy(baseline["selected"][side])
        for side in ("LONG", "SHORT")
    }
    selected["LONG"][0]["final_distance"] += 0.01
    with pytest.raises(ValueError, match="LONG rank 1 final_distance drifted"):
        audit_expansion_rank_prefix(prereg, selected)
