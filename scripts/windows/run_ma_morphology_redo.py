r"""Fail-closed Windows runner for morphology-only negative-label YOLO arms.

The runner has two modes.  Its default preflight verifies the committed plan,
dataset audit, review receipt, source hashes, and frozen 40-epoch recipe.  It
does not import Ultralytics or touch CUDA.  ``--train`` additionally requires
the pinned RTX-3060 environment and runs A then B, each from the same base
YOLO checkpoint.  Result reporting is detector-only: morphology labels and
their image-level predictions are retained without any outcome or economics.

``launch_contract`` is a JSON object with ``experiment_id``, ``plan_sha256``,
``base_model_sha256``, ``dataset_audit``, ``review_path``, ``review_sha256``,
and ``files``.  ``files`` maps repository-relative source/control paths to
their SHA-256 digest.  Its audit object must exactly equal the local audit.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from scripts.windows.train_ma_profit3r import (
    SAFE_AUG,
    TRAIN_ARGS,
    verify_environment,
)
from yoyo.datasets.ma_morphology_redo import audit as audit_dataset


ROOT = Path(__file__).resolve().parents[2]
ARMS = ("A", "B")
CLASSES = ("dense_launch_long", "dense_launch_short")
EVAL_ARGS = {
    "imgsz": 1280,
    "batch": 8,
    "device": 0,
    "conf": 0.001,
    "iou": 0.7,
    "plots": False,
    "save_json": False,
    "workers": 2,
    "augment": False,
}
PREDICT_ARGS = {
    "imgsz": 1280,
    "device": 0,
    "conf": 0.25,
    "iou": 0.5,
    "augment": False,
    "agnostic_nms": False,
    "max_det": 300,
    "verbose": False,
}
LOW_CONF_PREDICT_ARGS = {**PREDICT_ARGS, "conf": 0.001, "iou": 0.7}


class MorphologyTrainingError(RuntimeError):
    """Raised when a morphology experiment precondition is not exact."""


def sha256_file(path: Path) -> str:
    """Hash one immutable control or artifact file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MorphologyTrainingError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise MorphologyTrainingError(f"JSON object required: {path}")
    return value


