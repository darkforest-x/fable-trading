"""Explain frozen Monday first-touch losses and owner-approved stop sensitivity.

All descriptive future excursions are OUTCOMES, never admission features.
Entry1m and first5m/15m close classifications are explicitly retrospective;
stops before those closes are separated to prevent an executable-filter claim.
Stop arms keep all original limit orders, random times, target prices and fee0.
Native executions compare equal notional bp and common original0.7percent risk,
not differently normalized R sums as if exposure were unchanged.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_fib_limits as base
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation.pepe_range_exits import inference

DEFAULT=ROOT/'experiments/active/exp-btc-monday-fib1618-okx-20261022-v1'


def quantile(values):
    x=np.asarray(values,dtype=float)
    return dict(n=len(x),p25=float(np.quantile(x,.25)),median=float(np.median(x)),
                p75=float(np.quantile(x,.75)),maximum=float(x.max())) if len(x) else dict(n=0)


def path_mfe(bars,row,tick,path='OHLC',entry_mode='limit'):
    """Favorable excursion AFTER resting limit fill, before original stop/end.

    Integer-tick monotone segment audit, matching the disclosed native OHLC
    assumption. Stop-bar favorable prices after stopping are never credited.
    Reverse ordering supplies sensitivity, not observed tick truth.
    """
    entry=round(row.entry/tick);stop=round(row.stop/tick);side=row.side
    if entry_mode not in ('limit','market'):raise ValueError(entry_mode)
    # Market signals enter at the recorded minute close: its earlier wick
    # cannot count as excursion. The following open is already held exposure.
    entered=entry_mode=='market';best=0
    start=int(row.entry_i)+(1 if entered else 0)
    for i in range(start,int(row.exit_i)+1):
        o,h,l,c=bars[i];points=[o,h,l,c] if path=='OHLC' else [o,l,h,c]
        if not entered and side*(o-entry)<=0:
            entered=True
        if entered:
            if side*(o-stop)<=0:
                return best*tick/row.risk
            best=max(best,side*(o-entry))
        for a,b in zip(points[:-1],points[1:]):
            if not entered and min(a,b)<=entry<=max(a,b):
                entered=True;a=entry
            if not entered:
                continue
            if min(a,b)<=stop<=max(a,b):
                best=max(best,side*(a-entry))
                return best*tick/row.risk
            best=max(best,side*(a-entry),side*(b-entry))
    return best*tick/row.risk


def diagnostics(frame,signal,cfg):
    prices=frame[base.OHLC].to_numpy();ticks=np.rint(prices/cfg['tick']).astype(np.int64)
    higher={m:frame.resample(f'{m}min',closed='right',label='right').agg(
        {'open':'first','high':'max','low':'min','close':'last'}) for m in (5,15)}
    records=[]
    for row in signal.itertuples():
        # These descriptions are conditioned on realized fills, never predictions.
        i,j=int(row.entry_i),int(row.exit_i);side=row.side
        p=prices[i];outside=side*(p[3]-row.entry)<-cfg['tick']/2
        crossed=side*(p[0]-row.entry)>=-cfg['tick']/2 and outside
        body_outside=side*(p[0]-row.entry)<-cfg['tick']/2 and outside
        d=dict(parent_id=row.parent_id,week=row.week,side=side,entry_time=row.entry_time,
            entry=row.entry,stop=row.stop,tp80=row.tp80,tp20=row.tp20,gross_r=row.gross_r,
            closed=row.closed,full_initial_stop=bool(row.closed and not row.tp80_hit),
            tp80_hit=row.tp80_hit,tp20_hit=row.tp20_hit,holding_minutes=row.holding_minutes,
            target80_r=side*(row.tp80-row.entry)/row.risk,
            target80_pct=100*side*(row.tp80-row.entry)/row.entry,
            target20_r=side*(row.tp20-row.entry)/row.risk,
            monday_width_pct=100*abs(row.tp20-row.tp80)/.382/row.entry,
            entry_minute_close_outside=bool(outside),entry_minute_crossing_body=bool(crossed),
            entry_minute_entire_body_outside=bool(body_outside),
            stopped_within_entry_minute=bool(row.closed and not row.tp80_hit and i==j),
            mfe_r_ohlc=path_mfe(ticks,row,cfg['tick']),
            mfe_r_olhc=path_mfe(ticks,row,cfg['tick'],'OLHC'))
        for minutes,q in higher.items():
            when=frame.index[i].ceil(f'{minutes}min');bar=q.loc[when]
            stopped_before=bool(row.closed and not row.tp80_hit and frame.index[j]<=when)
            outside=side*(bar.close-row.entry)<-cfg['tick']/2
            whole=side*(bar.open-row.entry)<-cfg['tick']/2 and outside
            d[f'close{minutes}_time']=str(when)
            d[f'stopped_before_close{minutes}']=stopped_before
            d[f'close{minutes}_outside']=bool(outside)
            d[f'close{minutes}_whole_body_outside']=bool(whole)
        if d['full_initial_stop']:
            for hours in (24,168):
                last=min(len(frame)-1,j+hours*60)
                future=prices[j+1:last+1]
                hit=np.flatnonzero(side*((future[:,1] if side==1 else future[:,2])-row.tp80)>=-cfg['tick']/2)
                d[f'poststop_{hours}h_complete']=bool(j+hours*60<len(frame))
                d[f'poststop_tp80_{hours}h']=bool(len(hit))
                if len(hit):
                    k=j+1+int(hit[0]);window=prices[i:k+1]
                    adverse=(row.entry-window[:,2].min()) if side==1 else (window[:,1].max()-row.entry)
                    d[f'recovery_{hours}h_minutes']=k-j
                    d[f'recovery_{hours}h_max_adverse_pct']=100*adverse/row.entry
                else:
                    d[f'recovery_{hours}h_minutes']=None
                    d[f'recovery_{hours}h_max_adverse_pct']=None
        records.append(d)
    table=pd.DataFrame(records)
    losers=table[table.full_initial_stop];winners=table[table.closed & (table.gross_r>0)]
    bands=[]
    for lo,hi in [(0,.25),(.25,.5),(.5,1),(1,2),(2,float('inf'))]:
        s=losers[(losers.mfe_r_ohlc>=lo)&(losers.mfe_r_ohlc<hi)]
        bands.append(dict(lower_r=lo,upper_r=hi if np.isfinite(hi) else None,
                          n=len(s),pct=100*len(s)/len(losers)))
    thresholds=[]
    for level in (.25,.5,1,2):
        thresholds.append(dict(r=level,ohlc_count=int((losers.mfe_r_ohlc>=level).sum()),
            olhc_count=int((losers.mfe_r_olhc>=level).sum())))
    close_tables=[]
    for label in ['entry_minute_close_outside','close5_outside','close15_outside']:
        for value in (False,True):
            g=table[table[label]==value];c=g[g.closed]
            close_tables.append(dict(label=label,value=value,n=len(g),closed=len(c),
                wins=int((c.gross_r>0).sum()),win_pct=100*(c.gross_r>0).mean() if len(c) else None))
    recovery=[]
    for h in (24,168):
        eligible=losers[losers[f'poststop_{h}h_complete']==True]
        got=eligible[eligible[f'poststop_tp80_{h}h']==True]
        recovery.append(dict(hours=h,complete=len(eligible),recovered=len(got),
            percentage=100*len(got)/len(eligible),
            required_max_adverse_pct=quantile(got[f'recovery_{h}h_max_adverse_pct'].dropna())))
    summary=dict(trades=len(table),full_initial_stop=len(losers),winners=len(winners),
        partial_then_stop=int((table.tp80_hit & ~table.tp20_hit & table.closed).sum()),
        tp80_r=quantile(table.target80_r),tp80_pct=quantile(table.target80_pct),tp20_r=quantile(table.target20_r),
        loser_mfe_r=quantile(losers.mfe_r_ohlc),loser_duration_minutes=quantile(losers.holding_minutes),
        loser_mfe_bands=bands,loser_mfe_thresholds=thresholds,
        stopped_entry_minute=int(table.stopped_within_entry_minute.sum()),
        losers_stop_within={str(m):int((losers.holding_minutes<=m).sum()) for m in [5,15,30,60,240,1440]},
        loser_entry_close_outside=int(losers.entry_minute_close_outside.sum()),
        loser_entry_crossing_body=int(losers.entry_minute_crossing_body.sum()),
        loser_entry_entire_body_outside=int(losers.entry_minute_entire_body_outside.sum()),
        loser_before_5m=int(losers.stopped_before_close5.sum()),loser_before_15m=int(losers.stopped_before_close15.sum()),
        loser_5m_outside=int(losers.close5_outside.sum()),loser_15m_outside=int(losers.close15_outside.sum()),
        loser_15m_whole_body_outside=int(losers.close15_whole_body_outside.sum()),
        close_descriptive_tables=close_tables,post_stop_recovery=recovery,
        average_winner_r=float(winners.gross_r.mean()),
        break_even_win_pct=100/(1+float(winners.gross_r.mean())),
        limitations='Post-entry bodies and future recovery are retrospective descriptions. They are not tested entry filters or proof that widening stops improves returns. Recovery adverse extremes include the full target-touch minute and are conservative bounds. OHLC vs OLHC is path sensitivity, not known tick order.')
    return table,summary


def stop_candidates(original,stop,code,tick):
    rows=original.copy()
    rows['base_risk']=rows.risk
    rows['stop_fraction']=stop;rows['stop_label']=code
    rows['trade_id']=rows.trade_id+'-'+code
    rows['stop']=[base.rounded(e*(1-s*stop),tick,s==-1) for e,s in zip(rows.entry,rows.side)]
    rows['risk']=rows.side*(rows.entry-rows.stop)
    return rows


def stop_study(frame,cfg,dcfg,dest):
    frozen={p:pd.read_csv(dest/filename,float_precision='round_trip') for p,filename in
            [('signal','weekly_orders.csv'),('random','random_entries.csv')]}
    allrows=[];audits=[]
    for pop,original in frozen.items():
        entries=pd.concat([stop_candidates(original,v,f'stop{v*100:g}',cfg['tick'])
                           for v in dcfg['stop_fractions']],ignore_index=True)
        candidates=base.legs(entries)
        rows,audit=base.native(frame,candidates,'limit' if pop=='signal' else 'market',
                              dest/f'native_{pop}_wider_stops',dict(cfg,diagnostic=dcfg))
        sensitivity,check=base.audit_paths(frame,candidates,rows,'limit' if pop=='signal' else 'market',cfg)
        rows=rows.merge(sensitivity,on='trade_id',validate='one_to_one')
        total=base.aggregate(rows,cfg)
        meta=entries[['trade_id','stop_fraction','stop_label','base_risk']].rename(columns={'trade_id':'parent_id'})
        total=total.merge(meta,on='parent_id',validate='one_to_one')
        total['base_r']=total.gross_r*total.risk/total.base_risk
        total['notional_bp']=10000*total.gross_r*total.risk/total.entry
        total['stopfirst_base_r']=total.stopfirst_r*total.risk/total.base_risk
        total.to_csv(dest/f'{pop}_wider_stops.csv',index=False)
        allrows.append(total);audits.append(dict(population=pop,**check,positions=audit['positions'],fills=audit['fills']))
    joined=pd.concat(allrows,ignore_index=True)
    baseline=pd.concat([pd.read_csv(dest/f'{p}_trades.csv',float_precision='round_trip') for p in ('signal','random')])
    baseline['stop_fraction']=cfg['stop_fraction'];baseline['stop_label']='stop0.7'
    baseline['base_r']=baseline.gross_r;baseline['base_risk']=baseline.risk
    baseline['notional_bp']=10000*baseline.gross_r*baseline.risk/baseline.entry
    baseline['stopfirst_base_r']=baseline.stopfirst_r
    joined=pd.concat([baseline,joined],ignore_index=True)
    base_signal=baseline[baseline.population=='signal'].set_index('pair_id')
    # Identical submissions, filled opportunities and price targets in each arm.
    for label,g in joined[joined.population=='signal'].groupby('stop_label'):
        assert set(g.pair_id)==set(base_signal.index)
        a=g.set_index('pair_id').loc[base_signal.index]
        for key in ['entry_i','entry','tp80','tp20']:
            np.testing.assert_allclose(a[key],base_signal[key],rtol=0,atol=1e-8)
    results=[]
    for label in ['stop0.7',*[f'stop{x*100:g}' for x in dcfg['stop_fractions']]]:
        for seg in (-1,0,1):
            g=joined[joined.stop_label==label]
            if seg>=0:
                g=g[g.segment==seg]
            s=g[g.population=='signal'];r=g[g.population=='random']
            metrics={}
            for name,t in [('signal',s),('random',r)]:
                closed=t[t.closed]
                metrics[name]=dict(base.metric(t),common07risk_r=float(closed.base_r.sum()),
                    mean_notional_bp=float(closed.notional_bp.mean()),
                    total_notional_bp=float(closed.notional_bp.sum()),
                    stopfirst_common07risk_r=float(closed.stopfirst_base_r.sum()))
            b=base_signal.loc[s.pair_id].reset_index()
            rescued=int(((b.gross_r.to_numpy()<0)&s.closed.to_numpy()&(s.gross_r.to_numpy()>0)).sum())
            damaged=int(((b.gross_r.to_numpy()>0)&s.closed.to_numpy()&(s.gross_r.to_numpy()<0)).sum())
            complete=r.groupby('pair_id').closed.all();ids=complete[complete].index
            pair=s[s.closed&s.pair_id.isin(ids)][['pair_id','week','base_r']].rename(columns={'base_r':'net_r'})
            pair=pair.join(r.groupby('pair_id').base_r.mean().rename('net_r_random'),on='pair_id')
            results.append(dict(stop=label,segment=seg,**metrics,rescued_old_losers=rescued,
                damaged_old_winners=damaged,matched_pairs=len(pair),comparison=inference(pair,cfg['seed'])))
    dump(dest/'stop_comparison.json',dict(authorization=dcfg,results=results,audits=audits,
        accounting='common07risk_R uses each original trade risk as denominator; equal-notional bp exposes larger loss size. Own-risk R remains descriptive. Open end marks excluded from closed results.'))
    print(json.dumps([x for x in results if x['segment']==-1],indent=2),flush=True)


def run(dest):
    base.EXP=dest
    cfg=json.loads((dest/'config.json').read_text());dcfg=json.loads((dest/'diagnostic_config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit diagnostic builder before generation')
    frame=base.load_data(cfg);signal=pd.read_csv(dest/'signal_trades.csv',float_precision='round_trip')
    table,summary=diagnostics(frame,signal,cfg)
    table.to_csv(dest/'path_diagnostics.csv',index=False)
    dump(dest/'path_diagnostics.json',summary)
    print(json.dumps(summary,indent=2),flush=True)
    stop_study(frame,cfg,dcfg,dest)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,default=DEFAULT)
    run(p.parse_args().directory.resolve())
