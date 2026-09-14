"""Receipt-bound orchestration of the preregistered native V1 triple-exit study.

Signals and all admission features use only completed bars at/before decision.
The fixed August-2023 liquidity universe is prepared separately. Historical
ledger identities are never selected using their outcomes. Controls use the
same asset, entry UTC day, timeframe and a fixed signal ATR/price bucket;
no outcome, holding duration or future volatility enters matching.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features, replay, risk_reference
from yoyo.evaluation.spike_native_v1_be05 import (
    CACHE_ROOT, IMMUTABLE_LEDGER, EXPECTED_LEDGER_SHA256, SOURCE_EXP, _frozen_stop,
)
from yoyo.evaluation.spike_v1_triple_exit import ALL_ARMS, replay_native_v1_arms
from yoyo.evaluation.spike_v1_twoyear_allmarkets import _read_utc_source, aggregate, _continuous

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-v1-triple-exit-20260914-v1'
VOL_EDGES = np.array([0, .005, .01, .02, .05, .1, np.inf])
SEED = 20260914


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + '\n')
    tmp.replace(path)


def write_csv(path, frame):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp.gz')
    frame.to_csv(temp, index=False, compression={'method': 'gzip', 'mtime': 0})
    temp.replace(path)


def freeze_check():
    config = json.loads((EXP / 'config.json').read_text())
    for name, expected in config['frozen_sources_sha256'].items():
        if sha(ROOT / name) != expected:
            raise ValueError('frozen source changed: ' + name)
    for name in ['spike_v1_triple_study.py', 'spike_v1_triple_exit.py']:
        path = 'yoyo/evaluation/' + name
        landed = subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT)
        if hashlib.sha256(landed).hexdigest() != sha(ROOT / path):
            raise ValueError('builder must be committed before outcome use: ' + path)
    return config


def catalog_metadata():
    catalog = json.loads((SOURCE_EXP / 'data/catalog.json').read_text())
    listed = set()
    for name, col in [('nasdaqlisted', 'Symbol'), ('otherlisted', 'ACT Symbol')]:
        table = pd.read_csv(EXP / 'metadata' / (name + '.txt'), sep='|', dtype=str)
        listed.update(table.loc[table.get('Test Issue', 'N').eq('N'), col].dropna())
    result = {}
    for row in catalog:
        raw = row.get('raw', {})
        stock = (raw.get('underlyingType') == 'EQUITY' or raw.get('contract_type') == 'stocks'
                 or str(raw.get('instCategory')) == '3')
        asset = str(row['asset'])
        us = stock and asset in listed
        result[row['venue'], row['symbol']] = dict(
            tick=row.get('tick'), base_asset=asset, stock_linked=us,
            instrument_type='us_listed_equity' if us else 'equity_us_listing_unknown' if stock
            else 'gold_backed' if asset == 'PAXG' else 'usd_stablecoin' if asset == 'USDC' else 'crypto',
            classification_status='unknown_kept' if stock and not us else 'metadata_identified')
    return result


def dedup_events(events):
    """Select earliest executable event per base asset/UTC entry day, no labels."""
    frame = events.copy()
    frame['entry_time'] = pd.to_datetime(frame.entry_time, utc=True)
    frame['entry_day'] = frame.entry_time.dt.strftime('%Y-%m-%d')
    frame['_venue_rank'] = frame.venue.map({'binance': 0, 'okx': 1, 'gate': 2}).fillna(9)
    ordered = frame.sort_values(['entry_time', 'timeframe_min', '_venue_rank', 'event_id'],
                                ascending=[True, False, True, True])
    if {'entry_price', 'initial_stop'}.issubset(ordered.columns):
        ordered = ordered.loc[ordered.entry_price.gt(ordered.initial_stop)]
    selected = set(ordered.drop_duplicates(['base_asset', 'entry_day']).event_id)
    frame['dedup_keep'] = frame.event_id.isin(selected)
    return frame.drop(columns='_venue_rank')


def cached_streams():
    out = {}
    for receipt in sorted(CACHE_ROOT.glob('*/receipt.csv')):
        table = pd.read_csv(receipt)
        if table.empty:
            continue
        row = table.iloc[0].to_dict()
        cache = receipt.parent / 'control_cache.pkl.gz'
        proof = receipt.parent / 'control_cache.receipt.json'
        if cache.exists() and proof.exists():
            out.setdefault((str(row['venue']), str(row['symbol']), int(row['minutes'])), []).append((cache, proof, row))
    return out


def original_events():
    if sha(IMMUTABLE_LEDGER) != EXPECTED_LEDGER_SHA256:
        raise ValueError('immutable original6253 ledger changed')
    events = pd.read_csv(IMMUTABLE_LEDGER)
    metadata = catalog_metadata()
    rows = []
    for row in events.to_dict('records'):
        meta = metadata[row['venue'], row['symbol']]
        row.update(meta)
        if not np.isfinite(float(meta['tick'])) or float(meta['tick']) <= 0:
            raise ValueError('source ledger lacks verified positive tick')
        row['initial_stop'] = _frozen_stop(row['signal_close'], row['reference_signal_risk'], float(meta['tick']))
        row['side'] = 1
        rows.append(row)
    return dedup_events(pd.DataFrame(rows))


def original_bars(key, events, cache_index):
    venue, symbol, minutes = key
    proof = SOURCE_EXP / f'results/covered_ledgers/{venue}/{symbol}_{minutes}m.csv.receipt.json'
    receipt = json.loads(proof.read_text())
    expected_hash = receipt['source_sha256']
    pieces = []
    for cache, cached_proof, stream in cache_index.get(key, []):
        binding = json.loads(cached_proof.read_text())
        if (str(stream['source_sha256']) != expected_hash or binding.get('source_sha256') != expected_hash):
            continue
        if float(stream['tick']) != float(events.tick.iloc[0]):
            continue
        if sha(cache) != binding['cache_sha256']:
            raise ValueError('featured cache changed: ' + str(cache))
        item = pd.read_pickle(cache, compression='gzip')
        bars = item['bars']
        bars.index = pd.to_datetime(bars.index, utc=True)
        pieces.extend(_continuous(bars, minutes))
    signals = set(pd.to_datetime(events.signal_bar_open, utc=True))
    covered = {t for p in pieces for t in signals if t in p.index}
    if covered == signals:
        return pieces, dict(source='verified_feature_cache', source_sha256=expected_hash)
    candidates = [SOURCE_EXP / f'data/market_receipts/{venue}/{symbol}.json',
                  SOURCE_EXP / f'data/gate_timeframe_receipts/{symbol}_{minutes}m.json']
    for path in candidates:
        if not path.exists():
            continue
        r = json.loads(path.read_text())
        data_path = Path(r.get('path', ''))
        if not data_path.is_file() or sha(data_path) != expected_hash:
            continue
        source = _read_utc_source(str(data_path))
        native = 'gate_timeframe_receipts' in str(path)
        frame = source if native else aggregate(source, minutes)
        pieces = [features(x) for x in _continuous(frame, minutes) if len(x) >= 341]
        return pieces, dict(source='verified_original_ohlcv', source_sha256=expected_hash, source_path=str(data_path))
    raise ValueError('no hash-bound source for all events: ' + str(key))


def native_events(bars, *, venue, symbol, asset, minutes, tick, start, end):
    """Generate native V1 admissions, preserving its own one-path state."""
    signals = replay(bars, tick)
    rows = []
    for stamp, signal in signals.loc[signals.burst].iterrows():
        i = bars.index.get_loc(stamp)
        if i + 1 >= len(bars) or not (start <= bars.index[i + 1] < end):
            continue
        rows.append(dict(event_id=hashlib.sha256(f'{venue}|{symbol}|{minutes}|{stamp.isoformat()}'.encode()).hexdigest(),
                         venue=venue, symbol=symbol, asset=asset, base_asset=asset, timeframe_min=minutes,
                         side=1, signal_bar_open=stamp, signal_close=float(bars.close.iloc[i]),
                         reference_signal_risk=float(signal.risk), initial_stop=float(signal.initial_stop),
                         entry_time=bars.index[i + 1], entry_price=float(bars.open.iloc[i + 1]), volume_ratio=float(bars.rv.iloc[i]),
                         tr_atr_expansion=float(bars.expansion.iloc[i]), tick=tick,
                         stock_linked=False, instrument_type='crypto'))
    return pd.DataFrame(rows)


def control_events(bars, events, tick, maximum=20):
    """Same-day causal-volatility matching; exclude the signal's own entry bar."""
    rv = bars.rv.to_numpy(float)
    atr = bars.atr.to_numpy(float)
    closes = bars.close.to_numpy(float)
    low5 = bars.recentLow.to_numpy(float) if 'recentLow' in bars else bars.low.rolling(5).min().to_numpy(float)
    ready = np.isfinite(atr) & np.isfinite(low5) & np.isfinite(rv)
    if 'ready' in bars:
        ready &= bars.ready.fillna(False).to_numpy(bool)
    bucket = np.searchsorted(VOL_EDGES, atr / closes, side='right') - 1
    entry_days = (bars.index + pd.Timedelta(minutes=int(events.timeframe_min.iloc[0]))).strftime('%Y-%m-%d')
    pools = {}
    for i in np.flatnonzero(ready[:-1]):
        pools.setdefault((entry_days[i], int(bucket[i])), []).append(i)
    rows, audit = [], []
    for event in events.to_dict('records'):
        stamp = pd.Timestamp(event['signal_bar_open'])
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize('UTC')
        position = bars.index.get_loc(stamp)
        pool = pools.get((entry_days[position], int(bucket[position])), [])
        pool = [i for i in pool if i != position]
        pool.sort(key=lambda i: hashlib.sha256(f'{SEED}|{event["event_id"]}|{bars.index[i].isoformat()}'.encode()).digest())
        accepted = []
        for i in pool:
            ref = risk_reference(1, closes[i], low5[i], atr[i], tick=tick)
            if ref.valid and float(bars.open.iloc[i + 1]) > ref.stop:
                accepted.append((i, ref))
            if len(accepted) >= maximum:
                break
        audit.append(dict(event_id=event['event_id'], eligible_candidates=len(pool), selected_controls=len(accepted),
                          volatility_bucket=int(bucket[position]), entry_day=entry_days[position]))
        for i, ref in accepted:
            rows.append(dict(event_id=hashlib.sha256(f'control|{event["event_id"]}|{bars.index[i].isoformat()}'.encode()).hexdigest(),
                             parent_event_id=event['event_id'], signal_bar_open=bars.index[i], signal_close=closes[i],
                             reference_signal_risk=ref.risk, initial_stop=ref.stop, volume_ratio=rv[i], side=1,
                             base_asset=event['base_asset'], stock_linked=event.get('stock_linked', False),
                             timeframe_min=event['timeframe_min'], venue=event['venue'], symbol=event['symbol'],
                             entry_day=entry_days[i], dedup_keep=event['dedup_keep']))
    return pd.DataFrame(rows), pd.DataFrame(audit)


