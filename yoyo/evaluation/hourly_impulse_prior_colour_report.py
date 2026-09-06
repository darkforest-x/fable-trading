"""V13 full-Markdown native report over saved prior4h entry-gate evidence.

Reuses V8's complete section parser and actual SQLite distribution query, not
its exit-policy semantics. Native Data Analytics0.2.10 performs portable HTML
rendering/QA. All251 original opportunities retain both tails, zero and unknown;
counts in unequal/open bins are not density. No strategy, prices or inference
are run here. The sole entry gate uses completed4h SMA40(HL2) colour at K1 OPEN.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import _fence, build_artifact as build_base
from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


EXPERIMENT_ID = "exp-btcusdtp-1h-prior4h-colour-preholdout-20260906-v13"
CHART_ID = "v13_case_difference_distribution"
MARKER = "<!-- V13_DISTRIBUTION -->"
BASE_POLICY = {"id":"5m_native40", "management_minutes":5, "ma_kind":"SMA", "ma_length":40,
               "exit_mode":"transition_colour", "confirmations":1}
CANDIDATE_POLICY = {**BASE_POLICY, "id":"5m_native40_prior4h_colour", "entry_gate":"prior4h_colour_at_k1_open"}
GATE_CONTRACT = {
    "time":"signal_time_K1_open", "minutes":240, "ma_kind":"SMA", "ma_length":40,
    "ma_source":"HL2", "side":"1_if_hl2_greater_equal_ma_else_minus1",
    "maximum_age_hours_exclusive":4, "minimum_contiguous_complete_bars":40,
    "require_atr":False, "require_slope":False, "control_gate":"own_context_no_transfer",
    "known_opposite":"zero_no_entry_no_fee", "unknown":"NaN_not_abstention",
    "serial_unknown":"conservative_full72h_reservation_not_actual_position",
    "population":"all251_cases462_controls154_fixed_triples97_unmatched",
}


def validate_policies(summary):
    """Reject another experiment or any bundled gate/exit/unknown mutation."""
    if summary.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("Wrong V13 experiment identity")
    expected = {"baseline":BASE_POLICY, "candidate":CANDIDATE_POLICY}
    arms = summary.get("arms", {})
    if set(arms) != set(expected):
        raise ValueError("V13 requires baseline and candidate")
    for name, policy in expected.items():
        if json.dumps(arms[name].get("policy"), sort_keys=True) != json.dumps(policy, sort_keys=True):
            raise ValueError("V13 policies differ only by the prior4h colour entry gate")
    if json.dumps(summary.get("gate_contract"), sort_keys=True) != json.dumps(GATE_CONTRACT, sort_keys=True):
        raise ValueError("V13 requires own prior4h colour at K1 OPEN; no slope/ATR requirement")


def build_artifact(markdown, summary, case_delta, *, markdown_path, summary_path,
                   case_delta_path, generated_at, fixture=False):
    """Return a full canonical artifact, preserving authored peer sections."""
    validate_policies(summary)
    if not fixture and len(case_delta) != 251:
        raise ValueError("V13 requires all251 original opportunities")
    delta = case_delta.copy()
    if delta.columns.duplicated().any():
        raise ValueError("Duplicate saved columns")
    aliases = {}
    for normal in ("before", "after"):
        alias = "episode_net_return_" + normal
        if alias in delta:
            if normal in delta and not np.allclose(pd.to_numeric(delta[alias]), pd.to_numeric(delta[normal]),
                                                   rtol=0, atol=1e-12, equal_nan=True):
                raise ValueError("Contradictory opportunity-return aliases")
            if normal not in delta:
                delta[normal] = delta[alias]
                aliases[normal] = alias
    translations = {"<!-- SOURCE: v13_summary -->":"<!-- SOURCE: v8_summary -->",
                    "<!-- SOURCE: v13_case_delta -->":"<!-- SOURCE: v8_case_delta -->"}
    lines, active, count = [], None, 0
    for line in markdown.splitlines(keepends=True):
        outside = active is None
        active = _fence(line.rstrip("\r\n"), active)
        if outside and line.strip() == MARKER:
            count += 1
            line = line.replace(MARKER, "<!-- V8_DISTRIBUTION -->")
        elif outside and line.strip() in translations:
            line = line.replace(line.strip(), translations[line.strip()])
        elif outside and ("<!-- SOURCE:" in line or "_DISTRIBUTION -->" in line):
            raise ValueError("Only explicit V13 source/distribution directives are allowed")
        lines.append(line)
    if count != 1:
        raise ValueError("Exactly one V13 distribution marker required")
    artifact = build_base("".join(lines), summary, delta, markdown_path=markdown_path,
        summary_path=summary_path, case_delta_path=case_delta_path, generated_at=generated_at, fixture=fixture)
    names = {"v8_summary":"v13_summary", "v8_case_delta":"v13_case_delta"}
    for block in artifact["manifest"]["blocks"]:
        if "sourceId" in block: block["sourceId"] = names.get(block["sourceId"], block["sourceId"])
        if block["type"] == "chart": block.update(id="v13_distribution_chart", chartId=CHART_ID)
    for source in artifact["sources"]:
        source["id"] = names.get(source["id"], source["id"])
        source["label"] = source["label"].replace("V8", "V13")
        if source["id"] == "research_code": source["path"] = "yoyo/evaluation/hourly_impulse_prior_colour_research.py"
        if source["id"] == "presentation_code": source["path"] = "yoyo/evaluation/hourly_impulse_prior_colour_report.py"
        if source["id"] == "v13_case_delta":
            source["query"]["description"] = (
                f"Actual SQLite count query over {source['path']}, loaded as main.case_delta; "
                f"saved opportunity return aliases mapped locally: {aliases}. "
                "hourly_impulse_prior_colour_research.py supplied full251 episode returns. "
                "Both arms retain native5m SMA40(HL2) true-colour exit; candidate adds only own completed4h "
                "SMA40(HL2) colour at K1 OPEN. No prices, strategy or inference are replayed by this query.")
            source["query"]["metric_definitions"][0] = (
                "difference=after-before for each of all251 original opportunities. Accepted keeps actual "
                "trade economics; known-opposite abstention has zero net return and no fee; unknown is NULL, "
                "never zero. after is prior4h-gated policy; before is original ungated entry. bp=difference*10000.")
            source["query"]["metric_definitions"][-1] = (
                "Bin minimum/maximum/mean/sum use original opportunity changes in bp. Only actual completed "
                "trades bear20bp roundtrip cost, not abstentions or unknowns. Opportunity averages and selected "
                "trade averages have different denominators; sums are not compounded account P/L.")
    effect = summary["effects"]["case_delta"]
    chart = artifact["manifest"]["charts"][0]
    chart.update(id=CHART_ID, dataset=CHART_ID, sourceId="v13_case_delta",
        title="入场前4小时颜色门的全机会收益变化分布",
        question="仅增加事前4h同向门，全部原始1h机会的收益变化如何分布？",
        subtitle=f"2023–2024 · 全部 {effect['total_pairs']} 机会，未知 {effect['unknown_pairs']} · 门控−原版，bp · 已知放弃计零；分箱计数非密度；零类 |Δ|≤1e−8 bp")
    chart["encodings"]["x"]["label"] = "全机会净收益变化区间（bp；事前4h门控−原版）"
    data = artifact["snapshot"]["datasets"]
    data[CHART_ID] = data.pop("v8_case_difference_distribution")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("markdown", "summary", "case-delta", "output"): parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    paths = {k:safe_identity(getattr(args,k)) for k in ("markdown","summary","case_delta","output")}
    output = ROOT/paths["output"]
    if output.exists() or paths["output"] in (paths["markdown"],paths["summary"],paths["case_delta"]):
        raise ValueError("Use new output; preserve evidence")
    artifact = build_artifact((ROOT/paths["markdown"]).read_text(), json.loads((ROOT/paths["summary"]).read_text()),
        pd.read_csv(ROOT/paths["case_delta"]), markdown_path=paths["markdown"], summary_path=paths["summary"],
        case_delta_path=paths["case_delta"], generated_at=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"output":str(output),"blocks":len(artifact["manifest"]["blocks"]),"charts":1,"opportunities":251}))


if __name__ == "__main__": main()
