"""Validate a fixed prior20-bar sweep as V2.31 touch eligibility.

Known-history follow-up to morphology, not independent discovery. Read only
OHLC preceding/at each touch; apply sweep BEFORE key pairing. Preserve all
entry/body/key rules, exact-wick stops,18approved exits and20bp costs. Reuse
unchanged native paths and run changed pairs only through existing Nautilus.
The accepted MTF-first rule is a separate admission intersection; it uses
only the key signal's paired15m arrow at its original close.
"""
import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_range_sweep_reference import select_sweep_matches, sweep_mask

CONFIG = ROOT/'experiments/active/exp-range-v231-sweep20-20261021-v1/config.json'
CODE = ['yoyo/evaluation/range_sweep_study.py',
        'yoyo/evaluation/monday_range_sweep_reference.py',
        'yoyo/evaluation/monday_range_v2_31_reference.py']

def prepare(cfg, spec, dest):
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals, atr
    from yoyo.evaluation.monday_range_v2_31_reference import select_matches
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
    from yoyo.evaluation.range_v221_compare import controls, candidate_rows
    from yoyo.evaluation.range_v230_exit_compare import run_symbol
    from yoyo.evaluation.range_v231_midpoint import exact_stops
    from yoyo.evaluation.range_v231_returns import read, combine, FIELDS
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
    selected = select_sweep_matches(q, arrows, grid, names, weeks,
                              window=cfg['sweep_window'], **kwargs)
    records = exact_stops(selected, q, spec['tick'], cfg['end'])
    probes = sorted({r['entry_i']+d for r in records[::max(1,len(records)//8)] for d in (0,1)})
    for end in probes:
        got = select_sweep_matches(q.iloc[:end], arrows[:end], grid[:end], names, weeks[:end],
                             window=cfg['sweep_window'], **kwargs)
        assert got == [r for r in selected if r['entry_i'] < end]
    lookup = {(int(r.signal_i), int(r.side)): int(r.pair_id) for r in old.itertuples()}
    next_id = int(old.pair_id.max())+1
    for r in records:
        key = (r['signal_i'], r['side'])
        if key not in lookup:
            lookup[key] = next_id; next_id += 1
        r['pair_id'] = lookup[key]
        assert sweep_mask(q, r['side'], window=cfg['sweep_window'], tick=spec['tick'])[r['touch_i']]
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


def execute(cfg, dest):
    """Only changed native execution inputs are replayed, with both controls."""
    from yoyo.evaluation.range_framework import run
    from yoyo.evaluation.range_v230_exit_compare import run_symbol
    from yoyo.evaluation.range_v231_returns import read, combine
    metadata = {s['symbol']: prepare(cfg, s, dest) for s in cfg['symbols']}
    approved = json.loads((ROOT/cfg['exit_reference']/'config.json').read_text())
    delta_cfg = dict(approved, experiment_id=cfg['experiment_id'],
                     source=str((dest/'delta').relative_to(ROOT)))
    checks = []
    for spec in cfg['symbols']:
        coin = spec['symbol']; m = metadata[coin]
        if m['changed']:
            run(dest/'delta'/coin/'native.json')
            run_symbol(delta_cfg, dict(spec, expected_signals=len(m['changed'])), dest/'delta/exits')
        base = read(dest/coin/'candidates.csv')
        for arm in ['pivot', *[a['name'] for a in approved['arms']]]:
            patch = dest/'delta'/coin/'native/pivot' if arm == 'pivot' else dest/'delta/exits'/coin/arm
            delta = read(patch/'trades.csv') if m['changed'] else None
            prior = ROOT/cfg['comparison']/coin/arm
            current = combine(read(prior/'trades.csv'), delta, m['kept'], m['changed'], base)
            low_path = dest/'delta'/coin/'native/strict_ties/trades.csv' if arm == 'pivot' else patch/'stop_first.csv'
            low_delta = read(low_path) if m['changed'] else None
            lower = combine(read(prior/'sensitivity.csv'), low_delta, m['kept'], m['changed'], base)
            folder = dest/coin/arm; folder.mkdir(exist_ok=True)
            current.to_csv(folder/'trades.csv', index=False)
            lower.to_csv(folder/'sensitivity.csv', index=False)
            checks.append(dict(symbol=coin, arm=arm, reused_pairs=m['unchanged'], replayed_pairs=len(m['changed'])))
    dump(dest/'execution.json', dict(metadata=metadata, checks=checks))


def analyze(cfg, dest):
    """Reconcile baseline, pre-pair selection and accepted MTF conjunction."""
    from yoyo.evaluation.range_morphology import read
    from yoyo.evaluation.range_mtf_confluence import gate_rows, summarize
    from yoyo.evaluation.range_mtf_confluence_window import window_gate
    from yoyo.evaluation.pepe_range_exits import prepare as mtf_context, aggregate, inference, pairs
    from yoyo.evaluation.range_management_study import holm
    execution = json.loads((dest/'execution.json').read_text())
    previous = json.loads((ROOT/cfg['mtf_previous']/'summary.json').read_text())
    arms = list(dict.fromkeys(r['arm'] for r in previous['rows'] if r['arm'] != 'screenshot'))
    results = []; checks = []; sources = []; primary = []
    for spec in cfg['symbols']:
        coin = spec['symbol']; folder = dest/coin; old = ROOT/cfg['comparison']/coin
        path = ROOT/cfg['source']/coin/'candles.csv.gz'; sources.append(file_stamp(path))
        five = read(path)
        five.index = pd.DatetimeIndex(pd.to_datetime(five.pop('ts'), unit='ms', utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
        q = aggregate(five, 15); ctx = mtf_context(five)
        matches = read(folder/'signal_matches.csv'); base = read(folder/'candidates.csv')
        g = gate_rows(ctx, matches, base); g['window_both'] = window_gate(g)
        g.to_csv(folder/'admission.csv', index=False)
        oldg = read(ROOT/cfg['mtf_previous']/f'{coin}_admission.csv')
        oldmatches = read(old/'signal_matches.csv')
        oldmorph = read(ROOT/'experiments/active/exp-range-v231-morphology-20261021-v1'/f'{coin}_features.csv')
        original_sweep = [bool(sweep_mask(q, int(r.side), window=cfg['sweep_window'], tick=spec['tick'])[int(r.touch_i)]) for r in oldmatches.itertuples()]
        stored = oldmorph.set_index('pair_id').loc[oldmatches.pair_id, 'sweep20'].to_numpy(bool)
        np.testing.assert_array_equal(original_sweep, stored)
        post_ids = set(oldmatches.loc[original_sweep, 'pair_id'])
        prefix_checks = 0
        for idx in sorted(set(np.linspace(0, len(g)-1, 6, dtype=int))):
            row = g.iloc[idx]; pid = int(row.pair_id); end = int(row.entry_i)+1
            got = gate_rows(mtf_context(five.iloc[:end]), matches[matches.pair_id == pid], base[base.pair_id == pid])
            pd.testing.assert_frame_equal(g.loc[g.pair_id == pid, got.columns].reset_index(drop=True), got)
            prefix_checks += 1
        checks.append(dict(symbol=coin, sweep_n=len(g), sweep_mtf_n=int(g.window_both.sum()),
            postfilter_n=len(post_ids), rematched=execution['metadata'][coin]['reassigned'], mtf_prefix_checks=prefix_checks))
        times = pd.to_datetime(g.entry_time, utc=True); oldtimes = pd.to_datetime(oldg.entry_time, utc=True)
        for arm in arms:
            newrows = read(folder/arm/'trades.csv'); oldrows = read(old/arm/'trades.csv')
            low = read(folder/arm/'sensitivity.csv')
            sources.extend([file_stamp(old/arm/'trades.csv'), file_stamp(folder/arm/'trades.csv')])
            for period in ['all', 'first_half', 'second_half', 'recent2m']:
                def mask(t):
                    if period == 'all': return np.ones(len(t), bool)
                    if period == 'first_half': return t < pd.Timestamp(cfg['midpoint'])
                    return t >= pd.Timestamp(cfg['midpoint'] if period == 'second_half' else cfg['recent_start'])
                prior = oldg[mask(oldtimes)]; current = g[mask(times)]
                groups = dict(baseline=(oldrows, prior.pair_id.tolist()),
                    mtf=(oldrows, prior.loc[prior.window_both, 'pair_id'].tolist()),
                    postfilter_sweep=(oldrows, prior.loc[prior.pair_id.isin(post_ids), 'pair_id'].tolist()),
                    sweep=(newrows, current.pair_id.tolist()),
                    sweep_mtf=(newrows, current.loc[current.window_both, 'pair_id'].tolist()))
                for name, (rows, ids) in groups.items():
                    stat = summarize(rows, ids)
                    if name in ['baseline', 'mtf']:
                        saved = next(r for r in previous['rows'] if r['symbol'] == coin and r['arm'] == arm and r['period'] == period and r['group'] == ('all' if name == 'baseline' else 'both'))
                        for field in ['n', 'wins', 'net_r', 'random_net_r']:
                            np.testing.assert_allclose(stat[field], saved[field], rtol=0, atol=1e-7)
                    if name in ['sweep', 'sweep_mtf']:
                        stat['stop_first_net_r'] = summarize(low, ids)['net_r']
                    results.append(dict(symbol=coin, arm=arm, period=period, group=name, **stat))
                    if arm == 'pivot' and period == 'all' and name in ['sweep', 'sweep_mtf']:
                        part = rows[rows.pair_id.isin(ids)]
                        primary.append(dict(symbol=coin, group=name,
                            **inference(pairs(part[part.population == 'signal'], part[part.population == 'random']), cfg['seed'])))
    for row, p in zip(primary, holm([r['p_one_sided'] for r in primary])):
        row['holm_four_p'] = float(p)
    stability = []
    for coin in ['BTC', 'ETH']:
        for group in ['sweep', 'sweep_mtf']:
            rs = [r for r in results if r['symbol'] == coin and r['period'] == 'all' and r['group'] == group]
            baseline = {r['arm']: r for r in results if r['symbol'] == coin and r['period'] == 'all' and r['group'] == ('mtf' if group == 'sweep_mtf' else 'baseline')}
            stability.append(dict(symbol=coin, group=group, arms=len(rs),
                higher_win_arms=sum(r['win_rate'] > baseline[r['arm']]['win_rate'] for r in rs),
                positive_net_arms=sum(r['net_r'] > 0 for r in rs)))
    for stamp in sources: assert stamp == file_stamp(stamp['path'])
    pd.DataFrame(results).to_csv(dest/'comparison.csv', index=False)
    dump(dest/'summary.json', dict(config=cfg, rows=results, primary_tests=primary, checks=checks,
        execution=execution, stability=stability, sources=sources,
        builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        limitations='Fixed20 selected after known-history morphology. Same inspected data, not independent validation. Conditional matched random controls are not sweep/MTF constrained.20bp cost, exact wickSL, existing18exits and5mOHLC fill assumptions. IndependentR not account returns. No Pine/default or live change.'))
    print(json.dumps([r for r in results if r['arm'] == 'pivot' and r['period'] in ['all', 'recent2m'] and r['group'] != 'postfilter_sweep'], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--analyze', action='store_true')
    args = parser.parse_args()
    if args.execute == args.analyze: raise ValueError('Choose exactly one execution stage')
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE, str(CONFIG)], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    cfg = json.loads(CONFIG.read_text()); dest = CONFIG.parent
    target = dest/('execution.json' if args.execute else 'summary.json')
    if target.exists():
        print('Reuse completed '+str(target)); return
    (execute if args.execute else analyze)(cfg, dest)


if __name__ == '__main__': main()

