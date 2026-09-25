"""Assemble the owner-requested shared-timeframe three-view morphology dataset.

Positive/core bytes and the v6 clear-background evidence remain frozen. Grade-A
negative event identities are re-screened and re-rendered using the same HL2
renderer and pre7/9/11,post5 geometry. Source SHA values describe the current
frozen source version, not an unsupported claim about the legacy full CSV.
All screening uses OHLC only through core+5; profit labels are never consulted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as render
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_launch_owner_yolo_dataset import negative_feature_masks
from yoyo.datasets.ma_morphology_redo import (
    in_split, protection_intervals, read_interval, rows, sha, utc, write_json, write_rows,
)
from yoyo.datasets.ma_profit_cohort import canonical_asset

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-ma-morphology-v6-threeview-20260925-v1'
PLAN = EXP / 'assembly_plan.json'
VIEWS = {'P7': 7, 'P9': 9, 'P11': 11}
OLD_VIEWS = {'A': 'P9', 'B1': 'P7', 'B2': 'P11'}


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text())
    if plan['views'] != {'pre': [7, 9, 11], 'post': 5}:
        raise ValueError('View contract drift')
    for item in plan['inputs'].values():
        if sha(ROOT / item['path']) != item['sha256']:
            raise ValueError('Frozen input changed: ' + item['path'])
    return plan


def source_inventory(plan_path: Path, output: Path) -> dict:
    plan = load_plan(plan_path)
    commit = render._committed([Path(__file__), plan_path])
    if output.exists():
        raise FileExistsError(output)
    source_paths = sorted({r['source_path'] for r in rows(ROOT / plan['inputs']['grade_manifest']['path'])
                           if r.get('sample_kind') == 'negative'})
    result = {'source_commit': commit, 'plan_sha256': sha(plan_path),
              'identity_scope': 'current_source_version_not_legacy_whole_source_parity',
              'sources': {p: {'sha256': sha(ROOT / p), 'size_bytes': (ROOT / p).stat().st_size} for p in source_paths}}
    write_json(output, result)
    return {'sources': len(source_paths), 'sha256': sha(output)}


def event_split(row: dict, parent_plan: dict) -> str | None:
    """Preserve chronology; old validation events never become training rows."""
    start = utc(row['core_start_time']) - pd.Timedelta(minutes=15 * 11)
    decision = utc(row['core_end_time']) + pd.Timedelta(minutes=15 * 6)
    for split in ('train', 'val', 'test'):
        if in_split(start, decision, split, parent_plan):
            if row['split'] == 'val' and split == 'train':
                return None
            return split
    return None


def overlaps(start, end, intervals) -> bool:
    return any(start <= hi and end >= lo for lo, hi in intervals)


def _density(mask: dict, config: dict) -> np.ndarray:
    return ((mask['ma_envelope_atr'] <= config['ma_envelope_atr_max'])
            & (mask['ma_spread_end_atr'] <= config['ma_spread_end_atr_max'])
            & (mask['max_body_atr'] <= config['max_body_atr_max'])
            & (mask['candle_envelope_atr'] <= config['candle_envelope_atr_max'])
            & (mask['minimum_close_to_ma_atr'] <= config['minimum_close_to_ma_atr_max']))


def screen_negative(frame: pd.DataFrame, row: dict, protocol: dict) -> tuple[dict, pd.DataFrame]:
    """Check old class and all complete core+5 subwindows of the visible image.

    Uses open_time/OHLC and inherited ATR14, CLOSE/HL2 SMA/EMA20/60/120.
    Only 1200 warmup bars + pre11 + core4/5 + post5 enter the indicators.
    Dense subwindows with observable movement outside the existing no-launch
    bounds are ambiguous and are not assigned an empty detection label.
    """
    start_time, end_time = utc(row['core_start_time']), utc(row['core_end_time'])
    step = pd.Timedelta(minutes=15)
    visible_start, decision = start_time - 11 * step, end_time + 6 * step
    support = frame.loc[(frame.open_time >= visible_start - 1200 * step)
                        & (frame.open_time < decision)].copy().reset_index(drop=True)
    n = int(row['core_bars'])
    reasons = []
    if n not in (4, 5) or len(support) != 1211 + n + 5:
        return {'accepted': False, 'reasons': ['incomplete_context']}, support
    if not support.open_time.diff().iloc[1:].eq(step).all():
        return {'accepted': False, 'reasons': ['known_gap']}, support
    if support.open_time.iloc[1211] != start_time or support.open_time.iloc[1210+n] != end_time:
        raise ValueError('Core timestamp/index binding drift')
    values = support[['open', 'high', 'low', 'close']].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values <= 0).any()
            or (support.high < support[['open', 'close', 'low']].max(axis=1)).any()
            or (support.low > support[['open', 'close', 'high']].min(axis=1)).any()):
        return {'accepted': False, 'reasons': ['invalid_ohlc']}, support
    visible = support.iloc[1200:]
    if visible.close.nunique() < 4 or int((visible.high > visible.low).sum()) < 4:
        return {'accepted': False, 'reasons': ['inactive']}, support
    close_frame = add_candidate_features(support)
    hl2_frame = render.add_hl2_mas(close_frame)
    core_end = 1210 + n
    metrics, ambiguous = {}, []
    old_fields = {'ma_envelope_atr': 'ma_envelope_atr', 'ma_spread_end_atr': 'ma_spread_end_atr',
                  'max_body_atr': 'max_body_atr', 'candle_envelope_atr': 'candle_envelope_atr',
                  'minimum_close_to_ma_atr': 'minimum_close_to_ma_atr',
                  'abs_close_progress_atr_core_plus_2': 'close2',
                  'abs_close_progress_atr_core_plus_3': 'close3',
                  'abs_close_progress_atr_core_plus_5': 'close5',
                  'two_sided_excursion_atr_core_plus_1_to_5': 'excursion'}
    for basis, enriched in (('close', close_frame), ('hl2', hl2_frame)):
        cache = {length: negative_feature_masks(enriched, core_len=length, prereg=protocol) for length in (4, 5)}
        target = cache[n]
        if not target[row['negative_kind']][core_end]:
            reasons.append(basis + '_legacy_kind_not_reproduced')
        metrics[basis] = {key: float(target[key][core_end]) for key in set(old_fields.values())}
        if basis == 'close':
            for old, key in old_fields.items():
                if old not in row or not np.isclose(row[old], target[key][core_end], rtol=1e-5, atol=1e-6):
                    reasons.append('legacy_feature_identity_drift')
                    break
        for length, mask in cache.items():
            dense = _density(mask, protocol['negative_sampling']['hard_definition'])
            # hard = dense AND all frozen no-launch conditions. A dense core
            # that fails this condition might be another target in the image.
            for end in range(1200 + length - 1, len(support) - 5):
                if dense[end] and not mask['hard'][end]:
                    ambiguous.append({'basis': basis, 'core_bars': length, 'end_in_visible': end - 1200})
    if ambiguous:
        reasons.append('other_visible_dense_motion')
    return {'accepted': not reasons, 'reasons': sorted(set(reasons)), 'metrics': metrics,
            'ambiguous_subwindows': ambiguous, 'uses_profit_outcome': False,
            'visible_end_close_time_utc': decision.isoformat()}, support


def _link(parent: Path, output: Path, row: dict) -> None:
    for kind in ('image', 'label'):
        source = parent / row[kind + '_path']
        if sha(source) != row[kind + '_sha256']:
            raise ValueError('Parent asset hash drift: ' + str(source))
        dest = output / row[kind + '_path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, dest)


def audit_assets(output: Path, manifest: list[dict]) -> dict:
    events, pixels = {}, {}
    for row in manifest:
        state = events.setdefault(row['event_id'], {'split': row['split'], 'views': set(), 'decision': row['decision_at_utc']})
        if state['split'] != row['split'] or state['decision'] != row['decision_at_utc'] or row['variant'] in state['views']:
            raise ValueError('Event split/clock/view drift')
        state['views'].add(row['variant'])
        if not state['views'] <= set(VIEWS) or row['visible_end_close_time_utc'] != row['decision_at_utc']:
            raise ValueError('Unexpected view or future visibility')
        for kind in ('image', 'label'):
            relative = Path(row[kind + '_path'])
            if relative.is_absolute() or '..' in relative.parts or sha(output / relative) != row[kind + '_sha256']:
                raise ValueError('Asset hash/path drift')
        old = pixels.setdefault(row['image_sha256'], row['event_id'])
        if old != row['event_id']:
            raise ValueError('Repeated pixels across distinct events')
        image = cv2.imread(str(output / row['image_path']))
        if image is None or image.shape != (742, 1280, 3):
            raise ValueError('Image geometry drift')
        label = (output / row['label_path']).read_text().strip()
        if row['class_id'] is None:
            if label or row['sample_kind'] != 'negative':
                raise ValueError('Negative label mismatch')
        else:
            fields = [float(x) for x in label.split()]
            if len(fields) != 5 or fields[0] != row['class_id'] or row['class_id'] not in (0, 1) or not np.isfinite(fields).all():
                raise ValueError('Positive label mismatch')
            _, cx, cy, w, h = fields
            if w <= 0 or h <= 0 or not (0 <= cx-w/2 <= cx+w/2 <= 1 and 0 <= cy-h/2 <= cy+h/2 <= 1):
                raise ValueError('Positive box outside image')
    return {'status': 'passed', 'events': len(events), 'images': len(manifest), 'max_views_per_event': max(len(x['views']) for x in events.values()),
            'counts': dict(Counter(f"{r['split']}:{r['sample_kind']}:{r['evaluation_pool']}" for r in manifest))}


def build(plan_path: Path, sources_path: Path, output: Path, pilot: int = 0) -> dict:
    plan = load_plan(plan_path)
    commit = render._committed([Path(__file__), plan_path, sources_path,
        ROOT/'yoyo/datasets/ma_profit_dataset.py', ROOT/'yoyo/datasets/ma_launch_owner_yolo_dataset.py',
        ROOT/'yoyo/datasets/ma_morphology_redo.py', ROOT/'yoyo/datasets/fifteen_minute_launch_candidates.py',
        ROOT/'yoyo/datasets/ma_rope_filter.py', ROOT/'yoyo/layers/l1_detection/render.py',
        ROOT/'yoyo/datasets/ma_launch_owner_recrop_review.py'])
    sources = json.loads(sources_path.read_text())
    if sources['plan_sha256'] != sha(plan_path):
        raise ValueError('Sources belong to another plan')
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    parent_plan = json.loads((ROOT/plan['inputs']['parent_plan']['path']).read_text())
    protocol = json.loads((ROOT/plan['inputs']['grade_protocol']['path']).read_text())
    parent = ROOT / plan['parent_dataset']
    positives = ROOT / plan['positive_dataset']
    pos = rows(positives/'positive_manifest.jsonl')
    if pilot:
        selected = set()
        for split in ('train', 'val', 'test'):
            selected.update(sorted({r['event_id'] for r in pos if r['split'] == split})[:2])
        pos = [r for r in pos if r['event_id'] in selected]
    selected = {r['event_id'] for r in pos}
    manifest = []
    for row in pos:
        row = {**row, 'sample_kind': 'positive', 'evaluation_pool': 'reference'}
        _link(positives, output, row)
        manifest.append(row)
    protected = protection_intervals(parent_plan)
    used = defaultdict(list)
    for row in rows(parent/'manifest.jsonl'):
        if row['class_id'] is not None:
            continue
        # Protect all inherited background events, even in a small pilot.
        used[canonical_asset(row['canonical_asset'])].append((utc(row['visible_start_utc']), utc(row['decision_at_utc'])))
        if pilot and row['paired_positive_event_id'] not in selected:
            continue
        _link(parent, output, row)
        manifest.append({**row, 'sample_kind': 'negative', 'evaluation_pool': 'reference',
                         'variant': OLD_VIEWS[row['variant']]})
    grade_rows = rows(ROOT/plan['inputs']['grade_manifest']['path'])
    negatives = {}
    margin = pd.Timedelta(hours=parent_plan['negative_sampling']['protection_hours'])
    seen_positive = set()
    for row in grade_rows:
        if row.get('sample_kind') == 'negative':
            negatives.setdefault(row['negative_event_id'], row)
        elif row['event_id'] not in seen_positive:
            seen_positive.add(row['event_id'])
            protected.setdefault(canonical_asset(row['symbol']), []).append((utc(row['core_start_time'])-margin, utc(row['core_end_time'])+pd.Timedelta(minutes=15)+margin))
    candidates = list(negatives.values())
    if pilot:
        buckets = defaultdict(list)
        for row in candidates:
            buckets[(row['split'], row['negative_kind'])].append(row)
        candidates = []
        for key in sorted(buckets):
            candidates.extend(sorted(buckets[key], key=lambda r: r['negative_event_id'])[:pilot])
    grouped = defaultdict(list)
    exclusions, evidence_rows = [], []
    for row in candidates:
        ident = 'grade-a-negative::' + row['negative_event_id']
        split = event_split(row, parent_plan)
        start = utc(row['core_start_time'])-pd.Timedelta(minutes=165)
        decision = utc(row['core_end_time'])+pd.Timedelta(minutes=90)
        asset = canonical_asset(row['symbol'])
        reason = ('chronology_or_old_validation' if split is None else
                  'candidate_or_gold_protection' if overlaps(start, decision, protected.get(asset, [])) else
                  'inherited_background_overlap' if overlaps(start, decision, used[asset]) else None)
        if reason:
            exclusions.append({'event_id': ident, 'reason': reason})
        else:
            grouped[row['source_path']].append((row, split))
    for source_i, (source_path, group) in enumerate(sorted(grouped.items()), 1):
        expected = sources['sources'][source_path]['sha256']
        if sha(ROOT/source_path) != expected:
            raise ValueError('Frozen source changed: '+source_path)
        lo = min(utc(r['core_start_time']) for r, _ in group)-pd.Timedelta(minutes=15*1211)
        hi = max(utc(r['core_end_time']) for r, _ in group)+pd.Timedelta(minutes=90)
        frame = read_interval(ROOT/source_path, lo, hi)
        for row, split in sorted(group, key=lambda item: (item[0]['core_start_time'], item[0]['negative_event_id'])):
            ident = 'grade-a-negative::'+row['negative_event_id']
            asset = canonical_asset(row['symbol'])
            start = utc(row['core_start_time'])-pd.Timedelta(minutes=165)
            decision = utc(row['core_end_time'])+pd.Timedelta(minutes=90)
            if overlaps(start, decision, used[asset]):
                exclusions.append({'event_id': ident, 'reason': 'negative_event_overlap'})
                continue
            screen, support = screen_negative(frame, row, protocol)
            evidence_rows.append({'event_id': ident, 'source_path': source_path, 'source_sha256': expected,
                                  'legacy_kind': row['negative_kind'], 'screen': screen})
            if not screen['accepted']:
                exclusions.append({'event_id': ident, 'reason': '|'.join(screen['reasons'])})
                continue
            used[asset].append((start, decision))
            core_start, core_end = 1211, 1210+row['core_bars']
            for variant, pre in VIEWS.items():
                png, _, _ = render._window_asset(support, core_start_i=core_start, core_end_i=core_end,
                    pre_bars=pre, post_bars=5, support_start_i=0, price_scale=render.VISIBLE_RANGE_PRICE_SCALE)
                stem = render.asset_stem(ident, variant)
                image_path, label_path = f'images/{split}/{stem}.png', f'labels/{split}/{stem}.txt'
                (output/image_path).parent.mkdir(parents=True, exist_ok=True)
                (output/label_path).parent.mkdir(parents=True, exist_ok=True)
                (output/image_path).write_bytes(png)
                (output/label_path).write_text('')
                manifest.append({'event_id': ident, 'cluster_id': ident, 'split': split, 'variant': variant,
                    'sample_kind': 'negative', 'evaluation_pool': 'grade_a_challenge', 'class_id': None,
                    'negative_kind': 'grade_a_'+row['negative_kind'], 'pre_bars': pre, 'post_bars': 5,
                    'bar_minutes': 15, 'core_bars': row['core_bars'], 'core_start_time': row['core_start_time'],
                    'core_end_time': row['core_end_time'], 'canonical_asset': asset, 'source_path': source_path,
                    'source_sha256': expected, 'legacy_negative_event_id': row['negative_event_id'], 'legacy_split': row['split'],
                    'decision_at_utc': decision.isoformat(), 'visible_end_close_time_utc': decision.isoformat(),
                    'visible_start_utc': (utc(row['core_start_time'])-pd.Timedelta(minutes=15*pre)).isoformat(),
                    'image_path': image_path, 'label_path': label_path, 'image_sha256': hashlib.sha256(png).hexdigest(),
                    'label_sha256': hashlib.sha256(b'').hexdigest(), 'sample_owner_confirmed': False,
                    'profit_used_for_negative_label': False, 'training_eligible': False, 'production_eligible': False})
        if source_i % 20 == 0:
            print(json.dumps({'sources_done': source_i, 'sources_total': len(grouped), 'images': len(manifest), 'rejected': len(exclusions)}), flush=True)
    write_rows(output/'manifest.jsonl', manifest)
    write_rows(output/'negative_screening.jsonl', evidence_rows)
    write_rows(output/'excluded_candidates.jsonl', exclusions)
    audit = audit_assets(output, manifest)
    train_pos = sum(r['split']=='train' and r['sample_kind']=='positive' for r in manifest)
    train_neg = sum(r['split']=='train' and r['sample_kind']=='negative' for r in manifest)
    hard_train = sum(r['split']=='train' and r.get('negative_kind')=='grade_a_hard' for r in manifest)
    if not pilot and (train_pos != 4518 or hard_train == 0):
        raise ValueError('Missing fixed positives or accepted hard training negatives')
    receipt = {'source_commit': commit, 'plan_sha256': sha(plan_path), 'sources_sha256': sha(sources_path),
        'manifest_sha256': sha(output/'manifest.jsonl'), 'screening_sha256': sha(output/'negative_screening.jsonl'),
        'exclusions_sha256': sha(output/'excluded_candidates.jsonl'), 'dataset_ready': not bool(pilot), 'pilot': bool(pilot),
        'train_positive_images': train_pos, 'train_negative_images': train_neg, 'train_grade_a_hard_images': hard_train,
        'grade_a_events_accepted': sum(e['screen']['accepted'] for e in evidence_rows),
        'grade_a_events_rejected': len(exclusions), 'rejection_counts': dict(Counter(e['reason'] for e in exclusions)),
        'audit': audit, 'training_started': False, 'training_eligible': False, 'production_eligible': False}
    write_json(output/'build_receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze-sources', 'build'])
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--pilot', type=int, default=0)
    args = parser.parse_args()
    result = source_inventory(args.plan, args.output) if args.action == 'freeze-sources' else build(args.plan, args.sources, args.output, args.pilot)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
