"""Frozen early-v6 compatibility check against existing owner-reviewed cases.

This is a diagnostic, not a new independent accuracy benchmark: old renderers,
short-only verdicts, inherited boxes, and possible training-lineage overlap are
reported separately. No labels are inferred from subsequent price returns.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from yoyo.evaluation.ma_early_validation import ROOT, EXP, PLAN, sha
from yoyo.evaluation.ma_early_replay import read_rows, write_json, render_window, persist_image, iou


def build(out):
    from yoyo.datasets.ma_profit_dataset import _committed
    from yoyo.datasets.ma_morphology_redo import read_interval
    import pandas as pd
    commit = _committed([Path(__file__)])
    if out.exists():
        raise FileExistsError(out)
    (out/'images').mkdir(parents=True)
    rows = []
    sources = []

    def load(path):
        sources.append({'path':path, 'sha256':sha(ROOT/path)})
        return read_rows(ROOT/path)

    def copy(row, ident, path, expected_sha, cohort, target=None, **extra):
        source = ROOT/path
        assert sha(source) == expected_sha, path
        image = 'images/'+ident+'.png'
        shutil.copyfile(source, out/image)
        rows.append({'id':ident, 'cohort':cohort, 'image_path':image,
            'image_sha256':expected_sha, 'source_image':path, 'target_xyxy':target,
            'symbol':row['symbol'], 'minutes':15, 'class_id':1,
            'label_scope':'owner_short_semantics_inherited_geometry_not_reconfirmed', **extra})

    for row in load('datasets/owner_short_gold_center_v1/positive_manifest.jsonl'):
        if row['split'] != 'val':
            continue
        assert row['source_owner_gold_confirmed']
        x,y,w,h = row['yolo_box']
        target = [(x-w/2)*1280,(y-h/2)*742,(x+w/2)*1280,(y+h/2)*742]
        copy(row,'gold_'+row['sample_id'],row['image_path'],row['image_sha256'],
            'legacy_owner_positive',target, decision_at_utc=row['end_time'],
            original_owner_ids=row['owner_annotation_ids'],
            interval_start=row['start_time'],interval_end=row['end_time'])

    for row in load('analysis/output/owner_short_train_hardneg_newblocks200_v3/owner_review_labeled_manifest.jsonl'):
        if not row['owner_confirmed'] or row['owner_decision'] != 'hard_negative':
            continue
        copy(row,'negative_'+row['review_id'],row['causal_input_path'],row['causal_input_sha256'],
            'legacy_owner_short_negative', decision_at_utc=row['decision_time'],
            interval_start=row['window_start_time'],interval_end=row['decision_time'],
            owner_decision=row['owner_decision'],
            negative_scope='Short target rejected; long predictions are unadjudicated')

    for row in load('experiments/active/exp-15m-ma-launch-owner-strict-review50-v5/results/review_manifest.jsonl'):
        if row['status'] != 'OWNER_REFERENCE_RECROP':
            continue
        ident='reference_'+str(row['source_order'])
        box=row['box']; target=[box[k] for k in ('x0','y0','x1','y1')]
        copy(row,ident+'_original',row['model_input_path'],row['model_input_sha256'],
            'owner_reference_original',target, owner_reason=row['reason'],post=0)
        path=ROOT/row['source_path']; anchor=pd.Timestamp(row['anchor_time'])
        frame=read_interval(path,anchor-pd.Timedelta(days=16),anchor+pd.Timedelta(hours=2))
        core_end=frame.index[frame._source_i.eq(row['core_end_source_i'])].tolist()
        assert len(core_end)==1
        end=core_end[0]; start=end-row['core_bars']+1
        assert frame.open_time.iloc[end] == anchor+pd.Timedelta(minutes=15*row['core_end_offset'])
        for post in (0,1,2):
            png,meta=render_window(frame,end+post,17,15)
            changed=frame.copy(); changed.loc[end+post+1:,['open','high','low','close']] *= 11
            assert render_window(changed,end+post,17,15)[0] == png
            x=[meta['left']+(v-meta['visible_start'])/(meta['n']-1)*meta['plot_width']
               for v in (start-.34,end+.34)]
            y=[meta['top']+(meta['price_max']-v)/(meta['price_max']-meta['price_min'])*meta['plot_height']
               for v in (box['box_price_high'],box['box_price_low'])]
            meta.update(cohort='owner_reference_current_renderer',symbol=row['symbol'],minutes=15,
                class_id=1,post=post,owner_reason=row['reason'],target_xyxy=[x[0],y[0],x[1],y[1]],
                label_scope='Owner accepted source shape, not this recrop/onset/HL2 geometry',
                source_path=row['source_path'],source_sha256=sha(path),
                core_end_time=frame.open_time.iloc[end].isoformat())
            rows.append(persist_image(out,ident+'_current_p'+str(post),png,meta))

    training=read_rows(ROOT/'datasets/ma_launch_owner1500_morph_v6_early_20260926_v1/manifest.jsonl')
    train_hashes={r['image_sha256'] for r in training if r['split']=='train'}
    for row in rows:
        row['exact_train_image_overlap']=row['image_sha256'] in train_hashes
        # A broad same-asset interval check is evidence, not a proof of independence.
        asset=row['symbol'].split('_')[0]
        end=pd.Timestamp(row.get('interval_end',row.get('core_end_time','1970-01-01T00:00:00Z')))
        start=pd.Timestamp(row.get('interval_start',end-pd.Timedelta(hours=4)))
        row['same_asset_train_core_within_1day']=sorted({r['event_id'] for r in training
            if r['split']=='train' and r['canonical_asset']==asset
            and start-pd.Timedelta(days=1) <= pd.Timestamp(r['core_end_time']) <= end+pd.Timedelta(days=1)})
    (out/'manifest.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    receipt={'builder_commit':commit,'images':len(rows),'counts':dict(Counter(r['cohort'] for r in rows)),
        'sources':sources,'manifest_sha256':sha(out/'manifest.jsonl'),'plan_sha256':sha(ROOT/PLAN),
        'training_manifest_sha256':sha(ROOT/'datasets/ma_launch_owner1500_morph_v6_early_20260926_v1/manifest.jsonl'),
        'purpose':'Reference compatibility, not an independent or current-renderer accuracy estimate',
        'current_render_future_mutation_checks':9,'production_eligible':False}
    write_json(out/'build_receipt.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


def score(inputs,out,device):
    import cv2
    import torch
    from ultralytics import YOLO
    from yoyo.datasets.ma_morphology_training_package import check_environment
    from yoyo.evaluation.ma_gainers_model_scan import install_preprocess_shape_observer,verify_model_names
    plan=json.loads((ROOT/PLAN).read_text(encoding='utf-8'))
    receipt=json.loads((inputs/'build_receipt.json').read_text(encoding='utf-8'))
    assert sha(inputs/'manifest.jsonl')==receipt['manifest_sha256']
    assert sha(ROOT/PLAN)==receipt['plan_sha256']
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    env=check_environment(cuda_required=device=='0');torch.set_num_threads(4)
    rows=read_rows(inputs/'manifest.jsonl'); binding=plan['models']['early_v6']
    assert sha(ROOT/binding['path'])==binding['sha256']
    model=YOLO(str(ROOT/binding['path']));verify_model_names(model.names)
    install_preprocess_shape_observer(model)
    with (out/'predictions.jsonl').open('x',encoding='utf-8') as log:
        for start in range(0,len(rows),8):
            group=rows[start:start+8]
            for r in group:assert sha(inputs/r['image_path'])==r['image_sha256']
            images=[cv2.imread(str(inputs/r['image_path'])) for r in group]
            results=model.predict(images,device=device,verbose=False,save=False,**plan['predict'])
            assert tuple(model.predictor._ma_gainers_observed_preprocess_shape)==(768,1280)
            for r,p in zip(group,results):
                boxes=[{'class_id':int(c),'confidence':float(s),'xyxy':[float(v) for v in xy]}
                    for xy,s,c in zip(p.boxes.xyxy.cpu().tolist(),p.boxes.conf.cpu().tolist(),p.boxes.cls.cpu().tolist())]
                log.write(json.dumps({'id':r['id'],'boxes':boxes})+'\n')
            log.flush()
            if start%80==0:print(json.dumps({'done':min(start+8,len(rows)),'total':len(rows)}),flush=True)
    write_json(out/'score_receipt.json',{'status':'complete','images':len(rows),'environment':env,
        'weight':binding,'predict':plan['predict'],'tensor_shape':[768,1280],
        'manifest_sha256':sha(inputs/'manifest.jsonl'),'predictions_sha256':sha(out/'predictions.jsonl')})


def summarize(inputs,scores):
    rows=read_rows(inputs/'manifest.jsonl'); preds={r['id']:r['boxes'] for r in read_rows(scores/'predictions.jsonl')}
    assert len(rows)==len(preds) and {r['id'] for r in rows}==set(preds)
    result={}
    details=[]
    for r in rows:
        b=preds[r['id']]; target=r['target_xyxy']
        same=[p for p in b if p['class_id']==1]
        matched=bool(target and any(iou(p['xyxy'],target)>=.5 for p in same))
        entry={'id':r['id'],'cohort':r['cohort'],'short_alarm':bool(same),'any_alarm':bool(b),
               'matched_short_target':matched,'exact_train_image_overlap':r['exact_train_image_overlap'],
               'train_core_nearby':bool(r['same_asset_train_core_within_1day'])}
        details.append(entry)
    for cohort in sorted({r['cohort'] for r in details}):
        rr=[r for r in details if r['cohort']==cohort]
        result[cohort]={'images':len(rr),**{k:sum(r[k] for r in rr) for k in
            ('short_alarm','any_alarm','matched_short_target','exact_train_image_overlap','train_core_nearby')}}
    write_json(scores/'summary.json',{'cohorts':result,'details':details,
        'interpretation':'Short-only historical reference compatibility; no unseen/live accuracy claim',
        'production_eligible':False})
    print(json.dumps(result,ensure_ascii=False),flush=True)


def gallery(inputs,scores,out):
    """Post-inference overlays; their pixels never enter either model."""
    from PIL import Image, ImageDraw
    from yoyo.evaluation.ma_early_review_images import panel,font
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    rows=read_rows(inputs/'manifest.jsonl')
    predictions={r['id']:r['boxes'] for r in read_rows(scores/'predictions.jsonl')}
    selections=[]
    for r in rows:
        if r['cohort']=='owner_reference_current_renderer' and r['post']==1:
            name=r['id']+'.png'
            fig=panel(inputs,r,predictions[r['id']],f"历史认可参考 #{r['id'].split('_')[1]} · 当前画法 · 核心后1根")
            fig.save(out/name)
            selections.append({'id':r['id'],'file':name,'sha256':sha(out/name),'rule':'All three explicit references at post1'})
    matches=[r for r in rows if r['cohort']=='legacy_owner_positive'
        and any(b['class_id']==1 and iou(b['xyxy'],r['target_xyxy'])>=.5 for b in predictions[r['id']])]
    if matches:
        matches.sort(key=lambda r:r['id']);r=matches[len(matches)//2]
        name='legacy_reference_hit.png'
        panel(inputs,r,predictions[r['id']],'能识别的例子 · 历史人工正例 · 原始画法').save(out/name)
        selections.append({'id':r['id'],'file':name,'sha256':sha(out/name),'rule':'Median matched legacy reference by ID'})
    replay=ROOT/'data/crypto/research/early_v6_evaluation_20260926_v1'
    replay_rows={r['id']:r for r in read_rows(replay/'manifest.jsonl')}
    replay_scores={r['id']:r['boxes'] for r in read_rows(ROOT/EXP/'early_replay_scores_collected_20260926/early_v6.jsonl')}
    key=json.loads((ROOT/EXP/'blind_review_20260926_v1/private/key.json').read_text(encoding='utf-8'))
    reviewed=[]
    for k in key[:4]:
        r=replay_rows[k['source_id']];boxes=replay_scores[r['id']]
        reviewed.append({'review_id':k['review_id'],'source_id':r['id'],'boxes':boxes,
            'owner_verdict':'no','scope':'Each page-level 无 was recorded for both displayed rows; no future labels inferred'})
        if boxes:
            name='owner_rejected_'+k['review_id']+'.png'
            r=dict(r,target_xyxy=None)
            panel(replay,r,boxes,'你刚判为“无”的图 · 模型仍给出高置信度框').save(out/name)
            selections.append({'id':r['id'],'file':name,'sha256':sha(out/name),'rule':'All alarms among four existing owner no verdicts'})
    # Review every symbol's strongest alarm, exposing actual market behavior.
    by_symbol={}
    for ident,boxes in replay_scores.items():
        r=replay_rows[ident]
        if r['cohort']!='market' or not boxes:continue
        candidate=(max(b['confidence'] for b in boxes),ident)
        if candidate>by_symbol.get(r['symbol'],(-1,'')):by_symbol[r['symbol']]=candidate
    panels=[]
    for symbol,(_,ident) in sorted(by_symbol.items()):
        r=replay_rows[ident]
        fig=panel(replay,r,replay_scores[ident],'连续行情 · 每币最高置信度检测 · 未经人工裁决')
        name='market_'+symbol+'.png';fig.save(out/name)
        panels.append(fig.resize((640,418)))
        selections.append({'id':ident,'file':name,'sha256':sha(out/name),'rule':'Highest confidence alarm per symbol; not representative precision sample'})
    for start in range(0,len(panels),4):
        canvas=Image.new('RGB',(1280,836),'#f1f4f5')
        for i,fig in enumerate(panels[start:start+4]):canvas.paste(fig,((i%2)*640,(i//2)*418))
        canvas.save(out/f'market_sheet_{start//4+1}.png')
    write_json(out/'selection.json',{'items':selections,'owner_four':reviewed,
        'input_manifest_sha256':sha(inputs/'manifest.jsonl'),
        'prediction_sha256':sha(scores/'predictions.jsonl'),'rendered_after_inference':True})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['build','score','summarize','gallery'])
    p.add_argument('--inputs',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--device',default='cpu')
    p.add_argument('--scores',type=Path)
    a=p.parse_args()
    if a.command=='build':build(a.output.resolve())
    elif a.command=='score':score(a.inputs.resolve(),a.output.resolve(),a.device)
    elif a.command=='summarize':summarize(a.inputs.resolve(),a.output.resolve())
    else:gallery(a.inputs.resolve(),a.scores.resolve(),a.output.resolve())
