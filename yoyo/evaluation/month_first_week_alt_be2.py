"""Fixed short monthly entries, one intrabar 2% breakeven intervention.

The reference ledger supplies the same entries, fixed exits and matched control
clocks. Only its 20 dual-hit reference exits may change when finer data can
resolve the tape. Only the outcome labels and post-entry excursions see future
data. Relative thresholds in the VectorBT native stop selector are referenced
to the start of each stage; actual fills use the unrounded absolute order.
"""
from itertools import product
from pathlib import Path
import json

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.signals.nb import ohlc_stop_choice_nb
from vectorbt.signals.enums import StopType

from yoyo.evaluation.month_first_week_alt_entries import ROOT, prices, universe

EXP=ROOT/'experiments/active/exp-alt-month-first-week-be2-20261110-v1'
PARENT=ROOT/'experiments/active/exp-alt-month-first-week-near-target-20261110-v1'
TAKER=.0005
MAKER=.0002
EPS=4*np.finfo(float).eps
HOUR=pd.Timedelta(hours=1)


@njit(cache=True)
def first_order(points, start, upper_stop, lower_target):
    initial=points[start,0]
    if initial>=upper_stop*(1-EPS):
        return start,1
    if initial<=lower_target*(1+EPS):
        return start,2
    prices_out=np.empty_like(points)
    types=np.empty(points.shape,np.int64)
    found=ohlc_stop_choice_nb(start,len(points),0,points,points,points,points,
        prices_out,types,max(0.,upper_stop/initial-1-EPS),False,
        max(0.,1-lower_target/initial-EPS),True,True,0,True,np.empty(1,np.int64),True)
    if len(found):
        i=found[0]
        return i,1 if types[i,0]==StopType.StopLoss else 2
    return len(points)-1,0


def tape(ohlc, orders, trigger, be):
    """Open/high/low/close order assumptions within individual source candles."""
    price=[]; source=[]; opening=[]
    for i,(o,h,l,c) in enumerate(ohlc):
        order=orders.get(i,'hl')
        if order=='hl': path=(o,h,l,c)
        elif order=='lh': path=(o,l,h,c)
        elif order=='activate_high': path=(o,trigger,be,h,l,c)
        elif order=='activate_low': path=(o,trigger,be,l,h,c)
        elif order=='be_low': path=(o,be,l,h,c)
        else: raise ValueError(order)
        price.extend(path)
        source.extend([i]*len(path))
        opening.extend([True]+[False]*(len(path)-1))
    return np.asarray(price).reshape(-1,1),np.asarray(source),np.asarray(opening)


def order_scenarios(ohlc, stop, target, trigger, be):
    """Only activation and the first original exit candle can change exit order.

    Extra retraces allow a short to touch trigger, then BE, before making the
    observed low, including on the same minute the original TP is reached.
    """
    opens,highs,lows,_=ohlc.T
    terminal=np.flatnonzero((highs>=stop*(1-EPS))|(lows<=target*(1+EPS)))
    last=int(terminal[0]) if len(terminal) else len(ohlc)-1
    choices={}
    activations=np.flatnonzero(lows[:last+1]<=trigger*(1+EPS))
    if target < trigger*(1-EPS) and len(activations):
        i=int(activations[0])
        if highs[i]>=be and lows[i]<=trigger and opens[i]>trigger and opens[i]<stop:
            choices[i]=['hl','lh','activate_high','activate_low']
    if len(terminal):
        i=last
        active_possible=target < trigger*(1-EPS) and len(activations)>0
        if opens[i]>target and lows[i]<=target and (highs[i]>=stop or (active_possible and highs[i]>=be)):
            opts=choices.setdefault(i,['hl','lh'])
            if active_possible and opens[i]<be and highs[i]>=be and 'be_low' not in opts:
                opts.append('be_low')
    if not choices:
        return [{}]
    return [dict(zip(choices,opts)) for opts in product(*choices.values())]


def fixed_exit(points,opening,start,stop,target,stop_name):
    i,kind=first_order(points,start,stop,target)
    if kind==1:
        return i, max(stop,points[i,0]) if opening[i] else stop,stop_name
    if kind==2:
        return i,target,'target'
    return i,points[i,0],'timeout'


