"""Package the saved V23 support audit, without price or outcome access.

Use the canonical Data Analytics artifact contract and actual SQLite grouped
counts from all original assignment rows. The portable reader owns HTML.
This builder verifies frozen output hashes, not raw feature correctness.
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
REPORT = "analysis/p1_btcusdtp_hourly_background_support_v23_20260907.md"
TITLE = "Entry Comparison Coverage"
QUERY = """SELECT fold, COUNT(*) AS mothers,
 SUM(CASE WHEN assigned_controls=3 THEN 1 ELSE 0 END) AS matched_mothers,
 SUM(assigned_controls) AS controls,
 SUM(CASE WHEN assigned_controls=3 THEN 1.0 ELSE 0.0 END)/COUNT(*) AS coverage,
 SUM(CASE WHEN assigned_controls=0 THEN 1 ELSE 0 END) AS unmatched_mothers
 FROM assignments GROUP BY fold ORDER BY fold"""


def main():
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    if subprocess.check_output(["git", "show", "HEAD:"+relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit report builder before execution")
    markdown = (ROOT/REPORT).read_text()
    if not markdown.startswith("# "+TITLE+"\n"):
        raise ValueError("Canonical title mismatch")
    sections = [x.strip() for x in re.split(r"(?m)(?=^## )", markdown.strip())]
    if len(sections) < 9:
        raise ValueError("Incomplete technical narrative")
    summary = json.loads((E/"results/summary.json").read_text())
    for name, expected in summary["output_hashes"].items():
        if hashlib.sha256((E/"results"/name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen output changed: "+name)
    assignments = pd.read_csv(E/"results/assignments.csv.gz")
    with sqlite3.connect(":memory:") as con:
        assignments.to_sql("assignments", con, index=False)
        rows = pd.read_sql_query(QUERY, con).to_dict("records")
    if sum(r["mothers"] for r in rows) != 251 or sum(r["matched_mothers"] for r in rows) != 248:
        raise ValueError("Reviewed support denominator changed")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sources = [
        {"id":"report", "label":"V23 · 完整支持审计与限制", "path":REPORT},
        {"id":"summary", "label":"V23 · 冻结容量和来源收据", "path":REL+"/results/summary.json"},
        {"id":"coverage", "label":"V23 · 原251母的配对覆盖", "path":REL+"/results/assignments.csv.gz",
         "query":{"sql":QUERY,"engine":"sqlite","language":"sql","tables_used":["assignments"],
                  "executed_at":stamp,"description":"原251母按四半年统计完整三控制组；未知不删",
                  "filters":["All original251 requests; reused2023--2024 development; no outcome filters"]}},
    ]
    blocks = []
    for i, section in enumerate(sections):
        blocks.append({"id":"section_"+str(i),"type":"markdown","layout":"full","body":section,
                       **({"sourceId":"report"} if i else {})})
        if section.startswith("## 四个半年"):
            blocks.append({"id":"coverage_chart","type":"chart","chartId":"coverage","layout":"full"})
    if not any(b.get("chartId")=="coverage" for b in blocks):
        raise ValueError("Missing coverage section")
    chart={"id":"coverage","type":"bar","title":"四半年完整对照覆盖率",
           "description":"2023—2024年；分母为各段原始信号数，完整组要求3个不重复对照",
           "showDescription":True,"dataset":"fold_coverage","sourceId":"coverage",
           "palette":{"kind":"sequential","name":"blue"},"labels":{"values":"all"},
           "settings":{"sort":"none"},
           "encodings":{"x":{"field":"fold","type":"nominal","label":"半年"},
                        "y":{"field":"coverage","type":"quantitative","label":"完整覆盖率","format":"percent"},
                        "tooltip":[{"field":"mothers","type":"quantitative","label":"原始信号数"},
                                   {"field":"matched_mothers","type":"quantitative","label":"完整组数"},
                                   {"field":"controls","type":"quantitative","label":"对照数"},
                                   {"field":"unmatched_mothers","type":"quantitative","label":"未知或未配对"}]}}
    artifact={"surface":"report","manifest":{"version":1,"surface":"report","title":TITLE,
              "generatedAt":stamp,"filters":[],"cards":[],"charts":[chart],"tables":[],"blocks":blocks,"sources":sources},
              "snapshot":{"version":1,"generatedAt":stamp,"status":"ready","datasets":{"fold_coverage":rows}},"sources":sources}
    (E/"artifact.json").write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    (E/"artifact_build_receipt.json").write_text(json.dumps({"generated_at":stamp,
        "report_sha256":hashlib.sha256((ROOT/REPORT).read_bytes()).hexdigest(),
        "summary_sha256":hashlib.sha256((E/"results/summary.json").read_bytes()).hexdigest(),
        "sections":len(sections),"actual_query":QUERY,"rows":rows,
        "all_sections_preserved":True},ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"sections":len(sections),"chart_rows":len(rows),"mothers":len(assignments)}))


if __name__ == "__main__":
    main()
