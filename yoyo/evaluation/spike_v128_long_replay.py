"""Extend the frozen V12.8 replay using authenticated same-venue local history.

Only the observation horizon changes. Features consume closed bars through each
signal; original fixed exits, serial occupancy, matched random anchors and 20bp
costs are delegated to the existing engines. Chart-hint rendering is omitted.
Source overlaps must agree and missing bars remain missing. The archived current
symbol universe and tick metadata are not a point-in-time historical universe.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation import spike_v128_retest_entry as retest
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v128_recent import (
    complete_bars, _window, _empty, facts_for, line_events, features, _data_gap,
    _map_htf, pair_events, source, serial, matched_controls,
)

COLUMNS = ['ts', 'open', 'high', 'low', 'close', 'volume']
TEST = Path('tests/evaluation/test_spike_v128_long_replay.py')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def merge_history(old, recent, cfg):
    """Join exact timestamp overlaps; compare OHLCV and never synthesize gap bars."""
    for frame in (old, recent):
        if frame.ts.duplicated().any() or not frame.ts.is_monotonic_increasing:
            raise ValueError('source timestamps duplicate or unordered')
        if (frame.ts % 300000 != 0).any():
            raise ValueError('source is not aligned five-minute data')
        if not np.isfinite(frame[COLUMNS].to_numpy()).all():
            raise ValueError('non-finite OHLCV')
    a, b = old.set_index('ts'), recent.set_index('ts')
    overlap = a.index.intersection(b.index)
    if not np.allclose(a.loc[overlap].to_numpy(), b.loc[overlap].to_numpy(), rtol=1e-12, atol=1e-12):
        raise ValueError('same-source OHLCV overlap conflict')
    joined = pd.concat([a, b.loc[~b.index.isin(a.index)]]).sort_index().reset_index()
    start = pd.Timestamp(cfg['warmup_start']).value // 10**6
    end = pd.Timestamp(cfg['end']).value // 10**6
    joined = joined[(joined.ts >= start) & (joined.ts + 300000 <= end)].reset_index(drop=True)
    return joined, len(overlap)


def data_worker(args):
    row, cfg, cfg_sha, output = args
    symbol = row['symbol']; output = Path(output)
    receipt_path = output/'receipts'/f'{symbol}.json'
    inputs = [row['inputs'][0], {'path': row['path'], 'sha256': row['sha256']}]
    for item in inputs:
        if parent.digest(Path(item['path'])) != item['sha256']:
            raise ValueError(f'input hash changed: {symbol}')
    if receipt_path.exists():
        r = json.loads(receipt_path.read_text())
        if r['config_sha256'] != cfg_sha or r['inputs'] != inputs or parent.digest(Path(r['path'])) != r['sha256']:
            raise ValueError('data receipt changed')
        return r
    old, recent = [pd.read_csv(i['path'], usecols=COLUMNS) for i in inputs]
    frame, overlap = merge_history(old, recent, cfg)
    if frame.empty:
        raise ValueError(f'no data for {symbol}')
    path = output/'series'/f'{symbol}.csv.gz'
    frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
    r = {'symbol': symbol, 'path': str(path), 'sha256': parent.digest(path),
         'inputs': inputs, 'config_sha256': cfg_sha, 'status': 'complete',
         'rows': len(frame), 'first_ms': int(frame.ts.iloc[0]), 'last_ms': int(frame.ts.iloc[-1]),
         'gap_count': int((frame.ts.diff().dropna() != 300000).sum()), 'overlap_verified_rows': overlap}
    parent.dump(receipt_path, r)
    return r


def baseline_worker(args):
    """Adapt the old receipt writer inside an isolated process, omitting diagnostics."""
    original = parent.run_stream
    parent.run_stream = ledger_stream
    try:
        return parent.worker(args)
    finally:
        parent.run_stream = original


def declared_sources(config):
    code = _local_transitive_python((Path(__file__), Path(retest.__file__)))
    paths = tuple(dict.fromkeys((Path(config), TEST, parent.PINE, parent.PINE_V128, *code)))
    if not _committed(paths):
        raise ValueError('commit replay code, tests and configuration before market construction')
    return {str(p): parent.digest(p) for p in paths}


def run(config, stage, output, workers=8, symbols=None):
    config, output = Path(config), Path(output)
    cfg = json.loads(config.read_text()); code = declared_sources(config)
    if cfg['timeframes'] != [15, 60] or cfg['round_trip_cost'] != .002:
        raise ValueError('fixed timeframe/cost contract changed')
    if not pd.Timestamp(cfg['warmup_start']) <= pd.Timestamp(cfg['start']) < pd.Timestamp(cfg['split']) < pd.Timestamp(cfg['end']):
        raise ValueError('invalid temporal partition')
    source_path = Path(cfg['source_manifest'] if stage == 'data' else cfg['input_manifest'])
    manifest = json.loads(source_path.read_text())
    if manifest.get('failed'):
        raise ValueError('input has failed streams')
    by_symbol = {r['symbol']: r for r in manifest['streams']}
    if len(by_symbol) != len(manifest['streams']):
        raise ValueError('duplicate manifest symbols')
    universe = cfg.get('symbols') or sorted(by_symbol)
    requested = sorted(symbols or universe)
    if not requested or not set(requested) <= set(universe) <= set(by_symbol):
        raise ValueError('invalid universe')
    subset = symbols is not None
    output.mkdir(parents=True, exist_ok=True)
    identity = {'config': cfg, 'config_sha256': parent.digest(config), 'code': code,
                'symbols': requested, 'timeframes': cfg['timeframes'], 'subset': subset,
                'input_manifest': str(source_path), 'input_manifest_sha256': parent.digest(source_path),
                'inputs': {s: by_symbol[s]['sha256'] for s in requested},
                'symbol_meta_source': {'path': str(parent.source.EXCHANGE_INFO), 'sha256': parent.digest(parent.source.EXCHANGE_INFO)}}
    if stage == 'retest':
        pr = Path(cfg['parent_run']); pi = json.loads((pr/'identity.json').read_text()); pm = json.loads((pr/'manifest.json').read_text())
        if pm['errors'] or fingerprint(pi) != pm['run_identity'] or (not subset and (not pm['complete'] or pi['subset'])):
            raise ValueError('parent incomplete/identity changed')
        for key in ('start', 'split', 'end', 'timeframes', 'round_trip_cost', 'control_seed'):
            if cfg[key] != pi['config'][key]: raise ValueError(f'parent contract changed: {key}')
        for path, sha in pi['code'].items():
            if parent.digest(Path(path)) != sha: raise ValueError('parent source changed')
        if identity['inputs'] != {s: pi['inputs'][s] for s in requested}:
            raise ValueError('parent input selection changed')
        identity |= {'parent_identity_sha256': parent.digest(pr/'identity.json'), 'parent_manifest_sha256': parent.digest(pr/'manifest.json')}
    ip = output/'identity.json'
    if ip.exists() and json.loads(ip.read_text()) != identity:
        raise ValueError('identity changed; preserve old output')
    parent.dump(ip, identity); rid = fingerprint(identity)
    if stage == 'data':
        (output/'series').mkdir(exist_ok=True); (output/'receipts').mkdir(exist_ok=True)
        tasks = [(by_symbol[s], cfg, parent.digest(config), str(output)) for s in requested]
        worker = data_worker
    else:
        (output/'streams').mkdir(exist_ok=True)
        meta = parent.source.symbol_meta()
        if stage == 'baseline':
            worker = baseline_worker
            tasks = [(s, by_symbol[s]['path'], meta[s], str(output), m, cfg, rid, identity['inputs'][s]) for s in requested for m in cfg['timeframes']]
        else:
            worker = retest.worker
            tasks = [(s, m, by_symbol[s]['path'], meta[s], str(output), cfg, rid, pi, pm, pi['inputs'][s]) for s in requested for m in cfg['timeframes']]
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, a): i for i, a in enumerate(tasks)}
        for n, future in enumerate(as_completed(futures), 1):
            try: receipts.append(future.result())
            except Exception as exc: errors.append({'task': futures[future], 'error': repr(exc)})
            if n == 1 or n % 20 == 0 or n == len(tasks):
                print(json.dumps({'stage': stage, 'done': n, 'total': len(tasks), 'errors': len(errors)}), flush=True)
    result = {'complete': not errors and not subset and len(receipts) == len(tasks), 'run_identity': rid, 'errors': errors}
    if stage == 'data':
        result |= {'streams': sorted(receipts, key=lambda r: r['symbol']), 'requested': requested, 'failed': errors,
                   'source_manifest': str(source_path), 'generated_at': pd.Timestamp.now(tz='UTC').isoformat()}
    else:
        result['receipts'] = {f"{r['symbol']}_{r['minutes']}m": parent.digest(output/'streams'/f"{r['symbol']}_{r['minutes']}m"/'receipt.json') for r in receipts}
    parent.dump(output/'manifest.json', result)
    if errors: raise RuntimeError(f'{len(errors)} failed streams, retained in manifest')


def ledger_stream(base5m: pd.DataFrame, symbol: str, meta: dict, minutes: int, cfg: dict) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build one symbol/timeframe event ledger, then evaluate independent serial arms."""
    tick, asset = float(meta["tick"]), str(meta["asset"])
    since = pd.Timestamp(cfg["warmup_start"])
    bars, partial = complete_bars(base5m.loc[base5m.index >= since], minutes)
    higher_minutes = 60 if minutes == 15 else 240
    higher, hpartial = complete_bars(base5m.loc[base5m.index >= since], higher_minutes)
    if len(bars) < 2:
        expected = pd.date_range(pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), freq=f"{minutes}min", inclusive="left")
        actual = bars.index[_window(bars.index, minutes, cfg)] if len(bars) else bars.index
        return _empty(), {"symbol": symbol, "minutes": minutes, "chart_bars_total": len(bars), "higher_bars": len(higher),
                          "window_bars_expected": len(expected), "window_bars_actual": len(actual),
                          "window_gap_count": int(len(expected) - len(actual)), "first": None if not len(bars) else bars.index[0],
                          "last": None if not len(bars) else bars.index[-1], "valid_ready_window_bars": 0,
                          "candidates": 0, "v9_candidates": 0, "joint_candidates": 0,
                          "partial_chart_buckets": partial, "partial_higher_buckets": hpartial, "chart_gaps": 0}
    facts = facts_for(bars, base5m, asset, tick, minutes)
    frame = facts["frame"]
    local = line_events(frame.open, frame.high, frame.low, frame.close, frame.atr, can_run=facts["can_run"], gap=facts["gap"], tick=tick,
                        confirmed_long=facts["v9_long"], parent_high=facts["parent_high"], parent_low=facts["parent_low"],
                        raw_side=facts["side"], long_alive=facts["long_alive"], ref_long_exit=facts["ref_long_exit"])
    hf = features(higher)
    hgap = _data_gap(hf, higher_minutes).to_numpy(bool)
    htf = line_events(hf.open, hf.high, hf.low, hf.close, hf.atr, can_run=~hgap & np.isfinite(hf.atr) & hf.atr.gt(0), gap=hgap,
                      tick=tick, htf=True)
    mapped = _map_htf(hf, frame, htf.winner_events, minutes, higher_minutes)
    chart_clock = frame.index.asi8 // 60_000_000_000
    joints = pair_events(frame.close.to_numpy(), bar_times=chart_clock, chart_breaks=local.events, htf_breaks=mapped,
                         box_id=facts["box"]["box_entry"], confirmed_long=facts["v9_long"], gap=facts["gap"])
    key = f"binance_um:{symbol}:{minutes}m"
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe_min": minutes}
    prepared = source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    in_window = _window(frame.index, minutes, cfg)
    all_events = []
    for i in np.flatnonzero(facts["v9"]):
        signal_close = frame.index[i] + pd.Timedelta(minutes=minutes)
        if signal_close >= pd.Timestamp(cfg["end"]):
            continue
        all_events.append({**identity, "candidate_family": "v9", "signal_i": int(i), "side": int(facts["side"][i]),
                           "signal_bar_open": frame.index[i], "signal_close": signal_close, "joint": False,
                           "in_window": bool(in_window[i])})
    joint_events_rows = []
    for event in joints:
        i = int(event["joint_i"])
        signal_close = frame.index[i] + pd.Timedelta(minutes=minutes)
        if signal_close >= pd.Timestamp(cfg["end"]):
            continue
        joint_events_rows.append({**identity, **event, "candidate_family": "joint", "signal_i": i, "side": 1,
                                  "signal_bar_open": frame.index[i], "signal_close": signal_close, "joint": True,
                                  "in_window": bool(in_window[i])})
    all_events.extend(joint_events_rows)
    all_events.sort(key=lambda x: (x["signal_i"], x["candidate_family"]))
    v9_candidates = [x for x in all_events if x["candidate_family"] == "v9"]
    trades, statuses = [], []
    for arm, events in (("v9_both", v9_candidates), ("joint", joint_events_rows)):
        t, s = serial(prepared, events, arm, key)
        trades.extend(t); statuses.extend(s)
    for row in statuses:
        row["symbol"], row["timeframe_min"] = symbol, minutes
    trade_table = pd.DataFrame(trades)
    if len(trade_table):
        trade_table["censored"] = trade_table["censored"].astype(bool)
        closed = trade_table.loc[~trade_table.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
    controls = matched_controls(prepared, trades, cfg) if trades else pd.DataFrame()
    frames, hints = pd.DataFrame(), pd.DataFrame()
    decisions = [event for event in all_events if event["in_window"]]
    for event in decisions:
        event["arm"] = "v9_both" if event["candidate_family"] == "v9" else "joint"
    expected_window = pd.date_range(pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), freq=f"{minutes}min", inclusive="left")
    actual_window = frame.index[in_window]
    return {"decisions": pd.DataFrame(decisions), "trades": trade_table, "statuses": pd.DataFrame(statuses),
            "controls": controls, "frames": frames, "hints": hints}, {
                "symbol": symbol, "minutes": minutes, "chart_bars_total": len(frame), "higher_bars": len(hf),
                "window_bars_expected": len(expected_window), "window_bars_actual": len(actual_window),
                "window_gap_count": int(len(expected_window) - len(actual_window)),
                "first": None if not len(frame) else frame.index[0], "last": None if not len(frame) else frame.index[-1],
                "valid_ready_window_bars": int((facts["ready"] & ~facts["gap"] & in_window).sum()),
                "candidates": len(decisions), "v9_candidates": sum(event["in_window"] for event in v9_candidates),
                "joint_candidates": sum(event["in_window"] for event in joint_events_rows), "partial_chart_buckets": partial,
                "partial_higher_buckets": hpartial, "chart_gaps": int(facts["gap"].sum()), "hint_diagnostics_omitted": True}



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--stage', choices=['data', 'baseline', 'retest'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--symbols', nargs='+')
    a = parser.parse_args()
    run(a.config, a.stage, a.output, a.workers, a.symbols)
