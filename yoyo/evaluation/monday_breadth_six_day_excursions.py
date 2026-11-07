"""Six-day long outcomes after the fixed breadth candle and its 30m observation.

Weekly lower-first-touch sets and decision times come from the fixed-timeframe study.
Forward high labels use 1728 consecutive 5m highs starting at entry and the exact
exit open. No data from the 5m bar starting at the exit, except its open, is used.
Realized returns sell at the fixed exit, with the same two taker fees as the baseline.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_fixed_timeframes as fixed
from yoyo.evaluation import monday_breadth_excursion_prices as market


previous = fixed.previous
base = previous.base
OUT = previous.census.ACTIVE/'exp-monday-breadth-six-day-excursions-20261108-v1'
HOLD_BARS = 6*24*12
HOLD = HOLD_BARS*fixed.STEP


def cached_six_day_returns():
    stored = pd.Series(dtype=float)

    def for_asset(asset, requested, frame):
        nonlocal stored
        positions = np.unique(requested)
        missing = np.setdiff1d(positions,stored.index)
        opens = frame.open.to_numpy()
        batches = [base.trade_returns(opens,missing[i:i+16000],hold_bars=HOLD_BARS)
                   for i in range(0,len(missing),16000)]
        if batches:
            stored = pd.concat([stored,*batches],verify_integrity=True)
        return stored.reindex(positions)
    return for_asset


def six_day_peaks(frame):
    inside = frame.high.iloc[::-1].rolling(HOLD_BARS).max().iloc[::-1]
    peak = np.maximum(inside,frame.open.shift(-HOLD_BARS))
    return 100*(peak/frame.open-1)


def add_prices_and_times(events, frame):
    entry_times = pd.DatetimeIndex(events.loc[events.net_pct.notna(),'decision'].unique())
    positions = frame.index.get_indexer(entry_times)
    highs = frame.high.to_numpy()
    opens = frame.open.to_numpy()
    peaks = []
    for pos in positions:
        exit_pos = pos+HOLD_BARS
        peak_pos = pos+np.argmax(highs[pos:exit_pos])
        if opens[exit_pos]>highs[peak_pos]:
            peaks.append((opens[exit_pos],exit_pos))
        else:
            peaks.append((highs[peak_pos],peak_pos))
    quotes = pd.DataFrame(peaks,columns=['peak_price','peak_pos'],index=entry_times)
    quotes['peak_bar_open'] = frame.index[quotes.pop('peak_pos').to_numpy()]
    quotes['entry_open'] = opens[positions]
    quotes['exit_open'] = opens[positions+HOLD_BARS]
    rows = events.join(quotes,on='decision',validate='many_to_one')
    rows['exit_time'] = rows.decision+HOLD
    assert np.allclose(rows.peak_pct,(rows.peak_price/rows.entry_open-1)*100,equal_nan=True)
    return rows


def peak_summaries(rows):
    """One maximum per case. Count thresholds only among cases with a complete path."""
    result = []
    valid = rows.dropna(subset=['peak_pct']).copy()
    valid['peak_ge5'] = 100*valid.peak_pct.ge(5)
    valid['peak_ge10'] = 100*valid.peak_pct.ge(10)
    valid['paired_peak_diff'] = valid.peak_pct-valid.control_peak_pct
    for period in ('all','early','late'):
        part = valid if period=='all' else valid[valid.period.eq(period)]
        scopes = pd.concat([part,part[part.initial.ge(25)].assign(band='25+')])
        groups = scopes.groupby(['minutes','asset','band','continued'],observed=True)
        summary = groups.agg(mean_peak_pct=('peak_pct','mean'),median_peak_pct=('peak_pct','median'),
                             max_peak_pct=('peak_pct','max'),mean_control_peak_pct=('control_peak_pct','mean'),
                             paired_peak_excess_pp=('paired_peak_diff','mean'),peak_ge5_pct=('peak_ge5','mean'),
                             peak_ge10_pct=('peak_ge10','mean')).reset_index()
        result.append(summary.assign(period=period))
    return pd.concat(result,ignore_index=True)


def main():
    inputs = pd.read_csv(fixed.OUT/'events.csv.gz',usecols=['asset','minutes','known','initial','new_coins',
                         'band','continued','monitor_bars','last_touch_age_min'],parse_dates=['known'])
    candles = inputs[inputs.asset.eq('BTC')].drop(columns='asset')
    _, ledger = previous.census.ledger_rows()
    saved = []
    for asset in previous.ASSETS:
        frame = market.prices(asset)
        peak_pct = six_day_peaks(frame).to_numpy()
        cached = cached_six_day_returns()
        asset_rows = []
        for minutes,bars in candles.groupby('minutes'):
            span = pd.Timedelta(minutes=minutes)
            counts = ledger.groupby([ledger.touch.dt.floor(span)+span,'side']).symbol.nunique()
            excluded = counts[counts.ge(25)].index.get_level_values(0).unique()
            mask,phases = fixed.control_clock(frame.index,minutes)
            trial = previous.evaluate(asset,bars,excluded,frame=frame,control_mask=mask,monitor_bars=phases,
                                      returns_for=cached,hold_bars=HOLD_BARS,peak_pct=peak_pct)
            asset_rows.append(trial)
        saved.append(add_prices_and_times(pd.concat(asset_rows,ignore_index=True),frame))
        print(f'Completed {asset}',flush=True)
    events = pd.concat(saved,ignore_index=True)
    keys = ['minutes','asset','band','continued','period']
    account = [previous.summaries(part).assign(minutes=minutes) for minutes,part in events.groupby('minutes')]
    summary = pd.concat(account,ignore_index=True).merge(peak_summaries(events),on=keys,how='left',validate='one_to_one')
    OUT.mkdir(exist_ok=True)
    events.to_csv(OUT/'events.csv.gz',index=False)
    summary.to_csv(OUT/'summary.csv',index=False)
    focused = events[events.minutes.eq(5)&events.band.eq('50-99')&~events.continued].copy()
    focused['signal_bjt'] = focused.decision.dt.tz_convert('Asia/Shanghai')
    wide = focused.pivot(index=['signal_bjt','initial'],columns='asset',values=['peak_pct','net_pct'])
    wide.columns = [f'{asset}_{metric}' for metric,asset in wide.columns]
    wide.to_csv(OUT/'5m_13_cases.csv')
    market.save_receipts(OUT)


if __name__ == '__main__':
    main()
