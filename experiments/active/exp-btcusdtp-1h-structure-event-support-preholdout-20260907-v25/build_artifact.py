"""Package reviewed V25 support evidence, with no price/outcome calculations.

Native monthly bar chart: all24 UTC months, accepted own case events, zero
origin, single blue root; retain original/known/abstain/unknown denominators.
Tables in the technical narrative serve exact support/threshold lookup.
No SQL was executed; chart dataset is a direct projection of frozen counts.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
E = Path(__file__).resolve().parent
REL = E.relative_to(ROOT).as_posix()
REPORT = "analysis/p1_btcusdtp_hourly_structure_event_support_v25_20260907.md"
TITLE = "Structure Event Support"


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
    monthly = counts.loc[counts.population.eq("case") & counts.dimension.eq("month")].sort_values("key")
    if len(monthly) != 24 or monthly.total.sum() != 251 or monthly.accepted.sum() != summary["population"]["case"]["accepted"]:
        raise ValueError("Monthly display must retain all251 cases and24 months")
    data = monthly.to_dict("records")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sources = [
        {"id":"report", "label":"V25 · 定义、支持分母、验证与限制", "path":REPORT},
        {"id":"monthly", "label":"V25 · frozen counts直接筛case/month并按UTC月份排序；无SQL", "path":REL+"/results/counts.csv.gz"},
        {"id":"summary", "label":"V25 · 只审支持、不读取收益", "path":REL+"/results/summary.json"},
    ]
    chart = {"id":"monthly", "type":"bar", "title":"每月K1结构事件数（2023–2024）",
             "description":"全部251原始入口，24个UTC月份含零月；单位：accepted case笔数，不是获利交易数",
             "showDescription":True, "dataset":"monthly", "sourceId":"monthly",
             "palette":{"kind":"sequential","name":"blue"}, "labels":{"values":"all"},
             "settings":{"sort":"none"},
             "encodings":{"x":{"field":"key","type":"nominal","label":"UTC月份"},
                          "y":{"field":"accepted","type":"quantitative","label":"当前方向事件数（笔）"},
                          "tooltip":[{"field":f,"type":"quantitative","label":label} for f,label in
                              [("total","原入口数"),("known","可观察数"),("abstain","已知非事件数"),("unknown","未知数")]]}}
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
                              "palette":"single blue root","non_color":"month labels and exact values",
                              "question":"是否有足够月份观察当前K1方向事件","dataset":"monthly"}],
               "table_rationale":"Exact threshold/denominator audit; no redundant charts for few threshold lookups",
               "actual_sql_queries":[],"new_prices_or_outcomes_read":False}
    (E/"artifact_build_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"sections":len(sections),"blocks":len(blocks),"charts":1,"months":24}))


if __name__ == "__main__":
    main()
