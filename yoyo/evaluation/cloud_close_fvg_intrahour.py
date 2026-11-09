"""Bound a traded-price drawdown from native minute ranges and the actual funded order history."""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.binance_um_vision_fetch import BASE,fetch
from yoyo.evaluation import cloud_close_fvg_priority_funding as funding
from yoyo.evaluation import cloud_close_november_paths as path

EXP = funding.EXP.parent/'exp-cloud-close-fvg-intrahour-20261109-v1'
CACHE = path.ROOT/'data/research/cloud_close_efficiency_binance_dd'
STAGES = ['open_nav','open_exit_nav','post_trim_nav','nav']
HOUR = path.HOUR


@lru_cache(maxsize=60)
def native_minute(symbol,hour):
    ticker = symbol+'USDT'
    day = pd.Timestamp(hour,unit='ms',tz='UTC').date().isoformat()
    name = f'{ticker}-1m-{day}.zip'
    url = f'{BASE}/daily/klines/{ticker}/1m/{name}'
    code = fetch(CACHE,'official_1m',ticker,url)[2]
    if code not in ('cached','fetched'):
        raise IOError(f'Missing Binance futures minute prices {ticker} {day}')
    frame = pd.read_csv(CACHE/'official_1m'/ticker/name,header=None,usecols=range(5),names=['time',*path.OHLC])
    if str(frame.time.iat[0]).lower() in ('open_time','open time'):
        frame = frame.iloc[1:].copy()
    frame['time'] = pd.to_numeric(frame.time,errors='raise').astype('int64')
    bars = frame.set_index('time').loc[hour:hour+HOUR-1].astype(float)
    if not np.array_equal(bars.index,hour+np.arange(60)*60000) or not np.isfinite(bars).all().all():
        raise ValueError(f'Incomplete or invalid native minute interval {symbol} {hour}')
    with np.load(path.history_slot(ticker)/'quotes.npz') as q:
        i = q['clock'].searchsorted(hour)
        if q['clock'][i]!=hour:
            raise ValueError(f'No original H1 for {symbol} {hour}')
        original = q['prices'][i]
    if not np.allclose(original,[bars.open.iat[0],bars.high.max(),bars.low.min(),bars.close.iat[-1]],rtol=1.e-11,atol=1.e-13):
        raise ValueError(f'Original H1 and minute prices differ {symbol} {hour}')
    return original,bars,url


