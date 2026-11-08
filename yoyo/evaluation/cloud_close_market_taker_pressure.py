"""Transfer the frozen V1.7 closed-key taker-volume screen to the complete Binance signal universe."""
import gc
import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as venue
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_second_reclaim as reclaim
from yoyo.evaluation import cloud_close_taker_pressure as pilot
from yoyo.evaluation import monday_retest_flow_data as native

EXP = pilot.ROOT/'experiments/active/exp-cloud-close-market-taker-pressure-20261109-v1'
MARKET = pilot.ROOT/'data/crypto/market'
HOUR = 3_600_000
KEY = 12*HOUR
ARCHIVES = [
    (15, MARKET/'kline_preholdout_binance_um15m/downloads'),
    (5, MARKET/'kline_preholdout_binance_um5m/downloads'),
    (5, MARKET/'kline_binance_um5m_forward_20260922/downloads'),
    (5, MARKET/'research-spike_v128_recent_20260923/downloads'),
    (15, pilot.FLOW_INPUT/'klines15m'),
]


def measurement(close, raw, minutes, canonical):
    """Use TS/OHLC/VOL/BUY from [close-12h,close), checked against each H1 OHLC."""
    step = minutes*60_000
    wanted = np.arange(close-KEY, close, step)
    positions = np.searchsorted(raw[:,native.TS],wanted)
    if np.any(positions >= len(raw)) or not np.array_equal(raw[positions,native.TS],wanted):
        return 'missing_bars', None
    bars = raw[positions].reshape(12,60//minutes,-1)
    aggregate = np.column_stack((bars[:,0,native.OPEN],bars[:,:,native.HIGH].max(axis=1),
                                 bars[:,:,native.LOW].min(axis=1),bars[:,-1,native.CLOSE]))
    if canonical is None or not np.allclose(aggregate, canonical, rtol=1e-9, atol=1e-15):
        return 'hourly_price_mismatch', None
    volume = bars[:,:,native.VOL]
    buys = bars[:,:,native.BUY]
    if not np.isfinite(buys).all() or volume.sum() <= 0:
        return 'unknown_native_flow', None
    return 'valid', float(2*buys.sum()/volume.sum()-1)


def symbol_features(job):
    symbol, entries = job
    ticker = symbol+'USDT'
    clock, hourly = venue.quotes(symbol)
    lookup = {}
    by_month = {}
    for close in entries:
        if close % KEY:
            raise ValueError(f'Off-grid key {symbol}, {close}')
        times = np.arange(close-KEY, close, HOUR)
        rows = np.searchsorted(clock, times)
        lookup[close] = hourly[rows] if (rows < len(clock)).all() and np.array_equal(clock[rows],times) else None
        month = pd.Timestamp(close-KEY,unit='ms',tz='UTC').strftime('%Y-%m')
        by_month.setdefault(month,[]).append(close)
    result = []
    for month, keys in by_month.items():
        missing = dict.fromkeys(keys, 'no_raw_archive')
        sources = [(mins,root/ticker/f'{ticker}-{mins}m-{month}.zip') for mins,root in ARCHIVES]
        if month == '2022-02':
            for day in (26,27,28):
                sources.append((5,venue.patch.DATA_DIR/f'{ticker}-5m-2022-02-{day}.zip'))
        for minutes, path in sources:
            if not missing:
                break
            if not path.is_file():
                continue
            raw = native._read_month(path,month,minutes*60_000)
            if np.any(np.diff(raw[:,native.TS]) <= 0):
                raise ValueError(f'Unsorted or duplicate source clock {path}')
            for close in list(missing):
                status, value = measurement(close,raw,minutes,lookup[close])
                if status == 'valid':
                    result.append((symbol,close,status,value,str(path.relative_to(pilot.ROOT))))
                    del missing[close]
                elif status != 'missing_bars' or missing[close] == 'no_raw_archive':
                    missing[close] = status
        result.extend((symbol,close,reason,np.nan,None) for close,reason in missing.items())
    return result


def pilot_check(frame):
    previous = pd.read_csv(pilot.EXP/'event_flow_screen.csv.gz',dtype={'symbol':str})
    previous = previous[previous.flow_status.str.startswith('valid_')].set_index('event_id')
    compare = frame.set_index('event_id').loc[previous.index]
    available = compare.flow_status.str.startswith('valid_')
    if not available.all():
        raise ValueError(f'Lost {int((~available).sum())} valid pilot windows in market transfer')
    discrepancy = np.abs(previous.flow_imbalance - compare.flow_imbalance)
    if discrepancy.max() > 1e-7 or not previous.flow_opposes_side.equals(compare.flow_opposes_side):
        raise ValueError(f'Pilot sign or magnitude mismatch: {discrepancy.max()}')
    return dict(pilot_valid_reconciled=len(compare), maximum_absolute_flow_difference=float(discrepancy.max()))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Market flow transfer already completed')
    paths = venue.assembled(set(venue.patch.affected_tickers()))
    events = paths.loc[paths.tp_r.eq(0),['event_id','parent_id','symbol','entry_ms','side','is_control']].copy()
    cached = EXP/'event_flow_screen.csv.gz'
    if cached.exists():
        frame = pd.read_csv(cached, dtype={'symbol':str}, float_precision='round_trip')
        if not frame[events.columns].equals(events.reset_index(drop=True)):
            raise ValueError('Saved features do not match current event roster')
    else:
        jobs = [(s, g.entry_ms.unique().tolist()) for s,g in events.groupby('symbol',sort=True)]
        data = []
        with ProcessPoolExecutor(max_workers=3) as executor:
            for i, result in enumerate(executor.map(symbol_features, jobs, chunksize=1),1):
                data.extend(result)
                if i % 50 == 0:
                    print('Read original Binance archives for',i,'of',len(jobs),'contracts',flush=True)
        measured = pd.DataFrame(data,columns=['symbol','entry_ms','raw_status','flow_imbalance','source'])
        frame = events.merge(measured,on=['symbol','entry_ms'],validate='many_to_one',how='left')
        if frame.raw_status.isna().any() or len(frame)!=len(events):
            raise ValueError('Missing transfer event')
        frame['flow_opposes_side'] = (frame.side*frame.flow_imbalance).lt(0)
        frame['flow_status'] = np.where(frame.raw_status.eq('valid'),
                                   np.where(frame.flow_opposes_side,'valid_opposing','valid_not_opposing'),frame.raw_status)
        pilot_check(frame)
        frame.to_csv(cached,index=False)
    summary = pilot_check(frame)
    cohorts = pilot._event_sets(frame)
    cohorts['opposing_ex_pilot'] = set(frame.loc[~frame.is_control & frame.flow_opposes_side
                                                   & ~frame.symbol.isin(pilot.PANEL),'event_id'])
    cohorts['available_ex_pilot'] = set(frame.loc[~frame.is_control & frame.raw_status.eq('valid')
                                                   & ~frame.symbol.isin(pilot.PANEL),'event_id'])
    cohorts['opposing_stressed'] = cohorts['opposing_actual']
    all_real = frame.loc[~frame.is_control]
    baseline = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = baseline[baseline.venue.eq('Binance') & baseline.scenario.eq('actual')].assign(cohort='unfiltered').to_dict('records')
    for key, ids in cohorts.items():
        if key == 'available_control':
            continue
        fee_slip = .0005 if key == 'opposing_stressed' else 0.
        metrics, trades, equity = pilot._run_account(paths[paths.event_id.isin(ids)],fee_slip)
        rows.extend(dict(cohort=key,period=p,**m) for p,m in metrics.items())
        if key == 'opposing_actual':
            trades.to_csv(EXP/'funded.csv.gz')
            equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
            summary.update(dev_pass=reclaim.meets(metrics['dev']), full_pass=reclaim.meets(metrics['all']))
        del equity,trades
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    summary.update(coverage=all_real.raw_status.value_counts().to_dict(), screened_contracts=int(all_real.symbol.nunique()),
                   covered_contracts=int(all_real.loc[all_real.raw_status.eq('valid'),'symbol'].nunique()),
                   counts={k:len(v) for k,v in cohorts.items()})
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')][['cohort','final_usdt','trades','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__ == '__main__':
    main()
