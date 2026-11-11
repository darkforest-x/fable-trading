"""Exploratory complement after seeing original photo retention.

Run after run.py. Use only the recorded same-candle EMA20/50, with the
opposite strict ordering from the trend-aligned rule. All fills are frozen.
"""
import numpy as np
import pandas as pd

from run import account, CUTOFF, DEST, evaluate, photo_coverage


def main():
    f = pd.read_csv(DEST / 'causal_features.csv.gz', float_precision='round_trip')
    against = 'opposite_ema'
    direction = np.where(f.side.eq('long'), 1, -1)
    f[against] = f.warmed & ((f.ema20 - f.ema50) * direction < 0)
    assert not (f[against] & f.ema_direction).any()
    photos = photo_coverage(f.assign(owner_style=f[against]))
    photos = photos[['symbol', 'month', 'side', 'owner_style']].rename(columns={'owner_style': against})
    account._atomic_csv(photos, DEST / 'countertrend_photos.csv')
    book = account.QuoteBook(account._load_quote_cache(set(f.loc[f[against], 'symbol']), CUTOFF))
    metrics, splits = [], []
    for stop in ('sl5', 'sl3'):
        rows, periods = evaluate(f, book, stop, {against: int(photos[against].sum())}, (against,))
        metrics.extend(rows)
        splits.extend(periods)
    account._atomic_csv(pd.DataFrame(metrics), DEST / 'countertrend_metrics.csv')
    account._atomic_csv(pd.DataFrame(splits), DEST / 'countertrend_periods.csv')


if __name__ == '__main__':
    main()
