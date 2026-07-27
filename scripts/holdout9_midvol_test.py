"""Holdout #9: the frozen mid-volatility x high-confidence configuration.

Runs exactly the configuration registered in analysis/p_prereg_holdout9_midvol.md
before this data was touched. Nothing here is tunable: the confidence threshold,
the volatility band, the barriers and the success criterion were all committed in
that card, and the card also fixes what happens on each outcome -- a failure
retires the configuration rather than licensing a boundary tweak and a retry.

Owner approved consumption #9 in conversation on 2026-07-27.

The single criterion: Wilson 95% lower bound on the win rate above the 28.6%
TP5/SL2 breakeven. Fewer than 8 in-band trades is reported as insufficient
sample, not as grounds to widen the band until some appear.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/holdout9_midvol_test.py
"""
from __future__ import annotations

import json
import math
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

# --- frozen by the pre-registration card; do not edit ---
POOL = PROJECT / "data" / "judgment_yolo_short_v6_holdout9.csv"
WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_short_star_v6/weights/best.pt"
CONF_HI = 0.50
VOL_LO, VOL_HI = 0.00245, 0.00343
BREAKEVEN = 2.0 / 7.0
MIN_N = 8
SCAN_CONF, BATCH = 0.05, 16


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def _flush(model, batch, out):
    if not batch:
        return
    res = model.predict([str(p) for _, _, p in batch], conf=SCAN_CONF,
                        verbose=False, device="cpu")
    for (i, tf, _), r in zip(batch, res):
        b = r.boxes
        best = None
        if b is not None and len(b) > 0:
            for row, c in zip(b.xywhn.cpu().numpy(), b.conf.cpu().numpy()):
                bar = right_edge_to_bar(float(row[0]) + float(row[2]) / 2, 0.0,
                                        tf, n_bars=WINDOW)
                if (WINDOW - 1) - bar <= TIP_EDGE_BARS and (best is None or c > best):
                    best = float(c)
        out.append((i, best))


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    print(f"holdout 候选 {len(d)}   窗口 {d['t'].min().date()} ~ {d['t'].max().date()}")

    model = load_yolo_model(str(WEIGHTS))
    series = list_series(bar="15m")
    tmp = PROJECT / "data" / "_h9.png"
    confs: list[tuple[int, float | None]] = []
    cache: dict[str, pd.DataFrame | None] = {}
    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            cache[sym] = add_mas(load_series(series[key])) if key in series else None
        fr = cache[sym]
        if fr is None:
            confs.extend([(i, None) for i in grp.index])
            continue
        times = pd.to_datetime(fr["open_time"], utc=True)
        batch = []
        for i in grp.index:
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
    d["conf"] = [dict(confs).get(i) for i in d.index]

    btc = load_series(series[("okx", "BTC_USDT_SWAP")])
    c, h, lo = (btc[x].astype(float) for x in ("close", "high", "low"))
    env = pd.DataFrame({"t": pd.to_datetime(btc["open_time"], utc=True),
                        "btc_vol": ((h - lo) / c).rolling(14).mean()}).dropna()
    d = pd.merge_asof(d.sort_values("t"), env.sort_values("t"), on="t",
                      direction="backward")

    hi = d[(d["conf"] >= CONF_HI)].dropna(subset=["btc_vol"])
    band = hi[(hi["btc_vol"] >= VOL_LO) & (hi["btc_vol"] <= VOL_HI)]
    out_band = hi[~((hi["btc_vol"] >= VOL_LO) & (hi["btc_vol"] <= VOL_HI))]

    k, n = int((band["label"] == 1).sum()), len(band)
    ci_lo, ci_hi = wilson(k, n)
    print(f"\nconf>={CONF_HI} 共 {len(hi)}   其中中波动带 [{VOL_LO}, {VOL_HI}] 内 {n}")

    if n < MIN_N:
        verdict = f"样本不足({n} < {MIN_N}):不予裁决。按预注册,不放宽波动带凑样本。"
        passed = None
    else:
        passed = bool(ci_lo * 100 > BREAKEVEN * 100)
        verdict = ("✅ 通过:区间下沿 %.1f%% > 盈亏平衡 %.1f%%" % (ci_lo * 100, BREAKEVEN * 100)
                   if passed else
                   "❌ 未通过:区间下沿 %.1f%% <= 盈亏平衡 %.1f%%。按预注册,该配置作废,"
                   "不得换边界重试。" % (ci_lo * 100, BREAKEVEN * 100))

    print(f"\n=== 主判据 ===")
    print(f"  中波动带 conf>={CONF_HI}: {k}/{n} = {100*k/max(n,1):.1f}%  "
          f"95%CI [{ci_lo*100:.1f}, {ci_hi*100:.1f}]   盈亏平衡 {BREAKEVEN*100:.1f}%")

    print(f"\n=== 辅助记录(不参与判定) ===")
    for name, s in (("中波动带", band), ("带外", out_band), ("全体 conf>=0.5", hi),
                    ("全体候选", d)):
        if len(s) == 0:
            continue
        r = s["realized_ret"].to_numpy()
        kk = int((s["label"] == 1).sum())
        print(f"  {name:16s} n={len(s):>4}  胜率 {100*kk/len(s):>5.1f}%  "
              f"毛PF {str(pf(r)):>6}  净@taker {r.mean()-SWAP_TAKER:>+9.5f}")

    print(f"\n判读: {verdict}")
    (PROJECT / "analysis" / "output" / "holdout9_midvol.json").write_text(
        json.dumps({
            "prereg": "analysis/p_prereg_holdout9_midvol.md",
            "consumption": 9, "window": [str(d["t"].min()), str(d["t"].max())],
            "conf_hi": CONF_HI, "band": [VOL_LO, VOL_HI],
            "n_candidates": len(d), "n_high_conf": len(hi), "n_in_band": n,
            "wins": k, "win_rate": round(k / n, 4) if n else None,
            "ci": [round(ci_lo, 4), round(ci_hi, 4)],
            "breakeven": round(BREAKEVEN, 4), "passed": passed,
            "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
