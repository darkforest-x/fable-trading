"""V15 full native report over saved5m-vs15m true-colour episode returns.

Reuses V8's complete fenced-Markdown parser and executed SQLite distribution
query, not V7 source-zone cohort or later entry filters. All251 original K1
opportunities retain signed bins, zero and unknown. Unequal/open bins are
counts, not density. Native15m also changes aggregation, initialization and
SMA40 memory; this is not a pure-frequency comparison. No price/strategy/
inference run or custom HTML runtime. Canonical Data Analytics0.2.10 packages it.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import math
from numbers import Number

import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import _fence,build_artifact as build_base
from yoyo.evaluation.hourly_impulse_report import ROOT,safe_identity


EXPERIMENT_ID="exp-btcusdtp-1h-native15-exit-preholdout-20260906-v15"
CHART_ID="v15_case_difference_distribution"
MARKER="<!-- native-exit-delta-chart -->"
BASE_POLICY={"id":"5m_native40","management_minutes":5,"ma_kind":"SMA","ma_length":40,
             "exit_mode":"transition_colour","confirmations":1}
CANDIDATE_POLICY={**BASE_POLICY,"id":"15m_native40","management_minutes":15}


def validate_summary(summary):
    """Require the fixed original-entry native-exit comparison, not another run."""
    if summary.get("experiment_id")!=EXPERIMENT_ID or summary.get("status")!="diagnostic_only_no_candidate_acceptance":
        raise ValueError("Wrong V15 experiment/status")
    expected={"baseline":BASE_POLICY,"candidate":CANDIDATE_POLICY}
    if set(summary.get("arms",{}))!=set(expected):raise ValueError("Exactly two frozen native-exit arms required")
    for arm,policy in expected.items():
        if json.dumps(summary["arms"][arm].get("policy"),sort_keys=True)!=json.dumps(policy,sort_keys=True):
            raise ValueError("Only native5m vs native15m SMA40 true-colour exits; no entry/cadence/launch additions")
    for flag in ("holdout_consumed","audit_prices_loaded","training_eligible","production_eligible","all_financial_gates_pass"):
        if summary.get(flag) is not False:raise ValueError("Unexpected safety/acceptance flag: "+flag)
    coverage=summary.get("known_coverage_ceiling")
    if not isinstance(coverage,Number) or isinstance(coverage,bool) or not math.isfinite(coverage) or abs(coverage-154/251)>1e-12:
        raise ValueError("Original154/251 matching coverage must remain explicit")


def build_artifact(markdown,summary,case_delta,*,markdown_path,summary_path,case_delta_path,generated_at,fixture=False):
    """Build the complete portable-artifact input without writing or reading data."""
    validate_summary(summary)
    if not fixture and len(case_delta)!=251:raise ValueError("All251 original K1 requests required")
    translations={"<!-- SOURCE: v15_summary -->":"<!-- SOURCE: v8_summary -->",
                  "<!-- SOURCE: v15_case_delta -->":"<!-- SOURCE: v8_case_delta -->"}
    lines,active,count=[],None,0
    for line in markdown.splitlines(keepends=True):
        outside=active is None;active=_fence(line.rstrip("\r\n"),active)
        if outside and line.strip()==MARKER:
            count+=1;line=line.replace(MARKER,"<!-- V8_DISTRIBUTION -->")
        elif outside and line.strip() in translations:
            line=line.replace(line.strip(),translations[line.strip()])
        elif outside and ("<!-- SOURCE:" in line or "_DISTRIBUTION -->" in line):
            raise ValueError("Only V15 source/distribution directives allowed")
        lines.append(line)
    if count!=1:raise ValueError("Exactly one native-exit-delta-chart marker required")
    artifact=build_base("".join(lines),summary,case_delta,markdown_path=markdown_path,summary_path=summary_path,
        case_delta_path=case_delta_path,generated_at=generated_at,fixture=fixture)
    names={"v8_summary":"v15_summary","v8_case_delta":"v15_case_delta"}
    for block in artifact["manifest"]["blocks"]:
        if "sourceId" in block:block["sourceId"]=names.get(block["sourceId"],block["sourceId"])
        if block["type"]=="chart":block.update(id="v15_distribution_chart",chartId=CHART_ID)
    for source in artifact["sources"]:
        source["id"]=names.get(source["id"],source["id"])
        source["label"]=source["label"].replace("V8","V15")
        if source["id"]=="research_code":source["path"]="yoyo/evaluation/hourly_impulse_native_exit_research.py"
        if source["id"]=="presentation_code":source["path"]="yoyo/evaluation/hourly_impulse_native_exit_report.py"
        if source["id"]=="v15_case_delta":
            source["query"]["description"]=(
                f"Actual SQLite count query over {source['path']}, loaded as main.case_delta. "
                "Source columns: event_id,mother_decision_time,before,after,difference. "
                "hourly_impulse_native_exit_research.py supplied all251 original direct1h K1 episode pairs. "
                "before=native5m SMA40 true-colour exit; after=native15m SMA40 true-colour exit. "
                "The entry cohort is unchanged and is not the source-zone breakout cohort. "
                "Native aggregation, initial available colour and SMA40 memory3h20m versus10h change together. "
                "No raw prices, strategy or inference are run by the presentation query.")
            source["query"]["metric_definitions"][0]=(
                "difference=after-before for every original direct1h K1 opportunity, no new entry gate. "
                "after=native15m and before=native5m transition-colour management, both SMA40(HL2). "
                "Unknown paired outcomes remain NULL; chart bp=difference*10000. This is not pure sampling cadence.")
            source["query"]["metric_definitions"][-1]=(
                "Bin min/max/mean/sum preserve original paired episode changes in bp. Each completed trade "
                "bears the same20bp roundtrip assumption, immutable K1 extreme stop and original72h limit. "
                "No filling unknowns, trimming tails or compounding event sums into account returns.")
    effect=summary["effects"]["case_delta"]
    chart=artifact["manifest"]["charts"][0]
    chart.update(id=CHART_ID,dataset=CHART_ID,sourceId="v15_case_delta",
        title="原生15分钟与5分钟退出的逐机会收益变化分布",
        question="保持原1h入场不变，原生管理规格变化如何影响全部机会？",
        subtitle=f"2023–2024 · 全部 {effect['total_pairs']} 原始机会，未知 {effect['unknown_pairs']} · 原生15m−5m，bp · 分箱计数非密度；零类 |Δ|≤1e−8 bp")
    chart["encodings"]["x"]["label"]="净收益变化区间（bp；原生15m−5m）"
    datasets=artifact["snapshot"]["datasets"]
    datasets[CHART_ID]=datasets.pop("v8_case_difference_distribution")
    return artifact


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("markdown","summary","case-delta","output"):parser.add_argument("--"+name,required=True)
    args=parser.parse_args();paths={k:safe_identity(getattr(args,k)) for k in ("markdown","summary","case_delta","output")}
    output=ROOT/paths["output"]
    if output.exists() or paths["output"] in (paths["markdown"],paths["summary"],paths["case_delta"]):raise ValueError("Use new output; preserve saved evidence")
    result=build_artifact((ROOT/paths["markdown"]).read_text(),json.loads((ROOT/paths["summary"]).read_text()),
        pd.read_csv(ROOT/paths["case_delta"]),markdown_path=paths["markdown"],summary_path=paths["summary"],
        case_delta_path=paths["case_delta"],generated_at=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"output":str(output),"blocks":len(result["manifest"]["blocks"]),"charts":1,"original_opportunities":251}))


if __name__=="__main__":main()
