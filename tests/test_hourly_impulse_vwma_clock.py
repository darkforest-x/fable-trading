"""Synthetic-only V28 cohort specialization, inference and runner guards."""
import inspect
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_fixed_clock_statistics as prior
from yoyo.evaluation import hourly_impulse_vwma_clock_statistics as stats
from yoyo.evaluation import hourly_impulse_vwma_clock_research as study


def fixture():
    cases,pairs=[],[];index=0
    for fold,count in stats._FOLD_COUNTS.items():
        start=pd.Timestamp(stats.FOLD_BOUNDS[fold][0])
        for ordinal in range(count):
            time=start+pd.DateOffset(months=ordinal%6)+pd.Timedelta(days=2+ordinal//6,hours=3)
            event="synthetic_%03d"%index;supported=index>=4
            for hours in stats.HORIZONS_HOURS:
                net=.004+(index%7)*.0001;gross=net+.002;cn=.001;cg=.003
                identity=dict(event_id=event,mother_id=event,fold=fold,direction=1 if index%2 else -1,
                              decision_time=time,mother_month=time.strftime("%Y-%m"),horizon_hours=hours,
                              role="primary" if hours==4 else "descriptive",matched_support=supported)
                cases.append(dict(identity,request_kind="case",status="known",reason="known",gross_markout=gross,cost_threshold_markout=net))
                pairs.append(dict(identity,case_status="known",case_reason="known",case_known=True,n_controls_expected=3,
                    n_controls_assigned=3 if supported else 0,n_controls_known=3 if supported else 0,pair_complete=supported,
                    pair_reason="known" if supported else "unmatched_support",case_gross_markout=gross,case_cost_threshold_markout=net,
                    control_mean_gross_markout=cg if supported else np.nan,control_mean_cost_threshold_markout=cn if supported else np.nan,
                    gross_excess_markout=gross-cg if supported else np.nan,cost_threshold_excess_markout=net-cn if supported else np.nan))
            index+=1
    return pd.DataFrame(cases),pd.DataFrame(pairs)


def analyze(c,p):return stats.analyze_fixed_clock_statistics(c,p,stats.FOLD_BOUNDS)


def test_static_specialization_changes_only_frozen_population_not_statistics():
    expected=inspect.getsource(prior).replace("V24","V28").replace("251","288").replace("248","284").replace("226","260")
    expected=expected.replace('{"2023H1": 55, "2023H2": 66, "2024H1": 55, "2024H2": 75}',
                              '{"2023H1": 68, "2023H2": 74, "2024H1": 66, "2024H2": 80}')
    assert inspect.getsource(stats)==expected
    assert prior.EXPECTED_MOTHERS==251 and prior.MINIMUM_KNOWN==226


def test_all288_and284_triplets_remain_distinct_and_no_profit_claim():
    c,p=fixture();a,b=c.copy(deep=True),p.copy(deep=True);r=analyze(c,p)
    assert r["decision"]["exploratory_continue"] and not r["decision"]["profitability_accepted"]
    assert r["contract"]["minimum_primary_known"]==260
    for h,row in r["horizons"].items():
        assert row["all_case"]["n_known"]==288 and row["paired_excess"]["n_known"]==284
        assert row["paired_excess"]["n_unknown"]==4
        if h!="4":assert "one_sided_p" not in row["all_case"]["monthly_cluster"]
    pd.testing.assert_frame_equal(c,a);pd.testing.assert_frame_equal(p,b)


@pytest.mark.parametrize("missing,passes",[(28,True),(29,False)])
def test_primary_260_known_case_and_pair_gate(missing,passes):
    c,p=fixture();mask=c.horizon_hours.eq(4)&c.event_id.isin(["synthetic_%03d"%i for i in range(4,4+missing)])
    c.loc[mask,["status","reason"]]=["unknown","missing_bar"]
    c.loc[mask,["gross_markout","cost_threshold_markout"]]=np.nan
    p.loc[mask,["case_status","case_reason","case_known","pair_complete","pair_reason"]]=["unknown","missing_bar",False,False,"unknown_case_label"]
    p.loc[mask,["case_gross_markout","case_cost_threshold_markout","gross_excess_markout","cost_threshold_excess_markout"]]=np.nan
    r=analyze(c,p)
    assert r["horizons"]["4"]["all_case"]["n_known"]==288-missing
    # The paired series has four already-unmatched mothers; both must pass.
    assert r["decision"]["primary_coverage_passed"] is False
    assert (r["horizons"]["4"]["all_case"]["n_known"]>=260) is passes


@pytest.mark.parametrize("count,passes",[(24,True),(25,False)])
def test_pair_known_boundary_is260(count,passes):
    c,p=fixture();mask=p.horizon_hours.eq(4)&p.event_id.isin(["synthetic_%03d"%i for i in range(4,4+count)])
    p.loc[mask,["n_controls_known","pair_complete","pair_reason"]]=[2,False,"unknown_control_label"]
    p.loc[mask,["control_mean_gross_markout","control_mean_cost_threshold_markout","gross_excess_markout","cost_threshold_excess_markout"]]=np.nan
    r=analyze(c,p);assert r["decision"]["primary_coverage_passed"] is passes


def test_all_zeros_excess_cannot_pass_and_seed_statistics_reproduce():
    c,p=fixture();mask=p.pair_complete
    p.loc[mask,"control_mean_gross_markout"]=p.loc[mask,"case_gross_markout"]
    p.loc[mask,"control_mean_cost_threshold_markout"]=p.loc[mask,"case_cost_threshold_markout"]
    p.loc[mask,["gross_excess_markout","cost_threshold_excess_markout"]]=0.
    r=analyze(c,p);assert r["horizons"]["4"]["paired_excess"]["monthly_cluster"]["one_sided_p"]==1.
    assert not r["decision"]["exploratory_continue"]
    assert r==analyze(c.sample(frac=1,random_state=7),p.sample(frac=1,random_state=8))


def test_mother_drop_or_unknown_zero_fill_fails():
    c,p=fixture()
    with pytest.raises(ValueError,match="288"):analyze(c[c.matched_support],p[p.matched_support])
    p.loc[~p.matched_support,"cost_threshold_excess_markout"]=0
    with pytest.raises(ValueError,match="zero-filled"):analyze(c,p)


def test_config_keeps_old_label_cost_seed_and_changes_only_cohort():
    c=study.configuration({})
    assert c["population"]==dict(mothers=288,matched_mothers=284,controls=852,unmatched_mothers=4)
    assert c["primary_horizon_hours"]==4 and c["cost_threshold_fraction"]==.002
    assert c["sampling"]["seed"]==20260907 and not c["sampling"]["fallback"]
    assert c["statistics_preregistration"]["minimum_complete_pairs"]==260
    assert not c["holdout_consumed"] and not c["execution_pnl"]


def test_source_guard_precedes_any_result_or_input(tmp_path,monkeypatch):
    d=tmp_path/study.E;d.mkdir(parents=True);(d/"config.json").write_text(json.dumps(study.configuration({})))
    def reject(*args):raise ValueError("source guard")
    monkeypatch.setattr(study,"committed_sources",reject)
    monkeypatch.setattr(study.pd,"read_csv",lambda *a,**k:pytest.fail("early table read"))
    with pytest.raises(ValueError,match="source guard"):study.run(tmp_path)
    assert not (d/"results").exists()


def test_bad_config_no_source_or_prices(tmp_path,monkeypatch):
    d=tmp_path/study.E;d.mkdir(parents=True);config=study.configuration({});config["cost_threshold_fraction"]=0
    (d/"config.json").write_text(json.dumps(config))
    monkeypatch.setattr(study,"committed_sources",lambda *a:pytest.fail("bad config reached source"))
    with pytest.raises(ValueError,match="config"):study.run(tmp_path)


def test_failed_input_prevents_raw_and_retains_failure(tmp_path,monkeypatch):
    d=tmp_path/study.E;d.mkdir(parents=True);(d/"config.json").write_text(json.dumps(study.configuration({})))
    monkeypatch.setattr(study,"committed_sources",lambda *a:("head",[]))
    monkeypatch.setattr(study.fixed,"load_open_source",lambda *a:pytest.fail("raw called before input verification"))
    with pytest.raises(ValueError,match="allowlist"):study.run(tmp_path)
    assert json.loads((d/"results/failure.json").read_text())["status"]=="failed_not_economic_evidence"


def test_refuses_overwrite(tmp_path,monkeypatch):
    d=tmp_path/study.E;d.mkdir(parents=True);(d/"config.json").write_text(json.dumps(study.configuration({})))
    (d/"results").mkdir();monkeypatch.setattr(study,"committed_sources",lambda *a:("head",[]))
    with pytest.raises(FileExistsError):study.run(tmp_path)


def auditor():
    path=Path(__file__).resolve().parents[1]/study.E/"audit_saved.py"
    spec=importlib.util.spec_from_file_location("v28_independent_auditor",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_independent_auditor_decimal_labels_and_cost_tamper():
    from yoyo.evaluation.hourly_impulse_fixed_clock import build_fixed_clock_labels
    time=pd.date_range("2023-01-02T00:00Z",periods=289,freq="5min")
    raw=pd.DataFrame({"open_time":time,"open":[str(1000+i) for i in range(289)]})
    request=pd.DataFrame([dict(event_id="x",decision_time=time[0],direction=1,fold="2023H1")])
    label=build_fixed_clock_labels(raw,request,{"2023H1":("2023-01-01T00:00Z","2023-07-01T00:00Z")})
    assert auditor().check_labels(raw,request,label)==4
    label.loc[1,"cost_threshold_markout"]+=.002
    with pytest.raises(ValueError,match="Numeric"):auditor().check_labels(raw,request,label)


def test_independent_auditor_missing_interior_unknown():
    from yoyo.evaluation.hourly_impulse_fixed_clock import build_fixed_clock_labels
    time=pd.date_range("2023-01-02T00:00Z",periods=289,freq="5min")
    raw=pd.DataFrame({"open_time":time,"open":"1000"}).drop(index=15)
    request=pd.DataFrame([dict(event_id="x",decision_time=time[0],direction=-1,fold="2023H1")])
    label=build_fixed_clock_labels(raw,request,{"2023H1":("2023-01-01T00:00Z","2023-07-01T00:00Z")})
    assert auditor().check_labels(raw,request,label)==4


def test_auditor_has_no_strategy_builder_imports():
    import ast
    tree=ast.parse(inspect.getsource(auditor()))
    assert not any(isinstance(n,ast.ImportFrom) and n.module and n.module.startswith("yoyo") for n in ast.walk(tree))
