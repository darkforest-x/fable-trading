"""Append completed workspace evidence to the repository artifact registry.

Registration records identity and provenance only. A completed workspace job
is not a statistical validation, training approval, or production approval.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import yaml

from yoyo.contracts.artifacts import ArtifactRecord


SOURCE_REPO = "darkforest-x/fable-trading"
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,180}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法确定工作区产物的 source_commit。") from error
    commit = result.stdout.strip()
    if not commit:
        raise ValueError("git HEAD 为空，无法登记工作区产物。")
    return commit


def _source_commit(root: Path, output: Path) -> str:
    provenance = output / "provenance.json"
    if provenance.exists() or provenance.is_symlink():
        resolved = provenance.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("provenance.json 不可读取或逃逸仓库目录。")
        try:
            value = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("provenance.json 无法解析。") from error
        if isinstance(value, dict) and isinstance(value.get("commit"), str) and value["commit"].strip():
            return value["commit"].strip()
    return _git_head(root)


def _append_bytes(original: bytes, record: dict) -> bytes:
    parsed = yaml.safe_load(original)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("artifacts"), list):
        raise ValueError("artifacts/registry.yaml 必须包含 artifacts 列表，未写入。")
    # An inline `artifacts: []` cannot be extended as valid YAML without
    # rewriting its original bytes. Require the repository's block-list form.
    if re.search(rb"(?m)^artifacts:\s*\[\s*\]\s*(?:#.*)?$", original):
        raise ValueError("artifacts 列表需使用块列表格式，才能逐字节保留旧注册表。")
    rows = parsed["artifacts"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("artifacts 列表包含非对象记录，未写入。")

    item = yaml.safe_dump([record], allow_unicode=True, sort_keys=False).rstrip().splitlines()
    addition = b"\n\n" + b"\n".join(("  " + line).encode("utf-8") for line in item) + b"\n"
    updated = original + addition
    reparsed = yaml.safe_load(updated)
    if not isinstance(reparsed, dict) or not isinstance(reparsed.get("artifacts"), list):
        raise ValueError("追加后的 artifacts 注册表结构不正确，未写入。")
    if len(reparsed["artifacts"]) != len(rows) + 1 or reparsed["artifacts"][-1].get("artifact_id") != record["artifact_id"]:
        raise ValueError("追加后的 artifacts 注册表未保留原有条目，未写入。")
    ArtifactRecord.from_mapping(reparsed["artifacts"][-1])
    return updated


def register_result(root: Path, job: dict) -> dict:
    """Idempotently register a completed job's result.json.

    The YAML registry is append-only here: its existing bytes are kept intact,
    the new row is contract-validated, and a concurrent external edit aborts.
    """
    root = Path(root).resolve(strict=True)
    if not isinstance(job, dict):
        raise ValueError("job 必须是对象。")
    job_id = job.get("id")
    experiment_id = job.get("experiment_id")
    recipe = job.get("recipe")
    if not isinstance(job_id, str) or not _SAFE_JOB_ID.fullmatch(job_id):
        raise ValueError("job id 格式不正确。")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("job experiment_id 缺失。")
    if not isinstance(recipe, str) or not recipe.strip():
        raise ValueError("job recipe 缺失。")
    if "output" not in job:
        raise ValueError("job output 缺失。")

    output = Path(job["output"])
    if not output.is_absolute():
        output = root / output
    try:
        output = output.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("job output 不存在或路径无效。") from error
    if not output.is_relative_to(root) or not output.is_dir():
        raise ValueError("job output 必须是仓库内的目录。")

    result_link = output / "result.json"
    try:
        result_path = result_link.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("result.json 不存在或路径无效。") from error
    if not result_path.is_relative_to(root) or not result_path.is_file():
        raise ValueError("result.json 必须是仓库内的普通文件。")
    sha256 = _sha256(result_path)
    size_bytes = result_path.stat().st_size
    source_path = result_path.relative_to(root).as_posix()
    artifact_id = f"workspace-run-{job_id}"

    registry = root / "artifacts" / "registry.yaml"
    if registry.is_symlink():
        raise ValueError("artifacts/registry.yaml 不可为符号链接。")
    registry = registry.resolve(strict=True)
    if not registry.is_relative_to(root) or not registry.is_file():
        raise ValueError("artifacts/registry.yaml 不可读取或逃逸仓库目录。")
    lock_path = registry.parent / ".workspace-artifact-registry.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise ValueError("无法安全锁定 artifacts 注册表。") from error

    with os.fdopen(lock_fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = registry.read_bytes()
        parsed = yaml.safe_load(original)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("artifacts"), list):
            raise ValueError("artifacts/registry.yaml 必须包含 artifacts 列表，未写入。")
        rows = parsed["artifacts"]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("artifacts 列表包含非对象记录，未写入。")
        matches = [row for row in rows if row.get("artifact_id") == artifact_id]
        if len(matches) > 1:
            raise ValueError(f"artifact_id {artifact_id} 在注册表中重复。")
        if matches:
            existing = matches[0]
            if existing.get("source_path") != source_path or existing.get("sha256") != sha256:
                raise ValueError(f"artifact_id {artifact_id} 已登记为不同路径或哈希，拒绝覆盖。")
            ArtifactRecord.from_mapping(existing)
            return dict(existing)

        record = {
            "artifact_id": artifact_id,
            "artifact_type": "manifest",
            "role": "offline_workspace_run",
            "source_repo": SOURCE_REPO,
            "source_commit": _source_commit(root, output),
            "source_path": source_path,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "storage_uri": f"repo://{source_path}",
            "holdout_status": "unknown",
            "training_eligible": False,
            "production_eligible": False,
            "notes": (
                f"workspace experiment_id={experiment_id}; recipe={recipe}. "
                "流程完成只表示任务运行结束，不代表研究验证、统计结论或资格变更。"
            ),
        }
        ArtifactRecord.from_mapping(record)
        updated = _append_bytes(original, record)
        # The job output is immutable by convention, but verify its identity
        # again before publishing the registry pointer.
        if _sha256(result_path) != sha256 or result_path.stat().st_size != size_bytes:
            raise ValueError("result.json 在登记期间发生变化，未写入。")
        if registry.read_bytes() != original:
            raise ValueError("artifacts 注册表同时有更新，请刷新后重试。")

        temporary = registry.with_name(f".workspace-artifacts-{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            if registry.read_bytes() != original:
                raise ValueError("artifacts 注册表同时有更新，请刷新后重试。")
            os.replace(temporary, registry)
        finally:
            temporary.unlink(missing_ok=True)
        return record