def _repo_path(value: object, *, root: Path = ROOT) -> Path:
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts:
        raise MorphologyTrainingError(f"path must be repository-relative: {value!r}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise MorphologyTrainingError(f"path escapes repository: {value!r}") from exc
    return resolved


def _require_sha(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path) if path.is_file() else ""
    if not isinstance(expected, str) or len(expected) != 64 or actual != expected:
        raise MorphologyTrainingError(f"{label} SHA mismatch: {path}")
    return actual


def expected_training_args() -> dict[str, Any]:
    """Return the immutable no-augmentation 40-epoch recipe."""

    return {**TRAIN_ARGS, **SAFE_AUG}


def validate_training_args(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Reject even semantically similar recipe drift; the plan is the contract."""

    actual = plan.get("training_args")
    expected = expected_training_args()
    if actual != expected:
        raise MorphologyTrainingError("plan training_args differ from frozen 40-epoch recipe")
    return dict(expected)


def _validate_contract_files(contract: Mapping[str, Any], *, root: Path) -> dict[str, str]:
    files = contract.get("files")
    if not isinstance(files, Mapping) or not files:
        raise MorphologyTrainingError("launch contract requires non-empty files SHA mapping")
    verified: dict[str, str] = {}
    for relative, digest in sorted(files.items()):
        if not isinstance(relative, str):
            raise MorphologyTrainingError("launch-contract file paths must be strings")
        verified[relative] = _require_sha(_repo_path(relative, root=root), digest, "launch file")
    return verified


def validate_preflight_contract(
    *,
    plan_path: Path,
    dataset: Path,
    launch_contract_path: Path,
    audit_result: Mapping[str, Any],
    root: Path = ROOT,
) -> dict[str, Any]:
    """Verify controls without CUDA, training, checkpoint, or run-dir writes."""

    plan_path, dataset, launch_contract_path = (
        plan_path.resolve(), dataset.resolve(), launch_contract_path.resolve()
    )
    plan, contract = _json(plan_path), _json(launch_contract_path)
    experiment_id = str(plan.get("experiment_id", ""))
    if not experiment_id or str(contract.get("experiment_id", "")) != experiment_id:
        raise MorphologyTrainingError("plan and launch contract experiment_id differ")
    if plan.get("training_eligible") is not False or plan.get("production_eligible") is not False:
        raise MorphologyTrainingError("offline training/production flags must remain false")
    if plan.get("owner_authorization", {}).get("training_authorized") is not True:
        raise MorphologyTrainingError("plan lacks owner-authorized offline training")
    plan_sha = _require_sha(plan_path, contract.get("plan_sha256"), "plan")
    recipe = validate_training_args(plan)
    inputs = plan.get("inputs")
    if not isinstance(inputs, Mapping) or not isinstance(inputs.get("base_model"), Mapping):
        raise MorphologyTrainingError("plan requires inputs.base_model")
    base_cfg = inputs["base_model"]
    base = _repo_path(base_cfg.get("path"), root=root)
    base_sha = _require_sha(base, base_cfg.get("sha256"), "base model")
    if contract.get("base_model_sha256") != base_sha:
        raise MorphologyTrainingError("launch contract base-model SHA differs from plan input")
    files = _validate_contract_files(contract, root=root)

    do_not_train = dataset / "DO_NOT_TRAIN_LABEL_SEMANTICS.json"
    if do_not_train.exists():
        raise MorphologyTrainingError("dataset label-semantics quarantine blocks training")
    summary = _json(dataset / "summary.json")
    if summary.get("pilot") is not False:
        raise MorphologyTrainingError("pilot dataset cannot enter training")
    if summary.get("training_eligible") is not False or summary.get("production_eligible") is not False:
        raise MorphologyTrainingError("dataset eligibility flags drifted")
    if dict(audit_result) != contract.get("dataset_audit"):
        raise MorphologyTrainingError("local morphology audit differs from launch contract")
    if audit_result.get("status") != "passed":
        raise MorphologyTrainingError("morphology audit did not pass")
    if audit_result.get("pilot") is not False or audit_result.get("per_sample_owner_gold") is not False:
        raise MorphologyTrainingError("audit must honestly retain pilot/per-sample owner-gold false")
    manifest = dataset / "manifest.jsonl"
    manifest_sha = sha256_file(manifest) if manifest.is_file() else ""
    if not manifest_sha or summary.get("manifest_sha256") != manifest_sha:
        raise MorphologyTrainingError("dataset manifest SHA does not match its summary")
    review = _repo_path(contract.get("review_path"), root=root)
    review_sha = _require_sha(review, contract.get("review_sha256"), "review receipt")
    review_payload = _json(review)
    if review_payload.get("status") != "passed_rendering_spot_check":
        raise MorphologyTrainingError("review receipt lacks passed_rendering_spot_check status")
    if (
        review_payload.get("manifest_sha256") != manifest_sha
        or contract.get("review_manifest_sha256") != manifest_sha
    ):
        raise MorphologyTrainingError("review receipt is not bound to the current dataset manifest")
    return {
        "experiment_id": experiment_id,
        "plan_sha256": plan_sha,
        "base_model": str(base),
        "base_model_sha256": base_sha,
        "training_args": recipe,
        "launch_files": files,
        "review_path": str(review),
        "review_sha256": review_sha,
        "review_manifest_sha256": manifest_sha,
        "dataset_audit": dict(audit_result),
    }


def validate_results_shape(results_csv: Path, *, epochs: int = 40) -> dict[str, Any]:
    """Require the complete finite 40-epoch record before artifact receipt."""

    if not results_csv.is_file():
        raise MorphologyTrainingError(f"missing results.csv: {results_csv}")
    try:
        result = pd.read_csv(results_csv)
    except (OSError, ValueError) as exc:
        raise MorphologyTrainingError("unreadable results.csv") from exc
    if len(result) != epochs:
        raise MorphologyTrainingError(f"results.csv has {len(result)} rows, expected {epochs}")
    if "epoch" not in result or result["epoch"].tolist() != list(range(1, epochs + 1)):
        raise MorphologyTrainingError("results.csv epoch sequence is not exactly 1 through 40")
    numeric = result.select_dtypes(include="number")
    if numeric.empty or not numeric.apply(lambda column: column.map(math.isfinite).all()).all():
        raise MorphologyTrainingError("results.csv has no finite numeric training metrics")
    return {"epochs": int(len(result)), "columns": list(result.columns), "sha256": sha256_file(results_csv)}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return sha256_file(path)


def write_morphology_yaml(dataset: Path, arm: str) -> Path:
    """Write a morphology-only YAML; legacy profitable aliases are forbidden."""

    if arm not in ARMS:
        raise MorphologyTrainingError(f"unknown training arm: {arm}")
    yaml_path = dataset / f"data_{arm}.windows.yaml"
    remote_root = str(dataset.resolve()).replace("\\", "/")
    yaml_path.write_text(
        f"path: {remote_root}\ntrain: train_{arm}.txt\nval: val.txt\ntest: test.txt\n"
        f"nc: 2\nnames: [{CLASSES[0]}, {CLASSES[1]}]\n",
        encoding="utf-8",
    )
    return yaml_path


def _keep_awake() -> None:
    """Ask Windows to keep the local GPU session awake while training runs."""

    if os.name == "nt":
        continuous, system, display = 0x80000000, 0x00000001, 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(continuous | system | display)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def _rank_auc(labels: list[int], scores: list[float]) -> float | None:
    """Dependency-free ROC AUC with average ranks for equal low-conf scores."""

    positives, negatives = sum(labels), len(labels) - sum(labels)
    if not positives or not negatives:
        return None
    ordered = sorted(enumerate(scores), key=lambda item: item[1])
    ranks = [0.0] * len(scores)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for original, _score in ordered[cursor:end]:
            ranks[original] = average_rank
        cursor = end
    return (sum(rank for rank, label in zip(ranks, labels) if label) - positives * (positives + 1) / 2.0) / (positives * negatives)


def _manifest_rows(dataset: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (dataset / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _morphology_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize one split only; every rate retains its morphology label scope."""

    labels = [int(row["binary_label"]) for row in rows]
    scores = [float(row["low_conf_max_event_score_all_classes"]) for row in rows]
    backgrounds = [row for row in rows if row["binary_label"] == 0]
    positives = [row for row in rows if row["binary_label"] == 1]
    return {
        "images": len(rows),
        "events": len({str(row["event_id"]) for row in rows}),
        "background_images": len(backgrounds),
        "positive_images": len(positives),
        "background_false_positive_rate_conf025_iou05": (
            sum(bool(row["background_false_positive_conf025_iou05"]) for row in backgrounds)
            / len(backgrounds)
            if backgrounds else None
        ),
        "positive_matched_class_spatial_hit_rate_conf025_iou05": (
            sum(bool(row["matched_class_spatial_hit_iou_ge_05"]) for row in positives)
            / len(positives)
            if positives else None
        ),
        "low_conf_all_class_event_score_roc_auc": _rank_auc(labels, scores),
        "label_scope": "morphology_only_no_economic_claim",
    }


def evaluate_common_images(model: Any, *, dataset: Path, run_dir: Path, arm: str) -> dict[str, Any]:
    """Save all common A-view detector predictions and morphology-only summaries."""

    rows = [
        row for row in _manifest_rows(dataset)
        if row.get("split") in {"val", "test"}
        and row.get("variant") == "A"
        and set(row.get("arms", ())) == {"A", "B"}
    ]
    if not rows:
        raise MorphologyTrainingError("no common A-view val/test rows for detector evaluation")
    saved: list[Mapping[str, Any]] = []
    for row in rows:
        image = dataset / str(row["image_path"])
        high = model.predict(str(image), **PREDICT_ARGS)[0]
        low = model.predict(str(image), **LOW_CONF_PREDICT_ARGS)[0]
        high_boxes = high.boxes
        low_boxes = low.boxes
        detections = []
        for xyxy, confidence, class_id in zip(
            high_boxes.xyxy.tolist(), high_boxes.conf.tolist(), high_boxes.cls.tolist()
        ):
            detections.append({"xyxy": [float(value) for value in xyxy], "confidence": float(confidence), "class_id": int(class_id)})
        all_low_scores = [float(value) for value in low_boxes.conf.tolist()]
        max_score = max(all_low_scores, default=0.0)
        label = row.get("class_id")
        hit = False
        max_matching_iou = 0.0
        if label is None:
            binary_label = 0
            background_fp = bool(detections)
        else:
            binary_label = 1
            background_fp = False
            box = row.get("box") or {}
            target = (
                (float(box["cx_norm"]) - float(box["w_norm"]) / 2) * 1280,
                (float(box["cy_norm"]) - float(box["h_norm"]) / 2) * 742,
                (float(box["cx_norm"]) + float(box["w_norm"]) / 2) * 1280,
                (float(box["cy_norm"]) + float(box["h_norm"]) / 2) * 742,
            )
            matching_ious = [
                _box_iou(tuple(item["xyxy"]), target)
                for item in detections
                if item["class_id"] == int(label)
            ]
            max_matching_iou = max(matching_ious, default=0.0)
            hit = max_matching_iou >= 0.5
        saved.append({
            "event_id": row.get("event_id"), "split": row["split"], "image_path": row["image_path"],
            "image_sha256": sha256_file(image), "class_id": label,
            "high_conf_detections": detections, "high_conf_predict_args": PREDICT_ARGS,
            "low_conf_predict_args": LOW_CONF_PREDICT_ARGS,
            "low_conf_max_event_score_all_classes": max_score, "binary_label": binary_label,
            "background_false_positive_conf025_iou05": background_fp,
            "matched_class_iou_max": max_matching_iou,
            "matched_class_spatial_hit_iou_ge_05": hit,
        })
    prediction_path = run_dir / f"arm_{arm}_common_eval_predictions.jsonl"
    prediction_sha = _write_jsonl(prediction_path, saved)
    return {
        "prediction_path": str(prediction_path), "prediction_sha256": prediction_sha,
        "by_split": {
            split: _morphology_summary([row for row in saved if row["split"] == split])
            for split in ("val", "test")
        },
    }


def run_training(
    *, plan_path: Path, dataset: Path, run_root: Path, launch_contract_path: Path, train: bool,
    audit_fn: Callable[..., Mapping[str, Any]] = audit_dataset,
) -> dict[str, Any]:
    """Preflight or execute both arms without ever overwriting a run directory."""

    audit_result = audit_fn(plan_path, dataset, write_receipt=False)
    preflight = validate_preflight_contract(
        plan_path=plan_path, dataset=dataset, launch_contract_path=launch_contract_path,
        audit_result=audit_result,
    )
    run_root = run_root.resolve()
    if run_root.exists():
        raise MorphologyTrainingError(f"refusing to overwrite existing run root: {run_root}")
    receipt: dict[str, Any] = {"status": "preflight_passed", "train_requested": train, "preflight": preflight, "arms": {}}
    if not train:
        return receipt
    environment = verify_environment()
    _keep_awake()
    run_root.mkdir(parents=True)
    receipt["environment"] = environment
    receipt["status"] = "running"
    _write_json(run_root / "training_receipt.json", receipt)
    from ultralytics import YOLO

    base = Path(preflight["base_model"])
    for arm in ARMS:
        arm_dir = run_root / f"arm_{arm}"
        if arm_dir.exists():
            raise MorphologyTrainingError(f"refusing to overwrite arm directory: {arm_dir}")
        yaml_path = write_morphology_yaml(dataset, arm)
        receipt["arms"][arm] = {
            "status": "running",
            "training_args": preflight["training_args"],
            "data_yaml_sha256": sha256_file(yaml_path),
        }
        _write_json(run_root / "training_receipt.json", receipt)
        try:
            model = YOLO(str(base))
            model.train(data=str(yaml_path), project=str(run_root), name=f"arm_{arm}", exist_ok=False, **preflight["training_args"])
            trainer = model.trainer
            best, last = Path(trainer.best), Path(trainer.last)
            csv_shape = validate_results_shape(arm_dir / "results.csv", epochs=40)
            args_yaml = arm_dir / "args.yaml"
            if not args_yaml.is_file():
                raise MorphologyTrainingError(f"missing Ultralytics args.yaml: {args_yaml}")
            arm_receipt: dict[str, Any] = {
                "status": "trained", "best": str(best.resolve()), "last": str(last.resolve()),
                "best_sha256": sha256_file(best), "last_sha256": sha256_file(last), "results_csv": csv_shape,
                "data_yaml_sha256": sha256_file(yaml_path),
                "args_yaml_sha256": sha256_file(args_yaml), "evaluations": {},
            }
            best_model = YOLO(str(best))
            for split in ("val", "test"):
                result = best_model.val(data=str(yaml_path), split=split, **EVAL_ARGS)
                summary = getattr(result, "summary", None)
                arm_receipt["evaluations"][split] = {
                    "results_dict": _jsonable(getattr(result, "results_dict", {})),
                    "summary": _jsonable(summary() if callable(summary) else summary),
                }
            arm_receipt["common_image_evaluation"] = evaluate_common_images(best_model, dataset=dataset, run_dir=run_root, arm=arm)
            receipt["arms"][arm] = arm_receipt
        except Exception as exc:
            receipt["status"] = "failed"
            receipt["arms"][arm] = {"status": "failed", "error": repr(exc)}
            _write_json(run_root / "training_receipt.json", receipt)
            raise
        _write_json(run_root / "training_receipt.json", receipt)
    receipt["status"] = "completed"
    _write_json(run_root / "training_receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--launch-contract", type=Path, required=True)
    parser.add_argument("--train", action="store_true", help="run A then B after offline preflight")
    args = parser.parse_args()
    print(json.dumps(run_training(plan_path=args.plan, dataset=args.dataset, run_root=args.run_root, launch_contract_path=args.launch_contract, train=args.train), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
