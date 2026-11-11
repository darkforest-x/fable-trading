"""Freeze a monthly-rail scorecard before 2025, then test identical accounts.

At the touch 5m open use the last 14 closed 5m true ranges, the preceding
one-hour close-to-close move, rail side and frozen rail-to-near distance.
At confirmation also use the UTC-aligned touching timeframe's completed OHLC
for strict recovery and outward wick share. No outcome enters a feature.
The score estimates historical fee/slippage-adjusted win frequency of one
specific trading rule using smoothed cell counts, not a transferable label.
"""
import json

import numpy as np
import pandas as pd

from prepare import BAR, CACHE, HERE, PARENT
from run_research import Inputs, START, SPLIT, END, PERIODS, account_result
from account import PERIODS as ACCOUNT_PERIODS
from engine import simulate_batch

YEAR26 = pd.Timestamp('2026-01-01T00:00:00Z').value // 1_000_000
INTERVALS = (5,15,60,240)
ALPHA = 30
BASE = ['side','high_atr','room','fast_approach']
CONF = BASE + ['reclaimed','long_wick']
TEST_PERIODS = {'2025':(SPLIT,YEAR26), '2026':(YEAR26,END), 'forward':(SPLIT,END)}
PERIODS.update(TEST_PERIODS)
ACCOUNT_PERIODS.update(TEST_PERIODS)


