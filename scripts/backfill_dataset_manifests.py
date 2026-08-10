#!/usr/bin/env python3
"""Backfill traceability fields onto YOLO datasets and emit a spec-§12 manifest.

Why this exists (2026-08-10)
----------------------------
Commit 4b5f48b landed ``datasets/*/*.json`` manifests but left ~800MB of
``datasets/*/images`` out of git, on the stated grounds that the images are
"regenerable from manifests + builders". That claim was never checkable:

* ``dense_owner_w20_midbox`` positives/negatives carry no ``image_sha256``,
  no ``event_id`` and no ``config_hash`` — so a rebuild could not be compared
  against what actually trained.
* Its 2300 hard negatives (1500 ``_hardneg_dense`` + 800 ``_hardneg_weak``)
  have **no manifest at all**; ``add_w20_hardneg_pack.py`` only wrote the
  counts in ``w20_hardneg_summary.json``.

``local_signal_v2_stageb`` was already built correctly and carries the full
field set; this script only adds ``label_sha256`` there and re-emits it in the
common format so both datasets audit the same way.

What it does
------------
1. Reconstructs the missing hard-negative manifest from the on-disk stems
   (``{sym}_{win_start:06d}_w{win_len}_hardneg_dense`` /
   ``..._hardneg_weak_c{conf:.3f}`` — the exact names
   ``add_w20_hardneg_pack.py`` writes at lines 141 and 225).
2. Writes ``manifest.jsonl`` (one row per sample, the field set of spec §12).
3. Writes ``manifest_audit.json``: conservation, coverage, duplicate and
   event-crosses-split checks — the machine-auditable invariants of §12.1.

What it deliberately does NOT do
--------------------------------
* It does not modify the builders' own ``*_manifest.json`` outputs
  (spec §18.1: never overwrite an existing dataset).
* It does not invent fields the builder never recorded. ``dense_owner_w20_midbox``
  is a Stage-A midbox dataset with no causal semantics, so ``decision_bar_index``,
  ``confirm_delay`` and ``visible_end_timestamp`` are emitted as ``null`` and
  listed in each row's ``missing_fields``. Writing a guessed decision bar there
  would manufacture exactly the false causality that
  ``docs/learnings/window-length-does-not-control-future-visibility.md`` warns about.

Usage
-----
  .venv/bin/python scripts/backfill_dataset_manifests.py
  .venv/bin/python scripts/backfill_dataset_manifests.py \
      --dataset datasets/dense_owner_w20_midbox
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PROJECT = Path(__file__).resolve().parents[1]

DEFAULT_DATASETS = (
    PROJECT / "datasets" / "dense_owner_w20_midbox",
    PROJECT / "datasets" / "local_signal_v2_stageb",
)

# 15m is the only bar the w20/lsv2 builders load (build_w20_midbox_dataset.py
# series_groups(): list_series(..., bar="15m")).
TIMEFRAME = "15m"

# Keep byte-identical to build_local_signal_v2_stageb.py:110-125 so ids computed
# here join against the Stage-B dataset. tests/test_manifest_backfill.py asserts
# the two implementations agree; if that test fails, they have drifted.
RENDERER_VERSION = "yoyo.l1_detection.render.render_chart"

HARDNEG_DENSE_RE = re.compile(r"^(?P<sym>.+)_(?P<w0>\d{6})_w(?P<win>\d+)_hardneg_dense$")
HARDNEG_WEAK_RE = re.compile(
    r"^(?P<sym>.+)_(?P<w0>\d{6})_w(?P<win>\d+)_hardneg_weak_c(?P<conf>[\d.]+)$"
)


def event_id_of(symbol: str, anchor_bar: int, source_stem: str) -> str:
    raw = f"{symbol}|{anchor_bar}|{source_stem}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def config_hash_of(**kwargs: object) -> str:
    blob = json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from the IHDR header, without pulling in cv2/PIL."""
    with path.open("rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return int(w), int(h)


def read_yolo_label(path: Path) -> list[tuple[float, float, float, float]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) >= 5:
            out.append(tuple(map(float, parts[1:5])))
    return out


def box_xyxy_px(
    label_path: Path, size: tuple[int, int] | None
) -> list[list[int]] | None:
    boxes = read_yolo_label(label_path)
    if not boxes or size is None:
        return None
    w, h = size
    out = []
    for xc, yc, bw, bh in boxes:
        out.append(
            [
                int(round((xc - bw / 2) * w)),
                int(round((yc - bh / 2) * h)),
                int(round((xc + bw / 2) * w)),
                int(round((yc + bh / 2) * h)),
            ]
        )
    return out


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _resolve_pair(ds: Path, split: str, stem: str) -> tuple[Path, Path]:
    return ds / "images" / split / f"{stem}.png", ds / "labels" / split / f"{stem}.txt"


def _split_of_stem(ds: Path, stem: str) -> str | None:
    for split in ("train", "val"):
        if (ds / "images" / split / f"{stem}.png").exists():
            return split
    return None


def reconstruct_hardneg_rows(ds: Path) -> list[dict]:
    """Rebuild the manifest add_w20_hardneg_pack.py never wrote, from disk."""
    rows: list[dict] = []
    for split in ("train", "val"):
        d = ds / "images" / split
        if not d.is_dir():
            continue
        for img in sorted(d.glob("*_hardneg_*.png")):
            stem = img.stem
            m = HARDNEG_DENSE_RE.match(stem)
            kind, conf = "hardneg_dense", None
            if not m:
                m = HARDNEG_WEAK_RE.match(stem)
                kind = "hardneg_weak"
                if m:
                    conf = float(m.group("conf"))
            if not m:
                rows.append({"stem": stem, "split": split, "kind": "hardneg_unparsed"})
                continue
            rows.append(
                {
                    "stem": stem,
                    "symbol": m.group("sym"),
                    "split": split,
                    "win_start": int(m.group("w0")),
                    "win_len": int(m.group("win")),
                    "kind": kind,
                    "weak_conf": conf,
                }
            )
    return rows


def emit_rows(ds: Path, created_at: str) -> Iterator[dict]:
    """One normalized row per sample, whatever the source manifest looked like."""
    name = ds.name
    pos_manifest = _load_json(ds / "w20_manifest.json") or []
    neg_manifest = _load_json(ds / "w20_neg_manifest.json") or []
    summary = _load_json(ds / "w20_summary.json") or _load_json(ds / "stageb_summary.json") or {}
    hn_summary = _load_json(ds / "w20_hardneg_summary.json") or {}

    pos_cfg = summary.get("config_hash") or config_hash_of(
        protocol=summary.get("protocol"),
        seed=summary.get("seed"),
        src=summary.get("src") or summary.get("src_manifest"),
        win_min=summary.get("win_min"),
        win_max=summary.get("win_max"),
        half_choices=summary.get("half_choices"),
        augs=summary.get("augs"),
        renderer=RENDERER_VERSION,
    )
    neg_cfg = config_hash_of(protocol=summary.get("protocol"), kind="empty_bg")
    hn_cfg = config_hash_of(
        protocol=hn_summary.get("protocol"),
        dense_spread_max=hn_summary.get("dense_spread_max"),
        weak_conf=hn_summary.get("weak_conf"),
        window=hn_summary.get("window"),
    )

    def base(stem: str, split: str, sample_type: str) -> dict:
        img, lbl = _resolve_pair(ds, split, stem)
        size = png_size(img) if img.exists() else None
        return {
            "sample_id": stem,
            "sample_type": sample_type,
            "timeframe": TIMEFRAME,
            "split": split,
            "source_dataset_version": name,
            "renderer_version": RENDERER_VERSION,
            "image_path": str(img.relative_to(PROJECT)) if img.exists() else None,
            "label_path": str(lbl.relative_to(PROJECT)) if lbl.exists() else None,
            "image_exists": img.exists(),
            "label_exists": lbl.exists(),
            "image_sha256": sha256_file(img) if img.exists() else None,
            "label_sha256": sha256_file(lbl) if lbl.exists() else None,
            "image_wh": list(size) if size else None,
            "box_xyxy_px": box_xyxy_px(lbl, size),
            "created_at": created_at,
        }

    # ---- positives -------------------------------------------------------
    for r in pos_manifest:
        stem = r.get("out_stem") or r.get("stem")
        split = r.get("split") or _split_of_stem(ds, stem) or "train"
        row = base(stem, split, "positive")
        src_stem = r.get("source_stem") or r.get("stem")
        anchor = r.get("mid_global")
        row.update(
            {
                "event_id": r.get("event_id")
                or (event_id_of(r["symbol"], anchor, src_stem) if anchor is not None else None),
                "symbol": r.get("symbol"),
                "source_label_id": src_stem,
                "anchor_bar_index": anchor,
                "window_start_bar": r.get("win_start"),
                "window_len": r.get("win_len"),
                "anchor_x_ratio": r.get("box_pos_frac"),
                "box_start_bar": (r.get("small_bars") or [None, None])[0],
                "box_end_bar": (r.get("small_bars") or [None, None])[1],
                "window_end_timestamp": r.get("end_time"),
                "confirm_delay": r.get("confirm_delay"),
                "decision_bar_index": r.get("decision_bar"),
                "future_bars": r.get("future_bars"),
                "stage": r.get("stage", "A"),
                "mode": r.get("mode"),
                "stored_mad": r.get("stored_mad"),
                "config_hash": r.get("config_hash") or pos_cfg,
                "hard_negative_type": None,
            }
        )
        yield row

    # ---- easy negatives --------------------------------------------------
    for r in neg_manifest:
        stem = r.get("stem")
        split = r.get("split") or _split_of_stem(ds, stem) or "train"
        row = base(stem, split, "easy_negative")
        row.update(
            {
                "event_id": None,
                "symbol": r.get("symbol"),
                "source_label_id": None,
                "anchor_bar_index": None,
                "window_start_bar": r.get("win_start"),
                "window_len": r.get("win_len"),
                "anchor_x_ratio": None,
                "box_start_bar": None,
                "box_end_bar": None,
                "window_end_timestamp": r.get("end_time"),
                "confirm_delay": None,
                "decision_bar_index": None,
                "future_bars": None,
                "stage": r.get("stage", "A"),
                "mode": r.get("mode"),
                "stored_mad": None,
                "config_hash": r.get("config_hash") or neg_cfg,
                "hard_negative_type": r.get("kind"),
            }
        )
        yield row

    # ---- hard negatives (manifest reconstructed from disk) ---------------
    for r in reconstruct_hardneg_rows(ds):
        stem = r["stem"]
        row = base(stem, r["split"], "hard_negative")
        row.update(
            {
                "event_id": None,
                "symbol": r.get("symbol"),
                "source_label_id": None,
                "anchor_bar_index": None,
                "window_start_bar": r.get("win_start"),
                "window_len": r.get("win_len"),
                "anchor_x_ratio": None,
                "box_start_bar": None,
                "box_end_bar": None,
                "window_end_timestamp": None,
                "confirm_delay": None,
                "decision_bar_index": None,
                "future_bars": None,
                "stage": "A",
                "mode": None,
                "stored_mad": None,
                "config_hash": hn_cfg,
                "hard_negative_type": r.get("kind"),
                "weak_conf": r.get("weak_conf"),
                "manifest_source": "reconstructed_from_disk_20260810",
            }
        )
        yield row


REQUIRED_SPEC12 = (
    "sample_id event_id sample_type hard_negative_type symbol timeframe "
    "anchor_bar_index confirm_delay decision_bar_index window_len "
    "anchor_x_ratio box_start_bar box_end_bar box_xyxy_px split "
    "source_dataset_version source_label_id renderer_version config_hash "
    "image_sha256 label_sha256 created_at"
).split()


def audit(ds: Path, rows: list[dict]) -> dict:
    disk_imgs = {p.stem for p in (ds / "images").rglob("*.png")}
    disk_lbls = {p.stem for p in (ds / "labels").rglob("*.txt")}
    manifest_ids = [r["sample_id"] for r in rows]
    seen: dict[str, int] = {}
    for s in manifest_ids:
        seen[s] = seen.get(s, 0) + 1
    dups = sorted(s for s, n in seen.items() if n > 1)
    ids = set(manifest_ids)

    ev_splits: dict[str, set[str]] = {}
    for r in rows:
        if r.get("event_id"):
            ev_splits.setdefault(r["event_id"], set()).add(r["split"])
    crossing = sorted(e for e, sp in ev_splits.items() if len(sp) > 1)

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["sample_type"]] = by_type.get(r["sample_type"], 0) + 1

    missing_per_field = {
        f: sum(1 for r in rows if r.get(f) is None) for f in REQUIRED_SPEC12
    }

    return {
        "dataset": ds.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "manifest_rows": len(rows),
            "disk_images": len(disk_imgs),
            "disk_labels": len(disk_lbls),
            "by_sample_type": by_type,
        },
        "invariants": {
            "manifest_matches_images": len(ids) == len(disk_imgs) and ids == disk_imgs,
            "images_match_labels": disk_imgs == disk_lbls,
            "no_duplicate_sample_id": not dups,
            "no_event_crosses_split": not crossing,
            "all_rows_have_image": all(r["image_exists"] for r in rows),
            "all_rows_have_label": all(r["label_exists"] for r in rows),
            "all_rows_hashed": all(r["image_sha256"] for r in rows),
        },
        "gaps": {
            "images_without_manifest_row": sorted(disk_imgs - ids)[:20],
            "n_images_without_manifest_row": len(disk_imgs - ids),
            "manifest_rows_without_image": sorted(ids - disk_imgs)[:20],
            "n_manifest_rows_without_image": len(ids - disk_imgs),
            "duplicate_sample_ids": dups[:20],
            "events_crossing_split": crossing[:20],
        },
        "null_counts_by_spec12_field": missing_per_field,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, action="append", default=None)
    ap.add_argument("--out-name", default="manifest.jsonl")
    args = ap.parse_args()
    datasets = args.dataset or list(DEFAULT_DATASETS)

    created_at = datetime.now(timezone.utc).isoformat()
    overall = []
    for ds in datasets:
        ds = ds if ds.is_absolute() else (PROJECT / ds)
        if not ds.is_dir():
            print(f"skip (missing): {ds}")
            continue
        rows = list(emit_rows(ds, created_at))
        for r in rows:
            r["missing_fields"] = [f for f in REQUIRED_SPEC12 if r.get(f) is None]
        out = ds / args.out_name
        with out.open("w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        rep = audit(ds, rows)
        (ds / "manifest_audit.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
        overall.append(rep)
        print(f"\n=== {ds.name} ===")
        print(f"  manifest.jsonl rows = {rep['counts']['manifest_rows']}")
        print(f"  by type             = {rep['counts']['by_sample_type']}")
        print(f"  disk images/labels  = {rep['counts']['disk_images']}/{rep['counts']['disk_labels']}")
        for k, v in rep["invariants"].items():
            print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        if rep["gaps"]["n_images_without_manifest_row"]:
            print(f"  !! {rep['gaps']['n_images_without_manifest_row']} images with no manifest row")

    print("\nwrote manifest.jsonl + manifest_audit.json for", len(overall), "dataset(s)")
    return 0 if all(all(r["invariants"].values()) for r in overall) else 1


if __name__ == "__main__":
    raise SystemExit(main())
