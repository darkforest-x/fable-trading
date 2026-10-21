"""Compare 0.618-only V2.31 entries against the frozen all-key 15m study.

Only entry candidate eligibility changes. Closed 15m OHLC, pre-touch Monday
snapshots, Stoch5/3/3, pre5 bodies, +/-2 touches, midpoint-side gate, exact
wick stops and all18 exits remain fixed. The complete key grid remains
available to key-based exits. Selection never reads outcomes. Preserve
unchanged native signal/control paths; replay only changed execution inputs.
Controls retain their original time if week/half/ATR14 bucket still matches;
otherwise select a new deterministic matching time without replacement.
"""
import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.monday_range_v2_2_reference import signals, atr
from yoyo.evaluation.monday_range_v2_31_reference import select_matches
from yoyo.evaluation.pepe_range_exits import aggregate
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.range_v221_compare import controls, candidate_rows
from yoyo.evaluation.range_v230_exit_compare import run_symbol
from yoyo.evaluation.range_v231_midpoint import exact_stops
from yoyo.evaluation.range_v231_returns import read, combine, describe, FIELDS

DEFAULT = ROOT/'experiments/active/exp-range-v231-fib0618-20261021-v1/config.json'
CODE = ['yoyo/evaluation/range_v231_fib0618.py',
        'yoyo/evaluation/monday_range_v2_31_reference.py']


