"""V14 support-only native presentation: synthetic counts, no price reads."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_prior_breakout_report import CHART_ID,EXPERIMENT_ID,MARKER,build_artifact,support_rows


def fixture():
    rows=[]
    for fold,total,accepted in zip(("2023H1","2023H2","2024H1","2024H2"),(55,66,55,75),(17,15,11,17)):
        rows.append(dict(population="case",dimension="fold",key=fold,total=total,accepted=accepted,abstain=total-accepted,unknown=0,accepted_rate=accepted/total))
    rows.append(dict(population="control",dimension="all",key="all",total=462,accepted=53,abstain=409,unknown=0,accepted_rate=53/462))
    summary=dict(experiment_id=EXPERIMENT_ID,status="insufficient_support_no_outcomes",support_pass=False,
        gate_hours=20,outcome_replays=0,population={"case":dict(total=251,accepted=60,abstain=191,unknown=0)},
        support_values=dict(events=60,minimum_fold_events=11),outcomes_read_or_computed=False,profitability_test=False,
        holdout_consumed=False,training_eligible=False,production_eligible=False)
    md="# Prior Breakout Support\n\n## Summary\n<!-- SOURCE: v14_summary -->\nNo outcomes.\n\n## Support\n<!-- SOURCE: v14_counts -->\nAll opportunities.\n"+MARKER+"\n\n## Caveats\nSupport is not profit.\n"
    kwargs=dict(markdown_path="analysis/v14.md",summary_path="experiments/v14/summary.json",counts_path="experiments/v14/counts.csv",generated_at="2026-09-06T00:00:00Z")
    return md,summary,pd.DataFrame(rows),kwargs


def test_actual_sql_fourfold_counts_full_sections_and_sources():
    md,s,c,k=fixture();saved=deepcopy(s)
    artifact=build_artifact(md,s,c,**k)
    assert s==saved
    rows=artifact["snapshot"]["datasets"][CHART_ID]
    assert [r["accepted"] for r in rows]==[17,15,11,17]
    assert sum(r["total"] for r in rows)==251
    assert rows[0]["acceptance_rate"]==17/55 and rows[0]["abstain"]==38
    blocks=artifact["manifest"]["blocks"]
    assert [b.get("sourceId") for b in blocks]==[None,"v14_summary","v14_counts",None,None]
    assert blocks[-1]["body"]=="## Caveats\nSupport is not profit."
    source=next(s for s in artifact["sources"] if s["id"]=="v14_counts")
    assert "population = 'case' AND dimension = 'fold'" in source["query"]["sql"]
    assert source["path"]=="experiments/v14/counts.csv"
    assert artifact["manifest"]["sources"]==artifact["sources"]
    text=json.dumps(artifact,allow_nan=False)
    assert "V8" not in text and "before" not in text and "zero" not in text


@pytest.mark.parametrize("fence",["```","~~~~"])
def test_fenced_literals_preserved(fence):
    md,s,c,k=fixture();literal=f"{fence}text\n{MARKER}\n<!-- V8_DISTRIBUTION -->\n{fence}"
    artifact=build_artifact(md+"\n## Literal\n"+literal,s,c,**k)
    assert artifact["manifest"]["blocks"][-1]["body"]=="## Literal\n"+literal


@pytest.mark.parametrize("mutation",[
    lambda s,c,k:s.update(experiment_id="V13"),
    lambda s,c,k:s.update(status="candidate_accepted"),
    lambda s,c,k:s.update(outcomes_read_or_computed=True),
    lambda s,c,k:s.update(outcome_replays=1),
    lambda s,c,k:s.update(support_pass=True),
    lambda s,c,k:s["population"]["case"].update(accepted=61),
    lambda s,c,k:s["support_values"].update(minimum_fold_events=12),
    lambda s,c,k:c.loc.__setitem__((0,"total"),54),
    lambda s,c,k:c.loc.__setitem__((0,"unknown"),1),
    lambda s,c,k:c.loc.__setitem__((0,"accepted_rate"),.99),
    lambda s,c,k:c.loc.__setitem__((0,"accepted"),float("nan")),
    lambda s,c,k:c.loc.__setitem__((0,"key"),"2023H2"),
    lambda s,c,k:k.update(generated_at="2026-09-06"),
])
def test_invalid_count_cohort_or_outcome_claim_rejected(mutation):
    md,s,c,k=fixture();mutation(s,c,k)
    with pytest.raises(ValueError):build_artifact(md,s,c,**k)


def test_unknown_preserved_not_conflated_with_abstain():
    _,_,c,_=fixture();c.loc[0,"unknown"]=3;c.loc[0,"abstain"]-=3
    assert support_rows(c)[0]["unknown"]==3
    assert support_rows(c)[0]["unknown_rate"]==3/55


def test_marker_must_end_section_and_no_legacy_source():
    md,s,c,k=fixture()
    with pytest.raises(ValueError):build_artifact(md.replace(MARKER,MARKER+"\nUnscoped post-chart prose."),s,c,**k)
    with pytest.raises(ValueError):build_artifact(md.replace("v14_counts","v13_summary"),s,c,**k)
