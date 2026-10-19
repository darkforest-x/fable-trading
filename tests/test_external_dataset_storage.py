"""Approved external data stays usable without opening arbitrary external paths."""
import hashlib
import json
import os
from pathlib import Path
import plistlib

import pytest

from yoyo.data import external_storage
from yoyo.data.dataset_catalog import DatasetCatalog, build_index, dataset_id, read_market_data

CSV = 'open_time,open,high,low,close,volume,confirm\n2026-01-01T00:00:00Z,1,2,1,2,10,1\n2026-01-01T00:05:00Z,2,3,2,3,11,1\n'


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    mount = tmp_path / 'disk'
    target = mount / 'datasets/history'
    target.mkdir(parents=True)
    file = target / 'binance_um_ETHUSDT_5m_2.csv'
    file.write_text(CSV)
    logical = 'data/crypto/market/kline_binance_um5m'
    (root / logical).parent.mkdir(parents=True)
    (root / logical).symlink_to(target, target_is_directory=True)
    (root / 'data/kline_binance_um5m').symlink_to('crypto/market/kline_binance_um5m', target_is_directory=True)
    home = root / 'data/crypto/_catalog'
    home.mkdir()
    manifest = root / 'data/_storage_migrations/test/hashes.jsonl'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'path': file.name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}) + '\n')
    (home / 'external_locations.json').write_text(json.dumps({'version': 1, 'datasets': {
        logical: {'target': str(target), 'mount': str(mount), 'partition_uuid': 'approved-disk',
                  'manifest': str(manifest.relative_to(root))}}}))
    state = {'mounted': True, 'uuid': 'approved-disk'}
    monkeypatch.setattr(external_storage.os.path, 'ismount', lambda _: state['mounted'])
    monkeypatch.setattr(external_storage.subprocess, 'check_output',
                        lambda *args, **kwargs: plistlib.dumps({'DiskUUID': state['uuid']}))
    return root, target, file, logical, state


def read(root):
    return read_market_data(root=root, dataset_id=dataset_id('data/kline_binance_um5m'),
        symbol='ETHUSDT', timeframe='5m', start='2026-01-01', end='2026-01-01T00:10:00Z')


def test_registered_external_reindex_and_read_keep_dataset_identity(migrated):
    root, target, file, logical, _ = migrated
    build_index(root)
    item = DatasetCatalog(root).detail(dataset_id('data/kline_binance_um5m'))['dataset']
    assert item['storage'] == 'external_managed'
    assert item['path'] == logical and item['file_count'] == 1
    assert read(root).close.tolist() == [2, 3]
    assert DatasetCatalog(root).overview()['summary']['managed_bytes'] == 0


@pytest.mark.parametrize('state_key,value,message', [
    ('mounted', False, '未连接'), ('uuid', 'another-disk', '身份')])
def test_missing_or_replaced_disk_rejects_read_and_preserves_index(migrated, state_key, value, message):
    root, _, _, _, state = migrated
    build_index(root)
    index = root / 'data/crypto/_catalog/index.sqlite3'
    previous = hashlib.sha256(index.read_bytes()).hexdigest()
    state[state_key] = value
    with pytest.raises(ValueError, match=message): read(root)
    with pytest.raises(ValueError, match=message): build_index(root)
    assert hashlib.sha256(index.read_bytes()).hexdigest() == previous


def test_same_size_same_timestamp_content_change_is_rejected(migrated):
    root, _, file, _, _ = migrated
    build_index(root)
    original = file.stat()
    file.write_text(CSV.replace(',1,2,1,2,10,1', ',1,9,1,9,10,1'))
    os.utime(file, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ValueError, match='校验清单'): read(root)


def test_verified_external_file_accepts_timestamp_precision_loss(migrated):
    root, _, file, _, _ = migrated
    stamp = 1_780_000_000_123_456_789
    os.utime(file, ns=(stamp, stamp))
    build_index(root)
    rounded = stamp // 1_000_000_000 * 1_000_000_000
    os.utime(file, ns=(rounded, rounded))
    assert read(root).close.tolist() == [2, 3]


