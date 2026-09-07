"""V34 synthetic calendar, checksum and source-isolation regression tests."""
import hashlib
import io
import zipfile

import pandas as pd
import pytest

from yoyo.data.binance_um_archives import BinanceArchiveError
from yoyo.data.binance_um_flow_archives import parse_flow_month_zip
from yoyo.evaluation import binance_flow_coverage as audit


def frame(month='2024-01', offsets=(0, 1, 2)):
    start = int(pd.Timestamp(month+'-01', tz='UTC').value//1_000_000)
    rows = [[start+i*300000, 101, 102, 99, 100, 10, start+(i+1)*300000-1, 1000, 7, 7, 700, 0] for i in offsets]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr(f'BTCUSDT-5m-{month}.csv', '\n'.join(','.join(map(str,r)) for r in rows)+'\n')
    payload = buffer.getvalue()
    return parse_flow_month_zip(payload, symbol='BTCUSDT', month=month, interval='5m',
        expected_sha256=hashlib.sha256(payload).hexdigest())[0]


def test_calendar_has_24_months_leap_year_and_exclusive_open_boundary():
    assert len(audit.MONTHS) == 24
    assert sum(len(audit.grid(m)) for m in audit.MONTHS) == 210528
    assert len(audit.grid('2024-02')) == 29*288
    last = frame('2024-12', (31*288-1,))
    result = audit.coverage(last, '2024-12')
    assert result['last_earliest_available_at'] == '2025-01-01T00:00:00+00:00'
    assert result['valid_bars'] == 1


@pytest.mark.parametrize('removed', [0, 4400, 8927])
def test_missing_first_middle_last_exactly_counted_not_filled(removed):
    all_bars = frame(offsets=tuple(i for i in range(8928) if i != removed))
    result = audit.coverage(all_bars, '2024-01')
    assert result['status'] == 'gapped'
    assert result['missing_bars'] == 1
    assert result['complete_hours'] == 743
    assert result['missing_open_times'] == [audit.grid('2024-01')[removed].isoformat()]
    assert len(all_bars) == 8927


def test_full_calendar_and_flow_color_not_identical():
    result = audit.coverage(frame(offsets=tuple(range(8928))), '2024-01')
    assert result['status'] == 'complete'
    assert result['complete_hours'] == 744
    assert result['candle_flow_opposite_bars'] == result['valid_bars'] == 8928
    assert result['imbalance_median'] == .4
    assert result['total_vwap_outside_ohlc_bars'] == 0


@pytest.mark.parametrize('change', ['duplicate', 'shifted', 'null', 'clock', 'close', 'flow', 'delta', 'inf'])
def test_corrupted_outputs_rejected(change):
    f = frame()
    if change == 'duplicate':
        f = pd.concat([f, f.iloc[-1:]], ignore_index=True)
    elif change == 'shifted':
        f.loc[0, 'open_time'] += pd.Timedelta(seconds=1)
    elif change == 'null':
        f.loc[0, 'quote_volume'] = None
    elif change == 'clock':
        f.loc[0, 'earliest_available_at'] -= pd.Timedelta(milliseconds=1)
    elif change == 'close':
        f.loc[0, 'close_time'] += pd.Timedelta(milliseconds=1)
    elif change == 'flow':
        f.loc[0, 'taker_sell_quote_volume'] = 999
    elif change == 'delta':
        f.loc[0, 'delta_quote_volume'] = 999
    elif change == 'inf':
        f.loc[0, 'quote_volume'] = float('inf')
    with pytest.raises(BinanceArchiveError):
        audit.coverage(f, '2024-01')


def test_unverified_source_does_not_read_cached_prices(monkeypatch):
    monkeypatch.setattr(audit.Path, 'read_bytes', lambda *a: pytest.fail('must not read source'))
    result = audit.audit_month(dict(month='2024-01', status='unknown', reason='checksum_404'), {})
    assert result['status'] == 'unknown'
    assert result['unknown_bars'] == 8928 and result['missing_bars'] == 0


def test_official_missing_checksum_never_replaced_with_self_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, '_request_bytes', lambda *a, **k: None)
    result = audit.source_receipt('2024-01', tmp_path)
    assert result['status'] == 'unknown' and result['reason'] == 'checksum_404'
    assert not list(tmp_path.iterdir())


