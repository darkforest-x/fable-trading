#!/usr/bin/env python3
"""Build tip-aligned YOLO dataset for Owner short-side boxes only.

Owner option-1 (2026-07-24): re-crop window so tip = image right edge, rewrite
YOLO boxes in that tip window, time-split train/val. Does NOT overwrite
``datasets/dense_owner_side_short`` (pretip symlink set).

Reuse:
  - tip geometry from ``build_htip_dataset`` / live ``predict_tip_window``
    (window ends at cut_global; full-series add_mas then slice)
  - box y-extent from ``segment_to_bbox`` MA-bundle convention
  - short filter from ``review_sheet.csv`` (owner_side==short)

Train/val: global cut_time vs VAL_CUT=2026-02-01 (IT-16 p3 boundary).
Holdout (>=2026-05-04) dropped. One image per box_id (multi-box pretip
stems often have distinct cut_globals — cannot share one tip window).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip.py
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip.py --limit 40
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip.py --sample-only
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.auto_label import DenseSegment, segment_to_bbox  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from scripts.build_htip_dataset import WINDOW, resolve_series  # noqa: E402

SHEET = PROJECT / "analysis/output/owner_side_review/review_sheet.csv"
OUT = PROJECT / "datasets/dense_owner_side_short_tip"
SAMPLE_OUT = PROJECT / "analysis/output/owner_side_short_tip_sample30"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
# Calendar cut matching IT-16 p3 start; ~80% of short cuts fall before this.
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")
COLOR_BOX = (0, 200, 0)  # BGR green
COLOR_TIP = (0, 0, 220)  # BGR red


@dataclass
class Stats:
    ok: int = 0
    skip_no_series: int = 0
    skip_window: int = 0
    skip_box: int = 0
    skip_error: int = 0
    holdout_dropped: int = 0
    missing_symbols: list[str] = field(default_factory=list)
    box_right_fracs: list[float] = field(default_factory=list)


def _git_desc() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _quantiles(xs: list[float]) -> dict:
    if not xs:
        return {}
    a = np.asarray(xs, dtype=float)
    return {
        "n": int(len(a)),
        "p25": round(float(np.quantile(a, 0.25)), 4),
        "p50": round(float(np.quantile(a, 0.50)), 4),
        "p75": round(float(np.quantile(a, 0.75)), 4),
        "frac_ge_0.9": round(float((a >= 0.9).mean()), 4),
        "frac_ge_0.95": round(float((a >= 0.95).mean()), 4),
    }


def tip_box_from_bars(
    tip_df: pd.DataFrame,
    tip_tf,
    *,
    bar_b0: int,
    bar_b1: int,
) -> tuple[float, float, float, float] | None:
    """Rewrite owner bar span into tip window: right edge forced to tip."""
    width = max(1, int(bar_b1) - int(bar_b0))
    t1 = WINDOW - 1
    t0 = max(0, t1 - width)
    return segment_to_bbox(tip_df, DenseSegment(start=t0, end=t1), tip_tf)


def process_row(
    r: pd.Series,
    *,
    out_img: Path,
    out_lbl: Path,
    cache: dict[str, pd.DataFrame | None],
    stats: Stats,
) -> bool:
    sym = str(r["symbol"])
    if sym not in cache:
        cache[sym] = resolve_series(sym)
        if cache[sym] is not None:
            cache[sym] = add_mas(cache[sym])
        else:
            stats.missing_symbols.append(sym)
    df = cache[sym]
    if df is None:
        stats.skip_no_series += 1
        return False
    cut = int(r["cut_global"])
    tip_start = cut - WINDOW + 1
    if tip_start < 0 or cut >= len(df):
        stats.skip_window += 1
        return False
    tip_sub = df.iloc[tip_start : cut + 1].reset_index(drop=True)
    if len(tip_sub) != WINDOW:
        stats.skip_window += 1
        return False
    try:
        _, tip_tf = render_chart(tip_sub, out_path=out_img)
        box = tip_box_from_bars(
            tip_sub,
            tip_tf,
            bar_b0=int(r["bar_b0"]),
            bar_b1=int(r["bar_b1"]),
        )
        if box is None:
            stats.skip_box += 1
            out_img.unlink(missing_ok=True)
            return False
        xc, yc, w, h = box
        right_frac = float(xc + w / 2)
        stats.box_right_fracs.append(right_frac)
        out_lbl.write_text(f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
    except Exception:
        stats.skip_error += 1
        out_img.unlink(missing_ok=True)
        return False
    stats.ok += 1
    return True


def _draw_overlay(img_path: Path, boxes: list[tuple[float, float, float, float]], out: Path) -> None:
    img = cv2.imread(str(img_path))
    if img is None:
        return
    h, w = img.shape[:2]
    for xc, yc, bw, bh in boxes:
        x1 = int((xc - bw / 2) * w)
        x2 = int((xc + bw / 2) * w)
        y1 = int((yc - bh / 2) * h)
        y2 = int((yc + bh / 2) * h)
        cv2.rectangle(img, (x1, y1), (x2, y2), COLOR_BOX, 2, cv2.LINE_AA)
        cv2.line(img, (x2, 0), (x2, h - 1), (0, 220, 220), 1, cv2.LINE_AA)
    cv2.line(img, (w - 1, 0), (w - 1, h - 1), COLOR_TIP, 2, cv2.LINE_AA)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)


def write_sample30(ds: Path, sheet_ok: pd.DataFrame, sample_dir: Path, seed: int = 42) -> None:
    """Draw 20 train + 10 val tip overlays for owner eyeball."""
    if sample_dir.exists():
        import shutil

        shutil.rmtree(sample_dir)
    (sample_dir / "images").mkdir(parents=True)

    rows = []
    for sp, n in (("train", 20), ("val", 10)):
        pool = sheet_ok[sheet_ok["split_time"] == sp]
        stems = sorted(pool["out_stem"].unique())
        rng = random.Random(seed + (0 if sp == "train" else 1))
        pick = stems if len(stems) <= n else rng.sample(stems, n)
        for i, stem in enumerate(sorted(pick), 1):
            rows.append((sp, i, stem, pool[pool["out_stem"] == stem].iloc[0]))

    md = [
        "# owner_side_short_tip — 30 张 tip 对齐样本（GT 绿框）",
        "",
        f"- 来源：`{ds.relative_to(PROJECT)}/`（tip 重裁窗 + 重写框；时间切分 VAL_CUT={VAL_CUT.date()}）",
        f"- 抽样：固定 seed={seed}，train 20 + val 10",
        "- 画法：绿色 = YOLO GT；青黄竖线 = 框右缘；图最右红竖线 = 盘口 tip",
        "- **未开训**；等 Owner 看图确认后再决定是否 train。",
        "",
        "| # | split | stem | symbol | box右缘 | cut_time | 图 |",
        "|---|-------|------|--------|---------|----------|----|",
    ]
    html = [
        "<!DOCTYPE html><html><head><meta charset=utf-8>",
        "<title>owner_side_short_tip sample30</title>",
        "<style>body{font-family:system-ui;margin:16px} img{max-width:960px;border:1px solid #ccc}"
        " .card{margin:24px 0}</style></head><body>",
        "<h1>owner_side_short_tip sample30</h1>",
        "<p>绿框=GT · 红线=tip(图最右) · 未开训</p>",
    ]
    for sp, i, stem, r in rows:
        img_src = ds / "images" / sp / f"{stem}.png"
        lbl = ds / "labels" / sp / f"{stem}.txt"
        boxes = []
        if lbl.exists():
            for line in lbl.read_text().splitlines():
                p = line.split()
                if len(p) >= 5:
                    boxes.append(tuple(map(float, p[1:5])))
        right = ", ".join(f"{xc + bw / 2:.3f}" for xc, _, bw, _ in boxes) or "?"
        name = f"{sp}_{i:02d}_{stem}.png"
        _draw_overlay(img_src, boxes, sample_dir / "images" / name)
        md.append(
            f"| {sp}-{i:02d} | {sp} | `{stem}` | {r['symbol']} | {right} | "
            f"{r['cut_time']} | [打开](images/{name}) |"
        )
        html.append(
            f"<div class=card><h3>{sp}-{i:02d} {stem}</h3>"
            f"<p>symbol={r['symbol']} · right={right} · cut={r['cut_time']}</p>"
            f"<img src='images/{name}'></div>"
        )
    md += ["", "## 绝对路径", "", f"`{sample_dir.resolve()}`", ""]
    html.append("</body></html>")
    (sample_dir / "index.md").write_text("\n".join(md) + "\n")
    (sample_dir / "index.html").write_text("\n".join(html) + "\n")
    print(f"wrote sample30 → {sample_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--limit", type=int, default=0, help="max boxes to render (0=all)")
    ap.add_argument("--sample-only", action="store_true", help="only redraw sample30 from existing out")
    ap.add_argument("--sample-out", type=Path, default=SAMPLE_OUT)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not SHEET.exists():
        print(f"missing sheet: {SHEET}", file=sys.stderr)
        return 2

    df = pd.read_csv(SHEET)
    df = df[df["owner_side"].astype(str).str.lower() == "short"].copy()
    df["ct"] = pd.to_datetime(df["cut_time"], utc=True)
    n_raw = len(df)
    holdout_n = int((df["ct"] >= HOLDOUT).sum())
    df = df[df["ct"] < HOLDOUT].copy()
    if df.empty:
        print("no short rows pre-holdout", file=sys.stderr)
        return 2

    df["split_time"] = np.where(df["ct"] < VAL_CUT, "train", "val")
    df["out_stem"] = df["box_id"].astype(str)

    if args.sample_only:
        if not args.out.exists():
            print(f"out missing: {args.out}", file=sys.stderr)
            return 2
        # recover built stems from labels
        kept = []
        for sp in ("train", "val"):
            for lbl in (args.out / "labels" / sp).glob("*.txt"):
                sub = df[(df["out_stem"] == lbl.stem) & (df["split_time"] == sp)]
                if not sub.empty:
                    kept.append(sub.iloc[0])
        write_sample30(args.out, pd.DataFrame(kept), args.sample_out, seed=args.seed)
        return 0

    import shutil

    if args.out.exists():
        shutil.rmtree(args.out)
    for sp in ("train", "val"):
        (args.out / "images" / sp).mkdir(parents=True)
        (args.out / "labels" / sp).mkdir(parents=True)

    stats = Stats(holdout_dropped=holdout_n)
    cache: dict[str, pd.DataFrame | None] = {}
    n_img = Counter()
    n_box = Counter()
    built_rows: list[pd.Series] = []
    old_right = df["box_right_frac"].astype(float).tolist()

    for i, (_, r) in enumerate(df.iterrows()):
        if args.limit and stats.ok >= args.limit:
            break
        sp = str(r["split_time"])
        stem = str(r["out_stem"])
        out_img = args.out / "images" / sp / f"{stem}.png"
        out_lbl = args.out / "labels" / sp / f"{stem}.txt"
        ok = process_row(r, out_img=out_img, out_lbl=out_lbl, cache=cache, stats=stats)
        if ok:
            n_img[sp] += 1
            n_box[sp] += 1
            built_rows.append(r)
            if stats.ok % 50 == 0:
                print(
                    f"  ok={stats.ok} skip_series={stats.skip_no_series} "
                    f"skip_win={stats.skip_window} …",
                    flush=True,
                )

    yaml = f"""# Owner short-side tip-aligned boxes (option-1 rebuild).
