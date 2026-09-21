"""Freeze and label owner-authorized Grade-A profit research events.

Source discovery and chart features never consume profit labels. This module
alone joins the known core/confirmation to its later twelve-hour outcome.
All original artifacts are read-only; outputs belong to a new experiment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.contracts.ma_profit_filter import resolve_ma_profit_event
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n')


def write_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')


def committed(paths):
    names = [str(Path(p).resolve().relative_to(ROOT)) for p in paths]
    if subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip() != 'main':
        raise ValueError('main required')
    dirty = subprocess.check_output(['git', 'status', '--porcelain', '--', *names], cwd=ROOT, text=True)
    if dirty.strip():
        raise ValueError('commit generator and frozen inputs first: ' + dirty)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def read_source(path, bar_minutes, cutoff):
    frame, _ = read_preholdout_prefix(Path(path), end_exclusive=pd.Timestamp(cutoff), bar_minutes=int(bar_minutes))
    # read_preholdout_prefix bounds opens; retain only fully closed bars.
    closes = pd.to_datetime(frame.open_time, utc=True) + pd.Timedelta(minutes=int(bar_minutes))
    return frame.loc[closes <= pd.Timestamp(cutoff)].reset_index(drop=True)


def prepare_legacy(plan_path):
    plan = json.loads(Path(plan_path).read_text())
    root = Path(plan_path).parent
    manifest = ROOT / plan['discovery']['legacy_manifest']
    groups = defaultdict(list)
    for row in read_rows(manifest):
        if row.get('sample_kind') == 'positive':
            groups[row['event_id']].append(row)
    if len(groups) != plan['discovery']['legacy_expected_positive_events']:
        raise ValueError(f'legacy event count drift: {len(groups)}')
    events = []
    for event_id, variants in sorted(groups.items()):
        r = min(variants, key=lambda x: (abs(int(x['post_bars']) - 5), int(x['variant_index'])))
        keys = ('symbol', 'direction', 'source_path', 'core_start_time', 'core_end_time',
                'source_core_start_i', 'source_core_end_i', 'quality_score', 'venue', 'core_bars')
        events.append({'event_id': event_id, 'bar_minutes': 15, 'origin': 'original1043',
                       **{key: r[key] for key in keys}})
    sources = []
    for name in sorted({r['source_path'] for r in events}):
        path = ROOT / name
        sources.append({'source_path': name, 'sha256': digest(path), 'size_bytes': path.stat().st_size})
    write_rows(root / 'legacy_events.jsonl', events)
    dump(root / 'legacy_sources.json', sources)
    dump(root / 'legacy_freeze_receipt.json', {'manifest': str(manifest.relative_to(ROOT)),
         'manifest_sha256': digest(manifest), 'events': len(events), 'sources': len(sources),
         'events_sha256': digest(root / 'legacy_events.jsonl')})
    print(json.dumps({'events': len(events), 'sources': len(sources)}), flush=True)


def split_for_event(frame, start_i, end_i, minutes, plan):
    support_i = start_i - 11
    if support_i - 1200 < 0:
        return 'purged', 'insufficient_1200bar_warmup'
    # Earlier prices used only to initialize MAs are legitimately known history.
    # The rendered event interval and future label must not cross split cuts.
    support_start = pd.Timestamp(frame.open_time.iloc[support_i])
    decision_i = end_i + plan['label_contract']['confirmation_bars']
    if decision_i >= len(frame):
        return 'purged', 'missing_confirmation'
    support_end = pd.Timestamp(frame.open_time.iloc[decision_i]) + pd.Timedelta(
        minutes=minutes, hours=plan['label_contract']['horizon_hours'])
    boundaries = [pd.Timestamp(plan['splits'][key]) for key in (
        'train_end_exclusive', 'validation_end_exclusive', 'test_end_exclusive')]
    for split, left, right in zip(('train', 'val', 'test'),
                                 (pd.Timestamp('1900-01-01T00:00Z'), *boundaries[:2]), boundaries):
        if support_start >= left and support_end < right:
            return split, ''
    return 'purged', 'support_crosses_time_split'


def label(plan_path, events_path, sources_path, output):
    plan_path, events_path, sources_path, output = map(Path, (plan_path, events_path, sources_path, output))
    if output.exists():
        raise FileExistsError(output)
    commit = committed([Path(__file__), ROOT / 'yoyo/contracts/ma_profit_filter.py',
                        plan_path, events_path, sources_path])
    plan = json.loads(plan_path.read_text())
    specs = json.loads(sources_path.read_text())
    if isinstance(specs, dict):
        specs = specs['sources']
    pins = {r.get('source_path', r.get('path')): r.get('sha256', r.get('prefix_sha256')) for r in specs}
    groups = defaultdict(list)
    for row in read_rows(events_path):
        groups[(row['source_path'], int(row.get('bar_minutes', 15)))].append(row)
    output.mkdir(parents=True)
    result, errors = [], []
    for order, ((source, minutes), events) in enumerate(sorted(groups.items()), 1):
        actual = digest(ROOT / source)
        if actual != pins.get(source):
            raise ValueError('source SHA drift or source unpinned: ' + source)
        frame = read_source(ROOT / source, minutes, plan['discovery']['data_end_exclusive'])
        times = pd.DatetimeIndex(pd.to_datetime(frame.open_time, utc=True))
        for event in events:
            s, e = times.get_indexer(pd.to_datetime([event['core_start_time'], event['core_end_time']], utc=True))
            if s < 0 or e < s or e - s + 1 != int(event['core_bars']):
                errors.append({'event_id': event['event_id'], 'error': 'core_timestamp_lineage'})
                continue
            args = plan['label_contract']
            profit = resolve_ma_profit_event(frame, int(s), int(e), event['direction'],
                bar_minutes=minutes, confirmation_bars=args['confirmation_bars'],
                horizon_hours=args['horizon_hours'], target_r=args['target_r'], round_trip_cost=args['round_trip_cost'])
            split, reason = split_for_event(frame, int(s), int(e), minutes, plan)
            result.append({**event, 'bar_minutes': minutes, 'source_sha256': actual,
                'source_core_start_i': int(s), 'source_core_end_i': int(e), 'profit': profit,
                'split': split, 'purge_reason': reason, 'sample_owner_confirmed': False,
                'production_eligible': False})
        if order % 25 == 0 or order == len(groups):
            print(f'label sources {order}/{len(groups)} events={len(result)} retained={sum(x["profit"]["retained"] for x in result)}', flush=True)
    write_rows(output / 'outcomes.jsonl', result)
    write_rows(output / 'lineage_errors.jsonl', errors)
    counts = Counter(r['profit']['outcome'] for r in result)
    retained = [r for r in result if r['profit']['retained']]
    summary = {'builder_commit': commit, 'input_events_sha256': digest(events_path),
        'source_manifest_sha256': digest(sources_path), 'plan_sha256': digest(plan_path),
        'events': len(result), 'lineage_errors': len(errors), 'outcomes': dict(counts),
        'retained_before_purge': len(retained), 'retained_by_split': dict(Counter(r['split'] for r in retained)),
        'all_by_split': dict(Counter(r['split'] for r in result)),
        'gross_r_target': args['target_r'], 'cost': args['round_trip_cost'],
        'outcomes_sha256': digest(output / 'outcomes.jsonl'), 'production_eligible': False}
    dump(output / 'summary.json', summary)
    print(json.dumps(summary), flush=True)
    if errors:
        raise ValueError('lineage errors: inspect output')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('prepare-legacy', 'label'))
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--events', type=Path)
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare-legacy':
        prepare_legacy(args.plan)
    else:
        label(args.plan, args.events, args.sources, args.out)


if __name__ == '__main__':
    main()
