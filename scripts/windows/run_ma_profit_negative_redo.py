"""Detached RTX3060 train/evaluate sequence; preserves every prior run."""
from __future__ import annotations
import argparse
import ctypes
from pathlib import Path
import subprocess
import sys
import time

from yoyo.datasets.ma_profit_negative_redo import ROOT, read_json, write_json, sha256


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,required=True)
    p.add_argument('--run-root',type=Path,required=True)
    args=p.parse_args()
    exp=args.experiment.resolve();run=args.run_root.resolve();plan=read_json(exp/'plan.json')
    dataset=ROOT/'datasets'/plan['dataset_name'];receipt_path=run/'job_receipt.json'
    if receipt_path.exists(): raise FileExistsError(receipt_path)
    state={'status':'running','started_unix':time.time(),'plan_sha256':sha256(exp/'plan.json'),'stages':{}}
    write_json(receipt_path,state)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        cmd=[sys.executable,'-u','-m','scripts.windows.train_ma_profit_negative_redo','--plan',str(exp/'plan.json'),'--dataset',str(dataset),'--selection',str(exp/'selection'),'--run-root',str(run),'--launch-contract',str(exp/'launch_contract.json'),'--train']
        subprocess.run(cmd,cwd=ROOT,check=True)
        training=read_json(run/'training_receipt.json')
        if training['status']!='completed':raise RuntimeError('training not completed')
        state['stages']['training']='completed';write_json(receipt_path,state)
        # The old evaluation ledger and shared images are exactly preserved,
        # which keeps the original frozen random-control binding unchanged.
        ledger=ROOT/plan['inputs']['old_ledger']['path']
        for arm in ('A','B'):
            out=exp/'evaluation'/f'arm_{arm}'
            subprocess.run([sys.executable,'-u','-m','scripts.windows.evaluate_ma_profit3r_20260922','--dataset',str(dataset),'--ledger',str(ledger),'--model',training['arms'][arm]['best'],'--arm',arm,'--split','both','--out',str(out)],cwd=ROOT,check=True)
            state['stages']['evaluation_'+arm]='completed';write_json(receipt_path,state)
        state.update(status='completed',completed_unix=time.time())
    except Exception as exc:
        state.update(status='failed',error=repr(exc));raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        write_json(receipt_path,state)


if __name__=='__main__':main()