# Tip = image right edge. Time split at {VAL_CUT.date()} (not sheet/v11 split).
path: {args.out.resolve()}
train: images/train
val: images/val
names:
  0: dense_cluster
nc: 1
"""
    (args.out / "data.yaml").write_text(yaml)

    # time-split evidence
    train_max = None
    val_min = None
    if built_rows:
        br = pd.DataFrame(built_rows)
        tr = br[br["split_time"] == "train"]["ct"]
        va = br[br["split_time"] == "val"]["ct"]
        train_max = str(tr.max()) if len(tr) else None
        val_min = str(va.min()) if len(va) else None

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "script": "scripts/build_owner_side_short_yolo_tip.py",
        "git": _git_desc(),
        "source_sheet": str(SHEET.relative_to(PROJECT)),
        "out": str(args.out.relative_to(PROJECT)),
        "recipe": (
            "tip re-crop: window ends at cut_global; full-series add_mas then slice; "
            "YOLO box right edge forced to tip; one image per box_id; short only"
        ),
        "window": WINDOW,
        "val_cut": str(VAL_CUT),
        "holdout_cut": str(HOLDOUT),
        "n_short_rows_raw": n_raw,
        "holdout_dropped": holdout_n,
        "n_images": dict(n_img),
        "n_boxes": dict(n_box),
        "skips": {
            "no_series": stats.skip_no_series,
            "window": stats.skip_window,
            "box": stats.skip_box,
            "error": stats.skip_error,
        },
        "missing_symbols_unique": sorted(set(stats.missing_symbols)),
        "box_right_frac_before": _quantiles(old_right),
        "box_right_frac_after": _quantiles(stats.box_right_fracs),
        "time_split_evidence": {
            "train_cut_time_max": train_max,
            "val_cut_time_min": val_min,
            "train_max_ge_val_min": bool(
                train_max and val_min and pd.Timestamp(train_max) >= pd.Timestamp(val_min)
            ),
            "rule": f"train: cut_time < {VAL_CUT.date()}; val: [{VAL_CUT.date()}, {HOLDOUT.date()})",
        },
        "training": "NOT started — await owner sample review",
    }
    (args.out / "build_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))

    if stats.ok == 0:
        print("BUILD FAILED: zero images rendered", file=sys.stderr)
        if stats.missing_symbols:
            print(
                f"missing kline for {len(set(stats.missing_symbols))} symbols "
                f"(e.g. {sorted(set(stats.missing_symbols))[:10]})",
                file=sys.stderr,
            )
        return 1

    write_sample30(args.out, pd.DataFrame(built_rows), args.sample_out, seed=args.seed)
    print(f"wrote {args.out}/data.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
