"""Select final stratified rendering review and replay its raw OHLC evidence.

Selection covers split/timeframe/paired-side strata and the narrowest spread
margin. It does not use a trained model or future return. Every selected
background is recalculated from its exact 1200-bar causal support; each actual
rendered variant must reproduce byte-for-byte. Paired positive windows form a
negative control for the clear-background screen, not new owner gold labels.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
import os

import pandas as pd
from PIL import Image, ImageDraw

from yoyo.datasets.ma_morphology_redo import ROOT, DEFAULT_PLAN, load_plan, read_json, rows, sha, utc, write_json, read_interval, render
from yoyo.datasets.ma_morphology_background import screen_window


def margin(event: dict) -> float:
    m=event['morphology_evidence']['metrics']
    return min(m['min_close_six_ma_spread'],m['min_hl2_six_ma_spread'])/m['required_spread']


def audit_positive_splits() -> dict:
    """Recheck original positive temporal/cluster geometry without trusting flags."""
    p=load_plan(DEFAULT_PLAN)
    ledger=[r for r in rows(ROOT/p['inputs']['old_ledger']['path']) if r['profit']['retained']]
    by_id={r['event_id']:r for r in ledger}
    manifest=[r for r in rows(ROOT/p['inputs']['old_manifest']['path']) if r['event_id'] in by_id]
    boundaries=[utc(p['splits'][k]) for k in ('train_end_exclusive','validation_end_exclusive','test_end_exclusive')]
    cluster_split={};ranges=defaultdict(list);strata=defaultdict(int)
    for row in manifest:
        event=by_id[row['event_id']]
        lo,hi={'train':(None,boundaries[0]),'val':tuple(boundaries[:2]),'test':tuple(boundaries[1:])}[row['split']]
        step=pd.Timedelta(minutes=row['bar_minutes'])
        start=utc(event['core_start_time'])-step*{'A':9,'B1':7,'B2':11}[row['variant']]
        decision=utc(event['core_end_time'])+step*6
        end=utc(event['profit']['label_window_end_utc'])
        if (lo is not None and start<lo) or end>=hi or end!=decision+pd.Timedelta(hours=12):raise ValueError('Positive crosses temporal split')
        if utc(row['decision_at_utc'])!=decision or utc(row['visible_end_close_time_utc'])!=decision:raise ValueError('Positive decision drift')
        if row['class_id']!={'LONG':0,'SHORT':1}[event['direction']]:raise ValueError('Positive direction drift')
        if row['feature_support_start_i']!=event['source_core_start_i']-1211:raise ValueError('Positive support drift')
        key=row['cluster_id']
        if cluster_split.setdefault(key,row['split'])!=row['split']:raise ValueError('Positive cluster split leakage')
        if row['variant']=='A':
            ranges[row['split']].append((start,end))
            strata[f"{row['split']}|{row['bar_minutes']}m|{row['direction']}"]+=1
    return {'status':'passed','positive_events':len(by_id),'physical_positive_images':len(manifest),
        'plan_sha256':sha(DEFAULT_PLAN),'parent_manifest_sha256':p['inputs']['old_manifest']['sha256'],
        'unique_clusters':len(cluster_split),'strata':dict(sorted(strata.items())),
        'split_ranges':{k:{'first_visible_start_utc':min(a for a,b in v).isoformat(),'last_label_end_utc':max(b for a,b in v).isoformat(),'events':len(v)} for k,v in sorted(ranges.items())}}


def select(ledger: list[dict]) -> list[dict]:
    buckets=defaultdict(list)
    for row in ledger:
        if row['morphology_label']=='clear_non_dense_background':
            buckets[(row['split'],row['bar_minutes'],row['paired_direction'])].append(row)
    # One hardest boundary per available stratum, deterministic before training.
    return [min(v,key=lambda r:(margin(r),r['event_id'])) for _,v in sorted(buckets.items())]


def replay_frame(event: dict) -> tuple[pd.DataFrame,int,int]:
    step=pd.Timedelta(minutes=int(event['bar_minutes']))
    start=utc(event['core_start_time'])-step*1211
    stop=utc(event['core_end_time'])+step*6
    frame=read_interval(ROOT/event['source_path'],start,stop)
    a=frame.index[frame['open_time']==utc(event['core_start_time'])].item()
    b=frame.index[frame['open_time']==utc(event['core_end_time'])].item()
    return frame,a,b


def prepare(dataset: Path, output: Path) -> dict:
    p=load_plan(DEFAULT_PLAN)
    if output.exists():raise FileExistsError(output)
    audit=read_json(dataset/'audit.json')
    if audit['status']!='passed' or audit['pilot'] or audit['manifest_sha256']!=sha(dataset/'manifest.jsonl'):
        raise ValueError('Completed full dataset audit required')
    ledger,manifest=rows(dataset/'dataset_ledger.jsonl'),rows(dataset/'manifest.jsonl')
    by_event={r['event_id']:r for r in ledger}
    assets=defaultdict(dict)
    for row in manifest:assets[row['event_id']][row['variant']]=row
    selected=select(ledger)
    evidence=[]
    hashes={}
    for n in selected:
        path=ROOT/n['source_path']
        if path not in hashes:hashes[path]=sha(path)
        if hashes[path]!=n['source_sha256']:raise ValueError('Review raw source changed')
        frame,a,b=replay_frame(n)
        check=screen_window(frame,core_start_i=a,core_end_i=b,bar_minutes=n['bar_minutes'])
        if not check['accepted'] or utc(check['decision_close_utc'])!=utc(n['decision_at_utc']):
            raise ValueError('Raw replay rejects recorded background')
        for key in ('required_spread','min_close_six_ma_spread','min_hl2_six_ma_spread'):
            if not math.isclose(check['metrics'][key],n['morphology_evidence']['metrics'][key],rel_tol=1e-12,abs_tol=1e-15):
                raise ValueError('Raw feature replay mismatch')
        verified=[]
        for variant,row in assets[n['event_id']].items():
            pre={'A':9,'B1':7,'B2':11}[variant]
            png,_,_=render._window_asset(frame,core_start_i=a,core_end_i=b,pre_bars=pre,post_bars=5,support_start_i=0,price_scale=render.VISIBLE_RANGE_PRICE_SCALE)
            import hashlib
            if hashlib.sha256(png).hexdigest()!=row['image_sha256']:raise ValueError('Raw render replay mismatch')
            verified.append(variant)
        pos=by_event[n['paired_positive_event_id']]
        pf,pa,pb=replay_frame(pos)
        control=screen_window(pf,core_start_i=pa,core_end_i=pb,bar_minutes=pos['bar_minutes'])
        if control['accepted']:raise ValueError('Positive also satisfies clear-background rule: '+pos['event_id'])
        evidence.append({'event_id':n['event_id'],'split':n['split'],'bar_minutes':n['bar_minutes'],
            'paired_direction':n['paired_direction'],'margin':margin(n),'replayed_variants':verified,
            'negative_raw_replay':check,'positive_control_reasons':control['reasons'],
            'positive_control_metrics':control['metrics']})
    output.mkdir(parents=True)
    pages=[]
    for page in range((len(selected)+3)//4):
        group=selected[page*4:page*4+4]
        canvas=Image.new('RGB',(1280,413*len(group)),'#eef0f4')
        draw=ImageDraw.Draw(canvas)
        for j,n in enumerate(group):
            variant=('A','B1','B2')[(page*4+j)%3] if n['split']=='train' else 'A'
            pos=assets[n['paired_positive_event_id']][variant]
            neg=assets[n['event_id']][variant]
            for side,row in enumerate((pos,neg)):
                im=Image.open(dataset/row['image_path']).convert('RGB')
                if side==0:
                    _,cx,cy,w,h=map(float,(dataset/row['label_path']).read_text().split())
                    ImageDraw.Draw(im).rectangle(((cx-w/2)*1280,(cy-h/2)*742,(cx+w/2)*1280,(cy+h/2)*742),outline='#ed3434',width=3)
                x,y=side*640,j*413
                draw.text((x+6,y+3),f"{'POSITIVE / actual box' if side==0 else 'BACKGROUND / empty label'}  {n['split']}  {variant}  {n['bar_minutes']}m",fill='#111827')
                draw.text((x+6,y+22),f"{n['canonical_asset']}  {row['core_end_time']}",fill='#374151')
                canvas.paste(im.resize((640,371),Image.Resampling.LANCZOS),(x,y+42))
        target=output/f'pairs_{page+1:02d}.png';canvas.save(target)
        pages.append({'path':target.name,'sha256':sha(target)})
    receipt={'status':'awaiting_visual_review','manifest_sha256':sha(dataset/'manifest.jsonl'),
        'audit_sha256':sha(dataset/'audit.json'),'plan_sha256':sha(DEFAULT_PLAN),'pages':pages,
        'pairs':len(selected),'selection':'minimum margin in every split/timeframe/paired-direction stratum',
        'raw_replay':evidence,'raw_source_sha256':{str(k.relative_to(ROOT)):v for k,v in hashes.items()},
        'positive_control_accepted':0,'sample_owner_confirmed':False}
    receipt['positive_split_audit']=audit_positive_splits()
    write_json(output/'selection.json',receipt)
    browse=dataset/'review_links'
    if browse.exists():raise FileExistsError(browse)
    for row in manifest:
        for arm in row['arms']:
            kind='backgrounds' if row['class_id'] is None else 'positives'
            directory=browse/arm/row['split']/kind
            directory.mkdir(parents=True,exist_ok=True)
            source=(dataset/row['image_path']).resolve()
            (directory/source.name).symlink_to(os.path.relpath(source,directory))
    (browse/'README.txt').write_text('Browsing links only; these are the same training PNGs, not extra independent events.\nA: one view/event. B: two views/train event. Validation/test are shared.\n',encoding='utf-8')
    return {k:v for k,v in receipt.items() if k not in ('raw_replay','raw_source_sha256')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();print(prepare(args.dataset,args.out))
