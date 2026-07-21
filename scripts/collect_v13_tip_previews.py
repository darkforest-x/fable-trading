#!/usr/bin/env python3
"""Collect a small batch of real tip-window previews with boxes (v13 smoke).

Replay tip windows at forward_log signal tips (and tip+1/+2) under production
gates (TIP_EDGE_BARS, owner_best). Writes PNG overlays + a manifest JSON —
does NOT build a training pool, does not touch holdout / forward_log.

Usage (prefer VPS where klines cover log signal times):
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \\
    scripts/collect_v13_tip_previews.py \\
    --log data/forward_log.csv --limit 12 --conf 0.20 \\
    --out analysis/output/v13_real_tip_preview
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_WEIGHTS,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
    _resolve_predict_device,
)

GREEN = (40, 180, 60)
RED = (40, 40, 220)
ORANGE = (0, 140, 255)


def _parse_ts(s: str) -> pd.Timestamp:
    ts = pd.Timestamp(s)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def load_frame(symbol: str) -> pd.DataFrame:
    for (_src, sym), paths in list_series(bar="15m").items():
        if sym == symbol:
            df = load_series(paths)
            if df.empty:
                raise FileNotFoundError(symbol)
            return df
    raise FileNotFoundError(symbol)


def draw_boxes(img_path: Path, boxes: list[dict], out_path: Path) -> None:
    img = cv2.imread(str(img_path))
    if img is None:
        return
    ih, iw = img.shape[:2]
    cv2.line(img, (iw - 2, 0), (iw - 2, ih - 1), RED, 2)
    for b in boxes:
        xc, yc, w, h = b["xc"], b["yc"], b["w"], b["h"]
        x1, y1 = int((xc - w / 2) * iw), int((yc - h / 2) * ih)
        x2, y2 = int((xc + w / 2) * iw), int((yc + h / 2) * ih)
        color = GREEN if b["kept"] else ORANGE
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{b['conf']:.2f} bar={b['bar_in_win']}{' KEEP' if b['kept'] else ' DROP'}"
        cv2.putText(
            img, label, (x1, max(16, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def predict_tip_window(
    frame: pd.DataFrame,
    tip_i: int,
    model,
    *,
    conf: float,
    tip_edge_bars: int,
    tmp_png: Path,
) -> tuple[list[dict], Path | None]:
    """Render tip window ending at tip_i; return annotated box rows."""
    start = tip_i - WINDOW + 1
    if start < 0 or tip_i >= len(frame):
        return [], None
    enriched = add_mas(frame)
    sub = enriched.iloc[start : tip_i + 1]
    if len(sub) != WINDOW:
        return [], None
    _, tf = render_chart(sub, out_path=tmp_png)
    res = model.predict(
        [str(tmp_png)], conf=conf, verbose=False, device=_resolve_predict_device()
    )[0]
    boxes: list[dict] = []
    min_bar = WINDOW - tip_edge_bars
    if res.boxes is not None:
        xywhn = res.boxes.xywhn.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        for b, c in zip(xywhn, confs):
            xc, yc, w, h = map(float, b[:4])
            bar = right_edge_to_bar(xc, w, tf, n_bars=WINDOW)
            boxes.append(
                {
                    "xc": xc,
                    "yc": yc,
                    "w": w,
                    "h": h,
                    "conf": float(c),
                    "bar_in_win": int(bar),
                    "offset_from_tip": int(WINDOW - 1 - bar),
                    "kept": bar >= min_bar,
                }
            )
    return boxes, tmp_png


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=PROJECT / "data" / "forward_log.csv")
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--conf", type=float, default=0.20, help="raw floor for preview")
    ap.add_argument("--tip-edge-bars", type=int, default=TIP_EDGE_BARS)
    ap.add_argument("--limit", type=int, default=12, help="max unique signal tips")
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "analysis" / "output" / "v13_real_tip_preview",
    )
    args = ap.parse_args()

    if not args.log.exists():
        print(f"log missing: {args.log}", file=sys.stderr)
        return 2
    if not args.weights.exists():
        print(f"weights missing: {args.weights}", file=sys.stderr)
        return 2

    rows = list(csv.DictReader(args.log.open()))
    # Prefer recent unique (symbol, signal_time); keep order from log.
    seen: set[tuple[str, str]] = set()
    targets: list[dict] = []
    for r in reversed(rows):
        key = (r["symbol"], r["signal_time"])
        if key in seen:
            continue
        seen.add(key)
        targets.append(r)
        if len(targets) >= args.limit:
            break
    targets = list(reversed(targets))

    out: Path = args.out if args.out.is_absolute() else PROJECT / args.out
    out.mkdir(parents=True, exist_ok=True)
    tmp_dir = out / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    model = load_yolo_model(args.weights)
    manifest: list[dict] = []
    counts = {"tip_hit": 0, "tip_miss_any_box": 0, "tip_empty": 0, "error": 0}

    for rec in targets:
        sym = rec["symbol"]
        st = rec["signal_time"]
        print(f"=== {sym} {st} ===", flush=True)
        try:
            frame = load_frame(sym)
        except FileNotFoundError as exc:
            counts["error"] += 1
            manifest.append({"symbol": sym, "signal_time": st, "error": str(exc)})
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        target_ts = _parse_ts(st)
        hits = (times == target_ts).to_numpy().nonzero()[0]
        if len(hits) == 0:
            signal_i = int((times - target_ts).abs().argmin())
            aligned = False
        else:
            signal_i = int(hits[0])
            aligned = True

        # tip at signal, tip+1, tip+2 (formation window)
        for back, tip_i in enumerate([signal_i, signal_i + 1, signal_i + 2]):
            if tip_i >= len(frame):
                continue
            stem = f"{sym}_{pd.Timestamp(times.iloc[signal_i]).strftime('%Y%m%d_%H%M')}_tipplus{back}"
            raw_png = tmp_dir / f"{stem}.png"
            boxes, _ = predict_tip_window(
                frame,
                tip_i,
                model,
                conf=args.conf,
                tip_edge_bars=args.tip_edge_bars,
                tmp_png=raw_png,
            )
            kept = [b for b in boxes if b["kept"]]
            preview = out / f"{stem}.png"
            draw_boxes(raw_png, boxes, preview)
            tag = "tip_hit" if kept else ("tip_miss_any_box" if boxes else "tip_empty")
            if back == 0:
                counts[tag] = counts.get(tag, 0) + 1
            entry = {
                "symbol": sym,
                "signal_time": st,
                "signal_i": signal_i,
                "tip_i": tip_i,
                "tip_plus": back,
                "aligned": aligned,
                "n_boxes_raw": len(boxes),
                "n_kept": len(kept),
                "tag": tag,
                "boxes": boxes,
                "preview": str(preview.relative_to(PROJECT)),
                "log_score": rec.get("score"),
                "log_lag_hint": rec.get("detected_at"),
            }
            manifest.append(entry)
            print(
                f"  tip+{back}: raw={len(boxes)} kept={len(kept)} tag={tag} -> {preview.name}",
                flush=True,
            )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(args.weights),
        "conf": args.conf,
        "tip_edge_bars": args.tip_edge_bars,
        "n_targets": len(targets),
        "counts_at_signal_tip": counts,
        "rows": manifest,
    }
    man_path = out / "manifest.json"
    man_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"counts_at_signal_tip={counts}")
    print(f"wrote {man_path} previews={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
