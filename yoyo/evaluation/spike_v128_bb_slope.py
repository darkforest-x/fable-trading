"""Causal BB-boundary slope stratification of the unchanged SPIKE V12.8 port.

Source: owner's 2026-09-24 ETH15m screenshots: a descending upper boundary
before a short versus a flat lower boundary before a long. BB is SMA200 +/-
2 population standard deviations, not BB20. Features consume close and ATR14
through t-1 for the primary trailing-12-bar OLS slope. Current-bar, 3/24-bar,
and latest prior qualifying compression endpoints are diagnostics, never gates.
For direction d, opposite-boundary movement is d*slope(basis)-slope(halfwidth),
in ATR per bar. This separates trend translation from actual contraction.

Original two-sided signals, independent long joint ledger, next-open fills,
stops, 2R-arm/4ATR trail, reverse exits and 20bp cost use committed references.
No training, Pine/monitor edits, optimized cutoff, or filtered-strategy claim.
Joint native TradingView pivot tie parity remains unverified.
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

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-bb-slope-20260924-v1')
CONFIG = EXP / 'config.json'
TEST = Path('tests/evaluation/test_spike_v128_bb_slope.py')
HTF = {1: 5, 3: 15, 5: 15, 15: 60, 30: 120, 60: 240, 240: 1440}


def ols_slope(values, length):
    """Trailing least-squares slope, no smoothing across a missing value."""
    values = np.asarray(values, float)
    result = np.full(len(values), np.nan)
    if len(values) >= length:
        x = np.arange(length, dtype=float) - (length - 1) / 2
        result[length - 1:] = np.convolve(values, x[::-1], mode='valid') / (x @ x)
    return result


def slope_features(frame, minutes):
    """Use close[200], past width[500], ATR14 and trailing 3/12/24 closed bars.

    Primary pre12 endpoints are t-1; now12 ends at t. Episode12 ends at the
    most recent j in [t-10,t-1] with compressed[j:j-2] all true. At a data gap,
    BB and slopes restart. No future exit or full episode right edge is used.
    """
    gap = parent._data_gap(frame, minutes).to_numpy(bool)
    segment = pd.Series(gap.cumsum(), index=frame.index)
    close = frame.close
    basis = close.groupby(segment).transform(lambda s: s.rolling(200).mean())
    half = close.groupby(segment).transform(lambda s: 2 * s.rolling(200).std(ddof=0))
    width = 2 * half / basis.abs()
    threshold = width.groupby(segment).transform(lambda s: s.shift().rolling(500).quantile(.1))
    compressed = width.le(threshold).to_numpy(bool)
    atr = frame.atr.to_numpy(float)
    raw = {'basis': basis.to_numpy(), 'halfwidth': half.to_numpy(),
           'upper': (basis + half).to_numpy(), 'lower': (basis - half).to_numpy(),
           'compressed': compressed, 'width': width.to_numpy()}
    slopes = {}
    # Leading NaNs after each gap prevent every cross-gap regression window.
    for length in (3, 12, 24):
        m, h = ols_slope(raw['basis'], length) / atr, ols_slope(raw['halfwidth'], length) / atr
        slopes[f'now{length}_basis'] = m
        slopes[f'now{length}_half'] = h
        slopes[f'pre{length}_basis'] = np.r_[np.nan, m[:-1]]
        slopes[f'pre{length}_half'] = np.r_[np.nan, h[:-1]]
    t = np.arange(len(frame))
    endpoint = np.full(len(frame), -1, int)
    run = compressed & np.r_[False, compressed[:-1]] & np.r_[False, False, compressed[:-2]]
    for offset in range(10, 0, -1):
        j = t - offset
        good = (j >= 0) & run[np.maximum(j, 0)]
        endpoint[good] = j[good]
    for part in ('basis', 'half'):
        slopes[f'episode12_{part}'] = np.where(endpoint >= 0, slopes[f'now12_{part}'][np.maximum(endpoint, 0)], np.nan)
    return raw | slopes | {'episode_endpoint': endpoint}


def at_event(values, i, side):
    """Direction-reflected slope; inward contraction is direction independent."""
    row = {}
    for prefix in ('pre12', 'now12', 'pre3', 'pre24', 'episode12'):
        trend = side * values[f'{prefix}_basis'][i]
        inward = -values[f'{prefix}_half'][i]
        row.update({f'{prefix}_trend': trend, f'{prefix}_inward': inward,
                    f'{prefix}_opp': trend + inward})
    row['bb_compressed_at_signal'] = bool(values['compressed'][i])
    row['episode_endpoint_i'] = int(values['episode_endpoint'][i])
    row['bb_width_at_signal'] = values['width'][i]
    return row


def facts_for(bars, base5, asset, tick, minutes):
    """Preserve Pine's 15m H1 SMA60 and 5m M15 EMA120 gates exactly."""
    if minutes == 15:
        return parent.pine_facts(bars, base5, asset, tick)
    if minutes == 5:
        return low.pine_facts(bars, base5, asset, tick, minutes)
    return parent._generic_facts(bars, asset, tick, minutes)


