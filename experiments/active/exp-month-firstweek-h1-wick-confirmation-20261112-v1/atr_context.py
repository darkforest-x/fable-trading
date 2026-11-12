"""One entry-time observable: wick length vs the mean true range of the prior 14 closed H1s.

The signal candle supplies its wick only. The preceding 14 true ranges use H/L and
the previous hourly close; all 15 historical hours must be contiguous. Compare a
wick of at least one prior ATR with a shorter wick on the same frozen trade ledger.
"""
import numpy as np
import pandas as pd

from replay import BOOK, HERE, HOUR, OUT, SPLIT, ThreadPoolExecutor, read_raw


def wick_to_atr(item):
    symbol, g = item
    raw = read_raw(symbol)
    t = raw[:, 0].astype('int64')
    i = t.searchsorted(g.touch_ms.to_numpy())
    assert np.array_equal(t[i], g.touch_ms)
    valid = (i >= 15) & (t[i] - t[np.maximum(i-15, 0)] == 15*HOUR)
    g = g.loc[valid].copy()
    i = i[valid]
    previous = i[:, None] + np.arange(-14, 0)
    hi = raw[previous, 2]
    lo = raw[previous, 3]
    prev_close = raw[previous - 1, 4]
    tr = np.maximum(hi, prev_close) - np.minimum(lo, prev_close)
    o, h, l, c = raw[i, 1:5].T
    wick = np.where(g.side.eq('long'), np.minimum(o,c)-l, h-np.maximum(o,c))
    atr = tr.mean(axis=1)
    g['wick_in_atr'] = np.divide(wick, atr, out=np.full(len(atr), np.nan), where=atr>0)
    return g


def main():
    s = pd.read_csv(BOOK/'signals.csv.gz', float_precision='round_trip')
    s = s[s.body_inside.eq(1) & s.wick_fraction.ge(.5)]
    book = pd.read_csv(OUT/'readings.csv.gz')
    entries = book.loc[book.clock.eq('entry_cloud'), ['trade_id','symbol','side','entry_ms','exit_ms','original_net_pct','exit_reason']]
    s = entries.merge(s[['symbol','entry_ms','side','touch_ms']], validate='one_to_one')
    with ThreadPoolExecutor(max_workers=4) as pool:
        a = pd.concat(pool.map(wick_to_atr, s.groupby('symbol'))).dropna(subset=['wick_in_atr'])
    a.to_csv(OUT/'prior_atr.csv.gz', index=False)
    a['at_least_one_atr'] = a.wick_in_atr >= 1
    a = a[a.original_net_pct.notna()].copy()
    rows = []
    for period, frame in [('all', a), ('through_2024', a[a.exit_ms.le(SPLIT) & a.entry_ms.lt(SPLIT)]),
                          ('from_2025', a[a.entry_ms.ge(SPLIT)])]:
        for side, subset in [('both', frame), *frame.groupby('side')]:
            for flag,g in subset.groupby('at_least_one_atr'):
                rows.append(dict(period=period, side=side, at_least_one_atr=flag, settled=len(g),
                                 targets=int(g.exit_reason.str.startswith('TAKE_PROFIT').sum()),
                                 win_pct=100*g.original_net_pct.gt(0).mean(), net_pct=g.original_net_pct.mean()))
    pd.DataFrame(rows).to_csv(HERE/'atr_counts.csv', index=False, float_format='%.6f')


if __name__ == '__main__':
    main()
