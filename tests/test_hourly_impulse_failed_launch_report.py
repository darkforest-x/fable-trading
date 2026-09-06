"""V17 presentation guards use synthetic summaries, never actual outcomes."""
from copy import deepcopy
import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_failed_launch_report import (
    BASE_POLICY,CANDIDATE_POLICY,CHART_ID,EXPERIMENT_ID,MARKER,build_artifact,
)


def fixture():
    delta=pd.DataFrame({"event_id":["a","b","c","d"],
        "mother_decision_time":pd.date_range("2023-02-01",periods=4,freq="h",tz="UTC"),
        "before":[-.01,.01,.01,.01],"after":[0,0,.01,None],"difference":[.01,-.01,0,None]})
    summary={"experiment_id":EXPERIMENT_ID,"status":"diagnostic_only_no_candidate_acceptance",
        "known_coverage_ceiling":154/251,"holdout_consumed":False,"audit_prices_loaded":False,
        "training_eligible":False,"production_eligible":False,"all_financial_gates_pass":False,
        "arms":{"baseline":{"policy":deepcopy(BASE_POLICY)},"candidate":{"policy":deepcopy(CANDIDATE_POLICY)}},
        "effects":{"case_delta":{"total_pairs":4,"n":3,"unknown_pairs":1,"improved":1,"worsened":1,"unchanged":1,"mean_bp":0}}}
    md="# Failed Launch Comparison\n\n## Summary\n<!-- SOURCE: v17_summary -->\nOriginal entry unchanged.\n\n## Distribution\n<!-- SOURCE: v17_case_delta -->\nAll unknowns retained.\n"+MARKER+"\n\n## Limitations\nQualified partial realization, not timeframe substitution.\n"
    kwargs=dict(markdown_path="analysis/v17.md",summary_path="experiments/v17/summary.json",
        case_delta_path="experiments/v17/case_delta.csv",generated_at="2026-09-06T00:00:00Z",fixture=True)
    return md,summary,delta,kwargs


def test_complete_markdown_sql_counts_and_correct_partial_semantics():
    md,s,d,k=fixture();saved=deepcopy(s);artifact=build_artifact(md,s,d,**k)
    assert s==saved
    blocks=artifact["manifest"]["blocks"]
    assert [b.get("sourceId") for b in blocks]==[None,"v17_summary","v17_case_delta",None,None]
    assert blocks[-1]["body"]=="## Limitations\nQualified partial realization, not timeframe substitution."
    rows=artifact["snapshot"]["datasets"][CHART_ID]
    assert sum(r["bin_count"] for r in rows)==4 and rows[5]["bin_count"]==rows[-1]["bin_count"]==1
    source=next(s for s in artifact["sources"] if s["id"]=="v17_case_delta")
    assert "WITH bins" in source["query"]["sql"]
    assert "strict0.002" in source["query"]["description"] and "before any partial" in source["query"]["description"]
    assert "can cut a subsequent recovery or winner" in source["query"]["description"]
    assert "independently recomputes serial occupancy" in source["query"]["description"]
    assert source["path"]=="experiments/v17/case_delta.csv"
    assert artifact["sources"]==artifact["manifest"]["sources"]
    text=json.dumps(artifact,allow_nan=False)
    assert "V8" not in text and "V15" not in text and "3h20m" not in text
    assert "identical final exit" not in text and "V16" in text
    assert "weighted across partial/remainder fills" in text and "censored remainder" in text
    paths={x["id"]:x.get("path") for x in artifact["sources"]}
    assert paths["research_code"]=="yoyo/evaluation/hourly_impulse_failed_launch_research.py"


@pytest.mark.parametrize("fence",["```","~~~~"])
def test_fenced_literals_and_peer_sections_survive(fence):
    md,s,d,k=fixture();literal=f"{fence}text\n{MARKER}\n<!-- SOURCE: v17_summary -->\n<!-- V15_DISTRIBUTION -->\n{fence}"
    artifact=build_artifact(md+"\n## Literal\n"+literal,s,d,**k)
    assert artifact["manifest"]["blocks"][-1]["body"]=="## Literal\n"+literal


