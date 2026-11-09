"""Try completing an initial capacity-constrained order at the following 12H open in the same wallet."""
import argparse
import gc

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_cash_giveback as cash

engine=cash.queue.engine
base=engine.base
EXP=cash.ROOT/'exp-cloud-close-deferred-fill-20261109-v1'
PANEL=cash.ROOT/'exp-cloud-close-second-window-capacity-20261109-v1'/'second_window_capacity.csv.gz'


@njit(cache=True)
def empty_slots(c):
    count=0
    for first in range(c.from_col,c.to_col,4):
        if c.last_position[first:first+4].sum()==0:
            count+=1
    return count


@njit(cache=True)
def order(c,nav,actions,prices,fees,reserve,weights,unit_weights,trim,native,side,multiple,slip,limits,
          second,planned,gap,add_size,add_price,add_fee,details):
    hour,phase=c.i//4,c.i%4
    slot,leg=c.col//4,c.col%4
    first=slot*4
    if phase==0 and leg==0 and actions[c.i,c.col]==1:
        # The identical ordinary reservation before the first-volume cap.
        collateral=min(nav/(c.group_len//4),c.free_cash_now/max(1,empty_slots(c)))
        unit_notional=native[hour,slot]*(1.+side[hour,slot]*slip)
        unit_cost=prices[c.i,c.col]*(1.+fees[c.i,c.col])
        planned[slot]=collateral*(1.-1.e-10)*unit_notional/unit_cost
        gap[slot]=0.
    if phase==1 and leg==0 and actions[c.i-1,c.col]==1:
        initial=c.last_position[first:first+4].sum()*native[hour,slot]*(1.+side[hour,slot]*slip)
        cap=limits[hour,slot]
        if np.isfinite(cap) and cap>0 and abs(initial-cap)<=1.e-6+1.e-8*cap:
            gap[slot]=max(0.,planned[slot]-initial)
    if phase==2 and second[hour,slot]>0 and trim[slot]<=1.e-10:
        if leg==0:
            add_size[first:first+4]=0.
            shares=c.last_position[first:first+4]
            held=shares.sum()
            if held>0 and gap[slot]>1.e-6:
                ratio=native[hour,slot]
                unit_notional=ratio*(1.+side[hour,slot]*slip)
                active=np.argmax(shares)
                price=c.close[c.i,first+active]+ratio*slip
                fee=base.wallet.TAKER*unit_notional/price
                cost_per_notional=price*(1.+fee)/unit_notional
                concentration=max(0.,.2*nav-held*ratio)
                # At the opening reference price actual added units must also fit the 20% limit on shorts.
                available_cap=min(concentration,concentration*unit_notional/ratio)
                available_cash=c.free_cash_now/(1+empty_slots(c))/cost_per_notional
                add=min(second[hour,slot],gap[slot],available_cap,available_cash)*(1.-1.e-10)
                if add>1.e-6:
                    add_size[first:first+4]=shares/held*add/unit_notional
                    add_price[slot]=price
                    add_fee[slot]=fee
                    details[hour,slot,0]=gap[slot]
                    details[hour,slot,1]=available_cap
                    details[hour,slot,2]=available_cash
        if add_size[c.col]>0:
            return nb.order_nb(size=add_size[c.col],price=add_price[slot],fees=add_fee[slot],
                               lock_cash=True,allow_partial=False,raise_reject=True)
        return NoOrder
    return base.trade(c,nav,actions,prices,fees,reserve,weights,unit_weights,trim,native,side,multiple,slip,limits)


def shadow_decisions(label,slip,parents,caps,quotes):
    """Read only the actual orders before the second opening and the native prior finished H1 close."""
    original=pd.read_csv(cash.SOURCE/f'aligned/funded_{label}.csv.gz',index_col='event_id')
    bound=original.index[np.isclose(original.notional,caps.loc[original.index],rtol=1.e-8,atol=1.e-6)]
    orders=pd.read_csv(cash.EXP/f'orders_{label}.csv.gz',float_precision='round_trip')
    rows=[]
    for eid,group in orders[orders.underlying_event_id.isin(bound)].groupby('underlying_event_id'):
        parent=parents.loc[eid]
        decision=int(parent.entry_ms)+cash.STEP
        prior=group[group.hour<decision]
        units=prior.net_units.sum()
        if units<=max(1e-12,prior.net_units.max()*1e-9):
            continue
        clock,px=quotes(parent.symbol)
        ix=clock.searchsorted(decision-cash.H)
        if clock[ix]!=decision-cash.H:
            raise ValueError('Missing H1 immediately before decision')
        liquidation=px[ix,3]*(1-parent.side*slip)
        profit=prior.underlying_cash.sum()+(parent.side-base.wallet.TAKER)*units*liquidation
        rows.append((eid,decision,profit))
    return pd.DataFrame(rows,columns=['event_id','second_entry_ms','previous_net_usdt']).set_index('event_id')


def run(context,label,slip,arm,model,levered,limits,signals,*,directory=EXP):
    data,market,_,parents,caps,ratios=context
    native,previous,side,_=market
    slots=native.shape[1]
    second=np.zeros_like(native)
    allowed=signals
    if arm!='disabled':
        positive=allowed.previous_net_usdt>0
        if arm in ('profit','nonpositive'):
            allowed=allowed.loc[positive if arm=='profit' else ~positive]
        elif arm=='original_price':
            # Same scenario's market slippage applies to both real buys or both real sells.
            candidates=parents.loc[allowed.index]
            first=candidates.entry_price*(1.+candidates.side*slip)
            later=allowed.second_native_open*(1.+candidates.side*slip)
            allowed=allowed.loc[(later-first)*candidates.side<=first*1e-12]
        elif arm!='all':
            raise ValueError('Unknown deferred fill gate')
        allowed=allowed.loc[allowed.second_entry_limit_10pct_usdt.gt(0)]
        times=(allowed.second_entry_ms-data['start'])//cash.H
        selected_slots=parents.loc[allowed.index,'slot']//4
        if not (allowed.second_entry_ms<=parents.loc[allowed.index,'exit_bar_ms']).all():
            raise ValueError('Second order assigned after parent availability')
        if not pd.MultiIndex.from_arrays([times,selected_slots]).is_unique:
            raise ValueError('Colliding second purchase opportunities')
        second[times,selected_slots]=allowed.second_entry_limit_10pct_usdt
    audit=np.zeros((*native.shape,3))
    pf=base.build_portfolio(data,model,native*levered,previous*levered,side,1.,slip,entry_limits=limits,
          order_func=order,extra_order_args=(second,np.zeros(slots),np.zeros(slots),np.zeros(slots*4),
          np.zeros(slots),np.zeros(slots),audit),extra_order_count=4*np.count_nonzero(second))
    saved=[]
    def collect(fills):
        saved.append(fills[['underlying_event_id','hour','stage','col','side','notional','fees']].copy())
    actual_data=dict(data,selected=data['selected'].loc[data['selected'].event_id.map(caps).gt(0)])
    funds,curve,_=base.receipts(pf,actual_data,model,ratios,fill_sink=collect)
    funds['exposure']=ratios
    fills=saved[0]
    initial=fills[fills.stage.eq(0)&fills.side.eq(0)].groupby('underlying_event_id').notional.sum()
    if set(initial.index)!=set(caps[caps>0].index) or (initial>caps.loc[initial.index]+1e-6).any():
        raise ValueError('Changed initial opening roster or exceeded first-hour capacity')
    name=label+'_'+arm
    if arm=='disabled':
        ref=cash.SOURCE/'aligned'
        old=pd.read_csv(ref/f'funded_{label}.csv.gz',index_col='event_id')
        old_curve=pd.read_csv(ref/f'hourly_{label}.csv.gz',index_col=0,parse_dates=True)
        if not (set(old.index)==set(initial.index) and curve.index.equals(old_curve.index)
                and np.allclose(old[['cash_pnl','notional']],funds.loc[old.index,['cash_pnl','notional']],atol=1e-5,rtol=1e-9)
                and np.allclose(old_curve,curve[old_curve.columns],atol=1e-4,rtol=1e-9)):
            raise ValueError('Disabled add does not reproduce frozen per-parent cash and all hourly marks')
        return dict(disabled_parent_hourly_parity=True,terminal=float(curve.nav.iloc[-1]),funded=len(funds))
    adds=fills.loc[fills.stage.eq(2)&fills.side.eq(0)].copy()
    checks=adds.groupby(['underlying_event_id','hour']).notional.sum().rename('actual_notional').reset_index()
    eids=checks.underlying_event_id
    when=((checks.hour-data['start'])//cash.H).to_numpy(dtype=int)
    slot=(parents.loc[eids,'slot']//4).to_numpy(dtype=int)
    checks['original_filled_usdt']=eids.map(initial)
    checks['original_cap_usdt']=eids.map(caps)
    checks['shadow_previous_net_usdt']=eids.map(allowed.previous_net_usdt)
    checks['second_capacity_usdt']=second[when,slot]
    for col,label in enumerate(['original_unfilled_usdt','spare_concentration_usdt','free_cash_notional_usdt']):
        checks[label]=audit[when,slot,col]
    constraints=checks[['second_capacity_usdt','original_unfilled_usdt','spare_concentration_usdt','free_cash_notional_usdt']]
    if (not checks.hour.equals(eids.map(allowed.second_entry_ms)) or checks.underlying_event_id.duplicated().any()
            or not np.allclose(checks.original_filled_usdt,checks.original_cap_usdt,rtol=1e-8,atol=1e-6)
            or not (checks.actual_notional<=constraints.min(axis=1)+1e-6).all()):
        raise ValueError('Second fill fails original cap hit, clock, budget or capacity reconciliation')
    checks.to_csv(directory/f'additions_{name}.csv.gz',index=False)
    funds.to_csv(directory/f'funded_{name}.csv.gz')
    curve.to_csv(directory/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
    metrics=base.parent.spot.hourly.tested.summarize(funds,curve)
    return dict(metrics=metrics,positive_capacity_eligible=len(allowed),changed_parents=len(checks),
                total_second_notional=float(adds.notional.sum()),second_fees_usdt=float(adds.fees.sum()),
                trim_fills=int((fills.stage.eq(2)&fills.side.eq(1)).sum()),opening_fills=len(initial))


def main(preflight=False,*,directory=EXP,arms=('profit','nonpositive')):
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed deferred entry study')
    context=cash.account()
    data,market,quotes,parents,caps,ratios=context
    native=market[0]
    limits=np.full_like(native,np.inf)
    limits[(parents.entry_ms-data['start'])//cash.H,parents.slot//4]=caps
    results={}
    for label,slip in [('median',0.),('median_stress',.0005)]:
        model,levered=engine.collateral_model(data,market,ratios,slip)
        parity_file=EXP/f'parity_{label}.json'
        if not parity_file.exists():
            verified=run(context,label,slip,'disabled',model,levered,limits,None)
            cash.queue.repair.archive.prices.safe_json(parity_file,verified)
            print(label,verified,flush=True)
        if not preflight:
            panel=pd.read_csv(PANEL,index_col='event_id')
            shadow=shadow_decisions(label,slip,parents,caps,quotes)
            if not panel.index.is_unique or not shadow.index.isin(panel.index).all():
                raise ValueError('Second-window source panel lacks previously open, cap-bound parents')
            aligned=panel.loc[shadow.index]
            if not shadow.second_entry_ms.equals(aligned.second_entry_ms):
                raise ValueError('Second-window source clock differs from decision')
            cost='stress_5bp' if slip else 'fee_only'
            expected=panel['cap_binding_'+cost]&panel[cost+'_position_status'].eq('live_at_decision_close')
            if (set(panel.index[expected])!=set(shadow.index) or not np.allclose(shadow.previous_net_usdt,
                    aligned[cost+'_decision_net_pnl_after_costs_usdt'],rtol=1e-8,atol=1e-6)):
                raise ValueError('Earlier order cash or eligible source roster fails independent decision-close check')
            observed=aligned.capacity_status.eq('observed')
            if not np.allclose(aligned.loc[observed,'second_entry_limit_10pct_usdt'],
                               .1*aligned.loc[observed,'prior_60m_median_usdt']):
                raise ValueError('Source second opening capacity mismatch')
            shadow['second_entry_limit_10pct_usdt']=aligned.second_entry_limit_10pct_usdt.where(observed,0.)
            shadow['second_native_open']=aligned.second_open_price_usdt
            for arm in arms:
                outcome=run(context,label,slip,arm,model,levered,limits,shadow,directory=directory)
                results[label+'_'+arm]=outcome
                print(label,arm,outcome['metrics']['all'],flush=True)
        del model
        gc.collect()
    if not preflight:
        cash.queue.repair.archive.prices.safe_json(directory/'summary.json',results)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--preflight',action='store_true')
    tests=parser.add_mutually_exclusive_group()
    tests.add_argument('--all-cap-bound',action='store_true')
    tests.add_argument('--original-price',action='store_true')
    args=parser.parse_args()
    if args.all_cap_bound:
        main(args.preflight,directory=cash.ROOT/'exp-cloud-close-complete-order-20261109-v1',arms=('all',))
    elif args.original_price:
        main(args.preflight,directory=cash.ROOT/'exp-cloud-close-deferred-price-limit-20261109-v1',arms=('original_price',))
    else:
        main(args.preflight)
