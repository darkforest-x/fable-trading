"""V18 full native report over a fixed conditional two-bar exit comparison.

The executed SQLite distribution query and complete fenced-Markdown parser
are reused from V8; its entry family and native5/native15 comparison are not.
All251 original direct-hourly opportunities remain, including zeros/unknowns.
Both arms retain V16's qualified native5m50% realization/native15m remainder.
Baseline is V17's immediate failed-economics full exit. Candidate alone waits
one adjacent completed native5 opposite bar, then rechecks slow alignment and
the new executable open. Profitable first-edge partials remain immediate.
Pending cancellation consumes the edge; confirmation is not another flip.
No price, strategy, inference, custom HTML or result generation occurs here.
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


EXPERIMENT_ID="exp-btcusdtp-1h-failed-confirm-preholdout-20260906-v18"
CHART_ID="v18_case_difference_distribution"
MARKER="<!-- failed-confirm-delta-chart -->"
BASE_POLICY={"id":"15m_native40_failed_launch","management_minutes":15,"ma_kind":"SMA","ma_length":40,
             "exit_mode":"transition_colour","confirmations":1,"fast_partial_fraction":0.5,"fast_failed_launch_exit":True}
CANDIDATE_POLICY={**BASE_POLICY,"id":"15m_native40_failed_confirm2","fast_failed_launch_confirmations":2}


def validate_summary(summary):
    """Reject other cohorts, exit switches or post-hoc acceptance declarations."""
    if summary.get("experiment_id")!=EXPERIMENT_ID or summary.get("status")!="diagnostic_only_no_candidate_acceptance":
        raise ValueError("Wrong V18 experiment/status")
    expected={"baseline":BASE_POLICY,"candidate":CANDIDATE_POLICY}
    if set(summary.get("arms",{}))!=set(expected):raise ValueError("Exactly two frozen failed-confirmation arms required")
    for arm,policy in expected.items():
        if json.dumps(summary["arms"][arm].get("policy"),sort_keys=True)!=json.dumps(policy,sort_keys=True):
            raise ValueError("Only frozen V17 baseline plus integer fast_failed_launch_confirmations=2 allowed")
    for flag in ("holdout_consumed","audit_prices_loaded","training_eligible","production_eligible","all_financial_gates_pass"):
        if summary.get(flag) is not False:raise ValueError("Unexpected safety/acceptance flag: "+flag)
    coverage=summary.get("known_coverage_ceiling")
    if not isinstance(coverage,Number) or isinstance(coverage,bool) or not math.isfinite(coverage) or abs(coverage-154/251)>1e-12:
        raise ValueError("Original154/251 matching coverage must remain explicit")


def build_artifact(markdown,summary,case_delta,*,markdown_path,summary_path,case_delta_path,generated_at,fixture=False):
    """Build a complete portable artifact from reviewed saved paired outcomes."""
    validate_summary(summary)
    if not fixture and len(case_delta)!=251:raise ValueError("All251 original K1 requests required")
    translations={"<!-- SOURCE: v18_summary -->":"<!-- SOURCE: v8_summary -->",
                  "<!-- SOURCE: v18_case_delta -->":"<!-- SOURCE: v8_case_delta -->"}
    lines,active,count=[],None,0
    for line in markdown.splitlines(keepends=True):
        outside=active is None;active=_fence(line.rstrip("\r\n"),active)
        if outside and line.strip()==MARKER:
            count+=1;line=line.replace(MARKER,"<!-- V8_DISTRIBUTION -->")
        elif outside and line.strip() in translations:
            line=line.replace(line.strip(),translations[line.strip()])
        elif outside and ("<!-- SOURCE:" in line or "_DISTRIBUTION -->" in line):
            raise ValueError("Only V18 source/distribution directives allowed")
        lines.append(line)
    if count!=1:raise ValueError("Exactly one failed-confirm-delta-chart marker required")
    artifact=build_base("".join(lines),summary,case_delta,markdown_path=markdown_path,summary_path=summary_path,
        case_delta_path=case_delta_path,generated_at=generated_at,fixture=fixture)
    names={"v8_summary":"v18_summary","v8_case_delta":"v18_case_delta"}
    for block in artifact["manifest"]["blocks"]:
        if "sourceId" in block:block["sourceId"]=names.get(block["sourceId"],block["sourceId"])
        if block["type"]=="chart":block.update(id="v18_distribution_chart",chartId=CHART_ID)
    for source in artifact["sources"]:
        source["id"]=names.get(source["id"],source["id"])
        source["label"]=source["label"].replace("V8","V18")
        if source["id"]=="research_code":source["path"]="yoyo/evaluation/hourly_impulse_failed_confirm_research.py"
        if source["id"]=="presentation_code":source["path"]="yoyo/evaluation/hourly_impulse_failed_confirm_report.py"
        if source["id"]=="v18_case_delta":
            source["query"]["description"]=(
                f"Actual SQLite count query over {source['path']}, loaded as main.case_delta. "
                "Source columns: event_id,mother_decision_time,before,after,difference. "
                "hourly_impulse_failed_confirm_research.py supplied all251 original direct1h K1 episode pairs. "
                "Both arms retain V16's native15m SMA40 true-colour remainder exits plus one qualified native5m50% partial. "
                "Baseline V17 immediately exits the complete position before any partial on a native5m true-colour edge "
                "when latest completed slow15m colour aligns but actual-open whole-position gross fails the strict0.002 test. "
                "Candidate instead creates pending on that edge; only the immediately next completed same-segment native5m "
                "opposite bar may confirm, after rechecking latest completed slow alignment and the actual new open gross<=0.002. "
                "Any failed recheck cancels and consumes that edge; a recovered profitable second open cannot invent a half fill. "
                "Profitable first-edge partial realization is not delayed. Confirmation is not a second true-colour edge. "
                "Equality is a failed economic condition, not a positive-net fill; Decimal quote accounting avoids false tiny winners. "
                "Waiting may preserve a recovery or worsen a loss. At the fixed20bp cost, V17's positive-net event paths "
                "remain unchanged: pending comes only from a V17 nonpositive failed full exit. This is not a profitability guarantee. "
                "No-pending paths retain all old fields. Each arm independently recomputes serial occupancy over all intentions. "
                "This is a conditional confirmation requirement, not a time-limited launch detector, new timeframe or entry gate. "
                "No raw prices, strategy or inference are run by the presentation query.")
            source["query"]["metric_definitions"][0]=(
                "difference=after-before for every original direct1h K1 opportunity. "
                "before=V17 native15m remainder plus qualified50% native5m partial and immediate failed-economics full exit; "
                "after=same policy with only that full exit requiring the next consecutive opposite native5m confirmation. "
                "Unknown paired outcomes remain NULL; chart bp=difference*10000.")
            source["query"]["metric_definitions"][-1]=(
                "Bin min/max/mean/sum preserve paired episode changes in bp. Completed original positions "
                "bear the same20bp total roundtrip cost assumption, weighted across partial/remainder fills; "
                "immutable K1 extreme stop and original72h limit remain. Partial realized proceeds do not "
                "make a censored remainder a known whole-trade result. No unknown filling, tail trimming or account compounding.")
    effect=summary["effects"]["case_delta"]
    chart=artifact["manifest"]["charts"][0]
    chart.update(id=CHART_ID,dataset=CHART_ID,sourceId="v18_case_delta",
        title="失败退出二次确认的逐机会收益变化分布",
        question="保持原入口与盈利半仓，失败全平等待下一根反色确认如何改变全部机会？",
        subtitle=f"2023–2024 · 全部 {effect['total_pairs']} 原始机会，未知 {effect['unknown_pairs']} · 二次确认候选−V17基准，bp · 分箱计数非密度；零类 |Δ|≤1e−8 bp")
    chart["encodings"]["x"]["label"]="净收益变化区间（bp；二次确认候选−V17基准）"
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
