"""Publish a future-only lookup through LS configuration without editing tasks.

Owner requested 100-150 future bars on 2026-09-08; use the completed 150-bar
pack for the active HL2 queue and retain byte-exact historical fallbacks.
The installed LS parseValue supports $review_id/image.png, not $review_id.png.
Original task metadata, predictions, annotations and drafts remain immutable.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets import label_studio_dataset_union as union
from yoyo.datasets import label_studio_import as ls
from yoyo.datasets import owner_review_export as audit
from yoyo.datasets.grade_a_label_studio import link_pack

ROOT = union.ROOT
PACK = ROOT / 'datasets/owner_review_future150_20260908_v1'
PROTOCOL = 'owner_review_future150_v1'
URL = '/data/local-files/?d=label_studio/' + PACK.name + '/images/$review_id/image.png'
PREVIOUS_CONFIG_COMMIT = '88038231b442e74b7a328fe1bf88bd03eecce212'
STATE = ROOT / 'output/offline_tasks/label_studio_future150_20260908'
EXPECTED_COUNTS = {'new_future150': 1043, 'legacy_future40_symlink': 2513}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def same_config(left, right):
    """LS may normalize XML whitespace when it persists the project."""
    return ET.canonicalize(left, strip_text=True) == ET.canonicalize(right, strip_text=True)


def validate_config(before, after):
    """Only the read-only future object's URL and presentation may differ."""
    names = {'RectangleLabels', 'Label', 'Choices', 'Choice', 'Image'}
    def controls(text):
        result = []
        for node in ET.fromstring(text).iter():
            if node.tag in names:
                attributes = dict(node.attrib)
                if node.tag == 'Image' and attributes.get('name') == 'future':
                    if attributes.get('value') not in ('$future_image', URL):
                        raise ValueError('Unexpected future image binding')
                    attributes['value'] = '$future_image'
                result.append((node.tag, attributes))
        return result
    if controls(before) != controls(after):
        raise ValueError('Annotation or primary-image contract changed')
    targets = [n for n in ET.fromstring(after).iter('Image') if n.get('name') == 'future']
    if len(targets) != 1 or targets[0].get('value') != URL:
        raise ValueError('Future must use the exact completed lookup')


def verify_lookup(rows, expected, pack=PACK):
    """Check identity, time, bytes, and exact legacy symlink targets independently."""
    seen, counts = set(), {'new_future150': 0, 'legacy_future40_symlink': 0}
    for row in rows:
        rid = row.get('review_id')
        if not isinstance(rid, str) or not re.fullmatch('[0-9a-f]{24}', rid) or rid in seen or rid not in expected:
            raise ValueError('Unknown or duplicate lookup identity')
        seen.add(rid)
        original = expected[rid]['data']
        if original['protocol_id'] not in (audit.NEW_PROTOCOL, audit.OLD_PROTOCOL):
            raise ValueError('Unknown original protocol')
        if row.get('source_protocol_id') != original['protocol_id'] or row.get('image_path') != f'images/{rid}/image.png':
            raise ValueError('Lookup source or path differs')
        path = pack / row['image_path']
        info = row['future']
        end = datetime.fromisoformat(info['review_available_at'])
        if end.tzinfo is None or end > HOLDOUT_START:
            raise ValueError('Future lookup crosses holdout')
        wanted = 'new_future150' if original['protocol_id'] == audit.NEW_PROTOCOL else 'legacy_future40_symlink'
        if row.get('mode') != wanted:
            raise ValueError('Wrong preview mode')
        counts[wanted] += 1
        requested = 150 if wanted == 'new_future150' else 40
        actual = info['actual_future_bars']
        if type(actual) is not int or not 0 <= actual <= requested or info['requested_future_bars'] != requested:
            raise ValueError('Future count is invalid')
        main_end = datetime.fromisoformat(expected[rid]['source_identity']['main_end_time'])
        if end != main_end + timedelta(minutes=15 * (actual + 1)):
            raise ValueError('Future time is not aligned to its original input')
        if (actual < requested and info.get('missing_future_reason') not in ('holdout_boundary', 'source_end', 'source_gap')
                or actual == requested and info.get('missing_future_reason') is not None):
            raise ValueError('Future truncation reason is inconsistent')
        if wanted == 'new_future150':
            if path.is_symlink() or path.resolve() != pack.resolve() / row['image_path']:
                raise ValueError('New preview must be a regular file inside its pack')
        else:
            relative = parse_qs(urlsplit(original['future_image']).query).get('d', [])
            prefix = f'label_studio/{audit.OLD_PACK.name}/'
            if len(relative) != 1 or not relative[0].startswith(prefix):
                raise ValueError('Unknown legacy image source')
            target = audit.OLD_PACK / relative[0][len(prefix):]
            if not target.resolve().is_relative_to(audit.OLD_PACK.resolve()) or not path.is_symlink() or path.resolve() != target.resolve():
                raise ValueError('Legacy preview link target differs')
            if row['image_sha256'] != original['future_image_sha256']:
                raise ValueError('Legacy preview bytes changed')
        if digest(path.read_bytes()) != row.get('image_sha256'):
            raise ValueError('Lookup image digest changed')
    if seen != set(expected) or counts != EXPECTED_COUNTS:
        raise ValueError('Lookup membership differs from the complete project')
    return counts


