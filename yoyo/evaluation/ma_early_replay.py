"""Build causal, label-blind early-v6 replay inputs and score frozen models.

Sources are existing frozen OHLC archives, never new market downloads. Pixels
use only open_time/OHLC through the selected closed endpoint, HL2 six MAs with
at least1200 preceding bars, and the training full-candle visible-range canvas.
Ground-truth geometry is metadata only. Rule-selected events are not human
onset gold; unlabelled market alarms are not declared false positives.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import ma_profit_dataset as renderer
from yoyo.datasets.ma_morphology_future_review import transform
from yoyo.datasets.ma_morphology_positions import _load_source_group, event_support
from yoyo.datasets.ma_morphology_redo import read_interval
from yoyo.evaluation.ma_early_validation import ROOT, PLAN, sha


def read_rows(path):
    return [json.loads(l) for l in Path(path).open(encoding='utf-8') if l.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def render_window(frame, endpoint, n, minutes):
    """Read OHLC at [max(0,end-n+1-1202):end], never future or a target box."""
    start = endpoint - n + 1
    support = max(0, start - 1202)
    if n < 4 or start - support < 1200 or endpoint >= len(frame):
        raise ValueError('insufficient_causal_warmup')
    prefix = frame.iloc[support:endpoint+1].copy()
    if not prefix.open_time.diff().iloc[1:].eq(pd.Timedelta(minutes=minutes)).all():
        raise ValueError('gapped_causal_warmup')
    visible = renderer.add_hl2_mas(prefix).iloc[-n:].reset_index(drop=True)
    tf = transform(visible, 1280, 742)
    slot = tf.plot_w / n
    tf = replace(tf, left=tf.left+slot/2, plot_w=slot*(n-1), candle_half_w=max(1,int(slot*.34)))
    image, _ = renderer.chart_render.render_chart(visible, width=1280, height=742, fixed_transform=tf)
    image = renderer._recolor_candles(image)
    ok, png = cv2.imencode('.png', image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise ValueError('PNG encoding failed')
    meta = {'n':n, 'endpoint':endpoint, 'visible_start':start, 'warmup_bars':start-support,
        'decision_at_utc':(visible.open_time.iloc[-1]+pd.Timedelta(minutes=minutes)).isoformat(),
        'left':tf.left, 'plot_width':tf.plot_w, 'price_min':tf.price_min, 'price_max':tf.price_max,
        'top':tf.top, 'plot_height':tf.plot_h, 'last_close':float(visible.close.iloc[-1])}
    return png.tobytes(), meta


def project_target(row, metadata):
    """Project inherited bar/price geometry without moving it to the right edge."""
    b = row['box']; core_start = 1211
    left = core_start + b['core_relative_left'] - metadata['visible_start']
    right = core_start + b['core_relative_right'] - metadata['visible_start']
    x = [metadata['left']+v/(metadata['n']-1)*metadata['plot_width'] for v in (left,right)]
    y = [metadata['top']+(metadata['price_max']-v)/(metadata['price_max']-metadata['price_min'])*metadata['plot_height']
         for v in (b['price_high'],b['price_low'])]
    return [x[0],y[0],x[1],y[1]]


def persist_image(out, ident, png, metadata):
    path = Path('images') / (ident+'.png')
    (out/path).write_bytes(png)
    return {**metadata, 'id':ident, 'image_path':str(path), 'image_sha256':hashlib.sha256(png).hexdigest()}


def event_group(job):
    source, group, windows, out = job
    out = Path(out); records=[]; skipped=[]; parity=[]
    _, _, full = _load_source_group((source, group))
    for row in group:
        if full is None:
            path=ROOT/row['source_recovery_path']
            assert sha(path)==row['source_recovery_sha256']
            step=pd.Timedelta(minutes=row['bar_minutes'])
            frame=read_interval(path,pd.Timestamp(row['core_start_time'])-1211*step,
                                pd.Timestamp(row['core_end_time'])+12*step)
        else:
            frame=full
        frame=event_support(frame,row); end=1210+row['core_bars']; post=row['post_bars']
        original, _=render_window(frame,end+post,row['visible_bars'],row['bar_minutes'])
        assert hashlib.sha256(original).hexdigest()==row['image_sha256'],row['event_id']
        changed=frame.copy();changed.loc[end+post+1:,['open','high','low','close']]*=11
        assert render_window(changed,end+post,row['visible_bars'],row['bar_minutes'])[0]==original
        parity.append(row['event_id'])
        key=hashlib.sha256(row['event_id'].encode()).hexdigest()[:20]
        for p in range(9):
            for n in windows:
                try:png,meta=render_window(frame,end+p,n,row['bar_minutes'])
                except ValueError as exc:
                    skipped.append({'event_id':row['event_id'],'post':p,'n':n,'reason':str(exc)});continue
                meta.update(cohort='event',event_id=row['event_id'],split=row['split'],
                    symbol=row['canonical_asset'],minutes=row['bar_minutes'],class_id=row['class_id'],
                    reference_post=post,post=p,core_end=end,core_close=float(frame.close.iloc[end]),
                    target_xyxy=project_target(row,meta),label_status=row['early_label_status'])
                records.append(persist_image(out,f'e_{key}_p{p}_n{n}',png,meta))
    return records,skipped,parity


def market_stream(job):
    row, cutoff, day_start, windows, out = job
    from yoyo.evaluation.ma_gainers_model_scan import load_native_ohlc,closed_endpoint_indices
    out=Path(out); path=ROOT/row['source_path']
    assert sha(path)==row['source_sha256']
    minutes=int(row['minutes'])
    frame=load_native_ohlc(path,minutes=minutes,cutoff=pd.Timestamp(cutoff))
    endpoints=closed_endpoint_indices(frame,minutes=minutes,day_start_utc=pd.Timestamp(day_start),cutoff=pd.Timestamp(cutoff))
    records=[];skipped=[];mutation_checked=False
    for end in endpoints:
        for n in windows:
            try:png,meta=render_window(frame,end,n,minutes)
            except ValueError as exc:
                skipped.append({'symbol':row['symbol'],'minutes':minutes,'endpoint':end,'n':n,'reason':str(exc)});continue
            if not mutation_checked:
                changed=frame.copy();changed.loc[end+1:,['open','high','low','close']]*=11
                assert render_window(changed,end,n,minutes)[0]==png
                mutation_checked=True
            meta.update(cohort='market',symbol=row['symbol'],minutes=minutes,source_path=row['source_path'],
                source_sha256=row['source_sha256'],class_id=None,label_status='unadjudicated_market_window')
            ident=f"m_{row['symbol']}_{minutes}_{end}_n{n}"
            records.append(persist_image(out,ident,png,meta))
    return records,skipped,{'symbol':row['symbol'],'minutes':minutes,'mutation_checked':mutation_checked}


def build(out):
    plan=json.loads((ROOT/PLAN).read_text(encoding='utf-8'));dataset=ROOT/plan['dataset']
    commit=renderer._committed([Path(__file__),ROOT/PLAN,ROOT/'yoyo/evaluation/ma_early_validation.py'])
    assert sha(dataset/'manifest.jsonl')==plan['manifest_sha256']
    if out.exists():raise FileExistsError(out)
    (out/'images').mkdir(parents=True)
    rows=read_rows(dataset/'manifest.jsonl');held=[r for r in rows if r['variant']=='P9' and r['split']!='train']
    records=[];skipped=[];parity=[];groups=defaultdict(list);window=plan['replay']['windows']
    # Exact frozen static images are evaluated at a fixed operating threshold.
    for r in held:
        src=dataset/r['image_path'];assert sha(src)==r['image_sha256']
        ident='s_'+Path(r['image_path']).stem;path=Path('images')/(ident+'.png');shutil.copyfile(src,out/path)
        records.append({'id':ident,'cohort':'static','image_path':str(path),'image_sha256':r['image_sha256'],
            'event_id':r['event_id'],'split':r['split'],'class_id':r['class_id'],'minutes':r['bar_minutes'],
            'n':r['visible_bars'],'symbol':r['canonical_asset'],'pool':r['evaluation_pool'],
            'negative_kind':r.get('negative_kind'),'target_xyxy':r['box']['pixel_box'] if r['box'] else None,
            'left':r['chart_x_left'],'plot_width':r['chart_plot_w'],'label_status':r['early_label_status']})
        if r['class_id'] is not None:groups[r['source_path']].append(r)
    with ProcessPoolExecutor(max_workers=3) as pool:
        for i,(rs,ss,pp) in enumerate(pool.map(event_group,[(s,g,window,str(out)) for s,g in sorted(groups.items())],chunksize=1)):
            records.extend(rs);skipped.extend(ss);parity.extend(pp)
            if i%20==0:print(json.dumps({'phase':'events','groups':i+1,'event_parity':len(parity),'images':len(records)}),flush=True)
    inputs=ROOT/plan['replay']['continuous_source'];ranking=json.loads((inputs/'ranking.json').read_text(encoding='utf-8'))
    coverage=json.loads((inputs/'coverage.json').read_text(encoding='utf-8'))
    sources={}
    for r in coverage:
        if r.get('status')=='ok':
            key=(r['symbol'],r['minutes'])
            sources[key]={'symbol':r['symbol'],'minutes':r['minutes'],'source_path':r['path'],
                          'source_sha256':r['sha256']}
        else:skipped.append({'stream':r,'reason':'frozen_source_unavailable'})
    mutations=[]
    cutoff=pd.to_datetime(ranking['cutoff_ms'],unit='ms',utc=True).isoformat()
    jobs=[(r,cutoff,ranking['day_start_utc'],window,str(out)) for _,r in sorted(sources.items())]
    with ProcessPoolExecutor(max_workers=3) as pool:
        for i,(rs,ss,check) in enumerate(pool.map(market_stream,jobs,chunksize=1)):
            records.extend(rs);skipped.extend(ss);mutations.append(check)
            if i%5==0:print(json.dumps({'phase':'market','streams':i+1,'images':len(records)}),flush=True)
    records.sort(key=lambda r:r['id'])
    with (out/'manifest.jsonl').open('w',encoding='utf-8') as f:
        for r in records:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    write_json(out/'skipped.json',skipped)
    train=[r for r in rows if r['split']=='train' and r['class_id'] is not None]
    templates={}
    for n in sorted({r['visible_bars'] for r in train}):
        rr=[r for r in train if r['visible_bars']==n]
        cls=Counter(r['class_id'] for r in rr).most_common(1)[0][0]
        templates[str(n)]={'class_id':cls,'xyxy':np.median([r['box']['pixel_box'] for r in rr],axis=0).tolist(),'confidence':1.0}
    receipt={'status':'built','source_commit':commit,'plan_sha256':sha(ROOT/PLAN),'manifest_sha256':sha(out/'manifest.jsonl'),
        'images':len(records),'counts':dict(Counter(r['cohort'] for r in records)),
        'event_training_pixel_parity':len(parity),'event_future_mutation_checks':len(parity),
        'market_future_mutation_checks':mutations,'skipped':len(skipped),'training_templates':templates,
        'source_bindings':list(sources.values()),'ranking_sha256':sha(inputs/'ranking.json'),
        'legacy_predictions_sha256':sha(inputs.parent/'results/predictions.jsonl'),'production_eligible':False}
    write_json(out/'build_receipt.json',receipt);print(json.dumps(receipt),flush=True)


def iou(a,b):
    w=max(0,min(a[2],b[2])-max(a[0],b[0]));h=max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=max(0,a[2]-a[0])*max(0,a[3]-a[1])+max(0,b[2]-b[0])*max(0,b[3]-b[1])-w*h
    return w*h/union if union>0 else 0.0


def score(inputs,output,device):
    import torch
    from ultralytics import YOLO
    from yoyo.evaluation.ma_gainers_model_scan import install_preprocess_shape_observer,verify_model_names
    from yoyo.datasets.ma_morphology_training_package import check_environment
    plan=json.loads((ROOT/PLAN).read_text(encoding='utf-8'));receipt=json.loads((inputs/'build_receipt.json').read_text(encoding='utf-8'))
    assert receipt['plan_sha256']==sha(ROOT/PLAN) and receipt['manifest_sha256']==sha(inputs/'manifest.jsonl')
    rows=read_rows(inputs/'manifest.jsonl');env=check_environment(cuda_required=device=='0')
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);torch.set_num_threads(4)
    for r in rows:assert sha(inputs/r['image_path'])==r['image_sha256']
    for name,binding in plan['models'].items():
        weight=ROOT/binding['path'];assert sha(weight)==binding['sha256']
        model=YOLO(str(weight));verify_model_names(model.names);install_preprocess_shape_observer(model)
        with (output/(name+'.jsonl')).open('x',encoding='utf-8') as log:
            for start in range(0,len(rows),8):
                group=rows[start:start+8];images=[cv2.imread(str(inputs/r['image_path'])) for r in group]
                predictions=model.predict(source=images,device=device,verbose=False,save=False,**plan['predict'])
                assert tuple(model.predictor._ma_gainers_observed_preprocess_shape)==(768,1280)
                for r,p in zip(group,predictions):
                    boxes=[{'class_id':int(c),'confidence':float(s),'xyxy':[float(v) for v in xy]}
                        for xy,s,c in zip(p.boxes.xyxy.cpu().tolist(),p.boxes.conf.cpu().tolist(),p.boxes.cls.cpu().tolist())]
                    log.write(json.dumps({'id':r['id'],'boxes':boxes})+'\n')
                if start%800==0:log.flush();print(json.dumps({'model':name,'done':min(start+8,len(rows)),'total':len(rows)}),flush=True)
    write_json(output/'score_receipt.json',{'status':'complete','environment':env,'images':len(rows),
        'manifest_sha256':receipt['manifest_sha256'],'plan_sha256':sha(ROOT/PLAN),
        'predictions_sha256':{name:sha(output/(name+'.jsonl')) for name in plan['models']},'production_eligible':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--output',type=Path,required=True)
    s=sub.add_parser('score');s.add_argument('--inputs',type=Path,required=True);s.add_argument('--output',type=Path,required=True);s.add_argument('--device',default='cpu')
    a=p.parse_args()
    if a.command=='build':build(a.output.resolve())
    else:score(a.inputs.resolve(),a.output.resolve(),a.device)
