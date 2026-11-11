"""Two views of the very same second 12h close. No trades or parameter search.

prepare() chooses on the prefix only. Read price_cards then context_cards, and
commit a separate per-card judgement at each pass before reveal(). Follow-up
uses at most the next two completed 12h candles under the still-active rail.
"""
import argparse
import hashlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / 'exp-month-firstweek-blind-review-20261111-v1'
sys.path[:0] = [str(PREVIOUS), str(HERE.parent / 'exp-alt-isolated-12h-firstweek-wicks-20261111-v1')]
from strategy_signals import BAR, CUTOFF, HOUR, OUT, WINDOWS, read_raw

DATA = OUT / 'adjacent_replay_v1'
WINDOW = {month:(start,ready,end) for month,start,ready,end in WINDOWS}
KNOWN = {('FARTCOINUSDT','2026-09','short'), ('ZKUSDT','2024-10','short'),
         ('XAGUSDT','2026-05','short'), ('APRUSDT','2026-08','long')}
NEW = 12


def digest(key, salt):
    return hashlib.sha256(('|'.join(key) + salt).encode()).hexdigest()


def segment(raw, start, n):
    t = raw[:, 0]
    ix = t.searchsorted(start)
    q = raw[ix:ix+n]
    if (len(q) != n or not np.array_equal(q[:,0], start + np.arange(n)*HOUR)
            or start+n*HOUR > CUTOFF):
        return None
    return q


def signed(x, rail, direction):
    return (x / rail - 1) * 100 * direction


def candle(hours, rail, direction):
    vals = signed(np.array([hours[0,1], hours[:,2].max(), hours[:,3].min(), hours[-1,4]]), rail, direction)
    return vals[0], max(vals[1:3]), min(vals[1:3]), vals[3]


