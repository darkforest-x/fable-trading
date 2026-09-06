"""Synthetic V12 saved-evidence fixtures only; no strategy or raw-price run."""
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json

import pytest

from yoyo.evaluation.hourly_impulse_frozen_ma_notebook import (
    EVIDENCE_FILES, EXPERIMENT_ID, RESULTS_RELATIVE, build_notebook, execute_notebook, validate_notebook,
)


def geometry_row(event_id, population, side, entry, atr, risk, ma, time, parent="", matched=False):
    distance = side*(entry-ma)
    close = entry+side
    g = distance/risk
    category = "negative" if g<0 else "zero" if g==0 else "inside" if g<1 else "equal_stop" if g==1 else "beyond_stop"
    sign = lambda x: 1 if x>0 else -1 if x<0 else 0
    return {"population":population,"event_id":event_id,"parent_event_id":parent,"matched_case":matched,
        "fold":"2023H1","signal_time":(time-timedelta(hours=1)).isoformat(),"decision_time":time.isoformat(),
        "direction":side,"ma":ma,"signal_close":close,"signal_atr":atr,"initial_stop":entry-side*risk,
        "entry_open":entry,"raw_entry_segment_id":0,"entry_distance_atr":distance/atr,"entry_side":sign(distance),
        "previous_hour_close_distance_atr":side*(close-ma)/atr,"previous_hour_close_side":sign(side*(close-ma)),
        "initial_R":risk,"entry_distance_r":g,"geometry_bin":category}


def synthetic_evidence(tmp_path, mutation=None):
    cases,mechanics,geometry=[],[],[]
    for i in range(251):
        time=datetime(2023,2,1,tzinfo=timezone.utc)+timedelta(hours=i)
        side=1 if i%2==0 else -1
        ma=100-side*5
        geometry.append(geometry_row(str(i),"case",side,100,2,10,ma,time,matched=i<154))
        row={"event_id":str(i)}
        for suffix in ("before","after"):
            hold=10 if i==2 else 120
            gross=-.005 if i==2 else -.08 if i==0 else .01
            if suffix=="after" and i<2:
                hold=10 if i==0 else 15
                gross=-.055
            row.update({"entry_time_"+suffix:time.isoformat(),"entry_price_"+suffix:100,"initial_stop_"+suffix:100-side*10,
                "direction_"+suffix:side,"ma_"+suffix:ma,"signal_time_"+suffix:(time-timedelta(hours=1)).isoformat(),
                "decision_time_"+suffix:time.isoformat(),"signal_atr_"+suffix:2,"closed_"+suffix:True,
                "gross_return_"+suffix:gross,"net_return_"+suffix:gross-.002,
                "exit_price_"+suffix:100*(1+side*gross),"exit_time_"+suffix:(time+timedelta(minutes=hold)).isoformat(),
                "hold_minutes_"+suffix:hold,"outcome_"+suffix:"frozen_ma_exit" if suffix=="after" and i<2 else "transition_colour_exit"})
        has_trigger=i<3
        trigger_hold=15 if i==1 else 10
        row.update(frozen_ma_enabled=True,frozen_ma_boundary=ma,frozen_ma_available_at=time.isoformat(),
            frozen_ma_entry_distance_atr=side*(100-ma)/2,
            frozen_ma_trigger_open_time=(time+timedelta(minutes=trigger_hold-5)).isoformat() if has_trigger else None,
            frozen_ma_trigger_available_at=(time+timedelta(minutes=trigger_hold)).isoformat() if has_trigger else None,
            frozen_ma_trigger_close=ma-side if has_trigger else None,
            frozen_ma_completed_close_count=trigger_hold//5 if has_trigger else 24,
            frozen_ma_status="structure_exit" if i<2 else "prior_exit")
        row["difference"]=row["net_return_after"]-row["net_return_before"]
        mechanics.append(row)
        cases.append({"event_id":str(i),"mother_decision_time":time.isoformat(),"before":row["net_return_before"],
            "after":row["net_return_after"],"difference":row["difference"]})
    for parent in range(154):
        side=1 if parent%2==0 else -1
        for ordinal in range(3):
            i=parent*3+ordinal
            g=(-.5,0,.5,1,1.5)[i%5]
            time=datetime(2023,4,1,tzinfo=timezone.utc)+timedelta(hours=i)
            geometry.append(geometry_row(f"ctrl-{i}","control",side,100,4,20,100-side*g*20,time,parent=str(parent),matched=True))
    policy={"id":"5m_native40","management_minutes":5,"ma_kind":"SMA","ma_length":40,"exit_mode":"transition_colour","confirmations":1}
    differences=[r["difference"] for r in cases]
    summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
        "holdout_consumed":False,"audit_prices_loaded":False,"production_eligible":False,"training_eligible":False,
        "all_financial_gates_pass":False,"known_coverage_ceiling":154/251,
        "arms":{"baseline":{"policy":policy},"candidate":{"policy":{**policy,"id":"5m_native40_frozen_ma","frozen_ma_exit":True}}},
        "effects":{"case_delta":{"total_pairs":251,"n":251,"unknown_pairs":0,"improved":1,"worsened":1,"unchanged":249,"mean_bp":sum(differences)/251*1e4}},
        "mechanics":{"frozen_ma_exits":2},"entry_geometry":{},"output_hashes":{}}
    groups={"all_cases":geometry[:251],"matched_cases":geometry[:154],"unmatched_cases":geometry[154:251],"controls":geometry[251:]}
    for name,rows in groups.items():
        counts=Counter(r["geometry_bin"] for r in rows)
        summary["entry_geometry"][name]={"n":len(rows),"geometry_bins":{k:counts[k] for k in ("negative","zero","inside","equal_stop","beyond_stop")}}
    if mutation:mutation(cases,mechanics,geometry,summary)
    directory=tmp_path/RESULTS_RELATIVE
    directory.mkdir(parents=True)
    for name,rows in zip(EVIDENCE_FILES,(cases,mechanics,geometry)):
        text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
        payload=text.getvalue().encode()
        if name.endswith(".gz"):payload=gzip.compress(payload,mtime=0)
        (directory/name).write_bytes(payload)
        summary["output_hashes"][name]=hashlib.sha256(payload).hexdigest()
    payload=json.dumps(summary,allow_nan=False).encode();(directory/"summary.json").write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_unexecuted_minimum_structure_and_compilation():
    result=build_notebook("a"*64)
    validate_notebook(result)
    metadata=result["metadata"]["fable_validation"]
    assert metadata["execution_engine"]=="not_executed"
    assert not metadata["jupyter_kernel_executed"] and not metadata["full_nbformat_schema_validated"]
    assert len([c for c in result["cells"] if c["cell_type"]=="code"])==5
    assert all(not c["outputs"] and c["execution_count"] is None for c in result["cells"] if c["cell_type"]=="code")


