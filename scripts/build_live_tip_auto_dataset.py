#!/usr/bin/env python3
"""Auto-label live tip charts with v12 (owner_best) tip-edge boxes.

Pipeline (Owner 2026-07-21):
  1. Render fixed 200-bar tip windows (right edge = "now", no future).
  2. Predict with models/owner_best.pt (v12 H-TIP mainline).
  3. Keep ONLY tip-edge boxes — same gate as live: bar_in_win in last
     TIP_EDGE_BARS (default 2 = tip / tip-1). Mid-window / post-hoc boxes drop.
  4. Optional soft filter: tip MA full_spread still huge → discard (noise).
  5. Write non-empty YOLO labels until --count (default 1000) or budget ends.

Does NOT use rule auto_label as GT, does NOT remap pad200 gold, does NOT
promote / train / touch VPS.

Usage:
  OMP_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python scripts/build_live_tip_auto_dataset.py \\
      --count 20 --preview 4   # smoke
  OMP_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python scripts/build_live_tip_auto_dataset.py \\
      --count 1000 --preview 4
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
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import BLOCKED_BASES  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import MIN_REL_SPAN, render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
SCORE_START = pd.Timestamp("2025-06-01", tz="UTC")
DS_NAME = "label_live_tip_auto1000"
CLASS_ID = 0
# Soft prior only: rank tip windows by tip full_spread (lower = denser).
# Acceptance still requires a v12 tip-edge box — this is NOT the GT.
TIP_LOOKBACK = 12
# Optional noise gate: tip mean full_spread above this → drop even with a box.
# 0 disables. Default 0.012 ≈ 1.2% (well above dense rule 0.55%).
DEFAULT_MAX_TIP_SPREAD = 0.012
STEM_RE = re.compile(r"^(?P<sym>.+)_(?P<idx>\d{4,})$")
GREEN = (60, 200, 120)


def tip_spread_rank(sub: pd.DataFrame) -> float:
    tip = sub.iloc[-TIP_LOOKBACK:]
    spread = pd.to_numeric(tip["full_spread"], errors="coerce")
    return float(spread.mean()) if spread.notna().any() else 9.0


def collect_tip_candidates(
    csv_paths: list[Path],
    *,
    stride: int,
    per_symbol: int,
    rng: random.Random,
) -> list[tuple[float, str, Path, int]]:
    """Return (rank, stem, csv_path, tip_idx) sorted densest-tip-first."""
    cands: list[tuple[float, str, Path, int]] = []
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
        enriched = add_mas(df)
        ts = enriched["open_time"]
        i0 = max(WINDOW - 1, int(ts.searchsorted(SCORE_START)))
        tips: list[int] = []
        i = i0
        while i < len(enriched):
            if ts.iloc[i] >= HOLDOUT_START:
                break
            tips.append(i)
            i += stride
        if not tips:
            continue
        if len(tips) > per_symbol * 5:
            tips = rng.sample(tips, per_symbol * 5)
        scored: list[tuple[float, str, Path, int]] = []
        for tip_i in tips:
            sub = enriched.iloc[tip_i - WINDOW + 1 : tip_i + 1].reset_index(drop=True)
            if len(sub) != WINDOW:
                continue
            if sub["full_spread"].isna().all():
                continue
            hi = float(sub["high"].max())
            lo = float(sub["low"].min())
            mid = (hi + lo) / 2.0
            if mid <= 0 or (hi - lo) / mid < MIN_REL_SPAN:
                continue
            rank = tip_spread_rank(sub)
            scored.append((rank, f"{sym}_{tip_i:06d}", csv_path, tip_i))
        scored.sort(key=lambda x: x[0])
        cands.extend(scored[:per_symbol])
        if k % 50 == 0:
            print(f"  scout {k}/{len(csv_paths)} pool={len(cands)}", flush=True)
        del df, enriched
    cands.sort(key=lambda x: x[0])
    return cands


def diversify(
    cands: list[tuple[float, str, Path, int]],
    *,
    max_try: int,
    per_sym_cap: int,
) -> list[tuple[float, str, Path, int]]:
    """Cap per-symbol so we try many coins before exhausting the densest few."""
    by_sym: dict[str, int] = defaultdict(int)
    out: list[tuple[float, str, Path, int]] = []
    for row in cands:
        sym = row[1].rsplit("_", 1)[0]
        if by_sym[sym] >= per_sym_cap:
            continue
        out.append(row)
        by_sym[sym] += 1
        if len(out) >= max_try:
            break
    if len(out) < max_try:
        have = {r[1] for r in out}
        for row in cands:
            if row[1] in have:
                continue
            out.append(row)
            have.add(row[1])
            if len(out) >= max_try:
                break
    return out


def tip_edge_boxes(
    result,
    tf,
    *,
    tip_edge_bars: int,
    conf_min: float,
) -> list[tuple[float, float, float, float, float]]:
    """Return (xc, yc, w, h, conf) kept by the live tip-edge gate."""
    kept: list[tuple[float, float, float, float, float]] = []
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return kept
    xywhn = boxes.xywhn.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    min_bar = WINDOW - tip_edge_bars
    for b, c in zip(xywhn, confs):
        conf = float(c)
        if conf < conf_min:
            continue
        cx, cy, w, h = map(float, b[:4])
        bar = right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
        if bar < min_bar:
            continue
        kept.append((cx, cy, w, h, conf))
    return kept


def to_yolo_lines(boxes: list[tuple[float, float, float, float, float]]) -> str:
    return "".join(
        f"{CLASS_ID} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n" for xc, yc, w, h, _ in boxes
    )


def draw_preview(img_path: Path, boxes, out_path: Path) -> None:
    img = cv2.imread(str(img_path))
    if img is None:
        return
    ih, iw = img.shape[:2]
    for xc, yc, w, h, conf in boxes:
        x1 = int((xc - w / 2) * iw)
        y1 = int((yc - h / 2) * ih)
        x2 = int((xc + w / 2) * iw)
        y2 = int((yc + h / 2) * ih)
        cv2.rectangle(img, (x1, y1), (x2, y2), GREEN, 3)
        cv2.putText(
            img,
            f"{conf:.2f}",
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            GREEN,
            2,
            cv2.LINE_AA,
        )
        # mark image right edge for tip alignment check
        cv2.line(img, (iw - 2, 0), (iw - 2, ih - 1), (40, 40, 220), 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=1000, help="target labeled images")
    ap.add_argument("--max-try", type=int, default=0, help="max tip windows to try (0=auto)")
    ap.add_argument("--stride", type=int, default=20)
    ap.add_argument("--per-symbol", type=int, default=16, help="scout keep per symbol")
    ap.add_argument("--per-sym-cap", type=int, default=12, help="try-cap per symbol")
    ap.add_argument("--seed", type=int, default=20260721)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--tip-edge-bars", type=int, default=TIP_EDGE_BARS)
    ap.add_argument(
        "--max-tip-spread",
        type=float,
        default=DEFAULT_MAX_TIP_SPREAD,
        help="soft noise gate; 0 disables",
    )
    ap.add_argument(
        "--weights",
        type=Path,
        default=PROJECT / "models" / "owner_best.pt",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "datasets" / DS_NAME,
    )
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT / "analysis" / "output" / "live_tip_auto_preview",
    )
    ap.add_argument("--preview", type=int, default=4)
    ap.add_argument("--device", default="", help="YOLO device override (cpu/0); empty=auto")
    args = ap.parse_args()

    if args.device:
        os.environ["FABLE_YOLO_DEVICE"] = args.device

    out: Path = args.out if args.out.is_absolute() else PROJECT / args.out
    forbidden = ("dense_owner_v11", "dense_owner_v12", "dense_owner_v12_htip")
    if any(part in forbidden for part in out.parts):
        raise SystemExit(f"refusing to write under forbidden path: {out}")

    img_dir = out / "images" / "train"
    lbl_dir = out / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    preview_dir: Path = (
        args.preview_dir if args.preview_dir.is_absolute() else PROJECT / args.preview_dir
    )
    preview_dir.mkdir(parents=True, exist_ok=True)

    weights = args.weights if args.weights.is_absolute() else PROJECT / args.weights
    if not weights.exists():
        raise SystemExit(f"weights missing: {weights}")

    # Confirm v12 cutover metadata when present.
    meta_json = weights.with_suffix(".json")
    weights_note = weights.name
    if meta_json.exists():
        try:
            mj = json.loads(meta_json.read_text(encoding="utf-8"))
            weights_note = (
                f"{weights.name} source_run={mj.get('source_run')} "
                f"promote_mode={mj.get('promote_mode')}"
            )
        except Exception:  # noqa: BLE001
            pass
    print(f"weights={weights_note}", flush=True)
    print(
        f"gate=tip_edge_bars={args.tip_edge_bars} conf>={args.conf} "
        f"max_tip_spread={args.max_tip_spread}",
        flush=True,
    )

    rng = random.Random(args.seed)
    csvs = sorted(
        Path(p)
        for p in glob.glob(str(PROJECT / "data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv"))
    )
    if not csvs:
        raise SystemExit("no SWAP 15m csv under data/kline_fetched/")

    t0 = time.time()
    print(f"scouting tip candidates across {len(csvs)} SWAP series…", flush=True)
    cands = collect_tip_candidates(
        csvs, stride=args.stride, per_symbol=args.per_symbol, rng=rng
    )
    max_try = args.max_try or max(args.count * 8, 4000)
    queue = diversify(cands, max_try=max_try, per_sym_cap=args.per_sym_cap)
    print(
        f"scout pool={len(cands)} try_queue={len(queue)} target={args.count}",
        flush=True,
    )

    model = load_yolo_model(weights)
    device = os.environ.get("FABLE_YOLO_DEVICE", "").strip() or None

    kept_meta: list[dict] = []
    stats = Counter()
    right_edges: list[float] = []
    preview_budget = args.preview

    # Process in small batches for predict efficiency.
    batch_size = 8
    i = 0
    while i < len(queue) and len(kept_meta) < args.count:
        batch = queue[i : i + batch_size]
        i += batch_size
        rendered: list[tuple[str, Path, int, Path, object, float, pd.DataFrame]] = []
        for rank, stem, csv_path, tip_i in batch:
            stats["tried"] += 1
            png = img_dir / f"{stem}.png"
            try:
                df = pd.read_csv(
                    csv_path, usecols=["ts", "open", "high", "low", "close", "volume"]
                )
                df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                enriched = add_mas(df)
                sub = enriched.iloc[tip_i - WINDOW + 1 : tip_i + 1].reset_index(drop=True)
                if len(sub) != WINDOW:
                    stats["skip_window"] += 1
                    continue
                tip_sp = tip_spread_rank(sub)
                if args.max_tip_spread > 0 and tip_sp > args.max_tip_spread:
                    stats["skip_spread"] += 1
                    continue
                _, tf = render_chart(sub, out_path=png)
                rendered.append((stem, csv_path, tip_i, png, tf, tip_sp, sub))
            except Exception as exc:  # noqa: BLE001
                stats["skip_render"] += 1
                stats["last_err"] = f"{type(exc).__name__}: {exc}"
                continue

        if not rendered:
            continue
        predict_kw = {"conf": max(0.05, args.conf - 0.05), "verbose": False}
        if device:
            predict_kw["device"] = device
        try:
            results = model.predict([str(r[3]) for r in rendered], **predict_kw)
        except Exception as exc:  # noqa: BLE001
            stats["predict_fail"] += len(rendered)
            stats["last_err"] = f"{type(exc).__name__}: {exc}"
            continue

        for (stem, csv_path, tip_i, png, tf, tip_sp, _sub), res in zip(rendered, results):
            kept = tip_edge_boxes(
                res, tf, tip_edge_bars=args.tip_edge_bars, conf_min=args.conf
            )
            stats["raw_pred_images"] += 1
            if not kept:
                stats["no_tip_box"] += 1
                # Drop unlabeled tip PNG to avoid empty-label pollution.
                if png.exists():
                    png.unlink()
                continue
            lbl = lbl_dir / f"{stem}.txt"
            lbl.write_text(to_yolo_lines(kept), encoding="utf-8")
            for xc, _, w, _, _ in kept:
                right_edges.append(xc + w / 2)
            row = {
                "stem": stem,
                "symbol": stem.rsplit("_", 1)[0],
                "tip_idx": tip_i,
                "tip_spread": round(tip_sp, 6),
                "n_boxes": len(kept),
                "max_conf": round(max(b[4] for b in kept), 4),
                "right_norms": [round(xc + w / 2, 4) for xc, _, w, _, _ in kept],
                "bars": [
                    right_edge_to_bar(xc, w, tf, n_bars=WINDOW)
                    for xc, _, w, _, _ in kept
                ],
                "csv": csv_path.name,
            }
            kept_meta.append(row)
            stats["labeled"] += 1
            if preview_budget > 0:
                draw_preview(
                    png,
                    kept,
                    preview_dir / f"preview_{len(kept_meta):02d}_{stem}.png",
                )
                preview_budget -= 1
            if len(kept_meta) >= args.count:
                break

        if stats["tried"] % 64 == 0 or len(kept_meta) >= args.count:
            print(
                f"  tried={stats['tried']} labeled={len(kept_meta)} "
                f"no_tip={stats['no_tip_box']} spread_skip={stats['skip_spread']} "
                f"{(time.time() - t0) / 60:.1f} min",
                flush=True,
            )

    # Stats / README
    sym_counts = Counter(r["symbol"] for r in kept_meta)
    right_arr = np.array(right_edges, dtype=float) if right_edges else np.array([])
    summary = {
        "protocol": "live_tip_v12_tip_edge_auto",
        "weights": str(weights.relative_to(PROJECT)),
        "weights_note": weights_note,
        "window": WINDOW,
        "tip_edge_bars": args.tip_edge_bars,
        "conf": args.conf,
        "max_tip_spread": args.max_tip_spread,
        "holdout_excluded_from": str(HOLDOUT_START.date()),
        "score_start": str(SCORE_START.date()),
        "target": args.count,
        "labeled": len(kept_meta),
        "tried": int(stats["tried"]),
        "no_tip_box": int(stats["no_tip_box"]),
        "skip_spread": int(stats["skip_spread"]),
        "symbols": len(sym_counts),
        "symbol_top20": sym_counts.most_common(20),
        "box_right_norm": {
            "n": int(right_arr.size),
            "min": float(right_arr.min()) if right_arr.size else None,
            "p50": float(np.median(right_arr)) if right_arr.size else None,
            "p10": float(np.percentile(right_arr, 10)) if right_arr.size else None,
            "mean": float(right_arr.mean()) if right_arr.size else None,
        },
        "out": str(out.relative_to(PROJECT)),
        "preview_dir": str(preview_dir.resolve()),
        "note": (
            "GT = v12 owner_best tip-edge boxes only (bar in last N). "
            "Tip-spread ranking is scout prior, not the label source."
        ),
        "meta_head": kept_meta[:20],
    }
    (out / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\n"
        "train: images/train\n"
        "val: images/train\n"
        "names:\n"
        "  0: dense_cluster\n",
        encoding="utf-8",
    )

    how_relax = []
    if len(kept_meta) < args.count:
        how_relax = [
            f"- Lower --conf (now {args.conf}; try 0.20)",
            f"- Raise --max-tip-spread (now {args.max_tip_spread}; or 0 to disable)",
            f"- Raise --tip-edge-bars (now {args.tip_edge_bars}; live uses 2 — prefer not)",
            "- Raise --max-try / --per-symbol / lower --stride to scan more tips",
        ]
    readme = [
        "Live tip auto labels (v12 tip-edge)",
        "===================================",
        f"Labeled: {len(kept_meta)} / target {args.count}",
        f"Tried tip windows: {stats['tried']}",
        f"Dropped (no tip-edge box): {stats['no_tip_box']}",
        f"Dropped (tip spread gate): {stats['skip_spread']}",
        f"Symbols covered: {len(sym_counts)}",
        f"Weights: {weights_note}",
        f"Gate: bar_in_win >= {WINDOW - args.tip_edge_bars} "
        f"(TIP_EDGE_BARS={args.tip_edge_bars}), conf>={args.conf}",
        f"Soft scout: rank by tip full_spread; accept only v12 tip-edge boxes",
        "",
        "Box right-edge (norm) distribution:",
        f"  n={summary['box_right_norm']['n']}  "
        f"min={summary['box_right_norm']['min']}  "
        f"p10={summary['box_right_norm']['p10']}  "
        f"p50={summary['box_right_norm']['p50']}  "
        f"mean={summary['box_right_norm']['mean']}",
        "",
        f"Images: {img_dir}",
        f"Labels: {lbl_dir} (non-empty .txt only)",
        f"Previews: {preview_dir.resolve()}",
        "",
        "Spot-check:",
        f"  open {preview_dir.resolve()}/preview_*.png",
        "  — green box should sit on the RIGHT; red line = image right edge",
        "",
    ]
    if how_relax:
        readme += ["Yield short of target — how to relax:", *how_relax, ""]
    (out / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
    (preview_dir / "README.txt").write_text(
        "\n".join(
            [
                "v12 tip-edge auto-label previews",
                f"dataset: {out}",
                f"labeled: {len(kept_meta)}",
                f"weights: {weights_note}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"DONE labeled={len(kept_meta)}/{args.count} tried={stats['tried']} "
        f"in {(time.time() - t0) / 60:.1f} min → {out}",
        flush=True,
    )
    for p in sorted(preview_dir.glob("preview_*.png"))[: args.preview]:
        print(f"PREVIEW={p.resolve()}", flush=True)
    return 0 if kept_meta else 1


if __name__ == "__main__":
    raise SystemExit(main())
