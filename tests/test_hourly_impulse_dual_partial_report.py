"""V16 presentation guards use synthetic summaries, never actual outcomes."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_dual_partial_report import (
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
    md="# Dual Partial Comparison\n\n## Summary\n<!-- SOURCE: v16_summary -->\nOriginal entry unchanged.\n\n## Distribution\n<!-- SOURCE: v16_case_delta -->\nAll unknowns retained.\n"+MARKER+"\n\n## Limitations\nQualified partial realization, not timeframe substitution.\n"
    kwargs=dict(markdown_path="analysis/v16.md",summary_path="experiments/v16/summary.json",
        case_delta_path="experiments/v16/case_delta.csv",generated_at="2026-09-06T00:00:00Z",fixture=True)
    return md,summary,delta,kwargs


def test_complete_markdown_sql_counts_and_correct_partial_semantics():
    md,s,d,k=fixture();saved=deepcopy(s);artifact=build_artifact(md,s,d,**k)
    assert s==saved
    blocks=artifact["manifest"]["blocks"]
    assert [b.get("sourceId") for b in blocks]==[None,"v16_summary","v16_case_delta",None,None]
    assert blocks[-1]["body"]=="## Limitations\nQualified partial realization, not timeframe substitution."
    rows=artifact["snapshot"]["datasets"][CHART_ID]
    assert sum(r["bin_count"] for r in rows)==4 and rows[5]["bin_count"]==rows[-1]["bin_count"]==1
    source=next(s for s in artifact["sources"] if s["id"]=="v16_case_delta")
    assert "WITH bins" in source["query"]["sql"]
    assert "realize50%" in source["query"]["description"] and "exceeds0.002" in source["query"]["description"]
    assert source["path"]=="experiments/v16/case_delta.csv"
    assert artifact["sources"]==artifact["manifest"]["sources"]
    text=json.dumps(artifact,allow_nan=False)
    assert "V8" not in text and "V15" not in text and "3h20m" not in text
    assert "weighted across partial/remainder fills" in text and "censored remainder" in text
    paths={x["id"]:x.get("path") for x in artifact["sources"]}
    assert paths["research_code"]=="yoyo/evaluation/hourly_impulse_dual_partial_research.py"


@pytest.mark.parametrize("fence",["```","~~~~"])
def test_fenced_literals_and_peer_sections_survive(fence):
    md,s,d,k=fixture();literal=f"{fence}text\n{MARKER}\n<!-- SOURCE: v16_summary -->\n<!-- V15_DISTRIBUTION -->\n{fence}"
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
    lambda s,d,k:s["arms"]["baseline"]["policy"].update(fast_partial_fraction=.5),
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
