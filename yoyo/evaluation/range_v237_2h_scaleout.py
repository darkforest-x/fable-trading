"""Two-hour conditional excursions and static scale-outs on causal V2.37.

No entry features are changed. Conditional diagnostics inspect future OHLC
from entry_i+1 through entry_i+24 or initial stop, exclusively as outcomes.
High/low ordering is unknown, so both explicit OHLC paths are retained.
Native Nautilus fills, not this diagnostic, supply all strategy payoffs.
Static tranches share the original stop/deadline and proportional costs;
their original-size weighted returns obey linearity, not a new fill model.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v237_twoyear import close_frame, read

EXP = ROOT/'experiments/active/exp-range-v237-2h-scaleout-20261022-v1'
CONFIG = EXP/'config.json'
EPS = 1e-7


def trajectory(bars, entry_i, end_i, side, entry, risk, order):
    """Ordered gross R outcomes with threshold knots and discrete opening gaps.

    Original SL is -1R. Stop at its first touch; continuous legs terminate
    exactly at -1, gaps at the observed open. No entry-candle extremes.
    Returned candle indices describe intervals, not known tick timestamps.
    """
    values, indices = [0.], [entry_i]
    levels = [-1., 0., .5, 1., 2., 3., 5.]
    for i in range(int(entry_i)+1, int(end_i)+1):
        for phase, col in enumerate([0,1,2,3] if order=='OHLC' else [0,2,1,3]):
            point = float(side*(bars[i,col]-entry)/risk)
            if phase == 0:
                values.append(point); indices.append(i)
                if point <= -1+EPS:
                    return np.asarray(values), np.asarray(indices), True
                continue
            previous = values[-1]
            stopped = point <= -1+EPS
            endpoint = -1. if stopped else point
            between = [x for x in levels if min(previous,endpoint)<x<max(previous,endpoint)]
            for knot in sorted(between, reverse=endpoint<previous):
                values.append(knot); indices.append(i)
            values.append(endpoint); indices.append(i)
            if stopped:
                return np.asarray(values), np.asarray(indices), True
    return np.asarray(values), np.asarray(indices), False


def conditional_rows(bars, base, path):
    """Condition on first touch; keep later return order distinct from drawdown."""
    out=[]
    for row in base.itertuples():
        values, indices, stopped = trajectory(bars,row.entry_i,row.end_i,row.side,row.entry,row.risk,path)
        for trigger in [1,2]:
            hits=np.flatnonzero(values>=trigger-EPS)
            record=dict(pair_id=row.pair_id,path=path,trigger=trigger,reached=bool(len(hits)),
                initial_stop_hit=stopped,whole_mfe_r=max(0.,float(values.max())))
            if len(hits):
                start=int(hits[0]); post=values[start:]
                peak_at=int(np.argmax(post)); until_peak=post[:peak_at+1]
                zeros=np.flatnonzero(post<=EPS)
                ret=int(zeros[0]) if len(zeros) else len(post)+1
                record.update(hit_i=int(indices[start]),minutes_to_touch_bar_close=int((indices[start]-row.entry_i)*5),
                    peak_r=float(post.max()),extra_r=float(post.max()-trigger),minimum_r=float(post.min()),
                    trigger_pullback_r=float(max(0.,trigger-post.min())),
                    max_drawdown_r=float((np.maximum.accumulate(post)-post).max()),
                    pullback_before_peak_r=float(max(0.,trigger-until_peak.min())),
                    returned_entry=bool(len(zeros)),returned_half_trigger=bool((post<=trigger/2+EPS).any()))
                for target in [2,3,5]:
                    if target<=trigger:continue
                    reached=np.flatnonzero(post>=target-EPS)
                    first=int(reached[0]) if len(reached) else len(post)+1
                    record.update({f'next{target}':bool(len(reached)),
                        f'next{target}_before_entry':bool(len(reached) and first<ret),
                        f'next{target}_after_entry_return':bool(len(reached) and first>ret),
                        f'pullback_before_next{target}_r':float(max(0.,trigger-post[:first+1].min())) if len(reached) else np.nan})
            out.append(record)
    return pd.DataFrame(out)


def load(symbol):
    cfg=json.loads(CONFIG.read_text());source=ROOT/cfg['source']
    old_cfg=json.loads((source/'config.json').read_text())
    price_root=ROOT/old_cfg['source']
    frame=close_frame(read(price_root/symbol/'candles.csv.gz'))
    base,occ=[read(source/symbol/name) for name in ['candidates.csv','occurrences.csv']]
    assert occ['mode'].eq('causal').all() and base.pair_id.is_unique
    for col in ['entry_i','end_i','entry','stop','risk','side']:
        np.testing.assert_array_equal(occ[col],occ.pair_id.map(base.set_index('pair_id')[col]))
    base['end_i']=base.entry_i+cfg['hold_minutes']//5
    assert (base.end_i < len(frame)).all(), 'Missing full 2h windows; do not shorten them'
    base['trade_id']=base.population+'-'+base.pair_id.astype(str)
    base['target_price']=np.nan
    np.testing.assert_array_equal((frame.index[base.end_i]-frame.index[base.entry_i]).total_seconds(),np.full(len(base),7200.))
    stamps=[file_stamp(p) for p in [CONFIG,source/symbol/'candidates.csv',source/symbol/'occurrences.csv',price_root/symbol/'candles.csv.gz']]
    return cfg,source,frame,base,occ,stamps


def run_symbol(symbol):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    from yoyo.evaluation.range_v237_fixed_exits import oracle
    from yoyo.evaluation.btc_range_wick_review import excursions
    cfg,source,frame,base,occ,stamps=load(symbol)
    tick=next(s['tick'] for s in cfg['symbols'] if s['symbol']==symbol)
    folder=EXP/symbol;folder.mkdir(exist_ok=True)
    bars=frame.to_numpy(); zeros=np.zeros(len(frame));checks=[]
    # Native no-TP stop/deadline exits do not depend on high/low ordering.
    variants=[(None,'OHLC')]+[(t,path) for path in cfg['paths'] for t in cfg['targets']]
    pieces=[]
    for target,path in variants:
        name='hold' if target is None else f'tp{target}_{path}'
        dest=folder/name;dest.mkdir(exist_ok=True)
        identity=dict(inputs=stamps,target=target,path=path,
            code=[file_stamp(ROOT/p) for p in ['yoyo/evaluation/range_v237_2h_scaleout.py',
                'yoyo/layers/l3_backtest/nautilus_independent.py']])
        if (dest/'receipt.json').exists():
            saved=json.loads((dest/'receipt.json').read_text());assert saved['identity']==identity
            result=read(dest/'trades.csv.gz');check=saved['check']
        else:
            cached=pd.DataFrame();pending=base
            if target in [1,2,3]:
                cached=read(source/symbol/'native'/name/'trades.csv')
                cached=cached[cached.exit_i<=cached.entry_i+24].copy()
                assert cached.reason.ne(0).all()
                cached['end_i']=cached.entry_i+24
                pending=base[~base.pair_id.isin(cached.pair_id)]
            policy=ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=cfg['cost']) if target is None else ExitPolicy(
                fixed_target_r=target,intrabar_path=path,opposite_hour_exit=False,round_trip_cost=cfg['cost'])
            native,fills,audit=run_native(frame,zeros,zeros,pending,policy,tick=tick,symbol=symbol)
            native.to_csv(dest/'new_native.csv.gz',index=False);fills.to_csv(dest/'fills.csv.gz',index=False)
            result=pd.concat([cached,native],ignore_index=True).sort_values('pair_id')
            ordered=base.sort_values('pair_id')
            np.testing.assert_array_equal(result.pair_id,ordered.pair_id)
            for col in ['entry_i','end_i','side','entry','stop','risk']:
                np.testing.assert_array_equal(result[col],ordered[col])
            if target is not None:
                expected=oracle(bars,ordered,target,False,path,tick)
                np.testing.assert_array_equal(result.exit_i,expected[:,0]);np.testing.assert_array_equal(result.reason,expected[:,2])
                np.testing.assert_allclose(result.exit_price,expected[:,1],atol=tick*1e-7,rtol=0)
            else:
                ex=excursions(bars,ordered)
                ix=np.where(ex.initial_stop_hit,ex.stop_i,ordered.end_i).astype(int)
                opens=bars[ix,0];gap=ordered.side.to_numpy()*(opens-ordered.stop.to_numpy())<=0
                prices=np.where(ex.initial_stop_hit,np.where(gap,opens,ordered.stop),bars[ix,3])
                np.testing.assert_array_equal(result.exit_i,ix)
                np.testing.assert_array_equal(result.reason,np.where(ex.initial_stop_hit,1,0))
                np.testing.assert_allclose(result.exit_price,prices,atol=tick*1e-7,rtol=0)
            np.testing.assert_allclose(result.net_r,result.gross_r-cfg['cost']*result.entry/result.risk,atol=1e-7,rtol=0)
            assert (result.exit_i<=result.end_i).all() and (result.exit_i>result.entry_i).all()
            result.to_csv(dest/'trades.csv.gz',index=False)
            check=dict(name=name,total=len(result),cached=len(cached),new_native=len(native),fills=len(fills),native_audit=audit,oracle_parity=True)
            dump(dest/'receipt.json',dict(identity=identity,check=check))
        result['target']='hold' if target is None else f'tp{target}';result['path']=path
        pieces.append(result);checks.append(check)
        print(symbol,name,'cached',check['cached'],'new',check['new_native'],flush=True)
    elementary=pd.concat(pieces,ignore_index=True)
    elementary.to_csv(folder/'elementary.csv.gz',index=False)
    cond=pd.concat([conditional_rows(bars,base,path) for path in cfg['paths']],ignore_index=True)
    for (path,target),g in cond.groupby(['path','trigger']):
        native=elementary[(elementary.path==path)&(elementary.target==f'tp{target}')].set_index('pair_id').loc[g.pair_id]
        np.testing.assert_array_equal(g.reached,native.reason.eq(4))
        hit=g.reached.to_numpy()
        np.testing.assert_array_equal(g.loc[hit,'hit_i'],native.loc[hit,'exit_i'])
    cond.to_csv(folder/'conditional_unique.csv.gz',index=False)
    dump(folder/'receipt.json',dict(inputs=stamps,checks=checks,conditional_native_reach_parity=True,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip()))


def metric(g):
    loss=-g.loc[g.net_r<0,'net_r'].sum()
    return dict(n=len(g),win_rate=float(g.net_r.gt(EPS).mean()),gross_r=float(g.gross_r.sum()),
        net_r=float(g.net_r.sum()),mean_net_r=float(g.net_r.mean()),mean_net_bp=float(g.net_notional_bp.mean()),
        pf=float(g.loc[g.net_r>0,'net_r'].sum()/loss) if loss else None,
        time_exit_weight=float(g.time_weight.mean()))


def analyze():
    from yoyo.evaluation.pepe_range_exits import inference
    from yoyo.evaluation.range_management_study import holm
    cfg=json.loads(CONFIG.read_text());stats=[];conditional=[];selected=[];infer=[]
    for spec in cfg['symbols']:
        symbol=spec['symbol'];_,_,_,base,occ,_=load(symbol);folder=EXP/symbol
        elementary=read(folder/'elementary.csv.gz')
        cd=occ.drop(columns='end_i').merge(read(folder/'conditional_unique.csv.gz'),on='pair_id',validate='many_to_many')
        assert len(cd)==len(occ)*4
        cd.to_csv(folder/'conditional.csv.gz',index=False)
        for (lane,pop,path,trigger),g in cd.groupby(['lane','population','path','trigger']):
            hit=g[g.reached]
            info=dict(symbol=symbol,lane=int(lane),population=pop,path=path,trigger=int(trigger),n=len(g),hit_n=len(hit),hit_rate=len(hit)/len(g))
            for col in ['peak_r','extra_r','minimum_r','trigger_pullback_r','max_drawdown_r','pullback_before_peak_r','minutes_to_touch_bar_close']:
                info[col+'_median']=float(hit[col].median());info[col+'_p75']=float(hit[col].quantile(.75))
            for col in ['returned_entry','returned_half_trigger','initial_stop_hit']:
                info[col+'_rate']=float(hit[col].mean())
            for t in [2,3,5]:
                if t<=trigger:continue
                for stem in [f'next{t}',f'next{t}_before_entry',f'next{t}_after_entry_return']:
                    info[stem+'_rate']=float(hit[stem].mean())
                info[f'pullback_before_next{t}_r_median']=float(hit[f'pullback_before_next{t}_r'].median())
            conditional.append(info)
        all_paths=[]
        for path in cfg['paths']:
            table={t:g.set_index('pair_id').sort_index() for t,g in elementary[elementary.path==path].groupby('target')}
            table['hold']=elementary[elementary.target=='hold'].set_index('pair_id').sort_index()
            schedules=[dict(name=t,targets=[None if t=='hold' else int(t[2:])],weights=[1.]) for t in ['hold','tp1','tp2','tp3','tp5']]+cfg['schedules']
            for schedule in schedules:
                legs=[table['hold' if t is None else f'tp{t}'] for t in schedule['targets']]
                for leg in legs[1:]:pd.testing.assert_index_equal(legs[0].index,leg.index)
                weights=np.asarray(schedule['weights']);assert abs(weights.sum()-1)<1e-10 and (weights>0).all()
                result=legs[0][['entry','risk']].copy()
                for col in ['gross_r','fee_r','net_r','net_notional_bp']:
                    result[col]=sum(w*g[col] for w,g in zip(weights,legs))
                result['time_weight']=sum(w*g.reason.eq(0).astype(float) for w,g in zip(weights,legs))
                np.testing.assert_allclose(result.fee_r,cfg['cost']*result.entry/result.risk,atol=1e-7,rtol=0)
                rows=occ.merge(result.drop(columns=['entry','risk']).reset_index(),on='pair_id',validate='many_to_one')
                rows['arm']=schedule['name'];rows['path']=path;all_paths.append(rows)
                for lane,group in rows.groupby('lane'):
                    for period,subset in [('all',group),('first_year',group[group.segment==0]),('later_year',group[group.segment==1])]:
                        s=subset[subset.population=='signal'];r=subset[subset.population=='random']
                        assert len(s)==len(r)
                        stats.append(dict(symbol=symbol,lane=int(lane),path=path,arm=schedule['name'],period=period,
                            **metric(s),random=metric(r)))
                    if path=='OHLC':
                        s=group[group.population=='signal'];r=group[group.population=='random']
                        paired=s[['event_id','week','net_r']].merge(r[['event_id','net_r']],on='event_id',suffixes=('','_random'),validate='one_to_one')
                        infer.append(dict(symbol=symbol,lane=int(lane),arm=schedule['name'],**inference(paired,cfg['seed'])))
        ledger=pd.concat(all_paths,ignore_index=True);ledger.to_csv(folder/'results.csv.gz',index=False)
        # Static fractions cannot outperform the best elementary total when
        # all stops/deadlines remain identical; verify for both populations.
        for _,g in ledger.groupby(['lane','path','population']):
            sums=g.groupby('arm').net_r.sum();best=max(sums[a] for a in ['hold','tp1','tp2','tp3','tp5'])
            assert all(sums[s['name']]<=best+EPS for s in cfg['schedules'])
        for lane in [5,15]:
            rows=[r for r in stats if r['symbol']==symbol and r['lane']==lane and r['path']=='OHLC']
            choices=[r for r in rows if r['period']=='first_year' and r['arm'].startswith('split')]
            best=max(choices,key=lambda x:x['mean_net_r'])
            selected.append(dict(symbol=symbol,lane=lane,chosen_first_year=best['arm'],
                first_year=best,later_year=next(r for r in rows if r['period']=='later_year' and r['arm']==best['arm']),
                full_history_best_partial=max([r for r in rows if r['period']=='all' and r['arm'].startswith('split')],key=lambda x:x['mean_net_r'])))
    for record,p in zip(infer,holm([r['p_one_sided'] for r in infer])):record['holm_p']=float(p)
    pd.DataFrame(conditional).to_csv(EXP/'conditional_summary.csv',index=False)
    pd.json_normalize(stats).to_csv(EXP/'comparison.csv',index=False)
    dump(EXP/'summary.json',dict(config=cfg,conditional=conditional,comparison=stats,selection=selected,inference=infer))
    print(json.dumps(selected,ensure_ascii=False,indent=2),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['run','analyze']);p.add_argument('--symbol',choices=['BTC','ETH']);a=p.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generating artifacts')
    if a.stage=='run':assert a.symbol;run_symbol(a.symbol)
    else:analyze()


if __name__=='__main__':main()
