"""Frozen V9 net-2R cost protection versus original V9, with matched controls.

Signal features use the current and preceding bars only. The intervention uses
the surviving bar's high/low at its close, never a full-history MFE. Random
entries match symbol/side/calendar month/current ATR-price bucket/window; only
their outcome calculation looks forward. ETH inputs end before May 2026; the
optional original universe requires a committed configuration-specific approval
before any cache or outcome is loaded. No parameter selection or live changes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v1_v8_be05 as parent
from yoyo.evaluation import spike_v9_cost_be2_engine as engine
from yoyo.evaluation.spike_fanshen_study import contexts
from yoyo.evaluation.spike_v9 import entry_decision
from yoyo.evaluation.spike_v9_full_replay import prepare_v9
from yoyo.evaluation.spike_v9_eth_lowtf import prepared_control
from yoyo.evaluation.spike_v9_full_report import block_statistics

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-v9-cost-be2-20260915-v1'
CONFIG = EXP / 'config.json'
PARITY = [*parent.KEY, 'censored']
ARMS = ('v9_original', 'v9_cost_be2')
DEPENDENCIES = [Path(__file__), CONFIG, EXP/'PROJECT_PLAN.md',
    ROOT/'yoyo/evaluation/spike_v9_cost_be2_engine.py',
    ROOT/'yoyo/evaluation/pine/spike_burst_v9_cost_be2.pine',
    ROOT/'tests/evaluation/test_spike_v9_cost_be2_engine.py',
    ROOT/'tests/evaluation/test_spike_v9_cost_be2_study.py',
    ROOT/'yoyo/evaluation/spike_v9_cost_be2_report.py',
    Path(parent.__file__), ROOT/'yoyo/evaluation/spike_exit_policy_study.py',
    ROOT/'yoyo/evaluation/spike_v9.py', ROOT/'yoyo/evaluation/spike_v9_full_replay.py',
    ROOT/'yoyo/evaluation/spike_v9_eth_lowtf.py', ROOT/'yoyo/evaluation/spike_v7_fast.py',
    ROOT/'yoyo/evaluation/spike_fanshen_study.py', ROOT/'yoyo/data/spike_fanshen_prefix.py',
    ROOT/'yoyo/evaluation/spike_burst_replay.py', ROOT/'yoyo/evaluation/spike_v8_lowtf_study.py',
    ROOT/'yoyo/evaluation/spike_v8_replay.py', ROOT/'yoyo/evaluation/spike_v6_wvf_study.py',
    ROOT/'yoyo/evaluation/spike_v9_full_report.py']


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1048576), b''): h.update(chunk)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str)+'\n')


def frozen_identity():
    """Refuse uncommitted builders before any market-data operation."""
    result = {}
    for path in DEPENDENCIES:
        rel = str(path.relative_to(ROOT))
        committed = subprocess.check_output(['git','show',f'HEAD:{rel}'], cwd=ROOT)
        if committed != path.read_bytes(): raise ValueError('uncommitted dependency: '+rel)
        result[rel] = sha(path)
    return result


def assert_parity(actual, expected):
    """Compare trades by causal entry identity, including boundary states."""
    pd.testing.assert_frame_equal(actual[PARITY].sort_values(['signal_i','side']).reset_index(drop=True),
        expected[PARITY].sort_values(['signal_i','side']).reset_index(drop=True),
        check_dtype=False, rtol=1e-9, atol=1e-9)


def event_key(frame):
    return frame.stream_key.astype(str)+':'+frame.signal_i.astype(int).astype(str)+':'+frame.side.astype(int).astype(str)


def annotate(frame, prepared, arm, window):
    frame = frame.copy()
    frame['arm'], frame['window'] = arm, window
    frame['stream_key'] = prepared.context.key
    for k,v in prepared.context.identity.items(): frame[k] = v
    frame['event_key'] = event_key(frame)
    frame['cost_r'] = .002 / frame.initial_risk_frac
    frame['one_tick_r'] = prepared.spec.tick / frame.initial_risk
    frame['near_be'] = ~frame.censored & frame.net_r.abs().le(frame.one_tick_r + 1e-9)
    frame['full_initial_stop'] = ~frame.censored & frame.exit_reason.astype(str).str.startswith('initial_stop')
    return frame


def audit_fills(trades, fills):
    """Independently reconcile closed fills to recorded gross/net returns."""
    closed = trades.loc[~trades.censored].set_index('trade_id')
    if closed.empty: return {'closed_checked': 0}
    entry = fills.loc[fills.kind.eq('entry')].set_index('trade_id')
    exit_rows = fills.loc[fills.kind.eq('exit') & fills.trade_id.isin(closed.index)].copy()
    if not entry.index.is_unique: raise ValueError('multiple entry fills')
    exit_rows['gross'] = exit_rows.qty_fraction * exit_rows.side * (exit_rows.price / exit_rows.trade_id.map(entry.price)-1)
    gross = exit_rows.groupby('trade_id').gross.sum().reindex(closed.index)
    costs = fills.groupby('trade_id').cost_return.sum().reindex(closed.index)
    qty = exit_rows.groupby('trade_id').qty_fraction.sum().reindex(closed.index)
    np.testing.assert_allclose(qty, 1., atol=1e-12)
    np.testing.assert_allclose(gross, closed.gross_return, atol=1e-11)
    np.testing.assert_allclose(gross-costs, closed.net_return, atol=1e-11)
    np.testing.assert_allclose(closed.net_r, closed.net_return/closed.initial_risk_frac, atol=1e-10)
    return {'closed_checked': len(closed), 'fill_accounting_passed': True}


def initial_record(prepared, i, side):
    if i+1 >= len(prepared.frame) or prepared.gap[i+1]: return None
    row = parent.base._initial_position_fast(prepared.frame.index, prepared.open, prepared.high,
        prepared.low, prepared.close, prepared.atr, prepared.gap, int(i), int(side), prepared.spec)
    if row is None: return None
    row['initial_risk_frac'] = row['initial_risk']/row['entry_price']
    return pd.Series(row)


def controls(prepared, targets, left, right, cfg):
    """One deterministic draw; retain failed/censored draws, never resample."""
    f = prepared.frame; delta = pd.Timedelta(minutes=prepared.context.minutes)
    scheduled = f.index + delta
    month = scheduled.strftime('%Y-%m')
    bins = np.searchsorted(cfg['atr_price_bins'], prepared.atr/prepared.close, side='left')
    eligible = f.ready.fillna(False).to_numpy(bool) & np.isfinite(prepared.atr) & (prepared.atr>0) & (prepared.close>0)
    eligible &= (scheduled>=left) & (scheduled<right) & (scheduled.dayofweek!=6)
    # Preserve the frozen full-pool chronological cut in the control draw.
    split = pd.Timestamp('2025-09-10T00:00Z')
    fold = scheduled>=split
    pools, paths, rows = {}, {}, []
    for target in targets.itertuples(index=False):
        i = int(f.index.get_loc(pd.Timestamp(target.signal_bar_open))); side = int(target.side)
        group = (month[i], int(bins[i]), bool(fold[i]))
        if group not in pools:
            pools[group] = np.flatnonzero(eligible & (month==group[0]) & (bins==group[1]) & (fold==group[2]))
        choices = pools[group][pools[group]!=i]
        chosen, result, reason = None, None, 'empty_stratum'
        if len(choices):
            key = f"{cfg['seed']}|{prepared.context.key}|{target.signal_bar_open}|{side}"
            chosen = int(choices[int(hashlib.sha256(key.encode()).hexdigest(),16)%len(choices)])
            enabled = target.arm=='v9_cost_be2'; cache_key = (chosen,side,enabled)
            if cache_key not in paths:
                row = initial_record(prepared,chosen,side)
                paths[cache_key] = None if row is None else engine.replay_fixed_entry(prepared.context,row,
                    arm='v8',enable_be=enabled,prepared=prepared)
            result = paths[cache_key]
            reason = 'invalid_initial' if result is None else 'censored' if result['censored'] else 'matched'
        matched = result is not None and not result['censored'] and not target.censored
        rows.append(dict(event_key=target.event_key,arm=target.arm,window=target.window,
            stream_key=prepared.context.key,**prepared.context.identity,entry_time=target.entry_time,
            target_exit_time=target.exit_time,target_net_r=target.net_r,target_net_return=target.net_return,
            target_censored=target.censored,matched=matched,reason='target_censored' if target.censored else reason,
            control_signal_time=None if chosen is None else f.index[chosen],
            control_entry_time=None if result is None else result['entry_time'],
            control_exit_time=None if result is None else result['exit_time'],
            control_net_r=np.nan if result is None or result['censored'] else result['net_r'],
            control_net_return=np.nan if result is None or result['censored'] else result['net_return'],
            month=group[0],vol_bin=group[1]))
    return pd.DataFrame(rows, columns=['event_key','arm','window','stream_key','venue','symbol','asset','timeframe_min',
        'entry_time','target_exit_time','target_net_r','target_net_return','target_censored','matched','reason',
        'control_signal_time','control_entry_time','control_exit_time','control_net_r','control_net_return','month','vol_bin'])


def run_prepared(prepared, folder, window, left, right, cfg, oracle=None):
    """Run serial arms and exact original-entry counterfactuals, then audit."""
    folder.mkdir(parents=True,exist_ok=False)
    tables, checks = [], []
    original_parent = parent.replay_serial(prepared.context,arm='v8',enable_be=False,prepared=prepared)[0]
    original = None
    for arm in ARMS:
        t, fills, events = engine.replay_serial(prepared.context,arm='v8',enable_be=arm==ARMS[1],prepared=prepared)
        checks.append(dict(arm=arm,**audit_fills(t,fills)))
        if arm==ARMS[0]:
            assert_parity(t,original_parent)
            if oracle is not None: assert_parity(t,oracle)
            original = t.copy()
        tagged = annotate(t,prepared,arm,window); tables.append(tagged)
        tagged.to_csv(folder/f'{arm}.trades.csv.gz',index=False)
        fills.to_csv(folder/f'{arm}.fills.csv.gz',index=False)
        events.to_csv(folder/f'{arm}.events.csv.gz',index=False)
    fixed_rows = []
    for _, row in original.iterrows():
        fixed_base = engine.replay_fixed_entry(prepared.context,row,arm='v8',enable_be=False,prepared=prepared)
        assert_parity(pd.DataFrame([fixed_base]),pd.DataFrame([row]))
        fixed_rows.append(engine.replay_fixed_entry(prepared.context,row,arm='v8',enable_be=True,prepared=prepared))
    fixed = pd.DataFrame(fixed_rows,columns=engine.FIXED_COLUMNS) if not fixed_rows else pd.DataFrame(fixed_rows)
    fixed = annotate(fixed,prepared,ARMS[1],window)
    fixed.to_csv(folder/'fixed_original_entries.cost_be2.csv.gz',index=False)
    left_fields = ['event_key','stream_key','venue','symbol','asset','timeframe_min','window','entry_time','side']
    outcome = ['net_r','gross_r','exit_time','censored','mfe_r','near_be','full_initial_stop']
    pair = tables[0][left_fields+outcome].merge(fixed[['event_key']+outcome],on='event_key',suffixes=('_original','_be2'),validate='one_to_one')
    pair['joint_closed'] = ~pair.censored_original & ~pair.censored_be2
    pair['delta_r'] = pair.net_r_be2-pair.net_r_original
    pair.to_csv(folder/'paired.csv.gz',index=False)
    all_t = pd.concat(tables,ignore_index=True)
    matches = controls(prepared,all_t,left,right,cfg['controls'])
    matches.to_csv(folder/'controls.csv.gz',index=False)
    manifest = dict(status='complete',stream_key=prepared.context.key,window=window,
        start=str(left),end=str(right),bars=len(prepared.frame),candidates=int(prepared.allowed.sum()),
        baseline_parent_parity=True,archived_v9_parity=oracle is not None,
        fixed_baseline_parity=True,accounting=checks,
        files={p.name:sha(p) for p in folder.iterdir() if p.is_file()})
    save(folder/'completion.json',manifest)
    return manifest


def eth_prepared(frame,raw,mask,minutes,left,right,cfg):
    """Explicit bounds avoid the parent helper's unrelated two-year constants."""
    frame = frame.loc[frame.index+pd.Timedelta(minutes=minutes)<=right].copy()
    raw = raw.reindex(frame.index); mask = mask.reindex(frame.index).fillna(False)
    stream = dict(name=f'okx_eth_{minutes}m',minutes=minutes,venue='okx',symbol='ETH-USDT-SWAP',asset='ETH',tick=cfg['tick'])
    p = prepared_control(frame,raw,stream)
    allowed = np.zeros(len(frame),bool); scheduled = frame.index+pd.Timedelta(minutes=minutes)
    for i in np.flatnonzero(mask.to_numpy(bool) & (scheduled>=left) & (scheduled<right)):
        allowed[i] = entry_decision('ETH',frame.rv.iloc[i],scheduled[i])['v9_bundle_allowed']
    return replace(p,allowed=allowed)


