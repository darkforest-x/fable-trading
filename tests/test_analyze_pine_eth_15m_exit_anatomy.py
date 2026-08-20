"""Contracts for the frozen V9 exit-anatomy diagnostic."""
import json

import pandas as pd

from scripts.analyze_pine_eth_15m_exit_anatomy import OUTPUT_CSV, OUTPUT_JSON


def test_exit_anatomy_is_bounded_and_does_not_tune_barriers() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    assert payload["trades"] == 110
    assert payload["holdout_rows_read"] == 0
    assert payload["final_preholdout_rows_read"] == 0
    assert payload["barrier_parameters_changed"] is False
    assert payload["barrier_search_performed"] is False


def test_break_even_name_is_not_cost_break_even() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    semantics = payload["break_even_cost_semantics"]
    assert semantics["configured_lock_bp"] == 10.0
    assert semantics["frozen_round_trip_cost_bp"] == 20.0
    assert semantics["locked_stop_project_net_bp"] == -10.0
    assert len(semantics["cross_period_evidence"]) == 3
    assert all(
        abs(row["mean_project_net_bp"] + 10.0) < 1e-9
        for row in semantics["cross_period_evidence"]
    )

    rows = pd.read_csv(OUTPUT_CSV)
    counts = rows["stop_subtype"].value_counts().to_dict()
    assert counts == {
        "initial_protective_stop": 50,
        "break_even_locked_stop": 49,
        "reverse": 11,
    }
    assert rows.loc[rows["stop_subtype"].eq("reverse"), "project_net_return"].gt(0).all()
