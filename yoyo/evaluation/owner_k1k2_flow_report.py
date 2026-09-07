"""V36 reporting and post-result clock diagnostics over frozen research evidence.

SQL and packaging read saved outcomes only; clock audit rereads the same approved
2023-2024 source. No new policy, thresholds, tuning, or execution replay.
Canonical portable report contract uses the installed Data Analytics reader.
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import subprocess

import pandas as pd

from yoyo.evaluation.owner_k1k2_genuine_flow import HERE, REL, ROOT, clean, sha, write_json

REPORT = "analysis/p1_btcusdtp_owner_k1k2_genuine_flow_v36_20260907.md"
TITLE = "K1/K2 Genuine Flow Test · V36"
QUERIES = {
    "folds": """WITH halves(fold) AS (VALUES('2023H1'),('2023H2'),('2024H1'),('2024H2')),
        arms(arm,gated) AS (VALUES('不过滤',0),('真实量同向',1))
        SELECT h.fold,a.arm,COUNT(t.event_id) AS trades,AVG(t.net_return)*10000 AS net_bp,
        AVG(t.gross_return)*10000 AS gross_bp,SUM(t.net_return>0) AS winners
        FROM halves h CROSS JOIN arms a LEFT JOIN main.case_trades t
        ON t.fold=h.fold AND t.closed=1 AND (a.gated=0 OR t.flow_pass=1)
        GROUP BY h.fold,a.arm ORDER BY h.fold,a.arm""",
    "failures": """SELECT flow_pass,failure_class,outcome,COUNT(*) AS trades,
        AVG(net_return)*10000 AS mean_net_bp,AVG(hold_minutes) AS mean_hold_minutes,
        SUM(gave_back_fee_covering_peak) AS gave_back_peak
        FROM main.case_trades GROUP BY flow_pass,failure_class,outcome ORDER BY flow_pass,trades DESC""",
    "geometry": """SELECT direction,CASE WHEN gap_bars<=4 THEN '2-4' ELSE '5-8' END AS gap_group,
        flow_pass,COUNT(*) AS trades,SUM(net_return>0) AS winners,
        AVG(gross_return)*10000 AS mean_gross_bp,AVG(net_return)*10000 AS mean_net_bp,
        AVG(body_ratio) AS k1_body,AVG(k2_touch_depth) AS touch_atr
        FROM main.case_trades WHERE closed=1 GROUP BY direction,gap_group,flow_pass
        ORDER BY direction,gap_group,flow_pass""",
    "pairs": """SELECT fold,COUNT(*) AS matched_cases,SUM(complete_pair) AS complete_pairs,
        AVG(baseline_excess_net)*10000 AS baseline_excess_bp,
        AVG(gated_excess_net)*10000 AS gated_excess_bp,
        AVG(incremental_excess_net)*10000 AS increment_bp,
        AVG(incremental_excess_gross)*10000 AS gross_increment_bp,
        AVG(incremental_excess_saved_cost)*10000 AS saved_cost_increment_bp
        FROM main.paired_contrasts GROUP BY fold ORDER BY fold""",
    "distribution": """SELECT flow_pass,CASE WHEN net_return<-.005 THEN '<-50'
        WHEN net_return<-.002 THEN '-50 to -20' WHEN net_return<0 THEN '-20 to 0'
        WHEN net_return<.002 THEN '0 to 20' WHEN net_return<.005 THEN '20 to 50' ELSE '>=50' END AS net_bp_bucket,
        COUNT(*) AS trades FROM main.case_trades WHERE closed=1 GROUP BY flow_pass,net_bp_bucket""",
}


def guard():
    commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    source = "yoyo/evaluation/owner_k1k2_flow_report.py"
    if subprocess.check_output(["git","show",commit+":"+source],cwd=ROOT)!=(ROOT/source).read_bytes():
        raise ValueError("Commit report builder before report materialization")
    summary = json.loads((HERE/"summary.json").read_text())
    if sha(HERE/"config.json") != summary["config_sha256"]: raise ValueError("Config drift")
    for path,info in summary["files"].items():
        if sha(ROOT/path)!=info["sha256"]: raise ValueError("Saved output drift")
    return commit,summary


def prepare():
    commit,s = guard()
    paths = {p.rsplit("/",1)[1].removesuffix(".csv"):ROOT/p for p in s["files"]}
    case = pd.read_csv(paths["case_trades"],float_precision="round_trip")
    pair = pd.read_csv(paths["paired_contrasts"],float_precision="round_trip")
    with sqlite3.connect(":memory:") as con:
        case.to_sql("case_trades",con,index=False)
        pair.to_sql("paired_contrasts",con,index=False)
        data = {key:pd.read_sql_query(query,con).to_dict("records") for key,query in QUERIES.items()}
    assert sum(r["trades"] for r in data["folds"] if r["arm"]=="不过滤")==s["baseline"]["closed"]
    assert sum(r["trades"] for r in data["failures"])==len(case)
    assert sum(r["matched_cases"] for r in data["pairs"])==s["matched_cases"]
    assert all(abs(r["increment_bp"]-r["gross_increment_bp"]-r["saved_cost_increment_bp"])<1e-9 for r in data["pairs"])
    write_json(HERE/"report_data.json",dict(data=data,queries=QUERIES,source_commit=commit,
        summary_sha256=sha(HERE/"summary.json"),generated_at=pd.Timestamp.now(tz="UTC")))
    print(json.dumps(clean(data),ensure_ascii=False,indent=2))


def package():
    commit,s=guard()
    saved=json.loads((HERE/"report_data.json").read_text())
    if saved["summary_sha256"]!=sha(HERE/"summary.json"): raise ValueError("Summary drift")
    clock_evidence=json.loads((HERE/"clock_audit.json").read_text())
    if clock_evidence["summary_sha256"]!=sha(HERE/"summary.json"): raise ValueError("Clock summary drift")
    if clock_evidence["csv_sha256"]!=sha(HERE/"clock_audit.csv"): raise ValueError("Clock rows drift")
    if subprocess.check_output(["git","show",commit+":"+REPORT],cwd=ROOT)!=(ROOT/REPORT).read_bytes():
        raise ValueError("Commit narrative before packaging")
    sections=[p.strip() for p in re.split(r"(?m)(?=^## )",(ROOT/REPORT).read_text().strip())]
    if not sections[0].startswith("# "+TITLE) or len(sections)<7: raise ValueError("Incomplete report spine")
    sources=[dict(id="report",label="V36 · 结论、定义、风险及复现",path=REPORT),
        dict(id="summary",label="V36 · 原始研究摘要",path=str(REL/"summary.json")),
        dict(id="clock_summary",label="V36 · 入场与退出时钟核对",path=str(REL/"clock_audit.json")),
        dict(id="clock_rows",label="V36 · 63笔颜色时钟逐笔证据",path=str(REL/"clock_audit.csv"))]
    for key,query in QUERIES.items():
        sources.append(dict(id=key,label="V36 · 已保存逐笔结果复算",path=str(REL/"report_data.json"),
            query=dict(sql=query,language="sql",engine="sqlite",executed_at=saved["generated_at"],
                tables_used=["main.case_trades","main.paired_contrasts"],
                description="完整V36原始事件及完整三对照；不是新的策略或阈值筛选",
                filters=["BinanceUSD-M BTCUSDT2023–2024; four halfyears; final72h excluded by clock; research only"],
                metric_definitions={"net_bp":"mean original-notional gross return minus0.002, multiplied by10000; not compounded portfolio return",
                    "trades":"closed independent events; repeated K1/overlapping positions allowed in this table"})))
    chart=dict(id="folds",type="bar",title="四个半年：每笔扣费收益",
        description="单位bp；1bp=0.01% · 同源基线与真实量同向组 · 独立事件，不是账户收益",showDescription=True,
        dataset="folds",sourceId="folds",palette=dict(kind="categorical",name="blueGold"),
        encodings=dict(x=dict(field="fold",type="nominal",label="半年分段"),
            y=dict(field="net_bp",type="quantitative",label="平均净收益bp"),
            color=dict(field="arm",type="nominal",label="规则"),
            tooltip=[dict(field="trades",type="quantitative",label="成交数"),dict(field="gross_bp",type="quantitative",label="平均毛收益bp")]))
    blocks=[]
    for i,section in enumerate(sections):
        blocks.append(dict(id="section_"+str(i),type="markdown",layout="full",body=section))
        if section.startswith("## 四个半年"):
            blocks.append(dict(id="fold_chart",type="chart",layout="full",chartId="folds"))
    if sum(b["type"]=="chart" for b in blocks)!=1: raise ValueError("Missing adjacent chart")
    stamp=pd.Timestamp.now(tz="UTC").isoformat()
    artifact=dict(surface="report",manifest=dict(version=1,surface="report",title=TITLE,generatedAt=stamp,
        sources=sources,blocks=blocks,charts=[chart],cards=[],tables=[],filters=[]),
        snapshot=dict(version=1,generatedAt=stamp,status="ready",datasets=dict(folds=saved["data"]["folds"])),sources=sources)
    write_json(HERE/"artifact.json",artifact)
    write_json(HERE/"artifact_build_receipt.json",dict(source_commit=commit,report_sha256=sha(ROOT/REPORT),
        summary_sha256=sha(HERE/"summary.json"),report_data_sha256=sha(HERE/"report_data.json"),
        clock_audit_sha256=sha(HERE/"clock_audit.json"),clock_rows_sha256=sha(HERE/"clock_audit.csv"),
        sections=len(sections),charts=1,generated_at=stamp))
    print(json.dumps(dict(sections=len(sections),charts=1)))


def clock():
    """Post-result mechanism audit only; entry and subsequent clocks kept distinct."""
    from yoyo.data.hourly_impulse import BAR_COLUMNS, add_features, resample_complete
    from yoyo.evaluation.owner_k1k2_genuine_flow import checked, load_month
    from yoyo.evaluation.owner_k1k2_exit_clock_audit import audit_clock
    commit,s=guard()
    audit_source="yoyo/evaluation/owner_k1k2_exit_clock_audit.py"
    if subprocess.check_output(["git","show",commit+":"+audit_source],cwd=ROOT)!=(ROOT/audit_source).read_bytes():
        raise ValueError("Commit diagnostic builder before price read")
    if (HERE/"clock_audit.json").exists() or (HERE/"clock_audit.csv").exists():
        raise ValueError("Do not overwrite mechanism evidence")
    _,config,manifest=checked()
    data=pd.concat([load_month(ROOT/r["output_path"],r) for r in manifest["monthly"]],ignore_index=True)
    raw=resample_complete(data[BAR_COLUMNS],5)
    featured=add_features(raw,"SMA",40)
    case=pd.read_csv(ROOT/config["output_dir"]/"case_trades.csv",float_precision="round_trip")
    audit=audit_clock(case,featured,raw)
    audit.to_csv(HERE/"clock_audit.csv",index=False,float_format="%.17g")
    flags=["entry_already_opposite","first_postentry_opposite","exited_first5m",
        "color_exit_without_new_flip","first_postentry_new_flip","exact_known_clocks"]
    results=[]
    for arm,rows in [("baseline",audit),("flow_positive",audit.loc[audit.flow_pass])]:
        results.append(dict(arm=arm,events=len(rows),flags={flag:dict(
            true=int(rows[flag].fillna(False).sum()),unknown=int(rows[flag].isna().sum())) for flag in flags}))
    write_json(HERE/"clock_audit.json",dict(source_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),
        summary_sha256=sha(HERE/"summary.json"),audit_source_sha256=sha(ROOT/audit_source),
        csv_sha256=sha(HERE/"clock_audit.csv"),rows=len(audit),results=results,
        semantic="post-result descriptive mechanism labels; not new entry features or a changed exit policy",
        source_start=config["start_inclusive"],source_end=config["end_exclusive"],holdout_evaluated=False))
    print(json.dumps(clean(results),ensure_ascii=False,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=["prepare","package","clock"])
    {"prepare":prepare,"package":package,"clock":clock}[parser.parse_args().phase]()
