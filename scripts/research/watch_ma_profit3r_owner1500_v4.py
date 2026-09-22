"""Join the already-running, frozen v4 A/B training to unchanged evaluators.

This operational script never trains, changes recipes, resamples controls, or
publishes models. Completed artifacts can be resumed only with exact bindings.
Training progress is measured from actual CSV updates, not the arm-end receipt.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-ma-profit3r-20260922-v1'
HOST = 'Administrator@192.168.1.2'
REMOTE_ROOT = 'C:/fable'
RUN = REMOTE_ROOT + '/runs/ma_profit3r_owner1500_20260922_v4'
REMOTE_EXP = REMOTE_ROOT + '/experiments/active/exp-ma-profit3r-20260922-v1'
DATASET = REMOTE_ROOT + '/datasets/ma_profit3r_owner1500_v4'
LEDGER_PATH = REMOTE_EXP + '/selection_queue_round_017/dataset_ledger.jsonl'
MANIFEST = 'f0207e44d955fda18e27e0a258f077065db8f995f7fc5f1c0d223201d9fecd4d'
LEDGER = 'e70c09343565e2c7376b6b9e897466965a8a07d1a2134f2520769c23f0b9d797'
RUNNER = '92c8c4bf39b7fde05c352f946bfe552a5de62b93a45a2dfdf98504661fbfa619'
METRICS = '5968d33e61d65e6dcac15c0967d695d47c1a2e786ec9d09af9673f967a570066'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
       '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=60', HOST]
EVAL_NAMES = {f'{kind}_{split}.{extension}' for split in ('val', 'test')
              for kind, extension in (('predictions', 'jsonl'), ('events', 'jsonl'), ('metrics', 'json'))}
REMOTE_PREAMBLE = '''import csv,hashlib,json,os,subprocess,ctypes
from pathlib import Path
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def emit(x): print(json.dumps(x))
'''


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def remote(code: str, *, timeout: int = 120, capture: bool = True):
    encoded = base64.b64encode((REMOTE_PREAMBLE + code).encode()).decode()
    command = f'C:/fable/.venv/Scripts/python.exe -c "import base64;exec(base64.b64decode(\'{encoded}\'))"'
    result = subprocess.run(SSH + [command], check=True, timeout=timeout,
                            capture_output=capture, text=True)
    return json.loads(result.stdout) if capture else None


def snapshot() -> dict:
    return remote(f'''r=Path({RUN!r})
x=json.loads((r/'training_receipt.json').read_text())
p={{}}
for arm in ('A','B'):
 f=r/('arm_'+arm)/'results.csv'
 rows=list(csv.DictReader(f.open())) if f.exists() else []
 p[arm]={{'epochs':[int(float(z['epoch'])) for z in rows], 'mtime_ns':f.stat().st_mtime_ns if f.exists() else None}}
emit({{'receipt':x,'progress':p}})
''')


def ready(state: dict) -> bool:
    receipt = state['receipt']
    if receipt.get('status') == 'failed':
        raise RuntimeError('remote training failed; inspect original receipt')
    if receipt.get('audit', {}).get('manifest_sha256') != MANIFEST or not receipt.get('train_requested'):
        raise RuntimeError('training manifest/request binding drift')
    if receipt.get('status') != 'completed':
        return False
    for arm in ('A', 'B'):
        item = receipt.get('arms', {}).get(arm, {})
        if item.get('status') != 'completed' or state['progress'][arm]['epochs'] != list(range(1, 41)):
            raise RuntimeError(f'{arm}: require completed receipt and exactly epochs 1..40')
        for key in ('best', 'last', 'results_csv'):
            path = item.get(key, '').replace('\\', '/')
            if not path.startswith(RUN + '/arm_' + arm + '/') or '..' in path.split('/'):
                raise RuntimeError(f'{arm}: invalid actual {key} path')
    return True


def state_write(status: str, **details) -> None:
    output = EXP / 'post_training_owner1500_v4_status.json'
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps({'status': status, 'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                'pid': os.getpid(), **details}, indent=2) + '\n')
    temp.replace(output)
    print(json.dumps({'status': status, **details}), flush=True)


def copy_file(source: str, destination: Path, expected: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha(destination) != expected:
            raise RuntimeError(f'existing local artifact differs: {destination}')
        return
    temp = destination.with_name(destination.name + '.downloading')
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    f'{HOST}:{source}', str(temp)], check=True, timeout=600)
    if sha(temp) != expected:
        raise RuntimeError(f'download SHA mismatch: {source}')
    temp.replace(destination)


def training_files(receipt: dict) -> dict:
    files = {'training_receipt.json': RUN + '/training_receipt.json'}
    for arm in ('A', 'B'):
        item = receipt['arms'][arm]
        for key, suffix in (('best', 'weights/best.pt'), ('last', 'weights/last.pt'), ('results_csv', 'results.csv')):
            files[f'arm_{arm}/{suffix}'] = item[key].replace('\\', '/')
        files[f'arm_{arm}/args.yaml'] = RUN + f'/arm_{arm}/args.yaml'
    checks = {DATASET + '/manifest.jsonl': MANIFEST, LEDGER_PATH: LEDGER,
              REMOTE_ROOT + '/scripts/windows/evaluate_ma_profit3r_20260922.py': RUNNER,
              REMOTE_ROOT + '/yoyo/evaluation/ma_profit_model_metrics.py': METRICS}
    records = remote(f'''checks={checks!r}
for p,h in checks.items():
 if sha(p)!=h: raise RuntimeError('frozen input/code SHA drift: '+p)
files={files!r}
emit({{name:{{'path':p,'sha256':sha(p),'size_bytes':Path(p).stat().st_size}} for name,p in files.items()}})
''')
    for name, row in records.items():
        copy_file(row['path'], EXP / 'trained_owner1500_v4' / name, row['sha256'])
    (EXP / 'trained_owner1500_v4' / 'download_inventory.json').write_text(json.dumps(records, indent=2) + '\n')
    return records


def validate_evaluation(meta: dict, arm: str, model_sha: str) -> None:
    expected = {'status': 'completed', 'arm': arm, 'model_sha256': model_sha,
                'manifest_sha256': MANIFEST, 'ledger_sha256': LEDGER,
                'runner_sha256': RUNNER, 'metrics_code_sha256': METRICS,
                'splits': ['val', 'test'], 'confidence': .001, 'nms_iou': .70,
                'imgsz': 1280, 'device': 0, 'augment': False, 'agnostic_nms': False,
                'max_det': 300, 'read_only_evaluation': True}
    if any(meta.get(k) != v for k, v in expected.items()) or set(meta.get('artifacts', {})) != EVAL_NAMES:
        raise RuntimeError(f'{arm}: incomplete or incorrectly bound evaluation')


def evaluation_probe(out: str) -> dict:
    return remote(f'''p=Path({out!r})
if not p.exists(): emit({{'exists':False}})
else:
 m=json.loads((p/'receipt.json').read_text())
 files={{'receipt.json':sha(p/'receipt.json')}}
 for n,h in m.get('artifacts',{{}}).items():
  if Path(n).name!=n or sha(p/n)!=h: raise RuntimeError('evaluation artifact drift: '+n)
  files[n]=h
 emit({{'exists':True,'meta':m,'files':files}})
''')


def evaluate(arm: str, receipt: dict, model_sha: str) -> Path:
    out = REMOTE_EXP + f'/evaluation_owner1500_v4/arm_{arm}'
    probe = evaluation_probe(out)
    if not probe['exists']:
        command = ['C:/fable/.venv/Scripts/python.exe', '-u', '-m', 'scripts.windows.evaluate_ma_profit3r_20260922',
                   '--dataset', DATASET, '--ledger', LEDGER_PATH, '--model', receipt['arms'][arm]['best'],
                   '--arm', arm, '--split', 'both', '--out', out]
        remote(f'''os.chdir({REMOTE_ROOT!r})
ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
try: subprocess.run({command!r},check=True)
finally: ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
''', timeout=7200, capture=False)
        probe = evaluation_probe(out)
    validate_evaluation(probe['meta'], arm, model_sha)
    local = EXP / 'evaluation_owner1500_v4' / f'arm_{arm}'
    for name, digest in probe['files'].items():
        copy_file(out + '/' + name, local / name, digest)
    return local


def controls(arm: str, evaluation: Path) -> None:
    out = EXP / 'control_comparison_owner1500_v4' / f'arm_{arm}'
    required = [ROOT / 'yoyo/evaluation/ma_profit_control_metrics.py', EXP / 'selection_queue_round_017/dataset_ledger.jsonl',
                evaluation / 'receipt.json', evaluation / 'events_val.jsonl', evaluation / 'events_test.jsonl']
    required += [EXP / 'matched_controls_owner1500_v2' / name for name in ('receipt.json', 'frozen_events.jsonl', 'frozen_sources.json')]
    required += [EXP / 'matched_control_outcomes_owner1500_v2' / name for name in ('summary.json', 'outcomes.jsonl')]
    if not out.exists():
        subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'yoyo.evaluation.ma_profit_control_metrics', '--evaluation', str(evaluation),
                        '--ledger', str(EXP / 'selection_queue_round_017/dataset_ledger.jsonl'),
                        '--controls', str(EXP / 'matched_controls_owner1500_v2'), '--outcomes', str(EXP / 'matched_control_outcomes_owner1500_v2'),
                        '--out', str(out)], check=True, cwd=ROOT)
    meta = json.loads((out / 'receipt.json').read_text())
    expected = {str(p.resolve()): sha(p) for p in required}
    actual = {x['path']: x['sha256'] for x in meta.get('inputs', [])}
    if meta.get('status') != 'completed' or meta.get('arm') != arm or actual != expected:
        raise RuntimeError(f'{arm}: control comparison binding drift')
    if set(meta.get('artifacts', {})) != {'matched_metrics_val.json', 'matched_metrics_test.json'}:
        raise RuntimeError(f'{arm}: control comparison artifact set drift')
    for name, digest in meta['artifacts'].items():
        if sha(out / name) != digest:
            raise RuntimeError(f'{arm}: control comparison SHA drift')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='print one read-only training snapshot')
    args = parser.parse_args()
    if args.check_only:
        s = snapshot()
        print(json.dumps({'ready': ready(s), 'progress': s['progress']}))
        return
    prior, changed_at = None, time.monotonic()
    try:
        while True:
            s = snapshot()
            if ready(s):
                break
            marker = json.dumps(s['progress'], sort_keys=True)
            if marker != prior:
                prior, changed_at = marker, time.monotonic()
                state_write('waiting_for_training', progress=s['progress'])
            elif time.monotonic() - changed_at >= 900:
                raise RuntimeError('actual training CSV unchanged for 15 minutes')
            time.sleep(30)
        state_write('syncing_completed_training')
        files = training_files(s['receipt'])
        for arm in ('A', 'B'):
            state_write('evaluating', arm=arm)
            evaluation = evaluate(arm, s['receipt'], files[f'arm_{arm}/weights/best.pt']['sha256'])
            state_write('comparing_controls', arm=arm)
            controls(arm, evaluation)
        state_write('completed', training='trained_owner1500_v4', evaluation='evaluation_owner1500_v4', controls='control_comparison_owner1500_v4')
    except Exception as exc:
        state_write('failed', error=f'{type(exc).__name__}: {exc}')
        raise


if __name__ == '__main__':
    main()
