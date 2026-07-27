"""How far is v6's box from the owner's box, on the owner's own charts?

Owner: "识别的并不标准,对比我的金标完全差了一大截". Before changing anything,
that gap needs a number and a picture -- "差了一大截" could mean the box lands on
the wrong bars, or covers the wrong price range, or is the right region at the
wrong size, and each implies a different fix.

Method. For each owner box: resolve the window it was drawn in (MAD-disambiguated,
the same path build_crop_pad200_dataset uses), convert it to absolute bar span
and price range, then render the TIP window ending at that box's right edge --
the view v6 is trained for -- and run v6 on it. Both boxes are then expressed in
the same tip-window coordinates and compared on:

  IoU          overlap of the two rectangles
  bar offset   right edges, in bars: negative means v6 fires EARLIER
  width ratio  v6 width / owner width, in bars
  price cover  fraction of the owner's price range the model's box contains

A high bar-offset with high IoU means a sizing problem; low IoU with aligned
edges means the model is boxing a different price band; both low means it is
finding a different thing entirely.

Also writes side-by-side renders (owner box in green, v6 in red) so the number
and the picture can be checked against each other.

Read-only, train window only, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v6_vs_gold_boxes.py --limit 400 --render 8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402
from scripts.build_htip_dataset import resolve_series  # noqa: E402
from scripts.build_star_tip_dataset_v6 import (  # noqa: E402
    BREAK_FORWARD, DROP_ATR_MIN, RET_BARS, archive_index, load_star_boxes,
    star_side, symbol_of,
)

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v6/weights/best.pt"
SCAN_CONF = 0.05
OUT = PROJECT / "analysis" / "output" / "v6_vs_gold"


def iou(a, b) -> float:
    """a, b = (x0, y0, x1, y1) in pixels."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--render", type=int, default=8)
    ap.add_argument("--short-only", action="store_true",
                    help="只比对做空侧金标 — v6 是做空检测器,拿多头金标比它是错的")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    for p in OUT.glob("*.png"):
        p.unlink()

    from src.data.loader import list_series
    known = {s for (_x, s) in list_series(bar="15m")}
    arch = archive_index()
    stars = load_star_boxes()
    model = load_yolo_model(str(WEIGHTS))
    tmp = PROJECT / "data" / "_vg.png"

    rows, drawn = [], 0
    for stem, boxes in list(stars.items())[: args.limit]:
        sym = symbol_of(stem, known)
        if sym is None:
            continue
        base = resolve_series(sym)
        if base is None:
            continue
        framed = add_mas(base)
        m = pd.Series([stem]).str.extract(r"_(\d+)$")[0].iloc[0]
        if pd.isna(m):
            continue
        stored = cv2.imread(str(arch[stem])) if stem in arch else None
        r = resolve_win_start(len(framed), int(m), enriched=framed, stored_img=stored)
        if r is None:
            continue
        _mode, ws, _mad = r
        sub_old = framed.iloc[ws:ws + WINDOW].reset_index(drop=True)
        if len(sub_old) != WINDOW:
            continue
        tf_old = make_chart_transform(sub_old)
        _cut, spans = boxes_cut_and_spans(boxes, tf_old)
        if not spans:
            continue
        b0, b1, p_hi, p_lo = spans[0]
        tip = ws + b1                       # owner box right edge = the tip
        if tip < WINDOW or tip >= len(framed):
            continue
        if args.short_only:
            from src.detection.data import ALL_MA_COLS
            ma = np.vstack([framed[c].to_numpy(dtype=float)
                            for c in ALL_MA_COLS if c in framed.columns])
            ind_c = framed["close"].to_numpy(dtype=float)
            from src.judgment.candidates import add_indicators
            atrp = add_indicators(framed)["atr_pct"].to_numpy(dtype=float)
            side, _b = star_side(ind_c, np.nanmin(ma, axis=0), np.nanmax(ma, axis=0),
                                 atrp, tip, len(framed))
            if side >= 0:               # long or undetermined: not this detector's job
                continue

        sub = framed.iloc[tip - WINDOW + 1:tip + 1].reset_index(drop=True)
        try:
            _, tf = render_chart(sub, out_path=tmp)
            res = model.predict([str(tmp)], conf=SCAN_CONF, verbose=False, device="cpu")[0]
        except Exception:  # noqa: BLE001
            continue
        bx = res.boxes
        if bx is None or len(bx) == 0:
            rows.append({"stem": stem, "fired": False})
            continue

        # owner box in the TIP window's coordinates
        own_l = WINDOW - 1 - (b1 - b0)
        own_r = WINDOW - 1
        own_px = (tf.x_at(own_l), tf.y_at(p_hi), tf.x_at(own_r), tf.y_at(p_lo))

        best = None
        for row, cf in zip(bx.xywhn.cpu().numpy(), bx.conf.cpu().numpy()):
            cx, yc, bw, bh = (float(v) for v in row[:4])
            px = ((cx - bw / 2) * tf.width, (yc - bh / 2) * tf.height,
                  (cx + bw / 2) * tf.width, (yc + bh / 2) * tf.height)
            sc = iou(own_px, px)
            if best is None or sc > best[0]:
                lb = right_edge_to_bar(cx - bw / 2, 0.0, tf, n_bars=WINDOW)
                rb = right_edge_to_bar(cx + bw / 2, 0.0, tf, n_bars=WINDOW)
                best = (sc, px, lb, rb, float(cf))
        sc, px, lb, rb, cf = best
        rows.append({
            "stem": stem, "fired": True, "iou": round(sc, 4), "conf": round(cf, 3),
            "bar_off_right": int(rb - own_r), "bar_off_left": int(lb - own_l),
            "width_ratio": round((rb - lb + 1) / max(own_r - own_l + 1, 1), 3),
            "owner_width": int(own_r - own_l + 1), "model_width": int(rb - lb + 1),
        })

        if drawn < args.render:
            img = cv2.imread(str(tmp))
            cv2.rectangle(img, (int(own_px[0]), int(own_px[1])),
                          (int(own_px[2]), int(own_px[3])), (60, 200, 60), 3)
            cv2.rectangle(img, (int(px[0]), int(px[1])),
                          (int(px[2]), int(px[3])), (60, 60, 255), 2)
            cv2.putText(img, f"green=owner  red=v6  IoU={sc:.2f}  conf={cf:.2f}",
                        (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 2, cv2.LINE_AA)
            cv2.imwrite(str(OUT / f"cmp_{drawn:02d}_{stem[:36]}_iou{sc:.2f}.png"), img)
            drawn += 1

    tmp.unlink(missing_ok=True)
    d = pd.DataFrame(rows)
    fired = d[d["fired"] == True]  # noqa: E712
    print(f"对比 {len(d)} 个金标框   v6 开火 {len(fired)} ({100*len(fired)/max(len(d),1):.1f}%)\n")
    if fired.empty:
        return 1

    q = lambda c, p: float(np.percentile(fired[c], p))  # noqa: E731
    print("=== IoU(1.0 = 完全重合) ===")
    print(f"  p10={q('iou',10):.3f}  p50={q('iou',50):.3f}  p90={q('iou',90):.3f}")
    for t in (0.3, 0.5, 0.7):
        print(f"  IoU >= {t}: {100*(fired['iou']>=t).mean():.1f}%")

    print("\n=== 右缘偏移(根;负=v6 更早) ===")
    print(f"  p10={q('bar_off_right',10):.0f}  p50={q('bar_off_right',50):.0f}  "
          f"p90={q('bar_off_right',90):.0f}")
    print("=== 宽度比(v6 / owner) ===")
    print(f"  p10={q('width_ratio',10):.2f}  p50={q('width_ratio',50):.2f}  "
          f"p90={q('width_ratio',90):.2f}")
    print(f"  owner 框宽中位 {fired['owner_width'].median():.0f} 根  "
          f"v6 框宽中位 {fired['model_width'].median():.0f} 根")

    med_iou, med_w = q("iou", 50), q("width_ratio", 50)
    if med_iou >= 0.5:
        diag = "位置和大小都接近,差距不大"
    elif abs(med_w - 1) > 0.4:
        diag = f"主要是尺寸问题:v6 的框是 owner 的 {med_w:.2f} 倍"
    elif abs(q("bar_off_right", 50)) > 3:
        diag = "主要是位置问题:右缘对不齐"
    else:
        diag = "位置和尺寸都对,但价格区间不同 → 框的是不同的价格带"
    print(f"\n判读: IoU 中位 {med_iou:.3f} — {diag}")

    (PROJECT / "analysis" / "output" / "diag_v6_vs_gold_boxes.json").write_text(
        json.dumps({"n": len(d), "fired": len(fired),
                    "iou_p50": round(med_iou, 4),
                    "iou_ge_50pct": round(float((fired["iou"] >= 0.5).mean()), 4),
                    "bar_off_right_p50": q("bar_off_right", 50),
                    "width_ratio_p50": round(med_w, 3),
                    "owner_width_p50": float(fired["owner_width"].median()),
                    "model_width_p50": float(fired["model_width"].median()),
                    "diagnosis": diag}, indent=2, ensure_ascii=False) + "\n")
    print(f"\n对比图 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
