"""Run one owner-authorized early-v6 training job with durable lifecycle state.

The dataset and preflight checks remain in the controlled training package.
This wrapper records completion/failure and keeps the Windows host awake for
this job only. It never restarts, promotes, changes live models or notifies.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import time

from yoyo.datasets.ma_morphology_training_package import sha256_file, train


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root',type=Path,required=True)
    parser.add_argument('--preflight-dir',type=Path,required=True)
    parser.add_argument('--expected-preflight-sha256',required=True)
    args=parser.parse_args()
    dataset=args.dataset_root.resolve();receipt=dataset/'training_job.json'
    if receipt.exists():raise FileExistsError('Job already registered: '+str(receipt))
    if sha256_file(args.preflight_dir/'preflight_receipt.json')!=args.expected_preflight_sha256:
        raise ValueError('Approved preflight bytes changed before launch')
    state={'status':'starting','pid':os.getpid(),'started_unix':time.time(),
        'preflight_sha256':args.expected_preflight_sha256,'dataset':str(dataset),
        'production_eligible':False,'active_model_changed':False}
    # Exclusive reservation also rejects two concurrent launch attempts.
    with receipt.open('x',encoding='utf-8') as f:json.dump(state,f,indent=2)
    if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    def save():
        temp=receipt.with_suffix('.tmp');temp.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8');temp.replace(receipt)
    try:
        state['status']='training';save()
        result=train(dataset,args.preflight_dir)
        run=Path(result['run_dir'])
        state.update(status='training_completed_evaluation_pending',completed_unix=time.time(),result=result,
            best_sha256=sha256_file(run/'weights/best.pt'),last_sha256=sha256_file(run/'weights/last.pt'),
            results_sha256=sha256_file(run/'results.csv'))
    except BaseException as exc:
        state.update(status='failed',failed_unix=time.time(),error=repr(exc));raise
    finally:
        if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        save()


if __name__=='__main__':main()
