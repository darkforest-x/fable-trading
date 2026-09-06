"""V14 native support-only report over saved counts, without outcome metrics.

Use Data Analytics0.2.10 canonical chart/section/source contracts and the
existing fenced-Markdown parser. The declared SQLite query really runs against
saved counts.csv: four case-fold accepted counts, retaining original totals,
abstentions, unknowns and acceptance rates. No raw prices, strategy, return
simulation or separate HTML runtime. Passing support is not profitability.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import _fence,markdown_sections
from yoyo.evaluation.hourly_impulse_report import ROOT,safe_identity


EXPERIMENT_ID="exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14"
CHART_ID="v14_case_fold_support"
MARKER="<!-- prior-breakout-support-chart -->"
STATUSES={"insufficient_support_no_outcomes","support_pass_requires_separate_replay"}
FOLDS=("2023H1","2023H2","2024H1","2024H2")
COUNT_COLUMNS=("population","dimension","key","total","accepted","abstain","unknown")
SQL="""SELECT key AS fold, total, accepted, abstain, unknown,
       accepted * 1.0 / NULLIF(total, 0) AS acceptance_rate,
       unknown * 1.0 / NULLIF(total, 0) AS unknown_rate
FROM main.counts
WHERE population = 'case' AND dimension = 'fold'
ORDER BY CASE key WHEN '2023H1' THEN 0 WHEN '2023H2' THEN 1
                  WHEN '2024H1' THEN 2 WHEN '2024H2' THEN 3 ELSE 4 END"""


def support_rows(counts,*,fixture=False):
    """Validate integer partition counts, then execute the declared chart SQL."""
    if counts.columns.duplicated().any() or not set(COUNT_COLUMNS).issubset(counts):
        raise ValueError("Missing or duplicate support-count columns")
    frame=counts[list(COUNT_COLUMNS)].copy()
    if frame[["population","dimension","key"]].isna().any().any() or frame.duplicated(["population","dimension","key"]).any():
        raise ValueError("Support grouping identities must be unique and nonnull")
    for name in ("total","accepted","abstain","unknown"):
        if frame[name].map(lambda v:isinstance(v,(bool,np.bool_))).any():
            raise ValueError("Boolean is not an integer count")
        values=pd.to_numeric(frame[name],errors="raise")
        if not (np.isfinite(values)&values.ge(0)&values.eq(np.floor(values))).all():
            raise ValueError("Counts must be finite nonnegative integers")
        frame[name]=values.astype(int)
    if not frame.total.eq(frame.accepted+frame.abstain+frame.unknown).all():
        raise ValueError("Three states must partition each original population")
    if "accepted_rate" in counts:
        actual=pd.to_numeric(counts.accepted_rate,errors="raise")
        expected=frame.accepted/frame.total.replace(0,np.nan)
        if not np.allclose(actual,expected,rtol=0,atol=1e-12,equal_nan=True):
            raise ValueError("Saved acceptance rate disagrees with original denominator")
    with sqlite3.connect(":memory:") as connection:
        frame.to_sql("counts",connection,index=False)
        rows=pd.read_sql_query(SQL,connection).to_dict("records")
    if [r["fold"] for r in rows]!=list(FOLDS):
        raise ValueError("Exactly four original halfyears required")
    if not fixture and [r["total"] for r in rows]!=[55,66,55,75]:
        raise ValueError("Original251 case-fold denominators changed")
    return [{k:None if pd.isna(v) else v for k,v in row.items()} for row in rows]


def build_artifact(markdown,summary,counts,*,markdown_path,summary_path,counts_path,generated_at,fixture=False):
    """Return full canonical report; rendering is owned by the portable builder."""
    if summary.get("experiment_id")!=EXPERIMENT_ID or summary.get("status") not in STATUSES:
        raise ValueError("V14 is a support-only audit, never an outcome result")
    for key in ("outcomes_read_or_computed","profitability_test","holdout_consumed","training_eligible","production_eligible"):
        if summary.get(key) is not False:
            raise ValueError("Unexpected outcome/production claim: "+key)
    if type(summary.get("outcome_replays")) is not int or summary["outcome_replays"]!=0 or summary.get("gate_hours")!=20:
        raise ValueError("Only support preflight for prior20 hours is permitted")
    if summary.get("support_pass") is not (summary["status"]=="support_pass_requires_separate_replay"):
        raise ValueError("Support status is inconsistent")
    lines,active,count=[],None,0
    mapping={"<!-- SOURCE: v14_summary -->":"<!-- SOURCE: v14_summary -->",
             "<!-- SOURCE: v14_counts -->":"<!-- SOURCE: v14_counts -->"}
    for line in markdown.splitlines(keepends=True):
        outside=active is None;active=_fence(line.rstrip("\r\n"),active)
        if outside and line.strip()==MARKER:
            count+=1;line=line.replace(MARKER,"<!-- V8_DISTRIBUTION -->")
        elif outside and ("<!-- SOURCE:" in line or "_DISTRIBUTION -->" in line) and line.strip() not in mapping:
            raise ValueError("Only V14 support source directives allowed")
        lines.append(line)
    if count!=1:raise ValueError("Exactly one prior-breakout support chart marker required")
    title,blocks=markdown_sections("".join(lines))
    rows=support_rows(counts,fixture=fixture)
    actual={key:sum(r[key] for r in rows) for key in ("total","accepted","abstain","unknown")}
    if summary.get("population",{}).get("case")!=actual:
        raise ValueError("Summary case population disagrees with fourfold counts")
    values=summary.get("support_values",{})
    if values.get("events")!=actual["accepted"] or values.get("minimum_fold_events")!=min(r["accepted"] for r in rows):
        raise ValueError("Summary support values disagree with displayed counts")
    timestamp=pd.Timestamp(generated_at)
    if pd.isna(timestamp) or timestamp.tzinfo is None:raise ValueError("Finite timezone-aware snapshot required")
    timestamp=timestamp.tz_convert("UTC").isoformat()
    paths={"report":safe_identity(markdown_path),"v14_summary":safe_identity(summary_path),"v14_counts":safe_identity(counts_path)}
    if len(set(paths.values()))!=len(paths):raise ValueError("Distinct source identities required")
    for block in blocks:
        if block.get("sourceId") not in (None,"v14_summary","v14_counts"):raise ValueError("Unknown support source")
        if block["type"]=="chart":block.update(id="v14_support_chart",chartId=CHART_ID)
    sources=[{"id":k,"label":label,"path":paths[k]} for k,label in (
        ("report","V14 · 完整支持度报告"),("v14_summary","V14 · 事前支持审计摘要"),
        ("v14_counts","V14 · 病例及自身控制的三态支持计数"))]
    sources[-1]["query"]={"engine":"SQLite","language":"sql","sql":SQL,"executed_at":timestamp,
        "tables_used":["main.counts"],"description":f"Actual query over {paths['v14_counts']}, loaded as main.counts after integer partition validation. No return or price replay.",
        "filters":["population='case' AND dimension='fold'","All four original2023--2024 halfyears; no selected-only denominator or outcome filtering"],
        "metric_definitions":["accepted=count of original requests satisfying their own prior20-complete-hour breakout gate; it is not executed trades or wins",
            "total=accepted+abstain+unknown for each original fold; all251 case requests retained",
            "acceptance_rate=accepted/total; unknown_rate=unknown/total; missing context is not known abstention",
            "This support-only audit computes no financial outcome, fee, return, profit factor or strategy acceptance"]}
    chart={"id":CHART_ID,"title":"前20小时突破条件的半年支持数",
        "subtitle":f"2023–2024 · 原始病例机会 {sum(r['total'] for r in rows)} 个 · 柱高为符合条件数，不是成交数或盈利数",
        "showDescription":True,"type":"bar","intent":"comparison","layout":"full","dataset":CHART_ID,"sourceId":"v14_counts",
        "question":"四个半年分别有多少原始机会满足固定突破条件？",
        "rationale":"Four predeclared halfyear categories compare finite support counts, retaining original denominators and unknown states. Single blue series with count labels.",
        "palette":{"kind":"sequential","name":"blue"},"labels":{"values":"all"},
        "settings":{"sort":"none","categoryLabelPolicy":"wrap"},"maxRows":4,"valueFormat":"number",
        "referenceLines":[{"axis":"y","value":0,"color":"neutral","lineStyle":"solid","label":"0 机会"}],
        "encodings":{"x":{"field":"fold","type":"ordinal","label":"原始开发半年"},
            "y":{"field":"accepted","type":"quantitative","label":"符合突破条件的机会数","format":"number"},
            "tooltip":[{"field":k,"type":"quantitative","label":label,"format":fmt} for k,label,fmt in (
                ("accepted","符合条件","number"),("total","全部原始机会","number"),("abstain","明确不满足","number"),
                ("unknown","上下文未知","number"),("acceptance_rate","符合比例","percent"),("unknown_rate","未知比例","percent"))]}}
    return {"surface":"report","manifest":{"version":1,"surface":"report","title":title,"generatedAt":timestamp,
        "filters":[],"cards":[],"charts":[chart],"tables":[],"sources":sources,"blocks":blocks},
        "snapshot":{"version":1,"generatedAt":timestamp,"status":"fixture" if fixture else "ready",
            "datasets":{CHART_ID:rows},"accessIssues":[]},"sources":sources}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("markdown","summary","counts","output"):parser.add_argument("--"+name,required=True)
    args=parser.parse_args();paths={k:safe_identity(getattr(args,k)) for k in ("markdown","summary","counts","output")}
    output=ROOT/paths["output"]
    if output.exists() or paths["output"] in (paths["markdown"],paths["summary"],paths["counts"]):raise ValueError("Use new output; preserve evidence")
    artifact=build_artifact((ROOT/paths["markdown"]).read_text(),json.loads((ROOT/paths["summary"]).read_text()),
        pd.read_csv(ROOT/paths["counts"]),markdown_path=paths["markdown"],summary_path=paths["summary"],counts_path=paths["counts"],generated_at=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"output":str(output),"blocks":len(artifact["manifest"]["blocks"]),"charts":1,"outcomes":False}))


if __name__=="__main__":main()
