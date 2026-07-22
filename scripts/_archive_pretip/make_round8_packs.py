"""Round-8 packs: FRESH 2026 windows, non-overlapping by construction.

Round7's post-mortem found the existing render pool exhausted: 96% of its
images share bars with an already-labelled window (renders were cut at
stride 50 over a 200-bar window, so neighbours overlap 75%), and only ~3.5% of
all labels fall in 2026 -- the very regime the detector trades in. This
generator fixes both at the source:

  - windows END inside [2026-01-01, 2026-05-04): the under-labelled recent
    regime, stopping strictly before the holdout/accept window so the frozen
    ruler and forward validation stay untouched;
  - stride == WINDOW (200 bars): zero overlap within round8. Overlap with the
    old pool is avoided by the date range itself (the old pool is ~96%
    pre-2026); residual collision risk is a handful of images and only costs
    duplicate labelling effort, never label corruption (stems differ);
  - prelabels from models/owner_best.pt (v9_chain, recall-heavy) with the
    IoU>=0.30 duplicate-box suppression from round7's dedup pass;
  - eval symbols excluded via the manifest rule; tokenized equities excluded.

Renders land in datasets/dense_2026h1/images/train (kept in full as the next
labelling reservoir -- future rounds select from here without re-rendering).
NOTE for the next training pipeline: add datasets/dense_2026h1/images/train to
its SRC_DIRS or the new labels will be silently skipped at dataset build.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/make_round8_packs.py            # full run
  PYTHONPATH=. .venv/bin/python scripts/make_round8_packs.py --limit 30 # smoke
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.universe import is_stockish                     # noqa: E402
from src.detection.data import add_mas                        # noqa: E402
from src.detection.owner_eval import is_eval_symbol           # noqa: E402
from src.detection.render import render_chart                 # noqa: E402

ROUND = 8
WINDOW = 200
START = pd.Timestamp("2026-01-01", tz="UTC")
END = pd.Timestamp("2026-05-04", tz="UTC")   # exclusive; holdout starts here
N_TOTAL = 2000
N_CHUNKS = 4
UNCERTAIN_LO, UNCERTAIN_HI = 0.15, 0.45
PRELABEL_CONF = 0.20
IOU_DEDUP = 0.30
OUT_DIR = PROJECT / "datasets/dense_2026h1/images/train"


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    iw = max(0.0, min(ax2, bx2)-max(ax1, bx1)); ih = max(0.0, min(ay2, by2)-max(ay1, by1))
    inter = iw*ih
    u = a[2]*a[3] + b[2]*b[3] - inter
    return inter/u if u > 0 else 0.0


def render_symbol(csv_path: Path) -> list[str]:
    """Render every non-overlapping 2026-H1 window for one symbol; return stems."""
    m = re.match(r"okx_(.+)_15m_\d+\.csv$", csv_path.name)
    if not m:
        return []
    sym = m.group(1)
    if is_eval_symbol(sym) or is_stockish(sym):
        return []
    df = pd.read_csv(csv_path)
    if len(df) < WINDOW + 150:   # MA warmup headroom before the first window
        return []
    df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    enriched = add_mas(df)
    ts = enriched["open_time"]
    stems = []
    # earliest end-index with full window + warmup; walk forward in whole windows
    i = max(WINDOW + 130, int(ts.searchsorted(START)))
    while i < len(enriched):
        t = ts.iloc[i]
        if t >= END:
            break
        if t >= START:
            stem = f"{sym}_{i:06d}"
            out = OUT_DIR / f"{stem}.png"
            if not out.exists():
                sub = enriched.iloc[i-WINDOW+1: i+1].reset_index(drop=True)
                render_chart(sub, out_path=out)
            stems.append(stem)
        i += WINDOW   # stride == window: zero overlap
    return stems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="symbols to process (smoke)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260717)

    t0 = time.time()
    csvs = sorted(glob.glob(str(PROJECT / "data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv")))
    if args.limit:
        csvs = csvs[: args.limit]
    all_stems: list[str] = []
    for k, f in enumerate(csvs, 1):
        all_stems.extend(render_symbol(Path(f)))
        if k % 40 == 0:
            print(f"  渲染 {k}/{len(csvs)} 币种, 累计 {len(all_stems)} 窗, "
                  f"{(time.time()-t0)/60:.0f} 分钟", flush=True)
    print(f"渲染完成: {len(all_stems)} 个不重叠 2026H1 窗口 "
          f"({(time.time()-t0)/60:.0f} 分钟)", flush=True)
    if not all_stems:
        raise SystemExit("没有可用窗口 -- 检查 K 线覆盖")

    from ultralytics import YOLO
    model = YOLO(str(PROJECT / "models/owner_best.pt"))
    scored = []
    t1 = time.time()
    for k, stem in enumerate(all_stems, 1):
        r = model.predict(str(OUT_DIR / f"{stem}.png"), conf=0.10, verbose=False)[0]
        cand = []
        if r.boxes is not None and len(r.boxes):
            for b, c in zip(r.boxes.xywhn.cpu().numpy(), r.boxes.conf.cpu().numpy()):
                cand.append((float(c), tuple(map(float, b[:4]))))
        cand.sort(reverse=True)
        keep = []
        for c, box in cand:
            if c >= PRELABEL_CONF and not any(iou(box, kb) >= IOU_DEDUP for kb in keep):
                keep.append(box)
        top = cand[0][0] if cand else 0.0
        scored.append((stem, keep, top))
        if k % 1000 == 0:
            rate = k / (time.time() - t1)
            print(f"  打分 {k}/{len(all_stems)}  {rate:.1f} 张/秒", flush=True)

    unc_idx = {i for i, s in enumerate(scored) if UNCERTAIN_LO <= s[2] <= UNCERTAIN_HI}
    unc = [s for i, s in enumerate(scored) if i in unc_idx]
    rest = [s for i, s in enumerate(scored) if i not in unc_idx]
    rng.shuffle(unc); rng.shuffle(rest)
    n_unc = min(len(unc), N_TOTAL // 2)
    take = unc[:n_unc] + rest[: N_TOTAL - n_unc]
    rng.shuffle(take)
    print(f"不确定区: {len(unc)} 张,取 {n_unc};其余取 {len(take)-n_unc};合计 {len(take)}", flush=True)

    per = -(-len(take) // N_CHUNKS)
    for i in range(N_CHUNKS):
        chunk = take[i*per: (i+1)*per]
        if not chunk:
            continue
        tasks = [{
            "data": {"image": f"/data/local-files/?d=dense_2026h1/images/train/{stem}.png",
                     "stem": stem, "split": "train"},
            "predictions": [{
                "model_version": "owner_v9_chain_dedup",
                "result": [{
                    "type": "rectanglelabels", "from_name": "label", "to_name": "image",
                    "original_width": 1280, "original_height": 742,
                    "value": {"x": (cx-w/2)*100, "y": (cy-h/2)*100,
                              "width": w*100, "height": h*100,
                              "rectanglelabels": ["dense_cluster"]},
                } for cx, cy, w, h in boxes],
            }],
        } for stem, boxes, _ in chunk]
        out = PROJECT / f"output/label_studio/tasks_round{ROUND}_chunk{i+1}.json"
        out.write_text(json.dumps(tasks, ensure_ascii=False))
        n_pre = sum(1 for t in tasks if t["predictions"][0]["result"])
        print(f"  {out.name}: {len(tasks)} 任务 ({n_pre} 张有预标)", flush=True)

    print(f"ROUND8_DONE in {(time.time()-t0)/60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