def test_actual_plain_python_checks502costs251cases462controls_and_priority_collision(tmp_path):
    notebook=build_notebook(synthetic_evidence(tmp_path));saved=deepcopy(notebook)
    result=execute_notebook(notebook,tmp_path)
    assert notebook==saved
    metadata=result["metadata"]["fable_validation"];facts=metadata["verified"]
    assert metadata["executed_code_cells"]==5 and metadata["execution_engine"]=="plain_python_top_down"
    assert not metadata["jupyter_kernel_executed"] and not metadata["full_nbformat_schema_validated"]
    assert facts["total_pairs"]==251 and facts["closed_cost_checks"]==502
    assert facts["frozen_ma_exits"]==2 and facts["recorded_triggers"]==3
    assert facts["geometry_controls"]==462 and facts["matched_cases"]==154 and facts["unmatched_cases"]==97
    assert "全部251" in "".join(result["cells"][0]["source"])
    assert all(c["outputs"] for c in result["cells"] if c["cell_type"]=="code")
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize("mutation",[
    lambda c,m,g,s:c[0].update(difference=.123),
    lambda c,m,g,s:m[0].update(gross_return_before=.5),
    lambda c,m,g,s:m[0].update(exit_price_after=999),
    lambda c,m,g,s:m[0].update(frozen_ma_boundary=99),
    lambda c,m,g,s:m[0].update(frozen_ma_trigger_close=m[0]["frozen_ma_boundary"]),
    lambda c,m,g,s:m[0].update(frozen_ma_trigger_open_time="2023-02-01T00:06:00+00:00"),
    lambda c,m,g,s:m[0].update(frozen_ma_trigger_available_at="2023-02-01T00:15:00+00:00"),
    lambda c,m,g,s:m[0].update(frozen_ma_completed_close_count=1),
    lambda c,m,g,s:m[0].update(frozen_ma_status="prior_exit"),
    lambda c,m,g,s:m[0].update(frozen_ma_available_at="2023-01-31T23:00:00+00:00"),
    lambda c,m,g,s:m[0].update(frozen_ma_trigger_close=None),
    lambda c,m,g,s:g[251].update(ma=89),
    lambda c,m,g,s:g[251].update(entry_distance_r=999),
    lambda c,m,g,s:g[251].update(parent_event_id="200"),
    lambda c,m,g,s:g[1].update(matched_case=False),
    lambda c,m,g,s:g[0].update(entry_side=-1),
    lambda c,m,g,s:s["entry_geometry"]["controls"].update(n=461),
    lambda c,m,g,s:s["arms"]["candidate"]["policy"].update(launch_deadline_minutes=60),
    lambda c,m,g,s:s["arms"]["candidate"]["policy"].update(frozen_ma_exit=1),
    lambda c,m,g,s:s.update(experiment_id="exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11"),
])
def test_rehashed_corrupt_evidence_rejected(tmp_path,mutation):
    notebook=build_notebook(synthetic_evidence(tmp_path,mutation))
    with pytest.raises(ValueError):execute_notebook(notebook,tmp_path)


