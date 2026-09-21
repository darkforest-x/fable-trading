"""Run the unchanged MA miner with an explicitly bound local-profile adapter.

The original miner, morphology rules, candidate population and outcome plan
remain byte-identical. Only strict-profile array work is restricted to the
already-enriched rows consumed by the original profile function. A committed
parity receipt is required before this entry point can run a source batch.
New source directories and receipt/master fields identify the implementation.
"""
from __future__ import annotations

import argparse
from functools import partial
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from yoyo.datasets import ma_profit_miner as miner
from yoyo.datasets.ma_profit_profile_window import extract_profile_window


ROOT = miner.ROOT
ADAPTER = ROOT / "yoyo/datasets/ma_profit_profile_window.py"
CONTRACT_NAME = "profile_window_contract.json"
IMPLEMENTATION_ID = "bounded_strict_profile_v1"
_BASE_BINDING = miner._receipt_binding
_BASE_OUTPUT_DIR = miner._source_output_dir
_BASE_INITIALIZER = miner._initialize_worker
_BASE_PROFILE = miner.extract_profile
_BASE_WRITE_JSON = miner._write_json
_IMPLEMENTATION: dict[str, Any] | None = None


def implementation(plan_path: Path) -> dict[str, Any]:
    """Validate the frozen equivalence evidence and return runtime lineage."""
    contract_path = Path(plan_path).resolve().parent / CONTRACT_NAME
    contract = json.loads(contract_path.read_text())
    expected = {
        "implementation_id": IMPLEMENTATION_ID,
        "change_scope": "strict_profile_array_window_only",
        "plan_sha256": miner.sha256_file(Path(plan_path)),
        "adapter_sha256": miner.sha256_file(ADAPTER),
        "miner_sha256": miner.sha256_file(Path(miner.__file__)),
        "rule_dependency_sha256": miner._rule_dependency_hashes(),
    }
    if any(contract.get(key) != value for key, value in expected.items()):
        raise miner.ProfitMinerError("profile-window contract implementation drift")
    proof_path = miner._repo_path(contract["parity_receipt_path"])
    if not proof_path.is_file():
        raise miner.ProfitMinerError("profile-window parity receipt is missing")
    if miner.sha256_file(proof_path) != contract.get("parity_receipt_sha256"):
        raise miner.ProfitMinerError("profile-window parity receipt SHA drift")
    proof = json.loads(proof_path.read_text())
    if (proof.get("status") != "passed" or proof.get("adapter_sha256") != expected["adapter_sha256"]
            or proof.get("miner_sha256") != expected["miner_sha256"]
            or proof.get("rule_dependency_sha256") != expected["rule_dependency_sha256"]
            or int(proof.get("exact_profiles", 0)) < 20 or proof.get("mismatches") != 0):
        raise miner.ProfitMinerError("profile-window equivalence evidence is incomplete")
    return {
        "implementation_id": IMPLEMENTATION_ID,
        "driver_sha256": miner.sha256_file(Path(__file__)),
        "adapter_sha256": expected["adapter_sha256"],
        "contract_sha256": miner.sha256_file(contract_path),
        "parity_receipt_sha256": miner.sha256_file(proof_path),
    }


def _receipt_binding(plan_sha: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    if _IMPLEMENTATION is None:
        raise miner.ProfitMinerError("profile implementation was not initialized")
    binding = _BASE_BINDING(plan_sha, spec)
    return {**binding, "profile_implementation": dict(_IMPLEMENTATION),
            "code_sha256": miner._json_sha({"original_code_sha256": binding["code_sha256"],
                                             "profile_implementation": _IMPLEMENTATION})}


def _source_output_dir(output_root: Path, spec: Mapping[str, Any]) -> Path:
    if _IMPLEMENTATION is None:
        raise miner.ProfitMinerError("profile implementation was not initialized")
    original = _BASE_OUTPUT_DIR(output_root, spec)
    return original.with_name(original.name + "__pw_" + _IMPLEMENTATION["adapter_sha256"][:12])


def _install(impl: Mapping[str, Any]) -> None:
    global _IMPLEMENTATION
    _IMPLEMENTATION = dict(impl)
    miner.extract_profile = extract_profile_window
    miner._receipt_binding = _receipt_binding
    miner._source_output_dir = _source_output_dir
    miner._write_json = _write_json


def _write_json(path: Path, value: object) -> None:
    # Publish a fully bound master atomically; do not first expose a legacy
    # master and add adapter metadata in a second write.
    if Path(path).name.startswith("master_") and isinstance(value, Mapping) and "source_summaries" in value:
        value = {**value, "profile_implementation": dict(_IMPLEMENTATION)}
    _BASE_WRITE_JSON(path, value)


def _initialize_worker(context, plan, plan_sha, output_root, *, impl):
    """Install the same adapter in spawned workers before original initialization."""
    _install(impl)
    _BASE_INITIALIZER(context, plan, plan_sha, output_root)


def build(plan_path: Path, sources_path: Path, *, workers: int = 2,
          output_name: str = "source_scans") -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", output_name):
        raise miner.ProfitMinerError("invalid isolated output directory name")
    plan_path, sources_path = Path(plan_path).resolve(), Path(sources_path).resolve()
    master_path = plan_path.parent / output_name / f"master_{sources_path.stem}.json"
    if master_path.exists():
        raise FileExistsError(f"refusing to overwrite completed source master: {master_path}")
    impl = implementation(plan_path)
    contract_path = plan_path.parent / CONTRACT_NAME
    contract = json.loads(contract_path.read_text())
    frozen = [Path(__file__), ADAPTER, contract_path,
              miner._repo_path(contract["parity_receipt_path"]), plan_path, sources_path]
    names = [miner._relative(path) for path in frozen]
    subprocess.check_output(["git", "ls-files", "--error-unmatch", "--", *names], cwd=ROOT, text=True)
    miner._assert_committed(frozen)
    old_output_name = miner.DEFAULT_OUTPUT_NAME
    try:
        _install(impl)
        miner._initialize_worker = partial(_initialize_worker, impl=impl)
        miner.DEFAULT_OUTPUT_NAME = output_name
        master = miner.build(plan_path, sources_path, workers=workers)
        master["profile_implementation"] = impl
        return master
    finally:
        global _IMPLEMENTATION
        _IMPLEMENTATION = None
        miner.extract_profile = _BASE_PROFILE
        miner._receipt_binding = _BASE_BINDING
        miner._source_output_dir = _BASE_OUTPUT_DIR
        miner._initialize_worker = _BASE_INITIALIZER
        miner._write_json = _BASE_WRITE_JSON
        miner.DEFAULT_OUTPUT_NAME = old_output_name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output-name", default="source_scans")
    args = parser.parse_args()
    result = build(args.plan, args.sources, workers=args.workers, output_name=args.output_name)
    print(json.dumps({key: result[key] for key in ("attempted_sources", "completed_sources", "failed_sources",
                                                  "strict_grade_a_before_cross_timeframe_dedup", "profile_implementation")}))


if __name__ == "__main__":
    main()
