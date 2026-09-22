"""Build morphology backgrounds without converting trading failures into labels.

Owner correction 2026-09-22: retain the selected profitable positive images,
but empty detection labels require independent visible morphology evidence.
This builder uses same-source/time-block backgrounds, protects every known
candidate and owner review, and keeps unresolved trading failures quarantined.
The screen uses only OHLC through the displayed decision close. Future profit
is retained only in positive selection provenance, never negative eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as render
from yoyo.datasets.ma_morphology_background import screen_window
from yoyo.datasets.ma_profit_cohort import canonical_asset

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/active/exp-ma-morphology-negatives-20260922-v3'
DEFAULT_PLAN = EXPERIMENT / 'plan.json'


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def write_rows(path: Path, value: list[dict]) -> None:
    path.write_text(''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in value))


def utc(value: Any) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t.tz_convert('UTC')


def load_plan(path: Path) -> dict:
    p = read_json(path)
    if p['experiment_id'] != EXPERIMENT.name or not p['owner_authorization']['training_authorized']:
        raise ValueError('Wrong or unauthorized morphology plan')
    if p['training_eligible'] or p['production_eligible']:
        raise ValueError('Offline-only flags drift')
    for value in p['inputs'].values():
        if sha(ROOT / value['path']) != value['sha256']:
            raise ValueError('Input drift: ' + value['path'])
    legacy = read_json(ROOT/p['inputs']['legacy_negative_protocol']['path'])
    if p['negative_sampling']['whole_visible_ma_spread_atr_min'] != legacy['negative_sampling']['easy_definition']['ma_spread_end_atr_min_any']:
        raise ValueError('Frozen morphology threshold changed')
    return p


def halfyear(t: pd.Timestamp) -> tuple[int, int]:
    return t.year, 1 if t.month <= 6 else 2


def in_split(start: pd.Timestamp, decision: pd.Timestamp, split: str, p: dict) -> bool:
    """Keep visible input and a conservative 12h safety interval inside split."""
    a, b, c = [utc(p['splits'][k]) for k in ('train_end_exclusive', 'validation_end_exclusive', 'test_end_exclusive')]
    lo, hi = {'train': (None, a), 'val': (a, b), 'test': (b, c)}[split]
    return (lo is None or start >= lo) and decision + pd.Timedelta(hours=12) < hi


def protection_intervals(p: dict) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """Protect ALL candidate shapes regardless of trading outcomes, plus gold."""
    result = defaultdict(list)
    margin = pd.Timedelta(hours=p['negative_sampling']['protection_hours'])
    for row in rows(ROOT / p['inputs']['candidate_ledger']['path']):
        symbol = canonical_asset(row.get('canonical_asset') or row['symbol'])
        result[symbol].append((utc(row['core_start_time']) - margin,
                               utc(row['core_end_time']) + pd.Timedelta(minutes=int(row['bar_minutes'])) + margin))
    for row in rows(ROOT / p['inputs']['manual_rows']['path']):
        result[canonical_asset(row['symbol'])].append((utc(row['window_start_time']) - margin,
            utc(row['decision_time']) + pd.Timedelta(minutes=15) + margin))
    for row in read_json(ROOT / p['inputs']['reference_exclusion']['path'])['events']:
        t = utc(row.get('anchor_time') or row['core_end_time'])
        result[canonical_asset(row['symbol'])].append((t - margin, t + margin))
    return dict(result)


def read_interval(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Read bounded source rows; all feature calculations occur after clipping.

    Source CSV chunks can contain later raw rows, but no rows outside this
    interval enter the returned frame, the screen, or the renderer.
    """
    header = pd.read_csv(path, nrows=0).columns
    time_col = 'ts' if 'ts' in header else 'open_time'
    needed = [time_col, 'open', 'high', 'low', 'close', 'volume']
    chunks, offset = [], 0
    for chunk in pd.read_csv(path, usecols=needed, chunksize=65536):
        raw = chunk[time_col]
        if pd.api.types.is_numeric_dtype(raw):
            times = pd.to_datetime(raw, unit='ms', utc=True)
        else:
            times = pd.to_datetime(raw, utc=True, format='mixed')
        if not times.is_monotonic_increasing:
            raise ValueError('Unsorted source: ' + str(path))
        keep = (times >= start) & (times < end)
        if keep.any():
            view = chunk.loc[keep].copy()
            view['open_time'] = times.loc[keep]
            view['_source_i'] = np.arange(offset, offset + len(chunk))[keep.to_numpy()]
            chunks.append(view.drop(columns=['ts'], errors='ignore'))
        offset += len(chunk)
        if len(times) and times.iloc[-1] >= end:
            break
    if not chunks:
        raise ValueError('Empty source interval')
    frame = pd.concat(chunks, ignore_index=True)
    if not frame['open_time'].is_monotonic_increasing:
        raise ValueError('Unsorted chunk boundary')
    return frame


