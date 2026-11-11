"""Sequential replay of each frozen Binance first touch per Beijing day.

prepare reads only identities/rails from the daily census. A bar tape has one row per
completed UTC 12h, computed from its own 12 H1 bars. A case card reads at most the
first touching 12h close. The follow-up pass consumes that tape strictly in order.
The original rail expires at the NY month boundary. No trades or future labels are
used to form a card, define confirmation or decide membership.
"""
import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'exp-alt-isolated-12h-firstweek-wicks-20261111-v1'
sys.path.insert(0, str(SOURCE))
from strategy_signals import BAR, CUTOFF, HOUR, WINDOWS, read_raw
from all_contract_touches import DEST

OUT = DEST / 'all_replay_v1'
WINDOW = {m: (s,r,e) for m,s,r,e in WINDOWS}
KEY = ['symbol','month','side']
INPUT = DEST / 'catalogue' / '所有合约_每日首次触轨.csv.gz'
SIDE = {'上轨':'short', '下轨':'long'}
ROUNDTRIP = dict(float_precision='round_trip')


def census():
    columns = ['币安合约','标的类别','纽约区间月','方向','首次触价小时（北京）','轨价']
    frame = pd.read_csv(INPUT, usecols=columns, **ROUNDTRIP)[columns]
    frame.columns = ['symbol','underlying','month','side','beijing_touch','rail']
    frame['side'] = frame.side.map(SIDE)
    frame['touch_ms'] = (pd.to_datetime(frame.beijing_touch, utc=True)
                         .astype('datetime64[ns, UTC]').astype('int64') // 1_000_000 - 8*HOUR)
    frame['id'] = frame.symbol + '|' + frame.beijing_touch.str[:10]
    assert frame.id.is_unique and len(frame)==50986 and frame.side.notna().all()
    return frame


def shift(price, rail, direction):
    return (price / rail - 1) * (direction * 100)


def geometry(hours, rail, direction):
    """OHLC and hourly path observable at this bar close; hours is exactly its 12 H1 candles."""
    opening = hours[0,1]
    high, low = hours[:,2].max(), hours[:,3].min()
    close = hours[-1,4]
    position = shift(hours[:,4], rail, direction)
    touches = (hours[:,3] <= rail) & (hours[:,2] >= rail)
    touch_indices = np.flatnonzero(touches)
    noninside = np.flatnonzero(position <= 0)
    wick = min(opening,close)-low if direction==1 else high-max(opening,close)
    return dict(
        open_pct=shift(opening,rail,direction), high_pct=shift(high if direction==1 else low,rail,direction),
        low_pct=shift(low if direction==1 else high,rail,direction), close_pct=position[-1],
        wick_range_pct=100*wick/(high-low) if high>low else np.nan,
        hours_inside=int(np.sum(position>0)), ending_inside_streak=int(11-noninside[-1]) if len(noninside) else 12,
        last_touch_hour=int(touch_indices[-1]+1) if len(touch_indices) else 0,
        h1_3=position[-3], h1_2=position[-2], h1_1=position[-1])


def make_tape(raw, first_touch, ready, until, rail, direction):
    """Aggregate source candles available by each close; omit incomplete or gapped bars."""
    times = raw[:,0]
    tape = {}
    for start in range(first_touch//BAR*BAR, until-BAR+1, BAR):
        i = times.searchsorted(start)
        hours = raw[i:i+12]
        if len(hours)!=12 or not np.array_equal(hours[:,0], start+np.arange(12)*HOUR):
            continue
        entry = geometry(hours, rail, direction)
        active = hours[hours[:,0]>=ready]
        entry['touched_active'] = bool(np.any((active[:,3]<=rail)&(active[:,2]>=rail)))
        tape[start+BAR]=entry
    return tape


def form_card(event, tape, raw, week, expires):
    """Touch H1 and first complete 12h close only. The source requires the first Beijing-day touch."""
    d = 1 if event.side=='long' else -1
    t = event.touch_ms
    when = t//BAR*BAR+BAR
    hh = raw[raw[:,0].searchsorted(t)]
    assert hh[0]==t and hh[3]<=event.rail<=hh[2]
    edge = week[0] if d==1 else week[1]
    assert np.isclose(event.rail, edge-d*.618*(week[1]-week[0]), rtol=1e-10,atol=0)
    b = tape.get(when)
    out = dict(id=event.id,symbol=event.symbol,month=event.month,side=event.side,underlying=event.underlying,
               beijing_touch=event.beijing_touch,touch_ms=t,rail=event.rail,near_edge=edge,
               touch_h1_close_pct=shift(hh[4],event.rail,d), scheduled_close_ms=when)
    if b is None:
        reason = '原轨到期' if when>expires else ('冻结截止' if when>CUTOFF else '行情不足')
        out.update(first_close_state='无完整12H', close_unavailable=reason)
    else:
        assert b['touched_active']
        c = b['close_pct']
        state = '收在线外' if c<0 else '收在线上' if c==0 else (
            '实体在线内且长影' if b['open_pct']>0 and b['wick_range_pct']>=50 else '收回')
        out.update(first_close_state=state, close_unavailable='', close_pct=c,
                   progress_to_edge_pct=c/shift(edge,event.rail,d)*100,
                   close_inside_streak=b['ending_inside_streak'], last_touch_hour=b['last_touch_hour'])
    return out


def prepare():
    OUT.mkdir(exist_ok=True,parents=True)
    tape_rows, cards = [], []
    for symbol,g in census().groupby('symbol',sort=True):
        raw=read_raw(symbol)
        for (month,side), group in g.groupby(['month','side']):
            start, ready, end=WINDOW[month]
            week=raw[raw[:,0].searchsorted(start):raw[:,0].searchsorted(ready)]
            assert np.array_equal(week[:,0],np.arange(start,ready,HOUR))
            levels=(week[:,3].min(),week[:,2].max())
            assert group.rail.nunique()==1
            tape=make_tape(raw,int(group.touch_ms.min()),ready,min(CUTOFF,end),
                           group.rail.iloc[0],1 if side=='long' else -1)
            for when,fields in tape.items():
                tape_rows.append(dict(symbol=symbol,month=month,side=side,time_ms=when,**fields))
            for item in group.itertuples():
                cards.append(form_card(item,tape,raw,levels,end))
    save(pd.DataFrame(tape_rows),'bar_tape')
    save(pd.DataFrame(cards),'cards')
    print(f'{len(cards)} case cards and {len(tape_rows)} unique rail/bar readings',flush=True)


def save(frame,name):
    dst=OUT/(name+'.csv.gz')
    tmp=dst.with_suffix('.tmp')
    frame.to_csv(tmp,index=False,compression={'method':'gzip','compresslevel':4,'mtime':0})
    tmp.replace(dst)


def continuous_hours(raw, after_touch, end):
    """Use H1 bars after the touch hour until the first missing timestamp or end of observation."""
    i,j=raw[:,0].searchsorted([after_touch,end])
    hours=raw[i:j]
    if len(hours):
        gaps=np.flatnonzero(hours[:,0]!=after_touch+np.arange(len(hours))*HOUR)
        if len(gaps): hours=hours[:gaps[0]]
    return hours


def read_path(event, bars, hours, end, raw_last):
    """Advance each case from first touch through the last contiguous close while its rail is active.

    A reclaim begins the first time a complete 12h closes strictly inside. Its favorable
    high/low is then known. The first following close past that extreme confirms departure.
    An immediately adjacent retest that reclaims and has not already confirmed departure
    extends the reference to the two bars' favorable extreme. Loss abandons this attempt.
    A later reclaim starts another attempt, recorded separately.
    """
    d=1 if event.side=='long' else -1
    limit=int(hours[-1,0]+HOUR) if len(hours) else event.touch_ms+HOUR
    end_reason='原轨到期' if limit>=end else ('冻结截止' if limit+HOUR>CUTOFF
               else '可读行情结束' if limit>=raw_last+HOUR else '行情缺口')
    obs=dict(id=event.id,path_until_ms=limit,path_end=end_reason,hours_after_touch=len(hours))
    # The touch H1's high/low could precede the first touch. Use only its close for the first mark.
    signed_closes=np.r_[event.touch_h1_close_pct,shift(hours[:,4],event.rail,d)]
    best_idx=int(np.argmax(signed_closes))
    marked_20=np.flatnonzero(signed_closes>=20)
    obs.update(best_h1_close_pct=signed_closes[best_idx],best_h1_close_ms=event.touch_ms+(1+best_idx)*HOUR,
               best_post_touch_hour_extreme_pct=float(max(0,shift(hours[:,2 if d==1 else 3],event.rail,d).max())) if len(hours) else None)
    if len(marked_20):
        obs['first_20_h1_close_ms']=int(event.touch_ms+(1+marked_20[0])*HOUR)
    start=event.scheduled_close_ms
    rail_inside=None
    ref=None
    reclaim_time=None
    attempt=0
    number=0
    adjacency=None
    confirmed=False
    first_24='拉锯'
    first_threshold=None
    for when in range(start, limit+1, BAR):
        b=bars.get(when)
        if b is None:  # a partial initial bar can still be followed by fully observed complete bars
            assert when==start
            continue
        number+=1
        c=b.close_pct
        if number==1 and when==start:
            first_threshold=b.high_pct
        if first_threshold is not None and start<when<=start+2*BAR and first_24=='拉锯':
            if c<0: first_24='失守'
            elif c>first_threshold: first_24='推进'
        if when==start+BAR and rail_inside is True and b.touched_active and c>0:
            adjacency=when
            obs['adjacent_ms']=when
            obs['adjacent_threshold_pct']=max(b.high_pct,bars[start].high_pct)
        if adjacency is not None and adjacency<when<=adjacency+2*BAR and 'adjacent_result_24h' not in obs:
            if c<0 or c>obs['adjacent_threshold_pct']:
                obs['adjacent_result_24h']='失守' if c<0 else '推进'
        if c<0:
            obs.setdefault('first_outside_ms',when)
            if ref is not None: obs.setdefault('first_loss_after_reclaim_ms',when)
            ref=None
            confirmed=False
        elif c>0:
            if ref is None:
                attempt+=1
                ref=b.high_pct
                reclaim_time=when
                obs.setdefault('first_reclaim_ms',when)
                obs.setdefault('first_reclaim_close_pct',c)
            elif not confirmed:
                if c>ref:
                    confirmed=True
                    if 'first_confirmation_ms' not in obs:
                        obs.update(first_confirmation_ms=when, confirmation_attempt=attempt,
                                   confirmation_close_pct=c, confirmation_reference_pct=ref,
                                   hours_to_confirmation=(when-event.touch_ms)/HOUR,
                                   confirmation_progress_pct=100*c/shift(event.near_edge,event.rail,d))
                elif when==reclaim_time+BAR and b.touched_active:
                    ref=max(ref,b.high_pct)
        rail_inside=c>0
    if adjacency is not None and 'adjacent_result_24h' not in obs:
        obs['adjacent_result_24h']='拉锯' if limit>=adjacency+2*BAR else '未满24H'
    obs['firstbar_result_24h']=(first_24 if first_threshold is not None and
                                (first_24!='拉锯' or limit>=start+2*BAR) else '观察不完整')
    obs['complete_12h_read']=number
    obs['attempts']=attempt
    if 'first_confirmation_ms' in obs:
        obs['path_type']='失守后重新收回并确认' if obs.get('first_outside_ms',float('inf'))<obs['first_confirmation_ms'] else '先守住并确认'
        at_confirm=int((obs['first_confirmation_ms']-event.touch_ms)//HOUR)
        obs['max_known_h1_close_pct_at_confirmation']=signed_closes[:at_confirm].max()
        after=hours[hours[:,0]>=obs['first_confirmation_ms']]
        obs['remaining_best_h1_close_pp']=max(0,float(shift(after[:,4],event.rail,d).max()-obs['confirmation_close_pct'])) if len(after) else None
    elif number==0: obs['path_type']='无完整12H收盘'
    elif 'first_loss_after_reclaim_ms' in obs: obs['path_type']='收回后失守，尚未确认'
    elif 'first_reclaim_ms' in obs: obs['path_type']='已有收回，尚未确认'
    else: obs['path_type']='未收回'
    return obs


def follow():
    cards=pd.read_csv(OUT/'cards.csv.gz',**ROUNDTRIP)
    tape=pd.read_csv(OUT/'bar_tape.csv.gz',**ROUNDTRIP)
    groups=tape.groupby(KEY,sort=False)
    records=[]
    for symbol,g in cards.groupby('symbol',sort=True):
        raw=read_raw(symbol)
        for (month,side), group in g.groupby(['month','side']):
            key=(symbol,month,side)
            bars={b.time_ms:b for b in groups.get_group(key).itertuples()} if key in groups.indices else {}
            end=WINDOW[month][2]
            for event in group.itertuples():
                hours=continuous_hours(raw,event.touch_ms+HOUR,min(end,CUTOFF//HOUR*HOUR))
                records.append(read_path(event,bars,hours,end,raw[-1,0]))
    results=cards.merge(pd.DataFrame(records),on='id',validate='one_to_one')
    assert len(results)==len(cards) and set(results.id)==set(census().id)
    save(results,'reviewed')
    print(results.path_type.value_counts().to_string(),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['prepare','follow'])
    if parser.parse_args().stage=='prepare': prepare()
    else: follow()
