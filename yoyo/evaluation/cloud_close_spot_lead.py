"""Compare spot and perpetual price changes from their own completed NY Monday midpoints."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_cap_filter as gap
from yoyo.evaluation import cloud_close_november_paths as prices
from yoyo.evaluation import cloud_close_spot_reclaim as wallet

EXP = wallet.EXP.parent / 'exp-cloud-close-spot-lead-20261109-v1'


def relative_move(spot, future, side):
    """Use Monday H1 high/low and the last H1 close of the confirmed key for each venue."""
    spot_high, spot_low, spot_close = spot
    high, low, close = future
    return side * np.log((spot_close / ((spot_high + spot_low) / 2)) /
                         (close / ((high + low) / 2)))


def observations(panel):
    eligible = panel.covered & panel.spot_monday_zero_trade_hours.eq(0) & panel.spot_key_zero_trade_hours.eq(0)
    spread = pd.Series(np.nan, index=panel.index, dtype=float)
    for symbol, group in panel[eligible].groupby('spot_symbol'):
        with np.load(prices.history_slot(symbol) / 'quotes.npz') as source:
            history = pd.DataFrame(source['prices'], columns=prices.OHLC, index=source['clock'])
        for row in group.itertuples():
            start = pd.Timestamp(row.week_start_utc).value // 1_000_000
            end = pd.Timestamp(row.monday_end_utc).value // 1_000_000
            key_end = row.entry_ms - prices.HOUR
            monday = history.loc[start:end - 1]
            if not np.array_equal(monday.index, np.arange(start, end, prices.HOUR)) or key_end not in history.index:
                continue
            futures = (monday.high.max(), monday.low.min(), history.at[key_end, 'close'])
            spot = (row.spot_monday_high, row.spot_monday_low, row.spot_key_close)
            spread.at[row.Index] = relative_move(spot, futures, row.side)
    if not np.isfinite(spread.dropna()).all():
        raise ValueError('Invalid native spot or perpetual price at an eligible close')
    return spread


def main():
    panel = pd.read_csv(wallet.PANEL).set_index('event_id', verify_integrity=True)
    spread = observations(panel)
    covered = spread.notna().astype('boolean')
    lead = spread.gt(0).astype('boolean').where(covered)
    decisions = pd.DataFrame({'covered': covered, 'spot_leads': lead, 'signed_log_difference': spread})
    decisions.to_csv(EXP / 'decisions.csv.gz')
    references = (wallet.vwap.EXP, wallet.timing.EXP, gap.fvg.EXP, gap.EXP)
    wallet.compare_filter(decisions, 'spot_leads', EXP, references)


if __name__ == '__main__':
    main()
