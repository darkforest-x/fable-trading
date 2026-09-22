"""Stage, start, inspect and collect the fixed negative redo on the existing GPU.

All uploads are explicit repository paths. Existing remote files may be reused
only when their SHA matches; nothing from another job is overwritten. Training
continues detached on Windows even if the Mac connection closes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import tarfile
import time

from scripts.research.watch_ma_profit3r_owner1500_v4 import remote, copy_file, HOST
from yoyo.datasets.ma_profit_negative_redo import ROOT, sha256, read_json, write_json

EXP=ROOT/'experiments/active/exp-ma-profit3r-negatives-20260922-v2'
OLD=ROOT/'experiments/active/exp-ma-profit3r-20260922-v1'
REMOTE_EXP='C:/fable/'+EXP.relative_to(ROOT).as_posix()
RUN='C:/fable/runs/ma_profit3r_owner1500_neg_v5'
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
        raise RuntimeError('evaluation binding failed')


def _expected_control_inputs(plan: dict, evaluation: Path, receipt: dict) -> dict[str, str]:
    """Return the exact input set emitted by the frozen controls runner."""
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
    splits = receipt.get('splits')
    if not isinstance(artifacts, dict) or splits != ['val', 'test']:
        raise RuntimeError('evaluation receipt split/artifact contract failed')
    for split in splits:
        name = f'events_{split}.jsonl'
        digest = artifacts.get(name)
        event_path = evaluation / name
        if not isinstance(digest, str) or sha256(event_path) != digest:
            raise RuntimeError('evaluation event artifact drift: ' + name)
        paths.append(event_path)
    return {str(path.resolve()): sha256(path) for path in paths}


def _validate_control_receipt(receipt: dict, *, arm: str, expected_inputs: dict[str, str]) -> None:
    if receipt.get('status') != 'completed' or receipt.get('arm') != arm:
        raise RuntimeError('control receipt failed')
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


def state(status: str, **kw):
    temp=EXP/'job_status.tmp'
    write_json(temp,{'status':status,'updated_unix':time.time(),**kw})
    temp.replace(EXP/'job_status.json')
    print(json.dumps({'status':status,**kw}),flush=True)


def stage() -> None:
    launch=read_json(EXP/'launch_contract.json');plan=read_json(EXP/'plan.json')
    dataset=ROOT/'datasets'/plan['dataset_name']
    files={ROOT/x['path'] for x in launch['files']}
    files.update(ROOT/x['path'] for x in plan['inputs'].values())
    # Local review links are browsing aids, never GPU training inputs.
    files.update(p for p in dataset.rglob('*')
                 if p.is_file() and not p.is_symlink()
                 and 'review' not in p.relative_to(dataset).parts)
    files.update(p for p in (EXP/'selection').iterdir() if p.is_file())
    files.add(EXP/'launch_contract.json')
    manifest={p.relative_to(ROOT).as_posix():sha256(p) for p in files}
    write_json(EXP/'upload_manifest.json',manifest);files.add(EXP/'upload_manifest.json')
    bundle=EXP/'upload.tar'
    with tarfile.open(bundle,'w') as tar:
        for path in sorted(files):tar.add(path,arcname=path.relative_to(ROOT).as_posix(),recursive=False)
    state('uploading',files=len(files),bytes=bundle.stat().st_size)
    remote("Path('C:/fable/experiments/active/exp-ma-profit3r-negatives-20260922-v2').mkdir(parents=True,exist_ok=True);emit({'ok':True})")
    subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=10',str(bundle),HOST+':'+REMOTE_EXP+'/upload.tar'],check=True)
    expected=sha256(bundle)
    result=remote(f'''import tarfile
root=Path('C:/fable');bundle=Path({REMOTE_EXP!r})/'upload.tar'
if sha(bundle)!={expected!r}:raise RuntimeError('upload bundle SHA mismatch')
with tarfile.open(bundle) as tar:
 members=tar.getmembers()
 for m in members:
  if not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts:raise RuntimeError('unsafe archive member')
 manifest=json.load(tar.extractfile({(EXP.relative_to(ROOT)/'upload_manifest.json').as_posix()!r}))
 for m in members:
  p=root/m.name
  if p.exists() and m.name in manifest and sha(p)!=manifest[m.name]:raise RuntimeError('remote file collision: '+m.name)
 for m in members:
  p=root/m.name
  if not p.exists():
   p.parent.mkdir(parents=True,exist_ok=True)
   with p.open('wb') as f:f.write(tar.extractfile(m).read())
 for name,h in manifest.items():
  if sha(root/name)!=h:raise RuntimeError('extracted SHA mismatch: '+name)
emit({{'status':'verified','files':len(manifest),'bundle_sha256':sha(bundle)}})
''',timeout=1800)
    write_json(EXP/'transfer_receipt.json',result);state('staged',transfer=result)


def start() -> None:
    if (EXP/'remote_launch.json').exists():raise FileExistsError('existing remote launch')
    plan=read_json(EXP/'plan.json')
    cmd=['C:/fable/.venv/Scripts/python.exe','-u','-m','scripts.windows.train_ma_profit_negative_redo','--plan',REMOTE_EXP+'/plan.json','--dataset','C:/fable/datasets/'+plan['dataset_name'],'--selection',REMOTE_EXP+'/selection','--run-root',RUN,'--launch-contract',REMOTE_EXP+'/launch_contract.json']
    state('remote_preflight')
    remote(f"os.chdir('C:/fable');subprocess.run({cmd!r},check=True)",timeout=1800,capture=False)
    code=f'''os.chdir('C:/fable')
r=Path({RUN!r});lock=r/'launch_lock.json'
with lock.open('x') as f:json.dump({{'requested':True}},f)
log=(r/'job.log').open('ab',buffering=0)
cmd=['C:/fable/.venv/Scripts/python.exe','-u','-m','scripts.windows.run_ma_profit_negative_redo','--experiment',{REMOTE_EXP!r},'--run-root',{RUN!r}]
p=subprocess.Popen(cmd,cwd='C:/fable',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
result={{'pid':p.pid,'run_root':str(r),'command':cmd}}
lock.write_text(json.dumps(result),encoding='utf-8');emit(result)
'''
    result=remote(code);write_json(EXP/'remote_launch.json',result);state('started',**result)


def snapshot() -> dict:
    return remote(f'''r=Path({RUN!r});out={{}}
for name in ('job_receipt.json','training_receipt.json'):
 p=r/name
 if p.exists():out[name]=json.loads(p.read_text(encoding='utf-8'))
progress={{}}
for arm in ('A','B'):
 p=r/('arm_'+arm)/'results.csv';rs=list(csv.DictReader(p.open(encoding='utf-8'))) if p.exists() else []
 progress[arm]={{'epoch':int(float(rs[-1]['epoch'])) if rs else 0,'mtime':p.stat().st_mtime if p.exists() else None}}
out['progress']=progress;emit(out)
''',timeout=120)


def collect() -> None:
    snap=snapshot()
    if snap.get('job_receipt.json',{}).get('status')!='completed':raise RuntimeError('remote job not completed')
    outputs={'trained/'+n:RUN+'/'+n for n in ('training_receipt.json','preflight.json','job_receipt.json')}
    for a in ('A','B'):
        for name in ('weights/best.pt','weights/last.pt','args.yaml','results.csv'):
            outputs[f'trained/arm_{a}/{name}']=f'{RUN}/arm_{a}/{name}'
        for name in ('receipt.json','predictions_val.jsonl','predictions_test.jsonl','events_val.jsonl','events_test.jsonl','metrics_val.json','metrics_test.json'):
            outputs[f'evaluation/arm_{a}/{name}']=f'{REMOTE_EXP}/evaluation/arm_{a}/{name}'
    records=remote(f"files={outputs!r};emit({{k:{{'path':v,'sha256':sha(v),'bytes':Path(v).stat().st_size}} for k,v in files.items()}})")
    for name,r in records.items():copy_file(r['path'],EXP/name,r['sha256'])
    write_json(EXP/'download_inventory.json',records)
    launch=read_json(EXP/'launch_contract.json')
    for item in launch['files']:
        if sha256(ROOT/item['path'])!=item['sha256']:
            raise RuntimeError('local frozen launch input/code drift: '+item['path'])
    plan=read_json(EXP/'plan.json');manifest_sha=sha256(ROOT/'datasets'/plan['dataset_name']/'manifest.jsonl')
    evaluator_sha=_launch_file_sha(launch,EVALUATOR_PATH)
    metrics_sha=_launch_file_sha(launch,METRICS_PATH)
    for a in ('A','B'):
        evaluation=EXP/'evaluation'/f'arm_{a}';receipt=read_json(evaluation/'receipt.json')
        expected={'status':'completed','arm':a,'model_sha256':sha256(EXP/'trained'/f'arm_{a}'/'weights/best.pt'),'manifest_sha256':manifest_sha,'ledger_sha256':plan['inputs']['old_ledger']['sha256'],'runner_sha256':evaluator_sha,'metrics_code_sha256':metrics_sha}
        _validate_evaluation_receipt(receipt,expected)
        for name,h in receipt['artifacts'].items():
            if Path(name).name!=name or sha256(evaluation/name)!=h:raise RuntimeError('evaluation artifact SHA failed')
        expected_inputs=_expected_control_inputs(plan,evaluation,receipt)
        out=EXP/'controls'/f'arm_{a}'
        if not out.exists():
            subprocess.run([str(ROOT/'.venv/bin/python'),'-m','yoyo.evaluation.ma_profit_control_metrics','--evaluation',str(evaluation),'--ledger',str(ROOT/plan['inputs']['old_ledger']['path']),'--controls',str(OLD/'matched_controls_owner1500_v2'),'--outcomes',str(OLD/'matched_control_outcomes_owner1500_v2'),'--out',str(out)],cwd=ROOT,check=True)
        c=read_json(out/'receipt.json')
        _validate_control_receipt(c,arm=a,expected_inputs=expected_inputs)
        for name,h in c['artifacts'].items():
            if sha256(out/name)!=h:raise RuntimeError('control artifact SHA failed')
    state('completed',training='trained',evaluation='evaluation',controls='controls')


def watch() -> None:
    prior=None;last_change=time.monotonic()
    while True:
        s=snapshot();job=s.get('job_receipt.json',{});train=s.get('training_receipt.json',{})
        if job.get('status')=='failed' or train.get('status')=='failed':raise RuntimeError(json.dumps(s))
        if job.get('status')=='completed':collect();return
        marker=json.dumps([s['progress'],job.get('stages',{})],sort_keys=True)
        if marker!=prior:prior=marker;last_change=time.monotonic();state('running',progress=s['progress'],stages=job.get('stages',{}))
        elif time.monotonic()-last_change>2700:raise RuntimeError('no epoch/stage change for45min; investigate without killing remote job')
        time.sleep(60)


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('stage','start','snapshot','collect','watch'));args=p.parse_args()
    try:
        if args.mode=='snapshot':print(json.dumps(snapshot()),flush=True)
        else:globals()[args.mode]()
    except Exception as exc:state('failed',phase=args.mode,error=repr(exc));raise


if __name__=='__main__':main()