def prepare(cfg, spec, dest):
    symbol = spec['symbol']; folder = dest/symbol
    source = ROOT/cfg['source']/symbol
    prior = ROOT/cfg['comparison']/symbol
    native = json.loads((source/'native.json').read_text())
    identity = dict(config=cfg, spec=spec, data=file_stamp(ROOT/native['data']),
                    baseline=file_stamp(prior/'signal_matches.csv'),
                    code=[file_stamp(ROOT/p) for p in CODE])
    receipt = folder/'preparation.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved['identity'] != identity:
            raise RuntimeError('Inputs changed; preserve results and use a new directory')
        return saved
    five = read(ROOT/native['data'])
    five.index = pd.DatetimeIndex(pd.to_datetime(five.pop('ts'), unit='ms', utc=True)
                                 + pd.Timedelta(minutes=5)).as_unit('ns')
    assert np.all(np.diff(five.index.asi8) == pd.Timedelta(minutes=5).value)
    q = aggregate(five, 15)
    grid, names, weeks, _ = monday_levels(q, cfg['ratios'], cfg['timezone'])
    arrows = signals(q).to_numpy()
    kwargs = dict(before=5, wait_bars=2, penetration=.1, buffer=.002,
                  tick=spec['tick'], timezone=cfg['timezone'], start=cfg['start'])
    baseline = exact_stops(select_matches(q, arrows, grid, names, weeks, **kwargs),
                           q, spec['tick'], cfg['end'])
    old = read(prior/'signal_matches.csv')
    columns = old.columns.drop('pair_id')
    pd.testing.assert_frame_equal(pd.DataFrame(baseline)[columns], old[columns],
                                  check_dtype=False, rtol=0, atol=1e-10)
    selected = select_matches(q, arrows, grid, names, weeks,
                              allowed_keys=cfg['allowed_entry_keys'], **kwargs)
    records = exact_stops(selected, q, spec['tick'], cfg['end'])
    probes = sorted({r['entry_i']+d for r in records[::max(1,len(records)//8)] for d in (0,1)})
    for end in probes:
        got = select_matches(q.iloc[:end], arrows[:end], grid[:end], names, weeks[:end],
                             allowed_keys=cfg['allowed_entry_keys'], **kwargs)
        assert got == [r for r in selected if r['entry_i'] < end]
    lookup = {(int(r.signal_i), int(r.side)): int(r.pair_id) for r in old.itertuples()}
    next_id = int(old.pair_id.max())+1
    for r in records:
        key = (r['signal_i'], r['side'])
        if key not in lookup:
            lookup[key] = next_id; next_id += 1
        r['pair_id'] = lookup[key]
        assert r['key'] == ('0.618:down' if r['side'] == 1 else '0.618:up')
    base = read(prior/'candidates.csv')
    bucket = np.digitize(atr(q,14).to_numpy()/q.close.to_numpy(), cfg['volatility_edges'])
    # Prior ledgers use epoch microseconds for week IDs; preserve their schema.
    week = (q.index-pd.to_timedelta(q.index.dayofweek,unit='D')).normalize().asi8//1000
    half = (q.index >= pd.Timestamp(cfg['midpoint'])).astype(int)
    excluded = set(np.flatnonzero(arrows)) | {r['entry_i'] for r in records}
    matched = {}; fallback = []; reserved = set()
    for r in records:
        existing = base[(base.pair_id == r['pair_id']) & (base.population == 'random')]
        ci = -1 if existing.empty else int(q.index.get_indexer([five.index[int(existing.iloc[0].entry_i)]])[0])
        i = r['entry_i']
        valid = ci >= 0 and ci not in excluded and ci not in reserved
        if valid:
            valid = (week[i], half[i], bucket[i]) == (week[ci], half[ci], bucket[ci])
            assert int(existing.iloc[0].week) == week[ci]
        if valid:
            matched[r['pair_id']] = ci; reserved.add(ci)
        else:
            fallback.append(r)
    if fallback:
        exclusion_arrows = arrows.copy()
        exclusion_arrows[list(reserved | excluded)] = 1
        additional, _, _, _ = controls(q, exclusion_arrows, fallback, cfg, spec)
        matched.update(additional)
    assert len(set(matched.values())) == len(records)
    chosen = candidate_rows(five, q, records, matched, (half,week,bucket), spec['tick'])
    changed = []
    for r in records:
        pid = r['pair_id']
        prior_pair = base[base.pair_id == pid].sort_values('population')
        new_pair = chosen[chosen.pair_id == pid].sort_values('population')
        if len(prior_pair) != 2 or not np.allclose(prior_pair[FIELDS], new_pair[FIELDS], rtol=0, atol=1e-8):
            changed.append(pid)
    mapped = pd.DataFrame(records)
    common = mapped.merge(old, on=['pair_id'], suffixes=('_new','_old'), validate='one_to_one')
    reassigned = [int(r.pair_id) for r in common.itertuples()
                  if any(getattr(r,k+'_new') != getattr(r,k+'_old')
                         for k in ['touch_i','entry_i','key','entry','stop'])]
    kept = [r['pair_id'] for r in records]
    folder.mkdir(parents=True, exist_ok=False)
    mapped.to_csv(folder/'signal_matches.csv', index=False)
    chosen.to_csv(folder/'candidates.csv', index=False)
    if changed:
        delta = dest/'delta'/symbol; delta.mkdir(parents=True)
        chosen[chosen.pair_id.isin(changed)].to_csv(delta/'candidates.csv', index=False)
        native.update(experiment_id=cfg['experiment_id'],
                      entry_candidates=str((delta/'candidates.csv').relative_to(ROOT)),
                      output=str((delta/'native').relative_to(ROOT)))
        dump(delta/'native.json', native)
    metadata = dict(identity=identity, previous=len(old), n=len(records),
                    longs=sum(r['side']==1 for r in records), shorts=sum(r['side']==-1 for r in records),
                    kept=kept, changed=changed, reassigned=reassigned,
                    unchanged=len(records)-len(changed), new_arrows=len(set(kept)-set(old.pair_id)),
                    removed=len(set(old.pair_id)-set(kept)), new_control_times=len(fallback),
                    prefix_checks=len(probes), source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(receipt,metadata)
    print(json.dumps(dict(symbol=symbol,**{k:v for k,v in metadata.items() if k not in ['identity','kept']})),flush=True)
    return metadata


def analyze(cfg, dest, metadata, approved):
    previous = ROOT/cfg['comparison']
    old = json.loads((previous/'summary.json').read_text())
    results = [dict(r,version='all_keys') for r in old['rows'] if r['version']=='v231']
    checks = []
    for spec in cfg['symbols']:
        symbol = spec['symbol']; m = metadata[symbol]
        base = read(dest/symbol/'candidates.csv')
        for arm in ['pivot',*[a['name'] for a in approved['arms']]]:
            patch = dest/'delta'/symbol/'native/pivot' if arm=='pivot' else dest/'delta/exits'/symbol/arm
            delta = read(patch/'trades.csv') if m['changed'] else None
            current = combine(read(previous/symbol/arm/'trades.csv'),delta,m['kept'],m['changed'],base)
            low_path = dest/'delta'/symbol/'native/strict_ties/trades.csv' if arm=='pivot' else patch/'stop_first.csv'
            low_delta = read(low_path) if m['changed'] else None
            lower = combine(read(previous/symbol/arm/'sensitivity.csv'),low_delta,m['kept'],m['changed'],base)
            folder = dest/symbol/arm; folder.mkdir(exist_ok=True)
            current.to_csv(folder/'trades.csv',index=False)
            lower.to_csv(folder/'sensitivity.csv',index=False)
            for group,part in [('all',current),('first_half',current[current.segment==0]),('second_half',current[current.segment==1])]:
                if part.empty:
                    continue
                r = describe(part,symbol,arm,'fib0618',group,cfg['seed'])
                if group=='all':
                    s=part[part.population=='signal']
                    r['sensitivity_net_r']=float(lower[lower.population=='signal'].net_r.sum())
                    r['boundary_n']=int((s.reason==0).sum()) if arm=='pivot' else int((s.boundary_weight>0).sum())
                results.append(r)
            checks.append(dict(symbol=symbol,arm=arm,reused_pairs=m['unchanged'],replayed_pairs=len(m['changed'])))
    for symbol in [s['symbol'] for s in cfg['symbols']]:
        family=[r for r in results if r['symbol']==symbol and r['group']=='all']
        for r,p,b in zip(family,holm([r['p_one_sided'] for r in family]),holm([r['bp_excess_p'] for r in family])):
            r['holm_p_key_family_arms']=float(p); r['bp_holm_p_key_family_arms']=float(b)
    dump(dest/'summary.json',dict(config=cfg,rows=results,checks=checks,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(json.dumps([r for r in results if r['arm']=='pivot' and r['group']=='all'],indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true'); args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,str(DEFAULT)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and config before generation')
    if (DEFAULT.parent/'summary.json').exists():
        print('Completed result exists: '+str(DEFAULT.parent/'summary.json')); return
    cfg=json.loads(DEFAULT.read_text()); dest=DEFAULT.parent
    metadata={s['symbol']:prepare(cfg,s,dest) for s in cfg['symbols']}
    if args.prepare_only: return
    approved=json.loads((ROOT/cfg['exit_reference']/'config.json').read_text())
    delta_cfg=dict(approved,experiment_id=cfg['experiment_id'],source=str((dest/'delta').relative_to(ROOT)))
    for spec in cfg['symbols']:
        changed=metadata[spec['symbol']]['changed']
        if changed:
            run(dest/'delta'/spec['symbol']/'native.json')
            run_symbol(delta_cfg,dict(spec,expected_signals=len(changed)),dest/'delta/exits')
    analyze(cfg,dest,metadata,approved)


if __name__=='__main__': main()