def choose_pilot(positives: list[dict], count: int) -> list[dict]:
    """Deterministic timeframe/side round-robin; never based on model scores."""
    buckets = defaultdict(list)
    for row in positives:
        if row['split'] == 'train':
            buckets[(row['bar_minutes'], row['direction'])].append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda x: hashlib.sha256(x['event_id'].encode()).hexdigest())
    chosen = []
    while len(chosen) < count and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(chosen) < count:
                chosen.append(buckets[key].pop(0))
    return chosen


def candidate_indices(frame: pd.DataFrame, positive: dict, p: dict) -> list[int]:
    """Same source and nearby time block, with a deterministic hourly grid."""
    times = frame['open_time']
    origin = utc(positive['core_end_time'])
    step = max(int(positive['bar_minutes']), p['negative_sampling']['candidate_step_minutes_minimum'])
    radius = p['negative_sampling']['search_radius_days'] * 1440
    wanted = [origin + pd.Timedelta(minutes=sign * d)
              for d in range(step, radius + 1, step) for sign in (-1, 1)]
    lookup = {t: i for i, t in enumerate(times)}
    return [lookup[t] for t in wanted if t in lookup and halfyear(t) == halfyear(origin)]


def choose_background(frame: pd.DataFrame, positive: dict, p: dict, protected: list, used: list) -> tuple[dict | None, Counter]:
    rejected = Counter()
    minutes, core_bars = int(positive['bar_minutes']), int(positive['core_bars'])
    times = frame['open_time']
    for end_i in candidate_indices(frame, positive, p):
        start_i = end_i - core_bars + 1
        visible_start, visible_end = start_i - 11, end_i + 5
        if visible_start < 1200 or visible_end >= len(frame):
            rejected['insufficient_context'] += 1
            continue
        start, decision = times.iloc[visible_start], times.iloc[visible_end] + pd.Timedelta(minutes=minutes)
        if not in_split(start, decision, positive['split'], p):
            rejected['split_boundary'] += 1
            continue
        if halfyear(start) != halfyear(utc(positive['core_end_time'])) or halfyear(decision) != halfyear(start):
            rejected['halfyear_boundary'] += 1
            continue
        if any(start <= hi and decision >= lo for lo, hi in protected):
            rejected['candidate_or_gold_protection'] += 1
            continue
        if any(start <= hi and decision >= lo for lo, hi in used):
            rejected['negative_interval_reuse'] += 1
            continue
        evidence = screen_window(frame, core_start_i=start_i, core_end_i=end_i,
            bar_minutes=minutes, threshold=p['negative_sampling']['whole_visible_ma_spread_atr_min'])
        if not evidence['accepted']:
            rejected.update(evidence['reasons'])
            continue
        if utc(evidence['decision_close_utc']) != decision:
            raise ValueError('Screen/render decision endpoint mismatch')
        ident = f"morphbg::{positive['canonical_asset']}::{minutes}::{times.iloc[end_i].isoformat()}::{core_bars}"
        row = {k: positive[k] for k in ('source_path', 'source_sha256', 'canonical_asset', 'symbol', 'bar_minutes', 'split')}
        row.update(event_id=ident, cluster_id=ident, paired_positive_event_id=positive['event_id'],
            paired_direction=positive['direction'], direction=None, core_bars=core_bars,
            core_start_time=times.iloc[start_i].isoformat(), core_end_time=times.iloc[end_i].isoformat(),
            source_core_start_i=int(frame['_source_i'].iloc[start_i]), source_core_end_i=int(frame['_source_i'].iloc[end_i]),
            morphology_label='clear_non_dense_background', sample_owner_confirmed=False,
            negative_kind='whole_view_non_dense', morphology_evidence=evidence,
            screen_frame_source_start_i=int(frame['_source_i'].iloc[0]),
            decision_at_utc=decision.isoformat(), visible_start_utc=start.isoformat(),
            safety_end_utc=(decision + pd.Timedelta(hours=12)).isoformat(), profit_used_for_negative_label=False)
        used.append((start, decision))
        return row, rejected
    return None, rejected


