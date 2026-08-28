r"""Fail-closed verifier for the exact YOLO files staged on a training host.

The verifier reads only dataset metadata, PNG/TXT bytes and the immutable
training preregistration. It never reads market data, holdout rows, model
weights or future labels. Run the same file on the Mac source tree and on the
Windows staging copy so a correct local manifest cannot hide an incomplete or
corrupted remote transfer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest without loading a PNG into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Read the lossless PNG canvas dimensions from the fixed IHDR header."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"not a valid PNG header: {path}")
    return struct.unpack(">II", header[16:24])


def _normalise_names(raw: Any) -> dict[str, str]:
    if isinstance(raw, list):
        return {str(i): str(name) for i, name in enumerate(raw)}
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    raise ValueError(f"data.yaml names must be a list or mapping, got {type(raw).__name__}")


def _int_mapping(raw: Mapping[str, Any], label: str) -> dict[str, int]:
    try:
        return {str(key): int(value) for key, value in raw.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must map keys to integers") from exc


def _safe_dataset_path(dataset_root: Path, raw: str, label: str) -> Path:
    path = (dataset_root / raw).resolve()
    try:
        path.relative_to(dataset_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes dataset root: {raw}") from exc
    return path


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def _iter_manifest(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid manifest JSON on line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"manifest line {line_number} is not an object")
            yield row


def verify_dataset(
    dataset_dir: Path,
    preregistration_path: Path,
    *,
    verify_file_hashes: bool,
) -> dict[str, Any]:
    """Verify the complete image/label/class/event contract for one dataset.

    Columns read from the manifest are identity and lineage fields only:
    ``dataset_sample_id``, ``sample_kind``, ``split``, event ids, relative file
    paths and SHA-256 digests. No OHLCV or future-value feature is read.
    """
    root = dataset_dir.resolve()
    prereg = json.loads(preregistration_path.read_text(encoding="utf-8"))
    immutable = prereg["immutable_inputs"]
    training = prereg["training"]
    expected_counts = immutable["counts"]
    expected_dimensions = tuple(int(value) for value in immutable["source_dimensions"])
    if len(expected_dimensions) != 2 or min(expected_dimensions) <= 0:
        raise ValueError("immutable_inputs.source_dimensions must be [width, height]")
    expected_classes = _normalise_names(training["classes"])
    expected_class_by_split = {
        str(split): _int_mapping(counts, f"class_instances_by_split.{split}")
        for split, counts in immutable["class_instances_by_split"].items()
    }
    expected_events = {
        str(split): _int_mapping(counts, f"events_by_split.{split}")
        for split, counts in immutable["events_by_split"].items()
    }

    manifest_path = root / "manifest.jsonl"
    summary_path = root / "build_summary.json"
    yaml_path = root / "data.yaml"
    for path in (root, manifest_path, summary_path, yaml_path):
        if not path.exists():
            raise ValueError(f"required dataset input is missing: {path}")

    _assert_equal(
        sha256_file(manifest_path), immutable["manifest_sha256"], "manifest SHA-256"
    )
    _assert_equal(
        sha256_file(summary_path),
        immutable["build_summary_sha256"],
        "build summary SHA-256",
    )

    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(yaml_payload, dict):
        raise ValueError("data.yaml must decode to a mapping")
    actual_classes = _normalise_names(yaml_payload.get("names"))
    _assert_equal(actual_classes, expected_classes, "YOLO class names")

    file_counts: dict[str, dict[str, int]] = {}
    label_class_by_split: dict[str, Counter[str]] = {}
    label_class_by_path: dict[str, str | None] = {}
    actual_paths: set[str] = set()
    positive_total = 0
    negative_total = 0
    for split in ("train", "val"):
        image_dir = root / "images" / split
        label_dir = root / "labels" / split
        images = sorted(image_dir.glob("*.png"))
        labels = sorted(label_dir.glob("*.txt"))
        image_stems = {path.stem for path in images}
        label_stems = {path.stem for path in labels}
        _assert_equal(image_stems, label_stems, f"{split} image/label stem set")

        for image_path in images:
            _assert_equal(
                _png_dimensions(image_path),
                expected_dimensions,
                f"PNG dimensions in {image_path}",
            )

        positive = 0
        negative = 0
        classes: Counter[str] = Counter()
        for label_path in labels:
            text = label_path.read_text(encoding="utf-8").strip()
            relative_label = label_path.relative_to(root).as_posix()
            if not text:
                negative += 1
                label_class_by_path[relative_label] = None
                continue
            rows = text.splitlines()
            _assert_equal(len(rows), 1, f"boxes in {label_path}")
            fields = rows[0].split()
            _assert_equal(len(fields), 5, f"YOLO fields in {label_path}")
            class_value = float(fields[0])
            class_id = int(class_value)
            if class_value != class_id or str(class_id) not in expected_classes:
                raise ValueError(f"unknown/non-integer class {fields[0]!r} in {label_path}")
            coords = [float(value) for value in fields[1:]]
            if not all(math.isfinite(value) for value in coords):
                raise ValueError(f"non-finite box coordinate in {label_path}")
            cx, cy, width, height = coords
            if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
                raise ValueError(f"box centre outside image in {label_path}: {coords}")
            if not (0.0 < width <= 1.0 and 0.0 < height <= 1.0):
                raise ValueError(f"invalid box size in {label_path}: {coords}")
            tolerance = 1e-6
            if (
                cx - width / 2 < -tolerance
                or cx + width / 2 > 1.0 + tolerance
                or cy - height / 2 < -tolerance
                or cy + height / 2 > 1.0 + tolerance
            ):
                raise ValueError(f"box crosses image boundary in {label_path}: {coords}")
            positive += 1
            classes[str(class_id)] += 1
            label_class_by_path[relative_label] = str(class_id)

        expected_split = expected_counts[split]
        _assert_equal(len(images), int(expected_split["images"]), f"{split} images")
        _assert_equal(len(labels), int(expected_split["labels"]), f"{split} labels")
        _assert_equal(positive, int(expected_split["positive"]), f"{split} positive labels")
        _assert_equal(negative, int(expected_split["negative"]), f"{split} empty labels")
        _assert_equal(dict(classes), expected_class_by_split[split], f"{split} class counts")
        positive_total += positive
        negative_total += negative
        label_class_by_split[split] = classes
        file_counts[split] = {
            "images": len(images),
            "labels": len(labels),
            "positive": positive,
            "negative": negative,
        }
        actual_paths.update(path.relative_to(root).as_posix() for path in images)
        actual_paths.update(path.relative_to(root).as_posix() for path in labels)

    manifest_counts: Counter[tuple[str, str]] = Counter()
    manifest_class_counts: Counter[tuple[str, str]] = Counter()
    event_ids: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    sample_ids: set[str] = set()
    manifest_paths: set[str] = set()
    image_hashes: set[str] = set()
    rows = 0
    hashes_verified = 0
    for row in _iter_manifest(manifest_path):
        rows += 1
        split = str(row["split"])
        kind = str(row["sample_kind"])
        if split not in {"train", "val"} or kind not in {"positive", "negative"}:
            raise ValueError(f"unexpected manifest split/kind: {split}/{kind}")
        sample_id = str(row["dataset_sample_id"])
        if sample_id in sample_ids:
            raise ValueError(f"duplicate dataset_sample_id: {sample_id}")
        sample_ids.add(sample_id)
        manifest_counts[(split, kind)] += 1
        if kind == "positive":
            event_id = str(row["event_id"])
            direction = str(row["direction"])
            if direction not in {"LONG", "SHORT"}:
                raise ValueError(f"unexpected positive direction: {direction!r}")
            class_id = "0" if direction == "LONG" else "1"
            manifest_class_counts[(split, class_id)] += 1
        else:
            event_id = str(row["negative_event_id"])
        event_ids[(split, kind)].add(event_id)

        for path_key, sha_key in (
            ("image_path", "image_sha256"),
            ("label_path", "label_sha256"),
        ):
            relative = str(row[path_key])
            expected_prefix = f"{'images' if path_key == 'image_path' else 'labels'}/{split}/"
            if not relative.startswith(expected_prefix):
                raise ValueError(
                    f"manifest {path_key} is outside its declared split {split}: {relative}"
                )
            path = _safe_dataset_path(root, relative, path_key)
            if not path.is_file():
                raise ValueError(f"manifest file is missing: {relative}")
            manifest_paths.add(relative)
            if verify_file_hashes:
                actual_sha = sha256_file(path)
                _assert_equal(actual_sha, str(row[sha_key]), f"{relative} SHA-256")
                hashes_verified += 1
                if path_key == "image_path":
                    if actual_sha in image_hashes:
                        raise ValueError(f"duplicate actual image SHA-256: {actual_sha}")
                    image_hashes.add(actual_sha)

        image_path = Path(str(row["image_path"]))
        label_path = Path(str(row["label_path"]))
        if image_path.stem != label_path.stem:
            raise ValueError(
                f"manifest image/label stems differ: {image_path.name} / {label_path.name}"
            )
        actual_label_class = label_class_by_path[label_path.as_posix()]
        expected_label_class = class_id if kind == "positive" else None
        _assert_equal(
            actual_label_class,
            expected_label_class,
            f"manifest kind/direction versus label class for {label_path.as_posix()}",
        )

    expected_total = int(expected_counts["total"]["images"])
    _assert_equal(rows, expected_total, "manifest rows")
    _assert_equal(manifest_paths, actual_paths, "manifest versus actual file path set")
    for split in ("train", "val"):
        _assert_equal(
            manifest_counts[(split, "positive")],
            int(expected_counts[split]["positive"]),
            f"manifest {split} positives",
        )
        _assert_equal(
            manifest_counts[(split, "negative")],
            int(expected_counts[split]["negative"]),
            f"manifest {split} negatives",
        )
        _assert_equal(
            {key: manifest_class_counts[(split, key)] for key in expected_classes},
            expected_class_by_split[split],
            f"manifest {split} class counts",
        )
        _assert_equal(
            {
                "positive": len(event_ids[(split, "positive")]),
                "negative": len(event_ids[(split, "negative")]),
            },
            expected_events[split],
            f"manifest {split} independent events",
        )

    if event_ids[("train", "positive")] & event_ids[("val", "positive")]:
        raise ValueError("positive event id crosses train/val")
    if event_ids[("train", "negative")] & event_ids[("val", "negative")]:
        raise ValueError("negative event id crosses train/val")
    if verify_file_hashes:
        _assert_equal(len(image_hashes), expected_total, "unique actual image hashes")

    return {
        "schema_version": 1,
        "passed": True,
        "experiment_id": prereg["experiment_id"],
        "dataset": str(root),
        "manifest_sha256": immutable["manifest_sha256"],
        "class_names": expected_classes,
        "source_dimensions": list(expected_dimensions),
        "file_counts": file_counts,
        "class_instances_by_split": {
            split: dict(label_class_by_split[split]) for split in ("train", "val")
        },
        "events_by_split": {
            split: {
                "positive": len(event_ids[(split, "positive")]),
                "negative": len(event_ids[(split, "negative")]),
            }
            for split in ("train", "val")
        },
        "positive_labels": positive_total,
        "negative_empty_labels": negative_total,
        "manifest_rows": rows,
        "unique_sample_ids": len(sample_ids),
        "file_hash_verification_enabled": verify_file_hashes,
        "file_hashes_verified": hashes_verified,
        "unique_actual_image_hashes": len(image_hashes) if verify_file_hashes else None,
        "holdout_rows_read": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-file-hashes", action="store_true")
    args = parser.parse_args()

    receipt = verify_dataset(
        args.dataset,
        args.prereg,
        verify_file_hashes=args.verify_file_hashes,
    )
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        "PREFLIGHT_OK|"
        f"{receipt['manifest_sha256']}|{receipt['manifest_rows']}|"
        f"{receipt['positive_labels']}|{receipt['negative_empty_labels']}|"
        f"{','.join(f'{key}:{value}' for key, value in receipt['class_names'].items())}"
    )


if __name__ == "__main__":
    main()
