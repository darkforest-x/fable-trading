"""Synthetic V17 notebook wiring; accounting-domain tests belong to verifier."""
from copy import deepcopy
import csv
import gzip
import hashlib
import io
import json

import pytest

from yoyo.evaluation.hourly_impulse_failed_launch_notebook import (
    BASE_POLICY,CANDIDATE_POLICY,EVIDENCE_FILES,EXPERIMENT_ID,RESULTS_RELATIVE,VERIFIER_FILES,
    build_notebook,execute_notebook,validate_notebook,
)


def synthetic_evidence(tmp_path,mutation=None):
    # The deliberate stub checks exactly the pure-table call and population
    # wiring. It does not claim to validate financial arithmetic independently.
    source='''import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location("_v17_notebook_stub_dependency",Path(__file__).with_name("verify_hourly_impulse_dual_partial_v16.py"))
dependency=importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependency)
def verify_tables(tables, summary, *, expected_counts=(251,462,154)):
    assert dependency.PINNED_DEPENDENCY is True
    assert summary["experiment_id"].endswith("-v17")
    for arm in ("baseline","candidate"):
        assert len(tables[arm]["case_trades"])==251
        assert len(tables[arm]["control_trades"])==462
        assert len(tables[arm]["matched"])==154
        assert set(tables[arm])=={"case_trades","control_trades","case_episodes","control_episodes","matched","single_pending"}
    for name in ("case_delta","excess_delta","serial_delta"):
        assert len(tables[name])==251
    return {"counts":{"cases":251,"controls":462,"matched":154,"unmatched":97},
            "effects":summary["effects"],"accounting":{"serial_recomputed":True,
                "original_cost_fraction":.002,"partial_fraction":.5,
                "failed_launch_exits":{"baseline/case":0,"baseline/control":0,"candidate/case":100,"candidate/control":200}},
            "raw_replay":False,"inferential_p_recomputed":False,"sma_recomputed":False,
            "unlogged_edges_excluded_independently":False,"limitation":"Synthetic saved-table stub only"}
def verify(*args):
    raise RuntimeError("Full runner must not be called")
def main():
    raise RuntimeError("CLI must not be called")
'''
    hashes={}
    for name in VERIFIER_FILES:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(source if name==VERIFIER_FILES[0] else "PINNED_DEPENDENCY=True\n")
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
        "known_coverage_ceiling":154/251,"holdout_consumed":False,"audit_prices_loaded":False,
        "training_eligible":False,"production_eligible":False,"all_financial_gates_pass":False,
        "arms":{"baseline":{"policy":deepcopy(BASE_POLICY),"metrics":{"events":250,"mean_net_bp":-10}},
                "candidate":{"policy":deepcopy(CANDIDATE_POLICY),"metrics":{"events":250,"mean_net_bp":-8}}},
        "effects":{"case_delta":{"total_pairs":251,"n":250,"unknown_pairs":1,"mean_bp":2},
                   "excess_delta":{"total_pairs":251,"n":154,"unknown_pairs":97,"mean_bp":1},
                   "serial_delta":{"total_pairs":251,"n":250,"unknown_pairs":1,"mean_bp":2}},"output_hashes":{}}
    if mutation:mutation(summary)
    directory=tmp_path/RESULTS_RELATIVE;directory.mkdir(parents=True)
    for name in EVIDENCE_FILES:
        n=462 if "/control_" in name else 154 if name.endswith("/matched.csv") else 251
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=["event_id","example_field"])
        writer.writeheader();writer.writerows({"event_id":str(i),"example_field":0} for i in range(n))
        data=stream.getvalue().encode()
        if name.endswith(".gz"):data=gzip.compress(data,mtime=0)
        path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        summary["output_hashes"][name]=hashlib.sha256(data).hexdigest()
    data=json.dumps(summary,allow_nan=False).encode();(directory/"summary.json").write_bytes(data)
    return hashlib.sha256(data).hexdigest(),hashes


