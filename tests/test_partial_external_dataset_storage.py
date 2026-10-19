"""Partial cold migration preserves local metadata and real catalog reads.

All fixtures use metadata verification. Content hashing is forbidden, including
the reindex path, so the tests reflect the owner's migration constraint.
"""
import json
from pathlib import Path
import plistlib
import subprocess
from types import SimpleNamespace

import pytest

from yoyo.data import external_storage as storage
from yoyo.data.dataset_catalog import DatasetCatalog, build_index, dataset_id

CSV = 'open_time,open,high,low,close,volume\n2026-01-01T00:00:00Z,1,2,1,2,10\n'


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root, mount = tmp_path / 'repo', tmp_path / 'disk'
    root.mkdir(); mount.mkdir()
    state = {'mounted': True, 'uuid': 'disk-id'}
    monkeypatch.setattr(storage.os.path, 'ismount', lambda _: state['mounted'])
    monkeypatch.setattr(storage.subprocess, 'check_output',
                        lambda *a, **kw: plistlib.dumps({'DiskUUID': state['uuid']}))
    def forbidden(*a, **kw):
        raise AssertionError('Content hashing is forbidden')
    monkeypatch.setattr(storage, 'hashlib', SimpleNamespace(sha256=forbidden))
    return root, mount, state


def migrate(root, mount, relative):
    source = root / relative
    target = mount / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    source.symlink_to(target, target_is_directory=target.is_dir())
    files = list(target.rglob('*')) if target.is_dir() else [target]
    manifest = root / 'data/_storage_migrations/test' / (relative.replace('/', '_') + '.jsonl')
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(''.join(json.dumps({
        'path': str(p.relative_to(target)), 'bytes': p.stat().st_size,
        'mtime_ns': p.stat().st_mtime_ns}) + '\n' for p in files if p.is_file()))
    config = root / 'data/crypto/_catalog/external_locations.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(config.read_text()) if config.exists() else {'version': 1, 'datasets': {}}
    payload['datasets'][relative] = {
        'target': str(target), 'mount': str(mount), 'partition_uuid': 'disk-id',
        'manifest': str(manifest.relative_to(root)), 'verification': 'metadata'}
    config.write_text(json.dumps(payload))
    return target


@pytest.mark.parametrize('source', ['data/research/history', 'data/altcoin_history',
                                    'archive/consolidated/old-research/datasets/history',
                                    'experiments/active/old-research/inputs',
                                    'experiments/active/old-research/data',
                                    'analysis/output/old-research/kline_snapshot'])
def test_research_directories_keep_identity_and_readability(environment, source):
    root, mount, _ = environment
    original = root / source
    original.mkdir(parents=True)
    (original / 'okx_ETH_USDT_SWAP_5m.csv').write_text(CSV)
    build_index(root)
    key = dataset_id(source)
    before = DatasetCatalog(root).detail(key)['dataset']
    target = migrate(root, mount, source)
    build_index(root)
    after = DatasetCatalog(root).detail(key)['dataset']
    assert after['id'] == before['id'] and after['path'] == before['path']
    assert after['storage'] == 'external_managed'
    assert DatasetCatalog(root).file(key, source + '/okx_ETH_USDT_SWAP_5m.csv').parent == target


@pytest.mark.parametrize('image_dir', ['images', 'review/original/public/images'])
def test_partial_images_and_single_manifest_survive_reindex_and_offline(environment, image_dir):
    root, mount, state = environment
    source = 'datasets/with-local-labels'
    path = root / source
    (path / image_dir / 'train').mkdir(parents=True)
    (path / image_dir / 'train/a.png').write_bytes(b'fixture image')
    (path / 'labels').mkdir()
    (path / 'labels/a.txt').write_text('0 0.5 0.5 0.2 0.2\n')
    (path / 'data.yaml').write_text('train: images/train\n')
    (path / 'manifest.jsonl').write_text('{"image":"images/train/a.png"}\n')
    key = dataset_id(source)
    view = root / 'data/crypto/vision' / key
    view.parent.mkdir(parents=True)
    view.symlink_to(path, target_is_directory=True)
    build_index(root)
    before = DatasetCatalog(root).detail(key)['dataset']
    image_target = migrate(root, mount, source + '/' + image_dir)
    manifest_target = migrate(root, mount, source + '/manifest.jsonl')
    build_index(root)
    catalog = DatasetCatalog(root)
    after = catalog.detail(key)['dataset']
    assert after['path'] == before['path'] and after['file_count'] == before['file_count'] == 4
    assert after['storage'] == 'partially_external_managed'
    assert catalog.file(key, before['path'] + '/' + image_dir + '/train/a.png') == image_target / 'train/a.png'
    assert catalog.file(key, before['path'] + '/manifest.jsonl') == manifest_target
    assert catalog.file(key, before['path'] + '/labels/a.txt') == path / 'labels/a.txt'
    assert not (path / 'labels').is_symlink() and not (path / 'data.yaml').is_symlink()
    index = root / 'data/crypto/_catalog/index.sqlite3'
    previous = index.read_bytes()
    image_target.rename(image_target.with_name('offline-images'))
    state['mounted'] = False
    with pytest.raises(ValueError, match='未连接'):
        build_index(root)
    assert index.read_bytes() == previous
    # Local labels remain usable while the removable images are offline.
    assert catalog.file(key, before['path'] + '/labels/a.txt').is_file()