def run_path(ohlc,stamps,order,row,static_ambiguous):
    entry=row.entry_fill;target=row.target_price;stop=row.stop_price
    trigger=entry*.98;be=entry*(1-TAKER)/(1+TAKER)
    points,source,opening=tape(ohlc,order,trigger,be)
    if static_ambiguous:
        _,old_price,old_reason=fixed_exit(points,opening,0,stop,target,'stop')
    else:
        old_price,old_reason=row.exit_price,row.exit_reason
    activated=None
    first_target=max(target,trigger)
    i,x,reason=fixed_exit(points,opening,0,stop,first_target,'stop')
    if (reason=='target' and target < trigger*(1-EPS)
            and not (row.exit_reason=='timeout' and stamps[source[i]]==row.exit_time)):
        activated=stamps[source[i]]
        i,x,reason=fixed_exit(points,opening,i,be,target,'breakeven')
    if reason=='timeout' and row.exit_reason!='timeout':
        raise AssertionError((row.trade_id,reason,old_reason))
    if reason=='target' and x!=target:  # 2% reached exactly at the 168h timeout open.
        if row.exit_reason!='timeout':raise AssertionError(row.trade_id)
        x=points[i,0]
        if x <= target*(1+EPS):
            x=target
        else:
            reason='timeout'
    # The final fill replaces the remaining source segment on which it occurred.
    visible=np.r_[points[:i,0],x]
    favorable=int(np.argmin(visible));adverse=int(np.argmax(visible))
    final_bar=source[i]
    before=ohlc[:final_bar]
    # Bounds exclude the part of the exit candle occurring after liquidation.
    known_fav=min(entry,x,before[:,2].min() if len(before) else entry)
    known_adv=max(entry,x,before[:,1].max() if len(before) else entry)
    fav_max=known_fav
    adv_max=known_adv
    if not opening[i]:
        known_fav=min(known_fav,ohlc[final_bar,0])
        known_adv=max(known_adv,ohlc[final_bar,0])
        if activated is not None:
            known_fav=min(known_fav,trigger)
        fav_max=min(known_fav,max(target,ohlc[final_bar,2]))
        adv_cap=be if activated is not None and activated<stamps[final_bar] else stop
        adv_max=max(known_adv,min(adv_cap,ohlc[final_bar,1]))
    return dict(exit_price=x,exit_reason=reason,exit_time=stamps[source[i]],activation_time=activated,
        old_price=old_price,old_reason=old_reason,
        mfe_lower=max(0.,100*(1-known_fav/entry)),mfe_upper=max(0.,100*(1-fav_max/entry)),
        mae_lower=max(0.,100*(known_adv/entry-1)),mae_upper=max(0.,100*(adv_max/entry-1)),mfe_pct=max(0.,100*(1-visible[favorable]/entry)),
        mae_pct=max(0.,100*(visible[adverse]/entry-1)),mfe_time=stamps[source[favorable]],
        mae_time=stamps[source[adverse]])


def cash(entry,exit_price,reason):
    quotes=np.vstack((entry,exit_price))
    fees=np.vstack((np.full(len(entry),TAKER),np.where(np.asarray(reason)=='target',MAKER,TAKER)))
    qty=np.vstack((-np.ones(len(entry)),np.ones(len(entry))))
    portfolio=vbt.Portfolio.from_orders(quotes,price=quotes,size=qty,fees=fees,init_cash=4*quotes.max(axis=0),
            cash_sharing=False,allow_partial=False,raise_reject=True)
    trades=portfolio.trades.records.sort_values('col')
    assert len(trades)==len(entry) and trades.status.eq(1).all()
    net=100*trades.pnl.to_numpy()/entry
    net[np.abs(net)<1e-10]=0
    return net


def price_window(row,bars,symbol_minutes):
    f=bars.loc[row.entry_time:row.exit_time]
    assert f.index[0]==row.entry_time and f.index[-1]==row.exit_time and len(f)==(row.exit_time-row.entry_time)//HOUR+1
    assert np.isclose(f.open.iloc[0],row.entry_fill,rtol=1e-10,atol=0)
    parts=[];times=[]
    for ts,values in zip(f.index,f[['open','high','low','close']].to_numpy()):
        # At the seven-day deadline only opening orders can still be filled.
        if ts==row.entry_time+168*HOUR:
            parts.append(np.full((1,4),values[0]));times.append(ts)
        elif ts in symbol_minutes:
            sub=symbol_minutes[ts];parts.append(sub[['open','high','low','close']].to_numpy())
            times.extend(sub.index)
        else:
            parts.append(values.reshape(1,4));times.append(ts)
    return np.concatenate(parts),pd.DatetimeIndex(times)


