from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from yoyo.datasets import ma_morphology_training_package as package


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _manifest_rows(dataset: Path) -> list[dict]:
    return [json.loads(line) for line in (dataset / "manifest.jsonl").read_text().splitlines()]


def _write_dataset_receipt(dataset: Path, rows: list[dict]) -> None:
    manifest_path = dataset / "manifest.jsonl"
    manifest_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    train_counts = Counter(row["sample_kind"] for row in rows if row["split"] == "train")
    _write_json(dataset / "build_receipt.json", {
        "dataset_ready": True,
        "manifest_sha256": _sha(manifest_path),
        "source_commit": "a" * 40,
        "train_positive_images": train_counts["positive"],
        "train_negative_images": train_counts["negative"],
    })


def _write_contract(repo: Path, *, eligible: bool = True, owner_authorized: bool = True) -> None:
    model = repo / "models/yolo11s.pt"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"frozen YOLO11s base checkpoint")
    off = {key: 0.0 for key in package.OFF_KEYS}
    recipe = {
        "inputs": {"base_model": {"path": "models/yolo11s.pt", "sha256": _sha(model)}},
        "training_args": {
            **off,
            "auto_augment": None,
            "batch": 8,
            "cache": False,
            "deterministic": True,
            "device": "0",
            "epochs": 40,
            "imgsz": 1280,
            "lr0": 0.0001,
            "lrf": 0.01,
            "optimizer": "AdamW",
            "patience": 0,
            "plots": False,
            "rect": True,
            "seed": 0,
            "workers": 2,
        },
    }
    recipe_path = repo / package.RECIPE_PLAN
    _write_json(recipe_path, recipe)
    active = {
        "experiment_id": "exp-ma-morphology-v6-threeview-20260925-v1",
        "training_eligible": eligible,
        "owner_authorization": {"training_authorized": owner_authorized},
        "training_recipe_reference": {"family": "YOLO11s", "imgsz": 1280, "epochs": 40, "batch": 8, "seed": 0},
        "inputs": {"parent_plan": {"path": package.RECIPE_PLAN.as_posix(), "sha256": _sha(recipe_path)}},
        "splits": {
            "train_end_exclusive": "2026-01-01T00:00:00Z",
            "validation_end_exclusive": "2026-05-01T00:00:00Z",
            "test_end_exclusive": "2026-09-21T16:00:00Z",
        },
    }
    _write_json(repo / package.ACTIVE_PLAN, active)
    (repo / "experiments/registry.yaml").parent.mkdir(parents=True, exist_ok=True)
    (repo / "experiments/registry.yaml").write_text(
        "experiments:\n  - experiment_id: exp-ma-morphology-v6-threeview-20260925-v1\n"
        f"    training_eligible: {'true' if eligible else 'false'}\n",
        encoding="utf-8",
    )


def _make_dataset(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    dataset = repo / "datasets/ma_launch_owner1500_morph_v6_threeview_ready_20260925_v1"
    for split in package.SPLITS:
        (dataset / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset / "labels" / split).mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    serial = 0

    def add(event: str, split: str, variant: str, kind: str, pool: str, decision: str) -> None:
        nonlocal serial
        serial += 1
        image_rel = f"images/{split}/{event}_{variant}_{serial}.png"
        label_rel = f"labels/{split}/{event}_{variant}_{serial}.txt"
        image = dataset / image_rel
        label = dataset / label_rel
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + f"image-{serial}".encode())
        class_id = 0 if kind == "positive" else None
        label_text = f"{class_id} 0.5 0.5 0.2 0.2\n" if kind == "positive" else ""
        label.write_text(label_text, encoding="utf-8")
        rows.append({
            "event_id": event,
            "cluster_id": event,
            "split": split,
            "variant": variant,
            "class_id": class_id,
            "sample_kind": kind,
            "evaluation_pool": pool,
            "image_path": image_rel,
            "label_path": label_rel,
            "image_sha256": _sha(image),
            "label_sha256": _sha(label),
            "decision_at_utc": decision,
            "visible_start_utc": decision.replace("T12:00:00Z", "T11:00:00Z"),
            "visible_end_close_time_utc": decision,
            "label_horizon_end_utc": decision.replace("T12:00:00Z", "T14:00:00Z"),
        })

    # Training uses every training view; same event is capped at the three
    # contract variants. Empty negative labels are legal and expected.
    for variant in package.VARIANTS:
        add("train-pos", "train", variant, "positive", "reference", "2025-06-01T12:00:00Z")
    add("train-neg", "train", "P9", "negative", "reference", "2025-08-01T12:00:00Z")
    for variant in package.VARIANTS:
        add("train-challenge-neg", "train", variant, "negative", "grade_a_challenge", "2025-09-01T12:00:00Z")
    # Fixed primary evaluation is P9/reference only; P7/P11 are paired views.
    add("val-pos", "val", "P9", "positive", "reference", "2026-02-01T12:00:00Z")
    add("val-pos", "val", "P7", "positive", "reference", "2026-02-01T12:00:00Z")
    add("val-neg", "val", "P9", "negative", "reference", "2026-02-02T12:00:00Z")
    add("test-pos", "test", "P9", "positive", "reference", "2026-06-01T12:00:00Z")
    add("test-pos", "test", "P11", "positive", "reference", "2026-06-01T12:00:00Z")
    add("test-neg", "test", "P9", "negative", "reference", "2026-06-02T12:00:00Z")
    add("challenge-test-neg", "test", "P9", "negative", "grade_a_challenge", "2026-07-01T12:00:00Z")
    _write_dataset_receipt(dataset, rows)
    _write_contract(repo)
    return repo, dataset


