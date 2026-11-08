"""All discoverable OKX crypto USDT swaps: V1.7 original references and causal features.
Frozen frontier equals the prior Top20 study. The Top20 ledger is reused intact.
New symbols take the same confirmed 12H signal, NY Monday and chart excursion route.
Only completed candles through the decision, or earlier fully observed plan exits,
may feed the features. Path extrema and exit events are outcome data only.
"""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_peak10_inputs import EXP as TOP, ROOT, OHLC, dump
from yoyo.evaluation.cloud_peak10_census import census
from yoyo.monitor.cloud_signal_plans import atr_points
from yoyo.monitor.cloud_signals import STEP, ema_points

EXP = ROOT/'experiments/active/exp-cloud-allmarket-screen-20261108-v1'
WORK = EXP/'work'
OLD = pd.read_csv(TOP/'signals.csv',float_precision='round_trip')
TOP_COINS = set(OLD.symbol)
BAR = 12 * 3600_000
assert BAR == STEP
CUTOFF = int(pd.Timestamp('2026-11-08T12:00:00Z').timestamp()*1000)
NEW_FEATURES = ['trend_eff_14','btc_same_side','rel_strength_14','atr_expansion','prior_stop_rate_6',
                'prior_completed_n','prior_observed_12h','is_current','was_top20','cohort']


def read_bars(folder):
    bars = pd.read_csv(folder/'bars12h.csv.gz', index_col='time',float_precision='round_trip')
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    assert (bars.index.to_numpy(dtype=np.int64) % STEP == 0).all()
    assert (bars.index.to_numpy(dtype=np.int64) + STEP <= CUTOFF).all()
    return bars


def context(frame):
    """On each 12H key's opening index t, log return / efficiency use the 14
    consecutive close changes ENDING at t-1. ATR expansion is the previous
    close's Wilder ATR14 over the median of the preceding 60 Wilder ATR14s
    (ending at t-2). EMA direction uses the key's just-confirmed close."""
    records = frame[OHLC].reset_index().to_dict(orient='records')
    ema = pd.DataFrame(ema_points(records)).set_index('time')
    atr = pd.DataFrame(atr_points(records)).set_index('time').atr14
    out = pd.DataFrame(index=frame.index)
    out['span'] = ema['count']
    spread = np.sign(ema.ema20 - ema.ema50)
    out['ema_side'] = spread.where(ema.ready)
    # rolling windows on complete grid; the contiguous count excludes pre-gap data.
    lc = np.log(frame.close)
    ret = lc.diff(14).shift()
    path = lc.diff().abs().rolling(14).sum().shift()
    ready = ema['count'].ge(16)
    out['return14'] = ret.where(ready)
    out['efficiency'] = (ret / path.where(path.gt(0))).where(ready)
    norm = atr.shift(2).rolling(60).median()
    out['atr_expansion'] = (atr.shift() / norm.where(norm.gt(0))).where(ema['count'].ge(76))
    return out


@lru_cache(maxsize=1)
def bitcoin():
    return context(read_bars(TOP/'inputs'/'BTC'))


def previous_stops(frame, times, observed):
    """For each confirmation, among the previous 365 days take the last 6
    completed stop/flip outcomes by exit candle END. An exit is usable once
    that candle has closed. Exclude pre-current-listing signals for current
    listing decisions. Need >=3 completed events to estimate a rate."""
    eligible = frame.exit_reason.isin(['stop','cloud_flip']) & observed.notna()
    rates, counts = [], []
    for i, t in times.items():
        mask = (eligible & (times < t) & (observed <= t)
                & (observed >= t-pd.Timedelta(days=365))
                & (frame.pre_current_listing == frame.at[i,'pre_current_listing']))
        subset = observed[mask].sort_values(kind='stable').tail(6).index
        counts.append(len(subset))
        rates.append(frame.loc[subset,'exit_reason'].eq('stop').mean() if len(subset)>=3 else np.nan)
    return rates, counts


