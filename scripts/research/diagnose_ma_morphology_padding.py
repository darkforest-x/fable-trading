"""Isolate validator padding with the frozen A checkpoint, images and labels.

Ultralytics 8.4.89 uses pad=0.5 for validation versus pad=0 for rectangular
training. The control changes only that padding; it does not retrain, relabel,
select checkpoints or change detection thresholds. CPU inference avoids the
ongoing RTX3060 B run. All data copies and outputs stay in a diagnostic folder.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import torch
import ultralytics
from ultralytics import YOLO
from ultralytics.data.dataset import YOLODataset
from ultralytics.models.yolo.detect.val import DetectionValidator

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "datasets/ma_launch_owner1500_morph_v6"
OUT = ROOT / "experiments/active/exp-ma-morphology-negatives-20260922-v3/evaluation_diagnosis"


class RecordingValidator(DetectionValidator):
    """Record actual input geometry without altering standard validation."""

    pad_override = None

    def build_dataset(self, img_path, mode="val", batch=None):
        if self.pad_override is None:
            dataset = super().build_dataset(img_path, mode=mode, batch=batch)
        else:
            cfg = self.args
            dataset = YOLODataset(
                img_path=img_path, imgsz=cfg.imgsz, batch_size=batch,
                augment=False, hyp=cfg, rect=cfg.rect, cache=cfg.cache or None,
                single_cls=cfg.single_cls or False, stride=self.stride,
                pad=self.pad_override, prefix="diagnostic: ", task=cfg.task,
                classes=cfg.classes, data=self.data, fraction=1.0,
            )
        self.geometry = {
            "dataset_pad": dataset.pad,
            "batch_shapes_hw": sorted({tuple(map(int, x)) for x in dataset.batch_shapes}),
            "first_im_files": dataset.im_files[:3],
        }
        return dataset

    def preprocess(self, batch):
        batch = super().preprocess(batch)
        if "first_tensor_bchw" not in self.geometry:
            self.geometry["first_tensor_bchw"] = list(batch["img"].shape)
            self.geometry["first_ratio_pad"] = batch["ratio_pad"][0]
        return batch

    def finalize_metrics(self):
        super().finalize_metrics()
        (self.save_dir / "geometry.json").write_text(json.dumps(self.geometry, indent=2))


class AlignedValidator(RecordingValidator):
    """Change only the padding to the existing training/predict geometry."""

    pad_override = 0.0


def main():
    torch.set_num_threads(4)
    rows = [json.loads(line) for line in (DATA / "manifest.jsonl").read_text().splitlines()]
    rows = [r for r in rows if r["split"] in ("val", "test") and r["variant"] == "A"]
    snapshot = json.loads((OUT / "remote_snapshot.json").read_text())
    weight = OUT / "arm_A_best.pt"
    digest = hashlib.sha256(weight.read_bytes()).hexdigest()
    assert digest == snapshot["receipt"]["arms"]["A"]["best_sha256"]
    # Isolated labels prevent diagnostic Ultralytics caches touching frozen data.
    data_copy = OUT / "data"
    for row in rows:
        image, label = data_copy / row["image_path"], data_copy / row["label_path"]
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        if not image.exists():
            image.symlink_to(DATA / row["image_path"])
        original = DATA / row["label_path"]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == row["label_sha256"]
        shutil.copyfile(original, label)
    yaml = OUT / "diagnostic.yaml"
    yaml.write_text(f"path: {data_copy}\ntrain: images/val\nval: images/val\ntest: images/test\nnames: [dense_launch_long, dense_launch_short]\n")
    output = {"weight_sha256": digest, "ultralytics": ultralytics.__version__,
              "torch": torch.__version__, "device": "cpu", "runs": {}}
    for name, validator in (("default_pad05", RecordingValidator), ("aligned_pad0", AlignedValidator)):
        for split in ("val", "test"):
            run = f"{name}_{split}"
            metrics = YOLO(str(weight)).val(
                validator=validator, data=str(yaml), split=split, imgsz=1280,
                batch=8, device="cpu", conf=0.001, iou=0.7, max_det=300,
                rect=True, workers=0, augment=False, plots=False, verbose=False,
                save_json=True, project=str(OUT / "runs"), name=run, exist_ok=False,
            )
            result_dir = OUT / "runs" / run
            output["runs"][run] = {
                "metrics": metrics.results_dict,
                "geometry": json.loads((result_dir / "geometry.json").read_text()),
                "prediction_sha256": hashlib.sha256((result_dir / "predictions.json").read_bytes()).hexdigest(),
            }
            (OUT / "padding_comparison.json").write_text(json.dumps(output, indent=2))
            print("DIAGNOSTIC_RESULT " + json.dumps({run: output["runs"][run]}), flush=True)


if __name__ == "__main__":
    main()
