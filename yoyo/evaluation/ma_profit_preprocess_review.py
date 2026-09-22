"""Diagnose rectangular padding sensitivity without changing frozen evaluation.

Uses the first four retained and first four failed validation IDs, sorted by ID.
Only rect changes between paired calls. Outcome labels select the QA strata but
never enter model input. This is a diagnostic, not a revised model result.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    import torch
    from ultralytics import YOLO
    from yoyo.evaluation.ma_profit_model_metrics import iou_xywh
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', type=Path, required=True)
    p.add_argument('--evaluation', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists() or not torch.cuda.is_available():
        raise RuntimeError('new diagnostic output and CUDA required')
    manifest = [json.loads(s) for s in (args.dataset/'manifest.jsonl').read_text(encoding='utf-8').splitlines()]
    rows = sorted([r for r in manifest if r['split']=='val' and r['variant']=='A'], key=lambda r:r['event_id'])
    selected = [r for positive in (True, False) for r in [x for x in rows if (x.get('box') is not None)==positive][:4]]
    records = []
    for arm in ('A','B'):
        parent = args.evaluation/f'arm_{arm}'
        meta = json.loads((parent/'receipt.json').read_text(encoding='utf-8'))
        model_path = Path(meta['model_path'])
        if meta['status']!='completed' or sha(model_path)!=meta['model_sha256'] or sha(args.dataset/'manifest.jsonl')!=meta['manifest_sha256']:
            raise RuntimeError('frozen input binding drift')
        originals = {r['event_id']:r for r in [json.loads(s) for s in (parent/'predictions_val.jsonl').read_text(encoding='utf-8').splitlines()]}
        model = YOLO(str(model_path))
        for rect in (True, False):
            for row in selected:
                image_path = args.dataset/row['image_path']
                if sha(image_path)!=row['image_sha256']:
                    raise RuntimeError('image drift')
                result = model.predict(str(image_path),imgsz=1280,conf=.001,iou=.70,device=0,augment=False,agnostic_nms=False,max_det=300,rect=rect,verbose=False)[0]
                tensor_shape = list(model.predictor.preprocess([result.orig_img]).shape)
                boxes=[]
                for xywh,confidence,class_id in zip(result.boxes.xywhn.cpu().tolist(),result.boxes.conf.cpu().tolist(),result.boxes.cls.cpu().tolist()):
                    boxes.append(dict(zip(('cx_norm','cy_norm','w_norm','h_norm'),xywh),confidence=float(confidence),class_id=int(class_id)))
                wanted = 0 if row['direction']=='LONG' else 1
                same=[b for b in boxes if b['class_id']==wanted]
                originals_boxes = originals[row['event_id']]['boxes']
                delta = max((abs(a[k]-b[k]) for a,b in zip(boxes,originals_boxes) for k in ('cx_norm','cy_norm','w_norm','h_norm','confidence','class_id')),default=0.) if len(boxes)==len(originals_boxes) else None
                records.append({'arm':arm,'event_id':row['event_id'],'retained':row.get('box') is not None,'rect':rect,'tensor_shape':tensor_shape,'half':bool(model.predictor.args.half),'score':max((b['confidence'] for b in same),default=0.),'hit':row.get('box') is not None and any(b['confidence']>=.25 and iou_xywh(b,row['box'])>=.5 for b in same),'max_iou':max((iou_xywh(b,row['box']) for b in same),default=0.) if row.get('box') else None,'box_count':len(boxes),'max_difference_from_frozen_prediction':delta,'boxes':boxes,'model_sha256':meta['model_sha256']})
    args.out.mkdir(parents=True)
    (args.out/'diagnostic.json').write_text(json.dumps({'status':'completed','purpose':'padding sensitivity diagnostic only; formal evaluation unchanged','selection':'first four val positives and four val failures in event_id order','manifest_sha256':sha(args.dataset/'manifest.jsonl'),'code_sha256':sha(Path(__file__)),'records':records,'production_eligible':False},indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status':'completed','records':len(records)}))


if __name__=='__main__':
    main()
