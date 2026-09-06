"""Synthetic V16 notebook wiring; accounting-domain tests belong to verifier."""
from copy import deepcopy
import csv
import gzip
import hashlib
import io
import json

import pytest

from yoyo.evaluation.hourly_impulse_dual_partial_notebook import (
    BASE_POLICY,CANDIDATE_POLICY,EVIDENCE_FILES,EXPERIMENT_ID,RESULTS_RELATIVE,VERIFIER_FILES,
    build_notebook,execute_notebook,validate_notebook,
)


def synthetic_evidence(tmp_path,mutation=None):
    # The deliberate stub checks exactly the pure-table call and population
    # wiring. It does not claim to validate financial arithmetic independently.
    source='''def verify_tables(tables, summary, *, expected_counts=(251,462,154)):
    assert summary["experiment_id"].endswith("-v16")
    for arm in ("baseline","candidate"):
        assert len(tables[arm]["case_trades"])==251
        assert len(tables[arm]["control_trades"])==462
        assert set(tables[arm])=={"case_trades","control_trades","case_episodes","control_episodes","matched","single_pending"}
    for name in ("case_delta","excess_delta","serial_delta"):
        assert len(tables[name])==251
    return {"counts":{"cases":251,"controls":462,"matched":154,"unmatched":97},
            "effects":summary["effects"],"accounting":{"unchanged_final_paths":713,
                "original_cost_fraction":.002,"partial_fraction":.5,"partial_fills":{}},
            "raw_replay":False,"inferential_p_recomputed":False,"sma_recomputed":False,
            "unlogged_edges_excluded_independently":False,"limitation":"Synthetic saved-table stub only"}
def verify(*args):
    raise RuntimeError("Full runner must not be called")
def main():
    raise RuntimeError("CLI must not be called")
'''
    hashes={}
    for name in VERIFIER_FILES:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source)
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
        n=462 if "/control_" in name else 251
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
    assert v["scope"]["raw_replay"] is False and v["accounting"]["unchanged_final_paths"]==713
    text="\n".join("".join(c["source"]) for c in result["cells"])
    assert "快5分钟真实翻色" in text and "最终退出时间" in text and "30bp压力测试不保证" in text
    assert "未知不补零" in text and "3小时20分钟→10小时" not in text
    assert "50%分批版" in "".join(result["cells"][0]["source"])
    assert all(c["outputs"] for c in result["cells"] if c["cell_type"]=="code")
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize("name",EVIDENCE_FILES)
def test_all_saved_ledger_hashes_required(tmp_path,name):
    pins=synthetic_evidence(tmp_path);path=tmp_path/RESULTS_RELATIVE/name
    path.write_bytes(path.read_bytes()+b"\n")
    with pytest.raises(ValueError,match="CSV hash mismatch"):execute_notebook(build_notebook(*pins),tmp_path)


def test_verifier_source_pinned(tmp_path):
    pins=synthetic_evidence(tmp_path);path=tmp_path/VERIFIER_FILES[0];path.write_text(path.read_text()+"\n")
    with pytest.raises(ValueError,match="dependency hash"):execute_notebook(build_notebook(*pins),tmp_path)


@pytest.mark.parametrize("mutation",[
    lambda s:s.update(experiment_id="exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"),
    lambda s:s.update(production_eligible=True),
    lambda s:s.update(known_coverage_ceiling=.99),
    lambda s:s["arms"]["candidate"]["policy"].update(fast_partial_fraction=.25),
    lambda s:s["arms"]["candidate"]["policy"].update(entry_gate="prior4h_colour_at_k1_open"),
    lambda s:s["arms"]["candidate"]["policy"].update(management_minutes=5),
    lambda s:s["arms"]["baseline"]["policy"].update(fast_partial_fraction=.5),
])
def test_rehashed_summary_must_be_exact_dual_partial_experiment(tmp_path,mutation):
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
