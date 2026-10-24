"""Finish the current native batch with ledger validation and saved-result analysis.

This is a one-shot dependency of the running research job, not a new backtest.
It waits for one explicitly identified native parent process, then requires all
633 success receipts. It never relaunches assets, repairs data, or guesses missing
results. Any incomplete batch or failed check leaves a failure receipt instead
of publishing a complete summary.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-monday-limit-reclaim-20261024-v1"
NATIVE_COMMAND = " -m yoyo.evaluation.monday_limit_reclaim --pending-only --workers 6"


def process_identity(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-pid", required=True, type=int)
    args = parser.parse_args()
    initial = process_identity(args.wait_pid)
    if not initial or NATIVE_COMMAND not in initial:
        raise RuntimeError("The supplied PID is not the expected running native batch")
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    state = {
        "experiment_id": EXP.name, "status": "waiting_for_native_batch",
        "native_pid": args.wait_pid, "native_process_identity": initial,
        "postprocess_source_commit": source,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "execution_replays": 0, "expected_assets": 633,
        "notion_results_synced": False,
    }

    def save() -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = EXP / "postprocess-state.tmp"
        temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(EXP / "postprocess-state.json")

    save()
    try:
        while process_identity(args.wait_pid) == initial:
            time.sleep(30)
        receipts = sorted((EXP / "symbols").glob("*/done.json"))
        state["completed_assets"] = len(receipts)
        if len(receipts) != state["expected_assets"]:
            raise RuntimeError(f"Native batch ended with {len(receipts)}/633 success receipts")
        for stage, module in (
            ("validating_saved_native_ledgers", "yoyo.evaluation.monday_limit_reclaim_validate"),
            ("analyzing_saved_native_results", "yoyo.evaluation.monday_limit_reclaim_analysis"),
        ):
            source_path = module.replace(".", "/") + ".py"
            subprocess.run(
                ["git", "diff", "--exit-code", "HEAD", "--", source_path],
                cwd=ROOT, check=True,
            )
            state[stage + "_source_commit"] = subprocess.check_output(
                ["git", "log", "-1", "--format=%H", "--", source_path],
                cwd=ROOT, text=True,
            ).strip()
            state["status"] = stage
            save()
            subprocess.run([sys.executable, "-m", module], cwd=ROOT, check=True)
        summary = json.loads((EXP / "analysis/summary.json").read_text())
        if summary["completion_status"] != "complete" or summary["assets_done"] != 633:
            raise RuntimeError("Full analysis did not produce a complete 633-asset summary")
        state.update(
            status="local_results_complete", summary="analysis/summary.json",
            chinese_metrics="analysis/core_metrics_zh.csv",
            validation="validation.json",
            next="Review full findings, update existing Notion record/task, and commit scoped final results.",
        )
        save()
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
