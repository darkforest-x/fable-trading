#!/usr/bin/env python3
"""Build dense_owner_v15_tipval: reuse v14 pad200 TRAIN, tip-align VAL only.

Single-variable experiment (Owner-approved 2026-07-22):
  - train: identical to dense_owner_v14_pad200 (path reference / softlink)
  - val: same crop-after-box + left-pad200 MAD-on protocol as v14 train
  - holdout: skip any pad200 val sample with end_time >= 2026-05-04
  - does NOT rebuild train positives; does NOT touch LIVE / promote / thresholds

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_v15_tipval_dataset.py
  PYTHONPATH=. .venv/bin/python scripts/build_v15_tipval_dataset.py --resume
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_crop_pad200_dataset import (  # noqa: E402
    Pad200Skip,
    process_pad200,
    read_boxes,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
V14 = PROJECT / "datasets" / "dense_owner_v14_pad200"
V11 = PROJECT / "datasets" / "dense_owner_v11"
OUT = PROJECT / "datasets" / "dense_owner_v15_tipval"


def _parse_end_time(raw: str) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(raw)
    except Exception:  # noqa: BLE001
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t


def _link_or_copy(src: Path, dst: Path) -> str:
    """Prefer symlink (Mac); fall back to copy if links fail."""
    if dst.exists() or dst.is_symlink():
        return "exists"
    try:
        os.symlink(src.resolve(), dst)
        return "symlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--v14", type=Path, default=V14)
    ap.add_argument("--src-val", type=Path, default=V11, help="orig val images/labels (v11)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument(
        "--max-new",
        type=int,
        default=0,
        help="exit after N new pad200 attempts (ok+skip) so watchdog can "
        "respawn a fresh process (jetsam-safe). 0=run to completion.",
    )
    ap.add_argument(
        "--link-train",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="symlink train images/labels from v14 (default ON)",
    )
    args = ap.parse_args()

    if not (args.v14 / "data.yaml").exists():
        print(f"missing v14 dataset: {args.v14}", file=sys.stderr)
        return 2
    if not (args.src_val / "images" / "val").exists():
        print(f"missing src val: {args.src_val}/images/val", file=sys.stderr)
        return 2

    dst = args.out
    if dst.exists() and not args.resume:
        print(f"refusing to clobber existing out: {dst} (pass --resume)", file=sys.stderr)
        return 2
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)

    # --- train: point at v14 (symlink files, or yaml relative path) ---
    n_train_link = 0
    if args.link_train:
        for split_kind in ("images", "labels"):
            src_dir = args.v14 / split_kind / "train"
            dst_dir = dst / split_kind / "train"
            for p in sorted(src_dir.iterdir()):
                if p.name.startswith("."):
                    continue
                mode = _link_or_copy(p, dst_dir / p.name)
                if mode != "exists":
                    n_train_link += 1

    # Prefer relative train path so Windows can reuse already-synced v14 train
    # without re-shipping ~1.6G. Local Mac still works via same relative layout.
    data_yaml = (
        f"path: {dst.resolve()}\n"
        f"train: ../dense_owner_v14_pad200/images/train\n"
        f"val: images/val\n"
        f"names:\n  0: dense_cluster\n"
    )
    (dst / "data.yaml").write_text(data_yaml)

    skip_log = dst / "tipval_skip.log"
    already_ok = {
        p.name.replace("_pad200.png", "")
        for p in (dst / "images" / "val").glob("*_pad200.png")
    }
    already_skip: set[str] = set()
    if skip_log.exists() and args.resume:
        for line in skip_log.read_text(encoding="utf-8").splitlines():
            stem0 = line.split("\t", 1)[0].strip()
            if stem0:
                already_skip.add(stem0)

    n_ok = len(already_ok)
    n_skip = len(already_skip)
    n_bg = 0
    n_holdout = 0
    skip_fh = skip_log.open("a" if args.resume else "w", encoding="utf-8")

    print(
        f"v15 tipval start resume={args.resume} max_new={args.max_new} "
        f"already_ok={n_ok} already_skip={n_skip} train_linked={n_train_link}",
        flush=True,
    )

    src_img_dir = args.src_val / "images" / "val"
    src_lbl_dir = args.src_val / "labels" / "val"
    n_new = 0
    batch_hit = False
    for img in sorted(src_img_dir.glob("*.png")):
        stem = img.stem
        lbl = src_lbl_dir / f"{stem}.txt"
        boxes = read_boxes(lbl) if lbl.exists() else []
        if not boxes:
            # empty / missing label: copy as-is (same empty_bg_policy as v14 train)
            dest = dst / "images" / "val" / img.name
            if not dest.exists():
                shutil.copy2(img, dest)
            if lbl.exists():
                dl = dst / "labels" / "val" / lbl.name
                if not dl.exists():
                    shutil.copy2(lbl, dl)
            n_bg += 1
            continue
        if stem in already_ok or stem in already_skip:
            continue
        out_img = dst / "images" / "val" / f"{stem}_pad200.png"
        out_lbl = dst / "labels" / "val" / f"{stem}_pad200.txt"
        try:
            res = process_pad200(
                stem,
                lbl,
                out_img,
                out_lbl,
                draw_preview=False,
                orig_img_path=img,
            )
        except Pad200Skip as skip:
            n_skip += 1
            n_new += 1
            skip_fh.write(f"{stem}\t{skip.reason}\t{skip.detail}\n")
            skip_fh.flush()
            print(skip.detail, flush=True)
            gc.collect()
            if args.max_new and n_new >= args.max_new:
                batch_hit = True
                break
            continue
        except Exception as exc:  # noqa: BLE001
            n_skip += 1
            n_new += 1
            skip_fh.write(f"{stem}\texc\t{exc}\n")
            skip_fh.flush()
            print(f"SKIP exc {stem}: {exc}", flush=True)
            gc.collect()
            if args.max_new and n_new >= args.max_new:
                batch_hit = True
                break
            continue
        if res is None:
            n_skip += 1
            n_new += 1
            skip_fh.write(f"{stem}\tother\tprocess_returned_none\n")
            skip_fh.flush()
            print(f"SKIP {stem} (other)", flush=True)
            gc.collect()
            if args.max_new and n_new >= args.max_new:
                batch_hit = True
                break
            continue
        end_t = _parse_end_time(res.end_time)
        if end_t is not None and end_t >= HOLDOUT_START:
            # Remove leaked files; do not keep holdout in tip-val.
            out_img.unlink(missing_ok=True)
            out_lbl.unlink(missing_ok=True)
            n_holdout += 1
            n_skip += 1
            n_new += 1
            skip_fh.write(
                f"{stem}\tholdout\tend_time={res.end_time} >= {HOLDOUT_START.date()}\n"
            )
            skip_fh.flush()
            print(f"SKIP holdout {stem}: end={res.end_time}", flush=True)
            gc.collect()
            if args.max_new and n_new >= args.max_new:
                batch_hit = True
                break
            continue
        n_ok += 1
        n_new += 1
        gc.collect()
        if n_ok % 50 == 0:
            print(
                f"  tipval_ok={n_ok} skip={n_skip} holdout={n_holdout} bg={n_bg}",
                flush=True,
            )
        if args.max_new and n_new >= args.max_new:
            batch_hit = True
            break

    skip_fh.close()

    if batch_hit:
        print(
            f"BATCH_EXIT n_new={n_new} ok={n_ok} skip={n_skip} "
            f"(watchdog will resume; no tipval_summary yet)",
            flush=True,
        )
        return 0

    skip_reasons: dict[str, int] = {}
    if skip_log.exists():
        for line in skip_log.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            reason = parts[1].strip() if len(parts) >= 2 else "untagged"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    # Geometry audit on tip-val positives
    rights: list[float] = []
    for lp in (dst / "labels" / "val").glob("*_pad200.txt"):
        for line in lp.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 5:
                xc, bw = float(parts[1]), float(parts[3])
                rights.append(xc + bw / 2)
    rights_sorted = sorted(rights)
    geo = {}
    if rights_sorted:
        n = len(rights_sorted)
        geo = {
            "n_boxes": n,
            "right_p50": rights_sorted[n // 2],
            "right_ge_0.95": sum(1 for r in rights_sorted if r >= 0.95) / n,
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": "v15_tipval_pad200_mad_on",
        "single_variable": "val_earlystop_distribution_only",
        "train_source": str(args.v14.resolve()),
        "train_path_in_yaml": "../dense_owner_v14_pad200/images/train",
        "src_val": str(args.src_val.resolve()),
        "out": str(dst.resolve()),
        "val_pad200": n_ok,
        "val_skip": n_skip,
        "val_skip_reasons": skip_reasons,
        "val_holdout_skipped": n_holdout,
        "val_bg_copied": n_bg,
        "holdout_excluded_from": str(HOLDOUT_START.date()),
        "val_policy": "pad200_mad_on_same_as_v14_train",
        "mad_gate": True,
        "train_files_linked": n_train_link,
        "geometry_val_pad200": geo,
        "gap_vs_tip_smoke": (
            "tip-val = historical dense gold cropped to box-right (no aftermath); "
            "tip-smoke = forward_log coins at *current* tip (may lack dense start). "
            "Same render+full-MA as live, but positive semantics still gold crop-after-box."
        ),
        "note": "Does not promote; does not rebuild v14 train positives.",
    }
    (dst / "tipval_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(
        f"DONE tipval_ok={n_ok} skip={n_skip} holdout={n_holdout} bg={n_bg}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
