#!/usr/bin/env python3
"""H-TOOL-2: hardneg preview gallery with box overlays (matplotlib, CPU).

Uses existing hardneg preview PNGs + candidate geometry. supervision is
optional — if not installed we stay on matplotlib/cv2 to avoid polluting the
training .venv while v13 occupies MPS.

Outputs:
  analysis/output/hardneg_overlay_gallery/{annotated/*.png, index.html, manifest.json}

Usage:
  PYTHONPATH=. .venv/bin/python scripts/overlay_hardneg_boxes.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY = PROJECT / "analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_summary.json"
DEFAULT_OUT = PROJECT / "analysis/output/hardneg_overlay_gallery"


def try_supervision():
    try:
        import supervision as sv  # noqa: F401

        return True
    except ImportError:
        return False


def annotate_matplotlib(img_bgr, row: dict, out_path: Path) -> None:
    img = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    x1 = (float(row["cx"]) - float(row["w"]) / 2) * w
    y1 = (float(row["cy"]) - float(row["h"]) / 2) * h
    bw = float(row["w"]) * w
    bh = float(row["h"]) * h
    tip_x = 0.95 * w

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=120)
    ax.imshow(img)
    ax.add_patch(
        Rectangle(
            (x1, y1),
            bw,
            bh,
            fill=False,
            edgecolor="#22d3ee",
            linewidth=2.0,
            label="hardneg GT",
        )
    )
    ax.axvline(tip_x, color="#eab308", linestyle="--", linewidth=1.2, label="tip x=0.95")
    # aftermath shade from box right → tip
    ax.axvspan(x1 + bw, tip_x, color="#ec4899", alpha=0.12, label="aftermath")
    ax.set_title(
        f"{row.get('stem')}  right={float(row['right']):.3f}  "
        f"after≈{float(row.get('bars_after', 0)):.0f}  h={float(row['h']):.3f}",
        fontsize=10,
    )
    ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax.set_axis_off()
    fig.tight_layout(pad=0.2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def annotate_supervision(img_bgr, row: dict, out_path: Path) -> None:
    import numpy as np
    import supervision as sv

    h, w = img_bgr.shape[:2]
    x1 = (float(row["cx"]) - float(row["w"]) / 2) * w
    y1 = (float(row["cy"]) - float(row["h"]) / 2) * h
    x2 = x1 + float(row["w"]) * w
    y2 = y1 + float(row["h"]) * h
    xyxy = np.array([[x1, y1, x2, y2]], dtype=float)
    detections = sv.Detections(xyxy=xyxy)
    box_annotator = sv.BoxAnnotator(thickness=2, color=sv.Color.from_hex("#22d3ee"))
    label_annotator = sv.LabelAnnotator(text_thickness=1, text_scale=0.5)
    annotated = box_annotator.annotate(scene=img_bgr.copy(), detections=detections)
    annotated = label_annotator.annotate(
        scene=annotated,
        detections=detections,
        labels=[f"hardneg r={float(row['right']):.2f}"],
    )
    tip_x = int(0.95 * w)
    cv2.line(annotated, (tip_x, 0), (tip_x, h - 1), (0, 200, 255), 1, cv2.LINE_AA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), annotated)


def write_index(out_dir: Path, items: list[dict], backend: str) -> None:
    cards = []
    for it in items:
        cards.append(
            f"""<div class="card">
  <h2>{it['stem']}</h2>
  <img src="{it['annotated_rel']}" alt="{it['stem']}"/>
  <p>right={it['right']:.3f} · after≈{it['bars_after']:.0f} · backend={backend}</p>
</div>"""
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>hardneg overlay gallery</title>
<style>
body{{font-family:ui-sans-serif,system-ui;margin:16px;background:#f8fafc}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:10px}}
img{{width:100%;height:auto}}
h1{{font-size:18px}} h2{{font-size:13px;margin:0 0 8px}}
.note{{font-size:13px;color:#444;max-width:900px}}
</style></head><body>
<h1>H-TOOL-2 hardneg 叠框画廊（CPU）</h1>
<p class="note">青框=硬负 GT；黄虚线=tip x=0.95；粉阴影=后文（matplotlib 后端）。
不进脉冲、不抢 MPS。backend={backend}</p>
<div class="grid">
{''.join(cards)}
</div>
</body></html>
"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--prefer-supervision", action="store_true")
    args = ap.parse_args()

    summary = json.loads(args.summary.read_text())
    previews = summary.get("previews") or []
    if not previews:
        raise SystemExit("no previews in summary — run build_hardneg_mid_cluster_inventory.py first")

    use_sv = args.prefer_supervision and try_supervision()
    backend = "supervision" if use_sv else "matplotlib"
    ann_dir = args.out_dir / "annotated"
    ann_dir.mkdir(parents=True, exist_ok=True)

    # need full geometry — reload candidates
    import csv

    cand_path = PROJECT / summary["csv"]
    by_stem = {}
    with cand_path.open() as f:
        for r in csv.DictReader(f):
            by_stem.setdefault(r["stem"], r)

    items = []
    for p in previews:
        stem = p["stem"]
        row = {**by_stem.get(stem, {}), **p}
        src = PROJECT / p["preview"]
        # prefer original dataset image if present for cleaner overlay
        img_rel = row.get("image_rel")
        src_ds = None
        if img_rel:
            src_ds = PROJECT / "datasets/dense_owner_v11" / img_rel
        img_path = src_ds if src_ds and src_ds.is_file() else src
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"skip missing {img_path}")
            continue
        out_png = ann_dir / f"{stem}_overlay.png"
        if use_sv:
            annotate_supervision(img, row, out_png)
        else:
            annotate_matplotlib(img, row, out_png)
        items.append(
            {
                "stem": stem,
                "right": float(row["right"]),
                "bars_after": float(row.get("bars_after") or 0),
                "annotated_rel": f"annotated/{out_png.name}",
                "source": str(img_path.relative_to(PROJECT)),
            }
        )

    write_index(args.out_dir, items, backend)
    manifest = {
        "n": len(items),
        "backend": backend,
        "supervision_available": try_supervision(),
        "index": str((args.out_dir / "index.html").relative_to(PROJECT)),
        "items": items,
        "gpu_used": False,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"backend={backend} n={len(items)} -> {args.out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
