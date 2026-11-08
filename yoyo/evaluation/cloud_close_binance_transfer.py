"""Transfer the fixed 0%/75% target weights to the existing Binance 12H signal history."""
import gc
import json
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation.monday_limit_reclaim_inputs import jobs
from yoyo.evaluation.signal_center_score import five_minute,stop_pct


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-binance-transfer-20261109-v1'
WORK=EXP/'work'
ARCHIVE=EXP.parent/'exp-signal-center-score-20261106-v1'
CONFIG=json.loads((EXP/'config.json').read_text())
START=pd.Timestamp(CONFIG['period_start_utc']).value//1_000_000
END=pd.Timestamp(CONFIG['period_end_exclusive_utc']).value//1_000_000
FIELDS=['open','high','low','close']
PATH_COLUMNS=['symbol','parent_id','event_id','is_control','side','entry_ms','exit_bar_ms','entry_price',
              'exit_price','exit_phase','exit_reason','exit_fee_rate','tp_r','stop_price','same_bar_ambiguous',
              'entry_order','eligible_at_own_time']


def aggregate(bars, hours):
    """Only complete groups of aligned native five-minute opening timestamps qualify."""
    g=bars.resample(f'{hours}h',origin='epoch',label='left',closed='left')
    part=g.agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),n=('open','count'))
    part=part[part.n.eq(12*hours)].drop(columns='n')
    part.index=pd.Index(part.index.as_unit('ms').asi8,name='time')
    return part


def process(job):
    ticker=job['symbol']
    slot=WORK/ticker
    receipt=slot/'coverage.json'
    if receipt.is_file():
        return json.loads(receipt.read_text())
    slot.mkdir(parents=True,exist_ok=True)
    source=ARCHIVE/'results/symbols'/ticker
    if not (source/'done.json').is_file():
        raise ValueError(f'Missing source receipt {ticker}')
    bars=five_minute(job)
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing or (bars.index.as_unit('ms').asi8%300000).any():
        raise ValueError(f'Invalid 5m clock {ticker}')
    times=bars.index.as_unit('ms').asi8
    bars=bars[(times>=START)&(times<END)]
    hourly,twelve=aggregate(bars,1),aggregate(bars,12)
    del bars
    rank_times=leader.trailing_return(twelve.reset_index()).dropna().index.to_numpy(dtype='int64')
    np.savez(slot/'quotes.npz',clock=hourly.index.to_numpy(),prices=hourly[FIELDS].to_numpy(),
             rank_times=rank_times)
    file=source/'signals.csv.gz'
    signals=pd.read_csv(file,usecols=['tf','close_ns','signal_id','week','side','stop']) if file.exists() else pd.DataFrame(columns=['tf','close_ns'])
    signals=signals[signals.tf.eq('12H') & signals.close_ns.ge(START*1_000_000)&signals.close_ns.lt(END*1_000_000)]
    skips=dict(missing_entry=0,nonpositive_risk=0)
    rows=[]
    counts=dict(matched=0,unmatched=0)
    if len(signals):
        q,clock,prepared=engine._prepare_symbol(twelve,hourly)
        features=prepared['features']
        atr=pd.Series(features['prior_atr_pct'],index=features['close_times'])
        aligned=pd.Series(np.sign(features['ema20']-features['ema50']),index=features['close_times'])
        ready=pd.Series(features['ema_ready'],index=features['close_times'])
        parents=[]
        for idx,r in signals.iterrows():
            timestamp=int(r.close_ns)//1_000_000
            if timestamp not in atr.index or not ready.loc[timestamp] or aligned.loc[timestamp]!=int(r.side):
                raise ValueError(f'Frozen 12H confirmation differs from source timeline {r.signal_id}')
            i=int(clock.searchsorted(timestamp))
            can_enter=i<len(clock) and clock[i]==timestamp
            price=float(q[i,0]) if can_enter else None
            risk=(price-float(r.stop))*int(r.side) if can_enter else None
            if not can_enter:
                skips['missing_entry']+=1
            elif risk<=0:
                skips['nonpositive_risk']+=1
            execute=can_enter and risk>0
            parents.append(dict(parent_id=r.signal_id,event_id=r.signal_id,source_row_number=int(idx+1),
                symbol=ticker.removesuffix('USDT'),week=r.week,side=int(r.side),key_open_ms=timestamp-leader.BAR,
                confirmation_ms=timestamp,rank=None,was_top20=False,is_current=True,cohort='binance_frozen',
                bar_source='complete Binance 5m resamples',entry_i=i if execute else None,entry_ms=timestamp,
                entry_price=price if execute else None,stop_price=float(r.stop),risk_distance=risk,
                risk_fraction=risk/price if execute else None,signal_prior_atr_pct=float(atr.loc[timestamp]),
                signal_reference_stop=float(r.stop),signal_reference_entry=float(r.stop)/(1-int(r.side)*stop_pct(ticker)),
                entry_source='next observed coincident H1 open',is_control=False))
        cfg=json.loads((wallet.EXP/'paths_config.json').read_text())
        controls,match=engine._match_controls(parents,twelve,hourly,features,cfg)
        counts=dict(matched=match['matched'],unmatched=match['unmatched'])
        events=[p for p in parents if p['entry_i'] is not None]+controls
        ranks={p['parent_id']:p['signal_prior_atr_pct'] for p in parents}
        for e in events:
            e['end_i'],e['path_upper_bound_reason']=engine._safe_path_end(e['entry_i'],e['side'],q,clock)
        fills,_=engine._simulate_symbol(events,q,clock,(0.,2.),8)
        avail=set(rank_times)
        for v in fills:
            v['entry_order']=ranks[v['parent_id']]
            v['eligible_at_own_time']=v['entry_ms'] in avail
        rows=fills
    pd.DataFrame(rows,columns=PATH_COLUMNS).to_csv(slot/'fills.csv.gz',index=False)
    meta=dict(ticker=ticker,source_signals=len(signals),hourly_bars=len(hourly),native12h_bars=len(twelve),
              native_turnover_coverage=len(rank_times),fill_columns=len(rows),**counts,**skips)
    temp=receipt.with_suffix('.tmp')
    temp.write_text(json.dumps(meta))
    temp.replace(receipt)
    return meta


