"""YOLO lifecycle browsing must preserve contradictory records and provenance."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from yoyo.research_workspace.yolo import YoloCatalog, overview, workflow_stages


@pytest.fixture
def workspace(tmp_path):
    experiments = [
        dict(experiment_id="exp-yolo-training-v1", status="active", question="Train a causal dataset",
             result="Training was running when registered", artifacts=["fixture-dataset"],
             training_eligible=False, production_eligible=False),
        dict(experiment_id="exp-plain-v1", status="rejected", question="An unrelated study", artifacts=[]),
        dict(experiment_id="exp-custom-v1", status="active", workflow="yolo", question="Review labels", artifacts=[]),
    ]
    (tmp_path / "experiments").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "experiments/registry.yaml").write_text(yaml.safe_dump(dict(experiments=experiments)))
    (tmp_path / "artifacts/registry.yaml").write_text(yaml.safe_dump(dict(artifacts=[dict(
        artifact_id="fixture-dataset", artifact_type="dataset", source_path="datasets/missing/manifest.jsonl",
        training_eligible=False, production_eligible=False)])))
    folder = tmp_path / "experiments/active/exp-yolo-training-v1"
    (folder / "collected/training/arm_A").mkdir(parents=True)
    (folder / "collected/job_receipt.json").write_text('{"status":"completed"}')
    (folder / "collected/training/arm_A/results.csv").write_text("epoch,metrics/mAP50(B)\n1,0.2\n2,0.3\n")
    (folder / "results_summary.json").write_text(json.dumps(dict(status="completed", arms={"A": dict(
        best_path="experiments/active/exp-yolo-training-v1/collected/training/arm_A/weights/best.pt",
        best_sha256="a"*64, epochs=2)})))
    return YoloCatalog(tmp_path), folder


def test_workflow_does_not_infer_completion_from_stage_membership():
    record = dict(experiment_id="exp-yolo-dataset-train-econ", status="rejected")
    assert set(workflow_stages(record)) == {"dataset", "training", "economic"}
    assert not workflow_stages(dict(experiment_id="exp-rsi-clock"))
    assert not workflow_stages(dict(experiment_id="exp-spike-v8-total2-1h-native-v1"))
    assert not workflow_stages(dict(experiment_id="exp-spike-selected-counts-v1",
                                    question="29 owner-selected symbols with box-any trades"))


def test_overview_keeps_registry_and_receipt_status_separate(workspace):
    catalog, folder = workspace
    before = (catalog.root / "experiments/registry.yaml").read_bytes()
    result = overview(catalog)
    assert result["summary"] == {"experiments": 2, "artifacts": 1}
    item = next(x for x in result["items"] if x["id"] == "exp-yolo-training-v1")
    assert item["status"] == "active"
    assert item["receipt_status"]["status"] == "completed"
    assert item["production_eligible"] is False
    assert result["models"][0]["registered"] is False
    assert result["models"][0]["local_exists"] is False
    assert result["pointers"][0]["exists"] is False
    assert (catalog.root / "experiments/registry.yaml").read_bytes() == before


def test_evidence_reads_training_and_redacts_unsafe_files(workspace, tmp_path):
    catalog, folder = workspace
    (folder / "results").mkdir()
    (folder / "results/manifest.json").write_text('{"images":100,"api_key":"do-not-display"}')
    (folder / "results/settings.json").write_text('{"x":"do-not-display"}')
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text('{"x":"do-not-display"}')
    (folder / "results/escaped_receipt.json").symlink_to(outside)
    result = catalog.evidence("exp-yolo-training-v1")
    assert len(result["tables"]) == 1
    run = result["yolo_evidence"]["training_runs"][0]
    assert run["last_epoch"] == "2" and run["metrics"]["metrics/mAP50(B)"] == "0.3"
    dataset = result["yolo_evidence"]["artifacts"][0]
    assert dataset["local_exists"] is False
    assert not dataset["production_eligible"]
    assert "do-not-display" not in json.dumps(result)
    manifest = next(x for x in result["files"] if x["path"].endswith("manifest.json"))
    assert manifest["downloadable"] is False


def test_non_yolo_and_unrecognized_directory_stay_out_of_scope(workspace):
    catalog, folder = workspace
    unrelated = catalog.root / "experiments/active/exp-plain-v1/results"
    unrelated.mkdir(parents=True)
    (unrelated / "metrics.csv").write_text("x\n1\n")
    assert not catalog.evidence("exp-plain-v1")["tables"]
    (folder / "datasets").mkdir()
    (folder / "datasets/metrics.csv").write_text("x\n1\n")
    assert all("datasets" not in x["path"] for x in catalog.evidence("exp-yolo-training-v1")["files"])


def test_runtime_snapshot_is_labeled_without_loading_model(workspace):
    catalog, _ = workspace
    result = overview(catalog, dict(snapshot_at_ms=0, stale=True, runtime=dict(model_gate=dict(
        status="ready", loaded=True, profile_id="notification-profile", model_sha256="a"*64))))
    card = result["pointers"][0]
    assert card["status"] == "ready"
    assert card["source_time"] == "1970-01-01T00:00:00+00:00"
    assert "已过期" in card["note"] and "仅通知" in card["note"]
    assert card["target"].startswith("notification-profile")
    assert all(not x["production_eligible"] for x in result["items"])


def test_model_registration_matches_version_hash_not_just_path(workspace):
    catalog, folder = workspace
    path = json.loads((folder / "results_summary.json").read_text())["arms"]["A"]["best_path"]
    registry = catalog.root / "artifacts/registry.yaml"
    original = yaml.safe_load(registry.read_text())
    original["artifacts"].append(dict(artifact_id="different-version", artifact_type="weights",
                                      source_path=path, sha256="b" * 64))
    registry.write_text(yaml.safe_dump(original))
    assert overview(catalog)["models"][0]["registered"] is False
    original["artifacts"][-1]["sha256"] = "a" * 64
    registry.write_text(yaml.safe_dump(original))
    assert overview(catalog)["models"][0]["registered"] is True
