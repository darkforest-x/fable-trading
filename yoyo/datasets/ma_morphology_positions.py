"""Correct the v6 fixed-right-edge shortcut with three equal-length crops.

Owner correction, 2026-09-25: move the *data window*, never the target alone.
R5/R8/R11 use (pre, post)=(11,5)/(8,8)/(5,11); original core TXT geometry is
inverted from the P9 training chart and reprojected in data coordinates.
Each view ends at its own known close, at least the original confirmation.
OHLC/HL2 SMA/EMA20/60/120 use core-1211 through that view's endpoint only.
Negatives are re-screened over the union of all three windows with unchanged
thresholds. Frozen P9 validation/test inputs remain separate reference rows.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from yoyo.datasets import ma_profit_dataset as render
from yoyo.datasets.ma_morphology_assembly import overlaps, screen_negative
from yoyo.datasets.ma_morphology_background import screen_window
from yoyo.datasets.ma_morphology_future_review import label_coordinates, transform
from yoyo.datasets.ma_morphology_redo import in_split, protection_intervals, read_interval, rows, sha, utc, write_json, write_rows
from yoyo.datasets.ma_profit_cohort import canonical_asset

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-ma-morphology-v6-threeview-20260925-v1'
PLAN = EXP/'position_plan.json'
VIEWS = {'R5': (11, 5), 'R8': (8, 8), 'R11': (5, 11)}
RECOVERY = ROOT/'data/crypto/research/ma_morphology_position_20260925_prefixes/recovery_manifest.json'


def event_support(frame: pd.DataFrame, row: dict) -> pd.DataFrame:
    """Bound all inputs before validation; the latest possible view is core+11."""
    step = pd.Timedelta(minutes=int(row['bar_minutes']))
    lo, hi = utc(row['core_start_time'])-1211*step, utc(row['core_end_time'])+12*step
    support = frame.loc[(frame.open_time >= lo) & (frame.open_time < hi)].reset_index(drop=True)
    if (len(support) != 1211+int(row['core_bars'])+11
            or not support.open_time.diff().iloc[1:].eq(step).all()):
        raise ValueError('Incomplete or gapped three-position source: '+row['event_id'])
    if support.open_time.iloc[1211] != utc(row['core_start_time']):
        raise ValueError('Core binding changed')
    return support


def render_positions(frame: pd.DataFrame, row: dict, original_label: str) -> dict:
    """Preserve actual P9 TXT bar/price geometry across genuine window shifts."""
    support = event_support(frame, row)
    start, end = 1211, 1210+int(row['core_bars'])
    old_png, _, _ = render._window_asset(support, core_start_i=start, core_end_i=end,
        pre_bars=9, post_bars=5, support_start_i=0, price_scale=render.VISIBLE_RANGE_PRICE_SCALE)
    if hashlib.sha256(old_png).hexdigest() != row['image_sha256']:
        raise ValueError('Original P9 source/pixel identity failed: '+row['event_id'])
    old_visible = render.add_hl2_mas(support.iloc[:end+6]).iloc[start-9:].reset_index(drop=True)
    actual = label_coordinates(original_label, transform(old_visible, 1280, 742))
    if (actual is None) != (row['class_id'] is None):
        raise ValueError('Original class/label mismatch')
    result = {}
    for variant, (pre, post) in VIEWS.items():
        png, _, _ = render._window_asset(support, core_start_i=start, core_end_i=end,
            pre_bars=pre, post_bars=post, support_start_i=0, price_scale=render.VISIBLE_RANGE_PRICE_SCALE)
        visible = render.add_hl2_mas(support.iloc[:end+post+1]).iloc[start-pre:].reset_index(drop=True)
        tf = transform(visible,1280,742)
        label, box = '', None
        if actual:
            left, right = actual['bar_left']-9+pre, actual['bar_right']-9+pre
            # Float projection keeps the original physical edges; no new reboxing.
            x0, x1 = [tf.left + x/(tf.n_bars-1)*tf.plot_w for x in (left,right)]
            y0, y1 = [tf.top+(tf.price_max-p)/(tf.price_max-tf.price_min)*tf.plot_h
                      for p in (actual['price_high'],actual['price_low'])]
            if not 0 <= x0 < x1 <= tf.width or not 0 <= y0 < y1 <= tf.height:
                raise ValueError('Original box escapes new view')
            label=f"{row['class_id']} {(x0+x1)/2/tf.width:.8f} {(y0+y1)/2/tf.height:.8f} {(x1-x0)/tf.width:.8f} {(y1-y0)/tf.height:.8f}\n"
            box={'core_relative_left':actual['bar_left']-9,'core_relative_right':actual['bar_right']-9,
                 'price_high':actual['price_high'],'price_low':actual['price_low'],
                 'bar_left':left,'bar_right':right,'pixel_box':[x0,y0,x1,y1]}
        result[variant]={'png':png,'label':label,'box':box,'pre_bars':pre,'post_bars':post,
            'visible_bars':len(visible),'visible_start_utc':visible.open_time.iloc[0].isoformat(),
            'decision_at_utc':(visible.open_time.iloc[-1]+pd.Timedelta(minutes=int(row['bar_minutes']))).isoformat(),
            'source_replay_sha_matches':True}
    return result


def build(plan_path: Path, output: Path, pilot: bool=False) -> dict:
    plan=json.loads(plan_path.read_text())
    if plan['views'] != {k:list(v) for k,v in VIEWS.items()}: raise ValueError('Position contract drift')
    dependencies=[Path(__file__),plan_path,ROOT/'yoyo/datasets/ma_morphology_assembly.py',
        ROOT/'yoyo/datasets/ma_morphology_background.py',ROOT/'yoyo/datasets/ma_morphology_future_review.py',
        ROOT/'yoyo/datasets/ma_profit_dataset.py',ROOT/'yoyo/layers/l1_detection/render.py']
    commit=render._committed(dependencies)
    for binding in plan['inputs'].values():
        if sha(ROOT/binding['path']) != binding['sha256']: raise ValueError('Frozen input changed: '+binding['path'])
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    parent=ROOT/plan['parent_dataset']
    original=rows(parent/'manifest.jsonl')
    # P9 exists once per event, including inherited negatives with sparse metadata.
    selected={r['event_id']:dict(r) for r in original if r['variant']=='P9'}
    ledger={r['event_id']:r for r in rows(ROOT/plan['inputs']['parent_ledger']['path'])}
    for ident,r in selected.items():
        if r.get('core_start_time') is None:
            source=ledger[ident]
            for key in ('source_sha256','core_end_time','bar_minutes'):
                if r[key]!=source[key]: raise ValueError('Parent geometry identity changed')
            r.update(core_start_time=source['core_start_time'],core_bars=source['core_bars'])
    if len(selected)!=len({r['event_id'] for r in original}): raise ValueError('Missing original P9 event')
    if pilot:
        ids=set(plan['review_event_ids'])
        for split in ('val','test'):
            for kind in ('positive','negative'):
                pool=sorted(r['event_id'] for r in selected.values() if r['split']==split and r['sample_kind']==kind)
                ids.update(pool[:2])
        selected={k:r for k,r in selected.items() if k in ids}
    recovery=json.loads(RECOVERY.read_text()) if RECOVERY.is_file() else {'events':{}}
    grade_rows=rows(ROOT/plan['inputs']['grade_manifest']['path'])
    grade_neg={r['negative_event_id']:r for r in grade_rows if r.get('sample_kind')=='negative'}
    parent_plan=json.loads((ROOT/plan['inputs']['parent_plan']['path']).read_text())
    protocol=json.loads((ROOT/plan['inputs']['grade_protocol']['path']).read_text())
    protected=protection_intervals(parent_plan)
    margin=pd.Timedelta(hours=parent_plan['negative_sampling']['protection_hours'])
    for r in grade_rows:
        if r.get('sample_kind')!='negative':
            protected.setdefault(canonical_asset(r['symbol']),[]).append((utc(r['core_start_time'])-margin,utc(r['core_end_time'])+pd.Timedelta(minutes=15)+margin))
    # Includes positives retained across venues/timeframes, independent of outcomes.
    protected={k:sorted(set(v)) for k,v in protected.items()}
    groups=defaultdict(list)
    for r in selected.values(): groups[r['source_path']].append(r)
    manifest,exclusions,screening=[],[],[]
    used=defaultdict(list)
    for source_i,(source_path,group) in enumerate(sorted(groups.items()),1):
        path=ROOT/source_path
        if path.is_file():
            identities={r['source_sha256'] for r in group}
            if len(identities)!=1 or sha(path) not in identities: raise ValueError('Source changed: '+source_path)
            minutes={r['bar_minutes'] for r in group}
            if len(minutes)!=1: raise ValueError('Mixed intervals in source')
            step=pd.Timedelta(minutes=int(next(iter(minutes))))
            frame=read_interval(path,min(utc(r['core_start_time']) for r in group)-1211*step,
                                max(utc(r['core_end_time']) for r in group)+12*step)
        else: frame=None
        for r in sorted(group,key=lambda r:(r['core_start_time'],r['event_id'])):
            ident=r['event_id']; step=pd.Timedelta(minutes=int(r['bar_minutes']))
            lo=utc(r['core_start_time'])-11*step; hi=utc(r['core_end_time'])+12*step
            reason=None; evidence=None
            if not in_split(lo,hi,r['split'],parent_plan): reason='expanded_window_or_safety_crosses_split'
            if r['sample_kind']=='negative':
                asset=canonical_asset(r['canonical_asset'])
                if overlaps(lo,hi,protected.get(asset,[])): reason='expanded_candidate_or_gold_protection'
                elif overlaps(lo,hi,used[asset]): reason='expanded_negative_overlap'
            if reason:
                exclusions.append({'event_id':ident,'reason':reason,'sample_kind':r['sample_kind']});continue
            recovered=None
            if frame is None:
                recovered=recovery['events'][ident]
                if recovered['full_csv_sha256_verified']!=r['source_sha256'] or recovered['post_rows']!=11: raise ValueError('Recovery source mismatch')
                recovered_path=ROOT/recovered['path']
                if sha(recovered_path)!=recovered['sha256']: raise ValueError('Recovery slice changed')
                current=read_interval(recovered_path,utc(r['core_start_time'])-1211*step,hi)
            else: current=frame
            support=event_support(current,r)
            if r['sample_kind']=='negative':
                if r['negative_kind']=='whole_view_non_dense':
                    evidence=screen_window(support,core_start_i=1211,core_end_i=1210+int(r['core_bars']),bar_minutes=int(r['bar_minutes']),post_bars=11)
                else:
                    evidence,_=screen_negative(support,grade_neg[r['legacy_negative_event_id']],protocol,post_bars=11)
                screening.append({'event_id':ident,'screen':evidence})
                if not evidence['accepted']:
                    exclusions.append({'event_id':ident,'reason':'|'.join(evidence['reasons']),'sample_kind':'negative'});continue
                used[asset].append((lo,hi))
            if sha(parent/r['label_path'])!=r['label_sha256'] or sha(parent/r['image_path'])!=r['image_sha256']: raise ValueError('Original training asset changed')
            rendered=render_positions(support,r,(parent/r['label_path']).read_text())
            for variant,item in rendered.items():
                stem=render.asset_stem(ident,variant)
                ip,lp=f"images/{r['split']}/{stem}.png",f"labels/{r['split']}/{stem}.txt"
                (output/ip).parent.mkdir(parents=True,exist_ok=True);(output/lp).parent.mkdir(parents=True,exist_ok=True)
                (output/ip).write_bytes(item['png']);(output/lp).write_text(item['label'])
                entry={k:v for k,v in r.items() if k not in ('box','parent_image_path','source_recovery_manifest','label_horizon_end_utc')}
                entry.update(variant=variant,pre_bars=item['pre_bars'],post_bars=item['post_bars'],visible_bars=item['visible_bars'],
                    original_decision_at_utc=r['decision_at_utc'],decision_at_utc=item['decision_at_utc'],
                    visible_end_close_time_utc=item['decision_at_utc'],visible_start_utc=item['visible_start_utc'],
                    source_replay_sha_matches=True,original_image_sha256=r['image_sha256'],original_label_sha256=r['label_sha256'],
                    original_label_path=r['label_path'],parent_label_horizon_end_utc=r.get('label_horizon_end_utc'),
                    box=item['box'],geometry_source='actual_parent_P9_TXT_projected_bar_price',
                    image_path=ip,label_path=lp,image_sha256=sha(output/ip),label_sha256=sha(output/lp),
                    source_recovery_path=recovered['path'] if recovered else None,
                    source_recovery_sha256=recovered['sha256'] if recovered else None,
                    training_eligible=False,production_eligible=False)
                manifest.append(entry)
        if source_i%10==0: print(json.dumps({'sources':source_i,'total':len(groups),'images':len(manifest),'excluded_events':len(exclusions)}),flush=True)
    # Reference rows do not count as augmented training views. Their bytes, labels,
    # membership and clocks remain exactly the previous benchmark, never retrained.
    for r in original:
        if r['split']=='train' or r['variant']!='P9':continue
        for kind in ('image','label'):
            src=parent/r[kind+'_path'];dst=output/r[kind+'_path']
            if sha(src)!=r[kind+'_sha256']: raise ValueError('Fixed reference changed')
            dst.parent.mkdir(parents=True,exist_ok=True);os.link(src,dst)
        manifest.append({**r,'fixed_reference_only':True})
    write_rows(output/'manifest.jsonl',manifest);write_rows(output/'exclusions.jsonl',exclusions);write_rows(output/'screening.jsonl',screening)
    counts=Counter((r['split'],r['sample_kind']) for r in manifest)
    positives=[r for r in manifest if r['class_id'] is not None and r['variant'] in VIEWS]
    receipt={'source_commit':commit,'plan_sha256':sha(plan_path),'manifest_sha256':sha(output/'manifest.jsonl'),
        'dataset_ready':not pilot,'pilot':pilot,'position_contract':'equal_length_actual_bar_shift_v1',
        'train_positive_images':counts['train','positive'],'train_negative_images':counts['train','negative'],
        'positive_position_images':len(positives),'positive_events':len({r['event_id'] for r in positives}),
        'post_bars_counts':dict(Counter(str(r['post_bars']) for r in positives)),
        'counts':dict(Counter(f"{r['split']}:{r['sample_kind']}:{r['variant']}" for r in manifest)),
        'excluded_events':len(exclusions),'exclusion_counts':dict(Counter(r['reason'] for r in exclusions)),
        'parent_manifest_sha256':sha(parent/'manifest.jsonl'),'training_eligible':False,'production_eligible':False}
    write_json(output/'build_receipt.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',type=Path,default=PLAN)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--pilot',action='store_true')
    args=parser.parse_args();print(json.dumps(build(args.plan,args.output,args.pilot),indent=2))


if __name__=='__main__':main()
