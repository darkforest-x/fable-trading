"""One-shot continuation of the already authorized fee-stop comparison.

Wait for the exact majors process and require both success receipts. Then the
same runner waits for the two original pools and computes only missing new-
policy symbols. This is a dependent local job, not a recurring scheduler.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import time
from yoyo.evaluation.monday_reclaim_fee import EXP, ROOT, process_identity
from yoyo.evaluation.range_framework import dump


def main():
    p=argparse.ArgumentParser();p.add_argument('--majors-pid',type=int,required=True)
    p.add_argument('--wait-pids',type=int,nargs='*',default=[]);args=p.parse_args()
    identity=process_identity(args.majors_pid)
    if identity and ' -m yoyo.evaluation.monday_reclaim_fee --symbols BTCUSDT ETHUSDT --workers 1' not in identity:
        raise RuntimeError('Majors process identity mismatch')
    state=dict(status='waiting_for_majors',pid=os.getpid(),majors_pid=args.majors_pid,
        majors_identity=identity,started_at=datetime.now(timezone.utc).isoformat())
    def save():dump(EXP/'queue-state.json',state)
    save()
    try:
        while identity and process_identity(args.majors_pid)==identity:time.sleep(30)
        if json.loads((EXP/'state.json').read_text())['status']!='partial_complete':
            raise RuntimeError('Majors did not finish successfully; do not blindly rerun')
        for symbol in ['BTCUSDT','ETHUSDT']:
            if not (EXP/'symbols'/symbol/'done.json').exists():
                raise RuntimeError(f'Missing major receipt: {symbol}')
        command=['/Users/zhangzc/.local/bin/nautilus-python','-m','yoyo.evaluation.monday_reclaim_fee',
                 '--workers','2','--wait-pids',*[str(pid) for pid in args.wait_pids]]
        state.update(status='dependent_full_batch_started',command=command);save()
        subprocess.run(command,cwd=ROOT,check=True)
        state.update(status='complete');save()
    except BaseException as exc:
        state.update(status='failed',error=repr(exc));save();raise


if __name__=='__main__':main()