def test_three_cells_15_saved_tables_and_no_raw_inference_claim(tmp_path):
    pins=synthetic_evidence(tmp_path);original=build_notebook(*pins);saved=deepcopy(original);validate_notebook(original)
    result=execute_notebook(original,tmp_path)
    assert original==saved
    m=result["metadata"]["fable_validation"];v=m["verified"]
    assert m["execution_engine"]=="plain_python_top_down" and m["executed_code_cells"]==3
    assert len(m["evidence_files"])==15 and m["verifier_reused_not_independent"]
    assert not m["jupyter_kernel_executed"] and not m["full_nbformat_schema_validated"]
    assert v["effects"]["case_delta"]["unknown_pairs"]==1 and v["effects"]["excess_delta"]["n"]==154
    assert v["counts"]=={"cases":251,"controls":462,"matched":154,"unmatched":97}
    assert v["scope"]["raw_replay"] is False and v["accounting"]["serial_recomputed"] is True
    assert v["accounting"]["failed_launch_exits"]["candidate/case"]==100
    text="\n".join("".join(c["source"]) for c in result["cells"])
    assert "快5分钟真实翻色" in text and "旧赢家可能转亏" in text and "不能把冻结20bp的触发门改成30bp" in text
    assert "未知不补零" in text and "3小时20分钟→10小时" not in text
    assert "失败条件全平" in "".join(result["cells"][0]["source"])
    assert "病例100笔" in "".join(result["cells"][0]["source"])
    assert set(m["verifier_hashes"])==set(VERIFIER_FILES) and len(VERIFIER_FILES)==2
    assert all(c["outputs"] for c in result["cells"] if c["cell_type"]=="code")
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize("name",EVIDENCE_FILES)
def test_all_saved_ledger_hashes_required(tmp_path,name):
    pins=synthetic_evidence(tmp_path);path=tmp_path/RESULTS_RELATIVE/name
    path.write_bytes(path.read_bytes()+b"\n")
    with pytest.raises(ValueError,match="CSV hash mismatch"):execute_notebook(build_notebook(*pins),tmp_path)


@pytest.mark.parametrize("name",VERIFIER_FILES)
def test_verifier_source_pinned(tmp_path,name):
    pins=synthetic_evidence(tmp_path);path=tmp_path/name;path.write_text(path.read_text()+"\n")
    with pytest.raises(ValueError,match="dependency hash"):execute_notebook(build_notebook(*pins),tmp_path)


@pytest.mark.parametrize("mutation",[
    lambda s:s.update(experiment_id="exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"),
    lambda s:s.update(production_eligible=True),
    lambda s:s.update(known_coverage_ceiling=.99),
    lambda s:s["arms"]["candidate"]["policy"].update(fast_partial_fraction=.25),
    lambda s:s["arms"]["candidate"]["policy"].update(entry_gate="prior4h_colour_at_k1_open"),
    lambda s:s["arms"]["candidate"]["policy"].update(management_minutes=5),
    lambda s:s["arms"]["baseline"]["policy"].update(fast_partial_fraction=.25),
    lambda s:s["arms"]["baseline"]["policy"].update(fast_failed_launch_exit=True),
    lambda s:s["arms"]["candidate"]["policy"].update(fast_failed_launch_exit=False),
    lambda s:s["arms"]["candidate"]["policy"].update(fast_failed_launch_exit=1),
    lambda s:s["arms"]["candidate"]["policy"].pop("fast_failed_launch_exit"),
])
def test_rehashed_summary_must_be_exact_failed_launch_experiment(tmp_path,mutation):
    with pytest.raises(ValueError):execute_notebook(build_notebook(*synthetic_evidence(tmp_path,mutation)),tmp_path)


def test_fixed_allowlist_and_rejected_receipt(tmp_path):
    pins=synthetic_evidence(tmp_path);book=build_notebook(*pins)
    next(c for c in book["cells"] if c["id"]=="load")["source"]=["evidence_path('native_entry_context.csv.gz')"]
    with pytest.raises(ValueError,match="allowlisted"):execute_notebook(book,tmp_path)
    path=tmp_path/VERIFIER_FILES[0]
    path.write_text(path.read_text().replace('return {"counts":','return {"status":"failed","counts":'))
    pins[1][VERIFIER_FILES[0]]=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match="Failed validation receipt"):execute_notebook(build_notebook(*pins),tmp_path)


