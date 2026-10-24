"""Descriptive and time-block matched analysis of frozen native MA6 outcomes.

No execution is performed here. All gains/costs come from native fills. XAU
statistics are gross only; closed-trade statistics exclude observation-boundary
settlements. Equal-weight independent R is not a cash-account return. Random
controls test alternative eligible order timing, with contemporaneous MA rules,
not equal absolute stop distances. Weekly resampling treats common market weeks
as clusters; all history has been inspected and is not a pristine holdout.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_ma6_retest import EXP,ART,finite
from yoyo.evaluation.range_framework import dump

CUT=pd.Timestamp('2023-01-01',tz='UTC')
POLICIES=['monday_full','opposite_1618_full','half_each']


def read(path):
    try:return pd.read_csv(path,float_precision='round_trip',low_memory=False)
    except pd.errors.EmptyDataError:return pd.DataFrame()


def group_name(symbol):
    return {'BTCUSDT':'BTC','ETHUSDT':'ETH','XAUUSD':'XAUUSD'}.get(symbol,'ALTCOINS')


def streak(mask):
    longest=running=0
    for value in mask:
        running=running+1 if value else 0;longest=max(longest,running)
    return longest


def streak_span(ordered):
    best=[];current=[]
    for r in ordered.itertuples():
        current=current+[r] if r.return_r<0 else []
        if len(current)>len(best):best=list(current)
    return dict(n=len(best),first_trade=best[0].trade_id if best else None,
                last_trade=best[-1].trade_id if best else None,
                first_exit=str(best[0].exit_interval_end) if best else None,
                last_exit=str(best[-1].exit_interval_end) if best else None)


def pairs(g):
    a=g[g.population.eq('signal')&g.closed].set_index('pair_id')
    b=g[g.population.eq('random')&g.closed].copy()
    if a.empty or b.empty:return pd.DataFrame()
    for c in ['symbol','actual_entry_week','side','bucket','period']:
        b=b[b[c].eq(b.pair_id.map(a[c]))]
    n=b.groupby('pair_id').size()
    b=b[b.pair_id.isin(n[n.eq(3)].index)]
    mean=b.groupby('pair_id')[['return_r','return_pct','planned_rr','holding_hours']].mean().add_prefix('control_')
    return a.join(mean,how='inner')


def infer(p,seed=20261024):
    if len(p)<2:return {}
    d=p.assign(delta=p.return_r-p.control_return_r)
    blocks=d.groupby('actual_entry_week').delta.agg(['sum','count'])
    if len(blocks)<2:return dict(independent_market_weeks=len(blocks))
    sums=blocks['sum'].to_numpy();counts=blocks['count'].to_numpy();rng=np.random.default_rng(seed)
    means=[];null=[]
    for _ in range(2000):
        ix=rng.integers(len(blocks),size=len(blocks));means.append(sums[ix].sum()/counts[ix].sum())
        null.append((sums*rng.choice([-1,1],len(blocks))).sum()/counts.sum())
    lo,hi=np.quantile(means,[.025,.975]);actual=sums.sum()/counts.sum()
    return dict(paired_ci_low=float(lo),paired_ci_high=float(hi),
        paired_p=float((1+sum(abs(v)>=abs(actual) for v in null))/2001),
        independent_market_weeks=len(blocks),inference='Market-week bootstrap95%; two-sided clustered sign-flip raw p; three exits, not selection-corrected')


def stats(g,with_inference=False,basis=None):
    a=g[g.population.eq('signal')];c=a[a.closed];r=c.return_r
    wins=r[r>0];loss=r[r<0];p=pairs(g)
    ordered=c.sort_values(['exit_interval_end','symbol','trade_id'],kind='stable')
    isstop=ordered.reason.eq(1) if not ordered.policy.eq('half_each').all() else ordered.sl_legs.eq(2)
    floating=(c.interior_mfe_r>0)&(c.reason.eq(1) | c.get('sl_legs',pd.Series(0,index=c.index)).fillna(0).gt(0))
    basis=basis or ('gross' if len(g) and g.symbol.eq('XAUUSD').all() else 'net')
    net=basis=='net'
    result=dict(basis=basis,filled_n=len(a),closed_n=len(c),censored_n=int((~a.closed).sum()),
        unreachable_target_n=int(a.target_unreachable.fillna(False).astype(bool).sum()),
        same_bar_target_stop_n=int(c.ambiguous.fillna(0).gt(0).sum()) if not a.policy.eq('half_each').all() else None,
        wins=len(wins),losses=len(loss),flat=int(r.eq(0).sum()),win_rate=r.gt(0).mean(),
        mean_r=r.mean(),total_r=r.sum() if len(r) else None,median_r=r.median(),mean_bp=c.return_pct.mean()*100,
        mean_gross_r=c.gross_r.mean(),mean_fee_r=c.fee_r.mean() if net else None,
        mean_net_r=r.mean() if net else None,total_net_r=r.sum() if net and len(r) else None,
        mean_net_bp=c.return_pct.mean()*100 if net else None,
        mean_win_r=wins.mean(),mean_loss_r=loss.mean(),
        payoff=wins.mean()/-loss.mean() if len(loss) else None,
        profit_factor=wins.sum()/-loss.sum() if len(loss) else None,
        planned_rr_median=c.planned_rr.median(),median_holding_hours=c.holding_hours.median(),
        p90_holding_hours=c.holding_hours.quantile(.9),mfe_r_mean=c.interior_mfe_r.mean(),
        mae_r_mean=c.interior_mae_r.mean(),profitable_excursion_then_sl_n=int(floating.sum()),
        fee_positive_excursion_then_sl_n=int((floating&c.interior_fee_positive_mfe.astype(bool)).sum()),
        losing_profitable_then_sl_n=int((floating&r.lt(0)).sum()),
        losing_fee_positive_then_sl_n=int((floating&r.lt(0)&c.interior_fee_positive_mfe.astype(bool)).sum()),
        longest_loss_streak=streak(ordered.return_r<0),longest_stop_streak=streak(isstop),
        fully_matched_signal_n=len(p),complete_control_group_n=len(p),controls_per_signal=3,
        matched_signal_mean_r=p.return_r.mean() if len(p) else None,
        matched_control_mean_r=p.control_return_r.mean() if len(p) else None,
        paired_excess_r=(p.return_r-p.control_return_r).mean() if len(p) else None,
        paired_signal_planned_rr=p.planned_rr.mean() if len(p) else None,
        paired_control_planned_rr=p.control_planned_rr.mean() if len(p) else None,
        matched_signal_mean_net_r=p.return_r.mean() if len(p) and net else None,
        matched_control_mean_net_r=p.control_return_r.mean() if len(p) and net else None,
        paired_excess_net_r=(p.return_r-p.control_return_r).mean() if len(p) and net else None,
        matched_signal_mean_gross_r=p.return_r.mean() if len(p) and not net else None,
        matched_control_mean_gross_r=p.control_return_r.mean() if len(p) and not net else None,
        tp_legs_then_sl_n=int(((c.get('tp_legs',pd.Series(0,index=c.index)).fillna(0)==1)&
                              (c.get('sl_legs',pd.Series(0,index=c.index)).fillna(0)==1)).sum()),
        paired_ci_low=None,paired_ci_high=None,paired_p=None)
    if with_inference:
        result.update(infer(p))
        result['loss_streak_span']=json.dumps(streak_span(ordered),ensure_ascii=False)
        bycoin=[dict(symbol=s,**streak_span(x)) for s,x in ordered.groupby('symbol')]
        worst=max(bycoin,key=lambda r:r['n']) if bycoin else dict(n=0)
        result['longest_single_symbol_loss_streak']=worst['n']
        result['single_symbol_streak_span']=json.dumps(worst,ensure_ascii=False)
    return result


def enrich(t):
    t=t.copy();t['closed']=t.closed.astype(str).str.lower().isin(['true','1','1.0'])
    for c in ['entry_time','exit_interval_start','exit_interval_end']:
        t[c]=pd.to_datetime(t[c],utc=True,format='mixed')
    ix=pd.DatetimeIndex(pd.to_datetime(t.entry_interval_start,utc=True)).tz_convert('America/New_York')
    t['actual_entry_week']=(ix.tz_localize(None).normalize()-pd.to_timedelta(ix.dayofweek,unit='D')).strftime('%Y-%m-%d')
    t['cross_cut']=(pd.to_datetime(t.entry_interval_start,utc=True)<CUT)&(t.exit_interval_end>=CUT)
    t['period']=np.where(pd.to_datetime(t.entry_interval_start,utc=True)>=CUT,'later','earlier')
    t['asset_group']=t.symbol.map(group_name);t['exit_mode']=t.policy
    entry_open=pd.to_datetime(t.entry_interval_start,utc=True)
    t['year']=entry_open.dt.year;t['month']=entry_open.dt.strftime('%Y-%m')
    t['entry_price']=t.entry;t['stop_price']=t.stop;t['net_bp']=t.net_notional_bp
    t['mfe_r']=t.interior_mfe_r;t['mae_r']=t.interior_mae_r
    t['opportunity_id']=t.pair_id;t['order_id']=t.parent_id
    t['exit_time']=t.exit_interval_end.astype(str)
    t['outcome']=np.where(~t.closed,'censored',np.where(t.return_r>0,'win',np.where(t.return_r<0,'loss','flat')))
    t['exit_reason']=t.reason.map({0:'observation_boundary',1:'stop',4:'target',10:'two_native_legs'})
    t['censored']=~t.closed
    t['profitable_excursion_then_sl']=(t.mfe_r>0)&(t.reason.eq(1)|t.sl_legs.fillna(0).gt(0))
    for c in ['entry_time','exit_interval_end']:
        t[c+'_beijing']=t[c].dt.tz_convert('Asia/Shanghai').astype(str)
    return t


def attempted_pairs(orders,trades,mode,asset,period):
    """No-fill is zero exposure only for opportunity expectancy, not win rate."""
    q=orders[orders.asset_group.eq(asset)].copy()
    at=pd.to_datetime(q.submitted_at,utc=True,format='mixed')
    if period!='all':q=q[(at>=CUT) if period=='later' else (at<CUT)]
    z=trades[trades.policy.eq(mode)].set_index('parent_id')
    q['attempt_r']=q.trade_id.map(z.return_r)
    q.loc[q.status.ne('filled'),'attempt_r']=0.
    crossing=((pd.to_datetime(z.submitted_at,utc=True)<CUT)&(z.exit_interval_end>=CUT))
    bad=z.index[(~z.closed)|(crossing if period!='all' else False)]
    q.loc[q.trade_id.isin(bad),'attempt_r']=np.nan
    a=q[q.population.eq('signal')].set_index('pair_id')
    b=q[q.population.eq('random')];n=b.groupby('pair_id').attempt_r.count()
    b=b[b.pair_id.isin(n[n.eq(3)].index)]
    means=b.groupby('pair_id').attempt_r.mean()
    p=a.join(means.rename('control_attempt_r'),how='inner').dropna(subset=['attempt_r','control_attempt_r'])
    return dict(opportunity_period_basis='submission time; any filled exposure crossing cutoff excluded',complete_opportunity_pairs=len(p),
        attempt_signal_mean_r=p.attempt_r.mean(),attempt_control_mean_r=p.control_attempt_r.mean(),
        attempt_paired_excess_r=(p.attempt_r-p.control_attempt_r).mean(),
        signal_fill_rate=q[q.population.eq('signal')].status.eq('filled').mean(),
        random_fill_rate=q[q.population.eq('random')].status.eq('filled').mean())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--partial',action='store_true')
    args=parser.parse_args()
    ART.mkdir(exist_ok=True);dest=ART/'report-data';dest.mkdir(exist_ok=True)
    completed=sorted((EXP/'symbols').glob('*/done.json'))
    if len(completed)!=633 and not args.partial:raise RuntimeError(f'Native coverage incomplete: {len(completed)}/633')
    tables={}
    for name in ['opportunities','orders','trades','fills']:
        pieces=[read(p.parent/f'{name}.csv.gz') for p in completed]
        pieces=[p for p in pieces if len(p)]
        tables[name]=pd.concat(pieces,ignore_index=True) if pieces else pd.DataFrame()
        tables[name].to_csv(EXP/f'all-{name}.csv.gz',index=False)
    t=enrich(tables['trades']);o=tables['opportunities'];e=tables['orders'];fills=tables['fills']
    # Signal preparer keeps exact first-touch/confirmation field names in raw
    # tables. These aliases are presentation-only and preserve every column.
    for d in (o,e):
        d['asset_group']=d.symbol.map(group_name)
        if 'trade_id' in d:d['opportunity_id']=d.get('pair_id',d.trade_id)
    o['invalidation_reason']=o.reason
    o['trigger_level']=o.level
    e['order_id']=e.trade_id;e['limit_price']=e.entry;e['stop_price']=e.stop
    e['exit_mode']='shared_entry'
    e['period']=np.where(pd.to_datetime(e.submitted_at,utc=True)>=CUT,'later','earlier')
    native=t[t.policy.ne('half_each')].set_index('trade_id')
    for col in ['symbol','asset_group','exit_mode','population','pair_id','parent_id','side','basis','period','cross_cut']:
        fills[col]=fills.trade_id.map(native[col])
    fills['opportunity_id']=fills.pair_id;fills['order_id']=fills.parent_id
    if not args.partial:
        from yoyo.evaluation.monday_ma6_order_timeline import enrich_orders
        e=enrich_orders(e,t,completed)
    fills['fill_id']=[f'fill-{i}' for i in range(len(fills))]
    fills['time']=pd.to_datetime(fills.ts_ns,utc=True,unit='ns').astype(str)
    fills['time_basis']='Synthetic OHLC path timestamp, not an observed trade tick'
    fills['notional']=fills.price*fills.quantity
    fills['fee_class']=np.where(fills.phase.isin(['entry','target']),'maker','taker')
    fills['fee_rate']=fills.commission/fills.notional
    fills.loc[fills.symbol.eq('XAUUSD'),['fee_rate','commission']]=np.nan
    fills.loc[fills.symbol.eq('XAUUSD'),'fee_class']='unknown_not_modeled'
    summaries=[];cuts=[]
    for (mode,asset),g in t.groupby(['policy','asset_group'],sort=True):
        for period in ['all','earlier','later']:
            x=g if period=='all' else g[g.period.eq(period)&~g.cross_cut]
            summaries.append(dict(exit_mode=mode,asset_group=asset,period=period,grain='summary',**stats(x,True,basis='gross' if asset=='XAUUSD' else 'net')))
        for dimension in ['year','month','side','symbol']:
            for value,x in g.groupby(dimension,sort=True):
                cuts.append(dict(exit_mode=mode,asset_group=asset,period='all',grain='slice',
                                 cut_dimension=dimension,cut_value=str(value),**stats(x)))
    summary=pd.DataFrame(summaries);metrics=pd.DataFrame(cuts)
    for i,r in summary.iterrows():
        for key,value in attempted_pairs(e,t,r.exit_mode,r.asset_group,r.period).items():summary.loc[i,key]=value
    ps=summary.paired_p.dropna().sort_values();last=0.
    for rank,(i,p) in enumerate(ps.items()):
        last=max(last,min(1.,float(p)*(len(ps)-rank)))
        summary.loc[i,'paired_p_holm']=last
    # Counts are pending-order opportunities, never multiplied by target policy.
    funnel=[]
    timecol=next(c for c in ['first_touch_at','touch_time','trigger_time','pin_time'] if c in o)
    o['first_touch_at']=pd.to_datetime(o[timecol],utc=True,format='mixed')
    o['period']=np.where(o.first_touch_at>=CUT,'later','earlier')
    for asset,g in o.groupby('asset_group'):
        for period in ['all','earlier','later']:
            z=g if period=='all' else g[g.period.eq(period)]
            ids=set(z.opportunity_id)
            q=e[e.population.eq('signal')&e.pair_id.isin(ids)]
            counts={'first_touch':len(z),'confirmed':int(z.confirm_i.ge(0).sum()),'submitted':len(q),'filled':int(q.status.eq('filled').sum()),
                    'expired':int(q.status.eq('expired').sum()),
                    'cancelled':int((~q.status.isin(['filled','expired'])).sum())}
            for key,value in counts.items():funnel.append(dict(asset_group=asset,period=period,stage=key,n=value))
            for key,value in z.status.value_counts().items():
                funnel.append(dict(asset_group=asset,period=period,stage='monitor_'+str(key),n=int(value)))
            sel=summary.asset_group.eq(asset)&summary.period.eq(period)
            summary.loc[sel,'opportunity_n']=len(z)
            summary.loc[sel,'confirmed_n']=int(z.confirm_i.ge(0).sum())
            summary.loc[sel,'invalidated_n']=int(z.status.isin(['invalid','invalid_unobservable']).sum())
            summary.loc[sel,'submitted_n']=len(q)
            summary.loc[sel,'canceled_n']=counts['cancelled']
            summary.loc[sel,'unfilled_n']=int(q.status.ne('filled').sum())
    for name,d in [('summary',summary),('metrics',metrics),('funnel',pd.DataFrame(funnel)),
                   ('opportunities',o),('orders',e),('trades',t),('fills',fills)]:
        d.to_csv(dest/f'{name}.csv.gz',index=False)
        if name in ['summary','metrics','funnel']:d.to_csv(EXP/f'{name}.csv',index=False)
    coverage=[json.loads(p.read_text()) for p in completed]
    result=dict(experiment_id=EXP.name,complete=len(completed)==633,expected_symbols=633,completed_symbols=len(completed),coverage=coverage,source_first=min(r['first'] for r in coverage),
        source_last=max(r['last'] for r in coverage),native_positions=sum(r['native_positions'] for r in coverage),
        native_fills=sum(r['native_fills'] for r in coverage),
        source_opportunities=len(o),submitted_orders=len(e),
        target_policy_rows=len(t),tables=finite(summary.to_dict('records')),
        limitations=['Independent R, not cash portfolio returns','XAU gross Bid-only; broker fees and spread unknown',
         'Resting limits synthetic unlimited liquidity; no queue, funding or slippage',
         'O-H-L-C is an execution assumption, not observed intrabar ticks',
         'Random controls match eligible submission timing and same MA rules, not identical absolute price distances',
         'All history previously available for research; chronological split is not a fresh holdout',
         'Excursions exclude uncertain entry and exit5m bars; profitable-then-stop counts are conservative lower bounds'],
        production_eligible=False,training_eligible=False)
    dump(EXP/'summary.json',finite(result));dump(ART/'summary.json',finite(result))
    print(json.dumps(finite(dict(native_positions=result['native_positions'],tables=result['tables'])),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
