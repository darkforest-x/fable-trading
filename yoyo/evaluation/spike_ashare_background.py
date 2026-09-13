"""Durable offline supervisor for the owner-requested A-share study.

It adopts only a verified collector PID, runs receipt-only continuations,
retries failed collection at most twice, and renders the final artifact.
It never changes live scanner, notifications, positions, or model policies.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from yoyo.evaluation.spike_ashare_data import save
from yoyo.evaluation.spike_ashare_study import EXP, ROOT


def collector_alive(pid):
    if not pid:return False
    found=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
    return found.returncode==0 and '-m yoyo.evaluation.spike_ashare_data ' in found.stdout


def evaluator_alive(pid):
    if not pid:return False
    found=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
    return found.returncode==0 and '-m yoyo.evaluation.spike_ashare_study' in found.stdout


def run(adopt=0, adopt_evaluator=0):
    out=EXP/'results';data=EXP/'data';status=out/'background_status.json'
    lock=(out/'background.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (out/'delivery_receipt.json').exists():
        save(status,dict(status='complete',delivery_receipt=str(out/'delivery_receipt.json')));return
    env=os.environ.copy();env['PYTHONPATH']='/tmp/spike-ashare-v18-bs093'
    def state(phase,**extra):
        save(status,dict(status='running',phase=phase,pid=os.getpid(),updated_at=time.time(),**extra))
    def command(module,*args):
        return [sys.executable,'-u','-m',module,*map(str,args)]
    with (out/'evaluation.log').open('a') as log:
        stop_path=out/'owner_stop.json'
        if stop_path.exists():
            # Finish an owner-frozen source set. Never enter collection/retry
            # paths, and let the current evaluator finish its active stream.
            stopped=json.loads(stop_path.read_text())
            adopted=adopt_evaluator or stopped['processes']['evaluator']
            try:
                while evaluator_alive(adopted):
                    state('owner_stopped_evaluating',evaluator_pid=adopted,owner_stopped=True)
                    time.sleep(20)
                actual=hashlib.sha256((data/'collection_records.json').read_bytes()).hexdigest()
                if actual!=stopped['source_records_sha256']:
                    raise ValueError('owner-frozen collection receipts changed')
                state('final_reconciliation',owner_stopped=True)
                subprocess.run(command('yoyo.evaluation.spike_ashare_study'),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
                subprocess.run(command('yoyo.evaluation.spike_ashare_delivery'),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
                summary=json.loads((out/'summary.json').read_text())
                save(status,dict(status='complete',result_status=summary['status'],owner_stopped=True,
                    updated_at=time.time(),covered_symbols=summary['covered_symbols'],
                    failed_symbols=summary['failed_symbols'],delivery_receipt=str(out/'delivery_receipt.json')))
            except BaseException as exc:
                save(status,dict(status='failed',owner_stopped=True,error=f'{type(exc).__name__}: {exc}',updated_at=time.time()))
                raise
            return
        evaluator=subprocess.Popen(command('yoyo.evaluation.spike_ashare_study','--follow'),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            if collector_alive(adopt):
                while collector_alive(adopt):
                    state('collecting',collector_pid=adopt,evaluator_pid=evaluator.pid)
                    if evaluator.poll() not in (None,0):raise RuntimeError('evaluation failed; inspect evaluation.log')
                    time.sleep(20)
            for attempt in range(3):
                progress=json.loads((data/'collection_progress.json').read_text()) if (data/'collection_progress.json').exists() else {}
                if progress.get('status')=='complete':break
                state('collection_retry',attempt=attempt+1)
                with (out/'collection.log').open('a') as source_log:
                    subprocess.run(command('yoyo.evaluation.spike_ashare_data','--config',EXP/'config.json',
                        '--destination',data,'--workers','4'),cwd=ROOT,env=env,stdout=source_log,stderr=subprocess.STDOUT,check=True)
                if attempt>=1:break
            if evaluator.poll() is None:
                # Its follow loop exits when the final collection pass ends.
                while evaluator.poll() is None:
                    state('evaluating',evaluator_pid=evaluator.pid);time.sleep(20)
            if evaluator.returncode!=0:raise RuntimeError('evaluation failed; inspect evaluation.log')
            state('final_reconciliation')
            subprocess.run(command('yoyo.evaluation.spike_ashare_study'),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            subprocess.run(command('yoyo.evaluation.spike_ashare_delivery'),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            summary=json.loads((out/'summary.json').read_text())
            save(status,dict(status='complete',result_status=summary['status'],updated_at=time.time(),
                 covered_symbols=summary['covered_symbols'],failed_symbols=summary['failed_symbols'],
                 delivery_receipt=str(out/'delivery_receipt.json')))
        except BaseException as exc:
            if evaluator.poll() is None:evaluator.terminate()
            save(status,dict(status='failed',error=f'{type(exc).__name__}: {exc}',updated_at=time.time()))
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--adopt-collector-pid',type=int,default=0)
    parser.add_argument('--adopt-evaluator-pid',type=int,default=0)
    args=parser.parse_args();run(args.adopt_collector_pid,args.adopt_evaluator_pid)