def test_missing_verifier_or_unapproved_dependency_rejected():
    with pytest.raises(ValueError):build_notebook("a"*64,{})
    with pytest.raises(ValueError):build_notebook("a"*64,{VERIFIER_FILES[0]:"b"*64,"unexpected.py":"c"*64})


def test_rehashed_verifier_cannot_claim_unperformed_raw_replay(tmp_path):
    pins=synthetic_evidence(tmp_path);path=tmp_path/VERIFIER_FILES[0]
    path.write_text(path.read_text().replace('"raw_replay":False','"raw_replay":True'))
    pins[1][VERIFIER_FILES[0]]=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match="scope overclaim"):execute_notebook(build_notebook(*pins),tmp_path)


@pytest.mark.parametrize("old,new,match",[
    ('"serial_recomputed":True','"serial_recomputed":False',"accounting contract"),
    ('"baseline/case":0','"baseline/case":1',"full-exit count"),
    ('"candidate/case":100','"candidate/case":True',"full-exit count"),
    ('"candidate/control":200','"candidate/control":463',"full-exit count"),
    ('"cases":251','"cases":250',"full population"),
    ('"sma_recomputed":False','"sma_recomputed":True',"scope overclaim"),
])
def test_rehashed_stub_receipt_must_preserve_v17_scope_and_full_population(tmp_path,old,new,match):
    pins=synthetic_evidence(tmp_path);path=tmp_path/VERIFIER_FILES[0]
    path.write_text(path.read_text().replace(old,new))
    pins[1][VERIFIER_FILES[0]]=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match=match):execute_notebook(build_notebook(*pins),tmp_path)


def test_all_dependency_hashes_are_checked_before_either_source_is_imported(tmp_path):
    pins=synthetic_evidence(tmp_path);path=tmp_path/VERIFIER_FILES[0]
    path.write_text("raise RuntimeError('MUST NOT IMPORT')\n")
    pins[1][VERIFIER_FILES[0]]=hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path/VERIFIER_FILES[1]).write_text("PINNED_DEPENDENCY=False\n")
    with pytest.raises(ValueError,match="dependency hash"):execute_notebook(build_notebook(*pins),tmp_path)


def test_rehashed_population_shrink_is_not_accepted_by_notebook_table_call(tmp_path):
    pins=synthetic_evidence(tmp_path)
    directory=tmp_path/RESULTS_RELATIVE;path=directory/"case_delta.csv"
    text=path.read_text();path.write_text("\n".join(text.splitlines()[:-1])+"\n")
    summary=json.loads((directory/"summary.json").read_text())
    summary["output_hashes"]["case_delta.csv"]=hashlib.sha256(path.read_bytes()).hexdigest()
    payload=json.dumps(summary).encode();(directory/"summary.json").write_bytes(payload)
    with pytest.raises(AssertionError):
        execute_notebook(build_notebook(hashlib.sha256(payload).hexdigest(),pins[1]),tmp_path)


def test_summary_hash_and_evidence_symlink_cannot_redirect_saved_sources(tmp_path):
    pins=synthetic_evidence(tmp_path);summary=tmp_path/RESULTS_RELATIVE/"summary.json"
    summary.write_bytes(summary.read_bytes()+b"\n")
    with pytest.raises(ValueError,match="summary hash"):execute_notebook(build_notebook(*pins),tmp_path)
    pins=(hashlib.sha256(summary.read_bytes()).hexdigest(),pins[1])
    evidence=tmp_path/RESULTS_RELATIVE/"case_delta.csv"
    moved=tmp_path/"redirected.csv";evidence.rename(moved);evidence.symlink_to(moved)
    with pytest.raises(ValueError,match="symlink"):execute_notebook(build_notebook(*pins),tmp_path)
