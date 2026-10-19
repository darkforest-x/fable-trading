"""Task62 V4 causal K-extreme filters and smaller structural stops.

Owner authorized K thresholds/lookback and both cap/buffer research on
2026-10-20. K uses closed OHLC through the arrow bar; min/max windows are
current plus 0/1/2/3/5 preceding bars. Confirmation and structural OHLC use
only signal-2 through signal+1; execution starts at signal+2 open. Future
minute OHLC is an outcome only. No time-of-day gate or production changes.
"""
import argparse
import ctypes
import itertools
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_stoch_confirm_exits import (
    EXP as V1, load_minutes, prepare, opportunities, trace,
)
from yoyo.evaluation.eth_stoch_target_be import EXP as V3, policies
from yoyo.evaluation.eth_stoch_tail_forensics import endpoint

EXP = Path('experiments/active/exp-eth5m-stoch-extreme-stop-20261020-v4')
FIELDS = {'exit_i': np.int32, 'exit_price': np.float64, 'reason': np.uint8,
          'be_activated': np.uint8, 'ambiguous': np.uint8}
REASONS = ['boundary', 'stop', 'stop_gap', 'target', 'target_gap']


def native_library(directory):
    """Compile this repository's small outcome kernel; no external packages."""
    target = directory/'native.dylib'
    subprocess.run(['clang++', '-O3', '-std=c++17', '-shared', '-fPIC',
                    str(Path(__file__).with_name('eth_stoch_extreme_native.cpp')),
                    '-o', str(target)], check=True)
    lib = ctypes.CDLL(str(target.resolve()))
    lib.replay_extreme.restype = None
    lib.replay_extreme.argtypes = [ctypes.c_void_p]*12 + [ctypes.c_int64]*2 + [ctypes.c_void_p]*5
    return lib


def native_replay(lib, ctx, events, stop, arms, cost, directory=None):
    """Precompute Python-rounded barriers and return typed outcome arrays."""
    n, pc = len(events), len(arms)
    entry = events.entry.to_numpy(float)
    side = events.side.to_numpy(np.int8)
    risk = side*(entry-stop)
    assert np.isfinite(risk).all()
    start = events.entry_i.to_numpy(np.int64)
    end = events.end_i.to_numpy(np.int64)
    assert np.all((start >= 0) & (start < end) & (end <= len(ctx['o'])))
    assert len(ctx['o']) < 2**31
    target = np.empty((n, pc)); dest = target.copy(); trigger = target.copy()
    for j, p in enumerate(arms):
        target[:, j] = [round((e+s*p.target*r)/.01)*.01 for e,s,r in zip(entry,side,risk)]
        raw = entry+side*(cost*entry if p.net_be else 0)
        dest[:, j] = np.where(side==1, np.ceil(raw*100-1e-8), np.floor(raw*100+1e-8))/100
        trigger[:, j] = np.maximum(p.be*risk, side*(dest[:, j]-entry)) if p.be else -1
    inputs = [ctx[k] for k in ('o','h','l','c')] + [start,end,side,entry,stop,target,dest,trigger]
    inputs = [np.ascontiguousarray(x) for x in inputs]
    arrays = {}
    for name, dtype in FIELDS.items():
        arrays[name] = (np.lib.format.open_memmap(directory/f'{name}.npy', mode='w+', dtype=dtype, shape=(n,pc))
                        if directory else np.empty((n,pc),dtype=dtype))
    lib.replay_extreme(*[ctypes.c_void_p(x.ctypes.data) for x in inputs], n, pc,
                      *[ctypes.c_void_p(x.ctypes.data) for x in arrays.values()])
    for a in arrays.values():
        if hasattr(a, 'flush'): a.flush()
    return arrays


def feature_extrema(k, lookbacks):
    """Arrow-bar K and previous lookback bars only; no confirmation-bar K."""
    return {lb: (k.rolling(lb+1,min_periods=1).min().to_numpy(),
                 k.rolling(lb+1,min_periods=1).max().to_numpy()) for lb in lookbacks}


