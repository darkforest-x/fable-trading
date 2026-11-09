"""Measure realized and still-invested cash for each actual 12H trade, using the framework's native fills."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_liquid_queue as queue

ROOT=queue.EXP.parent
EXP=ROOT/'exp-cloud-close-cash-giveback-20261109-v1'
SOURCE=ROOT/'exp-cloud-close-vix-priority-medians-20261109-v1'
ROSTER=ROOT/'exp-cloud-close-vix-priority-20261109-v1'/'aligned'
H=queue.repair.wallet.HOUR
STEP=12*H


def account():
    quotes,sources=queue.repair.checked_quotes()
    rows=pd.read_csv(ROSTER/'selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    data,_=queue.repair.old.parent.assemble(rows,queue.repair.old.changed_limits(rows,quotes),'OKX',quotes)
    market=queue.engine.base.underlying(data,quotes)
    parents=market[-1].set_index('event_id')
    raw=pd.read_csv(SOURCE/'source_rows.csv.gz',index_col='event_id',float_precision='round_trip').reindex(parents.index)
    if not parents.entry_ms.equals(raw.entry_ms):
        raise ValueError('Source timestamps changed')
    caps=(.1*raw.prior_median).where(raw.prior_status.eq('observed'),0.).fillna(0)
    caps=caps.where(caps>=1e-6,0.)
    ratios=1.5-.5*parents.side*parents.entry_ms.map(queue.rank.reference.study.season.direction(sources))
    return data,market,quotes,parents,caps,ratios


def parent_marks(orders,meta,fund,quotes,slip):
    """Actual fills by H1, native H1 close, and remaining inventory only; previous peaks at finished 12H."""
    ts,prices=quotes(meta.symbol)
    start,end=int(orders.hour.min()),int(orders.hour.max())
    hours=np.arange(start,end+H,H)
    ix=ts.searchsorted(hours)
    if np.any(ix>=len(ts)) or not np.array_equal(ts[ix],hours):
        raise ValueError('Missing occupied native holding hour')
    steps=orders.groupby('hour')[['underlying_cash','net_units']].sum().reindex(hours,fill_value=0.).cumsum()
    cash=steps.underlying_cash.to_numpy()
    units=steps.net_units.to_numpy()
    if units.min()<-1e-9*max(units.max(),1.) or not np.isclose(cash[-1],fund.cash_pnl,atol=1e-5,rtol=1e-8):
        raise ValueError('Native running cash or signed inventory failed to reconcile')
    close=prices[ix,3]
    marked=cash+meta.side*units*close
    liq_price=close*(1-meta.side*slip)
    liquid=cash+(meta.side-.0005)*units*liq_price
    still_open=units>max(orders.net_units.max()*1e-9,1e-12)
    decision=(hours+H)%STEP==0
    sample=pd.Series(liquid[decision&still_open],index=(hours+H)[decision&still_open])
    peak=float(sample.max()) if len(sample) else 0.
    risk=fund.notional*abs(meta.stop_price-meta.entry_price)/meta.entry_price
    if not risk>0:
        raise ValueError('Initial cash risk missing')
    previous=sample.cummax().shift()
    signal=sample[(previous>=2*risk)&(sample<=.5*previous)]
    stats=dict(event_id=fund.name,symbol=meta.symbol,side=int(meta.side),entry_ms=start,
               final_cash_pnl=float(fund.cash_pnl),initial_cash_risk=float(risk),observed_12h_closes=len(sample),
               best_live_12h_cash=peak,live_peak_positive=peak>0,
               cash_returned_after_positive_peak=max(0.,peak-fund.cash_pnl) if peak>0 else 0.,
               trigger_ms=int(signal.index[0]) if len(signal) else None)
    return hours,np.diff(np.r_[0.,marked]),stats


def run(data,market,quotes,parents,caps,ratios,label,slip):
    ref=SOURCE/'aligned'
    funds=pd.read_csv(ref/f'funded_{label}.csv.gz',index_col='event_id',float_precision='round_trip')
    curve=pd.read_csv(ref/f'hourly_{label}.csv.gz',index_col=0,parse_dates=True)
    records=[]
    def collect(fills):
        # The persisted native units and signed cash allow replaying only the requested accounting diagnostics.
        records.append(fills[['underlying_event_id','hour','stage','col','underlying_cash','net_units','fees']].copy())
    def verify(pf,model,actual,equity,multiples,used_slip):
        if not (set(actual.index)==set(funds.index) and np.allclose(actual.loc[funds.index,['notional','cash_pnl']],
               funds[['notional','cash_pnl']],atol=1e-5,rtol=1e-9) and equity.index.equals(curve.index)
               and np.allclose(equity[curve.columns],curve,atol=.001,rtol=1e-9)):
            raise ValueError('Native fill extraction differs from the frozen cash account')
    output=queue.engine.simulate(data,market,quotes,ratios,slip,EXP,label,snapshot=verify,entry_limits=caps,fill_sink=collect)
    if 'metrics' not in output or len(records)!=1:
        raise ValueError('Unable to recover native fills for the exact funded cash account')
    fills=records[0]
    fills.to_csv(EXP/f'orders_{label}.csv.gz',index=False)
    return measure(fills,funds,curve,parents,quotes,slip,label)


def measure(fills,funds,curve,parents,quotes,slip,label):
    changes=np.zeros(len(curve))
    rows=[]
    for eid,orders in fills.groupby('underlying_event_id',sort=False):
        clocks,change,stats=parent_marks(orders,parents.loc[eid],funds.loc[eid],quotes,slip)
        indices=(clocks-(curve.index[0].value//1_000_000-H))//H
        changes[indices]+=change
        rows.append(stats)
    err=np.max(np.abs(1000+changes.cumsum()-curve.nav))
    if len(rows)!=len(funds) or err>.001:
        raise ValueError(f'Parent cash marks fail hourly portfolio reconciliation ({err})')
    frame=pd.DataFrame(rows)
    frame['trigger_ms']=frame.trigger_ms.astype('Int64')
    frame.to_csv(EXP/f'parents_{label}.csv.gz',index=False)
    results={}
    for name,part in [('all',frame),('through_2024',frame[frame.entry_ms<1735689600000]),
                      ('2025',frame[frame.entry_ms.between(1735689600000,1767225599999)]),
                      ('2026',frame[frame.entry_ms>=1767225600000])]:
        peaks=part[part.live_peak_positive]
        tr=part[part.trigger_ms.notna()]
        results[name]=dict(funded=len(part),positive_live_peak=len(peaks),ended_negative_after_positive=int(peaks.final_cash_pnl.lt(0).sum()),
            final_realized_pnl=float(part.final_cash_pnl.sum()),positive_peak_giveback_usdt=float(peaks.cash_returned_after_positive_peak.sum()),
            shadow_exits=len(tr),triggered_final_pnl=float(tr.final_cash_pnl.sum()),triggered_final_winners=int(tr.final_cash_pnl.gt(0).sum()))
    return dict(max_hourly_nav_error_usdt=float(err),periods=results)


if __name__=='__main__':
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed parent cash mark extraction')
    context=account()
    results={label:run(*context,label,slip) for label,slip in [('median',0.),('median_stress',.0005)]}
    queue.repair.archive.prices.safe_json(EXP/'summary.json',results)