def verify_approval(cfg,identity):
    """Reject before loading any universe cache, including its old outcomes."""
    path = EXP/'authorization.json'
    raw = path.read_bytes()
    rel = str(path.relative_to(ROOT))
    if raw!=subprocess.check_output(['git','show',f'HEAD:{rel}'],cwd=ROOT): raise ValueError('approval must be committed')
    approved = json.loads(raw)
    if (approved.get('approved') is not True or approved.get('config_sha256')!=sha(CONFIG)
            or approved.get('strategy_version')!=cfg['strategy_version'] or approved.get('configuration_exposure')!=1):
        raise ValueError('new configuration-specific holdout approval required')
    if approved.get('builders') != identity: raise ValueError('implementation changed after approval freeze')
    return approved


def universe_stream(args):
    key,out,cfg,identity = args
    verify_approval(cfg,identity)
    folder = Path(out)/'streams'/key
    if (folder/'completion.json').exists():
        old=json.loads((folder/'completion.json').read_text())
        for name,digest in old['files'].items():
            if sha(folder/name)!=digest: raise ValueError('completed output changed')
        return old
    original = parent.base.load_verified_stream(ROOT/cfg['universe']['raw']/'streams'/key)
    prepared,_ = prepare_v9(parent.prepare_arm(original,arm='v8'))
    oracle_dir=ROOT/cfg['universe']['oracle']/'streams'/key
    receipt=json.loads((oracle_dir/'completion.json').read_text())
    name='v9.trades.csv.gz'
    if sha(oracle_dir/name)!=receipt['files'][name]: raise ValueError('old V9 receipt mismatch')
    oracle=pd.read_csv(oracle_dir/name)
    for c in ['signal_bar_open','entry_time','exit_time']: oracle[c]=pd.to_datetime(oracle[c],utc=True)
    result=run_prepared(prepared,folder,'full',pd.Timestamp(cfg['universe']['start']),pd.Timestamp(cfg['universe']['end']),cfg,oracle)
    result.update(input_cache_sha256=original.receipt['cache_sha256'],old_v9_receipt_sha256=sha(oracle_dir/'completion.json'))
    save(folder/'completion.json',result)
    return result


