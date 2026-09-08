"""Preserve old Owner work while retiring the full historical re-review workload.

This metadata-only inventory implements the Owner's September 8 decision to
review the actual HL2 events first. An answer status, a geometric overlap and an
image duplicate are different axes: none transfers a human decision to another
event. Raw answers stay in their immutable export; this module never contacts
Label Studio, reads market/media files, or rewrites any label or training set.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from yoyo.datasets import owner_review_export as export

ROOT = export.ROOT
QUEUE = ROOT / 'experiments/active/exp-owner-box-curation-20260907-v1/results/review_queue.json'
DISPOSITIONS = {
    'owner_boxes': 'retain_owner_boxes_pending_quality',
    'owner_no_target': 'retain_no_target_pending_background_checks',
    'owner_no_target_with_inherited_proposal': 'retain_no_target_pending_background_checks',
    'conflict_needs_review': 'quarantine_conflicting_answer',
    'event_conflict': 'quarantine_conflicting_answer',
    'owner_uncertain': 'retain_uncertain',
    'missing_decision': 'retain_missing_decision',
    'cancelled_only': 'retain_cancelled_only',
    'draft_only': 'preserve_draft',
    'unanswered': 'archive_pending_shape_screen',
}


def inventory(old_rows: list, events: list, answers: list, quality_rows: list) -> dict:
    """Join only by frozen review identity; a proposal never supplies an answer."""
    def unique(rows, key):
        result = {r[key]: r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate identity: ' + key)
        return result
    old = unique(old_rows, 'review_id')
    statuses = unique(events, 'review_id')
    quality = unique(quality_rows, 'review_id')
    if not set(old) <= set(statuses) or set(old) != set(quality):
        raise ValueError('Old population missing from statuses or quality evidence')
    grouped = defaultdict(list)
    for answer in answers:
        rid = answer['review_id']
        if rid not in statuses or answer['task_id'] != statuses[rid]['task_id']:
            raise ValueError('Answer identity differs from event')
        grouped[rid].append(answer)
    rows = []
    for rid, original in old.items():
        event = statuses[rid]
        if event['status'] not in DISPOSITIONS:
            raise ValueError('Unknown answer status')
        records = grouped[rid]
        effective = [a for a in records if a['effective_answer']]
        if bool(effective) != (event['status'] not in ('unanswered', 'draft_only', 'cancelled_only')):
            raise ValueError('Effective answer coverage disagrees with status')
        if any(a['protocol_id'] != export.OLD_PROTOCOL for a in records):
            raise ValueError('Historical answer uses another protocol')
        if quality[rid]['task_id'] != event['task_id']:
            raise ValueError('Quality evidence belongs to another task')
        canonical_boxes = []
        if event['status'] == 'owner_boxes' and not event['event_conflict']:
            canonical_boxes = effective[0]['effective_boxes']
            if any(not export.boxes_equal(canonical_boxes, a['effective_boxes']) for a in effective[1:]):
                raise ValueError('Owner box answers disagree despite an equivalent event status')
        rows.append({
            'review_id': rid, 'task_id': event['task_id'], 'box_id': original['box_id'],
            'symbol': original['symbol'], 'source_path': original['source_path'],
            'historical_owner_side': original['owner_side'], 'old_split_provenance': original['original_split'],
            'old_star_provenance_only': original['exact_star'],
            'answer_status': event['status'], 'disposition': DISPOSITIONS[event['status']],
            'annotation_ids': [a['annotation_id'] for a in records if a['record_kind'] == 'annotation'],
            'draft_ids': [a['draft_id'] for a in records if a['record_kind'] == 'draft'],
            'effective_answer_count': len(effective),
            'effective_boxes': [dict(box) for box in canonical_boxes],
            'canonical_geometry_policy': 'one_complete_equivalent_answer_all_answer_ids_retained',
            'conflict_answer_boxes_are_training_labels': False,
            'audit_flags': quality[rid]['audit_flags'],
            'negative_event_conflicts': quality[rid]['negative_event_conflicts'],
            'current_default_queue_member': False,
            'current_shape_screen': 'not_completed',
            'all_raw_records_retained': True,
            **export.FALSE_FLAGS,
        })
    rows.sort(key=lambda r: r['task_id'])
    return {'population': len(rows), 'rows': rows,
            'disposition_counts': dict(Counter(r['disposition'] for r in rows)),
            'answer_status_counts': dict(Counter(r['answer_status'] for r in rows)),
            'negative_event_review_holds': sorted({event for r in rows for event in r['negative_event_conflicts']}),
            'policy': {'raw_owner_annotations_edited': False, 'label_studio_mutations': False,
                       'automatic_label_transfer': False, 'automatic_deletions': False,
                       'old_whole_pool_owner_rereview_required': False,
                       'unanswered_is_background': False, 'shape_screen_complete': False,
                       'negative_holds_apply_to': 'next_dataset_membership_not_historical_training_files'},
            **export.FALSE_FLAGS}


def frozen_source() -> dict:
    if subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip() != 'main':
        raise ValueError('Inventory requires main')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    names = ['yoyo/datasets/historical_review_inventory.py', 'tests/test_historical_review_inventory.py',
             'yoyo/datasets/owner_review_export.py', 'yoyo/contracts/holdout.py']
    for name in names:
        if subprocess.check_output(['git', 'show', head + ':' + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError('Commit source before build: ' + name)
    return {'source_commit': head, 'source_sha256': {n: export.sha(ROOT / n) for n in names}}


def build(snapshot: Path, output: Path) -> dict:
    frozen = frozen_source()
    expected, old_mapping, sources = export.read_sources()  # Frozen media metadata, no media reads.
    snapshot = snapshot.resolve()
    if snapshot.parent != export.OUTPUT.resolve():
        raise ValueError('Use an existing timestamped Owner export directory')
    summary = json.loads((snapshot / 'summary.json').read_text())
    if summary.get('project_id') != export.PROJECT or summary.get('holdout_read') is not False:
        raise ValueError('Unexpected export project or holdout status')
    source_hashes = {str(snapshot / 'summary.json'): export.sha(snapshot / 'summary.json')}
    for name in ('raw.json', 'predictions.json', 'answers.jsonl'):
        p = snapshot / name
        if export.sha(p) != summary['files_sha256'][name]:
            raise ValueError('Snapshot digest changed: ' + name)
        source_hashes[str(p)] = export.sha(p)
    raw = json.loads((snapshot / 'raw.json').read_text())
    mapping = export.verify_tasks(raw['tasks'], expected, old_mapping)
    export.verify_predictions(raw['predictions'], expected, mapping)
    recomputed_answers, recomputed = export.build_answers(raw['tasks'], expected)
    answers = [json.loads(line) for line in (snapshot / 'answers.jsonl').read_text().splitlines()]
    if recomputed_answers != answers or recomputed['events'] != summary['events']:
        raise ValueError('Derived statuses differ from preserved raw answers')
    old = [json.loads(line) for line in (export.OLD_PACK / 'manifest.jsonl').read_text().splitlines()]
    queue = json.loads(QUEUE.read_text())
    if queue['source_manifest_sha256'] != export.OLD_MANIFEST_SHA:
        raise ValueError('Quality queue refers to a different old manifest')
    source_hashes[str(QUEUE)] = export.sha(QUEUE)
    source_hashes.update(sources['files_sha256'])
    result = inventory(old, summary['events'], answers, queue['rows'])
    result.update(**frozen, source_files_sha256=source_hashes,
                  collection_started_at=summary['collection_started_at'],
                  collection_finished_at=summary['collection_finished_at'], snapshot_is_atomic=False,
                  generated_at=datetime.now(timezone.utc).isoformat(),
                  original_export=str(snapshot.relative_to(ROOT)),
                  media_read=False, ohlcv_read=False, holdout_read=False)
    # Data is nested in expected; count the imported mapping, not a guessed total.
    result['current_hl2_queue_count'] = sum(expected[rid]['data']['protocol_id'] == export.NEW_PROTOCOL for rid in mapping)
    if any(export.sha(Path(path)) != digest for path, digest in source_hashes.items()):
        raise ValueError('Metadata changed during inventory')
    if frozen_source()['source_sha256'] != frozen['source_sha256']:
        raise ValueError('Source changed during inventory')
    output.mkdir(parents=True, exist_ok=False)
    lines = b''.join((json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode() for r in result.pop('rows'))
    (output / 'historical_inventory.jsonl').write_bytes(lines)
    result['inventory_sha256'] = hashlib.sha256(lines).hexdigest()
    (output / 'summary.json').write_bytes(export.blob(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.snapshot, args.output)
    print(json.dumps({k: result[k] for k in ('population', 'disposition_counts', 'negative_event_review_holds')}, ensure_ascii=False))
