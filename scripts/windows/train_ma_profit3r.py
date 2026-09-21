r"""Fail-closed Windows RTX 3060 runner for the preregistered MA-profit arms.

The command audits staged pixels, labels, manifests, selection controls and the
CUDA environment before it can train.  It never promotes a checkpoint.  Run
without ``--train`` to write only the preflight receipt; ``--train`` then runs
the A and B recipes sequentially from the same immutable YOLO11s weight.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
ARMS = ("A", "B")
SAFE_AUG = {
    "fliplr": 0.0, "flipud": 0.0, "mosaic": 0.0, "mixup": 0.0,
    "copy_paste": 0.0, "hsv_h": 0.0, "hsv_s": 0.0, "hsv_v": 0.0,
    "translate": 0.0, "scale": 0.0, "degrees": 0.0, "shear": 0.0,
    "perspective": 0.0, "erasing": 0.0, "auto_augment": None,
    "bgr": 0.0, "cutmix": 0.0,
}
TRAIN_ARGS = {
    "epochs": 40, "imgsz": 1280, "batch": 8, "optimizer": "AdamW",
    "lr0": 1e-4, "lrf": 0.01, "seed": 0, "deterministic": True,
    "patience": 0, "workers": 2, "device": "0", "rect": True,
    "plots": False, "cache": False, "multi_scale": 0.0,
}


class ProfitTrainingError(RuntimeError):
    """Raised when a copied MA-profit cohort is not safe to train."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProfitTrainingError(f"JSON object required: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ProfitTrainingError(f"manifest line {line_number} is not an object")
        result.append(value)
    return result


def _relative(root: Path, raw: object, label: str) -> Path:
    path = Path(str(raw))
    if path.is_absolute() or ".." in path.parts:
        raise ProfitTrainingError(f"{label} escapes dataset root: {raw!r}")
    return root / path


def _read_list(path: Path) -> list[str]:
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise ProfitTrainingError(f"duplicate image in list: {path}")
    if any(not item.startswith("./images/") for item in values):
        raise ProfitTrainingError(f"list paths must be root-relative ./images paths: {path}")
    return values


def write_windows_yaml(dataset_root: Path, arm: str, remote_dataset_root: str) -> Path:
    """Rewrite only the dataset root; copied list entries remain portable.

    ``train_*.txt``/``val.txt``/``test.txt`` contain ``./images/...`` entries,
    so they resolve against their copied list files after the remote root below
    is rewritten for a different Windows staging directory.
    """

    if arm not in ARMS:
        raise ProfitTrainingError(f"unknown arm: {arm}")
    names = ("profitable_dense_long", "profitable_dense_short")
    output = dataset_root / f"data_{arm}.windows.yaml"
    remote_dataset_root = remote_dataset_root.replace("\\", "/")
    output.write_text(
        f"path: {remote_dataset_root}\n"
        f"train: train_{arm}.txt\nval: val.txt\ntest: test.txt\nnc: 2\n"
        f"names: [{names[0]}, {names[1]}]\n",
        encoding="utf-8",
    )
    return output


def _receipt_events(receipt: Mapping[str, Any]) -> dict[str, tuple[str, str, str, str, str]]:
    raw_events = receipt.get("events")
    if not isinstance(raw_events, list):
        raise ProfitTrainingError("cohort receipt requires events list")
    result: dict[str, tuple[str, str, str, str, str]] = {}
    clusters: dict[str, str] = {}
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            raise ProfitTrainingError("cohort receipt events must be objects")
        event_id, cluster_id, split, canonical_asset, direction, core_end_time = (
            str(raw.get(key, "")) for key in ("event_id", "cluster_id", "split", "canonical_asset", "direction", "core_end_time")
        )
        if not event_id or not cluster_id or split not in {"train", "val", "test"} or not canonical_asset or direction not in {"LONG", "SHORT"} or not core_end_time:
            raise ProfitTrainingError("cohort receipt event requires identity, cluster, canonical asset, direction, core time and split")
        if event_id in result:
            raise ProfitTrainingError(f"duplicate receipt event_id: {event_id}")
        if cluster_id in clusters:
            raise ProfitTrainingError(f"cluster contains multiple selected events: {cluster_id}")
        result[event_id] = (cluster_id, split, canonical_asset, direction, core_end_time)
        clusters[cluster_id] = event_id
    return result