def render_background(frame: pd.DataFrame, row: dict, output: Path) -> list[dict]:
    times = frame['open_time']
    start_i = int(np.flatnonzero(times == utc(row['core_start_time']))[-1])
    end_i = int(np.flatnonzero(times == utc(row['core_end_time']))[-1])
    variants = [('A', 9)] + ([('B1', 7), ('B2', 11)] if row['split'] == 'train' else [])
    result = []
    for variant, pre in variants:
        png, _, visible = render._window_asset(frame, core_start_i=start_i, core_end_i=end_i,
            pre_bars=pre, post_bars=5, support_start_i=start_i-11-1200,
            price_scale=render.VISIBLE_RANGE_PRICE_SCALE)
        visible['feature_support_start_i'] = int(frame['_source_i'].iloc[start_i-11-1200])
        stem = render.asset_stem(row['event_id'], variant)
        ip, lp = f"images/{row['split']}/{stem}.png", f"labels/{row['split']}/{stem}.txt"
        (output / ip).write_bytes(png)
        (output / lp).write_text('')
        result.append({**{k: row[k] for k in ('event_id', 'cluster_id', 'canonical_asset', 'source_path', 'source_sha256', 'bar_minutes', 'split', 'core_end_time', 'paired_positive_event_id', 'morphology_label', 'negative_kind')},
            'arms': list(render.arms_for_asset(row['split'], variant)), 'variant': variant,
            'direction': None, 'class_id': None, 'box': None, 'image_path': ip, 'label_path': lp,
            'image_sha256': hashlib.sha256(png).hexdigest(), 'label_sha256': sha(output/lp),
            **visible, 'visible_start_utc': times.iloc[start_i-pre].isoformat(),
            'decision_at_utc': row['decision_at_utc'], 'visible_end_close_time_utc': row['decision_at_utc'],
            'profit_used_for_negative_label': False, 'sample_owner_confirmed': False,
            'training_eligible': False, 'production_eligible': False})
    return result


