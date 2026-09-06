"""Synthetic V22 arithmetic and input/outcome locks; no actual market data."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_breadth_change_research as r


def change_context(now=10, previous=-10, direction=1, known=True):
    value = (now-previous)*4 if known else np.nan
    row = dict(direction=direction, breadth_known=known,
        breadth_raw_sum_change=value, breadth_mean_now=now/50 if known else np.nan,
        breadth_mean_previous=previous/50 if known else np.nan,
        breadth_change=value/200, breadth_score=value/400,
        breadth_gate_state="unknown" if not known else "accepted" if direction*value > 0 else "abstain")
    for symbol in r.BREADTH_SYMBOLS:
        row[f"breadth_{symbol}_score"] = now
        row[f"breadth_{symbol}_previous_score"] = previous
    return pd.DataFrame([row])


@pytest.mark.parametrize("now,previous", [(50,-50),(-50,50),(2,0),(0,0),(50,50),(-20,-40)])
@pytest.mark.parametrize("direction", [-1,1])
def test_exact_half_change_and_sign(now, previous, direction):
    frame = change_context(now, previous, direction)
    original = frame.copy(deep=True)
    r.validate_change_accounting(frame)
    pd.testing.assert_frame_equal(frame, original)
    assert frame.breadth_score.iloc[0]*2 == frame.breadth_change.iloc[0]


def test_unknown_keeps_aggregate_nan_even_if_one_side_diagnostics_present():
    frame = change_context(known=False)
    r.validate_change_accounting(frame)
    frame.loc[0, "breadth_score"] = 0
    with pytest.raises(ValueError, match="Unknown"):
        r.validate_change_accounting(frame)


@pytest.mark.parametrize("field", ["breadth_raw_sum_change", "breadth_mean_now",
    "breadth_mean_previous", "breadth_change", "breadth_score"])
def test_scaling_drift_rejected(field):
    frame = change_context()
    frame[field] = frame[field].astype(float)
    frame.loc[0, field] += .001
    with pytest.raises(AssertionError, match="exact rank/change"):
        r.validate_change_accounting(frame)


@pytest.mark.parametrize("bad", [1, 51, -51, np.inf, np.nan, True, "10"])
def test_bad_one_of_eight_rank_scores_rejected(bad):
    frame = change_context()
    frame["breadth_XRPUSDT_previous_score"] = bad
    with pytest.raises(ValueError):
        r.validate_change_accounting(frame)


def test_forged_gate_rejected_even_when_absolute_mean_aligns():
    frame = change_context(20,40)
    assert frame.breadth_mean_now.iloc[0] > 0
    frame.loc[0, "breadth_gate_state"] = "accepted"
    with pytest.raises(ValueError, match="Integer"):
        r.validate_change_accounting(frame)


def test_missing_raw_diagnostics_rejected():
    with pytest.raises(ValueError, match="Full V22"):
        r.validate_change_accounting(change_context().drop(columns="breadth_XRPUSDT_previous_score"))


def test_no_float_cancellation_at_integer_zero():
    frame = change_context(0,0)
    # Different per-asset values, but the same integer sum, are exactly zero.
    for symbol, now, previous in zip(r.BREADTH_SYMBOLS, [2,4,6,8], [8,6,4,2]):
        frame[f"breadth_{symbol}_score"] = now
        frame[f"breadth_{symbol}_previous_score"] = previous
    frame.breadth_mean_now = frame.breadth_mean_previous = .1
    r.validate_change_accounting(frame)
    assert frame.breadth_gate_state.iloc[0] == "abstain"


def test_config_has_exactly_one_change_without_changed_exit():
    cfg = r.frozen_config()
    base = dict(development_folds=r.parent.FOLDS,
        execution=dict(max_hours=72, cost_fraction=.002, stop_first=True))
    r.verify_config(cfg, base)
    assert cfg["fixed_execution"] == r.parent.frozen_config()["fixed_execution"]
    assert cfg["gate"]["change_hours"] == 1
    assert cfg["gate"]["absolute_mean_alignment"] is False
    assert cfg["matching_coverage_required"] == .9
    bad = deepcopy(cfg)
    bad["gate"]["change_hours"] = 2
    with pytest.raises(ValueError, match="Frozen V22"):
        r.verify_config(bad, base)


def trace_files(tmp_path, monkeypatch, *, time="2023-01-01T00:00:00Z"):
    folder = tmp_path/r.TRACE_PARENT
    folder.mkdir(parents=True)
    trace_path = folder/"external_hourly_trace.csv.gz"
    # Synthetic metadata only; forbidden price cells never need to parse.
    pd.DataFrame(dict(symbol=["ETHUSDT"], open_time=[time], high=["NO_PRICE_PARSE"])).to_csv(trace_path, index=False)
    receipt_path = folder/"context_frozen.json"
    r.write_json(receipt_path, dict(requests=713, outcomes_read=False,
        output_hashes={trace_path.name:r.digest(trace_path)}))
    monkeypatch.setattr(r, "FREEZE_SHA", r.digest(receipt_path))
    monkeypatch.setattr(r, "TRACE_SHA", r.digest(trace_path))
    return folder


def test_bad_lineage_prevents_even_metadata_csv_read(tmp_path, monkeypatch):
    trace_files(tmp_path, monkeypatch)
    monkeypatch.setattr(r, "FREEZE_SHA", "not-real")
    monkeypatch.setattr(r.pd, "read_csv", lambda *a, **k: pytest.fail("No CSV before hash"))
    with pytest.raises(ValueError, match="freeze hash"):
        r.load_trace(tmp_path)


@pytest.mark.parametrize("time", ["2025-01-01T00:00:00Z", "2023-01-01", "2023-01-01T00:05:00Z"])
def test_invalid_metadata_stops_before_any_price_parse(tmp_path, monkeypatch, time):
    trace_files(tmp_path, monkeypatch, time=time)
    real, calls = pd.read_csv, []
    def metadata_only(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs == dict(usecols=["symbol","open_time"])
        return real(*args, **kwargs)
    monkeypatch.setattr(r.pd, "read_csv", metadata_only)
    with pytest.raises(ValueError):
        r.load_trace(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("summary", [dict(support_pass=False,support_gates={}),
    dict(support_pass=True,support_gates={"minimum_events":False})])
def test_failed_support_prevents_any_outcome_or_context_access(tmp_path, monkeypatch, summary):
    monkeypatch.setattr(r, "validate_change_accounting", lambda *a:pytest.fail("No context needed"))
    monkeypatch.setattr(r.parent, "read_outcomes_after_freeze", lambda *a:pytest.fail("No outcome"))
    with pytest.raises(ValueError, match="support"):
        r.read_outcomes_after_freeze(tmp_path, summary, pd.DataFrame())
    assert list(tmp_path.iterdir()) == []


def test_malformed_scaling_cannot_reach_cached_outcomes(tmp_path, monkeypatch):
    frame = change_context()
    frame.breadth_score = .9
    monkeypatch.setattr(r.parent, "read_outcomes_after_freeze", lambda *a:pytest.fail("No outcome"))
    with pytest.raises(AssertionError):
        r.read_outcomes_after_freeze(tmp_path, dict(support_pass=True,support_gates={"a":True}), frame)


def test_outcome_delegation_preserves_context_and_declares_new_semantics(tmp_path, monkeypatch):
    frame = change_context()
    seen = []
    def existing(results, summary, context):
        seen.append(context)
        return {}, {"baseline_saved_matching_and_serial_parity":True}
    monkeypatch.setattr(r.parent, "read_outcomes_after_freeze", existing)
    _, summary = r.read_outcomes_after_freeze(tmp_path, dict(support_pass=True,support_gates={"a":True}), frame)
    assert seen[0] is frame
    assert summary["gate_semantics"] == "rank_sum_change_over400_not_absolute_mean"
