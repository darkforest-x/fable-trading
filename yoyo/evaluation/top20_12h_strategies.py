"""Owner-authorized finite strategy research after the 12h reclaim census.

Signal admission reads only the frozen complete 12h signal and Monday levels.
B618/B705 read the post-needle impulse inside that closed 12h candle, then
completed 15m bars through confirmation; entry is the next observed open.
C uses a new closed 15m sweep, never its later confirmation. Earlier full
12h true-range means and close-to-level distances match conditional random
timing controls. Future prices are used solely by native VectorBT execution.
The current top20 universe and inspected historical data are not blind OOS.
"""
from __future__ import annotations
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import pandas as pd
from yoyo.evaluation.reclaim_strategy_engine import (
    path_segments, crossing, after_entry, native_barriers, account_legs)

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-top20-12h-strategies-20261101-v1'
SOURCE = ROOT/'experiments/active/exp-top20-12h-1618-reclaims-20261101-v1'
OHLC = ['open','high','low','close']
STEP = pd.Timedelta(minutes=15)
CODE = ['yoyo/evaluation/top20_12h_strategies.py',
        'yoyo/evaluation/reclaim_strategy_engine.py',
        'yoyo/evaluation/top20_12h_strategy_analysis.py',
        'tests/evaluation/test_top20_12h_strategies.py']


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                              allow_nan=False, default=str)+'\n')


def snap(price, tick, up):
    return float((np.ceil(price/tick-1e-8) if up else
                  np.floor(price/tick+1e-8))*tick)


def candidates():
    """Finite list frozen before outcomes; each extension has a named baseline."""
    rows = []
    for mode in ['A_limit','B_618','B_705','C_sweep']:
        for target in ['5R','10R','near','opposite']:
            rows.append(dict(config=f'{mode}_{target}_14d', mode=mode,
                             target=target, days=14, space_filter=False))
        for days in [7,28]:
            rows.append(dict(config=f'{mode}_5R_{days}d', mode=mode,
                             target='5R', days=days, space_filter=False))
        rows.append(dict(config=f'{mode}_partial_14d', mode=mode,
                         target='partial', days=14, space_filter=False))
        rows.append(dict(config=f'{mode}_5R_space5_14d', mode=mode,
                         target='5R', days=14, space_filter=True))
    return rows


def gap_ends(f):
    breaks = np.r_[np.flatnonzero(np.diff(f.index.as_unit('ns').asi8) != STEP.value), len(f)-1]
    return breaks[np.searchsorted(breaks, np.arange(len(f)))] + 1


def signal_plan(f, row, mode, tick, ends, cfg):
    """Causal order instructions only; never inspect post-entry outcomes."""
    r = dict(row)
    t = pd.Timestamp(r['signal_close']).tz_convert('UTC')
    start = int(f.index.searchsorted(t))
    if start >= len(f) or f.index[start] != t:
        return None, 'missing_confirmation_quote'
    expiry = min(t+pd.Timedelta(hours=cfg['pending_hours']),
                 pd.Timestamp(r['expiry']).tz_convert('UTC'))
    end = min(int(f.index.searchsorted(expiry)), int(ends[start]))
    if end <= start:
        return None, 'expired'
    side = int(r['side'])
    needle = r['low'] if side == 1 else r['high']
    stop = snap(needle, tick, side < 0)-side*tick
    plan = dict(r, mode=mode, start=start, end=end, stop=stop,
                trigger_time=str(t), control=False, pair_id=r['event_id'])
    if mode == 'A_limit':
        level = snap(r['level'], tick, side < 0)
        if level <= 0 or side*(level-stop) <= 0:
            return None, 'invalid_limit_risk'
        return dict(plan, limit=level, reference=level), 'pending'
    w = f.iloc[start:end]
    if mode.startswith('B_'):
        a = int(f.index.searchsorted(pd.Timestamp(r['signal_open']).tz_convert('UTC')))
        signal = f.iloc[a:start]
        if len(signal) != 48 or not np.all(np.diff(signal.index.as_unit('ns').asi8) == STEP.value):
            return None, 'incomplete_signal'
        extremes = signal.low.to_numpy() if side == 1 else signal.high.to_numpy()
        loc = int(np.flatnonzero(extremes == needle)[-1])
        impulse = signal.iloc[loc+1:]
        if impulse.empty:
            return None, 'no_post_needle_impulse'
        peak = float(impulse.high.max() if side == 1 else impulse.low.min())
        ratio = .618 if mode == 'B_618' else .705
        retrace = peak + ratio*(needle-peak)
        touched = (w.low.le(retrace) if side == 1 else w.high.ge(retrace)).cummax()
        broken = (w.low.le(stop) if side == 1 else w.high.ge(stop)).cummax()
        prev = f.high.shift().iloc[start:end] if side == 1 else f.low.shift().iloc[start:end]
        valid = touched & ~broken & (side*(w.close-prev)>0) & (side*(w.close-r['level'])>0) & (side*(w.close-retrace)>0)
        plan.update(retrace=retrace, impulse_peak=peak, needle_i=a+loc)
    else:
        valid = (w.low.lt(needle)&w.close.gt(needle)) if side == 1 else (w.high.gt(needle)&w.close.lt(needle))
    ix = np.flatnonzero(valid.to_numpy())
    if not len(ix):
        return None, 'no_confirmation'
    confirm = start+int(ix[0]); entry_i = confirm+1
    if entry_i >= end or entry_i >= len(f) or f.index[entry_i] != f.index[confirm]+STEP:
        return None, 'no_next_open_before_expiry'
    if mode == 'C_sweep':
        new_tip = float(f.low.iloc[confirm] if side == 1 else f.high.iloc[confirm])
        stop = snap(new_tip, tick, side < 0)-side*tick
    price = float(f.open.iloc[entry_i])
    if stop <= 0 or side*(price-stop) <= 0:
        return None, 'opening_quote_invalidates_stop'
    return dict(plan, start=entry_i, reference=price, stop=stop,
                confirm_time=str(f.index[confirm]+STEP), limit=None), 'market'