def attach_metadata(outcomes, events):
    if outcomes.empty:
        return outcomes
    columns = [c for c in ['event_id', 'parent_event_id', 'venue', 'symbol', 'asset', 'base_asset', 'timeframe_min',
                           'dedup_keep', 'entry_day', 'stock_linked', 'instrument_type', 'volume_ratio',
                           'tr_atr_expansion'] if c in events]
    return outcomes.merge(events[columns], on='event_id', how='left', validate='many_to_one')


def parity_audit(outcomes, expected):
    got = outcomes.loc[outcomes.arm.eq('baseline')].set_index('event_id')
    errors = []
    for row in expected.to_dict('records'):
        if row['event_id'] not in got.index:
            errors.append(dict(event_id=row['event_id'], field='missing_baseline'))
            continue
        actual = got.loc[row['event_id']]
        for field in ['entry_price', 'exit_price', 'net_r', 'risk_fraction_at_entry', 'gross_return', 'net_return']:
            if not np.isclose(float(actual[field]), float(row[field]), rtol=1e-8, atol=1e-9, equal_nan=True):
                errors.append(dict(event_id=row['event_id'], field=field, expected=row[field], actual=actual[field]))
        for field in ['entry_time', 'exit_time']:
            if pd.Timestamp(actual[field]) != pd.Timestamp(row[field]):
                errors.append(dict(event_id=row['event_id'], field=field, expected=str(row[field]), actual=str(actual[field])))
        a = str(actual.exit_reason).replace('protective_stop_gap', 'protective_stop').replace('entry_gap_through_initial_stop', 'entry_gap_through_stop')
        if a != row['exit_reason'] or bool(actual.censored) != bool(row['censored']):
            errors.append(dict(event_id=row['event_id'], field='exit_status', expected=row['exit_reason'], actual=a))
    return errors


