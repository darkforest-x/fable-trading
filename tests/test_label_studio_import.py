"""Blank Label Studio import contract, with all server calls replaced in memory."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import pytest
from yoyo.datasets import label_studio_import as importer


@pytest.fixture
def server(tmp_path, monkeypatch):
    root, data_root = tmp_path / 'reports', tmp_path / 'datasets' / 'pack'
    (root / 'label_studio').mkdir(parents=True); data_root.mkdir(parents=True)
    (root / 'label_studio' / 'pack').symlink_to(data_root, target_is_directory=True)
    state = SimpleNamespace(root=root, tasks=[], rows=[], stores=[], calls=[], probes=[], corrupt=False,
                            projects=[{'id': 1, 'title': 'metadata-only', 'label_config': '<View />'}])
    for index in range(3):
        data = {'review_id': f'r{index}'}
        for field in ('image', 'future_image', 'thumbnail'):
            path = data_root / field / f'{index}.png'; path.parent.mkdir(exist_ok=True)
            blob = f'{field}-{index}'.encode(); path.write_bytes(blob)
            data[field] = f'/data/local-files/?d=label_studio/pack/{field}/{index}.png'
            data['future_sha256' if field == 'future_image' else field + '_sha256'] = hashlib.sha256(blob).hexdigest()
        state.tasks.append({'data': data})
    tasks_file, config_file, receipt = (tmp_path / name for name in ('tasks.json', 'config.xml', 'receipt.json'))
    tasks_file.write_text(json.dumps(state.tasks))
    config_file.write_text('<View><Image name="i" value="$image"/><Image name="f" value="$future_image"/></View>')

    def api(_session, method, path, payload=None):
        state.calls.append((method, path, payload))
        if path.startswith(('/api/projects/validate', '/api/storages/localfiles/validate')): return {}
        if method == 'GET' and path.startswith('/api/projects?'): return {'count': len(state.projects), 'results': state.projects}
        if method == 'POST' and path == '/api/projects':
            project = {'id': 42, **payload}; state.projects.append(project); return project
        if method == 'GET' and path.startswith('/api/tasks?'):
            assert 'project=42' in path and 'fields=task_only' in path and 'resolve_uri=false' in path
            return {'total': len(state.rows), 'tasks': state.rows}
        if method == 'GET' and path.startswith('/api/storages/localfiles?'): return list(state.stores)
        if method == 'POST' and path == '/api/storages/localfiles':
            assert str(root) in payload['path']; state.stores.append(payload); return {'id': len(state.stores), **payload}
        if method == 'POST' and '/import?' in path:
            ids = []
            for task in payload:
                assert set(task) == {'data'}
                row = {'id': len(state.rows) + 100, 'data': task['data'], 'total_predictions': 0, 'total_annotations': 0}
                state.rows.append(row); ids.append(row['id'])
            return {'task_count': len(payload), 'task_ids': ids}
        raise AssertionError(f'Unexpected API call: {method} {path}')

    def open_image(url, **_kwargs):
        path = root / parse_qs(urlsplit(url).query)['d'][0]
        assert str(path.parent) in {store['path'] for store in state.stores}
        state.probes.append(path)
        return nullcontext(SimpleNamespace(status=200, read=lambda: b'corrupt' if state.corrupt else path.read_bytes()))

    monkeypatch.setattr(importer, 'session', lambda: (SimpleNamespace(open=open_image), ''))
    monkeypatch.setattr(importer, 'api', api)
    state.run = lambda: importer.import_blank_project('manual', tasks_file, config_file, receipt, root, batch_size=2)
    state.tasks_file, state.receipt = tasks_file, receipt
    return state


def test_reentry_adds_no_duplicates(server):
    first, second = server.run(), server.run()
    assert first['imported'] == 3 and second['imported'] == 0 and second['reused'] == 3
    assert len(server.rows) == 3 and first['task_ids'] == second['task_ids']
    assert sum(method == 'POST' and '/import?' in path for method, path, _ in server.calls) == 2


def test_changed_existing_task_data_is_rejected(server):
    server.run(); server.tasks[0]['data']['note'] = 'changed'
    server.tasks_file.write_text(json.dumps(server.tasks))
    with pytest.raises(ValueError, match='changed task data'): server.run()
    assert len(server.rows) == 3


def test_every_image_field_uses_document_root_symlink(server):
    receipt = server.run()
    assert {row['field'] for row in receipt['image_checks']} == {'image', 'future_image', 'thumbnail'}
    assert len(receipt['image_checks']) == 9 and len(server.stores) == 3
    assert all(Path(s['path']).exists() and server.root not in Path(s['path']).resolve().parents for s in server.stores)


def test_http_image_sha_mismatch_never_writes_success_receipt(server):
    server.corrupt = True
    with pytest.raises(ValueError, match='SHA-256'): server.run()
    assert not server.receipt.exists()


def test_blank_import_rejects_predictions_before_any_api_call(server):
    server.tasks[0]['predictions'] = []
    server.tasks_file.write_text(json.dumps(server.tasks))
    with pytest.raises(ValueError, match='never annotations or predictions'): server.run()
    assert not server.calls and not server.rows
