"""V9 presentation adapter: reuse validated counts, not V8 strategy semantics.

Only saved paired outcomes and authored Markdown are read. The old builder's
parser/count/identity checks are reused without changing its historical output.
Versioned marker lines are translated only for parsing; prose is never rewritten.
All returned strategy-specific source/metric/chart metadata is explicitly V9.
Canonical Data Analytics 0.2.10 artifact packaging owns rendering and HTML QA.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re

import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import build_artifact as build_base
from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


CHART_ID = "v9_case_difference_distribution"


def build_artifact(markdown, summary, case_delta, **kwargs):
    """Preserve authored narrative, replacing only explicit directive tokens."""
    policies = [arm.get("policy", {}) for arm in summary.get("arms", [])]
    if len(policies) != 2 or any(
        policy.get("management_minutes") != 5 or policy.get("ma_kind") != "SMA"
        or policy.get("ma_length") != 40 or policy.get("exit_mode") != "transition_colour"
        or policy.get("confirmations") != 1
        or policy.get("decision_minutes", 5) != expected
        for policy, expected in zip(policies, (5, 15))
    ):
        raise ValueError("V9 report requires same native5m SMA40 and check5m/check15m policies")
    # V8 tokens in literal prose/fences are not rewritten. New directives are
    # deliberately a separate versioned dialect, not an unsourced title change.
    translated = re.sub(r"(?m)^<!-- V9_DISTRIBUTION -->$", "<!-- V8_DISTRIBUTION -->", markdown)
    translated = re.sub(r"(?m)^<!-- SOURCE: v9_summary -->$", "<!-- SOURCE: v8_summary -->", translated)
    if markdown.count("<!-- V9_DISTRIBUTION -->") != 1 or "<!-- V8_DISTRIBUTION -->" in markdown:
        raise ValueError("exactly one V9 distribution directive required")
    artifact = build_base(translated, summary, case_delta, **kwargs)
    names = {"v8_summary": "v9_summary", "v8_case_delta": "v9_case_delta"}
    manifest = artifact["manifest"]
    for block in manifest["blocks"]:
        if "sourceId" in block:
            block["sourceId"] = names.get(block["sourceId"], block["sourceId"])
        if block["type"] == "chart":
            block.update(id="v9_distribution_chart", chartId=CHART_ID)
    # manifest.sources and top-level sources share this same canonical list.
    for source in artifact["sources"]:
        source["id"] = names.get(source["id"], source["id"])
        source["label"] = source["label"].replace("V8", "V9")
        if source["id"] == "research_code":
            source["path"] = "yoyo/evaluation/hourly_impulse_cadence_research.py"
        elif source["id"] == "presentation_code":
            source["path"] = "yoyo/evaluation/hourly_impulse_cadence_report.py"
        elif source["id"] == "v9_case_delta":
            query = source["query"]
            query["description"] = (
                f"Actual SQLite count query over {source['path']}, loaded as main.case_delta. "
                "The V9 Python evaluator produced fixed-request paired outcomes; "
                "both arms use the SAME native5m SMA40(HL2), with decision clocks5m versus15m. "
                "The count SQL and arithmetic validation are reused from hourly_impulse_management_report.py; "
                "no prices, selection or inference are recomputed by this presentation adapter."
            )
            query["metric_definitions"][0] = (
                "difference=after-before on each original request; after is native5m SMA40 checked every15m, "
                "before is the same native5m SMA40 checked every5m. Both are net returns; chart bp=difference*10000."
            )
    chart = manifest["charts"][0]
    chart.update(id=CHART_ID, dataset=CHART_ID, sourceId="v9_case_delta",
                 title="退出检查频率变化的逐请求收益分布",
                 question="均线特征不变时，放慢检查对原始请求的改善和恶化如何分布？")
    count = summary["effects"]["case_delta"]["total_pairs"]
    unknown = summary["effects"]["case_delta"]["unknown_pairs"]
    chart["subtitle"] = (
        f"2023–2024 · 全部 {count} 请求，未知 {unknown} · 同一5m SMA40，15m检查−5m检查，bp "
        "· 分箱计数非密度；零类 |Δ|≤1e−8 bp"
    )
    chart["encodings"]["x"]["label"] = "净收益变化区间（bp；15m检查−5m检查）"
    datasets = artifact["snapshot"]["datasets"]
    datasets[CHART_ID] = datasets.pop("v8_case_difference_distribution")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("markdown", "summary", "case-delta", "output"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    paths = {key: safe_identity(getattr(args, key)) for key in ("markdown", "summary", "case_delta", "output")}
    if paths["output"] in {paths["markdown"], paths["summary"], paths["case_delta"]}:
        raise ValueError("output cannot overwrite evidence")
    artifact = build_artifact((ROOT/paths["markdown"]).read_text(),
        json.loads((ROOT/paths["summary"]).read_text()), pd.read_csv(ROOT/paths["case_delta"]),
        markdown_path=paths["markdown"], summary_path=paths["summary"],
        case_delta_path=paths["case_delta"], generated_at=datetime.now(timezone.utc).isoformat())
    output = ROOT/paths["output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"blocks": len(artifact["manifest"]["blocks"]), "charts": 1, "output": str(output)}))


if __name__ == "__main__":
    main()
