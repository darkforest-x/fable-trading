"""V29 canonical report data and packaging; saved support only,never outcomes.

Prepare executes real SQLite queries against verified contexts/counts/trace.
Package reads the complete authored MD and preserves sections. These separate
create-only steps let diagnosis precede prose without inventing chart data.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess

import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
REL=HERE.relative_to(ROOT)
REPORT=Path("analysis/p1_btcusdtp_hourly_classifier_support_v29_20260907.md")
TITLE="Trend Classifier Support"
QUERIES={
 "monthly":"""SELECT key AS month,population,total,accepted,abstain,unknown,known,accepted_rate
 FROM counts WHERE dimension='month' ORDER BY key,population""",
 "folds":"""SELECT key AS fold,population,total,accepted,abstain,unknown,known,accepted_rate
 FROM counts WHERE dimension='fold' ORDER BY key,population""",
 "failures":"""WITH contexts AS (
 SELECT 'case' population,event_id,signal_time,direction,classifier_known,classifier_center,
 classifier_previous_center,classifier_step FROM cases UNION ALL
 SELECT 'control',event_id,signal_time,direction,classifier_known,classifier_center,
 classifier_previous_center,classifier_step FROM controls),
 flags AS (SELECT c.*,h.close,
 ((direction=1 AND classifier_center>classifier_previous_center) OR
  (direction=-1 AND classifier_center<=classifier_previous_center)) slope_pass,
 ((direction=1 AND h.close>classifier_center+classifier_step) OR
  (direction=-1 AND h.close<classifier_center-classifier_step)) distance_pass
 FROM contexts c LEFT JOIN hours h ON substr(replace(c.signal_time,'T',' '),1,19)=substr(h.open_time,1,19)),
 categories AS (SELECT *,CASE WHEN classifier_known=0 THEN 'unknown'
 WHEN slope_pass AND distance_pass THEN 'accepted'
 WHEN slope_pass THEN 'distance_only'
 WHEN distance_pass THEN 'slope_only' ELSE 'slope_and_distance' END reason FROM flags)
 SELECT population,reason,COUNT(*) events FROM categories GROUP BY population,reason ORDER BY population,reason""",
 "unknown":"""SELECT event_id,signal_time,decision_time,direction,fold,classifier_count,classifier_reason
 FROM cases WHERE classifier_known=0 ORDER BY decision_time""",
 "neutral":"""SELECT 'case' population,classifier_direction,COUNT(*) events FROM cases
 WHERE classifier_known=1 GROUP BY classifier_direction UNION ALL
 SELECT 'control',classifier_direction,COUNT(*) FROM controls
 WHERE classifier_known=1 GROUP BY classifier_direction"""
}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path,value):
    with Path(path).open('x') as h:json.dump(value,h,ensure_ascii=False,indent=2,allow_nan=False)


def checked():
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','show',commit+':'+str(Path(__file__).resolve().relative_to(ROOT))],cwd=ROOT)!=Path(__file__).read_bytes():
        raise ValueError('Commit report builder first')
    summary=json.loads((HERE/'results/summary.json').read_text())
    audit=json.loads((HERE/'audit.json').read_text())
    if audit['status']!='passed' or audit['summary_sha256']!=sha(HERE/'results/summary.json'):
        raise ValueError('Independent audit missing or changed')
    for name,value in summary['output_hashes'].items():
        if Path(name).name!=name or sha(HERE/'results'/name)!=value:raise ValueError('Support output drift')
    if summary['outcomes_read_or_computed'] or summary['economic_acceptance']:raise ValueError('Not support-only')
    return commit,summary


def prepare():
    commit,summary=checked()
    with sqlite3.connect(':memory:') as con:
        for table,file in [('counts','counts'),('cases','case_context'),('controls','control_context'),('hours','hourly_trace')]:
            pd.read_csv(HERE/'results'/(file+'.csv.gz')).to_sql(table,con,index=False)
        data={name:pd.read_sql_query(sql,con).to_dict('records') for name,sql in QUERIES.items()}
    if len(data['monthly'])!=48:raise ValueError('Full24months times two populations required')
    for pop in ('case','control'):
        actual=sum(r['events'] for r in data['failures'] if r['population']==pop)
        if actual!=summary['population'][pop]['total']:raise ValueError('Diagnostic join dropped or duplicated requests')
        passing=sum(r['events'] for r in data['failures'] if r['population']==pop and r['reason']=='accepted')
        if passing!=summary['population'][pop]['accepted']:raise ValueError('Diagnostic state differs from original gate')
    write(HERE/'report_data.json',dict(data=data,queries=QUERIES,source_commit=commit,
        summary_sha256=sha(HERE/'results/summary.json'),generated_at=pd.Timestamp.now(tz='UTC').isoformat(),outcomes_read=False))
    print(json.dumps(data,ensure_ascii=False,indent=2))


def package():
    commit,summary=checked();saved=json.loads((HERE/'report_data.json').read_text())
    if saved['summary_sha256']!=sha(HERE/'results/summary.json'):raise ValueError('Report data drift')
    md=(ROOT/REPORT).read_text();sections=[s.strip() for s in re.split(r'(?m)(?=^## )',md.strip())]
    if not md.startswith('# '+TITLE+'\n') or len(sections)<8:raise ValueError('Incomplete technical report')
    stamp=pd.Timestamp.now(tz='UTC').isoformat()
    sources=[dict(id='report',label='V29 · 完整定义、诊断和风险',path=str(REPORT)),
             dict(id='monthly',label='V29 · 冻结支持按月份与原始分母',path=str(REL/'results/counts.csv.gz'),
                  query=dict(sql=QUERIES['monthly'],engine='sqlite',language='sql',tables_used=['counts'],
                  executed_at=saved['generated_at'],description='24个月×原入口和各自随机控制的状态覆盖，不是收益',
                  filters=['2023–2024 UTC development,all251 cases and744 controls'],
                  metric_definitions={'accepted_rate':'accepted/total; unknown remains in denominator'}))]
    data=[r for r in saved['data']['monthly'] if r['population']=='case']
    chart=dict(id='monthly',type='bar',title='每月保留的原始入口',description='2023–2024 · 原251个入口；显示通过状态门的数量，未知仍留在分母',showDescription=True,
         dataset='monthly',sourceId='monthly',palette=dict(kind='sequential',name='blue'),labels=dict(values='all'),settings=dict(sort='none'),
         encodings=dict(x=dict(field='month',type='nominal',label='UTC月份'),y=dict(field='accepted',type='quantitative',label='保留入口数'),
         tooltip=[dict(field=f,type='quantitative',label=f) for f in ['total','abstain','unknown','accepted_rate']]))
    blocks=[]
    for i,section in enumerate(sections):
        blocks.append(dict(id='section_'+str(i),type='markdown',layout='full',body=section,**({'sourceId':'report'} if i else {})))
        if section.startswith('## 月份覆盖'):blocks.append(dict(id='monthly_chart',type='chart',layout='full',chartId='monthly'))
    if sum(b['type']=='chart' for b in blocks)!=1:raise ValueError('Missing chart narrative')
    artifact=dict(surface='report',manifest=dict(version=1,surface='report',title=TITLE,generatedAt=stamp,
        filters=[],cards=[],charts=[chart],tables=[],blocks=blocks,sources=sources),
        snapshot=dict(version=1,generatedAt=stamp,status='ready',datasets=dict(monthly=data)),sources=sources)
    write(HERE/'artifact.json',artifact)
    write(HERE/'artifact_build_receipt.json',dict(source_commit=commit,report_sha256=sha(ROOT/REPORT),
        report_data_sha256=sha(HERE/'report_data.json'),summary_sha256=sha(HERE/'results/summary.json'),
        sections=len(sections),charts=1,all_sections_preserved=True,outcomes_read=False,generated_at=stamp))
    print(json.dumps(dict(sections=len(sections),charts=1,months=len(data))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['prepare','package']);args=p.parse_args()
    prepare() if args.phase=='prepare' else package()
