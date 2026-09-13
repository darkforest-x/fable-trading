"""Receipt-bound four-cell SPIKE A-share study and static frontend publisher.

The owner-approved plan freezes versions, periods, costs and thresholds before
outcomes. Each per-security receipt binds code/config/daily bytes; resumptions
reuse completed evaluations. One deterministic random-entry control matches
each security's actual admission count by calendar quarter and causal ATR
quartile, with the identical serial execution. A symbol-cluster sign-flip test
compares paired strategy/control mean net returns; it is not an ML AUC test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_ashare_data import save
from yoyo.evaluation.spike_ashare_engine import build_signals, replay, sessions

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-spike-ashare-v1-v8-three-year-20260913-v1'
STATIC = ROOT/'yoyo/monitor/static/ashare-backtest'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(config):
    paths=['spike_ashare_engine.py','spike_ashare_study.py','spike_burst_replay.py',
           'spike_v7_fast.py','spike_burst_progressive.py','spike_burst_v6_structure.py',
           'spike_v6_bb_squeeze.py','spike_v6_wvf_study.py']
    return dict(config_sha256=sha(config),sources={name:sha(Path(__file__).with_name(name)) for name in paths})


def random_mask(cycles, version, key):
    """Match frozen admissions within security x quarter x causal ATR bucket."""
    eligible=cycles.in_window & cycles[f'ready_{version}']
    actual=eligible & cycles[f'long_{version}']
    rng=np.random.default_rng(int(hashlib.sha256(key.encode()).hexdigest()[:16],16))
    result=pd.Series(False,index=cycles.index)
    grouping=pd.DataFrame(dict(quarter=pd.to_datetime(cycles.decision_date).dt.to_period('Q').astype(str),
                               bucket=cycles.vol_bucket),index=cycles.index)
    missing=0
    for _, ids in grouping.loc[actual].groupby(['quarter','bucket']):
        first=ids.iloc[0]
        choices=cycles.index[eligible & ~actual & grouping.quarter.eq(first.quarter) & grouping.bucket.eq(first.bucket)].to_numpy()
        count=min(len(ids),len(choices));missing+=len(ids)-count
        if count: result.loc[rng.choice(choices,count,replace=False)]=True
    return result,missing


def metrics(trades):
    if trades.empty:
        return dict(closed_trades=0,open_trades=0,win_rate=None,profit_factor=None,
                    mean_gross_return=None,mean_net_return=None,sum_net_r=0.,median_net_r=None,
                    max_drawdown_r=None)
    closed=trades.loc[~trades.censored.astype(bool)]
    net=closed.net_r.to_numpy(float)
    gains=net[net>0].sum();loss=-net[net<0].sum()
    curve=np.r_[0.,np.cumsum(net)]
    return dict(closed_trades=len(closed),open_trades=int(trades.censored.astype(bool).sum()),
        win_rate=float((closed.net_return>0).mean()) if len(closed) else None,
        profit_factor=float(gains/loss) if loss>0 else None,
        mean_gross_return=float(closed.gross_return.mean()) if len(closed) else None,
        mean_net_return=float(closed.net_return.mean()) if len(closed) else None,
        sum_net_r=float(net.sum()),median_net_r=float(np.median(net)) if len(net) else None,
        max_drawdown_r=float(np.max(np.maximum.accumulate(curve)-curve)) if len(net) else None)


def paired_test(actual,control):
    """Fixed 9,999 symbol-cluster sign flips; one-sided excess-return null."""
    if actual.empty or control.empty:
        return None,0
    a=actual.loc[~actual.censored.astype(bool)].groupby('code').net_return.mean()
    b=control.loc[~control.censored.astype(bool)].groupby('code').net_return.mean()
    pair=pd.concat([a.rename('a'),b.rename('b')],axis=1).dropna()
    if len(pair)<2:
        return None,len(pair)
    delta=(pair.a-pair.b).to_numpy()
    rng=np.random.default_rng(20260913)
    larger=0
    observed=float(delta.mean())
    for _ in range(100):
        size=99 if _==99 else 100
        null=(rng.choice([-1,1],size=(size,len(delta)))*delta).mean(axis=1)
        larger+=int((null>=observed-1e-15).sum())
    return (larger+1)/10000,len(pair)


def process(code, record, data, output, config, run_identity):
    """Evaluate one source once, including native readiness coverage."""
    source=data/'daily'/f'{code}.csv'
    if sha(source)!=record['daily_sha256']:
        raise ValueError('source daily SHA differs from collector receipt')
    receipt_path=output/'streams'/f'{code}.json'
    required=dict(daily_sha256=record['daily_sha256'],identity=run_identity)
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        if any(receipt.get(k)!=v for k,v in required.items()):
            raise ValueError('completed stream identity changed; refusing another evaluation')
        for filename,digest in receipt['files'].items():
            if sha(output/'streams'/filename)!=digest:
                raise ValueError('completed stream output was altered')
        return receipt
    daily=pd.read_csv(source,dtype={'date':str,'code':str})
    calendar=pd.read_csv(data/'calendar.csv',dtype=str)
    start,end=config['start'],config['end']
    expected=[d for d in sessions(calendar) if str(daily.date.min())<=d<=min(str(daily.date.max()),end)]
    missing=sorted(set(expected)-set(daily.date))
    # Explicit suspended status rows may be skipped by indicators. Entirely
    # missing source sessions have unknown prices/status and must fail closed.
    if missing:
        raise ValueError(f'{len(missing)} unexplained missing exchange sessions')
    tables=[];coverage=[]
    for timeframe in config['timeframes']:
        cycles=build_signals(daily,calendar,timeframe,start,end)
        for version in config['versions']:
            if cycles.empty:
                coverage.append(dict(code=code,version=version,timeframe=timeframe,
                    rows=len(daily),cycle_bars=0,ready_bars=0,signals=0,first_ready=None,
                    control_unmatched=0,status='no_traded_bars',skips={}))
                continue
            result=replay(daily,calendar,cycles,version,start,end)
            controls,unmatched=random_mask(cycles,version,code+'|'+timeframe+'|'+version+'|20260913')
            control=replay(daily,calendar,cycles,version,start,end,admission_override=controls)
            for kind,value in [('strategy',result),('random',control)]:
                frame=value['trades'];frame['kind']=kind;tables.append(frame)
            ready=cycles.loc[cycles.in_window & cycles[f'ready_{version}']]
            coverage.append(dict(code=code,version=version,timeframe=timeframe,rows=len(daily),
                cycle_bars=len(cycles),ready_bars=len(ready),signals=result['signals'],
                first_ready=ready.decision_date.min() if len(ready) else None,
                control_unmatched=unmatched,status='evaluated' if len(ready) else 'warmup_insufficient',
                skips=result['skips'],control_skips=control['skips']))
    combined=pd.concat(tables,ignore_index=True) if tables else pd.DataFrame()
    path=output/'streams'/f'{code}.trades.csv.gz'
    path.parent.mkdir(parents=True,exist_ok=True)
    combined.to_csv(path,index=False,compression={'method':'gzip','mtime':0})
    receipt=dict(code=code,**required,coverage=coverage,files={path.name:sha(path)},
                 generated_at=pd.Timestamp.now(tz='UTC').isoformat())
    save(receipt_path,receipt)
    return receipt


def publish(output, data, config, final=False):
    """Expose honest current coverage; every displayed result has a receipt."""
    universe=json.loads((data/'universe.json').read_text())
    progress=json.loads((data/'collection_progress.json').read_text())
    receipts=[json.loads(p.read_text()) for p in sorted((output/'streams').glob('*.json'))]
    errors=json.loads((output/'evaluation_errors.json').read_text()) if (output/'evaluation_errors.json').exists() else {}
    coverage=pd.DataFrame([row for receipt in receipts for row in receipt['coverage']])
    frames=[]
    for receipt in receipts:
        for file,digest in receipt['files'].items():
            path=output/'streams'/file
            if sha(path)!=digest: raise ValueError('display receipt digest mismatch')
            try:
                frame=pd.read_csv(path)
                if len(frame): frames.append(frame)
            except pd.errors.EmptyDataError:
                pass
    trades=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    groups=[];annual=[]
    for timeframe in config['timeframes']:
        for version in config['versions']:
            cv=coverage.loc[coverage.version.eq(version)&coverage.timeframe.eq(timeframe)] if len(coverage) else pd.DataFrame()
            part=trades.loc[trades.version.eq(version)&trades.timeframe.eq(timeframe)] if len(trades) else pd.DataFrame()
            a=part.loc[part.kind.eq('strategy')].sort_values(['exit_date','code']) if len(part) else pd.DataFrame()
            b=part.loc[part.kind.eq('random')] if len(part) else pd.DataFrame()
            m=metrics(a);bm=metrics(b);p,paired=paired_test(a,b)
            if not len(cv):
                m={key:None for key in m}
            m.update(version=version,timeframe=timeframe,signals=int(cv.signals.sum()) if len(cv) else 0,
                     ready_symbols=int(cv.ready_bars.gt(0).sum()) if len(cv) else 0,
                     random_mean_net_return=bm['mean_net_return'],control_p_value=p,
                     paired_symbols=paired,random_closed_trades=bm['closed_trades'],
                     control_unmatched=int(cv.control_unmatched.sum()) if len(cv) else 0,
                     excess_mean_net_return=(m['mean_net_return']-bm['mean_net_return'])
                       if m['mean_net_return'] is not None and bm['mean_net_return'] is not None else None,
                     status=('complete' if final and not progress['errors'] and not errors else 'incomplete' if final else 'partial') if len(cv) else 'pending')
            if not len(cv):
                m['signals']=m['ready_symbols']=m['random_closed_trades']=None
            groups.append(m)
            if len(a):
                for year,t in a.groupby(a.entry_date.astype(str).str[:4]):
                    annual.append(dict(year=int(year),version=version,timeframe=timeframe,**metrics(t)))
    status='complete' if final and not progress['errors'] and not errors else 'incomplete' if final else 'running'
    summary=dict(schema_version=1,status=status,generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        start=config['start'],end=config['end'],source='BaoStock 0.9.3',universe_count=universe['count'],
        covered_symbols=len(receipts),failed_symbols=progress['errors']+len(errors),groups=groups,annual=annual,
        collection=progress,holdout_use_per_configuration=1,
        warnings=['按各版本实际预热就绪覆盖统计；V8 周线需要约 712 根历史周 K 线，不能把未就绪当无信号。',
                  '指标沿真实成交日/周 K 线条数计算，明确跨已记录停牌保留历史状态；不插入虚构价格。未解释的缺失交易日整只隔离。',
                  '日线 OHLC 无法还原盘中排队成交；涨停买入、跌停卖出采用保守阻断。后复权为总回报坐标，非分红送股现金账。',
                  '0.2% 为固定往返研究成本；累计净 R 是独立事件相加，不是共享账户收益或最大资金回撤。',
                  '随机对照固定单一种子，按同股票×同季度×因果 ATR 波动桶匹配入场候选；成交限制和持仓互斥可造成笔数不同。',
                  'p 值为股票聚类配对平均净收益的 9,999 次符号置换；与总池加权平均超额是不同统计量，不是独立前向验证。',
                  '四组配置各首次授权使用本次 A 股 holdout 区间；本轮不据结果调参。']+
                  ([] if final else ['全量仍在运行；当前数字仅覆盖已完成股票，不能当作全主板最终结论。']))
    STATIC.mkdir(parents=True,exist_ok=True)
    if final:
        trades.to_csv(output/'trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        coverage.to_csv(output/'coverage.csv',index=False)
        for name in ['trades.csv.gz','coverage.csv']:
            (STATIC/name).write_bytes((output/name).read_bytes())
        summary.update(trades_url='/static/ashare-backtest/trades.csv.gz',coverage_url='/static/ashare-backtest/coverage.csv')
    save(output/'summary.json',summary);save(STATIC/'summary.json',summary)
    return summary


def run(exp=EXP, follow=False):
    exp=Path(exp);config_path=exp/'config.json';config=json.loads(config_path.read_text())
    data=exp/'data';output=exp/'results';(output/'streams').mkdir(parents=True,exist_ok=True)
    run_id=identity(config_path)
    id_path=output/'evaluation_identity.json'
    if id_path.exists() and json.loads(id_path.read_text())!=run_id:
        raise ValueError('evaluation code changed; review existing consumption before another run')
    save(id_path,run_id)
    errors=json.loads((output/'evaluation_errors.json').read_text()) if (output/'evaluation_errors.json').exists() else {}
    done=set()
    while True:
        if not (data/'collection_records.json').exists():
            if not follow: raise ValueError('source collection has not produced records')
            time.sleep(10);continue
        records=json.loads((data/'collection_records.json').read_text())
        for record in records:
            code=record['code']
            if code in done or code in errors or 'error' in record: continue
            try:
                process(code,record,data,output,config,run_id)
                done.add(code)
            except Exception as exc:
                errors[code]=f'{type(exc).__name__}: {exc}'
                save(output/'evaluation_errors.json',errors)
            if (len(done)+len(errors))%10==0:
                publish(output,data,config)
                print(json.dumps(dict(evaluated=len(done),errors=len(errors))),flush=True)
        state=json.loads((data/'collection_progress.json').read_text())['status']
        if state in ('complete','incomplete'):
            publish(output,data,config,final=True);return
        publish(output,data,config)
        if not follow: return
        time.sleep(20)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,default=EXP)
    parser.add_argument('--follow',action='store_true')
    args=parser.parse_args();run(args.experiment,args.follow)
