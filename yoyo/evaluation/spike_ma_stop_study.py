"""Owner-authorized 2026-09-22 fixed MA-stop study on the prior 29-symbol pool.

Features: chart SMA120 uses the last 120 closed chart closes; H1 SMA60 uses
60 complete H1 closes visible at chart OPEN (Pine [1]/lookahead_on); ATR14
and the existing five-bar stop end at signal close. Future OHLC only scores
outcomes. Each policy independently replays the original serial state machine.
This is V9/H1-direction risk-box research, not new V12 bk geometry parity.
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

from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_ma_stop import ARMS, transform_initial
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate
from yoyo.evaluation.spike_v9_htf_sma_study import build_prepared
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-ma-stop-20260922-v1')
PRIOR = Path('experiments/active/exp-spike-v9-htf-sma-20260920-v1/run_v1')
SEED, REPS = 92226, 2000


def features(base, p):
    """Causal 120 chart closes and 60 completed hourly closes; no outcome reads."""
    out = confirmed_sma(base, p.frame.index, 60, (60,))
    out['sma120'] = p.frame.close.rolling(120, min_periods=120).mean()
    # Do not bridge a missing chart interval in the local SMA window.
    gap_count = pd.Series(p.gap, index=p.frame.index).rolling(119, min_periods=119).sum()
    out.loc[gap_count.gt(0), 'sma120'] = np.nan
    return out


def transformer(p, feature, arm):
    """Freeze MA and ATR values at the original signal index, before actual fill."""
    sma = feature.sma120.to_numpy(float)
    higher = feature.sma_60.to_numpy(float)
    def apply(row, i, side):
        assert int(row['side']) == side
        return transform_initial(row, arm=arm, sma120=sma[i], htf_sma60=higher[i],
                                 atr=p.atr[i], tick=p.spec.tick,
                                 buffer_atr=p.spec.stop_buffer_atr)
    return apply


def initial(p, i, side):
    if i + 1 >= len(p.frame) or p.gap[i + 1]:
        return None
    return source._initial_position_fast(p.frame.index, p.open, p.high, p.low,
                                        p.close, p.atr, p.gap, i, side, p.spec)


def score(p, i, side, apply):
    row = initial(p, i, side)
    if row is None:
        return None
    row = apply(row, i, side)
    if row is None:
        return None
    return engine.replay_fixed_entry(p.context, pd.Series(row), arm='v8', enable_be=False, prepared=p)


def annotate(t, p, feature, arm):
    t = t.copy()
    t['arm'] = arm
    t['event_key'] = [f'{p.context.key}|{pd.Timestamp(stamp).isoformat()}|{int(side)}'
                      for stamp, side in zip(t.signal_bar_open, t.side)]
    t['timeframe'] = '15m'
    bases, local, higher = [], [], []
    for row in t.itertuples(index=False):
        i = int(row.signal_i)
        original = initial(p, i, int(row.side))
        assert original is not None
        bases.append(original['initial_risk_frac'])
        local.append(feature.sma120.iloc[i]); higher.append(feature.sma_60.iloc[i])
    t['baseline_risk_frac'] = bases
    t['net_original_r'] = t.net_return / t.baseline_risk_frac
    t['sma120_at_signal'], t['h1_sma60_at_signal'] = local, higher
    t['position_multiplier'] = t.baseline_risk_frac / t.initial_risk_frac
    if len(t):
        closed = t.loc[~t.censored]
        np.testing.assert_allclose(closed.gross_return-closed.net_return, .002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return/closed.initial_risk_frac, atol=1e-10)
        assert (t.position_multiplier <= 1 + 1e-10).all()
    return t


def controls(p, feature, trades, apply_by_arm):
    """One shared deterministic draw per event, matching side/month/fold/vol bucket.

    Ready, current ATR/close, completed MA and direction are all causal.
    Unknown or censored outcomes never trigger another draw. All policies are
    scored at the same selected bar with their own initial risk denominator.
    """
    f = p.frame
    clock = f.index + pd.Timedelta(minutes=15)
    months = np.asarray(clock.strftime('%Y-%m'))
    folds = np.where(clock < source.SPLIT, 'earlier', 'later')
    bins = np.searchsorted(source.VOL_BINS, p.atr/p.close, side='left')
    finite = np.isfinite(feature[['sma120','sma_60']]).all(axis=1).to_numpy()
    ready = (f.ready.fillna(False).to_numpy(bool) & finite & source.in_window(f.index,15)
             & np.isfinite(p.atr) & (p.atr>0) & (clock.dayofweek!=6) & ~p.gap)
    pools, draws, cache, rows = {}, {}, {}, []
    for t in trades.itertuples(index=False):
        i, side = int(t.signal_i), int(t.side)
        strata = (months[i], folds[i], int(bins[i]), side)
        if strata not in pools:
            same = ready & (months==strata[0]) & (folds==strata[1]) & (bins==strata[2])
            same &= side_gate(p.close, np.full(len(f), side), feature.sma_60)
            pools[strata] = np.flatnonzero(same)
        if t.event_key not in draws:
            options = pools[strata][pools[strata]!=i]
            token = int(hashlib.sha256(f'{SEED}|{t.event_key}'.encode()).hexdigest(),16)
            draws[t.event_key] = int(options[token % len(options)]) if len(options) else None
        j = draws[t.event_key]
        result, original = None, None
        if j is not None:
            key = (j,side,t.arm)
            if key not in cache:
                cache[key] = score(p,j,side,apply_by_arm[t.arm])
            result = cache[key]; original = initial(p,j,side)
        matched = result is not None and not result['censored'] and not t.censored
        reason = 'matched' if matched else 'target_censored' if t.censored else 'no_pool' if j is None else 'invalid_or_censored'
        rows.append({'event_key':t.event_key,'arm':t.arm,'matched':matched,'reason':reason,
                     'control_signal_i':j,'control_signal_time':None if j is None else f.index[j],
                     'control_exit_time':None if result is None else result['exit_time'],
                     'control_net_r':result['net_r'] if matched else np.nan,
                     'control_net_return':result['net_return'] if matched else np.nan,
                     'control_net_original_r':result['net_return']/original['initial_risk_frac'] if matched else np.nan})
    return pd.DataFrame(rows, columns=['event_key','arm','matched','reason','control_signal_i','control_signal_time',
         'control_exit_time','control_net_r','control_net_return','control_net_original_r'])


def run_one(args):
    symbol, info, output, identity_hash = args
    started = time.monotonic(); folder=Path(output)/'streams'/symbol
    if (folder/'completion.json').exists():
        receipt=json.loads((folder/'completion.json').read_text())
        assert receipt['identity_hash']==identity_hash
        for name,digest in receipt['files'].items():
            assert source.digest(folder/name)==digest
        return receipt
    path=Path(info['path']); assert source.digest(path)==info['sha256']
    base=inc.guarded_5m(path,source.START-pd.Timedelta(days=source.WARMUP_BARS))
    facts=source.v9_facts(v11.bars_for(base,15),15,info['meta']['asset'],info['meta']['tick'])
    p=build_prepared(facts,symbol,info['meta'],'15m',15)
    feature=features(base,p)
    p=replace(p,allowed=p.allowed & side_gate(p.close,p.raw_side,feature.sma_60))
    apply={arm:transformer(p,feature,arm) for arm in ARMS}
    parts, fillparts, rejected = [], [], []
    for arm in ARMS:
        bad=[]
        def transform(row,i,side):
            changed=apply[arm](row,i,side)
            if changed is None:
                bad.append({'arm':arm,'symbol':symbol,'signal_i':i,'side':side,'reason':'invalid_ma_stop'})
            return changed
        t,fills,_=engine.replay_serial(p.context,arm='v8',enable_be=False,prepared=p,
                                      initial_transform=transform if arm!='baseline' else None)
        t=annotate(t,p,feature,arm); parts.append(t)
        fills['stop_arm']=arm; fillparts.append(fills); rejected.extend(bad)
    trades=pd.concat(parts,ignore_index=True)
    old=pd.read_csv(PRIOR/'streams'/symbol/'trades.csv.gz')
    old=old.loc[old.timeframe.eq('15m') & old.scope.eq('actual') & old.arm.eq('sma60')]
    new=trades.loc[trades.arm.eq('baseline')]
    cols=engine.KEY+['censored']
    pd.testing.assert_frame_equal(old[cols].reset_index(drop=True),new[cols].reset_index(drop=True),
                                   check_dtype=False,rtol=1e-9,atol=1e-9)
    fixed_rows=[]
    for t in new.itertuples(index=False):
        for arm in ARMS:
            result=score(p,int(t.signal_i),int(t.side),apply[arm])
            item={'event_key':t.event_key,'arm':arm,'symbol':symbol,'side':t.side,
                  'signal_i':t.signal_i,'signal_bar_open':t.signal_bar_open,
                  'baseline_net_r':t.net_r,'baseline_net_return':t.net_return,
                  'baseline_censored':t.censored,'baseline_risk_frac':t.initial_risk_frac,
                  'status':'invalid_ma_stop' if result is None else 'censored' if result['censored'] else 'closed'}
            if result is not None:
                item.update(result)
                item['net_original_r']=result['net_return']/t.initial_risk_frac
                item['position_multiplier']=t.initial_risk_frac/result['initial_risk_frac']
                if arm=='baseline':
                    for key in ('exit_i','exit_reason','censored'):
                        assert result[key]==getattr(t,key)
                    np.testing.assert_allclose(result['net_r'],t.net_r,atol=1e-10)
            fixed_rows.append(item)
    control=controls(p,feature,trades,apply)
    folder.mkdir(parents=True,exist_ok=True)
    tables={'trades':trades,'fills':pd.concat(fillparts,ignore_index=True),'controls':control,
            'fixed':pd.DataFrame(fixed_rows) if fixed_rows else pd.DataFrame(columns=['event_key','arm','symbol','side','signal_i','signal_bar_open','baseline_net_r','baseline_net_return','baseline_censored','baseline_risk_frac','status']),'rejected':pd.DataFrame(rejected,columns=['arm','symbol','signal_i','side','reason'])}
    for name,t in tables.items():
        t.to_csv(folder/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    assert source.digest(path)==info['sha256']
    receipt={'symbol':symbol,'identity_hash':identity_hash,'input_sha256':info['sha256'],
             'baseline_legacy_parity':True,'baseline_fixed_parity':True,'baseline_trades':len(new),
             'chart_bars':len(p.frame),'candidates':int(p.allowed.sum()),'rejected':len(rejected),
             'first_bar':str(p.frame.index.min()),'last_bar':str(p.frame.index.max()),
             'files':{f'{name}.csv.gz':source.digest(folder/f'{name}.csv.gz') for name in tables},
             'seconds':round(time.monotonic()-started,2)}
    (folder/'completion.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def run(output,workers=3,symbols=None):
    spec=source.ExecutionSpec()
    assert (spec.stop_bars,spec.stop_buffer_atr,spec.risk_floor_atr,spec.arm_r,spec.trail_atr,spec.round_trip_cost)==(5,.2,2.,2.,4.,.002)
    old=json.loads((PRIOR/'identity.json').read_text()); done=json.loads((PRIOR/'completion.json').read_text())
    old_payload=dict(old); old_hash=old_payload.pop('identity_hash')
    assert hashlib.sha256(json.dumps(old_payload,sort_keys=True).encode()).hexdigest()==old_hash==done['identity_hash']
    keys=sorted(symbols or old['inputs']); assert set(keys)<=set(old['inputs'])
    parents={}
    for symbol in keys:
        rpath=PRIOR/'streams'/symbol/'completion.json'; receipt=json.loads(rpath.read_text())
        assert receipt['identity_hash']==old_hash and receipt['source_sha256']==old['inputs'][symbol]['sha256']
        assert source.digest(rpath.parent/'trades.csv.gz')==receipt['files']['trades.csv.gz']
        parents[symbol]=source.digest(rpath)
    code=_local_transitive_python((Path(__file__),Path('yoyo/evaluation/spike_ma_stop.py')))
    declared=(*code,Path('yoyo/evaluation/spike_ma_stop_report.py'),EXP/'PROJECT_PLAN.md',Path('tests/evaluation/test_spike_ma_stop.py'),Path('tests/evaluation/test_spike_ma_stop_study.py'))
    assert _committed(declared),'Commit executable closure, tests and plan before backtest'
    assert source.digest(source.EXCHANGE_INFO)==old['metadata_sha256']
    identity={'schema':'spike-ma-stop-v1','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'arms':list(ARMS),'timeframe':'15m','start':str(source.START),'split':str(source.SPLIT),'end':str(inc.DATA_END),
              'inputs':{s:old['inputs'][s] for s in keys},'prior_identity_sha256':source.digest(PRIOR/'identity.json'),
              'prior_receipts':parents,'declared':{str(p):source.digest(p) for p in declared},
              'seed':SEED,'reps':REPS,'training_eligible':False,'production_eligible':False}
    identity_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    ip=output/'identity.json'
    if ip.exists(): assert json.loads(ip.read_text())==identity,'Use a new output after code changes'
    else: ip.write_text(json.dumps(identity,indent=2)+'\n')
    receipts=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(run_one,(s,identity['inputs'][s],str(output),identity_hash)) for s in keys]
        for future in as_completed(jobs):
            receipt=future.result();receipts.append(receipt)
            print(json.dumps({'completed':len(receipts),'total':len(keys),'symbol':receipt['symbol'],
                              'baseline':receipt['baseline_trades'],'seconds':receipt['seconds']}),flush=True)
    for name in ('trades','fixed','controls','rejected'):
        data=pd.concat([pd.read_csv(output/'streams'/s/f'{name}.csv.gz') for s in keys],ignore_index=True)
        data.to_csv(output/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    final={'identity_hash':identity_hash,'symbols':keys,'complete':True,'receipts':{s:source.digest(output/'streams'/s/'completion.json') for s in keys},
           'files':{f'{n}.csv.gz':source.digest(output/f'{n}.csv.gz') for n in ('trades','fixed','controls','rejected')}}
    (output/'completion.json').write_text(json.dumps(final,indent=2)+'\n')


if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--output',type=Path,default=EXP/'run_v1')
    cli.add_argument('--workers',type=int,default=3);cli.add_argument('--symbols',nargs='+')
    args=cli.parse_args();run(args.output,args.workers,args.symbols)
