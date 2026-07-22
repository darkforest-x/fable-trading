#!/usr/bin/env python3
"""Turn collected real-tip images into an owner review gallery + LS pack.

Reads data/real_tip_collect/manifest.csv (grown by collect_real_tips_pulse.py
on the VPS, pulled to Mac). Builds:
  - a filterable index.html (dense candidates first) for eyeballing;
  - review_sheet.csv (owner fills owner_class: launch / hardneg / empty);
  - a Label Studio tasks json for drawing boxes on confirmed launches.

Non-dense images need no box (auto-negatives); only rule-dense candidates go
to the review queue. This is the v17 pipeline's front door.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_real_tip_review_pack.py --limit 300
"""
from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
COLLECT = PROJECT / "data" / "real_tip_collect"
MANIFEST = COLLECT / "manifest.csv"
OUT = PROJECT / "analysis" / "output" / "real_tip_review"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=300, help="dense candidates in the pack")
    ap.add_argument("--empties", type=int, default=100, help="empty negatives shown for spot-check")
    args = ap.parse_args()
    if not MANIFEST.exists():
        raise SystemExit(f"no manifest at {MANIFEST} — run collect_real_tips_pulse.py (VPS) first")

    rows = list(csv.DictReader(MANIFEST.open()))
    dense = [r for r in rows if str(r.get("tip_dense")).lower() == "true"]
    empty = [r for r in rows if str(r.get("tip_dense")).lower() != "true"]
    dense = dense[-args.limit:]
    empty = empty[-args.empties:]
    OUT.mkdir(parents=True, exist_ok=True)

    sheet = OUT / "review_sheet.csv"
    with sheet.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=(
            "symbol", "tip_time", "tip_dense", "mean_full_spread",
            "owner_class", "owner_note", "png"))
        w.writeheader()
        for r in dense + empty:
            w.writerow({k: r.get(k, "") for k in (
                "symbol", "tip_time", "tip_dense", "mean_full_spread", "png")}
                | {"owner_class": "", "owner_note": ""})

    tasks = [{"data": {"image": f"/data/local-files/?d={r['png']}",
                       "symbol": r["symbol"], "tip_time": r["tip_time"]}}
             for r in dense]
    (OUT / "ls_tasks_real_tip.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2))

    parts = ["<html><head><meta charset='utf-8'><title>real-tip review</title>",
             "<style>body{font-family:sans-serif;background:#111;color:#eee}",
             "img{max-width:100%;border:1px solid #444}.c{margin:14px 0;border-bottom:1px solid #333;padding-bottom:10px}",
             "h3{color:#7dd3fc;margin:4px 0}.dense{color:#4ade80}.empty{color:#94a3b8}</style></head><body>",
             f"<h1>真实盘口 tip 审阅 · 密集候选 {len(dense)} · 空背景抽检 {len(empty)}</h1>",
             "<p>密集候选:判断是否真启动(launch)/假密集(hardneg);空背景=免审负样本,仅抽检误分类。填 review_sheet.csv。</p>"]
    for r in dense + empty:
        d = str(r.get("tip_dense")).lower() == "true"
        cls = "dense" if d else "empty"
        tag = "密集候选(待审)" if d else "空背景(免审负样本)"
        parts.append(
            f"<div class='c'><h3 class='{cls}'>{tag}</h3>"
            f"<div>{html.escape(r['symbol'])} · {html.escape(r['tip_time'])} · "
            f"spread={html.escape(str(r.get('mean_full_spread','')))}</div>"
            f"<img src='{PROJECT}/{html.escape(r['png'])}'></div>")
    parts.append("</body></html>")
    (OUT / "index.html").write_text("\n".join(parts), encoding="utf-8")

    print(json.dumps({
        "dense_total": len([r for r in rows if str(r.get('tip_dense')).lower() == 'true']),
        "empty_total": len(empty and [r for r in rows if str(r.get('tip_dense')).lower() != 'true'] or []),
        "pack_dense": len(dense), "pack_empty": len(empty),
        "gallery": str(OUT / "index.html"), "sheet": str(sheet),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