def enrich(frame, bars, spec):
    frame = frame.copy().reset_index(drop=True)
    if frame.empty:
        for col in NEW_FEATURES:
            frame[col] = pd.Series(dtype=float)
        return frame
    labels = context(bars)
    times = pd.to_datetime(frame.confirmed_bjt,utc=True)
    keys = (times.astype('int64')//1_000_000 - STEP).to_numpy()
    own, btc = labels.loc[keys].reset_index(drop=True), bitcoin().reindex(keys).reset_index(drop=True)
    frame['trend_eff_14'] = frame.side * own.efficiency
    frame['rel_strength_14'] = frame.side * (own.return14-btc.return14)
    frame['btc_same_side'] = (frame.side == btc.ema_side).astype(float).where(btc.ema_side.notna())
    frame['atr_expansion'] = own.atr_expansion
    observed = pd.to_datetime(frame.path_end_bar_bjt,utc=True,format='mixed',errors='coerce') + pd.Timedelta(hours=12)
    frame['prior_stop_rate_6'], frame['prior_completed_n'] = previous_stops(frame, times, observed)
    frame['prior_observed_12h'] = own.span
    frame['is_current'] = spec['is_current']
    frame['was_top20'] = spec['symbol'] in TOP_COINS
    frame['cohort'] = spec['cohort']
    return frame


def study_one(spec):
    symbol = spec['symbol']
    marker = WORK/(symbol+'.json')
    if marker.exists():
        return json.loads(marker.read_text())
    known = symbol in TOP_COINS
    folder = (TOP if known else EXP)/'inputs'/symbol
    if not (folder/'receipt.json').exists():
        return None
    if not known and json.loads((folder/'receipt.json').read_text()).get('status') != 'complete':
        return None
    if not (folder/'bars12h.csv.gz').exists() or not (folder/'bars1h.csv.gz').exists():
        return None
    bars = read_bars(folder)
    if known:
        raw = OLD[OLD.symbol.eq(symbol)].copy()
        oldcov = pd.read_csv(TOP/'coverage.csv').set_index('symbol').loc[symbol].to_dict()
        cov = dict(oldcov,symbol=symbol)
    elif not bars.empty:
        spec['rank'] = np.nan
        signals, cov = census(spec,folder)
        raw = pd.DataFrame(signals,columns=OLD.columns)
    else:
        raw = OLD.iloc[:0].copy()
        cov = dict(symbol=symbol,n12h=0,observed_weeks=0,ready_mondays=0,signals=0)
    result = enrich(raw,bars,spec)
    result.to_csv(WORK/(symbol+'.csv.gz'),index=False)
    meta = dict(cov, was_top20=known,is_current=spec['is_current'],cohort=spec['cohort'])
    dump(marker, meta)
    return meta


def ledger(all_rows):
    frame = pd.concat([pd.read_csv(WORK/(r['symbol']+'.csv.gz'),float_precision='round_trip')
                       for r in all_rows if r['signals']],ignore_index=True)
    frame = frame.sort_values(['confirmed_bjt','symbol']).reset_index(drop=True)
    assert not frame.duplicated(['symbol','week']).any()
    assert len(frame.query('was_top20'))==len(OLD)
    assert int((frame.was_top20 & frame.status.eq('hit')).sum())==209
    assert ((frame.peak_lower_r>10)==frame.status.eq('hit')).all()
    assert (frame.peak_lower_r<=frame.peak_upper_r).all()
    frame.to_csv(EXP/'signals.csv',index=False)
    hits = frame[frame.status=='hit'].copy()
    names = {'symbol':'币种','confirmed_bjt':'确认时间_北京时间','side':'方向','reference_entry':'触线参考价',
             'confirmation_close':'确认收盘价','reference_stop':'原参考止损','peak_lower_r':'确认后峰值R下界',
             'peak_upper_r':'确认后峰值R上界','confirmation_basis_lower_r':'按确认收盘至止损重算峰值R下界',
             'first_gt10_bjt':'首次超过10R所在12H开盘_北京时间','peak_time_bjt':'峰值所在12H开盘_北京时间',
             'exit_reason':'退出原因','path_end_bar_bjt':'退出K开盘_北京时间','was_top20':'旧Top20',
             'is_current':'截止日在市','pre_current_listing':'旧上市阶段'}
    hits.side = hits.side.map({1:'多',-1:'空'})
    hits.exit_reason=hits.exit_reason.replace({'stop':'固定止损','cloud_flip':'云带翻转','open':'观察中','gap':'数据缺口'})
    hits[list(names)].rename(columns=names).to_csv(EXP/'全市场明确超过10R.csv', index=False,encoding='utf-8-sig')
    pd.DataFrame(all_rows).to_csv(EXP/'signal_coverage.csv',index=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partial',action='store_true')
    args=parser.parse_args()
    specs=pd.read_csv(EXP/'universe.csv',keep_default_na=False).to_dict('records')
    if len({s['symbol'] for s in specs}) != len(specs):
        raise ValueError('Universe contains duplicate base symbols')
    for s in specs:
        if not str(s['list_time']).strip() or pd.isna(s['list_time']):
            s['list_time']=0
    WORK.mkdir(exist_ok=True)
    with ProcessPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(study_one,specs,chunksize=1))
    missing=[s['symbol'] for s, r in zip(specs,results) if r is None]
    print(f'{len(specs)-len(missing)} of {len(specs)} symbols processed',flush=True)
    if args.partial:
        return
    if missing:
        raise RuntimeError(f'Input not reconciled: {missing}')
    ledger(results)


if __name__=='__main__':
    main()
