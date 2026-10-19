"""Replay and export the exact YOLO train-loader image tensors for audit.

This audit reads only the frozen training split. It uses the run's saved
``args.yaml`` plus the immutable dataset and reproduces the Ultralytics 8.4.89
train data path through ``build_yolo_dataset`` and ``build_dataloader``. The
exported ``*_model_input.png`` files are lossless RGB representations of the
uint8 CHW tensors immediately before ``DetectionTrainer.preprocess_batch``;
that method only moves them to the device, casts to float, and divides by 255
because multi-scale training is disabled.

The source charts contain between 14 and 22 chronological 15-minute candles.
The loader may use only each chart and its YOLO label; it does not read later
OHLCV rows or the sealed holdout. The label overlay files are audit-only copies:
the model receives targets as separate tensors and never sees drawn boxes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
TARGET_COUNTS = {
    "positive_long": 2,
    "positive_short": 2,
    "negative_easy": 2,
    "negative_hard": 2,
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one file without changing it."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """Index the immutable manifest by dataset-relative image path."""

    rows: dict[str, dict[str, Any]] = {}
    with (dataset_dir / "manifest.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[row["image_path"]] = row
    return rows


def relative_image_path(path: str, dataset_dir: Path) -> str:
    """Normalize a loader image path to the manifest's POSIX spelling."""

    image = Path(path).resolve()
    return image.relative_to(dataset_dir.resolve()).as_posix()


def classify_sample(row: dict[str, Any]) -> str:
    """Map immutable manifest semantics to one of the four audit groups."""

    kind = row["sample_kind"]
    if kind == "positive_weak":
        return "positive_long" if int(row["class_id"]) == 0 else "positive_short"
    if kind == "negative_easy":
        return "negative_easy"
    if kind == "negative_hard":
        return "negative_hard"
    raise ValueError(f"unexpected sample_kind: {kind}")


def tensor_rgb(image_tensor: Any) -> np.ndarray:
    """Convert an Ultralytics uint8 RGB CHW tensor to an HWC array losslessly."""

    array = image_tensor.detach().cpu().numpy()
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[0] != 3:
        raise AssertionError(f"unexpected loader tensor: dtype={array.dtype}, shape={array.shape}")
    return np.ascontiguousarray(array.transpose(1, 2, 0))


def targets_for_image(batch: dict[str, Any], image_index: int) -> list[dict[str, Any]]:
    """Extract normalized xywh targets belonging to one image in a batch."""

    indices = batch["batch_idx"].detach().cpu().numpy().astype(int).reshape(-1)
    classes = batch["cls"].detach().cpu().numpy().astype(int).reshape(-1)
    boxes = batch["bboxes"].detach().cpu().numpy().reshape(-1, 4)
    return [
        {"class_id": int(classes[i]), "xywh_normalized": [float(x) for x in boxes[i]]}
        for i in np.flatnonzero(indices == image_index)
    ]


def draw_targets(rgb: np.ndarray, targets: list[dict[str, Any]]) -> Image.Image:
    """Draw a display-only target overlay on a copy of one model input."""

    image = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(image)
    width, height = image.size
    font = ImageFont.load_default()
    for target in targets:
        cx, cy, bw, bh = target["xywh_normalized"]
        x1 = (cx - bw / 2) * width
        y1 = (cy - bh / 2) * height
        x2 = (cx + bw / 2) * width
        y2 = (cy + bh / 2) * height
        label = CLASS_NAMES[target["class_id"]]
        draw.rectangle((x1, y1, x2, y2), outline=(255, 196, 0), width=5)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_w = text_box[2] - text_box[0]
        text_h = text_box[3] - text_box[1]
        label_y = max(0, y1 - text_h - 8)
        draw.rectangle((x1, label_y, x1 + text_w + 8, label_y + text_h + 8), fill=(255, 196, 0))
        draw.text((x1 + 4, label_y + 4), label, fill=(25, 25, 25), font=font)
    return image