def filter_mask(events, lower, upper, lookback):
    if lookback < 0:
        return np.ones(len(events), dtype=bool)
    return np.where(events.side.to_numpy()==1,
                    events[f'kmin_{lookback}'].to_numpy()<lower,
                    events[f'kmax_{lookback}'].to_numpy()>upper)


def stops(events, buffer):
    raw = events.stop.to_numpy()+events.side.to_numpy()*(1-buffer)
    result = np.where(events.side.to_numpy()==1, np.floor(raw*100+1e-8), np.ceil(raw*100-1e-8))/100
    return result, events.side.to_numpy()*(events.entry.to_numpy()-result)


def matched_indices(events, admitted, seed):
    """Same gated stratum, fixed seeded draws, independent of future outcomes."""
    signal = np.flatnonzero(admitted & events.is_signal.to_numpy(bool))
    pool = np.flatnonzero(admitted & events.raw_side.eq(0).to_numpy())
    groups = {}
    group_ids = events.group_id.to_numpy()
    for i in pool:
        groups.setdefault(group_ids[i], []).append(i)
    rng = np.random.default_rng(seed)
    left, right, missing = [], [], []
    for i in signal:
        choices = groups.get(group_ids[i])
        if not choices:
            missing.append(i)
        else:
            left.append(i); right.append(choices[int(rng.integers(len(choices)))])
    return signal, np.array(left,dtype=int), np.array(right,dtype=int), np.array(missing,dtype=int)


def prepare_events(ctx, cfg, source):
    all_rows = opportunities(ctx, source)
    _, risk_small = stops(all_rows, min(cfg['stop_buffers']))
    _, risk_large = stops(all_rows, max(cfg['stop_buffers']))
    all_rows = all_rows[(risk_large > 0) & (risk_small <= max(cfg['risk_caps'])+1e-8) &
                        (all_rows.is_signal | all_rows.raw_side.eq(0))].copy().reset_index(drop=True)
    # Union of all buffer admissions: an entry gap can put the open beyond a
    # tighter stop even while the original wider stop remains valid. The native
    # kernel explicitly marks nonpositive-risk rows255; no outcome is invented.
    extrema = feature_extrema(ctx['signals'].k, cfg['lookbacks'])
    anchors = all_rows.anchor_i.to_numpy(int)
    all_rows['k_at_arrow'] = ctx['signals'].k.to_numpy()[anchors]
    for lb, (lo, hi) in extrema.items():
        all_rows[f'kmin_{lb}'] = lo[anchors]
        all_rows[f'kmax_{lb}'] = hi[anchors]
    all_rows['group_id'] = pd.factorize(pd.MultiIndex.from_frame(all_rows[['segment','week','bucket','side']]))[0]
    all_rows['end_i'] = all_rows.segment.map({x:endpoint(ctx,source,x) for x in all_rows.segment.unique()})
    old = pd.read_csv(V1/'results_v1'/'entries.csv')
    _, r = stops(all_rows, 1.)
    mask = (r > 0) & (r <= 8+1e-8)
    idx, left, right, missing = matched_indices(all_rows, mask, cfg['control_seed'])
    assert all_rows.iloc[idx].anchor_i.tolist() == old.anchor_i.tolist()
    old_c = pd.read_csv(V1/'results_v1'/'control_entries.csv')
    assert all_rows.iloc[right].anchor_i.tolist() == old_c.anchor_i.tolist()
    for col in ['entry','stop','risk']:
        assert np.allclose(all_rows.iloc[idx][col], old[col], rtol=0, atol=1e-8)
    return all_rows, dict(original_entries=len(old), original_controls=len(old_c), missing_controls=len(missing))


