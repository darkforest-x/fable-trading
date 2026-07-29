"""Prepare and verify the full-frame ETH 3m v2 classification view.

Inputs are limited to the already-audited ``train/`` and ``val/`` rows in the
source manifest.  Weak/review and continuous-smoke rows are intentionally not
read: they are evidence/evaluation material, not training labels.

The source charts are 1280x742.  Ultralytics classification normally applies a
square crop, which can erase the right-edge decision tip.  This module instead
materializes a deterministic square, white-padded PNG while preserving the
entire chart.  The training entrypoint also uses a deterministic full-frame
transform, so neither train nor validation can crop the right edge.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

PROJECT = Path(__file__).resolve().parents[2]
SOURCE_DATASET = PROJECT / "datasets/eth_3m_short_pilot_v2"
OUTPUT_DATASET = PROJECT / "datasets/eth_3m_short_pilot_v2_cls_letterbox960"
VALIDATION_RECEIPT = PROJECT / "analysis/output/eth3m_short_pilot_v2_dataset/validation.json"
PREREG = PROJECT / "analysis/eth3m_short_pilot_v2_cls_prereg.json"
IMAGE_SIZE = 960
EXPECTED_CLASSES = {"no_start": 0, "short_start": 1}
EXPECTED_COUNTS = {
    ("train", "no_start"): 73,
    ("train", "short_start"): 22,
    ("val", "no_start"): 34,
    ("val", "short_start"): 8,
}


def sha256(path: Path) -> str:
    """Return a streaming SHA256 digest for an immutable input or artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def validate_authorization(prereg_path: Path = PREREG) -> dict[str, Any]:
    """Require the frozen owner authorization and experiment recipe."""
    data = load_json(prereg_path)
    auth = data.get("owner_authorization", {})
    recipe = data.get("training", {})
    gates = data.get("acceptance_gates", {})
    if auth.get("diagnostic_training") is not True:
        raise ValueError("prereg lacks owner diagnostic-training authorization")
    if auth.get("concurrent_3060_run") is not True:
        raise ValueError("prereg lacks owner concurrent-3060 authorization")
    if auth.get("owner_exact_words") != "直接去3060跑吧":
        raise ValueError("unexpected owner authorization wording")
    expected_recipe = {
        "model": "yolo11n-cls.pt",
        "model_sha256": "c62d41bf9625777760018bf914d2e6cd472420ccd01706d97a61cb6c82502bd7",
        "pretrained": True,
        "imgsz": IMAGE_SIZE,
        "batch": 4,
        "epochs": 100,
        "patience": 20,
        "seed": 42,
        "deterministic": True,
        "device": 0,
        "workers": 4,
        "cache": False,
    }
    for key, value in expected_recipe.items():
        if recipe.get(key) != value:
            raise ValueError(f"preregistered training.{key} must be {value!r}")
    expected_aug = {
        "fliplr": 0.0,
        "flipud": 0.0,
        "hsv_h": 0.0,
        "hsv_s": 0.0,
        "hsv_v": 0.0,
        "scale": 0.0,
        "translate": 0.0,
        "erasing": 0.0,
        "auto_augment": None,
    }
    if recipe.get("augmentations") != expected_aug:
        raise ValueError("preregistered augmentation block changed")
    if gates.get("probability_threshold") != 0.5 or gates.get("threshold_sweep") is not False:
        raise ValueError("acceptance threshold must remain frozen at 0.50 without a sweep")
    if data.get("prohibitions", {}).get("holdout_read") is not True:
        raise ValueError("prereg must prohibit holdout reads")
    return data


