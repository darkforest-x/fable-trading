"""Verify the local Kronos sidecar against the pinned research-only contract.

This command is offline and read-only except for its JSON receipt.  It does not
load Torch, read market data, train, score holdout, promote a model, or touch the
execution path.  The receipt proves which upstream checkout and exact
HuggingFace bytes a later experiment would use.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/audit_kronos_research_contract.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.contracts.kronos_research import (  # noqa: E402
    load_kronos_research_contract,
)


DEFAULT_CONFIG = ROOT / "configs/research/kronos_small_forecast_v1.yaml"
DEFAULT_OUT = (
    ROOT
    / "analysis/output/kronos_architecture_transfer_20260901/contract_audit.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(external_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(external_root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def snapshot_path(cache_root: Path, repo_id: str, revision: str) -> Path:
    key = "models--" + repo_id.replace("/", "--")
    return cache_root / key / "snapshots" / revision


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--external-root", type=Path, default=ROOT / "external/Kronos")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path.home() / ".cache/huggingface/hub",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    contract = load_kronos_research_contract(args.config)
    checks: List[Dict[str, Any]] = []

    def check(name: str, actual: Any, expected: Any) -> None:
        checks.append(
            {
                "name": name,
                "passed": actual == expected,
                "actual": actual,
                "expected": expected,
            }
        )

    check("external_checkout_exists", args.external_root.is_dir(), True)
    if args.external_root.is_dir():
        check("upstream_commit", git(args.external_root, "rev-parse", "HEAD"), contract.source_commit)
        check(
            "upstream_origin",
            git(args.external_root, "remote", "get-url", "origin"),
            contract.source_repository,
        )
        check("upstream_worktree_clean", git(args.external_root, "status", "--porcelain"), "")
        license_path = args.external_root / "LICENSE"
        check("license_exists", license_path.is_file(), True)
        if license_path.is_file():
            check("license_sha256", sha256_file(license_path), contract.source_license_sha256)

    for role, repo_id, revision, config_sha, weight_sha, weight_size in (
        (
            "model",
            contract.model_id,
            contract.model_revision,
            contract.model_config_sha256,
            contract.model_weights_sha256,
            contract.model_weights_size,
        ),
        (
            "tokenizer",
            contract.tokenizer_id,
            contract.tokenizer_revision,
            contract.tokenizer_config_sha256,
            contract.tokenizer_weights_sha256,
            contract.tokenizer_weights_size,
        ),
    ):
        snapshot = snapshot_path(args.cache_root, repo_id, revision)
        config_path = snapshot / "config.json"
        weights_path = snapshot / "model.safetensors"
        check(f"{role}_snapshot_exists", snapshot.is_dir(), True)
        check(f"{role}_config_exists", config_path.is_file(), True)
        check(f"{role}_weights_exists", weights_path.is_file(), True)
        if config_path.is_file():
            check(f"{role}_config_sha256", sha256_file(config_path), config_sha)
        if weights_path.is_file():
            check(f"{role}_weights_sha256", sha256_file(weights_path), weight_sha)
            check(f"{role}_weights_size", weights_path.stat().st_size, weight_size)

    upstream_exact = {
        "einops": "0.8.1",
        "huggingface-hub": "0.33.1",
        "matplotlib": "3.9.3",
        "pandas": "2.2.2",
        "tqdm": "4.67.1",
        "safetensors": "0.6.2",
    }
    runtime_versions = {
        package: package_version(package) for package in (*upstream_exact, "numpy", "torch")
    }
    dependency_mismatches = {
        package: {"runtime": runtime_versions[package], "upstream": expected}
        for package, expected in upstream_exact.items()
        if runtime_versions[package] != expected
    }

    hard_failures = [item for item in checks if not item["passed"]]
    receipt = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "market_data_rows_read": 0,
            "holdout_rows_read": 0,
            "training_runs": 0,
            "promotion_writes": 0,
            "execution_writes": 0,
        },
        "contract": str(args.config.resolve()),
        "checks": checks,
        "hard_failures": hard_failures,
        "runtime_versions": runtime_versions,
        "upstream_exact_dependency_versions": upstream_exact,
        "dependency_mismatches": dependency_mismatches,
        "isolated_runtime_required_for_future_market_experiment": bool(
            dependency_mismatches
        ),
        "passed": not hard_failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

