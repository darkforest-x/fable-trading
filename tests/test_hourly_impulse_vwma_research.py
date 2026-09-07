"""V26 synthetic reader ordering, receipt and timestamp guards."""
import hashlib
import json

import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_vwma_research as study


def test_exact_frozen_configuration_and_safe_inputs():
    config=study.frozen_config()
    assert len(study.INPUTS)==10 and len(study.SOURCES)==12
    assert config["baseline"]["max_cross_count"]==999
    assert config["baseline"]["max_extension_atr"]==99
    assert not config["baseline"]["require_ma_slope"]
    assert not config["outcomes_read_or_computed"] and not config["holdout_consumed"]
    assert all("episodes" not in p and "raw5" not in p for p in study.INPUTS)


def fixture(monkeypatch):
    times=pd.date_range("2024-01-01T00:00:00Z",periods=3,freq="h")
    monkeypatch.setattr(study,"opportunity_grid",lambda:pd.DataFrame({"signal_time":times[1:]}))
    config=study.frozen_config();config.update(trace_rows=3,trace_first_open=times[0].isoformat(),trace_last_open=times[-1].isoformat())
    return pd.DataFrame({"open_time":times,"open":[object()]*3}),config


def test_time_only_preflight(monkeypatch):
    frame,config=fixture(monkeypatch)
    assert len(study.timestamp_preflight(frame,config))==3


@pytest.mark.parametrize("mutation",["order","duplicate","count","phase","first","last","naive","subhour","outer_range"])
def test_invalid_preflight(monkeypatch,mutation):
    frame,config=fixture(monkeypatch)
    if mutation=="order":frame=frame.iloc[::-1]
    elif mutation=="duplicate":frame.loc[1,"open_time"]=frame.loc[0,"open_time"]
    elif mutation=="count":config["trace_rows"]=4
    elif mutation=="phase":config["phase_end_exclusive"]=config["trace_last_open"]
    elif mutation=="first":config["trace_first_open"]="2023-12-31T23:00:00Z"
    elif mutation=="last":config["trace_last_open"]="2024-01-01T03:00:00Z"
    elif mutation=="outer_range":monkeypatch.setattr(study,"opportunity_grid",lambda:pd.DataFrame({"signal_time":[pd.Timestamp("2024-01-01T03:00:00Z")]}))
    else:
        frame["open_time"]=frame.open_time.astype(object)
        frame.loc[1,"open_time"]="2024-01-01T01:05:00Z" if mutation=="subhour" else "2024-01-01 01:00:00"
    with pytest.raises(ValueError):study.timestamp_preflight(frame,config)


def test_uncommitted_source_fails_before_results_creation(tmp_path,monkeypatch):
    directory=tmp_path/study.EXPERIMENT;directory.mkdir(parents=True)
    (directory/"config.json").write_text(json.dumps(study.frozen_config()))
    def reject(root):raise ValueError("uncommitted")
    monkeypatch.setattr(study,"sources_committed",reject)
    with pytest.raises(ValueError,match="uncommitted"):study.run(tmp_path)
    assert not (directory/"results").exists()


def test_source_receipt_rejects_disk_drift(tmp_path,monkeypatch):
    monkeypatch.setattr(study,"SOURCES",["module.py"])
    (tmp_path/"module.py").write_text("different")
    monkeypatch.setattr(study.prior,"_git",lambda root,*a:b"head" if a[0]=="rev-parse" else b"original")
    with pytest.raises(ValueError,match="Uncommitted"):study.sources_committed(tmp_path)


def test_input_drift_fails_without_price_reader(tmp_path,monkeypatch):
    monkeypatch.setattr(study,"INPUTS",{"frozen":"expected"})
    (tmp_path/"frozen").write_text("changed")
    with pytest.raises(ValueError,match="Frozen input"):study.verify_inputs(tmp_path)


def test_old_parity_failure_cannot_reach_vwma(tmp_path,monkeypatch):
    directory=tmp_path/study.EXPERIMENT;directory.mkdir(parents=True)
    (directory/"config.json").write_text(json.dumps(study.frozen_config()))
    monkeypatch.setattr(study,"sources_committed",lambda r:("commit",[]))
    monkeypatch.setattr(study,"verify_inputs",lambda r:{})
    times=pd.DatetimeIndex([pd.Timestamp("2024-01-01T00:00:00Z")])
    monkeypatch.setattr(study,"timestamp_preflight",lambda f,c:times)
    monkeypatch.setattr(study.pd,"read_csv",lambda *a,**kw:pd.DataFrame({"open_time":times}))
    def reject(*a):raise AssertionError("baseline parity")
    monkeypatch.setattr(study,"old_arm",reject)
    monkeypatch.setattr(study,"add_reference_features",lambda *a:pytest.fail("VWMA reached before parity"))
    with pytest.raises(AssertionError,match="baseline parity"):study.run(tmp_path)
    assert (directory/"results/failure.json").exists()
    assert not (directory/"results/baseline_reproduced.json").exists()
