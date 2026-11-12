"""Describe what is visible during existing H1 wick trades, without changing their fills.

At entry use EMA20/50 of the latest fully closed UTC 12H with 50 consecutive closes.
At H1/H2 use only that completed hourly close, the original rail and the wick bar's
favorable extreme. At the first +10% touch use its H1 close if the trade is still open.
Original VectorBT exits supply outcomes. Price movement after each reading is gross,
in the original direction, measured from the observed close to the original exit.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(HERE.parent / 'exp-alt-isolated-12h-firstweek-wicks-20261111-v1')]
from census12 import HOUR, read_raw
from scan import bounds
from yoyo.monitor.cloud_signals import ema_points

BOOK = ROOT / 'data/research/alt-isolated-12h-wicks-20261111/expanded12/strategy_v1/confirm_1h'
OUT = BOOK / 'confirmation_replay'
SPLIT = pd.Timestamp('2025-01-01T00:00Z').value // 1_000_000


def cloud_lookup(raw):
    """Completed UTC 12H close only; the existing cloud helper resets on gaps."""
    time = raw[:, 0].astype('int64')
    step = 12 * HOUR
    groups, starts, counts = np.unique(time // step, return_index=True, return_counts=True)
    full = counts == 12
    records = [{'time': int(group * step), 'close': price}
               for group, price in zip(groups[full], raw[starts[full] + 11, 4])]
    return {p['time'] + step: np.sign(p['ema20'] - p['ema50'])
            for p in ema_points(records) if p['ready']}


def describe_symbol(item):
    symbol, signals = item
    raw = read_raw(symbol)
    time = raw[:, 0].astype('int64')
    cloud = cloud_lookup(raw)
    rows = []
    expiry = {m: bounds(m)[2].value // 1_000_000 for m in signals.month.unique()}
    for r in signals.itertuples(index=False):
        start = time.searchsorted(r.entry_ms)
        sign = 1 if r.side == 'long' else -1
        needle = raw[start - 1]
        assert needle[0] == r.touch_ms == r.entry_ms - HOUR
        assert time[start] == r.entry_ms and np.isclose(raw[start, 1], r.entry_price)
        favorable_end = needle[2 if sign == 1 else 3]

        def record(clock, state, when, mark):
            rows.append(dict(trade_id=r.trade_id, symbol=symbol, side=r.side, entry_ms=r.entry_ms,
                             clock=clock, state=state, known_ms=when, mark=mark,
                             exit_ms=r.exit_ms, exit_reason=r.exit_reason,
                             original_net_pct=r.net_return * 100,
                             floating_pct=sign * (mark / r.entry_price - 1) * 100,
                             remaining_gross_pct=sign * (r.exit_price / mark - 1) * 100))

        step = 12 * HOUR
        trend = cloud.get(r.entry_ms // step * step)
        state = 'insufficient' if trend is None else ('aligned' if trend * sign > 0 else 'opposed' if trend * sign < 0 else 'flat')
        record('entry_cloud', state, r.entry_ms, r.entry_price)
        for hour in (1, 2):
            known = r.entry_ms + hour * HOUR
            if r.closed_flag and r.exit_ms <= known:
                continue
            if known >= expiry[r.month] or known > r.last_mark_ms or start + hour > len(raw):
                continue
            bar = raw[start + hour - 1]
            if bar[0] + HOUR != known:
                continue
            price = bar[4]
            if sign * (price - favorable_end) > 0:
                state = 'beyond_needle'
            elif sign * (price - r.rail) > 0:
                state = 'inside'
            else:
                state = 'outside_or_on'
            record(f'hour{hour}', state, known, price)

        if not r.closed_flag:
            continue
        last = time.searchsorted(r.exit_bar_open_ms)
        assert time[last] == r.exit_bar_open_ms
        path = raw[start:last+1]
        extreme = path[:, 2 if sign == 1 else 3].copy()
        if not r.mfe_includes_exit_bar:
            extreme[-1] = path[-1, 1]
        if r.exit_reason.startswith('TAKE_PROFIT'):
            extreme[-1] = r.exit_price
        hits = np.flatnonzero(100 * sign * (extreme / r.entry_price - 1) >= 10)
        assert bool(len(hits)) == (r.mfe_pct >= 10)
        if len(hits):
            bar = path[hits[0]]
            known = int(bar[0]) + HOUR
            if known == r.exit_ms:
                record('first_10pct', 'closed_during_hour', known, np.nan)
            else:
                state = 'closed_at_or_above_10' if 100 * sign * (bar[4] / r.entry_price - 1) >= 10 else 'closed_below_10'
                record('first_10pct', state, known, bar[4])
    return rows


def main():
    opts = dict(float_precision='round_trip')
    signals = pd.read_csv(BOOK / 'signals.csv.gz', **opts)
    trades = pd.read_csv(BOOK / 'fixed_trades_sl5.csv.gz', **opts)
    keys = ['symbol', 'side', 'month', 'entry_ms']
    pool = trades.merge(signals[keys + ['body_inside', 'wick_fraction', 'touch_ms']],
                        on=keys, validate='one_to_one')
    pool = pool[pool.body_inside.eq(1) & pool.wick_fraction.ge(.5)]
    assert len(pool) == 7414 and pool.trade_id.is_unique
    with ThreadPoolExecutor(max_workers=4) as workers:
        readings = pd.DataFrame([row for batch in workers.map(describe_symbol, pool.groupby('symbol')) for row in batch])
    assert not readings.duplicated(['trade_id', 'clock']).any()
    rows = []
    for period, frame in [('all', readings), ('through_2024', readings[readings.exit_ms.le(SPLIT) & readings.entry_ms.lt(SPLIT)]),
                          ('from_2025', readings[readings.entry_ms.ge(SPLIT)])]:
        for side, subset in [('both', frame), *frame.groupby('side')]:
            for (clock, state), g in subset.groupby(['clock', 'state']):
                done = g[g.original_net_pct.notna()]
                rows.append(dict(period=period, side=side, clock=clock, state=state, signals=len(g), settled=len(done),
                                 targets=int(done.exit_reason.str.startswith('TAKE_PROFIT').sum()),
                                 stops=int(done.exit_reason.str.startswith('STOP_LOSS').sum()),
                                 wins=int(done.original_net_pct.gt(0).sum()),
                                 win_pct=done.original_net_pct.gt(0).mean()*100,
                                 net_pct=done.original_net_pct.mean(),
                                 floating_mean_pct=done.floating_pct.mean(),
                                 remaining_gross_mean_pct=done.remaining_gross_pct.mean()
                                    if state != 'closed_during_hour' else np.nan))
    OUT.mkdir(parents=True, exist_ok=True)
    readings.to_csv(OUT / 'readings.csv.gz', index=False)
    pd.DataFrame(rows).to_csv(HERE / 'counts.csv', index=False, float_format='%.6f')


if __name__ == '__main__':
    main()