def joints_for(facts, base1, tick, minutes):
    """Use the inherited V12.6 ordered pairing and Pine's automatic HTF map."""
    frame = facts['frame']
    local = parent.line_events(frame.open, frame.high, frame.low, frame.close, frame.atr,
        can_run=facts['can_run'], gap=facts['gap'], tick=tick, confirmed_long=facts['v9_long'],
        parent_high=facts['parent_high'], parent_low=facts['parent_low'], raw_side=facts['side'],
        long_alive=facts['long_alive'], ref_long_exit=facts['ref_long_exit'])
    higher, partial = low.complete_bars(base1, HTF[minutes])
    hf = parent.features(higher)
    hg = parent._data_gap(hf, HTF[minutes]).to_numpy(bool)
    ht = parent.line_events(hf.open, hf.high, hf.low, hf.close, hf.atr,
        can_run=~hg & np.isfinite(hf.atr) & hf.atr.gt(0), gap=hg, tick=tick, htf=True)
    mapped = parent._map_htf(hf, frame, ht.winner_events, minutes, HTF[minutes])
    joints = parent.pair_events(frame.close.to_numpy(), bar_times=frame.index.asi8 // 60_000_000_000,
        chart_breaks=local.events, htf_breaks=mapped, box_id=facts['box']['box_entry'],
        confirmed_long=facts['v9_long'], gap=facts['gap'])
    return joints, {'htf': HTF[minutes], 'partial_htf_buckets': partial,
                    'chart_pivot_ties': local.trace['pivot_tie_counts'], 'htf_pivot_ties': ht.trace['pivot_tie_counts']}


def declared_sources():
    paths = (Path(__file__), CONFIG, TEST, parent.PINE, parent.PINE_V128,
             *_local_transitive_python((Path(__file__),)))
    paths = tuple(dict.fromkeys(paths))
    if not _committed(paths):
        raise ValueError('commit builder, config, tests and imported sources before market construction')
    return {str(p): parent.digest(p) for p in paths}


