"""Launch the unchanged GPU job under a persistent SSH supervisor.

The first detached dispatcher returned PID6448, but that process disappeared
before creating a job receipt or writing its log. This recovery preserves the
frozen training/evaluation code, inputs, recipes, and failed dispatch evidence.
It refuses any existing training/job state or a duplicate supervised launch.
"""
from pathlib import Path
import subprocess

from scripts.research.watch_ma_profit3r_owner1500_v4 import remote
from yoyo.datasets.ma_profit_negative_redo import ROOT, read_json, sha256


def main():
    here=Path(__file__).resolve();exp=here.parent
    if subprocess.check_output(['git','status','--porcelain','--',str(here.relative_to(ROOT))],cwd=ROOT,text=True).strip():
        raise RuntimeError('commit recovery launcher before running')
    if subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()!='main':
        raise RuntimeError('main required')
    launch=read_json(exp/'launch_contract.json')
    for item in launch['files']:
        if sha256(ROOT/item['path'])!=item['sha256']:raise RuntimeError('frozen launch input drift')
    remote_exp='C:/fable/'+exp.relative_to(ROOT).as_posix()
    contract_sha=sha256(exp/'launch_contract.json')
    code=f'''import sys,time
os.chdir('C:/fable')
exp=Path({remote_exp!r});run=Path('C:/fable/runs/ma_profit3r_owner1500_neg_v5')
if sha(exp/'launch_contract.json')!={contract_sha!r}:raise RuntimeError('launch contract drift')
for name in ('job_receipt.json','training_receipt.json','arm_A','arm_B','supervised_launch.json'):
 if (run/name).exists():raise RuntimeError('existing job state: '+name)
prior=json.loads((run/'launch_lock.json').read_text())
if prior['pid']!=6448:raise RuntimeError('unexpected prior dispatch')
live=subprocess.run(['powershell','-NoProfile','-Command','Get-Process -Id 6448 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id'],capture_output=True,text=True)
if live.stdout.strip():raise RuntimeError('prior PID exists; inspect before recovery')
if (run/'job.log').stat().st_size:raise RuntimeError('prior job produced log; inspect before recovery')
pre=json.loads((run/'preflight.json').read_text())
if pre['status']!='preflight_passed' or pre['launch_contract_sha256']!={contract_sha!r}:raise RuntimeError('preflight not bound')
cmd=['C:/fable/.venv/Scripts/python.exe','-u','-m','scripts.windows.run_ma_profit_negative_redo','--experiment',str(exp),'--run-root',str(run)]
record={{'mode':'persistent_ssh_supervision','status':'launching','prior_dispatch_pid':6448,'prior_dispatch_observation':'PID absent, empty log, no job/training receipt or arm directories','launch_contract_sha256':{contract_sha!r},'recovery_launcher_sha256':{sha256(here)!r},'command':cmd,'started_unix':time.time()}}
with (run/'supervised_launch.json').open('x',encoding='utf-8') as f:json.dump(record,f)
log=(run/'job.log').open('ab',buffering=0)
p=subprocess.Popen(cmd,cwd='C:/fable',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
record.update(status='supervising',pid=p.pid)
(run/'supervised_launch.json').write_text(json.dumps(record),encoding='utf-8')
print(json.dumps(record),flush=True)
code=p.wait()
record.update(status='completed' if code==0 else 'failed',exit_code=code,ended_unix=time.time())
(run/'supervised_launch.json').write_text(json.dumps(record),encoding='utf-8')
print(json.dumps(record),flush=True)
sys.exit(code)
'''
    remote(code,timeout=172800,capture=False)


if __name__=='__main__':main()
