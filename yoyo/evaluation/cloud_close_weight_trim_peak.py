"""Test the Binance weight-trim drawdown gate with synchronized native minute prices."""
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_efficiency_binance_bounds as native
from yoyo.evaluation import cloud_close_weight_trim as weights

EXP = weights.EXP


def main():
    output = EXP / 'binance_minute_drawdown.json'
    if output.is_file():
        raise FileExistsError('The single-hour drawdown evidence already exists')
    eq = pd.read_csv(EXP/'hourly_Binance.csv.gz',index_col=0,parse_dates=True)
    # Use the hour of the highest upper envelope before the worst possible H1 drawdown.
    worst = (eq.lower_nav / eq.upper_nav.cummax()).idxmin()
    peak = eq.loc[:worst].upper_nav.idxmax()
    start = peak.value//1_000_000 - weights.w.HOUR
    extra = set(json.loads((weights.admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((weights.admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    with weights.admin.archive.market_quotes(extra):
        data,_ = weights.binance_inputs(extra)
    roster = data['selected']
    roster = roster[roster.entry_ms.le(start) & roster.exit_bar_ms.ge(start)].copy()
    if roster.entry_ms.eq(start).any() or roster.exit_bar_ms.eq(start).any():
        raise ValueError('Proof hour includes a native entry or exit')
    trims = pd.read_csv(EXP/'trims_Binance.csv.gz')
    if trims.hour.eq(start).any():
        raise ValueError('Active exposure changed inside the proposed proof hour')
    parents = pd.read_csv(EXP/'funded_Binance.csv.gz').set_index('event_id')
    trim_qty = trims.loc[trims.hour.lt(start)].groupby(['event_id','col'])['size'].sum()
    roster['quantity'] = (roster.event_id.map(parents.notional) / roster.entry_price
                          * np.where(roster.tp_r.eq(0), 1/3, 2/3)
                          - trim_qty.reindex(pd.MultiIndex.from_arrays([roster.event_id,roster.slot]),fill_value=0.).to_numpy())
    if (roster.quantity <= 0).any():
        raise ValueError('Unreconciled reduced-in-size active leg')
    signed = (roster.quantity*roster.side).groupby(roster.symbol).sum()
    def bars(symbol):
        quotes, meta = native._official_daily_bars(symbol,start,'1m',60_000)
        return symbol,quotes.reset_index(drop=True),meta
    with ThreadPoolExecutor(max_workers=3) as worker:
        snapshots = list(worker.map(bars, signed.index))
    opens, closes, minimum, maximum = [], [], [], []
    for sym, quotes, _ in snapshots:
        qty = signed[sym]
        opens.append(qty*quotes.open.to_numpy())
        closes.append(qty*quotes.close.to_numpy())
        minimum.append(qty*(quotes.low if qty>0 else quotes.high).to_numpy())
        maximum.append(qty*(quotes.high if qty>0 else quotes.low).to_numpy())
    delta = np.asarray(opens)[:,0].sum()
    base = eq.loc[peak,'post_trim_nav'] - delta
    minute_nav = base + np.sum(closes,axis=0)
    if not np.isclose(minute_nav[-1], eq.loc[peak,'nav'], atol=.005, rtol=1.e-7):
        raise ValueError('Actual static minute book does not reconcile to published trimmed wallet H1 close')
    # At each individual name's most favorable print, every other contract must
    # still lie inside its own simultaneous one-minute low/high envelope.
    min_book = base + np.sum(minimum,axis=0)
    peak_floor = min_book + np.max(np.asarray(maximum)-np.asarray(minimum),axis=0)
    floor = float(max(peak_floor.max(), minute_nav.max()))
    later = eq.loc[eq.index>peak,['open_nav','open_exit_nav','post_trim_nav','nav']]
    trough = later.min(axis=1).idxmin()
    measured = float(100*(1-later.loc[trough].min()/floor))
    result = dict(hour_open_utc=pd.Timestamp(start,unit='ms',tz='UTC').isoformat(),
                  actual_active_positions=len(signed),settled_quantity_by_symbol=signed.to_dict(),
                  observed_minute_close_peak=float(minute_nav.max()), proven_peak_floor=floor,
                  later_observed_trough_utc=trough.isoformat(), later_observed_trough=float(later.loc[trough].min()),
                  proven_drawdown_at_least_pct=measured, exceeds_35_pct=measured>35,
                  source=[m for _,_,m in snapshots])
    output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='source'},indent=2),flush=True)


if __name__ == '__main__':
    main()
