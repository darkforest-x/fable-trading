"""Prepare at most three causal views per existing v6 morphology event.

Owner request dated 2026-09-25: preserve the 1,841 selected events, use no
more than three positions per event, and audit Grade-A negative reuse before
retraining. The three views use pre=7/9/11 and post=5. OHLC, HL2 MAs and price
scaling stop at the unchanged confirmation close; 1,200 earlier bars seed MAs.
Existing core timestamps and geometry rules are reused, not manually relabeled.

This preparation stage deliberately exposes no training YAML: completing the
positive images does not establish compatible, non-conflicting negative labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_redo import read_interval, rows, sha, utc, write_json, write_rows

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-ma-morphology-v6-threeview-20260925-v1'
DEFAULT_PLAN = EXPERIMENT / 'plan.json'
VARIANTS = {'P7': (7, 'B1'), 'P9': (9, 'A'), 'P11': (11, 'B2')}


def validate_event(event: dict, plan: dict) -> None:
    """Validate unchanged membership, core clocks, and chronological partition."""
    minutes = int(event['bar_minutes'])
    start, end = utc(event['core_start_time']), utc(event['core_end_time'])
    step = pd.Timedelta(minutes=minutes)
    if minutes <= 0 or end - start != (int(event['core_bars']) - 1) * step:
        raise ValueError('Invalid core clock: ' + event['event_id'])
    if event['core_bars'] not in (4, 5) or event['direction'] not in ('LONG', 'SHORT'):
        raise ValueError('Invalid core class')
    decision = utc(event['profit']['decision_close_time_utc'])
    if decision != end + 6 * step or not event['profit']['retained']:
        raise ValueError('Original positive/confirmation contract drift')
    boundaries = plan['splits']
    lo, hi = {'train': (None, utc(boundaries['train_end_exclusive'])),
              'val': (utc(boundaries['train_end_exclusive']), utc(boundaries['validation_end_exclusive'])),
              'test': (utc(boundaries['validation_end_exclusive']), utc(boundaries['test_end_exclusive']))}[event['split']]
    visible_start = start - 11 * step
    label_end = utc(event['profit']['label_window_end_utc'])
    if (lo is not None and visible_start < lo) or label_end >= hi or label_end < decision:
        raise ValueError('Input or label horizon crosses split')


def render_views(frame: pd.DataFrame, event: dict) -> dict:
    """Render only the original core and its already-closed post-five prefix."""
    times = pd.to_datetime(frame['open_time'], utc=True)
    start_matches = np.flatnonzero(times == utc(event['core_start_time']))
    end_matches = np.flatnonzero(times == utc(event['core_end_time']))
    if len(start_matches) != 1 or len(end_matches) != 1:
        raise ValueError('Core timestamp absent or duplicated')
    start, end = int(start_matches[0]), int(end_matches[0])
    support, endpoint = start - 11 - renderer.SUPPORT_BARS, end + 5
    if support < 0 or endpoint >= len(frame):
        raise ValueError('Incomplete warmup or confirmation')
    step = pd.Timedelta(minutes=int(event['bar_minutes']))
    known = times.iloc[support:endpoint + 1]
    if not (known.diff().iloc[1:] == step).all():
        raise ValueError('Known-input candle gap')
    if times.iloc[endpoint] + step != utc(event['profit']['decision_close_time_utc']):
        raise ValueError('Decision clock drift')
    prefix = frame.iloc[:endpoint + 1].copy()
    output = {}
    for variant, (pre, _) in VARIANTS.items():
        png, box, visible = renderer._window_asset(
            prefix, core_start_i=start, core_end_i=end, pre_bars=pre, post_bars=5,
            support_start_i=support, price_scale=renderer.VISIBLE_RANGE_PRICE_SCALE)
        label = renderer.label_line({'positive': True, 'class_id': 0 if event['direction'] == 'LONG' else 1, 'box': box})
        output[variant] = {'png': png, 'box': box, 'label': label, 'visible': visible}
    return output


def _link_checked(source: Path, dest: Path, expected: str) -> None:
    if sha(source) != expected:
        raise ValueError('Parent asset SHA changed: ' + str(source))
    os.link(source, dest)


def build(plan_path: Path, output: Path, pilot: int = 0, recovery_path: Path | None = None) -> dict:
    plan = json.loads(plan_path.read_text())
    if plan['views'] != {'pre_bars': [7, 9, 11], 'post_bars': 5, 'maximum_per_event': 3}:
        raise ValueError('Three-view contract drift')
    dependencies = [Path(__file__), plan_path, ROOT / 'yoyo/datasets/ma_profit_dataset.py',
                    ROOT / 'yoyo/datasets/ma_morphology_redo.py', ROOT / 'yoyo/layers/l1_detection/render.py']
    commit = renderer._committed(dependencies)
    for identity in plan['inputs'].values():
        if sha(ROOT / identity['path']) != identity['sha256']:
            raise ValueError('Frozen input SHA drift: ' + identity['path'])
    parent = ROOT / plan['parent_dataset']
    recovery = json.loads(recovery_path.read_text())['events'] if recovery_path else {}
    events = [x for x in rows(ROOT / plan['inputs']['ledger']['path']) if x.get('morphology_label') == 'selected_grade_a_launch']
    if len(events) != 1841 or len({x['event_id'] for x in events}) != 1841:
        raise ValueError('Positive event population changed')
    if pilot:
        # Include evaluation events so new views are actually reconstructed.
        chosen = []
        for split in ('train', 'val', 'test'):
            pool = sorted((x for x in events if x['split'] == split), key=lambda x: x['event_id'])
            chosen.extend(pool[:pilot])
        events = chosen
    for event in events:
        validate_event(event, plan)
    old = {(x['event_id'], x['variant']): x for x in rows(ROOT / plan['inputs']['manifest']['path']) if x['class_id'] is not None}
    if output.exists():
        raise FileExistsError(output)
    for split in ('train', 'val', 'test'):
        for kind in ('images', 'labels'):
            (output / kind / split).mkdir(parents=True)
    checked_sources, manifest = set(), []
    reused = rendered = 0
    for index, event in enumerate(events, 1):
        available = {v: old.get((event['event_id'], parent_v)) for v, (_, parent_v) in VARIANTS.items()}
        new = {}
        if pilot or not all(available.values()):
            source = ROOT / event['source_path']
            recovered = None
            if not source.exists():
                recovered = recovery.get(event['event_id'])
                if not recovered or recovered['source_path'] != event['source_path'] or recovered['source_sha256'] != event['source_sha256']:
                    raise ValueError('Missing verified original source/prefix: ' + event['event_id'])
                relative = Path(recovered['path'])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Recovered prefix escapes repository')
                source = ROOT / relative
            if str(source) not in checked_sources:
                if sha(source) != (recovered['sha256'] if recovered else event['source_sha256']):
                    raise ValueError('Source SHA drift: ' + str(source))
                checked_sources.add(str(source))
            step = pd.Timedelta(minutes=int(event['bar_minutes']))
            frame = read_interval(source, utc(event['core_start_time']) - 1211 * step,
                                  utc(event['profit']['decision_close_time_utc']))
            new = render_views(frame, event)
            # Reconstruction must reproduce the original A, even when only P7/P11 are missing.
            for variant, old_row in available.items():
                if old_row and (hashlib.sha256(new[variant]['png']).hexdigest() != old_row['image_sha256']
                                or hashlib.sha256(new[variant]['label'].encode()).hexdigest() != old_row['label_sha256']):
                    raise ValueError('Original pixels/box not reproduced: ' + event['event_id'] + ' ' + variant)
        for variant, (pre, _) in VARIANTS.items():
            stem = renderer.asset_stem(event['event_id'], variant)
            image_rel = f'images/{event["split"]}/{stem}.png'
            label_rel = f'labels/{event["split"]}/{stem}.txt'
            old_row = available[variant]
            if old_row:
                _link_checked(parent / old_row['image_path'], output / image_rel, old_row['image_sha256'])
                _link_checked(parent / old_row['label_path'], output / label_rel, old_row['label_sha256'])
                image_sha, label_sha, box = old_row['image_sha256'], old_row['label_sha256'], old_row['box']
                reused += 1
            else:
                asset = new[variant]
                (output / image_rel).write_bytes(asset['png'])
                (output / label_rel).write_text(asset['label'])
                image_sha, label_sha, box = sha(output / image_rel), sha(output / label_rel), asset['box']
                rendered += 1
            step = pd.Timedelta(minutes=int(event['bar_minutes']))
            manifest.append({'event_id': event['event_id'], 'cluster_id': event['cluster_id'],
                'split': event['split'], 'variant': variant, 'pre_bars': pre, 'post_bars': 5,
                'core_bars': event['core_bars'], 'core_start_time': event['core_start_time'], 'core_end_time': event['core_end_time'],
                'direction': event['direction'], 'class_id': 0 if event['direction'] == 'LONG' else 1,
                'bar_minutes': event['bar_minutes'], 'canonical_asset': event['canonical_asset'],
                'source_path': event['source_path'], 'source_sha256': event['source_sha256'],
                'visible_start_utc': (utc(event['core_start_time']) - pre * step).isoformat(),
                'visible_end_close_time_utc': event['profit']['decision_close_time_utc'],
                'decision_at_utc': event['profit']['decision_close_time_utc'],
                'feature_support_start_utc': (utc(event['core_start_time']) - 1211 * step).isoformat(),
                'label_horizon_end_utc': event['profit']['label_window_end_utc'],
                'image_path': image_rel, 'image_sha256': image_sha, 'label_path': label_rel, 'label_sha256': label_sha, 'box': box,
                'parent_image_path': old_row['image_path'] if old_row else None,
                'source_recovery_manifest': str(recovery_path.relative_to(ROOT)) if recovery_path and event['event_id'] in recovery else None,
                'geometry_source': 'original_event_core_with_unchanged_renderer_rule',
                'positive_selection': 'parent_3R_retained_rule_candidate', 'sample_owner_confirmed': False,
                'training_eligible': False, 'production_eligible': False})
        if index % 100 == 0:
            print(json.dumps({'events_done': index, 'events_total': len(events), 'reused': reused, 'rendered': rendered}), flush=True)
    write_rows(output / 'positive_manifest.jsonl', manifest)
    write_rows(output / 'positive_events.jsonl', events)
    receipt = {'status': 'positive_preparation_complete', 'pilot': bool(pilot), 'source_commit': commit,
        'plan_sha256': sha(plan_path), 'positive_events': len(events), 'positive_images': len(manifest),
        'counts': dict(Counter(x['split'] for x in manifest)), 'reused_images': reused, 'newly_rendered_images': rendered,
        'manifest_sha256': sha(output / 'positive_manifest.jsonl'), 'events_sha256': sha(output / 'positive_events.jsonl'),
        'recovery_manifest_sha256': sha(recovery_path) if recovery_path else None,
        'training_ready': False, 'negative_status': 'requires_separate_compatibility_and_label_audit',
        'training_eligible': False, 'production_eligible': False}
    write_json(output / 'preparation_receipt.json', receipt)
    result = audit(output)
    write_json(output / 'positive_audit.json', result)
    return {**receipt, 'audit': result}


def audit(output: Path) -> dict:
    """Check per-event clocks, class/geometry, bytes, variants and split isolation."""
    receipt = json.loads((output / 'preparation_receipt.json').read_text())
    if sha(output / 'positive_manifest.jsonl') != receipt['manifest_sha256'] or sha(output / 'positive_events.jsonl') != receipt['events_sha256']:
        raise ValueError('Prepared metadata SHA drift')
    manifest = rows(output / 'positive_manifest.jsonl')
    events, seen, centers = {}, {}, set()
    for row in manifest:
        event = events.setdefault(row['event_id'], {'split': row['split'], 'variants': set(), 'decision': row['decision_at_utc']})
        if row['split'] != event['split'] or row['decision_at_utc'] != event['decision'] or row['variant'] in event['variants']:
            raise ValueError('Cross-split/event-clock or repeated variant')
        event['variants'].add(row['variant'])
        if row['visible_end_close_time_utc'] != row['decision_at_utc'] or row['post_bars'] != 5:
            raise ValueError('Future-visible variant')
        for kind in ('image', 'label'):
            relative = Path(row[kind + '_path'])
            if relative.is_absolute() or '..' in relative.parts or sha(output / relative) != row[kind + '_sha256']:
                raise ValueError('Asset path or SHA drift')
        if row['image_sha256'] in seen:
            raise ValueError('Duplicate image pixels/file')
        seen[row['image_sha256']] = row['event_id']
        image = cv2.imread(str(output / row['image_path']))
        if image is None or image.shape != (742, 1280, 3):
            raise ValueError('Invalid image canvas')
        values = [float(x) for x in (output / row['label_path']).read_text().split()]
        if len(values) != 5 or not np.isfinite(values).all() or values[0] != row['class_id'] or values[0] not in (0, 1):
            raise ValueError('Invalid class or detection label')
        _, cx, cy, width, height = values
        if width <= 0 or height <= 0 or not (0 <= cx-width/2 <= cx+width/2 <= 1 and 0 <= cy-height/2 <= cy+height/2 <= 1):
            raise ValueError('Invalid box geometry')
        centers.add(round(cx, 8))
    if any(e['variants'] != set(VARIANTS) for e in events.values()):
        raise ValueError('Event lacks exactly three variants')
    expected = {'train': 4518, 'val': 525, 'test': 480}
    counts = dict(Counter(x['split'] for x in manifest))
    if not receipt['pilot'] and (counts != expected or len(events) != 1841 or len(manifest) != 5523):
        raise ValueError('Full preparation membership/count mismatch')
    return {'status': 'passed', 'events': len(events), 'images': len(manifest), 'counts': counts,
            'unique_horizontal_centers': sorted(centers), 'max_views_per_event': 3,
            'training_ready': False, 'negative_gate_complete': False}


def negative_inventory(plan_path: Path, output: Path) -> dict:
    """Record why old negative image bytes are not a ready mixed-timeframe pool."""
    plan = json.loads(plan_path.read_text())
    grade_manifest = ROOT / 'datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1/manifest.jsonl'
    negatives = [x for x in rows(grade_manifest) if x.get('sample_kind') == 'negative']
    unique = {x['negative_event_id']: x for x in negatives}
    positive = [x for x in rows(ROOT / plan['inputs']['ledger']['path']) if x.get('morphology_label') == 'selected_grade_a_launch']
    period_counts = Counter()
    for row in unique.values():
        minutes = int((utc(row['core_end_time']) - utc(row['core_start_time'])).total_seconds() / 60 / (row['core_bars'] - 1))
        period_counts[minutes] += 1
    positive_periods = Counter(x['bar_minutes'] for x in positive)
    source_paths = {x['source_path'] for x in unique.values()}
    result = {'schema': 'grade-a-v6-negative-compatibility-v1',
        'grade_manifest_sha256': sha(grade_manifest), 'v6_ledger_sha256': plan['inputs']['ledger']['sha256'],
        'negative_images': len(negatives), 'negative_events': len(unique),
        'negative_event_splits': dict(Counter(x['split'] for x in unique.values())),
        'negative_event_kinds': dict(Counter(x['negative_kind'] for x in unique.values())),
        'negative_periods': dict(period_counts), 'positive_periods': dict(positive_periods),
        'source_paths': len(source_paths), 'source_paths_existing': sum((ROOT / x).exists() for x in source_paths),
        'source_sha_audit_performed': False, 'direct_png_reuse_ready': False, 'training_ready': False,
        'reasons': ['Existing Grade-A image geometry, colors/MA representation and 7/8 variants differ from new HL2 three-view inputs.',
                    'Old negatives are 15m-only; using them as all multi-timeframe negatives creates an unmatched population.',
                    'Old val overlaps new validation/test calendar; cannot promote it into training.',
                    'Hard labels mean no launch, not no density or loss; reuse only under explicit dense-launch semantics and observable post-five criteria.',
                    'Re-render and recheck source identity, candidate/gold overlap, chronological partition and complete visible-window label coverage.'],
        'candidate_policy': 'Consider old train event identities as a 15m negative candidate pool; do not copy PNGs or treat this inventory as label approval.',
        'training_eligible': False, 'production_eligible': False}
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'audit', 'audit-negatives'])
    parser.add_argument('--plan', type=Path, default=DEFAULT_PLAN)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pilot', type=int, default=0, help='events per split')
    parser.add_argument('--recovery', type=Path)
    args = parser.parse_args()
    if args.action == 'build':
        result = build(args.plan, args.output, args.pilot, args.recovery.resolve() if args.recovery else None)
    elif args.action == 'audit-negatives':
        result = negative_inventory(args.plan, args.output)
    else:
        result = audit(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
