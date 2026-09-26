"""Frozen early-v6 padding diagnosis and model-independent blind review inputs.

Sampling reads only existing causal market windows and clocks, never model
scores or future prices. Review annotations start empty; this creates no gold
labels, training samples or production eligibility. Padding comparisons reuse
the saved validator's identical NMS configuration and original coordinates.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from yoyo.evaluation.ma_early_replay import read_rows, write_json, iou
from yoyo.evaluation.ma_early_validation import ROOT, EXP, PLAN, sha

INPUTS = ROOT / 'data/crypto/research/early_v6_evaluation_20260926_v1'
VALIDATION = ROOT / EXP / 'early_validation_collected_20260926'
FOLLOWUP_PLAN = ROOT / EXP / 'early_followup_plan_20260926.json'


def frozen_plan():
    plan = json.loads(FOLLOWUP_PLAN.read_text(encoding='utf-8'))
    assert sha(INPUTS/'manifest.jsonl') == plan['input_manifest_sha256']
    assert sha(VALIDATION/'validation.json') == plan['validation_receipt_sha256']
    return plan


def coco_boxes(records):
    """Ultralytics saved JSON uses one-based categories; internal labels use zero."""
    result = defaultdict(list)
    for r in records:
        assert r['category_id'] in (1, 2)
        x, y, w, h = r['bbox']
        result['s_'+str(r['image_id'])].append({'class_id': r['category_id']-1,
            'confidence': r['score'], 'xyxy': [x, y, x+w, y+h]})
    return result


def matching_score(row, boxes):
    if row['class_id'] is None:
        return None
    return max((b['confidence'] for b in boxes if b['class_id']==row['class_id']
                and iou(row['target_xyxy'], b['xyxy']) >= .5), default=0.0)


def padding_audit(output):
    plan = frozen_plan()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    receipt = json.loads((VALIDATION/'validation.json').read_text(encoding='utf-8'))
    rows = [r for r in read_rows(INPUTS/'manifest.jsonl') if r['cohort']=='static']
    pairs = []
    for split in ('val', 'test'):
        for pool in ('reference', 'grade_a_challenge'):
            predictions = {}
            for pad in ('pad0', 'pad05'):
                name = f'early_v6_{pad}_{split}_{pool}'
                path = VALIDATION/'runs'/name/'raw_predictions.json'
                assert sha(path) == receipt['runs'][name]['predictions_sha256']
                predictions[pad] = coco_boxes(json.loads(path.read_text(encoding='utf-8')))
            for r in rows:
                if (r['split'], r['pool']) != (split, pool):
                    continue
                boxes = {p: predictions[p].get(r['id'], []) for p in predictions}
                scores = {p: matching_score(r, b) for p, b in boxes.items()}
                active = {p: [b for b in bb if b['confidence']>=plan['confidence']]
                          for p, bb in boxes.items()}
                state = ('negative' if r['class_id'] is None else
                    ('hit' if scores['pad0']>=.25 else 'miss')+'_to_'+
                    ('hit' if scores['pad05']>=.25 else 'miss'))
                reason = None
                if state == 'hit_to_miss':
                    if scores['pad05']>0:
                        reason = 'matching_box_below_confidence_threshold'
                    elif any(b['class_id']!=r['class_id'] and iou(r['target_xyxy'], b['xyxy'])>=.5
                             for b in active['pad05']):
                        reason = 'overlapping_box_wrong_direction'
                    elif active['pad05']:
                        reason = 'boxes_no_longer_match_original_core'
                    else:
                        reason = 'no_above_threshold_prediction'
                pairs.append({'id': r['id'], 'event_id': r['event_id'], 'split': split,
                    'pool': pool, 'symbol': r['symbol'], 'class_id': r['class_id'],
                    'state': state, 'matching_scores': scores, 'loss_observation': reason,
                    'above_threshold_boxes': active, 'target_xyxy': r['target_xyxy']})
    summary = {}
    for split in ('val', 'test'):
        rr = [r for r in pairs if r['split']==split]
        counts = dict(Counter(r['state'] for r in rr))
        summary[split] = {'states': counts,
            'negative_alarm_images': {p: sum(bool(r['above_threshold_boxes'][p]) for r in rr
                                           if r['class_id'] is None) for p in ('pad0','pad05')},
            'loss_observations': dict(Counter(r['loss_observation'] for r in rr if r['state']=='hit_to_miss'))}
    lost = [r for r in pairs if r['split']=='test' and r['state']=='hit_to_miss']
    selected = min(lost, key=lambda r: (-r['matching_scores']['pad0'], r['id']))
    write_json(output/'pairs.json', pairs)
    write_json(output/'summary.json', {'scope': 'Paired fixed .25 confidence, original validator NMS .7; not AP-optimal P/R.',
        'counts': summary, 'selected_probe': selected,
        'selection_rule': 'Highest pad0 matching confidence among all test hit-to-miss images; diagnostic worst case, not representative.',
        'plan_sha256': sha(FOLLOWUP_PLAN), 'code_sha256': sha(Path(__file__)), 'pairs_sha256': sha(output/'pairs.json'),
        'production_eligible': False})


def sample_blind(rows, plan):
    """Select nonoverlapping same-asset visible intervals without reading scores."""
    from datetime import datetime, timedelta
    seed = plan['blind_seed']
    cutoff = datetime.fromisoformat(plan['blind_visible_start_not_before'])
    candidates = []
    for r in rows:
        if r['cohort']!='market' or r['n']!=17:
            continue
        end = datetime.fromisoformat(r['decision_at_utc'])
        start = end-timedelta(minutes=17*r['minutes'])
        if start>=cutoff:
            candidates.append((r,start,end))
    rank = lambda r: hashlib.sha256((seed+'|'+r['id']).encode()).hexdigest()
    candidates.sort(key=lambda item: rank(item[0]))
    selected = []
    for _ in range(plan['blind_per_timeframe']):
        for minutes in (60,30,15):
            for r,start,end in candidates:
                if r['minutes']!=minutes or any(r['id']==x[0]['id'] for x in selected):
                    continue
                same_asset = [x for x in selected if x[0]['symbol']==r['symbol']]
                if len(same_asset)>=2 or any(start < x[2] and x[1] < end for x in same_asset):
                    continue
                selected.append((r,start,end))
                break
    return [r for r,_,_ in selected]


def font(size):
    from PIL import ImageFont
    return ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc', size)


def numbered_image(row, title):
    from PIL import Image, ImageDraw
    path = INPUTS/row['image_path']
    assert sha(path)==row['image_sha256']
    image = Image.open(path).convert('RGB')
    canvas = Image.new('RGB', (1280,850), 'white')
    canvas.paste(image,(0,65)); d = ImageDraw.Draw(canvas)
    d.text((22,13), title, font=font(28), fill='#17353c')
    for n in range(row['n']):
        x = row['left']+row['plot_width']*n/(row['n']-1)
        d.text((x,812),str(n+1),font=font(18),fill='#53646a',anchor='mt')
    return canvas


def blind_packet(output):
    from PIL import Image, ImageDraw
    plan = frozen_plan()
    if output.exists():
        raise FileExistsError(output)
    public=output/'reviewer'; private=output/'private'
    public.mkdir(parents=True); private.mkdir()
    rows=read_rows(INPUTS/'manifest.jsonl'); selected=sample_blind(rows,plan)
    assert len(selected)>=plan['blind_repeats'], 'Insufficient nonoverlapping cases; do not relax the rule.'
    tasks=[{'row':r,'repeat':False,'sort_key':r['id']} for r in selected]
    tasks += [{'row':r,'repeat':True,'sort_key':r['id']+'|repeat'} for r in selected[:plan['blind_repeats']]]
    for salt in range(10000):
        ordered=sorted(tasks,key=lambda t:hashlib.sha256((plan['blind_seed']+str(salt)+t['sort_key']).encode()).hexdigest())
        positions=defaultdict(list)
        for i,t in enumerate(ordered):positions[t['row']['id']].append(i)
        if all(len(p)==1 or p[1]-p[0]>=7 for p in positions.values()):break
    else:raise AssertionError('Could not space blind repeats')
    key=[]; answers=[]; tiles=[]
    for i,t in enumerate(ordered,1):
        ident=f'R{i:02d}';r=t['row']
        canvas=numbered_image(r,f'{ident} · 只看当前可见形态，右端为已闭合K线')
        canvas.save(public/(ident+'.png'));tiles.append(canvas)
        key.append({'review_id':ident,'source_id':r['id'],'symbol':r['symbol'],'minutes':r['minutes'],
            'decision_at_utc':r['decision_at_utc'],'is_repeat':t['repeat'],'source_sha256':r['image_sha256'],
            'rendered_sha256':sha(public/(ident+'.png'))})
        answers.append({'review_id':ident,'reviewer_id':None,'reviewed_at':None,'shape_present':None,
            'direction':None,'core_start_bar':None,'core_end_bar':None,'launch_visible':None,
            'first_visible_launch_bar':None,'comment':None})
    # Two charts per portrait page remain legible on a phone; no outcomes or predicted boxes.
    for start in range(0,len(tiles),2):
        page=Image.new('RGB',(1280,1748),'#edf3f4')
        for offset,tile in enumerate(tiles[start:start+2]):page.paste(tile,(0,offset*868))
        ImageDraw.Draw(page).text((20,1727),'请答：有 / 无 / 不确定；方向；核心首末编号；是否已启动。',font=font(17),fill='#415a62')
        page.save(public/f'page_{start//2+1:02d}.png')
    write_json(public/'answers.json',answers)
    write_json(public/'instructions.json',{'purpose':'Blind morphology review; no model outputs, future bars, trade returns or original labels are shown.',
        'shape_present':['yes','no','uncertain'],'direction':['long','short','none','uncertain'],
        'launch_visible':['yes','no','uncertain'],'bar_numbering':'Left-to-right1..17. Core and launch are separate judgments.',
        'rule':'Judge the intended compact MA platform/launch shape using only the visible prefix. Ambiguity remains uncertain; do not infer labels from future wins.',
        'status':'awaiting_human_review','production_eligible':False})
    write_json(private/'key.json',key)
    write_json(output/'receipt.json',{'status':'prepared_not_adjudicated','selection_uses_predictions':False,
        'unique_cases':len(selected),'target_unique_cases':plan['blind_per_timeframe']*3,
        'shortfall_reason':'Keep temporal exclusions even when the soft sample target is not reached.',
        'review_tasks':len(tasks),'blind_repeats':plan['blind_repeats'],
        'timeframe_counts':dict(Counter(r['minutes'] for r in selected)),
        'symbols':len({r['symbol'] for r in selected}),'same_asset_visible_intervals_overlap':False,
        'selection_bias':'Existing same-day top-gainers snapshot; random review sampling does not repair universe selection bias.',
        'private_key_sha256':sha(private/'key.json'),'plan_sha256':sha(FOLLOWUP_PLAN),'code_sha256':sha(Path(__file__)),
        'source_manifest_sha256':sha(INPUTS/'manifest.jsonl'),'shuffle_salt':salt,
        'human_labels_created':0,'training_eligible':False,'production_eligible':False})


def padding_probe(audit, output, device, phase_plan=None):
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO
    from yoyo.datasets.ma_morphology_training_package import check_environment
    plan=frozen_plan(); env=check_environment(cuda_required=device=='0')
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    selected=json.loads((audit/'summary.json').read_text(encoding='utf-8'))['selected_probe']
    row=next(r for r in read_rows(INPUTS/'manifest.jsonl') if r['id']==selected['id'])
    path=INPUTS/row['image_path']; assert sha(path)==row['image_sha256']
    weight_plan=json.loads((ROOT/PLAN).read_text(encoding='utf-8'))['models']['early_v6']
    weight=ROOT/weight_plan['path']; assert sha(weight)==weight_plan['sha256']
    model=YOLO(str(weight)); rgb=cv2.cvtColor(cv2.imread(str(path)),cv2.COLOR_BGR2RGB)
    assert rgb.shape==(742,1280,3)
    runs=[]; tensors=[]
    arms=[('baseline',0,0),('padded',16,16),('restored',0,0)]
    if phase_plan is not None:
        control=json.loads(phase_plan.read_text(encoding='utf-8'))
        assert control['source_audit_sha256']==sha(audit/'summary.json')
        assert control['image_id']==row['id']
        arms=[(a['name'],a['left'],a['right']) for a in control['arms']]
        assert all(left+right==32 for _,left,right in arms)
    for name,side,right in arms:
        canvas=np.pad(rgb,((13,13),(side,right),(0,0)),constant_values=114)
        assert np.array_equal(canvas[13:-13,side:side+1280],rgb)
        tensor=torch.from_numpy(np.ascontiguousarray(canvas.transpose(2,0,1))).float()/255
        tensor=tensor.unsqueeze(0).repeat(8,1,1,1); tensors.append(tensor)
        result=model.predict(source=tensor,device=device,conf=.001,iou=.7,imgsz=1280,verbose=False,save=False)
        boxes=[]
        for xy,score,cls in zip(result[0].boxes.xyxy.cpu().tolist(),result[0].boxes.conf.cpu().tolist(),result[0].boxes.cls.cpu().tolist()):
            boxes.append({'class_id':int(cls),'confidence':score,
                'xyxy':[max(0,min(1280,xy[0]-side)),max(0,min(742,xy[1]-13)),
                        max(0,min(1280,xy[2]-side)),max(0,min(742,xy[3]-13))]})
        runs.append({'name':name,'left_padding':side,'right_padding':right,'tensor_shape':list(tensor.shape),
            'tensor_sha256':hashlib.sha256(tensor.numpy().tobytes()).hexdigest(),
            'model_fp16':bool(model.predictor.model.fp16),'matching_score':matching_score(row,boxes),'boxes':boxes})
    assert torch.equal(tensors[0],tensors[-1])
    a,b,c=runs[0],runs[1],runs[-1]
    restored=(len(a['boxes'])==len(c['boxes']) and all(x['class_id']==y['class_id'] and
        abs(x['confidence']-y['confidence'])<1e-5 and max(abs(v-w) for v,w in zip(x['xyxy'],y['xyxy']))<.01
        for x,y in zip(a['boxes'],c['boxes'])))
    write_json(output/'probe.json',{'status':'complete','image_id':row['id'],'image_sha256':row['image_sha256'],
        'weight_sha256':weight_plan['sha256'],'class_id':row['class_id'],'target_xyxy':row['target_xyxy'],
        'runs':runs,'restoration_matches':restored,'actual_image_content_identical':True,'environment':env,
        'saved_validation_failure_reproduced':a['matching_score']>=.25 and b['matching_score']<.25,
        'scope':('Fixed1312px canvas, vary only left/right allocation of32px total gray border.' if phase_plan else
                 'Fixed tensor A/B/A: only16px left/right gray border. Boxes mapped back to original pixels. No retraining or threshold change.'),
        'phase_plan_sha256':sha(phase_plan) if phase_plan else None,
        'plan_sha256':sha(FOLLOWUP_PLAN),'code_sha256':sha(Path(__file__)),
        'production_eligible':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    for name in ('audit','blind','probe'):
        c=sub.add_parser(name); c.add_argument('--output',type=Path,required=True)
        if name=='probe':
            c.add_argument('--audit',type=Path,required=True);c.add_argument('--device',default='cpu')
            c.add_argument('--phase-plan',type=Path)
    args=p.parse_args()
    if args.command=='audit':padding_audit(args.output.resolve())
    elif args.command=='blind':blind_packet(args.output.resolve())
    else:padding_probe(args.audit.resolve(),args.output.resolve(),args.device,args.phase_plan.resolve() if args.phase_plan else None)
