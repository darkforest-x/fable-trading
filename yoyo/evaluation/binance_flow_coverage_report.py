"""Canonical V34 report and companion notebook from verified historical flow.

Reads only V34's hash-locked 2023-2024 outputs. SQLite queries recompute calendar
and same-bar sign diagnostics; no returns, targets, thresholds or selection.
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.DataFrame.to_sql.html
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import sqlite3
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REL = Path('experiments/active/exp-btcusdtp-genuine-flow-coverage-20260907-v34')
HERE = ROOT/REL
REPORT = Path('analysis/p1_btcusdtp_genuine_flow_coverage_v34_20260907.md')
TITLE = 'Genuine Flow Coverage · V34'
QUERIES = {
    'monthly': '''SELECT b.month, c.expected_bars, COUNT(*) AS valid_bars,
        c.expected_bars-COUNT(*) AS missing_bars, SUM(b.volume=0) AS zero_bars,
        SUM(b.price_direction!=0 AND b.flow_direction!=0) AS nonzero_bars,
        SUM(b.price_direction*b.flow_direction<0) AS opposite_bars,
        1.0*SUM(b.price_direction*b.flow_direction<0)/
          NULLIF(SUM(b.price_direction!=0 AND b.flow_direction!=0),0) AS opposite_rate
        FROM bars b JOIN calendar c USING(month) GROUP BY b.month ORDER BY b.month''',
    'zero_bars': '''SELECT open_time, month, volume, quote_volume, trade_count,
        delta_quote_volume FROM bars WHERE volume=0 ORDER BY open_time''',
    'overview': '''SELECT COUNT(*) AS valid_bars, COUNT(DISTINCT open_time) AS unique_bars,
        COUNT(DISTINCT month) AS months, SUM(volume=0) AS zero_bars,
        SUM(price_direction!=0 AND flow_direction!=0) AS nonzero_bars,
        SUM(price_direction*flow_direction<0) AS opposite_bars,
        1.0*SUM(price_direction*flow_direction<0)/
          NULLIF(SUM(price_direction!=0 AND flow_direction!=0),0) AS opposite_rate
        FROM bars''',
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, obj: object) -> None:
    with path.open('x') as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def checked() -> tuple[str, dict, dict]:
    commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    rel = str(Path(__file__).relative_to(ROOT))
    if subprocess.check_output(['git','show',commit+':'+rel], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError('Commit report builder first')
    summary = json.loads((HERE/'summary.json').read_text())
    verification = json.loads((HERE/'verification.json').read_text())
    if verification['status'] != 'passed' or verification['summary_sha256'] != sha(HERE/'summary.json'):
        raise ValueError('Verification missing/drifted')
    if summary['source_manifest_sha256'] != sha(HERE/'source_manifest.json'):
        raise ValueError('Manifest drift')
    if summary['status'] != 'complete':
        raise ValueError('This report requires verified complete inputs; record missing-source blocker separately')
    for row in summary['monthly']:
        if sha(ROOT/row['output_path']) != row['output_sha256']:
            raise ValueError('Derived flow data drift')
    return commit, summary, verification


def prepare() -> None:
    commit, summary, verification = checked()
    frames = []
    for month in summary['monthly']:
        f = pd.read_csv(ROOT/month['output_path'], float_precision='round_trip',
            usecols=['open_time','open','close','volume','quote_volume','trade_count','delta_quote_volume'])
        f['month'] = month['month']
        f['price_direction'] = (f['close']>f['open']).astype(int)-(f['close']<f['open']).astype(int)
        f['flow_direction'] = (f['delta_quote_volume']>0).astype(int)-(f['delta_quote_volume']<0).astype(int)
        frames.append(f.drop(columns=['open','close']))
    all_bars = pd.concat(frames, ignore_index=True)
    if all_bars['open_time'].duplicated().any():
        raise ValueError('Duplicated raw bar identities')
    with sqlite3.connect(':memory:') as con:
        all_bars.to_sql('bars', con, index=False)
        pd.DataFrame([dict(month=r['month'], expected_bars=r['expected_bars'])
                      for r in summary['monthly']]).to_sql('calendar', con, index=False)
        data = {name:pd.read_sql_query(sql, con).to_dict('records') for name,sql in QUERIES.items()}
    overview = data['overview'][0]
    for key, saved in [('valid_bars','valid_bars'),('opposite_bars','candle_flow_opposite_bars'),
                       ('nonzero_bars','candle_flow_nonzero_bars'),('zero_bars','zero_volume_bars')]:
        if overview[key] != summary[saved]:
            raise ValueError('Independent SQL recomputation differs: '+key)
    for computed, saved in zip(data['monthly'],summary['monthly']):
        if computed['month'] != saved['month'] or computed['opposite_bars'] != saved['candle_flow_opposite_bars']:
            raise ValueError('Monthly SQL recomputation differs')
    if checked()[1:] != (summary, verification):
        raise ValueError('Inputs changed during report preparation')
    result = dict(data=data, queries=QUERIES, source_commit=commit,
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(), summary_sha256=sha(HERE/'summary.json'),
        verification_sha256=sha(HERE/'verification.json'), no_economic_labels=True)
    write(HERE/'report_data.json', result)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def package() -> None:
    commit, summary, _ = checked()
    saved = json.loads((HERE/'report_data.json').read_text())
    if saved['summary_sha256'] != sha(HERE/'summary.json'):
        raise ValueError('Report data lineage drift')
    if subprocess.check_output(['git','show',commit+':'+str(REPORT)], cwd=ROOT) != (ROOT/REPORT).read_bytes():
        raise ValueError('Commit narrative first')
    md = (ROOT/REPORT).read_text()
    sections = [s.strip() for s in re.split(r'(?m)(?=^## )',md.strip())]
    if not md.startswith('# '+TITLE+'\n') or len(sections)<7:
        raise ValueError('Incomplete report structure')
    stamp = pd.Timestamp.now(tz='UTC').isoformat()
    sources = [dict(id='report',label='V34 · 覆盖审计、定义与限制',path=str(REPORT))]
    for name in QUERIES:
        sources.append(dict(id=name,label='V34 · 真实流量档案的描述性复算',path=str(REL/'report_data.json'),
            query=dict(sql=QUERIES[name],engine='sqlite',language='sql',tables_used=['main.bars','main.calendar']
                if name=='monthly' else ['main.bars'], executed_at=saved['generated_at'],
                description='读取24份固定SHA的5m输出，逐根重算方向和完整性；没有未来价格或交易标签',
                filters=['BTCUSDT Binance USD-M; bar opens in [2023-01-01,2025-01-01) UTC; all bars, no sample'],
                metric_definitions={'opposite_rate':'count((close-open)*delta_quote<0)/count(close!=open AND delta_quote!=0); fraction',
                    'missing_bars':'calendar expected grid count minus unique validated monthly bars',
                    'zero_bars':'count(volume=0); preserved as known zero, not missing or a tradeable bar'})))
    chart = dict(id='opposite',type='bar',title='逐月价格方向与主动成交方向相反占比',
        description='2023–2024 UTC · 仅双方方向均非零的5分钟K线；不是胜率或预测效果',
        showDescription=True,dataset='monthly',sourceId='monthly',
        palette=dict(kind='categorical',name='blueGold'),
        encodings=dict(x=dict(field='month',type='nominal',label='UTC月份'),
            y=dict(field='opposite_rate',type='quantitative',label='相反占比（0–1）'),
            tooltip=[dict(field='nonzero_bars',type='quantitative',label='非零方向根数'),
                     dict(field='opposite_bars',type='quantitative',label='相反根数'),
                     dict(field='valid_bars',type='quantitative',label='全部有效根数')]))
    blocks=[]
    for i,section in enumerate(sections):
        blocks.append(dict(id='section_'+str(i),type='markdown',layout='full',body=section,
                           **({'sourceId':'report'} if i else {})))
        if section.startswith('## 真买卖量'):
            blocks.append(dict(id='opposite_chart',type='chart',layout='full',chartId='opposite'))
    if sum(b['type']=='chart' for b in blocks)!=1:
        raise ValueError('Missing chart reading path')
    artifact=dict(surface='report',manifest=dict(version=1,surface='report',title=TITLE,generatedAt=stamp,
        sources=sources,blocks=blocks,charts=[chart],cards=[],tables=[],filters=[]),
        snapshot=dict(version=1,generatedAt=stamp,status='ready',datasets=dict(monthly=saved['data']['monthly'])),sources=sources)
    write(HERE/'artifact.json',artifact)
    write(HERE/'artifact_build_receipt.json',dict(source_commit=commit,report_sha256=sha(ROOT/REPORT),
        report_data_sha256=sha(HERE/'report_data.json'),sections=len(sections),charts=1,generated_at=stamp))
    notebook(saved)
    print(json.dumps(dict(sections=len(sections),charts=1,rows=len(saved['data']['monthly']))))


def notebook(saved: dict) -> None:
    """stdlib scaffold fallback: no nbformat/nbclient/kernel in contract venv.

    Execute each Python cell sequentially in one fresh namespace and record
    stdout. This proves code execution, not Jupyter UI/kernel compatibility.
    """
    def md(text):
        return dict(cell_type='markdown',metadata={},source=text.splitlines(keepends=True))
    def code(text):
        return dict(cell_type='code',metadata={},source=text.splitlines(keepends=True),execution_count=None,outputs=[])
    cells=[md('## tl;dr\n24个月、210,528根完整；38根零量。209,923根双方非零方向中40,489根相反。没有收益检验。'),
        md('## Context & Methods\n本笔记只复核V34保存的SQL结果和SHA，不读取新价格。\n### Key Assumptions\nUTC开盘左闭右开；理论完整边界不等于网络到达。此为stdlib顺序回放，不是Jupyter kernel验收。'),
        code('from pathlib import Path\nimport json, hashlib\n'
             "root = Path.cwd()\nwhile not (root/'yoyo').exists() and root != root.parent:\n    root = root.parent\n"
             f"here = root/{str(REL)!r}\n"
             "saved = json.loads((here/'report_data.json').read_text())\n"
             f"assert hashlib.sha256((here/'report_data.json').read_bytes()).hexdigest() == {sha(HERE/'report_data.json')!r}\n"
             "print('saved SQL result hash: verified')\n"),
        md('## Data\n24个月完整月表；下方仅展示前10个月。源SQL保存在report_data.json的queries。'),
        code("months = saved['data']['monthly']\nassert len(months)==24\nprint(json.dumps(months[:10], ensure_ascii=False))\n"),
        md('## Results\n核对完整时间分母、方向反例分母和零量条目。'),
        code("overview = saved['data']['overview'][0]\n"
             "assert sum(r['valid_bars'] for r in months) == overview['valid_bars'] == 210528\n"
             "assert sum(r['missing_bars'] for r in months)==0\n"
             "assert sum(r['opposite_bars'] for r in months)==overview['opposite_bars']==40489\n"
             "assert overview['nonzero_bars']==209923\n"
             "assert len(saved['data']['zero_bars'])==38\n"
             "print(json.dumps(overview, ensure_ascii=False))\n"),
        md('## Takeaways\n输入完整且不同于颜色代理，不代表交易有正期望。下一实验需单独冻结因果时钟与验证方案。')]
    namespace={}
    for i,cell in enumerate(c for c in cells if c['cell_type']=='code',1):
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):
            exec(compile(''.join(cell['source']),f'V34 notebook cell {i}','exec'),namespace)
        cell['execution_count']=i
        cell['outputs']=[dict(output_type='stream',name='stdout',text=capture.getvalue().splitlines(keepends=True))]
    nb=dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3'),
        language_info=dict(name='python',version='3.9.6'),validation='stdlib sequential execution; no Jupyter kernel'),nbformat=4,nbformat_minor=4)
    assert all(c['cell_type'] in ('code','markdown') and isinstance(c['source'],list) for c in cells)
    write(HERE/'verification.ipynb',nb)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','package'])
    args=parser.parse_args()
    prepare() if args.phase=='prepare' else package()
