"""Fail-closed tests for local/RTX3060 YOLO dataset staging verification."""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
import yaml

from scripts.windows.verify_yolo_dataset import verify_dataset


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_bytes(rgb: tuple[int, int, int]) -> bytes:
    width, height = 2, 2
    row = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "dataset"
    for split in ("train", "val"):
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)

    rows: list[dict[str, object]] = []
    palette = iter(((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15), (16, 17, 18)))
    for split in ("train", "val"):
        for index, (kind, direction, class_id) in enumerate(
            (("positive", "LONG", 0), ("positive", "SHORT", 1), ("negative", None, None)),
            1,
        ):
            stem = f"{split}_{index}"
            image = root / "images" / split / f"{stem}.png"
            label = root / "labels" / split / f"{stem}.txt"
            image.write_bytes(_png_bytes(next(palette)))
            label.write_text(
                "" if class_id is None else f"{class_id} 0.5 0.5 0.5 0.5\n",
                encoding="utf-8",
            )
            row: dict[str, object] = {
                "dataset_sample_id": stem,
                "sample_kind": kind,
                "split": split,
                "image_path": image.relative_to(root).as_posix(),
                "label_path": label.relative_to(root).as_posix(),
                "image_sha256": _sha(image),
                "label_sha256": _sha(label),
            }
            if direction is None:
                row["negative_event_id"] = f"negative-{split}"
            else:
                row["event_id"] = f"positive-{split}-{direction}"
                row["direction"] = direction
            rows.append(row)

    manifest = root / "manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary = root / "build_summary.json"
    summary.write_text('{"passed": true}\n', encoding="utf-8")
    (root / "data.yaml").write_text(
        yaml.safe_dump(
            {
                "path": str(root),
                "train": "images/train",
                "val": "images/val",
                "names": {0: "dense_long", 1: "dense_short"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    prereg = tmp_path / "preregistration.json"
    prereg.write_text(
        json.dumps(
            {
                "experiment_id": "fixture",
                "immutable_inputs": {
                    "manifest_sha256": _sha(manifest),
                    "build_summary_sha256": _sha(summary),
                    "source_dimensions": [2, 2],
                    "counts": {
                        "train": {"images": 3, "labels": 3, "positive": 2, "negative": 1},
                        "val": {"images": 3, "labels": 3, "positive": 2, "negative": 1},
                        "total": {"images": 6, "labels": 6, "positive": 4, "negative": 2},
                    },
                    "class_instances_by_split": {
                        "train": {"0": 1, "1": 1},
                        "val": {"0": 1, "1": 1},
                    },
                    "events_by_split": {
                        "train": {"positive": 2, "negative": 1},
                        "val": {"positive": 2, "negative": 1},
                    },
                },
                "training": {"classes": {"0": "dense_long", "1": "dense_short"}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root, prereg


def _refresh_manifest_hash(root: Path, prereg: Path) -> None:
    payload = json.loads(prereg.read_text(encoding="utf-8"))
    payload["immutable_inputs"]["manifest_sha256"] = _sha(root / "manifest.jsonl")
    prereg.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_full_verifier_accepts_exact_two_class_dataset(tmp_path: Path) -> None:
    root, prereg = _write_fixture(tmp_path)

    receipt = verify_dataset(root, prereg, verify_file_hashes=True)

    assert receipt["passed"] is True
    assert receipt["manifest_rows"] == 6
    assert receipt["file_hashes_verified"] == 12
    assert receipt["source_dimensions"] == [2, 2]
    assert receipt["class_instances_by_split"] == {
        "train": {"0": 1, "1": 1},
        "val": {"0": 1, "1": 1},
    }


def test_verifier_rejects_pairwise_direction_label_swap_even_when_counts_match(
    tmp_path: Path,
) -> None:
    root, prereg = _write_fixture(tmp_path)
    manifest = root / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    train_positives = [row for row in rows if row["split"] == "train" and row["sample_kind"] == "positive"]
    train_positives[0]["direction"], train_positives[1]["direction"] = (
        train_positives[1]["direction"],
        train_positives[0]["direction"],
    )
    manifest.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    _refresh_manifest_hash(root, prereg)

    with pytest.raises(ValueError, match="direction versus label class"):
        verify_dataset(root, prereg, verify_file_hashes=True)


def test_verifier_rejects_corrupted_staged_image(tmp_path: Path) -> None:
    root, prereg = _write_fixture(tmp_path)
    image = root / "images" / "val" / "val_3.png"
    image.write_bytes(image.read_bytes() + b"corruption")

    with pytest.raises(ValueError, match="SHA-256"):
        verify_dataset(root, prereg, verify_file_hashes=True)
