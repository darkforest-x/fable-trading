"""Freeze completed negative-redo evidence and comparable previous-run metrics."""
from __future__ import annotations
import csv
from pathlib import Path
import subprocess

from yoyo.datasets.ma_profit_negative_redo import ROOT, read_json, rows, sha256, write_json

EXP=ROOT/'experiments/active/exp-ma-profit3r-negatives-20260922-v2'
OLD=ROOT/'experiments/active/exp-ma-profit3r-20260922-v1'


def main() -> None:
    module=Path(__file__).resolve()
    if subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()!='main':raise RuntimeError('main required')
    if subprocess.check_output(['git','status','--porcelain','--',str(module.relative_to(ROOT))],cwd=ROOT,text=True).strip():raise RuntimeError('commit delivery builder first')
    out=EXP/'delivery'
    if out.exists():raise FileExistsError(out)
    inputs=set()
    def record(path):inputs.add(path);return read_json(path)
    plan=record(EXP/'plan.json');launch=record(EXP/'launch_contract.json');inventory=record(EXP/'download_inventory.json')
    for name,r in inventory.items():
        p=EXP/name
        if not p.resolve().is_relative_to(EXP) or sha256(p)!=r['sha256'] or p.stat().st_size!=r['bytes']:raise RuntimeError('download drift')
        inputs.add(p)
    training=record(EXP/'trained/training_receipt.json')
    if training['status']!='completed' or training['audit']!=launch['dataset_audit']:raise RuntimeError('training/audit binding drift')
    summary={'status':'completed','production_eligible':False,'research_decision':'pending_review','counts':training['audit']['actual_label_counts'],'economic_rows':[],'detection_rows':[],'arms':{}}
    ledger=ROOT/plan['inputs']['old_ledger']['path'];inputs.add(ledger)
    pool={s:{r['event_id'] for r in rows(ledger) if r['split']==s} for s in ('val','test')}
    for arm in ('A','B'):
        if training['arms'][arm]['status']!='completed':raise RuntimeError('incomplete arm')
        weight=EXP/f'trained/arm_{arm}/weights/best.pt'
        epoch_rows=list(csv.DictReader((EXP/f'trained/arm_{arm}/results.csv').open()))
        if [int(float(r['epoch'])) for r in epoch_rows]!=list(range(1,41)):raise RuntimeError('epoch sequence drift')
        summary['arms'][arm]={'epochs':40,'best_sha256':sha256(weight),'csv_max_map_row':max(epoch_rows,key=lambda r:float(r['metrics/mAP50-95(B)']))}
        evdir=EXP/f'evaluation/arm_{arm}';ev=record(evdir/'receipt.json')
        expect={'status':'completed','arm':arm,'model_sha256':sha256(weight),'manifest_sha256':training['audit']['manifest_sha256'],'ledger_sha256':sha256(ledger),'splits':['val','test'],'confidence':.001,'nms_iou':.70,'imgsz':1280,'device':0,'augment':False,'max_det':300,'agnostic_nms':False}
        if any(ev.get(k)!=v for k,v in expect.items()):raise RuntimeError('eval contract drift')
        for name,h in ev['artifacts'].items():
            p=evdir/name;inputs.add(p)
            if Path(name).name!=name or sha256(p)!=h:raise RuntimeError('eval bytes drift')
        cdir=EXP/f'controls/arm_{arm}';cr=record(cdir/'receipt.json')
        if cr['status']!='completed' or cr['arm']!=arm:raise RuntimeError('controls incomplete')
        for r in cr['inputs']:
            p=Path(r['path']);inputs.add(p)
            if sha256(p)!=r['sha256']:raise RuntimeError('control input drift')
        for name,h in cr['artifacts'].items():
            p=cdir/name;inputs.add(p)
            if sha256(p)!=h:raise RuntimeError('control output drift')
        for split in ('val','test'):
            es=rows(evdir/f'events_{split}.jsonl')
            if len(es)!=len(pool[split]) or {r['event_id'] for r in es}!=pool[split]:raise RuntimeError('eval event pool drift')
            m=record(evdir/f'metrics_{split}.json');c=record(cdir/f'matched_metrics_{split}.json')
            for name,mkey,ckey in ((arm,'model','model_top10'),('quality_score','quality_score_baseline','quality_top10')):
                if arm=='B' and name=='quality_score':continue
                x=m[mkey];y=c['overall'][ckey]
                summary['economic_rows'].append({'group':name,'split':split,'N':x['events'],'positives':x['retained_events'],'auc':x['roc_auc'],'p_diagnostic':x['ranking_permutation'].get('p_greater_or_equal'),'topN':x['top10']['events'],'gross_bp':x['top10']['mean_gross_bp'],'net_bp':x['top10']['mean_net_bp'],'tp_rate':x['top10']['tp_rate'],'net_win_rate':x['top10']['net_profitable_rate'],'pairedN':y['paired_events'],'paired_candidate_net_bp':y['candidate_on_paired_events']['net_bp'],'paired_control_net_bp':y['per_event_mean_control']['net_bp'],'paired_delta_bp':y['candidate_minus_control']['net_bp']})
            summary['detection_rows'].append({'arm':arm,'split':split,**m['model']['detection'],'triggered_net_bp':m['model']['triggered_candidate_direction']['mean_net_bp']})
    prior=record(OLD/'delivery_owner1500_v4/summary.json')
    summary['previous_economic_rows']=prior['economic_rows'];summary['previous_detection_rows']=prior['detection_rows']
    model_rows=[r for r in summary['economic_rows'] if r['group'] in ('A','B')]
    summary['research_decision']='favorable_result_requires_independent_audit' if any(r['net_bp']>0 and r['p_diagnostic']<.01 and r['paired_delta_bp']>0 for r in model_rows) else 'not_accepted'
    for name in ('local_audit.json','transfer_receipt.json','remote_launch.json','visual_review/review.json','visual_review/selection.json','environment_probe.json','job_status.json','selection/receipt.json','selection/exclusions.jsonl','selection/negatives.jsonl','plan.json'):
        inputs.add(EXP/name)
    out.mkdir()
    write_json(out/'summary.json',summary)
    records=[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha256(p),'size_bytes':p.stat().st_size} for p in sorted(inputs)]
    write_json(out/'manifest.json',{'status':'completed','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'builder_sha256':sha256(module),'files':records,'summary_sha256':sha256(out/'summary.json'),'research_decision':summary['research_decision'],'production_eligible':False})
    print({'status':'completed','verified_files':len(records),'decision':summary['research_decision']})


if __name__=='__main__':main()