def fill_plan(f, segments, plan, cfg):
    """Delegate resting fills to native crossing; market entries use known open."""
    start = int(plan['start']); side = int(plan['side'])
    if plan.get('limit') is not None:
        fill = crossing(segments, 4*start, 4*int(plan['end']), plan['limit'], side)
        if fill is None:
            return None
        segment, price = fill
        # A newly submitted limit already marketable at the first quote is taker.
        fee = cfg['taker_fee'] if segment == 4*start else cfg['maker_fee']
    else:
        segment, price, fee = 4*start, float(f.open.iloc[start]), cfg['taker_fee']
    risk = side*(plan['reference']-plan['stop'])
    if risk <= 0:
        raise ValueError('Invalid predeclared risk')
    return dict(plan, entry_segment=segment, entry_i=segment//4, entry=price,
                entry_time=str(f.index[segment//4]), risk=risk, entry_fee=fee,
                gap_through_stop=bool(side*(price-plan['stop']) <= 0))


def execute_targets(f, seg, entry, ends, path, cfg):
    """One max-horizon native search per unique target, cached by shorter holds."""
    side = int(entry['side']); i = int(entry['entry_i']); price = entry['entry']
    risk = entry['risk']; tick = entry['tick']; start = int(entry['entry_segment'])
    max_expiry = f.index[i]+pd.Timedelta(days=28)
    end = min(int(f.index.searchsorted(max_expiry)), int(ends[i]))
    # Time exit is at the first quote of expiry; barriers strictly before it.
    tail = after_entry(seg, start, 4*end, price)
    if entry.get('control'):
        targets = {k:price+side*risk*v for k,v in entry['target_ratios'].items()}
    else:
        width = entry['monday_high']-entry['monday_low']
        targets = {'5R':price+side*5*risk, '10R':price+side*10*risk,
                   'near':entry['monday_low'] if side == 1 else entry['monday_high'],
                   'opposite':entry['monday_low']+1.618*width if side == 1 else entry['monday_high']-1.618*width}
    records = []
    for name, target0 in targets.items():
        target = snap(target0, tick, side > 0)
        if target <= 0 or side*(target-price) <= 0:
            continue
        hit = native_barriers(tail, entry['stop'], target, side)
        # Shorter holding caps reuse this native first hit, no second search.
        horizons = [7,14,28] if name == '5R' else [14]
        for days in horizons:
            expiry = f.index[i]+pd.Timedelta(days=days)
            expiry_i = int(f.index.searchsorted(expiry))
            target_available = (expiry_i < len(f) and f.index[expiry_i] == expiry
                and expiry_i <= ends[i] and f.index[expiry_i-1]+STEP == expiry)
            first_abs = start+hit[0] if hit is not None else 4*len(f)+1
            if hit is not None and first_abs < 4*expiry_i:
                j = first_abs//4; exit_price = hit[1]; reason = hit[2]; closed=True
            elif target_available:
                j = expiry_i; exit_price=float(f.open.iloc[j]); reason='time'; closed=True
            else:
                j = min(int(ends[i])-1, len(f)-1); exit_price=float(f.close.iloc[j]); reason='censored'; closed=False
            ident = f"{entry['event_id']}|{entry['mode']}|{path}|{name}|{days}"
            rec = {k:v for k,v in entry.items() if k not in ['target_ratios']}
            records.append(dict(rec, leg_id=ident, path=path, target_name=name,
                days=days, target=target, target_r=side*(target-price)/risk,
                exit_time=str(f.index[j]), exit_i=j, exit_price=exit_price,
                exit_segment=(first_abs%4 if reason in ['stop','target'] else 0),
                reason=reason, closed=closed,weight=1.))
    # Two framework-owned half-size legs reproduce an unchanged protective stop.
    # Native crossing outcomes are reused; no second path search is performed.
    by_name={r['target_name']:r for r in records if r['days']==14}
    if '5R' in by_name and 'opposite' in by_name and by_name['opposite']['target_r']>by_name['5R']['target_r']:
        for name in ['5R','opposite']:
            r=by_name[name]
            records.append(dict(r,target_name='partial_'+name,weight=.5,leg_id=r['leg_id']+'|half'))
    return records


def control_plans(f, obs, row, signal_entry, ends, tick, cfg):
    """Conditional timing null: preserve source risk/TR, target R and order type.

    Random timestamps match symbol/NY week/side/prior12h vol bucket and
    half-TR close-distance bucket; Tue/Thu admission is preserved. Risk is a
    sampled nuisance parameter from the matched signal, not a deployable
    control feature. Admission geometry is deliberately removed in this null.
    """
    scale = row['tr_prior14']
    if not np.isfinite(scale) or scale <= 0 or not np.isfinite(row['vol_bucket']):
        return []
    distance = int(np.floor(abs(row['close']-row['level'])/scale*2))
    pool = obs[(obs.week==row['week']) & (obs.side==row['side']) &
               (obs.vol_bucket==row['vol_bucket']) & obs.inside &
               (obs.distance_bucket==distance) & (obs.event_id!=row['event_id'])]
    if not len(pool):
        return []
    rng = np.random.default_rng(cfg['seed']+int(row['bar_i'])*7+(row['side']+1))
    chosen = pool.iloc[rng.choice(len(pool), min(cfg['controls_per_signal'],len(pool)),replace=False)]
    controls=[]
    width=row['monday_high']-row['monday_low']; side=int(row['side'])
    near=row['monday_low'] if side==1 else row['monday_high']
    opp=row['monday_low']+1.618*width if side==1 else row['monday_high']-1.618*width
    ratios={'5R':5.,'10R':10.,'near':side*(near-signal_entry['entry'])/signal_entry['risk'],
            'opposite':side*(opp-signal_entry['entry'])/signal_entry['risk']}
    for number,r in enumerate(chosen.to_dict('records')):
        t=pd.Timestamp(r['signal_close']).tz_convert('UTC'); start=int(f.index.searchsorted(t))
        if start>=len(f) or f.index[start]!=t: continue
        end=min(int(f.index.searchsorted(min(t+pd.Timedelta(hours=cfg['pending_hours']),pd.Timestamp(r['expiry']).tz_convert('UTC')))),int(ends[start]))
        if end<=start: continue
        ref=snap(r['level'],tick,side<0) if row['mode']=='A_limit' else float(f.open.iloc[start])
        risk=signal_entry['risk']/scale*r['tr_prior14']
        stop=snap(ref-side*risk,tick,side<0)
        if stop<=0 or side*(ref-stop)<=0:continue
        controls.append(dict(r, mode=row['mode'],start=start,end=end,stop=stop,
            limit=ref if row['mode']=='A_limit' else None,reference=ref,
            trigger_time=str(t),control=True,pair_id=row['event_id'],
            event_id=f"{row['event_id']}|random{number}",control_source_id=r['event_id'],
            target_ratios=ratios,tick=tick))
    return controls


def load_inputs(symbol, receipt):
    f=pd.read_csv(receipt['source'],float_precision='round_trip')
    f.index=pd.DatetimeIndex(pd.to_datetime(f.pop('open_time'),utc=True)).as_unit('ns')
    assert f.index.is_monotonic_increasing and f.index.is_unique
    assert np.isfinite(f[OHLC]).all().all() and (f[OHLC]>0).all().all()
    assert f.high.ge(f[OHLC].max(axis=1)).all() and f.low.le(f[OHLC].min(axis=1)).all()
    events=pd.read_csv(SOURCE/'events.csv',float_precision='round_trip')
    events=events[(events.symbol==symbol)&events.kind.eq('pure_wick')]
    weekday=pd.to_datetime(events.signal_open,utc=True).dt.tz_convert('Asia/Shanghai').dt.dayofweek
    events=events[weekday.isin([1,3])].copy()
    obs=pd.read_csv(SOURCE/'observations.csv.gz',float_precision='round_trip')
    obs=obs[obs.symbol.eq(symbol)&obs.inside&obs.tr_prior14.gt(0)].copy()
    days=pd.to_datetime(obs.signal_open,utc=True).dt.tz_convert('Asia/Shanghai').dt.dayofweek
    obs=obs[days.isin([1,3])]
    obs['distance_bucket']=np.floor(abs(obs.close-obs.level)/obs.tr_prior14*2)
    return f,events,obs


def process(receipt, cfg, tick):
    symbol=receipt['symbol'];dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    begin=time.monotonic();p=Path(receipt['source']);stat=p.stat()
    assert stat.st_size==receipt['bytes'] and stat.st_mtime_ns==receipt['mtime_ns'], 'Changed frozen input'
    code_stamps={name:dict(bytes=(ROOT/name).stat().st_size,mtime_ns=(ROOT/name).stat().st_mtime_ns)
                 for name in CODE[:2]+['yoyo/evaluation/monday_timed_engine.py']}
    if (dest/'done.json').exists():
        old=json.loads((dest/'done.json').read_text())
        assert old['config']==cfg and old['code_stamps']==code_stamps, 'Changed completed execution'
        return old
    f,events,obs=load_inputs(symbol,receipt);ends=gap_ends(f)
    segments={path:path_segments(f[OHLC].to_numpy(),path) for path in ['OHLC','OLHC']}
    plans=[];coverage=[]
    for row in events.to_dict('records'):
        for mode in ['A_limit','B_618','B_705','C_sweep']:
            plan,status=signal_plan(f,row,mode,tick,ends,cfg)
            coverage.append(dict(event_id=row['event_id'],symbol=symbol,mode=mode,
                                 week=row['week'],signal_close=row['signal_close'],status=status))
            if plan:plans.append(dict(plan,tick=tick))
    legs=[];entry_records=[];matches=[]
    for plan in plans:
        for path,seg in segments.items():
            entry=fill_plan(f,seg,plan,cfg)
            if entry is None:
                entry_records.append(dict(event_id=plan['event_id'],mode=plan['mode'],path=path,filled=False));continue
            entry_records.append(dict(entry,path=path,filled=True))
            legs.extend(execute_targets(f,seg,entry,ends,path,cfg))
            controls=control_plans(f,obs,plan,entry,ends,tick,cfg)
            matches.append(dict(event_id=plan['event_id'],mode=plan['mode'],path=path,controls=len(controls)))
            for control in controls:
                ce=fill_plan(f,seg,control,cfg)
                entry_records.append(dict(control if ce is None else ce,path=path,filled=ce is not None))
                if ce:legs.extend(execute_targets(f,seg,ce,ends,path,cfg))
    x,fills=account_legs(pd.DataFrame(legs),cfg)
    x.to_csv(dest/'legs.csv.gz',index=False);fills.to_csv(dest/'fills.csv.gz',index=False)
    pd.DataFrame(coverage).to_csv(dest/'coverage.csv',index=False)
    pd.DataFrame(entry_records).to_csv(dest/'entries.csv.gz',index=False)
    pd.DataFrame(matches).to_csv(dest/'matching.csv',index=False)
    assert p.stat().st_size==stat.st_size and p.stat().st_mtime_ns==stat.st_mtime_ns
    done=dict(symbol=symbol,signals=len(events),plans=len(plans),legs=len(x),fills=len(fills),
              seconds=time.monotonic()-begin,source=receipt,config=cfg,code_stamps=code_stamps,
              builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              versions={k:version(k) for k in ['vectorbt','numpy','pandas','numba']})
    dump(dest/'done.json',done);return done


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',nargs='*');args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before execution')
    for name in CODE:
        subprocess.run(['git','ls-files','--error-unmatch',name],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    cfg=json.loads((EXP/'config.json').read_text())
    assert cfg['candidates']==candidates()
    receipts=json.loads((SOURCE/'input_receipt.json').read_text())['sources']
    ranking=json.loads((ROOT/cfg['ranking_source']).read_text())['top20']
    ticks={r['symbol']:r['tick'] for r in ranking}
    for r in receipts:
        if not args.symbols or r['symbol'] in args.symbols:
            done=process(r,cfg,ticks[r['symbol']])
            print(json.dumps({k:done[k] for k in ['symbol','signals','plans','legs','fills','seconds']},default=str),flush=True)


if __name__=='__main__':main()