def _validate_pixels_and_label(root: Path, row: Mapping[str, Any]) -> None:
    """Check actual decodable pixels and YOLO geometry, not only file hashes."""

    import cv2
    import numpy as np

    pixels = cv2.imdecode(np.frombuffer((root / str(row["image_path"])).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if pixels is None or pixels.shape != (742, 1280, 3):
        raise ProfitTrainingError("unreadable image or unexpected 1280x742 geometry")
    label = (root / str(row["label_path"])).read_text(encoding="utf-8").strip()
    expected_class = row.get("class_id")
    if expected_class is None:
        if label or row["split"] == "train":
            raise ProfitTrainingError("negative label or training-positive semantics mismatch")
        return
    try:
        expected_class = int(expected_class)
    except (TypeError, ValueError) as exc:
        raise ProfitTrainingError("invalid manifest class") from exc
    parts, box = label.split(), row.get("box")
    if not isinstance(box, Mapping) or len(parts) != 5 or parts[0] not in {"0", "1"} or int(parts[0]) != expected_class:
        raise ProfitTrainingError("YOLO class or label row mismatch")
    try:
        cx, cy, width, height = map(float, parts[1:])
    except ValueError as exc:
        raise ProfitTrainingError("invalid YOLO geometry") from exc
    if not all(map(math.isfinite, (cx, cy, width, height))) or width <= 0 or height <= 0:
        raise ProfitTrainingError("invalid YOLO geometry")
    if min(cx-width/2, cy-height/2) < -1e-7 or max(cx+width/2, cy+height/2) > 1+1e-7:
        raise ProfitTrainingError("YOLO box leaves image")
    if any(abs(value-float(box.get(key, float("inf")))) > 1e-7 for key, value in zip(("cx_norm", "cy_norm", "w_norm", "h_norm"), (cx, cy, width, height))):
        raise ProfitTrainingError("YOLO label and manifest box disagree")


def validate_dataset(
    dataset_root: Path, plan_path: Path, dataset_plan_path: Path, contract_path: Path,
    cohort_receipt_path: Path,
) -> dict[str, Any]:
    """Verify staged image/label SHA, arm geometry, cohorts and causal endpoints."""

    root = dataset_root.resolve()
    plan, dataset_plan = _json(plan_path), _json(dataset_plan_path)
    contract, cohort = _json(contract_path), _json(cohort_receipt_path)
    experiment_id = str(plan.get("experiment_id", ""))
    if not experiment_id or any(value.get("experiment_id") != experiment_id for value in (dataset_plan, contract, cohort)):
        raise ProfitTrainingError("plan, dataset plan, contract and cohort receipt experiment_id must agree")
    if not bool(plan.get("owner_authorization", {}).get("training_authorized", False)):
        raise ProfitTrainingError("plan does not authorize this research training")
    if bool(plan.get("safety", {}).get("training_eligible", True)):
        raise ProfitTrainingError("plan training_eligible must remain false before audit")
    if str(contract.get("original_plan_sha256", "")) != sha256_file(plan_path):
        raise ProfitTrainingError("training contract original plan SHA drift")
    if str(dataset_plan.get("original_plan_sha256", "")) != sha256_file(plan_path):
        raise ProfitTrainingError("dataset plan original plan SHA drift")
    if str(dataset_plan.get("training_contract_sha256", "")) != sha256_file(contract_path):
        raise ProfitTrainingError("dataset plan training contract SHA drift")
    if str(dataset_plan.get("selection_receipt_sha256", "")) != sha256_file(cohort_receipt_path):
        raise ProfitTrainingError("dataset plan selection receipt SHA drift")
    if not cohort.get("capacity_gate") or cohort.get("dataset_ledger_sha256") != dataset_plan.get("events_sha256") or cohort.get("selected_events_sha256") != dataset_plan.get("events_sha256") or cohort.get("training_contract_sha256") != sha256_file(contract_path):
        raise ProfitTrainingError("cohort capacity or frozen ledger binding failed")
    minimum, maximum = contract.get("minimum_train_winners"), contract.get("maximum_train_winners")
    if not isinstance(minimum, int) or not isinstance(maximum, int) or (minimum, maximum) != (3000, 5000):
        raise ProfitTrainingError("contract must freeze train_event_min=3000 and train_event_max=5000")
    manifest_path = root / "manifest.jsonl"
    if not manifest_path.is_file():
        raise ProfitTrainingError("dataset manifest.jsonl is missing")
    manifest_sha = sha256_file(manifest_path)
    summary = _json(root / "summary.json")
    if str(summary.get("manifest_sha256", "")) != manifest_sha:
        raise ProfitTrainingError("summary manifest SHA drift")
    bindings = {
        "events_sha256": str(dataset_plan.get("events_sha256", "")),
        "selection_receipt_sha256": sha256_file(cohort_receipt_path),
        "training_contract_sha256": sha256_file(contract_path),
        "plan_sha256": sha256_file(dataset_plan_path),
    }
    if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in bindings.values()):
        raise ProfitTrainingError("dataset plan and summary require complete SHA-256 bindings")
    if any(str(summary.get(key, "")) != value for key, value in bindings.items()):
        raise ProfitTrainingError("summary frozen-control SHA binding drift")
    rows = _jsonl(manifest_path)
    if not rows:
        raise ProfitTrainingError("empty dataset manifest")
    receipt_events = _receipt_events(cohort)
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_paths: set[str] = set()
    image_hash_splits: dict[str, str] = {}
    for row in rows:
        event_id, split = str(row.get("event_id", "")), str(row.get("split", ""))
        arms = tuple(str(value) for value in row.get("arms", ()))
        if not event_id or split not in {"train", "val", "test"} or not arms or any(arm not in ARMS for arm in arms):
            raise ProfitTrainingError("manifest requires event_id, train|val|test split, and A/B arms")
        if event_id not in receipt_events or receipt_events[event_id][1] != split:
            raise ProfitTrainingError(f"manifest/receipt split mismatch for {event_id}")
        receipt_identity = receipt_events[event_id]
        if (str(row.get("canonical_asset", "")), str(row.get("direction", "")), str(row.get("core_end_time", ""))) != receipt_identity[2:]:
            raise ProfitTrainingError(f"manifest/receipt identity mismatch for {event_id}")
        if str(row.get("visible_end_close_time_utc", "")) != str(row.get("decision_at_utc", "")):
            raise ProfitTrainingError(f"future-visible image endpoint for {event_id}")
        for path_key, sha_key in (("image_path", "image_sha256"), ("label_path", "label_sha256")):
            relative = str(row.get(path_key, ""))
            expected_prefix = f"{'images' if path_key == 'image_path' else 'labels'}/{split}/"
            if not relative.startswith(expected_prefix):
                raise ProfitTrainingError(f"{path_key} split path mismatch for {event_id}")
            path = _relative(root, relative, path_key)
            if not path.is_file() or sha256_file(path) != str(row.get(sha_key, "")):
                raise ProfitTrainingError(f"staged {path_key} SHA mismatch: {relative}")
            all_paths.add(relative)
        if str(row.get("cluster_id", "")) != receipt_events[event_id][0]:
            raise ProfitTrainingError("manifest cluster does not match cohort receipt")
        pixel_sha = str(row["image_sha256"])
        if pixel_sha in image_hash_splits and image_hash_splits[pixel_sha] != split:
            raise ProfitTrainingError("identical image appears across time splits")
        image_hash_splits[pixel_sha] = split
        _validate_pixels_and_label(root, row)
        by_event[event_id].append(row)
    if set(by_event) != set(receipt_events):
        raise ProfitTrainingError("cohort receipt must cover exactly the rendered manifest events")

    arm_samples: dict[str, dict[str, list[dict[str, Any]]]] = {
        arm: {split: [] for split in ("train", "val", "test")} for arm in ARMS
    }
    for row in rows:
        for arm in row["arms"]:
            arm_samples[str(arm)][str(row["split"])].append(row)
    train_events: dict[str, set[str]] = {}
    for arm in ARMS:
        per_event: dict[str, list[str]] = defaultdict(list)
        for row in arm_samples[arm]["train"]:
            per_event[str(row["event_id"])].append(str(row["variant"]))
        expected = ["A"] if arm == "A" else ["B1", "B2"]
        if any(sorted(variants) != expected for variants in per_event.values()):
            raise ProfitTrainingError(f"{arm} training variants are not {expected}")
        train_events[arm] = set(per_event)
        if not minimum <= len(train_events[arm]) <= maximum:
            raise ProfitTrainingError(f"{arm} train event count outside {minimum}..{maximum}")
        if len(train_events[arm]) != len({receipt_events[event][0] for event in train_events[arm]}):
            raise ProfitTrainingError(f"{arm} has cluster leakage in train")
    if train_events["A"] != train_events["B"]:
        raise ProfitTrainingError("A and B must use the same selected training events")
    for split in ("val", "test"):
        a_paths = {str(row["image_path"]) for row in arm_samples["A"][split]}
        b_paths = {str(row["image_path"]) for row in arm_samples["B"][split]}
        if a_paths != b_paths or any(str(row["variant"]) != "A" for row in arm_samples["A"][split]):
            raise ProfitTrainingError(f"A/B must share only single-view A {split} images")
    for arm in ARMS:
        lists = {split: _read_list(root / (f"train_{arm}.txt" if split == "train" else f"{split}.txt")) for split in ("train", "val", "test")}
        expected_paths = {split: {f"./{row['image_path']}" for row in arm_samples[arm][split]} for split in lists}
        if any(set(lists[split]) != expected_paths[split] for split in lists):
            raise ProfitTrainingError(f"{arm} image lists disagree with manifest")
    return {
        "manifest_sha256": manifest_sha,
        "manifest_rows": len(rows),
        "verified_files": len(all_paths),
        "arms": {arm: {split: len(arm_samples[arm][split]) for split in arm_samples[arm]} for arm in ARMS},
    }


def _expected_versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "constraints-ci.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.lstrip().startswith("#"):
            name, version = line.split("==", 1)
            if name in {"torch", "ultralytics", "numpy", "pandas"}:
                values[name] = version.strip()
    return values


def verify_environment() -> dict[str, Any]:
    """Require the pinned Python stack and an RTX 3060 with free CUDA memory."""

    import torch
    import ultralytics
    import numpy
    import pandas

    actual = {"torch": torch.__version__, "ultralytics": ultralytics.__version__, "numpy": numpy.__version__, "pandas": pandas.__version__}
    expected = _expected_versions()
    for name, version in expected.items():
        comparable = actual[name].split("+", 1)[0] if name == "torch" else actual[name]
        if comparable != version:
            raise ProfitTrainingError(f"{name} version drift: {actual[name]} != {version}")
    if not torch.cuda.is_available():
        raise ProfitTrainingError("CUDA unavailable; CPU fallback is forbidden")
    name = torch.cuda.get_device_name(0)
    free, total = torch.cuda.mem_get_info(0)
    if "3060" not in name.lower() or free <= 0:
        raise ProfitTrainingError(f"required RTX 3060/free VRAM unavailable: {name}, {free}")
    return {"versions": actual, "gpu": name, "free_vram_bytes": int(free), "total_vram_bytes": int(total)}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_audited_remote_root(dataset: Path, remote_dataset_root: str | None) -> str:
    """Only allow YAML rewriting for the exact dataset directory just audited."""

    if remote_dataset_root and Path(remote_dataset_root).resolve() != dataset:
        raise ProfitTrainingError("training root must be the dataset that was audited")
    return remote_dataset_root or str(dataset)


def run_training(args: argparse.Namespace) -> dict[str, Any]:
    """Audit, record and, only when requested, train both YOLO arms sequentially."""

    dataset, plan, dataset_plan, contract, cohort, base = (Path(value).resolve() for value in (args.dataset, args.plan, args.dataset_plan, args.contract, args.cohort_receipt, args.model))
    if not base.is_file():
        raise ProfitTrainingError(f"base model missing: {base}")
    audit, environment = validate_dataset(dataset, plan, dataset_plan, contract, cohort), verify_environment()
    expected_base_sha = _json(dataset_plan).get("base_model_sha256")
    if sha256_file(base) != expected_base_sha:
        raise ProfitTrainingError("base weights differ from frozen dataset plan")
    remote_root = resolve_audited_remote_root(dataset, args.remote_dataset_root)
    yaml_paths = {arm: write_windows_yaml(dataset, arm, remote_root) for arm in ARMS}
    receipt: dict[str, Any] = {"status": "preflight_passed", "train_requested": bool(args.train), "args": vars(args), "audit": audit, "environment": environment, "base_model_sha256": sha256_file(base), "data_yaml_sha256": {arm: sha256_file(path) for arm, path in yaml_paths.items()}, "training_args": {**TRAIN_ARGS, **SAFE_AUG}, "arms": {}}
    run_root = Path(args.run_root).resolve()
    _write_json(run_root / "training_receipt.json", receipt)
    if not args.train:
        return receipt
    from ultralytics import YOLO

    for arm in ARMS:
        arm_dir = run_root / f"arm_{arm}"
        if arm_dir.exists():
            raise ProfitTrainingError(f"refusing to overwrite or rename existing training arm: {arm_dir}")
        try:
            if sha256_file(base) != expected_base_sha:
                raise ProfitTrainingError("base weights changed between training arms")
            model = YOLO(str(base))
            model.train(data=str(yaml_paths[arm]), project=str(run_root), name=f"arm_{arm}", exist_ok=False, **TRAIN_ARGS, **SAFE_AUG)
            trainer = model.trainer
            receipt["arms"][arm] = {"status": "completed", "best": str(Path(trainer.best).resolve()), "last": str(Path(trainer.last).resolve()), "results_csv": str((arm_dir / "results.csv").resolve())}
        except Exception as exc:
            receipt["arms"][arm] = {"status": "failed", "error": repr(exc)}
            receipt["status"] = "failed"
            _write_json(run_root / "training_receipt.json", receipt)
            raise
        _write_json(run_root / "training_receipt.json", receipt)
    receipt["status"] = "completed"
    _write_json(run_root / "training_receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--plan", required=True, help="immutable original preregistration plan")
    parser.add_argument("--dataset-plan", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--cohort-receipt", required=True)
    parser.add_argument("--model", default="C:/fable/models/yolo11s.pt")
    parser.add_argument("--run-root", default="C:/fable/runs/ma_profit3r")
    parser.add_argument("--remote-dataset-root", default=None)
    parser.add_argument("--train", action="store_true", help="run A then B after preflight; default is audit only")
    args = parser.parse_args()
    print(json.dumps(run_training(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