def features(data, minutes):
    roots = data.candidates[data.candidates.rule.eq('touch_close')]
    events = pd.read_csv(PARENT/'event_paths.csv.gz',usecols=['event_id','level','ready_utc_derived']).set_index('event_id')
    n = minutes//5
    records = []
    for row in roots.itertuples(index=False):
        a = data.array[row.grid_idx]
        k = row.entry_idx - 1
        root_ms = int(a[k,0])
        start = k - (root_ms//BAR) % n
        end = start+n
        event = events.loc[row.event_id]
        ready = pd.Timestamp(event.ready_utc_derived).value//1_000_000
        if a[start,0] < ready or a[end,0]+12*BAR > min(row.month_end_ms, END):
            continue
        h,l,c = a[k-14:k,2],a[k-14:k,3],a[k-15:k-1,4]
        atr_pct = np.maximum.reduce([h-l,abs(h-c),abs(l-c)]).mean()/a[k-1,4]
        rail = event.level
        upper = row.side == 'upper'
        sign = 1 if upper else -1
        move = sign*(a[k-1,4]/a[k-13,4]-1)
        candle = a[start:end]
        o,hi,lo,close = candle[0,1],candle[:,2].max(),candle[:,3].min(),candle[-1,4]
        assert (hi >= rail if upper else lo <= rail)
        wick = hi-max(o,close) if upper else min(o,close)-lo
        records.append(dict(candidate_id=f'{row.event_id}/confirmation-{minutes}m',event_id=row.event_id,symbol=row.symbol,
            side=row.side,target_price=row.target_price,entry_idx=end,entry_ms=int(a[end,0]),touch_ms=root_ms,
            pre_available_ms=root_ms,confirmed_ms=int(a[end-1,0])+BAR, high_atr=bool(atr_pct>=.005),
            room=bool(abs(rail-row.target_price)/rail>=.04),fast_approach=bool(move>=.01),
            reclaimed=bool(sign*(close-rail)<0),long_wick=bool(hi>lo and wick>=.5*(hi-lo))))
    return pd.DataFrame(records)


def score(rows, label, stage, global_rate, prior=None):
    fields = BASE if stage=='pre' else CONF
    fit = rows.loc[label.index,fields].assign(win=label)
    agg = fit.groupby(fields,sort=True).win.agg(['sum','count']).reset_index()
    out = rows[fields].merge(agg,how='left',on=fields,sort=False,validate='many_to_one')
    parent = global_rate if prior is None else prior
    result = (out['sum'].fillna(0).to_numpy()+ALPHA*parent)/(out['count'].fillna(0).to_numpy()+ALPHA)
    return result, agg


def run():
    data = Inputs()
    metrics, library, scorefiles, diagnostics, funded = [], [], [], [], []
    # Require the same observable event universe and a tradable one-hour
    # post-confirmation window for all intervals before comparing them.
    frames = {m:features(data,m) for m in INTERVALS}
    common = set.intersection(*(set(f.event_id) for f in frames.values()))
    receipt = {'shared_events':len(common),'available_by_timeframe':{str(m):len(f) for m,f in frames.items()}}
    for m,f in frames.items():
        f = f[f.event_id.isin(common)].reset_index(drop=True)
        params = dict(tp_mode='fixed_pct',tp_value=.04,sl_pct=.02,horizon_bars=12)
        trades,marks = simulate_batch(data.windows,f,slippage_bps=1,**params)
        scored = f.merge(trades[['candidate_id','exit_ms','net_pct']],on='candidate_id',validate='one_to_one')
        assert len(scored)==len(common)
        fit = (scored.entry_ms>=START)&(scored.exit_ms<SPLIT)
        label = scored.loc[fit,'net_pct']>0
        prior = label.mean()
        pre,book_pre = score(scored,label,'pre',prior)
        confirm,book_post = score(scored,label,'confirm',prior,pre)
        gates = {}
        for stage,arr,table in [('pre',pre,book_pre),('confirmation',confirm,book_post)]:
            threshold = float(np.quantile(arr[fit],.60))
            metric = stage+'_score'
            scored[metric] = arr
            gates[stage] = threshold
            table['timeframe_minutes'],table['stage'],table['threshold'],table['early_global_win'] = m,stage,threshold,prior
            library.append(table)
            for period,left,right in [('2025',SPLIT,YEAR26),('2026',YEAR26,END),('forward',SPLIT,END)]:
                val = scored[(scored.entry_ms>=left)&(scored.exit_ms<right)].copy()
                for band,values in [('all',val),('above',val[val[metric]>=threshold]),('below',val[val[metric]<threshold])]:
                    if len(values):
                        y = values.net_pct.gt(0)
                        diagnostics.append(dict(timeframe_minutes=m,stage=stage,period=period,band=band,n=len(values),
                            win_pct=100*y.mean(),avg_net_pct=values.net_pct.mean(),mean_score_pct=100*values[metric].mean(),
                            brier=((values[metric]-y.astype(float))**2).mean(),
                            brier_constant=((prior-y.astype(float))**2).mean()))
        scored['minutes']=m
        scored['fit_outcome']=fit
        scorefiles.append(scored)
        policies = {'all':np.ones(len(scored),dtype=bool),'pre_top40':pre>=gates['pre'],
                    'confirm_top40':confirm>=gates['confirmation'],'reclaim':scored.reclaimed,
                    'reclaim_confirm_top40':scored.reclaimed & (confirm>=gates['confirmation'])}
        for bps in (1,5):
            fills,paths = (trades,marks) if bps==1 else simulate_batch(data.windows,f,slippage_bps=bps,**params)
            for name,mask in policies.items():
                ids = scored.loc[mask,'candidate_id']
                a = fills[fills.candidate_id.isin(ids)]
                b = paths[paths.candidate_id.isin(ids)]
                for period in ('2025','2026','forward'):
                    result = account_result(a,b,period,.05)
                    metrics.append(dict(result['summary'],timeframe_minutes=m,policy=name,slip_bps=bps))
                    if bps==1 and period=='forward':
                        funded.append(result['funded_trades'].assign(timeframe_minutes=m,policy=name))
        print('Finished', m, 'minutes', gates, flush=True)
    pd.DataFrame(metrics).to_csv(HERE/'timeframe_accounts.csv',index=False)
    pd.concat(library,ignore_index=True).to_csv(HERE/'timeframe_scorecard.csv',index=False)
    pd.concat(scorefiles,ignore_index=True).to_csv(HERE/'timeframe_scores.csv.gz',index=False)
    pd.concat(funded,ignore_index=True).to_csv(HERE/'timeframe_funded_trades.csv.gz',index=False)
    pd.DataFrame(diagnostics).to_csv(HERE/'timeframe_calibration.csv',index=False)
    (HERE/'timeframe_coverage.json').write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':
    run()
