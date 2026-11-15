"""Count early ETH observations against frozen 12H keys, without simulating trades.

At each completed hour, use only the high/low since this UTC 12H bar opened,
the latest hourly close, the prior confirmed 12H EMAs and the completed NY
Monday range. The next open, eventual key and subsequent stop touches are labels.
"""
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.cloud_peak10_census import anchors
from yoyo.evaluation.cloud_peak10_inputs import EXP, HOUR
from yoyo.monitor.cloud_signals import STEP, ema_points, signal
from yoyo.monitor.monday_fib import week_window


OUT = EXP.parent / 'exp-cloud-eth-hourly-early-signal-20261115-v1'
SYMBOL = 'ETH-USDT-SWAP'


def first_hourly_candidate(hourly, start, previous, anchor):
    block = hourly.reindex(range(start, start + STEP, HOUR))
    if block[['open', 'high', 'low', 'close']].isna().any().any():
        raise ValueError(f'Incomplete eligible hour block at {start}')
    lower, upper = anchor['lower'], anchor['upper']
    low, high = float('inf'), float('-inf')
    for offset, (_, bar) in enumerate(block.iloc[:-1].iterrows(), start=1):
        low, high = min(low, bar.low), max(high, bar.high)
        fast = previous['ema20'] + (bar.close - previous['ema20']) * (2 / 21)
        slow = previous['ema50'] + (bar.close - previous['ema50']) * (2 / 51)
        if fast > slow and low < lower < bar.close < upper:
            side, stop = 1, lower * 0.985
        elif fast < slow and lower < bar.close < upper < high:
            side, stop = -1, upper * 1.015
        else:
            continue
        future = block.iloc[offset:]
        touched = future.low.min() <= stop if side == 1 else future.high.max() >= stop
        return dict(early_key_ms=start, early_side=side, early_at_ms=start + offset * HOUR,
                    early_next_open=float(future.open.iloc[0]), reference_stop=stop,
                    stop_touched_before_close=bool(touched))
    return {}


def main():
    config = json.loads((OUT / 'config.json').read_text())
    cutoff = int(pd.Timestamp(config['cutoff_utc']).timestamp() * 1000)
    directory = EXP / 'inputs' / 'ETH'
    bars, hours = (pd.read_csv(directory / f'bars{period}.csv.gz', index_col='time',
                               float_precision='round_trip') for period in ('12h', '1h'))
    bars = bars[bars.index + STEP <= cutoff]
    calendar = anchors(hours, bars, SYMBOL)
    records = bars.reset_index().to_dict('records')
    ema = ema_points(records)
    early, formal = {}, {}
    for i, bar in enumerate(records):
        start = bar['time']
        week = week_window(start)['week']
        anchor = calendar[week]
        if (week in formal or not ema[i]['ready'] or anchor['state'] != 'ready'
                or not anchor['end_ms'] <= start < start + STEP <= anchor['expires_ms']):
            continue
        if week not in early:
            candidate = first_hourly_candidate(hours, start, ema[i - 1], anchor)
            if candidate:
                early[week] = candidate
        key = signal(SYMBOL, bar, ema[i], anchor, cutoff)
        if key:
            formal[week] = dict(key_ms=start, side=key['side'], confirmed_ms=start + STEP,
                                confirmed_next_open=float(hours.loc[start + STEP, 'open']))

    source = pd.read_csv(EXP / 'signals.csv')
    original = {(r.week, int(pd.Timestamp(r.key_open_bjt).timestamp() * 1000), r.side)
                for r in source[source.symbol == 'ETH'].itertuples()}
    if {(w, r['key_ms'], r['side']) for w, r in formal.items()} != original:
        raise ValueError('Reconstructed confirmed keys differ from the frozen ETH ledger')
    rows = []
    for week in sorted(early.keys() | formal.keys()):
        candidate, key = early.get(week, {}), formal.get(week, {})
        matched = bool(candidate and key and candidate['early_key_ms'] == key['key_ms']
                       and candidate['early_side'] == key['side'])
        row = dict(week=week, matched=matched, **candidate, **key)
        if matched:
            row['lead_hours'] = (key['confirmed_ms'] - candidate['early_at_ms']) // HOUR
            row['price_improvement'] = (key['confirmed_next_open'] - candidate['early_next_open']) * key['side']
        rows.append(row)
    ledger = pd.DataFrame(rows)
    paired = ledger[ledger.matched]
    summary = dict(symbol=SYMBOL, cutoff_utc=config['cutoff_utc'], confirmed=len(formal),
                   early=len(early), matched=len(paired), unmatched_early=len(early) - len(paired),
                   missed_keys=len(formal) - len(paired), median_lead_hours=float(paired.lead_hours.median()),
                   better_open=int((paired.price_improvement > 0).sum()),
                   worse_open=int((paired.price_improvement < 0).sum()),
                   equal_open=int((paired.price_improvement == 0).sum()),
                   early_stop_touches=int(ledger.stop_touched_before_close.sum()),
                   matched_stop_touches=int(paired.stop_touched_before_close.sum()))
    ledger.to_csv(OUT / 'weeks.csv', index=False)
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
