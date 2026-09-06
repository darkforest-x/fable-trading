"""Synthetic presentation validation; no archive or financial outcome reads."""
from copy import deepcopy
import pandas as pd
import pytest
from yoyo.evaluation import hourly_impulse_support_report as r


def fixture():
    a=pd.DataFrame([{**{stage+"_count":10 for stage in r.STAGES},"event_id":"a","match_status":"matched"},
        {**{stage+"_count":2 for stage in r.STAGES},"event_id":"b","match_status":"insufficient_exact_controls"},
        {"event_id":"c","match_status":"missing_causal_matching_support"}])
    return a,{"mothers":3,"greedy_matched":1,"old_status_counts":a.match_status.value_counts().to_dict()}


def test_all_unmatched_counted_once():
    a,s=fixture(); rows=r.shortage_rows(a,s)
    assert sum(x["mother_count"] for x in rows)==2
    assert [x["stage"] for x in rows]==["same_month","missing_support"]
    assert all(x["all_mothers"]==3 and x["unmatched_mothers"]==2 for x in rows)


@pytest.mark.parametrize("change",["id","status","upward","missing","negative","fraction","fake_match"])
def test_invalid_evidence_fails(change):
    a,s=fixture()
    if change=="id": a.loc[1,"event_id"]="a"
    elif change=="status": s["old_status_counts"]={}
    elif change=="upward": a.loc[1,"same_utc6h_count"]=3
    elif change=="missing": a.loc[1,"same_slope_count"]=None
    elif change=="negative": a.loc[1,"unused_before_count"]=-1
    elif change=="fraction": a.loc[1,"unused_before_count"]=1.5
    else: a.loc[0,"unused_before_count"]=2
    with pytest.raises(ValueError): r.shortage_rows(a,s)


def test_parser_preserves_fences_sections_and_only_consumes_real_directives():
    md="# Support\n\n## One\n<!-- SOURCE: v10_summary -->\nText\n\n## Two\n````python\n## Literal\n```\n<!-- V10_SHORTAGE -->\n````\n<!-- V10_SHORTAGE -->\n"
    title,blocks=r.sections(md)
    assert title=="Support" and len(blocks)==4
    assert "## Literal" in blocks[2]["body"] and r.MARKER in blocks[2]["body"]
    assert blocks[1]["sourceId"]=="v10_summary"


@pytest.mark.parametrize("md",["", "# T\nbody", "# T\n## X\n````\n"+r.MARKER,
    "# T\n## X\n"+r.MARKER+"\nTrailing", "# T\n## X\n"+r.MARKER+"\n"+r.MARKER,
    "# T\n## X\n/Users/me\n"+r.MARKER])
def test_bad_narrative_rejected(md):
    with pytest.raises(ValueError): r.sections(md)


def test_native_artifact_retains_population_and_explicit_sources():
    a,s=fixture()
    artifact=r.build_artifact("# Support\n## Details\n"+r.MARKER,s,a,
        markdown_path="analysis/report.md",summary_path="research/summary.json",audit_path="research/mothers.csv",
        generated_at="2026-09-06T00:00:00Z")
    assert artifact["snapshot"]["status"]=="ready"
    assert len(artifact["manifest"]["charts"])==1
    assert sum(x["mother_count"] for x in artifact["snapshot"]["datasets"]["shortage"])==2
    assert artifact["manifest"]["charts"][0]["sourceId"]=="v10_mothers"
