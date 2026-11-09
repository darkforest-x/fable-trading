"""Check actual native traded minute gaps for every executed post-doubling fixed 12H stop."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from zipfile import BadZipFile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_doubled_candle_low as stops
from yoyo.evaluation import cloud_close_entry_turnover as turnover

EXP = stops.EXP.parent/'exp-cloud-close-doubled-stop-minutes-20261109-v1'


def evidence(r,quotes):
    bars,url = turnover.minute.minute_quote(r.symbol,int(r.exit_bar_ms),quotes)
    if bars is None:
        return dict(status=url)
    amounts,vol_url = turnover.volume_hour(r.symbol,int(r.exit_bar_ms),bars,url)
    info = dict(price_url=url,volume_url=vol_url)
    if amounts is None:
        return dict(status=vol_url,**info)
    quote,base = amounts.quote,amounts.base
    vwap = quote.div(base.where(base.gt(0)))
    if (not np.isfinite(amounts[['base','quote']]).all().all() or (amounts[['base','quote']]<0).any().any()
            or base.eq(0).ne(quote.eq(0)).any() or
            ((vwap<bars.low*(1-1e-5))|(vwap>bars.high*(1+1e-5))).any()):
        return dict(status='unreconciled_volume_or_vwap',**info)
    touched = bars[base.gt(0) & bars.low.le(r.activation_low)]
    if touched.empty:
        return dict(status='no_positive_volume_at_or_below_stop',**info)
    minute = touched.iloc[0]
    at = int(touched.index[0])
    price = min(minute.open,r.activation_low)
    maximum = r.notional/r.entry_price*price
    return dict(status='observed',first_trade_minute_ms=at,native_minute_open=minute.open,
                gap_fill_price=price, gap_bps=10000*(r.exit_price-price)/r.exit_price,
                trigger_minute_turnover=quote.at[at],original_full_size_exit_upper_usdt=maximum,
                original_full_size_fits_whole_minute=maximum<=quote.at[at],**info)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed original doubled-stop minute audit')
    quotes,_ = stops.source.queue.repair.checked_quotes()
    signals = pd.read_csv(stops.EXP/'stop_activations.csv.gz')
    actual = pd.read_csv(stops.EXP/'funded_fixed.csv.gz',dtype={'symbol':str})
    tape = pd.read_csv(stops.source.ROSTER/'selected_paths.csv.gz',dtype={'symbol':str})
    signals = signals[signals.changed & signals['mode'].eq('fixed')].merge(
        actual[['event_id','symbol','notional','exit_bar_ms']],on=['event_id','exit_bar_ms'],validate='one_to_one')
    signals = signals.merge(tape[tape.tp_r.eq(0)][['event_id','entry_price']],on='event_id',validate='one_to_one')
    expected = json.loads((stops.EXP/'summary.json').read_text())['counts']['fixed']['funded_with_new_stop']
    if len(signals)!=expected or not len(signals) or len(actual)!=1585:
        raise ValueError('Wrong original funded population')
    def collect(r):
        for i in range(3):
            try:
                return dict(r._asdict(),**evidence(r,quotes))
            except (requests.RequestException,TimeoutError,BadZipFile):
                if i == 2:
                    raise
                time.sleep(1+i)
    with ThreadPoolExecutor(max_workers=3) as workers:
        rows = pd.DataFrame(workers.map(collect,signals.itertuples(index=False)))
    rows.to_csv(EXP/'minute_stop_receipts.csv.gz',index=False)
    checked = rows[rows.status.eq('observed')]
    stats = dict(requested=len(signals),observed=len(checked),states=rows.status.value_counts().to_dict())
    if len(checked):
        stats.update(adverse_gaps=int(checked.gap_bps.gt(.000001).sum()),max_adverse_bps=checked.gap_bps.max(),
            initial_max_fits_trigger_minute=int(checked.original_full_size_fits_whole_minute.sum()))
    stops.source.queue.repair.archive.prices.safe_json(EXP/'summary.json',stats)
    print(stats,flush=True)


if __name__ == '__main__':
    main()
