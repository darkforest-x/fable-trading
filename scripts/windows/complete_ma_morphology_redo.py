"""Windows-only detached completion: train A/B then frozen economics receipts."""
from __future__ import annotations

import argparse
import ctypes
import time
from pathlib import Path

from scripts.windows.run_ma_morphology_redo import run_training
from yoyo.datasets.ma_morphology_redo import read_json, sha, write_json
from yoyo.evaluation.ma_morphology_economics import run as run_economics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True); parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True); parser.add_argument("--launch-contract", type=Path, required=True)
    args = parser.parse_args(); exp = args.experiment.resolve(); receipt_path = exp / "job_receipt.json"
    if receipt_path.exists(): raise FileExistsError(receipt_path)
    state = {
        "status": "running", "started_unix": time.time(), "plan_sha256": sha(exp / "plan.json"),
        "launch_sha256": sha(args.launch_contract), "stages": {},
    }
    write_json(receipt_path, state); ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        training = run_training(plan_path=exp / "plan.json", dataset=args.dataset, run_root=args.run_root, launch_contract_path=args.launch_contract, train=True)
        if training.get("status") != "completed": raise RuntimeError("training did not complete")
        state["stages"]["training"] = "completed"; write_json(receipt_path, state)
        for arm in ("A", "B"):
            run_economics(exp / "plan.json", Path(training["arms"][arm]["best"]), arm, exp / "economics" / f"arm_{arm}", args.launch_contract)
            state["stages"][f"economics_{arm}"] = "completed"; write_json(receipt_path, state)
        state.update(status="completed", completed_unix=time.time())
    except Exception as exc:
        state.update(status="failed", error=repr(exc)); raise
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000); write_json(receipt_path, state)


if __name__ == "__main__": main()