def run(scope,workers=4):
    cfg=json.loads(CONFIG.read_text());identity=frozen_identity()
    out=EXP/'results'/scope
    if scope=='universe': approval=verify_approval(cfg,identity)
    else: approval=None
    out.mkdir(parents=True,exist_ok=True)
    run_id=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    started=out/'run_started.json'
    if started.exists():
        prior=json.loads(started.read_text())
        if prior['run_identity']!=run_id: raise ValueError('use new output for changed builders')
    else:
        save(started,dict(scope=scope,started_at=pd.Timestamp.now(tz='UTC'),run_identity=run_id,builders=identity,
            source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            holdout_consumed=scope=='universe',configuration_exposure=1 if scope=='universe' else 0,approval=approval))
    results=[];began=time.monotonic()
    if scope=='eth':
        ecfg=cfg['eth']; receipts=[]
        for minutes,frame,raw,mask,receipt in contexts(ecfg):
            receipts.append(dict(minutes=minutes,**receipt));save(out/'inputs.json',receipts)
            start=max(pd.Timestamp(ecfg['available_start']),frame.index[ecfg['minimum_warmup_bars']]+pd.Timedelta(minutes=minutes))
            for window,left in [('available',start),('common',max(start,pd.Timestamp(ecfg['common_start'])))]:
                right=pd.Timestamp(ecfg['end']);p=eth_prepared(frame,raw,mask,minutes,left,right,ecfg)
                folder=out/'streams'/f'eth_{minutes}m_{window}'
                if (folder/'completion.json').exists():
                    result=json.loads((folder/'completion.json').read_text())
                    for name,d in result['files'].items():
                        if sha(folder/name)!=d: raise ValueError('completed ETH output changed')
                else: result=run_prepared(p,folder,window,left,right,cfg)
                results.append(result); print('completed',folder.name,'seconds',round(time.monotonic()-began,1),flush=True)
    else:
        u=cfg['universe'];raw=ROOT/u['raw']
        if sha(raw/'manifest.json')!=u['raw_manifest_sha256']:raise ValueError('frozen universe manifest changed')
        keys=sorted(p.name for p in (raw/'streams').iterdir() if (p/'completion.json').exists())
        if len(keys)!=u['expected_streams']:raise ValueError('universe scope incomplete')
        with ProcessPoolExecutor(max_workers=workers) as pool:
            jobs=[pool.submit(universe_stream,(key,str(out),cfg,identity)) for key in keys]
            for future in as_completed(jobs):
                results.append(future.result())
                if len(results)%50==0 or len(results)==len(keys):
                    print('completed',len(results),'of',len(keys),'seconds',round(time.monotonic()-began,1),flush=True)
    save(out/'manifest.json',dict(status='complete',scope=scope,run_identity=run_id,streams=len(results),
        expected_streams=12 if scope=='eth' else cfg['universe']['expected_streams'],holdout_consumed=scope=='universe',
        configuration_exposure=1 if scope=='universe' else 0,receipts=results))
    print('COMPLETE',scope,len(results),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope',choices=['eth','universe']);parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();run(args.scope,args.workers)