def validate_source(
    source: Path = SOURCE_DATASET,
    validation_path: Path = VALIDATION_RECEIPT,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Validate the audited source without opening weak, smoke, or holdout data."""
    source = source.resolve()
    validation = load_json(validation_path)
    meta = load_json(source / "build_meta.json")
    rows = read_manifest(source / "manifest.csv")
    if validation.get("status") != "passed" or validation.get("holdout_read") is not False:
        raise ValueError("source independent validation is not a clean pass")
    if validation.get("model_trained") is not False:
        raise ValueError("source validation receipt unexpectedly reports prior training")
    receipt_dataset = Path(validation.get("dataset", "")).resolve()
    if receipt_dataset != source:
        raise ValueError("validation receipt points at a different dataset")
    status = meta.get("status", {})
    if not (meta.get("diagnostic_pilot_only") and status.get("diagnostic_pilot_only")):
        raise ValueError("source must remain explicitly diagnostic")
    if meta.get("formal_gold_dataset") or meta.get("promotion_eligible"):
        raise ValueError("source is not allowed to be formal or promotion-eligible")
    if meta.get("task") != "image_classification_current_tip_short_start":
        raise ValueError("unexpected source task")
    if load_json(source / "classes.json") != EXPECTED_CLASSES:
        raise ValueError("class mapping changed")
    counts = Counter((row["split"], row["class_name"]) for row in rows)
    if counts != Counter(EXPECTED_COUNTS):
        raise ValueError(f"source train/val counts changed: {dict(counts)}")
    for row in rows:
        rel = Path(row["image_rel"])
        if row["split"] not in {"train", "val"} or rel.parts[:2] != (
            row["split"], row["class_name"]
        ):
            raise ValueError(f"non train/val or mismatched manifest row: {rel}")
        image = source / rel
        if sha256(image) != row["image_sha256"]:
            raise ValueError(f"source image hash mismatch: {rel}")
    actual = {
        path.relative_to(source).as_posix()
        for split in ("train", "val")
        for path in (source / split).glob("*/*")
        if path.is_file()
    }
    expected = {row["image_rel"] for row in rows}
    if actual != expected:
        raise ValueError("source class directories differ from manifest")
    return validation, meta, rows


def letterbox_full_frame(source: Path, destination: Path, size: int = IMAGE_SIZE) -> dict[str, int]:
    """Resize a whole chart into a white square; never crop either time edge."""
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    src_w, src_h = image.size
    scale = min(size / src_w, size / src_h)
    dst_w = max(1, int(src_w * scale + 0.5))
    dst_h = max(1, int(src_h * scale + 0.5))
    resized = image.resize((dst_w, dst_h), Image.Resampling.LANCZOS)
    left = (size - dst_w) // 2
    top = (size - dst_h) // 2
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    canvas.paste(resized, (left, top))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG", optimize=False, compress_level=9)
    return {
        "source_width": src_w,
        "source_height": src_h,
        "resized_width": dst_w,
        "resized_height": dst_h,
        "pad_left": left,
        "pad_top": top,
        "pad_right": size - dst_w - left,
        "pad_bottom": size - dst_h - top,
    }


def verify_prepared(output: Path = OUTPUT_DATASET) -> dict[str, Any]:
    """Verify every derived hash, geometry, class, split, and exclusion rule."""
    output = output.resolve()
    meta = load_json(output / "build_meta.json")
    rows = read_manifest(output / "manifest.csv")
    if meta.get("diagnostic_pilot_only") is not True or meta.get("promotion_eligible") is not False:
        raise ValueError("prepared metadata lost diagnostic-only status")
    if meta.get("image_size") != IMAGE_SIZE or meta.get("full_frame_preserved") is not True:
        raise ValueError("prepared full-frame geometry contract changed")
    if any((output / name).exists() for name in ("weak_or_review", "smoke", "holdout")):
        raise ValueError("non-training material leaked into prepared dataset")
    counts = Counter((row["split"], row["class_name"]) for row in rows)
    if counts != Counter(EXPECTED_COUNTS):
        raise ValueError(f"prepared train/val counts changed: {dict(counts)}")
    expected_files: set[str] = set()
    for row in rows:
        path = output / row["image_rel"]
        rel = Path(row["image_rel"])
        expected_target = EXPECTED_CLASSES.get(row["class_name"])
        if rel.parts[:2] != (row["split"], row["class_name"]) or row["target"] != str(expected_target):
            raise ValueError(f"prepared manifest class/path/target mismatch: {rel}")
        expected_files.add(rel.as_posix())
        if sha256(path) != row["prepared_sha256"]:
            raise ValueError(f"prepared image hash mismatch: {path}")
        with Image.open(path) as image:
            if image.mode != "RGB" or image.size != (IMAGE_SIZE, IMAGE_SIZE):
                raise ValueError(f"prepared image is not RGB {IMAGE_SIZE}x{IMAGE_SIZE}: {path}")
        if int(row["pad_left"]) != 0 or int(row["resized_width"]) != IMAGE_SIZE:
            raise ValueError("right-edge time axis is not preserved against the square boundary")
    actual_files = {
        path.relative_to(output).as_posix()
        for split in ("train", "val")
        for path in (output / split).glob("*/*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("prepared class directories differ from the hash manifest")
    for split in ("train", "val"):
        class_dirs = {path.name for path in (output / split).iterdir() if path.is_dir()}
        if class_dirs != set(EXPECTED_CLASSES):
            raise ValueError(f"unexpected class directory under {split}: {sorted(class_dirs)}")
    return meta
