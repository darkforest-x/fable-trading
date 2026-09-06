"""Synthetic V13 full-report and opportunity-denominator contracts."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_prior_colour_report import (
    BASE_POLICY,CANDIDATE_POLICY,CHART_ID,EXPERIMENT_ID,GATE_CONTRACT,build_artifact,
)


def fixture():
    delta=pd.DataFrame({"event_id":["a","b","c","d"],
        "mother_decision_time":pd.date_range("2023-02-01",periods=4,freq="h",tz="UTC"),
        "before":[-.01,.01,.01,.01],"after":[0,0,.01,None],"difference":[.01,-.01,0,None]})
    summary={"experiment_id":EXPERIMENT_ID,"gate_contract":deepcopy(GATE_CONTRACT),
        "arms":{"baseline":{"policy":deepcopy(BASE_POLICY)},"candidate":{"policy":deepcopy(CANDIDATE_POLICY)}},
        "effects":{"case_delta":{"total_pairs":4,"n":3,"unknown_pairs":1,"improved":1,"worsened":1,"unchanged":1,"mean_bp":0}}}
    markdown="# Prior Colour\n\n## Summary\n<!-- SOURCE: v13_summary -->\nFull opportunities.\n\n## Distribution\n<!-- SOURCE: v13_case_delta -->\nUnknown is not zero.\n<!-- V13_DISTRIBUTION -->\n\n## Caveat\nSelected trade mean is different.\n"
    kwargs={"markdown_path":"analysis/v13.md","summary_path":"experiments/v13/summary.json",
        "case_delta_path":"experiments/v13/case_delta.csv","generated_at":"2026-09-06T00:00:00Z","fixture":True}
    return markdown,summary,delta,kwargs


def test_whole_sections_sql_source_and_zero_unknown_semantics():
    md,summary,delta,kwargs=fixture();before=deepcopy(summary)
    artifact=build_artifact(md,summary,delta,**kwargs)
    assert summary==before
    blocks=artifact["manifest"]["blocks"]
    assert [b.get("sourceId") for b in blocks]==[None,"v13_summary","v13_case_delta",None,None]
    assert blocks[-1]["body"]=="## Caveat\nSelected trade mean is different."
    source=next(s for s in artifact["sources"] if s["id"]=="v13_case_delta")
    assert source["path"]=="experiments/v13/case_delta.csv"
    assert "WITH bins" in source["query"]["sql"]
    rows=artifact["snapshot"]["datasets"][CHART_ID]
    assert sum(r["bin_count"] for r in rows)==4 and rows[5]["bin_count"]==rows[-1]["bin_count"]==1
    text=json.dumps(artifact,allow_nan=False)
    assert "known-opposite abstention" in text and "Only actual completed" in text
    assert "native15m" not in text and "V8" not in text and "frozen_ma" not in text
    assert artifact["sources"]==artifact["manifest"]["sources"]


def test_documented_episode_aliases_do_not_rewrite_input():
    md,s,d,k=fixture();d=d.rename(columns={x:"episode_net_return_"+x for x in ("before","after")})
    artifact=build_artifact(md,s,d,**k)
    assert "before" not in d
    assert "episode_net_return_before" in json.dumps(artifact["sources"])
    d["before"]=.2
    with pytest.raises(ValueError,match="Contradictory"):build_artifact(md,s,d,**k)


@pytest.mark.parametrize("fence",["```","~~~~"])
def test_fenced_directives_and_all_peer_sections_preserved(fence):
    md,s,d,k=fixture();literal=f"{fence}text\n<!-- V12_DISTRIBUTION -->\n<!-- SOURCE: v13_summary -->\n{fence}"
    artifact=build_artifact(md+"\n## Literal\n"+literal,s,d,**k)
    assert artifact["manifest"]["blocks"][-1]["body"]=="## Literal\n"+literal


@pytest.mark.parametrize("mutation",[
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(frozen_ma_exit=True),
    lambda s,d,k:s["arms"]["candidate"]["policy"].update(launch_deadline_minutes=60),
    lambda s,d,k:s["arms"]["baseline"]["policy"].update(management_minutes=15),
    lambda s,d,k:s["gate_contract"].update(time="K1_close"),
    lambda s,d,k:s["gate_contract"].update(require_slope=True),
    lambda s,d,k:s["gate_contract"].update(require_atr=0),
    lambda s,d,k:s.update(experiment_id="V12"),
    lambda s,d,k:s["effects"]["case_delta"].update(total_pairs=3),
    lambda s,d,k:d.loc.__setitem__((0,"difference"),.2),
    lambda s,d,k:k.update(fixture=False),
])
def test_wrong_policy_identity_clock_or_denominator_rejected(mutation):
    md,s,d,k=fixture();mutation(s,d,k)
    with pytest.raises(ValueError):build_artifact(md,s,d,**k)


@pytest.mark.parametrize("replacement",["","<!-- V12_DISTRIBUTION -->","<!-- V13_DISTRIBUTION -->\n<!-- V13_DISTRIBUTION -->"])
def test_exact_distribution_marker(replacement):
    md,s,d,k=fixture()
    with pytest.raises(ValueError):build_artifact(md.replace("<!-- V13_DISTRIBUTION -->",replacement),s,d,**k)
