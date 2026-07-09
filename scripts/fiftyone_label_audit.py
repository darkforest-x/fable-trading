"""Browse dense_15m_full labels in FiftyOne; optional model preds + mistakenness.

Requires isolated env with fiftyone (default: ../fable-trading-codex/.venv_yolo_tools
or .venv_yolo_tools). Does not install into project .venv.

Usage:
  # GT only (fast)
  /path/to/venv/bin/python scripts/fiftyone_label_audit.py --split val --launch

  # GT + preds + mistakenness (after export_yolo_preds_for_audit.py)
  /path/to/venv/bin/python scripts/fiftyone_label_audit.py --split val \\
      --preds datasets/dense_15m_full/preds_val_conf30 --launch
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import fiftyone as fo
import fiftyone.brain as fob
from fiftyone import ViewField as F


def _load_yolo_split(dataset_dir: Path, split: str, name: str) -> fo.Dataset:
    """Load images + ground-truth boxes (YOLO txt, no conf column)."""
    if name in fo.list_datasets():
        fo.delete_dataset(name)
    # YOLOv5Dataset expects dataset_dir with data.yaml or images/labels layout.
    data_yaml = dataset_dir / "data.yaml"
    kwargs = dict(
        dataset_type=fo.types.YOLOv5Dataset,
        split=split,
        label_field="ground_truth",
        name=name,
    )
    if data_yaml.exists():
        dataset = fo.Dataset.from_dir(dataset_dir=str(dataset_dir), **kwargs)
    else:
        dataset = fo.Dataset.from_dir(
            dataset_dir=str(dataset_dir),
            dataset_type=fo.types.YOLOv5Dataset,
            split=split,
            label_field="ground_truth",
            name=name,
        )
    return dataset


def _parse_yolo_line(line: str, has_conf: bool) -> tuple:
    parts = line.split()
    if has_conf and len(parts) >= 6:
        cls, cx, cy, w, h, conf = parts[:6]
        return int(float(cls)), float(cx), float(cy), float(w), float(h), float(conf)
    cls, cx, cy, w, h = parts[:5]
    return int(float(cls)), float(cx), float(cy), float(w), float(h), None


def _add_predictions(dataset: fo.Dataset, pred_label_dir: Path, class_name: str = "dense_cluster") -> None:
    """Attach detections from YOLO txt (optional trailing conf)."""
    for sample in dataset:
        stem = Path(sample.filepath).stem
        txt = pred_label_dir / f"{stem}.txt"
        dets = []
        if txt.exists() and txt.read_text().strip():
            for line in txt.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                cls, cx, cy, w, h, conf = _parse_yolo_line(line, has_conf=True)
                # FiftyOne bounding_box is [x,y,w,h] top-left relative
                x = cx - w / 2
                y = cy - h / 2
                d = fo.Detection(
                    label=class_name,
                    bounding_box=[x, y, w, h],
                )
                if conf is not None:
                    d.confidence = conf
                dets.append(d)
        sample["predictions"] = fo.Detections(detections=dets)
        sample.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="datasets/dense_15m_full")
    parser.add_argument("--split", default="val", choices=("val", "train"))
    parser.add_argument("--name", default="", help="FiftyOne dataset name")
    parser.add_argument("--preds", default="", help="dir with labels/<split>/*.txt from export script")
    parser.add_argument("--launch", action="store_true", help="open FiftyOne App")
    parser.add_argument("--port", type=int, default=5151)
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--no-mistakenness", action="store_true")
    parser.add_argument("--export-hard", default="", help="optional dir to export hard samples list")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset).resolve()
    name = args.name or f"fable_dense_{args.split}"
    print(f"loading {dataset_dir} split={args.split} as {name} ...")
    dataset = _load_yolo_split(dataset_dir, args.split, name)
    print(f"samples={len(dataset)}")

    if args.preds:
        pred_root = Path(args.preds)
        candidates = [
            pred_root / "labels" / args.split,
            pred_root / args.split,
            pred_root,
        ]
        pred_labels = next((p for p in candidates if p.is_dir() and list(p.glob("*.txt"))), None)
        if pred_labels is None:
            raise SystemExit(f"prediction labels not found under {pred_root}")
        print(f"adding predictions from {pred_labels}")
        _add_predictions(dataset, pred_labels)

        if not args.no_mistakenness:
            print("computing mistakenness (needs predictions) ...")
            try:
                fob.compute_mistakenness(dataset, "predictions", label_field="ground_truth")
                print("mistakenness field added — sort by mistakenness in the App")
            except Exception as exc:  # noqa: BLE001 — report FO/brain version issues
                print(f"mistakenness failed (browse still works): {exc}")

    # useful views as saved views
    try:
        dataset.save_view("has_gt", dataset.match(F("ground_truth.detections").length() > 0))
        dataset.save_view("background", dataset.match(F("ground_truth.detections").length() == 0))
    except Exception:
        pass

    summary = {
        "name": name,
        "samples": len(dataset),
        "split": args.split,
        "preds": args.preds or None,
        "app": f"http://{args.address}:{args.port}" if args.launch else None,
    }
    out_json = Path("output/offline_tasks/fiftyone_session_summary.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

    if args.export_hard and "mistakenness" in dataset.get_field_schema():
        hard = dataset.sort_by("mistakenness", reverse=True).limit(50)
        export_dir = Path(args.export_hard)
        export_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for s in hard:
            rows.append(
                f"{Path(s.filepath).name}\tmistakenness={getattr(s, 'mistakenness', None)}"
            )
        (export_dir / "top50_mistakenness.tsv").write_text("\n".join(rows) + "\n")
        print(f"wrote {export_dir / 'top50_mistakenness.tsv'}")

    if args.launch:
        print(f"Launching FiftyOne App on http://{args.address}:{args.port}")
        print("In App: filter has_gt / sort mistakenness / toggle ground_truth vs predictions")
        session = fo.launch_app(dataset, address=args.address, port=args.port)
        session.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
