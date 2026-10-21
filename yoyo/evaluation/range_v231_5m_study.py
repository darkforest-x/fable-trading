"""Owner-requested full 5m version of V2.31 versus its frozen 15m results.

Inputs: existing completed 5m OHLC, same NY Monday range/Fib, Stoch5/3/3,
five prior signal-timeframe bodies, +/-2 touch bars, >=10% wick penetration,
inclusive midpoint-side gate and Tue-Fri entry close. Exact touch extreme
stops; each signal remains an independent position. 5m 2/2 pivots execute
only at confirmation close. Optional exits reuse the same17 approved policies.
Controls use same coin/side/week/half/5m ATR14-to-close bucket without future
returns. No optimization, data download, funding or account compounding.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT,dump,file_stamp,run
from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.monday_range_v2_2_reference import signals
from yoyo.evaluation.monday_range_v2_31_reference import select_matches
from yoyo.evaluation.pepe_range_exits import aggregate
from yoyo.evaluation.range_v221_compare import controls,candidate_rows
from yoyo.evaluation.range_v231_midpoint import exact_stops
from yoyo.evaluation.range_v230_exit_compare import run_symbol
from yoyo.evaluation.range_v231_returns import describe
from yoyo.evaluation.range_management_study import holm

DEFAULT=ROOT/'experiments/active/exp-range-v231-5m-20261021-v1/config.json'
CODE=['yoyo/evaluation/range_v231_5m_study.py','yoyo/evaluation/btc_range_wick_fanshen.py',
      'yoyo/evaluation/monday_range_v2_31_reference.py','yoyo/evaluation/range_v230_exit_compare.py',
      'yoyo/layers/l3_backtest/nautilus_independent.py']


def prepare(cfg,spec,dest):
    symbol=spec['symbol'];folder=dest/symbol
    receipt=folder/'preparation.json'
    source=ROOT/cfg['source']/symbol
    native=json.loads((source/'native.json').read_text())
    identity=dict(config=cfg,spec=spec,data=file_stamp(ROOT/native['data']),
                  code=[file_stamp(ROOT/p) for p in CODE])
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if old['identity']!=identity:raise RuntimeError('Preparation changed; use a new output directory')
        return old
    five=pd.read_csv(ROOT/native['data'],float_precision='round_trip')
    five.index=pd.DatetimeIndex(pd.to_datetime(five.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    assert np.all(np.diff(five.index.asi8)==pd.Timedelta(minutes=5).value)
    kwargs=dict(before=5,wait_bars=2,penetration=.1,buffer=.002,tick=spec['tick'],
                timezone=cfg['timezone'],start=cfg['start'])
    # Verify that adding explicit clocks did not change the existing 15m oracle.
    q=aggregate(five,15);g,n,w,developing=monday_levels(q,cfg['ratios'],cfg['timezone'])
    baseline=exact_stops(select_matches(q,signals(q).to_numpy(),g,n,w,**kwargs),q,spec['tick'],cfg['end'])
    frozen=pd.read_csv(ROOT/cfg['admissions_15m']/f'{symbol}_signal_matches.csv',float_precision='round_trip')
    pd.testing.assert_frame_equal(pd.DataFrame(baseline)[frozen.columns],frozen,check_dtype=False,rtol=0,atol=1e-10)
    grid,names,weeks,_=monday_levels(five,cfg['ratios'],cfg['timezone'],bar_minutes=5)
    arrows=signals(five).to_numpy()
    selected=select_matches(five,arrows,grid,names,weeks,bar_minutes=5,**kwargs)
    records=[dict(r,pair_id=i) for i,r in enumerate(exact_stops(selected,five,spec['tick'],cfg['end']))]
    ends=sorted({r['entry_i']+d for r in records[::max(1,len(records)//8)] for d in [0,1] if r['entry_i']+d>0})
    for end in ends:
        got=select_matches(five.iloc[:end],arrows[:end],grid[:end],names,weeks[:end],bar_minutes=5,**kwargs)
        assert got==[r for r in selected if r['entry_i']<end]
    for row in records:
        j=row['touch_i'];e=row['entry_i'];mid=grid[j,0]+.5*(grid[j,1]-grid[j,0])
        assert row['level']<=mid if row['side']==1 else row['level']>=mid
        assert pd.Timestamp(row['entry_time'])==five.index[e]
        assert pd.Timestamp(row['touch_time'])==five.index[j]-pd.Timedelta(minutes=5)
    # On completed, nondeveloping ranges, the two source resolutions agree.
    aligned=five.index.get_indexer(q.index)
    valid=(~pd.isna(w))&np.isfinite(g[:,0])&~developing
    np.testing.assert_allclose(grid[aligned[valid],:2],g[valid,:2],rtol=0,atol=1e-8)
    matched,half,week,bucket=controls(five,arrows,records,cfg,spec)
    candidates=candidate_rows(five,five,records,matched,(half,week,bucket),spec['tick'])
    assert len(set(matched.values()))==len(matched)
    folder.mkdir(parents=True,exist_ok=False)
    pd.DataFrame(records).to_csv(folder/'signal_matches.csv',index=False)
    candidates.to_csv(folder/'candidates.csv',index=False)
    native.update(experiment_id=cfg['experiment_id'],management_minutes=5,
                  entry_candidates=str((folder/'candidates.csv').relative_to(ROOT)),
                  policies=[dict(name='pivot',confirmed_pivot_exit=True,pivot_minutes=5,opposite_hour_exit=False),
                            dict(name='strict_ties',confirmed_pivot_exit=True,pivot_minutes=5,strict_pivot_ties=True,opposite_hour_exit=False)],
                  output=str((folder/'native').relative_to(ROOT)))
    dump(folder/'native.json',native)
    result=dict(identity=identity,n=len(records),longs=sum(r['side']==1 for r in records),
                shorts=sum(r['side']==-1 for r in records),prefix_checks=len(ends),
                unchanged_15m_baseline=len(baseline),quarter_rows=len(q),five_rows=len(five),
                source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(receipt,result)
    print(json.dumps({k:v for k,v in result.items() if k!='identity'}),flush=True)
    return result


def analyze(cfg,dest,approved):
    old=json.loads((ROOT/cfg['comparison_15m']/'summary.json').read_text())
    results=[dict(r,timeframe=15) for r in old['rows'] if r['version']=='v231']
    for spec in cfg['symbols']:
        symbol=spec['symbol']
        for arm in ['pivot',*[a['name'] for a in approved['arms']]]:
            folder=dest/symbol/'native/pivot' if arm=='pivot' else dest/'exits'/symbol/arm
            rows=pd.read_csv(folder/'trades.csv',float_precision='round_trip')
            lower=pd.read_csv(dest/symbol/'native/strict_ties/trades.csv' if arm=='pivot' else folder/'stop_first.csv',float_precision='round_trip')
            for group,part in [('all',rows),('first_half',rows[rows.segment==0]),('second_half',rows[rows.segment==1])]:
                r=describe(part,symbol,arm,'v231_5m',group,cfg['seed'])
                r['timeframe']=5
                if group=='all':
                    s=part[part.population=='signal']
                    r['sensitivity_net_r']=float(lower[lower.population=='signal'].net_r.sum())
                    r['boundary_n']=int((s.reason==0).sum()) if arm=='pivot' else int((s.boundary_weight>0).sum())
                results.append(r)
    for symbol in [s['symbol'] for s in cfg['symbols']]:
        family=[r for r in results if r['symbol']==symbol and r['group']=='all']
        for r,p,b in zip(family,holm([r['p_one_sided'] for r in family]),holm([r['bp_excess_p'] for r in family])):
            r['holm_p_timeframe_arms']=float(p);r['bp_holm_p_timeframe_arms']=float(b)
    dump(dest/'summary.json',dict(config=cfg,rows=results,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(json.dumps([r for r in results if r['arm']=='pivot' and r['group']=='all'],indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();cfg=json.loads(DEFAULT.read_text());dest=DEFAULT.parent
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,str(DEFAULT)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and config before generation')
    metadata={s['symbol']:prepare(cfg,s,dest) for s in cfg['symbols']}
    if args.prepare_only:return
    approved=json.loads((ROOT/cfg['exit_reference']/'config.json').read_text())
    exit_cfg=dict(approved,experiment_id=cfg['experiment_id'],source=str(dest.relative_to(ROOT)),key_minutes=5)
    for spec in cfg['symbols']:
        run(dest/spec['symbol']/'native.json')
        run_symbol(exit_cfg,dict(spec,expected_signals=metadata[spec['symbol']]['n']),dest/'exits')
    analyze(cfg,dest,approved)


if __name__=='__main__':main()
