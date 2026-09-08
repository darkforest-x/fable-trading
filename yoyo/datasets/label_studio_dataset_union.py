"""Append actual HL2 training proposals to the existing Owner review project.

The Owner requested one review per current positive event on 2026-09-08.
Only new tasks, project presentation settings and its existing default view
are written. Original tasks, predictions, human annotations and drafts are
never patched. Predictions are read through their unfiltered REST endpoint;
LS 1.13.1 TaskSerializer otherwise filters them by project.model_version.
The current queue explicitly selects the HL2 proposal version. LS 1.13.1's
next-task route returns no prelabels for an empty model version; historical
proposals remain stored and require their version when that queue is resumed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from urllib.parse import parse_qs, quote, urlsplit
import xml.etree.ElementTree as ET

from yoyo.datasets import label_studio_import as base
from yoyo.datasets import label_studio_proposal_import as historical

ROOT = Path(__file__).resolve().parents[2]
PROJECT = 77
VIEW = 44
PROTOCOL = 'grade_a_hl2_training_label_review_v1'
PACK = ROOT / 'datasets/grade_a_hl2_review_20260908_v1'
OLD_PACK = ROOT / 'datasets/owner_box_refinement_20260907_v1'
CONFIG = ROOT / 'configs/labelstudio/owner_unified_future40.xml'
TITLE = 'YOLO 人工审核 · 历史标注与模型训练数据 · 未来40根'
STATE = ROOT / 'output/offline_tasks/grade_a_hl2_union_20260908'
OLD_TASK_SHA = 'bc89a4d5e41003f1bf191013d60ba76d84fafbd67a28e17c2dfd3f0c6bc6697a'
EXPECTED_OLD_COUNT = 2513
EXPECTED_NEW_COUNT = 1043


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    if path.exists() and path.read_text() != blob:
        raise ValueError(f'Existing evidence differs: {path}')
    path.write_text(blob)


def canonical(value: dict, protocol: str) -> dict:
    if value.get('model_version') != protocol:
        raise ValueError('Prediction protocol drift')
    copied = deepcopy(value)
    copied['model_version'] = historical.PROTOCOL
    result = historical.canonical_prediction(copied)
    result['model_version'] = protocol
    return result


def expected_tasks(old: list, new: list) -> dict:
    expected = {}
    for tasks, protocol in ((old, historical.PROTOCOL), (new, PROTOCOL)):
        for task in tasks:
            if set(task) != {'data', 'predictions'}:
                raise ValueError('Only data and predictions may be imported')
            data = task['data']
            rid = data.get('review_id')
            if not isinstance(rid, str) or not rid or rid in expected or data.get('protocol_id') != protocol:
                raise ValueError('Duplicate or foreign review identity')
            if len(task['predictions']) != 1:
                raise ValueError('Exactly one proposal per task')
            canonical(task['predictions'][0], protocol)
            expected[rid] = task
    return expected


def inspect(rows: list, predictions: list, expected: dict, required: set) -> dict:
    by_task = {}
    for prediction in predictions:
        by_task.setdefault(prediction['task'], []).append(prediction)
    seen, ids = {}, set()
    for row in rows:
        rid = row.get('data', {}).get('review_id')
        tid = row.get('id')
        if type(tid) is not int or tid <= 0 or tid in ids or rid in seen or rid not in expected:
            raise ValueError('Live task identity drift')
        if row['data'] != expected[rid]['data']:
            raise ValueError('Existing task data changed; never overwrite')
        actual = by_task.pop(tid, [])
        if row.get('total_predictions') != 1 or len(actual) != 1:
            raise ValueError('Live task must have exactly one original prediction')
        protocol = row['data']['protocol_id']
        if canonical(actual[0], protocol) != canonical(expected[rid]['predictions'][0], protocol):
            raise ValueError('Existing prediction changed; never overwrite')
        if type(row.get('total_annotations')) is not int or row['total_annotations'] < 0:
            raise ValueError('Missing annotation count')
        seen[rid] = row
        ids.add(tid)
    if by_task or not required <= set(seen):
        raise ValueError('Original tasks missing or orphan predictions')
    return seen


def default_filters() -> dict:
    return {'conjunction': 'and', 'items': [{'filter': 'filter:tasks:data.protocol_id',
        'operator': 'equal', 'type': 'String', 'value': PROTOCOL}]}


def image_resources(tasks: list, root: Path) -> list:
    resources = []
    for task in tasks:
        data = task['data']
        for field in ('image', 'future_image', 'original_image', 'comparison_image'):
            url = urlsplit(data.get(field, ''))
            d = parse_qs(url.query).get('d', [])
            if url.scheme or url.netloc or url.path != '/data/local-files/' or len(d) != 1:
                raise ValueError('Only local image resources are permitted')
            path = PurePosixPath(d[0])
            if path.is_absolute() or '..' in path.parts or path.parts[:2] != ('label_studio', PACK.name):
                raise ValueError('New images must stay in the dedicated review pack')
            local = root / path
            if not local.resolve().is_relative_to(PACK.resolve()) or not local.is_file():
                raise ValueError('Image escaped the pack or is missing')
            if sha(local) != data.get(field + '_sha256'):
                raise ValueError('Image digest drift')
            resources.append((data['review_id'], field, data[field], sha(local)))
    return resources


def source_identity() -> dict:
    if subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip() != 'main':
        raise ValueError('Use the shared main branch')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    names = ['yoyo/datasets/label_studio_dataset_union.py', 'yoyo/datasets/label_studio_import.py',
        'yoyo/datasets/label_studio_proposal_import.py', 'configs/labelstudio/owner_unified_future40.xml',
        'tests/test_label_studio_dataset_union.py']
    for name in names:
        if subprocess.check_output(['git', 'show', head + ':' + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError('Commit delivery source before running: ' + name)
    return {'source_commit': head, 'code_sha256': {name: sha(ROOT / name) for name in names}}


def deliver() -> dict:
    frozen = source_identity()
    if base.BASE != 'http://127.0.0.1:8081' or sha(OLD_PACK / 'tasks.json') != OLD_TASK_SHA:
        raise ValueError('Wrong server or historical input drift')
    old = json.loads((OLD_PACK / 'tasks.json').read_text())
    new = json.loads((PACK / 'tasks.json').read_text())
    build = json.loads((PACK / 'admin/build_receipt.json').read_text())
    if (sha(PACK / 'tasks.json') != build['tasks_sha256']
            or len(old) != EXPECTED_OLD_COUNT or len(new) != EXPECTED_NEW_COUNT):
        raise ValueError('Frozen task membership changed')
    expected = expected_tasks(old, new)
    original = {t['data']['review_id'] for t in old}
    fresh = {t['data']['review_id'] for t in new}
    sess = base.session()
    project = base.api(sess, 'GET', f'/api/projects/{PROJECT}/')
    config = CONFIG.read_text()
    previous_config = historical.CONFIG.read_text()
    if (project.get('title') not in {historical.TITLE, TITLE}
            or project.get('model_version') not in {historical.PROTOCOL, PROTOCOL}
            or project.get('show_collab_predictions') is not True
            or project.get('evaluate_predictions_automatically') is not False
            or ET.canonicalize(project['label_config'], strip_text=True) not in
                {ET.canonicalize(c, strip_text=True) for c in (config, previous_config)}):
        raise ValueError('Project configuration changed beyond the authorized transition')
    root = base.document_root(sess, PROJECT)
    resources = image_resources(new, Path(root))
    base.api(sess, 'POST', '/api/projects/validate/', {'label_config': config})

    def snapshot():
        rows = base._pages(sess, f'/api/tasks?project={PROJECT}&fields=all&resolve_uri=false'
            '&include=id,data,total_predictions,total_annotations,annotations,drafts')
        predictions = base._pages(sess, f'/api/predictions?task__project={PROJECT}')
        return rows, predictions, inspect(rows, predictions, expected, original)

    rows, predictions, before = snapshot()
    now = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run = STATE / now
    view = base.api(sess, 'GET', f'/api/dm/views/{VIEW}/')
    if view.get('project') != PROJECT:
        raise ValueError('Default view belongs to another project')
    filters = view.get('data', {}).get('filters')
    if filters not in (None, {}, {'conjunction': 'and', 'items': []}, default_filters()):
        raise ValueError('User edited default filters; do not overwrite')
    selection = view.get('data', {}).get('selectedItems')
    if selection and selection != {'all': True, 'excluded': []}:
        raise ValueError('Default view has an explicit user selection')
    write(run / 'before.json', {'project': project, 'view': view, 'tasks': rows, 'predictions': predictions})
    directories = sorted({str((Path(root) / parse_qs(urlsplit(url).query)['d'][0]).parent)
                          for _, _, url, _ in resources})
    stores = base._pages(sess, f'/api/storages/localfiles?project={PROJECT}')
    for directory in directories:
        matches = [s for s in stores if s.get('path') == directory]
        if any(s.get('use_blob_urls') is not False for s in matches):
            raise ValueError('Existing storage has incompatible URL settings')
        base.api(sess, 'POST', '/api/storages/localfiles/validate', {
            'project': PROJECT, 'path': directory, 'use_blob_urls': False})
    for directory in directories:
        if not any(s.get('path') == directory for s in stores):
            base.api(sess, 'POST', '/api/storages/localfiles', {
                'project': PROJECT, 'title': PACK.name + '/' + Path(directory).name,
                'path': directory, 'use_blob_urls': False})
    checks = []
    examples = {new[i]['data']['review_id'] for i in (0, len(new)//2, len(new)-1)}
    # Resolve actual HTTP access before exposing any task or changing its queue.
    for rid, field, url, digest in resources:
        if rid in examples:
            with sess[0].open(base.BASE + url, timeout=30) as response:
                if response.status != 200 or hashlib.sha256(response.read()).hexdigest() != digest:
                    raise ValueError('Served image does not match the frozen file')
            checks.append({'review_id': rid, 'field': field, 'sha256': digest, 'status': 200})
    # No task, annotation, draft, or existing prediction endpoint is ever written.
    missing = [task for task in new if task['data']['review_id'] not in before]
    for start in range(0, len(missing), 250):
        batch = missing[start:start + 250]
        imported = base.api(sess, 'POST', f'/api/projects/{PROJECT}/import?return_task_ids=true', batch)
        if imported.get('task_count') != len(batch) or len(imported.get('task_ids', [])) != len(batch):
            raise ValueError('Incomplete import; rerun to reconcile without replacing anything')
    current = base.api(sess, 'GET', f'/api/projects/{PROJECT}/')
    for key in ('title', 'label_config', 'model_version', 'show_collab_predictions'):
        if current[key] != project[key]:
            raise ValueError('Project settings changed during import')
    base.api(sess, 'PATCH', f'/api/projects/{PROJECT}/', {
        'title': TITLE, 'label_config': config, 'model_version': PROTOCOL, 'show_collab_predictions': True})
    _, _, after = snapshot()
    if set(after) != set(expected):
        raise ValueError('Post-import membership differs')
    # User edits may legitimately continue during the import: preserve identity
    # and count them, without claiming byte equality for concurrent human work.
    if any(after[rid]['id'] != row['id'] for rid, row in before.items()):
        raise ValueError('Previously existing task ID changed')
    wanted = {after[rid]['id'] for rid in fresh}
    query = quote(json.dumps({'filters': default_filters()}, separators=(',', ':')))
    path = f'/api/tasks?project={PROJECT}&fields=task_only&include=id&query={query}'
    if {r['id'] for r in base._pages(sess, path)} != wanted:
        raise ValueError('Source filter did not select the exact new event set')
    current_view = base.api(sess, 'GET', f'/api/dm/views/{VIEW}/')
    if current_view != view:
        raise ValueError('Default view changed during delivery')
    data = deepcopy(view['data'])
    data.update(title='本周审核', filters=default_filters(), ordering=['tasks:id'],
        selectedItems={'all': True, 'excluded': []})
    base.api(sess, 'PATCH', f'/api/dm/views/{VIEW}/', {'data': data})
    view_rows = base._pages(sess, f'/api/tasks?project={PROJECT}&view={VIEW}&fields=task_only&include=id')
    if {r['id'] for r in view_rows} != wanted:
        raise ValueError('Saved view does not select the expected event set')
    receipt = {**frozen, 'generated_at': now, 'project_id': PROJECT, 'view_id': VIEW,
        'url': f'{base.BASE}/projects/{PROJECT}/data?tab={VIEW}', 'tasks_sha256': sha(PACK/'tasks.json'),
        'total_tasks': len(after), 'historical_tasks': len(original), 'current_events': len(fresh),
        'imported': len(missing), 'reused_current': len(fresh)-len(missing),
        'annotations_before': sum(r['total_annotations'] for r in before.values()),
        'annotations_after': sum(r['total_annotations'] for r in after.values()),
        'existing_task_ids_preserved': True, 'existing_task_writes': 0,
        'annotation_or_draft_writes': 0, 'before_snapshot': str(run/'before.json'),
        'before_snapshot_sha256': sha(run/'before.json'), 'image_checks': checks,
        'task_ids': {rid: after[rid]['id'] for rid in fresh}}
    write(run / 'receipt.json', receipt)
    print(str(run / 'receipt.json'))
    return receipt


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = deliver()
    print(json.dumps({k: result[k] for k in ('total_tasks', 'current_events', 'imported', 'url')}, ensure_ascii=False))
