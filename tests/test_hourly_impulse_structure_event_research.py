"""V25 synthetic runner gates and I/O ordering; never read study data."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import hourly_impulse_structure_event_research as study


def trace_fixture():
    frame = pd.DataFrame({"open_time": pd.date_range("2024-01-01T00:00:00Z", periods=3, freq="h")})
    config = study.frozen_config()
    config.update(trace_rows=3, trace_start="2024-01-01T00:00:00Z", trace_end="2024-01-01T02:00:00Z")
    return frame, config


def populations():
    cases, controls = [], []
    for (fold, start, end), count, control_count in zip(study.DEFAULT_FOLDS, [55,66,55,75], [156,198,165,225]):
        for i in range(count):
            decision = pd.Timestamp(start)+pd.Timedelta(hours=48+i)
            cases.append({"event_id":"%s:m%d" % (fold,i),"signal_time":decision-pd.Timedelta(hours=1),
                          "decision_time":decision,"direction":1 if i%2 else -1,"fold":fold})
        for i in range(control_count):
            decision = pd.Timestamp(start)+pd.Timedelta(days=30,hours=i)
            controls.append({"event_id":"%s:c%d" % (fold,i),"signal_time":decision-pd.Timedelta(hours=1),
                             "decision_time":decision,"direction":1 if i%2 else -1,"fold":fold})
    cases = pd.DataFrame(cases);controls = pd.DataFrame(controls)
    return cases, controls, cases.copy(deep=True)


def test_preflight_only_uses_time_column_and_keeps_gaps():
    frame, config = trace_fixture()
    frame["unread_OHLC"] = [object(),None,{"bad":True}]
    before = frame.copy(deep=True)
    result = study.preflight_trace(frame, config)
    assert result.equals(pd.DatetimeIndex(frame.open_time))
    assert_frame_equal(before, frame)
    # Missing complete hours are allowed: state resets downstream, never fill.
    gap = frame.iloc[[0,2]].copy();config["trace_rows"] = 2
    assert len(study.preflight_trace(gap,config)) == 2


@pytest.mark.parametrize("mutation", ["count","empty","duplicate","reverse","start","end","phase","naive","epoch","offset","subhour","nanosecond","missing"])
def test_trace_preflight_rejects_invalid_clocks_before_ohlc(mutation):
    frame,config = trace_fixture()
    if mutation == "count":config["trace_rows"] = 4
    elif mutation == "empty":frame = frame.iloc[:0];config["trace_rows"] = 0
    elif mutation == "duplicate":frame.loc[1,"open_time"] = frame.loc[0,"open_time"]
    elif mutation == "reverse":frame = frame.iloc[::-1]
    elif mutation == "start":config["trace_start"] = "2023-12-31T23:00:00Z"
    elif mutation == "end":config["trace_end"] = "2024-01-01T03:00:00Z"
    elif mutation == "phase":config["phase_end_exclusive"] = config["trace_end"]
    else:
        bad = {"naive":"2024-01-01 01:00:00","epoch":1704070800,
               "offset":"2024-01-01T02:00:00+01:00","subhour":"2024-01-01T01:05:00Z",
               "nanosecond":"2024-01-01T01:00:00.000000001Z","missing":None}[mutation]
        frame["open_time"] = frame.open_time.astype(object);frame.loc[1,"open_time"] = bad
    with pytest.raises(ValueError):study.preflight_trace(frame,config)


def test_validate_original_251_identity_all_folds_and_744_controls():
    cases,controls,original = populations();before = [x.copy(deep=True) for x in (cases,controls,original)]
    study.validate_requests(cases,controls,original)
    # Identity is keyed, not dependent on CSV input row order.
    study.validate_requests(cases.iloc[::-1],controls.iloc[::-1],original)
    for x,y in zip((cases,controls,original),before):assert_frame_equal(x,y)


@pytest.mark.parametrize("mutation", ["case_count","control_count","original_count","id","duplicate_case","direction","clock","fold_count","unknown_control_fold","naive","subhour"])
def test_population_identity_and_clock_drift_rejected(mutation):
    cases,controls,original = populations()
    if mutation == "case_count":cases = cases.iloc[:-1]
    elif mutation == "control_count":controls = controls.iloc[:-1]
    elif mutation == "original_count":original = original.iloc[:-1]
    elif mutation == "id":cases.loc[0,"event_id"] = "different"
    elif mutation == "duplicate_case":cases.loc[0,"event_id"] = cases.loc[1,"event_id"]
    elif mutation == "direction":cases.loc[0,"direction"] *= -1
    elif mutation == "clock":cases.loc[0,"decision_time"] += pd.Timedelta(hours=1)
    elif mutation == "fold_count":cases.loc[0,"fold"] = original.loc[0,"fold"] = "2023H2"
    elif mutation == "unknown_control_fold":controls.loc[0,"fold"] = "unknown"
    elif mutation == "naive":controls["signal_time"] = controls.signal_time.astype(object);controls.loc[0,"signal_time"] = "2023-01-31 00:00:00"
    elif mutation == "subhour":controls.loc[0,"signal_time"] += pd.Timedelta(minutes=5)
    with pytest.raises((ValueError,AssertionError)):study.validate_requests(cases,controls,original)


@pytest.mark.parametrize("population", ["case","control"])
@pytest.mark.parametrize("location,allowed", [("start",True),("before_start",False),("before_embargo",True),("exact_embargo",False),("after_embargo",False)])
def test_halfyear_and_exact_72h_embargo(population,location,allowed):
    cases,controls,original = populations();frame = cases if population == "case" else controls
    start,end = pd.Timestamp(study.DEFAULT_FOLDS[0][1]),pd.Timestamp(study.DEFAULT_FOLDS[0][2])
    decision = {"start":start,"before_start":start-pd.Timedelta(hours=1),
                "before_embargo":end-pd.Timedelta(hours=73),"exact_embargo":end-pd.Timedelta(hours=72),
                "after_embargo":end-pd.Timedelta(hours=71)}[location]
    frame.loc[0,["signal_time","decision_time"]] = [decision-pd.Timedelta(hours=1),decision]
    if population == "case":original.loc[0,["signal_time","decision_time"]] = frame.loc[0,["signal_time","decision_time"]].to_numpy()
    if allowed:study.validate_requests(cases,controls,original)
    else:
        with pytest.raises(ValueError,match="72h"):study.validate_requests(cases,controls,original)


def mock_run(tmp_path,monkeypatch,fail_at=None):
    trace,config = trace_fixture();directory = tmp_path/study.EXPERIMENT
    directory.mkdir(parents=True);(directory/'config.json').write_text(json.dumps(config))
    calls=[];sentinel = pd.DataFrame({"event_id":["synthetic-only"]})
    monkeypatch.setattr(study,"frozen_config",lambda:config)
    def commit(root):
        calls.append("source_commit")
        if fail_at == "commit":raise ValueError("synthetic uncommitted source")
        if calls.count("source_commit") == 1:
            assert not (directory/'results').exists()
        else:
            assert 'support' in calls and not (directory/'results'/'support_frozen.json').exists()
        source_sha = 'changed' if fail_at == 'source_changed' and calls.count('source_commit') > 1 else 'not-market-data'
        return "synthetic-builder",[{"path":"synthetic.py","sha256":source_sha}]
    monkeypatch.setattr(study,"committed_sources",commit)
    def inputs(root):
        calls.append("input_hashes")
        assert (directory/'results'/'started.json').exists()
        if fail_at == "inputs":raise ValueError("synthetic input hash drift")
        if fail_at == 'inputs_changed' and calls.count('input_hashes') > 1:
            return {'inputs_verified':'different frozen input','outcomes_read':False}
        return {"inputs_verified":True,"outcomes_read":False}
    monkeypatch.setattr(study,"verify_inputs",inputs)
    allowed = {str(study.V24/n) for n in ('case_requests.csv.gz','control_requests.csv.gz','random_assignments.csv.gz','random_allocation.csv.gz')}
    def read(path,**kw):
        relative = str(Path(path).relative_to(tmp_path))
        calls.append(("read",relative,kw))
        assert "input_hashes" in calls
        if relative == str(study.V20/'hourly_trace.csv.gz'):
            assert kw.get("usecols") in (["open_time"],study.TRACE_COLUMNS)
            if kw["usecols"] == ["open_time"]:
                assert not any(isinstance(v,tuple) and v[0]=="read" and v[2].get("usecols")==study.TRACE_COLUMNS for v in calls[:-1])
                out=trace.copy()
                if fail_at == "preflight":out.loc[1,"open_time"] = out.loc[0,"open_time"]
                return out
            assert any(isinstance(v,tuple) and v[0]=="read" and v[2].get("usecols")==["open_time"] for v in calls[:-1])
            if fail_at == "trace_read":raise OSError("synthetic OHLC read failure")
            out=trace.copy();out["open"] = 100.
            if fail_at == "trace_clock_drift":out.loc[1,"open_time"] += pd.Timedelta(hours=1)
            return out
        if relative in allowed:
            assert not kw
            return sentinel.copy()
        if relative == str(study.MOTHERS):
            assert kw == {"usecols":study.IDENTITY}
            return sentinel.copy()
        raise AssertionError("Attempt to read an unapproved data/label/outcome path: "+relative)
    monkeypatch.setattr(study.pd,"read_csv",read)
    def validate(c,k,o):
        calls.append("validate_requests")
        if fail_at == "identity":raise ValueError("synthetic identity drift")
    monkeypatch.setattr(study,"validate_requests",validate)
    def support(c,k,t,a,m):
        calls.append("support")
        assert calls[-2] == "validate_requests"
        assert not (directory/'results'/'support_frozen.json').exists()
        if fail_at == "support":raise ValueError("synthetic support failure")
        tables={name:pd.DataFrame({"identity":["synthetic"],"unknown":[True]}) for name in ('case_context','control_context','counts','matched_support')}
        return tables,{"status":"insufficient_support_no_outcomes","support_pass":False,
                       "outcomes_read_or_computed":False,"economic_acceptance":False}
    monkeypatch.setattr(study,"build_structure_event_support",support)
    return directory,calls,config


def test_run_frozen_sources_before_any_results_or_input_reads(tmp_path,monkeypatch):
    directory,calls,_ = mock_run(tmp_path,monkeypatch,fail_at="commit")
    with pytest.raises(ValueError,match="uncommitted"):study.run(tmp_path)
    assert calls == ["source_commit"] and not (directory/'results').exists()


def test_config_drift_stops_before_source_gate_or_results(tmp_path,monkeypatch):
    directory,calls,config = mock_run(tmp_path,monkeypatch)
    changed=dict(config);changed["embargo_hours"] = 71
    (directory/'config.json').write_text(json.dumps(changed))
    with pytest.raises(ValueError,match="configuration"):study.run(tmp_path)
    assert calls == [] and not (directory/'results').exists()


def test_existing_results_refused_without_any_overwrite(tmp_path,monkeypatch):
    directory,calls,_ = mock_run(tmp_path,monkeypatch)
    monkeypatch.setattr(study,"committed_sources",lambda root:("synthetic",[]))
    result=directory/'results';result.mkdir();marker=result/'failure.json';marker.write_bytes(b'original-failure-evidence')
    with pytest.raises(FileExistsError):study.run(tmp_path)
    assert marker.read_bytes()==b'original-failure-evidence' and list(result.iterdir())==[marker]
    assert calls == []


def test_mock_run_time_only_then_ohlc_and_never_labels(tmp_path,monkeypatch):
    directory,calls,_ = mock_run(tmp_path,monkeypatch)
    summary=study.run(tmp_path);result=directory/'results'
    reads=[v for v in calls if isinstance(v,tuple)]
    assert calls[:2] == ["source_commit","input_hashes"]
    assert len(reads)==7 and reads[0][2]=={"usecols":["open_time"]} and reads[1][2]=={"usecols":study.TRACE_COLUMNS}
    assert not any(any(x in v[1] for x in ('labels','trades','episodes','open_prefix','archive')) for v in reads)
    assert calls[-4:] == ["validate_requests","support","input_hashes","source_commit"]
    assert summary['outcomes_read_or_computed'] is False and summary['economic_acceptance'] is False
    assert set(summary['output_hashes']) == {n+'.csv.gz' for n in ('case_context','control_context','counts','matched_support')}
    for name,sha in summary['output_hashes'].items():assert hashlib.sha256((result/name).read_bytes()).hexdigest()==sha
    frozen=json.loads((result/'support_frozen.json').read_text())
    assert frozen['output_hashes']==summary['output_hashes'] and frozen['timestamp_preflight_before_prices']
    assert not frozen['outcomes_read_or_computed'] and not frozen['raw5_read'] and not frozen['holdout_consumed']
    assert summary['support_frozen_sha256']==hashlib.sha256((result/'support_frozen.json').read_bytes()).hexdigest()
    assert not (result/'failure.json').exists()


@pytest.mark.parametrize("stage", ["inputs","preflight","trace_read","trace_clock_drift","identity","support","inputs_changed","source_changed"])
def test_failure_preserves_started_and_failure_and_rerun_refuses(tmp_path,monkeypatch,stage):
    directory,calls,_ = mock_run(tmp_path,monkeypatch,fail_at=stage)
    with pytest.raises((ValueError,OSError)):study.run(tmp_path)
    result=directory/'results';assert (result/'started.json').exists()
    failed=json.loads((result/'failure.json').read_text())
    assert failed['status']=='failed_not_evidence' and failed['error_type'] in ('ValueError','OSError')
    assert not (result/'summary.json').exists() and not (result/'support_frozen.json').exists()
    if stage in ('inputs','preflight'):
        assert not any(isinstance(v,tuple) and v[2].get('usecols')==study.TRACE_COLUMNS for v in calls)
    if stage in ('inputs','preflight','trace_read','trace_clock_drift','identity'):assert 'support' not in calls
    if stage in ('inputs_changed','source_changed'):
        assert 'support' in calls and calls.count('input_hashes') == 2
        assert not list(result.glob('*.csv.gz'))
        if stage == 'source_changed':assert calls.count('source_commit') == 2
    snapshot={p.name:p.read_bytes() for p in result.iterdir()}
    monkeypatch.setattr(study,'committed_sources',lambda root:('synthetic',[]))
    with pytest.raises(FileExistsError):study.run(tmp_path)
    assert {p.name:p.read_bytes() for p in result.iterdir()}==snapshot


def test_committed_sources_compares_disk_to_git_without_market_io(tmp_path,monkeypatch):
    path=tmp_path/'synthetic.py';path.write_bytes(b'committed')
    monkeypatch.setattr(study,'SOURCES',['synthetic.py'])
    monkeypatch.setattr(study,'_git',lambda root,*args:b'fake-commit\n' if args==('rev-parse','HEAD') else b'committed')
    commit,receipt=study.committed_sources(tmp_path)
    assert commit=='fake-commit' and receipt==[{'path':'synthetic.py','sha256':hashlib.sha256(b'committed').hexdigest()}]
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='Uncommitted'):study.committed_sources(tmp_path)
