"""Generate research entry signals from completed 12h candles, plus a separate review cohort.

One initial observation per asset/Beijing day comes from the existing daily-touch
ledger. No outcome field is used by the signal builder. A and B are alternative
entries. Both record the next bar open, before costs, as an entry reference.
A full-path portfolio backtest is separate.
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from census12 import HOUR, OUT, read_raw
from filter_stop_before_target import beijing_time, to_ms
from pepe_retests import BAR, wick_data
from scan import bounds

KEY = ['币安合约', '纽约区间月', '方向', '触轨小时（北京）']
SIGNALS = OUT / '12h首针收回与二次试线_入场候选.csv'
REVIEW = OUT / '12h单针与二次试线_去重复盘样本.csv'


def review_set():
    old = pd.read_csv(OUT / '12小时长影线_局部单针_穿轨不足5pct_再碰前反转至少20pct.csv')
    old = old.rename(columns={'触轨小时（北京时间）': KEY[-1]})
    old = old[KEY].assign(原严格单针=True)
    new = pd.read_csv(OUT / '2033组_首针与达标前触轨根数.csv')
    new = new[new['首根长影线收回'] & new['至多触轨根数'].le(2)]
    new = new.rename(columns={'首触小时（北京）': KEY[-1], '至多触轨根数': '月度首触至达标碰轨根数'})
    rows = old.merge(new[KEY + ['月度首触至达标碰轨根数']], on=KEY, how='outer', validate='one_to_one')
    rows['原严格单针'] = rows['原严格单针'].eq(True)
    rows['月度首触至达标碰轨根数'] = rows['月度首触至达标碰轨根数'].astype('Int64')
    return rows.sort_values([KEY[-1], KEY[0]])


@lru_cache(maxsize=None)
def expiry(month):
    return bounds(month)[2].value // 10**6


def setups(raw, month, side, touch, rail):
    sign = 1 if side == '下轨' else -1
    _, qualified, _ = wick_data(raw, touch, rail, sign)
    if not qualified:
        return
    bar = touch // BAR * BAR
    decision = bar + BAR
    end = expiry(month)
    if decision >= end:
        return
    times = raw[:, 0]
    i = times.searchsorted(decision)
    if i >= len(raw) or times[i] != decision:
        return
    signal_bar = raw[i-12:i]
    adverse = signal_bar[:, 3].min() if sign == 1 else signal_bar[:, 2].max()
    if sign * (adverse / rail - 1) <= -0.05 + 1e-12:
        return
    if sign * (signal_bar[-1, 4] / rail - 1) >= 0.20 - 1e-12:
        return
    yield 'A 首针收回', decision, raw[i, 1]

    # Stop watching at the first retest, rail expiry, a gap, or an intervening
    # 5% adverse / 20% favorable excursion. All tests end before the B entry.
    for j in range(i, times.searchsorted(end), 12):
        next_bar = times[j] + BAR
        if next_bar >= end or j+12 >= len(raw) or times[j+12] != next_bar:
            return
        block = raw[j:j+12]
        if not np.array_equal(block[:, 0], times[j] + np.arange(12)*HOUR):
            return
        highest, lowest = block[:, 2].max(), block[:, 3].min()
        best, worst = (highest, lowest) if sign == 1 else (lowest, highest)
        if sign * (best/rail-1) >= 0.20-1e-12 or sign * (worst/rail-1) <= -0.05+1e-12:
            return
        if ((block[:, 3] <= rail) & (block[:, 2] >= rail)).any():
            if sign * (block[-1, 4] - rail) > 0:
                yield 'B 二次试线收回', int(next_bar), raw[j+12, 1]
            return


def main():
    daily = pd.read_csv(OUT / '逐日首次碰轨_7天14天45天.csv.gz',
                        usecols=['币安合约','纽约区间月','方向','首次触价小时（北京）','轨价'])
    rows = []
    for symbol, d in daily.groupby('币安合约', sort=False):
        raw = read_raw(symbol)
        for month, side, anchor, rail in d[['纽约区间月','方向','首次触价小时（北京）','轨价']].itertuples(index=False, name=None):
            for version, decision, price in setups(raw, month, side, to_ms(anchor), rail):
                rows.append([symbol,month,side,anchor,version,beijing_time(decision),rail,price])
    frame = pd.DataFrame(rows, columns=KEY+['方案','入场参考小时（北京）','1.618轨价','下一根开盘价'])
    frame = frame.sort_values(['入场参考小时（北京）','币安合约',KEY[-1]]).drop_duplicates(
        ['币安合约','纽约区间月','方向','方案','入场参考小时（北京）'])
    frame.to_csv(SIGNALS, index=False, encoding='utf-8-sig')
    review = review_set()
    review.to_csv(REVIEW, index=False, encoding='utf-8-sig')
    print('复盘',len(review),'组',len(review.drop_duplicates(KEY[:-1])))
    print(frame.groupby('方案').size().to_string())


if __name__ == '__main__':
    main()
