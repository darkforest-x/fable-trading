"""Package reviewed V26 support evidence, with no price/outcome calculations.

Native monthly difference bar chart: all24 UTC months, zero origin, blue
root and signed values; retain both references and full clock denominators.
Tables in the technical narrative serve exact support/threshold lookup.
Chart dataset executes the recorded SQLite projection of frozen counts.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.to_sql.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_sql_query.html
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
REPORT = "analysis/p1_btcusdtp_hourly_vwma_reference_support_v26_20260907.md"
TITLE = "SMA vs VWMA Entry Support"
QUERY = """SELECT s.key, s.total, s.accepted AS sma_accepted,
v.accepted AS vwma_accepted, v.accepted-s.accepted AS delta,
s.abstain AS sma_abstain, v.abstain AS vwma_abstain,
s.unknown AS sma_unknown, v.unknown AS vwma_unknown
FROM counts s JOIN counts v ON s.key=v.key
WHERE s.population='all_clock_directions' AND s.dimension='month' AND s.arm='sma'
AND v.population='all_clock_directions' AND v.dimension='month' AND v.arm='vwma'
ORDER BY s.key"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    relative = Path(__file__).resolve().relative_to(ROOT).as_posix()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "show", commit+":"+relative], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit report builder before execution")
    summary = json.loads((E/"results/summary.json").read_text())
    if sha(E/"results/support_frozen.json") != summary["support_frozen_sha256"]:
        raise ValueError("Support checkpoint changed")
    frozen = json.loads((E/"results/support_frozen.json").read_text())
    if summary["output_hashes"] != frozen["output_hashes"] or summary["outcomes_read_or_computed"] or summary["economic_acceptance"]:
        raise ValueError("Not a frozen support-only result")
    for name, expected in summary["output_hashes"].items():
        if Path(name).name != name or sha(E/"results"/name) != expected:
            raise ValueError("Saved support evidence changed: "+name)
    markdown = (ROOT/REPORT).read_text()
    if not markdown.startswith("# "+TITLE+"\n"):
        raise ValueError("Canonical title mismatch")
    sections = [s.strip() for s in re.split(r"(?m)(?=^## )", markdown.strip())]
    if len(sections) < 8:
        raise ValueError("Incomplete technical narrative")
    counts = pd.read_csv(E/"results/counts.csv.gz")
    with sqlite3.connect(":memory:") as connection:
        counts.to_sql("counts", connection, index=False)
        monthly = pd.read_sql_query(QUERY, connection)
    if len(monthly) != 24 or monthly.total.sum() != 34512 or monthly.sma_accepted.sum() != 251 or monthly.vwma_accepted.sum() != 288:
        raise ValueError("Monthly display must retain both arms and all24 months")
    data = monthly.to_dict("records")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sources = [
        {"id":"report", "label":"V26 · 定义、支持分母、验证与限制", "path":REPORT},
        {"id":"monthly", "label":"V26 · frozen counts中两臂全部月份，保留零月", "path":REL+"/results/counts.csv.gz",
         "query":{"sql":QUERY,"engine":"sqlite","language":"sql","tables_used":["counts"],
                  "executed_at":stamp,"description":"实际SQLite连接冻结124行计数中的两臂月份；24个月保留两参考、全时钟分母及未知",
                  "filters":["population=all_clock_directions; dimension=month; no outcome selection"]}},
        {"id":"summary", "label":"V26 · 只审支持、不读取收益", "path":REL+"/results/summary.json"},
    ]
    chart = {"id":"monthly", "type":"bar", "title":"每月入口净变化（2023–2024）",
             "description":"VWMA40减SMA40，单位：机会数；正值代表入口增加，不代表利润增加",
             "showDescription":True, "dataset":"monthly", "sourceId":"monthly",
             "palette":{"kind":"sequential","name":"blue"}, "labels":{"values":"all"},
             "settings":{"sort":"none"},
             "encodings":{"x":{"field":"key","type":"nominal","label":"UTC月份"},
                          "y":{"field":"delta","type":"quantitative","label":"净变化（笔）"},
                          "tooltip":[{"field":f,"type":"quantitative","label":label} for f,label in
                              [("total","时钟×方向分母"),("sma_accepted","SMA入口"),("vwma_accepted","VWMA入口"),("sma_unknown","SMA未知"),("vwma_unknown","VWMA未知")]]}}
    blocks = []
    for i, section in enumerate(sections):
        blocks.append({"id":"section_"+str(i),"type":"markdown","layout":"full","body":section,
                       **({"sourceId":"report"} if i else {})})
        if section.startswith("## 月份支持"):
            blocks.append({"id":"monthly_chart","type":"chart","layout":"full","chartId":"monthly"})
    if sum(b["type"]=="chart" for b in blocks) != 1:
        raise ValueError("Missing monthly chart section")
    artifact = {"surface":"report", "manifest":{"version":1,"surface":"report","title":TITLE,
                "generatedAt":stamp,"filters":[],"cards":[],"charts":[chart],"tables":[],
                "blocks":blocks,"sources":sources},
                "snapshot":{"version":1,"generatedAt":stamp,"status":"ready","datasets":{"monthly":data}},"sources":sources}
    (E/"artifact.json").write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    receipt = {"generated_at":stamp,"source_commit":commit,"report_sha256":sha(ROOT/REPORT),
               "support_summary_sha256":sha(E/"results/summary.json"),"sources":sources,
               "all_sections_preserved":True,"sections":len(sections),"blocks":len(blocks),
               "chart_map":[{"section":"月份支持","family":"comparison","type":"bar","rows":24,
                              "palette":"single blue root","non_color":"month order, zero baseline and signed values",
                              "question":"两种参考的入口支持是否覆盖全部月份","dataset":"monthly"}],
               "table_rationale":"Exact threshold/denominator audit; no redundant charts for few threshold lookups",
               "actual_sql_queries":[QUERY],"new_prices_or_outcomes_read":False}
    (E/"artifact_build_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"sections":len(sections),"blocks":len(blocks),"charts":1,"months":24}))


if __name__ == "__main__":
    main()
