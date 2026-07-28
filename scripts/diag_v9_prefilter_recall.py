"""Which cheap prefilter keeps v9's fires? Measure before spending 2 GPU-hours.

Rebuilding the judgment pool on v9 means running the detector over history. Doing
it on every bar is ~2 hours of GPU; the v16 dump avoided that with a mechanical
"dense" prefilter (fast<=0.0028 & full<=0.0055, run>=5) that cut the bar universe
~15x. Reusing that gate for v9 would be a mistake worth checking rather than
assuming: only 31.0% of the owner's own accepted boxes qualify as dense by the
mechanical definition, and v9 was trained on the owner's stars, not on the rule.

So this measures, on the same bars, for each candidate prefilter:

  KEEP   share of all bars that survive it  -> how much GPU time it saves
  RECALL share of v9's every-bar fires it retains -> what it costs in candidates

A prefilter is only usable if recall is high; saving an hour by dropping a third
of the candidate pool would silently change what the judgment layer is trained on
and make the whole rebuild uninterpretable.

Candidates measured:
  v16_dense   the existing gate (fast<=0.0028, full<=0.0055, run>=5)
  v9_dense    v9's own build thresholds (fast<=0.0045, full<=0.0088, run>=5)
  break       close below the MA bundle with an >=1 ATR 8-bar fall -- what v9 was
              actually anchored on (star_side's short condition)
  break_loose the same break with no ATR-size requirement
  none        every bar (the ground truth this scores against)

Read-only, train side only (<2026-05-04), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v9_prefilter_recall.py --n-symbols 4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v9/weights/best.pt"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
V16_FAST, V16_FULL = 0.0028, 0.0055
V9_FAST, V9_FULL = 0.0045, 0.0088       # build_star_tip_dataset_v9 thresholds
MIN_DENSE = 5
RET_BARS, DROP_ATR_MIN = 8, 1.0         # star_side's short condition
BATCH = 32


def run_mask(dense: np.ndarray, min_run: int) -> np.ndarray:
    """Bars ending a contiguous run of >=min_run dense bars."""
    out = np.zeros(len(dense), dtype=bool)
    run = 0
    for i, d in enumerate(dense):
        run = run + 1 if d else 0
        out[i] = run >= min_run
    return out


def masks_for(fr: pd.DataFrame) -> dict[str, np.ndarray]:
    fast = pd.to_numeric(fr["fast_spread"], errors="coerce").to_numpy(dtype=float)
    full = pd.to_numeric(fr["full_spread"], errors="coerce").to_numpy(dtype=float)
    ind = add_indicators(fr)
    close = fr["close"].to_numpy(dtype=float)
    atrp = ind["atr_pct"].to_numpy(dtype=float)
    ma = np.vstack([fr[c].to_numpy(dtype=float) for c in ALL_MA_COLS if c in fr.columns])
    ma_min = np.nanmin(ma, axis=0)

    below = np.zeros(len(fr), dtype=bool)
    hard = np.zeros(len(fr), dtype=bool)
    for j in range(RET_BARS, len(fr)):
        if not np.isfinite(ma_min[j]) or close[j] >= ma_min[j]:
            continue
        below[j] = True
        if np.isfinite(atrp[j]) and atrp[j] > 0:
            if (close[j] / close[j - RET_BARS] - 1) / atrp[j] < -DROP_ATR_MIN:
                hard[j] = True
    return {
        "v16_dense": run_mask((fast <= V16_FAST) & (full <= V16_FULL), MIN_DENSE),
        "v9_dense": run_mask((fast <= V9_FAST) & (full <= V9_FULL), MIN_DENSE),
        "break": hard,
        "break_loose": below,
        "v9_dense_or_break": run_mask((fast <= V9_FAST) & (full <= V9_FULL), MIN_DENSE) | hard,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-symbols", type=int, default=4)
    ap.add_argument("--max-bars", type=int, default=3000,
                    help="bars per symbol to scan on every bar (the ground truth)")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    if not WEIGHTS.exists():
        print(f"权重缺失: {WEIGHTS}")
        return 2
    model = load_yolo_model(str(WEIGHTS))
    series = list_series(bar="15m")
    syms = sorted({s for (_x, s) in series if s.endswith("_USDT_SWAP")})[: args.n_symbols]

    names = ["v16_dense", "v9_dense", "break", "break_loose", "v9_dense_or_break"]
    n_bars_total = 0
    keep_tot = {k: 0 for k in names}
    fires_tot = 0
    hit_tot = {k: 0 for k in names}
    tmp_dir = PROJECT / "data" / "_pf"
    tmp_dir.mkdir(exist_ok=True)
    t0 = time.perf_counter()

    for sym in syms:
        fr = add_mas(load_series(series[("okx", sym)]))
        t = pd.to_datetime(fr["open_time"], utc=True)
        fr = fr[t < HOLDOUT_START].reset_index(drop=True)     # holdout untouched
        if len(fr) < WINDOW + 200:
            continue
        hi = len(fr)
        lo = max(WINDOW, hi - args.max_bars)
        m = masks_for(fr)
        idx = list(range(lo, hi))
        n_bars_total += len(idx)
        for k in names:
            keep_tot[k] += int(m[k][lo:hi].sum())

        fires: list[int] = []
        for s in range(0, len(idx), BATCH):
            chunk = idx[s:s + BATCH]
            paths, tfs = [], []
            for bi, i in enumerate(chunk):
                p = tmp_dir / f"w{bi}.png"
                try:
                    _, tf = render_chart(fr.iloc[i - WINDOW + 1:i + 1], out_path=p)
                except Exception:  # noqa: BLE001
                    continue
                paths.append(str(p)); tfs.append((i, tf))
            if not paths:
                continue
            res = model.predict(paths, conf=DEFAULT_CONF, verbose=False,
                                device=args.device)
            for (i, tf), r in zip(tfs, res):
                b = r.boxes
                if b is None or len(b) == 0:
                    continue
                for row in b.xywhn.cpu().numpy():
                    cx, w = float(row[0]), float(row[2])
                    if right_edge_to_bar(cx, w, tf, n_bars=WINDOW) >= WINDOW - TIP_EDGE_BARS:
                        fires.append(i)
                        break
        fires_tot += len(fires)
        for k in names:
            hit_tot[k] += int(sum(1 for i in fires if m[k][i]))
        print(f"  {sym}: 扫 {len(idx)} bar, v9 开火 {len(fires)}", flush=True)

    for p in tmp_dir.glob("*.png"):
        p.unlink(missing_ok=True)
    wall = time.perf_counter() - t0

    print(f"\n全量扫描 {n_bars_total} bar 用时 {wall/60:.1f} min "
          f"({wall/max(n_bars_total,1)*1000:.0f} ms/bar)   v9 开火 {fires_tot}\n")
    print(f"{'预筛':<20} {'留下bar':>9} {'省GPU':>8} {'保住v9候选':>12}")
    out = {}
    for k in names:
        keep = keep_tot[k] / max(n_bars_total, 1)
        rec = hit_tot[k] / max(fires_tot, 1)
        out[k] = {"keep_frac": round(keep, 4), "recall": round(rec, 4),
                  "n_kept_fires": hit_tot[k]}
        print(f"{k:<20} {keep*100:>8.1f}% {1/max(keep,1e-9):>7.0f}x "
              f"{rec*100:>11.1f}%")
    print(f"{'none (全扫)':<20} {100.0:>8.1f}% {1:>7.0f}x {100.0:>11.1f}%")

    usable = [(k, out[k]) for k in names if out[k]["recall"] >= 0.95]
    best = min(usable, key=lambda kv: kv[1]["keep_frac"]) if usable else None
    verdict = (f"用 {best[0]}:留 {best[1]['keep_frac']*100:.1f}% 的 bar,"
               f"保住 {best[1]['recall']*100:.1f}% 的 v9 候选,提速 "
               f"{1/best[1]['keep_frac']:.0f} 倍" if best else
               "没有预筛能保住 ≥95% 候选 —— 必须全扫,否则候选池被静默改变")
    print(f"\n判读: {verdict}")
    if out["v16_dense"]["recall"] < 0.95:
        print(f"注意:v16 那套预筛只保住 {out['v16_dense']['recall']*100:.1f}% 的 v9 候选,"
              f"直接复用会静默丢弃 {100-out['v16_dense']['recall']*100:.1f}%")

    (PROJECT / "analysis" / "output" / "diag_v9_prefilter_recall.json").write_text(
        json.dumps({"n_symbols": len(syms), "n_bars": n_bars_total,
                    "n_fires": fires_tot, "wall_min": round(wall / 60, 2),
                    "prefilters": out, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
