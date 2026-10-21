"""Native nearest-key partial realization plus an entry-relative90minute cap.

Reuse the completed BTC15m cohort, initial stops, frozen known-at-entry Monday
levels,20bp costs, native half-leg execution/aggregation and price-time oracle.
Compare cap alone first, then add50percent at the nearest favorable key.
No pivot filter, breakeven, future key repricing or inferred daily boundary.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v230_exit_compare import entry_keys, make_legs, aggregate_legs, oracle
from yoyo.evaluation.range_v231_returns import read, describe
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.pepe_range_breakeven import native
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

CONFIG=ROOT/'experiments/active/exp-btc-v231-key90m-20261021-v1/config.json'


def capped_legs(base,keys,tick,minutes=90,partial=True):
    """Only entry metadata and its known keys; cap is18completed5m bars."""
    if minutes<=0 or minutes%5:
        raise ValueError('Time cap must be whole positive5m bars')
    capped=base.copy()
    capped['data_end_i']=capped.end_i
    capped['time_cap_i']=capped.entry_i+minutes//5
    capped['end_i']=np.minimum(capped.end_i,capped.time_cap_i)
    legs=make_legs(capped,dict(key_tp=True),keys,tick)
    legs.loc[legs.leg==1,'target_price']=np.nan
    if not partial:
        legs=legs[legs.leg==0].copy()
        legs['weight']=1.
        legs['target_price']=np.nan
    return capped,legs


def main():
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    if (dest/'summary.json').exists():
        print('Completed result exists: '+str(dest/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG),
            'yoyo/layers/l3_backtest/nautilus_independent.py'],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and configuration before generation')
    assert cfg['partial_fraction']==.5 and cfg['round_trip_cost']==.002
    source=ROOT/cfg['source'];base=read(source/'candidates.csv')
    base['trade_id']=base.population+'-'+base.pair_id.astype(str)
    base['risk']=base.side*(base.entry-base.stop)
    assert len(base)==30 and (base.population=='signal').sum()==15
    frame=read(ROOT/cfg['data'])
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    frame=frame[['open','high','low','close']]
    keys=entry_keys(frame,base,cfg['ratios'],cfg['timezone'])
    ctx=dict(bars=np.ascontiguousarray(frame.to_numpy(float)),atr=np.ones(len(frame)),hour=np.zeros(len(frame)))
    library=native();results=[];checks=[]
    for arm in cfg['arms']:
        folder=dest/arm
        if folder.exists():
            raise RuntimeError('Preserve existing output: '+str(folder))
        capped,legs=capped_legs(base,keys,cfg['tick'],cfg['max_hold_minutes'],arm.startswith('key'))
        policy=ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=cfg['round_trip_cost'])
        rows,fills,audit=run_native(frame,ctx['atr'],ctx['hour'],legs,policy,tick=cfg['tick'],symbol=cfg['symbol'])
        conservative,check=oracle(ctx,legs,{},cfg['tick'],cfg['round_trip_cost'],library,rows)
        parents=aggregate_legs(rows,capped,cfg['round_trip_cost'])
        lower=aggregate_legs(conservative,capped,cfg['round_trip_cost'])
        assert (rows.exit_i-rows.entry_i<=18).all()
        np.testing.assert_array_equal(parents.sort_values('trade_id').entry_i,base.sort_values('trade_id').entry_i)
        desc=describe(parents,cfg['symbol'],arm,'v231_key90m','all',cfg['seed'])
        signal=rows[rows.population=='signal']
        ps=parents[parents.population=='signal'];rs=lower[lower.population=='signal']
        cap=signal[(signal.reason==0)&(signal.exit_i==signal.time_cap_i)]
        boundary=signal[(signal.reason==0)&(signal.exit_i<signal.time_cap_i)]
        best=ps.net_r.idxmax()
        desc.update(sensitivity_net_r=float(rs.net_r.sum()),
            key_half_fills=int((signal.reason==4).sum()),time_cap_trades=int(cap.parent_id.nunique()),
            data_boundary_trades=int(boundary.parent_id.nunique()),
            median_hold_minutes=float(((ps.exit_i-ps.entry_i)*5).median()),
            largest_pair=int(ps.loc[best,'pair_id']),largest_net_r=float(ps.loc[best,'net_r']),
            net_r_without_largest=float(ps.net_r.sum()-ps.loc[best,'net_r']))
        folder.mkdir()
        legs.to_csv(folder/'legs.csv',index=False)
        rows.to_csv(folder/'native_trades.csv',index=False)
        fills.to_csv(folder/'fills.csv',index=False)
        parents.to_csv(folder/'trades.csv',index=False)
        lower.to_csv(folder/'stop_first.csv',index=False)
        dump(folder/'audit.json',dict(native=audit,oracle=check))
        checks.append(dict(arm=arm,**check));results.append(desc)
        print(json.dumps({k:desc[k] for k in ['arm','wins','net_r','random_net_r','mean_net_bp','key_half_fills','time_cap_trades']}),flush=True)
    for r,p in zip(results,holm([r['p_one_sided'] for r in results])):
        r['holm_p_two_arms']=float(p)
    dump(dest/'summary.json',dict(config=cfg,rows=results,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs=[file_stamp(ROOT/cfg['data']),file_stamp(source/'candidates.csv')],
        limitations='Known15signal sample, not account returns; no funding/spread/depth; native5m O-H-L-C plus stop-first sensitivity. Time cap exits are scheduled exits, separate from data-boundary marks. No Pine change.'))
    dump(dest/'verification.json',dict(checks=checks,all_parent_entries_unchanged=True,max_hold_minutes=90))


if __name__=='__main__': main()
