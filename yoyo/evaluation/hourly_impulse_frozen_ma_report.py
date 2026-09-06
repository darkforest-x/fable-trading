"""V12 native report adapter over saved fixed-K1-MA exit evidence only.

Canonical Data Analytics0.2.10 supplies rendering and portable HTML QA. Reuse
the V8 complete-Markdown parser and actual SQLite count query, never V8/V11
policy semantics. All251 requests retain signed bins, zero atom and unknowns;
unequal/open-ended bins are counts, not density. The one blue quantitative
series uses direct count labels and a full-width chart without extra runtime.
No prices, strategies, inference, parameter selection or artifact writes occur
inside build_artifact. Whole peer sections and fenced literal text survive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath

import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import _fence, build_artifact as build_base
from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


EXPERIMENT_ID = "exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12"
CHART_ID = "v12_case_difference_distribution"
MARKER = "<!-- V12_DISTRIBUTION -->"


def validate_policies(summary):
    """Require the exact V12 two-arm policy, including actual boolean opt-in."""
    arms = summary.get("arms", {})
    if not isinstance(arms, dict) or set(arms) != {"baseline", "candidate"}:
        raise ValueError("V12 requires baseline and candidate arms")
    base = {"id": "5m_native40", "management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
            "exit_mode": "transition_colour", "confirmations": 1}
    for name, expected in (("baseline", base), ("candidate", {**base, "id": "5m_native40_frozen_ma", "frozen_ma_exit": True})):
        policy = arms[name].get("policy", {})
        if policy != expected or (name == "candidate" and policy.get("frozen_ma_exit") is not True):
            raise ValueError("V12 requires native5m trueflip plus only frozen_ma_exit=True, no launch/cadence")
        if any(isinstance(v, bool) for k,v in policy.items() if k != "frozen_ma_exit"):
            raise ValueError("Boolean cannot replace numeric policy fields")


def build_artifact(markdown, summary, case_delta, **kwargs):
    """Build one full canonical report; replace only explicit directive lines."""
    validate_policies(summary)
    if summary.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("Wrong V12 experiment identity")
    if not kwargs.get("fixture", False) and len(case_delta) != 251:
        raise ValueError("V12 requires all251 original direct-K1 requests")
    translations = {"<!-- SOURCE: v12_summary -->": "<!-- SOURCE: v8_summary -->",
                    "<!-- SOURCE: v12_mechanics -->": "<!-- SOURCE: v8_case_delta -->",
                    "<!-- SOURCE: v12_geometry -->": "<!-- SOURCE: report -->"}
    lines, active, count = [], None, 0
    for line in markdown.splitlines(keepends=True):
        outside = active is None
        active = _fence(line.rstrip("\r\n"), active)
        if outside and line.strip() in {"<!-- V8_DISTRIBUTION -->", "<!-- V11_DISTRIBUTION -->",
                                       "<!-- SOURCE: report -->", "<!-- SOURCE: v8_summary -->", "<!-- SOURCE: v8_case_delta -->"}:
            raise ValueError("Legacy/internal directive is not valid in V12")
        if outside and line.strip() == MARKER:
            count += 1
            line = line.replace(MARKER, "<!-- V8_DISTRIBUTION -->")
        elif outside and line.strip() in translations:
            line = line.replace(line.strip(), translations[line.strip()])
        lines.append(line)
    if count != 1:
        raise ValueError("Exactly one V12 distribution directive required")
    artifact = build_base("".join(lines), summary, case_delta, **kwargs)
    names = {"v8_summary": "v12_summary", "v8_case_delta": "v12_case_delta"}
    block_names = {"v8_summary": "v12_summary", "v8_case_delta": "v12_mechanics", "report": "v12_geometry"}
    manifest = artifact["manifest"]
    for block in manifest["blocks"]:
        if "sourceId" in block:
            block["sourceId"] = block_names.get(block["sourceId"], block["sourceId"])
        if block["type"] == "chart":
            block.update(id="v12_distribution_chart", chartId=CHART_ID)
    for source in artifact["sources"]:
        source["id"] = names.get(source["id"], source["id"])
        source["label"] = source["label"].replace("V8", "V12")
        if source["id"] == "research_code":
            source["path"] = "yoyo/evaluation/hourly_impulse_frozen_ma_research.py"
        elif source["id"] == "presentation_code":
            source["path"] = "yoyo/evaluation/hourly_impulse_frozen_ma_report.py"
        elif source["id"] == "v12_case_delta":
            source["query"]["description"] = (
                f"Actual SQLite count query over {source['path']}, loaded as main.case_delta. "
                "Both V12 arms use native5m SMA40(HL2) true colour transitions; only the candidate "
                "adds first fully-held5m CLOSE strictly beyond the frozen signal-hour SMA40 on the wrong side, "
                "filled at its next actual5m open. No launch deadline or sampled cadence is added. "
                "hourly_impulse_frozen_ma_research.py supplied outcomes. This adapter reuses count SQL "
                "and arithmetic checks from hourly_impulse_management_report.py, without price/inference replay."
            )
            source["query"]["metric_definitions"][0] = (
                "difference=after-before for every original direct-K1 request; after adds frozen signal-hour "
                "MA structural invalidation, before is native5m trueflip alone. Frozen MA is each request's "
                "own completed signal-hour SMA40(HL2), not a moving management MA or transferred case price. "
                "Both are fractional net returns; chart bp=difference*10000."
            )
    directory = PurePosixPath(safe_identity(kwargs["case_delta_path"])).parent
    artifact["sources"].extend([
        {"id": "v12_mechanics", "label": "V12 · 完整逐笔退出政策配对明细", "path": str(directory / "paired_case_mechanics.csv.gz")},
        {"id": "v12_geometry", "label": "V12 · 全部病例与固定控制的入场时边界几何", "path": str(directory / "entry_geometry.csv")},
    ])
    effect = summary["effects"]["case_delta"]
    chart = manifest["charts"][0]
    chart.update(id=CHART_ID, dataset=CHART_ID, sourceId="v12_case_delta",
        title="冻结 K1 均线失效退出的逐请求收益变化分布",
        question="加入冻结结构失效退出后，全部原始1h直接入场请求的变化如何分布？",
        subtitle=f"2023–2024 · 全部 {effect['total_pairs']} 请求，未知 {effect['unknown_pairs']} · 加入冻结均线退出−原版，bp · 分箱计数非密度；零类 |Δ|≤1e−8 bp")
    chart["encodings"]["x"]["label"] = "净收益变化区间（bp；加入冻结均线退出−原版）"
    datasets = artifact["snapshot"]["datasets"]
    datasets[CHART_ID] = datasets.pop("v8_case_difference_distribution")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("markdown", "summary", "case-delta", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    paths = {key: safe_identity(getattr(args, key)) for key in ("markdown", "summary", "case_delta", "output")}
    output = ROOT / paths["output"]
    if output.exists() or paths["output"] in {paths["markdown"], paths["summary"], paths["case_delta"]}:
        raise ValueError("Use a new artifact output; do not overwrite evidence")
    artifact = build_artifact((ROOT / paths["markdown"]).read_text(),
        json.loads((ROOT / paths["summary"]).read_text()), pd.read_csv(ROOT / paths["case_delta"]),
        markdown_path=paths["markdown"], summary_path=paths["summary"], case_delta_path=paths["case_delta"],
        generated_at=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"blocks": len(artifact["manifest"]["blocks"]), "charts": 1, "requests": 251, "output": str(output)}))


if __name__ == "__main__":
    main()