def ci(g,column):
    monthly=g.groupby('month')[column].agg(['count','sum'])
    weights=np.random.default_rng(20261110).multinomial(len(monthly),np.repeat(1/len(monthly),len(monthly)),size=5000)
    return np.quantile((weights@monthly['sum'])/(weights@monthly['count']),[.025,.975]).tolist()


def summary(ledger):
    rows=[]
    for group,x in ledger.groupby('is_control'):
        for period,g in [('all',x),*list(x.groupby('period'))]:
            lo,hi=ci(g,'delta_pp')
            base_loss=g.old_net_pct < -1e-8
            breakeven=g.exit_reason.eq('breakeven')
            saved=base_loss & breakeven & g.net_pct.ge(-1e-8)
            cut=breakeven & g.old_reason.eq('target')
            rows.append(dict(group='random' if group else 'signal',period=period,n=len(g),
                previous=g.old_net_pct.mean(),new=g.net_pct.mean(),delta=g.delta_pp.mean(),ci_low=lo,ci_high=hi,
                old_frozen=g.frozen_net_pct.mean(),order_low=g.delta_min.mean(),order_high=g.delta_max.mean(),
                stress_before=g.old_stress_pct.mean(),stress_after=g.stress_pct.mean(),
                old_positive=int(g.old_net_pct.gt(1e-8).sum()),net_positive=int(g.net_pct.gt(1e-8).sum()),
                flat=int(g.net_pct.abs().le(1e-8).sum()),negative=int(g.net_pct.lt(-1e-8).sum()),
                old_pf=g.loc[g.old_net_pct>0,'old_net_pct'].sum()/-g.loc[g.old_net_pct<0,'old_net_pct'].sum(),
                new_pf=g.loc[g.net_pct>0,'net_pct'].sum()/-g.loc[g.net_pct<0,'net_pct'].sum(),
                activated=int(g.activation_time.notna().sum()),be_exits=int(breakeven.sum()),gaps=int((breakeven & g.net_pct.lt(-1e-8)).sum()),
                saved=int(saved.sum()),original_stops_saved=int((saved & g.old_reason.eq('stop')).sum()),
                improved_losses=int((base_loss & (g.delta_pp>1e-8)).sum()),targets_washed=int(cut.sum()),
                full_4_targets_washed=int((cut & np.isclose(g.target_price/g.entry_fill,.96)).sum()),
                earned_pp=g.delta_pp.clip(lower=0).sum(),given_back_pp=-g.delta_pp.clip(upper=0).sum(),
                outcome_sensitive=int((g.delta_max-g.delta_min>1e-8).sum()),
                baseline_changes=int(g.old_reason.ne(g.frozen_exit_reason).sum())))
    pd.DataFrame(rows).to_csv(EXP/'metrics.csv',index=False)
    signals=ledger[~ledger.is_control]
    pairs=signals.merge(ledger[ledger.is_control][['paired_id','period','net_pct','delta_pp']],
        on='paired_id',suffixes=('','_random'),validate='1:1')
    pairs['edge_pp']=pairs.net_pct-pairs.net_pct_random
    pairs['edge_change_pp']=pairs.delta_pp-pairs.delta_pp_random
    comparisons={}
    for p,g in [('all',pairs),*list(pairs[pairs.period==pairs.period_random].groupby('period'))]:
        comparisons[p]=dict(n=len(g),signal=g.net_pct.mean(),random=g.net_pct_random.mean(),
            excess_pp=g.edge_pp.mean(),ci=ci(g,'edge_pp'),change_in_excess_pp=g.edge_change_pp.mean())
    with (EXP/'summary.json').open('w') as out:
        json.dump(dict(metrics=rows,matched=comparisons),out,indent=2,allow_nan=False)


