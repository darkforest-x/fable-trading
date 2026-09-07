"""V37 report-only SQL and distribution audit; no market reads or policy changes.

All quantitative rows come from immutable V37 outputs. pandas merge validation
and SQLite grouping use the same documented version contract as the runner.
Concentration/arming are descriptive outcome labels, not selectable factors.
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.owner_k1k2_transition_exit import ROOT, REL, HERE
from yoyo.evaluation.owner_k1k2_genuine_flow import sha, clean, write_json

REPORT="analysis/p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.md"
TITLE="K1/K2 Exit Timing Test · V37"
QUERIES={
    "folds":"""SELECT cohort,arm,fold,COUNT(*) AS trades,AVG(net_return)*10000 AS net_bp,
        AVG(gross_return)*10000 AS gross_bp,SUM(net_return>0) AS winners,
        AVG(hold_minutes) AS hold_minutes FROM main.trades WHERE closed=1
        GROUP BY cohort,arm,fold ORDER BY fold,cohort,arm""",
    "mechanisms":"""SELECT cohort,outcome,CASE WHEN transition_first_armed_at IS NULL THEN 'never_armed'
        ELSE 'armed' END AS arming,COUNT(*) AS trades,AVG(net_return)*10000 AS net_bp,
        SUM(net_return>0) AS winners,AVG(hold_minutes) AS hold_minutes
        FROM main.trades WHERE arm='transition' GROUP BY cohort,outcome,arming ORDER BY cohort,outcome,arming""",
    "pairs":"""SELECT fold,COUNT(*) AS pairs,SUM(complete_pair) AS known_pairs,
        AVG(state_excess_net)*10000 AS state_excess_bp,
        AVG(transition_excess_net)*10000 AS transition_excess_bp,
        AVG(delta_excess_net)*10000 AS delta_excess_bp
        FROM main.pairs GROUP BY fold ORDER BY fold""",
}


def guard():
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    name="yoyo/evaluation/owner_k1k2_transition_report.py"
    if subprocess.check_output(["git","show",commit+":"+name],cwd=ROOT)!=(ROOT/name).read_bytes():
        raise ValueError("Commit reporting builder first")
    s=json.loads((HERE/"summary.json").read_text())
    for path,meta in s["files"].items():
        if not path.startswith("data/owner_k1k2_transition_exit_v37/") or sha(ROOT/path)!=meta["sha256"]:
            raise ValueError("Saved V37 output drift")
    return commit,s


def prepare():
    commit,s=guard()
    files={p.rsplit("/",1)[1].removesuffix(".csv"):ROOT/p for p in s["files"]}
    frames=[]
    for arm in ["state","transition"]:
        for cohort in ["case","control"]:
            f=pd.read_csv(files[arm+"_"+cohort+"_trades"],float_precision="round_trip")
            frames.append(f.assign(arm=arm,cohort=cohort))
    trades=pd.concat(frames,ignore_index=True)
    pairs=pd.read_csv(files["paired_contrasts"],float_precision="round_trip")
    case=pd.read_csv(files["case_changes"],float_precision="round_trip")
    ctrl=pd.read_csv(files["control_changes"],float_precision="round_trip")
    if not case.color_exit_without_new_flip.isin([True,False]).all() or not ctrl.color_exit_without_new_flip.isin([True,False]).all():
        raise ValueError("Unknown diagnostic group must not disappear")
    if len(case)!=63 or len(ctrl)!=108 or len(pairs)!=36: raise ValueError("Original support drift")
    with sqlite3.connect(":memory:") as con:
        trades.to_sql("trades",con,index=False); pairs.to_sql("pairs",con,index=False)
        data={k:pd.read_sql_query(q,con).to_dict("records") for k,q in QUERIES.items()}
    assert sum(r["trades"] for r in data["folds"])==342
    assert sum(r["trades"] for r in data["mechanisms"])==171
    diagnostics=[]
    for arm in ["state","transition"]:
        for cohort in ["case","control"]:
            x=trades.loc[trades.arm.eq(arm)&trades.cohort.eq(cohort),"net_return"]*1e4
            q=x.quantile([.25,.75]); low,high=q.iloc[0]-1.5*(q.iloc[1]-q.iloc[0]),q.iloc[1]+1.5*(q.iloc[1]-q.iloc[0])
            diagnostics.append(dict(arm=arm,cohort=cohort,n=len(x),unknown=int(x.isna().sum()),
                mean=x.mean(),median=x.median(),sd=x.std(),minimum=x.min(),maximum=x.max(),
                skew=x.skew(),q25=q.iloc[0],q75=q.iloc[1],iqr_outliers=int(((x<low)|(x>high)).sum())))
    d=case.delta_net.to_numpy(float)*1e4
    pos=np.maximum(d,0).sum()
    top=case.assign(delta_bp=d).sort_values(["delta_bp","event_id"],ascending=[False,True]).head(3)
    concentration=dict(total_delta_bp=d.sum(),positive_delta_sum_bp=pos,
        largest_delta_bp=float(d.max()),largest_share_positive=float(d.max()/pos) if pos else None,
        without_largest_delta_mean_bp=float((d.sum()-d.max())/(len(d)-1)),
        leave_one_out_mean_min_bp=float(np.min((d.sum()-d)/(len(d)-1))),
        leave_one_out_mean_max_bp=float(np.max((d.sum()-d)/(len(d)-1))),
        top3=top[["event_id","decision_time","delta_bp","state_net","transition_net"]].to_dict("records"))
    scatter=case[["event_id","decision_time","state_net","transition_net","delta_hold_minutes","color_exit_without_new_flip"]].copy()
    scatter["state_bp"]=scatter.state_net*1e4; scatter["transition_bp"]=scatter.transition_net*1e4
    scatter["group"]=np.where(scatter.color_exit_without_new_flip,"原反色延续38笔","其他25笔")
    data["scatter"]=scatter.to_dict("records")
    tests=[]
    for path in ["tests/test_owner_k1k2_transition_exit.py","tests/test_owner_k1k2_exit_transition_contract.py"]:
        committed=subprocess.check_output(["git","show",s["source_commit"]+":"+path],cwd=ROOT)
        if committed!=(ROOT/path).read_bytes(): raise ValueError("Tests no longer match pre-outcome source commit")
        tests.append(dict(path=path,sha256=sha(ROOT/path),present_at_run_commit=True))
    write_json(HERE/"report_data.json",dict(source_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),
        summary_sha256=sha(HERE/"summary.json"),data=data,queries=QUERIES,diagnostics=diagnostics,
        concentration=concentration,pre_outcome_test_proof=tests))
    print(json.dumps(clean(dict(data={k:v for k,v in data.items() if k!="scatter"},diagnostics=diagnostics,concentration=concentration)),indent=2))


def package():
    commit,s=guard(); saved=json.loads((HERE/"report_data.json").read_text())
    if saved["summary_sha256"]!=sha(HERE/"summary.json"): raise ValueError("Summary drift")
    if subprocess.check_output(["git","show",commit+":"+REPORT],cwd=ROOT)!=(ROOT/REPORT).read_bytes():
        raise ValueError("Commit final reviewed narrative first")
    sections=[p.strip() for p in re.split(r"(?m)(?=^## )",(ROOT/REPORT).read_text().strip())]
    sources=[dict(id="summary",label="V37 · 冻结回测摘要",path=str(REL/"summary.json")),
        dict(id="report_data",label="V37 · 逐笔变化、分布与集中度复算",path=str(REL/"report_data.json")),
        dict(id="report",label="V37 · 定义、结论与复现",path=REPORT),
        dict(id="review",label="V37 · 匹配支持与初态独立复核",path=str(REL/"REVIEW.md"))]
    for key,q in QUERIES.items():
        sources.append(dict(id=key,label="V37 · 同名单回测SQL核对",path=str(REL/"report_data.json"),query=dict(
            sql=q,language="sql",engine="sqlite",executed_at=saved["generated_at"],
            tables_used=["main.pairs"] if key=="pairs" else ["main.trades"],
            filters=["same BinanceUSD-M BTCUSDT2023-2024 original63cases108controls; no flow gate; closed results"],
            description="Original frozen independent-event replay, not a new policy selection",
            metric_definitions={"net_bp":"gross original-notional return minus0.002, times10000; not compounded account PnL"})))
    charts=[dict(id="folds",type="bar",title="四个半年每笔净收益",dataset="case_folds",sourceId="folds",
        description="同一63个形态事件 · 单位bp · 1bp=0.01%",showDescription=True,palette=dict(kind="categorical",name="blueGold"),
        encodings=dict(x=dict(field="fold",type="nominal"),y=dict(field="net_bp",type="quantitative"),
            color=dict(field="arm",type="nominal"),tooltip=[dict(field="trades",type="quantitative"),dict(field="gross_bp",type="quantitative") ])),
        dict(id="scatter",type="scatter",title="同一笔交易：状态退出与翻转退出",dataset="scatter",sourceId="report_data",
            description="横轴原净bp，纵轴新净bp；63笔全部保留",showDescription=True,palette=dict(kind="categorical",name="blueGold"),
            encodings=dict(x=dict(field="state_bp",type="quantitative"),y=dict(field="transition_bp",type="quantitative"),
                color=dict(field="group",type="nominal"),tooltip=[dict(field="event_id",type="nominal"),dict(field="decision_time",type="nominal"),dict(field="delta_hold_minutes",type="quantitative")]))]
    blocks=[]
    for i,section in enumerate(sections):
        blocks.append(dict(id="section_"+str(i),type="markdown",layout="full",body=section))
        if section.startswith("## 四个半年"): blocks.append(dict(id="fold_chart",type="chart",layout="full",chartId="folds"))
        if section.startswith("## 改善集中"): blocks.append(dict(id="change_chart",type="chart",layout="full",chartId="scatter"))
    if sum(b["type"]=="chart" for b in blocks)!=2 or sections[0]!="# "+TITLE: raise ValueError("Incomplete report spine")
    stamp=pd.Timestamp.now(tz="UTC").isoformat()
    folddata=[dict(r,arm="状态退出" if r["arm"]=="state" else "新翻色退出") for r in saved["data"]["folds"] if r["cohort"]=="case"]
    artifact=dict(surface="report",manifest=dict(version=1,surface="report",title=TITLE,generatedAt=stamp,
        blocks=blocks,charts=charts,cards=[],tables=[],filters=[],sources=sources),
        snapshot=dict(version=1,status="ready",generatedAt=stamp,datasets=dict(case_folds=folddata,scatter=saved["data"]["scatter"])),sources=sources)
    write_json(HERE/"artifact.json",artifact)
    write_json(HERE/"artifact_build_receipt.json",dict(source_commit=commit,generated_at=stamp,
        summary_sha256=sha(HERE/"summary.json"),report_sha256=sha(ROOT/REPORT),report_data_sha256=sha(HERE/"report_data.json")))
    print(json.dumps(dict(sections=len(sections),charts=2)))


if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("phase",choices=["prepare","package"])
    {"prepare":prepare,"package":package}[p.parse_args().phase]()