def slice_peak(folder,equity,t):
    hour = t.value//1_000_000-HOUR
    row = equity.loc[t]
    legs = pd.read_csv(folder/'funded_legs.csv.gz',dtype={'symbol':str}).set_index(['event_id','tp_r'])
    orders = pd.read_csv(folder/'orders.csv.gz')
    included = (orders.hour_open_ms<hour)|(orders.hour_open_ms.eq(hour)&orders.stage.le(2))
    orders = orders[included].copy()
    orders['units'] = np.where(orders.side.eq(0),orders['size'],-orders['size'])
    actual = orders.groupby(['event_id','tp_r']).units.sum()
    held = legs[legs.entry_ms.le(hour)&legs.exit_bar_ms.ge(hour)&~(legs.exit_bar_ms.eq(hour)&legs.exit_phase.eq('open'))].copy()
    held['units'] = actual.reindex(held.index)
    if held.units.isna().any() or (held.units*held.side<=0).any():
        raise ValueError('Missing actual post-trim child quantity at peak hour')
    flows = pd.read_csv(folder/'applied_funding.csv.gz',usecols=['scheduled_stage','applied','funding_cashflow_usdt'])
    first = (hour-path.venue.START)//HOUR*4
    later = flows.loc[flows.applied & flows.scheduled_stage.isin([first+2,first+3]),'funding_cashflow_usdt']
    lo = np.full(60,row.post_trim_pre_funding_nav+later.clip(upper=0).sum())
    hi = np.full(60,row.post_trim_pre_funding_nav+later.clip(lower=0).sum())
    ending = float(row.post_trim_pre_funding_nav+later.sum())
    known_ranges = [np.zeros(60)]
    provenance = []
    for symbol,group in held.groupby('symbol'):
        ohlc,bars,url = native_minute(symbol,hour)
        was = bars.close.shift().fillna(ohlc[0])
        low = np.minimum(bars.low,was).to_numpy()
        high = np.maximum(bars.high,was).to_numpy()
        guaranteed_part = np.zeros(60)
        live_full_hour = not group.exit_bar_ms.eq(hour).any()
        for r in group.itertuples():
            left,right = (low-ohlc[0])*r.units,(high-ohlc[0])*r.units
            adverse,favorable = np.minimum(left,right),np.maximum(left,right)
            if r.exit_bar_ms==hour:
                settled = (r.native_exit_fill_price-ohlc[0])*r.units-r.native_exit_fee_usdt
                adverse,favorable = np.minimum(adverse,settled),np.maximum(favorable,settled)
                ending += settled
            else:
                ending += (ohlc[3]-ohlc[0])*r.units
                # Use a price actually traded in each minute for the witness,
                # even when a previous close enlarged the conservative bounds.
                traded = bars.high.to_numpy() if r.units>0 else bars.low.to_numpy()
                guaranteed_part += (traded-ohlc[0])*r.units-adverse
            lo += adverse
            hi += favorable
        if live_full_hour:
            known_ranges.append(guaranteed_part)
        provenance.append(dict(symbol=symbol,utc_hour_open_ms=hour,source=url,minutes=len(bars)))
    if abs(ending-row.nav)>.025:
        raise ValueError(f'Native sub-hour quantities and cash do not explain the H1 close: {ending} vs {row.nav}')
    witness = lo+np.maximum.reduce(known_ranges)
    if (witness>hi+.01).any():
        raise ValueError('Peak witness exceeds conservative minute envelope')
    return pd.DataFrame(dict(minute_ms=hour+np.arange(60)*60000,low=lo,high=hi,witness=witness)),provenance


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed priority peak refinement')
    source = json.loads((funding.EXP/'summary.json').read_text())
    output = {}
    receipts = []
    for scenario in source['scenarios']:
        folder = funding.EXP/scenario
        equity = pd.read_csv(folder/'hourly_equity.csv.gz',parse_dates=[0],index_col=0)
        dd_bound = 1-equity.lower_nav/equity.upper_nav.cummax().clip(lower=1000)
        trough = dd_bound.idxmax()
        peak = equity.loc[:trough].upper_nav.idxmax()
        minutes,urls = slice_peak(folder,equity,peak)
        minutes.to_csv(EXP/f'minutes_{scenario}.csv',index=False)
        receipts.extend(urls)
        stages = equity.loc[peak,STAGES].astype(float)
        all_orders = pd.read_csv(folder/'orders.csv.gz',usecols=['hour_open_ms','price','size','fees'])
        peak_hour = peak.value//1_000_000-HOUR
        peak_fees = all_orders.loc[all_orders.hour_open_ms.eq(peak_hour),'fees'].sum()
        all_flows = pd.read_csv(folder/'applied_funding.csv.gz',
                                usecols=['scheduled_stage','applied','funding_cashflow_usdt'])
        peak_offset = (peak_hour-path.venue.START)//HOUR
        peak_flows = all_flows.loc[all_flows.applied&all_flows.scheduled_stage.floordiv(4).eq(peak_offset),
                                   'funding_cashflow_usdt'].abs().sum()
        worst_later = equity.loc[equity.index>peak,STAGES].stack().idxmin()
        later_mark = float(equity.loc[worst_later])
        known = max(float(stages.max()),float(minutes.witness.max()))
        if known<=0 or later_mark<=0:
            raise ValueError('Invalid marked NAV evidence')
        revised = equity.upper_nav.copy()
        upper = max(max(stages.max(),equity.loc[peak,'post_trim_pre_funding_nav'])+peak_fees+peak_flows,
                    minutes.high.max())
        revised.loc[peak] = min(revised.loc[peak],upper)
        witness_dd = max(source['scenarios'][scenario]['metrics']['all']['dd_mark_pct'],100*(1-later_mark/known))
        tighter = 100*float((1-equity.lower_nav/revised.cummax().clip(lower=1000)).max())
        if tighter+1.e-7<witness_dd:
            raise ValueError('Drawdown witness exceeded estimated upper bound')
        output[scenario] = dict(peak_hour_end_utc=str(peak),possible_trough_hour_end_utc=str(trough),
                    minute_witness_utc=str(pd.Timestamp(int(minutes.loc[minutes.witness.idxmax(),'minute_ms']),unit='ms',tz='UTC')),
                    later_witness_hour_end_utc=str(worst_later[0]),later_witness_stage=worst_later[1],
                    later_witness_nav_usdt=later_mark,earlier_witness_peak_usdt=known,refined_peak_max_usdt=upper,
                    dynamic_dd_lower_pct=witness_dd,refined_full_upper_pct=tighter,
                    source_h1_upper_pct=float(dd_bound.max()*100),risk_breach_witness=bool(witness_dd>35))
    pd.DataFrame(receipts).drop_duplicates().to_csv(EXP/'minute_sources.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps(output,indent=2),flush=True)


if __name__=='__main__':
    main()