def test_candidate_only_diagnostic_columns_cannot_be_guessed_as_after(tmp_path):
    def rename(cases,mechanics,geometry,summary):
        for row in mechanics:row["frozen_ma_boundary_after"]=row.pop("frozen_ma_boundary")
    notebook=build_notebook(synthetic_evidence(tmp_path,rename))
    with pytest.raises(ValueError,match="Missing required"):
        execute_notebook(notebook,tmp_path)


def test_unknown_candidate_keeps_original_denominator(tmp_path):
    def unknown(cases,mechanics,geometry,summary):
        cases[4].update(after=None,difference=None)
        mechanics[4].update(closed_after=False,gross_return_after=None,net_return_after=None,difference=None,
                            outcome_after="right_censored",frozen_ma_status="unknown_source")
        summary["effects"]["case_delta"].update(n=250,unknown_pairs=1,unchanged=248,
            mean_bp=sum(r["difference"] for r in cases if r["difference"] is not None)/250*1e4)
    result=execute_notebook(build_notebook(synthetic_evidence(tmp_path,unknown)),tmp_path)
    facts=result["metadata"]["fable_validation"]["verified"]
    assert facts["total_pairs"]==251 and facts["unknown_pairs"]==1 and facts["closed_cost_checks"]==501


def test_consistent_control_geometry_still_requires_parent_risk_transfer(tmp_path):
    def different_scale(cases,mechanics,geometry,summary):
        row=geometry[251]
        row["signal_atr"]*=2
        row["entry_distance_atr"]/=2
        row["previous_hour_close_distance_atr"]/=2
    with pytest.raises(ValueError,match="risk/ATR not transferred"):
        execute_notebook(build_notebook(synthetic_evidence(tmp_path,different_scale)),tmp_path)


@pytest.mark.parametrize("name",EVIDENCE_FILES)
def test_each_saved_source_hash_is_required(tmp_path,name):
    notebook=build_notebook(synthetic_evidence(tmp_path))
    with (tmp_path/RESULTS_RELATIVE/name).open("ab") as stream:stream.write(b"\n")
    with pytest.raises(ValueError,match="CSV hash mismatch"):
        execute_notebook(notebook,tmp_path)


def test_allowlist_rejects_extra_file_and_geometry_symlink_escape(tmp_path):
    digest=synthetic_evidence(tmp_path)
    notebook=build_notebook(digest)
    setup=next(c for c in notebook["cells"] if c["id"]=="setup")
    setup["source"].append('\nevidence_path("raw.csv")\n')
    with pytest.raises(ValueError,match="allowlist"):
        execute_notebook(notebook,tmp_path)
    evidence=tmp_path/RESULTS_RELATIVE/"entry_geometry.csv"
    outside=tmp_path/"geometry.csv";evidence.rename(outside);evidence.symlink_to(outside)
    with pytest.raises(ValueError,match="symlink escaped"):
        execute_notebook(build_notebook(digest),tmp_path)


@pytest.mark.parametrize("digest",[None,"","g"*64,"a"*63])
def test_summary_hash_required(digest):
    with pytest.raises(ValueError,match="SHA256"):build_notebook(digest)
