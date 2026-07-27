"""Does v6's confidence separate the trades that work from the ones that don't?

Owner pushed back on the flat result, and the first thing that looks wrong on
inspection is the firing rate: v6 fires 290 times over 12 majors in 5 days, i.e.
~4.8 per symbol per day. The owner drew 5550 boxes across ~12567 charts in a
year -- their pattern is RARE. A detector firing several times a day per symbol
is answering a much looser question than the one they were labelling, and
DEFAULT_CONF=0.30 is a low bar for a single-class detector.

So: re-predict every candidate in the pool, record the confidence of the box
that passes the tip-edge gate, and sweep the threshold against realized return.
If the pattern is real but rare, the top confidence slice should separate; if
confidence is uninformative, every slice sits at the same breakeven.

Reported at gross PF and win rate as well as net, because with TP5/SL2 the
breakeven win rate is 2/7 = 28.6% and a driftless random walk delivers exactly
that -- the number to beat is 28.6%, not 0.

Read-only, no promote. Pool ends 2026-05-03, so no holdout.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_v6_conf_sweep.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

import os
POOL = Path(os.environ.get("CONF_POOL",
                          PROJECT / "data" / "judgment_yolo_short_v6.csv"))
WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v6/weights/best.pt"
SCAN_CONF = 0.05          # scan low so the whole confidence range is visible
BATCH = 16
BREAKEVEN = 2.0 / 7.0     # TP5/SL2 on a driftless walk


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    print(f"候选 {len(d)}")
    model = load_yolo_model(str(WEIGHTS))
    series = list_series(bar="15m")
    tmp = PROJECT / "data" / "_confsweep.png"

    confs: list[float | None] = []
    cache: dict[str, pd.DataFrame | None] = {}
    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            cache[sym] = add_mas(load_series(series[key])) if key in series else None
        fr = cache[sym]
        idxs = list(grp.index)
        if fr is None:
            confs.extend([(i, None) for i in idxs])
            continue
        times = pd.to_datetime(fr["open_time"], utc=True)
        batch = []
        for i in idxs:
            ti = int(times.searchsorted(d.at[i, "t"]))
            if ti < WINDOW or ti >= len(fr):
                confs.append((i, None))
                continue
            p = tmp.with_name(f"{tmp.stem}_{len(batch)}.png")
            try:
                _, tf = render_chart(fr.iloc[ti - WINDOW + 1:ti + 1], out_path=p)
            except Exception:  # noqa: BLE001
                confs.append((i, None))
                continue
            batch.append((i, tf, p))
            if len(batch) >= BATCH:
                _flush(model, batch, confs)
                batch = []
        _flush(model, batch, confs)
    for k in range(BATCH):
        tmp.with_name(f"{tmp.stem}_{k}.png").unlink(missing_ok=True)

    cmap = dict(confs)
    d["conf"] = [cmap.get(i) for i in d.index]
    ok = d.dropna(subset=["conf"]).copy()
    print(f"取到置信度 {len(ok)}  分布: "
          f"p10={ok.conf.quantile(.1):.3f} p50={ok.conf.median():.3f} "
          f"p90={ok.conf.quantile(.9):.3f} max={ok.conf.max():.3f}\n")

    rows = []
    print(f"{'conf 门槛':>10} {'笔数':>6} {'胜率':>8} {'毛PF':>7} {'净@taker':>11}")
    print(f"{'':>10} {'':>6} {'(平衡 28.6%)':>8}")
    for c in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90):
        s = ok[ok.conf >= c]
        if len(s) < 30:
            print(f"{c:>10.2f} {len(s):>6}  样本太少")
            continue
        r = s["realized_ret"].to_numpy()
        wr = float((s["label"] == 1).mean())
        rows.append({"conf": c, "n": len(s), "win_rate": round(wr, 4),
                     "gross_PF": pf(r), "net_taker": round(float(r.mean() - SWAP_TAKER), 5)})
        print(f"{c:>10.2f} {len(s):>6} {wr*100:>7.1f}% {str(pf(r)):>7} "
              f"{r.mean()-SWAP_TAKER:>+11.5f}")

    # persist per-row conf so the time-split test below needs no re-scan
    ok[["symbol", "signal_time", "conf", "label", "realized_ret"]].to_csv(
        PROJECT / "analysis" / "output" / "v6_conf_per_candidate.csv", index=False)

    # The failure mode this project keeps hitting is "worked early, died late".
    # Split by time and require the high-conf slice to survive the LATER half.
    print("\n=== 时间切分:conf>=0.5 在前后半段各自的表现 ===")
    ok2 = ok.sort_values("t").reset_index(drop=True)
    half = len(ok2) // 2
    for name, seg in (("前半段", ok2.iloc[:half]), ("后半段(更近)", ok2.iloc[half:])):
        hi = seg[seg.conf >= 0.5]
        allr = seg["realized_ret"].to_numpy()
        print(f"  {name}: {seg['t'].iloc[0].date()}~{seg['t'].iloc[-1].date()}  "
              f"全体 n={len(seg)} 胜率 {(seg['label']==1).mean()*100:.1f}% PF {pf(allr)}")
        if len(hi) >= 10:
            r = hi["realized_ret"].to_numpy()
            print(f"      conf>=0.5: n={len(hi)} 胜率 {(hi['label']==1).mean()*100:.1f}% "
                  f"毛PF {pf(r)} 净@taker {r.mean()-SWAP_TAKER:+.5f}")
        else:
            print(f"      conf>=0.5: n={len(hi)} 太少")

    best = max(rows, key=lambda x: x["win_rate"]) if rows else None
    verdict = ("置信度无区分力:各档胜率都贴 28.6% → 检测器的把握程度不含方向信息"
               if best and best["win_rate"] < BREAKEVEN + 0.03
               else f"高置信度有区分:conf>={best['conf']} 胜率 {best['win_rate']*100:.1f}%")
    print(f"\n判读: {verdict}")
    (PROJECT / "analysis" / "output" / f'diag_v6_conf_sweep_{POOL.stem}.json').write_text(
        json.dumps({"pool": str(POOL.name), "weights": str(WEIGHTS.name),
                    "breakeven_win": round(BREAKEVEN, 4), "rows": rows,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


def _flush(model, batch, confs):
    if not batch:
        return
    res = model.predict([str(p) for _, _, p in batch], conf=SCAN_CONF,
                        verbose=False, device="cpu")
    for (i, tf, _), r in zip(batch, res):
        b = r.boxes
        best = None
        if b is not None and len(b) > 0:
            xy = b.xywhn.cpu().numpy()
            cf = b.conf.cpu().numpy()
            for row, c in zip(xy, cf):
                bar = right_edge_to_bar(float(row[0]) + float(row[2]) / 2, 0.0, tf, n_bars=WINDOW)
                if (WINDOW - 1) - bar <= TIP_EDGE_BARS and (best is None or c > best):
                    best = float(c)
        confs.append((i, best))


if __name__ == "__main__":
    raise SystemExit(main())
