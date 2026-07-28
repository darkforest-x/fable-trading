#!/usr/bin/env python3
"""Dump ~1000 tip_v1b tip-edge detections on real klines for Owner review (S3).

Owner ask (2026-07-24): after short detector is trained, run it on real OHLCV,
box ~1000 charts, and **exclude** samples already used in
`dense_owner_side_short_tip` / `dense_owner_side_short` gold sets.

Protocol (live tip, discipline 12):
  - fixed 200-bar window, right edge = tip bar, no future
  - keep ONLY tip-edge boxes (bar in last TIP_EDGE_BARS)
  - default weights = owner_side_short_tip_v1b best.pt
  - does NOT promote / train / touch holdout / ACTIVE / forward_log

Usage:
  # smoke
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
    scripts/dump_short_tip_detect_sample.py --count 20 --preview 8

  # full ~1000
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
    scripts/dump_short_tip_detect_sample.py --count 1000 --preview 40
"""
from __future__ import annotations

import argparse
import glob
import html
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
os.environ.setdefault("MPLBACKEND", "Agg")

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
# Prefer pre-holdout history that still leaves room outside the tip gold cut
# without burning holdout. Same band as other live-tip packs.
SCORE_START = pd.Timestamp("2025-06-01", tz="UTC")
TIP_LOOKBACK = 12
DEFAULT_MAX_TIP_SPREAD = 0.012
CLASS_ID = 0
GREEN = (60, 200, 120)
# stems look like SYM_000123 or okx_SYM_000123 or ...__b0
STEM_IDX_RE = re.compile(
    r"^(?:okx_)?(?P<sym>.+?)_(?P<idx>\d{4,})(?:__b\d+)?$", re.IGNORECASE
)
DEFAULT_WEIGHTS = (
    PROJECT
    / "runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt"
)
DEFAULT_EXCLUDE = [
    PROJECT / "datasets/dense_owner_side_short_tip",
    PROJECT / "datasets/dense_owner_side_short",
]


def tip_spread_rank(sub: pd.DataFrame) -> float:
    tip = sub.iloc[-TIP_LOOKBACK:]
    spread = pd.to_numeric(tip["full_spread"], errors="coerce")
    return float(spread.mean()) if spread.notna().any() else 9.0


def parse_stem_key(stem: str) -> tuple[str, int] | None:
    m = STEM_IDX_RE.match(stem)
    if not m:
        return None
    return m.group("sym"), int(m.group("idx"))


def load_exclude_keys(paths: list[Path]) -> tuple[set[tuple[str, int]], set[str], dict]:
    """Return (symbol, tip_idx) keys + raw stems found under exclude datasets."""
    keys: set[tuple[str, int]] = set()
    stems: set[str] = set()
    per_ds: dict[str, int] = {}
    for root in paths:
        if not root.exists():
            per_ds[str(root)] = -1
            continue
        n = 0
        for p in root.rglob("*"):
            if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".npy", ".txt"}:
                continue
            stem = p.stem
            # labels may share stem; images preferred but either works
            if p.suffix.lower() == ".txt" and p.parent.name not in {"train", "val", "labels"}:
                pass
            stems.add(stem)
            key = parse_stem_key(stem)
            if key is not None:
                keys.add(key)
                n += 1
        per_ds[str(root)] = n
    return keys, stems, per_ds


def collect_tip_candidates(
    csv_paths: list[Path],
    *,
    stride: int,
    per_symbol: int,
    rng: random.Random,
    exclude_keys: set[tuple[str, int]],
) -> list[tuple[float, str, Path, int]]:
    cands: list[tuple[float, str, Path, int]] = []
    skipped_ex = 0
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
            key = (sym, int(tip_i))
            # also try with/without okx_ prefix variants used in gold stems
            if key in exclude_keys or (f"okx_{sym}", tip_i) in exclude_keys:
                skipped_ex += 1
                continue
            # gold sometimes stores stem without SWAP quirks; match idx on bare sym too
            bare = sym
            if any(s == bare and idx == tip_i for s, idx in exclude_keys):
                skipped_ex += 1
                continue
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
            print(
                f"  scout {k}/{len(csv_paths)} pool={len(cands)} exclude_hits~{skipped_ex}",
                flush=True,
            )
        del df, enriched
    cands.sort(key=lambda x: x[0])
    print(f"scout exclude_skips={skipped_ex}", flush=True)
    return cands


