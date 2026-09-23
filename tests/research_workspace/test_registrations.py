"""Safe artifact registry appends for completed research workspace jobs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from yoyo.contracts.artifacts import ArtifactRecord
from yoyo.research_workspace.registrations import register_result


COMMIT = "a" * 40


def _workspace(tmp_path: Path):
    root = tmp_path / "repo"
    registry_dir = root / "artifacts"
    registry_dir.mkdir(parents=True)
    original = (
        b"# historical registry prefix must remain byte-for-byte\n"
        b"schema_version: 1\n"
        b"artifacts:\n"
        b"  - artifact_id: existing-artifact\n"
        b"    artifact_type: config\n"
        b"    role: fixture\n"
        b"    source_repo: darkforest-x/fable-trading\n"
        b"    source_commit: old-commit\n"
        b"    source_path: config/old.yaml\n"
        b"    holdout_status: unknown\n"
        b"    training_eligible: false\n"
        b"    production_eligible: false\n"
        b"# preserve this terminal comment and trailing spaces  \n"
    )
    (registry_dir / "registry.yaml").write_bytes(original)
    output = root / "experiments" / "active" / "exp-fixture" / "workspace_runs" / "job-123"
    output.mkdir(parents=True)
    result_bytes = json.dumps({"tables": [], "notes": ["completed only"]}, ensure_ascii=False).encode("utf-8")
    (output / "result.json").write_bytes(result_bytes)
    (output / "provenance.json").write_text(json.dumps({"commit": COMMIT}), encoding="utf-8")
    job = {
        "id": "job-123",
        "experiment_id": "exp-fixture",
        "output": str(output),
        "recipe": "verify-evidence",
    }
    return root, output, original, result_bytes, job


def test_register_result_is_idempotent_and_preserves_registry_prefix(tmp_path):
    root, _, original, result_bytes, job = _workspace(tmp_path)
    registry = root / "artifacts" / "registry.yaml"

    record = register_result(root, job)
    after_first = registry.read_bytes()
    repeated = register_result(root, job)

    assert after_first.startswith(original)
    assert registry.read_bytes() == after_first
    assert repeated == record
    assert record["artifact_id"] == "workspace-run-job-123"
    assert record["artifact_type"] == "manifest"
    assert record["role"] == "offline_workspace_run"
    assert record["source_commit"] == COMMIT
    assert record["source_path"] == "experiments/active/exp-fixture/workspace_runs/job-123/result.json"
    assert record["sha256"] == hashlib.sha256(result_bytes).hexdigest()
    assert record["size_bytes"] == len(result_bytes)
    assert record["storage_uri"] == f"repo://{record['source_path']}"
    assert record["holdout_status"] == "unknown"
    assert record["training_eligible"] is False
    assert record["production_eligible"] is False
    assert "exp-fixture" in record["notes"] and "verify-evidence" in record["notes"]
    assert "不代表研究验证" in record["notes"]
    parsed = yaml.safe_load(after_first)
    assert [item["artifact_id"] for item in parsed["artifacts"]] == [
        "existing-artifact", "workspace-run-job-123"
    ]
    assert ArtifactRecord.from_mapping(record).artifact_id == record["artifact_id"]


def test_register_result_refuses_same_id_with_changed_hash_without_write(tmp_path):
    root, output, _, _, job = _workspace(tmp_path)
    registry = root / "artifacts" / "registry.yaml"
    register_result(root, job)
    before = registry.read_bytes()
    (output / "result.json").write_text('{"tables":[{"rows":1}]}', encoding="utf-8")

    with pytest.raises(ValueError, match="不同路径或哈希"):
        register_result(root, job)
    assert registry.read_bytes() == before


def test_register_result_rejects_output_directory_outside_root(tmp_path):
    root, _, original, _, job = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text("{}", encoding="utf-8")
    job["output"] = str(outside)

    with pytest.raises(ValueError, match="仓库内的目录"):
        register_result(root, job)
    assert (root / "artifacts" / "registry.yaml").read_bytes() == original


def test_register_result_rejects_result_symlink_escaping_root(tmp_path):
    root, output, original, _, job = _workspace(tmp_path)
    outside = tmp_path / "outside-result.json"
    outside.write_text("{}", encoding="utf-8")
    (output / "result.json").unlink()
    try:
        (output / "result.json").symlink_to(outside)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="仓库内的普通文件"):
        register_result(root, job)
    assert (root / "artifacts" / "registry.yaml").read_bytes() == original


def test_register_result_falls_back_to_git_head_when_provenance_has_no_commit(tmp_path, monkeypatch):
    root, output, _, _, job = _workspace(tmp_path)
    (output / "provenance.json").write_text(json.dumps({"command": ["fixed"]}), encoding="utf-8")
    monkeypatch.setattr("yoyo.research_workspace.registrations._git_head", lambda _: "fallback-head")

    record = register_result(root, job)

    assert record["source_commit"] == "fallback-head"


def test_register_result_requires_block_artifact_list_to_keep_bytes(tmp_path):
    root, _, _, _, job = _workspace(tmp_path)
    registry = root / "artifacts" / "registry.yaml"
    registry.write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")
    before = registry.read_bytes()

    with pytest.raises(ValueError, match="块列表格式"):
        register_result(root, job)
    assert registry.read_bytes() == before
