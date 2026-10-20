"""Owner's post-hoc second-diamond comparison, reusing all six completed arms.

Only the first opposite confirmed diamond after entry is skipped, regardless
of its profitability. Subsequent events retain the fee-positive gate and
five original-size reductions. Inputs/features and all other exits stay fixed.
"""
import json
from pathlib import Path
import subprocess

import pandas as pd

from yoyo.evaluation.range_rsi_scaleout import context,scaleout_overlay,ROOT,dump

DEFAULT=ROOT/"experiments/active/exp-eth-timebe-rsi-20261020-v2/config.json"


def prepare(config=DEFAULT):
    cfg=json.loads(config.read_text())
    original=ROOT/cfg["reuse_experiment"]
    previous=json.loads((original/"config.json").read_text())
    checks=["yoyo/evaluation/range_rsi_second.py","yoyo/evaluation/range_rsi_scaleout.py",
            "yoyo/layers/l3_backtest/nautilus_independent.py",str(config)]
    if subprocess.run(["git","diff","--quiet","HEAD","--",*checks],cwd=ROOT).returncode:
        raise RuntimeError("Commit source and config before building")
    if (config.parent/"screen_receipt.json").exists():
        raise RuntimeError("Completed second-diamond screen exists; reuse it")
    _,_,ctx,base,info=context(config)
    assert cfg["arms"][:len(previous["arms"])]==previous["arms"]
    receipts={}
    for arm in previous["arms"]:
        src=original/"runs"/arm["name"]
        receipt=json.loads((src/"receipt.json").read_text())
        assert receipt["audit"]["parity"]["status"]=="passed"
        assert receipt["inputs"]==info["identity"]
        dst=config.parent/"runs"/arm["name"]
        dst.parent.mkdir(exist_ok=True)
        dst.symlink_to(src,target_is_directory=True)
        receipts[arm["name"]]=str(src/"receipt.json")
    baseline=pd.read_csv(original/"runs/baseline/screen.csv",float_precision="round_trip")
    for arm in cfg["arms"][len(previous["arms"]):]:
        assert not arm["time_be"] and arm["rsi_skip_events"]==1
        folder=config.parent/"runs"/arm["name"]
        folder.mkdir(exist_ok=True)
        scaleout_overlay(baseline,ctx,arm["rsi_minutes"],skip_events=1).to_csv(
            folder/"screen.csv",index=False)
    dump(config.parent/"screen_receipt.json",dict(
        reuse_receipts=receipts,new_arms=2,paths_per_arm=len(base),context=info,
        posthoc_owner_request=True,
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()))


if __name__=="__main__":
    prepare()
