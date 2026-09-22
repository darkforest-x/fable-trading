"""Frozen V12.6 research replay: recheck own-symbol H1 direction at the joint.

Source: owner approval on 2026-09-22 of the net-win/tail audit. Only the
joint-entry permission changes. Fifteen-minute V9 references already use the
completed H1 SMA60; treatment requires it again at the first joint event.
The two policies share candidate generation, next-open price exits, 20bp cost,
and raw reverse signals. No model training, live state, or Pine is modified.

Features use chart OHLCV through the decision close, and 60 consecutive H1
closes available at chart OPEN (Pine SMA[1]/lookahead_on). Complete five-minute
buckets are required. The merged geometry port retains an explicit unverified
native pivot-tie limitation; this is research, not native TradingView parity.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v10_4_increment as execution
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v7_fast import _data_gap, _detect_confirmed_fast
from yoyo.evaluation.spike_v10_4 import reference_long_exits
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v126-htf-recheck-20260922-v1')
CONFIG = EXP / 'config.json'
PLAN = EXP / 'PROJECT_PLAN.md'
PINE = Path('yoyo/evaluation/pine/spike_burst_v12_6.pine')
ARMS = ('baseline', 'joint_h1_recheck')
START, SPLIT, END = source.START, source.SPLIT, source.DATA_END
MINUTES = 15
SEED = 922126
TABLES = ('decisions', 'trades', 'statuses', 'controls', 'reference_boxes', 'breaks')


def dump(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str, allow_nan=False) + '\n')


def complete_bars(base, minutes):
    """Only complete valid OHLCV buckets; removed buckets remain timestamp gaps."""
    if minutes % 5:
        raise ValueError('five-minute multiples required')
    if base.empty:
        return base.copy(), 0
    raw = base[['open', 'high', 'low', 'close', 'volume']]
    valid = (np.isfinite(raw).all(axis=1) & raw.low.gt(0) & raw.volume.ge(0)
             & raw.high.ge(raw[['open', 'close', 'low']].max(axis=1))
             & raw.low.le(raw[['open', 'close', 'high']].min(axis=1)))
    groups = raw.resample(f'{minutes}min', origin='epoch')
    bars = groups.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    count = groups.close.count()
    nvalid = valid.astype(int).resample(f'{minutes}min', origin='epoch').sum()
    keep = count.eq(minutes // 5) & nvalid.eq(minutes // 5)
    return bars.loc[keep], int((count.gt(0) & ~keep).sum())


def _legacy(frame, gap, advance, volume_ratio, side):
    """Pine original confirmation, current/prior 12/3-bar fields only."""
    dense = frame.ready & frame.pastWidth.le(3) & frame.pastCrosses.ge(2)
    recent = dense.shift().rolling(12, min_periods=12).sum().gt(0)
    ph = frame.high.shift().rolling(12, min_periods=12).max().to_numpy()
    pl = frame.low.shift().rolling(12, min_periods=12).min().to_numpy()
    c = frame.close.to_numpy()
    fast = frame[['s20', 'e20']]
    early = (frame.ready & (frame.close.gt(ph) & frame.close.gt(fast.max(axis=1)) if side == 1
             else frame.close.lt(pl) & frame.close.lt(fast.min(axis=1)))).to_numpy(bool)
    quality = (frame.ready & recent & volume_ratio.ge(1.5)
        & (advance.ge(1.5) & frame.md.ge(frame.sb) & frame.middle.gt(frame.middle.shift()) if side == 1
           else advance.le(-1.5) & frame.md.le(frame.sb) & frame.middle.lt(frame.middle.shift()))).to_numpy(bool)
    n = len(frame)
    conf, high, low = np.zeros(n, bool), np.full(n, np.nan), np.full(n, np.nan)
    previous, last, parent, done, hi, lo = False, -1, -1, False, np.nan, np.nan
    for i in range(n):
        if gap[i]:
            previous, last, parent, done = False, -1, -1, False
            hi = lo = np.nan
        fresh = early[i] and not previous and (last < 0 or i-last >= 12)
        previous = early[i]
        if fresh:
            last = parent = i
            hi, lo, done = ph[i], pl[i], False
        if parent >= 0 and i-parent > 3:
            parent, done, hi, lo = -1, False, np.nan, np.nan
        if parent >= 0 and not done and quality[i] and (c[i] > hi if side == 1 else c[i] < lo):
            conf[i], high[i], low[i], done = True, hi, lo, True
    return conf, high, low


def pine_facts(bars, base, asset, tick):
    """V12.6 V9 facts and reference boxes, including post-gap volume reset.

    SMA/EMA/RMA series preserve their chart-bar recurrence across gaps. Only
    the documented volume array, ready delay and BB admission reset. The old
    shared V9 helper omits the volume reset, so it is not silently substituted.
    """
    frame = features(bars)
    gap = _data_gap(frame, MINUTES).to_numpy(bool)
    ages = np.arange(len(frame)) - np.maximum.accumulate(np.where(gap, np.arange(len(frame)), 0))
    frame['ready'] &= ages >= 12
    segments = pd.Series(gap.astype(int).cumsum(), index=frame.index)
    med = frame.volume.groupby(segments).transform(lambda v: v.rolling(20, min_periods=20).median())
    frame['rv'] = (frame.volume / med.shift()).where(med.shift().gt(0))
    advance = (frame.close-frame.close.shift(3)) / frame.atr.shift(3)
    volume_ratio = (frame.volume.rolling(3).sum() / (3*med.shift(3))).where(med.shift(3).gt(0))
    result = {}
    for side, name in ((1, 'long'), (-1, 'short')):
        conf, hi, lo = _legacy(frame, gap, advance, volume_ratio, side)
        supplied = frame[['open','high','low','close','md','sb','atr','ropeHigh','ropeLow','ready']].copy()
        supplied['legacy_confirmed'], supplied['legacy_parent_high'], supplied['legacy_parent_low'] = conf, hi, lo
        supplied['advance3'], supplied['volume_ratio3'] = advance, volume_ratio
        supplied['data_gap'], supplied['confirmed'] = gap, True
        result[name] = _detect_confirmed_fast(supplied, side)
        if side == 1:
            known = (frame.ready.to_numpy(bool) & ~gap & frame.atr.gt(0).to_numpy()
                & np.isfinite(frame[['md','sb','atr','ropeHigh','middle']]).all(axis=1).to_numpy())
            result['parent_high'], result['parent_low'] = structure_parents(
                frame.close.to_numpy(), known, conf, hi, lo, result[name])
    if (result['long'] & result['short']).any():
        raise ValueError('ambiguous raw direction')
    side = np.where(result['long'], 1, np.where(result['short'], -1, 0))
    c, atr = frame.close.to_numpy(), frame.atr.to_numpy()
    edge = np.where(side == 1, frame.ropeHigh, frame.ropeLow)
    distance = side * (c-edge) / np.where(atr > 0, atr, np.nan)
    basis = frame.close.rolling(20, min_periods=20).mean()
    width = 4*frame.close.rolling(20, min_periods=20).std(ddof=0)/basis.abs()
    threshold = width.shift().rolling(500, min_periods=500).quantile(.10, interpolation='linear')
    compressed = (ages+1 >= 520) & width.le(threshold)
    prior = pd.Series(False, index=frame.index)
    for offset in range(1, 11):
        prior |= compressed.shift(offset, fill_value=False) & compressed.shift(offset+1, fill_value=False) & compressed.shift(offset+2, fill_value=False)
    bb = ((ages+1 >= 532) & threshold.notna() & prior).to_numpy(bool)
    rv = frame.rv.to_numpy()
    clock = frame.index + pd.Timedelta(minutes=MINUTES)
    bundle = (asset not in ('', 'USDC')) & np.isfinite(rv) & (rv >= 0) & (rv <= 50) & np.asarray(clock.dayofweek != 6)
    ma = confirmed_sma(base, frame.index, 60, (60,))
    h1 = ma.sma_60.to_numpy()
    final = (side != 0) & bb & bundle & (distance <= 3) & side_gate(c, side, h1)
    state = {}
    exits = reference_long_exits(frame.high, frame.low, frame.close, frame.atr,
        ready=frame.ready, gap=gap, raw_side=side, signal_side=np.where(final, side, 0), tick=tick, state=state)
    result.update(frame=frame, gap=gap, side=side, v9=final, v9_long=final & (side == 1),
        ref_long_exit=exits, box=state, h1=ma, ready=frame.ready.to_numpy(bool),
        can_run=~gap & np.isfinite(atr) & (atr > 0),
        long_alive=frame.ready.to_numpy(bool) & (atr > 0) & (c > frame.ropeHigh.to_numpy()) & (side != -1))
    return result


def structure_parents(close, known, legacy, highs, lows, confirmed):
    """Expose Pine's long structure parent state without forward filling resets.

    Uses only current close/known status, causal legacy confirmation provenance,
    and current raw structural confirmation. A consumed parent remains visible
    until a new parent or unknown/gap, exactly like Pine's persistent variables.
    """
    high, low = np.full(len(close), np.nan), np.full(len(close), np.nan)
    hi = lo = np.nan
    pending = False
    for i in range(len(close)):
        if not known[i]:
            hi = lo = np.nan
            pending = False
        else:
            if legacy[i] and np.isfinite(highs[i]) and np.isfinite(lows[i]) and lows[i] <= highs[i]:
                hi, lo, pending = highs[i], lows[i], True
            if pending and close[i] < lo:
                hi = lo = np.nan
                pending = False
            elif confirmed[i]:
                pending = False
        high[i], low[i] = hi, lo
    return high, low


def joint_permission(close, h1):
    """Long joint direction is explicit, independent of a new raw V9 signal."""
    c, ma = np.asarray(close, float), np.asarray(h1, float)
    known = np.isfinite(c) & np.isfinite(ma) & (c > 0) & (ma > 0)
    return known, known & (c > ma)


def serial(events, gate, evaluate, n):
    """Consume first candidate once; each arm has independent trade occupancy."""
    rows, statuses, flat, holder = [], [], -1, None
    for e in events:
        i = int(e['signal_i'])
        common = {**e, 'arm': gate}
        if gate == 'joint_h1_recheck' and not e['h1_pass']:
            statuses.append({**common, 'status': 'rejected_direction' if e['h1_known'] else 'rejected_unknown', 'blocking_trade': None})
            continue
        if i < flat:
            statuses.append({**common, 'status': 'skipped_in_position', 'blocking_trade': holder})
            continue
        status, result = evaluate(i)
        statuses.append({**common, 'status': status, 'blocking_trade': None})
        if result is not None:
            rows.append({**common, **{k: result.get(k, e.get(k)) for k in source.TRADE_KEEP}, 'status': status})
            holder = e['trade_key']
            flat = n+1 if status == 'censored_boundary' else int(result['exit_i'])
    return rows, statuses


def controls(prepared, trades, ready, h1_pass, evaluate):
    """Same symbol/month/time-fold/causal vol bucket, same exit/cost and gate.

    One deterministic random draw per event and arm; no retry after censoring.
    Control pools do not require a SPIKE event, which would defeat the null.
    """
    frame = prepared.frame
    clock = frame.index + pd.Timedelta(minutes=MINUTES)
    month = np.asarray(clock.strftime('%Y-%m'))
    folds = np.where(clock < SPLIT, 'earlier', 'later')
    bins = np.searchsorted(source.VOL_BINS, prepared.atr / prepared.close, side='left')
    valid = ready & ~prepared.gap & np.isfinite(prepared.atr) & (prepared.atr > 0) & (clock >= START) & (clock < END)
    pools, rows = {}, []
    for row in trades:
        i, arm = int(row['signal_i']), row['arm']
        key = (arm, month[i], folds[i], int(bins[i]))
        if key not in pools:
            mask = valid & (month == key[1]) & (folds == key[2]) & (bins == key[3])
            if arm == 'joint_h1_recheck':
                mask &= h1_pass
            pools[key] = np.flatnonzero(mask)
        options = pools[key][pools[key] != i]
        j, result, status = None, None, 'empty_stratum'
        if len(options):
            token = int(hashlib.sha256(f'{SEED}|{row["trade_key"]}'.encode()).hexdigest(), 16)
            j = int(options[token % len(options)])
            status, result = evaluate(j)
        matched = result is not None and not result['censored'] and not row['censored']
        rows.append({'trade_key': row['trade_key'], 'arm': arm, 'matched': bool(matched),
            'reason': 'target_censored' if row['censored'] else 'matched' if matched else status,
            'control_signal_i': j, 'control_signal_time': None if j is None else clock[j],
            'control_exit_time': None if result is None else result['exit_time'],
            'control_net_r': result['net_r'] if matched else np.nan,
            'control_net_return': result['net_return'] if matched else np.nan})
    return pd.DataFrame(rows, columns=['trade_key','arm','matched','reason','control_signal_i','control_signal_time',
        'control_exit_time','control_net_r','control_net_return'])


def run_stream(base, symbol, meta):
    """Reconstruct the complete candidate ledger, then replay the two policies."""
    from yoyo.evaluation.spike_v126_engine import line_events, pair_events
    tick, asset = float(meta['tick']), meta['asset']
    since = START-pd.Timedelta(minutes=1500*MINUTES)
    bars, partial = complete_bars(base.loc[base.index >= since], MINUTES)
    higher, hpartial = complete_bars(base, 60)
    if len(bars) < 2:
        return {name: pd.DataFrame() for name in TABLES}, {'bars': len(bars), 'candidates': 0}
    facts = pine_facts(bars, base, asset, tick)
    f = facts['frame']
    local = line_events(f.open, f.high, f.low, f.close, f.atr, can_run=facts['can_run'], gap=facts['gap'],
        tick=tick, confirmed_long=facts['v9_long'], parent_high=facts['parent_high'], parent_low=facts['parent_low'],
        raw_side=facts['side'], long_alive=facts['long_alive'], ref_long_exit=facts['ref_long_exit'])
    hf = features(higher)
    hgap = _data_gap(hf, 60).to_numpy(bool)
    ht = line_events(hf.open, hf.high, hf.low, hf.close, hf.atr,
        can_run=~hgap & np.isfinite(hf.atr) & hf.atr.gt(0), gap=hgap, tick=tick, htf=True)
    clock = f.index.asi8 // 60_000_000_000
    hclock = hf.index.asi8 // 60_000_000_000
    mapped = []
    for e in ht.winner_events:
        visible_time = int(hclock[int(e.get('i', e.get('break_i')))]) + 60
        j = int(np.searchsorted(clock, visible_time))
        if j >= len(clock) or clock[j]-visible_time >= MINUTES:
            continue
        mapped.append({**e, 'visible_i': j,
            'break_bar_open': hf.index[int(e['break_i'])],
            'break_close_time': hf.index[int(e['break_i'])]+pd.Timedelta(minutes=60),
            'first_visible_chart_open':f.index[j],
            **{k+'_t': int(hclock[int(e[k])]) for k in ('ax','bx','cx')}})
    joints = pair_events(f.close.to_numpy(), bar_times=clock, chart_breaks=local.events,
        htf_breaks=mapped, box_id=facts['box']['box_entry'], confirmed_long=facts['v9_long'], gap=facts['gap'])
    known, passed = joint_permission(f.close, facts['h1'].sma_60)
    events = []
    for e in joints:
        i = int(e['joint_i'])
        ts = f.index[i] + pd.Timedelta(minutes=MINUTES)
        if not (START <= ts < END):
            continue
        parent = int(e['box_entry_i'])
        is_htf = e['source'] == 'htf'
        break_open = hf.index[int(e['break_i'])] if is_htf else f.index[int(e['break_i'])]
        events.append({**e, 'signal_i': i, 'signal_bar_open': f.index[i], 'signal_close': ts,
            'pair_source':e['source'], 'line_source':'raw' if e['source_id'] == 0 else 'soft',
            'break_bar_open':break_open, 'break_close_time':break_open+pd.Timedelta(minutes=60 if is_htf else MINUTES),
            'first_visible_chart_open':f.index[int(e['visible_i'])] if is_htf else break_open,
            'trade_key': f'binance_um:{symbol}:15m:v126:{ts.isoformat()}', 'symbol':symbol, 'asset':asset,
            'timeframe':'15m', 'v9_reference_time':f.index[parent]+pd.Timedelta(minutes=MINUTES),
            'v9_reference_price':float(f.close.iloc[parent]), 'bars_after_v9':i-parent,
            'joint_close':float(f.close.iloc[i]), 'h1_known':bool(known[i]), 'h1_pass':bool(passed[i]),
            'h1_sma60':float(facts['h1'].sma_60.iloc[i]), 'h1_source_close':facts['h1'].htf_close_time.iloc[i],
            'next_open_time':f.index[i+1] if i+1 < len(f) else pd.NaT,
            'next_open_price':float(f.open.iloc[i+1]) if i+1 < len(f) else np.nan,
            'first_discovered_at':None, 'discovery_basis':'historical_replay_not_live'})
    events.sort(key=lambda e:e['signal_i'])
    if len({e['box_entry_i'] for e in events}) != len(events):
        raise ValueError('a V9 box generated multiple joint candidates')
    key = f'binance_um:{symbol}:15m'
    prepared = source.prepared_arm(f,facts['gap'],facts['side'],key,{'symbol':symbol,'timeframe':'15m'},MINUTES,tick)
    cache = {}
    def evaluate(i):
        if i not in cache:
            cache[i] = execution.attempt(prepared,i)
        return cache[i]
    trades, statuses = [], []
    for arm in ARMS:
        t,s = serial(events,arm,evaluate,len(f))
        trades.extend(t); statuses.extend(s)
    trade_cols = list(dict.fromkeys([*source.TRADE_KEEP,'status','arm','trade_key','symbol','asset','timeframe',
        'source','order','box_entry_i','signal_close','h1_pass','h1_known','h1_sma60','bars_after_v9']))
    table = pd.DataFrame(trades,columns=trade_cols)
    if len(table):
        closed = table.loc[~table.censored]
        np.testing.assert_allclose(closed.gross_return-closed.net_return,.002,atol=1e-12)
        np.testing.assert_allclose(closed.net_r,closed.net_return/closed.initial_risk_frac,atol=1e-10)
    references = []
    for parent in sorted(set(int(x) for x in facts['box']['box_entry'] if x >= 0)):
        references.append({'symbol':symbol,'box_entry_i':parent,'v9_reference_close':f.index[parent]+pd.Timedelta(minutes=MINUTES),
            'v9_reference_price':f.close.iloc[parent],'h1_sma60_at_v9':facts['h1'].sma_60.iloc[parent]})
    breaks = [{**e,'engine_source':'chart'} for e in local.events] + [{**e,'engine_source':'htf'} for e in mapped]
    parts = {'decisions':pd.DataFrame(events),'trades':table,'statuses':pd.DataFrame(statuses),
        'controls':controls(prepared,trades,facts['ready'],passed,evaluate),
        'reference_boxes':pd.DataFrame(references),'breaks':pd.DataFrame(breaks)}
    summary = {'bars':len(f),'higher_bars':len(hf),'partial_chart_buckets_excluded':partial,
        'partial_h1_buckets_excluded':hpartial,'chart_gaps':int(facts['gap'].sum()),'candidates':len(events),
        'recheck_rejects':sum(not e['h1_pass'] for e in events), 'v9_long_confirmations':int(facts['v9_long'].sum()),
        'chart_breaks':len(local.events),'htf_breaks_visible':len(mapped),
        'native_pine_parity':False,'pivot_tie_policy':'strict_unverified_native',
        'chart_pivot_tie_counts':local.trace['pivot_tie_counts'],
        'htf_pivot_tie_counts':ht.trace['pivot_tie_counts'],
        'chart_store_codes':local.store_codes,'htf_store_codes':ht.store_codes}
    return parts,summary


def validate_receipt(directory, run_hash, input_sha):
    r = json.loads((directory/'receipt.json').read_text())
    if r['run_identity'] != run_hash or r['input_sha256'] != input_sha:
        raise ValueError('receipt identity mismatch')
    if set(r['files']) != {f'{name}.csv.gz' for name in TABLES}:
        raise ValueError('receipt inventory mismatch')
    for name,sha in r['files'].items():
        if source.digest(directory/name) != sha:
            raise ValueError(f'artifact changed: {directory/name}')
    return r


def worker(args):
    symbol,path,meta,out,run_hash,input_sha = args
    directory = Path(out)/'streams'/symbol
    if (directory/'receipt.json').exists():
        return validate_receipt(directory,run_hash,input_sha)
    if directory.exists():
        raise ValueError(f'incomplete output: {directory}')
    before = time.perf_counter()
    if source.digest(Path(path)) != input_sha:
        raise ValueError('input changed since registration')
    raw = pd.read_csv(path,usecols=['ts','open','high','low','close','volume'])
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts,unit='ms',utc=True))
    if not index.is_unique or not index.is_monotonic_increasing or (index.asi8 % pd.Timedelta(minutes=5).value != 0).any():
        raise ValueError('archive has unordered/duplicate/unaligned clock')
    base = raw[['open','high','low','close','volume']].set_axis(index)
    # Frozen archive window, not a prohibition on later historical research.
    since = START-pd.Timedelta(minutes=1500*60)
    base = base.loc[(base.index >= since) & (base.index+pd.Timedelta(minutes=5) <= END)]
    parts,summary = run_stream(base,symbol,meta)
    directory.mkdir(parents=True)
    for name,frame in parts.items():
        frame.to_csv(directory/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    receipt = {'symbol':symbol,'run_identity':run_hash,'input_sha256':input_sha,'summary':summary,
        'files':{f'{name}.csv.gz':source.digest(directory/f'{name}.csv.gz') for name in TABLES},
        'wall_seconds':time.perf_counter()-before,'status':'complete'}
    dump(directory/'receipt.json',receipt)
    return receipt


def run(out,workers=4,symbols=None):
    config = json.loads(CONFIG.read_text())
    code = _local_transitive_python((Path(__file__),Path('yoyo/evaluation/spike_v126_engine.py')))
    declared = (*code,PINE,CONFIG,PLAN,Path('tests/evaluation/test_spike_v126_engine.py'),
        Path('tests/evaluation/test_spike_v126_htf_recheck.py'))
    if not _committed(declared):
        raise ValueError('commit all replay sources/plan/tests before market replay')
    spec = source.ExecutionSpec()
    if (spec.round_trip_cost,spec.arm_r,spec.trail_atr) != (.002,2.,4.):
        raise ValueError('execution contract changed')
    if config['arms'] != list(ARMS) or config['start'] != START.isoformat() or config['end'] != END.isoformat():
        raise ValueError('frozen specification mismatch')
    allfiles = source.series_files()
    parent = json.loads(Path(config['parent_input_identity']).read_text())
    if len(allfiles) != config['expected_symbols'] or set(allfiles) != set(parent['inputs']):
        raise ValueError('input universe changed from frozen archive')
    if source.digest(source.EXCHANGE_INFO) != parent['exchange_info']['sha256']:
        raise ValueError('archived exchange metadata changed')
    chosen = sorted(allfiles if symbols is None else symbols)
    if not chosen or any(s not in allfiles for s in chosen):
        raise ValueError('invalid symbols')
    files = {s:allfiles[s] for s in chosen}
    inputs = {s:source.digest(p) for s,p in files.items()}
    if any(inputs[s] != parent['inputs'][s] for s in chosen):
        raise ValueError('archive differs from parent')
    identity = {'config':config,'inputs':inputs,'input_paths':{s:str(p) for s,p in files.items()},
        'source':{str(p):source.digest(p) for p in declared},'symbols':chosen,
        'metadata_sha256':source.digest(source.EXCHANGE_INFO),'subset':symbols is not None,
        'python':subprocess.check_output(['.venv/bin/python','--version'],text=True).strip(),
        'numpy':np.__version__,'pandas':pd.__version__}
    run_hash = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    out = Path(out)
    if (out/'identity.json').exists():
        if json.loads((out/'identity.json').read_text()) != identity:
            raise ValueError('resume identity changed')
    else:
        out.mkdir(parents=True,exist_ok=True)
        dump(out/'identity.json',identity)
        dump(out/'started.json',{'run_identity':run_hash,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'started_at':pd.Timestamp.now(tz='UTC')})
    meta = source.symbol_meta()
    tasks = [(s,str(files[s]),meta[s],str(out),run_hash,inputs[s]) for s in chosen]
    results,errors = [],[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(worker,t):t[0] for t in tasks}
        for future in as_completed(pending):
            symbol = pending[future]
            try:
                result = future.result(); results.append(result)
                print(json.dumps({'completed':len(results),'total':len(tasks),'symbol':symbol,
                    'candidates':result['summary']['candidates'],'seconds':round(result['wall_seconds'],2)}),flush=True)
            except Exception as exc:
                errors.append({'symbol':symbol,'error':repr(exc)})
                print(json.dumps(errors[-1]),flush=True)
    dump(out/'manifest.json',{'complete':not errors and len(results)==len(tasks),'run_identity':run_hash,
        'symbols':chosen,'errors':errors,'receipts':{r['symbol']:source.digest(out/'streams'/r['symbol']/'receipt.json') for r in results},
        'candidate_count':sum(r['summary']['candidates'] for r in results),
        'native_pine_parity':False,'training_eligible':False,'production_eligible':False})
    if errors:
        raise RuntimeError(f'{len(errors)} failed streams')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--symbols',nargs='+')
    args=parser.parse_args()
    run(args.output,args.workers,args.symbols)