def fit_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit an image into a white panel without changing its aspect ratio."""

    panel = Image.new("RGB", size, "white")
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - copy.width) // 2
    y = (size[1] - copy.height) // 2
    panel.paste(copy, (x, y))
    return panel


def make_contact_sheet(
    samples: list[dict[str, Any]],
    output_path: Path,
    title: str,
) -> None:
    """Render source, exact input, and display-only label overlay side by side."""

    panel_width, panel_height = 480, 310
    margin, title_height, row_label_height, gap = 20, 60, 42, 12
    row_height = row_label_height + panel_height + gap
    canvas_width = margin * 2 + panel_width * 3 + gap * 2
    canvas_height = title_height + row_height * len(samples) + margin
    canvas = Image.new("RGB", (canvas_width, canvas_height), (238, 241, 245))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((margin, 18), title, fill=(18, 30, 45), font=font)
    headings = (
        "SOURCE PNG ON DISK (1280x742)",
        "MODEL INPUT TENSOR (NO DRAWN BOX)",
        "TARGET OVERLAY (DISPLAY ONLY)",
    )
    for column, heading in enumerate(headings):
        x = margin + column * (panel_width + gap)
        draw.text((x, 40), heading, fill=(49, 63, 78), font=font)

    for row_index, sample in enumerate(samples):
        y = title_height + row_index * row_height
        class_text = sample["group"].replace("_", " ")
        detail = (
            f'{row_index + 1}. {class_text} | {sample["symbol"]} | '
            f'{sample["sample_id"]} | batch {sample["batch_number"]}, slot {sample["batch_slot"]}'
        )
        draw.text((margin, y + 10), detail, fill=(18, 30, 45), font=font)
        paths = (
            Path(sample["source_copy_path"]),
            Path(sample["model_input_path"]),
            Path(sample["overlay_path"]),
        )
        for column, path in enumerate(paths):
            x = margin + column * (panel_width + gap)
            panel = fit_image(Image.open(path).convert("RGB"), (panel_width, panel_height))
            canvas.paste(panel, (x, y + row_label_height))
            draw.rectangle(
                (x, y + row_label_height, x + panel_width - 1, y + row_label_height + panel_height - 1),
                outline=(185, 192, 201),
                width=1,
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--args-yaml", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-batches", type=int, default=128)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    output_dir = args.out_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    from ultralytics import __version__ as ultralytics_version
    from ultralytics.cfg import DEFAULT_CFG, get_cfg
    from ultralytics.data import build_dataloader, build_yolo_dataset
    from ultralytics.data.utils import check_det_dataset
    from ultralytics.utils import YAML
    from ultralytics.utils.torch_utils import init_seeds

    run_args = YAML.load(args.args_yaml)
    # Preserve every data-path and augmentation argument while replacing only
    # remote paths irrelevant to constructing the loader.
    run_args["data"] = str(dataset_dir / "data.yaml")
    run_args["model"] = "yolo11s.pt"
    run_args["project"] = str(output_dir / "unused_runs")
    run_args["device"] = "cpu"
    cfg = get_cfg(DEFAULT_CFG, run_args)
    if int(cfg.imgsz) != 1280 or int(cfg.seed) != 0 or not bool(cfg.deterministic):
        raise AssertionError("this audit requires the saved 1280/seed0/deterministic run contract")
    if cfg.multi_scale not in {0, 0.0, False}:
        raise AssertionError("multi-scale would add a later resize not captured by this exporter")

    manifest = load_manifest(dataset_dir)
    data = check_det_dataset(str(dataset_dir / "data.yaml"))
    init_seeds(int(cfg.seed) + 1, deterministic=bool(cfg.deterministic))
    train_dataset = build_yolo_dataset(
        cfg,
        data["train"],
        int(cfg.batch),
        data,
        mode="train",
        rect=False,
        stride=32,
    )
    train_loader = build_dataloader(
        train_dataset,
        batch=int(cfg.batch),
        workers=args.workers,
        shuffle=True,
        rank=-1,
    )

    selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    batches_seen = 0
    for batch_number, batch in enumerate(train_loader, start=1):
        batches_seen = batch_number
        for slot, image_path_raw in enumerate(batch["im_file"]):
            relative = relative_image_path(image_path_raw, dataset_dir)
            row = manifest[relative]
            group = classify_sample(row)
            if len(selected[group]) >= TARGET_COUNTS[group]:
                continue
            targets = targets_for_image(batch, slot)
            if group.startswith("positive_") and len(targets) != 1:
                continue
            if group.startswith("negative_") and targets:
                raise AssertionError(f"negative loader sample unexpectedly has targets: {relative}")

            source_path = dataset_dir / relative
            actual_source_hash = sha256_file(source_path)
            if actual_source_hash != row["image_sha256"]:
                raise AssertionError(f"source image hash mismatch: {relative}")
            label_path = dataset_dir / row["label_path"]
            actual_label_hash = sha256_file(label_path)
            if actual_label_hash != row["label_sha256"]:
                raise AssertionError(f"label hash mismatch: {row['label_path']}")

            prefix = f"{group}_{len(selected[group]) + 1:02d}_{row['sample_id']}"
            source_copy = output_dir / f"{prefix}_source.png"
            model_input = output_dir / f"{prefix}_model_input.png"
            overlay = output_dir / f"{prefix}_label_overlay.png"
            shutil.copyfile(source_path, source_copy)
            rgb = tensor_rgb(batch["img"][slot])
            Image.fromarray(rgb).save(model_input, format="PNG", optimize=False)
            draw_targets(rgb, targets).save(overlay, format="PNG", optimize=False)
            with Image.open(source_path) as source_image:
                source_size = list(source_image.size)

            sample = {
                "group": group,
                "sample_id": row["sample_id"],
                "symbol": row["symbol"],
                "sample_kind": row["sample_kind"],
                "class_id": row.get("class_id"),
                "class_name": row.get("class_name"),
                "batch_number": batch_number,
                "batch_slot": slot,
                "source_relative_path": relative,
                "source_size_wh": source_size,
                "source_sha256": actual_source_hash,
                "label_relative_path": row["label_path"],
                "label_sha256": actual_label_hash,
                "model_input_size_wh": [int(rgb.shape[1]), int(rgb.shape[0])],
                "model_input_sha256": sha256_file(model_input),
                "targets_after_augmentation": targets,
                "source_copy_path": str(source_copy),
                "source_copy_sha256": sha256_file(source_copy),
                "model_input_path": str(model_input),
                "overlay_path": str(overlay),
                "overlay_sha256": sha256_file(overlay),
            }
            if sample["source_copy_sha256"] != sample["source_sha256"]:
                raise AssertionError("source copy was not byte-identical")
            selected[group].append(sample)
        if all(len(selected[key]) >= count for key, count in TARGET_COUNTS.items()):
            break
        if batch_number >= args.max_batches:
            break

    missing = {key: TARGET_COUNTS[key] - len(selected[key]) for key in TARGET_COUNTS if len(selected[key]) < TARGET_COUNTS[key]}
    if missing:
        raise RuntimeError(f"not enough requested sample groups after {batches_seen} batches: {missing}")

    positives = selected["positive_long"] + selected["positive_short"]
    negatives = selected["negative_easy"] + selected["negative_hard"]
    positive_sheet = output_dir / "positive_actual_model_inputs.png"
    negative_sheet = output_dir / "negative_actual_model_inputs.png"
    make_contact_sheet(positives, positive_sheet, "POSITIVE TRAIN SAMPLES — EXACT FIRST-EPOCH LOADER REPLAY")
    make_contact_sheet(negatives, negative_sheet, "NEGATIVE TRAIN SAMPLES — EXACT FIRST-EPOCH LOADER REPLAY")

    receipt = {
        "protocol": "ma_launch_t3_imgsz1280_actual_loader_input_audit_v1",
        "ultralytics_version": ultralytics_version,
        "dataset_dir": str(dataset_dir),
        "dataset_manifest_sha256": sha256_file(dataset_dir / "manifest.jsonl"),
        "saved_run_args_path": str(args.args_yaml.resolve()),
        "saved_run_args_sha256": sha256_file(args.args_yaml.resolve()),
        "loader_contract": {
            "split": "train_only",
            "imgsz": int(cfg.imgsz),
            "batch": int(cfg.batch),
            "workers": int(args.workers),
            "seed": int(cfg.seed),
            "deterministic": bool(cfg.deterministic),
            "rect_requested": bool(cfg.rect),
            "rect_effective": bool(train_dataset.rect),
            "augment": bool(train_dataset.augment),
            "translate": float(cfg.translate),
            "scale": float(cfg.scale),
            "multi_scale": float(cfg.multi_scale),
            "fliplr": float(cfg.fliplr),
            "flipud": float(cfg.flipud),
            "mosaic": float(cfg.mosaic),
            "mixup": float(cfg.mixup),
            "hsv_h": float(cfg.hsv_h),
            "hsv_s": float(cfg.hsv_s),
            "hsv_v": float(cfg.hsv_v),
            "preprocess_after_export": "to(device).float() / 255; no multi-scale resize",
        },
        "historical_snapshot_present": False,
        "historical_snapshot_note": (
            "The completed run used plots=false and retained no train_batch JPG or per-batch tensor hashes. "
            "These files are a deterministic replay on the frozen inputs and exact saved loader contract, "
            "not an independently archived historical runtime snapshot."
        ),
        "batches_seen": batches_seen,
        "samples": positives + negatives,
        "contact_sheets": {
            "positive": {
                "path": str(positive_sheet),
                "sha256": sha256_file(positive_sheet),
            },
            "negative": {
                "path": str(negative_sheet),
                "sha256": sha256_file(negative_sheet),
            },
        },
        "safety": {
            "holdout_read": False,
            "training_started": False,
            "weights_changed": False,
            "active_or_frozen_changed": False,
            "promotion_or_deployment": False,
        },
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(receipt_path), "samples": len(receipt["samples"]), "batches_seen": batches_seen}))


if __name__ == "__main__":
    main()
