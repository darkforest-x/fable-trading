"""Safety and topology tests for the explicit L1 -> L2 bypass runner."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from scripts.research_15m_ma_launch_l1_l2_bypass_l15 import (
    CANDIDATE_READ_COLUMNS,
    EXPERIMENT_ID,
    L2_FEATURE_COLUMNS,
    BypassError,
    bool_series,
    load_preregistration,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "research_15m_ma_launch_l1_l2_bypass_l15.py"


def test_preregistration_freezes_the_three_stage_bypass() -> None:
    prereg = load_preregistration()
    assert prereg["experiment_id"] == EXPERIMENT_ID
    assert prereg["pipeline"]["enabled_stages"] == [
        "l1_frozen_yolo_candidates",
        "episode_dependency_collapse",
        "side_specific_l2_return_regression",
    ]
    assert prereg["pipeline"]["disabled_stages"] == [
        "l15_global_shape_classifier"
    ]
    assert tuple(prereg["l2"]["feature_columns"]) == L2_FEATURE_COLUMNS
    assert prereg["safety"]["holdout_read"] is False
    assert prereg["safety"]["production_eligible"] is False


def test_runner_import_graph_has_no_l15_or_global_shape_dependency() -> None:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert not any("l15" in module.lower() for module in modules)
    assert not any("global_shape" in module.lower() for module in modules)


def test_candidate_allow_list_contains_no_l15_field() -> None:
    assert set(L2_FEATURE_COLUMNS).issubset(CANDIDATE_READ_COLUMNS)
    assert not any(column.lower().startswith("l15") for column in CANDIDATE_READ_COLUMNS)


def test_boolean_parser_fails_closed() -> None:
    parsed = bool_series(pd.Series([True, False, "true", "false"]), label="x")
    assert parsed.tolist() == [True, False, True, False]
    with pytest.raises(BypassError, match="non-boolean"):
        bool_series(pd.Series(["yes"]), label="x")
