"""Synthetic713-opportunity V13 notebook; no raw prices or research calls."""
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import io
import json

import pytest

from yoyo.evaluation.hourly_impulse_prior_colour_notebook import (
    BASE_POLICY,CANDIDATE_POLICY,GATE_CONTRACT,EVIDENCE_FILES,EXPERIMENT_ID,RESULTS_RELATIVE,
    build_notebook,execute_notebook,validate_notebook,
)


def synthetic_evidence(tmp_path,mutation=None,*,all_abstain=False):
    tables={name:[] for name in EVIDENCE_FILES};contexts=[]
    for population,n in (("case",251),("control",462)):
        for i in range(n):
            event_id=f"{population}-{i}"; side=1 if i%2==0 else -1
            time=datetime(2023,2 if population=="case" else 4,1,tzinfo=timezone.utc)+timedelta(hours=i)
            signal=time-timedelta(hours=1);available=signal.replace(hour=signal.hour//4*4)
            state="abstain" if all_abstain else ("accepted","abstain","unknown")[i%3]
            known=state!="unknown";colour=side if state=="accepted" else -side
            gate={"event_id":event_id,"population":population,"signal_time":signal.isoformat(),"direction":side,
                "prior_colour_bar_open":(available-timedelta(hours=4)).isoformat(),"prior_colour_available_at":available.isoformat(),
                "prior_colour_ma":100 if known else None,"prior_colour_hl2":100+colour if known else None,
                "prior_colour_side":colour if known else None,"prior_colour_known":known,
                "prior_colour_reason":"known" if known else "warmup","prior_colour_count":40 if known else 39,
                "prior_colour_gate_state":state,"prior_colour_raw_segment_id":0}
            contexts.append(gate)
            net=.01 if i%2 else -.01; gross=net+.002
            trade={"event_id":event_id,"closed":True,"entry_price":100,"exit_price":100*(1+side*gross),
                "gross_return":gross,"net_return":net,"direction":side,"entry_time":time.isoformat(),
                "exit_time":(time+timedelta(hours=2)).isoformat(),"initial_stop":100-side*10,"outcome":"transition_colour_exit"}
            old={"event_id":event_id,"mother_decision_time":time.isoformat(),"mother_deadline":(time+timedelta(hours=72)).isoformat(),
                "signal_time":signal.isoformat(),"direction":side,"observed":True,"executed":True,"completed_trade":True,
                "episode_net_return":net,"status":"request_emitted","episode_status":"transition_colour_exit",
                "entry_time":time.isoformat(),"exit_time":trade["exit_time"],"terminal_time":time.isoformat(),
                "occupied_until":trade["exit_time"],"fold":"2023H1"}
            if population=="control":old["parent_event_id"]=f"case-{i//3}"
            new={**old,**{k:v for k,v in gate.items() if k.startswith("prior_colour_")},"policy_fee_fraction":.002}
            if state!="accepted":
                new.update(status="prior_colour_"+state,episode_status="prior_colour_"+state,observed=state=="abstain",
                    executed=False,completed_trade=False,episode_net_return=0 if state=="abstain" else None,
                    entry_time=None,exit_time=None,policy_fee_fraction=0 if state=="abstain" else None,
                    occupied_until=old["mother_decision_time"] if state=="abstain" else old["mother_deadline"])
            tables[f"baseline/{population}_episodes.csv.gz"].append(old)
            tables[f"candidate/{population}_episodes.csv.gz"].append(new)
            tables[f"baseline/{population}_trades.csv.gz"].append(trade)
            if state=="accepted":tables[f"candidate/{population}_trades.csv.gz"].append(deepcopy(trade))
            if population=="case":tables["case_delta.csv"].append({"event_id":event_id,"mother_decision_time":time.isoformat(),
                "before":net,"after":new["episode_net_return"],"difference":None if state=="unknown" else new["episode_net_return"]-net})
    tables["context_gates.csv"]=contexts
    counts={}
    for pop in ("case","control"):
        values=Counter(r["prior_colour_gate_state"] for r in contexts if r["population"]==pop)
        counts[pop]={"total":sum(values.values()),**{s:values[s] for s in ("accepted","abstain","unknown")}}
    differences=[r["difference"] for r in tables["case_delta.csv"] if r["difference"] is not None]
    selected=tables["candidate/case_trades.csv.gz"]
    known=[r["episode_net_return"] for r in tables["candidate/case_episodes.csv.gz"] if r["observed"]]
    summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
        "holdout_consumed":False,"audit_prices_loaded":False,"production_eligible":False,"training_eligible":False,
        "all_financial_gates_pass":False,"known_coverage_ceiling":154/251,"gate_contract":deepcopy(GATE_CONTRACT),
        "arms":{"baseline":{"policy":deepcopy(BASE_POLICY)},"candidate":{"policy":deepcopy(CANDIDATE_POLICY),"gate_counts":counts,
            "metrics":{"events":len(selected),"mean_net_bp":sum(r["net_return"] for r in selected)/len(selected)*1e4 if selected else None},
            "net_effect":{"mean_bp":sum(known)/len(known)*1e4}}},
        "effects":{"case_delta":{"total_pairs":251,"n":len(differences),"unknown_pairs":251-len(differences),
            "improved":sum(v>1e-12 for v in differences),"worsened":sum(v < -1e-12 for v in differences),
            "unchanged":sum(abs(v)<=1e-12 for v in differences),"mean_bp":sum(differences)/len(differences)*1e4}},"output_hashes":{}}
    for pop,key in (("case","mechanics"),("control","control_mechanics")):
        old={r["event_id"]:r for r in tables[f"baseline/{pop}_episodes.csv.gz"]}
        blocked=[old[r["event_id"]]["episode_net_return"] for r in tables[f"candidate/{pop}_episodes.csv.gz"] if r["prior_colour_gate_state"]=="abstain"]
        summary[key]={**counts[pop],"avoided_net_losers":sum(v<0 for v in blocked),"missed_net_winners":sum(v>0 for v in blocked),
            "avoided_loss_total_bp":-sum(v for v in blocked if v<0)*1e4,"missed_winner_total_bp":sum(v for v in blocked if v>0)*1e4}
    if mutation:mutation(tables,summary)
    directory=tmp_path/RESULTS_RELATIVE;directory.mkdir(parents=True)
    for name,rows in tables.items():
        reference=rows or tables[name.replace("candidate/","baseline/")]
        text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(reference[0]));writer.writeheader();writer.writerows(rows)
        payload=text.getvalue().encode()
        if name.endswith(".gz"):payload=gzip.compress(payload,mtime=0)
        path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
        summary["output_hashes"][name]=hashlib.sha256(payload).hexdigest()
    payload=json.dumps(summary,allow_nan=False).encode();(directory/"summary.json").write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_scaffold_and_plain_python713_gate_population_zero_unknown(tmp_path):
    original=build_notebook(synthetic_evidence(tmp_path));saved=deepcopy(original);validate_notebook(original)
    result=execute_notebook(original,tmp_path)
    assert original==saved
    m=result["metadata"]["fable_validation"];v=m["verified"]
    assert m["execution_engine"]=="plain_python_top_down" and m["executed_code_cells"]==5
    assert not m["jupyter_kernel_executed"] and not m["full_nbformat_schema_validated"]
    assert v["total_pairs"]==251 and v["unknown_pairs"]==83 and v["selected_cases"]==84
    assert v["gate_counts"]["control"]=={"total":462,"accepted":154,"abstain":154,"unknown":154}
    assert v["actual_cost_checks"]==713+84+154 and v["accepted_field_checks"]==238
    assert v["matched_cases"]==154 and v["unmatched_cases"]==97
    assert all(c["outputs"] for c in result["cells"] if c["cell_type"]=="code")
    json.dumps(result,allow_nan=False)


