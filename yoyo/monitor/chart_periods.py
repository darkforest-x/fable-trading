"""Display periods over validated OKX candles; 2m uses complete UTC 1m pairs.

OKX documents native 1m/3m, but not 2m. Aggregation is display-only and never
adds a synthetic upstream bar to CandleHub or the signal/execution consumers.
Source: https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks
"""
from yoyo.data.candle_hub import STEPS, bar_open

PERIODS = ('1m', '2m', '3m', '5m', '15m', '30m', '1H', '4H', '12H', '1D')


def native_bar(period):
    return {'2m': '1m', '12H': '12Hutc', '1D': '1Dutc'}.get(period, period)


def step_ms(period):
    return 120000 if period == '2m' else STEPS[native_bar(period)]


def open_time(stamp, period):
    return stamp // 120000 * 120000 if period == '2m' else bar_open(stamp, native_bar(period))


def candles(rows, period=None, *, now=None, forming=False):
    if period != '2m':
        return [dict(time=t, open=float(r[1]), high=float(r[2]), low=float(r[3]),
                     close=float(r[4]), volume=float(r[5])) for t, r in sorted(rows.items())]
    buckets = {}
    for t, row in sorted(rows.items()):
        buckets.setdefault(open_time(t, period), {})[t] = row
    result = []
    for t, group in buckets.items():
        current = forming and now is not None and t == open_time(now, period)
        end = (now // 60000 + 1) * 60000 if current else t + 120000
        expected = list(range(t, end, 60000))
        if sorted(group) != expected or not current and any(r[8] != '1' for r in group.values()):
            continue
        parts = [group[x] for x in expected]
        result.append(dict(time=t, open=float(parts[0][1]), high=max(float(r[2]) for r in parts),
                           low=min(float(r[3]) for r in parts), close=float(parts[-1][4]),
                           volume=sum(float(r[5]) for r in parts)))
    return result
