"""Freeze completed negative-redo evidence and comparable previous-run metrics."""
from __future__ import annotations
import csv
from pathlib import Path
import subprocess

from yoyo.datasets.ma_profit_negative_redo import ROOT, read_json, rows, sha256, write_json

EXP=ROOT/'experiments/active/exp-ma-profit3r-negatives-20260922-v2'
OLD=ROOT/'experiments/active/exp-ma-profit3r-20260922-v1'
EVALUATOR_PATH = 'scripts/windows/evaluate_ma_profit3r_20260922.py'
METRICS_PATH = 'yoyo/evaluation/ma_profit_model_metrics.py'
CONTROL_METRICS_PATH = 'yoyo/evaluation/ma_profit_control_metrics.py'
CONTROL_ARTIFACTS = {'matched_metrics_val.json', 'matched_metrics_test.json'}


def _launch_file_sha(launch: dict, path: str) -> str:
    matches = [item.get('sha256') for item in launch.get('files', [])
               if isinstance(item, dict) and item.get('path') == path]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise RuntimeError('launch contract missing frozen file: ' + path)
    return matches[0]


def _validate_evaluation_receipt(receipt: dict, expected: dict) -> None:
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise RuntimeError('eval contract drift')


def _expected_control_inputs(plan: dict, evaluation: Path, receipt: dict) -> dict[str, str]:
    """Match the exact local input schema written by ma_profit_control_metrics."""
    ledger = ROOT / plan['inputs']['old_ledger']['path']
    if sha256(ledger) != plan['inputs']['old_ledger']['sha256']:
        raise RuntimeError('old evaluation ledger drift')
    paths = [
        ROOT / CONTROL_METRICS_PATH,
        ledger,
        evaluation / 'receipt.json',
        OLD / 'matched_controls_owner1500_v2/receipt.json',
        OLD / 'matched_controls_owner1500_v2/frozen_events.jsonl',
        OLD / 'matched_controls_owner1500_v2/frozen_sources.json',
        OLD / 'matched_control_outcomes_owner1500_v2/summary.json',
        OLD / 'matched_control_outcomes_owner1500_v2/outcomes.jsonl',
    ]
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, dict) or receipt.get('splits') != ['val', 'test']:
        raise RuntimeError('evaluation receipt split/artifact contract failed')
    for split in ('val', 'test'):
        name = f'events_{split}.jsonl'
        event_path = evaluation / name
        if not isinstance(artifacts.get(name), str) or sha256(event_path) != artifacts[name]:
            raise RuntimeError('evaluation event artifact drift: ' + name)
        paths.append(event_path)
    return {str(path.resolve()): sha256(path) for path in paths}


def _validate_control_receipt(receipt: dict, *, arm: str, expected_inputs: dict[str, str]) -> None:
    if receipt.get('status') != 'completed' or receipt.get('arm') != arm:
        raise RuntimeError('controls incomplete')
    rows = receipt.get('inputs')
    if not isinstance(rows, list):
        raise RuntimeError('control receipt inputs missing')
    actual: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str) or not isinstance(row.get('sha256'), str):
            raise RuntimeError('malformed control receipt input')
        key = str(Path(row['path']).resolve())
        if key in actual:
            raise RuntimeError('duplicate control receipt input')
        actual[key] = row['sha256']
    if actual != expected_inputs:
        raise RuntimeError('control receipt input binding failed')
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != CONTROL_ARTIFACTS:
        raise RuntimeError('control receipt artifact contract failed')


def main() -> None:
    module=Path(__file__).resolve()
    if subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()!='main':raise RuntimeError('main required')
    if subprocess.check_output(['git','status','--porcelain','--',str(module.relative_to(ROOT))],cwd=ROOT,text=True).strip():raise RuntimeError('commit delivery builder first')
    out=EXP/'delivery'
    if out.exists():raise FileExistsError(out)
    inputs=set()
    def record(path):inputs.add(path);return read_json(path)
    plan=record(EXP/'plan.json');launch=record(EXP/'launch_contract.json');inventory=record(EXP/'download_inventory.json')
    for item in launch['files']:
        if sha256(ROOT/item['path'])!=item['sha256']:
            raise RuntimeError('local frozen launch input/code drift: '+item['path'])
    evaluator_sha=_launch_file_sha(launch,EVALUATOR_PATH)
    metrics_sha=_launch_file_sha(launch,METRICS_PATH)
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
        expect={'status':'completed','arm':arm,'model_sha256':sha256(weight),'manifest_sha256':training['audit']['manifest_sha256'],'ledger_sha256':sha256(ledger),'splits':['val','test'],'confidence':.001,'nms_iou':.70,'imgsz':1280,'device':0,'augment':False,'max_det':300,'agnostic_nms':False,'runner_sha256':evaluator_sha,'metrics_code_sha256':metrics_sha}
        _validate_evaluation_receipt(ev,expect)
        for name,h in ev['artifacts'].items():
            p=evdir/name;inputs.add(p)
            if Path(name).name!=name or sha256(p)!=h:raise RuntimeError('eval bytes drift')
        expected_control_inputs=_expected_control_inputs(plan,evdir,ev)
        cdir=EXP/f'controls/arm_{arm}';cr=record(cdir/'receipt.json')
        _validate_control_receipt(cr,arm=arm,expected_inputs=expected_control_inputs)
        inputs.update(Path(path) for path in expected_control_inputs)
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