@lru_cache(maxsize=180)
def quotes(symbol):
    with np.load(WORK/(symbol+'USDT')/'quotes.npz') as f:
        return f['clock'],f['prices']


def summarize(positions,equity):
    return {p:wallet.summarize(positions,equity,*lims) for p,lims in {
        'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
        '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}.items()}


def account(pool, partial, slip=0.):
    runner=pool[pool.tp_r.eq(0)]
    if partial:
        data=split.pair_data(runner,pool[pool.tp_r.eq(2)],9,start=START,end=END,quote_loader=quotes)
        result=split.simulate(data,.75,slip)
    else:
        data=wallet.prepare(runner,9,start=START,end=END,quote_loader=quotes)
        positions,equity=wallet.account(data,slip)
        result=summarize(positions,equity),positions,equity
    return result,data['gap_marks']


def main():
    WORK.mkdir(exist_ok=True)
    js=[j for j in jobs() if j['group']!='spot_gold']
    if len(js)!=632 or len({j['symbol'] for j in js})!=632:
        raise ValueError('Original 632-contract universe differs')
    with ProcessPoolExecutor(max_workers=2) as executor:
        records=[]
        for i,rec in enumerate(executor.map(process,js),1):
            records.append(rec)
            if i%40==0: print('Priced Binance contracts',i,'of',len(js),flush=True)
    coverage=pd.DataFrame(records)
    coverage.to_csv(EXP/'coverage.csv',index=False)
    membership={}
    parts=[]
    for r in records:
        slot=WORK/r['ticker']
        with np.load(slot/'quotes.npz') as f:
            for t in f['rank_times']:
                membership[t]=membership.get(t,0)+1
        if r['fill_columns']:
            parts.append(pd.read_csv(slot/'fills.csv.gz',dtype={'symbol':str},
                                     float_precision='round_trip',keep_default_na=False))
    paths=pd.concat(parts,ignore_index=True)
    if paths.duplicated(['event_id','tp_r']).any() or not paths.groupby('event_id').size().eq(2).all():
        raise ValueError('Duplicated or incomplete event paths')
    paths['eligible']=paths.eligible_at_own_time & paths.entry_ms.map(membership).fillna(0).ge(10)
    paths.to_csv(EXP/'fills.csv.gz',index=False)
    real=paths[paths.eligible&~paths.is_control]
    ctl=paths[paths.eligible&paths.is_control&paths.parent_id.isin(real.parent_id)]
    paired=real[real.parent_id.isin(ctl.parent_id)]
    rows=[]
    gaps={}
    for name,pool,partial,slip in [('runner',real,False,0.),('partial75',real,True,0.),
        ('slippage_5bp',real,True,.0005),('paired_signals',paired,True,0.),('control',ctl,True,0.)]:
        print('Funding',name,flush=True)
        (metrics,pos,eq),gaps[name]=account(pool,partial,slip)
        if name in ('runner','partial75'):
            pos.to_csv(EXP/f'funded_{name}.csv.gz',index=False if name=='runner' else True)
            eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        rows.extend(dict(policy=name,period=p,**v) for p,v in metrics.items())
        gc.collect()
    table=pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    summary=dict(source_signals=int(coverage.source_signals.sum()),priced_real=len(paths[~paths.is_control])//2,
                 eligible_signals=len(real)//2,eligible_pairs=len(paired)//2,missing_H1_open=int(coverage.missing_entry.sum()),
                 nonpositive_risk=int(coverage.nonpositive_risk.sum()),ambiguous_legs=int(paths.same_bar_ambiguous.sum()),
                 position_leg_hours_unquoted=gaps,all_metrics=table[table.period.eq('all')].to_dict('records'))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