def prepare_data():
    """Receipt-verify existing OKX files and append a separate confirmed API tail."""
    cfg = json.loads(CONFIG.read_text())
    sources = declared_sources()
    old = low.input_files(low.config())
    root = Path(cfg['data_dir'])
    root.mkdir(parents=True, exist_ok=True)
    receipts = {}
    for symbol in cfg['symbols']:
        target = root / f'{symbol}.json'
        if target.exists():
            r = json.loads(target.read_text())
            if r['end'] != cfg['end']:
                raise ValueError('data endpoint changed')
            for p, sha in r['inputs'].items():
                if parent.digest(Path(p)) != sha:
                    raise ValueError('data input changed')
        else:
            api = low.fetch_recent(symbol, pd.Timestamp(low.config()['end']) - pd.Timedelta(minutes=1),
                                   pd.Timestamp(cfg['end']), root)
            paths = [*old[symbol], Path(api['path'])]
            base = low.load_base(paths, pd.Timestamp(cfg['end']))
            r = {'symbol': symbol, 'end': cfg['end'], 'rows': len(base),
                 'first': base.index[0].isoformat(), 'last': base.index[-1].isoformat(),
                 'gap_count': int((base.index.to_series().diff().dropna() != pd.Timedelta(minutes=1)).sum()),
                 'inputs': {str(p): parent.digest(p) for p in paths}, 'api': api,
                 'builder_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                 'sources': sources, 'generated_at': pd.Timestamp.now(tz='UTC').isoformat()}
            parent.dump(target, r)
        receipts[symbol] = r
        print(json.dumps({'data': symbol, 'rows': r['rows'], 'gaps': r['gap_count']}), flush=True)
    parent.dump(root / 'manifest.json', receipts)


def worker(args):
    symbol, minutes, cfg, output, identity = args
    output = Path(output) / 'streams' / f'{symbol}_{minutes}m'
    output.mkdir(parents=True, exist_ok=True)
    rp = output / 'receipt.json'
    if rp.exists():
        r = json.loads(rp.read_text())
        if r['run_identity'] != identity:
            raise ValueError('run identity changed')
        for name, sha in r['files'].items():
            if parent.digest(output / name) != sha:
                raise ValueError('output changed')
        return r
    began = time.monotonic()
    data = json.loads((Path(cfg['data_dir']) / f'{symbol}.json').read_text())
    paths = [Path(p) for p in data['inputs']]
    for p in paths:
        if parent.digest(p) != data['inputs'][str(p)]:
            raise ValueError('input drift')
    base = low.load_base(paths, pd.Timestamp(cfg['end']))
    bars, partial = low.complete_bars(base, minutes)
    base5, _ = low.complete_bars(base, 5)
    meta = cfg['symbols'][symbol]
    facts = facts_for(bars, base5, meta['asset'], meta['tick'], minutes)
    frame = facts['frame']
    values = slope_features(frame, minutes)
    joined, ji = joints_for(facts, base, meta['tick'], minutes)
    key = f'okx:{symbol}:{minutes}m'
    common = {'venue': 'okx', 'symbol': symbol, 'asset': meta['asset'], 'timeframe_min': minutes}
    prepared = parent.source.prepared_arm(frame, facts['gap'], facts['side'], key, common, minutes, meta['tick'])
    masks = {'v9_both': [(int(i), int(facts['side'][i])) for i in np.flatnonzero(facts['v9'])],
             'joint': [(int(e['joint_i']), 1) for e in joined]}
    trades, statuses, events = [], [], []
    for arm, indices in masks.items():
        candidates = []
        for i, side in indices:
            clock = frame.index[i] + pd.Timedelta(minutes=minutes)
            if clock >= pd.Timestamp(cfg['end']):
                continue
            candidates.append({**common, 'signal_i': i, 'side': side, 'signal_bar_open': frame.index[i],
                'signal_close': clock, 'in_window': clock >= pd.Timestamp(cfg['start']),
                **at_event(values, i, side)})
        t, s = parent.serial(prepared, candidates, arm, key)
        trades.extend(t); statuses.extend(s)
        events.extend({**e, 'arm': arm} for e in candidates if e['in_window'])
    tt = pd.DataFrame(trades)
    cc = parent.matched_controls(prepared, trades, cfg) if trades else pd.DataFrame()
    if len(tt):
        closed = tt.loc[~tt.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
        np.testing.assert_allclose(tt.pre12_opp, tt.pre12_trend + tt.pre12_inward, equal_nan=True)
    tables = {'trades.csv.gz': tt, 'controls.csv.gz': cc,
              'candidates.csv.gz': pd.DataFrame(events), 'statuses.csv.gz': pd.DataFrame(statuses)}
    # Exact source bars for screenshot-window audit; no visual reconstruction inference.
    if symbol == 'ETH_USDT_SWAP' and minutes == 15:
        cases = []
        for name, start, end, side in [('flat_long', '2026-09-09T00:00Z', '2026-09-09T16:00Z', 1),
                                       ('steep_short', '2026-09-23T03:00Z', cfg['end'], -1)]:
            for i in np.flatnonzero((frame.index >= pd.Timestamp(start)) & (frame.index < pd.Timestamp(end))):
                cases.append({'case': name, 'bar_open': frame.index[i], **frame.iloc[i][['open','high','low','close','atr']].to_dict(),
                    'bb_upper': values['upper'][i], 'bb_lower': values['lower'][i],
                    'ordinary_signal': bool(facts['v9'][i]), 'raw_side': int(facts['side'][i]),
                    **at_event(values, i, side)})
        tables['screenshot_windows.csv.gz'] = pd.DataFrame(cases)
    for name, table in tables.items():
        table.to_csv(output / name, index=False, compression={'method': 'gzip', 'mtime': 0})
    r = {'symbol': symbol, 'minutes': minutes, 'run_identity': identity, 'rows': len(frame),
         'first': frame.index[0].isoformat(), 'last': frame.index[-1].isoformat(),
         'partial_buckets': partial, 'gaps': int(facts['gap'].sum()), 'candidates': len(events),
         'trades': len(tt), 'censored': int(tt.censored.sum()) if len(tt) else 0,
         'joint': ji, 'elapsed_seconds': time.monotonic() - began,
         'files': {name: parent.digest(output / name) for name in tables}}
    parent.dump(rp, r)
    return r


def run(output, workers=3, minutes=None):
    cfg = json.loads(CONFIG.read_text()); output = Path(output)
    sources = declared_sources()
    selected = cfg['timeframes'] if minutes is None else minutes
    ident = {'config': cfg, 'code': sources, 'selected': selected,
             'data_manifest_sha256': parent.digest(Path(cfg['data_dir']) / 'manifest.json'),
             'builder_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
    rid = hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    ip = output / 'identity.json'
    if ip.exists() and json.loads(ip.read_text()) != ident:
        raise ValueError('identity changed; use a new output directory')
    parent.dump(ip, ident)
    tasks = [(s, m, cfg, str(output), rid) for s in cfg['symbols'] for m in selected]
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(worker, a): (a[0], a[1]) for a in tasks}
        for future in as_completed(jobs):
            try:
                r = future.result(); receipts.append(r)
                print(json.dumps({'done': len(receipts), 'total': len(tasks), 'symbol': r['symbol'],
                                  'minutes': r['minutes'], 'trades': r['trades'], 'seconds': r['elapsed_seconds']}), flush=True)
            except Exception as exc:
                error = {'task': jobs[future], 'error': repr(exc)}; errors.append(error)
                print(json.dumps(error), flush=True)
    parent.dump(output / 'manifest.json', {'complete': not errors and minutes is None,
        'run_identity': rid, 'errors': errors, 'receipts': receipts,
        'generated_at': pd.Timestamp.now(tz='UTC').isoformat(),
        'training_eligible': False, 'production_eligible': False})
    if errors:
        raise RuntimeError(f'{len(errors)} stream errors')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['data', 'run'])
    parser.add_argument('--output', type=Path, default=EXP / 'run_v1')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--minutes', type=int, nargs='+')
    args = parser.parse_args()
    prepare_data() if args.stage == 'data' else run(args.output, args.workers, args.minutes)
