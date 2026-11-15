"""Classify the frozen 111 hourly entries against the first-breach wick definition."""
from pathlib import Path

import pandas as pd

from yoyo.monitor.monday_fib import week_window


def main():
    out = Path(__file__).resolve().parent
    data = out.parent/'exp-cloud-top20-peak10r-20261108-v1/inputs/ETH/bars1h.csv.gz'
    h = pd.read_csv(data, index_col='time', float_precision='round_trip')
    old = pd.read_csv(out.parent/'exp-cloud-eth-hourly-early-execution-20261115-v1/trades.csv')
    rows = []
    for trade in old[old.cohort == 'early'].itertuples():
        window = week_window(trade.key_ms)
        anchor = h.loc[window['start']:window['end']-1]
        assert len(anchor) == 24
        lo, hi = anchor.low.min(), anchor.high.max()
        lower, upper = lo-.618*(hi-lo), hi+.618*(hi-lo)
        t = int(trade.entry_ms)-3_600_000
        bar, previous = h.loc[t], h.loc[window['end']:t-1]
        if trade.side == -1:
            clean = not (previous.high > upper).any()
            wick = bar.high > upper and bar.open <= upper and lower < bar.close < upper
        else:
            clean = not (previous.low < lower).any()
            wick = bar.low < lower and bar.open >= lower and lower < bar.close < upper
        rows.append(dict(week=trade.week, side=trade.side, close_bjt=trade.entry_bjt,
                         untouched_before=clean, wick_on_single_bar=wick, qualifies=clean and wick))
    pd.DataFrame(rows).to_csv(out/'old_111_entries.csv', index=False)


if __name__ == '__main__':
    main()
