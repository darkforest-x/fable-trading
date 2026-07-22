#!/usr/bin/env python3
"""Import a small hardneg/tip preview batch into FiftyOne (CPU, side venv).

Does NOT run YOLO inference. Does NOT touch training .venv / MPS / holdout.

Default source: hardneg mid-cluster preview PNGs already on disk
(analysis/output/hardneg_mid_cluster/previews). Optional GT boxes from
candidates CSV when image_rel resolves under dense_owner_v11.

Usage (dedicated FiftyOne side venv — NOT training .venv, NOT .venv-tools):
  .venv-fo/bin/python scripts/fiftyone_hardneg_browse.py
  .venv-fo/bin/python scripts/fiftyone_hardneg_browse.py --launch --port 5152

Without --launch: builds/persists the dataset and prints how to reopen.
FiftyOne fights mitmproxy/ydata pins → keep it in `.venv-fo`.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY = PROJECT / "analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_summary.json"
DEFAULT_NAME = "fable_hardneg_discovery"


def _yolo_to_fo_box(cx: float, cy: float, w: float, h: float) -> list[float]:
    return [cx - w / 2, cy - h / 2, w, h]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--port", type=int, default=5152)
    args = ap.parse_args()

    import fiftyone as fo

    summary = json.loads(args.summary.read_text())
    previews = (summary.get("previews") or [])[: args.limit]
    if not previews:
        raise SystemExit("no previews — run build_hardneg_mid_cluster_inventory.py first")

    by_stem: dict[str, dict] = {}
    cand_path = PROJECT / summary["csv"]
    if cand_path.is_file():
        with cand_path.open() as f:
            for row in csv.DictReader(f):
                by_stem.setdefault(row["stem"], row)

    if args.name in fo.list_datasets():
        fo.delete_dataset(args.name)

    samples = []
    for p in previews:
        stem = p["stem"]
        row = {**by_stem.get(stem, {}), **p}
        preview = PROJECT / p["preview"]
        img_rel = row.get("image_rel")
        src = PROJECT / "datasets/dense_owner_v11" / img_rel if img_rel else None
        filepath = src if src and src.is_file() else preview
        if not filepath.is_file():
            print(f"skip missing {filepath}")
            continue
        sample = fo.Sample(filepath=str(filepath))
        sample["stem"] = stem
        sample["right"] = float(row.get("right") or 0)
        sample["bars_after"] = float(row.get("bars_after") or 0)
        sample["symbol_key"] = row.get("symbol_key") or ""
        if all(k in row for k in ("cx", "cy", "w", "h")):
            sample["hardneg_gt"] = fo.Detections(
                detections=[
                    fo.Detection(
                        label="hardneg_mid",
                        bounding_box=_yolo_to_fo_box(
                            float(row["cx"]),
                            float(row["cy"]),
                            float(row["w"]),
                            float(row["h"]),
                        ),
                    )
                ]
            )
        samples.append(sample)

    ds = fo.Dataset(args.name)
    ds.add_samples(samples)
    ds.persistent = True
    print(f"fiftyone dataset={args.name} n={len(ds)} persistent=True")
    print(
        "reopen: .venv-fo/bin/python -c \"import fiftyone as fo; "
        f"ds=fo.load_dataset('{args.name}'); "
        f"fo.launch_app(ds, port={args.port}, address='127.0.0.1')\""
    )

    if args.launch:
        print(f"launching App http://127.0.0.1:{args.port} (Ctrl+C to stop)")
        session = fo.launch_app(ds, port=args.port, address="127.0.0.1")
        session.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
