#!/usr/bin/env python3
"""Stream-expand owner_side_review pack while the serve process stays up.

Does NOT block labeling:
  - Writes render_status.json for the gallery banner
  - Renders missing previews into previews/*.jpg
  - Appends newly extracted boxes to review_sheet.csv / items.json
  - Always preserves existing owner_side / owner_note

Runs until --target-boxes reached (or positives exhausted). Safe to Ctrl+C.

Usage (alongside serve):
  PYTHONPATH=. .venv/bin/python scripts/stream_owner_side_pack.py --target-boxes 2000
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_owner_side_review_pack import (  # noqa: E402
    SHEET_COLS,
    DEFAULT_OUT,
    DEFAULT_SRC,
    draw_preview,
    extract_boxes,
    list_positive_labels,
    pick_stems_for_sample,
    write_gallery_html,
)
from scripts.serve_owner_side_review import load_reviews  # noqa: E402

VALID = frozenset({"long", "short", "skip"})


def write_status(out: Path, **kwargs) -> None:
    payload = {"running": True, "ts": time.time(), **kwargs}
    tmp = out / "render_status.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out / "render_status.json")


def read_sheet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_sheet_atomic(path: Path, rows: list[dict]) -> None:
    """Rewrite sheet preserving columns; caller must merge owner_side first."""
    fields = list(SHEET_COLS)
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    tmp = path.with_suffix(".csv.streamtmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    # Merge any sides written by serve during our rewrite window.
    live_sides = {}
    if path.exists():
        for r in read_sheet(path):
            side = (r.get("owner_side") or "").strip().lower()
            if side in VALID:
                live_sides[r["box_id"]] = (
                    side,
                    r.get("owner_note") or "",
                )
    if live_sides:
        merged = read_sheet(tmp)
        for r in merged:
            if r["box_id"] in live_sides:
                r["owner_side"], r["owner_note"] = live_sides[r["box_id"]]
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in merged:
                w.writerow({k: r.get(k, "") for k in fields})
        rows = merged
    tmp.replace(path)
    return


def rows_to_items(rows: list[dict]) -> list[dict]:
    items = []
    for r in rows:
        items.append(
            {
                "box_id": r["box_id"],
                "symbol": r.get("symbol", ""),
                "stem": r.get("stem", ""),
                "split": r.get("split", "train"),
                "cut_time": r.get("cut_time", ""),
                "cut_global": int(float(r["cut_global"])) if r.get("cut_global") else 0,
                "width_bars": int(float(r["width_bars"])) if r.get("width_bars") else 0,
                "bar_b0": int(float(r["bar_b0"])) if r.get("bar_b0") not in ("", None) else 0,
                "bar_b1": int(float(r["bar_b1"])) if r.get("bar_b1") not in ("", None) else 0,
                "yolo": [
                    float(r["yolo_xc"]) if r.get("yolo_xc") not in ("", None) else 0.0,
                    float(r["yolo_yc"]) if r.get("yolo_yc") not in ("", None) else 0.0,
                    float(r["yolo_w"]) if r.get("yolo_w") not in ("", None) else 0.0,
                    float(r["yolo_h"]) if r.get("yolo_h") not in ("", None) else 0.0,
                ],
                "n_boxes_on_image": int(float(r.get("n_boxes_on_image") or 1)),
                "box_index": int(float(r.get("box_index") or 0)),
                "image_path": r.get("image_path", ""),
                "preview_path": r.get("preview_path", ""),
                "in_sample": int(float(r.get("in_sample") or 0)),
                "spread_chg8": r.get("spread_chg8", ""),
                "fast_spread": r.get("fast_spread", ""),
                "owner_side": r.get("owner_side", ""),
                "owner_note": r.get("owner_note", ""),
            }
        )
    return items


def write_items(out: Path, rows: list[dict]) -> None:
    # Re-apply latest sides from reviews.jsonl / sheet before publish
    sides = load_reviews(out)
    items = rows_to_items(rows)
    for it in items:
        if it["box_id"] in sides:
            it["owner_side"] = sides[it["box_id"]]["owner_side"]
            it["owner_note"] = sides[it["box_id"]].get("owner_note", "")
    tmp = out / "items.json.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out / "items.json")


def render_missing(src: Path, out: Path, rows: list[dict], *, batch: int = 40) -> int:
    n = 0
    for r in rows:
        if n >= batch:
            break
        prev = (r.get("preview_path") or "").strip()
        path = out / prev if prev else out / "previews" / f"{r['box_id']}.jpg"
        if path.is_file():
            if not prev:
                r["preview_path"] = f"previews/{r['box_id']}.jpg"
            continue
        ok = draw_preview(src, r, out / "previews" / f"{r['box_id']}.jpg")
        if ok:
            r["preview_path"] = f"previews/{r['box_id']}.jpg"
            n += 1
    return n


def expand_batch(
    src: Path,
    existing_stems: set[str],
    *,
    want_boxes: int,
    seed: int,
    series_cache: dict,
) -> list[dict]:
    positives = [
        p for p in list_positive_labels(src) if p[1] not in existing_stems
    ]
    if not positives:
        return []
    stems = pick_stems_for_sample(positives, want_boxes, seed)
    rows, skips = extract_boxes(
        src,
        series_cache=series_cache,
        with_diag=False,
        allow_stems=stems,
    )
    print(f"  expand extracted={len(rows)} skips={skips}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--target-boxes", type=int, default=2500)
    ap.add_argument("--batch-stems", type=int, default=120, help="new stems per expand round")
    ap.add_argument("--seed", type=int, default=20260723)
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "previews").mkdir(exist_ok=True)
    write_gallery_html(out)

    sheet_path = out / "review_sheet.csv"
    series_cache: dict = {}
    round_i = 0
    try:
        while True:
            round_i += 1
            rows = read_sheet(sheet_path)
            # Preserve sides from live reviews
            sides = load_reviews(out)
            for r in rows:
                if r["box_id"] in sides:
                    r["owner_side"] = sides[r["box_id"]]["owner_side"]
                    r["owner_note"] = sides[r["box_id"]].get("owner_note", "")

            n_ready = sum(
                1
                for r in rows
                if r.get("preview_path") and (out / r["preview_path"]).is_file()
            )
            write_status(
                out,
                message=f"round {round_i}: sheet={len(rows)} ready={n_ready}",
                n_boxes=len(rows),
                n_preview_ready=n_ready,
                target=args.target_boxes,
            )

            # 1) Render missing previews (never blocks serve — separate process)
            made = render_missing(args.src, out, rows, batch=50)
            if made:
                write_sheet_atomic(sheet_path, rows)
                write_items(out, rows)
                print(f"rendered +{made} previews (ready≈{n_ready + made})", flush=True)
                continue

            if len(rows) >= args.target_boxes:
                write_status(
                    out,
                    running=False,
                    message=f"done target={args.target_boxes}",
                    n_boxes=len(rows),
                    n_preview_ready=n_ready,
                    target=args.target_boxes,
                )
                print(f"DONE boxes={len(rows)} previews_ready={n_ready}")
                break

            # 2) Expand metadata (new boxes, preview_path empty → gallery shows 渲染中)
            existing = {r["stem"] for r in rows}
            new_rows = expand_batch(
                args.src,
                existing,
                want_boxes=args.batch_stems,
                seed=args.seed + round_i,
                series_cache=series_cache,
            )
            if not new_rows:
                write_status(
                    out,
                    running=False,
                    message="no more extractable boxes",
                    n_boxes=len(rows),
                    n_preview_ready=n_ready,
                    target=args.target_boxes,
                )
                print("DONE — positives exhausted")
                break
            have = {r["box_id"] for r in rows}
            for nr in new_rows:
                if nr["box_id"] in have:
                    continue
                nr["in_sample"] = 1 if len(rows) < 500 else 0
                nr["preview_path"] = ""
                nr["owner_side"] = ""
                nr["owner_note"] = ""
                # stringify for csv
                rows.append({k: nr.get(k, "") for k in SHEET_COLS})
                have.add(nr["box_id"])
            write_sheet_atomic(sheet_path, rows)
            write_items(out, rows)
            print(f"appended metadata → sheet={len(rows)}", flush=True)
            # loop continues → render those next
    except KeyboardInterrupt:
        write_status(out, running=False, message="interrupted")
        print("interrupted", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
