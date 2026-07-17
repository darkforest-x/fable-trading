"""H-TS: build a time-cut owner dataset (window end < 2026-05-04).

Hypothesis (RESEARCH_AGENDA H-TS / HANDOFF): ~2.5% of labelled detector images
fall inside the accept window (≥2026-05-04). The detector has therefore "seen"
shapes that the judgment/backtest accept window also scores — a structural leak
that could inflate PF 7.5.

This script copies dense_owner_v9 → dense_owner_hts, keeping only images whose
**window end bar** is strictly before 2026-05-04 UTC. Frozen-eval symbols stay
excluded (already out of v9). Val/train splits preserved from source.

Stem conventions (must match renderers):
  - dense_2026h1 / round8: stem index = window END bar
  - older dense_* packs:   stem index = window START bar (end = start+WINDOW-1)

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_hts_dataset.py
  PYTHONPATH=. .venv/bin/python scripts/build_hts_dataset.py --src dense_owner_v9 --dst dense_owner_hts
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pandas as pd

from src.data.loader import list_series, load_series

PROJECT = Path(__file__).resolve().parents[1]
CUTOFF = pd.Timestamp("2026-05-04", tz="UTC")
WINDOW = 200


def parse_stem(stem: str) -> tuple[str, int] | None:
    s = re.sub(r"^okx_", "", stem)
    m = re.match(r"^(.+)_(\d{5,6})$", s)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def load_frame_index() -> dict[str, list[Path]]:
    """symbol -> csv paths (okx only)."""
    out: dict[str, list[Path]] = {}
    for (src, sym), paths in list_series(bar="15m").items():
        if src != "okx":
            continue
        out[sym] = paths
        # also index without _SWAP for spot-style stems
        if sym.endswith("_USDT_SWAP"):
            out.setdefault(sym.replace("_SWAP", ""), paths)
        if sym.endswith("_USDT"):
            out.setdefault(sym + "_SWAP", paths)
    return out


def resolve_frame(symbol: str, index: dict[str, list[Path]], cache: dict[str, pd.DataFrame]):
    if symbol in cache:
        return cache[symbol]
    paths = index.get(symbol)
    if paths is None:
        return None
    frame = load_series(paths)
    if frame is None or len(frame) == 0:
        return None
    cache[symbol] = frame
    return frame


def window_end_time(
    stem: str,
    *,
    h1_stems: set[str],
    index: dict[str, list[Path]],
    cache: dict[str, pd.DataFrame],
) -> pd.Timestamp | None:
    parsed = parse_stem(stem)
    if parsed is None:
        return None
    symbol, idx = parsed
    frame = resolve_frame(symbol, index, cache)
    if frame is None:
        return None
    in_h1 = stem in h1_stems or re.sub(r"^okx_", "", stem) in h1_stems
    # primary convention
    end_i = idx if in_h1 else idx + WINDOW - 1
    if not (0 <= end_i < len(frame)):
        alt = idx + WINDOW - 1 if in_h1 else idx
        if 0 <= alt < len(frame):
            end_i = alt
        else:
            return None
    t = pd.Timestamp(frame["open_time"].iloc[end_i])
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="dense_owner_v9")
    ap.add_argument("--dst", default="dense_owner_hts")
    ap.add_argument("--cutoff", default="2026-05-04", help="exclusive UTC date")
    args = ap.parse_args()
    cutoff = pd.Timestamp(args.cutoff, tz="UTC")
    src = PROJECT / "datasets" / args.src
    dst = PROJECT / "datasets" / args.dst
    if not src.exists():
        raise SystemExit(f"missing source dataset: {src}")

    h1_dir = PROJECT / "datasets/dense_2026h1/images/train"
    h1_stems = {p.stem for p in h1_dir.glob("*.png")} if h1_dir.exists() else set()

    print("indexing klines…", flush=True)
    index = load_frame_index()
    cache: dict[str, pd.DataFrame] = {}
    stats: Counter[str] = Counter()
    kept: list[dict] = []
    dropped: list[dict] = []

    if dst.exists():
        shutil.rmtree(dst)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (dst / sub).mkdir(parents=True, exist_ok=True)

    for split in ("train", "val"):
        img_dir = src / "images" / split
        lbl_dir = src / "labels" / split
        for img in sorted(img_dir.glob("*.png")):
            stem = img.stem
            t = window_end_time(stem, h1_stems=h1_stems, index=index, cache=cache)
            if t is None:
                stats[f"{split}_unresolved"] += 1
                dropped.append({"stem": stem, "split": split, "reason": "unresolved"})
                continue
            if t >= cutoff:
                stats[f"{split}_post_cutoff"] += 1
                dropped.append({"stem": stem, "split": split, "reason": "post_cutoff", "end": str(t)})
                continue
            stats[f"{split}_kept"] += 1
            shutil.copy2(img, dst / "images" / split / img.name)
            lbl = lbl_dir / f"{stem}.txt"
            if lbl.exists():
                shutil.copy2(lbl, dst / "labels" / split / lbl.name)
            else:
                (dst / "labels" / split / f"{stem}.txt").write_text("")
            kept.append({"stem": stem, "split": split, "end": str(t)})

    (dst / "data.yaml").write_text(
        f"path: {dst.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_cluster\n",
        encoding="utf-8",
    )
    meta = {
        "hypothesis": "H-TS",
        "src": str(src),
        "dst": str(dst),
        "cutoff_exclusive_utc": str(cutoff),
        "window": WINDOW,
        "stats": dict(stats),
        "n_kept": len(kept),
        "n_dropped": len(dropped),
        "drop_reasons": {
            "post_cutoff": sum(1 for d in dropped if d.get("reason") == "post_cutoff"),
            "unresolved": sum(1 for d in dropped if d.get("reason") == "unresolved"),
        },
    }
    (dst / "hts_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PROJECT / "analysis" / "output" / "hts_dataset_build.json").parent.mkdir(parents=True, exist_ok=True)
    (PROJECT / "analysis" / "output" / "hts_dataset_build.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
