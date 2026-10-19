"""Operate only the independent discipline service, preserving its paper ledger.

An explicit installation flag enables live user-confirmed execution. Credentials
remain in the existing private runtime. No order is submitted by this helper.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import time

ROOT=Path(__file__).resolve().parents[3]
RUNTIME=Path.home()/'Library/Application Support/SpikeDiscipline'
PYTHON=Path.home()/'Library/Caches/SpikeDiscipline/service-venv/bin/python'
LABEL='com.spike.discipline.live'
PLIST=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
PAPER='com.spike.discipline.paper'

def loaded(target):
    return subprocess.run(['launchctl','print',target],capture_output=True).returncode==0

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['install','start','stop','status'])
    parser.add_argument('--enable-execution',action='store_true')
    args=parser.parse_args()
    domain=f'gui/{os.getuid()}'
    target=f'{domain}/{LABEL}'
    if args.command=='status':
        result=subprocess.run(['launchctl','print',target],capture_output=True,text=True)
        print(LABEL)
        for line in result.stdout.splitlines():
            if line.strip().startswith(('state = ','pid = ','last exit code = ')):
                print(line.strip())
        return result.returncode
    if args.command=='stop':
        return subprocess.call(['launchctl','bootout',target])
    if args.command=='install':
        os.umask(0o077)
        if not PYTHON.is_file() or not (RUNTIME/'okx-live-credentials.json').is_file() or not (RUNTIME/'access-token').is_file():
            parser.error('Missing independent service runtime or credentials')
        arguments=[str(PYTHON),'-m','yoyo.discipline_app','serve','--mode','okx_live']
        if args.enable_execution:arguments.append('--enable-live-execution')
        document=dict(Label=LABEL,ProgramArguments=arguments,WorkingDirectory=str(ROOT),
            RunAtLoad=True,KeepAlive=True,ThrottleInterval=15,ProcessType='Background',
            StandardOutPath=str(RUNTIME/'live.stdout.log'),StandardErrorPath=str(RUNTIME/'live.stderr.log'),
            EnvironmentVariables={'PYTHONUNBUFFERED':'1'})
        if PLIST.exists() and plistlib.loads(PLIST.read_bytes())!=document:
            parser.error('Existing live service configuration differs; inspect before replacing')
        PLIST.write_bytes(plistlib.dumps(document));PLIST.chmod(0o600)
    if not PLIST.exists():parser.error('Run install first')
    if loaded(target):
        print('Independent live service already loaded')
        return 0
    paper_target=f'{domain}/{PAPER}'
    paper_loaded=loaded(paper_target)
    if paper_loaded:
        db=sqlite3.connect(f'file:{RUNTIME / "paper.sqlite3"}?mode=ro',uri=True)
        try:state=json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        finally:db.close()
        if state['positions'] or state['pending']:
            parser.error('Paper positions or orders still exist; refusing to interrupt their management')
        subprocess.run(['launchctl','bootout',paper_target],check=True)
        for _ in range(20):
            if not loaded(paper_target):break
            time.sleep(.1)
        if loaded(paper_target):parser.error('Paper process has not stopped')
    try:
        subprocess.run(['launchctl','bootstrap',domain,str(PLIST)],check=True)
    except subprocess.CalledProcessError:
        if paper_loaded:
            subprocess.run(['launchctl','bootstrap',domain,str(PLIST.parent/f'{PAPER}.plist')],check=True)
        raise
    print('Independent live service started; no order submitted; paper.sqlite3 preserved')
    return 0

if __name__=='__main__':raise SystemExit(main())
