"""Reveal anonymous discretionary observations only after all three calls have been frozen."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'exp-alt-isolated-12h-firstweek-wicks-20261111-v1'))
from strategy_signals import OUT, HOUR, CUTOFF, WINDOWS, read_raw

BAR = 12 * HOUR
HORIZON = 45 * 24
BASE = OUT / 'blind_review_v1'
BOUNDS = {m: (start, ready, end) for m, start, ready, end in WINDOWS}


def pct(price, rail, sign):
    return 100 * sign * (price / rail - 1)


def pre_move(hourly, rail, sign, size):
    """Is the favourable excursion known to have occurred after first touch by decision close?"""
    edge = hourly[:, 2 if sign == 1 else 3]
    if np.any(pct(edge[1:], rail, sign) >= size) or pct(hourly[0, 4], rail, sign) >= size:
        return '已到'
    if pct(edge[0], rail, sign) >= size:
        return '首触小时内顺序不明'
    return '未到'


def adjudicate(meta, first, retry):
    q = read_raw(meta.symbol)
    q = q[q[:, 0] + HOUR <= CUTOFF]
    t = q[:, 0].astype('int64')
    sign = 1 if meta.side == 'long' else -1
    start, ready, month_end = BOUNDS[meta.month]
    week = q[(t >= start) & (t < ready)]
    assert len(week) == (ready - start) // HOUR
    hi, lo = week[:, 2].max(), week[:, 3].min()
    rail = lo - .618 * (hi-lo) if sign == 1 else hi + .618 * (hi-lo)
    anchor = int(meta.first_touch_open_ms)
    first_hour = np.flatnonzero((t >= max(anchor, ready)) & (t < anchor+BAR) &
                                (q[:, 3] <= rail) & (q[:, 2] >= rail))[0]
    results = []
    triggers = []
    if first == 'first_wick':
        triggers.append(('首针', anchor + BAR))
    if retry == 'second_touch':
        triggers.append(('二试', int(meta.phase3_retest_open_ms) + BAR))
    for kind, decision in triggers or [('观察', None)]:
        info = dict(blind_id=meta.blind_id, symbol=meta.symbol, month=meta.month, side=meta.side,
                    group=meta.group, rail=rail, watch_hour_ms=int(t[first_hour]), call=kind,
                    decision_ms=decision)
        if decision is None:
            results.append(info)
            continue
        at = int(t.searchsorted(decision))
        history = q[first_hour:at]
        if len(history) == 0 or t[at-1] + HOUR != decision or np.any(np.diff(history[:,0]) != HOUR):
            raise ValueError(f'Incomplete pre-decision path for {meta.blind_id}')
        for size in (5, 20):
            info[f'before_{size}pct'] = pre_move(history, rail, sign, size)
        if at >= len(q) or t[at] != decision or decision >= month_end:
            info['outcome'] = '无有效次根开盘'
            results.append(info)
            continue
        entry = q[at, 1]
        info['entry_reference'] = entry
        info['entry_distance_pct'] = pct(entry, rail, sign)
        info['outcome'] = '数据未完'
        for i, row in enumerate(q[at:at+HORIZON]):
            stamp, _, high, low, closing = row
            if stamp != decision + i * HOUR:
                info['outcome'] = '数据缺口'
                break
            reached = pct(high if sign == 1 else low, rail, sign) >= 20
            broken = (stamp + HOUR) % BAR == 0 and pct(closing, rail, sign) < 0
            if reached or broken:
                info['outcome'] = '先到20%' if reached else '先收破原轨'
                info['outcome_hour_ms'] = int(stamp)
                info['outcome_known_ms'] = int(stamp + HOUR)
                break
            if i + 1 == HORIZON:
                info['outcome'] = '45天内均未到'
        results.append(info)
    return results


def main():
    stages = [pd.read_csv(HERE / f'calls{i}.csv').set_index('blind_id') for i in (1, 2, 3)]
    key = pd.read_csv(BASE / 'deck/key.csv')
    assert set(key.blind_id) == set(stages[0].index)
    assert all(x.index.is_unique for x in stages)
    assert set(stages[2].index).issubset(stages[0].index)
    results = []
    for m in key.itertuples():
        decision = stages[0].at[m.blind_id, 'call']
        retry = stages[2].at[m.blind_id, 'call'] if m.blind_id in stages[2].index else None
        results.extend(adjudicate(m, decision, retry))
    df = pd.DataFrame(results)
    for col in ('watch_hour_ms', 'decision_ms', 'outcome_known_ms'):
        if col in df:
            df[col.replace('_ms','_beijing')] = pd.to_datetime(
                df[col], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    df.to_csv(HERE / 'revealed.csv', index=False)


if __name__ == '__main__':
    main()
