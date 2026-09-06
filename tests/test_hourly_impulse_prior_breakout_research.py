"""Synthetic-only V14 support, immutable denominators and no-outcome boundary."""
import ast
import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_prior_breakout_research as subject


def context(counts=(20, 20, 20, 20)):
    rows = []
    for fold_index, (fold, start, _) in enumerate(subject.FOLDS):
        for i in range(65):
            rows.append({"event_id": "%s_%s" % (fold, i), "fold": fold,
                "population": "case", "direction": 1 if i % 2 else -1,
                "decision_time": pd.Timestamp(start, tz="UTC") + pd.DateOffset(months=i % 6, days=4),
                subject.GATE_COLUMN: "accepted" if i < counts[fold_index] else "abstain"})
    return pd.DataFrame(rows)


def test_all_four_inherited_gates_and_exact_boundary():
    values, gates = subject.support_gates(context())
    assert values == {"events": 80, "minimum_fold_events": 20, "active_months": 24,
                      "minimum_fold_months": 6}
    assert all(gates.values())


@pytest.mark.parametrize("counts", [(19,20,20,20), (11,23,23,23), (0,30,30,30)])
def test_counts_do_not_replace_fold_gate(counts):
    _, gates = subject.support_gates(context(counts))
    assert not all(gates.values())


def test_controls_unknown_or_zero_denominators_cannot_rescue_gate():
    cases = context((1,1,1,1))
    controls = context().assign(population="control")
    assert subject.support_gates(pd.concat([cases, controls]))[0]["events"] == 4
    unknown = context().assign(**{subject.GATE_COLUMN: "unknown"})
    values, gates = subject.support_gates(unknown)
    assert values["events"] == values["minimum_fold_events"] == 0
    assert not any(gates.values())


def test_active_month_support_is_not_trade_count():
    rows = context()
    rows["decision_time"] = rows.groupby("fold").decision_time.transform("min")
    values, gates = subject.support_gates(rows)
    assert values["events"] == 80
    assert values["active_months"] == 4
    assert not gates["minimum_active_months"]
    assert not gates["minimum_months_per_fold"]


def test_count_table_complete_zero_folds_and_months():
    frame = context((1,2,3,4))
    frame.loc[frame.index[:2], subject.GATE_COLUMN] = "unknown"
    result = subject.support_counts(frame)
    assert len(result) == 62
    assert result[["population", "dimension", "key"]].duplicated().sum() == 0
    assert len(result.query("dimension == 'month'")) == 48
    assert result.query("population == 'control'").total.eq(0).all()
    total = result.query("population == 'case' and dimension == 'all'").iloc[0]
    assert total.total == total.accepted + total.abstain + total.unknown == len(frame)
    for dimension in ("fold", "direction", "month"):
        assert result.query("population == 'case' and dimension == @dimension").total.sum() == len(frame)


@pytest.mark.parametrize("value", [None, "skipped", 0])
def test_missing_state_not_automatically_abstention(value):
    frame = context()
    frame.loc[0, subject.GATE_COLUMN] = value
    with pytest.raises(ValueError):
        subject.support_counts(frame)


def test_matched_abstentions_keep_original_triple():
    original = pd.DataFrame([{"event_id": "x", "fold": "2023H1", "match_status": "matched"}])
    cases = pd.DataFrame([{"event_id": "x", "population": "case", "parent_event_id": None,
                          subject.GATE_COLUMN: "abstain"}])
    controls = pd.DataFrame([{"event_id": "c%d" % i, "population": "control", "parent_event_id": "x",
        subject.GATE_COLUMN: state} for i, state in enumerate(subject.STATES)])
    out = subject.matched_support(pd.concat([cases, controls]), original)
    assert len(out) == 1 and out.iloc[0].control_total == 3
    assert out.iloc[0].control_ids == "c0|c1|c2"
    assert not out.iloc[0].all_known
    with pytest.raises(ValueError):
        subject.matched_support(pd.concat([cases, controls.iloc[:2]]), original)


@pytest.mark.parametrize("key", ["support", "gate", "development_folds", "matching_coverage"])
def test_frozen_contract_cannot_drift(key):
    base = json.loads((subject.ROOT / subject.BASE_CONFIG).read_text())
    subject.verify_config(subject.frozen_config(), base)
    config = deepcopy(subject.frozen_config())
    config[key] = {}
    with pytest.raises(ValueError):
        subject.verify_config(config, base)


def test_support_runner_has_no_outcome_call_or_parent_path():
    source = Path(subject.__file__).read_text()
    tree = ast.parse(source)
    banned = {"simulate_events", "simulate_requests", "replay_arm", "evaluate", "matched", "metrics",
              "paired_effects", "positive_inference", "gated_episodes"}
    calls = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
             for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}
    assert not calls & banned
    assert "colour-transition-preholdout" not in source
    assert set(subject.INPUTS) == {"original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv", "assignment_receipt.json"}
    assert subject.frozen_config()["no_outcome_entry_point"] is True


def test_source_windows_exports_no_future_or_incomplete_hour():
    raw = pd.DataFrame({"open_time": pd.date_range("2023-01-01", periods=24*12, freq="5min", tz="UTC"),
        "open": 10., "high": 12., "low": 8., "close": 11., "volume": 1.})
    signal = pd.Timestamp("2023-01-01 21:00", tz="UTC")
    requests = pd.DataFrame([{"event_id": "x", "population": "case", "signal_time": signal}])
    rows = subject.source_windows(requests, raw)
    assert len(rows) == 21
    assert rows.query("role == 'prior'").open_time.max() == signal-pd.Timedelta(hours=1)
    assert rows.query("role == 'k1'").open_time.tolist() == [signal]
    rows2 = subject.source_windows(requests, raw.drop(index=12*10))
    assert len(rows2) == 20 and not rows2.open_time.eq(pd.Timestamp("2023-01-01 10:00", tz="UTC")).any()
