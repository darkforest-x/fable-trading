"""Validate the two explicitly approved MA-profit training-capacity contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


EXPERIMENT_ID = "exp-ma-profit3r-20260922-v1"
ORIGINAL_CONTRACT_NAME = "training_contract.json"
OWNER_V2_CONTRACT_NAME = "training_contract_owner1500_v2.json"
OWNER_V2_AMENDMENT_NAME = "owner_amendment_1500_v2.json"
OWNER_V2_REQUEST = "到1500个就开始处理 处理好了直接去训练吧"
QUOTA_SCOPE = "train_independent_retained_events"


class TrainingContractError(RuntimeError):
    """Raised when a capacity contract is outside the owner's frozen approvals."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrainingContractError(f"invalid training control: {path}") from exc
    if not isinstance(value, dict):
        raise TrainingContractError(f"training control must be an object: {path}")
    return value


def _exact(value: Mapping[str, Any], expected: Mapping[str, Any], *, label: str) -> None:
    if any(value.get(key) != wanted for key, wanted in expected.items()):
        raise TrainingContractError(f"{label} binding drift")


def _repo_relative(path: Path, repo_root: Path) -> str:
    """Serialize frozen repository paths with the JSON contract's slash syntax."""

    return path.relative_to(repo_root).as_posix()


def validate_training_contract(
    plan_path: Path,
    contract_path: Path,
    *,
    repo_root: Path,
    expected_experiment: str = EXPERIMENT_ID,
) -> dict[str, Any]:
    """Return only an original 3000/5000 or owner-bound v2 1500/5000 contract.

    The v2 path is deliberately named and sibling-bound: a similar JSON with a
    lower quota cannot become eligible merely by carrying plausible fields.
    """

    plan_path, contract_path, repo_root = Path(plan_path).resolve(), Path(contract_path).resolve(), Path(repo_root).resolve()
    original_path = plan_path.parent / ORIGINAL_CONTRACT_NAME
    owner_v2_path = plan_path.parent / OWNER_V2_CONTRACT_NAME
    plan_sha = sha256_file(plan_path)
    plan = _json(plan_path)
    contract = _json(contract_path)
    if plan.get("experiment_id") != expected_experiment or contract.get("experiment_id") != expected_experiment:
        raise TrainingContractError("training contract experiment drift")
    if contract.get("quota_scope") != QUOTA_SCOPE or contract.get("original_plan_sha256") != plan_sha:
        raise TrainingContractError("training contract plan/quota binding drift")
    quota = (contract.get("minimum_train_winners"), contract.get("maximum_train_winners"))
    if contract_path == original_path:
        if quota != (3000, 5000):
            raise TrainingContractError("original training contract must retain 3000/5000")
        return contract
    if contract_path != owner_v2_path:
        raise TrainingContractError("unapproved training contract path")
    if quota != (1500, 5000) or contract.get("schema_version") != 2:
        raise TrainingContractError("owner v2 contract must retain exactly 1500/5000")
    if not original_path.is_file():
        raise TrainingContractError("owner v2 contract lacks original training contract")
    original_sha = sha256_file(original_path)
    original = _json(original_path)
    if (original.get("experiment_id") != expected_experiment
            or original.get("quota_scope") != QUOTA_SCOPE
            or original.get("original_plan_sha256") != plan_sha
            or (original.get("minimum_train_winners"), original.get("maximum_train_winners")) != (3000, 5000)):
        raise TrainingContractError("owner v2 original training contract binding drift")
    expected_amendment_path = plan_path.parent / OWNER_V2_AMENDMENT_NAME
    try:
        expected_relative = _repo_relative(expected_amendment_path, repo_root)
    except ValueError as exc:
        raise TrainingContractError("owner v2 controls escape repository") from exc
    _exact(contract, {
        "original_training_contract_sha256": original_sha,
        "owner_amendment_path": expected_relative,
        "owner_amendment_sha256": sha256_file(expected_amendment_path) if expected_amendment_path.is_file() else None,
    }, label="owner v2 contract")
    amendment = _json(expected_amendment_path)
    _exact(amendment, {
        "schema_version": 1,
        "experiment_id": expected_experiment,
        "owner_request": OWNER_V2_REQUEST,
        "authorized_minimum_train_winners": 1500,
        "maximum_train_winners": 5000,
        "quota_scope": QUOTA_SCOPE,
        "original_plan_sha256": plan_sha,
        "original_training_contract_sha256": original_sha,
        "only_capacity_changed": True,
        "training_authorized": True,
        "production_eligible": False,
    }, label="owner amendment")
    return contract


def contract_inputs(plan_path: Path, contract_path: Path, *, repo_root: Path) -> list[Path]:
    """Return formal-guard inputs after validating an approved contract."""

    contract = validate_training_contract(plan_path, contract_path, repo_root=repo_root)
    original = Path(plan_path).resolve().parent / ORIGINAL_CONTRACT_NAME
    result = [original, Path(contract_path)]
    if int(contract["minimum_train_winners"]) == 1500:
        result.append(Path(plan_path).resolve().parent / OWNER_V2_AMENDMENT_NAME)
    return result