def build(plan_path: Path, output: Path, pilot: int = 0) -> dict:
    p = load_plan(plan_path)
    render._committed([Path(__file__), Path(__file__).with_name('ma_morphology_background.py'), plan_path,
        ROOT/'yoyo/datasets/ma_profit_dataset.py', ROOT/'yoyo/layers/l1_detection/render.py'])
    if output.exists():
        raise FileExistsError(output)
    parent = ROOT / p['parent_dataset_root']
    ledger = rows(ROOT / p['inputs']['old_ledger']['path'])
    positives = [x for x in ledger if x['profit']['retained'] and x['split'] in {'train','val','test'}]
    if pilot:
        positives = choose_pilot(positives, pilot)
    ids = {x['event_id'] for x in positives}
    original = [x for x in rows(ROOT / p['inputs']['old_manifest']['path']) if x['event_id'] in ids]
    output.mkdir(parents=True)
    for split in ('train','val','test'):
        for kind in ('images','labels'):
            (output/kind/split).mkdir(parents=True)
    manifest = []
    for row in original:
        if row['class_id'] not in (0,1) or row['box'] is None:
            raise ValueError('Positive source has empty label')
        for kind in ('image','label'):
            relative = row[kind+'_path']
            if sha(parent/relative) != row[kind+'_sha256']:
                raise ValueError('Positive source file drift')
            shutil.copyfile(parent/relative, output/relative)
        manifest.append({**row, 'morphology_label':'selected_grade_a_launch', 'sample_owner_confirmed':False,
            'positive_selection':'3R_retained_rule_candidate', 'profit_used_to_clear_shape_label':False})
    protected, used, by_source = protection_intervals(p), defaultdict(list), defaultdict(list)
    for row in positives:
        by_source[row['source_path']].append(row)
    backgrounds, shortages, sources = [], [], []
    for n, (source, source_rows) in enumerate(sorted(by_source.items()),1):
        file = ROOT/source
        if sha(file) != source_rows[0]['source_sha256']:
            raise ValueError('OHLC source drift: '+source)
        minutes = int(source_rows[0]['bar_minutes'])
        if any(x['bar_minutes'] != minutes or x['source_sha256'] != source_rows[0]['source_sha256'] for x in source_rows):
            raise ValueError('Source contract inconsistent')
        radius = pd.Timedelta(days=p['negative_sampling']['search_radius_days'])
        start = min(utc(x['core_start_time']) for x in source_rows)-radius-pd.Timedelta(minutes=minutes*1211)
        end = max(utc(x['core_end_time']) for x in source_rows)+radius+pd.Timedelta(minutes=minutes*7)
        frame = read_interval(file,start,end)
        for positive in sorted(source_rows,key=lambda x:x['event_id']):
            asset = canonical_asset(positive['canonical_asset'])
            background, reasons = choose_background(frame,positive,p,protected.get(asset,[]),used[asset])
            if background is None:
                shortages.append({'event_id':positive['event_id'],'split':positive['split'],'source_path':source,'reasons':dict(reasons)})
                continue
            backgrounds.append(background)
            manifest.extend(render_background(frame,background,output))
        sources.append({'path':source,'sha256':source_rows[0]['source_sha256'],'loaded_rows':len(frame)})
        write_json(output/'progress.json',{'status':'building','source':n,'sources':len(by_source),'positive_events':len(positives),'negative_events':len(backgrounds),'shortages':len(shortages)})
        print(json.dumps({'source':n,'total':len(by_source),'negative_events':len(backgrounds),'shortages':len(shortages)}),flush=True)
    write_rows(output/'manifest.jsonl',manifest)
    write_rows(output/'dataset_ledger.jsonl',[{**x,'morphology_label':'selected_grade_a_launch'} for x in positives]+backgrounds)
    write_rows(output/'negative_evidence.jsonl',backgrounds)
    write_rows(output/'shortages.jsonl',shortages)
    write_rows(output/'quarantine.jsonl',[{'event_id':x['event_id'],'split':x['split'],'source_path':x['source_path'],
        'reason':'outcome_nonwinner_is_not_morphology_background','morphology_label':'unadjudicated_rule_candidate'}
        for x in rows(ROOT/p['inputs']['candidate_ledger']['path']) if not x['profit']['retained']])
    for split in ('val','test'):
        (output/f'{split}.txt').write_text(''.join('./'+x['image_path']+'\n' for x in manifest if x['split']==split))
    for arm in ('A','B'):
        (output/f'train_{arm}.txt').write_text(''.join('./'+x['image_path']+'\n' for x in manifest if x['split']=='train' and arm in x['arms']))
        (output/f'data_{arm}.yaml').write_text(f'path: {output.resolve()}\ntrain: train_{arm}.txt\nval: val.txt\ntest: test.txt\nnc: 2\nnames: [dense_launch_long, dense_launch_short]\n')
    counts = {a:{s:{'positive':sum(x['class_id'] is not None for x in manifest if x['split']==s and a in x['arms']),
        'negative':sum(x['class_id'] is None for x in manifest if x['split']==s and a in x['arms'])}
        for s in ('train','val','test')} for a in ('A','B')}
    summary = {'status':'completed','pilot':bool(pilot),'plan_sha256':sha(plan_path),'manifest_sha256':sha(output/'manifest.jsonl'),
        'ledger_sha256':sha(output/'dataset_ledger.jsonl'),'negative_evidence_sha256':sha(output/'negative_evidence.jsonl'),
        'counts':counts,'positive_events':len(positives),'negative_events':len(backgrounds),'shortages':len(shortages),
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'sources':sources,'training_eligible':False,'production_eligible':False,'sample_owner_confirmed':False}
    write_json(output/'summary.json',summary)
    return summary


