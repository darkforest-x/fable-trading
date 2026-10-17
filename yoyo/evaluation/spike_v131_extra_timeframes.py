"""Replay the frozen V13 ordinary retest on the missing 30m/4H archive grids.

Owner 2026-10-17 requests per-side worst-20 exclusion diagnostics. Reuse the
registered 638-symbol Binance 5m history, complete UTC resampling, generic V12.8
anchor facts (no 15m-only HTF gate), ordered <=24-bar confirmation, next-open
entry, original stop, 2R/4ATR exits and 20bp cost. Features read only each closed
OHLCV prefix, via the existing causal engine. Warm serial occupancy from Nov2022;
report actual entries from Jan2023. No live universe gate or portfolio simulation.

Controls reuse the original symbol/side/anchor-week/fold/ATR-price-bin draw and
seed, then the same retest; no replacement on failure. Preserve the old Sunday
anchor exclusion. Hashing a trade ID is the frozen RNG, not a content audit.
Source receipts record registered paths, sizes, mtimes and real reads, no hashes.
Worst-20 rankings are descriptive full-history selection; a second diagnostic
ranks only entries AND exits before 2025 and evaluates later entries.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation import spike_v128_retest_entry as retest
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v131_bottom20 import comparison, worst_twenty
from yoyo.evaluation.spike_v131_symbol_summary import metrics

EXP = Path('experiments/active/exp-spike-v131-30m4h-bottom20-20261017-v1')
CONFIG = EXP / 'config.json'
COLS = ['trade_key', 'symbol', 'timeframe_min', 'side', 'arm', 'policy',
        'anchor_i', 'anchor_close', 'signal_i', 'entry_i', 'exit_i', 'entry_time',
        'exit_time', 'entry_price', 'initial_stop', 'initial_risk', 'gross_return',
        'net_return', 'net_r', 'status', 'exit_reason', 'censored']
CONTROL_COLS = ['trade_key', 'symbol', 'timeframe_min', 'side', 'control_sig',
                'control_anchor_close', 'control_entry_time', 'control_exit_time',
                'control_net_r', 'control_net_return', 'control_status', 'eligible']


def provenance(path):
    p = Path(path)
    st = p.stat()
    return dict(path=str(p), realpath=str(p.resolve()), bytes=st.st_size, mtime_ns=st.st_mtime_ns)


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def build_stream(base, symbol, meta, minutes, cfg):
    """Return all candidates, serial trades and original-stratum random draws."""
    bars, partial = parent.complete_bars(base, minutes)
    empty = pd.DataFrame(columns=COLS)
    if len(bars) < 2:
        return empty, empty.copy(), pd.DataFrame(columns=CONTROL_COLS), dict(bars=len(bars), partial=partial)
    tick = float(meta['tick'])
    facts = (parent.facts_for(bars, base, meta['asset'], tick, minutes) if minutes == 15
             else parent._generic_facts(bars, meta['asset'], tick, minutes))
    frame = facts['frame']
    key = f'binance_um:{symbol}:{minutes}m'
    identity = dict(venue='binance_um', symbol=symbol, asset=meta['asset'], timeframe_min=minutes)
    prepared = parent.source.prepared_arm(frame, facts['gap'], facts['side'], key, identity, minutes, tick)
    assert prepared.spec.arm_r == 2 and prepared.spec.trail_atr == 4
    outcomes = []
    for i in np.flatnonzero(facts['v9']):
        stamp = frame.index[i] + pd.Timedelta(minutes=minutes)
        if stamp >= pd.Timestamp(cfg['end']):
            continue
        side = int(facts['side'][i])
        status, result, trace = retest.delayed_attempt(prepared, int(i), side, cfg)
        actual_i = int(trace.get('confirmation_i') or trace['decision_i'])
        event = identity | dict(arm='v9_both', policy='retest', side=side,
            anchor_i=int(i), anchor_close=stamp, signal_i=actual_i, status=status,
            trade_key=f'{key}:v9_both:{stamp.isoformat()}')
        outcomes.append(event if result is None else retest.attach_execution(event, result))
    trades, _ = retest.select_serial(outcomes, 'retest', len(frame))
    # Serial occupancy is established before cropping the reporting window.
    trades = [r for r in trades if pd.Timestamp(cfg['analysis_start']) <= r['entry_time'] < pd.Timestamp(cfg['end'])]
    closed = [r for r in trades if r['status'] == 'closed']
    # Draw against the original anchor rather than the later confirmation.
    anchors = [r | {'signal_i': r['anchor_i']} for r in closed]
    draws = parent.matched_controls(prepared, anchors, cfg) if anchors else pd.DataFrame()
    controls = []
    clock = frame.index + pd.Timedelta(minutes=minutes)
    for c in draws.to_dict('records'):
        chosen = c['control_sig']
        outcome, result = 'empty_stratum', None
        if pd.notna(chosen):
            outcome, result, _ = retest.delayed_attempt(prepared, int(chosen), int(c['side']), cfg)
        anchor_time = None if pd.isna(chosen) else clock[int(chosen)]
        controls.append({k: c[k] for k in ['trade_key', 'symbol', 'timeframe_min', 'side', 'control_sig']} |
            dict(control_anchor_close=anchor_time, control_status=outcome,
                 eligible=bool(result is not None and outcome == 'closed' and anchor_time.dayofweek != 6),
                 control_entry_time=None if result is None else result['entry_time'],
                 control_exit_time=None if result is None else result['exit_time'],
                 control_net_r=None if result is None else result['net_r'],
                 control_net_return=None if result is None else result['net_return']))
    tables = [pd.DataFrame(rows).reindex(columns=cols) for rows, cols in
              [(outcomes, COLS), (trades, COLS), (controls, CONTROL_COLS)]]
    target = tables[1]
    assert not target.trade_key.duplicated().any()
    if closed:
        c = target[target.status.eq('closed')]
        np.testing.assert_allclose(c.gross_return - c.net_return, .002, atol=1e-12, rtol=0)
        np.testing.assert_allclose(c.net_r, c.net_return * c.entry_price / c.initial_risk, atol=1e-10, rtol=0)
    return *tables, dict(bars=len(frame), partial=partial, gaps=int(facts['gap'].sum()),
        first_bar=str(frame.index.min()), last_bar=str(frame.index.max()),
        candidates=len(outcomes), taken=len(trades), closed=len(closed), censored=len(trades)-len(closed))


def worker(args):
    row, meta, cfg, root, minutes_list = args
    started = time.perf_counter()
    symbol = row['symbol']; root = Path(root)
    before = provenance(row['path'])
    raw = pd.read_csv(row['path'], usecols=['ts', 'open', 'high', 'low', 'close', 'volume'])
    assert len(raw) == row['rows'] and int(raw.ts.iloc[0]) == row['first_ms'] and int(raw.ts.iloc[-1]) == row['last_ms']
    assert not raw.ts.duplicated().any() and raw.ts.is_monotonic_increasing and (raw.ts % 300000 == 0).all()
    assert np.isfinite(raw.to_numpy(float)).all()
    base = raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(), unit='ms', utc=True))
    base = base[(base.index >= pd.Timestamp(cfg['warmup_start'])) &
                (base.index + pd.Timedelta(minutes=5) <= pd.Timestamp(cfg['end']))]
    summaries = []
    for minutes in minutes_list:
        directory = root / 'streams' / f'{symbol}_{minutes}m'
        directory.mkdir(parents=True, exist_ok=False)
        candidates, trades, controls, summary = build_stream(base, symbol, meta, minutes, cfg)
        for name, table in [('candidates', candidates), ('trades', trades), ('controls', controls)]:
            table.to_csv(directory / f'{name}.csv.gz', index=False)
        summary |= dict(symbol=symbol, timeframe_min=minutes, input=before)
        dump(directory / 'receipt.json', summary)
        summaries.append(summary)
    assert before == provenance(row['path'])
    return dict(symbol=symbol, seconds=time.perf_counter()-started, summaries=summaries)


def aggregate(root, cfg, receipts):
    trades, controls = [], []
    for r in receipts:
        for s in r['summaries']:
            directory = root / 'streams' / f"{s['symbol']}_{s['timeframe_min']}m"
            trades.append(pd.read_csv(directory / 'trades.csv.gz'))
            controls.append(pd.read_csv(directory / 'controls.csv.gz'))
    all_trades, all_controls = pd.concat(trades, ignore_index=True), pd.concat(controls, ignore_index=True)
    all_trades.to_csv(root / 'trades.csv.gz', index=False)
    all_controls.to_csv(root / 'controls.csv.gz', index=False)
    trades = all_trades[all_trades.status.eq('closed')].copy()
    assert not trades.trade_key.duplicated().any()
    for col in ['entry_time', 'exit_time']:
        trades[col] = pd.to_datetime(trades[col], utc=True)
    controls = all_controls[all_controls.eligible.eq(True)].copy()
    controls['control_entry_time'] = pd.to_datetime(controls.control_entry_time, utc=True)
    keys = ['trade_key', 'symbol', 'timeframe_min', 'side']
    pairs = trades.merge(controls[keys + ['control_net_r', 'control_entry_time']], on=keys, validate='one_to_one')
    cut = pd.Timestamp(cfg['split'])
    groups, rankings, comparisons, bottom = [], [], [], []
    for minutes in cfg['timeframes']:
        for side in [1, -1]:
            part = trades[trades.timeframe_min.eq(minutes) & trades.side.eq(side)]
            paired = pairs[pairs.timeframe_min.eq(minutes) & pairs.side.eq(side)]
            label = dict(timeframe_min=minutes, timeframe={30:'30m',240:'4H'}[minutes], side=side,
                         direction='多头' if side == 1 else '空头')
            rows = [label | dict(symbol=s) | metrics(f) for s, f in part.groupby('symbol')]
            rows.sort(key=lambda r: (-r['sum_net_r'], r['symbol']))
            rankings.extend(r | {'rank': i} for i, r in enumerate(rows, 1))
            early = part[(part.entry_time < cut) & (part.exit_time < cut)]
            later = part[part.entry_time >= cut]
            early_ranked, full_ranked = worst_twenty(early), worst_twenty(part)
            full = comparison(part, paired, full_ranked)
            chronological = comparison(later, paired[paired.control_entry_time >= cut], early_ranked)
            groups.append(label | dict(symbols=len(rows), first_entry=str(part.entry_time.min()),
                last_entry=str(part.entry_time.max()), full_history=full, full_history_bottom20=full_ranked,
                chronology=dict(early_closed=len(early), later_closed=len(later),
                    cross_boundary_excluded=len(part)-len(early)-len(later), early_bottom20=early_ranked,
                    later=chronological)))
            for scope, result, selected in [('full_history_retrospective', full, full_ranked),
                    ('early_rank_later_evaluate', chronological, early_ranked)]:
                comparisons.extend(label | dict(scope=scope, state=state) | result[state]
                                   for state in ['before', 'removed', 'after'])
                bottom.extend(label | dict(scope=scope) | r for r in selected)
    for name, rows in [('symbol_rankings', rankings), ('comparisons', comparisons), ('bottom20_symbols', bottom)]:
        pd.DataFrame(rows).to_csv(root / f'{name}.csv', index=False, encoding='utf-8-sig')
    dump(root / 'result.json', dict(generated_at=datetime.now(timezone.utc).isoformat(), config=cfg,
        streams=sum(len(r['summaries']) for r in receipts), censored=len(all_trades)-len(trades), groups=groups,
        limitations=['Full-history outcome-ranked removal is retrospective, not prospective evidence.',
            'Chronological diagnostic reuses observed history; no blind/forward validation.',
            'Removal filters frozen trades; opposite-side events could be admitted differently in a new filtered serial replay.',
            'Current archived universe/ticks are not point-in-time; no funding or portfolio sizing.',
            'Sparse matched coverage; cumulative R is not account return.']))


def run(output, workers, symbols, minutes):
    cfg = json.loads(CONFIG.read_text())
    assert cfg['round_trip_cost'] == .002 and cfg['timeframes'] == [30, 240]
    paths = sorted(set([Path(__file__), CONFIG, *_local_transitive_python((Path(__file__),))]), key=str)
    for p in paths:
        subprocess.run(['git', 'ls-files', '--error-unmatch', str(p)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', *map(str, paths)], check=True)
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(Path(cfg['input_manifest']).read_text())
    assert manifest['complete'] and not manifest['errors'] and not manifest['failed']
    rows = manifest['streams']; assert len(rows) == 638 and len({r['symbol'] for r in rows}) == 638
    if symbols:
        rows = [r for r in rows if r['symbol'] in symbols]
        assert len(rows) == len(symbols)
    minutes = minutes or cfg['timeframes']
    assert set(minutes) <= {15, 30, 240}
    meta = parent.source.symbol_meta()
    dump(root / 'identity.json', dict(config=cfg, subset=bool(symbols), timeframes=minutes,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        code=[provenance(p) for p in paths], manifest=provenance(cfg['input_manifest']),
        metadata=provenance(parent.source.EXCHANGE_INFO), source_symbols=[r['symbol'] for r in rows]))
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, (r, meta[r['symbol']], cfg, str(root), minutes)): r['symbol'] for r in rows}
        for n, f in enumerate(as_completed(futures), 1):
            try:
                receipts.append(f.result())
            except Exception as exc:
                errors.append(dict(symbol=futures[f], error=repr(exc)))
            if n == 1 or n % 20 == 0 or n == len(rows):
                print(json.dumps(dict(done=n,total=len(rows),errors=len(errors))), flush=True)
    dump(root / 'receipt.json', dict(complete=not errors, subset=bool(symbols), errors=errors,
        generated_at=datetime.now(timezone.utc).isoformat(), receipts=receipts))
    if errors:
        raise RuntimeError(str(errors[:5]))
    if not symbols and minutes == cfg['timeframes']:
        aggregate(root, cfg, receipts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=EXP / 'results_v1')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--minutes', type=int, nargs='+')
    a = parser.parse_args()
    run(a.output, a.workers, a.symbols, a.minutes)
