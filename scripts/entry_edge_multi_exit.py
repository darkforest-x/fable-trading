"""Does the dense-cluster ENTRY have edge under exits other than TP5/SL2?

Owner pushed back ("不可能") on the no-edge finding. Every prior test fixed the
exit at TP5/SL2/72bar -- a wide, low-win structure needing a +5*ATR move. But
manual trading uses quick profits / tight stops, and the old P0 report found
these clusters have MAE alpha (smaller drawdowns) but no MFE alpha (no bigger
upside). A wide TP5 would miss an edge that lives in "doesn't drop" + quick
exit. So test the ENTRY independent of the rigid exit:

  - directional drift: mean forward return at +4/+8/+12/+24/+48 bars (no barrier)
  - MFE / MAE over 72 bars (what's capturable / how much heat)
  - win rate + net under TIGHT exits: TP{1,2,3}xATR / SL{1,1.5}xATR

Compared owner-boxes vs rule-dense vs random. Causal, <2026-05-04.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.costs import FORWARD_COST  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from scripts.build_crop_pad200_dataset import (  # noqa: E402
    WINDOW, boxes_cut_and_spans, parse_stem, read_boxes, resolve_series, resolve_win_start,
)

V11 = PROJECT / "datasets" / "_deprecated_pretip" / "dense_owner_v11"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
HORIZ = 72
FAST_MAX, FULL_MAX = 0.0028, 0.0055


def path_metrics(ind, i):
    """Directional drift, MFE/MAE, tight-exit outcomes from entry i+1 open."""
    entry_i = i + 1
    if entry_i >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i]); atr_pct = float(ind["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    entry = float(ind["open"].iloc[entry_i])
    if entry <= 0:
        return None
    last = min(entry_i + HORIZ - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[entry_i:last + 1]
    lo = ind["low"].to_numpy()[entry_i:last + 1]
    cl = ind["close"].to_numpy()[entry_i:last + 1]
    if len(cl) < 8:
        return None
    m = {}
    for hbar in (4, 8, 12, 24, 48):
        if len(cl) > hbar:
            m[f"ret{hbar}"] = cl[hbar] / entry - 1
    m["mfe"] = float(hi.max() / entry - 1)   # max favorable
    m["mae"] = float(lo.min() / entry - 1)   # max adverse (<=0)
    # tight exits: first-touch TP a*ATR vs SL b*ATR
    for a in (1.0, 2.0, 3.0):
        for b in (1.0, 1.5):
            up = entry + a * atr; dn = entry - b * atr
            ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
            dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
            if ut == dt == 10**9:
                g = cl[-1] / entry - 1
            elif ut <= dt:
                g = a * atr / entry
            else:
                g = -b * atr / entry
            m[f"net_tp{a:g}_sl{b:g}"] = g - FORWARD_COST
    return m


def agg(rows):
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    out = {"n": len(df)}
    for hbar in (4, 8, 12, 24, 48):
        col = f"ret{hbar}"
        if col in df:
            out[f"ret{hbar}_mean_bps"] = round(float(df[col].mean()) * 1e4, 1)
    out["mfe_mean"] = round(float(df["mfe"].mean()), 4)
    out["mae_mean"] = round(float(df["mae"].mean()), 4)
    for c in [c for c in df.columns if c.startswith("net_")]:
        x = df[c].to_numpy()
        w, l = x[x > 0].sum(), x[x < 0].sum()
        out[c] = {"win": round(float((x > 0).mean()), 3),
                  "PF": round(float(w / -l), 3) if l < 0 else None,
                  "mean_bps": round(float(x.mean()) * 1e4, 1)}
    return out


def main() -> int:
    rng = random.Random(7)
    owner, dense, rand = [], [], []
    resolved: dict[str, pd.DataFrame | None] = {}
    enr: dict[str, tuple] = {}
    label_files = list((V11 / "labels" / "train").glob("*.txt")) + list((V11 / "labels" / "val").glob("*.txt"))
    print(f"{len(label_files)} label files", flush=True)
    for k, lbl in enumerate(label_files):
        stem = lbl.stem
        if is_eval_stem(stem):
            continue
        boxes = read_boxes(lbl)
        if not boxes:
            continue
        parsed = parse_stem(stem)
        if not parsed:
            continue
        body, idx = parsed
        if body not in resolved:
            resolved[body] = resolve_series(body)
        df = resolved[body]
        if df is None:
            continue
        if body not in enr:
            ema = add_mas(df); ind = add_indicators(df)
            fast = pd.to_numeric(ema["fast_spread"], errors="coerce").to_numpy()
            full = pd.to_numeric(ema["full_spread"], errors="coerce").to_numpy()
            times = pd.to_datetime(df["open_time"], utc=True)
            pre = int((times < HOLDOUT_START).sum())
            enr[body] = (ema, ind, fast, full, times, pre)
        ema, ind, fast, full, times, pre = enr[body]
        n = len(df)
        png = V11 / "images" / ("train" if (V11 / "images/train" / f"{stem}.png").exists() else "val") / f"{stem}.png"
        stored = cv2.imread(str(png)) if png.exists() else None
        res = resolve_win_start(n, idx, enriched=ema, stored_img=stored)
        if res is None:
            continue
        _m, win_start, _d = res
        if win_start < 0 or win_start + WINDOW > n:
            continue
        sub = ema.iloc[win_start:win_start + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            continue
        try:
            cut_local, _sp = boxes_cut_and_spans(boxes, make_chart_transform(sub))
        except Exception:
            continue
        cut = win_start + cut_local
        if cut < 210 or cut >= min(n - HORIZ - 2, pre):
            continue
        pm = path_metrics(ind, cut)
        if pm:
            owner.append(pm)
        if k % 800 == 0:
            print(f"  {k}/{len(label_files)} owner={len(owner)}", flush=True)

    # rule-dense + random from the same symbols (pre-holdout)
    for body, (ema, ind, fast, full, times, pre) in enr.items():
        dmask = (fast <= FAST_MAX) & (full <= FULL_MAX)
        run = 0
        dbars = []
        for i in range(len(dmask)):
            run = run + 1 if dmask[i] else 0
            if run == 5 and 210 <= i < min(len(ind) - HORIZ - 2, pre):
                dbars.append(i)
        for i in dbars[:40]:
            pm = path_metrics(ind, i)
            if pm:
                dense.append(pm)
        pool = list(range(210, min(len(ind) - HORIZ - 2, pre)))
        for i in rng.sample(pool, min(40, len(pool))):
            pm = path_metrics(ind, i)
            if pm:
                rand.append(pm)

    out = {"owner_boxes": agg(owner), "rule_dense": agg(dense), "random": agg(rand)}
    (PROJECT / "analysis" / "output" / "entry_edge_multi_exit.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
