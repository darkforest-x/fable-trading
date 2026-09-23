"""Read-only model inventory, identity audit, and annotation behavior."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from yoyo.research_workspace.catalog import Catalog
from yoyo.research_workspace.model_catalog import ModelCatalog
from yoyo.research_workspace.store import WorkspaceStore


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root(tmp_path: Path) -> tuple[Path, list[dict], list[dict]]:
    root = tmp_path / "repo"
    for relative in (
        "experiments/active/exp-shared/results",
        "experiments/active/exp-yoyo-eth-semantic-mvp",
        "artifacts",
        "models",
        "analysis/output/l2/models",
        "yoyo/layers/l1_detection/numeric_baseline",
        "yoyo/vision_research",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    long_model = root / "analysis/output/l2/models/long.txt"
    short_model = root / "analysis/output/l2/models/short.txt"
    long_model.write_text("tree\nfeature_names=long_feature\n", encoding="utf-8")
    short_model.write_text("tree\nfeature_names=short_feature\n", encoding="utf-8")
    frozen_model = root / "models/frozen_base.txt"
    frozen_model.write_text("tree\nfeature_names=frozen_a frozen_b\n", encoding="utf-8")
    (root / "models/detector.pt").write_bytes(b"opaque checkpoint bytes")

    frozen_meta = {
        "artifact_version": 1,
        "config": "fixture_frozen",
        "objective": "regression",
        "model_path": "models/frozen_base.txt",
        "feature_columns": ["frozen_a", "frozen_b"],
        "splits": {
            "train": {"range": ["2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"]},
            "val": {"range": ["2024-01-11T00:00:00Z", "2024-01-14T00:00:00Z"]},
        },
    }
    (root / "models/frozen_base.json").write_text(json.dumps(frozen_meta), encoding="utf-8")

    shared_receipt = root / "experiments/active/exp-shared/results/shared_training_receipt.json"
    shared_receipt.write_text(json.dumps({
        "objective": "must_not_be_applied_to_either_model",
        "feature_columns": ["wrong_shared_feature"],
        "feature_semantics": "unbound generic receipt",
    }), encoding="utf-8")
    long_receipt = root / "experiments/active/exp-shared/results/long_training_receipt.json"
    long_receipt.write_text(json.dumps({
        "model_path": "analysis/output/l2/models/long.txt",
        "objective": "long-only objective",
        "feature_columns": ["long_feature"],
        "feature_semantics": "long model semantics",
        "splits": {
            "train": {"range": ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"]},
            "val": {"range": ["2024-02-02T00:00:00Z", "2024-02-09T00:00:00Z"]},
        },
    }), encoding="utf-8")
    short_receipt = root / "experiments/active/exp-shared/results/short_training_receipt.json"
    short_receipt.write_text(json.dumps({
        "model_sha256": _sha(short_model),
        "objective": "short-only objective",
        "feature_columns": ["short_feature"],
        "feature_semantics": "short model semantics",
    }), encoding="utf-8")

    feature_source = root / "yoyo/layers/l1_detection/numeric_baseline/features.py"
    feature_source.write_text(
        "FEATURE_COLUMNS = ['compression', 'slope']\n"
        '"""All fields use data available by the decision bar."""\n', encoding="utf-8"
    )
    (root / "yoyo/layers/l1_detection/numeric_baseline/train.py").write_text(
        "# research-only baseline\n", encoding="utf-8"
    )

    artifacts = [
        {
            "artifact_id": "detector-weight", "artifact_type": "weights", "role": "pattern_teacher",
            "source_path": "models/detector.pt", "sha256": "0" * 64, "training_eligible": True,
            "production_eligible": True,
        },
        {
            "artifact_id": "l2-long", "artifact_type": "model", "role": "rejected_research_long_regressor",
            "source_path": "analysis/output/l2/models/long.txt", "sha256": _sha(long_model),
            "training_eligible": False, "production_eligible": False,
        },
        {
            "artifact_id": "l2-short", "artifact_type": "model", "role": "rejected_research_short_regressor",
            "source_path": "analysis/output/l2/models/short.txt", "sha256": _sha(short_model),
            "training_eligible": False, "production_eligible": False,
        },
        {
            "artifact_id": "shared-receipt", "artifact_type": "manifest", "role": "training_receipt",
            "source_path": "experiments/active/exp-shared/results/shared_training_receipt.json",
        },
        {
            "artifact_id": "long-receipt", "artifact_type": "manifest", "role": "training_receipt",
            "source_path": "experiments/active/exp-shared/results/long_training_receipt.json",
        },
        {
            "artifact_id": "short-receipt", "artifact_type": "manifest", "role": "training_receipt",
            "source_path": "experiments/active/exp-shared/results/short_training_receipt.json",
        },
        {
            "artifact_id": "numeric-report", "artifact_type": "report", "role": "historical_negative_report",
            "source_path": "experiments/active/exp-yoyo-eth-semantic-mvp/report.md",
        },
    ]
    experiments = [
        {
            "experiment_id": "exp-shared", "status": "rejected", "question": "two-model fixture",
            "workflow": "yolo", "artifacts": ["detector-weight", "l2-long", "l2-short", "shared-receipt", "long-receipt", "short-receipt"],
        },
        {
            "experiment_id": "exp-yoyo-eth-semantic-mvp", "status": "rejected",
            "question": "Numeric baseline fixture", "artifacts": ["numeric-report"],
        },
    ]
    (root / "experiments/registry.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "experiments": experiments}, sort_keys=False), encoding="utf-8"
    )
    (root / "artifacts/registry.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "artifacts": artifacts}, sort_keys=False), encoding="utf-8"
    )
    return root, experiments, artifacts


@pytest.fixture
def workspace(tmp_path):
    root, _, _ = _root(tmp_path)
    store = WorkspaceStore(tmp_path / "runtime")
    catalog = Catalog(root)
    return root, catalog, store, ModelCatalog(root, catalog, store)


def test_overview_lists_registered_and_capability_families_without_hashing_models(workspace, monkeypatch):
    _, _, _, models = workspace
    from yoyo.research_workspace import model_catalog as module

    original_hash = module._sha256_file

    def deny_text_hash(path, max_bytes):
        if path.suffix == ".txt" or path.suffix == ".pt":
            raise AssertionError("overview must not hash model weights")
        return original_hash(path, max_bytes)

    monkeypatch.setattr(module, "_sha256_file", deny_text_hash)
    monkeypatch.setattr(ModelCatalog, "_text_info", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("overview must not run the explicit model audit reader")
    ))
    result = models.overview()
    items = {item["id"]: item for item in result["items"]}

    assert {family["id"] for family in result["families"]} == {
        "yolo_detector", "l2_frozen", "l2_research", "numeric_baseline", "vision_review"
    }
    assert items["detector-weight"]["declared_eligibility"][0]["production_eligible"] is True
    assert items["detector-weight"]["production_eligible"] is False
    assert items["detector-weight"]["local_exists"] is True
    assert items["l2-long"]["feature_count"] == 1
    assert items["l2-short"]["feature_count"] == 1
    assert items["frozen:frozen_base"]["family"] == "l2_frozen"
    assert items["frozen:frozen_base"]["feature_count"] == 2
    assert items["numeric-baseline-family"]["feature_count"] == 2
    assert items["numeric-baseline-family"]["artifact_ids"] == ["numeric-report"]
    assert "VLM" in items["vision-review-capability"]["name"]
    assert all(item["training_eligible"] is False and item["production_eligible"] is False for item in items.values())
    assert all(family["view"] in {"models", "yolo", "vision"} for family in result["families"])
    assert result["gates"]["training"]["allowed"] is False
    assert result["gates"]["production"]["allowed"] is False


def test_registered_metadata_is_bound_to_exact_model_not_shared_experiment(workspace):
    _, _, _, models = workspace
    overview = models.overview()
    items = {item["id"]: item for item in overview["items"]}

    assert items["l2-long"]["metadata_path"].endswith("long_training_receipt.json")
    assert items["l2-long"]["objective"] == "long-only objective"
    assert items["l2-long"]["feature_semantics"] == "long model semantics"
    assert items["l2-long"]["feature_count"] == 1
    assert items["l2-short"]["metadata_path"].endswith("short_training_receipt.json")
    assert items["l2-short"]["objective"] == "short-only objective"
    assert items["l2-short"]["feature_semantics"] == "short model semantics"
    assert "shared_training_receipt.json" not in (items["l2-long"]["metadata_path"] or "")
    assert "shared_training_receipt.json" not in (items["l2-short"]["metadata_path"] or "")


def test_audit_hashes_small_text_model_persists_and_marks_stale(workspace):
    root, _, store, models = workspace
    result = models.audit("l2-long")
    by_name = {check["name"]: check for check in result["checks"]}

    assert by_name["text_model_sha256"]["status"] == "pass"
    assert by_name["feature_names"]["status"] == "pass"
    assert by_name["feature_semantics"]["status"] == "pass"
    assert by_name["split_chronology"]["status"] == "pass"
    assert result["revision"] == 1
    assert store.history("model_audit", "l2-long")[0]["fingerprint"] == result["fingerprint"]
    assert models.get("l2-long")["audit"]["stale"] is False

    model_path = root / "analysis/output/l2/models/long.txt"
    model_path.write_text("tree\nfeature_names=long_feature\nchanged=true\n", encoding="utf-8")
    assert models.get("l2-long")["audit"]["stale"] is True
    assert models.get("l2-long")["audit"]["status"] == "stale"


def test_annotation_uses_optimistic_revision_and_cannot_grant_eligibility(workspace):
    _, _, store, models = workspace
    updated = models.annotate("l2-long", "archived", "Keep as historical comparison.", 0)
    assert updated["status"] == "archived"
    assert updated["revision"] == 1
    assert updated["annotation_notes"] == "Keep as historical comparison."
    assert updated["training_eligible"] is False
    assert updated["production_eligible"] is False
    assert len(store.history("model_annotation", "l2-long")) == 1

    with pytest.raises(ValueError, match="另一页面更新"):
        models.annotate("l2-long", "research", "stale write", 0)
    with pytest.raises(ValueError, match="stage"):
        models.annotate("l2-long", "active", "not allowed", 1)
    with pytest.raises(KeyError):
        models.annotate("unknown-model", "research", "no", 0)


def test_yolo_weight_audit_never_reads_or_rehashes_binary_checkpoint(workspace, monkeypatch):
    root, _, _, models = workspace
    original_open = Path.open

    def deny_checkpoint_open(path, *args, **kwargs):
        if path.suffix == ".pt":
            raise AssertionError("binary weight must not be opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_checkpoint_open)
    audit = models.audit("detector-weight")
    check = next(row for row in audit["checks"] if row["name"] == "weight_sha256")
    assert check["status"] == "not_rehashed"
    assert models.get("detector-weight")["local_exists"] is True


def test_changed_registry_digest_or_feature_source_invalidates_audit(workspace):
    root, _, _, models = workspace
    models.audit("l2-long")
    registry = root / "artifacts/registry.yaml"
    data = yaml.safe_load(registry.read_text())
    next(x for x in data["artifacts"] if x["artifact_id"] == "l2-long")["sha256"] = "f" * 64
    registry.write_text(yaml.safe_dump(data, sort_keys=False))
    assert models.get("l2-long")["audit"]["stale"] is True
    models.audit("numeric-baseline-family")
    features = root / "yoyo/layers/l1_detection/numeric_baseline/features.py"
    features.write_text("FEATURE_COLUMNS = ['changed']\n")
    assert models.get("numeric-baseline-family")["audit"]["stale"] is True


def test_symlink_escape_model_is_not_exposed_or_read(tmp_path):
    root, _, _ = _root(tmp_path)
    outside = tmp_path / "outside.pt"
    outside.write_bytes(b"secret external weights")
    try:
        (root / "models/escape.pt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    raw = yaml.safe_load((root / "artifacts/registry.yaml").read_text(encoding="utf-8"))
    raw["artifacts"].append({
        "artifact_id": "escape-weight", "artifact_type": "weights", "role": "pattern_teacher",
        "source_path": "models/escape.pt", "sha256": "f" * 64,
        "training_eligible": False, "production_eligible": False,
    })
    (root / "artifacts/registry.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    store = WorkspaceStore(tmp_path / "runtime")
    models = ModelCatalog(root, Catalog(root), store)

    item = models.get("escape-weight")
    assert item["source_path"] is None
    assert item["local_exists"] is False
    assert models.audit("escape-weight")["status"] == "incomplete"
