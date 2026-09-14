"""Pre-registered ETH V8 capital-policy study, never a live trading adapter.

Sources: frozen ``spike_v8_lowtf_study.v8_mask/_run`` and
``spike_v6_wvf_study_post.matched_random_controls``. Signal features use only
OHLCV through the confirmation bar. The only searched object is the capital
policy, one field at a time. Labels use later bars inside each evaluation
window; window-end positions are marked explicitly instead of deleted.

This is a finite-balance trade cashbook. It cannot certify exchange liquidation,
intrabar equity drawdown, historical margin tiers, funding, or executable fills.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pickle
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_martingale_account import simulate
from yoyo.evaluation.spike_v8_lowtf_study import v8_mask, _run, _fold_mask
from yoyo.evaluation.spike_v6_wvf_study import _data_gap
from yoyo.evaluation.spike_v6_wvf_study_post import matched_random_controls

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-eth-martingale-20260914-v1'
CONFIG = EXP / 'config.json'
SELECTION = EXP / 'selection.json'
RESULTS = EXP / 'results'
BUILDERS = [Path(__file__), ROOT/'yoyo/evaluation/spike_martingale_account.py',
            ROOT/'tests/evaluation/test_spike_martingale_account.py',
            ROOT/'tests/evaluation/test_spike_eth_martingale_study.py',
            CONFIG, EXP/'PROJECT_PLAN.md', EXP/'authorization.json']


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def clean_committed(paths):
    for p in paths:
        rel = str(Path(p).resolve().relative_to(ROOT))
        subprocess.run(['git', 'cat-file', '-e', f'HEAD:{rel}'], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', rel], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL)


def dump(path, obj):
    def safe(x):
        if isinstance(x, dict): return {str(k): safe(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [safe(v) for v in x]
        if isinstance(x, np.generic): return safe(x.item())
        if isinstance(x, float) and not math.isfinite(x): return None
        if isinstance(x, (pd.Timestamp, datetime)): return x.isoformat()
        return x
    Path(path).write_text(json.dumps(safe(obj), ensure_ascii=False, indent=2)+'\n')


def read_visible(path, end):
    """Read a hash-pinned chronological source, never parse bars past ``end``.

    The time string is inspected before numeric values. At the exclusive end
    this reader stops; future OHLCV is neither converted nor handed to features.
    """
    end_ms = int(pd.Timestamp(end).timestamp()*1000)
    rows = []
    previous = -1
    with Path(path).open() as f:
        for row in csv.DictReader(f):
            stamp = int(float(row['ts']))
            if stamp >= end_ms: break
            if stamp <= previous: raise ValueError('source time is not strictly increasing')
            previous = stamp
            rows.append([stamp]+[float(row[k]) for k in ('open','high','low','close','volume')])
    if not rows: raise ValueError('no visible source bars')
    df = pd.DataFrame(rows, columns=['ts','open','high','low','close','volume'])
    df.index = pd.to_datetime(df.pop('ts'), unit='ms', utc=True)
    if not np.isfinite(df.to_numpy()).all(): raise ValueError('nonfinite source bars')
    return df


def prepare(phase):
    config = json.loads(CONFIG.read_text())
    clean_committed(BUILDERS)
    if phase == 'holdout':
        clean_committed([SELECTION])
        authorization = json.loads((EXP/'authorization.json').read_text())
        if authorization['holdout_exposure'] != 1: raise ValueError('unexpected exposure')
        if (EXP/'holdout_consumption.json').exists():
            raise FileExistsError('holdout already opened for this configuration')
        dump(EXP/'holdout_consumption.json', dict(
            started_at=datetime.now(timezone.utc), exposure_number=1,
            owner_quote=authorization['owner_holdout_quote'], selection_sha256=sha(SELECTION),
            meaning='single frozen capital policy and its fixed-risk comparator; no search'))
    out = RESULTS / phase
    out.mkdir(parents=True, exist_ok=False)
    receipt = {}
    for stream in config['streams']:
        if phase == 'holdout' and stream['minutes'] != 3: continue
        path = ROOT/stream['path']
        if sha(path) != stream['sha256']: raise ValueError(f'changed source: {path}')
        end = config['holdout'][1] if phase == 'holdout' else config['preholdout'][1]
        if phase != 'holdout' and pd.Timestamp(end) > pd.Timestamp(HOLDOUT_START):
            raise ValueError('development end crosses holdout')
        bars = read_visible(path, end)
        print(f'{stream["name"]}: visible bars {len(bars)}, computing frozen V8', flush=True)
        raw, _, mask = v8_mask(bars, stream['minutes'])
        frame = features(bars)
        frame.attrs['minutes'] = stream['minutes']
        context = dict(frame=frame, raw=raw, mask=mask, stream=stream)
        with (out/(stream['name']+'.pkl')).open('wb') as f: pickle.dump(context, f)
        receipt[stream['name']] = dict(bars=len(bars), first=str(bars.index.min()),
            last=str(bars.index.max()), source_sha256=stream['sha256'],
            context_sha256=sha(out/(stream['name']+'.pkl')))
        if phase == 'pre':
            trades, counts = window(context, *config['development'])
            trades.to_csv(out/(stream['name']+'_development.csv'), index=False)
            receipt[stream['name']]['development'] = counts
        print(f'{stream["name"]}: prepared', flush=True)
    dump(out/'preparation.json', dict(created_at=datetime.now(timezone.utc),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT,text=True).strip(),
        config_sha256=sha(CONFIG), sources=receipt))


def context_for(name, phase='pre'):
    receipt = json.loads((RESULTS/phase/'preparation.json').read_text())
    p = RESULTS/phase/(name+'.pkl')
    if sha(p) != receipt['sources'][name]['context_sha256']: raise ValueError('context hash drift')
    with p.open('rb') as f: return pickle.load(f)


def window(ctx, start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    stream, frame = ctx['stream'], ctx['frame']
    allowed = ctx['mask'] & _fold_mask(frame.index, start, end, stream['minutes'])
    _, trades = _run(frame, ctx['raw'], allowed, tick=stream['tick'],
                     minutes=stream['minutes'], end=end)
    if trades.empty: raise ValueError('no V8 opportunities in window')
    if trades.exit_reason.eq('data_gap').any() or trades.net_return.isna().any():
        raise ValueError('unresolved gap: do not silently erase the position')
    trades['signal_confirm_time'] = pd.to_datetime(trades.signal_bar_open,utc=True)+pd.Timedelta(minutes=stream['minutes'])
    trades['entry_time'] = pd.to_datetime(trades.entry_time,utc=True)
    trades['exit_time_raw'] = pd.to_datetime(trades.exit_time,utc=True)
    expected_precision = {'bar_open_or_intrabar_window','last_complete_close'}
    if not set(trades.exit_time_precision).issubset(expected_precision):
        raise ValueError('unknown frozen exit precision')
    # The frozen engine deliberately labels all ordinary exits with one broad
    # precision. Its gap/opposite branches execute at open; ordinary stops are
    # only known within that bar and conservatively become known at its close.
    exact = trades.exit_reason.str.endswith('_gap') | trades.exit_reason.eq('opposite_v6_next_open')
    trades['exit_time'] = trades.exit_time_raw + pd.to_timedelta(np.where(exact,0,stream['minutes']),unit='m')
    trades['trade_id'] = [hashlib.sha256(f'{stream["name"]}|{r.signal_bar_open}|{r.side}'.encode()).hexdigest()[:24] for r in trades.itertuples()]
    trades = trades.sort_values('entry_time').reset_index(drop=True)
    if not trades.entry_time.ge(start).all() or not trades.exit_time.le(end).all():
        raise ValueError('window boundary violation')
    return trades, dict(candidates=int(allowed.sum()), opportunities=len(trades),
                       natural_closed=int((~trades.censored.astype(bool)).sum()),
                       boundary_marks=int(trades.censored.astype(bool).sum()))


def records(trades):
    out = trades.to_dict('records')
    for row in out:
        for key in ['entry_time','exit_time']:
            row[key] = pd.Timestamp(row[key]).isoformat()
    return out


def run_policy(trades, policy, ledger=True):
    return simulate(records(trades), initial_balance=1000, multiplier=2,
                    record_ledger=ledger, **policy)


def verified_development_csv(name, config):
    """Bind selection input to the prepared source/context, not an arbitrary CSV."""
    receipt=json.loads((RESULTS/'pre'/'preparation.json').read_text())
    if receipt['config_sha256']!=sha(CONFIG): raise ValueError('prepared config hash drift')
    ctx=context_for(name)
    rebuilt,_=window(ctx,*config['development'])
    expected=hashlib.sha256(rebuilt.to_csv(index=False).encode()).hexdigest()
    path=RESULTS/'pre'/(name+'_development.csv')
    if sha(path)!=expected: raise ValueError('development CSV differs from pinned context replay')
    return pd.read_csv(path),expected


def select():
    clean_committed(BUILDERS)
    if SELECTION.exists(): raise FileExistsError('selection already frozen')
    cfg = json.loads(CONFIG.read_text())
    rows, selected, input_hashes = [], {}, {}
    for stream in cfg['streams']:
        name = stream['name']
        trades,input_hashes[name] = verified_development_csv(name,cfg)
        policy = dict(base_risk_usdt=10,max_level=3,reset_mode='win',leverage_cap=10)
        for stage, key, values in [
            ('1_levels','max_level',cfg['max_levels']),
            ('2_reset','reset_mode',cfg['reset_modes']),
            ('3_base_risk','base_risk_usdt',cfg['base_risks']),
            ('4_capacity','leverage_cap',cfg['leverage_caps'])]:
            candidates=[]
            for value in values:
                p = dict(policy, **{key:value})
                result = run_policy(trades,p,False)['summary']
                row=dict(stream=name,stage=stage,**p,**result)
                rows.append(row); candidates.append((p,result))
            policy, result = max(candidates,key=lambda item:(item[1]['final_balance'],
                -item[0]['base_risk_usdt'],-item[0]['max_level'],-item[0]['leverage_cap'],
                item[0]['reset_mode']=='win'))
            print(name,stage,policy,result['final_balance'],flush=True)
        selected[name]=dict(policy=policy,development_summary=result)
    pd.DataFrame(rows).to_csv(RESULTS/'pre'/'development_search.csv',index=False)
    dump(SELECTION,dict(frozen_at=datetime.now(timezone.utc),
        search_method='four single-field development-only sweeps; bounded coordinate search, not global optimum',
        search_sha256=sha(RESULTS/'pre'/'development_search.csv'),
        config_sha256=sha(CONFIG),selected=selected,holdout_exposures_at_selection=0))
    saved=json.loads(SELECTION.read_text())
    saved['development_input_sha256']=input_hashes
    dump(SELECTION,saved)


def controls_for(ctx,trades,start,end,seeds):
    natural=trades.loc[~trades.censored.astype(bool)].copy()
    frame=ctx['frame'].loc[ctx['frame'].index<pd.Timestamp(end)].copy()
    frame.attrs['minutes']=ctx['stream']['minutes']
    cache=dict(bars=frame,signals=ctx['raw'].reindex(frame.index),
               data_gap=_data_gap(frame,ctx['stream']['minutes']))
    matches,_=matched_random_controls(cache,natural,tick=ctx['stream']['tick'],
        fold_start=pd.Timestamp(start),fold_end=pd.Timestamp(end),seeds=seeds)
    return matches


def attribution(trades,ledger,matches,seed,draws):
    led=pd.DataFrame(ledger)
    accepted=led.loc[led.accepted.astype(bool)].merge(trades,on='trade_id',suffixes=('','_source'))
    if not len(accepted):
        return dict(matched_control_pnl=0,matched_actual_pnl=0,matched_delta=0,matched_n=0,
                    matched_p=None,clusters=0), pd.DataFrame()
    # Match by immutable original signal-bar-open identity, never by outcome.
    accepted['target_time']=pd.to_datetime(accepted.signal_bar_open,utc=True)
    matched=matches.loc[matches.matched.astype(bool)].copy()
    matched['target_time']=pd.to_datetime(matched.target_time,utc=True)
    event=matched.groupby(['target_time','side'],as_index=False).agg(
        control_net_r=('control_net_r','mean'),replicas=('seed','nunique'))
    paired=accepted.merge(event,on=['target_time','side'])
    # The kernel ledger risk field is intentionally entry-known; recover it
    # from actual notional and the frozen entry stop fraction, never future PNL.
    paired['risk_dollars']=paired.notional*paired.initial_risk_frac
    paired['actual_pnl']=paired.notional*paired.net_return
    # Equal stop-dollar budgets, not equal contract counts: each control R is
    # normalised by that control's own initial stop distance, by construction.
    paired['control_pnl']=paired.risk_dollars*paired.control_net_r
    paired['delta']=paired.actual_pnl-paired.control_pnl
    paired['month']=paired.target_time.dt.strftime('%Y-%m')
    clusters=paired.groupby('month').delta.sum().to_numpy(float)
    observed=abs(float(clusters.sum()))
    rng=np.random.default_rng(seed)
    null=(rng.choice([-1,1],size=(draws,len(clusters)))*clusters).sum(axis=1)
    p=(1+int((np.abs(null)>=observed-1e-10).sum()))/(draws+1) if len(clusters) else None
    return dict(matched_control_pnl=float(paired.control_pnl.sum()),
        matched_actual_pnl=float(paired.actual_pnl.sum()),matched_delta=float(paired.delta.sum()),
        matched_n=len(paired),matched_p=p,clusters=len(clusters)),paired


def evaluate(phase):
    clean_committed(BUILDERS+[SELECTION])
    cfg=json.loads(CONFIG.read_text()); selection=json.loads(SELECTION.read_text())
    if selection['config_sha256']!=sha(CONFIG): raise ValueError('selection/config mismatch')
    out=RESULTS/(phase+'_evaluation'); out.mkdir(exist_ok=False)
    result_rows=[]
    for stream in cfg['streams']:
        name=stream['name']
        if phase=='holdout' and stream['minutes']!=3: continue
        ctx=context_for(name,'holdout' if phase=='holdout' else 'pre')
        periods=['holdout'] if phase=='holdout' else ['development','validation','preholdout','continuous_pre']
        for period in periods:
            bounds=cfg[period] if period!='continuous_pre' else [cfg['development'][0],cfg['preholdout'][1]]
            trades,counts=window(ctx,*bounds)
            prefix=f'{name}_{period}'
            trades.to_csv(out/(prefix+'_opportunities.csv'),index=False)
            print(prefix,'matching controls',len(trades),flush=True)
            matches=controls_for(ctx,trades,*bounds,cfg['control_seeds'])
            matches.to_csv(out/(prefix+'_controls.csv.gz'),index=False,compression='gzip')
            chosen=selection['selected'][name]['policy']
            for arm,policy in [('martingale',chosen),('fixed',dict(chosen,max_level=0,reset_mode='win'))]:
                result=run_policy(trades,policy)
                led=pd.DataFrame(result['ledger'])
                led.to_csv(out/(prefix+'_'+arm+'_ledger.csv'),index=False)
                att,pairs=attribution(trades,result['ledger'],matches,cfg['seed'],cfg['permutation_draws'])
                pairs.to_csv(out/(prefix+'_'+arm+'_pairs.csv'),index=False)
                natural=trades.loc[~trades.censored.astype(bool)]
                result_rows.append(dict(stream=name,period=period,arm=arm,**policy,
                    **counts,**result['summary'],**att,
                    reference_mean_net_r=float(natural.net_r.mean()),
                    reference_win_rate=float(natural.net_return.gt(0).mean()),
                    reference_net_r_sum=float(natural.net_r.sum())))
            print(prefix,'evaluated',flush=True)
    pd.DataFrame(result_rows).to_csv(out/'summary.csv',index=False)
    dump(out/'evaluation.json',dict(created_at=datetime.now(timezone.utc),
         selection_sha256=sha(SELECTION),phase=phase,
         holdout_exposure_number=1 if phase=='holdout' else 0,
         files={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','select','evaluate'])
    parser.add_argument('--phase',choices=['pre','holdout'],default='pre')
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.phase)
    elif args.command=='select':
        if args.phase!='pre': raise ValueError('cannot select on holdout')
        select()
    else: evaluate(args.phase)


if __name__=='__main__': main()
