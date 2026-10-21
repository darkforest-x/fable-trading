"""Two-hour MFE and static scale-outs on frozen original V2.31 entries.

Entry features remain frozen. Diagnostics read future OHLC only within
(entry close, entry close+120min], stopping at the original wick SL. Future
extrema are outcomes, never entry features or assumed fill prices. Native
Nautilus orders execute six elementary policies. Unchanged-stop tranches
are weighted independent native legs; no new execution engine or BE.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

DEST = ROOT/'experiments/active/exp-range-v231-2h-mfe-20261022-v1'


def read(path):
    return pd.read_csv(path, float_precision='round_trip')


def schedules(cfg):
    out = [dict(name=f'fixed_{t}r',targets=[t],weights=[1.]) for t in cfg['targets']]
    for stem in ['two','three']:
        for targets in cfg[f'{stem}_leg_targets']:
            for weights in cfg[f'{stem}_leg_weights']:
                name='split_'+'_'.join(map(str,targets))+'r_'+'_'.join(f'{w:.4f}' for w in weights)
                out.append(dict(name=name,targets=targets,weights=weights))
    out.append(dict(name='split_five_equal',targets=cfg['five_leg_targets'],weights=cfg['five_leg_weights']))
    assert len(out)==18 and len({s['name'] for s in out})==18
    return out


def load(cfg,spec):
    source=ROOT/cfg['source']/spec['symbol']
    raw=read(source/'candles.csv.gz')
    frame=raw[['open','high','low','close']].copy()
    frame.index=pd.DatetimeIndex(pd.to_datetime(raw.ts,unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    assert frame.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
    base=read(source/'candidates.csv')
    base['entry_id']=base.population+'-'+base.pair_id.astype(str)
    base['trade_id']=base.entry_id
    base['risk']=base.side*(base.entry-base.stop)
    base['source_end_i']=base.end_i
    base['end_i']=base.entry_i+cfg['hold_minutes']//5
    if base.end_i.max() >= len(frame):
        # Preserve frozen entries; reuse the registered confirmed minute tail
        # solely for the last entry's complete outcome horizon.
        from yoyo.evaluation.monday_range_v2_2_reference import aggregate
        original=json.loads((source.parent/'config.json').read_text())
        tail_spec=next(s for s in original['symbols'] if s['symbol']==spec['symbol'])
        minute=read(ROOT/tail_spec['inputs'][-1])
        minute.index=pd.DatetimeIndex(pd.to_datetime(minute.ts,unit='ms',utc=True)).as_unit('ns')
        tail=aggregate(minute[['open','high','low','close']],5)
        overlap=frame.index.intersection(tail.index)
        pd.testing.assert_frame_equal(frame.loc[overlap],tail.loc[overlap],check_freq=False,check_names=False)
        required=frame.index[base.entry_i.max()]+pd.Timedelta(minutes=cfg['hold_minutes'])
        frame=pd.concat([frame,tail[(tail.index>frame.index[-1])&(tail.index<=required)]])
    assert base.end_i.max()<len(frame), 'Missing complete two-hour outcome horizon'
    assert frame.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
    assert (base.end_i-base.entry_i).eq(24).all(), 'Incomplete two-hour windows require explicit handling'
    assert len(base)==2*spec['n'] and base.entry_id.is_unique
    assert base.population.eq('signal').sum()==base.population.eq('random').sum()==spec['n']
    np.testing.assert_allclose(frame.close.to_numpy()[base.entry_i],base.entry,rtol=0,atol=1e-8)
    return source,frame,base


def mix_ledgers(elementary,schedule):
    """Weight original shares; valid only with common unchanged SL/deadline.

    Proportional fees are required. BE, target-dependent stops or shared
    capital constraints invalidate this linear identity.
    """
    weights=np.asarray(schedule['weights'],float)
    assert np.all(weights>0) and abs(weights.sum()-1)<1e-10
    legs=[elementary[f'fixed_{t}r'].set_index('entry_id').sort_index() for t in schedule['targets']]
    out=legs[0].copy()
    for leg in legs[1:]:
        pd.testing.assert_index_equal(out.index,leg.index)
        for col in ['pair_id','population','entry_i','end_i','side','entry','stop','risk']:
            pd.testing.assert_series_equal(out[col],leg[col],check_names=False)
    for col in ['gross_r','fee_r','net_r','net_notional_bp','boundary_weight','boundary_net_r']:
        out[col]=sum(w*leg[col] for w,leg in zip(weights,legs))
    out['exit_i']=np.max(np.stack([leg.exit_i.to_numpy() for leg in legs]),axis=0)
    out['ambiguous_legs']=sum(leg.ambiguous_legs for leg in legs)
    out['arm']=schedule['name']
    out['trade_id']=out.arm+':'+out.index
    np.testing.assert_allclose(out.gross_r-out.fee_r,out.net_r,rtol=0,atol=1e-8)
    return out.reset_index()


def run_symbol(cfg,spec):
    from yoyo.evaluation.btc_range_wick_review import excursions
    from yoyo.evaluation.range_v230_exit_compare import make_legs,aggregate_legs,oracle,CODE
    from yoyo.evaluation.pepe_range_breakeven import native
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    folder=DEST/spec['symbol']
    source,frame,base=load(cfg,spec)
    identity=dict(config=cfg,inputs=[file_stamp(source/p) for p in ['candles.csv.gz','candidates.csv']],
                  code=[file_stamp(ROOT/p) for p in [*CODE,'yoyo/evaluation/range_twohour_mfe.py']])
    if base.end_i.max()>base.source_end_i.max():
        original=json.loads((source.parent/'config.json').read_text())
        original_spec=next(s for s in original['symbols'] if s['symbol']==spec['symbol'])
        identity['inputs'].append(file_stamp(ROOT/original_spec['inputs'][-1]))
    if (folder/'receipt.json').exists():
        assert json.loads((folder/'receipt.json').read_text())['identity']==identity
        print(spec['symbol']+': reuse completed native output',flush=True)
        return
    if folder.exists():
        raise RuntimeError(f'Incomplete output, inspect before recovery: {folder}')
    lo,hi=pd.Timestamp('2022-12-18T02:00Z'),pd.Timestamp('2022-12-18T11:30Z')
    touched=(frame.index[base.entry_i]<hi)&(frame.index[base.end_i]>=lo)
    revision_n=int(touched.sum()) if spec['symbol']=='ETH' else 0
    assert revision_n==0,'Known official ETH revisions require additional price sensitivity'
    parents,legs=[],[]
    for target in [None,*cfg['targets']]:
        arm='hold_2h' if target is None else f'fixed_{target}r'
        b=base.copy();b['arm']=arm;b['trade_id']=arm+':'+b.entry_id
        one=make_legs(b,dict(target_r=target or 1),dict.fromkeys(b.trade_id,[]),spec['tick'])
        if target is None:one['target_price']=np.nan
        parents.append(b);legs.append(one)
    parents,legs=pd.concat(parents,ignore_index=True),pd.concat(legs,ignore_index=True)
    ctx=dict(bars=np.ascontiguousarray(frame.to_numpy(float)),atr=np.zeros(len(frame)),hour=np.zeros(len(frame)))
    policy=ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=cfg['round_trip_cost'])
    rows,fills,audit=run_native(frame,ctx['atr'],ctx['hour'],legs,policy,tick=spec['tick'],symbol=spec['symbol'])
    conservative,checks=oracle(ctx,legs,{},spec['tick'],cfg['round_trip_cost'],native(),rows)
    actual,lower=[aggregate_legs(x,parents,cfg['round_trip_cost']) for x in [rows,conservative]]
    for result in [actual,lower]:
        assert (result.exit_i>result.entry_i).all() and (result.exit_i<=result.end_i).all()
        np.testing.assert_allclose(result.fee_r,cfg['round_trip_cost']*result.entry/result.risk,rtol=0,atol=1e-7)
    mfe=base.merge(excursions(ctx['bars'],base),on='trade_id',validate='one_to_one')
    mfe['fee_r']=cfg['round_trip_cost']*mfe.entry/mfe.risk
    mfe['risk_pct']=100*mfe.risk/mfe.entry
    full=[]
    for row in base.itertuples():
        path=ctx['bars'][row.entry_i+1:row.end_i+1]
        fav=path[:,1]-row.entry if row.side==1 else row.entry-path[:,2]
        full.append(max(0.,float(fav.max()))/row.risk)
    mfe['full_2h_mfe_r_ignoring_stop']=full
    mfe['horizon_close_r']=base.side*(frame.close.to_numpy()[base.end_i]-base.entry)/base.risk
    hold=rows[rows.arm=='hold_2h'].set_index('entry_id').loc[base.entry_id]
    np.testing.assert_array_equal(hold.exit_i,np.where(mfe.initial_stop_hit,mfe.stop_i,base.end_i))
    np.testing.assert_allclose(hold.peak_r,mfe.mfe_r,rtol=0,atol=1e-8)
    assert (mfe.mfe_upper_r>=mfe.mfe_r-1e-8).all()
    assert (mfe.full_2h_mfe_r_ignoring_stop>=mfe.mfe_upper_r-1e-8).all()
    folder.mkdir(parents=True)
    for name,data in [('candidates',base),('legs',legs),('native_trades',rows),('fills',fills),
                      ('elementary',actual),('elementary_stop_first',lower),('mfe',mfe)]:
        data.to_csv(folder/(name+'.csv'),index=False)
    dump(folder/'receipt.json',dict(identity=identity,audit=audit,oracle=checks,
        native_mfe_lower_bound_parity=True,all_horizons_120_minutes=True,revision_horizon_exposure=revision_n,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(json.dumps(dict(symbol=spec['symbol'],positions=len(rows),fills=len(fills),oracle=checks)),flush=True)


def metric(rows):
    return dict(n=len(rows),gross_win_rate=float(rows.gross_r.gt(1e-9).mean()),
        wins=int(rows.net_r.gt(1e-9).sum()),win_rate=float(rows.net_r.gt(1e-9).mean()),
        gross_r=float(rows.gross_r.sum()),fee_r=float(rows.fee_r.sum()),net_r=float(rows.net_r.sum()),
        mean_net_r=float(rows.net_r.mean()),mean_net_bp=float(rows.net_notional_bp.mean()),
        time_exit_n=int(rows.boundary_weight.gt(0).sum()),time_exit_weight=float(rows.boundary_weight.mean()))


def analyze(cfg):
    from yoyo.evaluation.pepe_range_exits import pairs,inference
    from yoyo.evaluation.range_management_study import holm
    summaries,distributions,selection,hist_rows,reach_rows=[],[],[],[],[]
    bins=[0,.5,1,2,3,5,10,20,np.inf]
    labels=['0–0.5R','0.5–1R','1–2R','2–3R','3–5R','5–10R','10–20R','≥20R']
    for spec in cfg['symbols']:
        symbol,folder=spec['symbol'],DEST/spec['symbol']
        elementary,lower=[read(folder/p) for p in ['elementary.csv','elementary_stop_first.csv']]
        tables=[{name:g for name,g in data.groupby('arm')} for data in [elementary,lower]]
        combined=[]
        for table,filename in zip(tables,['trades.csv','stop_first.csv']):
            result=pd.concat([table['hold_2h'],*[mix_ledgers(table,s) for s in schedules(cfg)]],ignore_index=True)
            result=result.sort_values(['arm','entry_i','entry_id']);result.to_csv(folder/filename,index=False)
            combined.append(result)
        primary,conservative=combined
        full_summary=[]
        for name,rows in primary.groupby('arm'):
            sensitivity=conservative[conservative.arm==name].set_index('entry_id')
            signal=rows[rows.population=='signal']
            for period,mask in [('all',np.ones(len(signal),bool)),
                ('first_three_years',pd.to_datetime(signal.entry_time,utc=True)<pd.Timestamp(cfg['selection_end'])),
                ('later_two_years',pd.to_datetime(signal.entry_time,utc=True)>=pd.Timestamp(cfg['selection_end']))]:
                ids=signal.loc[mask,'pair_id'];part=rows[rows.pair_id.isin(ids)]
                s,r=[part[part.population==p] for p in ['signal','random']]
                assert len(s)==len(r)==len(ids)
                record=dict(symbol=symbol,arm=name,period=period,signal=metric(s),random=metric(r),
                    stop_first_signal=metric(sensitivity.loc[s.entry_id]),stop_first_random=metric(sensitivity.loc[r.entry_id]))
                if period=='all':
                    record['paired_weekly_inference_r']=inference(pairs(s,r),cfg['seed'])
                    record['paired_weekly_inference_bp']=inference(pairs(s.assign(net_r=s.net_notional_bp),r.assign(net_r=r.net_notional_bp)),cfg['seed'])
                    full_summary.append(record)
                summaries.append(record)
        for record,p in zip(full_summary,holm([x['paired_weekly_inference_r']['p_one_sided'] for x in full_summary])):
            record['holm_p_r_over_19_arms']=float(p)
        coin_rows=[x for x in summaries if x['symbol']==symbol]
        for family,valid in [('all_19',lambda n:True),('partial_13',lambda n:n.startswith('split'))]:
            first=[r for r in coin_rows if r['period']=='first_three_years' and valid(r['arm'])]
            best=max(first,key=lambda r:r['signal']['mean_net_r'])
            later=next(r for r in coin_rows if r['period']=='later_two_years' and r['arm']==best['arm'])
            selection.append(dict(symbol=symbol,family=family,chosen=best['arm'],first=best,later=later))
        for pop in ['signal','random']:
            sums=primary[primary.population==pop].groupby('arm').net_r.sum()
            best_fixed=max(sums[f'fixed_{t}r'] for t in cfg['targets'])
            assert all(sums[s['name']]<=best_fixed+1e-7 for s in schedules(cfg))
        mfe=read(folder/'mfe.csv')
        for pop in ['signal','random']:
            g=mfe[mfe.population==pop]
            info=dict(symbol=symbol,population=pop,n=len(g),stop_within_2h=int(g.initial_stop_hit.sum()),
                ambiguous_mfe_n=int((g.mfe_upper_r>g.mfe_r+1e-8).sum()),cost_r_median=float(g.fee_r.median()),
                cost_r_mean=float(g.fee_r.mean()),cost_gt_1r_n=int(g.fee_r.gt(1).sum()),risk_pct_median=float(g.risk_pct.median()),
                mfe_covers_cost_n=int((g.mfe_r>=g.fee_r).sum()),upper_mfe_covers_cost_n=int((g.mfe_upper_r>=g.fee_r).sum()),
                full_mfe_covers_cost_n=int((g.full_2h_mfe_r_ignoring_stop>=g.fee_r).sum()),horizons={})
            for field in ['mfe_r','mfe_upper_r','full_2h_mfe_r_ignoring_stop']:
                v=g[field];info['horizons'][field]=dict(mean=float(v.mean()),median=float(v.median()),
                    p75=float(v.quantile(.75)),p90=float(v.quantile(.9)),max=float(v.max()))
                counts,_=np.histogram(v,bins);assert counts.sum()==len(g)
                hist_rows.extend(dict(symbol=symbol,population=pop,measure=field,bin=label,n=int(n),percent=float(n/len(g)*100)) for label,n in zip(labels,counts))
            distributions.append(info)
        native=read(folder/'native_trades.csv')
        for pop in ['signal','random']:
            for target in cfg['targets']:
                part=native[(native.arm==f'fixed_{target}r')&(native.population==pop)]
                hits=part[part.reason==4];m=mfe[mfe.population==pop]
                reach_rows.append(dict(symbol=symbol,population=pop,target_r=target,n=len(part),hits=len(hits),hit_rate=len(hits)/len(part),
                    median_minutes_to_hit=float((hits.exit_i-hits.entry_i).median()*5) if len(hits) else None,
                    lower_extreme_hits=int(m.mfe_r.ge(target-1e-8).sum()),upper_extreme_hits=int(m.mfe_upper_r.ge(target-1e-8).sum()),
                    ignore_stop_hits=int(m.full_2h_mfe_r_ignoring_stop.ge(target-1e-8).sum())))
    pd.DataFrame(hist_rows).to_csv(DEST/'histogram.csv',index=False)
    pd.DataFrame(reach_rows).to_csv(DEST/'target_reach.csv',index=False)
    flat=[dict(symbol=r['symbol'],arm=r['arm'],period=r['period'],**r['signal'],random_net_r=r['random']['net_r'],
               random_mean_net_bp=r['random']['mean_net_bp'],stop_first_net_r=r['stop_first_signal']['net_r']) for r in summaries]
    pd.DataFrame(flat).to_csv(DEST/'comparison.csv',index=False)
    dump(DEST/'summary.json',dict(config=cfg,schedules=schedules(cfg),distributions=distributions,rows=summaries,
        selection=selection,static_weighted_native_identity=True,
        interpretation='MFE is a hindsight high-water mark, not fillable profit.2h deadlines are intentional exits, not dataset boundaries.20bp excludes funding and additional slippage.Independent initial-risk R is not account return.'))
    print('Analysis complete: '+str(DEST/'summary.json'),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['run','analyze'],required=True)
    parser.add_argument('--symbol',choices=['BTC','ETH'])
    args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/range_twohour_mfe.py',str(DEST/'config.json')],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and configuration before execution')
    cfg=json.loads((DEST/'config.json').read_text())
    if args.stage=='run':
        for spec in cfg['symbols']:
            if args.symbol is None or args.symbol==spec['symbol']:run_symbol(cfg,spec)
    else:analyze(cfg)


if __name__=='__main__':main()
