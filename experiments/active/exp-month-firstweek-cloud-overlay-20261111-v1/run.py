"""One-variable overlay on the frozen Binance first-week VectorBT entries.

All rules decide at the original UTC 12h confirmation close, immediately
before the next open. EMA uses completed 12h close history only. Native cloud
confirmation additionally uses complete past NY Monday high/low and current
12h high/low. No later cloud bar or outcome participates in selection.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(HERE.parent / 'exp-alt-isolated-12h-firstweek-wicks-20261111-v1')]

import strategy_account as account
from strategy_review import OUT, add_rules, photo_coverage
from strategy_signals import CUTOFF, HOUR
from yoyo.monitor.cloud_signals import ema_points
from native_weekly import native_sides

DEST = OUT / 'cloud_overlay_v1'
STEP = 12 * HOUR
SPLIT = int(pd.Timestamp('2025-01-01T00:00Z').timestamp() * 1000)
RULES = ('original', 'warmed', 'ema_direction', 'close_beyond_band', 'native_same_candle')


def bar_features(raw):
    """Aggregate complete UTC H1 OHLC into 12h bars, reset EMA after every gap."""
    hour = pd.DataFrame(raw, columns=['time', 'open', 'high', 'low', 'close'])
    hour['time'] = hour.time.astype('int64')
    bars = hour.groupby(hour.time // STEP, sort=True).agg(
        high=('high', 'max'), low=('low', 'min'), close=('close', 'last'), count=('time', 'size'))
    bars = bars.loc[bars['count'].eq(12)].copy()
    bars['time'] = bars.index.to_numpy() * STEP
    points = pd.DataFrame(ema_points(bars[['time', 'close']].to_dict('records')))
    ready = points.ready.to_numpy()
    fast, slow = points.ema20.to_numpy(), points.ema50.to_numpy()
    sides = native_sides(hour, bars, fast, slow, ready)
    return pd.DataFrame(dict(entry_ms=bars.time.to_numpy() + STEP, ema20=fast, ema50=slow,
                             ready=ready, confirmed_close=bars.close.to_numpy(),
                             native_side=sides)).set_index('entry_ms')


def features(signals, book):
    pieces = []
    for symbol, original in signals.groupby('symbol', sort=True):
        rows = original.join(bar_features(book.raw[symbol]), on='entry_ms', validate='many_to_one')
        if rows.ready.isna().any():
            raise ValueError(f'{symbol}: missing completed 12h confirmation')
        side = np.where(rows.side.eq('long'), 1, -1)
        expected_close = rows.rail * (1 + side * rows.close_to_rail_pct / 100)
        if not np.allclose(rows.confirmed_close, expected_close, rtol=1e-10, atol=0):
            raise ValueError(f'{symbol}: cloud close differs from monthly confirmation close')
        rows['original'] = True
        rows['warmed'] = rows.ready
        rows['ema_direction'] = rows.ready & ((rows.ema20 - rows.ema50) * side > 0)
        rows['close_beyond_band'] = (rows.ready & ((rows.confirmed_close - rows.ema20) * side > 0)
                                     & ((rows.confirmed_close - rows.ema50) * side > 0))
        rows['native_same_candle'] = rows.ready & rows.native_side.eq(side)
        assert not (rows.native_same_candle & ~rows.ema_direction).any()
        pieces.append(rows)
    return pd.concat(pieces).sort_index()


def paired_contrast(closed, rule):
    """Compare to other warmed monthly setups of same symbol/side/year/touch rank.

    Equal weight per pair, without replacement in each stratum, seed fixed.
    Cases lacking a comparable unselected setup remain outside this diagnostic.
    """
    rng = np.random.default_rng(20261111)
    pairs = []
    for _, g in closed[closed.warmed].groupby(['symbol', 'side', 'year', 'rank_12h'], sort=True):
        selected = g.loc[g[rule], 'net_return'].to_numpy()
        other = g.loc[~g[rule], 'net_return'].to_numpy()
        n = min(len(selected), len(other))
        if n:
            pairs.append(np.column_stack((rng.choice(selected, n, replace=False),
                                          rng.choice(other, n, replace=False))))
    if not pairs:
        return dict(matched_pairs=0)
    means = np.concatenate(pairs).mean(axis=0) * 100
    return dict(matched_pairs=sum(len(x) for x in pairs), matched_case_pct=means[0],
                matched_comparison_pct=means[1], matched_gap_pp=means[0]-means[1])


def evaluate(f, book, stop, coverage, rules=RULES):
    ledger = pd.read_csv(OUT / f'fixed_trades_{stop}.csv.gz')
    ledger = ledger.loc[ledger.owner_style].merge(
        f[['signal_row', 'symbol', 'side', 'entry_ms', 'rank_12h', *dict.fromkeys(('warmed', *rules))]],
        on=['signal_row', 'symbol', 'side', 'entry_ms'], validate='one_to_one')
    assert len(ledger) == len(f)
    closed = ledger[ledger.closed_flag].copy()
    closed['year'] = pd.to_datetime(closed.entry_ms, unit='ms', utc=True).dt.year
    ref = pd.read_csv(OUT / f'account_{stop}_metrics.csv').set_index('strategy').loc['owner_style']
    metrics, splits = [], []
    for rule in rules:
        kept = closed[closed[rule]]
        dropped = closed[~closed[rule]]
        if rule == 'original':
            m = ref.to_dict()
        else:
            end = CUTOFF // HOUR * HOUR
            candidates = account._make_candidates(ledger[ledger[rule]], book, end)
            orders, _, m = account._account_one(candidates, rule, stop, book, end)
            account._atomic_csv(pd.DataFrame(orders), DEST / f'positions_{rule}_{stop}.csv.gz')
        m.update(strategy=rule, stop=stop, original_sample_photos=coverage[rule],
                 closed_source=len(kept), source_wins=int(kept.net_return.gt(0).sum()),
                 removed_wins=int(dropped.net_return.gt(0).sum()),
                 removed_losses=int(dropped.net_return.lt(0).sum()),
                 source_mean_net_pct=100 * kept.net_return.mean(),
                 source_mean_gross_pct=100 * kept.gross_return.mean())
        for name, subset in [('through_2024', kept[kept.exit_ms.le(SPLIT) & kept.entry_ms.lt(SPLIT)]),
                             ('from_2025', kept[kept.entry_ms.ge(SPLIT)])]:
            splits.append(dict(stop=stop, strategy=rule, period=name, closed=len(subset),
                               mean_net_pct=100 * subset.net_return.mean(),
                               win_rate_pct=100 * subset.net_return.gt(0).mean()))
        if rule not in ('original', 'warmed'):
            m.update(paired_contrast(closed, rule))
        metrics.append(m)
        print(stop, rule, int(m['candidate_signals']), round(m['final_equity_usdt'], 2), flush=True)
    return metrics, splits


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    signals = add_rules(pd.read_csv(OUT / 'signals.csv.gz')).reset_index(names='signal_row')
    selected = signals.loc[signals.owner_style].copy()
    book = account.QuoteBook(account._load_quote_cache(set(selected.symbol), CUTOFF))
    f = features(selected, book)
    account._atomic_csv(f, DEST / 'causal_features.csv.gz')
    photos = None
    for rule in RULES:
        covered = photo_coverage(f.assign(owner_style=f[rule]))
        matched = covered.set_index(['symbol', 'month', 'side']).owner_style
        if photos is None:
            photos = pd.DataFrame(index=matched.index)
        photos[rule] = matched
    account._atomic_csv(photos.reset_index(), DEST / 'photo_coverage.csv')
    totals = photos.sum().astype(int).to_dict()
    metrics, splits = [], []
    for stop in ('sl5', 'sl3'):
        rows, periods = evaluate(f, book, stop, totals)
        metrics.extend(rows)
        splits.extend(periods)
    account._atomic_csv(pd.DataFrame(metrics), DEST / 'metrics.csv')
    account._atomic_csv(pd.DataFrame(splits), DEST / 'periods.csv')


if __name__ == '__main__':
    main()