def frozen_source():
    if subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip() != 'main':
        raise ValueError('Publish only from main')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    files = ['yoyo/review/publish_future_context.py', 'tests/test_publish_future_context.py',
             'configs/labelstudio/owner_unified_future40.xml', 'yoyo/datasets/label_studio_dataset_union.py',
             'yoyo/datasets/owner_review_export.py', 'yoyo/datasets/label_studio_import.py',
             'yoyo/datasets/grade_a_label_studio.py', 'scripts/ls_auto_import.py',
             'scripts/export_owner_labels.py', 'yoyo/contracts/holdout.py']
    for name in files:
        if subprocess.check_output(['git', 'show', head + ':' + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError('Commit publisher source before use: ' + name)
    return head


def verify_receipt(ready, source_info):
    """Require a completed, source-pinned build with matching immutable inputs."""
    if (ready.get('protocol_id') != PROTOCOL or ready.get('status') != 'ready_for_human_review_future150'
            or any(ready.get(k) is not False for k in ('holdout_read', 'training_eligible',
                'production_eligible', 'new_gold', 'new_training', 'new_model_inference',
                'label_studio_mutation_in_builder', 'future_used_for_training_input', 'future_used_for_labels'))
            or any(ready.get(k) != value for k, value in {'requested_future_bars': 150, 'new_events': 1043,
                'legacy_events': 2513, 'lookup_images': 3556, 'verified_positive_image_label_pairs': 8000,
                'main_replay_passed': 1043}.items())):
        raise ValueError('Future pack is not a completed safe review artifact')
    before = ready.get('immutable_media_before_sha256', '')
    if (not re.fullmatch('[0-9a-f]{64}', before) or before != ready.get('immutable_media_after_sha256')
            or ready.get('read_source_metadata_sha256') != source_info['files_sha256']):
        raise ValueError('Original media or current source identity differs')
    commit = ready.get('source_commit', '')
    code = ready.get('code_sha256', {})
    if (not re.fullmatch('[0-9a-f]{40}', commit)
            or 'yoyo/datasets/review_future_context.py' not in code or not ready.get('source_sha256')):
        raise ValueError('Missing pinned builder identity')
    for name, expected_sha in {**code, **ready['source_sha256']}.items():
        path = ROOT / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.resolve() != ROOT.resolve() / name:
            raise ValueError('Build source path escapes repository')
        if digest(path.read_bytes()) != expected_sha:
            raise ValueError('Build source bytes changed: ' + name)
        if name in code and digest(subprocess.check_output(['git', 'show', commit + ':' + name], cwd=ROOT)) != expected_sha:
            raise ValueError('Builder was not frozen before execution: ' + name)


def publish():
    head = frozen_source()
    if ls.BASE != 'http://127.0.0.1:8081':
        raise ValueError('Wrong Label Studio instance')
    ready = json.loads((PACK / 'admin/build_receipt.json').read_text())
    expected, old, source_info = audit.read_sources()
    verify_receipt(ready, source_info)
    manifest = (PACK / 'manifest.jsonl').read_bytes()
    if digest(manifest) != ready['manifest_sha256']:
        raise ValueError('Lookup manifest drift')
    rows = [json.loads(line) for line in manifest.splitlines()]
    counts = verify_lookup(rows, expected)
    config = union.CONFIG.read_text()
    previous = subprocess.check_output(['git', 'show', PREVIOUS_CONFIG_COMMIT + ':' +
                                      str(union.CONFIG.relative_to(ROOT))], cwd=ROOT, text=True)
    validate_config(previous, config)
    s = ls.session()
    project = ls.api(s, 'GET', '/api/projects/77/')
    view = ls.api(s, 'GET', '/api/dm/views/44/')
    if (project['title'] not in (union.TITLE, union.PREVIOUS_REVIEW_TITLE)
            or project['model_version'] != union.PROTOCOL or not project['show_collab_predictions']
            or project['evaluate_predictions_automatically'] is not False
            or not any(same_config(project['label_config'], item) for item in (previous, config))
            or view['data']['filters'] != union.default_filters()):
        raise ValueError('Current project or queue differs from the authorized transition')
    tasks_url = '/api/tasks?project=77&fields=task_only&include=id,data&resolve_uri=false'
    tasks = ls._pages(s, tasks_url)
    mapping = audit.verify_tasks(tasks, expected, old)
    if len(mapping) != len(expected):
        raise ValueError('Live project is incomplete')
    audit.verify_predictions(ls._pages(s, '/api/predictions?task__project=77'), expected, mapping)
    run = STATE / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    union.write(run / 'before.json', {'project': project, 'view': view})
    link_pack(PACK)
    root = ls.document_root(s, 77)
    directory = root / 'label_studio' / PACK.name / 'images'
    if directory.resolve() != (PACK / 'images').resolve():
        raise ValueError('Served lookup directory resolves elsewhere')
    storage = {'project': 77, 'path': str(directory), 'use_blob_urls': False}
    ls.api(s, 'POST', '/api/storages/localfiles/validate', storage)
    stores = ls._pages(s, '/api/storages/localfiles?project=77')
    hits = [r for r in stores if r['path'] == str(directory)]
    if any(r.get('use_blob_urls') is not False for r in hits):
        raise ValueError('Existing storage has unexpected behavior')
    if not hits:
        ls.api(s, 'POST', '/api/storages/localfiles', {**storage, 'title': PACK.name})
    samples = [next(r for r in rows if r['mode'] == 'legacy_future40_symlink')]
    fresh = [r for r in rows if r['mode'] == 'new_future150']
    samples += [min(fresh, key=lambda r: r['future']['actual_future_bars']),
                max(fresh, key=lambda r: r['future']['actual_future_bars'])]
    checks = []
    for row in samples:
        url = URL.replace('$review_id', row['review_id'])
        with s[0].open(ls.BASE + url, timeout=30) as response:
            if response.status != 200 or digest(response.read()) != row['image_sha256']:
                raise ValueError('Served future image differs')
        checks.append({'review_id': row['review_id'], 'status': 200, 'image_sha256': row['image_sha256']})
    ls.api(s, 'POST', '/api/projects/validate/', {'label_config': config})
    current = ls.api(s, 'GET', '/api/projects/77/')
    if (any(current[k] != project[k] for k in ('title', 'model_version'))
            or not same_config(current['label_config'], project['label_config'])):
        raise ValueError('Project settings changed during preflight')
    if not same_config(current['label_config'], config) or current['title'] != union.TITLE:
        ls.api(s, 'PATCH', '/api/projects/77/', {'label_config': config, 'title': union.TITLE})
    after = ls.api(s, 'GET', '/api/projects/77/')
    if not same_config(after['label_config'], config) or after['title'] != union.TITLE or after['model_version'] != union.PROTOCOL:
        raise ValueError('Config readback differs')
    if ls.api(s, 'GET', '/api/dm/views/44/')['data'] != view['data']:
        raise ValueError('Review filter changed during delivery')
    if audit.verify_tasks(ls._pages(s, tasks_url), expected, old) != mapping:
        raise ValueError('Task data changed during delivery')
    audit.verify_predictions(ls._pages(s, '/api/predictions?task__project=77'), expected, mapping)
    result = {'source_commit': head, 'project_id': 77, 'view_id': 44, 'project_title': union.TITLE,
              'preview_protocol': PROTOCOL, 'manifest_sha256': digest(manifest), 'lookup_counts': counts,
              'config_sha256': digest(config.encode()), 'served_config_sha256': digest(after['label_config'].encode()),
              'http_checks': checks,
              'annotation_writes': 0, 'draft_writes': 0, 'task_writes': 0, 'prediction_writes': 0,
              'answers_before': project.get('total_annotations_number'),
              'answers_after': after.get('total_annotations_number'),
              'holdout_read': False, 'training_eligible': False, 'production_eligible': False,
              'receipt_path': str(run / 'receipt.json')}
    union.write(run / 'receipt.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(publish(), ensure_ascii=False, indent=2))
