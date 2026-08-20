from pathlib import Path
from types import SimpleNamespace

import pytest

# The script under test imports torch and ultralytics at module level -- it
# evaluates a YOLO checkpoint, so that is not incidental. Where the GPU stack is
# absent (CI, a fresh clone), skipping says so; a collection error would report
# it as a broken test suite instead.
pytest.importorskip("torch", reason="the script under test evaluates a YOLO checkpoint")
pytest.importorskip("ultralytics", reason="the script under test evaluates a YOLO checkpoint")

from scripts.eval_owner_short_gold_center_model import metric_receipt, resolve_dataset  # noqa: E402


def test_resolve_dataset_requires_paired_rendered_val(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    images = root / "images" / "val"
    labels = root / "labels" / "val"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    (images / "positive.png").write_bytes(b"rendered")
    (labels / "positive.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    data = root / "data.yaml"
    data.write_text(
        f"path: {root}\ntrain: images/train\nval: images/val\nnames:\n  0: target\n",
        encoding="utf-8",
    )

    dataset_root, val_images, image_paths, label_paths = resolve_dataset(data)

    assert dataset_root == root
    assert val_images == images
    assert [path.name for path in image_paths] == ["positive.png"]
    assert [path.name for path in label_paths] == ["positive.txt"]


def test_metric_receipt_uses_detection_box_metrics() -> None:
    result = SimpleNamespace(
        box=SimpleNamespace(mp=0.8, mr=0.7, map50=0.75, map=0.6), fitness=0.6
    )

    assert metric_receipt(result) == {
        "precision": 0.8,
        "recall": 0.7,
        "map50": 0.75,
        "map50_95": 0.6,
        "fitness": 0.6,
    }


def test_resolve_dataset_rejects_missing_label(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    images = root / "images" / "val"
    (root / "labels" / "val").mkdir(parents=True)
    images.mkdir(parents=True)
    (images / "orphan.png").write_bytes(b"rendered")
    data = root / "data.yaml"
    data.write_text(f"path: {root}\nval: images/val\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid frozen val pairing"):
        resolve_dataset(data)
