"""Count distinct 12h touches before the first favorable 20% move in the confirmed cohort.

Use the same side and first-touch time as the source ledger. A retest only counts
while the original month's rail is active. The outcome may occur after expiry.
An H1 candle that hits both the rail and the target can leave the final count
ambiguous. Save the lower and upper bounds separately.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from census12 import HOUR, OUT, read_raw
from filter_stop_before_target import beijing_time, to_ms
from scan import bounds

BAR = 12 * HOUR
SOURCE = OUT / '首触确认机会_45天内20pct先于5pct止损.csv'
DEST = OUT / '2033组_首针与达标前触轨根数.csv'


def wick_data(raw, clock, rail, sign):
    start = clock // BAR * BAR
    i = raw[:, 0].searchsorted(start)
    block = raw[i:i + 12]
    if not np.array_equal(block[:, 0], start + np.arange(12) * HOUR):
        return False, False, np.nan
    o, c = block[0, 1], block[-1, 4]
    h, l = block[:, 2].max(), block[:, 3].min()
    wick = min(o, c) - l if sign == 1 else h - max(o, c)
    eps = rail * 1e-12
    pure = min(sign * (o - rail), sign * (c - rail)) > eps
    ratio = wick / (h - l) if h > l else 0.
    return pure, pure and ratio >= 0.5 - 1e-12 and wick + eps >= 2 * abs(o - c), ratio


def observe(row, raw):
    symbol, month, side, entry_text, rail, target_text = row
    entry, target = to_ms(entry_text), to_ms(target_text)
    expiry = bounds(month)[2].value // 10**6
    sign = 1 if side == '下轨' else -1
    times = raw[:, 0]
    i, j = times.searchsorted([entry, target])
    assert times[i] == entry and times[j] == target and raw[i, 3] <= rail <= raw[i, 2]
    # Exclude the target hour until its opening price has resolved any double hit.
    stop = min(j, times.searchsorted(expiry))
    window = raw[i:stop]
    hit = window[(window[:, 3] <= rail) & (window[:, 2] >= rail), 0].astype('int64')
    confirmed = {entry // BAR: entry}
    for t in hit:
        confirmed.setdefault(int(t) // BAR, int(t))
    optional = 0
    target_bin = target // BAR
    if target < expiry and target_bin not in confirmed and raw[j, 3] <= rail <= raw[j, 2]:
        opening = raw[j, 1]
        if sign * (opening - rail) <= 0:
            confirmed[target_bin] = target
        elif sign * (opening - rail) < 0.2 * rail:
            optional = 1
    touches = list(confirmed.values())
    second = touches[1] if len(touches) > 1 else None
    pure, long_wick, ratio = wick_data(raw, entry, rail, sign)
    second_pure = wick_data(raw, second, rail, sign)[0] if second is not None else None
    return [symbol, month, side, entry_text, beijing_time(second), target_text, rail,
            (target-entry)//HOUR, len(touches), len(touches)+optional,
            pure, long_wick, round(100*ratio, 2), second_pure]


def main():
    ledger = pd.read_csv(SOURCE)
    fields = ['币安合约','纽约区间月','触价轨道（下轨做多／上轨做空）','触价小时（北京）',
              '入场轨价','确认结果所在小时（北京）']
    records = []
    for symbol, group in ledger[fields].groupby('币安合约'):
        raw = read_raw(symbol)
        records.extend(observe(row, raw) for row in group.itertuples(index=False, name=None))
    cols = ['币安合约','纽约区间月','方向','首触小时（北京）','第二根触轨小时（北京）',
            '首达20%小时（北京）','1.618轨价','达标用时（小时）','至少触轨根数','至多触轨根数',
            '首根实体在线内','首根长影线收回','首根影线占比%','第二根实体在线内']
    a = pd.DataFrame(records, columns=cols).sort_values(['首触小时（北京）','币安合约'])
    a.to_csv(DEST, index=False, encoding='utf-8-sig')
    for name, part in [('全部',a),('长影线',a[a['首根长影线收回']])]:
        exact = part[part['至少触轨根数'].eq(part['至多触轨根数'])]
        for count in [1, 2]:
            d = exact[exact['至少触轨根数'].eq(count)]
            print(name, count, len(d), 'over7d',sum(d['达标用时（小时）']>168))
        print(name,'不确定',len(part)-len(exact))
    strict = a[a['首根长影线收回'] & a['至少触轨根数'].eq(2) & a['至多触轨根数'].eq(2)]
    print(strict[(strict['达标用时（小时）']>168) & strict['第二根实体在线内'].eq(True)].to_string(index=False,max_rows=85))


if __name__=='__main__':
    main()
