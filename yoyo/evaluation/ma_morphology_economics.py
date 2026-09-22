"""Score a morphology detector on the unchanged full trading-candidate bank.

The old outcome-labelled image bank is inference-only: its empty detection
labels are never passed to YOLO validation and never treated as shape absence.
Every candidate is scored from pixels in its known rule direction, then joined
to frozen future return labels and matched random controls. This conditional
research analysis is separate from the rebuilt morphology test set.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
import time

import numpy as np

from yoyo.datasets.ma_morphology_redo import ROOT, load_plan, rows, read_json, write_json, write_rows, sha
from yoyo.evaluation.ma_profit_control_metrics import compare_controls
from yoyo.evaluation.ma_profit_model_metrics import _auc, _economics

OLD=ROOT/'experiments/active/exp-ma-profit3r-20260922-v1'
CONTROL_PATHS=[OLD/'matched_controls_owner1500_v2/receipt.json',
    OLD/'matched_controls_owner1500_v2/frozen_events.jsonl',
    OLD/'matched_control_outcomes_owner1500_v2/outcomes.jsonl',
    OLD/'matched_controls_owner1500_v2/frozen_sources.json',
    OLD/'matched_control_outcomes_owner1500_v2/summary.json']


def economic_events(ledger: list[dict], predictions: list[dict], split: str) -> list[dict]:
    """Join exact event sets; no spatial GT or outcome enters the score."""
    selected=[r for r in ledger if r['split']==split]
    targets={r['event_id']:r for r in selected}
    predicted={r['event_id']:r for r in predictions}
    if split not in ('val','test') or not targets or len(targets)!=len(selected) or len(predicted)!=len(predictions) or set(targets)!=set(predicted):raise ValueError('Incomplete economic candidate bank')
    result=[]
    for key,row in sorted(targets.items()):
        wanted=0 if row['direction']=='LONG' else 1
        if row['direction'] not in ('LONG','SHORT'):raise ValueError('Unknown candidate direction')
        boxes=predicted[key]['boxes']
        if any(b['class_id'] not in (0,1) or not math.isfinite(b['confidence']) or not 0<=b['confidence']<=1 for b in boxes):raise ValueError('Invalid inference score')
        score=max((b['confidence'] for b in boxes if b['class_id']==wanted),default=0.0)
        profit=row['profit']
        if profit['outcome'] not in ('TP','SL','TIMEOUT'):raise ValueError('Unknown outcome in frozen resolved bank')
        entry,risk=float(profit['entry_price']),float(profit['risk_price'])
        gross_r,net_r=float(profit['gross_r']),float(profit['net_r'])
        if not all(math.isfinite(x) for x in (entry,risk,gross_r,net_r)) or min(entry,risk)<=0:raise ValueError('Invalid economic label')
        if not math.isclose((gross_r-net_r)*risk/entry,.002,abs_tol=1e-9):raise ValueError('Cost contract drift')
        result.append({'event_id':key,'split':split,'direction':row['direction'],'bar_minutes':int(row['bar_minutes']),
            'score':score,'quality_score':row.get('quality_score'),'retained':bool(profit['retained']),
            'outcome':profit['outcome'],'net_profitable':net_r>0,'gross_r':gross_r,'net_r':net_r,
            'gross_bp':gross_r*risk/entry*1e4,'net_bp':net_r*risk/entry*1e4})
    return result


def matched_comparison(events: list[dict], ledger: list[dict], controls: list[dict], labelled: list[dict], selection: dict, split: str) -> dict:
    """Adapt the legacy helper's trigger field without claiming live deployment."""
    adapted=[dict(r,same_direction_deployed=r['score']>=.25) for r in events]
    result=compare_controls(adapted,ledger,controls,labelled,selection,split=split)
    for group in [result['overall'],*result['strata'].values()]:
        group['rule_direction_score_at_least_025']=group.pop('triggered_candidate_direction')
    result['trigger_scope']='Offline rule-known direction confidence >=0.25; no live deployment or morphology-absence inference.'
    return result


def validate_control_lineage(ledger_path: Path) -> None:
    """Bind matched entries, source manifest and labels to the target ledger."""
    selected,labelled=read_json(CONTROL_PATHS[0]),read_json(CONTROL_PATHS[4])
    if not any(r.get('sha256')==sha(ledger_path) for r in selected.get('inputs',[])):
        raise ValueError('Matched controls target a different ledger')
    if (sha(CONTROL_PATHS[1])!=selected['frozen_events_sha256']
        or sha(CONTROL_PATHS[1])!=labelled['input_events_sha256']
        or sha(CONTROL_PATHS[3])!=selected['frozen_sources_sha256']
        or sha(CONTROL_PATHS[3])!=labelled['source_manifest_sha256']
        or sha(CONTROL_PATHS[2])!=labelled['outcomes_sha256'] or labelled['lineage_errors']!=0):
        raise ValueError('Matched controls lineage drift')


