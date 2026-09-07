"""V27 runner contract checks using synthetic fixtures only."""
import json
from pathlib import Path

import pytest

from yoyo.evaluation import hourly_impulse_vwma_background_research as study


@pytest.mark.parametrize("matched,passed",[(0,False),(259,False),(260,True),(288,True)])
def test_new_cohort_gate_not_legacy_251_gate(matched,passed):
    old={"mothers":288,"maximum_matched":matched,"coverage_gate_passed":False,"required_complete_mothers":226,
         "status":"background_support_insufficient","outcomes_read_or_computed":False}
    result=study.rebase_capacity_summary(old)
    assert result["coverage_gate_passed"] is passed
    assert result["required_complete_mothers"]==260 and result["legacy_251_226_gate_discarded"]
    assert result["experiment_id"]==study.EXPERIMENT_ID and not result["profitability_test"]
    assert old["required_complete_mothers"]==226


def test_old_cohort_cannot_pass_as_new():
    with pytest.raises(ValueError):study.rebase_capacity_summary({"mothers":251,"maximum_matched":248})


def test_no_change_to_matching_or_execution_contract():
    config=study.configuration({}, {})
    assert config["count_per_mother"]==3 and config["matching_keys"]==["month","utc_6h_bucket","vol_bucket"]
    assert config["mothers"]==288 and config["required_complete_mothers"]==260
    assert config["embargo_hours"]==72 and config["component_time_limit_seconds"]==30
    assert config["seed"] is None and not config["outcomes_read_or_computed"]
    assert not config["holdout_consumed"] and not config["raw_price_io"]
    assert len(study.V26_NAMES)==13 and len(study.SOURCES)==12


def test_source_guard_before_results_or_prices(tmp_path,monkeypatch):
    directory=tmp_path/study.E;directory.mkdir(parents=True)
    (directory/"config.json").write_text(json.dumps(study.configuration({},{})))
    def reject(root):raise ValueError("uncommitted")
    monkeypatch.setattr(study,"committed_sources",reject)
    monkeypatch.setattr(study.pd,"read_csv",lambda *a,**kw:pytest.fail("price read"))
    with pytest.raises(ValueError,match="uncommitted"):study.run(tmp_path)
    assert not (directory/"results").exists()


def test_changed_preregistered_gate_rejected(tmp_path,monkeypatch):
    directory=tmp_path/study.E;directory.mkdir(parents=True)
    config=study.configuration({},{});config["required_complete_mothers"]=259
    (directory/"config.json").write_text(json.dumps(config))
    monkeypatch.setattr(study,"committed_sources",lambda *a:pytest.fail("source called after bad config"))
    with pytest.raises(ValueError,match="config"):study.run(tmp_path)


def test_uncommitted_bytes_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(study,"SOURCES",["code.py"])
    (tmp_path/"code.py").write_text("changed")
    monkeypatch.setattr(study.subprocess,"check_output",lambda args,**kw:"head" if args[1]=="rev-parse" else b"original")
    with pytest.raises(ValueError,match="Uncommitted"):study.committed_sources(tmp_path)


def test_input_allowlist_rejects_unknown_file(tmp_path):
    with pytest.raises(ValueError,match="allowlist"):study.verify_inputs(tmp_path,study.configuration({"unknown":"sha"},{}))


def test_failure_receipt_on_input_mismatch_before_tables(tmp_path,monkeypatch):
    directory=tmp_path/study.E;directory.mkdir(parents=True)
    (directory/"config.json").write_text(json.dumps(study.configuration({},{})))
    monkeypatch.setattr(study,"committed_sources",lambda *a:("commit",[]))
    monkeypatch.setattr(study.pd,"read_csv",lambda *a,**kw:pytest.fail("table read before receipt check"))
    with pytest.raises(ValueError,match="allowlist"):study.run(tmp_path)
    assert json.loads((directory/"results/failure.json").read_text())["status"]=="failed_not_support_evidence"
    assert not (directory/"results/support_frozen.json").exists()


def test_existing_results_not_overwritten(tmp_path,monkeypatch):
    directory=tmp_path/study.E;directory.mkdir(parents=True)
    (directory/"config.json").write_text(json.dumps(study.configuration({},{})))
    (directory/"results").mkdir();(directory/"results/retained").write_text("old")
    monkeypatch.setattr(study,"committed_sources",lambda *a:("commit",[]))
    with pytest.raises(FileExistsError):study.run(tmp_path)
    assert (directory/"results/retained").read_text()=="old"
