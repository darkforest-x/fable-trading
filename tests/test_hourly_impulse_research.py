"""Synthetic selection, chronological embargo, and clustered-metric contracts.

These tests never construct a source-backed Study or read market prices.
"""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_research as subject


def weekly_times(count=4):
    return pd.Series(pd.date_range("2024-01-01", periods=count, freq="7D", tz="UTC"))


def trade_rows():
    return pd.DataFrame({
        "event_id": ["a", "b", "c", "d"],
        "closed": [True] * 4,
        "outcome": ["colour_exit", "hard_stop", "colour_exit", "hard_stop"],
        "net_return": [.01, -.005, .02, -.005],
        "gross_return": [.012, -.003, .022, -.003],
        "fold": ["first", "first", "second", "second"],
        "entry_time": weekly_times(),
        "range_atr": [1.0, 2.0, 3.0, 4.0],
        "hold_minutes": [60., 120., 180., 240.],
    })


def test_cluster_p_requires_independent_weeks_not_number_of_trades():
    many_same_week = pd.Series(pd.date_range("2024-01-01", periods=100, freq="h", tz="UTC"))
    assert np.isnan(subject.cluster_p(pd.Series(np.ones(100)), many_same_week))
    assert np.isnan(subject.cluster_p(pd.Series([.1] * 3), weekly_times(3)))


def test_cluster_p_duplicate_events_inside_weeks_do_not_multiply_evidence():
    values = pd.Series([1.0, 2.0, 3.0, 4.0])
    times = weekly_times()
    base = subject.cluster_p(values, times)
    duplicate_values = pd.Series(np.repeat(values.to_numpy(), 100))
    duplicate_times = pd.Series(np.repeat(times.to_numpy(), 100))
    duplicated = subject.cluster_p(duplicate_values, duplicate_times)
    assert base == duplicated
    # Four wholly positive independent blocks have one-sided sign p=1/16,
    # regardless of the 400 correlated rows in the duplicated version.
    assert .05 < base < .08
    assert subject.cluster_p(-values, times) == 1.0


def test_cluster_p_uses_iso_year_week_and_is_seed_reproducible():
    times = pd.Series(pd.to_datetime([
        "2023-12-31", "2024-01-01", "2024-01-08", "2024-01-15",
    ], utc=True))
    result = subject.cluster_p(pd.Series([1., 1., 1., 1.]), times, seed=17)
    assert np.isfinite(result)
    assert result == subject.cluster_p(pd.Series([1., 1., 1., 1.]), times, seed=17)


def test_cluster_p_excludes_infinite_and_missing_values_with_their_timestamps():
    values = pd.Series([1., 2., 3., 4.])
    expected = subject.cluster_p(values, weekly_times(), seed=31)
    augmented = pd.Series([1., 2., 3., 4., np.inf, -np.inf, np.nan])
    actual = subject.cluster_p(augmented, weekly_times(7), seed=31)
    assert actual == expected
    # Invalid returns cannot supply additional independent calendar blocks.
    assert np.isnan(subject.cluster_p(pd.Series([1., 2., 3., np.inf]), weekly_times()))


def test_metrics_ignore_marked_censored_positions_and_rejected_entries():
    actual = trade_rows()
    extras = actual.iloc[:3].copy()
    extras["event_id"] = ["unclosed_runner", "missing_open", "invalid_risk"]
    extras["closed"] = False
    extras["outcome"] = ["right_censored", "entry_missing", "entry_invalid_risk"]
    # Hostile non-null values verify that the closed flag, not positive marks,
    # controls the accepted sample and the top-decile ranking.
    extras["net_return"] = [10., 20., 30.]
    extras["gross_return"] = [10.002, 20.002, 30.002]
    extras["range_atr"] = [100., 200., 300.]
    result = subject.metrics(pd.concat([actual, extras], ignore_index=True), ["first", "second"])
    baseline = subject.metrics(actual, ["first", "second"])
    assert result["events"] == 4
    assert result["censored"] == 1
    assert result["rejected_entries"] == 2
    assert result["excluded_results"] == 3
    assert result["mean_net_bp"] == pytest.approx(50)
    assert result["win_rate"] == .5
    assert result["profit_factor"] == pytest.approx(3)
    assert result["range_top_decile_net_bp"] == pytest.approx(-50)
    for key in ("mean_net_bp", "range_top_decile_net_bp", "robust_score_bp", "net_week_cluster_p", "minimum_fold_events"):
        assert result[key] == baseline[key]