def audit(plan_path: Path, output: Path, *, write_receipt: bool = True) -> dict:
    """Recheck every control and sample; remote preflight can stay read-only."""
    p = load_plan(plan_path)
    summary = read_json(output/'summary.json')
    if summary['status']!='completed' or summary['plan_sha256']!=sha(plan_path):raise ValueError('Plan/build receipt mismatch')
    for name,key in [('manifest.jsonl','manifest_sha256'),('dataset_ledger.jsonl','ledger_sha256'),('negative_evidence.jsonl','negative_evidence_sha256')]:
        if sha(output/name)!=summary[key]:raise ValueError('Dataset metadata drift')
    manifest, ledger = rows(output/'manifest.jsonl'), rows(output/'dataset_ledger.jsonl')
    by_id = {x['event_id']:x for x in ledger}
    if len(by_id)!=len(ledger):raise ValueError('Duplicate event')
    if not summary['pilot']:
        expected_positive={x['event_id'] for x in rows(ROOT/p['inputs']['old_ledger']['path']) if x['profit']['retained'] and x['split'] in {'train','val','test'}}
        actual_positive={x['event_id'] for x in ledger if x['morphology_label']=='selected_grade_a_launch'}
        if actual_positive!=expected_positive:raise ValueError('Positive event membership changed')
    protected, occupied=protection_intervals(p),defaultdict(list)
    for event in ledger:
        if event['morphology_label']!='clear_non_dense_background':continue
        asset=canonical_asset(event['canonical_asset'])
        start,end=utc(event['visible_start_utc']),utc(event['decision_at_utc'])
        if any(start<=hi and end>=lo for lo,hi in protected.get(asset,[])+occupied[asset]):raise ValueError('Background protection/overlap failure')
        occupied[asset].append((start,end))
        evidence=event['morphology_evidence']
        if utc(evidence['decision_close_utc'])!=end:raise ValueError('Evidence decision mismatch')
        m=evidence['metrics']
        if m['threshold']!=p['negative_sampling']['whole_visible_ma_spread_atr_min'] or m['uses_profit_or_outcome']:raise ValueError('Evidence contract drift')
        if not all(np.isfinite(m[k]) for k in ('required_spread','min_close_six_ma_spread','min_hl2_six_ma_spread')):raise ValueError('Nonfinite evidence')
        if min(m['min_close_six_ma_spread'],m['min_hl2_six_ma_spread'])<m['required_spread']:raise ValueError('Dense background accepted')
        paired=by_id[event['paired_positive_event_id']]
        if any(paired[k]!=event[k] for k in ('source_path','canonical_asset','bar_minutes','split')):raise ValueError('Nuisance pairing mismatch')
    parent_rows = {x['image_path']:x for x in rows(ROOT/p['inputs']['old_manifest']['path'])}
    seen_hashes, event_split, counts = {}, {}, Counter()
    for row in manifest:
        event = by_id[row['event_id']]
        if event_split.setdefault(row['event_id'],row['split']) != row['split']:raise ValueError('Cross-split event')
        for kind in ('image','label'):
            path=Path(row[kind+'_path'])
            if path.is_absolute() or '..' in path.parts or sha(output/path)!=row[kind+'_sha256']:raise ValueError('Invalid file/hash')
        im=cv2.imread(str(output/row['image_path']))
        if im is None or im.shape!=(742,1280,3):raise ValueError('Wrong image shape')
        if row['image_sha256'] in seen_hashes:raise ValueError('Duplicate pixels')
        seen_hashes[row['image_sha256']]=row['image_path']
        label=(output/row['label_path']).read_text().strip()
        if row['class_id'] is None:
            if label or row['box'] is not None or event.get('profit') is not None:raise ValueError('Invalid background semantics')
            if event['morphology_label']!='clear_non_dense_background' or not event['morphology_evidence']['accepted'] or event['profit_used_for_negative_label']:raise ValueError('Missing morphology proof')
            if not in_split(utc(event['visible_start_utc']),utc(event['decision_at_utc']),row['split'],p):raise ValueError('Background time split')
        else:
            old=parent_rows[row['image_path']]
            if old['image_sha256']!=row['image_sha256'] or old['label_sha256']!=row['label_sha256']:raise ValueError('Original positive changed')
            v=[float(x) for x in label.split()]
            if len(v)!=5 or v[0]!=row['class_id'] or not all(np.isfinite(v)) or min(v[3:])<=0:raise ValueError('Invalid positive label')
            if not (0<=v[1]-v[3]/2<=v[1]+v[3]/2<=1 and 0<=v[2]-v[4]/2<=v[2]+v[4]/2<=1):raise ValueError('Box outside image')
        if utc(row['visible_end_close_time_utc'])!=utc(row['decision_at_utc']):raise ValueError('Input beyond decision')
        for arm in row['arms']:counts[(arm,row['split'],'negative' if row['class_id'] is None else 'positive')]+=1
    for arm in ('A','B'):
        for split in ('train','val','test'):
            actual={k:counts[(arm,split,k)] for k in ('positive','negative')}
            if actual!=summary['counts'][arm][split]:raise ValueError('Actual label count mismatch')
            if not summary['pilot'] and not all(actual.values()):raise ValueError('Missing positive/negative population')
            expected=['./'+x['image_path'] for x in manifest if x['split']==split and arm in x['arms']]
            name=f'train_{arm}.txt' if split=='train' else f'{split}.txt'
            if (output/name).read_text().splitlines()!=expected:raise ValueError('Loader list mismatch')
    result={'status':'passed','manifest_sha256':summary['manifest_sha256'],'ledger_sha256':summary['ledger_sha256'],
        'counts':summary['counts'],'verified_files':2*len(manifest),'pilot':summary['pilot'],'original_positive_files_preserved':True,
        'morphology_evidence_verified_from_ledger':True,'per_sample_owner_gold':False}
    if write_receipt:
        write_json(output/'audit.json',result)
    return result