def run_stream(cohort, key, events, pieces, source, *, end, controls=True):
    label = hashlib.sha256('|'.join(map(str, key)).encode()).hexdigest()[:16]
    folder = EXP / 'results' / cohort / 'streams' / label
    contract = dict(key=list(key), source=source, end=str(end), controls=controls, engine_sha256=sha(Path(__file__).with_name('spike_v1_triple_exit.py')),
                    runner_sha256=sha(__file__), events_sha256=hashlib.sha256(events.to_csv(index=False).encode()).hexdigest())
    receipt = folder / 'receipt.json'
    if receipt.exists() and json.loads(receipt.read_text()).get('contract') == contract:
        return
    folder.mkdir(parents=True, exist_ok=True)
    outcomes, controls_out, admissions, match_audits, failures = [], [], [], [], []
    seen = set()
    for bars in pieces:
        bars = bars.loc[bars.index < end]
        chosen = events.loc[pd.to_datetime(events.signal_bar_open, utc=True).isin(bars.index)].copy()
        if chosen.empty:
            continue
        chosen = chosen.loc[~chosen.event_id.isin(seen)]
        if chosen.empty:
            continue
        seen.update(chosen.event_id)
        result = replay_native_v1_arms(bars, chosen, tick=float(events.tick.iloc[0]), arms=ALL_ARMS, end=end, store_paths=False)
        outcomes.append(attach_metadata(result.outcomes, chosen)); admissions.append(result.admission)
        if cohort == 'original6253':
            failures.extend(parity_audit(result.outcomes, chosen))
        # Controls are generated before observing outcomes. Their filtered
        # arms pass the exact same entry filters in the shared replay engine.
        if controls:
            random, audit = control_events(bars, chosen, float(events.tick.iloc[0]))
            match_audits.append(audit)
            if len(random):
                rr = replay_native_v1_arms(bars, random, tick=float(events.tick.iloc[0]),
                                           arms=('baseline', 'triple', 'filtered_triple'), end=end, store_paths=False)
                controls_out.append(attach_metadata(rr.outcomes, random))
    missing = set(events.event_id) - seen
    failures.extend(dict(event_id=x, field='signal_source_missing') for x in sorted(missing))
    if outcomes:
        write_csv(folder / 'outcomes.csv.gz', pd.concat(outcomes, ignore_index=True))
    if admissions:
        write_csv(folder / 'admission.csv.gz', pd.concat(admissions, ignore_index=True))
    if controls_out:
        write_csv(folder / 'controls.csv.gz', pd.concat(controls_out, ignore_index=True))
    if match_audits:
        write_csv(folder / 'matching.csv.gz', pd.concat(match_audits, ignore_index=True))
    atomic_json(receipt, dict(contract=contract, expected_events=len(events), seen_events=len(seen), parity_errors=failures))
    if failures:
        raise AssertionError(f'{key}: parity/source failure; inspect {receipt}')


