"""Is v8's recall collapse a confidence-threshold artefact, or did it learn narrow?

v8 fixed the box width problem -- ratio 2.00 -> 1.22, IoU median 0.261 -> 0.544 --
and its recall on the owner's gold fell from v6's 51.5% to 14.1%, while its own
val hit an all-time best (mAP50-95 0.916). Those two numbers point opposite ways,
so before assuming anything, separate the two explanations that fit:

  THRESHOLD  the model still finds the pattern but is less confident about it,
             so boxes fall under the scan floor. Recall returns as conf drops.
  NARROW     a constant 10-bar target taught it to accept only that exact shape,
             so the owner's 9-13 bar spread mostly misses. Recall stays flat no
             matter how low the floor goes.

The fix differs completely: a threshold problem is one number, a narrowness
problem needs the training width to carry a distribution.

Sweeping conf costs one pass and no retraining. v6 is swept alongside on the same
boxes so the comparison is like-for-like rather than against a remembered number.

Read-only. No promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v8_recall_vs_conf.py --limit 260
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402
from scripts.build_htip_dataset import resolve_series  # noqa: E402
from scripts.build_star_tip_dataset_v8 import (  # noqa: E402
    archive_index, load_star_boxes, star_side, symbol_of,
)

MODELS = {
    "v6": PROJECT / "runs/detect/runs/detect/owner_short_star_v6/weights/best.pt",
    "v8": PROJECT / "runs/detect/runs/detect/owner_short_star_v8/weights/best.pt",
}
FLOOR = 0.01                       # scan far below any usable threshold
GRID = (0.01, 0.03, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=260)
    args = ap.parse_args()

    known = {s for (_x, s) in list_series(bar="15m")}
    arch = archive_index()
    stars = load_star_boxes()
    models = {k: load_yolo_model(str(v)) for k, v in MODELS.items() if v.exists()}
    print(f"模型: {list(models)}")
    tmp = PROJECT / "data" / "_rc.png"

    # collect the short-side gold tips once, then score every model on them
    tips: list[tuple[str, int, int]] = []          # (symbol, tip, owner_width)
    for stem, boxes in list(stars.items())[: args.limit]:
        sym = symbol_of(stem, known)
        if sym is None:
            continue
        base = resolve_series(sym)
        if base is None:
            continue
        framed = add_mas(base)
        m = re.search(r"_(\d+)$", stem)
        if not m:
            continue
        stored = cv2.imread(str(arch[stem])) if stem in arch else None
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed, stored_img=stored)
        if r is None:
            continue
        _mo, ws, _mad = r
        sub_old = framed.iloc[ws:ws + WINDOW].reset_index(drop=True)
        if len(sub_old) != WINDOW:
            continue
        _c, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub_old))
        if not spans:
            continue
        b0, b1, _ph, _pl = spans[0]
        tip = ws + b1
        if tip < WINDOW or tip >= len(framed):
            continue
        ma = np.vstack([framed[c].to_numpy(dtype=float)
                        for c in ALL_MA_COLS if c in framed.columns])
        atrp = add_indicators(framed)["atr_pct"].to_numpy(dtype=float)
        side, _b = star_side(framed["close"].to_numpy(dtype=float),
                             np.nanmin(ma, axis=0), np.nanmax(ma, axis=0),
                             atrp, tip, len(framed))
        if side >= 0:
            continue
        tips.append((sym, tip, b1 - b0 + 1))
    print(f"空头金标 tip: {len(tips)}\n")

    frames: dict[str, pd.DataFrame] = {}
    rows = []
    for sym, tip, ow in tips:
        if sym not in frames:
            base = resolve_series(sym)
            frames[sym] = add_mas(base) if base is not None else None
        fr = frames[sym]
        if fr is None or tip >= len(fr):
            continue
        try:
            _, tf = render_chart(fr.iloc[tip - WINDOW + 1:tip + 1], out_path=tmp)
        except Exception:  # noqa: BLE001
            continue
        rec = {"owner_width": ow}
        for name, mdl in models.items():
            try:
                res = mdl.predict([str(tmp)], conf=FLOOR, verbose=False, device="cpu")[0]
            except Exception:  # noqa: BLE001
                continue
            best = 0.0
            b = res.boxes
            if b is not None and len(b) > 0:
                for row, cf in zip(b.xywhn.cpu().numpy(), b.conf.cpu().numpy()):
                    bar = right_edge_to_bar(float(row[0]) + float(row[2]) / 2, 0.0,
                                            tf, n_bars=WINDOW)
                    if (WINDOW - 1) - bar <= TIP_EDGE_BARS:
                        best = max(best, float(cf))
            rec[name] = best
        rows.append(rec)
    tmp.unlink(missing_ok=True)

    d = pd.DataFrame(rows)
    print(f"{'conf 门槛':>10}" + "".join(f"{k:>12}" for k in models))
    out = {k: {} for k in models}
    for c in GRID:
        line = f"{c:>10.2f}"
        for k in models:
            rate = float((d[k] >= c).mean()) if k in d else float("nan")
            out[k][str(c)] = round(rate, 4)
            line += f"{rate*100:>11.1f}%"
        print(line)

    print(f"\n=== 各模型在金标上的置信度分布 ===")
    for k in models:
        if k not in d:
            continue
        s = d[k]
        print(f"  {k}: 完全不开火(=0) {100*(s<=0).mean():>5.1f}%   "
              f"开火者 p50={s[s>0].median():.3f} p90={s[s>0].quantile(.9):.3f}")

    if "v8" in d:
        floor_rate = float((d["v8"] >= FLOOR).mean())
        v6_rate = float((d["v6"] >= 0.05).mean()) if "v6" in d else np.nan
        verdict = ("THRESHOLD:把门槛降到 %.2f 后 v8 召回 %.1f%%,追上/超过 v6 的 %.1f%% "
                   "→ 只是变保守,调阈值即可" % (FLOOR, floor_rate * 100, v6_rate * 100)
                   if floor_rate >= v6_rate else
                   "NARROW:门槛降到 %.2f 召回仍只有 %.1f%%(v6 在 0.05 时 %.1f%%) "
                   "→ 模型真的学窄了,训练宽度需要带分布" % (FLOOR, floor_rate * 100, v6_rate * 100))
        print(f"\n判读: {verdict}")
    else:
        verdict = "v8 权重缺失"

    (PROJECT / "analysis" / "output" / "diag_v8_recall_vs_conf.json").write_text(
        json.dumps({"n_tips": len(d), "recall_by_conf": out,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