def test_metrics_invalid_closed_results_do_not_count_as_opened_censored_positions():
    actual = trade_rows()
    invalid = actual.iloc[:2].copy()
    invalid["event_id"] = ["invalid_positive", "invalid_negative"]
    invalid["net_return"] = [np.inf, -np.inf]
    combined = pd.concat([actual, invalid], ignore_index=True)
    result = subject.metrics(combined, ["first", "second"])
    assert result["events"] == 4
    assert result["mean_net_bp"] == pytest.approx(50)
    assert result["censored"] == 0
    assert result["rejected_entries"] == 0
    assert result["excluded_results"] == 2


def test_metrics_reports_attrition_even_if_no_position_has_closed():
    unclosed = trade_rows().iloc[:3].copy()
    unclosed["closed"] = False
    unclosed["outcome"] = ["right_censored", "data_gap_censored", "entry_missing"]
    unclosed["net_return"] = np.nan
    result = subject.metrics(unclosed, ["first", "second"])
    assert result["events"] == 0
    assert result["censored"] == 2
    assert result["rejected_entries"] == 1
    assert result["excluded_results"] == 3
    assert result["eligible"] is False


def test_metrics_missing_fold_cannot_receive_an_eligible_robust_score():
    result = subject.metrics(trade_rows(), ["first", "second", "missing"])
    assert result["minimum_fold_events"] == 0
    assert result["robust_score_bp"] == -1e9
    assert result["events"] == 4
    closed_none = trade_rows().assign(closed=False)
    empty = subject.metrics(closed_none, ["first", "second"])
    assert empty["events"] == 0
    assert empty["eligible"] is False
    assert np.isnan(empty["mean_net_bp"])


@pytest.mark.parametrize(
    "score,worst,eligible,expected",
    [(11.99, 7., True, False), (12., 6.99, True, False),
     (12., 7., False, False), (12., 7., True, True),
     (50., -10., True, False)],
)
def test_selection_requires_registered_margin_and_worst_fold_tolerance(score, worst, eligible, expected):
    config = {"selection": {"move_margin_bp": 2., "worst_fold_tolerance_bp": 3.}}
    incumbent = {"robust_score_bp": 10., "worst_fold_bp": 10.}
    candidate = {"robust_score_bp": score, "worst_fold_bp": worst, "eligible": eligible}
    assert subject.improvement(candidate, incumbent, config) is expected


def test_selection_does_not_invent_unregistered_margin_defaults():
    with pytest.raises(KeyError):
        subject.improvement(
            {"eligible": True, "robust_score_bp": 20., "worst_fold_bp": 10.},
            {"robust_score_bp": 10., "worst_fold_bp": 10.},
            {"selection": {"worst_fold_tolerance_bp": 3.}},
        )


def test_entries_route_ma_settings_and_embargo_each_full_label_horizon(monkeypatch):
    study = subject.Study.__new__(subject.Study)
    study.config = {"execution": {"max_hours": 72}}
    study.folds = [
        ["first", "2024-01-01", "2024-01-08"],
        ["second", "2024-01-08", "2024-01-15"],
    ]
    decisions = pd.to_datetime([
        "2023-12-31 23:00", "2024-01-01 00:00", "2024-01-04 23:00",
        "2024-01-05 00:00", "2024-01-07 23:00", "2024-01-08 00:00",
        "2024-01-11 23:00", "2024-01-12 00:00", "2024-01-15 00:00",
    ], utc=True)
    generated = pd.DataFrame({
        "event_id": ["e%d" % n for n in range(len(decisions))],
        "signal_time": decisions - pd.Timedelta(hours=1),
        "decision_time": decisions,
    })
    sentinel = pd.DataFrame({"synthetic": [1]})
    calls = []

    def fake_featured(minutes, kind, length):
        calls.append((minutes, kind, length))
        return sentinel

    def fake_entries(frame, params):
        assert frame is sentinel
        assert params == {"shape": "engulf_only", "side": "short"}
        return generated.copy()

    def disallow_source(*args, **kwargs):
        raise AssertionError("Synthetic tests must not open source data")

    monkeypatch.setattr(study, "featured", fake_featured)
    monkeypatch.setattr(subject, "make_entries", fake_entries)
    monkeypatch.setattr(subject, "load_source", disallow_source)
    params = {"ma_kind": "EMA", "ma_length": 20, "shape": "engulf_only", "side": "short"}
    original = deepcopy(params)
    result = study.entries(params)
    assert calls == [(60, "EMA", 20)]
    assert params == original
    assert result["event_id"].tolist() == ["e1", "e2", "e5", "e6"]
    assert result["fold"].tolist() == ["first", "first", "second", "second"]
    assert generated.columns.tolist() == ["event_id", "signal_time", "decision_time"]
    for name, start, end in study.folds:
        part = result.loc[result["fold"].eq(name)]
        assert part["decision_time"].ge(subject.utc(start)).all()
        assert (part["decision_time"] + pd.Timedelta(hours=72)).lt(subject.utc(end)).all()
