"""Bound critical drawdown peak/trough hours with synchronized actual OKX minute prices."""
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_double_directory as experiment

engine = experiment.exposure
wallet = engine.wallet
EXP = engine.EXP.parent/'exp-cloud-close-double-minute-risk-20261109-v1'
CACHE = wallet.ROOT/'data/research/cloud_close_double_minute_risk'
HOUR = wallet.HOUR
FIELDS = ['open','high','low','close']
STAGES = ['open_nav','open_exit_nav','post_trim_nav','nav']


def targets(curve):
    troughs = curve.loc[curve.lower_nav/curve.upper_nav.cummax().clip(lower=1000)<=.5].index
    peaks = {p for t in troughs for p in curve.loc[:t].index[curve.loc[:t,'upper_nav']>=2*curve.at[t,'lower_nav']]}
    return sorted(peaks|set(troughs))


def minute_quote(symbol,hour,quotes):
    """Use exact native time/price OHLC. A conflicting or absent H1 match leaves its bound intact."""
    inst = symbol+'-USDT-SWAP'
    day = pd.Timestamp(hour,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
    slot = CACHE/inst
    slot.mkdir(parents=True,exist_ok=True)
    filename = f'{inst}-candlesticks-{day}.zip'
    url = f'https://static.okx.com/cdn/okex/traderecords/candlesticks/daily/{day.replace("-","")}/{filename}'
    file = slot/filename
    absent = file.with_suffix('.404')
    if not (file.is_file() or absent.is_file()):
        res = requests.get(url,timeout=(8,22))
        if res.status_code==404:
            absent.write_text(url)
        else:
            res.raise_for_status()
            file.write_bytes(res.content)
    def valid(x):
        return (np.array_equal(x.index,hour+np.arange(60)*60000) and np.isfinite(x[FIELDS]).all().all() and
                x.low.gt(0).all() and x.low.le(x[['open','close']].min(axis=1)).all() and
                x.high.ge(x[['open','close']].max(axis=1)).all())
    bars = pd.DataFrame()
    if file.is_file():
        raw = pd.read_csv(file, usecols=['instrument_name','open_time','confirm',*FIELDS],float_precision='round_trip')
        if set(raw.instrument_name)!={inst}:
            raise ValueError('Invalid native archive instrument')
        bars = raw[raw['confirm'].eq(1)].set_index('open_time').sort_index().loc[hour:hour+HOUR-1,FIELDS]
    if not len(bars) or not valid(bars):
        cache = slot/f'api-{hour}.json'
        if not cache.exists():
            res = requests.get('https://www.okx.com/api/v5/market/history-candles',params=dict(
                    instId=inst,bar='1m',after=hour+HOUR,limit=60),timeout=(8,22))
            res.raise_for_status()
            body = res.json()
            if body.get('code') not in ('0','51001'):
                raise ValueError(f'Unexpected OKX response {inst}: {body.get("code")}')
            cache.write_text(json.dumps(body))
        body = json.loads(cache.read_text())
        if not body['data']:
            return None,'minute_archive_and_history_unavailable'
        api = pd.DataFrame(body['data'],columns=['time',*FIELDS,'vol','vol_ccy','volume_quote','confirm']).apply(pd.to_numeric)
        bars = api[api['confirm'].eq(1)].set_index('time').sort_index()[FIELDS]
        url = 'https://www.okx.com/api/v5/market/history-candles'
    if not valid(bars):
        return None,'missing_or_unconfirmed_native_minutes'
    clock,ohlc = quotes(symbol)
    i = clock.searchsorted(hour)
    actual = [bars.open.iloc[0],bars.high.max(),bars.low.min(),bars.close.iloc[-1]]
    if clock[i]!=hour or not np.allclose(actual,ohlc[i],rtol=1.e-8,atol=1.e-12):
        return None,'native_minute_ohlc_differs_from_frozen_H1'
    return bars,url


def snapshots(quotes,folder):
    base = pd.read_csv(experiment.EXP/'hourly_1.5x_stress.csv.gz',index_col=0,parse_dates=[0])
    old_positions = pd.read_csv(experiment.EXP/'funded_1.5x_stress.csv.gz',index_col='event_id')
    times = targets(base)

    def capture(pf,data,positions,equity,multiple,slip):
        if (not np.allclose(equity[base.columns],base,atol=.005,rtol=1.e-8) or
            not np.allclose(positions.loc[old_positions.index,'cash_pnl'],old_positions.cash_pnl,atol=.0001,rtol=1.e-8)):
            raise ValueError('Exposure receipts do not reproduce the audited stressed account')
        assets = np.asarray(pf.assets())
        legs = data['selected']
        results = []
        for end in times:
            hour = end.value//1_000_000-HOUR
            idx = int((hour-data['start'])//HOUR*4+2)
            active = legs[legs.entry_ms.le(hour)&legs.exit_bar_ms.ge(hour)]
            for row in active.itertuples():
                qty = assets[idx,row.slot]*multiple*row.side/row.entry_price
                if abs(qty)<1.e-13 or (hour==row.exit_bar_ms and row.exit_phase=='open'):
                    continue
                clock,prices = quotes(row.symbol)
                original_open = prices[clock.searchsorted(hour),0]
                results.append(dict(hour=hour,event_id=row.event_id,symbol=row.symbol,slot=row.slot,side=row.side,
                    units=qty,open=original_open,exit_now=row.exit_bar_ms==hour,exit_price=row.exit_price,
                    exit_phase=row.exit_phase,exit_reason=row.exit_reason,exit_fee_rate=row.exit_fee_rate))
        pd.DataFrame(results).to_csv(folder/'post_trim_units.csv.gz',index=False)

    return base,capture


def ranges(group,row,hour,quotes):
    """Envelope all other positions' extremes; preserve before/after paid price ambiguity within an exit minute."""
    low = np.full(60,row.post_trim_nav)
    high = low.copy()
    lo_close = low.copy()
    hi_close = low.copy()
    ending = row.post_trim_nav
    notes = []
    for symbol,legs in group.groupby('symbol'):
        bars,source = minute_quote(symbol,hour,quotes)
        notes.append(dict(hour=hour,symbol=symbol,source=source))
        if bars is None:
            return None,notes
        for r in legs.itertuples():
            adverse = ((bars.low if r.units>0 else bars.high).to_numpy()-r.open)*r.units
            favorable = ((bars.high if r.units>0 else bars.low).to_numpy()-r.open)*r.units
            close = (bars.close.to_numpy()-r.open)*r.units
            low_c,high_c = close.copy(),close.copy()
            if r.exit_now:
                market = r.exit_fee_rate>=wallet.TAKER
                executed = r.exit_price*(1-r.side*(.0005 if market else 0.))
                settled = (executed-r.open)*r.units-abs(r.units)*executed*r.exit_fee_rate
                if r.exit_phase=='close':
                    first = last = 59
                elif r.exit_phase=='intrabar':
                    # A jump across a stop/limit between adjacent minute closes
                    # can execute without printing the barrier inside that minute.
                    previous = np.r_[r.open,bars.close.to_numpy()[:-1]]
                    lower = np.minimum(bars.low.to_numpy(),previous)
                    upper = np.maximum(bars.high.to_numpy(),previous)
                    touched = np.flatnonzero((lower<=r.exit_price)&(upper>=r.exit_price))
                    if not len(touched):
                        return None,[*notes,dict(hour=hour,symbol=symbol,source='intrabar_trigger_not_bracketed')]
                    first,last = int(touched[0]),int(touched[-1])
                else:
                    raise ValueError('Open exit remained held after trim')
                adverse[first:last+1] = np.minimum(adverse[first:last+1],settled)
                favorable[first:last+1] = np.maximum(favorable[first:last+1],settled)
                low_c[first:last] = np.minimum(low_c[first:last],settled)
                high_c[first:last] = np.maximum(high_c[first:last],settled)
                adverse[last+1:] = favorable[last+1:] = settled
                low_c[last:] = high_c[last:] = settled
                ending += settled
            else:
                ending += close[-1]
            low += adverse
            high += favorable
            lo_close += low_c
            hi_close += high_c
    if abs(ending-row.nav)>.035 or (low>high+.0001).any() or (lo_close>hi_close+.0001).any():
        raise ValueError(f'Conserved native quantities do not explain H1 ending NAV for {hour}: {ending} != {row.nav}')
    return pd.DataFrame(dict(time=hour+np.arange(60)*60000,low=low,high=high,close_lower=lo_close,close_upper=hi_close)),notes


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed historical minute exposure audit')
    rows,quotes,_ = experiment.inputs()
    equity,capture = snapshots(quotes,EXP)
    if not (EXP/'post_trim_units.csv.gz').exists():
        data,_ = experiment.parent.assemble(rows,experiment.changed_limits(rows,quotes),'OKX',quotes)
        engine.simulate(data,engine.underlying(data,quotes),1.5,.0005,quote_loader=quotes,snapshot=capture)
        del data
    held = pd.read_csv(EXP/'post_trim_units.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    upper,lower = equity.upper_nav.copy(),equity.lower_nav.copy()
    attained_hi = equity[STAGES].max(axis=1)
    attained_lo = equity[STAGES].min(axis=1)
    notes,used,observations = [],[],[]
    for end in targets(equity):
        hour = end.value//1_000_000-HOUR
        bounded,meta = ranges(held[held.hour.eq(hour)],equity.loc[end],hour,quotes)
        notes.extend(meta)
        if bounded is None:
            continue
        used.append(end.isoformat())
        observations.append(bounded.assign(hour=hour))
        hi = max(bounded.high.max(),attained_hi.at[end])
        lo = min(bounded.low.min(),attained_lo.at[end])
        upper.at[end] = min(upper.at[end],hi)
        lower.at[end] = max(lower.at[end],lo)
        attained_hi.at[end] = max(attained_hi.at[end],bounded.close_lower.max())
        attained_lo.at[end] = min(attained_lo.at[end],bounded.close_upper.min())
    result = 100*(1-lower/upper.cummax().clip(lower=1000))
    # Earlier hours for distinct-hour guaranteed witnesses. Also retain the
    # original fully ordered within-hour stage witness for the same hour.
    old = json.loads((experiment.EXP/'summary.json').read_text())['cases']['fixed_5bp']['metrics']['all']
    witness = max(old['dd_mark_pct'],100*(1-(attained_lo/attained_hi.cummax().shift().fillna(1000).clip(lower=1000)).min()))
    if witness>result.max()+.00001:
        raise ValueError('Attained witness exceeds every possible drawdown')
    if observations:
        pd.concat(observations,ignore_index=True).to_csv(EXP/'minute_bounds.csv.gz',index=False)
    pd.DataFrame(notes).to_csv(EXP/'source_receipts.csv',index=False)
    obj = dict(original_upper_pct=old['dd_bound_pct'],original_observed_pct=old['dd_mark_pct'],
        refined_lower_pct=witness,refined_upper_pct=result.max(),fully_refined=used,
        targeted_hours=len(targets(equity)),remaining_potential_breach_hours=int(result.ge(50).sum()),
        scope='This bounds traded-price drawdown for the frozen 5bp-slippage 360-name account, including its published CVC gap exit. Historical funding and unobserved margin marks remain unresolved.')
    (EXP/'summary.json').write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
    print(obj,flush=True)


if __name__=='__main__':
    main()
