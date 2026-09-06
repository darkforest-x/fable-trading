"""Package the complete reviewed V20 narrative with source-backed support bars.

The only chart query directly aggregates the713 saved entry-known contexts;
it is not a fabricated SQL label on precomputed statistics. The report itself
is authored/reviewed separately. No market access, strategy fitting or replay.
"""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import subprocess

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
E = Path(__file__).resolve().parent
REL = E.relative_to(ROOT).as_posix()
REPORT = "analysis/p1_btcusdtp_hourly_structure_v20_20260906.md"
TITLE = "Hourly Structure Gate"
QUERY = """SELECT population, fold, COUNT(*) AS total,
SUM(CASE WHEN structure_gate_state='accepted' THEN 1 ELSE 0 END) AS accepted,
SUM(CASE WHEN structure_gate_state='abstain' THEN 1 ELSE 0 END) AS abstain,
SUM(CASE WHEN structure_gate_state='unknown' THEN 1 ELSE 0 END) AS unknown
FROM entry_context GROUP BY population,fold ORDER BY population,fold"""


def sections(markdown):
    if not markdown.startswith("# "+TITLE+"\n"):
        raise ValueError("Canonical report title changed")
    pieces = re.split(r"(?m)(?=^## )", markdown.strip())
    if len(pieces) < 7:
        raise ValueError("Preserve complete definitions/evidence/method/limits/next-step narrative")
    if any(not piece.strip() for piece in pieces):
        raise ValueError("Empty report section")
    return [piece.strip() for piece in pieces]


def main():
    relative_builder = Path(__file__).resolve().relative_to(ROOT).as_posix()
    committed = subprocess.check_output(["git", "show", "HEAD:"+relative_builder], cwd=ROOT)
    if committed != Path(__file__).read_bytes():
        raise ValueError("Commit builder before generating artifact")
    raw_report = (ROOT/REPORT).read_text()
    bodies = sections(raw_report)
    summary = json.loads((E/"results/summary.json").read_text())
    freeze = json.loads((E/"results/context_frozen.json").read_text())
    for name, expected in freeze["output_hashes"].items():
        if hashlib.sha256((E/"results"/name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen context changed")
    context = pd.read_csv(E/"results/entry_context.csv.gz")
    connection = sqlite3.connect(":memory:")
    context[["population", "fold", "structure_gate_state"]].to_sql("entry_context", connection, index=False)
    rows = pd.read_sql_query(QUERY, connection).to_dict("records")
    connection.close()
    for pop, expected in summary["population"].items():
        group = [r for r in rows if r["population"] == pop]
        if any(sum(r[k] for r in group) != expected[k] for k in ("total", "accepted", "abstain", "unknown")):
            raise ValueError("Chart aggregate disagrees with reviewed support")
    stamp = summary["generated_at"]
    sources = [
        {"id": "report", "label": "V20 · 完整方法、结果与限制", "path": REPORT},
        {"id": "support", "label": "V20 · 原始713个自身入场结构状态", "path": REL+"/results/entry_context.csv.gz",
         "query": {"sql": QUERY, "engine": "sqlite", "language": "sql",
            "tables_used": ["entry_context"], "description": "按原始半年度汇总各自结构门，不读取收益",
            "filters": ["2023--2024 original251 cases and462 fixed own controls"],
            "executed_at": pd.Timestamp.now(tz="UTC").isoformat()}},
        {"id": "summary", "label": "V20 · 冻结汇总", "path": REL+"/results/summary.json"},
        {"id": "verification", "label": "V20 · 独立小时状态复核", "path": REL+"/results/independent_verification.json"},
    ]
    blocks = []
    for i, body in enumerate(bodies):
        blocks.append({"id": "section_"+str(i), "type": "markdown", "layout": "full", "body": body,
            **({"sourceId": "report"} if i else {})})
        if body.startswith("## 时间覆盖"):
            blocks.append({"id": "support_chart", "type": "chart", "chartId": "support", "layout": "full"})
    if not any(b.get("chartId") == "support" for b in blocks):
        raise ValueError("Missing planned support evidence section")
    chart = {"id": "support", "type": "bar", "title": "各半年度获准的原始 K1 机会",
        "description": "2023—2024年；柱为结构同向的机会数，参考线为每半年度最低12笔，不代表收益",
        "showDescription": True, "dataset": "case_support", "sourceId": "support",
        "palette": {"kind": "sequential", "name": "blue"}, "labels": {"values": "all"},
        "settings": {"sort": "none", "categoryLabelPolicy": "wrap"},
        "referenceLines": [{"axis": "y", "value": 12, "color": "neutral", "lineStyle": "dashed", "label": "最低12笔"}],
        "encodings": {"x": {"field": "fold", "type": "nominal", "label": "原始半年度"},
            "y": {"field": "accepted", "type": "quantitative", "label": "获准机会数", "format": "number"},
            "tooltip": [{"field": "total", "type": "quantitative", "label": "全部机会"},
                {"field": "abstain", "type": "quantitative", "label": "已知反向"},
                {"field": "unknown", "type": "quantitative", "label": "未知"}]}}
    artifact = {"surface": "report", "manifest": {"version": 1, "surface": "report", "title": TITLE,
        "generatedAt": stamp, "filters": [], "cards": [], "charts": [chart], "tables": [],
        "blocks": blocks, "sources": sources}, "snapshot": {"version": 1, "generatedAt": stamp,
        "status": "ready", "datasets": {"case_support": [r for r in rows if r["population"] == "case"]}}, "sources": sources}
    (E/"artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2)+"\n")
    (E/"artifact_build_receipt.json").write_text(json.dumps({"report_sha256": hashlib.sha256((ROOT/REPORT).read_bytes()).hexdigest(),
        "sections": len(bodies), "context_rows": len(context), "actual_query": QUERY,
        "query_results": rows, "all_markdown_sections_preserved": True}, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"sections": len(bodies), "chart_rows": len(artifact["snapshot"]["datasets"]["case_support"])}))


if __name__ == "__main__":
    main()
