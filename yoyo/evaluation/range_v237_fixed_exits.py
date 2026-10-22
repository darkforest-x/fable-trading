"""Owner-requested fixed targets and touch-BE on frozen causal V2.37 entries.

No new entry features: reuse entry_i, entry, side, initial stop and matched
controls from the preceding two-year study. The sole research axis is exits.
Nautilus executes all positions; a small signed-path oracle validates prices
and order of events using the stated OHLC assumption, not observed ticks.
Future bars are outcomes only. No Pine, live defaults or source data changes.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_v237_twoyear import read, close_frame, metrics

EXP = ROOT/'experiments/active/exp-range-v237-fixed-exits-20261022-v1'
CONFIG = EXP/'config.json'


def policies():
    return [dict(name=f'tp{target}'+('_be1' if be else '')+'_'+path,
                 fixed_target_r=target, opposite_hour_exit=False,
                 breakeven_r=1 if be else None, breakeven_includes_cost=False,
                 breakeven_on_touch=be, intrabar_path=path)
            for path in ['OHLC','OLHC'] for target,be in [(1,False),(2,False),(3,False),(2,True),(3,True)]]


def oracle(bars, entries, target_r, be, path, tick):
    """Validate the native simulated path; never generate research fills.

    Within each bar, an opening gap precedes continuous monotone legs. Resting
    exits match before the endpoint callback can amend a stop. Favorable 1R
    touch and target at the same price therefore exit at target. Boundary
    marks remain reason0, and entry-bar price history is never inspected.
    """
    output=[];eps=tick*1e-7
    order=[0,1,2,3] if path=='OHLC' else [0,2,1,3]
    for r in entries.itertuples():
        side,entry,risk=r.side,r.entry,r.risk
        raw=(entry+side*target_r*risk)/tick
        target=(math.ceil(raw-1e-8) if side>0 else math.floor(raw+1e-8))*tick
        stop=r.stop;armed=False;done=False
        for j in range(int(r.entry_i)+1,int(r.end_i)+1):
            for phase,col in enumerate(order):
                price=bars[j,col]
                if side*(price-stop)<=eps:
                    price=price if phase==0 else stop
                    output.append((j,price,5 if armed else 1));done=True;break
                if side*(price-target)>=-eps:
                    output.append((j,target,4));done=True;break
                if be and not armed and side*(price-entry)+eps>=risk:
                    stop=entry;armed=True
            if done:break
        if not done:output.append((int(r.end_i),bars[int(r.end_i),3],0))
    return np.array(output)


def prepare(symbol):
    cfg=json.loads(CONFIG.read_text());src=ROOT/cfg['source']/symbol
    dest=EXP/symbol;dest.mkdir(exist_ok=True)
    if (dest/'preparation.json').exists():return
    occ=read(src/'occurrences.csv');occ=occ[occ['mode']=='causal'].copy()
    base=read(src/'candidates.csv');base=base[base.pair_id.isin(occ.pair_id)].copy()
    assert base.pair_id.is_unique and len(occ)==2*sum(occ.population.eq('signal'))
    keyed=base.set_index('pair_id')
    for col in ['entry_i','side','entry','stop','risk','end_i']:
        np.testing.assert_array_equal(occ[col],occ.pair_id.map(keyed[col]))
    # Price and risk are on the same exchange tick grid, so TP1 is the BE1
    # activation price. The already-resting full TP wins; no duplicate replay.
    tick=next(s['tick'] for s in cfg['symbols'] if s['symbol']==symbol)
    raw=(base.entry+base.side*base.risk)/tick
    target=np.where(base.side>0,np.ceil(raw-1e-8),np.floor(raw+1e-8))*tick
    np.testing.assert_allclose(target,base.entry+base.side*base.risk,atol=tick*1e-7,rtol=0)
    base.to_csv(dest/'candidates.csv',index=False);occ.to_csv(dest/'occurrences.csv',index=False)
    native=json.loads((src/'native.json').read_text())
    native.update(experiment_id=cfg['experiment_id'],entry_candidates=str((dest/'candidates.csv').relative_to(ROOT)),
        policies=policies(),output=str((dest/'native').relative_to(ROOT)))
    dump(dest/'native.json',native)
    dump(dest/'preparation.json',dict(symbol=symbol,unique_positions=len(base),occurrences=len(occ),
        signals=occ[occ.population=='signal'].groupby('lane').size().to_dict(),
        inputs=[file_stamp(src/x) for x in ['candidates.csv','occurrences.csv','candles.csv.gz','results.csv.gz']],
        aliases={'tp1_be1':'tp1'},entry_stop_and_control_parity=True))
    print(symbol,len(base),len(occ),flush=True)


def score(rows):
    m=metrics(rows)
    n=rows[rows.reason!=0]
    m.update(tp=int(n.reason.eq(4).sum()),be=int(n.reason.eq(5).sum()),
        tp_net_nonpositive=int((n.reason.eq(4)&n.net_r.le(1e-9)).sum()),
        gross_flat=int(n.gross_r.abs().lt(1e-7).sum()),
        marked_net_r=float(rows.net_r.sum()),
        mean_win_r=float(n.loc[n.net_r>1e-9,'net_r'].mean()) if (n.net_r>1e-9).any() else None)
    return m


def analyze(symbol):
    cfg=json.loads(CONFIG.read_text());src=ROOT/cfg['source']/symbol;dest=EXP/symbol
    tick=next(s['tick'] for s in cfg['symbols'] if s['symbol']==symbol)
    occ=read(dest/'occurrences.csv');base=read(dest/'candidates.csv').sort_values('pair_id')
    frame=close_frame(read(src/'candles.csv.gz'));bars=frame[['open','high','low','close']].to_numpy()
    outcomes=['exit_i','exit_price','reason','peak_r','arm_i','protection','gross_r','fee_r','net_r','net_notional_bp','exit_time','be_trigger_i','be_effective_i']
    parts=[];checks=[]
    for spec in policies():
        rows=read(dest/'native'/spec['name']/'trades.csv').sort_values('pair_id')
        np.testing.assert_array_equal(rows.pair_id,base.pair_id)
        expected=oracle(bars,base,spec['fixed_target_r'],spec['breakeven_on_touch'],spec['intrabar_path'],tick)
        np.testing.assert_array_equal(rows.exit_i,expected[:,0]);np.testing.assert_array_equal(rows.reason,expected[:,2])
        np.testing.assert_allclose(rows.exit_price,expected[:,1],atol=tick*1e-7,rtol=0)
        for col in ['entry_i','side','entry','stop','risk','end_i']:
            np.testing.assert_array_equal(rows[col],base[col])
        np.testing.assert_allclose(rows.framework_commission,rows.quantity*rows.entry*cfg['cost'],atol=2e-8,rtol=0)
        np.testing.assert_allclose(rows.net_r,rows.gross_r-rows.entry/rows.risk*cfg['cost'],atol=1e-7,rtol=0)
        merged=occ.merge(rows[['pair_id',*outcomes]],on='pair_id',validate='many_to_one')
        merged['policy']=spec['name'].rsplit('_',1)[0];merged['path']=spec['intrabar_path']
        parts.append(merged);checks.append(dict(policy=spec['name'],oracle_paths=len(rows)))
    results=pd.concat(parts,ignore_index=True)
    baseline=read(src/'results.csv.gz');baseline=baseline[baseline['mode']=='causal'].copy()
    baseline['policy']='baseline';baseline['path']='cached'
    results=pd.concat([results,baseline],ignore_index=True)
    results.to_csv(dest/'results.csv.gz',index=False)
    summary=[];years=[];deltas=[]
    for (lane,policy,path),group in results.groupby(['lane','policy','path']):
        s=group[group.population=='signal'];r=group[group.population=='random']
        summary.append(dict(symbol=symbol,lane=int(lane),policy=policy,path=path,**score(s),random=score(r)))
        for period,g in s.groupby('segment'):
            years.append(dict(symbol=symbol,lane=int(lane),policy=policy,path=path,period=int(period),
                **score(g),random=score(r[r.segment==period])))
        if policy!='baseline':
            b=baseline[(baseline.lane==lane)&(baseline.population=='signal')]
            joined=s.merge(b[['event_id','net_r']],on='event_id',suffixes=('','_baseline'),validate='one_to_one')
            deltas.append(dict(symbol=symbol,lane=int(lane),policy=policy,path=path,
                improved=int((joined.net_r>joined.net_r_baseline+1e-7).sum()),
                worsened=int((joined.net_r<joined.net_r_baseline-1e-7).sum()),
                marked_delta_r=float((joined.net_r-joined.net_r_baseline).sum())))
    # Conservative independent-trade endpoint-order envelope. It is a bound,
    # not a claim that simultaneous long/short positions saw different markets.
    bounds=[]
    for (lane,policy),group in results[results.policy!='baseline'].groupby(['lane','policy']):
        s=group[group.population=='signal'];r=group[group.population=='random']
        identity=['population','event_id']
        low=group.loc[group.groupby(identity).net_r.idxmin()];high=group.loc[group.groupby(identity).net_r.idxmax()]
        a=s[s.path=='OHLC'].set_index('event_id');b=s[s.path=='OLHC'].set_index('event_id').loc[a.index]
        bounds.append(dict(symbol=symbol,lane=int(lane),policy=policy,
            different=int((abs(a.net_r-b.net_r)>1e-7).sum()),
            lower=score(low[low.population=='signal']),upper=score(high[high.population=='signal']),
            random_lower=score(low[low.population=='random']),random_upper=score(high[high.population=='random'])))
    dump(dest/'summary.json',dict(summary=summary,years=years,deltas=deltas,bounds=bounds,checks=checks))
    print(symbol,'validated',sum(c['oracle_paths'] for c in checks),'paths',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run','analyze','summary']);p.add_argument('--symbol',choices=['BTC','ETH']);a=p.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and config before generating results')
    if a.stage=='summary':
        parts=[json.loads((EXP/s/'summary.json').read_text()) for s in ['BTC','ETH']]
        dump(EXP/'summary.json',dict(config=json.loads(CONFIG.read_text()),
            aliases={'tp1_be1':'tp1'},**{k:sum([p[k] for p in parts],[]) for k in ['summary','years','deltas','bounds','checks']}));return
    assert a.symbol
    if a.stage=='prepare':prepare(a.symbol)
    elif a.stage=='run':run(EXP/a.symbol/'native.json')
    else:analyze(a.symbol)


if __name__=='__main__':main()
