"""Build a review pack from v9's own high-confidence fires, to mine hard negatives.

v9 fires 137-274x more often than the owner labels, and no confidence threshold
fixes it: reaching the owner's density needs conf >= 0.50, where recall is zero.
The cause is in the training set. Its negative sampler skips any bar that passes
the dense test --

    ok, _ = passes(fast, full, close, tip, ...)
    if ok:
        continue                       # looks like the pattern, so not sampled

-- so every easy negative is a bar that plainly does not look like the setup, and
the model was never shown a "looks like it but is not". The only hard negatives it
saw were 319 owner-rejected tips from the v1b pack, against roughly 1600 trivial
ones. It learned "looks dense -> fire", which is exactly what the owner saw in
the charts.

The examples that would teach the distinction are not hypothetical: they are
v9's own confident fires on bars the owner would reject. This renders those into
the same pack format serve_short_tip_review.py already drives, so labelling is
one keypress per box and the labels write back atomically.

Sampling is deliberately NOT "the most confident N". Taking only the top of the
distribution would teach the model about its most extreme errors and leave the
bulk of its firing range untouched, and it would also make the review sample
unrepresentative of the density problem being fixed. Fires are drawn stratified
across confidence bands so the pack mirrors what v9 actually does in production.

Gold tips are excluded by symbol+bar proximity, so the owner is not asked to
re-judge boxes they already accepted.

Read-only against models and data; writes a new pack directory. Promotes nothing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_v9_hardneg_pack.py --n 300
  PYTHONPATH=. .venv/bin/python scripts/serve_short_tip_review.py \
      --pack analysis/output/v9_hardneg_pack --port 8771
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS, add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402
from scripts.build_htip_dataset import resolve_series  # noqa: E402
from scripts.build_star_tip_dataset_v9 import (  # noqa: E402
    archive_index, load_star_boxes, symbol_of,
)

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt"
OUT = PROJECT / "analysis" / "output" / "v9_hardneg_pack"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
# The builder splits train/val at VAL_CUT, so a pack mined from the bars just
# before holdout lands entirely in val -- which is what happened: all 276 owner
# reviewed rejections went to validation and the model trained on none of them.
# Hard negatives have to come from the train side to teach anything.
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")
FLOOR = 0.05
BANDS = ((0.05, 0.15), (0.15, 0.25), (0.25, 0.35), (0.35, 1.01))
GOLD_GUARD_BARS = 30          # a fire this close to a gold tip is not a new case
SEED = 20260728


def gold_tips(known: set[str]) -> dict[str, list[int]]:
    """Owner gold tip bar indices per symbol, so accepted boxes are not re-asked."""
    out: dict[str, list[int]] = {}
    arch = archive_index()
    for stem, boxes in load_star_boxes().items():
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
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed,
                              stored_img=stored)
        if r is None:
            continue
        _mo, ws, _mad = r
        sub = framed.iloc[ws:ws + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            continue
        _c, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub))
        if spans:
            out.setdefault(sym, []).append(ws + spans[0][1])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--n-symbols", type=int, default=40)
    ap.add_argument("--bars", type=int, default=1500, help="bars scanned per symbol")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--before", default=None,
                    help="only mine fires before this date (use VAL_CUT for train-side)")
    args = ap.parse_args()

    if not WEIGHTS.exists():
        print(f"权重缺失: {WEIGHTS}")
        return 2
    for sub in ("images/train", "labels/train", "previews"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    model = load_yolo_model(str(WEIGHTS))
    series = list_series(bar="15m")
    known = {s for (_x, s) in series}
    print("① 定位 owner 金标(避免让你重判已认可的框)…", flush=True)
    gold = gold_tips(known)
    print(f"   {sum(len(v) for v in gold.values())} 个金标 tip,覆盖 {len(gold)} 币\n")

    syms = sorted({s for (_x, s) in series
                   if s.endswith("_USDT_SWAP") and not is_stockish(s)})
    rng = random.Random(SEED)
    rng.shuffle(syms)
    syms = syms[: args.n_symbols]

    print(f"② 扫 {len(syms)} 币 x {args.bars} bar,收集 v9 的开火…", flush=True)
    tmp = OUT / "_scan.png"
    fires: list[dict] = []
    for k, sym in enumerate(syms, 1):
        try:
            fr = add_mas(load_series(series[("okx", sym)]))
        except Exception:  # noqa: BLE001
            continue
        t_all = pd.to_datetime(fr["open_time"], utc=True)
        cutoff = pd.Timestamp(args.before, tz="UTC") if args.before else HOLDOUT
        fr = fr[t_all < min(cutoff, HOLDOUT)].reset_index(drop=True)   # iron rule 1
        if len(fr) < WINDOW + 50:
            continue
        times = pd.to_datetime(fr["open_time"], utc=True)
        # Scanning the last N bars before the cutoff puts every negative inside one
        # 10-day window, and the model then learns those ten days rather than the
        # general "looks like it but is not". Each symbol starts at its own random
        # offset so the pack spans the whole train period at the same GPU cost.
        span = len(fr) - WINDOW - args.bars
        base = WINDOW + (rng.randrange(span) if span > 0 else 0)
        lo, hi_bar = base, min(len(fr), base + args.bars)
        g = gold.get(sym, [])
        last = -10 ** 9
        n_sym = 0
        for t in range(lo, hi_bar):
            try:
                _, tform = render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=tmp)
                res = model.predict([str(tmp)], conf=FLOOR, verbose=False,
                                    device=args.device)[0]
            except Exception:  # noqa: BLE001
                continue
            b = res.boxes
            if b is None or len(b) == 0:
                continue
            best_cf, best_box = 0.0, None
            for row, cf in zip(b.xywhn.cpu().numpy(), b.conf.cpu().numpy()):
                if right_edge_to_bar(float(row[0]), float(row[2]), tform,
                                     n_bars=WINDOW) >= WINDOW - TIP_EDGE_BARS:
                    if float(cf) > best_cf:
                        best_cf, best_box = float(cf), row.tolist()
            if best_box is None or t - last < MIN_GAP_BARS:
                continue
            if any(abs(t - gt) <= GOLD_GUARD_BARS for gt in g):
                continue                       # already an accepted box
            last = t
            n_sym += 1
            fires.append({"symbol": sym, "tip": t, "conf": best_cf,
                          "box": best_box, "tip_time": str(times.iloc[t])})
        print(f"   [{k}/{len(syms)}] {sym:<20} 开火 {n_sym}", flush=True)
    tmp.unlink(missing_ok=True)
    print(f"\n   共 {len(fires)} 个开火(已排除金标附近)")

    # stratified across confidence, so the pack mirrors production firing rather
    # than only the model's most extreme errors
    per_band = max(1, args.n // len(BANDS))
    picked: list[dict] = []
    for lo_c, hi_c in BANDS:
        band = [f for f in fires if lo_c <= f["conf"] < hi_c]
        rng.shuffle(band)
        picked.extend(band[:per_band])
        print(f"   conf [{lo_c:.2f},{hi_c:.2f}) 有 {len(band)} 个,取 "
              f"{min(len(band), per_band)}")
    rng.shuffle(picked)
    picked = picked[: args.n]

    print(f"\n③ 渲染 {len(picked)} 张审阅图…", flush=True)
    rows = []
    frames: dict[str, pd.DataFrame] = {}
    for i, f in enumerate(picked):
        sym = f["symbol"]
        if sym not in frames:
            fr = add_mas(load_series(series[("okx", sym)]))
            t_all = pd.to_datetime(fr["open_time"], utc=True)
            frames[sym] = fr[t_all < HOLDOUT].reset_index(drop=True)
        fr = frames[sym]
        t = f["tip"]
        stem = f"{sym}_{t}"
        img = OUT / "images" / "train" / f"{stem}.png"
        try:
            render_chart(fr.iloc[t - WINDOW + 1:t + 1], out_path=img)
        except Exception:  # noqa: BLE001
            continue
        xc, yc, w, h = f["box"][:4]
        (OUT / "labels" / "train" / f"{stem}.txt").write_text(
            f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
        rows.append({"i": len(rows), "stem": stem, "symbol": sym,
                     "tip_idx": t, "tip_time": f["tip_time"],
                     "max_conf": round(f["conf"], 4), "n_boxes": 1,
                     "tip_spread": "", "right_p50": "",
                     "owner_keep": "", "owner_note": "",
                     "image": f"images/train/{stem}.png"})

    with (OUT / "review_sheet.csv").open("w", newline="", encoding="utf-8") as fh:
        w_ = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w_.writeheader()
        w_.writerows(rows)
    (OUT / "manifest.json").write_text(json.dumps(
        {"weights": WEIGHTS.name, "n_rows": len(rows), "floor": FLOOR,
         "bands": [list(b) for b in BANDS], "seed": SEED,
         "n_symbols": len(syms), "bars_per_symbol": args.bars,
         "gold_guard_bars": GOLD_GUARD_BARS,
         "purpose": "hard negatives: v9's own confident fires, for retraining"},
        indent=2, ensure_ascii=False) + "\n")

    print(f"\n审阅包已生成:{OUT}")
    print(f"  {len(rows)} 张   conf 中位 "
          f"{np.median([r['max_conf'] for r in rows]):.3f}")
    print(f"\n开始审阅:")
    print(f"  PYTHONPATH=. .venv/bin/python scripts/serve_short_tip_review.py "
          f"--pack {OUT.relative_to(PROJECT)} --port 8771")
    print(f"  然后打开 http://127.0.0.1:8771/   K=是形态  D=不是  S=跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
