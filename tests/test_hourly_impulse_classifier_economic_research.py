"""Synthetic V30 clock checks; no actual saved outcome table is accessed."""
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_classifier_economic_research import preflight


def frame():
    return pd.DataFrame(dict(decision_time=["2023-02-01T04:00:00Z"],
        endpoint_time=["2023-02-01T08:00:00Z"], horizon_hours=[4]))


def test_native_hour_development_clock():
    result = preflight(frame())
    assert result["decision_time"]["first"] == "2023-02-01 04:00:00+00:00"


@pytest.mark.parametrize("column,value", [
    ("decision_time", "2023-02-01 04:00:00"),
    ("decision_time", "2023-02-01T04:15:00Z"),
    ("endpoint_time", "2025-01-01T08:00:00Z"),
    ("decision_time", "2022-12-31T04:00:00Z"),
    ("endpoint_time", "2023-02-01T09:00:00Z"),
    ("horizon_hours", 5), ("decision_time", 1675224000),
])
def test_reject_scope_clock_or_unregistered_horizon(column, value):
    data = frame()
    data.loc[0, column] = value
    with pytest.raises(ValueError):
        preflight(data)