def main():
    parent=pd.read_csv(PARENT/'trades.csv.gz',dtype={'cap':str},parse_dates=['entry_time','exit_time'])
    parent=parent[(parent.cap=='4') & (parent.side=='upper')].copy()
    minute=pd.read_csv(EXP/'refined_minutes.csv.gz',parse_dates=['hour','time'])
    minute=minute.set_index('time').sort_index()
    by_symbol={symbol:dict(tuple(f.groupby('hour'))) for symbol,f in minute.groupby('symbol')}
    sources=universe().set_index('symbol',drop=False)
    scenarios=[];positions=[]
    for symbol,g in parent.groupby('symbol'):
        bars=prices(sources.loc[symbol])
        for row in g.itertuples(index=False):
            values,stamps=price_window(row,bars,by_symbol.get(symbol,{}))
            paths=order_scenarios(values,row.stop_price,row.target_price,row.entry_fill*.98,row.entry_fill*(1-TAKER)/(1+TAKER))
            for branch,order in enumerate(paths):
                result=run_path(values,stamps,order,row,row.ambiguous_sl_tp)
                scenarios.append(dict(**result,trade_id=row.trade_id,branch=branch,entry=row.entry_fill,
                    frozen=row.net_return_pct,old_ambiguous=row.ambiguous_sl_tp))
            positions.append(dict(trade_id=row.trade_id,paired_id=row.paired_id,symbol=symbol,month=row.month,
                period=row.period,is_control=row.is_control,entry_time=row.entry_time,entry_fill=row.entry_fill,
                stop_price=row.stop_price,target_price=row.target_price,frozen_exit_reason=row.exit_reason,
                frozen_net_pct=row.net_return_pct,baseline_mfe_pct=row.mfe_pct))
    trial=pd.DataFrame(scenarios);trial['net_pct']=cash(trial.entry.to_numpy(),trial.exit_price.to_numpy(),trial.exit_reason)
    trial['old_net_pct']=trial.frozen
    dual=trial[trial.old_ambiguous]
    if len(dual):
        trial.loc[dual.index,'old_net_pct']=cash(dual.entry.to_numpy(),dual.old_price.to_numpy(),dual.old_reason)
    trial['delta_pp']=trial.net_pct-trial.old_net_pct
    bounds=trial.groupby('trade_id').agg(delta_min=('delta_pp','min'),delta_max=('delta_pp','max'),
        float_min=('mfe_lower','min'),float_max=('mfe_upper','max'),
        adverse_min=('mae_lower','min'),adverse_max=('mae_upper','max'))
    primary=trial[trial.branch==0].drop(columns=['entry','frozen','old_ambiguous','branch'])
    ledger=pd.DataFrame(positions).merge(primary,on='trade_id',validate='1:1').join(bounds,on='trade_id')
    for stem,name in [('', 'stress_pct'),('old_','old_stress_pct')]:
        reason=ledger.exit_reason if not stem else ledger.old_reason
        px=ledger.exit_price if not stem else ledger.old_price
        ledger[name]=ledger[stem+'net_pct']-.05*(1+(reason!='target')*px/ledger.entry_fill)
    assert len(ledger)==len(parent) and ledger.trade_id.is_unique
    unaffected=ledger[~ledger.exit_reason.eq('breakeven')]
    if not np.allclose(unaffected.net_pct,unaffected.old_net_pct,atol=1e-8,rtol=0):
        raise AssertionError('An unchanged exit has a different net return on the identical price path')
    for field,lo,hi in [('mfe_pct','float_min','float_max'),('mae_pct','adverse_min','adverse_max')]:
        assert ((ledger[field]>=ledger[lo]-1e-8)&(ledger[field]<=ledger[hi]+1e-8)).all()
    ledger.to_csv(EXP/'positions.csv.gz',index=False)
    public=ledger.loc[~ledger.is_control,['symbol','entry_time','entry_fill','target_price','activation_time',
        'old_reason','old_net_pct','exit_time','exit_reason','exit_price','net_pct','delta_pp',
        'mfe_pct','mfe_time','float_min','float_max','mae_pct','mae_time','adverse_min','adverse_max','delta_min','delta_max']].copy()
    public.columns=['合约','入场时间UTC','入场价','目标价','浮盈2%首次触发UTC','原退出原因','原净盈亏%',
        '新退出时间UTC','新退出原因','新退出价','新净盈亏%','净盈亏差_百分点','实际持仓最大浮盈%',
        '浮盈峰值UTC','最大浮盈下界%','最大浮盈上界%','实际持仓最大浮亏%','浮亏峰值UTC','最大浮亏幅度下界%','最大浮亏幅度上界%','路径差下限_百分点','路径差上限_百分点']
    public.to_csv(EXP/'逐笔保本对照.csv',index=False,encoding='utf-8-sig',float_format='%.9g')
    summary(ledger)


if __name__=='__main__':main()
