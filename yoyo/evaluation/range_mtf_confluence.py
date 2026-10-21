"""Intersect frozen key entries with causal H1-watch / 15m-arrow entries.

Sources: V2.30/V2.31 txObserve, txEligible and txEngine; completed5m OHLC
aggregated to15m/H1 by the existing preparer. Every H1 arrow refreshes the
watch before same-close eligibility, so strictly later15m arrows qualify.
No candle after the original key-entry close enters admission or initialSL.
Reuse18 existing ledgers. Screenshot exit management uses the existing
Nautilus adapter, with matched-control relative risk preserved. No Pine edit.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import prepare,aggregate,stop_events,replay
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation.range_entry_clock import watched_entries
from yoyo.evaluation.range_morphology import read,wilson,association,cluster_design
from yoyo.evaluation.range_management_study import holm

CONFIG=ROOT/'experiments/active/exp-range-v231-mtf-confluence-20261021-v1/config.json'


def watch_state(hour):
    """Latest completed H1 arrow at each5m close, including a refresh now."""
    side=np.zeros(len(hour),int);since=np.full(len(hour),-1,int)
    active,at=0,-1
    for i,h in enumerate(hour):
        if h: active,at=int(h),i
        side[i],since[i]=active,at
    return side,since


def gate_rows(ctx,matches,candidates):
    """Admission reads only H1/15m closes through the frozen entry timestamp."""
    frame=ctx['frame'];q=aggregate(frame,15)
    sides,since=watch_state(ctx['hour'])
    mirrored,observed=watched_entries(ctx['hour'],ctx['quarter'])
    np.testing.assert_array_equal(mirrored,ctx['signal'])
    np.testing.assert_array_equal(observed,ctx['observed'])
    base=candidates[candidates.population=='signal'].set_index('pair_id')
    records=[]
    for r in matches.itertuples():
        e=int(base.loc[r.pair_id,'entry_i'])
        a=int(frame.index.get_indexer([q.index[int(r.signal_i)]])[0])
        assert 0<=a<=e and frame.index[e]==q.index[int(r.entry_i)]
        assert int(ctx['quarter'][a])==int(r.side)
        assert abs(frame.close.iloc[e]-r.entry)<1e-8
        now=int(ctx['signal'][e])==r.side
        arrow=int(ctx['signal'][a])==r.side
        if now: assert ctx['quarter'][e]==r.side and sides[e]==r.side and since[e]<e
        records.append(dict(pair_id=int(r.pair_id),side=int(r.side),entry_i=e,arrow_i=a,
            entry_time=frame.index[e].isoformat(),arrow_close=frame.index[a].isoformat(),
            key=r.key,level=float(r.level),strict_both_now=bool(now),paired_arrow_was_mtf=bool(arrow),
            same_arrow_and_entry=a==e,entry_watch_side=int(sides[e]),
            entry_watch_close=frame.index[since[e]].isoformat() if since[e]>=0 else None,
            entry_has_fresh_hour=bool(ctx['hour'][e]),entry_has_same_arrow=bool(ctx['quarter'][e]==r.side)))
    return pd.DataFrame(records)


def screenshot_candidates(ctx,base,tick,cfg):
    """SignalSL uses five completed5m bars; controls inherit its relative risk."""
    signal=base[base.population=='signal'].copy()
    signal,_=stop_events(ctx,signal,cfg['stop_window_5m'],cfg['buffer_percent'],tick)
    assert len(signal)==int((base.population=='signal').sum())
    random=base[base.population=='random'].copy().set_index('pair_id')
    signal=signal.set_index('pair_id')
    assert set(signal.index)==set(random.index)
    random=random.loc[signal.index].copy()
    distance=signal.risk/signal.entry*random.entry
    raw=(random.entry-random.side*distance)/tick
    random['stop']=np.where(random.side==1,np.floor(raw+1e-8),np.ceil(raw-1e-8))*tick
    random['risk']=random.side*(random.entry-random.stop)
    assert (random.risk>0).all() and (random.stop>0).all()
    assert (random.risk-distance>=-tick*1e-7).all() and (random.risk-distance<tick+tick*1e-7).all()
    return pd.concat([signal.reset_index(),random.reset_index()]).sort_values(['pair_id','population']).reset_index(drop=True)


def summarize(rows,ids):
    part=rows[rows.pair_id.isin(ids)]
    s=part[part.population=='signal'].set_index('pair_id').sort_index()
    r=part[part.population=='random'].set_index('pair_id').sort_index()
    assert set(s.index)==set(r.index)==set(ids)
    if s.empty: return dict(n=0,wins=0,win_rate=None,net_r=0.,random_net_r=0.)
    n=len(s);wins=int((s.net_r>1e-9).sum())
    boundary=s.reason==0 if 'reason' in s else s.boundary_weight>0
    return dict(n=n,wins=wins,win_rate=wins/n,win_wilson95=wilson(wins,n),
        gross_wins=int((s.gross_r>1e-9).sum()),net_r=float(s.net_r.sum()),gross_r=float(s.gross_r.sum()),
        random_wins=int((r.net_r>1e-9).sum()),random_net_r=float(r.net_r.sum()),
        mean_bp=float(s.net_notional_bp.mean()),random_mean_bp=float(r.net_notional_bp.mean()),
        mean_r=float(s.net_r.mean()),median_risk_bp=float((s.side*(s.entry-s.stop)/s.entry*10000).median()),
        mean_fee_r=float(s.fee_r.mean()),boundary_n=int(boundary.sum()),
        boundary_net_r=float(s.loc[boundary,'net_r'].sum()),
        without_largest=float(s.net_r.sum()-s.net_r.max()),
        median_hold_minutes=float(((s.exit_i-s.entry_i)*5).median()) if 'exit_i' in s else None)


def oracle_check(ctx,base,rows,tick,cfg):
    """Existing independent C++ oracle checks all newly executed paths."""
    ordered=base.sort_values(['pair_id','population'])
    matrix=ordered[['entry_i','side','entry','stop','end_i']].to_numpy(float)
    expected=replay(ctx,matrix,arm=cfg['trail_arm_r'],multiple=cfg['trail_atr_multiple'],tick=tick)
    actual=rows.sort_values(['pair_id','population'])
    for field,col in [('exit_i',0),('reason',2),('arm_i',4),('steps',7)]:
        np.testing.assert_array_equal(actual[field],expected[:,col],err_msg=field)
    for field,col in [('exit_price',1),('protection',5)]:
        np.testing.assert_allclose(actual[field],expected[:,col],rtol=0,atol=tick*1e-7,err_msg=field)
    gross=actual.side*(actual.exit_price-actual.entry)/(actual.side*(actual.entry-actual.stop))
    fee=cfg['round_trip_cost']*actual.entry/(actual.side*(actual.entry-actual.stop))
    np.testing.assert_allclose(actual.net_r,gross-fee,rtol=0,atol=1e-7)
    np.testing.assert_allclose(actual.framework_commission,actual.quantity*actual.entry*cfg['round_trip_cost'],rtol=0,atol=2e-8)
    return dict(native_paths=len(rows),oracle_paths=len(rows),fills_expected=2*len(rows))


def main():
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    if (dest/'summary.json').exists():
        print('Reuse completed '+str(dest/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before research')
    # Both screenshot and current versions have the identical MTF execution block.
    pines=[(ROOT/f'yoyo/evaluation/pine/monday_range_v2_{v}.pine').read_text() for v in [30,31]]
    blocks=[p[p.index('type TxTrade'):p.index('type TxDrawing')] for p in pines]
    assert blocks[0]==blocks[1]
    result=[];tests=[];checks=[];sources=[];admissions=[];stability=[]
    original=json.loads((ROOT/cfg['source']/'summary.json').read_text())
    arms=list(dict.fromkeys(r['arm'] for r in original['rows']))
    for spec in cfg['symbols']:
        coin=spec['symbol'];tick=spec['tick'];folder=dest/coin;folder.mkdir(exist_ok=True)
        source=ROOT/cfg['source']/coin;data=ROOT/cfg['candles']/coin/'candles.csv.gz'
        base=read(source/'candidates.csv');matches=read(source/'signal_matches.csv')
        assert len(matches)==spec['expected'] and len(base)==2*len(matches)
        five=read(data);five.index=pd.DatetimeIndex(pd.to_datetime(five.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
        assert np.all(np.diff(five.index.asi8)==pd.Timedelta(minutes=5).value)
        ctx=prepare(five);gates=gate_rows(ctx,matches,base)
        prefix_checks=0
        for idx in sorted(set(np.linspace(0,len(gates)-1,6,dtype=int))):
            row=gates.iloc[idx];pid=int(row.pair_id);end=int(row.entry_i)+1
            expected=gates[gates.pair_id==pid].reset_index(drop=True)
            got=gate_rows(prepare(five.iloc[:end]),matches[matches.pair_id==pid],base[base.pair_id==pid])
            pd.testing.assert_frame_equal(expected,got);prefix_checks+=1
        newbase=screenshot_candidates(ctx,base,tick,cfg)
        gates.to_csv(folder/'admission.csv',index=False)
        candidate_path=folder/'screenshot_candidates.csv'
        if candidate_path.exists():
            pd.testing.assert_frame_equal(read(candidate_path),newbase,check_dtype=False)
        else:
            newbase.to_csv(candidate_path,index=False)
        native=dict(schema=1,experiment_id=cfg['experiment_id'],engine='nautilus',symbol=coin,
            tick=tick,market_tick=tick,entry_mode='provided',data=str(data.relative_to(ROOT)),
            entry_candidates=str((folder/'screenshot_candidates.csv').relative_to(ROOT)),
            stop_window_5m=5,buffer_percent=.05,maximum_risk_percent=0,round_trip_cost=cfg['round_trip_cost'],
            policies=[dict(name='screenshot',trail_arm_r=5,trail_atr_multiple=4,opposite_hour_exit=True)],
            output=str((folder/'native').relative_to(ROOT)))
        dump(folder/'native.json',native)
        subprocess.run([str(ROOT/'scripts/backtest-range'),'--config',str(folder/'native.json')],check=True,cwd=ROOT)
        native_rows=read(folder/'native/screenshot/trades.csv')
        check=oracle_check(ctx,newbase,native_rows,tick,cfg)
        check.update(symbol=coin,prefix_checks=prefix_checks,
            strict_n=int(gates.strict_both_now.sum()),paired_arrow_mtf_n=int(gates.paired_arrow_was_mtf.sum()))
        checks.append(check)
        admissions.extend(dict(symbol=coin,**r) for r in gates.to_dict('records'))
        times=pd.to_datetime(gates.entry_time,utc=True)
        periods=dict(all=np.ones(len(gates),bool),first_half=times<pd.Timestamp(cfg['midpoint']),
            second_half=times>=pd.Timestamp(cfg['midpoint']),recent2m=times>=pd.Timestamp(cfg['recent_start']))
        for arm in [*arms,'screenshot']:
            path=folder/'native/screenshot/trades.csv' if arm=='screenshot' else source/arm/'trades.csv'
            rows=read(path);sources.append(file_stamp(path))
            for period,valid in periods.items():
                g=gates[valid]
                groups=dict(all=g.pair_id.tolist(),both=g.loc[g.strict_both_now,'pair_id'].tolist(),
                    excluded=g.loc[~g.strict_both_now,'pair_id'].tolist())
                stats={name:summarize(rows,ids) for name,ids in groups.items()}
                for col in ['n','wins','net_r','random_net_r']:
                    np.testing.assert_allclose(stats['both'][col]+stats['excluded'][col],stats['all'][col],rtol=0,atol=1e-7)
                if period=='all' and arm!='screenshot':
                    prior=next(r for r in original['rows'] if r['symbol']==coin and r['version']=='v231' and r['group']=='all' and r['arm']==arm)
                    for col in ['n','wins','net_r','random_net_r']:
                        np.testing.assert_allclose(stats['all'][col],prior[col],rtol=0,atol=1e-7)
                result.extend(dict(symbol=coin,arm=arm,period=period,group=name,**stat) for name,stat in stats.items())
            if arm=='pivot':
                s=rows[rows.population=='signal'].set_index('pair_id').loc[gates.pair_id]
                local=pd.to_datetime(gates.entry_time,utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
                weeks=(local.dt.normalize()-pd.to_timedelta(local.dt.dayofweek,unit='D')).dt.strftime('%Y-%m-%d')
                d=pd.DataFrame(dict(net_win=(s.net_r.to_numpy()>1e-9).astype(int),week=weeks,
                    side=gates.side,risk_bp=s.side.to_numpy()*(s.entry-s.stop).to_numpy()/s.entry.to_numpy()*10000,
                    entry_time=gates.entry_time))
                tests.append(dict(symbol=coin,**association(d,gates.strict_both_now,cluster_design(d,cfg),cfg)))
        for filename in ['signal_matches.csv','candidates.csv']:sources.append(file_stamp(source/filename))
        sources.append(file_stamp(data))
        print(json.dumps(check),flush=True)
    for r,p in zip(tests,holm([r['wild_p'] for r in tests])):r['holm_p']=float(p)
    for coin in ['BTC','ETH']:
        same=[r for r in result if r['symbol']==coin and r['period']=='all' and r['group']=='both' and r['arm']!='screenshot']
        base_stats={r['arm']:r for r in result if r['symbol']==coin and r['period']=='all' and r['group']=='all'}
        stability.append(dict(symbol=coin,higher_win_arms=sum(r['win_rate'] is not None and r['win_rate']>base_stats[r['arm']]['win_rate'] for r in same),
            total_arms=len(same),positive_net_arms=sum(r['net_r']>0 for r in same)))
    for stamp in sources:assert stamp==file_stamp(stamp['path'])
    pd.DataFrame(result).to_csv(dest/'comparison.csv',index=False)
    dump(dest/'summary.json',dict(config=cfg,rows=result,primary_tests=tests,stability=stability,checks=checks,
        sources=sources,mtf_pine_versions_equal=True,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        limitations='Historical selected cohort; same entry times. Shared15m oscillator means modules are dependent. Controls are conditional paired backgrounds, not MTF-constrained candles. Exact tick stops and frozen20bp costs; no funding/slippage/account compounding. Final boundary marks are not natural exits. No Pine/default change.'))
    print(json.dumps([r for r in result if r['arm'] in ['pivot','screenshot'] and r['period'] in ['all','recent2m'] and r['group']!='excluded'],indent=2),flush=True)


if __name__=='__main__':main()