def diversify(
    cands: list[tuple[float, str, Path, int]],
    *,
    max_try: int,
    per_sym_cap: int,
) -> list[tuple[float, str, Path, int]]:
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
        cv2.line(img, (iw - 2, 0), (iw - 2, ih - 1), (40, 40, 220), 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def write_review_sheet(meta: list[dict], out_csv: Path, out_html: Path, preview_dir: Path) -> None:
    rows = []
    for i, r in enumerate(meta, 1):
        rows.append(
            {
                "i": i,
                "stem": r["stem"],
                "symbol": r["symbol"],
                "tip_idx": r["tip_idx"],
                "tip_time": r.get("tip_time", ""),
                "max_conf": r["max_conf"],
                "n_boxes": r["n_boxes"],
                "tip_spread": r["tip_spread"],
                "right_p50": round(float(np.median(r["right_norms"])), 4) if r["right_norms"] else "",
                "owner_keep": "",
                "owner_note": "",
                "image": f"images/train/{r['stem']}.png",
            }
        )
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    # compact HTML gallery (first 60 + count)
    cards = []
    for r in rows[:60]:
        prev = preview_dir / f"preview_{r['i']:04d}_{r['stem']}.png"
        src = prev if prev.exists() else Path(r["image"])
        # relative from review html location (out root)
        rel = os.path.relpath(src, out_html.parent)
        cards.append(
            "<div class='card'>"
            f"<div class='meta'>#{r['i']} {html.escape(r['symbol'])} "
            f"conf={r['max_conf']} spread={r['tip_spread']}</div>"
            f"<img src='{html.escape(rel)}' loading='lazy'/>"
            f"<div class='stem'>{html.escape(r['stem'])}</div>"
            "</div>"
        )
    body = "\n".join(cards)
    out_html.write_text(
        f"""<!doctype html>
<html><head><meta charset='utf-8'/>
<title>short tip_v1b detect sample</title>
<style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}}
.card{{background:#1b1b1b;border:1px solid #333;border-radius:8px;padding:8px}}
img{{width:100%;height:auto;border-radius:4px}}
.meta{{font-size:12px;margin-bottom:6px;color:#9cf}}
.stem{{font-size:11px;color:#888;word-break:break-all;margin-top:4px}}
</style></head>
<body>
<h1>owner_side_short_tip_v1b detect sample</h1>
<p>Total labeled: {len(rows)}. Showing first {min(60,len(rows))} previews.
Fill <code>review_sheet.csv</code> owner_keep / owner_note. Red line = image right edge; green = tip-edge box.</p>
<div class='grid'>
{body}
</div>
</body></html>
""",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--max-try", type=int, default=0)
    ap.add_argument("--stride", type=int, default=20)
    ap.add_argument("--per-symbol", type=int, default=16)
    ap.add_argument("--per-sym-cap", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20260724)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--tip-edge-bars", type=int, default=TIP_EDGE_BARS)
    ap.add_argument("--max-tip-spread", type=float, default=DEFAULT_MAX_TIP_SPREAD)
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "analysis/output/owner_side_short_tip_v1b_detect1000",
    )
    ap.add_argument("--preview", type=int, default=40)
    ap.add_argument("--device", default="", help="cpu / mps / 0; empty=auto")
    ap.add_argument(
        "--exclude-dataset",
        action="append",
        default=[],
        help="dataset root to exclude (repeatable). Defaults to tip+pretip short gold.",
    )
    args = ap.parse_args()

    if args.device:
        os.environ["FABLE_YOLO_DEVICE"] = args.device

    out: Path = args.out if args.out.is_absolute() else PROJECT / args.out
    img_dir = out / "images" / "train"
    lbl_dir = out / "labels" / "train"
    preview_dir = out / "previews"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    weights = args.weights if args.weights.is_absolute() else PROJECT / args.weights
    if not weights.exists():
        raise SystemExit(f"weights missing: {weights}")

    excl_paths = [Path(p) for p in args.exclude_dataset] if args.exclude_dataset else list(DEFAULT_EXCLUDE)
    excl_paths = [p if p.is_absolute() else PROJECT / p for p in excl_paths]
    exclude_keys, exclude_stems, excl_stats = load_exclude_keys(excl_paths)
    print(
        f"exclude_datasets={ {k: v for k, v in excl_stats.items()} } "
        f"keys={len(exclude_keys)} stems={len(exclude_stems)}",
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
        csvs,
        stride=args.stride,
        per_symbol=args.per_symbol,
        rng=rng,
        exclude_keys=exclude_keys,
    )
    # second-pass filter by stem string collision with gold stems
    cands = [c for c in cands if c[1] not in exclude_stems and f"okx_{c[1]}" not in exclude_stems]
    max_try = args.max_try or max(args.count * 10, 5000)
    queue = diversify(cands, max_try=max_try, per_sym_cap=args.per_sym_cap)
    print(
        f"scout pool={len(cands)} try_queue={len(queue)} target={args.count} "
        f"weights={weights}",
        flush=True,
    )

    model = load_yolo_model(weights)
    device = os.environ.get("FABLE_YOLO_DEVICE", "").strip() or None

    kept_meta: list[dict] = []
    stats = Counter()
    right_edges: list[float] = []
    preview_budget = args.preview
    batch_size = 6
    i = 0
    while i < len(queue) and len(kept_meta) < args.count:
        batch = queue[i : i + batch_size]
        i += batch_size
        rendered: list[tuple[str, Path, int, Path, object, float, pd.Timestamp]] = []
        for rank, stem, csv_path, tip_i in batch:
            stats["tried"] += 1
            # hard exclude if gold used this tip idx
            sym = stem.rsplit("_", 1)[0]
            if (sym, tip_i) in exclude_keys or stem in exclude_stems:
                stats["skip_exclude"] += 1
                continue
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
                tip_time = pd.Timestamp(sub["open_time"].iloc[-1])
                _, tf = render_chart(sub, out_path=png)
                rendered.append((stem, csv_path, tip_i, png, tf, tip_sp, tip_time))
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

        for (stem, csv_path, tip_i, png, tf, tip_sp, tip_time), res in zip(rendered, results):
            kept = tip_edge_boxes(
                res, tf, tip_edge_bars=args.tip_edge_bars, conf_min=args.conf
            )
            stats["raw_pred_images"] += 1
            if not kept:
                stats["no_tip_box"] += 1
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
                "tip_time": str(tip_time),
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
                    preview_dir / f"preview_{len(kept_meta):04d}_{stem}.png",
                )
                preview_budget -= 1
            if len(kept_meta) >= args.count:
                break

        if stats["tried"] % 48 == 0 or len(kept_meta) >= args.count:
            print(
                f"  tried={stats['tried']} labeled={len(kept_meta)} "
                f"no_tip={stats['no_tip_box']} spread_skip={stats['skip_spread']} "
                f"exclude={stats['skip_exclude']} {(time.time() - t0) / 60:.1f} min",
                flush=True,
            )

    sym_counts = Counter(r["symbol"] for r in kept_meta)
    right_arr = np.array(right_edges, dtype=float) if right_edges else np.array([])
    # collision audit vs exclude
    collisions = [
        r["stem"]
        for r in kept_meta
        if (r["symbol"], int(r["tip_idx"])) in exclude_keys or r["stem"] in exclude_stems
    ]
    summary = {
        "protocol": "short_tip_v1b_detect_sample_s3",
        "weights": str(weights.relative_to(PROJECT)) if str(weights).startswith(str(PROJECT)) else str(weights),
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
        "skip_exclude": int(stats["skip_exclude"]),
        "exclude_key_n": len(exclude_keys),
        "exclude_datasets": excl_stats,
        "collisions_with_train": collisions,
        "symbols": len(sym_counts),
        "symbol_top20": sym_counts.most_common(20),
        "box_right_norm": {
            "n": int(right_arr.size),
            "min": float(right_arr.min()) if right_arr.size else None,
            "p50": float(np.median(right_arr)) if right_arr.size else None,
            "p10": float(np.percentile(right_arr, 10)) if right_arr.size else None,
            "mean": float(right_arr.mean()) if right_arr.size else None,
        },
        "out": str(out),
        "promote": "NO",
        "note": (
            "Owner visual pack only. tip_v1b tip-edge boxes on real klines; "
            "train gold stems/indices excluded. Not a promote gate by itself."
        ),
        "meta_head": kept_meta[:20],
    }
    (out / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_review_sheet(
        kept_meta,
        out / "review_sheet.csv",
        out / "index.html",
        preview_dir,
    )
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\n"
        "train: images/train\n"
        "val: images/train\n"
        "names:\n"
        "  0: dense_cluster\n",
        encoding="utf-8",
    )
    readme = [
        "short tip_v1b detect sample (S3 Owner pack)",
        "==========================================",
        f"Labeled: {len(kept_meta)} / target {args.count}",
        f"Tried: {stats['tried']}",
        f"No tip-edge box: {stats['no_tip_box']}",
        f"Spread gate drops: {stats['skip_spread']}",
        f"Exclude hits at try: {stats['skip_exclude']}",
        f"Train collisions in output: {len(collisions)} (must be 0)",
        f"Weights: {weights}",
        f"Gate: tip-edge last {args.tip_edge_bars} bars, conf>={args.conf}",
        f"Right-edge p50: {summary['box_right_norm']['p50']}",
        "",
        "Review:",
        f"  open {out / 'index.html'}",
        f"  fill {out / 'review_sheet.csv'} columns owner_keep / owner_note",
        "",
        "Do NOT promote from this pack alone.",
    ]
    (out / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in [
        "labeled", "tried", "no_tip_box", "skip_spread", "skip_exclude",
        "symbols", "box_right_norm", "collisions_with_train", "promote"
    ]}, ensure_ascii=False, indent=2))
    print(f"wrote {out}", flush=True)
    if collisions:
        print("WARNING: train collisions present — inspect before review", flush=True)
        return 2
    return 0 if len(kept_meta) else 1


if __name__ == "__main__":
    raise SystemExit(main())