def make_card(key, first_close, rail, raw):
    """Use two completed UTC12h OHLC, trailing H1 closes, and the frozen NY first week."""
    symbol, month, side = key
    start, ready, end = WINDOW[month]
    second_close = first_close + BAR
    if second_close >= end:
        return None
    h = segment(raw, first_close-BAR, 24)
    if h is None:
        return None
    d = 1 if side == 'long' else -1
    first, second = h[:12], h[12:]
    touches = (second[:,3] <= rail) & (second[:,2] >= rail)
    if not touches.any() or min(signed(first[-1,4],rail,d), signed(second[-1,4],rail,d)) <= 0:
        return None
    week = segment(raw, start, (ready-start)//HOUR)
    if week is None:
        raise ValueError(f'Incomplete frozen anchor for {key}')
    high, low = week[:,2].max(), week[:,3].min()
    edge = low if side=='long' else high
    assert np.isclose(rail, edge - d*.618*(high-low), rtol=1e-10, atol=0)
    name = digest(key,'adjacent-masked-v1')[:6].upper()
    price = dict(card=name)
    for n, bars in enumerate((first,second),1):
        price.update({f'{k}{n}':v for k,v in zip('OHLC',candle(bars,rail,d))})
    prior = segment(raw, first_close-4*BAR,36)
    price['prior_closes'] = (' / '.join(f'{x:.1f}' for x in signed(prior[11::12,4],rail,d))
                             if prior is not None else '')
    last = int(np.flatnonzero(touches)[-1])
    hourly = signed(second[:,4],rail,d)
    context = dict(card=name, progress_to_near_edge_pct=100*(second[-1,4]-rail)/(edge-rail),
                   last_three_closes=' / '.join(f'{x:.2f}' for x in hourly[-3:]),
                   h1_closes_from_last_touch=' / '.join(f'{x:.2f}' for x in hourly[last:]),
                   hours_closed_outside_second=int(np.count_nonzero(hourly<0)),
                   last_touch_hour=last+1)
    meta = dict(card=name,symbol=symbol,month=month,side=side,rail=rail,near_edge=edge,
                second_close=second_close,group='旧例' if key in KNOWN else '新例')
    return price, context, meta


def prepare():
    DATA.mkdir(parents=True,exist_ok=True)
    signal = pd.read_csv(OUT/'signals.csv.gz', usecols=['symbol','month','side','rank_12h','entry_ms','rail'],
                         float_precision='round_trip').query('rank_12h == 1')
    old_symbols = set(pd.read_csv(PREVIOUS/'revealed.csv',usecols=['symbol']).symbol)
    rows = {(r.symbol,r.month,r.side):r for r in signal.itertuples()}
    keys = sorted(KNOWN) + sorted([k for k in rows if k[0] not in old_symbols],
                                  key=lambda k:digest(k,'sample-adjacent-v1'))
    selected = []; names = set(); covered = set()
    for key in keys:
        if key[0] in names:
            continue
        if key in rows:
            r = rows[key]; raw = read_raw(key[0])
            card = make_card(key, int(r.entry_ms), float(r.rail), raw)
            if card is not None:
                selected.append(card); names.add(key[0]); covered.add(key)
        if len(covered-KNOWN)==NEW:
            break
    assert covered.issuperset(KNOWN) and len(selected)==NEW+len(KNOWN)
    for ix, name in enumerate(['price_cards','context_cards','key']):
        frame = pd.DataFrame([c[ix] for c in selected]).sort_values('card')
        assert frame.card.is_unique
        frame.to_csv(DATA/f'{name}.csv',index=False)
    print(f'{len(selected)} cards prepared')


def remaining_closes(raw, decision, expires, rail, side):
    """Describe later completed UTC12h closes through rail expiry, with the original rail as reference."""
    direction = 1 if side == 'long' else -1
    best = None
    best_before_loss = None
    first_outside = None
    best_time = None
    count = 0
    complete = True
    for start in range(decision, expires - BAR + 1, BAR):
        hours = segment(raw, start, 12)
        if hours is None:
            complete = False
            break
        value = signed(hours[-1, 4], rail, direction)
        when = start + BAR
        count += 1
        if best is None or value > best:
            best, best_time = value, when
        if first_outside is None:
            if value < 0:
                first_outside = when
            elif best_before_loss is None or value > best_before_loss:
                best_before_loss = value
    return dict(followup_12h_closes=count, followup_complete=complete,
                first_outside_12h_close_ms=first_outside, max_favorable_close_pct=best,
                max_favorable_close_ms=best_time, best_inside_close_before_loss_pct=best_before_loss)


def reveal():
    meta = pd.read_csv(DATA/'key.csv',float_precision='round_trip')
    notes = [pd.read_csv(HERE/f'calls_{name}.csv') for name in ('price','context')]
    for f in notes:
        assert set(f.card)==set(meta.card) and f.card.is_unique and set(f.call)<=set(['推进','失守','观察'])
    meta=meta.merge(notes[0],on='card').merge(notes[1],on='card',suffixes=('_price','_context'))
    price=pd.read_csv(DATA/'price_cards.csv',float_precision='round_trip').set_index('card')
    checks=[]
    for r in meta.itertuples():
        raw=read_raw(r.symbol); d=1 if r.side=='long' else -1
        threshold=price.loc[r.card,['H1','H2']].max()
        outcome='拉锯'; closes=[]; stamp=None
        for offset in (0,BAR):
            t=int(r.second_close)+offset
            if t+BAR>WINDOW[r.month][2]:
                outcome='区间到期';break
            seg=segment(raw,t,12)
            if seg is None:
                outcome='数据未全';break
            c=signed(seg[-1,4],r.rail,d);closes.append(f'{c:.3f}')
            if c>threshold or c<0:
                outcome='推进' if c>threshold else '失守';stamp=t+BAR;break
        followup = remaining_closes(raw, int(r.second_close), WINDOW[r.month][2], r.rail, r.side)
        checks.append(dict(card=r.card,outcome=outcome,observed_closes=' / '.join(closes),resolved_ms=stamp,
                           **followup))
    full=(meta.merge(pd.DataFrame(checks),on='card').merge(price.reset_index(),on='card')
          .merge(pd.read_csv(DATA/'context_cards.csv'),on='card'))
    full.to_csv(HERE/'observations.csv',index=False,encoding='utf-8-sig')
    print(full[['card','group','symbol','month','side','call_price','call_context','outcome','observed_closes']].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','reveal'])
    if p.parse_args().action=='prepare': prepare()
    else: reveal()