def run_original(limit=None, controls=True):
    freeze_check()
    events = original_events()
    write_csv(EXP / 'results/original6253/events.csv.gz', events)
    cache = cached_streams()
    grouped = list(events.groupby(['venue', 'symbol', 'timeframe_min'], sort=True))
    for number, (key, sample) in enumerate(grouped[:limit] if limit else grouped, 1):
        started = time.monotonic()
        pieces, source = original_bars(key, sample, cache)
        run_stream('original6253', key, sample, pieces, source, end=pd.Timestamp('2026-09-10T00:00:00Z'), controls=controls)
        print('original6253', number, len(grouped), key, len(sample), round(time.monotonic()-started, 2), flush=True)


def prepare_top20():
    config = freeze_check()
    path = EXP / 'data/stream_manifest.json'
    manifest = json.loads(path.read_text())
    start, end = pd.Timestamp(config['start']), pd.Timestamp(config['end_exclusive'])
    folder = EXP / 'results/top20/prepared'; folder.mkdir(parents=True, exist_ok=True)
    registry, all_events = [], []
    for stream in manifest['streams']:
        key = (stream['symbol'], int(stream['minutes']))
        row = dict(stream)
        if not stream.get('tick_size') or not stream.get('normalized_path'):
            registry.append(row | dict(replay_status='unavailable_tick_or_data', prepared_segments=[]))
            continue
        source = Path(stream['normalized_path'])
        if sha(source) != stream['normalized_sha256']:
            raise ValueError('normalized archive source changed: ' + str(source))
        bars = pd.read_csv(source)
        bars.index = pd.to_datetime(bars.pop('open_time'), utc=True)
        bars = bars.loc[bars.index < end]
        paths, n = [], 0
        for number, segment in enumerate(_continuous(bars, key[1])):
            if len(segment) < 341:
                continue
            featured = features(segment)
            events = native_events(featured, venue='binance', symbol=key[0], asset=stream['asset'],
                                   minutes=key[1], tick=float(stream['tick_size']), start=start, end=end)
            p = folder / f'{key[0]}_{key[1]}m_{number}.pkl.gz'
            featured.to_pickle(p, compression='gzip')
            paths.append(dict(path=str(p), sha256=sha(p), bars=len(featured)))
            if len(events):
                all_events.append(events); n += len(events)
        registry.append(row | dict(replay_status='prepared', prepared_segments=paths, native_events=n))
        print('top20_prepare', key, n, flush=True)
    events = dedup_events(pd.concat(all_events, ignore_index=True)) if all_events else pd.DataFrame()
    write_csv(EXP / 'results/top20/events.csv.gz', events)
    atomic_json(EXP / 'results/top20/prepared_manifest.json', dict(source_manifest_sha256=sha(path), streams=registry))