def test_registered_vision_preserves_managed_view_path(migrated):
    root, target, file, logical, state = migrated
    from yoyo.data.dataset_catalog import sources
    source = 'datasets/historical-training-samples'
    (root / source).parent.mkdir()
    (root / source).symlink_to(target, target_is_directory=True)
    config = root / 'data/crypto/_catalog/external_locations.json'
    payload = json.loads(config.read_text())
    payload['datasets'][source] = payload['datasets'].pop(logical)
    config.write_text(json.dumps(payload))
    view = root / 'data/crypto/vision' / dataset_id(source)
    view.parent.mkdir()
    view.symlink_to(root / source, target_is_directory=True)
    item = next(x for x in sources(root) if x['id'] == dataset_id(source))
    assert item['path'] == view.relative_to(root).as_posix()
    build_index(root)
    assert DatasetCatalog(root).file(item['id'], item['path'] + '/' + file.name) == file
    index = root / 'data/crypto/_catalog/index.sqlite3'
    previous = index.read_bytes()
    target.rename(target.with_name('temporarily-unmounted'))
    state['mounted'] = False
    with pytest.raises(ValueError, match='未连接'): build_index(root)
    assert index.read_bytes() == previous


def test_owner_metadata_mode_never_hashes_files(migrated, monkeypatch):
    root, target, file, logical, _ = migrated
    build_index(root)
    key = dataset_id('data/kline_binance_um5m')
    config = root / 'data/crypto/_catalog/external_locations.json'
    payload = json.loads(config.read_text())
    item = payload['datasets'][logical]
    item['verification'] = 'metadata'
    config.write_text(json.dumps(payload))
    (root / item['manifest']).write_text(json.dumps({
        'path': file.name, 'bytes': file.stat().st_size,
        'mtime_ns': file.stat().st_mtime_ns}) + '\n')
    def hashing_forbidden(*args, **kwargs):
        raise AssertionError('Metadata mode must not hash content')
    monkeypatch.setattr(external_storage.hashlib, 'sha256', hashing_forbidden)
    assert DatasetCatalog(root).file(key, logical + '/' + file.name) == file
    file.write_text(CSV + 'changed')
    with pytest.raises(ValueError, match='大小或修改时间'):
        DatasetCatalog(root).file(key, logical + '/' + file.name)


def test_external_symlink_escape_and_unmanifested_files_are_not_indexed(migrated):
    root, target, file, logical, _ = migrated
    outside = target.parent / 'private.csv'
    outside.write_text('private')
    (target / 'escape.csv').symlink_to(outside)
    (target / 'unregistered.csv').write_text(CSV)
    build_index(root)
    key = dataset_id('data/kline_binance_um5m')
    item = DatasetCatalog(root).detail(key)['dataset']
    assert item['file_count'] == 1 and item['missing_count'] == 2
    for name in ('escape.csv', 'unregistered.csv'):
        with pytest.raises(KeyError): DatasetCatalog(root).file(key, logical + '/' + name)
    assert len(read(root)) == 2


def test_removed_registered_file_returns_not_found(migrated):
    root, _, file, logical, _ = migrated
    build_index(root)
    file.unlink()
    with pytest.raises(KeyError):
        DatasetCatalog(root).file(dataset_id('data/kline_binance_um5m'), logical + '/' + file.name)


def test_background_os_permission_denial_keeps_existing_index(migrated, monkeypatch):
    root, _, file, _, _ = migrated
    build_index(root)
    index = root / 'data/crypto/_catalog/index.sqlite3'
    previous = index.read_bytes()
    original_open = Path.open
    def denied(path, *args, **kwargs):
        if path.resolve() == file:
            raise PermissionError('simulated removable-volume privacy restriction')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(ValueError, match='尚未授权'): read(root)
    with pytest.raises(ValueError, match='尚未授权'): build_index(root)
    assert index.read_bytes() == previous