def _env() -> dict:
    return {
        "versions": {"torch": "2.8.0+cu124", "torchvision": "0.23.0+cu124", "ultralytics": "8.4.89",
                     "numpy": "2.0.2", "pandas": "2.3.3"},
        "cuda_available": False,
        "cuda_required": False,
    }


def _audit(repo: Path, dataset: Path) -> dict:
    return package.audit_dataset(
        dataset,
        repo_root=repo,
        expected_eval_counts={"val": 2, "test": 2},
    )


def test_preflight_writes_primary_and_challenge_lists_without_mixing_pools(tmp_path: Path) -> None:
    repo, dataset = _make_dataset(tmp_path)
    audit = _audit(repo, dataset)
    assert audit["train_positive_images"] == 3
    assert audit["train_negative_images"] == 4
    assert audit["main_p9_reference_eval_counts"] == {"val": 2, "test": 2}
    assert audit["grade_a_challenge_p9_counts"] == {"test": 1}

    output = dataset / "training_package"
    receipt = package.create_preflight(
        dataset,
        output,
        repo_root=repo,
        env_result=_env(),
        expected_eval_counts={"val": 2, "test": 2},
    )
    assert receipt["dataset_ready"] is True
    assert receipt["training_eligible"] is True
    assert receipt["list_counts"]["train.txt"] == 7
    assert receipt["list_counts"]["val.txt"] == 2
    assert receipt["list_counts"]["test.txt"] == 2
    assert receipt["list_counts"]["challenge_test.txt"] == 1
    train_lines = (output / "train.txt").read_text().splitlines()
    assert len(train_lines) == 7
    assert sum("train-challenge-neg" in line for line in train_lines) == 3
    assert len((output / "val.txt").read_text().splitlines()) == 2
    assert len((output / "test.txt").read_text().splitlines()) == 2
    assert len((output / "challenge_test.txt").read_text().splitlines()) == 1
    assert len((output / "robustness_val_P7_reference.txt").read_text().splitlines()) == 1
    assert len((output / "robustness_test_P11_reference.txt").read_text().splitlines()) == 1
    assert len(receipt["data_yaml_sha256"]) == 64
    assert "challenge_test.txt" not in (output / "data.yaml").read_text()
    assert (output / "preflight_receipt.json").is_file()
    with pytest.raises(package.MorphologyTrainingError, match="overwrite"):
        package.create_preflight(
            dataset, output, repo_root=repo, env_result=_env(), expected_eval_counts={"val": 2, "test": 2}
        )


def test_preflight_fails_on_image_hash_mismatch(tmp_path: Path) -> None:
    repo, dataset = _make_dataset(tmp_path)
    rows = _manifest_rows(dataset)
    rows[0]["image_sha256"] = "0" * 64
    _write_dataset_receipt(dataset, rows)
    with pytest.raises(package.MorphologyTrainingError, match="image SHA mismatch"):
        _audit(repo, dataset)


