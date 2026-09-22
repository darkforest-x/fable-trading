"""Freeze actual owner1500 v4 training/evaluation evidence without re-scoring.

Consumes completed receipts and their exact bytes. No threshold selection,
training, label construction, or model promotion occurs in this delivery step.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1048576), b''): h.update(block)
    return h.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args=p.parse_args(); exp=args.experiment.resolve(); out=args.out.resolve()
    if out.exists(): raise RuntimeError('delivery output exists')
    module=Path(__file__).resolve().relative_to(ROOT)
    if subprocess.check_output(['git','status','--porcelain','--',str(module)],cwd=ROOT,text=True).strip(): raise RuntimeError('commit delivery builder first')
    inputs=set()
    def record(path):
        path=Path(path).resolve(); inputs.add(path); return read(path)
    inventory=record(exp/'trained_owner1500_v4/download_inventory.json')
    for relative,row in inventory.items():
        path=exp/'trained_owner1500_v4'/relative
        if not path.resolve().is_relative_to(exp/'trained_owner1500_v4'): raise RuntimeError('training path escape')
        inputs.add(path.resolve())
        if sha(path)!=row['sha256'] or path.stat().st_size!=row['size_bytes']: raise RuntimeError('training bytes drift')
    train=record(exp/'trained_owner1500_v4/training_receipt.json')
    if train['status']!='completed' or any(train['arms'][a]['status']!='completed' for a in ('A','B')): raise RuntimeError('training incomplete')
    manifest_sha=train['audit']['manifest_sha256']
    ledger_path=exp/'selection_queue_round_017/dataset_ledger.jsonl'; inputs.add(ledger_path)
    ledger_sha=sha(ledger_path)
    pool={s:{r['event_id'] for r in [json.loads(line) for line in ledger_path.read_text().splitlines()] if r['split']==s} for s in ('val','test')}
    summary={'status':'completed','research_decision':'rejected','production_eligible':False,'training_counts':train['audit'], 'arms':{},'economic_rows':[],'detection_rows':[]}
    for arm in ('A','B'):
        csv_path=exp/'trained_owner1500_v4'/f'arm_{arm}/results.csv'
        rows=list(csv.DictReader(csv_path.open()))
        if [int(r['epoch']) for r in rows]!=list(range(1,41)): raise RuntimeError('epoch sequence drift')
        best=max(rows,key=lambda r:float(r['metrics/mAP50-95(B)']))
        summary['arms'][arm]={'epochs':40,'csv_max_fitness_epoch':int(best['epoch']),'csv_max_fitness_metrics':best,'best_sha256':inventory[f'arm_{arm}/weights/best.pt']['sha256']}
        base=exp/'evaluation_owner1500_v4'/f'arm_{arm}'
        ev=record(base/'receipt.json')
        if ev['status']!='completed' or ev['arm']!=arm or ev['model_sha256']!=summary['arms'][arm]['best_sha256'] or ev['manifest_sha256']!=manifest_sha or ev['ledger_sha256']!=ledger_sha or ev['splits']!=['val','test']: raise RuntimeError('evaluation binding drift')
        for name,digest in ev['artifacts'].items():
            path=base/name; inputs.add(path)
            if sha(path)!=digest: raise RuntimeError('evaluation artifact drift')
        control_base=exp/'control_comparison_owner1500_v4'/f'arm_{arm}'
        cr=record(control_base/'receipt.json')
        if cr['status']!='completed' or cr['arm']!=arm: raise RuntimeError('control comparison incomplete')
        for row in cr['inputs']:
            path=Path(row['path']); inputs.add(path)
            if sha(path)!=row['sha256']: raise RuntimeError('control input drift')
        for name,digest in cr['artifacts'].items():
            path=control_base/name; inputs.add(path)
            if sha(path)!=digest: raise RuntimeError('control artifact drift')
        for split in ('val','test'):
            events=[json.loads(line) for line in (base/f'events_{split}.jsonl').read_text().splitlines()]
            if len(events)!=len(pool[split]) or {r['event_id'] for r in events}!=pool[split]: raise RuntimeError('full evaluation pool drift')
            m=record(base/f'metrics_{split}.json'); c=record(control_base/f'matched_metrics_{split}.json')
            for label,key,ckey in ((arm,'model','model_top10'),('quality_score','quality_score_baseline','quality_top10')):
                if label=='quality_score' and arm=='B': continue
                x=m[key]; y=c['overall'][ckey]
                summary['economic_rows'].append({'group':label,'split':split,'N':x['events'],'positives':x['retained_events'],'auc':x['roc_auc'],'p_diagnostic':x['ranking_permutation'].get('p_greater_or_equal'),'topN':x['top10']['events'],'gross_bp':x['top10']['mean_gross_bp'],'net_bp':x['top10']['mean_net_bp'],'tp_rate':x['top10']['tp_rate'],'net_win_rate':x['top10']['net_profitable_rate'],'pairedN':y['paired_events'],'paired_candidate_net_bp':y['candidate_on_paired_events']['net_bp'],'paired_control_net_bp':y['per_event_mean_control']['net_bp'],'paired_delta_bp':y['candidate_minus_control']['net_bp']})
            summary['detection_rows'].append({'arm':arm,'split':split,**m['model']['detection'],'triggered_net_bp':m['model']['triggered_candidate_direction']['mean_net_bp']})
    if any(r['net_bp']>0 and r['p_diagnostic']<.01 for r in summary['economic_rows'] if r['group'] in ('A','B')): raise RuntimeError('unexpected favorable result requires separate audit; do not default reject')
    for relative in ('plan.json','dataset_plan_owner1500_v4.json','training_contract_owner1500_v2.json','owner_amendment_1500_v2.json','dataset_owner1500_v4_audit/receipt.json','owner1500_v4_visual_review.json','post_training_owner1500_v4_status.json','preprocess_diagnostic_owner1500_v4/diagnostic.json','preprocess_diagnostic_owner1500_v4/transfer_receipt.json'):
        inputs.add(exp/relative)
    out.mkdir(parents=True)
    summary_path=out/'summary.json'; summary_path.write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    records=[{'path':str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),'sha256':sha(path),'size_bytes':path.stat().st_size} for path in sorted(inputs)]
    manifest={'status':'completed','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'builder':str(module),'builder_sha256':sha(Path(__file__)),'manifest_sha256':manifest_sha,'ledger_sha256':ledger_sha,'artifacts':records,'summary_sha256':sha(summary_path),'production_eligible':False,'research_decision':'rejected','scope':'1506 train events per Owner capacity amendment; A/B 40 epochs; full val/test and frozen matched controls. All reported returns retrospective, not production.'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':'completed','verified_files':len(records),'decision':'rejected'}))


if __name__=='__main__': main()
