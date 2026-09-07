"""Package V27/V28 reviewed report Markdown with native source-backed charts.

One portable HTML delivery mode. Source-first builder reads only saved evidence
and executes actual SQLite projections. No raw quotes, refitting or exit search.
V27 monthly support:24 cells with denominator and missingness. V28 monthly4h
cost threshold and50bp histogram preserve every known case, never clip tails.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import re
import sqlite3
import subprocess

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
EXPERIMENTS={27:"exp-btcusdtp-1h-vwma-background-support-preholdout-20260907-v27",
             28:"exp-btcusdtp-1h-vwma-fixed-clock-preholdout-20260907-v28"}
REPORTS={27:"analysis/p1_btcusdtp_hourly_vwma_background_v27_20260907.md",
         28:"analysis/p1_btcusdtp_hourly_vwma_fixed_clock_v28_20260907.md"}
TITLES={27:"VWMA Background Support",28:"VWMA Entry Persistence"}
SUPPORT_SQL="""SELECT substr(m.decision_time,1,7) AS month,COUNT(*) AS mothers,
SUM(CASE WHEN a.match_status='matched' THEN 1 ELSE 0 END) AS matched,
SUM(a.assigned_controls) AS controls
FROM mothers m JOIN assignments a USING(event_id) GROUP BY month ORDER BY month"""
MONTH_SQL="""WITH c AS (SELECT mother_month AS month,COUNT(*) AS mothers,
COUNT(cost_threshold_markout) AS known,AVG(gross_markout)*10000 AS gross_bp,
AVG(cost_threshold_markout)*10000 AS cost_threshold_bp
FROM cases WHERE horizon_hours=4 GROUP BY mother_month),
p AS (SELECT mother_month AS month,COUNT(cost_threshold_excess_markout) AS paired,
AVG(control_mean_cost_threshold_markout)*10000 AS background_bp,
AVG(cost_threshold_excess_markout)*10000 AS excess_bp
FROM pairs WHERE horizon_hours=4 GROUP BY mother_month)
SELECT * FROM c JOIN p USING(month) ORDER BY month"""
HIST_SQL="""WITH RECURSIVE binned AS (
SELECT FLOOR(cost_threshold_markout*10000/50)*50 AS lower_bp FROM cases
WHERE horizon_hours=4 AND status='known'),
bins(lower_bp) AS (SELECT MIN(lower_bp) FROM binned UNION ALL
SELECT lower_bp+50 FROM bins WHERE lower_bp<(SELECT MAX(lower_bp) FROM binned)),
counts AS (SELECT lower_bp,COUNT(*) AS events FROM binned GROUP BY lower_bp)
SELECT CAST(bins.lower_bp AS INTEGER)||' to <'||CAST(bins.lower_bp+50 AS INTEGER) AS bin,
bins.lower_bp,bins.lower_bp+50 AS upper_bp,COALESCE(counts.events,0) AS events
FROM bins LEFT JOIN counts USING(lower_bp) ORDER BY bins.lower_bp"""


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(version):
    rel=Path("experiments/active")/EXPERIMENTS[version];e=ROOT/rel
    own=Path(__file__).resolve();commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    if subprocess.check_output(["git","show",commit+":"+str(own.relative_to(ROOT))],cwd=ROOT)!=own.read_bytes():raise ValueError("Commit report builder first")
    summary=json.loads((e/"results/summary.json").read_text());audit=json.loads((e/("audit.json" if version==27 else "audit_complete.json")).read_text())
    if audit["status"]!="passed" or audit["summary_sha256"]!=sha(e/"results/summary.json"):raise ValueError("Independent audit missing or changed")
    for name,expected in summary["output_hashes"].items():
        if Path(name).name!=name or sha(e/"results"/name)!=expected:raise ValueError("Report evidence drift")
    if version==28:
        if sha(e/"results/statistics.json")!=summary["statistics_sha256"] or summary["executable_pnl"]:raise ValueError("Inference/scope drift")
    markdown=(ROOT/REPORTS[version]).read_text()
    if not markdown.startswith("# "+TITLES[version]+"\n"):raise ValueError("Title mismatch")
    sections=[s.strip() for s in re.split(r"(?m)(?=^## )",markdown.strip())]
    if len(sections)<8:raise ValueError("Insufficient technical report")
    queries={"monthly":SUPPORT_SQL} if version==27 else {"monthly":MONTH_SQL,"distribution":HIST_SQL}
    files={"mothers":"original_mothers","assignments":"assignments"} if version==27 else {"cases":"case_labels","pairs":"paired_labels"}
    with sqlite3.connect(":memory:") as con:
        con.create_function("FLOOR",1,math.floor)
        for table,file in files.items():pd.read_csv(e/"results"/(file+".csv.gz")).to_sql(table,con,index=False)
        data={name:pd.read_sql_query(query,con).to_dict("records") for name,query in queries.items()}
    if len(data["monthly"])!=24 or sum(r["mothers"] for r in data["monthly"])!=288:raise ValueError("Monthly denominator changed")
    if version==27 and sum(r["matched"] for r in data["monthly"])!=284:raise ValueError("Support chart changed")
    if version==28 and sum(r["events"] for r in data["distribution"])!=sum(r["known"] for r in data["monthly"]):raise ValueError("Histogram drops tails")
    stamp=pd.Timestamp.now(tz="UTC").isoformat()
    sources=[{"id":"report","label":"V%d · 完整定义、结果与风险"%version,"path":REPORTS[version]}]
    for name,query in queries.items():
        sources.append({"id":name,"label":"V%d · 保存证据实际SQLite查询 · %s"%(version,name),"path":str(rel/"results"),
             "query":{"sql":query,"engine":"sqlite","language":"sql","executed_at":stamp,"tables_used":list(files),
                      "description":"按原始母单时间聚合；保留未知分母和全部尾部，不用于调参",
                      "filters":["2023/24 development,288 original VWMA mothers"],
                      "metric_definitions":{file:str(rel/"results"/(file+".csv.gz")) for file in files.values()}}})
    specs=[("monthly","每月匹配支持","2023–2024 · 已配到完整三控的入口数；分母288，未知4",
            "month","UTC月份","matched","完整三控母单数",["mothers","controls"])] if version==27 else [
           ("monthly","每月4h成本门结果","2023–2024 · 单位bp；全部已知入口，OPEN变化减20bp，不是实际PnL",
            "month","UTC月份","cost_threshold_bp","平均成本门 (bp)",["mothers","known","gross_bp","paired","background_bp","excess_bp"]),
           ("distribution","4h成本门分布","每档50bp；保留全部已知入口与两侧尾部，不模拟止损",
            "bin","成本门区间 (bp)","events","入口数",["lower_bp","upper_bp"])]
    charts=[]
    for name,title,desc,x,xlabel,y,ylabel,tooltip in specs:
        charts.append({"id":name,"type":"bar","title":title,"description":desc,"showDescription":True,
             "dataset":name,"sourceId":name,"palette":{"kind":"sequential","name":"blue"},"labels":{"values":"all"},
             "settings":{"sort":"none"},"encodings":{"x":{"field":x,"type":"nominal","label":xlabel},
             "y":{"field":y,"type":"quantitative","label":ylabel},"tooltip":[{"field":f,"type":"quantitative","label":f} for f in tooltip]}})
    blocks=[]
    for i,section in enumerate(sections):
        blocks.append({"id":"section_"+str(i),"type":"markdown","layout":"full","body":section,**({"sourceId":"report"} if i else {})})
        for prefix,name in [("## 月份支持","monthly"),("## 月份表现","monthly"),("## 分布与统计","distribution")]:
            if section.startswith(prefix):blocks.append({"id":name+"_chart","type":"chart","layout":"full","chartId":name})
    if sum(b["type"]=="chart" for b in blocks)!=len(charts):raise ValueError("Missing chart section")
    artifact={"surface":"report","manifest":{"version":1,"surface":"report","title":TITLES[version],"generatedAt":stamp,
              "filters":[],"cards":[],"charts":charts,"tables":[],"blocks":blocks,"sources":sources},
              "snapshot":{"version":1,"generatedAt":stamp,"status":"ready","datasets":data},"sources":sources}
    for name,value in [("artifact.json",artifact),("artifact_build_receipt.json",dict(source_commit=commit,report_sha256=sha(ROOT/REPORTS[version]),
                 summary_sha256=sha(e/"results/summary.json"),actual_queries=queries,sections=len(sections),charts=len(charts),
                 all_sections_preserved=True,raw_prices_read=False,generated_at=stamp))]:
        with (e/name).open("x") as h:json.dump(value,h,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(dict(version=version,sections=len(sections),charts=len(charts),months=24)))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--version",type=int,choices=[27,28],required=True)
    build(parser.parse_args().version)
