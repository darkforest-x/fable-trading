"""Reproduce the bounded metadata audit; never open images, labels or OHLCV.

This audit compares frozen close/HL2 negative membership and the already
reported historical duplicate groups. It does not change labels or infer Gold.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from yoyo.contracts.holdout import HOLDOUT_START as HOLDOUT

ROOT = Path(__file__).resolve().parents[2]

FROZEN = {
    'datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl': '22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2',
    'datasets/ma_launch_owner_grade_a8000_yolo_neg24000_hl2_v1/manifest.jsonl': 'ec93d6bfd04cc84a24a34cd745af2c74943f9a75e664831060619511ba60f6d7',
    'datasets/owner_box_refinement_20260907_v1/manifest.jsonl': '7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641',
    'experiments/active/exp-owner-box-curation-20260907-v1/results/quality_tools_details/duplicate_groups.json': '097f9ef6e387f17512076334d8af17b32104e9139a63ebd8e476f480bc23dffc',
    'experiments/active/exp-owner-box-curation-20260907-v1/results/quality_tools.json': '6a4484b75832a1c79c3d24be653d40357b291041c2df30bf54cd821d44a6d5cd',
}
FIELDS = ('dataset_sample_id', 'negative_event_id', 'sample_kind', 'venue', 'symbol',
    'exchange_symbol', 'source_path', 'split', 'window_start_i', 'window_end_i',
    'window_start_time', 'window_end_time', 'core_start_time', 'core_end_time',
    'dependency_end_time', 'class_id', 'class_name', 'boxes_per_image',
    'paired_positive_event_id', 'paired_direction')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None or value >= HOLDOUT:
        raise ValueError('Time is missing its zone or reaches holdout')
    return value


def negative_rows(path):
    result = {}
    with path.open() as handle:
        for line in handle:
            raw = json.loads(line)
            if raw['sample_kind'] != 'negative':
                continue
            row = {key: raw.get(key) for key in FIELDS}
            rid = row['dataset_sample_id']
            if rid in result or not row['negative_event_id']:
                raise ValueError('Duplicate sample or absent negative event ID')
            for key in ('window_start_time', 'window_end_time', 'core_start_time',
                        'core_end_time', 'dependency_end_time'):
                stamp(row[key])
            if row['boxes_per_image'] != 0 or row['class_id'] is not None or row['class_name'] is not None:
                raise ValueError('Negative row is not blank')
            result[rid] = row
    return result


def audit(root):
    names = list(FROZEN)
    for name, digest in FROZEN.items():
        if sha(root / name) != digest:
            raise ValueError('Frozen input changed: ' + name)
    old, new = (negative_rows(root / name) for name in names[:2])
    if len(old) != 24000 or old != new or len({r['negative_event_id'] for r in new.values()}) != 3129:
        raise ValueError('Close/HL2 background membership or metadata drift')
    original = [json.loads(line) for line in (root / names[2]).read_text().splitlines()]
    by_id = {r['review_id']: r for r in original}
    if len(by_id) != 2513:
        raise ValueError('Historical population differs')
    aliases = defaultdict(list)
    for row in original:
        stamp(row['main_start_time']); stamp(row['main_end_time'])
        aliases[row['alias_candidate_group']].append(row['review_id'])
    alias_sets = {frozenset(g) for g in aliases.values() if len(g) > 1}
    groups = json.loads((root / names[3]).read_text())
    exact_sets = {frozenset(g) for g in groups['exact_duplicates']}
    if len(exact_sets) != 17 or exact_sets != alias_sets or len(groups['near_duplicates']) != 2:
        raise ValueError('Known duplicate/alias inventory changed')
    details = {}
    for kind, entries in groups.items():
        details[kind] = []
        for ids in entries:
            rows = [by_id[rid] for rid in ids]
            image_hashes = {r['assets'][r['asset_roles']['image']] for r in rows}
            originals = {tuple(r['original_geometry']['yolo_xywh']) for r in rows}
            item = {'review_ids': ids, 'box_ids': [r['box_id'] for r in rows],
                'symbols': [r['symbol'] for r in rows],
                'alias_candidate_groups': [r['alias_candidate_group'] for r in rows],
                'original_window_dependency_ids': [r['original_window_dependency_id'] for r in rows],
                'same_review_image_sha256': len(image_hashes) == 1,
                'raw_original_yolo_coordinates_equal': len(originals) == 1,
                'coordinate_note': 'Historical canvas coordinates are not directly comparable across crops.'}
            if kind == 'exact_duplicates' and (len(image_hashes) != 1 or len(originals) != 2):
                raise ValueError('Known exact duplicate geometry differs')
            if kind == 'near_duplicates' and (len({r['symbol'] for r in rows}) != 2 or len(image_hashes) != 2):
                raise ValueError('Known cross-symbol pHash group differs')
            details[kind].append(item)
    conflicts = {}
    for event_id in ('1f096c88a11b75c095922034', '946ab4ce435d96b30d5c17a9'):
        rows = [r for r in new.values() if r['negative_event_id'] == event_id]
        if len(rows) != 8 or {r['venue'] for r in rows} != {'okx'}:
            raise ValueError('Known negative event changed')
        conflicts[event_id] = {'symbol': rows[0]['symbol'], 'split': rows[0]['split'],
            'dataset_sample_ids': sorted(r['dataset_sample_id'] for r in rows),
            'all_fields_identical_to_close': True}
    return {'schema_version': 1, 'audit_id': 'owner_hl2_background_metadata_parity_v1',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'root': str(root),
        'input_sha256': FROZEN, 'auditor_sha256': sha(Path(__file__)),
        'negative_variants': len(new), 'negative_events': 3129, 'compared_fields': FIELDS,
        'all_negative_metadata_equal': True, 'all_compared_times_preholdout': True,
        'exact_duplicate_groups': 17, 'exact_duplicate_members': 34,
        'alias_groups_are_the_same_17_groups': True, 'near_duplicate_groups': 2,
        'near_duplicate_semantics': json.loads((root / names[4]).read_text())['cleanvision']['near_duplicate_semantics'],
        'groups': details, 'known_negative_events': conflicts,
        'media_read': False, 'ohlcv_read': False, 'holdout_read': False,
        'label_files_read': False, 'automatic_deletions': False, 'automatic_label_changes': False,
        'new_gold': False, 'training_eligible': False, 'production_eligible': False,
        'interpretation': 'Metadata conflicts remain review candidates; no automatic label transfer.'}


def committed_source(root):
    if subprocess.check_output(['git', 'branch', '--show-current'], cwd=root, text=True).strip() != 'main':
        raise ValueError('Shared main is required')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    names = ['yoyo/datasets/owner_hl2_background_parity.py', 'yoyo/contracts/holdout.py']
    for name in names:
        if subprocess.check_output(['git', 'show', head + ':' + name], cwd=root) != (root / name).read_bytes():
            raise ValueError('Commit source before audit: ' + name)
    return {'source_commit': head, 'source_sha256': {name: sha(root / name) for name in names}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    source = committed_source(root)
    result = {**audit(root), **source}
    if committed_source(root)['source_sha256'] != source['source_sha256'] or any(sha(root / p) != d for p, d in FROZEN.items()):
        raise ValueError('Source or input changed during audit')
    if args.output.exists():
        raise SystemExit('Output exists; preserve the previous audit or choose another --output')
    with args.output.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')
    print(args.output)