def test_all_known_abstention_not_fake_cost_or_trade(tmp_path):
    result=execute_notebook(build_notebook(synthetic_evidence(tmp_path,all_abstain=True)),tmp_path)
    v=result["metadata"]["fable_validation"]["verified"]
    assert v["selected_cases"]==0 and v["actual_cost_checks"]==713
    assert v["candidate_opportunity_mean_bp"]==0 and v["candidate_selected_mean_bp"] is None


@pytest.mark.parametrize("mutation",[
    lambda t,s:t["candidate/case_episodes.csv.gz"][1].update(policy_fee_fraction=.002),
    lambda t,s:t["candidate/case_episodes.csv.gz"][2].update(episode_net_return=0),
    lambda t,s:t["candidate/case_episodes.csv.gz"][2].update(observed=True),
    lambda t,s:t["candidate/case_episodes.csv.gz"][2].update(occupied_until=t["candidate/case_episodes.csv.gz"][2]["mother_decision_time"]),
    lambda t,s:t["candidate/case_episodes.csv.gz"][1].update(entry_time="2023-02-01T01:00:00+00:00"),
    lambda t,s:t["candidate/case_trades.csv.gz"][0].update(gross_return=.1),
    lambda t,s:t["baseline/case_trades.csv.gz"][1].update(gross_return=.1),
    lambda t,s:t["case_delta.csv"][2].update(difference=0),
    lambda t,s:t["case_delta.csv"][1].update(after=.3),
    lambda t,s:t["context_gates.csv"][0].update(prior_colour_available_at="2023-02-01T00:00:00+00:00"),
    lambda t,s:t["context_gates.csv"][0].update(prior_colour_count=39),
    lambda t,s:t["context_gates.csv"][0].update(prior_colour_side=-1),
    lambda t,s:t["context_gates.csv"][2].update(prior_colour_side=1),
    lambda t,s:t["context_gates.csv"][251].update(prior_colour_gate_state="abstain"),
    lambda t,s:t["baseline/control_episodes.csv.gz"][0].update(parent_event_id="case-200"),
    lambda t,s:s["arms"]["candidate"]["metrics"].update(events=251),
    lambda t,s:s["arms"]["candidate"]["net_effect"].update(mean_bp=99),
    lambda t,s:s["mechanics"].update(missed_net_winners=0),
    lambda t,s:s["control_mechanics"].update(avoided_loss_total_bp=0),
    lambda t,s:s["gate_contract"].update(require_slope=True),
    lambda t,s:s["arms"]["candidate"]["policy"].update(frozen_ma_exit=True),
    lambda t,s:s.update(experiment_id="V12"),
])
def test_rehashed_corrupt_evidence_fails_closed(tmp_path,mutation):
    with pytest.raises((ValueError,KeyError)):
        execute_notebook(build_notebook(synthetic_evidence(tmp_path,mutation)),tmp_path)


def test_hash_and_fixed_file_allowlist(tmp_path):
    pinned=synthetic_evidence(tmp_path)
    file=tmp_path/RESULTS_RELATIVE/"context_gates.csv"
    file.write_text(file.read_text()+"\n")
    with pytest.raises(ValueError,match="hash mismatch"):execute_notebook(build_notebook(pinned),tmp_path)
    original=build_notebook(pinned)
    next(c for c in original["cells"] if c["id"]=="load")["source"]=["evidence_path('raw.csv')"]
    with pytest.raises(ValueError,match="allowlisted"):execute_notebook(original,tmp_path)


def test_summary_hash_requires_exact_hex():
    with pytest.raises(ValueError):build_notebook("not-sha256")
