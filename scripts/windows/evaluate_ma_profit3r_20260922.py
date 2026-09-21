"""Read-only CUDA evaluation for one MA-profit YOLO arm on the common pool.

Runs no training and writes a new immutable prediction receipt.  The supplied
model is scored on every shared A image in val/test at confidence .001; fixed
deployment metrics are subsequently calculated at the preregistered .25/.70.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.evaluation.ma_profit_model_metrics import evaluate_events, join_events


CONFIDENCE, NMS_IOU, IMAGE_SIZE = .001, .70, 1280


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _common_rows(dataset: Path, arm: str, split: str) -> list[dict[str, Any]]:
    rows = _jsonl(dataset / "manifest.jsonl")
    selected = [row for row in rows if row.get("split") == split and arm in row.get("arms", []) and row.get("variant") == "A"]
    if not selected or len({str(row.get("event_id")) for row in selected}) != len(selected):
        raise ValueError(f"missing/duplicate common {split} event images")
    for row in selected:
        relative = Path(str(row["image_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("image path escapes dataset")
        path = dataset / relative
        if not path.is_file() or sha256_file(path) != row.get("image_sha256"):
            raise ValueError(f"manifest image SHA drift: {row.get('image_path')}")
    return sorted(selected, key=lambda row: str(row["event_id"]))


def infer(model_path: Path, dataset: Path, arm: str, split: str) -> list[dict[str, Any]]:
    """Return all boxes at low confidence, preserving exact manifest identity."""

    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required; CPU fallback is forbidden")
    rows, model = _common_rows(dataset, arm, split), YOLO(str(model_path))
    if dict(model.names) != {0: "profitable_dense_long", 1: "profitable_dense_short"}:
        raise ValueError("checkpoint class semantics differ from frozen experiment")
    output: list[dict[str, Any]] = []
    for row in rows:
        result = model.predict(str(dataset / row["image_path"]), imgsz=IMAGE_SIZE, conf=CONFIDENCE, iou=NMS_IOU, device=0, augment=False, agnostic_nms=False, max_det=300, verbose=False)[0]
        boxes = []
        if result.boxes is not None:
            for xywh, confidence, class_id in zip(result.boxes.xywhn.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()):
                boxes.append({"class_id": int(class_id), "confidence": float(confidence), "cx_norm": float(xywh[0]), "cy_norm": float(xywh[1]), "w_norm": float(xywh[2]), "h_norm": float(xywh[3])})
        output.append({"event_id": row["event_id"], "split": split, "image_path": row["image_path"], "image_sha256": row["image_sha256"], "boxes": boxes})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--arm", choices=("A", "B"), required=True)
    parser.add_argument("--split", choices=("val", "test", "both"), default="both")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(); dataset, out = args.dataset.resolve(), args.out.resolve()
    if out.exists() or not args.model.is_file():
        raise SystemExit("output exists or model is missing")
    splits = ("val", "test") if args.split == "both" else (args.split,)
    manifest = dataset / "manifest.jsonl"; out.mkdir(parents=True)
    metadata = {"status": "running", "arm": args.arm, "model_path": str(args.model.resolve()), "model_sha256": sha256_file(args.model), "manifest_sha256": sha256_file(manifest), "ledger_sha256": sha256_file(args.ledger), "confidence": CONFIDENCE, "nms_iou": NMS_IOU, "imgsz": IMAGE_SIZE, "device": 0, "augment": False, "splits": list(splits), "read_only_evaluation": True, "artifacts": {}}
    metadata.update({"agnostic_nms": False, "max_det": 300, "runner_sha256": sha256_file(Path(__file__)), "metrics_code_sha256": sha256_file(ROOT / "yoyo/evaluation/ma_profit_model_metrics.py")})
    _write_json(out / "receipt.json", metadata)
    ledger = _jsonl(args.ledger)
    try:
        for split in splits:
            predictions = infer(args.model, dataset, args.arm, split)
            if sha256_file(args.model) != metadata["model_sha256"] or sha256_file(manifest) != metadata["manifest_sha256"]:
                raise ValueError("checkpoint or manifest changed during evaluation")
            _write_jsonl(out / f"predictions_{split}.jsonl", predictions)
            events = join_events(ledger, _jsonl(manifest), predictions, split=split)
            _write_jsonl(out / f"events_{split}.jsonl", events)
            _write_json(out / f"metrics_{split}.json", evaluate_events(events))
            for name in (f"predictions_{split}.jsonl", f"events_{split}.jsonl", f"metrics_{split}.json"):
                metadata["artifacts"][name] = sha256_file(out / name)
        metadata["status"] = "completed"
    except Exception as exc:
        metadata.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        _write_json(out / "receipt.json", metadata)


if __name__ == "__main__":
    main()
