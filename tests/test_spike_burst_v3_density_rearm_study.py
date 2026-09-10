"""Synthetic-only contracts for the G density-rearm research runner."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_v3_density_rearm_study as study


def fixture_tables():
    times=pd.to_datetime(["2026-08-01T12:00Z","2026-08-02T12:00Z"])
    labels=pd.DataFrame(dict(instrument=["x","x"],event_i=[100,124],decision_time=times,
        label=["positive","positive"],large_peak=[True,True]))
    detections,signals=[],[]
    for arm in study.ARMS:
        for stage in ("early","confirmed"):
            for i,t in zip((100,124),times):
                hit=arm=="v3" or i==100
                detections.append(dict(instrument="x",event_i=i,arm=arm,stage=stage,hit_1=hit,hit_6=hit))
                if hit:signals.append(dict(instrument="x",decision_i=i,decision_time=t,arm=arm,stage=stage,
                    event_id=arm+stage+str(i),match_status="matched"))
    return labels,pd.DataFrame(detections),pd.DataFrame(signals),pd.DataFrame(dict(decision_time=times,eligible=[1,1]))


def test_fixed_positive_and_large_denominators_cannot_be_shrunk():
    result=study.retention_summary(*fixture_tables())
    r=result[(result.arm=="density_rearm")&(result.stage=="early")&(result.period=="full")].iloc[0]
    assert (r.positive_events,r.large_positive_events,r.hits_1)==(2,2,1)
    assert r.retention==r.large_recall_1==.5 and r.distinct_labels==1 and r.label_drop==.5
    assert r.same_time_removed==1 and r.new_times==0


def test_missing_detection_fails_instead_of_improving_denominator():
    labels,dets,signals,exposure=fixture_tables()
    dets=dets[~((dets.arm=="density_rearm")&(dets.stage=="early")&(dets.event_i==124))]
    with pytest.raises(ValueError,match="fixed label denominator"):
        study.retention_summary(labels,dets,signals,exposure)


def test_parent_economics_do_not_wait_for_or_count_children():
    _,_,signals,_=fixture_tables()
    actual=study.economic_parents(signals)
    assert len(actual)==3 and actual.stage.eq("early").all()
    shuffled=signals.copy()
    shuffled.loc[shuffled.stage=="confirmed","decision_i"]=9999
    pd.testing.assert_frame_equal(actual,study.economic_parents(shuffled))
    with pytest.raises(ValueError,match="Duplicate economic parent"):
        study.economic_parents(pd.concat([signals,signals.iloc[[0]]]))


def fake_frame(n=400):
    index=pd.date_range("2026-08-01",periods=n,freq="h",tz="UTC")
    price=100+np.arange(n)*.01
    return pd.DataFrame(dict(open=price,high=price+1,low=price-1,close=price+.1,
        atr=np.ones(n),rv=np.ones(n)*2,expansion=np.ones(n)*2,ready=np.ones(n,dtype=bool),
        history_count=np.arange(n)+341,atr_pct=.01+np.arange(n)*.000001),index=index)


def fake_state(frame,arm):
    n=len(frame)
    state=pd.DataFrame(index=frame.index)
    for k in study.BASE_FIELDS:state[k]=False if k in ("early","confirmed","candidate_edge","cooldown_blocked") else np.nan
    state.loc[frame.index[[100,112,114,150]],"candidate_edge"]=True
    parents=(100,112,150) if arm=="v3" else (100,114,150)
    children=(101,151) if arm=="v3" else (101,115,151)
    for p in parents:
        state.loc[frame.index[p],"early"]=True
        state.loc[frame.index[p:p+4],"parent_i"]=p
        state.loc[frame.index[p:p+4],"frozen_parent_high"]=frame.high.iloc[p-12:p].max()
    state.loc[frame.index[list(children)],"confirmed"]=True
    for i in children:state.loc[frame.index[i],"confirm_age"]=i-int(state.parent_i.iloc[i])
    state.loc[frame.index[114 if arm=="v3" else 112],"cooldown_blocked"]=arm=="v3"
    if arm=="v3":return state
    bools={"density_valid","density_is_dense","density_known_false","density_support_complete",
        "density_support_finite","density_summaries_finite","quota_armed_before","quota_armed_for_edge",
        "quota_armed","bootstrap_quota","bootstrap_accepted","structure_bootstrap","density_false_seen",
        "support_disjoint","rearmed","structure_blocked","gap_reset"}
    texts={"accept_origin","reject_reason"}
    for k in study.G_FIELDS:state[k]=False if k in bools else "" if k in texts else np.nan
    state["density_valid"]=True
    state["quota_armed"]=True
    state.loc[frame.index[112],"structure_blocked"]=True
    state.loc[frame.index[112],"reject_reason"]="await_disjoint_support"
    for number,p in enumerate(parents):
        origin="bootstrap" if number==0 else "density_rearm"
        state.loc[frame.index[p],"accept_origin"]=origin
        state.loc[frame.index[p],"bootstrap_accepted"]=number==0
        state.loc[frame.index[p:],"structure_owner_i"]=p
        state.loc[frame.index[p:],"structure_high"]=frame.high.iloc[p-12:p].max()
        state.loc[frame.index[p:],"structure_low"]=frame.low.iloc[p-12:p].min()
        state.loc[frame.index[p:],"structure_id"]=number+1
        if number:
            state.loc[frame.index[p],"rearm_i"]=p-1
            state.loc[frame.index[p],"rearm_window_start_i"]=p-13
            state.loc[frame.index[p],"rearm_window_end_i"]=p-2
            state.loc[frame.index[p],"rearm_owner_i"]=parents[number-1]
            state.loc[frame.index[p],"rearm_wait_bars"]=p-1-parents[number-1]
            state.loc[frame.index[p-1],"rearmed"]=True
    return state


def test_parent_provenance_is_frozen_at_acceptance_not_child_or_future_rearm():
    frame=fake_frame();s=fake_state(frame,"density_rearm")
    before=study.event_metadata(frame,s,"confirmed",115)
    assert before["parent_i"]==before["economic_parent_i"]==114
    assert before["publication_i"]==115 and before["parent_rearm_i"]==113
    mutated=s.copy()
    mutated.loc[frame.index[115:],"rearm_i"]=9999
    mutated.loc[frame.index[115:],"accept_origin"]="future"
    pd.testing.assert_series_equal(pd.Series(study.event_metadata(frame,mutated,"confirmed",115)),pd.Series(before))
    prefix=study.event_metadata(frame.iloc[:116],s.iloc[:116],"confirmed",115)
    pd.testing.assert_series_equal(pd.Series(prefix),pd.Series(before))


@pytest.mark.parametrize("kind",["future_parent","missing_parent","wrong_age","future_rearm"])
def test_event_metadata_rejects_noncausal_or_wrong_parent(kind):
    f=fake_frame();s=fake_state(f,"density_rearm")
    if kind=="future_parent":s.loc[f.index[115],"parent_i"]=150
    if kind=="missing_parent":s.loc[f.index[114],"early"]=False
    if kind=="wrong_age":s.loc[f.index[115],"confirm_age"]=0
    if kind=="future_rearm":s.loc[f.index[114],"rearm_i"]=116
    with pytest.raises(ValueError):study.event_metadata(f,s,"confirmed",115)


def test_rearming_does_not_shift_economic_parent_clock():
    f=fake_frame();s=fake_state(f,"density_rearm")
    r=study.event_metadata(f,s,"early",114)
    assert r["parent_decision_time"]==f.index[114]+study.old.HOUR
    assert r["parent_close"]==f.close.iloc[114]
    with pytest.raises(ValueError,match="delayed"):
        study.event_metadata(f,s,"early",115)


def test_common_controls_are_causal_ready_and_same_week_bucket():
    f=fake_frame(600);v=fake_state(f,"v3");g=fake_state(f,"density_rearm")
    f.loc[f.index[200:230],"ready"]=False
    a=study.causal_controls(f,{"v3":v,"density_rearm":g},"x")
    union=sorted({int(i) for s in (v,g) for k in ("early","confirmed") for i in np.flatnonzero(s[k])})
    rank=f.atr_pct.rolling(240,min_periods=60).rank(pct=True)
    buckets=np.ceil(rank*5)
    clocks=f.index+study.old.HOUR
    weeks=clocks.normalize()-pd.to_timedelta(clocks.weekday,unit="d")
    for p,chosen in a.items():
        assert len(chosen)<=3 and len(set(chosen))==len(chosen)
        for i in chosen:
            assert f.ready.iloc[i] and i<len(f)-1 and buckets.iloc[i]==buckets.iloc[p] and weeks[i]==weeks[p]
            assert not any(i-12<=j<=i for j in union)
    # Same parent at both arms has exactly one common matching result.
    assert set(a)=={100,112,114,150}
    b=study.causal_controls(f,{"density_rearm":g,"v3":v},"x")
    assert a==b


def test_control_event_exclusion_does_not_use_future_events(monkeypatch):
    f=fake_frame(600);v=fake_state(f,"v3");g=fake_state(f,"density_rearm")
    captured={}
    def fake_match(frame,all_candidates,evaluation_candidates,instrument,minutes,start,end):
        captured.update(frame=frame,events=all_candidates,parents=evaluation_candidates)
        return {i:[] for i in evaluation_candidates}
    monkeypatch.setattr(study,"match_controls",fake_match)
    study.causal_controls(f,{"v3":v,"density_rearm":g},"x")
    assert captured["events"]==[100,101,112,114,115,150,151]
    assert captured["parents"]==[100,112,114,150]
    # The existing matcher only excludes j>=event, through event+12.
    from yoyo.evaluation.spike_burst_dataset import match_controls
    only_future=match_controls(f,[500],[100],"x",60,study.old.START,study.old.END)
    none=match_controls(f,[],[100],"x",60,study.old.START,study.old.END)
    assert only_future[100]==none[100]


def p_tables():
    old=pd.DataFrame(dict(arm=study.HISTORICAL_ARMS,raw_p=[.04,.05,.003,.02,.01,.005],holm_p_six=[1]*6))
    new=pd.DataFrame(dict(arm=["density_rearm"],period=["full"],permutation_p=[.001]))
    return old,new


def test_seven_hypotheses_use_frozen_raw_p_not_old_adjusted_values():
    old,new=p_tables();family=study.structural_holm(old,new).set_index("arm")
    assert len(family)==7
    assert family.loc["density_rearm","holm_p_seven"]==pytest.approx(.007)
    assert family.loc["confirmation_gate","holm_p_seven"]==pytest.approx(.018)
    assert family.loc["reference","raw_p"]==.04
    assert family.loc["reference","holm_p_seven"]==pytest.approx(.08)


@pytest.mark.parametrize("kind",["missing","duplicate","extra","invalid"])
def test_missing_or_invalid_old_hypothesis_fails_closed(kind):
    old,new=p_tables()
    if kind=="missing":old=old.iloc[:-1]
    if kind=="duplicate":old=pd.concat([old,old.iloc[[0]]])
    if kind=="extra":old=pd.concat([old,pd.DataFrame(dict(arm=["other"],raw_p=[.1]))])
    if kind=="invalid":old.loc[0,"raw_p"]=-1
    with pytest.raises(ValueError):study.structural_holm(old,new)


def test_unknown_historical_p_remains_in_family_as_one():
    old,new=p_tables();old.loc[0,"raw_p"]=np.nan
    family=study.structural_holm(old,new).set_index("arm")
    assert family.loc["reference","correction_input_p"]==1 and len(family)==7


def assessment_tables():
    r=pd.DataFrame(dict(arm=["density_rearm"],stage=["early"],period=["full"],baseline_hit_events=[1463],
        large_positive_events=[947],positive_events=[1660],baseline_signals=[10386],retained_hit_events=[1317],
        large_hits_1=[758],distinct_labels=[6021],label_drop=[.5]))
    t=pd.DataFrame(dict(arm=["density_rearm"],period=["full"],mean_excess_bp=[1.],holm_p_seven=[.009]))
    return r,t


def test_original_absolute_acceptance_boundaries_are_unchanged():
    r,t=assessment_tables();passed=study.assessment(r,t)
    assert all(passed[k] for k in ("label_drop_pass","baseline_retention_pass","large_recall_pass","economic_pass"))
    for column,value,key in [("distinct_labels",6022,"label_drop_pass"),("retained_hit_events",1316,"baseline_retention_pass"),
                             ("large_hits_1",757,"large_recall_pass")]:
        x=r.copy();x.loc[0,column]=value
        assert not study.assessment(x,t)[key]
    assert not passed["accepted_for_deployment"] and not passed["live_deployed"]


@pytest.mark.parametrize("column",["baseline_hit_events","large_positive_events","positive_events","baseline_signals"])
def test_assessment_rejects_denominator_shifts(column):
    r,t=assessment_tables();r.loc[0,column]-=1
    with pytest.raises(ValueError,match="denominator"):study.assessment(r,t)


@pytest.mark.parametrize("key,value",[("tick",.02),("features_sha256","other"),("minutes",240)])
def test_cache_signature_cannot_cross_source_tick_or_period(key,value):
    job=dict(instrument="x",features_sha256="frozen",tick=.01,minutes=60)
    signatures={"x":study.job_signature(job)}
    study.assert_cache_signature(job,signatures)
    with pytest.raises(ValueError,match="source/tick/timeframe/cutoff"):
        study.assert_cache_signature(dict(job,**{key:value}),signatures)


def test_cache_cutoff_is_part_of_signature(monkeypatch):
    job=dict(instrument="x",features_sha256="frozen",tick=.01,minutes=60)
    signatures={"x":study.job_signature(job)}
    monkeypatch.setattr(study.old,"END",study.old.END+study.old.HOUR)
    with pytest.raises(ValueError,match="cutoff"):study.assert_cache_signature(job,signatures)


def test_cached_duplicate_outcomes_must_agree():
    assert study._same_outcome(dict(price=1.,unknown=np.nan),dict(price=1.,unknown=np.nan))
    assert not study._same_outcome(dict(price=1.),dict(price=2.))
    assert not study._same_outcome(dict(price=1.),dict(price=1.,extra=2.))


def test_no_evaluation_or_outcome_reads_before_prepared(tmp_path,monkeypatch):
    monkeypatch.setattr(study,"OUTPUT",tmp_path)
    monkeypatch.setattr(study,"source_pins",lambda:{})
    monkeypatch.setattr(study.pd,"read_csv",lambda *a,**k:pytest.fail("outcome read before prepared"))
    with pytest.raises(ValueError,match="Globally prepared"):study.evaluate(1)
    with pytest.raises(ValueError,match="Globally prepared"):study.initialize_evaluation()


def test_no_market_price_path_before_worker_initialization(monkeypatch):
    monkeypatch.setattr(study,"_EVALUATION_PREPARED_SHA",None)
    monkeypatch.setattr(study,"load_feature",lambda *a:pytest.fail("price file read"))
    with pytest.raises(ValueError,match="initialization"):study.score_job({})


def test_source_pin_failure_precedes_any_market_or_label_read(monkeypatch):
    def denied():raise ValueError("not committed")
    monkeypatch.setattr(study,"source_pins",denied)
    monkeypatch.setattr(study,"frozen_inputs",lambda:pytest.fail("market input parsed"))
    with pytest.raises(ValueError,match="not committed"):study.prepare()


def test_actual_preflight_must_cover_exact_sources_and_head(tmp_path,monkeypatch):
    root=tmp_path;exp=root/"exp";builder=root/"yoyo/evaluation/spike_burst_v3_density_rearm_study.py"
    required=[builder,root/"yoyo/evaluation/spike_burst_v3_density_rearm_gate.py",
        root/"tests/test_spike_burst_v3_density_rearm_study.py",root/"tests/test_spike_burst_v3_density_rearm_gate.py",exp/"PROJECT_PLAN.md"]
    deps=[root/"yoyo/evaluation"/n for n in ("spike_burst_early_warning.py","spike_burst_replay.py",
        "spike_burst_progressive.py","spike_burst_execution.py","spike_burst_dataset.py",
        "spike_burst_recall_study.py","altseason_research.py")]
    for p in required+deps:p.parent.mkdir(parents=True,exist_ok=True);p.write_text("# frozen\n")
    qa=exp/"qa/preflight_review.json";qa.parent.mkdir()
    qa.write_text(json.dumps(dict(status="passed",source_sha256={str(p.relative_to(root)):study.old.sha(p) for p in required})))
    monkeypatch.setattr(study,"ROOT",root);monkeypatch.setattr(study,"EXPERIMENT",exp);monkeypatch.setattr(study,"__file__",str(builder))
    monkeypatch.setattr(study.subprocess,"check_output",lambda cmd,cwd:(root/cmd[-1][5:]).read_bytes())
    assert len(study.source_pins())==13
    required[1].write_text("# changed\n")
    with pytest.raises(ValueError,match="Preflight"):study.source_pins()
    monkeypatch.setattr(study.subprocess,"check_output",lambda *a,**k:b"different HEAD")
    with pytest.raises(ValueError,match="Commit exact"):study.source_pins()


def test_structural_span_is_descriptive_gap_censor_not_trade_exit():
    frame=fake_frame();s=fake_state(frame,"density_rearm")
    s.loc[frame.index[130],"gap_reset"]=True
    spans=study.structure_spans(frame,s,dict(instrument="x",asset="X",symbol="X",venue="okx",minutes=60))
    assert spans.owner_i.tolist()==[100,114,150]
    assert spans.span_end_reason.tolist()==["new_owner","gap_censored","sample_end_censored"]
    assert spans.observed_bars.tolist()==[14,16,250]
    assert spans.descriptive_only.all()


def synthetic_pipeline(tmp_path,monkeypatch):
    """Two entirely generated assets; no repository market file is opened."""
    cache=tmp_path/"frozen_synthetic";cache.mkdir()
    output=tmp_path/"new_synthetic"
    jobs,frames,label_rows,prior_rows,cache_rows=[],{},[],[],[]
    for asset in ("SYNTH_A","SYNTH_B"):
        instrument=asset+"-USDT-SWAP"
        frame=fake_frame();frame.attrs["minutes"]=60
        source=tmp_path/(asset+".fixture");source.write_text("generated synthetic OHLC only: "+asset)
        job=dict(instrument=instrument,asset=asset,symbol=asset+"USDT.P",venue="okx",minutes=60,
                 tick=.01,features_path=str(source),features_sha256=study.old.sha(source))
        jobs.append(job);frames[str(source)]=frame
        for i,kind in ((100,"positive"),(150,"positive"),(200,"negative"),(390,"unknown")):
            label_rows.append(dict(instrument=instrument,event_i=i,decision_time=frame.index[i]+study.old.HOUR,
                bar_open=frame.index[i],label=kind,large_peak=kind=="positive"))
        baseline=fake_state(frame,"v3")
        for stage in ("early","confirmed"):
            for i in np.flatnonzero(baseline[stage]):
                prior_rows.append(dict(instrument=instrument,arm="v3",stage=stage,decision_i=int(i),
                    decision_time=frame.index[i]+study.old.HOUR,signal_close=frame.close.iloc[i],
                    parent_i=int(baseline.parent_i.iloc[i])))
        # Prior cached paths are also synthetic. Real execution math supplies
        # a complete serialized contract; this is never a market evaluation.
        cache_rows.append(dict(instrument=instrument,**study.simulate_trade(frame,100,.01,study.old.END)))
    labels=pd.DataFrame(label_rows)
    study.old.write_csv(cache/"signals.csv.gz",pd.DataFrame(prior_rows))
    study.old.write_csv(cache/"labels.csv.gz",labels)
    study.old.write_csv(cache/"trade_events.csv.gz",pd.DataFrame(cache_rows))
    study.old.write_csv(cache/"trade_controls.csv.gz",pd.DataFrame(cache_rows))
    study.old.write_json(cache/"matching.json",dict(jobs=jobs))
    refs={str(p):study.old.sha(p) for p in cache.iterdir()}
    fixed=dict(markets=2,source_bars=800,source_labels=8,anchors=8,positive=4,negative=2,unknown=2,
        large_positive=4,parents=6,children=4,distinct_labels=10,timely_positive=4,timely_children=4,large_hits=4)
    config=copy.deepcopy(study.CONFIG);config["fixed_population"]=fixed
    monkeypatch.setattr(study,"FIXED",fixed)
    monkeypatch.setattr(study,"CONFIG",config)
    monkeypatch.setattr(study,"OUTPUT",output)
    monkeypatch.setattr(study,"CACHE_SOURCE",cache)
    monkeypatch.setattr(study,"source_pins",lambda:{"synthetic_fixture":"no_market"})
    monkeypatch.setattr(study,"frozen_inputs",lambda:(jobs,labels.copy(),refs.copy()))
    monkeypatch.setattr(study,"load_feature",lambda p,d,m,e:frames[str(p)].copy())
    monkeypatch.setattr(study.v3,"detect",lambda frame:fake_state(frame,"v3"))
    def synthetic_gate(frame):
        s=fake_state(frame,"density_rearm")
        # Include an unknown bootstrap accept, unknown blocked edge and an
        # unknown unlocked hour. Unknown does not automatically mean rejection.
        s.loc[frame.index[[100,112,200]],"density_valid"]=False
        s.loc[frame.index[[100,112]],"quota_armed"]=False
        s.loc[frame.index[112],"reject_reason"]="density_unknown"
        return s
    monkeypatch.setattr(study.gate,"detect",synthetic_gate)
    return output,cache,jobs,frames,labels


def test_two_asset_prepare_freezes_real_clock_new_parent_and_shared_controls(tmp_path,monkeypatch):
    output,cache,jobs,frames,labels=synthetic_pipeline(tmp_path,monkeypatch)
    study.prepare()
    manifest=json.loads((output/"prepared_manifest.json").read_text())
    assert manifest["status"]=="complete"
    for item in manifest["artifacts"]:assert study.old.sha(Path(item["path"]))==item["sha256"]
    assert (cache/"labels.csv.gz").read_bytes()==(output/"labels.csv.gz").read_bytes()
    base=json.loads((output/"baseline_contract.json").read_text())
    assert base==dict(parents=6,children=4,distinct_labels=10,timely_positive=4,timely_children=4,
                     large_hits=4,source_bars=800,markets=2)
    signals=pd.read_csv(output/"signals.csv.gz")
    parents=signals[signals.stage.eq("early")]
    for job in jobs:
        v=parents[parents.instrument.eq(job["instrument"]) & parents.arm.eq("v3")]
        g=parents[parents.instrument.eq(job["instrument"]) & parents.arm.eq("density_rearm")]
        assert v.decision_i.tolist()==[100,112,150] and g.decision_i.tolist()==[100,114,150]
        child=signals[signals.instrument.eq(job["instrument"]) & signals.arm.eq("density_rearm") & signals.decision_i.eq(115)].iloc[0]
        assert child.parent_i==child.economic_parent_i==114 and child.parent_rearm_i==113
        assert pd.Timestamp(child.parent_decision_time)==frames[job["features_path"]].index[114]+study.old.HOUR
        assert child.parent_accept_origin=="density_rearm"
    controls=pd.read_csv(output/"controls.csv.gz")
    assert controls.stage.eq("early").all()
    assert set(controls.matched_event_id).issubset(set(parents.event_id))
    for instrument in parents.instrument.unique():
        pair=parents[parents.instrument.eq(instrument) & parents.decision_i.eq(100)]
        chosen=[controls[controls.matched_event_id.eq(e)].decision_i.tolist() for e in pair.event_id]
        assert len(chosen)==2 and chosen[0]==chosen[1]
    report=pd.read_csv(output/"rearm_summary.csv").set_index("period").loc["full"]
    assert (report.bootstrap_parents,report.rearmed_parents)==(2,4)
    assert report.density_unknown_raw_edges==4
    assert report.density_unknown_asset_hours==6 and report.density_unknown_locked_asset_hours==4
    assert not report.coverage_credit
    with pytest.raises(ValueError,match="overwrite"):study.prepare()


def test_prepare_rejects_changed_original_v3_count_before_economics(tmp_path,monkeypatch):
    output,_,_,_,_=synthetic_pipeline(tmp_path,monkeypatch)
    monkeypatch.setattr(study,"FIXED",dict(study.FIXED,timely_positive=5))
    monkeypatch.setattr(study,"simulate_trade",lambda *a,**k:pytest.fail("prepare must never score"))
    with pytest.raises(ValueError,match="baseline/count/clock drift"):study.prepare()
    assert not (output/"prepared_manifest.json").exists()
    assert not (output/"evaluation_started.json").exists()


def test_prepare_rejects_changed_original_v3_sequence_before_freezing(tmp_path,monkeypatch):
    output,cache,_,_,_=synthetic_pipeline(tmp_path,monkeypatch)
    prior=pd.read_csv(cache/"signals.csv.gz")
    prior=prior[~(prior.instrument.eq("SYNTH_A-USDT-SWAP") & prior.stage.eq("early") & prior.decision_i.eq(112))]
    study.old.write_csv(cache/"signals.csv.gz",prior)
    with pytest.raises(ValueError,match="baseline sequence drift"):study.prepare()
    assert not (output/"prepared_manifest.json").exists()


def test_prepared_cache_and_new_synthetic_paths_keep_next_open_cost_and_parent_only(tmp_path,monkeypatch):
    output,_,jobs,frames,_=synthetic_pipeline(tmp_path,monkeypatch)
    study.prepare()
    prepared=json.loads((output/"prepared_manifest.json").read_text())
    study.old.write_json(output/"evaluation_started.json",dict(prepared_sha=study.old.sha(output/"prepared_manifest.json"),
        source_pins=prepared["source_pins"]))
    for field in ("_SIGNALS","_CONTROLS","_EVALUATION_PREPARED_SHA"):
        monkeypatch.setattr(study,field,None)
    for field in ("_CACHE","_CACHE_SIGNATURES"):monkeypatch.setattr(study,field,{})
    study.initialize_evaluation()
    assert len(study._CACHE)==2 and study._SIGNALS.stage.eq("early").all()
    real_simulate=study.simulate_trade
    calls=[]
    def tracked(frame,i,tick,end):
        calls.append((id(frame),i))
        assert i!=100,"Authenticated same-source cached path should be reused"
        return real_simulate(frame,i,tick,end)
    monkeypatch.setattr(study,"simulate_trade",tracked)
    for job in jobs:
        outcomes,new_paths,_=study.score_job(job)
        actual,controls=outcomes
        assert len(actual)==6 and all(row["stage"]=="early" for row in actual+controls)
        assert new_paths==len({r["decision_i"] for r in actual+controls}-{100})
        for row in actual+controls:
            i=row["decision_i"];frame=frames[job["features_path"]]
            assert row["entry_i"]==i+1 and row["entry_price"]==frame.open.iloc[i+1]
            assert pd.Timestamp(row["entry_time"])==frame.index[i+1]
            assert row["fee_bp"]==20 and row["net_bp"]==pytest.approx(row["gross_bp"]-20)
        retained=[row for row in actual if row["decision_i"]==100]
        assert len(retained)==2 and retained[0]["net_bp"]==retained[1]["net_bp"]
        assert {row["decision_i"] for row in actual}=={100,112,114,150}
    assert calls
    # Tamper between stages: hash verification occurs before cached rows load.
    with (output/"signals.csv.gz").open("ab") as stream:stream.write(b"tampered")
    with pytest.raises(ValueError):study.initialize_evaluation()
