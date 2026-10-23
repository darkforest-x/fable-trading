"""Package reviewed Sep16 observations for the existing Spike report style.

No trades are simulated. The report exposes every coin-direction event, every
observed touching 5m candle, and same-day matched descriptive price controls.
"""
from __future__ import annotations
import base64
import json
import subprocess
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
from yoyo.evaluation.monday_sep16_review import EXP,ROOT,DAY,DAY_END,MON,END,STEP,save,clean,bj
APP=ROOT/'reports/monday-sep16-20261024'


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:raise RuntimeError('Commit packager first')
    r=json.loads((EXP/'reviewed.json').read_text());s=r['summary'];events=pd.DataFrame(r['events']);bars=pd.DataFrame(r['bars']);rules=pd.DataFrame(r['rules'])
    extra={}
    extra['shape_summary']=[dict(shape=name,first_events=int(events['shape'].eq(name).sum()),all_touch_bars=int(bars['shape'].eq(name).sum())) for name in bars['shape'].unique()]
    extra['hourly']=[dict(hour=f'{hour:02}:00',first_events=int(events.first_touch_bj.str[11:13].eq(f'{hour:02}').sum()),all_touch_bars=int(bars.open_time_bj.str[11:13].eq(f'{hour:02}').sum())) for hour in range(24)]
    extra['rule_summary']=rules.groupby('rule').agg(events=('event_id','size'),qualified=('qualified','sum')).reset_index().to_dict('records')
    extra['path_summary']=events.groupby(['direction','path_label']).agg(events=('event_id','size')).reset_index().to_dict('records')
    extra['contexts']=[]
    for symbol in sorted(events.symbol.unique()):
        f=pd.read_csv(EXP/'contexts'/f'{symbol}.csv.gz',parse_dates=['time_utc']).set_index('time_utc')
        z=f.loc[DAY-pd.Timedelta(hours=1):DAY_END+pd.Timedelta(hours=3)]
        h=f.loc[MON:END-STEP].resample('1h').agg(dict(open='first',high='max',low='min',close='last',volume='sum')).dropna()
        def arrays(frame):return [[bj(t),*[float(b[c]) for c in ['open','high','low','close','volume']]] for t,b in frame.iterrows()]
        extra['contexts'].append(dict(symbol=symbol,candles_json=json.dumps(arrays(z),separators=(',',':')),hourly_json=json.dumps(arrays(h),separators=(',',':'))))
    # Exact observed data and invariants; no inference of trade profits.
    assert s['upper_symbols']+s['lower_symbols']-s['both_symbols']==s['touched_symbols']
    assert len(bars)==int(events.touch_bars.sum())
    assert events.event_id.is_unique and not bars.duplicated(['event_id','open_time_bj']).any()
    assert (bars.low<=bars.level).all() and (bars.high>=bars.level).all()
    assert all(0<=x<=s['touch_events'] for x in [s['end_inside'],s['near_boundary_reached']])
    assert s['overlap_conflict_bars']==0
    extra['summary']=[s]
    tables={**{k:v for k,v in r.items() if k!='summary'},**extra}
    methods='Run /Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.monday_sep16_review, then -m yoyo.evaluation.monday_sep16_charts and -m yoyo.evaluation.monday_sep16_report_data. Read-only registered OHLCV. Frozen NY Monday high/low, extensions +/-0.618 Monday range. See committed builders and config for exact transforms.'
    queries={}
    for key,rows in tables.items():
        sourcefile=EXP/f'{key}.csv' if (EXP/f'{key}.csv').exists() else EXP/'reviewed.json'
        queries[key]=dict(rows=rows,source=dict(label='9月16日周一1.618复盘 · '+key,files=[str(sourcefile),str(EXP/'config.json')],description='Binance USDT永续登记行情；北京时间2026-09-16，后续至2026-09-21 12:00。价格复盘，不是交易盈亏。',evidenceFlow=[dict(title='登记历史行情',detail='data-research-spike_v128_recent_20260923-8849e2f9；638文件；每币完整性、活动性、OHLCV与旧数据重叠检查见coverage。'),dict(title='可复现变换',detail=methods)],metricDefinitions=[dict(label='触价与后续变化',definition='当日5m最低价≤1.618≤最高价；每币每方向选当日首根作为路径起点。后续变化从该根收盘计；下沿上涨为正，上沿下跌为正。固定时段从收盘后开始，不含本根极值。随机基准同币同日同方向同前14根波动桶，3个收盘时点均值。')]),methods=[dict(language='text',code=methods)])
        if key=='contexts':queries[key]['payloadColumns']=['candles_json','hourly_json']
    images={}
    for e in r['events']:
        for view in ['detail','full']:
            name=f"{e['event_id']}-{view}";p=EXP/'images'/f'{name}.png'
            if p.exists():images[name]='data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()
    snapshot=dict(surface='report',title='9月16日：1.618触价之后，哪些收回、哪些继续突破',generatedAt=datetime.now(timezone.utc).isoformat(),status='reviewed',buildStatus='creating',filters=[],queries=queries,report={'asOf':'2026-09-21'})
    if (APP/'src/data.json').exists():snapshot['id']=json.loads((APP/'src/data.json').read_text())['id']
    save(EXP/'report-snapshot.json',snapshot)
    if APP.exists():
        save(APP/'src/data.json',snapshot);save(APP/'src/content/report/chart-images.json',images)
    save(EXP/'report-audit.json',dict(count_identities='passed',touch_bar_bounds='passed',unique_keys='passed',overlap_consistency='passed',images=len(images),expected_images=s['touch_events']*2))
    print(json.dumps({'queries':{k:len(v['rows']) for k,v in queries.items()},'images':len(images)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