def test_partial_migration_does_not_follow_unregistered_symlink(environment):
    root, mount, _ = environment
    source = 'datasets/partial'
    path = root / source
    (path / 'images').mkdir(parents=True)
    (path / 'images/a.png').write_bytes(b'fixture')
    target = migrate(root, mount, source + '/images')
    private = mount / 'private'
    private.mkdir(); (private / 'secret.png').write_bytes(b'not registered')
    (path / 'other').symlink_to(private, target_is_directory=True)
    (target / 'escaped.png').symlink_to(private / 'secret.png')
    build_index(root)
    catalog = DatasetCatalog(root)
    key = dataset_id(source)
    assert catalog.detail(key)['dataset']['file_count'] == 1
    with pytest.raises(KeyError): catalog.file(key, source + '/images/escaped.png')
    with pytest.raises(KeyError): catalog.file(key, source + '/other/secret.png')


@pytest.mark.parametrize('source', ['datasets/ok/../private', 'datasets/ok/.private', 'data/research/../credentials'])
def test_registry_rejects_unsafe_source_paths(environment, source):
    root, mount, _ = environment
    config = root / 'data/crypto/_catalog/external_locations.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'version': 1, 'datasets': {source: {
        'target': str(mount / 'target'), 'mount': str(mount), 'partition_uuid': 'disk-id',
        'manifest': 'data/_storage_migrations/test/manifest.jsonl', 'verification': 'metadata'}}}))
    with pytest.raises(ValueError, match='无效'):
        storage.registrations(root)


@pytest.mark.parametrize('failure', [subprocess.TimeoutExpired('diskutil', 10),
                                   OSError('temporary query failure'),
                                   plistlib.InvalidFileException()])
def test_volume_query_retries_once_without_caching_identity(environment, monkeypatch, failure):
    _, mount, state = environment
    calls = []
    def query(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise failure
        return plistlib.dumps({'DiskUUID': state['uuid']})
    monkeypatch.setattr(storage.subprocess, 'check_output', query)
    item = {'mount': mount, 'partition_uuid': 'disk-id'}
    storage.validate_volume(item)
    assert len(calls) == 2
    state['uuid'] = 'replacement-disk'
    with pytest.raises(ValueError, match='身份与登记不一致'):
        storage.validate_volume(item)
    assert len(calls) == 3


def test_persistent_volume_query_failure_stops_after_two_attempts(environment, monkeypatch):
    _, mount, _ = environment
    calls = []
    def query(*args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired('diskutil', 10)
    monkeypatch.setattr(storage.subprocess, 'check_output', query)
    with pytest.raises(ValueError, match='无法核实'):
        storage.validate_volume({'mount': mount, 'partition_uuid': 'disk-id'})
    assert len(calls) == 2


def test_volume_unplugged_between_queries_is_rejected(environment, monkeypatch):
    _, mount, state = environment
    calls = []
    def query(*args, **kwargs):
        calls.append(args)
        state['mounted'] = False
        raise OSError('unplugged')
    monkeypatch.setattr(storage.subprocess, 'check_output', query)
    with pytest.raises(ValueError, match='未连接'):
        storage.validate_volume({'mount': mount, 'partition_uuid': 'disk-id'})
    assert len(calls) == 1