def test_preflight_fails_on_orphan_positive_label(tmp_path: Path) -> None:
    repo, dataset = _make_dataset(tmp_path)
    (dataset / "labels/train/orphan.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    with pytest.raises(package.MorphologyTrainingError, match="orphan/missing labels"):
        _audit(repo, dataset)


def test_preflight_fails_when_timestamp_is_assigned_to_wrong_split(tmp_path: Path) -> None:
    repo, dataset = _make_dataset(tmp_path)
    rows = _manifest_rows(dataset)
    for row in rows:
        if row["split"] == "val":
            row["decision_at_utc"] = "2025-12-01T12:00:00Z"
            row["visible_start_utc"] = "2025-12-01T11:00:00Z"
            row["visible_end_close_time_utc"] = "2025-12-01T12:00:00Z"
            row["label_horizon_end_utc"] = "2025-12-01T14:00:00Z"
    _write_dataset_receipt(dataset, rows)
    with pytest.raises(package.MorphologyTrainingError, match="another split"):
        _audit(repo, dataset)


@pytest.mark.parametrize("failure", ["visible_start", "safety_horizon"])
def test_preflight_fails_when_visible_or_12h_window_crosses_split(tmp_path: Path, failure: str) -> None:
    repo, dataset = _make_dataset(tmp_path)
    rows = _manifest_rows(dataset)
    for row in rows:
        if row["event_id"] != "val-pos":
            continue
        if failure == "visible_start":
            row["visible_start_utc"] = "2025-12-31T23:00:00Z"
        else:
            row["decision_at_utc"] = "2026-04-30T20:00:00Z"
            row["visible_start_utc"] = "2026-04-30T19:00:00Z"
            row["visible_end_close_time_utc"] = "2026-04-30T20:00:00Z"
            row["label_horizon_end_utc"] = "2026-05-01T08:00:00Z"
    _write_dataset_receipt(dataset, rows)
    message = "visible input starts" if failure == "visible_start" else "12h safety interval"
    with pytest.raises(package.MorphologyTrainingError, match=message):
        _audit(repo, dataset)


def test_preflight_fails_when_event_has_more_than_three_views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, dataset = _make_dataset(tmp_path)
    rows = _manifest_rows(dataset)
    source = next(row for row in rows if row["event_id"] == "train-pos" and row["variant"] == "P11")
    image_rel = "images/train/train-pos_P13.png"
    label_rel = "labels/train/train-pos_P13.txt"
    image, label = dataset / image_rel, dataset / label_rel
    image.write_bytes(b"\x89PNG\r\n\x1a\nextra-view")
    label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    extra = {**source, "variant": "P13", "image_path": image_rel, "label_path": label_rel,
             "image_sha256": _sha(image), "label_sha256": _sha(label)}
    rows.append(extra)
    _write_dataset_receipt(dataset, rows)
    monkeypatch.setattr(package, "VARIANTS", (*package.VARIANTS, "P13"))
    with pytest.raises(package.MorphologyTrainingError, match="more than three views"):
        _audit(repo, dataset)


def test_preflight_fails_when_training_negative_pool_is_empty(tmp_path: Path) -> None:
    repo, dataset = _make_dataset(tmp_path)
    rows = _manifest_rows(dataset)
    removed = [row for row in rows if row["split"] == "train" and row["sample_kind"] == "negative"]
    rows = [row for row in rows if row not in removed]
    for row in removed:
        (dataset / row["image_path"]).unlink()
        (dataset / row["label_path"]).unlink()
    _write_dataset_receipt(dataset, rows)
    with pytest.raises(package.MorphologyTrainingError, match="both positive and negative"):
        _audit(repo, dataset)


def test_package_and_environment_recipe_stays_frozen(tmp_path: Path) -> None:
    repo, _ = _make_dataset(tmp_path)
    contract = package._load_training_contract(repo)
    args = contract["training_args"]
    assert (args["imgsz"], args["epochs"], args["batch"], args["seed"]) == (1280, 40, 8, 0)
    assert all(args[key] == 0 for key in package.OFF_KEYS)
    assert contract["base_model_sha256"] == _sha(repo / "models/yolo11s.pt")


def test_environment_ignores_torch_cuda_build_suffix_but_requires_gpu_for_train(tmp_path: Path) -> None:
    constraints = tmp_path / "constraints-ci.txt"
    constraints.write_text(
        "torch==2.8.0\ntorchvision==0.23.0\nultralytics==8.4.89\nnumpy==2.0.2\npandas==2.3.3\n",
        encoding="utf-8",
    )
    modules = {
        "torch": SimpleNamespace(__version__="2.8.0+cu126", cuda=SimpleNamespace(is_available=lambda: False)),
        "torchvision": SimpleNamespace(__version__="0.23.0+cu126"),
        "ultralytics": SimpleNamespace(__version__="8.4.89"),
        "numpy": SimpleNamespace(__version__="2.0.2"),
        "pandas": SimpleNamespace(__version__="2.3.3"),
    }
    result = package.check_environment(importer=modules.__getitem__, constraints_path=constraints)
    assert result["versions"]["torch"] == "2.8.0+cu126"
    with pytest.raises(package.MorphologyTrainingError, match="requires CUDA"):
        package.check_environment(cuda_required=True, importer=modules.__getitem__, constraints_path=constraints)
