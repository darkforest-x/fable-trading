"""Synthetic V10 orchestration: never read an archive or financial outcomes."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_support_research as r


def configuration():
    return json.loads((Path(__file__).parents[1]/"experiments/active"/r.EXPERIMENT_ID/"config.json").read_text())


def base_config():
    return {"development_folds": deepcopy(r.FOLDS),
            "execution": {"cost_fraction": .002, "max_hours": 72, "stop_first": True}}


def test_frozen_configuration_does_not_include_any_outcome_inputs():
    config = configuration()
    r.verify_config(config, base_config())
    assert set(config["inputs"]) == {"original_mothers.csv.gz", "control_mothers.csv.gz", "assignments.csv", "assignment_receipt.json"}
    assert r.CAPACITY["required_complete_mothers"] == 226
    assert r.MATCHING["count"] == 3
    assert r.EXPECTED["status_counts"] == {"matched":154,"insufficient_exact_controls":94,"missing_causal_matching_support":3}


@pytest.mark.parametrize("field", ["count", "seed", "embargo_hours", "no_reuse", "keys_unchanged", "no_fallback"])
def test_matching_drift_fails(field):
    c=configuration()
    c["matching"][field] = None
    with pytest.raises(ValueError): r.verify_config(c, base_config())


@pytest.mark.parametrize("field", ["minimum_coverage", "required_complete_mothers", "time_limit_seconds", "optimal_required", "outcomes_used"])
def test_capacity_design_drift_fails(field):
    c=configuration()
    c["capacity"][field] = None
    with pytest.raises(ValueError): r.verify_config(c, base_config())


@pytest.mark.parametrize("change", ["base", "parent", "hash", "outcome_input", "holdout", "training", "production", "no_outcomes", "numeric_false", "cost", "hours", "fold"])
def test_scope_and_permissions_fail_before_data(change):
    c,b=configuration(),base_config()
    if change=="base": c["base_config"]="other"
    elif change=="parent": c["parent_results"]="other"
    elif change=="hash": c["inputs"]["assignments.csv"]="0"*64
    elif change=="outcome_input": c["inputs"]["trades.csv"]="0"*64
    elif change=="no_outcomes": c["no_outcome_entry_point"]=False
    elif change=="numeric_false": c["training_eligible"]=0
    elif change in ("holdout","training","production"):
        c[{"holdout":"holdout_consumed","training":"training_eligible","production":"production_eligible"}[change]]=True
    elif change=="cost": b["execution"]["cost_fraction"] = .001
    elif change=="hours": b["execution"]["max_hours"]=48
    else: b["development_folds"][-1][-1]="2026-01-01"
    with pytest.raises(ValueError): r.verify_config(c,b)


def parity_frame():
    return pd.DataFrame({"event_id":["a","b"], "decision_time":pd.to_datetime(["2024-01-01","2024-01-02"],utc=True),
        "known_5m_available":pd.to_datetime(["2024-01-01","2024-01-02"],utc=True), "risk":[.1,.2], "key":["up","down"]})


def test_all_columns_parity_handles_serialized_available_clock_and_order():
    a,b=parity_frame(),parity_frame().iloc[::-1].copy()
    for c in ("decision_time","known_5m_available"): b[c]=b[c].astype(str)
    b["risk"] += 1e-14
    r.exact_saved_parity(a,b)


@pytest.mark.parametrize("change", ["time_ns", "available_ns", "numeric", "missing", "new_column", "duplicate", "null", "text"])
def test_parity_disagreement_cannot_hide_in_aggregate(change):
    a,b=parity_frame(),parity_frame()
    if change=="time_ns": b.loc[0,"decision_time"]+=pd.Timedelta(1,"ns")
    elif change=="available_ns": b.loc[0,"known_5m_available"]+=pd.Timedelta(1,"ns")
    elif change=="numeric": b.loc[0,"risk"]+=.001
    elif change=="missing": b=b.drop(columns="key")
    elif change=="new_column": b["accidental_outcome"]=1
    elif change=="duplicate": b.loc[1,"event_id"]="a"
    elif change=="null": b.loc[0,"event_id"]=None
    else: b.loc[0,"key"]="UP"
    with pytest.raises((AssertionError,ValueError)): r.exact_saved_parity(a,b)


def fake_audit(monkeypatch):
    mothers=pd.DataFrame({"event_id":["m"+str(i) for i in range(4)],"fold":[x[0] for x in r.FOLDS],
        "decision_time":pd.to_datetime([x[1] for x in r.FOLDS],utc=True)})
    controls=pd.DataFrame([{"event_id":m.event_id+"c"+str(i),"parent_event_id":m.event_id,
        "decision_time":m.decision_time+pd.Timedelta(hours=i+1),"fold":m.fold} for m in mothers.itertuples() for i in range(3)])
    assignments=mothers.assign(match_status="matched")
    matching=pd.DataFrame({"decision_time":controls.decision_time,"candidate_eligible":True})
    receipts=[{"candidate_count_before_exact_keys":3*(i+1),"hash":"f"+str(i)} for i in range(4)]
    calls=[]
    def support(part, frame, **kwargs):
        fold=part.fold.iloc[0]
        i=[x[0] for x in r.FOLDS].index(fold)
        c=controls.loc[controls.fold.eq(fold)].copy()
        edges=pd.DataFrame({"event_id":c.parent_event_id,"candidate_id":c.decision_time.map(pd.Timestamp.isoformat)})
        return {"greedy_controls":c,"greedy_assignments":assignments.loc[assignments.fold.eq(fold)].copy(),
                "eligible_edges":edges,"mother_audit":part,"greedy_diagnostics":receipts[i]}
    def capacity(ids, edges, **kwargs):
        calls.append((ids,edges.copy(),kwargs))
        return edges.copy(),{"optimal":True,"matched_mothers":4}
    monkeypatch.setattr(r,"build_matching_frame",lambda *args:matching)
    monkeypatch.setattr(r,"build_support_audit",support)
    monkeypatch.setattr(r,"maximum_complete_matching",capacity)
    monkeypatch.setattr(r,"EXPECTED",{**r.EXPECTED,"matched":4})
    study=SimpleNamespace(config={"baseline":{}},raw=None,folds=r.FOLDS,
        entries=lambda p:mothers.copy(),featured=lambda *args:None)
    return study,mothers,controls,assignments,deepcopy(receipts),calls


def test_pipeline_matches_all_old_records_before_capacity_and_uses_every_id(monkeypatch):
    study,m,c,a,receipts,calls=fake_audit(monkeypatch)
    outputs,summary=r.audit_population(study,m,c,a,receipts)
    assert len(calls)==1 and calls[0][0]==m.event_id.tolist()
    assert calls[0][1].columns.tolist()==["event_id","candidate_id"]
    assert len(calls[0][1])==12 and calls[0][2]=={"count":3,"time_limit":30.0}
    assert summary["historical_full_parity"] and not summary["outcomes_read_or_computed"]
    assert summary["maximum_matched"]==4 and summary["allocation_recoverable"]==0
    assert outputs["fold_coverage"].active_candidate_eligible.tolist()==[3]*4
    assert outputs["fold_coverage"].historical_cumulative_pool_before_keys.tolist()==[3,6,9,12]


def test_verified_full_graph_checkpoint_precedes_solver_and_survives_failure(monkeypatch):
    from yoyo.evaluation.hourly_impulse_matching_capacity import MatchingCapacityError
    study,m,c,a,receipts,calls=fake_audit(monkeypatch)
    events=[]
    def checkpoint(tables, receipt):
        events.append("saved")
        assert {"original_mothers","matching_frame","eligible_edges","greedy_controls"}.issubset(tables)
        assert len(tables["eligible_edges"])==12
        assert receipt["historical_full_parity"] and receipt["original_assignment_feasible"]
        assert not receipt["capacity_attempted"]
    def solver(*args,**kwargs):
        assert events==["saved"]
        raise MatchingCapacityError("synthetic timeout", {"optimal":False})
    monkeypatch.setattr(r,"maximum_complete_matching",solver)
    with pytest.raises(MatchingCapacityError):
        r.audit_population(study,m,c,a,receipts,checkpoint=checkpoint)
    assert events==["saved"]


def test_failed_parity_never_emits_verified_checkpoint(monkeypatch):
    study,m,c,a,receipts,calls=fake_audit(monkeypatch)
    receipts[0]["hash"]="wrong"
    events=[]
    with pytest.raises(ValueError):
        r.audit_population(study,m,c,a,receipts,checkpoint=lambda *args:events.append("saved"))
    assert not events and not calls


@pytest.mark.parametrize("change", ["mother_ns","control_ns","assignment_status","receipt_hash","receipt_pool","missing_edge"])
def test_no_capacity_after_failed_historical_parity(monkeypatch,change):
    study,m,c,a,receipts,calls=fake_audit(monkeypatch)
    # Fake provider closes over originals; mutate only the expected saved copy.
    m,c,a=deepcopy(m),deepcopy(c),deepcopy(a)
    if change=="mother_ns": m.loc[0,"decision_time"]+=pd.Timedelta(1,"ns")
    elif change=="control_ns": c.loc[0,"decision_time"]+=pd.Timedelta(1,"ns")
    elif change=="assignment_status": a.loc[0,"match_status"]="missing"
    elif change=="receipt_hash": receipts[0]["hash"]="wrong"
    elif change=="receipt_pool": receipts[0]["candidate_count_before_exact_keys"]+=1
    else:
        original=r.build_support_audit
        def missing(*args,**kwargs):
            x=original(*args,**kwargs)
            x["eligible_edges"]=x["eligible_edges"].iloc[1:]
            return x
        monkeypatch.setattr(r,"build_support_audit",missing)
    with pytest.raises((ValueError,AssertionError)):
        r.audit_population(study,m,c,a,receipts)
    assert not calls


@pytest.mark.parametrize("case",["not_optimal","below_feasible"])
def test_uncertified_capacity_does_not_prove_unattainability(monkeypatch,case):
    study,m,c,a,receipts,calls=fake_audit(monkeypatch)
    monkeypatch.setattr(r,"maximum_complete_matching",lambda *args,**kwargs:
        (pd.DataFrame(columns=["event_id","candidate_id"]),{"optimal":case!="not_optimal","matched_mothers":3 if case=="below_feasible" else 4}))
    with pytest.raises(ValueError): r.audit_population(study,m,c,a,receipts)


def test_runner_has_no_outcome_execution_or_input_route():
    import ast,inspect
    tree=ast.parse(inspect.getsource(r))
    forbidden={"evaluate","simulate_events","simulate_requests","metrics","diagnose_frame","episode_ledger","fit"}
    calls={node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id
           for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,(ast.Attribute,ast.Name))}
    assert not calls.intersection(forbidden)
    assert set(r.INPUTS)==set(configuration()["inputs"])


@pytest.mark.parametrize("change", [None,"count","null_id","duplicate","unknown_fold","fold_end","null_time","orphan_control","reuse","partial_group","assignment_identity","status"])
def test_population_validation_preserves_full_mothers_and_strict_support(monkeypatch,change):
    study,m,c,a,_,_=fake_audit(monkeypatch)
    monkeypatch.setattr(r,"EXPECTED",{"mothers":4,"controls":12,"matched":4,"status_counts":{"matched":4}})
    if change=="count": m=m.iloc[:-1]
    elif change=="null_id": m.loc[0,"event_id"]=None
    elif change=="duplicate": a.loc[1,"event_id"]=a.event_id.iloc[0]
    elif change=="unknown_fold": m.loc[0,"fold"]="future"
    elif change=="fold_end": c.loc[0,"decision_time"]=pd.Timestamp("2023-07-01",tz="UTC")-pd.Timedelta(hours=72)
    elif change=="null_time": m.loc[0,"decision_time"]=pd.NaT
    elif change=="orphan_control": c.loc[0,"parent_event_id"]="foreign"
    elif change=="reuse": c.loc[1,"decision_time"]=c.decision_time.iloc[0]
    elif change=="partial_group": c.loc[0,"parent_event_id"]="m1"
    elif change=="assignment_identity": a.loc[0,"event_id"]="foreign"
    elif change=="status": a.loc[0,"match_status"]="missing"
    if change is None: r.validate_population(m,c,a)
    else:
        with pytest.raises(ValueError): r.validate_population(m,c,a)


@pytest.mark.parametrize("failure",[None,"base_hash","input_hash","sources","existing_results"])
def test_run_guards_precede_any_price_loading(tmp_path,monkeypatch,failure):
    events=[]
    original=r.EXPERIMENT
    target=tmp_path/"experiment"
    target.mkdir()
    (target/"config.json").write_text((original/"config.json").read_text())
    (target/"PROJECT_PLAN.md").write_text("synthetic only")
    base_path=tmp_path/r.BASE_CONFIG
    base_path.parent.mkdir(parents=True)
    base_path.write_text(json.dumps(base_config()))
    parent=tmp_path/r.PARENT
    parent.mkdir(parents=True)
    (parent/"assignment_receipt.json").write_text("[]")
    monkeypatch.setattr(r,"ROOT",tmp_path)
    monkeypatch.setattr(r,"EXPERIMENT",target)
    def digest(path):
        events.append("hash:"+path.name)
        if path==base_path: return "bad" if failure=="base_hash" else r.BASE_SHA256
        return "bad" if failure=="input_hash" else r.INPUTS[path.name]
    def committed(paths):
        events.append("sources")
        if failure=="sources": raise ValueError("uncommitted source")
        return []
    def loaded(*args):
        events.append("prices")
        raise RuntimeError("price boundary sentinel")
    monkeypatch.setattr(r,"digest",digest)
    monkeypatch.setattr(r,"committed_sources",committed)
    monkeypatch.setattr(r,"read_table",lambda p: pd.DataFrame())
    monkeypatch.setattr(r,"validate_population",lambda *args:events.append("population"))
    monkeypatch.setattr(r,"Study",loaded)
    monkeypatch.setattr(r.subprocess,"check_output",lambda *args,**kwargs:"synthetic_commit")
    if failure=="existing_results": (target/"results").mkdir()
    if failure is None:
        with pytest.raises(RuntimeError,match="price boundary sentinel"): r.run()
        assert events[-1]=="prices" and events[-2]=="population"
        assert (target/"results/started.json").exists()
        failure_receipt=json.loads((target/"results/failure.json").read_text())
        assert failure_receipt["status"]=="failed_not_capacity_evidence"
        assert failure_receipt["support_frozen"] is False
        assert events.index("sources")<events.index("hash:original_mothers.csv.gz")
    else:
        with pytest.raises(ValueError): r.run()
        assert "prices" not in events
