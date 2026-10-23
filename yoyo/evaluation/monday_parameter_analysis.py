"""Analyze the frozen parameter ledger with strict entry-week controls.

Long confirmation windows can carry a Sunday pin into next Monday. Historical
controls were sampled in the pin's NY week. Preserve those raw fills, but mark
cross-week pairs unmatched rather than call them same-entry-week controls.
Only three closed controls with the signal's actual entry week and volatility
bucket enter paired statistics. Selection remains earlier mean independent R.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_vectorbt_research import EXP, ROOT
from yoyo.evaluation.range_framework import dump


def add_actual_week(f):
    f=f.copy()
    ix=pd.DatetimeIndex(pd.to_datetime(f.entry_time,utc=True)).tz_convert('America/New_York')
    f['actual_entry_week']=(ix.tz_localize(None).normalize()-pd.to_timedelta(ix.day_of_week,unit='D')).strftime('%Y-%m-%d')
    return f


def strict_pairs(g,basis):
    """Join by original pair identity and verify actual block, side and bucket."""
    a=g[g.population.eq('signal') & g.closed].set_index('pair_id')
    b=g[g.population.eq('random') & g.closed].copy()
    if not len(a) or not len(b):return pd.DataFrame()
    for col in ('actual_entry_week','bucket','symbol','side'):
        b=b[b[col].eq(b.pair_id.map(a[col]))]
    count=b.groupby('pair_id').size()
    complete=count[count.eq(3)].index
    b=b[b.pair_id.isin(complete)]
    means=b.groupby('pair_id')[[f'{basis}_r',f'{basis}_bp']].mean().rename(columns=lambda x:'control_'+x)
    return a.join(means,how='inner')


def describe(g,basis):
    a=g[g.population.eq('signal') & g.closed];r=a[f'{basis}_r']
    p=strict_pairs(g,basis);loss=float(-r[r<0].sum())
    return dict(signals=int(g.population.eq('signal').sum()),closed=len(a),
        censored=int((g.population.eq('signal')&~g.closed).sum()),
        win_rate=float((r>0).mean()) if len(r) else None,
        mean_r=float(r.mean()) if len(r) else None,total_r=float(r.sum()),
        median_r=float(r.median()) if len(r) else None,
        profit_factor=float(r[r>0].sum()/loss) if loss else None,
        mean_bp=float(a[f'{basis}_bp'].mean()) if len(a) else None,
        paired=len(p),paired_signal_r=float(p[f'{basis}_r'].mean()) if len(p) else None,
        paired_random_r=float(p[f'control_{basis}_r'].mean()) if len(p) else None,
        paired_excess_bp=float((p[f'{basis}_bp']-p[f'control_{basis}_bp']).mean()) if len(p) else None,
        stop_first_mean_r=float(a[f'stop_first_{basis}_r'].mean()) if len(a) else None,
        cross_week_signals=int(a.actual_entry_week.ne(a.week).sum()))


def bootstrap(p,basis,seed):
    if p.empty:return {}
    c=p.assign(delta=p[f'{basis}_bp']-p[f'control_{basis}_bp']).groupby('actual_entry_week').delta.agg(['sum','count'])
    sums=c['sum'].to_numpy();counts=c['count'].to_numpy();rng=np.random.default_rng(seed);draws=[];null=[]
    observed=float(sums.sum()/counts.sum())
    for _ in range(2000):
        ix=rng.integers(len(sums),size=len(sums));draws.append(sums[ix].sum()/counts[ix].sum())
        null.append((sums*rng.choice([-1,1],len(sums))).sum()/counts.sum())
    return dict(excess_bp=observed,ci95_bp=list(map(float,np.quantile(draws,[.025,.975]))),
                p_raw=float((1+np.sum(np.asarray(null)>=observed))/2001))


def group_data(c):
    first=c.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD'])
    return {'BTC':c[c.symbol.eq('BTCUSDT')],'ETH':c[c.symbol.eq('ETHUSDT')],
            'XAUUSD':c[c.symbol.eq('XAUUSD')],'GROUP1_GROSS':c[first],'ALTCOINS':c[~first]}


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit analysis before generating results')
    cfg=json.loads((EXP/'config.json').read_text())
    f=add_actual_week(pd.read_csv(EXP/'all-trades.csv.gz',float_precision='round_trip'))
    rows=[];lookup={}
    for v in cfg['candidates']:
        for name,g in group_data(f[f.config_id.eq(v['config_id'])]).items():
            if g.empty:continue
            basis='gross' if name in ('XAUUSD','GROUP1_GROSS') else 'net'
            for period in ('all','earlier','later'):
                x=g if period=='all' else g[g.segment.eq(int(period=='later')) & ~g.crosses_selection_cut]
                row=dict(**v,group=name,period=period,basis=basis,**describe(x,basis))
                rows.append(row);lookup[(v['config_id'],name,period)]=(row,x)
    table=pd.DataFrame(rows);table.to_csv(EXP/'metrics-entry-week.csv',index=False)
    selections=[]
    for case,axis in [(2,'confirmations'),(3,'confirmations'),(4,'confirmations'),(2,'volume_window')]:
        subset=table[table.case.eq(case)&(table.volume_window.eq(20) if axis=='confirmations' else table.confirmations.eq(3))]
        for name,early in subset[subset.period.eq('earlier')].groupby('group'):
            early=early[early.closed.gt(0)].sort_values(['mean_r',axis],ascending=[False,True])
            if early.empty:continue
            best=early.iloc[0];later,x=lookup[(best.config_id,name,'later')]
            baseline,_=lookup[(f'case{case}-n3-v20',name,'later')]
            selections.append(dict(case=case,axis=axis,group=name,basis=best.basis,
                value=int(best[axis]),earlier_n=int(best.closed),earlier_mean_r=float(best.mean_r),
                later=later,baseline_later=baseline,
                inference=bootstrap(strict_pairs(x,best.basis),best.basis,cfg['seed'])))
    tests=sorted([(i,s['inference']['p_raw']) for i,s in enumerate(selections) if 'p_raw' in s['inference']],key=lambda x:x[1])
    previous=0.
    for rank,(i,p) in enumerate(tests):
        previous=max(previous,min(1.,p*(len(tests)-rank)));selections[i]['inference']['holm_p']=previous
    old=json.loads((EXP/'summary.json').read_text())
    out={k:old[k] for k in ('configurations','symbols','signal_rows','unique_new_framework_positions','reused_unique_positions','baseline_signal_checks')}
    out.update(selection=selections,analysis_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        strict_control_policy='Actual NY entry week, symbol, side and vol bucket; three closed controls. Cross-week pin controls remain raw but are unmatched. Paired signal and random meanR are shown together.',
        cross_week_signal_rows=int((f.population.eq('signal') & f.actual_entry_week.ne(f.week)).sum()),
        selection_crossing_signal_rows=int((f.population.eq('signal') & f.crosses_selection_cut).sum()),
        limitations=cfg['limitations'],training_eligible=False,production_eligible=False)
    dump(EXP/'summary-entry-week.json',out)
    print(json.dumps(out,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
