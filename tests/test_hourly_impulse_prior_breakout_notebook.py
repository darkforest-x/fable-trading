"""Synthetic wrapper/IO tests; gate-domain checks belong to the pinned verifier."""
from copy import deepcopy
import csv
import hashlib
import io
import json

import pytest

from yoyo.evaluation.hourly_impulse_prior_breakout_notebook import (
    EVIDENCE_FILES,EXPERIMENT_ID,RESULTS_RELATIVE,VERIFIER_FILES,build_notebook,execute_notebook,validate_notebook,
)


def synthetic_evidence(tmp_path,*,economic_column=False):
    # This stub tests the wrapper's restricted call/pinning contract only. The
    # real verifier's extrema, timing, counts and matching have separate tests.
    source='''def verify_tables(context, source_windows, counts, matched, summary):
    assert len(context) == 713 and len(matched) == 154
    assert len(source_windows) == 1 and len(counts) == 4
    return {"status": "passed", "saved_source_rows": len(source_windows)}
def verify(*args):
    raise RuntimeError("Whole verifier runner must not be called")
def main():
    raise RuntimeError("CLI must not be called")
'''
    helper='"""Synthetic dependency: no IO or finance."""\n'
    hashes={}
    for name,text in zip(VERIFIER_FILES,(source,helper)):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    rows={"entry_context.csv":[dict(event_id=str(i),population="case" if i<251 else "control") for i in range(713)],
        "counts.csv":[dict(population="case",dimension="fold",key=str(i),accepted=1) for i in range(4)],
        "matched_support.csv":[dict(event_id=str(i)) for i in range(154)],
        "prior_hourly_rows.csv":[dict(event_id="0",role="prior",open_time="2023-01-01T00:00:00Z")]}
    if economic_column:
        for row in rows["entry_context.csv"]:row["net_return"]=0
    summary=dict(experiment_id=EXPERIMENT_ID,status="insufficient_support_no_outcomes",outcome_replays=0,
        population={"case":dict(total=251,accepted=60,abstain=191,unknown=0),"control":dict(total=462,accepted=53,abstain=409,unknown=0)},
        support_values=dict(events=60,minimum_fold_events=11,active_months=23,minimum_fold_months=5),
        support_gates=dict(minimum_events=False,minimum_per_fold=False,minimum_active_months=True,minimum_months_per_fold=True),
        support_pass=False,matching=dict(matched=154,unmatched=97,coverage=154/251),output_hashes={},
        outcomes_read_or_computed=False,profitability_test=False,holdout_consumed=False,training_eligible=False,production_eligible=False)
    directory=tmp_path/RESULTS_RELATIVE;directory.mkdir(parents=True)
    for name,table in rows.items():
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
        data=stream.getvalue().encode();(directory/name).write_bytes(data)
        summary["output_hashes"][name]=hashlib.sha256(data).hexdigest()
    data=json.dumps(summary,allow_nan=False).encode();(directory/"summary.json").write_bytes(data)
    return hashlib.sha256(data).hexdigest(),hashes


def test_three_cells_fixed_verifier_reuse_honest_gap(tmp_path):
    pins=synthetic_evidence(tmp_path);original=build_notebook(*pins);saved=deepcopy(original);validate_notebook(original)
    result=execute_notebook(original,tmp_path)
    assert original==saved
    m=result["metadata"]["fable_validation"]
    assert m["executed_code_cells"]==3 and m["execution_engine"]=="plain_python_top_down"
    assert m["verifier_reused_not_independent"] is True
    assert not m["jupyter_kernel_executed"] and not m["full_nbformat_schema_validated"]
    assert m["verified"]["outcome_replays"]==0
    assert "符合60" in "".join(result["cells"][0]["source"])
    assert all(c["outputs"] for c in result["cells"] if c["cell_type"]=="code")
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize("name",VERIFIER_FILES)
def test_hash_pin_includes_loaded_common_dependency(tmp_path,name):
    pins=synthetic_evidence(tmp_path);p=tmp_path/name;p.write_text(p.read_text()+"\n")
    with pytest.raises(ValueError,match="dependency hash"):execute_notebook(build_notebook(*pins),tmp_path)


@pytest.mark.parametrize("name",EVIDENCE_FILES)
def test_every_csv_pin_required(tmp_path,name):
    pins=synthetic_evidence(tmp_path);p=tmp_path/RESULTS_RELATIVE/name;p.write_text(p.read_text()+"\n")
    with pytest.raises(ValueError,match="CSV hash"):execute_notebook(build_notebook(*pins),tmp_path)


def test_economic_schema_rejected_even_when_rehashed(tmp_path):
    pins=synthetic_evidence(tmp_path,economic_column=True)
    with pytest.raises(ValueError,match="Economic columns"):execute_notebook(build_notebook(*pins),tmp_path)


def test_failed_verifier_receipt_is_not_promoted(tmp_path):
    summary_pin,hashes=synthetic_evidence(tmp_path)
    path=tmp_path/VERIFIER_FILES[0]
    path.write_text(path.read_text().replace('"status": "passed"','"status": "failed"'))
    hashes[VERIFIER_FILES[0]]=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match="passed validation receipt"):
        execute_notebook(build_notebook(summary_pin,hashes),tmp_path)


def test_fixed_allowlists_and_full_pin_set(tmp_path):
    pins=synthetic_evidence(tmp_path);book=build_notebook(*pins)
    next(c for c in book["cells"] if c["id"]=="load")["source"]=["evidence_path('raw.csv')"]
    with pytest.raises(ValueError,match="support allowlist"):execute_notebook(book,tmp_path)
    with pytest.raises(ValueError):build_notebook(pins[0],{VERIFIER_FILES[0]:pins[1][VERIFIER_FILES[0]]})
    with pytest.raises(ValueError):build_notebook("invalid",pins[1])
