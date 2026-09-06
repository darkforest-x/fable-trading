"""Synthetic gate and control-replay audit; no source or result files are read."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_staged_research as research


def frozen_config():
    return {"selection": {
        "development_minimum_events": 80,
        "development_minimum_per_fold": 12,
        "development_positive_folds": 4,
        "development_min_profit_factor": 1.1,
        "development_min_mean_net_bp": 0,
        "matched_coverage": .9,
    }}


def passing_metrics():
    return {"events": 80, "minimum_fold_events": 12, "positive_folds": 4,
            "mean_net_bp": 1.0, "profit_factor": 1.11}


def passing_match():
    return {"coverage": .9, "mean_excess_bp": 1.0}


def test_development_gate_thresholds_preserve_frozen_inclusive_and_strict_edges():
    info, match = passing_metrics(), passing_match()
    assert all(research.development_gates(info, match, frozen_config()).values())
    assert info == passing_metrics() and match == passing_match()


@pytest.mark.parametrize("field,value,gate", [
    ("events", 79, "samples"),
    ("minimum_fold_events", 11, "samples"),
    ("positive_folds", 3, "positive_folds"),
    ("mean_net_bp", 0.0, "net_profit"),
    ("mean_net_bp", np.nan, "net_profit"),
    ("profit_factor", 1.1, "profit_factor"),
])
def test_development_gate_rejects_each_failed_metric(field, value, gate):
    info = passing_metrics()
    info[field] = value
    gates = research.development_gates(info, passing_match(), frozen_config())
    assert not gates[gate]
    assert not all(gates.values())


@pytest.mark.parametrize("field,value,gate", [
    ("coverage", .89999, "matched_coverage"),
    ("coverage", np.nan, "matched_coverage"),
    ("mean_excess_bp", 0, "matched_excess"),
    ("mean_excess_bp", None, "matched_excess"),
    ("mean_excess_bp", np.nan, "matched_excess"),
])
def test_development_gate_cannot_pass_without_positive_exact_control_evidence(field, value, gate):
    match = passing_match()
    match[field] = value
    gates = research.development_gates(passing_metrics(), match, frozen_config())
    assert not gates[gate]
    assert not all(gates.values())


def test_zero_event_development_metrics_fail_closed_without_missing_key_errors():
    info = {"events": 0, "minimum_fold_events": 0, "mean_net_bp": np.nan}
    match = {"coverage": 0, "mean_excess_bp": None}
    assert not any(research.development_gates(info, match, frozen_config()).values())


def test_matched_controls_are_replayed_and_stale_reference_outcomes_are_discarded(monkeypatch):
    stamp = pd.Timestamp("2024-01-02T00:00:00Z")
    source_trades = pd.DataFrame([
        {"event_id": "a", "closed": True, "net_return": .1},
        {"event_id": "b", "closed": True, "net_return": .2},
    ])
    requests = pd.DataFrame([
        {"event_id": "a0", "parent_event_id": "a", "closed": True, "net_return": .99, "entry_time": stamp},
        {"event_id": "a1", "parent_event_id": "a", "closed": False, "net_return": np.nan, "entry_time": stamp + pd.Timedelta(hours=1)},
        {"event_id": "b0", "parent_event_id": "b", "closed": True, "net_return": .99, "entry_time": stamp + pd.Timedelta(hours=2)},
        {"event_id": "b1", "parent_event_id": "b", "closed": True, "net_return": .99, "entry_time": stamp + pd.Timedelta(hours=3)},
    ])
    old_pairs = pd.DataFrame([
        {"event_id": "a", "event_net_return": .1, "entry_time": stamp, "control_mean_return": .99, "excess": -.89},
        {"event_id": "b", "event_net_return": .2, "entry_time": stamp, "control_mean_return": .99, "excess": -.79},
    ])
    requests_before, pairs_before = requests.copy(deep=True), old_pairs.copy(deep=True)
    calls = []

    def fake_assignment(trades, policy, entry):
        pd.testing.assert_frame_equal(trades, source_trades)
        assert policy == research.REFERENCE_POLICY
        assert entry == {"require_ma_slope": True}
        return requests.copy(), old_pairs.copy(), {"mean_excess_bp": -9999}

    study = SimpleNamespace(config={"matching": {"count_per_trade": 2}}, matched=fake_assignment)

    def fake_evaluate(actual_study, actual_requests, policy):
        assert actual_study is study
        pd.testing.assert_frame_equal(actual_requests, requests_before)
        calls.append(deepcopy(policy))
        result = actual_requests.copy()
        result["closed"] = [True, True, True, False]
        result["net_return"] = [-.01, .03, .04, np.nan]
        return result

    monkeypatch.setattr(research, "evaluate", fake_evaluate)
    monkeypatch.setattr(research, "cluster_p", lambda values, times, monthly: .25 if monthly else -1)
    policy = {"partial_targets": [[1, .5]], "takeover_r": 1}
    controls, pairs, metrics = research.matched(study, source_trades, policy, {"require_ma_slope": True})
    assert calls == [policy]
    assert len(controls) == 4  # Baseline-censored a1 was still replayed and closed.
    assert metrics["coverage"] == .5  # Staged-censored b1 disqualifies its pair.
    assert metrics["mean_excess_bp"] == pytest.approx(900)
    assert metrics["control_mean_net_bp"] == pytest.approx(100)
    assert metrics["matched_event_mean_net_bp"] == pytest.approx(1000)
    assert metrics["month_cluster_p"] == .25
    assert pairs.loc[pairs.event_id.eq("b"), "excess"].isna().all()
    pd.testing.assert_frame_equal(requests, requests_before)
    pd.testing.assert_frame_equal(old_pairs, pairs_before)


def test_no_available_controls_produces_rejected_coverage_without_replay(monkeypatch):
    empty = pd.DataFrame()
    study = SimpleNamespace(matched=lambda *args: (empty, empty, {}))
    monkeypatch.setattr(research, "evaluate", lambda *args: pytest.fail("No controls to replay"))
    controls, pairs, summary = research.matched(study, pd.DataFrame(), {}, {})
    assert controls.empty and pairs.empty
    assert summary["coverage"] == 0 and summary["mean_excess_bp"] is None


def test_evaluate_preserves_fold_end_embargo_and_effective_policy(monkeypatch):
    folds = [("A", "2023-01-01", "2023-07-01"), ("B", "2023-07-01", "2024-01-01")]
    featured_calls, replay_calls = [], []

    def featured(minutes, kind, length):
        featured_calls.append((minutes, kind, length))
        return pd.DataFrame({"marker": [minutes]})

    study = SimpleNamespace(folds=folds, config={"execution": {"cost_fraction": .002, "max_hours": 72}}, raw=pd.DataFrame(), featured=featured)
    entries = pd.DataFrame([{"event_id": "a", "fold": "A"}, {"event_id": "b", "fold": "B"}])

    def replay(raw, early, runner, part, policy, *, end_exclusive):
        assert early.marker.iloc[0] == 15 and runner.marker.iloc[0] == 60
        replay_calls.append((part.event_id.tolist(), dict(policy), end_exclusive))
        return part.copy()

    monkeypatch.setattr(research, "simulate_staged_events", replay)
    policy = {"id": "half", "partial_targets": [[1, .5]], "takeover_r": 1}
    result = research.evaluate(study, entries, policy)
    assert result.event_id.tolist() == ["a", "b"]
    assert featured_calls == [(15, "SMA", 40), (60, "SMA", 40)]
    assert replay_calls[0][0] == ["a"] and replay_calls[1][0] == ["b"]
    assert replay_calls[0][2] == pd.Timestamp("2023-07-01T00:00:00Z")
    assert replay_calls[1][2] == pd.Timestamp("2024-01-01T00:00:00Z")
    assert replay_calls[0][1]["cost_fraction"] == .002
    assert replay_calls[0][1]["max_hours"] == 72
