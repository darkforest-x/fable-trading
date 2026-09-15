"""Fixed ETH low-timeframe V9 admission on authenticated V8 feature caches.

Only current confirmation RV/base and scheduled close enter the new gate.
Initial risk uses the unchanged five-bar/ATR next-open parent contract. Raw
reversals remain untouched. Random controls match current ATR/close fixed bins,
side, signal month and the original fold; future bars only score selected exits.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v8_lowtf_study as low
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_be05_report import metrics
from yoyo.evaluation.spike_v7_fast import _initial_position_fast
from yoyo.evaluation.spike_v9 import entry_decision, VERSION
from yoyo.evaluation.spike_v9_full_report import block_statistics
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v9-eth-lowtf-20260915-v1')
CONFIG = EXP / 'config.json'
DEPENDENCIES = [Path(__file__), CONFIG, EXP/'PROJECT_PLAN.md', EXP/'authorization.json',
                Path('tests/evaluation/test_spike_v9_eth_lowtf.py'), Path(low.__file__), Path(fixed.__file__),
                Path('yoyo/evaluation/spike_v9.py'), Path('yoyo/evaluation/spike_v7_fast.py'),
                Path('yoyo/evaluation/spike_v6_wvf_study.py'), Path('yoyo/evaluation/spike_exit_policy_study.py'),
                Path('yoyo/evaluation/spike_be05_report.py'), Path('yoyo/evaluation/spike_v9_full_report.py')]
PARITY_COLUMNS = ['signal_i','entry_i','side','exit_i','exit_reason','entry_price','exit_price',
                  'initial_stop','initial_risk','net_return','net_r']


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''): h.update(block)
    return h.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str)+'\n')


def admission(frame, mask, minutes, asset='ETH'):
    """Evaluate only original V8 candidates, preserving their full-frame ordinals."""
    if frame.index.tz is None or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError('invalid confirmation clock')
    if not frame.index.equals(mask.index): raise ValueError('unaligned V8 mask')
    allowed = pd.Series(False, index=frame.index)
    rows = []
    for i in np.flatnonzero(mask.fillna(False).to_numpy(bool)):
        stamp = frame.index[i]
        decision = entry_decision(asset, frame.rv.iloc[i], stamp+pd.Timedelta(minutes=minutes))
        allowed.iloc[i] = decision['v9_bundle_allowed']
        rows.append(dict(signal_i=int(i), signal_bar_open=stamp, **decision))
    return allowed, pd.DataFrame(rows)


def prepared_control(frame, raw, stream):
    """Bypass unrelated two-year admission defaults; use this exact supplied fold."""
    minutes = stream['minutes']; frame.attrs['minutes'] = minutes
    gap = low._data_gap(frame, minutes).to_numpy(bool)
    side = np.where(raw.long_signal.fillna(False), 1, np.where(raw.short_signal.fillna(False), -1, 0))
    if (raw.long_signal.fillna(False) & raw.short_signal.fillna(False)).any(): raise ValueError('ambiguous raw side')
    identity = {k: stream[k] for k in ('venue','symbol','asset')}
    identity['timeframe_min'] = minutes
    context = fixed.base.StreamContext(Path('.'), stream['name'], {}, {'bars':frame,'tick':stream['tick']}, pd.DataFrame(), minutes, identity)
    arrays = [frame[k].to_numpy(float) for k in ('open','high','low','close','atr')]
    return fixed.PreparedArm(context, 'v8', 'v7_both', frame, gap, np.zeros(len(frame),bool), side,
                             *arrays, {}, low.ExecutionSpec(tick=stream['tick']))


def evaluate(prepared, i, side):
    """One fixed entry with original next-open stop/reversal/trail order, BE off."""
    if i+1 >= len(prepared.frame) or prepared.gap[i+1]: return None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                 prepared.close, prepared.atr, prepared.gap, i, side, prepared.spec)
    if row is None: return None
    return fixed.replay_fixed_entry(prepared.context, pd.Series(row), arm='v8', enable_be=False, prepared=prepared)


def parity(actual, expected):
    """The old native closed ledger is the oracle, never a newly fitted baseline."""
    a = actual[PARITY_COLUMNS].sort_values(['signal_i','side']).reset_index(drop=True)
    b = expected[PARITY_COLUMNS].sort_values(['signal_i','side']).reset_index(drop=True)
    pd.testing.assert_frame_equal(a,b,check_dtype=False,check_exact=False,rtol=1e-10,atol=1e-10)


def controls(prepared, targets, start, end, *, seed=91535, bins=(.005,.01,.02,.05,.1)):
    """One deterministic draw per event; failed/censored draws are never replaced."""
    frame = prepared.frame; delta = pd.Timedelta(minutes=prepared.context.minutes)
    vol = np.searchsorted(bins, prepared.atr/prepared.close, side='left')
    month = frame.index.strftime('%Y-%m')
    eligible = frame.ready.fillna(False).to_numpy(bool) & np.isfinite(prepared.atr) & (prepared.atr>0) & np.isfinite(prepared.close) & (prepared.close>0)
    eligible &= (frame.index+delta >= start) & (frame.index+delta < end)
    groups, cache, rows = {}, {}, []
    for target in targets.itertuples(index=False):
        i, side = int(target.signal_i), int(target.side)
        key = (month[i], int(vol[i])); event = target.event_key
        if key not in groups: groups[key] = np.flatnonzero(eligible & (month==key[0]) & (vol==key[1]))
        if event not in cache:
            choices = groups[key][groups[key] != i]
            if not len(choices): cache[event] = (None,None,'empty_stratum')
            else:
                value = int(hashlib.sha256(f'{seed}|{event}'.encode()).hexdigest(),16)
                chosen = int(choices[value % len(choices)]); result = evaluate(prepared,chosen,side)
                cache[event] = (chosen,result,'invalid_initial' if result is None else 'censored' if result['censored'] else 'matched')
        chosen,result,reason = cache[event]
        matched = result is not None and not bool(result['censored']) and not bool(target.censored)
        rows.append(dict(event_key=event,arm=target.arm,stream=target.stream,fold=target.fold,
                         entry_time=target.entry_time,signal_i=i,side=side,matched=matched,
                         reason='target_censored' if target.censored else reason,
                         target_net_r=target.net_r,target_net_return=target.net_return,
                         control_signal_i=chosen,control_signal_time=None if chosen is None else frame.index[chosen],
                         control_net_r=np.nan if result is None or result['censored'] else result['net_r'],
                         control_net_return=np.nan if result is None or result['censored'] else result['net_return'],
                         control_exit_time=None if result is None else result['exit_time'],
                         month=key[0],vol_bin=key[1]))
    return pd.DataFrame(rows)


def stats(trades, matches):
    rows=[]
    for (stream,arm), group in trades.groupby(['stream','arm']):
        for fold,part in [('full',group),*group.groupby('fold')]:
            pair = matches.loc[matches.arm.eq(arm) & matches.event_key.isin(part.event_key) & matches.matched]
            excess=pair.target_net_r-pair.control_net_r
            closed=part.loc[~part.censored]
            rows.append(dict(stream=stream,arm=arm,fold=fold,**metrics(part),matched_pairs=len(pair),
                unmatched=len(part)-len(pair),paired_target_mean_r=pair.target_net_r.mean(),control_mean_r=pair.control_net_r.mean(),
                paired_excess_r=excess.mean(),**block_statistics(excess,pd.to_datetime(pair.entry_time,utc=True).dt.strftime('%Y-%m')),
                gross_r_sum=closed.gross_r.sum(),cost_r_sum=(.002/closed.initial_risk_frac).sum()))
    return pd.DataFrame(rows)


def attribution(trades, decisions):
    """Decompose the serial change by immutable entry identity, without repricing."""
    rows=[]
    for stream,group in trades.groupby('stream'):
        for fold,part in [('full',group),*group.groupby('fold')]:
            d=decisions.loc[decisions.stream.eq(stream)]
            if fold!='full': d=d.loc[d.fold.eq(fold)]
            a=part.loc[part.arm.eq('v8') & ~part.censored].set_index('event_key')
            b=part.loc[part.arm.eq('v9') & ~part.censored].set_index('event_key')
            shared=a.index.intersection(b.index)
            if not np.allclose(a.loc[shared,'net_r'],b.loc[shared,'net_r'],rtol=1e-10,atol=1e-9):
                raise ValueError('entry-only intervention changed a shared closed exit')
            tail_a=a.index[a.net_r.ge(10)]; tail_b=b.index[b.net_r.ge(10)]
            removed=a.loc[a.index.difference(b.index)]; added=b.loc[b.index.difference(a.index)]
            rows.append(dict(stream=stream,fold=fold,candidates_v8=len(d),candidates_v9=int(d.v9_bundle_allowed.sum()),
                rejected=int((~d.v9_bundle_allowed).sum()),shared_closed=len(shared),removed_closed=len(removed),added_closed=len(added),
                removed_net_r=removed.net_r.sum(),added_net_r=added.net_r.sum(),
                original_ge10=len(tail_a),retained_original_ge10=len(tail_a.intersection(tail_b)),
                lost_original_ge10=len(tail_a.difference(tail_b)),added_ge10=len(tail_b.difference(tail_a))))
    return pd.DataFrame(rows)


def run(output):
    if not _committed(tuple(DEPENDENCIES)): raise ValueError('commit unchanged runner/tests/contract before market reads')
    cfg=json.loads(CONFIG.read_text())
    if cfg['round_trip_cost'] != .002 or cfg['strategy_version'] != VERSION: raise ValueError('strategy identity drift')
    output.mkdir(parents=True,exist_ok=False)
    dump(output/'evaluation_started.json',dict(started_at=datetime.now(timezone.utc),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        lowtf_specific_exposure=1,unchanged_v9_historical_evaluation=2,history='owner-authorized exposed history',dependencies={str(p):sha(p) for p in DEPENDENCIES}))
    try:
        for name,expected in cfg['baseline_files'].items():
            if sha(Path(cfg['baseline'])/name)!=expected: raise ValueError('baseline source changed')
        oracle=pd.read_csv(Path(cfg['baseline'])/'primary_closed_trades.csv.gz')
        signal_oracle=pd.read_csv(Path(cfg['baseline'])/'primary_signals.csv.gz')
        all_trades,all_controls,all_decisions,receipts=[],[],[],[]
        for stream in cfg['streams']:
            started=time.perf_counter();folder=output/stream['name'];folder.mkdir()
            for path,expected in [(stream['context'],stream['context_sha256']),(stream['preparation_receipt'],stream['preparation_sha256']),(stream['source_path'],stream['source_sha256'])]:
                if sha(path)!=expected: raise ValueError(f'source changed: {path}')
            with Path(stream['context']).open('rb') as handle: ctx=pickle.load(handle)
            frame,raw,mask=ctx['frame'],ctx['raw'],ctx['mask']
            if (len(frame)!=stream['bars'] or not frame.index.equals(raw.index) or not frame.index.equals(mask.index)
                    or ctx['stream']['symbol']!=stream['symbol'] or ctx['stream']['tick']!=stream['tick']
                    or ctx['stream']['minutes']!=stream['minutes']): raise ValueError('context identity mismatch')
            treatment,decisions=admission(frame,mask,stream['minutes'],stream['asset'])
            print(json.dumps({'stream':stream['name'],'bars':len(frame),'stage':'loaded_authenticated_cache'}),flush=True)
            for fold,start,end in [('development',stream['development_start'],stream['split']),('validation',stream['split'],stream['end'])]:
                start,end=pd.Timestamp(start),pd.Timestamp(end)
                window=low._fold_mask(frame.index,start,end,stream['minutes'])
                visible=frame.loc[frame.index<end];signal=raw.reindex(visible.index)
                prepared=prepared_control(visible,signal,stream)
                pairs=[]
                for arm,allowed in [('v8',mask),('v9',treatment)]:
                    _,trades=low._run(frame,raw,allowed & window,tick=stream['tick'],minutes=stream['minutes'],end=end)
                    trades['arm'],trades['stream'],trades['fold']=arm,stream['name'],fold
                    trades['strategy_version']=VERSION if arm=='v9' else 'frozen-lowtf-v8'
                    trades['event_key']=stream['name']+':'+fold+':'+trades.signal_i.astype(int).astype(str)+':'+trades.side.astype(int).astype(str)
                    trades['censored']=trades.censored.astype(bool)
                    if arm=='v8':
                        expected=oracle.loc[oracle.symbol.eq(stream['symbol']) & oracle.minutes.eq(stream['minutes']) & oracle.fold.eq(fold) & oracle.arm.eq('v8')]
                        parity(trades.loc[~trades.censored],expected)
                        expected_signals=signal_oracle.loc[signal_oracle.symbol.eq(stream['symbol']) & signal_oracle.minutes.eq(stream['minutes']) & signal_oracle.fold.eq(fold) & signal_oracle.arm.eq('v8')]
                        original=low._signal_rows(frame.index,raw,mask & window,symbol=stream['symbol'],venue=stream['venue'],minutes=stream['minutes'],arm='v8')
                        original_keys=original[['signal_confirm_time','side']].copy()
                        expected_keys=expected_signals[['signal_confirm_time','side']].copy()
                        for keys in (original_keys,expected_keys):
                            keys['signal_confirm_time']=pd.to_datetime(keys.signal_confirm_time,utc=True)
                            keys['side']=keys.side.astype(int)
                        pd.testing.assert_frame_equal(original_keys.reset_index(drop=True),expected_keys.reset_index(drop=True),check_dtype=False)
                    for target in trades.loc[~trades.censored].itertuples(index=False):
                        replay=evaluate(prepared,int(target.signal_i),int(target.side))
                        if replay is None or replay['censored'] or replay['exit_i']!=target.exit_i or replay['exit_reason']!=target.exit_reason or not np.isclose(replay['net_r'],target.net_r,atol=1e-9,rtol=1e-10): raise ValueError('fixed control engine differs from native closed target')
                    pairs.append(trades);all_trades.append(trades)
                    trades.to_csv(folder/f'{fold}.{arm}.trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
                combined=pd.concat(pairs,ignore_index=True)
                matched=controls(prepared,combined,start,end,seed=cfg['control_seed'],bins=cfg['vol_bins']);all_controls.append(matched)
                matched.to_csv(folder/f'{fold}.controls.csv.gz',index=False,compression={'method':'gzip','mtime':0})
                selected=decisions.loc[(decisions.scheduled_open_utc>=start)&(decisions.scheduled_open_utc<end)].copy()
                selected['fold'],selected['stream']=fold,stream['name'];all_decisions.append(selected)
                selected.to_csv(folder/f'{fold}.decisions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
                print(json.dumps({'stream':stream['name'],'fold':fold,'stage':'baseline_and_control_parity_passed','events':len(combined),'elapsed_seconds':round(time.perf_counter()-started,2)}),flush=True)
            rec={'stream':stream['name'],'baseline_closed_and_signals_parity':True,'fixed_control_target_parity':True,'files':{p.name:sha(p) for p in folder.iterdir() if p.is_file()}}
            dump(folder/'receipt.json',rec);receipts.append(rec)
            del ctx,frame,raw,mask,prepared,visible,signal
        trades=pd.concat(all_trades,ignore_index=True);matched=pd.concat(all_controls,ignore_index=True);decisions=pd.concat(all_decisions,ignore_index=True)
        if trades.duplicated(['arm','event_key']).any() or len(trades)!=len(matched): raise ValueError('event/control identity mismatch')
        summary=stats(trades,matched)
        summary.to_csv(output/'summary.csv',index=False)
        attribution(trades,decisions).to_csv(output/'attribution.csv',index=False)
        for name,frame in [('trades',trades),('controls',matched),('decisions',decisions)]: frame.to_csv(output/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        dump(output/'manifest.json',{'complete':True,'streams':len(receipts),'folds':4,'configuration':cfg,'receipts':receipts,'files':{p.name:sha(p) for p in output.iterdir() if p.is_file()}})
        print(summary.to_string(index=False),flush=True)
    except Exception as exc:
        dump(output/'failure.json',{'type':type(exc).__name__,'error':str(exc)})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
