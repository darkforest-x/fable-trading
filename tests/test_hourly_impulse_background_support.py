"""Synthetic-only V23 support and checkpoint contracts; no market files."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import hourly_impulse_background_support as study


def fixture(mother_count=1, candidate_count=3, direction=1):
    # All decisions occupy the same January UTC 00--06 block. Sparse dates
    # are legitimate matching opportunities; rolling features are supplied.
    times = pd.date_range("2023-01-02T01:00:00Z", periods=mother_count + candidate_count, freq="D")
    records = []
    for i, time in enumerate(times):
        row = {"open_time": time - pd.Timedelta(hours=1), "signal_time": time - pd.Timedelta(hours=1),
               "decision_time": time, "open": 100., "high": 103., "low": 99., "close": 102.,
               "signal_atr": 2., "entry_open": 102., "ma": 99., "ma_side": 1,
               "source_segment_id": 0, "entry_source_segment_id": 0,
               "known_5m_available": time, "known_5m_colour": 1, "known_hourly_colour": 1,
               "unsigned_hourly_slope_sign": 1, "month": time.strftime("%Y-%m"),
               "utc_6h_bucket": time.hour // 6, "vol_bucket": 1,
               "ma_slope_atr": .25, "long_close_location": .75, "short_close_location": .25}
        row.update({key: True for key in study.FLAGS})
        row.update(raw_strict_body_cross=False, current_or_prior_cross_excluded=False,
                   actual_mother_decision_excluded=i < mother_count, candidate_eligible=i >= mother_count)
        records.append(row)
    h = pd.DataFrame(records)
    mothers = pd.DataFrame([{"event_id": "mother" + str(i), "signal_time": time - pd.Timedelta(hours=1),
                             "decision_time": time, "direction": direction, "initial_stop": 100. if direction == 1 else 104.,
                             "signal_atr": 2., "fold": "2023H1", "original_extra": "preserve"}
                            for i, time in enumerate(times[:mother_count])])
    return mothers, h


def solve(mothers, frame):
    return study.allocate_support(study.build_support_graph(mothers, frame))


def test_frozen_config_is_exact_and_not_random_allocation():
    config = json.loads((study.ROOT / study.EXPERIMENT / "config.json").read_text())
    assert config == study.frozen_config()
    assert config["matching_keys"] == ["month", "utc_6h_bucket", "vol_bucket"]
    assert config["required_complete_mothers"] == 226
    assert config["seed"] is None
    assert not config["outcomes_read_or_computed"]


@pytest.mark.parametrize("direction", [1, -1])
def test_three_keys_own_control_diagnostics_and_original_parity(direction):
    mothers, h = fixture(direction=direction)
    h.loc[1:, ["known_5m_colour", "known_hourly_colour", "unsigned_hourly_slope_sign"]] = -1
    before_m, before_h = mothers.copy(deep=True), h.copy(deep=True)
    tables, summary = solve(mothers, h)
    assert summary["maximum_matched"] == 1
    assert summary["controls"] == 3
    assert not summary["coverage_gate_passed"]  # Never redefine 226/251 for a tiny fixture.
    controls = tables["controls"]
    assert controls.known_5m_colour.eq(-1).all()
    assert controls.signed_hourly_slope_sign.eq(-direction).all()
    assert controls.direction.eq(direction).all()
    assert np.allclose(direction * (controls.entry_open - controls.initial_stop) / controls.signal_atr, 1.)
    assert_frame_equal(tables["original_mothers"], before_m, check_exact=True)
    assert_frame_equal(tables["matching_frame"], before_h, check_exact=True)
    assert_frame_equal(mothers, before_m, check_exact=True)
    assert_frame_equal(h, before_h, check_exact=True)


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_complete_three_or_zero_no_partial_groups(count):
    mothers, h = fixture(candidate_count=count)
    tables, summary = solve(mothers, h)
    assert summary["maximum_matched"] == int(count >= 3)
    assert len(tables["controls"]) == (3 if count >= 3 else 0)
    assert len(tables["assignments"]) == 1


def test_opposite_directions_compete_for_same_true_timestamp():
    mothers, h = fixture(mother_count=2, candidate_count=5)
    mothers.loc[1, ["direction", "initial_stop"]] = [-1, 104.]
    tables, summary = solve(mothers, h)
    assert summary["maximum_matched"] == 1
    assert len(summary["components"]) == 1
    assert not tables["controls"].decision_time.duplicated().any()
    assert set(tables["assignments"].match_status) == {"matched", "shared_capacity_unmatched"}


def test_risk_validity_is_per_mother_edge_not_candidate_only():
    mothers, h = fixture(mother_count=2, candidate_count=3)
    mothers.loc[1, "initial_stop"] = 1.  # Large but valid original risk.
    h.loc[2:, "entry_open"] = 50.
    graph = study.build_support_graph(mothers, h)
    assert set(graph["eligible_edges"].event_id) == {"mother0"}
    stage = graph["stage_counts"].set_index("event_id")
    assert stage.loc["mother1", "after_actual_exclusion"] == 3
    assert stage.loc["mother1", "valid_transferred_stop"] == 0


def test_missing_volatility_support_retains_unknown_mother():
    mothers, h = fixture()
    h.loc[0, "vol_bucket"] = np.nan
    h.loc[0, "matching_support"] = False
    graph = study.build_support_graph(mothers, h)
    assert graph["eligible_edges"].empty
    assert graph["mother_support"].iloc[0].support_reason == "missing_causal_matching_support"
    assert pd.isna(graph["mother_support"].iloc[0].available_controls)
    assert graph["stage_counts"].drop(columns=["event_id", "fold"]).isna().all().all()
    assert len(graph["original_mothers"]) == 1


def test_cross_current_and_previous_and_actual_exclusion_are_retained():
    mothers, h = fixture(candidate_count=5)
    # Two consecutive hours so current crossing excludes the following hour.
    for column in ("open_time", "signal_time", "decision_time", "known_5m_available"):
        h.loc[2, column] = h.loc[1, column] + pd.Timedelta(hours=1)
    h.loc[1, "ma"] = 101.
    h.loc[1, "raw_strict_body_cross"] = True
    h.loc[[1, 2], "current_or_prior_cross_excluded"] = True
    h.loc[[1, 2], "candidate_eligible"] = False
    h.loc[3, ["actual_mother_decision_excluded", "candidate_eligible"]] = [True, False]
    graph = study.build_support_graph(mothers, h)
    assert len(graph["eligible_edges"]) == 2
    stage = graph["stage_counts"].iloc[0]
    assert stage.valid_support == 6
    assert stage.after_cross_exclusion == 4
    assert stage.after_actual_exclusion == 2


def test_strict_fold_end_embargo_and_same_month():
    mothers, h = fixture(candidate_count=3)
    offset = pd.Timestamp("2023-06-26T01:00:00Z") - h.decision_time.iloc[0]
    for column in ("open_time", "signal_time", "decision_time", "known_5m_available"):
        h[column] += offset
    for column in ("signal_time", "decision_time"):
        mothers[column] += offset
    h["month"] = "2023-06"
    graph = study.build_support_graph(mothers, h)
    assert graph["stage_counts"].iloc[0].within_fold_embargo == 2
    assert len(graph["eligible_edges"]) == 1  # June 28 01:00 is beyond June28 00 cutoff.
    mothers.loc[0, "decision_time"] = pd.Timestamp("2023-06-28T01:00:00Z")
    mothers.loc[0, "signal_time"] = pd.Timestamp("2023-06-28T00:00:00Z")
    assert study.build_support_graph(mothers, h)["mother_support"].iloc[0].support_reason == "outside_fold_embargo"


@pytest.mark.parametrize("mutation", ["duplicate_id", "duplicate_hour", "naive", "boolean_direction", "wrong_clock", "post2024", "false_key", "false_cross", "flag_string", "outcome", "bad_colour_clock", "bad_source"])
def test_malformed_input_fail_closed(mutation):
    mothers, h = fixture(mother_count=2)
    if mutation == "duplicate_id":
        mothers.loc[1, "event_id"] = mothers.loc[0, "event_id"]
    elif mutation == "duplicate_hour":
        h.loc[1, "decision_time"] = h.loc[0, "decision_time"]
    elif mutation == "naive":
        mothers["signal_time"] = mothers.signal_time.dt.tz_localize(None)
    elif mutation == "boolean_direction":
        mothers["direction"] = True
    elif mutation == "wrong_clock":
        mothers["decision_time"] += pd.Timedelta(hours=1)
    elif mutation == "post2024":
        for column in ("open_time", "signal_time", "decision_time", "known_5m_available"):
            h[column] += pd.Timedelta(days=800)
    elif mutation == "false_key":
        h.loc[1, "month"] = "2023-02"
    elif mutation == "false_cross":
        h.loc[1, "raw_strict_body_cross"] = True
    elif mutation == "flag_string":
        h["matching_support"] = "True"
    elif mutation == "outcome":
        mothers["net_return"] = .1
    elif mutation == "bad_colour_clock":
        h.loc[1, "known_5m_available"] -= pd.Timedelta(minutes=5)
    elif mutation == "bad_source":
        h.loc[1, "entry_source_segment_id"] = 999
    with pytest.raises(ValueError):
        study.build_support_graph(mothers, h)


def test_per_component_30_second_budget_and_no_solver_fallback(monkeypatch):
    mothers, h = fixture(mother_count=2, candidate_count=6)
    h.loc[1, "vol_bucket"] = 2
    h.loc[5:, "vol_bucket"] = 2
    actual = study.maximum_complete_matching
    calls = []
    def wrapped(ids, edges, **kwargs):
        calls.append((ids, kwargs))
        return actual(ids, edges, **kwargs)
    monkeypatch.setattr(study, "maximum_complete_matching", wrapped)
    tables, summary = solve(mothers, h)
    assert len(calls) == 2
    assert all(kwargs == {"count": 3, "time_limit": 30.0} for _, kwargs in calls)
    assert summary["maximum_matched"] == 2
    def failed(*args, **kwargs):
        raise RuntimeError("synthetic nonoptimal")
    monkeypatch.setattr(study, "maximum_complete_matching", failed)
    with pytest.raises(RuntimeError, match="nonoptimal"):
        solve(mothers, h)


def test_missing_mother_hour_and_invalid_risk_remain_rows():
    mothers, h = fixture(mother_count=2)
    mothers.loc[0, "initial_stop"] = 103.
    h = h.drop(index=1).reset_index(drop=True)
    tables, _ = solve(mothers, h)
    assert tables["assignments"].match_status.tolist() == ["invalid_mother_risk", "missing_mother_hourly_decision"]


def test_empty_preserves_schema():
    mothers, h = fixture()
    tables, summary = solve(mothers.iloc[:0], h)
    assert tables["assignments"].empty and tables["controls"].empty
    assert summary["mothers"] == 0 and summary["maximum_matched"] == 0


@pytest.mark.parametrize("column,value", [("candidate_time", pd.Timestamp("2023-03-01T00:00:00Z")),
                                         ("synthetic_stop", 999.), ("fold", "2024H1")])
def test_allocation_rejects_edited_graph_qualification(column, value):
    mothers, h = fixture()
    graph = study.build_support_graph(mothers, h)
    graph["eligible_edges"].loc[0, column] = value
    with pytest.raises(ValueError, match="rebuilt"):
        study.allocate_support(graph)


def test_json_nonfinite_is_null_not_nonstandard_nan(tmp_path):
    path = tmp_path / "receipt.json"
    study._write_json(path, {"missing": np.nan, "nested": [np.inf, pd.NA, np.int64(3)]})
    assert json.loads(path.read_text()) == {"missing": None, "nested": [None, None, 3]}
    assert "NaN" not in path.read_text() and "Infinity" not in path.read_text()


def prepare_run(tmp_path, monkeypatch):
    directory = tmp_path / study.EXPERIMENT
    directory.mkdir(parents=True)
    (directory / "config.json").write_text(json.dumps(study.frozen_config()))
    mothers, h = fixture()
    # Runner's population guard is tested separately. Synthetic full population
    # supplies required fold counts while graph construction is mocked here.
    full = pd.concat([mothers.assign(event_id=[str(i)], fold=fold) for i, fold in enumerate(
        ["2023H1"] * 55 + ["2023H2"] * 66 + ["2024H1"] * 55 + ["2024H2"] * 75)], ignore_index=True)
    graph = study.build_support_graph(mothers, h)
    calls = []
    monkeypatch.setattr(study, "committed_sources", lambda root: (calls.append("committed") or ("synthetic", [])))
    monkeypatch.setattr(study, "verify_saved_inputs", lambda root: (calls.append("verified") or {"saved_only": True}))
    monkeypatch.setattr(study.pd, "read_csv", lambda path: (calls.append("read") or (full if Path(path).name == "original_mothers.csv.gz" else h)))
    monkeypatch.setattr(study, "build_support_graph", lambda *args: graph)
    return directory / "results", calls


def test_runner_freezes_before_solver_and_saves_failure(tmp_path, monkeypatch):
    results, calls = prepare_run(tmp_path, monkeypatch)
    def failed(graph):
        assert (results / "support_frozen.json").exists()
        frozen = json.loads((results / "support_frozen.json").read_text())
        assert frozen["capacity_attempted"] is False
        assert frozen["outcomes_read_or_computed"] is False
        assert all(study._sha(results / name) == sha for name, sha in frozen["output_hashes"].items())
        raise RuntimeError("solver stopped")
    monkeypatch.setattr(study, "allocate_support", failed)
    with pytest.raises(RuntimeError, match="solver stopped"):
        study.run(tmp_path)
    assert calls[:4] == ["committed", "verified", "read", "read"]
    assert json.loads((results / "failure.json").read_text())["support_frozen"]
    assert not (results / "summary.json").exists()


def test_runner_refuses_overwrite_before_input_read(tmp_path, monkeypatch):
    results, calls = prepare_run(tmp_path, monkeypatch)
    results.mkdir()
    with pytest.raises(FileExistsError):
        study.run(tmp_path)
    assert calls == []


def test_runner_uncommitted_or_bad_lineage_never_reads_csv(tmp_path, monkeypatch):
    results, calls = prepare_run(tmp_path, monkeypatch)
    def fail(root):
        raise ValueError("not committed")
    monkeypatch.setattr(study, "committed_sources", fail)
    with pytest.raises(ValueError, match="not committed"):
        study.run(tmp_path)
    assert not results.exists() and calls == []
    monkeypatch.setattr(study, "committed_sources", lambda root: ("synthetic", []))
    monkeypatch.setattr(study, "verify_saved_inputs", fail)
    with pytest.raises(ValueError):
        study.run(tmp_path)
    assert calls == [] and (results / "failure.json").exists()


@pytest.mark.parametrize("mutation", [None, "hash", "cutoff", "source", "late_commit", "outcomes"])
def test_saved_provenance_checks_before_any_csv(tmp_path, monkeypatch, mutation):
    import hashlib
    source_bytes = b"synthetic source only"
    sources = [{"path": "synthetic.py", "sha256": hashlib.sha256(source_bytes).hexdigest()}]
    receipt = {"phase_price_last_open": "2024-12-31T23:55:00+00:00", "holdout_price_rows": 0,
               "timestamp_preflight_before_price_hash": True}
    directory = tmp_path / study.V10
    directory.mkdir(parents=True)
    mother_path = tmp_path / study.MOTHERS
    mother_path.parent.mkdir(parents=True)
    mother_path.write_bytes(b"not parsed csv")
    (directory / "matching_frame.csv.gz").write_bytes(b"not parsed matching")
    output_hashes = {"original_mothers.csv.gz": study._sha(mother_path),
                     "matching_frame.csv.gz": study._sha(directory / "matching_frame.csv.gz")}
    started = {"at": "2026-09-06T00:00:01Z", "builder_commit": "synthetic", "sources": sources,
               "inputs": {"original_mothers.csv.gz": output_hashes["original_mothers.csv.gz"]}}
    frozen = {"generated_at": "2026-09-06T00:00:02Z", "historical_full_parity": True,
              "original_assignment_feasible": True, "mothers": 251, "capacity_attempted": False,
              "source_receipt": receipt, "source_receipts": sources, "output_hashes": output_hashes}
    summary = {"generated_at": "2026-09-06T00:00:03Z", "mothers": 251, "outcomes_read_or_computed": False,
               "holdout_consumed": False, "source_receipt": receipt, "source_receipts": sources,
               "output_hashes": output_hashes}
    if mutation == "cutoff":
        receipt["phase_price_last_open"] = "2025-01-01T00:00:00Z"
    if mutation == "outcomes":
        summary["outcomes_read_or_computed"] = True
    for name, obj in [("started.json", started), ("support_frozen.json", frozen), ("summary.json", summary)]:
        (directory / name).write_text(json.dumps(obj))
    inputs = {str(path): study._sha(tmp_path / path) for path in
              [study.MOTHERS, study.V10 / "matching_frame.csv.gz", study.V10 / "started.json", study.V10 / "support_frozen.json", study.V10 / "summary.json"]}
    monkeypatch.setattr(study, "INPUTS", inputs)
    monkeypatch.setattr(study, "_git_bytes", lambda *args: b"bad source" if mutation == "source" else source_bytes)
    monkeypatch.setattr(study.subprocess, "check_output", lambda *args, **kwargs:
                        "2026-09-07T00:00:00Z" if mutation == "late_commit" else "2026-09-06T00:00:00Z")
    monkeypatch.setattr(study.pd, "read_csv", lambda *args, **kwargs: pytest.fail("metadata verification must not parse CSV"))
    if mutation == "hash":
        mother_path.write_bytes(b"changed")
    if mutation is None:
        result = study.verify_saved_inputs(tmp_path)
        assert result["saved_only"] and result["upstream_sources_verified_at_commit"] == 1
        assert not result["raw_aggregation_independently_recomputed"]
    else:
        with pytest.raises(ValueError):
            study.verify_saved_inputs(tmp_path)
