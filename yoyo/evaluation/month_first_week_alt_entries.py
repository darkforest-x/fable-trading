"""First monthly 1.618 tests on frozen OKX H1. Event outcomes alone look ahead.

Entry features use the first complete NY week and OHLC through the confirming
close (up to 24 hours from the first touch). Controls match trailing 24-hour
volatility on same-month alternatives, solely as retrospective benchmarks.
"""
import argparse
import json
from pathlib import Path
from zlib import crc32

import numpy as np
import pandas as pd

from yoyo.evaluation.month_first_week_touch_review import bounds

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'experiments/active/exp-alt-month-first-week-entries-20261110-v1'
SOURCE = ROOT / 'experiments/active/exp-cloud-allmarket-screen-20261108-v1'
RETIRED = ROOT / 'experiments/active/exp-cloud-close-okx-retired-notices-20261109-v1'
RETIRED_BARS = ROOT / 'data/research/cloud_close_okx_retired_prices/series'
CONFIG = json.loads((DEST / 'config.json').read_text())
RULES = CONFIG['entry_rules']
HOUR = pd.Timedelta(hours=1)
CALENDAR = [(str(m), *bounds(str(m))) for m in pd.period_range(
    CONFIG['first_month'], CONFIG['last_month'], freq='M')]


def universe():
    current = pd.read_csv(SOURCE / 'universe.csv')
    coverage = pd.read_csv(SOURCE / 'input_coverage.csv').set_index('symbol')
    rows = []
    for item in current.itertuples():
        if item.symbol in {'BTC', 'ETH', 'USDC'}:
            continue
        path = coverage.loc[item.symbol, 'source_path']
        folder = ROOT / path if pd.notna(path) else SOURCE / 'inputs' / item.symbol
        rows.append((item.symbol, item.cohort, folder / 'bars1h.csv.gz',
                     pd.Timestamp(item.archive_end_ms, unit='ms', tz='UTC')))
    retired = pd.read_csv(RETIRED / 'crosschecked_lifecycles.csv')
    for item in retired[retired.selected].itertuples():
        rows.append((item.symbol, 'verified_retired', RETIRED_BARS /
                     (item.symbol + '-USDT-SWAP') / 'bars1h.csv.gz', pd.Timestamp(item.delivery_utc)))
    frame = pd.DataFrame(rows, columns=['symbol', 'cohort', 'file', 'cutoff'])
    assert frame.symbol.is_unique
    return frame


def prices(spec):
    f = pd.read_csv(spec.file, usecols=['time', 'open', 'high', 'low', 'close'])
    f.index = pd.DatetimeIndex(pd.to_datetime(f.pop('time'), unit='ms', utc=True))
    f = f.loc[(f.index + HOUR <= spec.cutoff) & (f.index >= CALENDAR[0][1] - 2 * 24 * HOUR)]
    if (not f.index.is_monotonic_increasing or not f.index.is_unique or
            ((f.index.minute != 0) | (f.index.second != 0)).any() or f.isna().any().any()
            or (f.low <= 0).any() or (f.high < f[['open', 'close', 'low']].max(axis=1)).any()
            or (f.low > f[['open', 'close']].min(axis=1)).any()):
        raise ValueError(f'Invalid hourly data: {spec.symbol}')
    return f