def parity_and_oracle(ctx, events, arrays, stop, arms, buffer):
    """Full V3 identity on buffer1; scalar replay on tails/random extremes."""
    anchor_index = pd.Series(np.arange(len(events)), index=events.anchor_i)
    parity = 0
    if buffer == 1:
        for pop, entries in [('signal','entries.csv'),('random','control_entries.csv')]:
            old_entries = pd.read_csv(V1/'results_v1'/entries).set_index('pair_id')
            old = pd.read_csv(V3/'results'/f'{pop}_trades.csv.gz')
            rowids = anchor_index.loc[old_entries.loc[old.pair_id].anchor_i].to_numpy()
            cols = old.policy.map({p.name:j for j,p in enumerate(arms)}).to_numpy()
            assert np.array_equal(arrays['exit_i'][rowids,cols], old.exit_i)
            assert np.array_equal(arrays['reason'][rowids,cols], old.reason.map({x:i for i,x in enumerate(REASONS)}))
            assert np.allclose(arrays['exit_price'][rowids,cols], old.exit_price, rtol=0, atol=1e-8)
            assert np.array_equal(arrays['be_activated'][rowids,cols], old.be_activated)
            assert np.array_equal(arrays['ambiguous'][rowids,cols], old.ambiguous_old_stop_target)
            parity += len(old)
    quiet = dict(ctx, arrow=np.zeros_like(ctx['arrow']))
    rng = np.random.default_rng(621020)
    valid = np.flatnonzero(events.side.to_numpy()*(events.entry.to_numpy()-stop)>0)
    picked = set(map(int,rng.choice(valid,min(30,len(valid)),replace=False)))
    durations = arrays['exit_i']-events.entry_i.to_numpy()[:,None]
    # Include longest-held, both target/stop ambiguity, and each boundary type.
    for j in range(len(arms)):
        picked.update(map(int,np.argsort(durations[:,j])[-2:]))
        for value in (0,2,4):
            x = np.flatnonzero(arrays['reason'][:,j]==value)
            picked.update(map(int,x[:2]))
        picked.update(map(int,np.flatnonzero(arrays['ambiguous'][:,j])[:2]))
    checks = 0
    for i in sorted(picked):
        r = events.iloc[i]
        for j,p in enumerate(arms):
            z = trace(quiet,int(r.entry_i),int(r.side),float(r.entry),float(stop[i]),p,
                      end_i=int(r.end_i))
            assert z['exit_i'] == arrays['exit_i'][i,j], (buffer,i,p.name,z)
            assert REASONS[int(arrays['reason'][i,j])] == z['reason'], (buffer,i,p.name,z)
            assert abs(z['exit_price']-arrays['exit_price'][i,j]) < 1e-8
            assert z['be_activated'] == bool(arrays['be_activated'][i,j])
            checks += 1
    return dict(v3_parity=parity, scalar_paths=checks, sampled_events=len(picked))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out',default='paths')
    args = ap.parse_args()
    out = EXP/args.out
    out.mkdir(exist_ok=False)
    cfg = json.loads((EXP/'config.json').read_text())
    source = json.loads((V1/'config.json').read_text())
    commit = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    minutes, facts = load_minutes(source)
    ctx = prepare(minutes)
    events, checks = prepare_events(ctx,cfg,source)
    events.to_csv(out/'events.csv.gz',index=False)
    arms = policies(cfg)
    (out/'policies.json').write_text(json.dumps([p.__dict__ for p in arms],indent=2)+'\n')
    lib = native_library(out)
    print('candidate events',len(events),'signal candidates',int(events.is_signal.sum()),flush=True)
    validations = []
    for buffer in cfg['stop_buffers']:
        begin = time.time()
        folder = out/f'buffer_{buffer:g}'
        folder.mkdir()
        stop,risk = stops(events,buffer)
        a = native_replay(lib,ctx,events,stop,arms,cfg['round_trip_cost'],folder)
        print('native buffer',buffer,'paths',len(events)*len(arms),'seconds',time.time()-begin,flush=True)
        checks_b = parity_and_oracle(ctx,events,a,stop,arms,buffer)
        validations.append(dict(buffer=buffer,**checks_b))
        print('checked',validations[-1],flush=True)
        del a
    facts.update(builder_commit=commit,original_parity=checks,native_validation=validations,
                 candidate_events=len(events),policies=len(arms),training_eligible=False,production_eligible=False)
    (out/'receipt.json').write_text(json.dumps(facts,indent=2)+'\n')


if __name__ == '__main__':
    main()
