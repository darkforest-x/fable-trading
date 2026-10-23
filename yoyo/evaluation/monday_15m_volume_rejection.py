"""Owner case2, 2026-10-23: first15m 1618 wick +21bar peak volume +3body holds.

Freeze completed America/New_York Monday H/L. For each week and side only the
FIRST observed touch counts, regardless of whether that candle qualifies.
Pin body is inside (open may equal level, close strictly inside); nonzero wick
touches level. No candle color or wick/body ratio is imposed. Volume is the
original Binance base-asset quantity, >=each of the20 prior complete15m bars,
and positive. The next3 contiguous complete15m bodies must be strictly inside;
their wicks may retouch or exceed the pin. Entry is after all3 confirmations,
at the following observed open. SL=pin extreme; TP=near Monday edge. No timeout.
Reject unknown first touches after a source gap in the week. Features use pin
and prior20 volume bars, three CLOSED confirmation OHLCs, and prior14 completed
5m TR for the unchanged random-control volatility bucket. No future decisions.

Reuse case1 execute() and existing Nautilus1.231.0 native5m execution. The new
code only prepares candidates and summaries. No framework or cost changes.
HistData spot XAUUSD has zero placeholder volume and is explicitly unavailable.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, runtime_versions

EXP = ROOT/'experiments/active/exp-monday-15m-volume-rejection-20261023-v1'
CODE = [*old.CODE, 'yoyo/evaluation/monday_15m_volume_rejection.py',
        'tests/evaluation/test_monday_15m_volume_rejection.py']
STEP = pd.Timedelta(minutes=15)


def load(paths):
    parts = []
    for path in paths:
        f = pd.read_csv(path, usecols=['ts', *old.OHLC, 'volume'], float_precision='round_trip')
        f.index = pd.DatetimeIndex(pd.to_datetime(f.pop('ts'), unit='ms', utc=True)).as_unit('ns')
        parts.append(f)
    f = pd.concat(parts).sort_index()
    dup = f[f.index.duplicated(keep=False)]
    if len(dup) and dup.groupby(level=0).nunique().gt(1).any().any():
        raise ValueError('Conflicting source OHLCV')
    f = f[~f.index.duplicated()].copy(); f['count'] = 5
    if not np.isfinite(f).all().all() or (f[old.OHLC] <= 0).any().any() or (f.volume < 0).any():
        raise ValueError('Invalid OHLCV')
    if (f.high < f[old.OHLC].max(axis=1)).any() or (f.low > f[old.OHLC].min(axis=1)).any():
        raise ValueError('Invalid price range')
    return f


def aggregate(f):
    group = f.resample('15min', closed='left', label='left')
    bars = group.agg(dict(open='first', high='max', low='min', close='last', volume='sum')).dropna()
    bars['count'] = group.close.count().reindex(bars.index)
    bars.index = bars.index.as_unit('ns')
    return bars


def week_keys(index):
    local = index.tz_convert(old.TZ)
    monday = local.tz_localize(None).normalize()-pd.to_timedelta(local.dayofweek, unit='D')
    return local, monday.strftime('%Y-%m-%d')


def pattern_mask(f, level, side):
    """Mask indexed by pin OPEN; known only after pin+3 confirmation closes."""
    body_min = f[['open', 'close']].min(axis=1)
    body_max = f[['open', 'close']].max(axis=1)
    if side == 1:
        pin = (f.low <= level) & (f.open >= level) & (f.close > level) & (f.low < body_min)
        inside = body_min
    else:
        pin = (f.high >= level) & (f.open <= level) & (f.close < level) & (f.high > body_max)
        inside = body_max
    complete = f['count'].eq(3)
    prior_max = f.volume.shift().rolling(20, min_periods=20).max()
    history = complete.shift().rolling(20).sum().eq(20)
    history &= (f.index.to_series()-f.index.to_series().shift(20)).eq(STEP*20)
    peak = f.volume.gt(0) & f.volume.ge(prior_max) & history
    confirms = complete.copy()
    for k in (1, 2, 3):
        confirms &= complete.shift(-k, fill_value=False)
        confirms &= (f.index.to_series().shift(-k)-f.index.to_series()).eq(STEP*k)
        confirms &= inside.shift(-k).gt(level) if side == 1 else inside.shift(-k).lt(level)
    return pin & complete, peak, confirms, prior_max


def candidates(f, symbol, cfg):
    h = aggregate(f); local, week = week_keys(h.index)
    anchors = h[local.dayofweek == 0].assign(week=week[local.dayofweek == 0]).groupby('week').agg(
        high=('high','max'), low=('low','min'), bars=('close','size'), source_count=('count','sum'))
    anchors = anchors[anchors.bars.eq(96) & anchors.source_count.eq(288)]
    hi = pd.Series(week,index=h.index).map(anchors.high)
    lo = pd.Series(week,index=h.index).map(anchors.low)
    # A missing candle could conceal an earlier touch; require a known prefix.
    start = pd.DatetimeIndex(pd.to_datetime(week)).tz_localize(old.TZ).tz_convert('UTC').as_unit('ns')
    expected_slot = (h.index.asi8-start.asi8)//STEP.value
    seen_slot = pd.Series(np.arange(len(h)),index=h.index).groupby(week).cumcount()
    known_prefix = seen_slot.eq(expected_slot) & h['count'].eq(3).groupby(week).cummin()
    tr = pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    atr = tr.rolling(14,min_periods=14).mean().shift()/f.open
    bucket = np.floor(np.log2(atr.where(atr>0))*4).fillna(-999).to_numpy(int)
    rows=[]; funnel={}; rejected=0
    for side in (1,-1):
        level = lo-.618*(hi-lo) if side==1 else hi+.618*(hi-lo)
        touched = (h.low.le(level) if side==1 else h.high.ge(level)) & hi.notna() & (hi>lo) & (local.dayofweek>=1)
        first = touched & touched.astype(int).groupby(week).cumsum().eq(1)
        unknown = first & ~known_prefix
        first &= known_prefix
        pin,peak,confirms,prior_max = pattern_mask(h,level,side)
        mask = first & pin & peak & confirms
        funnel[str(side)] = dict(first_touches=int(first.sum()),unknown_prefix=int(unknown.sum()),
            wick=int((first&pin).sum()),volume_peak=int((first&pin&peak).sum()),three_body_holds=int(mask.sum()))
        for j in np.flatnonzero(mask.to_numpy()):
            when=h.index[j]+STEP*4; i=f.index.get_indexer([when])[0]
            if i<0: rejected+=1; continue
            entry=float(f.open.iloc[i]); stop=float(h.low.iloc[j] if side==1 else h.high.iloc[j])
            target=float(lo.iloc[j] if side==1 else hi.iloc[j]); risk=side*(entry-stop)
            if risk<=0 or side*(target-entry)<=0 or bucket[i]==-999:
                rejected+=1; continue
            pair=f'{symbol}-{i}-{side}'
            rows.append(dict(trade_id=pair,pair_id=pair,population='signal',symbol=symbol,
                entry_i=int(i),pin_i=int(f.index.get_indexer([h.index[j]])[0]),side=side,
                entry=entry,stop=stop,target_price=target,risk=risk,level=float(level.iloc[j]),
                week=week[j],bucket=int(bucket[i]),entry_time=str(when),pin_time=str(h.index[j]),
                confirmation_close_time=str(when),pin_volume=float(h.volume.iloc[j]),
                prior20_volume_max=float(prior_max.iloc[j]),
                segment=int(when>=pd.Timestamp(cfg['split'])),monday_high=float(hi.iloc[j]),monday_low=float(lo.iloc[j])))
    signals=pd.DataFrame(rows)
    detail=dict(valid_mondays=len(anchors),funnel=funnel,rejected_geometry=rejected,unmatched=0)
    if signals.empty:return signals,detail
    # Reuse the case1 same5m volatility convention; controls enter on15m opens.
    l5,w5=week_keys(f.index)
    eligible=(l5.dayofweek>=1)&(bucket!=-999)&(f.index.minute%15==0)&np.r_[False,np.diff(f.index.asi8)==old.STEP.value]
    pools={}
    for i in np.flatnonzero(eligible):pools.setdefault((w5[i],bucket[i]),[]).append(i)
    rng=np.random.default_rng(cfg['seed']);controls=[]
    for r in signals.itertuples():
        pool=np.array(pools.get((r.week,r.bucket),[]),dtype=int);pool=pool[pool!=r.entry_i]
        if len(pool)<cfg['controls_per_signal']:detail['unmatched']+=1;continue
        for k,i in enumerate(rng.choice(pool,cfg['controls_per_signal'],replace=False)):
            entry=float(f.open.iloc[i]);scale=entry/r.entry
            controls.append(dict(trade_id=f'{r.pair_id}-c{k}',pair_id=r.pair_id,population='random',symbol=symbol,
                entry_i=int(i),side=r.side,entry=entry,stop=entry-r.side*r.risk*scale,
                target_price=entry+(r.target_price-r.entry)*scale,risk=r.risk*scale,
                week=r.week,bucket=r.bucket,entry_time=str(f.index[i]),segment=int(f.index[i]>=pd.Timestamp(cfg['split']))))
    return pd.concat([signals,pd.DataFrame(controls)],ignore_index=True),detail


def process(job,cfg):
    symbol,paths=job;folder=EXP/'symbols'/symbol;folder.mkdir(parents=True,exist_ok=True)
    identity=dict(config=cfg,inputs=[file_stamp(p) for p in paths],code=[file_stamp(ROOT/p) for p in CODE],runtime=runtime_versions())
    if (folder/'done.json').exists():
        assert json.loads((folder/'identity.json').read_text())==identity,'Changed completed input/code'
        return json.loads((folder/'done.json').read_text())
    f=load(paths); entries,detail=candidates(f,symbol,cfg)
    entries.to_csv(folder/'candidates.csv.gz',index=False)
    audit={}
    if len(entries):_,audit=old.execute(f,entries,symbol,folder,cfg)
    done=dict(symbol=symbol,bars=len(f),first=str(f.index[0]),last=str(f.index[-1]),
        signals=int(entries.population.eq('signal').sum()) if len(entries) else 0,positions=audit.get('positions',0),**detail)
    dump(folder/'identity.json',identity);dump(folder/'done.json',done)
    return done


def compare(g,cfg):
    a=g[g.population.eq('signal')&g.closed].set_index('pair_id')
    b=g[g.population.eq('random')&g.closed].groupby('pair_id')
    means=b.net_bp.mean()[b.size().eq(cfg['controls_per_signal'])]
    paired=a.join(means.rename('control_bp'),how='inner')
    diff=paired.net_bp-paired.control_bp
    clusters=pd.DataFrame({'week':paired.week,'diff':diff}).groupby('week')['diff'].agg(['sum','count'])
    if not len(clusters):return dict(paired=0)
    sums=clusters['sum'].to_numpy();counts=clusters['count'].to_numpy();rng=np.random.default_rng(cfg['seed'])
    draws=[];null=[]
    for _ in range(2000):
        ix=rng.integers(len(sums),size=len(sums));draws.append(sums[ix].sum()/counts[ix].sum())
        null.append((sums*rng.choice([-1,1],len(sums))).sum()/counts.sum())
    return dict(paired=len(paired),metric='net_bp',mean_excess_bp=float(diff.mean()),
        ci95=list(map(float,np.quantile(draws,[.025,.975]))),cluster_signflip_p=float((1+np.sum(np.array(null)>=diff.mean()))/2001))


def summarize(cfg):
    f=pd.concat([pd.read_csv(p) for p in (EXP/'symbols').glob('*/trades.csv.gz')],ignore_index=True)
    first=f.symbol.isin(['BTCUSDT','ETHUSDT'])
    sets={'BTC':f[f.symbol.eq('BTCUSDT')],'ETH':f[f.symbol.eq('ETHUSDT')],
        'GROUP1_BTC_ETH_ONLY':f[first],'ALTCOINS':f[~first]}
    tables={};comparisons={}
    for name,g in sets.items():
        tables[name]={}
        for segment in ['all','earlier','later']:
            x=g if segment=='all' else g[g.segment.eq(int(segment=='later'))]
            tables[name][segment]={p:old.stats(x[x.population.eq(p)]) for p in ['signal','random']}
        comparisons[name]=compare(g,cfg)
    f.to_csv(EXP/'all_trades.csv.gz',index=False)
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    case1=json.loads((old.EXP/'summary.json').read_text())
    out=dict(config=cfg,tables=tables,comparisons=comparisons,
        by_symbol={s:old.stats(g[g.population.eq('signal')]) for s,g in f.groupby('symbol')},
        completed_symbols=len(done),signals=sum(x['signals'] for x in done),native_positions=sum(x['positions'] for x in done),
        coverage=done,gold_status='unavailable: all existing HistData volume values are zero placeholders',
        case1_reference={s:case1['tables'][s]['all'] for s in ['BTC','ETH','ALTCOINS']},
        case1_comparison_caveat='Different owner-defined protocol: timeframe, first-touch admission, volume and confirmation all changed; not volume attribution',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',out)
    print(json.dumps(dict(completed=len(done),tables=tables,comparisons=comparisons)),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',nargs='*');parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--summary',action='store_true');args=parser.parse_args()
    cfg=json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:raise RuntimeError('Commit builder first')
    if args.summary:summarize(cfg);return
    selected,excluded=old.jobs(cfg);selected=[j for j in selected if j[0]!='XAUUSD']
    dump(EXP/'universe.json',dict(selected=[s for s,_ in selected],excluded=excluded,
        gold_unavailable='HistData spot volume is all zero; no perpetual or fabricated substitute'))
    if args.symbols:selected=[j for j in selected if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(process,j,cfg):j[0] for j in selected}
        for future in as_completed(futures):
            try:print(json.dumps(future.result()),flush=True)
            except Exception as e:print('FAILED '+futures[future]+': '+repr(e),flush=True);raise
    if not args.symbols:summarize(cfg)


if __name__=='__main__':main()
