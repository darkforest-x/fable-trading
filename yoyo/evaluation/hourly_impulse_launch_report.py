"""V11 saved-evidence presentation adapter for the fixed 60min/0.5R rule.

Reuse the V8 complete-Markdown parser and actual SQLite count query, not its
strategy labels. No price, strategy, inference or parameter search is run here.
The canonical Data Analytics 0.2.10 artifact owns portable HTML rendering/QA.
Chart contract: all251 original direct-K1 paired request deltas; signed unequal
and open-ended bins, a separate zero atom and unknown bin; count, not density.
One blue root, signed labels, full-width report layout, no redundant legend.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath

import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import _fence, build_artifact as build_base
from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


EXPERIMENT_ID = "exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11"
CHART_ID = "v11_case_difference_distribution"
MARKER = "<!-- V11_DISTRIBUTION -->"


def validate_policies(summary):
    """Reject relabelled exit specifications; do not mutate the fact source."""
    arms = summary.get("arms", {})
    if not isinstance(arms, dict) or set(arms) != {"baseline", "candidate"}:
        raise ValueError("V11 requires baseline and candidate arms")
    base = {"management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
            "exit_mode": "transition_colour", "confirmations": 1}
    for name, extra in (("baseline", {}), ("candidate", {"launch_deadline_minutes": 60, "launch_progress_r": .5})):
        policy = {k: v for k, v in arms[name].get("policy", {}).items() if k != "id"}
        if policy != {**base, **extra} or any(isinstance(v, bool) for v in policy.values()):
            raise ValueError("V11 requires same native5m SMA40 trueflip plus only60min/0.5R launch deadline")


def build_artifact(markdown, summary, case_delta, **kwargs):
    """Preserve every authored section and fenced literal; consume directives."""
    validate_policies(summary)
    if summary.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("Wrong V11 experiment identity")
    if not kwargs.get("fixture", False) and len(case_delta) != 251:
        raise ValueError("V11 requires all251 original direct-K1 requests")
    lines, active, count = [], None, 0
    translations = {"<!-- SOURCE: v11_summary -->": "<!-- SOURCE: v8_summary -->",
                    "<!-- SOURCE: v11_mechanics -->": "<!-- SOURCE: v8_case_delta -->"}
    for line in markdown.splitlines(keepends=True):
        outside = active is None
        active = _fence(line.rstrip("\r\n"), active)
        if outside and line.strip() == "<!-- V8_DISTRIBUTION -->":
            raise ValueError("V8 distribution directive is not valid in V11")
        if outside and line.strip() == MARKER:
            count += 1
            line = line.replace(MARKER, "<!-- V8_DISTRIBUTION -->")
        elif outside and line.strip() in translations:
            line = line.replace(line.strip(), translations[line.strip()])
        lines.append(line)
    if count != 1:
        raise ValueError("Exactly one V11 distribution directive required")
    artifact = build_base("".join(lines), summary, case_delta, **kwargs)
    names = {"v8_summary": "v11_summary", "v8_case_delta": "v11_case_delta"}
    manifest = artifact["manifest"]
    for block in manifest["blocks"]:
        if "sourceId" in block:
            block["sourceId"] = ("v11_mechanics" if block["sourceId"] == "v8_case_delta"
                                 else names.get(block["sourceId"], block["sourceId"]))
        if block["type"] == "chart":
            block.update(id="v11_distribution_chart", chartId=CHART_ID)
    for source in artifact["sources"]:
        source["id"] = names.get(source["id"], source["id"])
        source["label"] = source["label"].replace("V8", "V11")
        if source["id"] == "research_code":
            source["path"] = "yoyo/evaluation/hourly_impulse_launch_research.py"
        elif source["id"] == "presentation_code":
            source["path"] = "yoyo/evaluation/hourly_impulse_launch_report.py"
        elif source["id"] == "v11_case_delta":
            source["query"]["description"] = (
                f"Actual SQLite count query over {source['path']}, loaded as main.case_delta. "
                "Both V11 arms use native5m SMA40(HL2) true colour transitions; only the candidate adds "
                "a60min launch deadline unless a completed post-entry5m CLOSE reached0.5initialR. "
                "The upstream hourly_impulse_launch_research.py supplies fixed-request outcomes. "
                "This adapter reuses count SQL and arithmetic checks from hourly_impulse_management_report.py; "
                "it does not reread prices or rerun inference."
            )
            source["query"]["metric_definitions"][0] = (
                "difference=after-before for every original direct-K1 request; after adds the60min/0.5initialR "
                "completed-close launch deadline, before is V5 native5m trueflip without it. "
                "Both values are fractional net returns; chart bp=difference*10000."
            )
    artifact["sources"].append({"id": "v11_mechanics", "label": "V11 · 完整逐笔退出政策配对明细",
        "path": str(PurePosixPath(safe_identity(kwargs["case_delta_path"])).parent / "paired_case_mechanics.csv.gz")})
    effect = summary["effects"]["case_delta"]
    chart = manifest["charts"][0]
    chart.update(id=CHART_ID, dataset=CHART_ID, sourceId="v11_case_delta",
        title="60 分钟启动期限的逐请求收益变化分布",
        question="加入启动期限后，全部原始1h直接入场请求的改善和恶化如何分布？",
        subtitle=f"2023–2024 · 全部 {effect['total_pairs']} 请求，未知 {effect['unknown_pairs']} · 加入期限−原版，bp · 分箱计数非密度；零类 |Δ|≤1e−8 bp")
    chart["encodings"]["x"]["label"] = "净收益变化区间（bp；加入启动期限−原版）"
    datasets = artifact["snapshot"]["datasets"]
    datasets[CHART_ID] = datasets.pop("v8_case_difference_distribution")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("markdown", "summary", "case-delta", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    paths = {key: safe_identity(getattr(args, key)) for key in ("markdown", "summary", "case_delta", "output")}
    if paths["output"] in {paths["markdown"], paths["summary"], paths["case_delta"]}:
        raise ValueError("Output cannot overwrite evidence")
    output = ROOT / paths["output"]
    if output.exists():
        raise ValueError("Use a new artifact output; preserve previous delivery")
    artifact = build_artifact((ROOT / paths["markdown"]).read_text(),
        json.loads((ROOT / paths["summary"]).read_text()), pd.read_csv(ROOT / paths["case_delta"]),
        markdown_path=paths["markdown"], summary_path=paths["summary"], case_delta_path=paths["case_delta"],
        generated_at=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"blocks": len(artifact["manifest"]["blocks"]), "charts": 1, "requests": 251, "output": str(output)}))


if __name__ == "__main__":
    main()
