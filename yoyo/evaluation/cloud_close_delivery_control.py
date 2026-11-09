"""Rebuild the matched Binance control with actual contract expiry dates and native index marks."""
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_complete as study

archive = study.archive
HOUR = archive.native.wallet.HOUR
EXP = study.EXP.parent / 'exp-cloud-close-delivery-control-20261109-v1'


def reference_prices(spec):
    """Use only OHLC of the exact sixty or thirty one-minute index-price bars preceding delivery."""
    marks, inputs = {}, []
    for symbol, details in spec.items():
        delivery = pd.Timestamp(details['utc'])
        minutes = details['minutes']
        filename = f'{symbol}-1m-{delivery.date()}.zip'
        url = f'https://data.binance.vision/data/futures/um/daily/indexPriceKlines/{symbol}/1m/{filename}'
        if archive.fetch(archive.CACHE, 'index1m', symbol, url)[2] not in ('cached', 'fetched'):
            raise IOError(f'Missing settlement index marks for {symbol}')
        path = archive.CACHE / 'index1m' / symbol / filename
        frame = pd.read_csv(path, header=None, usecols=range(5), names=['time','open','high','low','close'])
        if str(frame.time.iloc[0]).lower() in ('open_time','open time'):
            frame = frame.iloc[1:].copy()
        frame['time'] = pd.to_numeric(frame.time, errors='raise').astype('int64')
        expected = pd.date_range(end=delivery-pd.Timedelta(minutes=1), periods=minutes,
                                 freq='min').as_unit('ms').asi8
        bars = frame.set_index('time').reindex(expected).astype(float)
        if bars.isna().any().any() or (bars <= 0).any().any() or not (
            bars.low.le(bars[['open','close']].min(axis=1)) & bars.high.ge(bars[['open','close']].max(axis=1))).all():
            raise ValueError(f'Incomplete or invalid settlement index for {symbol}')
        marks[symbol] = {name: float(bars[name].mean()) for name in ['low','close','high']}
        inputs.append(bars.assign(symbol=symbol, archive=url))
    pd.concat(inputs).to_csv(EXP / 'settlement_index_1m.csv', index_label='minute_open_ms')
    return marks


