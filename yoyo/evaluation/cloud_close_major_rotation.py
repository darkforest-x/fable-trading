"""Rotate one fully funded position between BTC, ETH and cash after closed 12H cloud updates."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_transfer as stats
from yoyo.evaluation import cloud_close_major_cross as source

EXP=source.EXP.parent/'exp-cloud-close-major-rotation-20261109-v1'
SYMBOLS=['BTC','ETH']


def bullish(close):
    """V1.7 confirmed 12H closing-price EMA20/50 spread, seeded and warmed on 50 consecutive bars."""
    rows=close.rename('close').rename_axis('time').reset_index().to_dict('records')
    result=source.engine._ema_points_12h(rows)
    return (result.ready & result.ema20.gt(result.ema50)).to_numpy()


def targets(loaded):
    first,second=[loaded[s]['bars12'].close for s in SYMBOLS]
    states=pd.DataFrame({'btc_up':bullish(first),'eth_up':bullish(second),
                         'ratio_up':bullish(second/first)},index=first.index+source.STEP_12H)
    for mode,eth in {'relative':states.ratio_up,'inverse':~states.ratio_up,
                     'btc_first':False,'eth_first':True}.items():
        states[mode]=np.select([states.eth_up & (~states.btc_up|eth),states.btc_up],[1,0],default=-1)
    return states


def run(loaded,states,name,slip,*,symbols=SYMBOLS,directory=EXP,portfolio_builder=None,reference=None,fee_rate=source.TAKER):
    clock=loaded['BTC']['bars1'].index
    series=states[name].reindex(clock,method='ffill').fillna(-1).astype(int)
    current=series.to_numpy()
    prior=np.r_[-1,current[:-1]]
    changed=prior!=current
    if not np.all(clock[changed]%source.STEP_12H==0):
        raise ValueError('An ordinary rotation is outside a 12H decision')
    def prices(field):
        values=np.column_stack([loaded[s]['bars1'][field].reindex(clock) for s in symbols])
        held=current[:,None]==np.arange(len(symbols))[None,:]
        if field=='open':
            held|=prior[:,None]==np.arange(len(symbols))[None,:]
        if not (np.isfinite(values[held]) & (values[held]>0)).all():
            raise ValueError(f'Missing genuine native {field} at a held or traded hour')
        return np.where(np.isfinite(values),values,1.)
    opening,high,low,closing=map(prices,['open','high','low','close'])
    marks=np.empty((2*len(clock),len(symbols)))
    marks[::2],marks[1::2]=opening,closing
    size=np.full_like(marks,np.nan)
    call_seq=np.tile(np.arange(len(symbols)),(len(size),1))
    for i in np.flatnonzero(changed):
        old,new=prior[i],current[i]
        if old>=0:
            size[2*i,old]=-np.inf
            call_seq[2*i,0],call_seq[2*i,old]=old,0
        if new>=0:
            size[2*i,new]=np.inf
    if current[-1]>=0:
        size[-1,current[-1]]=-np.inf
    if portfolio_builder is None:
        pf=source.wallet.vbt.Portfolio.from_orders(marks,size=size,price=marks,direction='longonly',
            fees=fee_rate,slippage=slip,init_cash=1000.,group_by=True,cash_sharing=True,call_seq=call_seq,
            allow_partial=True,raise_reject=True)
    else:
        pf=portfolio_builder(marks,size,call_seq,slip)
    positions=np.asarray(pf.assets())[::2]
    actual=np.where(positions.max(axis=1)>1e-10,positions.argmax(axis=1),-1)
    if (positions>1e-10).sum(axis=1).max()>1 or not np.array_equal(current,actual):
        raise ValueError('Open positions differ from the chosen single asset')
    orders=pf.orders.records
    if len(orders)!=np.count_nonzero(~np.isnan(size)) or not np.allclose(orders.fees,orders.price*orders['size']*fee_rate):
        raise ValueError('Native market order count or notional fee mismatch')
    trades=pf.trades.records.copy()
    if not trades.status.eq(1).all() or np.asarray(pf.assets())[-1].max()>1e-9:
        raise ValueError('Terminal native long is unsettled')
    funds=pd.DataFrame(dict(symbol=np.asarray(symbols)[trades.col],entry_ms=clock.to_numpy()[trades.entry_idx//2],
        exit_bar_ms=clock.to_numpy()[trades.exit_idx//2],entry_price=trades.entry_price,exit_price=trades.exit_price,
        notional=trades['size']*trades.entry_price,cash_pnl=trades.pnl,fees=trades.entry_fees+trades.exit_fees))
    funds['net_return']=funds.cash_pnl/funds.notional
    values=np.asarray(pf.value()).reshape(-1,2)
    after_open=values[:,0]
    previous_units=np.vstack([np.zeros(len(symbols)),positions[:-1]])
    cash_open=np.asarray(pf.cash()).reshape(-1,2)[:,0]
    prior_cash=np.r_[1000.,cash_open[:-1]]
    before_open=prior_cash+(previous_units*opening).sum(axis=1)
    trough=cash_open+(positions*low).sum(axis=1)
    peak=cash_open+(positions*high).sum(axis=1)
    floor=np.minimum.reduce([trough,before_open,after_open,values[:,1]])
    ceiling=np.maximum.reduce([peak,before_open,after_open,values[:,1]])
    index=pd.to_datetime(clock.to_numpy()+source.HOUR_MS,unit='ms',utc=True)
    curve=pd.DataFrame(dict(nav=values[:,1],open_nav=before_open,open_exit_nav=after_open,
                            lower_nav=floor,upper_nav=ceiling),index=index)
    if not np.isclose(1000.+funds.cash_pnl.sum(),curve.nav.iat[-1],atol=1e-6):
        raise ValueError('Actual two-asset round trips fail terminal cash reconciliation')
    prior_peak=np.maximum.accumulate(np.r_[1000.,ceiling[:-1]])
    # All current equities follow prior hours. Within each hour the open precedes
    # the wick and the wick precedes the close, but the two wicks are unordered.
    min_ratio=min(np.min(floor/prior_peak),np.min(trough/np.maximum(before_open,after_open)),
                  np.min(values[:,1]/ceiling))
    metrics=stats.summarize(funds,curve)
    confirmed=100.*(1.-min_ratio)
    if not metrics['all']['dd_mark_pct']-1e-8<=confirmed<=metrics['all']['dd_bound_pct']+1e-8:
        raise ValueError('Verified chronological wick drawdown outside its bounds')
    key=name+('_stress' if slip else '')
    if reference is None:
        funds.to_csv(directory/f'funded_{key}.csv.gz',index=False)
        curve.to_csv(directory/f'hourly_{key}.csv.gz',index_label='hour_end_utc')
    else:
        old,old_curve=reference
        if (not funds[['symbol','entry_ms','exit_bar_ms']].equals(old[['symbol','entry_ms','exit_bar_ms']])
                or not np.allclose(old[['cash_pnl','notional']],funds[['cash_pnl','notional']],rtol=1e-7,atol=1e-5)
                or not curve.index.equals(old_curve.index)
                or not np.allclose(old_curve,curve[old_curve.columns],rtol=1e-7,atol=1e-5)):
            raise ValueError('Rotation adapter fails original trade or hourly equity parity')
    return dict(metrics=metrics,confirmed_wick_dd_pct=float(confirmed),orders=len(orders),
                fees_usdt=float(orders.fees.sum()),hours_btc=int((current==symbols.index('BTC')).sum()),
                hours_eth=int((current==symbols.index('ETH')).sum()),hours_cash=int((current==-1).sum()),
                hours_by_symbol={s:int((current==i).sum()) for i,s in enumerate(symbols)})


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native 12H relative rotation')
    loaded,start,end,_=source._load_common(json.loads(source.CONFIG.read_text()))
    states=targets(loaded)
    states.to_csv(EXP/'decisions.csv.gz',index_label='earliest_order_ms')
    out={}
    for name in ['relative','inverse','btc_first','eth_first']:
        for slip in [0.,.0005]:
            key=name+('_stress' if slip else '')
            out[key]=run(loaded,states,name,slip)
            print(key,out[key]['metrics']['all'],flush=True)
    source._write_json(EXP/'summary.json',dict(start_ms=start,end_ms=end,arms=out))


if __name__=='__main__':
    main()
