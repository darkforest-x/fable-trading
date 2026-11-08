"""Tighten five historical hourly NAV envelopes on frozen fills with synchronized native 5m quotes."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet

EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-drawdown-bounds-20261109-v1'
INPUTS = wallet.ROOT / 'data/research/cloud_close_drawdown_bounds_20261109'
PARENT = repair.EXP / 'thirds'
PEAK = pd.Timestamp('2025-11-04T21:00Z')
TROUGHS = pd.date_range('2025-11-12T12:00Z', periods=4, freq='h')
QUOTES = ['open', 'high', 'low', 'close']
FIVE_MIN = 300_000


def funded_legs():
    positions = pd.read_csv(PARENT/'funded_OKX.csv.gz', usecols=['event_id','notional'])
    native = wallet.read_paths()
    legs = native[native.tp_r.isin([0,2])].merge(positions,on='event_id',validate='many_to_one')
    if len(legs) != 2 * len(positions) or legs.duplicated(['event_id','tp_r']).any():
        raise ValueError('Funded ledger and frozen exit legs disagree')
    legs['signed_size'] = legs.notional / legs.entry_price * np.where(legs.tp_r.eq(2),2/3,1/3) * legs.side
    return legs


def hour_envelope(stamp, old, legs, subbars, step_ms=FIVE_MIN):
    hour = stamp.value // 1_000_000
    active = legs[(legs.entry_ms <= hour) & (legs.exit_bar_ms >= hour)]
    after_open = active[~(active.exit_bar_ms.eq(hour) & active.exit_phase.eq('open'))]
    slices = wallet.HOUR // step_ms
    low = np.full(slices, old.open_exit_nav)
    high = low.copy()
    exact_closes = low.copy()
    known_closes = True
    end_nav = old.open_exit_nav
    observed = set()
    for row in after_open.itertuples():
        times, ohlc = wallet.quotes(row.symbol)
        idx = times.searchsorted(hour)
        if idx == len(times) or times[idx] != hour:
            raise ValueError(f'Unobserved H1 for {row.symbol} at {stamp}')
        hourly = ohlc[idx]
        group = subbars[(subbars.symbol == row.symbol) & subbars.time.between(hour,hour+wallet.HOUR-1)]
        if len(group):
            five = group.sort_values('time')
            if not np.array_equal(five.time, hour+np.arange(slices)*step_ms):
                raise ValueError(f'Incomplete finer bars in hour for {row.symbol}')
            aggregate = [five.open.iloc[0],five.high.max(),five.low.min(),five.close.iloc[-1]]
            if not np.allclose(aggregate,hourly,rtol=1e-10,atol=1e-13):
                raise ValueError(f'5m aggregate does not match native hourly source for {row.symbol} at {stamp}')
            bounds = five[['low','high']].to_numpy()
            observed.add(row.symbol)
            exact_closes += (five.close.to_numpy()-hourly[0])*row.signed_size
        else:
            # Keep the entire native H1 envelope for every slice when the finer bars are absent.
            bounds = np.tile(hourly[[2,1]],(slices,1))
            known_closes = False
        variations = (bounds-hourly[0]) * row.signed_size
        low_move, high_move = variations.min(axis=1), variations.max(axis=1)
        if row.exit_bar_ms == hour:
            known_closes = False
            settled = (row.exit_price-hourly[0])*row.signed_size - row.exit_price*abs(row.signed_size)*row.exit_fee_rate
            if len(group) and row.exit_phase == 'intrabar' and row.exit_reason == 'stop':
                touched = five.low.le(row.stop_price) if row.side == 1 else five.high.ge(row.stop_price)
                hits = np.flatnonzero(touched)
                if not len(hits):
                    raise ValueError(f'Native stop was never reached in the observed subbars: {row.symbol}')
                # Keep both mark and settlement in the crossing bar. After that first
                # crossing the native stop has fully exited; subsequent prices carry no exposure.
                first_hit = hits[0]
                low_move[first_hit] = min(low_move[first_hit],settled)
                high_move[first_hit] = max(high_move[first_hit],settled)
                low_move[first_hit+1:] = settled
                high_move[first_hit+1:] = settled
            else:
                # Unknown within-hour ordering: allow both a still-open and already-closed position.
                low_move = np.minimum(low_move,settled)
                high_move = np.maximum(high_move,settled)
            end_nav += settled
        else:
            end_nav += (hourly[3]-hourly[0])*row.signed_size
        low += low_move
        high += high_move
    if not np.isclose(end_nav,old.nav,atol=1e-4,rtol=1e-9):
        raise ValueError(f'Exact NAV self-check failed at {stamp}: {end_nav} versus {old.nav}')
    stages = [old.open_nav,old.open_exit_nav,old.nav]
    return dict(hour_end=stamp+pd.Timedelta(hours=1), refined_lower=min(*stages,low.min()),
                refined_upper=max(*stages,high.max()), previous_lower=old.lower_nav, previous_upper=old.upper_nav,
                min_observed_close=float(exact_closes.min()) if known_closes else None,
                max_observed_close=float(exact_closes.max()) if known_closes else None,
                observed_symbols='|'.join(sorted(observed)), active_symbols='|'.join(sorted(after_open.symbol.unique())))


def max_drawdown(equity):
    peaks = np.maximum.accumulate(np.r_[1000.,equity.upper_nav])
    return float(100*(1 - (np.r_[1000.,equity.lower_nav]/peaks).min()))


def main():
    equity = pd.read_csv(PARENT/'hourly_OKX.csv.gz', index_col='hour_end_utc', parse_dates=True)
    legs = funded_legs()
    quotes = pd.read_csv(INPUTS/'quotes.csv', dtype={'symbol':str}, keep_default_na=False)
    if quotes.duplicated(['symbol','time']).any() or (quotes[QUOTES]<=0).any().any():
        raise ValueError('Invalid subhour quotes')
    original_bound = max_drawdown(equity)
    records = []
    for stamp in [PEAK,*TROUGHS]:
        end = stamp+pd.Timedelta(hours=1)
        rec = hour_envelope(stamp,equity.loc[end],legs,quotes)
        equity.loc[end,['lower_nav','upper_nav']] = [rec['refined_lower'],rec['refined_upper']]
        records.append(rec)
    table = pd.DataFrame(records)
    table.to_csv(EXP/'hourly_refinement.csv',index=False)
    result = dict(venue='OKX', original_drawdown_upper_pct=original_bound,
                  refined_drawdown_upper_pct=max_drawdown(equity), terminal_usdt=float(equity.nav.iloc[-1]),
                  marked_hourly_nav_unchanged=True, tightened_hours=len(records),
                  passes_35pct_drawdown_upper=max_drawdown(equity)<=35)
    serialized = json.dumps(result,indent=2,allow_nan=False)+'\n'
    (EXP/'five_minute_summary.json').write_text(serialized)
    if not (EXP/'minute_summary.json').exists():
        (EXP/'summary.json').write_text(serialized)
    print(result)


if __name__ == '__main__':
    main()
