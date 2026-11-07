"""Audit the post-entry second-wave trigger against matched entries and quarter-matched randomized early exits."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_selection as s
from yoyo.evaluation import monday_breadth_opt_second_wave as wave
from yoyo.evaluation import monday_breadth_opt_payoffs as quotes
from yoyo.evaluation.monday_breadth_opt_match_exits import evaluate

REQUEST=study.OUT/'second_wave_requests'
CONTROL=s.OUT/'controls/second_wave'


def requests():
    z=pd.read_csv(study.OUT/'second_wave_chosen.csv')
    events=pd.read_parquet(study.OUT/'second_wave_exit_events.parquet')
    entries=pd.read_csv(study.OUT/'all_entries.csv').set_index('candidate_id')
    rules=[]; paths=[];placebos=[]
    for row in z.itertuples():
        entry=row.candidate_id;key='wave_'+row.asset
        rule=entries.loc[entry].to_dict()
        rule.update(candidate_id=key,entry_id=entry,hold_hours=144,policy_id=0,role='dynamic_primary',
                    checkpoints=str(row.checkpoints),threshold=row.threshold,eligible=True)
        rules.append(rule)
        g=events[(events.candidate_id==entry)&(events.checkpoints.astype(str)==str(row.checkpoints))&(events.threshold==row.threshold)].copy()
        g['candidate_id']=key;paths.append(g)
        fr,re=s.inputs.cached_partition(rule['minutes'],rule['wait_min'])
        # Prespecified best rules have one checkpoint; the placebo conditions on each quarter's observed exit count.
        if '-' in str(row.checkpoints):continue
        clock=int(row.checkpoints)
        o=g[g.epoch==3].copy()
        pos=pd.Index(fr.known).get_indexer(o.known)
        o['early_return']=re[pos,s.HOLDS.index(clock),s.ASSETS.index(row.asset)]
        o=o.dropna(subset=['early_return','baseline_pct','net_pct'])
        o['gain_if_exited']=o.early_return-o.baseline_pct
        o['quarter']=o.decision.dt.quarter
        if not len(o):continue
        n_draw=50000;rng=np.random.default_rng(20261108)
        totals=np.zeros(n_draw)
        for _,a in o.groupby('quarter'):
            count=int(a.exit_h.lt(144).sum())
            if count:
                permutations=np.argsort(rng.random((n_draw,len(a))),axis=1)[:,:count]
                totals+=a.gain_if_exited.to_numpy()[permutations].sum(axis=1)
        null=totals/len(o)
        observed=o.difference.mean();center=null.mean()
        placebos.append(dict(asset=row.asset,n=len(o),early=int(o.exit_h.lt(144).sum()),actual_net=o.net_pct.mean(),
            no_early_exit=o.baseline_pct.mean(),always_early=o.early_return.mean(),random_early_net=o.baseline_pct.mean()+center,
            advantage_over_random_timing=observed-center,p_two_sided=(1+np.sum(np.abs(null-center)>=abs(observed-center)-1e-10))/(n_draw+1),
            worst_event=o.net_pct.min(),remove_best_month=s.describe(o)['exmonth']))
    REQUEST.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rules).to_csv(REQUEST/'locked_rules.csv',index=False)
    pd.concat(paths,ignore_index=True).to_parquet(REQUEST/'locked_events.parquet',index=False)
    pd.DataFrame(placebos).to_csv(study.OUT/'second_wave_random_exit_timing.csv',index=False)


def exit_hours(w,x,known,n,checkpoints,threshold):
    barrier={'five':5,'ten':10,'twentyfive':25,'quarter_initial':.25*n,'one_initial':n}[threshold]
    states=dict(zip((6,12,24,48),wave.expansion(w,x,pd.Timestamp(known))))
    return next((int(c) for c in str(checkpoints).split('-') if states[int(c)]>=barrier),144)


def matched(past):
    directory=CONTROL/'past_only' if past else CONTROL
    draws=pd.read_parquet(directory/'dynamic_matches.parquet')
    rules=pd.read_csv(REQUEST/'locked_rules.csv');events=pd.read_parquet(REQUEST/'locked_events.parquet')
    if draws.empty:
        empty=pd.DataFrame(columns=['candidate_id','decision','actual_pct','random_pct'])
        evaluate(rules,events,empty,'dynamic_primary').to_csv(directory/'second_wave_validity.csv',index=False)
        return
    link=['candidate_id','decision','initial','exit_h','net_pct']
    q=draws[['candidate_id','decision','symbol','random_entry','weight','draw']].merge(events[link],on=['candidate_id','decision'],validate='many_to_one')
    q=q.merge(rules[['candidate_id','minutes','wait_min','threshold','checkpoints','asset']],on='candidate_id',validate='many_to_one')
    q['control_h']=[exit_hours(r.minutes,r.wait_min,r.random_entry-pd.Timedelta(minutes=r.wait_min),r.initial,r.checkpoints,r.threshold) for r in q.itertuples()]
    def price(pair):
        sym,g=pair
        g=g.copy()
        rand=g[['random_entry','control_h']].drop_duplicates().copy()
        targets=rand.random_entry+pd.to_timedelta(rand.control_h,unit='h')
        f,_=quotes.load_full_ohlc(sym,rand.random_entry.min(),targets.max(),repair_missing=False)
        buy=f.open.reindex(rand.random_entry).to_numpy();sell=f.open.reindex(targets).to_numpy()
        ok=np.isfinite(buy)&np.isfinite(sell)
        rand['random_pct']=np.nan
        rand.loc[ok,'random_pct']=quotes._vbt_net_returns(buy[ok],sell[ok])
        g=g.merge(rand,on=['random_entry','control_h'],validate='many_to_one')
        g['actual_pct']=g.net_pct
        # Match the same named actual members when only a subset of the basket qualifies.
        for (w,x),sub in g[g.asset=='ALT'].groupby(['minutes','wait_min']):
            files=list((quotes.RESEARCH/'constituents'/f'minutes={w}'/f'symbol={sym}').glob('part-*.parquet'))
            raw=pd.concat([pd.read_parquet(f,columns=['decision','hold_hours','net_return_pct'],filters=[('wait_min','=',int(x))]) for f in files])
            ix=pd.MultiIndex.from_arrays([sub.decision,sub.exit_h])
            g.loc[sub.index,'actual_pct']=raw.set_index(['decision','hold_hours']).net_return_pct.reindex(ix).to_numpy()
        return g
    with ThreadPoolExecutor(2) as pool:
        priced=pd.concat(pool.map(price,q.groupby('symbol')),ignore_index=True)
    priced.to_parquet(directory/'matched_wave_fills.parquet',index=False)
    z=priced.dropna(subset=['actual_pct','random_pct']).groupby(['candidate_id','decision','symbol']).agg(
        weight=('weight','first'),a=('actual_pct','mean'),b=('random_pct','mean')).reset_index()
    z['a']*=z.weight;z['b']*=z.weight
    agg=z.groupby(['candidate_id','decision'],as_index=False)[['weight','a','b']].sum()
    agg['actual_pct']=agg.a/agg.weight;agg['random_pct']=agg.b/agg.weight
    agg.to_parquet(directory/'wave_paired_events.parquet',index=False)
    evaluate(rules,events,agg[agg.weight>=.80-1e-8],'dynamic_primary').to_csv(directory/'second_wave_validity.csv',index=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['requests','matched']);parser.add_argument('--past-only',action='store_true')
    a=parser.parse_args()
    if a.stage=='requests':requests()
    else:matched(a.past_only)
