#!/usr/bin/env python3
"""Live tip-window Label Studio pack (Owner 2026-07-21).

Simulates live tip geometry for human labeling:
  - fixed 200-bar window, right edge = "current" bar, no future bars
  - clean render (no GT / no remapped mid-window boxes drawn on PNG)
  - empty YOLO labels; LS tasks ship with empty predictions
  - prefer windows where MA bundle is tight near the tip (scout heuristic)

Does NOT remap historical mid-window gold boxes (pad200 path Owner rejected
as unlabelable). Does not touch dense_owner_v11 / v12 / promote / VPS.

Usage:
  # 2 smoke samples first
  OMP_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python scripts/make_live_tip_label_pack.py \\
      --count 2 --out datasets/label_live_tip_1000 --preview-only

  # full ~1000 pack
  OMP_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python scripts/make_live_tip_label_pack.py \\
      --count 1000 --out datasets/label_live_tip_1000 --chunks 4
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

# Keep BLAS/OpenMP quiet before numpy/cv2 heavy paths (caller should also export).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import BLOCKED_BASES  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.auto_label import find_dense_segments  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import MIN_REL_SPAN, render_chart  # noqa: E402

WINDOW = 200
TIP_LOOKBACK = 12  # bars at right edge used for density score
TIP_HIT_BARS = 16  # dense segment must end within this many bars of tip
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
# Prefer recent pre-holdout history (still usable for later train without holdout burn).
SCORE_START = pd.Timestamp("2025-06-01", tz="UTC")
DS_NAME = "label_live_tip_1000"
STEM_RE = re.compile(r"^(?P<sym>.+)_(?P<idx>\d{4,})$")
# Flat/stable windows look like a ribbon after MIN_REL_SPAN floor — useless to label.
MIN_NATIVE_REL_SPAN = MIN_REL_SPAN  # require real price range >= render floor


def labelled_index(pool: dict) -> dict[str, list[int]]:
    by: dict[str, list[int]] = defaultdict(list)
    for stem in pool:
        m = STEM_RE.match(stem)
        if m:
            by[m.group("sym")].append(int(m.group("idx")))
    for sym in by:
        by[sym].sort()
    return by


def prior_task_stems() -> set[str]:
    out: set[str] = set()
    root = PROJECT / "output/label_studio"
    if not root.exists():
        return out
    for path in root.glob("tasks_*.json"):
        try:
            tasks = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(tasks, list):
            continue
        for t in tasks:
            stem = (t.get("data") or {}).get("stem")
            if stem:
                out.add(str(stem))
    return out


def tip_score(sub: pd.DataFrame) -> tuple[float, bool]:
    """Lower score = denser tip. Flag if a dense segment ends near tip."""
    tip = sub.iloc[-TIP_LOOKBACK:]
    spread = pd.to_numeric(tip["full_spread"], errors="coerce")
    score = float(spread.mean()) if spread.notna().any() else 9.0
    segs = find_dense_segments(sub)
    tip_dense = any(seg.end >= WINDOW - TIP_HIT_BARS for seg in segs)
    # Prefer tip-dense; among them, tighter spread ranks first.
    rank = score - (0.05 if tip_dense else 0.0)
    return rank, tip_dense


def collect_candidates(
    csv_paths: list[Path],
    labelled: dict[str, list[int]],
    prior: set[str],
    *,
    stride: int,
    per_symbol: int,
    rng: random.Random,
) -> list[tuple[float, str, Path, int, bool]]:
    """Return (rank, stem, csv_path, tip_idx, tip_dense) sorted best-first."""
    cands: list[tuple[float, str, Path, int, bool]] = []
    for k, csv_path in enumerate(csv_paths, 1):
        m = re.match(r"okx_(.+)_15m_\d+\.csv$", csv_path.name)
        if not m:
            continue
        sym = m.group(1)
        base = sym.split("_", 1)[0]
        if is_eval_symbol(sym) or is_stockish(sym) or base in BLOCKED_BASES:
            continue
        df = pd.read_csv(csv_path, usecols=["ts", "open", "high", "low", "close", "volume"])
        if len(df) < WINDOW + 150:
            continue
        df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        # Full-series MAs so tip spreads match live (not window-local warmup drift).
        enriched = add_mas(df)
        ts = enriched["open_time"]
        seen = labelled.get(sym, [])
        i0 = max(WINDOW - 1, int(ts.searchsorted(SCORE_START)))
        # tip index = last bar of window; skip holdout-era tips
        tips: list[int] = []
        i = i0
        while i < len(enriched):
            if ts.iloc[i] >= HOLDOUT_START:
                break
            tips.append(i)
            i += stride
        if not tips:
            continue
        # Score a random subsample per symbol to keep memory/CPU bounded.
        if len(tips) > per_symbol * 4:
            tips = rng.sample(tips, per_symbol * 4)
        scored_sym: list[tuple[float, str, Path, int, bool]] = []
        for tip_i in tips:
            if any(abs(tip_i - j) < WINDOW for j in seen):
                continue
            stem = f"{sym}_{tip_i:06d}"
            if stem in prior:
                continue
            sub = enriched.iloc[tip_i - WINDOW + 1 : tip_i + 1].reset_index(drop=True)
            if len(sub) != WINDOW:
                continue
            if sub["full_spread"].isna().all():
                continue
            # Drop flat windows (stables / dead ranges) that collapse under y-floor.
            hi = float(sub["high"].max())
            lo = float(sub["low"].min())
            mid = (hi + lo) / 2.0
            if mid <= 0 or (hi - lo) / mid < MIN_NATIVE_REL_SPAN:
                continue
            rank, tip_dense = tip_score(sub)
            scored_sym.append((rank, stem, csv_path, tip_i, tip_dense))
        scored_sym.sort(key=lambda x: x[0])
        cands.extend(scored_sym[:per_symbol])
        if k % 50 == 0:
            print(f"  scout {k}/{len(csv_paths)} symbols, pool={len(cands)}", flush=True)
        # Drop per-symbol frame promptly.
        del df, enriched
    cands.sort(key=lambda x: x[0])
    return cands


def diversify(
    cands: list[tuple[float, str, Path, int, bool]],
    count: int,
    rng: random.Random,
) -> list[tuple[float, str, Path, int, bool]]:
    """Take best tip-dense first, then fill; cap per-symbol for variety."""
    tip_dense = [c for c in cands if c[4]]
    rest = [c for c in cands if not c[4]]
    # Keep some exploration mass so Owner also sees near-miss tip charts.
    n_dense = min(len(tip_dense), int(count * 0.70))
    n_rest = min(len(rest), count - n_dense)
    # If tip_dense shortfall, backfill from rest.
    if n_dense + n_rest < count:
        n_rest = min(len(rest), count - n_dense)

    def take_capped(src: list, n: int, cap: int) -> list:
        by_sym: dict[str, int] = defaultdict(int)
        out = []
        for row in src:
            sym = row[1].rsplit("_", 1)[0]
            if by_sym[sym] >= cap:
                continue
            out.append(row)
            by_sym[sym] += 1
            if len(out) >= n:
                break
        return out

    cap = max(3, count // 80)  # ~12–13 max per symbol at 1000
    picked = take_capped(tip_dense, n_dense, cap) + take_capped(rest, n_rest, cap)
    # Top up if caps left holes.
    if len(picked) < count:
        have = {r[1] for r in picked}
        for row in tip_dense + rest:
            if row[1] in have:
                continue
            picked.append(row)
            have.add(row[1])
            if len(picked) >= count:
                break
    rng.shuffle(picked)
    return picked[:count]


def render_one(csv_path: Path, tip_i: int, out_png: Path) -> None:
    df = pd.read_csv(csv_path, usecols=["ts", "open", "high", "low", "close", "volume"])
    df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    enriched = add_mas(df)
    sub = enriched.iloc[tip_i - WINDOW + 1 : tip_i + 1].reset_index(drop=True)
    assert len(sub) == WINDOW
    out_png.parent.mkdir(parents=True, exist_ok=True)
    render_chart(sub, out_path=out_png)


def write_ls_chunks(
    stems: list[str],
    *,
    ds_name: str,
    chunks: int,
    out_prefix: str,
) -> list[Path]:
    ls_dir = PROJECT / "output/label_studio"
    ls_dir.mkdir(parents=True, exist_ok=True)
    per = -(-len(stems) // chunks)
    paths: list[Path] = []
    for i in range(chunks):
        chunk = stems[i * per : (i + 1) * per]
        if not chunk:
            continue
        tasks = [
            {
                "data": {
                    "image": f"/data/local-files/?d={ds_name}/images/train/{stem}.png",
                    "stem": stem,
                    "split": "train",
                    "pack": "live_tip",
                    "note": "empty_label_tip_geometry",
                },
                # Empty predictions: Owner draws boxes from scratch (no remap GT).
                "predictions": [],
            }
            for stem in chunk
        ]
        path = ls_dir / f"{out_prefix}_chunk{i + 1}.json"
        path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
        paths.append(path)
        print(f"  LS {path.name}: {len(tasks)} tasks (empty predictions)", flush=True)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--chunks", type=int, default=4)
    ap.add_argument("--seed", type=int, default=20260721)
    ap.add_argument("--stride", type=int, default=40, help="tip index stride while scouting")
    ap.add_argument("--per-symbol", type=int, default=8, help="max scored tips kept per symbol")
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "datasets" / DS_NAME,
    )
    ap.add_argument(
        "--preview-only",
        action="store_true",
        help="only render --count samples (for smoke), still writes LS JSON",
    )
    ap.add_argument("--ls-prefix", default="tasks_live_tip_1000")
    args = ap.parse_args()

    out: Path = args.out if args.out.is_absolute() else PROJECT / args.out
    # Hard guard: never write into owner v11/v12 trees.
    forbidden = ("dense_owner_v11", "dense_owner_v12", "dense_owner_v12_htip")
    if any(part in forbidden for part in out.parts):
        raise SystemExit(f"refusing to write under forbidden path: {out}")

    img_dir = out / "images" / "train"
    lbl_dir = out / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    pool = json.loads((PROJECT / "data/golden_pool.json").read_text(encoding="utf-8"))
    labelled = labelled_index(pool)
    prior = prior_task_stems()
    print(
        f"golden_pool={len(pool)} prior_task_stems={len(prior)} "
        f"out={out.relative_to(PROJECT)}",
        flush=True,
    )

    csvs = sorted(
        Path(p)
        for p in glob.glob(str(PROJECT / "data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv"))
    )
    if not csvs:
        raise SystemExit("no SWAP 15m csv under data/kline_fetched/")

    t0 = time.time()
    print(f"scouting tip candidates across {len(csvs)} SWAP series…", flush=True)
    cands = collect_candidates(
        csvs,
        labelled,
        prior,
        stride=args.stride,
        per_symbol=args.per_symbol,
        rng=rng,
    )
    print(f"scored pool={len(cands)} tip_dense={sum(1 for c in cands if c[4])}", flush=True)
    if len(cands) < args.count:
        raise SystemExit(f"only {len(cands)} candidates < requested {args.count}")

    picked = diversify(cands, args.count, rng)
    n_dense = sum(1 for c in picked if c[4])
    print(
        f"picked={len(picked)} tip_dense={n_dense} "
        f"({100 * n_dense / len(picked):.0f}%)",
        flush=True,
    )

    stems: list[str] = []
    meta_rows = []
    for k, (rank, stem, csv_path, tip_i, tip_dense) in enumerate(picked, 1):
        png = img_dir / f"{stem}.png"
        txt = lbl_dir / f"{stem}.txt"
        if not png.exists():
            render_one(csv_path, tip_i, png)
        # Empty label file = awaiting Owner boxes.
        if not txt.exists():
            txt.write_text("", encoding="utf-8")
        stems.append(stem)
        meta_rows.append(
            {
                "stem": stem,
                "symbol": stem.rsplit("_", 1)[0],
                "tip_idx": tip_i,
                "rank": rank,
                "tip_dense_rule": tip_dense,
                "csv": csv_path.name,
            }
        )
        if k % 50 == 0 or k == len(picked):
            print(
                f"  render {k}/{len(picked)}  {(time.time() - t0) / 60:.1f} min",
                flush=True,
            )

    ds_name = out.name
    ls_paths = write_ls_chunks(
        stems, ds_name=ds_name, chunks=args.chunks, out_prefix=args.ls_prefix
    )

    summary = {
        "protocol": "live_tip_empty_label",
        "window": WINDOW,
        "count": len(stems),
        "tip_dense_rule_hits": n_dense,
        "holdout_excluded_from": str(HOLDOUT_START.date()),
        "score_start": str(SCORE_START.date()),
        "out": str(out.relative_to(PROJECT)),
        "images": str((img_dir).relative_to(PROJECT)),
        "labels": str((lbl_dir).relative_to(PROJECT)),
        "ls_tasks": [str(p.relative_to(PROJECT)) for p in ls_paths],
        "note": (
            "PNGs have no boxes; labels empty; LS predictions empty. "
            "Import tasks_*.json into Label Studio (label_config_v2.xml)."
        ),
        "stems_sample": stems[:5],
        "meta": meta_rows,
    }
    (out / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "README_IMPORT.txt").write_text(
        "\n".join(
            [
                "Live tip labeling pack (empty labels)",
                "====================================",
                "1. docker compose -f scripts/label_studio_compose.yml up -d",
                "2. Open http://127.0.0.1:8081",
                "3. New project → Labeling Interface → paste output/label_studio/label_config_v2.xml",
                "4. Import → output/label_studio/tasks_live_tip_1000_chunk1.json (…chunk2…)",
                "   (datasets/ is already mounted; image URLs use "
                f"d={ds_name}/images/train/…)",
                "5. Or: PYTHONPATH=. python3 scripts/ls_auto_import.py "
                f"live_tip_1000_c1 output/label_studio/{args.ls_prefix}_chunk1.json",
                "6. Draw dense_cluster on the RIGHT tip when present; submit.",
                "",
                f"Images: {img_dir.relative_to(PROJECT)}",
                f"Labels: {lbl_dir.relative_to(PROJECT)} (empty .txt)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(
        f"DONE count={len(stems)} tip_dense={n_dense} "
        f"in {(time.time() - t0) / 60:.1f} min → {out}",
        flush=True,
    )
    if stems:
        print(f"SAMPLE1={img_dir / stems[0]}.png")
        if len(stems) > 1:
            print(f"SAMPLE2={img_dir / stems[1]}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