def summarize(events: list[dict], key: str) -> dict:
    if any(r.get(key) is None for r in events):return {'status':'missing_score'}
    ordered=sorted(events,key=lambda r:(-r[key],r['event_id']))
    scores=np.array([r[key] for r in ordered],dtype=float)
    labels=np.array([r['retained'] for r in ordered],dtype=int)
    k=max(1,math.ceil(len(ordered)*.1))
    top=ordered[:k]
    values=np.array([r['net_bp'] for r in ordered])
    if len(np.unique(scores))<2:
        permutation={'status':'not_identifiable_constant_score'}
    else:
        rng=np.random.default_rng(0)
        observed=float(values[:k].mean())
        ge=sum(float(values[rng.permutation(len(values))[:k]].mean())>=observed for _ in range(1999))
        permutation={'status':'diagnostic_only_overlapping_events','draws':1999,'seed':0,'p_greater_or_equal':(ge+1)/2000}
    return {'status':'ok','events':len(events),'retained_profit_events':int(labels.sum()),
        'nonretained_profit_events':int(len(labels)-labels.sum()),'roc_auc_profit_label':_auc(labels,scores),
        'all':_economics(ordered),'top10':_economics(top),'top10_event_ids':[r['event_id'] for r in top],
        'ranking_permutation':permutation,'selection_note':'ceil10percent with event_id tiebreak; no spatial label gating'}


def run(plan_path: Path, model_path: Path, arm: str, out: Path, launch_path: Path) -> dict:
    p=load_plan(plan_path)
    if out.exists():raise FileExistsError(out)
    launch=read_json(launch_path)
    if launch['plan_sha256']!=sha(plan_path):raise ValueError('Launch plan drift')
    for name,digest in launch['files'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Launch file drift: '+name)
    for path in [Path(__file__),*CONTROL_PATHS,ROOT/p['inputs']['old_ledger']['path'],ROOT/p['inputs']['old_manifest']['path']]:
        if launch['files'].get(path.relative_to(ROOT).as_posix())!=sha(path):raise ValueError('Economic dependency not frozen: '+str(path))
    ledger=rows(ROOT/p['inputs']['old_ledger']['path'])
    validate_control_lineage(ROOT/p['inputs']['old_ledger']['path'])
    manifest=rows(ROOT/p['inputs']['old_manifest']['path'])
    bank=ROOT/p['parent_dataset_root']
    from ultralytics import YOLO
    model=YOLO(str(model_path))
    if dict(model.names)!={0:'dense_launch_long',1:'dense_launch_short'}:raise ValueError('Wrong detector semantics')
    out.mkdir(parents=True)
    receipt={'status':'running','arm':arm,'started_unix':time.time(),'model_sha256':sha(model_path),
        'plan_sha256':sha(plan_path),'launch_sha256':sha(launch_path),'code_sha256':sha(Path(__file__)),
        'bank_manifest_sha256':sha(ROOT/p['inputs']['old_manifest']['path']),
        'artifacts':{},'semantics':'Economic labels only; no morphology false-positive conclusions from nonwinners.',
        'scope':'Frozen rule candidates and rule-known direction; not autonomous full-market direction selection.'}
    write_json(out/'receipt.json',receipt)
    controls,selection,labelled=rows(CONTROL_PATHS[1]),read_json(CONTROL_PATHS[0]),rows(CONTROL_PATHS[2])
    try:
        for split in ('val','test'):
            selected=sorted((r for r in manifest if r['split']==split and r['variant']=='A'),key=lambda r:r['event_id'])
            predictions=[]
            for row in selected:
                path=bank/row['image_path']
                if sha(path)!=row['image_sha256']:raise ValueError('Economic bank pixel drift')
                pred=model.predict(str(path),imgsz=1280,conf=.001,iou=.70,device=0,augment=False,agnostic_nms=False,max_det=300,verbose=False)[0]
                boxes=[]
                if pred.boxes is not None:
                    for xywh,conf,cls in zip(pred.boxes.xywhn.cpu().tolist(),pred.boxes.conf.cpu().tolist(),pred.boxes.cls.cpu().tolist()):
                        boxes.append(dict(zip(('cx_norm','cy_norm','w_norm','h_norm'),xywh),confidence=float(conf),class_id=int(cls)))
                predictions.append({'event_id':row['event_id'],'split':split,'image_path':row['image_path'],'image_sha256':row['image_sha256'],'boxes':boxes})
            events=economic_events(ledger,predictions,split)
            metrics={'model':summarize(events,'score'),'quality_score_baseline':summarize(events,'quality_score'),
                'matched_random_control':matched_comparison(events,ledger,controls,labelled,selection,split),
                'detection_metrics':'not_applicable: bank shape labels remain unadjudicated',
                'strata':{}}
            groups=defaultdict(list)
            for row in events:
                for name in ('direction','bar_minutes'):groups[f'{name}={row[name]}'].append(row)
            metrics['strata']={key:{'model':summarize(value,'score'),'quality_score_baseline':summarize(value,'quality_score')} for key,value in sorted(groups.items())}
            write_rows(out/f'predictions_{split}.jsonl',predictions)
            write_rows(out/f'events_{split}.jsonl',events)
            write_json(out/f'metrics_{split}.json',metrics)
            for name in (f'predictions_{split}.jsonl',f'events_{split}.jsonl',f'metrics_{split}.json'):receipt['artifacts'][name]=sha(out/name)
        if sha(model_path)!=receipt['model_sha256']:raise ValueError('Model changed during evaluation')
        receipt.update(status='completed',completed_unix=time.time())
    except Exception as exc:
        receipt.update(status='failed',error=repr(exc))
        raise
    finally:write_json(out/'receipt.json',receipt)
    return receipt


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','model','out','launch-contract'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--arm',choices=['A','B'],required=True)
    args=p.parse_args();run(args.plan,args.model,args.arm,args.out,args.launch_contract)


if __name__=='__main__':main()
