"""Package frozen V24 fixed-clock evidence through the canonical report reader.

Only saved labels, diagnostics and preregistered inference are consumed. Native
bar charts use actual SQLite grouped queries; no new quotes or outcome search.
The 50bp histogram bins affect display only and retain the full population.
"""
from pathlib import Path
import hashlib
import json
import math
import re
import sqlite3
import subprocess

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
E = Path(__file__).resolve().parent
REL = E.relative_to(ROOT).as_posix()
REPORT = "analysis/p1_btcusdtp_hourly_fixed_clock_v24_20260907.md"
TITLE = "Entry Persistence Audit"
PINS = {
    "results/summary.json": "d3bbaebde040c519231611fd42833119c4eca6ecccc51c559762ef67ad936926",
    "analysis_results/statistics.json": "84e238e336f39494e36c1c45c11e872e856cdbe9691031d29ddcd1bea6670173",
    "analysis_results/diagnostics_before_inference.json": "fd36fd9680a307330f4ee39bc4543d39bbccde902c11a05372e541333e400311",
}
QUERIES = {
    "folds": """SELECT fold, COUNT(*) AS total, COUNT(cost_threshold_markout) AS known,
        AVG(gross_markout)*10000 AS gross_bp,
        AVG(cost_threshold_markout)*10000 AS cost_threshold_bp
        FROM cases WHERE horizon_hours=4 GROUP BY fold ORDER BY fold""",
    "distribution": """WITH RECURSIVE binned AS (
        SELECT FLOOR(cost_threshold_markout*10000/50)*50 AS lower_bp FROM cases
        WHERE horizon_hours=4 AND status='known'),
        bins(lower_bp) AS (SELECT MIN(lower_bp) FROM binned UNION ALL
        SELECT lower_bp+50 FROM bins WHERE lower_bp < (SELECT MAX(lower_bp) FROM binned)),
        counts AS (SELECT lower_bp, COUNT(*) AS events FROM binned GROUP BY lower_bp)
        SELECT CAST(bins.lower_bp AS INTEGER)||' to <'||CAST(bins.lower_bp+50 AS INTEGER) AS bin,
        bins.lower_bp, bins.lower_bp+50 AS upper_bp, COALESCE(counts.events,0) AS events
        FROM bins LEFT JOIN counts USING(lower_bp) ORDER BY bins.lower_bp""",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "show", commit+":"+relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit report builder before execution")
    for name, expected in PINS.items():
        if sha(E/name) != expected:
            raise ValueError("Frozen V24 evidence changed: "+name)
    summary = json.loads((E/"results/summary.json").read_text())
    inference = json.loads((E/"analysis_results/statistics.json").read_text())
    for name, expected in summary["output_hashes"].items():
        if Path(name).name != name or sha(E/"results"/name) != expected:
            raise ValueError("Saved label changed: "+name)
    if inference["decision"]["status"] != "not_supported" or inference["decision"]["profitability_accepted"]:
        raise ValueError("Reviewed decision changed")
    markdown = (ROOT/REPORT).read_text()
    if not markdown.startswith("# "+TITLE+"\n"):
        raise ValueError("Canonical title mismatch")
    sections = [s.strip() for s in re.split(r"(?m)(?=^## )", markdown.strip())]
    if len(sections) < 10:
        raise ValueError("Incomplete technical narrative")
    cases = pd.read_csv(E/"results/case_labels.csv.gz")
    with sqlite3.connect(":memory:") as con:
        con.create_function("FLOOR", 1, math.floor)
        cases.to_sql("cases", con, index=False)
        datasets = {name: pd.read_sql_query(query, con).to_dict("records") for name, query in QUERIES.items()}
    if sum(r["known"] for r in datasets["folds"]) != 251 or sum(r["events"] for r in datasets["distribution"]) != 251:
        raise ValueError("Charts must retain every primary observation")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sources = [
        {"id": "report", "label": "V24 · 完整方法、失败分析与限制", "path": REPORT},
        {"id": "inference", "label": "V24 · 冻结的月簇区间与检验", "path": REL+"/analysis_results/statistics.json"},
    ]
    for name, query in QUERIES.items():
        sources.append({"id": name, "label": "V24 · 4h全251入口 · "+name,
            "path": REL+"/results/case_labels.csv.gz",
            "query": {"sql": query, "engine": "sqlite", "language": "sql", "tables_used": ["cases"],
                      "executed_at": stamp, "description": "实际查询全部4h标签；50bp分箱仅用于显示，不删尾部",
                      "filters": ["horizon_hours=4; all251 known cases; reused2023--2024 development"]}})
    chart_specs = [
        ("folds", "四个半年均未覆盖成本", "全部原始入口；固定4h OPEN变化减20bp，不是策略净收益",
         "fold", "半年", "cost_threshold_bp", "平均成本阈值 markout (bp)",
         [{"field": "total", "type": "quantitative", "label": "原始入口数"},
          {"field": "gross_bp", "type": "quantitative", "label": "平均毛 markout (bp)"}]),
        ("distribution", "4h结果分布：保留全部尾部", "每档50bp；251入口，零左侧未覆盖20bp成本；未模拟止损",
         "bin", "成本阈值 markout 区间 (bp)", "events", "入口数",
         [{"field": "lower_bp", "type": "quantitative", "label": "含下界 (bp)"},
          {"field": "upper_bp", "type": "quantitative", "label": "不含上界 (bp)"}]),
    ]
    charts = [{"id": name, "type": "bar", "title": title, "description": description,
               "showDescription": True, "dataset": name, "sourceId": name,
               "palette": {"kind": "sequential", "name": "blue"}, "labels": {"values": "all"},
               "settings": {"sort": "none"},
               "encodings": {"x": {"field": x, "type": "nominal", "label": xl},
                             "y": {"field": y, "type": "quantitative", "label": yl}, "tooltip": tooltip}}
              for name, title, description, x, xl, y, yl, tooltip in chart_specs]
    blocks = []
    for i, section in enumerate(sections):
        blocks.append({"id": "section_"+str(i), "type": "markdown", "layout": "full", "body": section,
                       **({"sourceId": "report"} if i else {})})
        for prefix, chart_id in [("## 四个半年", "folds"), ("## 分布与统计", "distribution")]:
            if section.startswith(prefix):
                blocks.append({"id": chart_id+"_chart", "type": "chart", "layout": "full", "chartId": chart_id})
    if sum(b["type"] == "chart" for b in blocks) != 2:
        raise ValueError("Missing analytical chart section")
    artifact = {"surface": "report", "manifest": {"version": 1, "surface": "report", "title": TITLE,
                "generatedAt": stamp, "filters": [], "cards": [], "charts": charts, "tables": [],
                "blocks": blocks, "sources": sources},
                "snapshot": {"version": 1, "generatedAt": stamp, "status": "ready", "datasets": datasets}, "sources": sources}
    (E/"artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    receipt = {"generated_at": stamp, "source_commit": commit, "report_sha256": sha(ROOT/REPORT),
               "input_hashes": PINS, "actual_queries": QUERIES, "datasets": datasets,
               "sections": len(sections), "all_sections_preserved": True, "raw_prices_read": False}
    (E/"artifact_build_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"sections": len(sections), "blocks": len(blocks), "charts": len(charts), "primary_cases": 251}))


if __name__ == "__main__":
    main()
