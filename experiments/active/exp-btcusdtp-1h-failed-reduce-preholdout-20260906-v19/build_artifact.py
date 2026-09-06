"""Preserve the full V19 technical narrative with actual paired-delta counts.

Read saved evidence only; source query aggregates all251 original observations
directly, not a fabricated SQL label on precomputed statistics. No raw market
access, policy fitting, inference change or standalone HTML implementation.
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
REPORT = "analysis/p1_btcusdtp_hourly_failed_reduce_v19_20260906.md"
TITLE = "Confirmed Risk Reduction"
QUERY = """WITH binned AS (SELECT *, CASE
 WHEN difference IS NULL THEN 99 WHEN difference < -0.01 THEN 0
 WHEN difference < -0.005 THEN 1 WHEN difference < -0.002 THEN 2
 WHEN difference < 0 THEN 3 WHEN difference = 0 THEN 4
 WHEN difference < 0.002 THEN 5 WHEN difference < 0.005 THEN 6
 WHEN difference < 0.01 THEN 7 ELSE 8 END AS bin_order FROM case_delta)
 SELECT bin_order, COUNT(*) AS opportunities, AVG(difference)*10000 AS mean_delta_bp,
 AVG(before)*10000 AS mean_before_bp, AVG(after)*10000 AS mean_after_bp
 FROM binned GROUP BY bin_order ORDER BY bin_order"""


def main():
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    if subprocess.check_output(["git", "show", "HEAD:"+relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit report builder before execution")
    markdown = (ROOT/REPORT).read_text()
    if not markdown.startswith("# "+TITLE+"\n"):
        raise ValueError("Canonical title changed")
    sections = [s.strip() for s in re.split(r"(?m)(?=^## )", markdown.strip())]
    if len(sections) < 7 or not all(sections):
        raise ValueError("Preserve the full report narrative")
    summary = json.loads((E/"results/summary.json").read_text())
    for name, expected in summary["output_hashes"].items():
        if hashlib.sha256((E/"results"/name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen result changed: "+name)
    frame = pd.read_csv(E/"results/case_delta.csv")
    with sqlite3.connect(":memory:") as connection:
        frame.to_sql("case_delta", connection, index=False)
        rows = pd.read_sql_query(QUERY, connection).to_dict("records")
    labels = {0:"< −100",1:"−100 to < −50",2:"−50 to < −20",3:"−20 to < 0",4:"0",
        5:"> 0 to < 20",6:"20 to < 50",7:"50 to < 100",8:"≥ 100",99:"Unknown"}
    for row in rows:
        row["range_bp"] = labels[row["bin_order"]]
    if len(frame) != 251 or sum(r["opportunities"] for r in rows) != 251:
        raise ValueError("All251 original opportunities must be retained")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sources = [
        {"id":"report","label":"V19 · 完整规则、结果与限制","path":REPORT},
        {"id":"summary","label":"V19 · 冻结回放结果","path":REL+"/results/summary.json"},
        {"id":"verification","label":"V19 · 独立保存账本验证","path":REL+"/results/independent_verification.json"},
        {"id":"delta","label":"V19 · 全251机会净收益增量","path":REL+"/results/case_delta.csv",
         "query":{"sql":QUERY,"engine":"sqlite","language":"sql","tables_used":["case_delta"],
             "executed_at":stamp,"description":"直接按保存逐机会净增量分箱，包括零、极端值与未知",
             "filters":["All original251 BTC hourly K1 cases in reused2023--2024 development"]}},
    ]
    blocks = []
    for i, section in enumerate(sections):
        blocks.append({"id":"section_"+str(i),"type":"markdown","layout":"full","body":section,
            **({"sourceId":"report"} if i else {})})
        if section.startswith("## 收益增量分布"):
            blocks.append({"id":"delta_chart","type":"chart","chartId":"delta","layout":"full"})
    if not any(b.get("chartId") == "delta" for b in blocks):
        raise ValueError("Missing planned distribution section")
    chart = {"id":"delta","type":"bar","title":"逐机会净收益增量分布",
        "description":"2023—2024年，全251机会；单位bp，候选减基准；不等宽分箱计数而非密度",
        "showDescription":True,"dataset":"delta_distribution","sourceId":"delta",
        "palette":{"kind":"sequential","name":"blue"},"labels":{"values":"all"},
        "settings":{"sort":"none","categoryLabelPolicy":"wrap"},
        "encodings":{"x":{"field":"range_bp","type":"nominal","label":"增量范围（bp）"},
            "y":{"field":"opportunities","type":"quantitative","label":"机会数","format":"number"},
            "tooltip":[{"field":"mean_delta_bp","type":"quantitative","label":"箱内平均增量bp"},
                {"field":"mean_before_bp","type":"quantitative","label":"基准均净bp"},
                {"field":"mean_after_bp","type":"quantitative","label":"候选均净bp"}]}}
    artifact = {"surface":"report","manifest":{"version":1,"surface":"report","title":TITLE,
        "generatedAt":stamp,"filters":[],"cards":[],"charts":[chart],"tables":[],"blocks":blocks,"sources":sources},
        "snapshot":{"version":1,"generatedAt":stamp,"status":"ready","datasets":{"delta_distribution":rows}},
        "sources":sources}
    (E/"artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2)+"\n")
    (E/"artifact_build_receipt.json").write_text(json.dumps({"report_sha256":hashlib.sha256((ROOT/REPORT).read_bytes()).hexdigest(),
        "sections":len(sections),"actual_query":QUERY,"rows":rows,"all_sections_preserved":True}, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"sections":len(sections),"chart_rows":len(rows),"opportunities":len(frame)}))


if __name__ == "__main__":
    main()
