"""Synthetic accounting contracts for receipt-bound trend-baseline reporting."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.trend_baseline_report import (
    _controls_for_period,
    _event_curve,
    control_metrics,
    monthly,
    metrics,
)


CFG = {"start": "2025-01-01T00:00:00Z", "split": "2025-02-01T00:00:00Z", "end": "2025-03-01T00:00:00Z"}


def _rows(values: list[float], exits: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame({"net_r": values, "gross_r": np.asarray(values) + 1.0,
                          "net_return": np.asarray(values) / 100.0,
                          "gross_return": (np.asarray(values) + 1.0) / 100.0,
                          "censored": False, "exit_time": pd.to_datetime(exits, utc=True)})
    frame["entry_time"] = pd.Timestamp("2025-01-02T00:00:00Z")
    frame["event_key"] = [f"e{i}" for i in range(len(frame))]
    return frame


def test_cross_split_control_is_excluded_from_earlier_pairs() -> None:
    frame = _rows([1.0], ["2025-01-10T00:00:00Z"])
    frame["matched"] = True; frame["control_censored"] = False
    frame["control_entry_time"] = pd.Timestamp("2025-01-20T00:00:00Z")
    frame["control_exit_time"] = pd.Timestamp("2025-02-02T00:00:00Z")
    assert _controls_for_period(frame, pd.Timestamp(CFG["split"]), "earlier").empty


def test_same_exit_time_is_aggregated_before_event_drawdown() -> None:
    closed = _rows([10.0, -10.0], ["2025-01-10T00:00:00Z", "2025-01-10T00:00:00Z"])
    result = _event_curve(closed, pd.Timestamp(CFG["start"]), pd.Timestamp(CFG["end"]))
    assert result["event_drawdown_r"] == 0.0


def test_underwater_duration_includes_the_no_trade_tail() -> None:
    closed = _rows([-1.0], ["2025-01-10T00:00:00Z"])
    result = _event_curve(closed, pd.Timestamp(CFG["start"]), pd.Timestamp("2025-01-20T00:00:00Z"))
    # The initial zero mark is the high-water point, and no-trade time after
    # the loss remains underwater through the reporting interval end.
    assert result["max_underwater_days"] == 19.0


def test_underwater_duration_is_recorded_when_a_later_exit_recovers_the_high() -> None:
    closed = _rows([2.0, -1.0, 1.0], ["2025-01-02T00:00:00Z", "2025-01-05T00:00:00Z", "2025-01-09T00:00:00Z"])
    result = _event_curve(closed, pd.Timestamp(CFG["start"]), pd.Timestamp(CFG["end"]))
    assert result["event_drawdown_r"] == 1.0
    assert result["max_underwater_days"] == 7.0


def test_unique_controls_keep_distinct_streams_at_the_same_timestamp() -> None:
    frame = _rows([1.0, 1.0, 1.0], ["2025-01-10T00:00:00Z"] * 3)
    frame["matched"] = True; frame["control_censored"] = False
    frame["control_entry_time"] = pd.Timestamp("2025-01-03T00:00:00Z")
    frame["control_exit_time"] = pd.Timestamp("2025-01-04T00:00:00Z")
    frame["control_signal_time"] = pd.Timestamp("2025-01-02T00:00:00Z")
    frame["control_net_r"] = 0.0; frame["control_net_return"] = 0.0
    frame["symbol"] = ["A", "A", "B"]
    frame["timeframe"] = "15m"; frame["side"] = 1
    got = control_metrics(frame, pd.Timestamp(CFG["split"]), "full")
    assert got["random_pairs"] == 3
    assert got["random_unique_control_timestamps"] == 2


def test_monthly_entry_cohorts_do_not_publish_period_event_curve_metrics() -> None:
    frame = _rows([-1.0], ["2025-02-10T00:00:00Z"])
    frame["matched"] = True; frame["control_censored"] = False
    frame["control_entry_time"] = pd.Timestamp("2025-01-03T00:00:00Z")
    frame["control_exit_time"] = pd.Timestamp("2025-01-04T00:00:00Z")
    frame["control_signal_time"] = pd.Timestamp("2025-01-02T00:00:00Z")
    frame["control_net_r"] = 0.0; frame["control_net_return"] = 0.0
    frame["symbol"] = "A"; frame["timeframe"] = "15m"; frame["arm"] = "v9"; frame["side"] = 1
    frame["period"] = "earlier"
    table = monthly(frame, {**CFG, "timeframes": [["15m", 15]]})
    assert "event_drawdown_r" not in table and "max_underwater_days" not in table


def test_empty_group_is_stable_and_cost_tail_statistics_are_correct() -> None:
    empty = _rows([], [])
    empty["matched"] = pd.Series(dtype=bool); empty["control_censored"] = pd.Series(dtype=bool)
    empty["control_entry_time"] = pd.Series(dtype="datetime64[ns, UTC]")
    empty["control_exit_time"] = pd.Series(dtype="datetime64[ns, UTC]")
    assert metrics(empty, CFG, "full")["entries"] == 0

    values = _rows([10.0, 5.0, -1.0], ["2025-01-03T00:00:00Z"] * 3)
    result = metrics(values, CFG, "full")
    assert result["cost_r"] == 3.0
    assert result["winners_10r"] == 1
    assert result["winners_10r_positive_profit_share"] == 10.0 / 15.0