@pytest.mark.parametrize("mutation",[
    lambda s,d,k:s.update(experiment_id="exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"),
    lambda s,d,k:s.update(status="passed"),
    lambda s,d,k:s.update(known_coverage_ceiling=float("nan")),
    lambda s,d,k:s.update(production_eligible=True),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(management_minutes=5),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(fast_partial_fraction=.25),
    lambda s,d,k:s["arms"]["candidate"]["policy"].pop("fast_partial_fraction"),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(entry_gate="prior4h_colour_at_k1_open"),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(decision_minutes=15),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(frozen_ma_exit=True),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(launch_deadline_minutes=60),
    lambda s,d,k:s["arms"]["baseline"]["policy"].update(confirmations=True),
    lambda s,d,k:s["arms"]["baseline"]["policy"].update(fast_partial_fraction=.25),
    lambda s,d,k:s["arms"]["baseline"]["policy"].update(fast_failed_launch_exit=True),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(fast_failed_launch_exit=False),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(fast_failed_launch_exit=1),
    lambda s,d,k:s["arms"]["candidate"]["policy"].pop("fast_failed_launch_exit"),
    lambda s,d,k:s["effects"]["case_delta"].update(total_pairs=3),
    lambda s,d,k:d.loc.__setitem__((0,"difference"),.5),
    lambda s,d,k:k.update(fixture=False),
])
def test_wrong_identity_policy_population_or_arithmetic_fails(mutation):
    md,s,d,k=fixture();mutation(s,d,k)
    with pytest.raises(ValueError):build_artifact(md,s,d,**k)


@pytest.mark.parametrize("replacement",["","<!-- V8_DISTRIBUTION -->",MARKER+"\n"+MARKER,MARKER+"\nAfter marker prose"])
def test_marker_exact_once_at_section_end(replacement):
    md,s,d,k=fixture()
    with pytest.raises(ValueError):build_artifact(md.replace(MARKER,replacement),s,d,**k)


def test_all251_zero_unknown_and_extreme_changes_remain_in_distribution():
    md,summary,_,kwargs=fixture()
    delta=pd.DataFrame({"event_id":[str(i) for i in range(251)],
        "mother_decision_time":pd.date_range("2023-01-01",periods=251,freq="h",tz="UTC"),
        "before":[.01]*251,"after":[.01]*251,"difference":[0.]*251})
    delta.loc[0,["after","difference"]]=[-.09,-.1]
    delta.loc[1,["after","difference"]]=[.11,.1]
    delta.loc[250,["after","difference"]]=[None,None]
    summary["effects"]["case_delta"].update(total_pairs=251,n=250,unknown_pairs=1,improved=1,worsened=1,unchanged=248,mean_bp=0)
    kwargs["fixture"]=False
    artifact=build_artifact(md,summary,delta,**kwargs)
    rows=artifact["snapshot"]["datasets"][CHART_ID]
    assert sum(row["bin_count"] for row in rows)==251
    assert rows[0]["bin_count"]==rows[-2]["bin_count"]==rows[-1]["bin_count"]==1
    assert rows[5]["bin_count"]==248
    with pytest.raises(ValueError):build_artifact(md,summary,delta.iloc[:-1],**kwargs)


def test_presentation_mirrors_frozen_runner_policy_literals_without_importing_strategy():
    path=Path(__file__).resolve().parents[1]/"yoyo/evaluation/hourly_impulse_failed_launch_research.py"
    assignments={}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in {"POLICIES","EXPERIMENT_ID"}:
                    assignments[target.id]=ast.literal_eval(node.value)
    assert assignments["POLICIES"]==[BASE_POLICY,CANDIDATE_POLICY]
    assert assignments["EXPERIMENT_ID"]==EXPERIMENT_ID


@pytest.mark.parametrize("fault",["path","future_time","duplicate_id","inf","unknown_zero"])
def test_source_or_denominator_pollution_is_rejected(fault):
    md,s,d,k=fixture()
    if fault=="path":k["summary_path"]="/tmp/summary.json"
    elif fault=="future_time":d.loc[0,"mother_decision_time"]=pd.Timestamp("2026-05-04",tz="UTC")
    elif fault=="duplicate_id":d.loc[0,"event_id"]="b"
    elif fault=="inf":d.loc[0,"difference"]=float("inf")
    else:d.loc[3,"difference"]=0
    with pytest.raises(ValueError):build_artifact(md,s,d,**k)
