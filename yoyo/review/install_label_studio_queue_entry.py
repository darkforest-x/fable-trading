"""Make project 77's filtered review queue discoverable and its main button safe.

Append a separately reversible queue-entry hook after the existing right-click
hook. Only project/view titles are renamed; predictions, answers, drafts and
filters remain unchanged. Existing labeling tabs are not navigated or refreshed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from yoyo.datasets import label_studio_dataset_union as union
from yoyo.datasets import label_studio_import as ls
from yoyo.datasets import owner_review_export as audit
from yoyo.review import install_label_studio_shortcut as hook

ROOT = union.ROOT
SOURCE = Path(__file__).with_name('label_studio_queue_entry.js')
MARKER = b'\n;/* FABLE_LS_FILTERED_QUEUE_ENTRY_V1 */\n'
PREFIX = b'FABLE_LS_FILTERED_QUEUE_ENTRY_'


def frozen_source():
    if subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip() != 'main':
        raise ValueError('Use shared main')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    names = ['yoyo/review/install_label_studio_queue_entry.py', 'yoyo/review/label_studio_queue_entry.js',
             'yoyo/review/install_label_studio_shortcut.py', 'yoyo/datasets/label_studio_dataset_union.py',
             'tests/label_studio_queue_entry.test.cjs', 'tests/test_label_studio_shortcut_install.py',
             'tests/test_label_studio_queue_entry_install.py']
    for name in names:
        if subprocess.check_output(['git', 'show', head + ':' + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError('Commit source before installation: ' + name)
    return {'source_commit': head, 'source_sha256': {name: union.sha(ROOT / name) for name in names}}


def title_changes(project, view):
    """Validate the actual queue, then return title-only mutations."""
    if (project.get('id') != 77 or project.get('title') not in {union.TITLE, union.PREVIOUS_TITLE, union.PREVIOUS_REVIEW_TITLE}
            or project.get('model_version') != union.PROTOCOL
            or project.get('show_collab_predictions') is not True
            or project.get('evaluate_predictions_automatically') is not False):
        raise ValueError('Unexpected project or prelabel version')
    if view.get('id') != 44 or view.get('project') != 77 or view.get('data', {}).get('filters') != union.default_filters():
        raise ValueError('Current view no longer selects the protected HL2 queue')
    data = deepcopy(view['data'])
    data['title'] = union.VIEW_TITLE
    return {'title': union.TITLE}, {'data': data}


def name_entry(state):
    if ls.BASE != 'http://127.0.0.1:8081':
        raise ValueError('Wrong Label Studio instance')
    expected, old, _ = audit.read_sources()
    sess = ls.session()
    project = ls.api(sess, 'GET', '/api/projects/77/')
    view = ls.api(sess, 'GET', '/api/dm/views/44/')
    project_patch, view_patch = title_changes(project, view)
    tasks = ls._pages(sess, '/api/tasks?project=77&fields=task_only&include=id,data&resolve_uri=false')
    mapping = audit.verify_tasks(tasks, expected, old)
    predictions = ls._pages(sess, '/api/predictions?task__project=77')
    audit.verify_predictions(predictions, expected, mapping)
    wanted = {tid for rid, tid in mapping.items() if expected[rid]['data']['protocol_id'] == union.PROTOCOL}
    visible = ls._pages(sess, '/api/tasks?project=77&view=44&fields=task_only&include=id')
    if len(wanted) != 1043 or {r['id'] for r in visible} != wanted:
        raise ValueError('View must select exactly the frozen 1043 prelabelled tasks')
    before = state / 'names-before.json'
    if not before.exists():
        with before.open('x') as file:
            json.dump({'project': project, 'view': view}, file, ensure_ascii=False, indent=2)
    if ls.api(sess, 'GET', '/api/dm/views/44/') != view:
        raise ValueError('View changed while checking; do not overwrite')
    current = ls.api(sess, 'GET', '/api/projects/77/')
    if any(current[k] != project[k] for k in ('title', 'model_version', 'label_config')):
        raise ValueError('Project changed while checking')
    if project['title'] != union.TITLE:
        ls.api(sess, 'PATCH', '/api/projects/77/', project_patch)
    if view['data']['title'] != union.VIEW_TITLE:
        ls.api(sess, 'PATCH', '/api/dm/views/44/', view_patch)
    after_project = ls.api(sess, 'GET', '/api/projects/77/')
    if after_project['title'] != union.TITLE or after_project['model_version'] != union.PROTOCOL:
        raise ValueError('Project title or version readback differs')
    after = ls.api(sess, 'GET', '/api/dm/views/44/')
    if after['data'] != view_patch['data']:
        raise ValueError('View readback differs')
    return {'project_id': 77, 'view_id': 44, 'project_title': union.TITLE, 'view_title': union.VIEW_TITLE,
            'total_tasks': len(mapping), 'filtered_tasks': len(wanted), 'verified_original_predictions': len(predictions),
            'annotation_writes': 0, 'draft_writes': 0, 'task_or_prediction_writes': 0,
            'filter_changed': False, 'model_version_changed': False}


def install(bundle, state, restore=False, name_queue=False):
    source = frozen_source()
    if restore and name_queue:
        raise ValueError('Title clarification and hook rollback are separate operations')
    result = hook.install(bundle, state, restore, source_path=SOURCE, marker=MARKER, marker_prefix=PREFIX)
    if name_queue:
        result['entry'] = name_entry(state)
    result.update(source)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--name-entry', action='store_true')
    args = parser.parse_args()
    print(json.dumps(install(args.bundle, args.state_dir, args.restore, args.name_entry), ensure_ascii=False, indent=2))
