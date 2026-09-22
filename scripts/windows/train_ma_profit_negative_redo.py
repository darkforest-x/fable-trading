r"""Train the authorized negative-population redo after auditing actual labels.

Both arms start from the same base and retain the parent's fixed recipe.  Only
resolved training outcome negatives are added.  No active model is changed.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import math
import time

from scripts.windows.train_ma_profit3r import TRAIN_ARGS, SAFE_AUG, verify_environment, write_windows_yaml
from yoyo.datasets.ma_profit_negative_redo import audit, checked_plan, read_json, resolve, sha256, write_json


def run(args: argparse.Namespace) -> dict:
    plan, inputs = checked_plan(args.plan)
    if plan["training_args"] != {**TRAIN_ARGS, **SAFE_AUG}:
        raise RuntimeError("training recipe differs from frozen parent")
    if args.dataset.resolve().name != plan["dataset_name"]:
        raise RuntimeError("wrong dataset for redo plan")
    target = args.run_root/("training_receipt.json" if args.train else "preflight.json")
    if target.exists() or any((args.run_root/f"arm_{a}").exists() for a in ("A", "B")):
        raise FileExistsError("refusing to overwrite an existing run")
    launch = read_json(args.launch_contract)
    if launch.get("status") != "frozen_after_local_audit" or launch.get("plan_sha256") != sha256(args.plan):
        raise RuntimeError("launch contract is not the frozen audited plan")
    for item in launch["files"]:
        if sha256(resolve(item["path"])) != item["sha256"]:
            raise RuntimeError("frozen launch code/input mismatch: " + item["path"])
    audited = audit(args.plan, args.dataset, args.selection)
    if audited != launch["dataset_audit"]:
        raise RuntimeError("remote audited population differs from frozen local audit")
    receipt = {"status": "preflight_passed", "train_requested": args.train, "started_unix": time.time(),
               "audit": audited, "environment": verify_environment(), "launch_contract_sha256": sha256(args.launch_contract),
               "plan_sha256": sha256(args.plan), "runner_sha256": sha256(Path(__file__)),
               "training_args": plan["training_args"], "arms": {}}
    base = inputs["base_model"]
    receipt["base_model_sha256"] = sha256(base)
    yamls = {a: write_windows_yaml(args.dataset, a, str(args.dataset.resolve())) for a in ("A", "B")}
    receipt["data_yaml_sha256"] = {a: sha256(p) for a, p in yamls.items()}
    args.run_root.mkdir(parents=True, exist_ok=True)
    write_json(target, receipt)
    if not args.train:
        return receipt
    from ultralytics import YOLO
    for a in ("A", "B"):
        arm_path = args.run_root/f"arm_{a}"
        if arm_path.exists():
            raise FileExistsError(arm_path)
        try:
            if sha256(base) != receipt["base_model_sha256"]:
                raise RuntimeError("base model changed between arms")
            receipt.update(status="running", current_arm=a)
            write_json(target, receipt)
            model = YOLO(str(base))
            model.train(data=str(yamls[a]), project=str(args.run_root.resolve()), name=f"arm_{a}", exist_ok=False, **TRAIN_ARGS, **SAFE_AUG)
            csv_path = arm_path/"results.csv"
            epochs = list(csv.DictReader(csv_path.open(encoding="utf-8")))
            if [int(float(r["epoch"])) for r in epochs] != list(range(1, 41)):
                raise RuntimeError("training did not complete exactly 40 epochs")
            if any(not math.isfinite(float(v)) for r in epochs for v in r.values()):
                raise RuntimeError("nonfinite training metric")
            artifacts = {"best": Path(model.trainer.best).resolve(), "last": Path(model.trainer.last).resolve(), "results_csv": csv_path.resolve(), "args_yaml": (arm_path/"args.yaml").resolve()}
            receipt["arms"][a] = {"status": "completed", **{k: str(v) for k, v in artifacts.items()},
                "sha256": {k: sha256(v) for k, v in artifacts.items()}, "completed_epochs": 40}
            write_json(target, receipt)
        except Exception as exc:
            receipt.update(status="failed", error=repr(exc), failed_arm=a)
            write_json(target, receipt)
            raise
    receipt.update(status="completed", completed_unix=time.time())
    write_json(target, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("plan", "dataset", "selection", "run-root", "launch-contract"):
        parser.add_argument("--"+arg, required=True, type=Path)
    parser.add_argument("--train", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
