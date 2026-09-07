"""V34 immutable genuine-flow coverage audit; no outcomes, fitting or trading.

Input: named BTCUSDT USD-M 5m bars, 2023-2024 UTC only. Each row uses its own
OHLCV/taker base/quote flow and clocks; hourly coverage counts the same hour's
12 grid points after it closes. No forward prices, interpolation or imputation.

Sources: https://github.com/binance/binance-public-data#futures
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.date_range.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.DataFrame.to_csv.html

Do not reuse fetch_symbol: its 'complete' means some parseable data, not full
calendar coverage. The V33 parser and all legacy cached ZIP bytes are unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.binance_um_archives import (
    BinanceArchiveError, _request_bytes, archive_urls, parse_checksum, parse_month_zip,
)
from yoyo.data.binance_um_flow_archives import FLOW_COLUMNS, parse_flow_month_zip

ROOT = Path(__file__).resolve().parents[2]
REL = Path('experiments/active/exp-btcusdtp-genuine-flow-coverage-20260907-v34')
HERE = ROOT / REL
MONTHS = tuple(f'{year}-{month:02}' for year in (2023, 2024) for month in range(1, 13))
FROZEN = (Path(__file__).relative_to(ROOT), Path('yoyo/data/binance_um_archives.py'),
          Path('yoyo/data/binance_um_flow_archives.py'), REL / 'config.json')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def immutable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise BinanceArchiveError(f'Refusing to overwrite different bytes: {path}')
        return
    with path.open('xb') as stream:
        stream.write(payload)


def save_json(path: Path, obj: object) -> None:
    immutable(path, (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+'\n').encode())


def checked_sources() -> tuple[dict, dict]:
    branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip()
    if branch != 'main':
        raise BinanceArchiveError('main only')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    hashes = {}
    for rel in FROZEN:
        raw = (ROOT / rel).read_bytes()
        if subprocess.check_output(['git', 'show', commit+':'+str(rel)], cwd=ROOT) != raw:
            raise BinanceArchiveError('Commit frozen builder/config before reading market data')
        hashes[str(rel)] = digest(raw)
    config = json.loads((HERE/'config.json').read_text())
    if (config['symbol'], config['interval'], config['start_inclusive'], config['end_exclusive']) != (
            'BTCUSDT', '5m', '2023-01-01T00:00:00Z', '2025-01-01T00:00:00Z'):
        raise BinanceArchiveError('V34 scope changed; create a separately authorized experiment')
    return config, dict(source_commit=commit, source_hashes=hashes)


def grid(month: str) -> pd.DatetimeIndex:
    period = pd.Period(month, freq='M')
    return pd.date_range(pd.Timestamp(period.start_time, tz='UTC'),
                         pd.Timestamp((period+1).start_time, tz='UTC'), freq='5min', inclusive='left')


def coverage(frame: pd.DataFrame, month: str) -> dict:
    """Exact calendar set comparison; validity is independently rechecked here.

    Uses same-bar fields only. No economic labels or rolling predictive feature.
    Hourly completeness is a data flag, not an entry-time feature.
    """
    expected = grid(month)
    times = pd.DatetimeIndex(frame['open_time'])
    if times.has_duplicates or not times.is_monotonic_increasing or times.isna().any():
        raise BinanceArchiveError('duplicate/descending/null calendar key')
    if len(times.difference(expected)):
        raise BinanceArchiveError('unexpected calendar key')
    if frame.isna().any().any():
        raise BinanceArchiveError('null flow field')
    numeric = ['ts', 'open', 'high', 'low', 'close', 'volume', *FLOW_COLUMNS]
    if not np.isfinite(frame[numeric].to_numpy(dtype=float)).all():
        raise BinanceArchiveError('non-finite flow output')
    if not (frame['earliest_available_at'] == frame['open_time'] + pd.Timedelta(minutes=5)).all():
        raise BinanceArchiveError('availability boundary shifted')
    if not (frame['close_time'] == frame['earliest_available_at'] - pd.Timedelta(milliseconds=1)).all():
        raise BinanceArchiveError('inclusive close clock shifted')
    missing = expected.difference(times)
    counts = pd.Series(1, index=times).groupby(times.floor('h')).sum()
    candles = np.sign(frame['close']-frame['open'])
    flow = np.sign(frame['delta_quote_volume'])
    nonzero = (candles != 0) & (flow != 0)
    ratio = (frame['delta_quote_volume']/frame['quote_volume'].replace(0, np.nan)).dropna()
    # Algebraic float roundoff is checked after exact Decimal validations.
    for unit, total in (('base', 'volume'), ('quote', 'quote_volume')):
        if not np.allclose(frame[f'taker_buy_{unit}_volume'] + frame[f'taker_sell_{unit}_volume'],
                           frame[total], rtol=1e-14, atol=1e-12):
            raise BinanceArchiveError('derived flow conservation failed')
        buy, sell = frame[f'taker_buy_{unit}_volume'], frame[f'taker_sell_{unit}_volume']
        delta = frame[f'delta_{unit}_volume']
        # Independent float verification of a Decimal-derived result must scale
        # roundoff to the operands, not the near-zero cancellation result.
        tolerance = 8*np.finfo(float).eps*(abs(buy)+abs(sell)+abs(delta))
        if not (abs((buy-sell)-delta) <= tolerance).all():
            raise BinanceArchiveError('derived delta conservation failed')
    # Diagnostic only: float/source decimal rounding may create boundary noise.
    # Do not reject, clip or filter bars using these exact-bound comparisons.
    vwap_diagnostics = {}
    for side, base, quote in (('total', 'volume', 'quote_volume'),
            ('buy', 'taker_buy_base_volume', 'taker_buy_quote_volume'),
            ('sell', 'taker_sell_base_volume', 'taker_sell_quote_volume')):
        vwap = frame[quote] / frame[base].replace(0, np.nan)
        outside = (vwap < frame['low']) | (vwap > frame['high'])
        vwap_diagnostics[side+'_vwap_outside_ohlc_bars'] = int(outside.sum())
    return dict(month=month, status='gapped' if len(missing) else 'complete',
        expected_bars=len(expected), valid_bars=len(frame), missing_bars=len(missing),
        unknown_bars=0, coverage_rate=len(frame)/len(expected),
        missing_open_times=[stamp.isoformat() for stamp in missing],
        first_open=times[0].isoformat() if len(times) else None,
        last_open=times[-1].isoformat() if len(times) else None,
        last_earliest_available_at=frame['earliest_available_at'].iloc[-1].isoformat() if len(frame) else None,
        null_cells=0, duplicate_keys=0, zero_volume_bars=int((frame['volume']==0).sum()),
        expected_hours=len(expected)//12, complete_hours=int((counts==12).sum()),
        incomplete_hours=len(expected)//12-int((counts==12).sum()),
        candle_flow_nonzero_bars=int(nonzero.sum()),
        candle_flow_opposite_bars=int(((candles != flow) & nonzero).sum()),
        delta_quote_positive_bars=int((flow>0).sum()), delta_quote_negative_bars=int((flow<0).sum()),
        delta_quote_zero_bars=int((flow==0).sum()),
        imbalance_min=float(ratio.min()) if len(ratio) else None,
        imbalance_median=float(ratio.median()) if len(ratio) else None,
        imbalance_max=float(ratio.max()) if len(ratio) else None,
        columns=list(frame.columns), dtypes={k:str(v) for k,v in frame.dtypes.items()}, **vwap_diagnostics)


def source_receipt(month: str, output: Path) -> dict:
    url, checksum_url = archive_urls('BTCUSDT', month, '5m')
    receipt = dict(month=month, archive_url=url, checksum_url=checksum_url,
                   request_started_at=pd.Timestamp.now(tz='UTC').isoformat())
    try:
        raw = _request_bytes(checksum_url, retries=3, timeout=25)
        receipt['retrieved_at'] = pd.Timestamp.now(tz='UTC').isoformat()
        if raw is None:
            return dict(receipt, status='unknown', reason='checksum_404')
        sha = parse_checksum(raw, expected_filename=f'BTCUSDT-5m-{month}.zip')
        name = f'BTCUSDT-5m-{month}.zip.CHECKSUM'
        immutable(output/'checksums'/name, raw)
        return dict(receipt, status='verified_checksum', expected_sha256=sha,
                    checksum_file=name, checksum_sha256=digest(raw))
    except BinanceArchiveError as exc:
        return dict(receipt, status='unknown', reason=str(exc))


def sources() -> None:
    config, provenance = checked_sources()
    if (HERE/'source_manifest.json').exists():
        raise BinanceArchiveError('Source manifest already frozen; use audit, not a new retrieval')
    output = ROOT / config['data_output']
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda month: source_receipt(month, output), MONTHS))
    save_json(HERE/'source_manifest.json', dict(**provenance, months=rows,
        config_sha256=digest((HERE/'config.json').read_bytes()),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(), prices_read=False))
    print(json.dumps(dict(checksums_verified=sum(r['status']=='verified_checksum' for r in rows),
                         months=len(rows)), ensure_ascii=False), flush=True)


def audit_month(receipt: dict, config: dict, *, allow_network: bool = True) -> dict:
    month = receipt['month']
    unknown = dict(month=month, expected_bars=len(grid(month)), valid_bars=0,
                   missing_bars=0, unknown_bars=len(grid(month)), coverage_rate=0)
    if receipt['status'] != 'verified_checksum':
        return dict(unknown, status='unknown', reason=receipt['reason'])
    output = ROOT / config['data_output']
    name = f'BTCUSDT-5m-{month}.zip'
    raw_checksum = (output/'checksums'/receipt['checksum_file']).read_bytes()
    if digest(raw_checksum) != receipt['checksum_sha256'] or parse_checksum(
            raw_checksum, expected_filename=name) != receipt['expected_sha256']:
        raise BinanceArchiveError('Frozen checksum receipt drift')
    cached = ROOT / config['raw_cache'] / name
    own_zip = output/'downloads'/name
    if cached.exists():
        payload = cached.read_bytes()
        origin = 'read_only_existing_cache'
    elif own_zip.exists():
        payload = own_zip.read_bytes()
        origin = 'experiment_download'
    else:
        if not allow_network:
            raise BinanceArchiveError('Verification cannot fetch new prices')
        try:
            payload = _request_bytes(receipt['archive_url'], retries=3, timeout=25)
        except BinanceArchiveError as exc:
            return dict(unknown, status='unknown', reason='zip_request_failed: '+str(exc))
        if payload is None:
            return dict(unknown, status='unknown', reason='zip_404')
        origin = 'experiment_download'
    actual_sha = digest(payload)
    if actual_sha != receipt['expected_sha256']:
        return dict(unknown, status='invalid', reason='source_checksum_mismatch', actual_sha256=actual_sha)
    if origin == 'experiment_download':
        immutable(own_zip, payload)
    try:
        frame, parsed = parse_flow_month_zip(payload, symbol='BTCUSDT', month=month,
            expected_sha256=actual_sha, interval='5m')
        base, _ = parse_month_zip(payload, symbol='BTCUSDT', month=month,
            expected_sha256=actual_sha, interval='5m')
        pd.testing.assert_frame_equal(frame[list(base.columns)], base, check_exact=True)
        profile = coverage(frame, month)
        encoded = frame.to_csv(index=False, float_format='%.17g').encode()
        restored = pd.read_csv(io.BytesIO(encoded), float_precision='round_trip',
            dtype={k:str(t) for k,t in frame.dtypes.items() if k not in (
                'open_time', 'close_time', 'earliest_available_at')},
            parse_dates=['open_time', 'close_time', 'earliest_available_at'])
        pd.testing.assert_frame_equal(frame, restored, check_exact=True)
        flow_path = output/'bars'/f'BTCUSDT-5m-{month}-flow.csv'
        immutable(flow_path, encoded)
        if cached.exists() and digest(cached.read_bytes()) != actual_sha:
            raise BinanceArchiveError('Source changed during audit')
        return dict(profile, parser_audit=parsed, source_origin=origin,
            zip_sha256=actual_sha, output_path=str(flow_path.relative_to(ROOT)),
            output_sha256=digest(encoded), output_bytes=len(encoded),
            ohlcv_exact_parity=True, csv_roundtrip_exact=True)
    except (BinanceArchiveError, AssertionError, ValueError) as exc:
        return dict(unknown, status='invalid', reason=str(exc), zip_sha256=actual_sha)


def aggregate(rows: list[dict]) -> dict:
    if tuple(row['month'] for row in rows) != MONTHS:
        raise BinanceArchiveError('Exact 24-month roster required')
    totals = {key:sum(row.get(key, 0) for row in rows) for key in (
        'expected_bars', 'valid_bars', 'missing_bars', 'unknown_bars', 'complete_hours',
        'candle_flow_nonzero_bars', 'candle_flow_opposite_bars', 'zero_volume_bars')}
    if totals['expected_bars'] != 210528 or totals['expected_bars'] != sum(
            totals[k] for k in ('valid_bars', 'missing_bars', 'unknown_bars')):
        raise BinanceArchiveError('Coverage denominator does not reconcile')
    return dict(totals, coverage_rate=totals['valid_bars']/totals['expected_bars'],
        status='partial' if totals['unknown_bars'] else ('gapped' if totals['missing_bars'] else 'complete'),
        months_validated=sum(r['status'] in ('complete','gapped') for r in rows),
        months_complete=sum(r['status']=='complete' for r in rows),
        months_gapped=[r['month'] for r in rows if r['status']=='gapped'],
        months_unknown=[r['month'] for r in rows if r['status'] in ('unknown','invalid')],
        expected_hours=210528//12, unavailable_hours=210528//12-totals['complete_hours'],
        candle_flow_opposite_rate=totals['candle_flow_opposite_bars']/totals['candle_flow_nonzero_bars']
            if totals['candle_flow_nonzero_bars'] else None)


def audit() -> None:
    config, current = checked_sources()
    manifest_raw = (HERE/'source_manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    if current['source_hashes'] != manifest['source_hashes'] or tuple(
            r['month'] for r in manifest['months']) != MONTHS:
        raise BinanceArchiveError('Frozen builder/config/roster drift')
    rows = []
    for receipt in manifest['months']:
        row = audit_month(receipt, config)
        rows.append(row)
        print(json.dumps({k:row[k] for k in ('month','status','valid_bars','missing_bars','unknown_bars')}
                         | ({'reason':row['reason']} if 'reason' in row else {})), flush=True)
    if (HERE/'source_manifest.json').read_bytes() != manifest_raw:
        raise BinanceArchiveError('Source manifest changed during audit')
    result = dict(aggregate(rows), monthly=rows, source_commit=manifest['source_commit'],
        source_hashes=manifest['source_hashes'], source_manifest_sha256=digest(manifest_raw),
        generated_at=manifest['generated_at'], generated_at_semantics='frozen_source_retrieval_time',
        economic_labels_read=False, holdout_consumed=False, live_delivery_verified=False,
        training_eligible=False, production_eligible=False)
    save_json(HERE/'summary.json', result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('monthly','source_hashes')}, ensure_ascii=False), flush=True)


def verify() -> None:
    """Recheck original outputs after a reviewed audit-only code correction.

    Original source_manifest and summary are immutable. Source scope, raw SHA,
    parser and config MUST remain frozen. No new network reads are allowed.
    Record the current verifier commit separately, never relabel the original
    result as having been produced by later code.
    """
    config, current = checked_sources()
    manifest_raw = (HERE/'source_manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    summary_raw = (HERE/'summary.json').read_bytes()
    summary = json.loads(summary_raw)
    if digest(manifest_raw) != summary['source_manifest_sha256']:
        raise BinanceArchiveError('Original manifest drift')
    for rel, expected in manifest['source_hashes'].items():
        original = subprocess.check_output(['git', 'show', manifest['source_commit']+':'+rel], cwd=ROOT)
        if digest(original) != expected:
            raise BinanceArchiveError('Original source lineage drift')
        if rel != str(Path(__file__).relative_to(ROOT)) and current['source_hashes'][rel] != expected:
            raise BinanceArchiveError('Parser/config cannot change in an audit correction')
    if tuple(r['month'] for r in manifest['months']) != MONTHS:
        raise BinanceArchiveError('Original roster drift')
    for receipt in manifest['months']:
        name = f"BTCUSDT-5m-{receipt['month']}.zip"
        if not ((ROOT/config['raw_cache']/name).exists() or
                (ROOT/config['data_output']/'downloads'/name).exists()):
            raise BinanceArchiveError('Verification cannot fetch new prices')
    rows = [audit_month(receipt, config, allow_network=False) for receipt in manifest['months']]
    if rows != summary['monthly'] or any(summary[k] != v for k,v in aggregate(rows).items()):
        differences = [dict(month=a['month'], status=a['status'], reason=a.get('reason', 'monthly_field_difference'))
                       for a,b in zip(rows, summary['monthly']) if a != b]
        raise BinanceArchiveError('Corrected checks disagree: '+json.dumps(differences))
    if (HERE/'summary.json').read_bytes() != summary_raw or (HERE/'source_manifest.json').read_bytes() != manifest_raw:
        raise BinanceArchiveError('Original evidence changed during verification')
    result = dict(status='passed', original_source_commit=manifest['source_commit'],
        verifier_source_commit=current['source_commit'], verifier_source_hashes=current['source_hashes'],
        summary_sha256=digest(summary_raw), source_manifest_sha256=digest(manifest_raw),
        validated_months=len(rows), validated_bars=sum(r['valid_bars'] for r in rows),
        exact_all_monthly_fields=True, original_summary_unchanged=True,
        no_new_network_or_dates=True, generated_at=pd.Timestamp.now(tz='UTC').isoformat())
    save_json(HERE/'verification.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['sources', 'audit', 'verify'])
    args = parser.parse_args()
    {'sources':sources, 'audit':audit, 'verify':verify}[args.phase]()