def adjusted_legs(paths, extra, spec):
    original = pd.read_csv(study.EXP / 'original_contract_classification.csv').set_index('symbol')
    dates = original.archive_deliveryDate_ms.copy()
    same = original.current_onboardDate_ms.eq(original.archive_onboardDate_ms)
    dates.loc[same] = original.loc[same, ['archive_deliveryDate_ms', 'current_deliveryDate_ms']].min(axis=1)
    dates = dates[dates < archive.venue.END].to_dict()
    for symbol, details in spec.items():
        stamp = pd.Timestamp(details['utc']).value // 1_000_000
        if dates.get(symbol, stamp) != stamp:
            raise ValueError(f'Archive and exchange delivery clocks disagree for {symbol}')
        dates[symbol] = stamp
    entry_end = {k: t-HOUR//2 for k, t in dates.items()}
    audit = pd.read_csv(study.EXP / 'contract_lifetime_evidence.csv.gz', low_memory=False)
    for row in audit[audit.row_type.eq('extra29_archive_audit')].itertuples():
        if row.symbol not in dates:
            stamp = pd.Timestamp(row.last_positive_volume_15m_close_utc).value // 1_000_000
            entry_end[row.symbol] = dates[row.symbol] = stamp
    ranks = Counter()
    repaired = set(archive.native.patch.affected_tickers())
    for ticker in set(original.index) | extra:
        base = (archive.CACHE / 'symbols' if ticker in extra else
                archive.native.WORK if ticker in repaired else archive.venue.WORK)
        with np.load(base / ticker / 'quotes.npz') as quote:
            clocks = quote['rank_times']
            ranks.update(map(int, clocks[clocks < entry_end.get(ticker, np.inf)]))
    ticker = paths.symbol + 'USDT'
    ready = paths[paths.entry_ms.lt(ticker.map(entry_end).fillna(np.inf)) &
                  paths.entry_ms.map(ranks).fillna(0).ge(10)].copy()
    horizon = (ready.symbol + 'USDT').map(dates)
    exit_hour = horizon.floordiv(HOUR) * HOUR
    # An exit genuinely executed before a mid-hour delivery stays native. A
    # competing intrabar exit in that hour needs a finer source if selected.
    intrahour = horizon.mod(HOUR).gt(0)
    same_hour = ready.exit_bar_ms.eq(exit_hour)
    ambiguous = same_hour & intrahour & ready.exit_phase.eq('intrabar')
    forced = ready.exit_bar_ms.gt(exit_hour) | (same_hour & ~(intrahour & ready.exit_phase.eq('open')))
    ready['delivery_ambiguous'] = ambiguous
    ready['delivery'] = forced
    ready.loc[forced, 'exit_bar_ms'] = exit_hour[forced].astype('int64')
    ready.loc[forced, 'exit_phase'] = np.where(intrahour[forced], 'close', 'open')
    ready.loc[forced, 'exit_reason'] = 'delisting_settlement'
    ready.loc[forced, 'exit_fee_rate'] = archive.native.wallet.TAKER
    ready.loc[forced, 'exit_price'] = np.nan
    return ready


def admitted(rows):
    chosen, _ = archive.native.wallet.admit(rows[rows.tp_r.eq(0)], 9)
    return rows[rows.event_id.isin(chosen.event_id)].copy()


def fund(rows, marks, mode, *, slip=0.):
    forced = rows.delivery
    if rows.delivery_ambiguous.any():
        raise ValueError('A selected native exit competes with a mid-hour delivery; obtain intrahour evidence')
    for idx, row in rows.loc[forced].iterrows():
        prices = marks.get(row.symbol + 'USDT')
        if prices is None:
            raise ValueError(f'Need documented delivery and index data for {row.symbol}')
        label = 'close'
        if mode != 'proxy':
            label = 'high' if (row.side == 1) == (mode == 'favorable') else 'low'
        rows.at[idx, 'exit_price'] = prices[label]
    if rows.exit_price.isna().any():
        raise ValueError('Missing native or settlement price at a funded exit')
    data = archive.native.prepare(rows)
    metrics, pos, eq = study.tenth.split.simulate(data, 2 / 3, slip=slip, exposure=.9)
    if not np.isclose(1000+pos.cash_pnl.sum(), eq.nav.iloc[-1], atol=1e-4, rtol=1.e-8):
        raise ValueError('Corrected wallet does not reconcile')
    return metrics, pos, eq


def main():
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Delivery-corrected control study already completed')
    config = json.loads((EXP / 'config.json').read_text())
    marks = reference_prices(config['settlements'])
    extra = set(json.loads((archive.EXP / 'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((study.EXP / 'summary.json').read_text())['new_archive_cohorts']['priced'])
    paths = archive.pool(extra)
    results, records = {'index_price_scenarios': marks}, []
    with archive.market_quotes(extra):
        _, selected = study.tenth.trend.inputs('Binance', paths)
        ready = adjusted_legs(selected, extra, config['settlements'])
        real = ready[~ready.is_control]
        control = ready[ready.is_control & ready.parent_id.isin(real.parent_id)]
        frozen = pd.read_csv(study.EXP / 'funded_tenth.csv.gz')
        actual = admitted(real)
        if set(actual.event_id) != set(frozen.event_id) or actual.delivery.any():
            raise ValueError('Actual reference no longer matches an unchanged funded full-period wallet')
        results['original_funded_actual_parents_unchanged'] = len(frozen)
        for label, pool in [('paired', real[real.parent_id.isin(control.parent_id)]), ('control', control)]:
            legs = admitted(pool)
            results[label] = {}
            modes = ['proxy','adverse','favorable'] if legs.delivery.any() else ['proxy']
            for mode in modes:
                metrics, trades, eq = fund(legs.copy(), marks, mode)
                results[label][mode] = metrics['all']
                records.extend(dict(scenario=label, index_mode=mode, period=p, **m) for p,m in metrics.items())
                if mode == 'proxy':
                    trades.to_csv(EXP / f'funded_{label}.csv.gz')
                    eq.to_csv(EXP / f'hourly_{label}.csv.gz', index_label='hour_end_utc')
                    results[label]['settled_legs'] = legs.loc[legs.delivery,
                        ['event_id','symbol','side','tp_r','exit_bar_ms','exit_phase']].to_dict('records')
    pd.DataFrame(records).to_csv(EXP / 'account_metrics.csv', index=False)
    (EXP / 'summary.json').write_text(json.dumps(results, indent=2, allow_nan=False) + '\n')
    print(json.dumps(results, indent=2), flush=True)


if __name__ == '__main__':
    main()
