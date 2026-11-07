"""Compare rolling 5m and 10m lower breadth alarms, using independent VectorBT long trades.

Prices are Binance USD-M. Trigger inputs use only first-touch keys, side, symbol and bar
open from the frozen scored universe. Decisions count subsequent distinct names up to
the observation close. Matching uses only the 12 trailing closed 5m log returns and
the direction of the target's pre-alarm 1h return. Outcomes use the next bar open at
the observation boundary and the scheduled open 24 hours after the alarm.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation.monday_limit_alarm_entry import alarms
from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation.spike_alarm_major_price_response import MARKET, SOURCE

ARCHIVE = ROOT / 'experiments/active'
OUT = ARCHIVE / 'exp-monday-breadth-reversal-window-20261107-v1'
BAR_MS = 300_000
THRESHOLD = 25
WINDOWS = (5, 10)
OBS_BARS = 6
EXIT_BARS = 288
HOLD_BARS = EXIT_BARS - OBS_BARS
TAKER = .0005
SPLIT = pd.Timestamp('2025-01-01', tz='UTC')
END = pd.Timestamp('2026-09-22 13:55', tz='UTC')
SEED = 20261107


def rebuild():
    data = pd.read_csv(ARCHIVE / 'exp-monday-limit-score-features-v2-20261025-v1/features_v2.csv.gz',
                       usecols=['event', 'symbol', 'side', 'touch'])
    keys = pd.read_csv(ARCHIVE / 'exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz',
                       usecols=['event'])
    touches = keys.merge(data, how='left', validate='one_to_one')
    assert len(touches) == 58740 and touches.touch.notna().all()
    touches['ms'] = pd.to_datetime(touches.touch, utc=True).dt.as_unit('ms').astype('int64')
    frozen = pd.read_csv(SOURCE / 'alarms.csv.gz')
    links = pd.read_csv(SOURCE / 'alarm_windows.csv.gz').query('threshold == 25 and window == 30')
    frozen_links = links.groupby(['side', 'alarm_ms']).event.agg(set).to_dict()
    out = []
    for window in WINDOWS:
        log = alarms(touches, THRESHOLD, window_ms=window * 60_000)
        if window == 5:
            assert set(zip(log.side, log.ms)) == set(zip(frozen.side, frozen.ms))
        for side, alarms_for_side in log.groupby('side'):
            stream = touches[touches.side == side].sort_values('ms')
            times = stream.ms.to_numpy()
            for row in alarms_for_side.itertuples():
                left, alarm_end, observation_end = np.searchsorted(
                    times, [row.ms - window * 60_000, row.ms, row.ms + BAR_MS * OBS_BARS], side='right')
                trigger_symbols = set(stream.symbol.iloc[left:alarm_end])
                later = stream.iloc[alarm_end:observation_end]
                new = later[~later.symbol.isin(trigger_symbols)]
                if window == 5:
                    assert set(new.event) == frozen_links.get((side, row.ms), set())
                out.append(dict(window=window, side=side, known=pd.to_datetime(row.ms + BAR_MS, unit='ms', utc=True),
                                trigger_n=len(trigger_symbols), further_n=new.symbol.nunique()))
    return pd.DataFrame(out).sort_values(['window', 'known', 'side']).reset_index(drop=True)


def market_prices(asset):
    paths = list((MARKET/'kline_preholdout_binance_um5m/series').glob(f'binance_um_{asset}USDT_5m_*.csv'))
    assert len(paths) == 1
    def prices(path):
        d = pd.read_csv(path, usecols=['ts', 'open', 'close'])
        return d.set_index(pd.to_datetime(d.ts, unit='ms', utc=True))[['open','close']]
    history = prices(paths[0])
    seam = history.index[-1] + pd.Timedelta(minutes=5)
    if asset == 'SOL':
        minute = prices(ROOT/'data/research/ma6_knot_1m_20261011/series/binance_um_SOLUSDT_1m_2059200.csv')
        part = minute.loc[seam:END + pd.Timedelta(minutes=4)].asfreq('1min')
        groups = part.resample('5min')
        tail = groups.agg({'open':'first','close':'last'}).where(groups['close'].count().eq(5), axis=0)
    else:
        tail = prices(MARKET/f'kline_binance_um5m_forward_20260922/series/{asset}USDT.csv')
    assert tail.index[0] == seam and tail.index[-1] == END
    frame = pd.concat([history,tail]).asfreq('5min')
    assert ((frame > 0) | frame.isna()).all().all()
    return frame


def trade_returns(opens, starts):
    """One unlevered long per distinct entry time, held until its fixed market exit."""
    starts = np.unique(starts)
    prices = np.stack([opens[starts], opens[starts + HOLD_BARS]])
    portfolio = vbt.Portfolio.from_orders(prices, size=np.array([[1.],[-1.]]), fees=TAKER,
                                           init_cash=float(np.nanmax(prices)*2), cash_sharing=False)
    trades = portfolio.trades.records
    assert len(trades) == len(starts) and np.array_equal(trades.col.to_numpy(), np.arange(len(starts)))
    assert trades.status.eq(1).all() and trades.entry_idx.eq(0).all() and trades.exit_idx.eq(1).all()
    assert np.allclose(trades.entry_fees, prices[0]*TAKER) and np.allclose(trades.exit_fees, prices[1]*TAKER)
    return pd.Series(trades['return'].to_numpy()*100, index=starts)


def evaluate(asset, log):
    frame = market_prices(asset)
    times = frame.index
    anchor_prices = frame.close.shift(1)
    vol = np.log(anchor_prices).diff().pow(2).rolling(12).sum().pow(.5)*100
    pre = (anchor_prices.shift(OBS_BARS)/anchor_prices.shift(OBS_BARS+12)-1)*100
    volbin = np.digitize(vol, [.25,.5,1,2,4])
    calendar = (times.year*12 + times.month)*4 + times.hour//6
    ahead = frame.open.notna().iloc[::-1].rolling(HOLD_BARS+1).sum().iloc[::-1].eq(HOLD_BARS+1)
    valid = (ahead & vol.notna() & pre.notna()).to_numpy()
    split_at = times.searchsorted(SPLIT)
    eligible = valid.copy()
    eligible[max(0, split_at-HOLD_BARS):split_at] = False
    # Remove random observation/holding windows that overlap any study alarm's observation/return window.
    for when in log.known.unique():
        decision = times.searchsorted(when + pd.Timedelta(minutes=30))
        eligible[max(0, decision-EXIT_BARS):decision+EXIT_BARS+1] = False
    rows, matches = [], []
    for r in log[log.side == 1].itertuples():
        decision = r.known + pd.Timedelta(minutes=30)
        loc = times.get_indexer([decision])[0]
        if loc < 0 or not valid[loc]:
            raise ValueError(f'Missing required path for {asset} at {decision}')
        period = 'early' if loc + HOLD_BARS < split_at else ('late' if loc >= split_at else 'crossing')
        row = dict(asset=asset, window=r.window, known=r.known, decision=decision, period=period,
                   trigger_n=r.trigger_n, further_n=r.further_n, pre_alarm_1h_pct=pre.iloc[loc], loc=loc)
        regime = (np.sign(pre.to_numpy()) == np.sign(pre.iloc[loc]))
        pool = np.flatnonzero(eligible & (calendar == calendar[loc]) & (volbin == volbin[loc]) & regime)
        # Equal-time decisions in both definitions get exactly the same random controls.
        rng = np.random.default_rng([SEED, int(decision.value//10**9), ('BTC','ETH','SOL').index(asset)])
        picks = rng.choice(pool, size=min(24,len(pool)), replace=False)
        matches.append(picks)
        rows.append(row)
    res = pd.DataFrame(rows)
    starts = np.concatenate([res['loc'].to_numpy(), *matches])
    ret = trade_returns(frame.open.to_numpy(), starts)
    res['net_pct'] = res['loc'].map(ret)
    ledger = []
    for row,picks in zip(rows,matches):
        ledger.extend(dict(asset=asset, window=row['window'], known=row['known'], control_decision=times[k],
                           net_pct=ret[k]) for k in picks)
    ctrl = pd.DataFrame(ledger)
    res['control_n'] = [len(x) for x in matches]
    res['control_pct'] = [ret[x].mean() if len(x) else np.nan for x in matches]
    return res.drop(columns='loc'), ctrl


def summarize(events):
    rows = []
    for period in ('all','early','late'):
        frame = events if period == 'all' else events[events.period.eq(period)]
        for scope,g in [('all_targets',frame),('target_fell_before_alarm',frame[frame.pre_alarm_1h_pct.lt(0)])]:
            for (asset,window,more),part in g.groupby(['asset','window',g.further_n.gt(0).rename('more')]):
                delta = (part.net_pct - part.control_pct).dropna()
                rows.append(dict(period=period, scope=scope, asset=asset, window=window, continued=more,
                                 n=len(part), win_pct=100*part.net_pct.gt(0).mean(), net_pct=part.net_pct.mean(),
                                 median_net_pct=part.net_pct.median(), worst_net_pct=part.net_pct.min(),
                                 matched_n=len(delta), excess_pp=delta.mean()))
    return pd.DataFrame(rows)


def main():
    log = rebuild()
    result, controls = [], []
    for asset in ('BTC','ETH','SOL'):
        trades, matched = evaluate(asset,log)
        result.append(trades)
        controls.append(matched)
    sample = pd.concat(result, ignore_index=True)
    OUT.mkdir(exist_ok=True)
    log.to_csv(OUT/'alarms.csv',index=False)
    sample.to_csv(OUT/'events.csv',index=False)
    pd.concat(controls).to_csv(OUT/'controls.csv.gz',index=False)
    summarize(sample).to_csv(OUT/'summary.csv',index=False)
    print(log.groupby(['window','side']).size(), flush=True)


if __name__ == '__main__':
    main()
