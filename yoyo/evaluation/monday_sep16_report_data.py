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
LABELS={'event_id': '事件ID', 'symbol': '币种', 'side': '向内方向：1上涨-1下跌', 'direction': '线位方向', 'first_touch_bj': '当日首次触价开盘_北京时间', 'first_touch_utc': '当日首次触价开盘_UTC', 'weekly_first_bj': '本周首次达到或越过线_北京时间', 'weekly_first_in_day': '本周首次是否在当天', 'weekly_first_known': '本周首次之前行情是否完整', 'first_day_is_week_first': '当日首触是否也是本周首次', 'level': '1.618价格', 'monday_high': '周一高点', 'monday_low': '周一低点', 'touch_bars': '当日触价5m根数', 'shape': '触价K线形态', 'day_change_pct': '当日开收到收盘变化百分比', 'pct_5': '首触收盘后5分钟向内变化百分比', 'pct_15': '首触收盘后15分钟向内变化百分比', 'pct_60': '首触收盘后1小时向内变化百分比', 'pct_240': '首触收盘后4小时向内变化百分比', 'pct_1440': '首触收盘后24小时向内变化百分比', 'pct_4320': '首触收盘后72小时向内变化百分比', 'open_time_bj': 'K线开盘_北京时间', 'open_time_utc': 'K线开盘_UTC', 'open': '开盘价', 'high': '最高价', 'low': '最低价', 'close': '收盘价', 'volume': '成交量_原始基础币单位', 'body_pct_range': '实体占全幅百分比', 'lower_wick_pct_range': '下影占全幅百分比', 'upper_wick_pct_range': '上影占全幅百分比', 'volume_vs_prev20_max': '本柱量除以前20根最大量', 'volume_vs_prev20_mean': '本柱量除以前20根平均量', 'close_distance_pct': '收盘距1.618百分比_向内为正', 'prior_close_inside': '前根收盘是否在线内', 'weekly_first': '本根是否为本周首次', 'forward_complete': '后续至下周一行情完整', 'best_follow_pct': '后续最大向内变化百分比', 'worst_follow_pct': '后续最大向外变化百分比', 'best_time_bj': '后续最大向内变化所在K线_北京时间', 'worst_time_bj': '后续最大向外变化所在K线_北京时间', 'end_pct': '下周一前收盘向内变化百分比', 'end_inside': '期末收盘是否在线内', 'later_near_boundary_reached': '后续是否达到近侧周一边界', 'near_first_bj': '后续首次达到近侧周一边界_北京时间', 'near_already_reached_by_touch_close': '首触柱收盘是否已越过近侧边界', 'later_outside_close': '后续是否出现收盘在线外', 'outside_first_bj': '后续首次在线外收盘_北京时间', 'outside_close_share': '后续在线外收盘比例_0到1', 'forward_hours': '观察后续小时数', 'path_label': '后续路径分类', 'source': '原始数据文件', 'size_bytes': '文件大小_字节', 'mtime_ns': '文件修改时刻_纳秒时间戳', 'monday_bars': '周一5m根数', 'day_bars': '目标日5m根数', 'day_zero_volume': '目标日零成交量5m根数', 'day_flat_bars': '目标日平价5m根数', 'follow_complete': '目标日后行情完整', 'old_overlap_bars': '与旧数据重叠5m根数', 'old_conflict_bars': '重叠冲突5m根数', 'status': '纳入或排除状态', 'upper': '上沿1.618', 'lower': '下沿1.618', '下沿_touch_bars': '下沿触价5m根数', '上沿_touch_bars': '上沿触价5m根数', '下沿_beyond_without_touch': '在下沿线外但未实际碰线', '上沿_beyond_without_touch': '在上沿线外但未实际碰线', 'group': '统计分组', 'horizon_minutes': '首触收盘后观察分钟数', 'n': '完整事件数', 'positive': '向内为正事件数', 'positive_rate': '向内为正比例_0到1', 'mean_pct': '向内变化均值百分比', 'median_pct': '向内变化中位数百分比', 'p25_pct': '向内变化25分位百分比', 'p75_pct': '向内变化75分位百分比', 'paired_n': '三对照完整事件数', 'paired_mean_pct': '严格配对事件均值百分比', 'random_mean_pct': '对应随机均值百分比', 'paired_difference_pct': '事件减随机_百分点'}


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
    for key,name in [('events','触价事件'),('bars','所有触价K线'),('coverage','覆盖与排除台账'),('metrics','时间尺度统计')]:
        frame=pd.DataFrame(r[key]).rename(columns=LABELS)
        frame.to_csv(EXP/f'9月16日-{name}.csv',index=False,encoding='utf-8-sig')
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
        save(APP/'src/data.json',snapshot);save(APP/'src/content/report/chart-images.json',images);save(APP/'src/content/report/field-labels.json',LABELS)
    save(EXP/'report-audit.json',dict(count_identities='passed',touch_bar_bounds='passed',unique_keys='passed',overlap_consistency='passed',images=len(images),expected_images=s['touch_events']*2))
    print(json.dumps({'queries':{k:len(v['rows']) for k,v in queries.items()},'images':len(images)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
