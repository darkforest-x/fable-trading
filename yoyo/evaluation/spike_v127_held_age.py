"""Frozen V12.7 candidate replay: cap the age of held break evidence.

Source: owner approval on 2026-09-23 ("先回测第1条时限，再做V12.7") after the
read-only V12.6 latency decomposition. In V12.6 a confirmed break may pair
with any later V9 while every close stays above its frozen line, for up to
the 600-bar line life; break-first joints were 72% of candidates with a median
34 fifteen-minute bars between break and V9. The only change is the held
evidence lifetime passed to ``pair_events``: 600 -> 8 chart bars. Box-first
pairing, V9 admission, geometry, exits and the 20bp cost are unchanged.

Features use chart OHLCV through the decision close and HTF values visible at
the chart bar (``[1]``/lookahead_on port inherited from the V12.6 runner). The
extra latency fields (``cross_i``/``cross_close_time``/``cross_close``) use only
closes up to the break confirmation bar on the break's own clock: the start of
the uninterrupted above-line run that ends at that confirmation. Pivot-tie
parity with native TradingView remains unverified; this is research only.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v126_htf_recheck as v126
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v7_fast import _data_gap
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed


EXP = Path('experiments/active/exp-spike-v127-held-age-20260923-v1')
CONFIG = EXP / 'config.json'
PLAN = EXP / 'PROJECT_PLAN.md'
PINE = Path('yoyo/evaluation/pine/spike_burst_v12_6.pine')
ARMS = ('baseline', 'held_age_8')
HELD_LIFE = {'baseline': 600, 'held_age_8': 8}
START, SPLIT, END, MINUTES = v126.START, v126.SPLIT, v126.END, v126.MINUTES
TABLES = v126.TABLES
source, execution = v126.source, v126.execution


def line_value(ap, bp, ax, bx, x):
    """Same operation order as ``Line.at`` / Pine ``f_v10_price`` (linear)."""
    return ap + (bp - ap) * (float(x - ax) / (bx - ax))


def cross_start(close, ap, bp, ax, bx, born_i, break_i):
    """First bar of the above-line close run ending at the break confirmation.

    Reads closes only at or before ``break_i`` on the break's own clock and
    never before the line's birth bar, so it is known when the break is known.
    """
    j = int(break_i)
    while j - 1 > int(born_i) and close[j - 1] > line_value(ap, bp, ax, bx, j - 1):
        j -= 1
    return j


def _decision(e, f, hf, symbol, asset, facts, known, passed, arm):
    """V12.6 decision record plus the arm and causal crossing reference."""
    i = int(e['joint_i'])
    ts = f.index[i] + pd.Timedelta(minutes=MINUTES)
    parent = int(e['box_entry_i'])
    is_htf = e['source'] == 'htf'
    frame, minutes = (hf, 60) if is_htf else (f, MINUTES)
    break_open = frame.index[int(e['break_i'])]
    j = cross_start(frame.close.to_numpy(), float(e['ap']), float(e['bp']), int(e['ax']), int(e['bx']),
                    int(e['born_i']), int(e['break_i']))
    return {**e, 'arm': arm, 'held_life': HELD_LIFE[arm], 'signal_i': i, 'signal_bar_open': f.index[i], 'signal_close': ts,
        'pair_source': e['source'], 'line_source': 'raw' if e['source_id'] == 0 else 'soft',
        'break_bar_open': break_open, 'break_close_time': break_open + pd.Timedelta(minutes=minutes),
        'first_visible_chart_open': f.index[int(e['visible_i'])] if is_htf else break_open,
        'cross_i': j, 'cross_close_time': frame.index[j] + pd.Timedelta(minutes=minutes), 'cross_close': float(frame.close.iloc[j]),
        'trade_key': f'binance_um:{symbol}:15m:v126:{ts.isoformat()}', 'symbol': symbol, 'asset': asset,
        'timeframe': '15m', 'v9_reference_time': f.index[parent] + pd.Timedelta(minutes=MINUTES),
        'v9_reference_price': float(f.close.iloc[parent]), 'bars_after_v9': i - parent,
        'joint_close': float(f.close.iloc[i]), 'h1_known': bool(known[i]), 'h1_pass': bool(passed[i]),
        'h1_sma60': float(facts['h1'].sma_60.iloc[i]), 'h1_source_close': facts['h1'].htf_close_time.iloc[i],
        'next_open_time': f.index[i + 1] if i + 1 < len(f) else pd.NaT,
        'next_open_price': float(f.open.iloc[i + 1]) if i + 1 < len(f) else np.nan,
        'first_discovered_at': None, 'discovery_basis': 'historical_replay_not_live'}


def run_stream(base, symbol, meta):
    """Build lines once, pair per arm with its held life, replay each arm serially."""
    from yoyo.evaluation.spike_v126_engine import line_events, pair_events
    tick, asset = float(meta['tick']), meta['asset']
    since = START - pd.Timedelta(minutes=1500 * MINUTES)
    bars, partial = v126.complete_bars(base.loc[base.index >= since], MINUTES)
    higher, hpartial = v126.complete_bars(base, 60)
    if len(bars) < 2:
        return {name: pd.DataFrame() for name in TABLES}, {'bars': len(bars), 'candidates': {arm: 0 for arm in ARMS}}
    facts = v126.pine_facts(bars, base, asset, tick)
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
        if j >= len(clock) or clock[j] - visible_time >= MINUTES:
            continue
        mapped.append({**e, 'visible_i': j,
            'break_bar_open': hf.index[int(e['break_i'])],
            'break_close_time': hf.index[int(e['break_i'])] + pd.Timedelta(minutes=60),
            'first_visible_chart_open': f.index[j],
            **{k + '_t': int(hclock[int(e[k])]) for k in ('ax', 'bx', 'cx')}})
    known, passed = v126.joint_permission(f.close, facts['h1'].sma_60)
    decisions, trades, statuses, counts = [], [], [], {}
    key = f'binance_um:{symbol}:15m'
    prepared = source.prepared_arm(f, facts['gap'], facts['side'], key, {'symbol': symbol, 'timeframe': '15m'}, MINUTES, tick)
    cache = {}
    def evaluate(i):
        if i not in cache:
            cache[i] = execution.attempt(prepared, i)
        return cache[i]
    for arm in ARMS:
        joints = pair_events(f.close.to_numpy(), bar_times=clock, chart_breaks=local.events, htf_breaks=mapped,
            box_id=facts['box']['box_entry'], confirmed_long=facts['v9_long'], gap=facts['gap'], life=HELD_LIFE[arm])
        events = []
        for e in joints:
            ts = f.index[int(e['joint_i'])] + pd.Timedelta(minutes=MINUTES)
            if START <= ts < END:
                events.append(_decision(e, f, hf, symbol, asset, facts, known, passed, arm))
        events.sort(key=lambda e: e['signal_i'])
        if len({e['box_entry_i'] for e in events}) != len(events):
            raise ValueError('a V9 box generated multiple joint candidates')
        # v126.serial rejects only the H1-recheck arm; both arms here admit all.
        t, s = v126.serial(events, arm, evaluate, len(f))
        decisions.extend(events); trades.extend(t); statuses.extend(s); counts[arm] = len(events)
    trade_cols = list(dict.fromkeys([*source.TRADE_KEEP, 'status', 'arm', 'trade_key', 'symbol', 'asset', 'timeframe',
        'source', 'order', 'box_entry_i', 'signal_close', 'h1_pass', 'h1_known', 'h1_sma60', 'bars_after_v9', 'held_life']))
    table = pd.DataFrame(trades, columns=trade_cols)
    if len(table):
        closed = table.loc[~table.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
    references = [{'symbol': symbol, 'box_entry_i': p, 'v9_reference_close': f.index[p] + pd.Timedelta(minutes=MINUTES),
        'v9_reference_price': f.close.iloc[p], 'h1_sma60_at_v9': facts['h1'].sma_60.iloc[p]}
        for p in sorted(set(int(x) for x in facts['box']['box_entry'] if x >= 0))]
    breaks = [{**e, 'engine_source': 'chart'} for e in local.events] + [{**e, 'engine_source': 'htf'} for e in mapped]
    parts = {'decisions': pd.DataFrame(decisions), 'trades': table, 'statuses': pd.DataFrame(statuses),
        # One pool per arm, no admission gate: identical trade_keys draw identical controls.
        'controls': v126.controls(prepared, trades, facts['ready'], passed, evaluate),
        'reference_boxes': pd.DataFrame(references), 'breaks': pd.DataFrame(breaks)}
    summary = {'bars': len(f), 'higher_bars': len(hf), 'partial_chart_buckets_excluded': partial,
        'partial_h1_buckets_excluded': hpartial, 'chart_gaps': int(facts['gap'].sum()), 'candidates': counts,
        'v9_long_confirmations': int(facts['v9_long'].sum()), 'chart_breaks': len(local.events), 'htf_breaks_visible': len(mapped),
        'native_pine_parity': False, 'pivot_tie_policy': 'strict_unverified_native'}
    return parts, summary


def worker(args):
    symbol, path, meta, out, run_hash, input_sha = args
    directory = Path(out) / 'streams' / symbol
    if (directory / 'receipt.json').exists():
        return v126.validate_receipt(directory, run_hash, input_sha)
    if directory.exists():
        raise ValueError(f'incomplete output: {directory}')
    before = time.perf_counter()
    if source.digest(Path(path)) != input_sha:
        raise ValueError('input changed since registration')
    raw = pd.read_csv(path, usecols=['ts', 'open', 'high', 'low', 'close', 'volume'])
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts, unit='ms', utc=True))
    if not index.is_unique or not index.is_monotonic_increasing or (index.asi8 % pd.Timedelta(minutes=5).value != 0).any():
        raise ValueError('archive has unordered/duplicate/unaligned clock')
    base = raw[['open', 'high', 'low', 'close', 'volume']].set_axis(index)
    since = START - pd.Timedelta(minutes=1500 * 60)
    base = base.loc[(base.index >= since) & (base.index + pd.Timedelta(minutes=5) <= END)]
    parts, summary = run_stream(base, symbol, meta)
    directory.mkdir(parents=True)
    for name, frame in parts.items():
        frame.to_csv(directory / f'{name}.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
    receipt = {'symbol': symbol, 'run_identity': run_hash, 'input_sha256': input_sha, 'summary': summary,
        'files': {f'{name}.csv.gz': source.digest(directory / f'{name}.csv.gz') for name in TABLES},
        'wall_seconds': time.perf_counter() - before, 'status': 'complete'}
    v126.dump(directory / 'receipt.json', receipt)
    return receipt


def run(out, workers=4, symbols=None):
    config = json.loads(CONFIG.read_text())
    code = _local_transitive_python((Path(__file__), Path('yoyo/evaluation/spike_v126_engine.py')))
    declared = (*code, PINE, CONFIG, PLAN, Path('tests/evaluation/test_spike_v127_held_age.py'))
    if not _committed(declared):
        raise ValueError('commit all replay sources/plan/tests before market replay')
    spec = source.ExecutionSpec()
    if (spec.round_trip_cost, spec.arm_r, spec.trail_atr) != (.002, 2., 4.):
        raise ValueError('execution contract changed')
    if (config['arms'] != list(ARMS) or config['held_life_bars'] != HELD_LIFE or config['start'] != START.isoformat()
            or config['end'] != END.isoformat() or config['split'] != SPLIT.isoformat()
            or config['control_seed'] != v126.SEED):
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
    files = {s: allfiles[s] for s in chosen}
    inputs = {s: source.digest(p) for s, p in files.items()}
    if any(inputs[s] != parent['inputs'][s] for s in chosen):
        raise ValueError('archive differs from parent')
    identity = {'config': config, 'inputs': inputs, 'input_paths': {s: str(p) for s, p in files.items()},
        'source': {str(p): source.digest(p) for p in declared}, 'symbols': chosen,
        'metadata_sha256': source.digest(source.EXCHANGE_INFO), 'subset': symbols is not None,
        'python': subprocess.check_output(['.venv/bin/python', '--version'], text=True).strip(),
        'numpy': np.__version__, 'pandas': pd.__version__}
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    out = Path(out)
    if (out / 'identity.json').exists():
        if json.loads((out / 'identity.json').read_text()) != identity:
            raise ValueError('resume identity changed')
    else:
        out.mkdir(parents=True, exist_ok=True)
        v126.dump(out / 'identity.json', identity)
        v126.dump(out / 'started.json', {'run_identity': run_hash, 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'started_at': pd.Timestamp.now(tz='UTC')})
    meta = source.symbol_meta()
    tasks = [(s, str(files[s]), meta[s], str(out), run_hash, inputs[s]) for s in chosen]
    results, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(worker, t): t[0] for t in tasks}
        for future in as_completed(pending):
            symbol = pending[future]
            try:
                result = future.result(); results.append(result)
                print(json.dumps({'completed': len(results), 'total': len(tasks), 'symbol': symbol,
                    'candidates': result['summary']['candidates'], 'seconds': round(result['wall_seconds'], 2)}), flush=True)
            except Exception as exc:
                errors.append({'symbol': symbol, 'error': repr(exc)})
                print(json.dumps(errors[-1]), flush=True)
    totals = {arm: sum(r['summary']['candidates'].get(arm, 0) for r in results) for arm in ARMS}
    v126.dump(out / 'manifest.json', {'complete': not errors and len(results) == len(tasks), 'run_identity': run_hash,
        'symbols': chosen, 'errors': errors, 'receipts': {r['symbol']: source.digest(out / 'streams' / r['symbol'] / 'receipt.json') for r in results},
        'candidate_count': totals, 'native_pine_parity': False, 'training_eligible': False, 'production_eligible': False})
    if errors:
        raise RuntimeError(f'{len(errors)} failed streams')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--symbols', nargs='+')
    args = parser.parse_args()
    run(args.output, args.workers, args.symbols)