def run_top20(limit=None, controls=True):
    config = freeze_check()
    manifest_path = EXP / 'results/top20/prepared_manifest.json'
    if not manifest_path.exists():
        prepare_top20()
    manifest = json.loads(manifest_path.read_text())
    if manifest['source_manifest_sha256'] != sha(EXP / 'data/stream_manifest.json'):
        raise ValueError('prepared inputs bind a different archive manifest')
    events = pd.read_csv(EXP / 'results/top20/events.csv.gz')
    completed = 0
    for stream in manifest['streams']:
        if stream['replay_status'] != 'prepared':
            continue
        chosen = events.loc[events.symbol.eq(stream['symbol']) & events.timeframe_min.eq(stream['minutes'])]
        if chosen.empty:
            continue
        pieces = []
        for item in stream['prepared_segments']:
            if sha(item['path']) != item['sha256']:
                raise ValueError('prepared bars hash mismatch')
            pieces.append(pd.read_pickle(item['path'], compression='gzip'))
        run_stream('top20', ('binance', stream['symbol'], stream['minutes']), chosen, pieces,
                   dict(source='binance_native_monthly_zip', source_sha256=stream['source_sha256']),
                   end=pd.Timestamp(config['end_exclusive']), controls=controls)
        completed += 1
        print('top20_replay', completed, stream['symbol'], stream['minutes'], len(chosen), flush=True)
        if limit and completed >= limit:
            break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('cohort', choices=['original6253', 'top20', 'prepare_top20'])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--no-controls', action='store_true')
    args = parser.parse_args()
    if args.cohort == 'original6253':
        run_original(args.limit, controls=not args.no_controls)
    elif args.cohort == 'top20':
        run_top20(args.limit, controls=not args.no_controls)
    else:
        prepare_top20()


if __name__ == '__main__':
    main()
