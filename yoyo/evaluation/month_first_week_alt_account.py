"""Fund frozen monthly upper-extension trades on one shared VectorBT balance.

Only opening marks, previously opened positions, and deterministic symbol order
determine the size or admission of a new trade. Close and high/low marks are
outcome data; the sizing selection uses exclusively the pre-2025 path.
"""
from functools import lru_cache
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation.month_first_week_alt_entries import ROOT, prices, universe

EXPERIMENTS = ROOT / 'experiments/active'
EXP = EXPERIMENTS / 'exp-alt-month-first-week-account-20261110-v1'
FIXED = EXPERIMENTS / 'exp-alt-month-first-week-be2-20261110-v1'
PARENT = EXPERIMENTS / 'exp-alt-month-first-week-near-target-20261110-v1'
CONFIG = json.loads((EXP / 'config.json').read_text())
TAKER, MAKER = 0.0005, 0.0002


def millis(value):
    return pd.Timestamp(value).value // 1_000_000


def market_loader():
    manifests = universe().set_index('symbol', drop=False)

    @lru_cache(maxsize=None)
    def load(symbol):
        bars = prices(manifests.loc[symbol])
        return bars.index.as_unit('ms').asi8, bars[['open', 'high', 'low', 'close']].to_numpy()
    return load


def load_events(quotes):
    fixed = pd.read_csv(FIXED / 'positions.csv.gz')
    old = pd.read_csv(PARENT / 'trades.csv.gz', usecols=['trade_id', 'exit_time'])
    clocks = fixed.merge(old, on='trade_id', suffixes=('_be', ''), validate='1:1')
    assert len(clocks) == 4846
    events = clocks.rename(columns={'entry_fill': 'entry_price', 'old_price': 'fixed_exit',
                                    'old_reason': 'fixed_reason', 'trade_id': 'event_id'})
    events['exit_price'] = events.fixed_exit
    events['side'] = -1
    events['entry_ms'] = events.entry_time.map(millis)
    events['exit_bar_ms'] = events.exit_time.map(millis)
    events['exit_fee_rate'] = np.where(events.fixed_reason.eq('target'), MAKER, TAKER)
    events['exit_phase'] = 'intrabar'
    for symbol, group in events.groupby('symbol'):
        clock, ohlc = quotes(symbol)
        loc = np.searchsorted(clock, group.exit_bar_ms)
        assert np.array_equal(clock[loc], group.exit_bar_ms)
        opening = ohlc[loc, 0]
        at_open = ((group.fixed_reason.eq('target') & (opening <= group.target_price)) |
                   (group.fixed_reason.eq('stop') & (opening >= group.stop_price)) |
                   group.fixed_reason.eq('timeout'))
        events.loc[group.index[at_open], 'exit_phase'] = 'open'
    return events[['event_id','symbol','is_control','entry_ms','exit_bar_ms','side','entry_price',
                   'exit_price','exit_fee_rate','exit_phase','fixed_reason','old_net_pct']]


def run_account(data, size, slip):
    funded, equity = wallet.account(data, exposure=size * CONFIG['slots'], slip=slip)
    if not slip and not np.allclose(100 * funded.net_return, funded.old_net_pct, atol=1e-8, rtol=0):
        raise AssertionError('Funded gross prices or fee classes differ from the fixed baseline')
    # When several positions exit within the hour, allow their exit costs to
    # coexist with the adverse wicks in the other contracts.
    intrabar = funded[funded.exit_phase.eq('intrabar')]
    exit_slip = np.where(intrabar.exit_fee_rate.eq(TAKER), slip, 0.)
    close_cost = intrabar.quantity * intrabar.exit_price * (
        exit_slip + intrabar.exit_fee_rate * (1. + exit_slip))
    hour_ends = pd.to_datetime(intrabar.exit_bar_ms + wallet.HOUR, unit='ms', utc=True)
    reserve = close_cost.groupby(hour_ends).sum().reindex(equity.index, fill_value=0.)
    equity['lower_nav'] -= reserve
    assert (equity.lower_nav <= equity.nav + 1e-8).all()
    return funded, equity


def main():
    assert CONFIG['initial_usdt'] == 1000
    quotes = market_loader()
    events = load_events(quotes)
    metrics, summaries = [], {}
    chosen = None
    for is_control, name in [(False, 'signal'), (True, 'random')]:
        sample = events[events.is_control.eq(is_control)]
        data = wallet.prepare(sample, CONFIG['slots'], start=millis(CONFIG['start']),
                              end=millis(CONFIG['end']), quote_loader=quotes)
        if data['gap_marks']:
            raise ValueError('Active position lacks a native hourly mark')
        sizes = CONFIG['per_trade_fraction'] if not is_control else ([chosen] if chosen else [])
        eligible = []
        for size in sizes:
            conservative_early = []
            for slip in (0., .0005):
                funded, equity = run_account(data, size, slip)
                label = f"{name}_{round(100*size):02}_{round(10000*slip)}bp"
                equity.to_csv(EXP / f'{label}_equity.csv.gz', index_label='hour_end_utc')
                funded.to_csv(EXP / f'{label}_fills.csv.gz', index=False)
                for period, lo, hi in [('all',None,None),('early',None,CONFIG['split']),
                                       ('later',CONFIG['split'],None)]:
                    row = wallet.summarize(funded, equity, lo, hi)
                    row['initial_usdt'] = row['final_usdt'] / row['multiple']
                    row['return_pct'] = 100 * (row['multiple'] - 1)
                    row['rebased_from_1000'] = 1000 * row['multiple']
                    metrics.append(dict(pool=name, per_trade=size, slip_bp=round(10000*slip),
                                        period=period, **row))
                    if period == 'early':
                        conservative_early.append(row['dd_bound_pct'])
            if max(conservative_early) <= 25.:
                eligible.append(size)
        if not is_control:
            chosen = max(eligible, default=None)
        summaries[name] = dict(signals=len(sample), funded=len(data['selected']),
                               skipped=data['skips'], missing_hours=int(data['gap_marks']))
    pd.DataFrame(metrics).to_csv(EXP / 'metrics.csv', index=False)
    result = dict(selected=chosen, samples=summaries, risk_limit_pct=30, selection_limit_pct=25,
                  metrics=metrics)
    (EXP / 'summary.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print('selected',chosen,'sample',summaries,flush=True)


if __name__ == '__main__':
    main()
