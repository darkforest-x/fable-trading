"""V24 synthetic-only sampling, OPEN loader and checkpoint ordering tests."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from yoyo.evaluation import hourly_impulse_fixed_clock_research as study


def fixture():
    times = pd.to_datetime(["2023-01-02T01:00:00Z", "2023-01-03T01:00:00Z", "2023-01-04T01:00:00Z"])
    m = pd.DataFrame({"event_id": ["B", "A", "C"], "signal_time": times-pd.Timedelta(hours=1),
                      "decision_time": times, "direction": [1, -1, 1], "fold": "2023H1",
                      "initial_stop": [90., 110., 90.], "signal_atr": 2., "owner_field": "keep"}, index=[7, 3, 9])
    candidates = pd.date_range("2023-01-05T01:00:00Z", periods=10, freq="D")
    edges = pd.DataFrame([{"event_id": event, "candidate_id": time.isoformat(), "candidate_time": time,
                           "fold": "2023H1", "synthetic_stop": 90., "mother_risk_atr": 1.}
                          for event in ["B", "A"] for time in candidates])
    support = pd.DataFrame({"event_id": ["B", "A", "C"], "support_reason": ["eligible", "eligible", "missing_causal_matching_support"],
                            "available_controls": [10., 10., np.nan]})
    return m, edges, support


def test_config_exact_and_label_contract_not_profit_contract():
    saved = json.loads((study.ROOT / study.EXPERIMENT / "config.json").read_text())
    assert saved == study.frozen_config()
    assert saved["primary_horizon_hours"] == 4 and saved["horizons_hours"] == [1, 4, 12, 24]
    assert saved["sampling"]["seed"] == 20260907 and saved["sampling"]["streams"] == 1
    assert not saved["inference_executed_by_runner"] and not saved["execution_pnl"]


def test_exact_single_stream_sorted_components_and_no_witness_use():
    mothers, edges, support = fixture()
    before = mothers.copy(deep=True)
    tables, receipt = study.sample_controls(mothers, edges, support)
    expected = np.random.Generator(np.random.PCG64(20260907)).permutation(sorted(edges.candidate_id.unique())).tolist()
    allocation = tables["random_allocation"]
    assert allocation.event_id.tolist() == ["A"]*3 + ["B"]*3
    assert allocation.candidate_id.tolist() == expected[:6]
    assert allocation.control_slot.tolist() == [0, 1, 2, 0, 1, 2]
    assert not allocation.candidate_time.duplicated().any()
    assert receipt["matched_mothers"] == 2 and receipt["unmatched_mothers"] == 1
    assert receipt["controls"] == 6 and not receipt["outcomes_used"]
    assert_frame_equal(tables["original_mothers"], before, check_exact=True)
    assert_frame_equal(tables["case_requests"][before.columns], before, check_exact=True)
    assert_frame_equal(mothers, before, check_exact=True)
    assert tables["random_assignments"].event_id.tolist() == mothers.event_id.tolist()
    assert tables["case_requests"].matched_support.tolist() == [True, True, False]
    assert tables["control_requests"].groupby("mother_id").direction.first().to_dict() == {"A": -1, "B": 1}


def test_input_order_and_nonselection_fields_cannot_change_draws():
    mothers, edges, support = fixture()
    expected, _ = study.sample_controls(mothers, edges, support)
    changed = mothers.iloc[::-1].assign(irrelevant_future_winner=[True, False, True], signal_atr=99.)
    actual, _ = study.sample_controls(changed, edges.iloc[::-1], support.iloc[::-1])
    assert_frame_equal(actual["random_allocation"], expected["random_allocation"])


def test_two_components_share_one_rng_stream():
    mothers, edges, support = fixture()
    second = mothers.iloc[:1].copy().assign(event_id="Z")
    for column in ("signal_time", "decision_time"):
        second[column] += pd.Timedelta(days=31)
    other = edges.loc[edges.event_id.eq("A")].copy().assign(event_id="Z")
    other["candidate_time"] += pd.Timedelta(days=31)
    other["candidate_id"] = other.candidate_time.map(lambda x:x.isoformat())
    combined = pd.concat([mothers, second])
    s = pd.concat([support, pd.DataFrame([dict(event_id="Z", support_reason="eligible", available_controls=10)])])
    tables, _ = study.sample_controls(combined, pd.concat([edges, other]), s)
    rng = np.random.Generator(np.random.PCG64(20260907))
    rng.permutation(sorted(edges.candidate_id.unique()))
    expected = rng.permutation(sorted(other.candidate_id)).tolist()[:3]
    assert tables["random_allocation"].query("event_id == 'Z'").candidate_id.tolist() == expected


@pytest.mark.parametrize("mutation", ["not_complete", "too_few", "duplicate", "time", "fold", "actual", "support", "bool_direction", "naive", "late_fold", "bad_stop"])
def test_sampling_fail_closed_without_seed_retry(mutation):
    mothers, edges, support = fixture()
    if mutation == "not_complete": edges = edges.iloc[1:]
    elif mutation == "too_few":
        chosen = edges.candidate_id.unique()[:5]
        edges = edges.loc[edges.candidate_id.isin(chosen)]
        support.loc[support.support_reason.eq("eligible"), "available_controls"] = 5
    elif mutation == "duplicate": edges = pd.concat([edges, edges.iloc[:1]])
    elif mutation == "time": edges.loc[0, "candidate_time"] += pd.Timedelta(hours=1)
    elif mutation == "fold": edges.loc[0, "fold"] = "2024H1"
    elif mutation == "actual":
        edges.loc[0, "candidate_time"] = mothers.decision_time.iloc[0]
        edges.loc[0, "candidate_id"] = mothers.decision_time.iloc[0].isoformat()
    elif mutation == "support": support.loc[0, "available_controls"] = 11
    elif mutation == "bool_direction": mothers["direction"] = True
    elif mutation == "naive": mothers["decision_time"] = mothers.decision_time.dt.tz_localize(None)
    elif mutation == "late_fold":
        mothers.loc[7, "decision_time"] = pd.Timestamp("2023-06-28T00:00:00Z")
        mothers.loc[7, "signal_time"] = pd.Timestamp("2023-06-27T23:00:00Z")
    elif mutation == "bad_stop": edges.loc[0, "synthetic_stop"] = 0
    with pytest.raises(ValueError): study.sample_controls(mothers, edges, support)


def source_fixture(tmp_path, *, bad_hlc=False, bad_open=False):
    times = pd.date_range("2024-12-31T23:40:00Z", periods=7, freq="5min")
    raw = pd.DataFrame({"open_time":times, "open":["100.2000"]*7})
    if bad_open: raw.loc[2, "open"] = "bad"
    if bad_hlc: raw = raw.assign(high="bad", low="bad", close="bad")
    path = tmp_path / "source.csv"
    raw.to_csv(path,index=False)
    source = {"path":"source.csv", "audit":"audit.json", "sha256":study.digest(path), "end_exclusive":"2026-02-28T16:00:00Z"}
    audit = {"status":"complete", "holdout_ohlcv_rows_materialized":0, "output_sha256":source["sha256"],
             "first_time":str(times.min()), "last_time":str(times.max()), "rows":len(times)}
    auditpath = tmp_path / "audit.json"
    auditpath.write_text(json.dumps(audit))
    return source, study.digest(auditpath)


def test_loader_order_nrows_lexical_quotes_and_no_hlc(tmp_path, monkeypatch):
    source, auditsha = source_fixture(tmp_path, bad_hlc=True, bad_open=True)
    read, digest = study.pd.read_csv, study.digest
    calls = []
    def spy_read(path, **kwargs):
        calls.append(("read", kwargs.copy()))
        return read(path, **kwargs)
    def spy_hash(path):
        calls.append(("hash", Path(path).name))
        return digest(path)
    monkeypatch.setattr(study.pd,"read_csv",spy_read)
    monkeypatch.setattr(study,"digest",spy_hash)
    raw, receipt = study.load_open_source(tmp_path, source, auditsha)
    assert list(raw) == ["open_time","open"] and len(raw) == 4
    assert raw.open_time.max() == pd.Timestamp("2024-12-31T23:55:00Z")
    assert raw.open.tolist() == ["100.2000","100.2000","bad","100.2000"]
    assert calls[1] == ("read", {"usecols":["open_time"],"dtype":str,"keep_default_na":False})
    assert calls[2] == ("hash","source.csv")
    assert calls[3][0] == "read" and calls[3][1]["nrows"] == 4
    assert calls[3][1]["usecols"] == ["open_time","open"]
    assert receipt["post2024_price_rows"] == 0 and receipt["rows_dropped_for_hlc_or_open_validity"] == 0


@pytest.mark.parametrize("failure", ["audit_hash", "archive_hash", "audit_holdout", "duplicate_time", "unsorted", "naive", "off_grid"])
def test_bad_source_never_materializes_open_prefix(tmp_path, monkeypatch, failure):
    source, auditsha = source_fixture(tmp_path)
    if failure == "audit_hash": auditsha = "0"*64
    elif failure == "archive_hash":
        with (tmp_path / "source.csv").open("a") as f: f.write("\n")
    elif failure == "audit_holdout":
        path = tmp_path / "audit.json"; obj = json.loads(path.read_text()); obj["holdout_ohlcv_rows_materialized"] = 1
        path.write_text(json.dumps(obj)); auditsha = study.digest(path)
    read = study.pd.read_csv
    columns = []
    def spy(path, **kwargs):
        columns.append(kwargs["usecols"])
        frame = read(path, **kwargs)
        if kwargs["usecols"] == ["open_time"]:
            if failure == "duplicate_time": frame.iloc[1,0] = frame.iloc[0,0]
            elif failure == "unsorted": frame = frame.iloc[::-1]
            elif failure == "naive": frame.iloc[0,0] = "2024-12-31 23:40:00"
            elif failure == "off_grid": frame.iloc[0,0] = "2024-12-31T23:41:00Z"
        return frame
    monkeypatch.setattr(study.pd,"read_csv",spy)
    with pytest.raises(ValueError): study.load_open_source(tmp_path,source,auditsha)
    assert ["open_time","open"] not in columns


def labeled_fixture():
    mothers, edges, support = fixture()
    tables, _ = study.sample_controls(mothers,edges,support)
    times = pd.date_range("2023-01-01T00:00:00Z","2023-01-20T00:00:00Z",freq="5min")
    raw = pd.DataFrame({"open_time":times,"open":"100.0"})
    cases = study.label_requests(raw,tables["case_requests"])
    controls = study.label_requests(raw,tables["control_requests"])
    return tables, cases, controls


def test_all_mothers_labels_retained_unmatched_pair_only_unknown():
    tables, cases, controls = labeled_fixture()
    pairs = study.paired_labels(cases,controls)
    assert len(cases) == 12 and len(controls) == 24 and len(pairs) == 12
    assert cases.status.eq("known").all()
    unmatched = pairs.loc[pairs.event_id.eq("C")]
    assert unmatched.case_known.all() and not unmatched.pair_complete.any()
    assert unmatched.pair_reason.eq("unmatched_support").all() and unmatched.cost_threshold_excess_markout.isna().all()
    assert pairs.loc[pairs.event_id.ne("C"),"pair_complete"].all()
    assert pairs.loc[pairs.pair_complete,"cost_threshold_excess_markout"].eq(0).all()
    assert set(cases.role) == {"primary","descriptive"} and cases.request_kind.eq("case").all()
    summary = study.descriptive_summary(cases,controls,pairs)
    assert all(row["all_cases"]==3 and row["complete_pairs"]==2 for row in summary)


def test_unknown_control_never_redraws_or_uses_partial_mean():
    tables, cases, controls = labeled_fixture()
    controls.loc[controls.index[0],["status","reason","gross_markout","cost_threshold_markout"]] = ["unknown","missing_bar",np.nan,np.nan]
    pairs = study.paired_labels(cases,controls)
    row = pairs.loc[pairs.event_id.eq(controls.iloc[0].mother_id)&pairs.horizon_hours.eq(1)].iloc[0]
    assert row.n_controls_assigned == 3 and row.n_controls_known == 2
    assert not row.pair_complete and pd.isna(row.control_mean_gross_markout)
    assert row.pair_reason == "unknown_control_label"
    assert len(tables["random_allocation"]) == 6


def test_unknown_case_keeps_known_control_mean_but_not_excess():
    _, cases, controls = labeled_fixture()
    idx = cases.index[cases.event_id.eq("A") & cases.horizon_hours.eq(4)][0]
    cases.loc[idx,["status","reason","gross_markout","cost_threshold_markout"]] = ["unknown","invalid_open",np.nan,np.nan]
    row = study.paired_labels(cases,controls).loc[lambda f:f.event_id.eq("A")&f.horizon_hours.eq(4)].iloc[0]
    assert row.n_controls_known == 3 and np.isfinite(row.control_mean_cost_threshold_markout)
    assert not row.pair_complete and pd.isna(row.cost_threshold_excess_markout)


def prepare_runner(tmp_path,monkeypatch):
    directory = tmp_path / study.EXPERIMENT
    directory.mkdir(parents=True)
    (directory/"config.json").write_text(json.dumps(study.frozen_config()))
    mothers, edges, support = fixture()
    tables, sampling = study.sample_controls(mothers,edges,support)
    fake = pd.concat([mothers.iloc[:1].assign(event_id=str(i),fold=fold) for i,fold in enumerate(
        ["2023H1"]*55+["2023H2"]*66+["2024H1"]*55+["2024H2"]*75)],ignore_index=True)
    calls=[]
    monkeypatch.setattr(study,"committed_sources",lambda root:(calls.append("committed") or ("fake",[])))
    monkeypatch.setattr(study,"verify_inputs",lambda root:(calls.append("pins") or {}))
    def reader(path,**kwargs):
        calls.append(Path(path).name)
        if Path(path).name=="original_mothers.csv.gz": return fake.copy()
        if Path(path).name=="eligible_edges.csv.gz": return pd.DataFrame(index=range(13192))
        return support
    monkeypatch.setattr(study.pd,"read_csv",reader)
    monkeypatch.setattr(study,"sample_controls",lambda *args:(tables,{**sampling,"matched_mothers":248,"controls":744}))
    return directory/"results",calls


def test_runner_freezes_before_even_raw_timestamp_preflight(tmp_path,monkeypatch):
    results,calls=prepare_runner(tmp_path,monkeypatch)
    def loader(*args):
        calls.append("raw_time_preflight")
        frozen=json.loads((results/"sampling_frozen.json").read_text())
        assert frozen["before_any_raw_read"] and frozen["before_any_label"]
        assert all(study.digest(results/name)==sha for name,sha in frozen["output_hashes"].items())
        raise ValueError("synthetic loader stop")
    monkeypatch.setattr(study,"load_open_source",loader)
    with pytest.raises(ValueError,match="loader stop"): study.run(tmp_path)
    assert calls[:2]==["committed","pins"] and calls[-1]=="raw_time_preflight"
    assert json.loads((results/"failure.json").read_text())["sampling_frozen"]
    assert not (results/"summary.json").exists()


def test_runner_source_guard_and_refuse_overwrite(tmp_path,monkeypatch):
    results,calls=prepare_runner(tmp_path,monkeypatch)
    def stop(*args): raise ValueError("guard")
    monkeypatch.setattr(study,"committed_sources",stop)
    with pytest.raises(ValueError,match="guard"): study.run(tmp_path)
    assert not results.exists() and calls==[]
    results.mkdir()
    with pytest.raises(FileExistsError): study.run(tmp_path)
    assert calls==[]


def test_input_hash_failure_precedes_metadata_or_raw(tmp_path,monkeypatch):
    file=tmp_path/"input.txt"; file.write_text("input")
    monkeypatch.setattr(study,"INPUTS",{"input.txt":"0"*64})
    monkeypatch.setattr(study.pd,"read_csv",lambda *args,**kwargs:pytest.fail("must not parse"))
    with pytest.raises(ValueError,match="SHA mismatch"): study.verify_inputs(tmp_path)
