"""Launch the unchanged GPU job through the repository's proven WMI method.

The first detached dispatcher returned PID6448, but that process disappeared
before creating a job receipt or writing its log. This recovery preserves the
frozen training/evaluation code, inputs, recipes, and failed dispatch evidence.
It refuses any existing training/job state or a duplicate supervised launch.
"""
from pathlib import Path
import base64
import json
import subprocess

from scripts.research.watch_ma_profit3r_owner1500_v4 import remote
from yoyo.datasets.ma_profit_negative_redo import ROOT, read_json, sha256, write_json


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
    run='C:/fable/runs/ma_profit3r_owner1500_neg_v5'
    batch=('\r\n'.join([
        '@echo off','setlocal','set "PYTHONPATH=C:\\fable"','cd /d C:\\fable',
        f'>> "{run}/job.log" echo [launcher] started %DATE% %TIME%',
        f'C:\\fable\\.venv\\Scripts\\python.exe -u -m scripts.windows.run_ma_profit_negative_redo --experiment "{remote_exp}" --run-root "{run}" >> "{run}/job.log" 2>&1',
        'set FABLE_TRAIN_RC=%ERRORLEVEL%',
        f'>> "{run}/job.log" echo [launcher] exit_code=%FABLE_TRAIN_RC% %DATE% %TIME%',
        f'> "{run}/wmi_exit_code.txt" echo %FABLE_TRAIN_RC%',
        'exit /b %FABLE_TRAIN_RC%',''])
    ).encode('ascii')
    batch_file=exp/'launch_wmi.cmd'
    with batch_file.open('xb') as f:f.write(batch)
    batch_sha=sha256(batch_file)
    wmi_command='cmd.exe /d /c "'+(remote_exp+'/launch_wmi.cmd').replace('/','\\')+'"'
    ps="$ErrorActionPreference='Stop'; $r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine="+"'"+wmi_command+"';CurrentDirectory='C:\\fable'}; if ($r.ReturnValue -ne 0) { throw ('WMI failed: '+$r.ReturnValue) }; @{pid=$r.ProcessId;return_value=$r.ReturnValue} | ConvertTo-Json -Compress"
    encoded_ps=base64.b64encode(ps.encode('utf-16le')).decode()
    code=f'''import time,base64
os.chdir('C:/fable')
exp=Path({remote_exp!r});run=Path('C:/fable/runs/ma_profit3r_owner1500_neg_v5')
if sha(exp/'launch_contract.json')!={contract_sha!r}:raise RuntimeError('launch contract drift')
for name in ('job_receipt.json','training_receipt.json','arm_A','arm_B','wmi_launch.json'):
 if (run/name).exists():raise RuntimeError('existing job state: '+name)
prior=json.loads((run/'launch_lock.json').read_text())
if prior['pid']!=6448:raise RuntimeError('unexpected prior dispatch')
live=subprocess.run(['powershell','-NoProfile','-Command','Get-Process -Id 6448 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id'],capture_output=True,text=True)
if live.stdout.strip():raise RuntimeError('prior PID exists; inspect before recovery')
if (run/'job.log').stat().st_size:raise RuntimeError('prior job produced log; inspect before recovery')
pre=json.loads((run/'preflight.json').read_text())
if pre['status']!='preflight_passed' or pre['launch_contract_sha256']!={contract_sha!r}:raise RuntimeError('preflight not bound')
with (exp/'launch_wmi.cmd').open('xb') as f:f.write(base64.b64decode({base64.b64encode(batch).decode()!r}))
if sha(exp/'launch_wmi.cmd')!={batch_sha!r}:raise RuntimeError('batch SHA mismatch')
record={{'mode':'WMI_verified_cmd','status':'launching','prior_dispatch_pid':6448,'prior_dispatch_observation':'PID absent, empty log, no job/training receipt or arm directories','launch_contract_sha256':{contract_sha!r},'recovery_launcher_sha256':{sha256(here)!r},'batch_sha256':{batch_sha!r},'command':{wmi_command!r},'started_unix':time.time()}}
with (run/'wmi_launch.json').open('x',encoding='utf-8') as f:json.dump(record,f)
result=json.loads(subprocess.check_output(['powershell','-NoProfile','-EncodedCommand',{encoded_ps!r}],text=True))
record.update(status='dispatched',**result)
(run/'wmi_launch.json').write_text(json.dumps(record),encoding='utf-8')
emit(record)
'''
    result=remote(code,timeout=120)
    write_json(exp/'recovery_launch.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