def prepare_symbol(spec, bars):
    month_rows, events, candidates = [], [], []
    if bars.empty:
        return month_rows, events, candidates
    idx = bars.index
    hi, lo, closes, opens = (bars[k].to_numpy() for k in ['high', 'low', 'close', 'open'])
    volatility = np.log(bars.close).diff().rolling(24).std().shift(1)
    # require a continuous 25-close history before each control clock
    continuity = idx.to_series().diff().eq(HOUR).rolling(25).min().eq(1).to_numpy()
    vol = volatility.where(continuity).to_numpy()
    for month, start, ready, end in CALENDAR:
        if idx[-1] < start or idx[0] >= end:
            continue
        a,b,c = idx.searchsorted([start, ready, end])
        common = dict(symbol=spec.symbol, month=month, cohort=spec.cohort)
        row = dict(**common, complete=False, has_touch=False)
        month_rows.append(row)
        if b-a != (ready-start)//HOUR or c-b != (end-ready)//HOUR:
            continue
        row['complete'] = True
        high, low = hi[a:b].max(), lo[a:b].min()
        if high <= low:
            row['flat_week'] = True
            continue
        for side, sign, level, edge in [('lower', 1, low-.618*(high-low), low),
                                        ('upper', -1, high+.618*(high-low), high)]:
            if level <= 0:
                continue
            crossing = (lo[b:c] <= level) if sign == 1 else (hi[b:c] >= level)
            if not crossing.any():
                continue
            i = b + crossing.argmax()
            row['has_touch'] = True
            touch = idx[i]
            key = f'{spec.symbol}|{month}|{side}'
            event = dict(**common, event_id=key, side=side, touch=touch, high=high, low=low,
                         level=level, literal=(lo[i] <= level <= hi[i]))
            events.append(event)
            for n in (24, 168):
                if i+n < len(idx) and idx[i+n] == touch + n*HOUR:
                    fav = hi[i+1:i+n+1].max() if sign == 1 else lo[i+1:i+n+1].min()
                    adv = lo[i+1:i+n+1].min() if sign == 1 else hi[i+1:i+n+1].max()
                    event.update({f'inward_{n}h_pct': max(0., 100*sign*(fav/level-1)),
                                  f'outward_{n}h_pct': max(0., 100*sign*(1-adv/level)),
                                  f'reached_near_{n}h': sign*(fav-edge)>=0,
                                  f'inward_close_{n}h_pct': 100*sign*(closes[i+n]/level-1)})
            pending = set(RULES)
            streak = 0
            for j in range(i, min(i + CONFIG['confirmation_window_hours'], c-1)):
                inward = sign*(closes[j]-level) > 0
                streak = streak+1 if inward else 0
                if ((j > i and (hi[j]>=edge if sign==1 else lo[j]<=edge))
                        or sign*(closes[j]-edge) >= 0):
                    break
                next_bar = idx[j+1]
                tests = {'touch_1h':j==i, 'touch_close_inside':j==i and inward,
                         'touch_close_outside':j==i and sign*(closes[j]-level)<0,
                         'reclaim_1h':inward, 'reclaim_two_1h':streak>=2,
                         'reclaim_4h':inward and next_bar.hour%4==0,
                         'reclaim_1d':inward and next_bar.hour==0}
                for rule in RULES:
                    if rule not in pending or not tests[rule]:
                        continue
                    pending.remove(rule)
                    if sign*(opens[j+1]-edge) >= 0:
                        continue
                    trade_id = f'{key}|{rule}'
                    base = dict(**event, entry_time=next_bar, entry_rule=rule,
                                trade_id=trade_id, is_control=False, paired_id=trade_id)
                    candidates.append(base)
                    # Random times are comparison entries, not trading signals.
                    if not np.isfinite(vol[j+1]):
                        continue
                    loc = np.arange(b,c)
                    finite = np.isfinite(vol[loc])
                    if not finite.any():
                        continue
                    quartiles = np.quantile(vol[loc[finite]], [.25,.5,.75])
                    bucket = np.digitize(vol[loc],quartiles)
                    eligible = loc[finite & (bucket == np.digitize(vol[j+1],quartiles)) &
                                   (np.abs(loc-j-1)>=24)]
                    if not len(eligible):
                        continue
                    seed = CONFIG['seed'] + crc32(f'{key}|{next_bar.isoformat()}'.encode())
                    k = int(np.random.default_rng(seed).choice(eligible))
                    scale = opens[k]/opens[j+1]
                    candidates.append(dict(base, trade_id=trade_id+'|control', is_control=True,
                        entry_time=idx[k], touch=idx[k]-HOUR, high=high*scale, low=low*scale,
                        level=level*scale))
    return month_rows, events, candidates


def prepare():
    months,events,candidates,manifest = [],[],[],[]
    for spec in universe().itertuples():
        bars=prices(spec)
        m,e,t=prepare_symbol(spec,bars)
        months.extend(m);events.extend(e);candidates.extend(t)
        manifest.append(dict(symbol=spec.symbol,cohort=spec.cohort,file=str(spec.file.relative_to(ROOT)),
                             hours=len(bars),cutoff=spec.cutoff,first=bars.index.min(),last=bars.index.max()))
    datasets = {'months':months,'events':events,'candidates':candidates,'sources':manifest}
    for name, data in datasets.items():
        frame=pd.DataFrame(data)
        if name=='candidates':
            assert frame.trade_id.is_unique
            frame=frame[['trade_id','paired_id','event_id','is_control','symbol','month','cohort',
                         'side','touch','entry_time','entry_rule','level','high','low']]
        frame.to_csv(DEST/(name+'.csv.gz'),index=False)
    print({k:len(v) for k,v in datasets.items()}, flush=True)


def execute():
    from yoyo.evaluation.month_first_week_alt_trades import run_month_first_week_alt_trades
    groups = pd.read_csv(DEST/'candidates.csv.gz',parse_dates=['entry_time','touch']).groupby('symbol')
    files = DEST/'checkpoints'
    files.mkdir(exist_ok=True)
    completed = []
    for spec in universe().itertuples():
        if spec.symbol not in groups.groups:
            continue
        path = files/(spec.symbol+'.csv.gz')
        if not path.exists():
            candidates=groups.get_group(spec.symbol)
            result=run_month_first_week_alt_trades({spec.symbol:prices(spec)}, candidates)
            assert len(result)==len(candidates) and set(result.trade_id)==set(candidates.trade_id)
            tmp=path.with_suffix('.tmp')
            result.to_csv(tmp,index=False,compression='gzip'); tmp.replace(path)
        completed.append(path)
        if len(completed)%50==0:
            print('completed symbols',len(completed),flush=True)
    pd.concat([pd.read_csv(p) for p in completed],ignore_index=True).to_csv(DEST/'trades.csv.gz',index=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','execute'])
    args=parser.parse_args()
    {'prepare':prepare,'execute':execute}[args.action]()
