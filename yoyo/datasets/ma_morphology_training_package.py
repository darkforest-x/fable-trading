"""Fail-closed preflight and explicit training entry for the v6 morphology set.

The default ``preflight`` command only validates the merged manifest/assets,
freezes YOLO list files and ``data.yaml`` into a new output directory, and
prints a training command. ``train`` is an explicit remote-only action; it
requires a separately training-eligible v6 plan, pinned dependencies, CUDA,
the original YOLO11s base SHA, and a fresh output directory. It never promotes
weights or changes ACTIVE state.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tempfile
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = Path("datasets/ma_launch_owner1500_morph_v6_threeview_ready_20260925_v1")
ACTIVE_PLAN = Path("experiments/active/exp-ma-morphology-v6-threeview-20260925-v1/plan.json")
RECIPE_PLAN = Path("experiments/active/exp-ma-morphology-negatives-20260922-v3/plan.json")
CONSTRAINTS = Path("constraints-ci.txt")
EXPECTED_EVAL_COUNTS = {"val": 350, "test": 320}
CLASSES = ("dense_launch_long", "dense_launch_short")
SPLITS = ("train", "val", "test")
VARIANTS = ("P7", "P9", "P11")
POOLS = ("reference", "grade_a_challenge")
OFF_KEYS = (
    "bgr", "copy_paste", "cutmix", "degrees", "erasing", "fliplr", "flipud",
    "hsv_h", "hsv_s", "hsv_v", "mixup", "mosaic", "multi_scale",
    "perspective", "scale", "shear", "translate",
)
REQUIRED_FIELDS = (
    "event_id", "split", "variant", "class_id", "sample_kind", "evaluation_pool",
    "image_path", "label_path", "image_sha256", "label_sha256",
    "decision_at_utc", "visible_start_utc", "visible_end_close_time_utc",
)


class MorphologyTrainingError(RuntimeError):
    """Raised when a dataset, environment, or training contract fails closed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MorphologyTrainingError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise MorphologyTrainingError(f"JSON object required: {path}")
    return value


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MorphologyTrainingError(f"missing timestamp: {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MorphologyTrainingError(f"invalid timestamp: {field}={value!r}") from exc
    if parsed.tzinfo is None:
        raise MorphologyTrainingError(f"timestamp must include timezone: {field}")
    return parsed.astimezone(timezone.utc)


def _safe_relative_path(value: object, root: Path, field: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MorphologyTrainingError(f"invalid repository-relative {field}: {value!r}")
    rel = PurePosixPath(value)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise MorphologyTrainingError(f"unsafe repository-relative {field}: {value!r}")
    path = root.joinpath(*rel.parts)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise MorphologyTrainingError(f"missing or escaping {field}: {value!r}") from exc
    if not resolved.is_file():
        raise MorphologyTrainingError(f"{field} is not a file: {value!r}")
    return resolved, rel.as_posix()


def _verify_image(path: Path) -> None:
    suffix = path.suffix.lower()
    with path.open("rb") as handle:
        head = handle.read(8)
    if suffix == ".png" and head != b"\x89PNG\r\n\x1a\n":
        raise MorphologyTrainingError(f"PNG signature invalid: {path}")
    if suffix in (".jpg", ".jpeg") and not head.startswith(b"\xff\xd8\xff"):
        raise MorphologyTrainingError(f"JPEG signature invalid: {path}")
    if suffix not in (".png", ".jpg", ".jpeg"):
        raise MorphologyTrainingError(f"unsupported image extension: {path}")


def _verify_label(path: Path, row: Mapping[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    if row["sample_kind"] == "negative":
        if row["class_id"] is not None or text.strip():
            raise MorphologyTrainingError(f"negative must have null class and empty label: {row['event_id']}")
        return
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or len(lines[0]) != 5:
        raise MorphologyTrainingError(f"positive requires one YOLO box row: {row['event_id']}")
    try:
        values = [float(part) for part in lines[0]]
    except ValueError as exc:
        raise MorphologyTrainingError(f"non-numeric YOLO label: {row['event_id']}") from exc
    if not all(math.isfinite(value) for value in values):
        raise MorphologyTrainingError(f"non-finite YOLO label: {row['event_id']}")
    class_value = values[0]
    if class_value not in (0.0, 1.0) or int(class_value) != row["class_id"]:
        raise MorphologyTrainingError(f"positive class mismatch: {row['event_id']}")
    _, cx, cy, width, height = values
    if width <= 0 or height <= 0 or not (
        0 <= cx - width / 2 <= cx + width / 2 <= 1
        and 0 <= cy - height / 2 <= cy + height / 2 <= 1
    ):
        raise MorphologyTrainingError(f"invalid normalized box geometry: {row['event_id']}")


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MorphologyTrainingError(f"invalid manifest JSON at line {line_number}") from exc
                if not isinstance(row, dict):
                    raise MorphologyTrainingError(f"manifest object required at line {line_number}")
                rows.append(row)
    except OSError as exc:
        raise MorphologyTrainingError(f"cannot read manifest: {path}") from exc
    if not rows:
        raise MorphologyTrainingError("manifest is empty")
    return rows


def _load_split_boundaries(root: Path) -> dict[str, datetime]:
    plan = _read_json(root / ACTIVE_PLAN)
    splits = plan.get("splits")
    if not isinstance(splits, dict):
        raise MorphologyTrainingError("active v6 plan has no split contract")
    return {
        "train_end_exclusive": _parse_time(splits.get("train_end_exclusive"), "train_end_exclusive"),
        "validation_end_exclusive": _parse_time(splits.get("validation_end_exclusive"), "validation_end_exclusive"),
        "test_end_exclusive": _parse_time(splits.get("test_end_exclusive"), "test_end_exclusive"),
    }


def audit_dataset(
    dataset_root: Path,
    *,
    repo_root: Path = ROOT,
    expected_eval_counts: Mapping[str, int] = EXPECTED_EVAL_COUNTS,
    split_boundaries: Mapping[str, datetime] | None = None,
) -> dict[str, Any]:
    """Verify manifest, receipt, files, labels, causal clocks, splits and views."""
    root = dataset_root.resolve()
    repo = repo_root.resolve()
    if not root.is_dir():
        raise MorphologyTrainingError(f"dataset root missing: {root}")
    manifest_path = root / "manifest.jsonl"
    receipt_path = root / "build_receipt.json"
    if not manifest_path.is_file() or not receipt_path.is_file():
        raise MorphologyTrainingError("dataset requires manifest.jsonl and build_receipt.json")
    manifest_sha = sha256_file(manifest_path)
    receipt = _read_json(receipt_path)
    if receipt.get("dataset_ready") is not True or receipt.get("manifest_sha256") != manifest_sha:
        raise MorphologyTrainingError("build receipt readiness/manifest SHA mismatch")
    if not isinstance(receipt.get("source_commit"), str) or not receipt["source_commit"].strip():
        raise MorphologyTrainingError("build receipt lacks source_commit")
    rows = _read_manifest(manifest_path)
    boundaries = dict(split_boundaries or _load_split_boundaries(repo))

    seen_keys: set[tuple[str, str]] = set()
    image_paths: set[str] = set()
    label_paths: set[str] = set()
    image_hashes: dict[str, tuple[str, str]] = {}
    event_meta: dict[str, dict[str, Any]] = {}
    event_variants: dict[str, set[str]] = defaultdict(set)
    cluster_splits: dict[str, str] = {}
    counts: Counter[tuple[str, str]] = Counter()
    main_eval_counts = Counter()
    challenge_counts = Counter()
    stability_counts = Counter()

    for index, row in enumerate(rows, 1):
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise MorphologyTrainingError(f"manifest row {index} missing fields: {', '.join(missing)}")
        event_id, split, variant = row["event_id"], row["split"], row["variant"]
        if not isinstance(event_id, str) or not event_id.strip() or split not in SPLITS:
            raise MorphologyTrainingError(f"invalid event/split at manifest row {index}")
        if variant not in VARIANTS:
            raise MorphologyTrainingError(f"unsupported variant at manifest row {index}: {variant!r}")
        kind, pool, class_id = row["sample_kind"], row["evaluation_pool"], row["class_id"]
        if kind not in ("positive", "negative") or pool not in POOLS:
            raise MorphologyTrainingError(f"invalid sample_kind/evaluation_pool at row {index}")
        if kind == "positive" and (type(class_id) is not int or class_id not in (0, 1)):
            raise MorphologyTrainingError(f"positive class_id must be 0 or 1: {event_id}")
        if kind == "negative" and class_id is not None:
            raise MorphologyTrainingError(f"negative class_id must be null: {event_id}")
        key = (event_id, variant)
        if key in seen_keys:
            raise MorphologyTrainingError(f"duplicate event/variant row: {event_id} {variant}")
        seen_keys.add(key)
        meta = {"split": split, "sample_kind": kind, "class_id": class_id,
                "evaluation_pool": pool, "decision_at_utc": row["decision_at_utc"],
                "visible_end_close_time_utc": row["visible_end_close_time_utc"]}
        previous = event_meta.setdefault(event_id, meta)
        if previous != meta:
            raise MorphologyTrainingError(f"event crosses split or changes sample identity: {event_id}")
        event_variants[event_id].add(variant)
        if len(event_variants[event_id]) > 3:
            raise MorphologyTrainingError(f"event has more than three views: {event_id}")
        cluster = row.get("cluster_id")
        if cluster is not None:
            if not isinstance(cluster, str) or not cluster:
                raise MorphologyTrainingError(f"invalid cluster_id: {event_id}")
            prior_split = cluster_splits.setdefault(cluster, split)
            if prior_split != split:
                raise MorphologyTrainingError(f"event cluster crosses splits: {cluster}")

        decision = _parse_time(row["decision_at_utc"], "decision_at_utc")
        visible_start = _parse_time(row["visible_start_utc"], "visible_start_utc")
        visible_end = _parse_time(row["visible_end_close_time_utc"], "visible_end_close_time_utc")
        if visible_start > visible_end or visible_end > decision:
            raise MorphologyTrainingError(f"visible input extends past decision time: {event_id}")
        if split == "train":
            lower = None
            upper = boundaries["train_end_exclusive"]
            clock_ok = decision < boundaries["train_end_exclusive"]
        elif split == "val":
            lower = boundaries["train_end_exclusive"]
            upper = boundaries["validation_end_exclusive"]
            clock_ok = boundaries["train_end_exclusive"] <= decision < boundaries["validation_end_exclusive"]
        else:
            lower = boundaries["validation_end_exclusive"]
            upper = boundaries["test_end_exclusive"]
            clock_ok = boundaries["validation_end_exclusive"] <= decision < boundaries["test_end_exclusive"]
        if not clock_ok:
            raise MorphologyTrainingError(f"decision timestamp belongs to another split: {event_id} {split}")
        if lower is not None and visible_start < lower:
            raise MorphologyTrainingError(f"visible input starts before its split: {event_id} {split}")
        if decision + timedelta(hours=12) >= upper:
            raise MorphologyTrainingError(f"decision plus 12h safety interval crosses split: {event_id} {split}")
        label_horizon = row.get("label_horizon_end_utc")
        if label_horizon is not None:
            label_end = _parse_time(label_horizon, "label_horizon_end_utc")
            if label_end < decision or label_end > decision + timedelta(hours=12) or label_end >= upper:
                raise MorphologyTrainingError(f"label horizon crosses its split or 12h safety interval: {event_id}")

        image, image_rel = _safe_relative_path(row["image_path"], root, "image_path")
        label, label_rel = _safe_relative_path(row["label_path"], root, "label_path")
        if PurePosixPath(image_rel).parts[:2] != ("images", split):
            raise MorphologyTrainingError(f"image path disagrees with split: {event_id}")
        if PurePosixPath(label_rel).parts[:2] != ("labels", split):
            raise MorphologyTrainingError(f"label path disagrees with split: {event_id}")
        for name, path, expected, rel in (
            ("image", image, row["image_sha256"], image_rel),
            ("label", label, row["label_sha256"], label_rel),
        ):
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise MorphologyTrainingError(f"invalid {name} SHA: {event_id}")
            actual = sha256_file(path)
            if actual != expected:
                raise MorphologyTrainingError(f"{name} SHA mismatch: {event_id} {rel}")
            if name == "image":
                prior = image_hashes.get(actual)
                identity = (event_id, variant)
                if prior is not None and prior != identity:
                    raise MorphologyTrainingError(f"duplicate image bytes across samples: {rel}")
                image_hashes[actual] = identity
        _verify_image(image)
        _verify_label(label, row)
        if image_rel in image_paths or label_rel in label_paths:
            raise MorphologyTrainingError(f"asset path reused by multiple manifest rows: {event_id}")
        image_paths.add(image_rel)
        label_paths.add(label_rel)
        counts[(split, kind)] += 1
        if split in ("val", "test") and variant == "P9" and pool == "reference":
            main_eval_counts[split] += 1
        if split in ("val", "test") and variant == "P9" and pool == "grade_a_challenge":
            challenge_counts[split] += 1
        if split in ("val", "test") and variant in ("P7", "P11"):
            stability_counts[(split, variant, pool)] += 1

    actual_images = {
        path.relative_to(root).as_posix()
        for split in SPLITS for path in (root / "images" / split).rglob("*")
        if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg")
    }
    actual_labels = {
        path.relative_to(root).as_posix()
        for split in SPLITS for path in (root / "labels" / split).rglob("*")
        if path.is_file() and path.suffix.lower() == ".txt"
    }
    if actual_images != image_paths:
        missing = sorted(actual_images - image_paths)[:3]
        absent = sorted(image_paths - actual_images)[:3]
        raise MorphologyTrainingError(f"orphan/missing images; unmanifested={missing}, missing={absent}")
    if actual_labels != label_paths:
        missing = sorted(actual_labels - label_paths)[:3]
        absent = sorted(label_paths - actual_labels)[:3]
        raise MorphologyTrainingError(f"orphan/missing labels; unmanifested={missing}, missing={absent}")

    train_positive = counts[("train", "positive")]
    train_negative = counts[("train", "negative")]
    if train_positive <= 0 or train_negative <= 0:
        raise MorphologyTrainingError("training split must contain both positive and negative images")
    if dict(sorted(main_eval_counts.items())) != dict(sorted(expected_eval_counts.items())):
        raise MorphologyTrainingError(
            f"fixed P9 reference evaluation counts drift: expected={dict(expected_eval_counts)}, "
            f"actual={dict(main_eval_counts)}"
        )
    if receipt.get("train_positive_images") != train_positive:
        raise MorphologyTrainingError("build receipt train_positive_images mismatch")
    if receipt.get("train_negative_images") != train_negative:
        raise MorphologyTrainingError("build receipt train_negative_images mismatch")

    return {
        "status": "passed",
        "manifest_sha256": manifest_sha,
        "build_receipt_sha256": sha256_file(receipt_path),
        "source_commit": receipt["source_commit"],
        "sample_rows": len(rows),
        "event_count": len(event_meta),
        "train_positive_images": train_positive,
        "train_negative_images": train_negative,
        "counts_by_split_kind": {f"{split}_{kind}": counts[(split, kind)]
                                  for split in SPLITS for kind in ("positive", "negative")},
        "main_p9_reference_eval_counts": dict(main_eval_counts),
        "grade_a_challenge_p9_counts": dict(challenge_counts),
        "stability_view_counts": {
            f"{split}_{variant}_{pool}": stability_counts[(split, variant, pool)]
            for split in ("val", "test") for variant in ("P7", "P11") for pool in POOLS
        },
        "max_views_per_event": max(len(variants) for variants in event_variants.values()),
        "dataset_ready": True,
    }


def _load_training_contract(repo_root: Path = ROOT) -> dict[str, Any]:
    active_path = repo_root / ACTIVE_PLAN
    recipe_path = repo_root / RECIPE_PLAN
    active = _read_json(active_path)
    recipe = _read_json(recipe_path)
    recipe_inputs = recipe.get("inputs", {})
    base = recipe_inputs.get("base_model", {})
    args = recipe.get("training_args")
    if not isinstance(args, dict):
        raise MorphologyTrainingError("frozen recipe plan lacks training_args")
    if not isinstance(base, dict) or base.get("path") != "models/yolo11s.pt":
        raise MorphologyTrainingError("frozen recipe must use repository models/yolo11s.pt")
    if not isinstance(base.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", base["sha256"]):
        raise MorphologyTrainingError("frozen recipe lacks a valid base model SHA")
    fixed = {"imgsz": 1280, "epochs": 40, "batch": 8, "seed": 0}
    for key, expected in fixed.items():
        if args.get(key) != expected:
            raise MorphologyTrainingError(f"frozen recipe drift: {key} must be {expected}")
    if any(args.get(key) != 0 for key in OFF_KEYS):
        raise MorphologyTrainingError("frozen recipe must disable every augmentation")
    if args.get("auto_augment") is not None:
        raise MorphologyTrainingError("frozen recipe auto_augment must be null")
    if args.get("device") != "0" and args.get("device") != 0:
        raise MorphologyTrainingError("frozen recipe device must be GPU 0")
    reference = active.get("training_recipe_reference", {})
    for key, expected in fixed.items():
        if reference.get(key) != expected:
            raise MorphologyTrainingError(f"active plan recipe reference drift: {key}")
    if reference.get("family") != "YOLO11s":
        raise MorphologyTrainingError("active plan must reference YOLO11s")
    parent_ref = active.get("inputs", {}).get("parent_plan", {})
    if parent_ref.get("path") != RECIPE_PLAN.as_posix() or parent_ref.get("sha256") != sha256_file(recipe_path):
        raise MorphologyTrainingError("active plan no longer binds the frozen recipe plan")
    base_path = repo_root / base["path"]
    if not base_path.is_file() or sha256_file(base_path) != base["sha256"]:
        raise MorphologyTrainingError("original YOLO11s base model missing or SHA mismatch")
    return {
        "active_plan": active,
        "active_plan_sha256": sha256_file(active_path),
        "recipe_plan_sha256": sha256_file(recipe_path),
        "base_model_path": base_path,
        "base_model_sha256": base["sha256"],
        "training_args": dict(args),
    }


def _constraint_versions(path: Path) -> dict[str, str]:
    wanted = {"torch", "torchvision", "ultralytics", "numpy", "pandas"}
    pins: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MorphologyTrainingError(f"cannot read dependency contract: {path}") from exc
    for line in lines:
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s;]+)\s*$", line)
        if match:
            name = match.group(1).lower().replace("_", "-")
            if name in wanted:
                pins[name] = match.group(2)
    missing = sorted(wanted - set(pins))
    if missing:
        raise MorphologyTrainingError(f"dependency contract missing exact pins: {missing}")
    return pins


def check_environment(
    *,
    cuda_required: bool = False,
    importer: Callable[[str], Any] | None = None,
    constraints_path: Path | None = None,
) -> dict[str, Any]:
    """Check the pinned cross-machine runtime; CUDA is required only for train."""
    if importer is None:
        import importlib
        importer = importlib.import_module
    pins = _constraint_versions(constraints_path or (ROOT / CONSTRAINTS))
    expected = {name: pins[name] for name in ("torch", "torchvision", "ultralytics", "numpy", "pandas")}
    modules: dict[str, Any] = {}
    actual: dict[str, str] = {}
    for package in expected:
        try:
            module = importer(package)
        except ImportError as exc:
            raise MorphologyTrainingError(f"required package unavailable: {package}") from exc
        modules[package] = module
        version = str(getattr(module, "__version__", ""))
        normalized = version.split("+", 1)[0] if package in ("torch", "torchvision") else version
        if normalized != expected[package]:
            raise MorphologyTrainingError(
                f"{package} version mismatch: expected {expected[package]}, found {version or 'unknown'}"
            )
        actual[package] = version
    cuda_available = bool(modules["torch"].cuda.is_available())
    if cuda_required and not cuda_available:
        raise MorphologyTrainingError("explicit train requires CUDA on the remote training host")
    return {"versions": actual, "cuda_available": cuda_available, "cuda_required": cuda_required}


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_lines(path: Path, rows: list[dict[str, Any]], dataset_root: Path) -> int:
    values = [str((dataset_root / row["image_path"]).resolve()) for row in rows]
    path.write_text("".join(value + "\n" for value in values), encoding="utf-8")
    return len(values)


def _training_gate(repo_root: Path, active_plan: Mapping[str, Any]) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if active_plan.get("training_eligible") is not True:
        blockers.append("active v6 plan training_eligible is not true")
    authorization = active_plan.get("owner_authorization")
    if not isinstance(authorization, dict) or authorization.get("training_authorized") is not True:
        blockers.append("active v6 plan has no explicit owner training authorization")
    # The registry is the project-level eligibility gate; read it without relying
    # on a YAML library so the command remains stdlib-only on the remote host.
    registry_path = repo_root / "experiments/registry.yaml"
    try:
        content = registry_path.read_text(encoding="utf-8")
    except OSError:
        blockers.append("experiments/registry.yaml unavailable")
    else:
        anchor = f"experiment_id: {active_plan.get('experiment_id')}"
        start = content.find(anchor)
        if start < 0:
            blockers.append("active experiment missing from registry")
        else:
            next_entry = content.find("\n  - experiment_id:", start + len(anchor))
            entry = content[start: next_entry if next_entry >= 0 else len(content)]
            if not re.search(r"(?m)^\s+training_eligible:\s*true\s*$", entry):
                blockers.append("registry training_eligible is not true")
    return not blockers, blockers


def create_preflight(
    dataset_root: Path,
    output_dir: Path | None = None,
    *,
    repo_root: Path = ROOT,
    env_result: Mapping[str, Any] | None = None,
    expected_eval_counts: Mapping[str, int] = EXPECTED_EVAL_COUNTS,
    split_boundaries: Mapping[str, datetime] | None = None,
) -> dict[str, Any]:
    """Audit first; only after every gate passes, atomically write training inputs."""
    dataset = dataset_root.resolve()
    repo = repo_root.resolve()
    output = (output_dir or (dataset / "training_package")).resolve()
    if output.exists():
        raise MorphologyTrainingError(f"refusing to overwrite existing preflight output: {output}")
    try:
        output.relative_to(dataset)
    except ValueError as exc:
        raise MorphologyTrainingError("preflight output must stay inside the dataset root") from exc

    audit = audit_dataset(dataset, repo_root=repo, expected_eval_counts=expected_eval_counts,
                          split_boundaries=split_boundaries)
    environment = dict(env_result or check_environment(cuda_required=False, constraints_path=repo / CONSTRAINTS))
    contract = _load_training_contract(repo)
    manifest = _read_manifest(dataset / "manifest.jsonl")
    train_rows = [r for r in manifest if r["split"] == "train"]
    val_rows = [r for r in manifest if r["split"] == "val" and r["variant"] == "P9" and r["evaluation_pool"] == "reference"]
    test_rows = [r for r in manifest if r["split"] == "test" and r["variant"] == "P9" and r["evaluation_pool"] == "reference"]
    challenge_val = [r for r in manifest if r["split"] == "val" and r["variant"] == "P9" and r["evaluation_pool"] == "grade_a_challenge"]
    challenge_test = [r for r in manifest if r["split"] == "test" and r["variant"] == "P9" and r["evaluation_pool"] == "grade_a_challenge"]
    stability = {
        f"robustness_{split}_{variant}_{pool}.txt": [
            r for r in manifest if r["split"] == split and r["variant"] == variant and r["evaluation_pool"] == pool
        ]
        for split in ("val", "test") for variant in ("P7", "P11") for pool in POOLS
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ma-morphology-preflight-", dir=output.parent) as temporary:
        stage = Path(temporary) / "package"
        stage.mkdir()
        list_counts = {
            "train.txt": _write_lines(stage / "train.txt", train_rows, dataset),
            "val.txt": _write_lines(stage / "val.txt", val_rows, dataset),
            "test.txt": _write_lines(stage / "test.txt", test_rows, dataset),
            "challenge_val.txt": _write_lines(stage / "challenge_val.txt", challenge_val, dataset),
            "challenge_test.txt": _write_lines(stage / "challenge_test.txt", challenge_test, dataset),
        }
        for name, selected in stability.items():
            list_counts[name] = _write_lines(stage / name, selected, dataset)
        data_yaml = (
            f"path: {_yaml_string(str(dataset))}\n"
            f"train: {_yaml_string(str((output / 'train.txt').resolve()))}\n"
            f"val: {_yaml_string(str((output / 'val.txt').resolve()))}\n"
            f"test: {_yaml_string(str((output / 'test.txt').resolve()))}\n"
            f"names: [{', '.join(CLASSES)}]\n"
        )
        (stage / "data.yaml").write_text(data_yaml, encoding="utf-8")
        training_allowed, blockers = _training_gate(repo, contract["active_plan"])
        argv = [sys.executable, "-m", "yoyo.datasets.ma_morphology_training_package", "train",
                "--dataset-root", str(dataset), "--preflight-dir", str(output)]
        receipt = {
            "schema": "ma-morphology-training-preflight-v1",
            "status": "dataset_ready" if training_allowed else "dataset_ready_training_blocked",
            "dataset_ready": True,
            "training_eligible": training_allowed,
            "training_blockers": blockers,
            "dataset_root": str(dataset),
            "manifest_sha256": audit["manifest_sha256"],
            "build_receipt_sha256": audit["build_receipt_sha256"],
            "source_commit": audit["source_commit"],
            "audit": audit,
            "environment": environment,
            "training_args": contract["training_args"],
            "base_model_path": str(contract["base_model_path"]),
            "base_model_sha256": contract["base_model_sha256"],
            "active_plan_sha256": contract["active_plan_sha256"],
            "recipe_plan_sha256": contract["recipe_plan_sha256"],
            "list_counts": list_counts,
            "list_sha256": {name: sha256_file(stage / name) for name in list_counts},
            "data_yaml_sha256": sha256_file(stage / "data.yaml"),
            "training_command_argv": argv,
            "training_command": shlex.join(argv),
            "production_eligible": False,
            "active_model_changed": False,
        }
        (stage / "preflight_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        try:
            stage.rename(output)
        except FileExistsError as exc:
            raise MorphologyTrainingError(f"refusing to overwrite existing preflight output: {output}") from exc
    return receipt


def _validate_preflight_artifacts(dataset: Path, preflight: Path, repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = audit_dataset(dataset, repo_root=repo)
    receipt = _read_json(preflight / "preflight_receipt.json")
    if receipt.get("dataset_ready") is not True or receipt.get("manifest_sha256") != audit["manifest_sha256"]:
        raise MorphologyTrainingError("preflight receipt does not bind the current dataset manifest")
    if receipt.get("training_eligible") is not True:
        raise MorphologyTrainingError("preflight recorded training as blocked; rerun after explicit gate update")
    for name, expected in receipt.get("list_sha256", {}).items():
        path = preflight / name
        if not path.is_file() or sha256_file(path) != expected:
            raise MorphologyTrainingError(f"preflight list missing or SHA mismatch: {name}")
    data_yaml = preflight / "data.yaml"
    if not data_yaml.is_file() or sha256_file(data_yaml) != receipt.get("data_yaml_sha256"):
        raise MorphologyTrainingError("data.yaml missing or SHA mismatch")
    contract = _load_training_contract(repo)
    if receipt.get("base_model_sha256") != contract["base_model_sha256"]:
        raise MorphologyTrainingError("preflight base model identity changed")
    if receipt.get("recipe_plan_sha256") != contract["recipe_plan_sha256"] or receipt.get("active_plan_sha256") != contract["active_plan_sha256"]:
        raise MorphologyTrainingError("training plan changed since preflight")
    return receipt, contract


def train(dataset_root: Path, preflight_dir: Path, *, repo_root: Path = ROOT) -> dict[str, Any]:
    """Run the frozen YOLO recipe only after explicit CLI selection and all gates."""
    dataset, preflight, repo = dataset_root.resolve(), preflight_dir.resolve(), repo_root.resolve()
    receipt, contract = _validate_preflight_artifacts(dataset, preflight, repo)
    allowed, blockers = _training_gate(repo, contract["active_plan"])
    if not allowed:
        raise MorphologyTrainingError("training is not authorized: " + "; ".join(blockers))
    environment = check_environment(cuda_required=True, constraints_path=repo / CONSTRAINTS)
    run_name = "exp-ma-morphology-v6-threeview-20260925-v1"
    project = dataset / "training_runs"
    run_dir = project / run_name
    if run_dir.exists():
        raise MorphologyTrainingError(f"refusing to overwrite existing training output: {run_dir}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise MorphologyTrainingError("ultralytics unavailable on the remote training host") from exc
    model = YOLO(str(contract["base_model_path"]))
    results = model.train(
        data=str(preflight / "data.yaml"),
        project=str(project),
        name=run_name,
        exist_ok=False,
        **contract["training_args"],
    )
    result = {
        "status": "training_finished",
        "run_dir": str(run_dir),
        "manifest_sha256": receipt["manifest_sha256"],
        "base_model_sha256": contract["base_model_sha256"],
        "training_args": contract["training_args"],
        "environment": environment,
        "result_type": type(results).__name__,
        "production_eligible": False,
        "active_model_changed": False,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "morphology_training_receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight", help="audit the merged dataset and write lists/data.yaml")
    preflight.add_argument("--dataset-root", type=Path, default=ROOT / DEFAULT_DATASET)
    preflight.add_argument("--output-dir", type=Path)
    run = sub.add_parser("train", help="explicitly train on the remote CUDA host")
    run.add_argument("--dataset-root", type=Path, default=ROOT / DEFAULT_DATASET)
    run.add_argument("--preflight-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = create_preflight(args.dataset_root, args.output_dir)
        else:
            preflight = args.preflight_dir or (args.dataset_root / "training_package")
            result = train(args.dataset_root, preflight)
    except MorphologyTrainingError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through CLI manually
    raise SystemExit(main())
