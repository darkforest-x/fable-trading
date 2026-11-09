"""Fund native 12H cloud entries on Binance's entire observable 2017 USDT spot cohort."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_session_clock as clock
from yoyo.evaluation import cloud_close_spot_launch_universe as source

EXP = source.EXP.parent / 'exp-cloud-close-launch-spot-wallet-20261109-v1'
START = pd.Timestamp('2017-01-01', tz='UTC').value // 10**6
END = pd.Timestamp('2026-11-01', tz='UTC').value // 10**6
H = wallet.HOUR
FEE = .001
PERIODS = {'all': (None, None), '2017_2019': (None, '2020-01-01T00:00Z'),
           '2020_2024': ('2020-01-01T00:00Z', '2025-01-01T00:00Z'),
           '2025': ('2025-01-01T00:00Z', '2026-01-01T00:00Z'),
           '2026': ('2026-01-01T00:00Z', None)}


@lru_cache(maxsize=None)
def quotes(symbol):
    """Read native H1 trade OHLC only. Dashed spot bases preserve V1.7's exact BTC/ETH stop rules."""
    pair = symbol.replace('-', '')
    path = source.CACHE / 'normalized' / f'{pair}.csv.gz'
    bars = pd.read_csv(path, usecols=['time', 'open', 'high', 'low', 'close', 'trades'],
                       float_precision='round_trip')
    bars = bars.loc[bars.trades.gt(0)]
    return bars.time.to_numpy(), bars[['open', 'high', 'low', 'close']].to_numpy()


@lru_cache(maxsize=None)
def account_marks(symbol):
    times, bars = quotes(symbol)
    if times[-1] == END-H:
        return times, bars
    # Ring-fence a disappeared asset until expiry. No sale proceeds or reusable slot.
    # The marker is never used to generate entries, signals, or executable prices.
    tail = np.unique([times[-1]+H, END-H])
    floor = bars[-1, 3]*1e-12
    return np.r_[times, tail], np.vstack([bars, np.full((len(tail), 4), floor)])


def delays(paths):
    """At the next 12H boundary, use only past survival and 12 completed genuine H1 prices."""
    out = []
    for row in paths.itertuples(index=False):
        at = row.entry_ms + clock.STEP
        if at >= END or row.exit_bar_ms < at or (row.exit_bar_ms == at and row.exit_phase == 'open'):
            continue
        ts, px = quotes(row.symbol)
        i = int(ts.searchsorted(at))
        if i < 12 or i == len(ts) or ts[i] != at or ts[i-12] != at-clock.STEP:
            continue
        if px[i, 0] <= row.stop_price:
            continue
        item = row._asdict()
        item.update(entry_ms=at, entry_price=px[i, 0], event_id=row.event_id+'-wait12h')
        out.append(item)
    return pd.DataFrame(out)


def run_arm(paths, arm):
    # Path geometry comes from native market data; force full write-down for a stranded position.
    paths = paths.copy()
    written = paths.exit_reason.eq('series_tail_close') & paths.exit_bar_ms.lt(END-H)
    for i in paths.index[written]:
        r = paths.loc[i]
        if r.exit_bar_ms != quotes(r.symbol)[0][-1]:
            raise ValueError('Unexpected path tail before source tail')
        paths.loc[i, ['exit_bar_ms', 'exit_phase', 'exit_price', 'exit_reason']] = [
            END-H, 'close', account_marks(r.symbol)[1][-1,3], 'stranded_writeoff_at_cutoff']
    paths['exit_fee_rate'] = FEE
    paths.to_csv(EXP/f'paths_{arm}.csv.gz', index=False)
    data = wallet.prepare(paths, 3, start=START, end=END, quote_loader=account_marks)
    actions = data['actions']
    data['fees'][actions != 0] = FEE
    result = {}
    for slip in (0., .0005):
        inputs = {**data, 'prices': data['prices'].copy()}
        inputs['prices'][actions == 1] *= 1+slip
        inputs['prices'][actions == 2] *= 1-slip
        trades, nav = wallet.account(inputs)
        for dest, field in [('entry_fill', 'entry_price'), ('exit_fill', 'exit_price')]:
            trades[dest] = trades[field] * (1+slip if dest == 'entry_fill' else 1-slip)
        trades['fees_cash'] = trades.quantity * (trades.entry_fill+trades.exit_fill)*FEE
        if not np.allclose(trades.quantity*(trades.exit_fill-trades.entry_fill)-trades.fees_cash,
                           trades.cash_pnl, rtol=1e-9, atol=1e-8):
            raise ValueError('Spot fees/trade do not reconcile with VectorBT cash')
        stranded = trades.loc[trades.exit_reason.eq('stranded_writeoff_at_cutoff')]
        residual = (stranded.quantity*stranded.exit_fill).sum()
        if residual > .001:
            raise ValueError('Terminal write-off credits a measurable cash balance')
        key = arm+('_stress' if slip else '')
        trades.to_csv(EXP/f'funded_{key}.csv.gz', index=False)
        nav.to_csv(EXP/f'hourly_{key}.csv.gz', index_label='hour_end_utc')
        result[key] = dict(metrics={k: wallet.summarize(trades, nav, *v) for k,v in PERIODS.items()},
                           held_unquoted_hours=data['gap_marks'], final_writeoffs=len(stranded),
                           writeoff_residual_usdt=residual, fees_usdt=trades.fees_cash.sum(),
                           admission_skips=data['skips'])
        print(key, result[key]['metrics']['all'], flush=True)
    return result


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed original-year spot account')
    catalog = json.loads((source.EXP/'summary.json').read_text())['symbols']
    keys, parts = [], []
    columns = ['symbol','week','event_id','confirmation_ms','entry_ms','entry_price','entry_order',
               'exit_bar_ms','exit_price','exit_phase','exit_reason','side','stop_price','exit_fee_rate']
    for pair in sorted(catalog):
        symbol = pair[:-4]+'-USDT'
        paths, seen = clock.candidates(symbol, 0, quote_loader=quotes, start=START, end=END)
        if len(paths):
            parts.append(paths[columns])
        keys.extend(seen)
        print(symbol, len(seen), len(paths), flush=True)
    sig = pd.concat(parts, ignore_index=True)
    if not (sig.entry_ms == sig.confirmation_ms).all():
        raise ValueError('Entry does not coincide with completed 12H signal')
    pd.DataFrame(keys, columns=['event_id','symbol','decision_ms','week','side']).to_csv(
        EXP/'weekly_keys.csv.gz',index=False)
    result = dict(symbols=sorted(catalog), weekly_keys=len(keys), long_paths=len(sig))
    result.update(run_arm(sig, 'confirmed'))
    result.update(run_arm(delays(sig), 'delay12h'))
    clock.branch._write_json(EXP/'summary.json', result)


if __name__ == '__main__':
    main()