def test_immutable_writer_allows_identical_replay_only(tmp_path):
    path = tmp_path/'one.json'
    audit.immutable(path, b'first')
    audit.immutable(path, b'first')
    with pytest.raises(BinanceArchiveError, match='overwrite'):
        audit.immutable(path, b'second')
    assert path.read_bytes() == b'first'


def test_checksum_mismatch_keeps_cache_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    checksum = b'0'*64+b'  BTCUSDT-5m-2024-01.zip\n'
    raw = tmp_path/'old'/'BTCUSDT-5m-2024-01.zip'
    audit.immutable(raw, b'not-the-matching-source')
    audit.immutable(tmp_path/'new/checksums/x', checksum)
    receipt = dict(month='2024-01', status='verified_checksum', checksum_file='x',
                   checksum_sha256=audit.digest(checksum), expected_sha256='0'*64)
    result = audit.audit_month(receipt, dict(data_output='new', raw_cache='old'))
    assert result['status'] == 'invalid'
    assert result['reason'] == 'source_checksum_mismatch'
    assert raw.read_bytes() == b'not-the-matching-source'
    assert not (tmp_path/'new/bars').exists()


def test_aggregate_unknown_is_not_zero_volume_or_missing():
    rows = [dict(month=m, status='unknown', valid_bars=0, missing_bars=0,
                 unknown_bars=len(audit.grid(m)), expected_bars=len(audit.grid(m))) for m in audit.MONTHS]
    result = audit.aggregate(rows)
    assert result['unknown_bars'] == 210528
    assert result['missing_bars'] == 0 and result['status'] == 'partial'
    assert result['candle_flow_opposite_rate'] is None
    with pytest.raises(BinanceArchiveError, match='roster'):
        audit.aggregate(rows[:-1])


def valid_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    buffer = io.BytesIO()
    row = '1704067200000,101,102,99,100,10,1704067499999,1000,7,7,700,0\n'
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('BTCUSDT-5m-2024-01.csv', row)
    payload = buffer.getvalue()
    expected = audit.digest(payload)
    checksum = (expected+'  BTCUSDT-5m-2024-01.zip\n').encode()
    audit.immutable(tmp_path/'new/checksums/x', checksum)
    receipt = dict(month='2024-01', status='verified_checksum', checksum_file='x',
        checksum_sha256=audit.digest(checksum), expected_sha256=expected,
        archive_url='https://data.binance.vision/fixture-only')
    return receipt, payload, dict(data_output='new', raw_cache='old')


def test_valid_zip_roundtrip_replay_without_network(tmp_path, monkeypatch):
    receipt, payload, config = valid_receipt(tmp_path, monkeypatch)
    audit.immutable(tmp_path/'old/BTCUSDT-5m-2024-01.zip', payload)
    monkeypatch.setattr(audit, '_request_bytes', lambda *a, **k: pytest.fail('network forbidden'))
    first = audit.audit_month(receipt, config)
    second = audit.audit_month(receipt, config)
    assert first == second
    assert first['status'] == 'gapped' and first['valid_bars'] == 1
    assert first['csv_roundtrip_exact'] and first['ohlcv_exact_parity']
    assert audit.digest((tmp_path/first['output_path']).read_bytes()) == first['output_sha256']


def test_zip_network_error_retains_unknown_month(tmp_path, monkeypatch):
    receipt, _, config = valid_receipt(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise BinanceArchiveError('request failed')
    monkeypatch.setattr(audit, '_request_bytes', fail)
    result = audit.audit_month(receipt, config)
    assert result['status'] == 'unknown'
    assert result['unknown_bars'] == 8928 and result['missing_bars'] == 0
    assert result['reason'].startswith('zip_request_failed')


def test_delta_roundoff_scales_to_operands_not_cancelled_result():
    f = frame()
    f['taker_buy_quote_volume'] = 100000000.01
    f['taker_sell_quote_volume'] = 100000000.0
    f['quote_volume'] = 200000000.01
    f['delta_quote_volume'] = .01
    assert audit.coverage(f, '2024-01')['valid_bars'] == 3
    f.loc[0, 'delta_quote_volume'] = 1
    with pytest.raises(BinanceArchiveError, match='delta'):
        audit.coverage(f, '2024-01')
