"""Package the complete reviewed V22 narrative with source-backed support bars.

Chart queries directly aggregate713 saved entry-known contexts and251
paired deltas; neither is a fabricated SQL label on precomputed statistics. The report itself
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
REPORT = "analysis/p1_btcusdtp_hourly_breadth_change_v22_20260907.md"
TITLE = "External Rank Change"
QUERY = """SELECT population, fold, COUNT(*) AS total,
SUM(CASE WHEN breadth_gate_state='accepted' THEN 1 ELSE 0 END) AS accepted,
SUM(CASE WHEN breadth_gate_state='abstain' THEN 1 ELSE 0 END) AS abstain,
SUM(CASE WHEN breadth_gate_state='unknown' THEN 1 ELSE 0 END) AS unknown
FROM entry_context GROUP BY population,fold ORDER BY population,fold"""
DELTA_QUERY = """WITH binned AS (SELECT *, CASE
 WHEN difference IS NULL THEN 99 WHEN difference < -0.01 THEN 0
 WHEN difference < -0.005 THEN 1 WHEN difference < -0.002 THEN 2
 WHEN difference < 0 THEN 3 WHEN difference = 0 THEN 4
 WHEN difference < 0.002 THEN 5 WHEN difference < 0.005 THEN 6
 WHEN difference < 0.01 THEN 7 ELSE 8 END AS bin_order FROM case_delta)
 SELECT bin_order, COUNT(*) AS opportunities, AVG(difference)*10000 AS mean_delta_bp,
 AVG(before)*10000 AS mean_before_bp, AVG(after)*10000 AS mean_after_bp
 FROM binned GROUP BY bin_order ORDER BY bin_order"""


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
    context[["population", "fold", "breadth_gate_state"]].to_sql("entry_context", connection, index=False)
    rows = pd.read_sql_query(QUERY, connection).to_dict("records")
    delta = pd.read_csv(E/"results/case_delta.csv.gz")
    delta.to_sql("case_delta", connection, index=False)
    delta_rows = pd.read_sql_query(DELTA_QUERY, connection).to_dict("records")
    labels = {0: "< −100", 1: "−100 to < −50", 2: "−50 to < −20", 3: "−20 to < 0",
        4: "0", 5: "> 0 to < 20", 6: "20 to < 50", 7: "50 to < 100", 8: "≥ 100", 99: "Unknown"}
    for row in delta_rows:
        row["range_bp"] = labels[row["bin_order"]]
    connection.close()
    for pop, expected in summary["population"].items():
        group = [r for r in rows if r["population"] == pop]
        if any(sum(r[k] for r in group) != expected[k] for k in ("total", "accepted", "abstain", "unknown")):
            raise ValueError("Chart aggregate disagrees with reviewed support")
    stamp = summary["generated_at"]
    sources = [
        {"id": "report", "label": "V22 · 完整方法、结果与限制", "path": REPORT},
        {"id": "support", "label": "V22 · 原始713个自身外部评分变化方向状态", "path": REL+"/results/entry_context.csv.gz",
         "query": {"sql": QUERY, "engine": "sqlite", "language": "sql",
            "tables_used": ["entry_context"], "description": "按原始半年度汇总各自外部评分变化门，不读取收益",
            "filters": ["2023--2024 original251 cases and462 fixed own controls"],
            "executed_at": pd.Timestamp.now(tz="UTC").isoformat()}},
        {"id": "summary", "label": "V22 · 冻结汇总", "path": REL+"/results/summary.json"},
        {"id": "verification", "label": "V22 · 独立四币小时分数复核", "path": REL+"/results/independent_verification.json"},
        {"id": "delta", "label": "V22 · 全251机会收益增量", "path": REL+"/results/case_delta.csv.gz",
         "query": {"sql": DELTA_QUERY, "engine": "sqlite", "language": "sql", "tables_used": ["case_delta"],
             "description": "按每机会候选减基准的净收益增量分箱，不删除零值、极端值或未知",
             "executed_at": pd.Timestamp.now(tz="UTC").isoformat()}},
    ]
    blocks = []
    for i, body in enumerate(bodies):
        blocks.append({"id": "section_"+str(i), "type": "markdown", "layout": "full", "body": body,
            **({"sourceId": "report"} if i else {})})
        if body.startswith("## 时间覆盖"):
            blocks.append({"id": "support_chart", "type": "chart", "chartId": "support", "layout": "full"})
        if body.startswith("## 收益增量分布"):
            blocks.append({"id": "delta_chart", "type": "chart", "chartId": "delta", "layout": "full"})
    if not any(b.get("chartId") == "support" for b in blocks):
        raise ValueError("Missing planned support evidence section")
    chart = {"id": "support", "type": "bar", "title": "各半年度获准的原始 K1 机会",
        "description": "2023—2024年；柱为外部评分变化同向的机会数，参考线为每半年度最低12笔，不代表收益",
        "showDescription": True, "dataset": "case_support", "sourceId": "support",
        "palette": {"kind": "sequential", "name": "blue"}, "labels": {"values": "all"},
        "settings": {"sort": "none", "categoryLabelPolicy": "wrap"},
        "referenceLines": [{"axis": "y", "value": 12, "color": "neutral", "lineStyle": "dashed", "label": "最低12笔"}],
        "encodings": {"x": {"field": "fold", "type": "nominal", "label": "原始半年度"},
            "y": {"field": "accepted", "type": "quantitative", "label": "获准机会数", "format": "number"},
            "tooltip": [{"field": "total", "type": "quantitative", "label": "全部机会"},
                {"field": "abstain", "type": "quantitative", "label": "中性或反向"},
                {"field": "unknown", "type": "quantitative", "label": "未知"}]}}
    delta_chart = {"id": "delta", "type": "bar", "title": "逐机会净收益增量分布",
        "description": "单位bp；候选减基准；不等宽分箱的机会计数，非概率密度；0包含保留的原路径",
        "showDescription": True, "dataset": "delta_distribution", "sourceId": "delta",
        "palette": {"kind": "sequential", "name": "blue"}, "labels": {"values": "all"},
        "settings": {"sort": "none", "categoryLabelPolicy": "wrap"},
        "encodings": {"x": {"field": "range_bp", "type": "nominal", "label": "增量范围（bp）"},
            "y": {"field": "opportunities", "type": "quantitative", "label": "机会数", "format": "number"},
            "tooltip": [{"field": "mean_delta_bp", "type": "quantitative", "label": "箱内平均增量bp"},
                {"field": "mean_before_bp", "type": "quantitative", "label": "基准均净bp"},
                {"field": "mean_after_bp", "type": "quantitative", "label": "候选均净bp"}]}}
    if sum(row["opportunities"] for row in delta_rows) != 251:
        raise ValueError("Delta distribution must retain all251 opportunities")
    artifact = {"surface": "report", "manifest": {"version": 1, "surface": "report", "title": TITLE,
        "generatedAt": stamp, "filters": [], "cards": [], "charts": [chart, delta_chart], "tables": [],
        "blocks": blocks, "sources": sources}, "snapshot": {"version": 1, "generatedAt": stamp,
        "status": "ready", "datasets": {"case_support": [r for r in rows if r["population"] == "case"],
            "delta_distribution": delta_rows}}, "sources": sources}
    (E/"artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2)+"\n")
    (E/"artifact_build_receipt.json").write_text(json.dumps({"report_sha256": hashlib.sha256((ROOT/REPORT).read_bytes()).hexdigest(),
        "sections": len(bodies), "context_rows": len(context), "actual_query": QUERY,
        "query_results": rows, "delta_query": DELTA_QUERY, "delta_query_results": delta_rows,
        "all_markdown_sections_preserved": True}, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"sections": len(bodies), "chart_rows": len(artifact["snapshot"]["datasets"]["case_support"])}))


if __name__ == "__main__":
    main()
