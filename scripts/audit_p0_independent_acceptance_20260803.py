#!/usr/bin/env python3
"""Independently accept or reject the nine P0 commits before P1 may start.

This audit evaluates the repository range ``4333fa7..fba6a65`` directly.  It
does not call either P0 audit generator as an authority.  It inspects the commit
chain and changed paths, recomputes protected hashes, streams only the frozen
pre-holdout dataset's signal timestamps, validates the checked-in reports/JSON,
and reruns both the focused and complete test suites with the repository venv.

The only write is the versioned JSON receipt under ``analysis/output``.  No
model, dataset, ACTIVE pointer, forward log, ledger, threshold, deployment, or
order interface is mutated.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
BASE = "4333fa722b6a98fdaa8a36f37f1d468d43956b5f"
TARGET = "fba6a65eab37022ffed7f6b1c729e5bbd0e40894"
CUTOFF = datetime.fromisoformat("2026-05-04T00:00:00+00:00")
OUT = PROJECT / "analysis/output/p0_independent_acceptance_20260803.json"

EXPECTED_COMMITS = [
    "95ebfb0a931d1f3070701be5ee1068b6d343dd8b",
    "cd9ca5a11a2d1b2cdb1508f033c5cd2b7375f210",
    "8cd2a569d0199977f631180d9de58d3d90200120",
    "892964c1933d077ac930d6c3e7845b380e632305",
    "1cb669c3dfc4d14977e9dd761a9f4848c074fb1e",
    "ee98ebd6794f3c658cc407971827090558d35d65",
    "8e90390d119476ba397a04ab31d95a23d50b16ac",
    "969dda71f5eb090c44c439f255496be0985b7531",
    TARGET,
]

FOCUSED_TESTS = [
    "tests/test_executor_side_guard.py",
    "tests/test_active_bundle_protocol.py",
    "tests/test_forward_provenance.py",
    "tests/test_forward_feature_semantics.py",
    "tests/test_canonical_outcomes.py",
    "tests/test_return_cost_contract.py",
    "tests/test_execution_timeline.py",
    "tests/test_global_tip_age_gate.py",
]

AUDIT_DIRTY_PATHS = {
    "analysis/html/p0_independent_acceptance_20260803.html",
    "analysis/output/p0_independent_acceptance_20260803.json",
    "analysis/p0_independent_acceptance_20260803.md",
    "docs/learnings/reproducible-test-results-must-name-the-project-interpreter.md",
    "scripts/audit_p0_independent_acceptance_20260803.py",
}


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=PROJECT, text=True, capture_output=True, check=check
    )


def git(*args: str) -> str:
    return run("git", *args).stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha256(revision: str, path: str) -> str:
    blob = run("git", "show", f"{revision}:{path}").stdout.encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def git_path_exists(revision: str, path: str) -> bool:
    return run("git", "cat-file", "-e", f"{revision}:{path}", check=False).returncode == 0


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def parse_time(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def inspect_preholdout_dataset(path: Path) -> dict[str, Any]:
    """Read signal timestamps only; the known frozen file must contain zero holdout rows."""
    rows = 0
    maximum: datetime | None = None
    holdout_rows = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = parse_time(row["signal_time"])
            rows += 1
            maximum = timestamp if maximum is None or timestamp > maximum else maximum
            if timestamp >= CUTOFF:
                holdout_rows += 1
    return {
        "path": str(path.relative_to(PROJECT)),
        "sha256": sha256(path),
        "row_count": rows,
        "max_signal_time": maximum.isoformat() if maximum else None,
        "cutoff": CUTOFF.isoformat(),
        "holdout_rows_read": holdout_rows,
    }


def pytest_receipt(paths: list[str]) -> dict[str, Any]:
    python = PROJECT / ".venv/bin/python"
    command = [str(python), "-m", "pytest", "-q", "-rs", *paths]
    result = run(*command, check=False)
    output = result.stdout + result.stderr
    passed = re.findall(r"(\d+) passed", output)
    skipped = re.findall(r"(\d+) skipped", output)
    skip_reasons = [line for line in output.splitlines() if line.startswith("SKIPPED")]
    return {
        "command": "PYTHONPATH=. " + " ".join(command),
        "python": str(python),
        "returncode": result.returncode,
        "passed": int(passed[-1]) if passed else 0,
        "skipped": int(skipped[-1]) if skipped else 0,
        "skip_reasons": skip_reasons,
        "tail": "\n".join(output.splitlines()[-24:]),
    }


def changed_paths() -> list[str]:
    return [line for line in git("diff", "--name-only", f"{BASE}..{TARGET}").splitlines() if line]


def dirty_paths() -> list[str]:
    paths: list[str] = []
    for line in git("status", "--porcelain").splitlines():
        if line:
            paths.append(line[3:].strip())
    return sorted(paths)


def main() -> int:
    baseline = load_json(
        PROJECT / "analysis/output/p0_safety_baseline_20260803/artifact_hashes.json"
    )
    runtime = load_json(PROJECT / "analysis/output/p0_runtime_parity_audit_20260803.json")
    returns = load_json(PROJECT / "analysis/output/p0_return_semantics_20260803.json")
    sidecar = load_json(PROJECT / "models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json")

    commits = git("rev-list", "--reverse", f"{BASE}..{TARGET}").splitlines()
    paths = changed_paths()
    forbidden_paths = sorted(
        path
        for path in paths
        if path.startswith("data/")
        or path == "models/ACTIVE"
        or path == "models/active_bundle.json"
        or (path.startswith("models/") and path != "models/active_bundle.example.json")
        or any(token in path.lower() for token in (".env", "secret", "api_key", "keys.json"))
        or path.startswith(("deploy/", "infra/"))
    )
    deleted_paths = [
        line.split("\t", 1)[1]
        for line in git("diff", "--name-status", f"{BASE}..{TARGET}").splitlines()
        if line.startswith("D\t")
    ]
    modified_historical_reports = sorted(
        path
        for path in paths
        if path.startswith("analysis/")
        and path not in {"analysis/INDEX.md"}
        and not path.startswith("analysis/output/p0_")
        and not path.startswith("analysis/html/p0_")
        and not Path(path).name.startswith("p0_")
    )
    added_diff = git("diff", "--unified=0", f"{BASE}..{TARGET}", "--", "src", "scripts")
    added_executable_lines = "\n".join(
        line[1:] for line in added_diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    training_calls_added = sorted(
        set(re.findall(r"\b(?:lgb\.)?train\s*\(", added_executable_lines))
    )
    order_calls_added = sorted(
        set(re.findall(r"\b(?:place_market|create_order|cancel_order)\s*\(", added_executable_lines))
    )

    protected: dict[str, Any] = {}
    for path in ("models/ACTIVE", "data/forward_log.csv", "data/executor_ledger.jsonl"):
        current = sha256(PROJECT / path)
        protected[path] = {
            "baseline_sha256": baseline[path]["sha256"],
            "current_sha256": current,
            "unchanged": current == baseline[path]["sha256"],
        }

    dataset_path = PROJECT / str(sidecar["dataset_path"])
    preholdout = inspect_preholdout_dataset(dataset_path)
    preholdout["sidecar_sha256"] = sidecar["dataset_sha256"]
    preholdout["hash_matches_sidecar"] = preholdout["sha256"] == sidecar["dataset_sha256"]

    before_tests = {path: sha256(PROJECT / path) for path in protected}
    focused = pytest_receipt(FOCUSED_TESTS)
    complete = pytest_receipt(["tests"])
    after_tests = {path: sha256(PROJECT / path) for path in protected}
    tests_preserved_protected = before_tests == after_tests

    p0_md = PROJECT / "analysis/p0_safety_protocol_repair_20260803.md"
    parity_md = PROJECT / "analysis/p0_runtime_parity_audit_20260803.md"
    p0_html = PROJECT / "analysis/html/p0_safety_protocol_repair_20260803.html"
    parity_html = PROJECT / "analysis/html/p0_runtime_parity_audit_20260803.html"
    handoff = (PROJECT / "HANDOFF.md").read_text(encoding="utf-8")
    p0_text = p0_md.read_text(encoding="utf-8")
    parity_text = parity_md.read_text(encoding="utf-8")

    dirty = dirty_paths()
    only_audit_dirty = set(dirty).issubset(AUDIT_DIRTY_PATHS)
    checks = {
        "commit_chain_exact": commits == EXPECTED_COMMITS,
        "nine_commits": len(commits) == 9,
        "branch_main": git("branch", "--show-current") == "main",
        "p0_target_equals_origin_main": git("rev-parse", "origin/main") == TARGET,
        "p0_target_equals_pre_acceptance_head": git("rev-parse", "HEAD") == TARGET,
        "worktree_has_only_acceptance_artifacts": only_audit_dirty,
        "no_forbidden_path_changes": not forbidden_paths,
        "no_deleted_paths": not deleted_paths,
        "no_historical_report_rewrites": not modified_historical_reports,
        "no_training_call_added": not training_calls_added,
        "no_order_call_added": not order_calls_added,
        "active_pointer_same_at_endpoints": (
            git_blob_sha256(BASE, "models/ACTIVE")
            == git_blob_sha256(TARGET, "models/ACTIVE")
            == protected["models/ACTIVE"]["current_sha256"]
        ),
        "active_bundle_absent_at_endpoints_and_now": (
            not git_path_exists(BASE, "models/active_bundle.json")
            and not git_path_exists(TARGET, "models/active_bundle.json")
            and not (PROJECT / "models/active_bundle.json").exists()
        ),
        "protected_hashes_unchanged": all(item["unchanged"] for item in protected.values()),
        "frozen_dataset_hash_matches": preholdout["hash_matches_sidecar"],
        "cutoff_after_dataset_max": (
            preholdout["max_signal_time"] is not None
            and parse_time(str(preholdout["max_signal_time"])) < CUTOFF
            and preholdout["holdout_rows_read"] == 0
        ),
        "runtime_parity_rejected": str(runtime.get("parity_verdict", "")).startswith("REJECTED:"),
        "runtime_did_not_claim_active_bundle": runtime.get("repo", {}).get("active_bundle_exists") is False,
        "runtime_safety_flags_clean": runtime.get("safety") == {
            "trained": False,
            "holdout_read": False,
            "active_changed": False,
            "forward_log_changed": False,
            "ledger_changed": False,
            "deployed": False,
            "order_triggered": False,
        },
        "return_audit_is_preholdout": (
            returns.get("holdout_read") is False
            and parse_time(str(returns["dataset_max_signal_time"])) < CUTOFF
        ),
        "selector_was_recorded_abnormal": (
            float(returns["calibration_pass_rate"]) > 0.9
            and float(returns["threshold_equal_rate"]) > 0.8
            and runtime.get("active_runtime", {}).get("execution_status") == "legacy_audit_only"
        ),
        "reports_exist_and_have_direct_verdicts": (
            all(path.exists() and path.stat().st_size > 0 for path in (p0_md, parity_md, p0_html, parity_html))
            and "P0-SAFETY 本地验收通过；当前策略仍不可执行" in p0_text
            and "研究结论不得转移" in parity_text
        ),
        "handoff_is_fail_closed": (
            "P0-SAFETY 已完成，必须停在 Owner gate" in handoff
            and "models/active_bundle.json" in handoff
            and "production 会 fail-closed" in handoff
        ),
        "focused_tests_pass": focused["returncode"] == 0 and focused["passed"] == 133,
        "complete_tests_pass_without_deselect": complete["returncode"] == 0 and complete["passed"] == 473,
        "tests_preserved_protected_files": tests_preserved_protected,
    }

    invariants = {
        "short_missing_nan_unknown_protocol_mismatch_rejected_before_client": {
            "accepted": checks["focused_tests_pass"],
            "evidence": [
                "tests/test_executor_side_guard.py::test_rejected_side_never_calls_trading_client",
                "tests/test_executor_side_guard.py::test_long_row_under_short_protocol_cannot_reach_buy_client",
            ],
        },
        "production_exact_bundle_hash_no_latest_fallback": {
            "accepted": checks["focused_tests_pass"] and checks["active_bundle_absent_at_endpoints_and_now"],
            "evidence": [
                "tests/test_active_bundle_protocol.py::test_corrupt_bundle_does_not_fall_back",
                "tests/test_active_bundle_protocol.py::test_forward_production_stops_before_reading_log_when_bundle_is_absent",
            ],
        },
        "signal_decision_request_fill_separated_no_fill_no_actual_pnl": {
            "accepted": checks["focused_tests_pass"],
            "evidence": [
                "tests/test_execution_timeline.py::test_f01_and_f04_paper_fill_is_first_open_strictly_after_decision",
                "tests/test_execution_timeline.py::test_f03_candidate_without_fill_is_not_an_actual_closed_trade",
            ],
        },
        "whole_series_tip_age_at_most_two": {
            "accepted": checks["focused_tests_pass"],
            "evidence": [
                "tests/test_global_tip_age_gate.py::test_g01_forward_final_gate_rejects_back2_local_bar198_mapping",
                "tests/test_global_tip_age_gate.py::test_g02_global_tip_age_zero_one_two_are_accepted",
            ],
        },
        "research_lift_not_attributed_to_active": {
            "accepted": checks["runtime_parity_rejected"],
            "evidence": ["analysis/output/p0_runtime_parity_audit_20260803.json"],
        },
        "abnormal_q90_recorded_not_retuned": {
            "accepted": checks["selector_was_recorded_abnormal"] and checks["active_pointer_same_at_endpoints"],
            "evidence": [
                "analysis/output/p0_return_semantics_20260803.json",
                "tests/test_active_bundle_protocol.py::test_abnormal_selector_can_be_audited_but_never_execution_eligible",
            ],
        },
        "gross_taker_maker_no_implicit_double_deduction": {
            "accepted": checks["focused_tests_pass"],
            "evidence": [
                "tests/test_return_cost_contract.py::test_taker_to_maker_restores_gross_before_applying_maker",
                "tests/test_return_cost_contract.py::test_deduct_api_refuses_already_net_input",
            ],
        },
    }

    accepted = all(checks.values()) and all(item["accepted"] for item in invariants.values())
    result = {
        "audit_version": "p0_independent_acceptance_20260803",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "accepted" if accepted else "rejected",
        "p1_entry_allowed": accepted,
        "scope": {
            "base": BASE,
            "target": TARGET,
            "commit_count": len(commits),
            "independent_of_previous_completion_summary": True,
        },
        "environment": {
            "platform": platform.platform(),
            "python": str(PROJECT / ".venv/bin/python"),
            "python_version": run(str(PROJECT / ".venv/bin/python"), "--version").stdout.strip(),
            "pythonpath": os.environ.get("PYTHONPATH", "."),
        },
        "repository": {
            "branch": git("branch", "--show-current"),
            "head": git("rev-parse", "HEAD"),
            "origin_main": git("rev-parse", "origin/main"),
            "expected_commits": EXPECTED_COMMITS,
            "actual_commits": commits,
            "changed_path_count": len(paths),
            "changed_paths": paths,
            "forbidden_paths": forbidden_paths,
            "deleted_paths": deleted_paths,
            "modified_historical_reports": modified_historical_reports,
            "dirty_paths_during_acceptance_generation": dirty,
            "training_calls_added": training_calls_added,
            "order_calls_added": order_calls_added,
        },
        "protected_artifacts": protected,
        "preholdout_evidence": preholdout,
        "p0_machine_audits": {
            "runtime_parity_json": "analysis/output/p0_runtime_parity_audit_20260803.json",
            "runtime_json_code_head": runtime.get("repo", {}).get("head"),
            "return_json": "analysis/output/p0_return_semantics_20260803.json",
            "return_dataset_max_signal_time": returns.get("dataset_max_signal_time"),
        },
        "tests": {"focused": focused, "complete": complete},
        "invariants": invariants,
        "checks": checks,
        "safety": {
            "holdout_rows_read": preholdout["holdout_rows_read"],
            "trained": False,
            "threshold_changed": False,
            "active_changed": not protected["models/ACTIVE"]["unchanged"],
            "active_bundle_created": (PROJECT / "models/active_bundle.json").exists(),
            "forward_log_changed": not protected["data/forward_log.csv"]["unchanged"],
            "ledger_changed": not protected["data/executor_ledger.jsonl"]["unchanged"],
            "deployed": False,
            "order_triggered": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
