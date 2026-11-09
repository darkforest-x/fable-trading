"""Read-only original project V1.7 signals for the isolated VectorBT research interpreter."""
import argparse
import json

import pandas as pd

from yoyo.evaluation import cloud_allmarket_inputs as original
from yoyo.evaluation import cloud_peak10_census as census
from yoyo.monitor import cloud_signals

FIELDS = original.OHLC


def signal_keys(symbol, hour, bars, cutoff):
    """Original signal OHLC/EMA20/50 and NY Monday range; ATR14 comes from the preceding closed 12H."""
    inst = symbol+'-USDT-SWAP'
    calendar = census.anchors(hour,bars,inst)
    records = bars[FIELDS].reset_index().to_dict('records')
    ema = cloud_signals.ema_points(records)
    atr = census.atr_points(records)
    seen = set()
    keys = []
    for i,bar in enumerate(records):
        anchor = calendar[census.week_window(bar['time'])['week']]
        if anchor['week'] in seen:
            continue
        key = cloud_signals.signal(inst,bar,ema[i],anchor,cutoff)
        if key is None:
            continue
        seen.add(key['week'])
        prior = atr[i-1]
        keys.append(dict(key_open_ms=key['signal_open_ms'],entry_ms=key['confirmed_ms'],
                   symbol=symbol,side=key['side'],week=key['week'],event_id=key['id'],
                   stop_price=key['stop'],entry_order=100*prior['atr14']/prior['close']))
    return keys


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('symbol')
    parser.add_argument('hours')
    parser.add_argument('end_ms',type=int)
    parser.add_argument('--native12')
    args = parser.parse_args()
    hours = pd.read_csv(args.hours,index_col='time',float_precision='round_trip')
    hours = hours.loc[hours.index<args.end_ms]
    bars = (pd.read_csv(args.native12,index_col='time',float_precision='round_trip') if args.native12 else
            original.aggregate(hours,cloud_signals.STEP,12,'complete native hours'))
    bars.index.name = 'time'
    print(json.dumps(signal_keys(args.symbol,hours,bars,args.end_ms),allow_nan=False))
