"""Synthetic SQL arithmetic/unknown checks before V30 report materialization."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import pandas as pd
import pytest

path = Path(__file__).resolve().parents[1]/"experiments/active/exp-btcusdtp-1h-classifier-economics-preholdout-20260907-v30/build_report.py"
spec = importlib.util.spec_from_file_location("_report_v30", path)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_policy_cost_and_gross_decomposition_same_denominator():
    cases = pd.DataFrame(dict(horizon_hours=[4,4,4], classifier_gate_state=["accepted", "abstain", "unknown"],
        gross_markout=[.003, -.001, .5], cost_threshold_markout=[.001, -.003, .498],
        policy_cost_threshold_markout=[.001, 0., None]))
    with sqlite3.connect(":memory:") as con:
        cases.to_sql("cases", con, index=False)
        row = report.query_records(con, report.QUERIES["policy_decomposition"])[0]
    assert row["known_gate_n"] == 2 and row["abstain_n"] == 1
    assert row["avoided_cost_bp"] == pytest.approx(10.)
    assert row["avoided_gross_bp"] == pytest.approx(5.)
    assert row["delta_bp"] == pytest.approx(15.)


def test_sql_missing_stays_json_null_not_zero_or_nan():
    with sqlite3.connect(":memory:") as con:
        data = report.query_records(con, "SELECT 1 id,NULL unavailable UNION ALL SELECT 2,3.5")
    assert data[0]["unavailable"] is None
    assert json.loads(json.dumps(data, allow_nan=False))[0]["unavailable"] is None