def preview(output: Path) -> dict:
    """Make paired review sheets, with actual positive boxes and no fake N box."""
    from PIL import Image, ImageDraw
    manifest=rows(output/'manifest.jsonl')
    by_id={x['event_id']:x for x in manifest if x['variant']=='A'}
    negatives=sorted((x for x in by_id.values() if x['class_id'] is None),key=lambda x:x['paired_positive_event_id'])
    if not negatives:raise ValueError('No backgrounds to review')
    target=output/'review'
    if target.exists():raise FileExistsError(target)
    target.mkdir()
    pages,selection=[],[]
    for page in range((len(negatives)+4)//5):
        group=negatives[page*5:page*5+5]
        width,thumb_h,title_h=640,371,42
        canvas=Image.new('RGB',(2*width,(thumb_h+title_h)*len(group)),'#eef0f4')
        draw=ImageDraw.Draw(canvas)
        for i,n in enumerate(group):
            positive=by_id[n['paired_positive_event_id']]
            for side,row in enumerate((positive,n)):
                image=Image.open(output/row['image_path']).convert('RGB')
                if side==0:
                    values=[float(x) for x in (output/row['label_path']).read_text().split()]
                    _,cx,cy,w,h=values
                    ImageDraw.Draw(image).rectangle(((cx-w/2)*1280,(cy-h/2)*742,(cx+w/2)*1280,(cy+h/2)*742),outline='#ee3333',width=3)
                image=image.resize((width,thumb_h),Image.Resampling.LANCZOS)
                x,y=side*width,i*(thumb_h+title_h)
                kind='POSITIVE (actual YOLO box)' if side==0 else 'BACKGROUND (empty YOLO label)'
                draw.text((x+8,y+4),f"{kind}  {row['bar_minutes']}m  {row['canonical_asset']}",fill='#111827')
                draw.text((x+8,y+22),row['core_end_time'],fill='#374151')
                canvas.paste(image,(x,y+title_h))
                selection.append({'page':page+1,'row':i+1,'side':side,'event_id':row['event_id'],'image_path':row['image_path']})
        path=target/f'pairs_{page+1:02d}.png'
        canvas.save(path)
        pages.append({'path':str(path.relative_to(output)),'sha256':sha(path)})
    receipt={'status':'rendered_for_review','manifest_sha256':sha(output/'manifest.jsonl'),'pages':pages,'samples':selection,
        'note':'Left boxes read from actual positive YOLO TXT; right backgrounds have no rectangle. Source PNGs unchanged. Not Owner gold confirmation.'}
    write_json(target/'receipt.json',receipt)
    return receipt


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['build','audit','preview']);p.add_argument('--plan',type=Path,default=DEFAULT_PLAN)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--pilot',type=int,default=0)
    args=p.parse_args()
    result=build(args.plan,args.out,args.pilot) if args.command=='build' else audit(args.plan,args.out) if args.command=='audit' else preview(args.out)
    print(json.dumps({k:v for k,v in result.items() if k!='sources'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
