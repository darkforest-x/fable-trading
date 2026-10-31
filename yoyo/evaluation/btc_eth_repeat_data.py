"""Causal BTC/ETH repeated Monday-1.618 research inputs (owner 2026-10-31).

Reuse registered OKX 5m OHLCV and read-only public cache. No fills or PnL here.
Feature inputs: completed 5m OHLCV; simple 14-bar true-range mean on completed
15m bars; completed 4h EMA20/50; returns over 15m/1h/4h/24h; preceding 288-bar
volume median; completed Monday range; prior episode's observed OHLC through
the submission close only. No current touch candle enters entry features.
NY week grouping uses calendar localization, not a fixed UTC seven-day offset.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-btc-eth-repeat-1618-20261031-v1'
CACHE = Path.home() / 'Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3'
NY = 'America/New_York'
STEP = pd.Timedelta(minutes=5)
FEATURES = ['is_eth', 'side', 'range_pct', 'reward_r', 'width_atr', 'atr_pct',
            'distance_atr', 'ret15', 'ret60', 'ret240', 'ret1440', 'ema4h_gap',
            'volume_ratio', 'wick', 'close_location', 'ordinal_log',
            'since_previous_log', 'prior_favorable_atr', 'prior_adverse_atr',
            'prior_near', 'hours_left', 'monday_close_position']


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str, allow_nan=False)+'\n')


def config():
    return json.loads((EXP/'config.json').read_text())


def rounded(value, tick, up):
    return float((np.ceil(value/tick-1e-8) if up else np.floor(value/tick+1e-8))*tick)


def week_key(index):
    local = index.tz_convert(NY).tz_localize(None)
    return (local.normalize()-pd.to_timedelta(local.weekday, unit='D')).strftime('%Y-%m-%d')


def bounds(week):
    date = pd.Timestamp(week)
    return tuple((date+pd.Timedelta(days=d)).tz_localize(NY).tz_convert('UTC') for d in (0, 1, 7))


def load_snapshot():
    cfg = config()
    dest = EXP/'inputs'
    dest.mkdir(parents=True, exist_ok=True)
    if (dest/'receipt.json').exists():
        return {s: read_frame(s) for s in cfg['symbols']}
    frames, receipts = {}, {}
    for symbol in cfg['symbols']:
        path = ROOT/f'data/crypto/market/exp-monday-12h-partial-recovery-20261026-v1/{symbol}/candles.csv.gz'
        if not path.exists():
            raise FileNotFoundError(f'Registered source unavailable: {path}')
        raw = pd.read_csv(path, float_precision='round_trip')
        raw.index = pd.to_datetime(raw.open_time, utc=True)
        raw = raw[['open', 'high', 'low', 'close', 'volume']].astype(float)
        with sqlite3.connect(CACHE.as_uri()+'?mode=ro', uri=True) as db:
            values = [json.loads(r[0]) for r in db.execute(
                'SELECT raw FROM candles WHERE key=? AND t>=? AND t<? ORDER BY t',
                (f'https://www.okx.com|{symbol}-USDT-SWAP|5m',
                 int((raw.index[-1]-pd.Timedelta(days=1)).timestamp()*1000),
                 int(pd.Timestamp(cfg['snapshot_before']).timestamp()*1000)))]
        recent = pd.DataFrame(values, columns=['ts','open','high','low','close','volume','base','quote','confirm'])
        recent = recent[recent.confirm.astype(str).eq('1')]
        recent.index = pd.to_datetime(recent.ts.astype('int64'), unit='ms', utc=True)
        recent = recent[raw.columns].astype(float)
        common = raw.index.intersection(recent.index)
        if len(common):
            np.testing.assert_allclose(raw.loc[common], recent.loc[common], rtol=0, atol=1e-6)
        frames[symbol] = pd.concat([raw, recent[recent.index > raw.index[-1]]])
        receipts[symbol] = dict(source=str(path), real_source=str(path.resolve()),
                                size=path.stat().st_size, mtime=path.stat().st_mtime,
                                overlap_rows=len(common), cache_tail_rows=int((recent.index > raw.index[-1]).sum()))
    end = min(f.index[-1] for f in frames.values())
    for symbol, frame in frames.items():
        frame = frame[(frame.index >= pd.Timestamp('2023-12-31T16:00:00Z')) & (frame.index <= end)].copy()
        assert frame.index.is_unique and (np.diff(frame.index.asi8) == STEP.value).all()
        assert np.isfinite(frame.to_numpy()).all() and (frame[['open','high','low','close']] > 0).all().all()
        assert (frame.high >= frame[['open','close']].max(axis=1)).all()
        assert (frame.low <= frame[['open','close']].min(axis=1)).all()
        frame.index += STEP
        frame.index.name = 'close_time'
        frames[symbol] = frame
        frame.to_csv(dest/f'{symbol}_5m.csv.gz')
        receipts[symbol].update(rows=len(frame), first_close=str(frame.index[0]), last_close=str(frame.index[-1]))
    dump(dest/'receipt.json', dict(sources=receipts, common_last_close=str(end+STEP),
                                 builder_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()))
    return frames


def read_frame(symbol):
    frame = pd.read_csv(EXP/f'inputs/{symbol}_5m.csv.gz', float_precision='round_trip')
    frame.index = pd.to_datetime(frame.pop('close_time'), utc=True)
    return frame


def base_features(frame):
    """All rolling/resample results become visible at their completed close."""
    agg = dict(open='first', high='max', low='min', close='last', volume='sum')
    bars = frame.resample('15min', closed='right', label='right').agg(agg)
    bars = bars[frame.close.resample('15min', closed='right', label='right').count().eq(3)]
    tr = pd.concat([bars.high-bars.low, (bars.high-bars.close.shift()).abs(),
                    (bars.low-bars.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().reindex(frame.index, method='ffill')
    h4 = frame.close.resample('4h', closed='right', label='right').last()
    h4 = h4[frame.close.resample('4h', closed='right', label='right').count().eq(48)]
    gap = h4.ewm(span=20, adjust=False, min_periods=20).mean()-h4.ewm(span=50, adjust=False, min_periods=50).mean()
    out = pd.DataFrame(index=frame.index)
    out['atr'] = atr
    out['atr_pct'] = atr/frame.close
    out['ema4h_gap'] = gap.reindex(frame.index, method='ffill')/atr
    for mins in (15,60,240,1440):
        out[f'ret{mins}'] = (frame.close/frame.close.shift(mins//5)-1)*100
    out['volume_ratio'] = frame.volume / frame.volume.shift().rolling(288, min_periods=48).median().replace(0,np.nan)
    span = (frame.high-frame.low).replace(0,np.nan)
    out['close_location'] = (frame.close-frame.low)/span
    out['upper_wick'] = (frame.high-frame[['open','close']].max(axis=1))/span
    out['lower_wick'] = (frame[['open','close']].min(axis=1)-frame.low)/span
    out['vol_bucket'] = np.floor(np.log2(out.atr_pct.clip(lower=1e-9))*4)
    return out


def make_features(frame, base, i, symbol, side, high, low, mon_close, level, ordinal, previous, expiry):
    b, close = base.iloc[i], float(frame.close.iloc[i])
    atr = float(b.atr)
    near = high if side == -1 else low
    feature = dict(is_eth=int(symbol=='ETH'), side=side, range_pct=(high-low)/close,
                   reward_r=abs(level-near)/(level*.007), width_atr=(high-low)/atr,
                   atr_pct=b.atr_pct, distance_atr=abs(level-close)/atr,
                   volume_ratio=b.volume_ratio, wick=b.upper_wick if side==-1 else b.lower_wick,
                   close_location=b.close_location if side==1 else 1-b.close_location,
                   ordinal_log=np.log1p(ordinal-1), since_previous_log=0.,
                   prior_favorable_atr=0., prior_adverse_atr=0., prior_near=0,
                   hours_left=(expiry-frame.index[i]).total_seconds()/3600,
                   monday_close_position=(mon_close-low)/(high-low) if side==1 else (high-mon_close)/(high-low))
    for name in ['ret15','ret60','ret240','ret1440','ema4h_gap']:
        feature[name] = side*float(b[name])
    if previous is not None:
        observed = frame.iloc[previous:i+1]
        favorable = level-observed.low.min() if side==-1 else observed.high.max()-level
        adverse = observed.high.max()-level if side==-1 else level-observed.low.min()
        feature.update(since_previous_log=np.log1p((i-previous+1)*5),
                       prior_favorable_atr=max(0,favorable)/atr, prior_adverse_atr=max(0,adverse)/atr,
                       prior_near=int(observed.low.min()<=near if side==-1 else observed.high.max()>=near))
    return feature


def candidates(symbol, frame):
    cfg, tick = config(), .1 if symbol=='BTC' else .01
    base = base_features(frame)
    opens = frame.index-STEP
    fif = frame.copy()
    fif.index = opens
    fif = fif.resample('15min').agg(dict(open='first',high='max',low='min',close='last'))
    counts = pd.Series(1,index=opens).resample('15min').sum()
    fif = fif[counts.eq(3)]
    rows, orders, controls = [], [], []
    rng = np.random.default_rng(cfg['seed']+int(symbol=='ETH'))
    for week in sorted(set(week_key(opens))):
        start, formed, expiry = bounds(week)
        if start < pd.Timestamp(cfg['start']):
            continue
        mon = frame[(opens >= start) & (opens < formed)]
        if len(mon)!=288:
            continue
        high, low, mon_close = float(mon.high.max()), float(mon.low.min()), float(mon.close.iloc[-1])
        span = high-low
        window = fif[(fif.index >= formed) & (fif.index < expiry)]
        end_i = int(frame.index.searchsorted(min(expiry,frame.index[-1]), side='right')-1)
        complete = expiry <= frame.index[-1]
        for side in (-1,1):
            level = high+.618*span if side==-1 else low-.618*span
            targets = [high,low,low-.618*span] if side==-1 else [low,high,high+.618*span]
            hit = (window.low <= level) & (window.high >= level)
            starts = window.index[hit & ~hit.shift(fill_value=False)]
            previous = None
            for ordinal, stamp in enumerate(starts, 1):
                indices = np.flatnonzero((opens >= stamp) & (opens < stamp+pd.Timedelta(minutes=15)) &
                                         (frame.low.to_numpy() <= level) & (frame.high.to_numpy() >= level))
                assert len(indices)
                fill_i, event = int(indices[0]), f'{symbol}-{week}-{side}-{ordinal}'
                i = fill_i-1
                entry = rounded(level,tick,side==-1)
                stop = rounded(entry*(1-side*cfg['stop_fraction']),tick,side==-1)
                eligible = i>=0 and side*(frame.close.iloc[i]-entry)>=tick-1e-8 and i<end_i
                features = make_features(frame,base,i,symbol,side,high,low,mon_close,entry,ordinal,previous,expiry)
                row = dict(event_id=event,symbol=symbol,week=week,side=side,ordinal=ordinal,
                           submission_i=i, touch_i=fill_i, submission_time=str(frame.index[i]),
                           touch_open=str(opens[fill_i]), expiry=str(expiry), complete=complete,
                           eligible=bool(eligible), level=level, entry=entry,stop=stop,
                           monday_high=high,monday_low=low,monday_close=mon_close,
                           previous_i=-1 if previous is None else previous, vol_bucket=float(base.vol_bucket.iloc[i]),
                           **{f'f_{k}':v for k,v in features.items()})
                rows.append(row)
                previous = fill_i
                if not eligible:
                    continue
                shared = dict(event_id=event,symbol=symbol,week=week,side=side,ordinal=ordinal,
                              entry_i=i,submission_i=i,expire_i=fill_i,end_i=end_i,entry=entry,stop=stop,
                              risk=side*(entry-stop),order_mode='limit',kind='signal',complete=complete)
                for name, price in zip(cfg['targets'],targets):
                    orders.append(dict(shared,trade_id=f'{event}:{name}',target=name,
                                       target_price=rounded(price,tick,side==1)))
                # Matched controls are chosen without observing their future outcomes.
                pool = np.flatnonzero((frame.index >= formed) & (frame.index < min(expiry,frame.index[-1])) &
                                      base.vol_bucket.eq(base.vol_bucket.iloc[i]).to_numpy())
                pool = pool[pool != i]
                if len(pool) < cfg['controls_per_event']:
                    continue
                for k, ci in enumerate(rng.choice(pool,cfg['controls_per_event'],replace=False)):
                    ce = float(frame.close.iloc[ci])
                    cs = rounded(ce*(1-side*cfg['stop_fraction']),tick,side==-1)
                    for name, price in zip(cfg['targets'],targets):
                        target = rounded(ce*(1+(price-entry)/entry),tick,side==1)
                        controls.append(dict(shared, trade_id=f'{event}:control{k}:{name}',
                                             entry_i=int(ci),submission_i=int(ci),expire_i=int(ci)+1,
                                             entry=ce,stop=cs,risk=side*(ce-cs),order_mode='market',
                                             kind='control',control_id=k,target=name,target_price=target))
    return pd.DataFrame(rows), pd.DataFrame(orders+controls)


def main():
    frames = load_snapshot()
    all_events=[]
    for symbol, frame in frames.items():
        events, entries = candidates(symbol,frame)
        events.to_csv(EXP/f'{symbol}_events.csv.gz',index=False)
        entries.to_csv(EXP/f'{symbol}_entries.csv.gz',index=False)
        all_events.append(events)
        print(symbol, dict(rows=len(frame),events=len(events),eligible=int(events.eligible.sum()),
                           complete=int(events.complete.sum()),native_orders=len(entries)),flush=True)
    pd.concat(all_events).to_csv(EXP/'events.csv.gz',index=False)


if __name__ == '__main__':
    main()
