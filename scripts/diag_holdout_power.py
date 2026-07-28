"""How big an effect can the holdout actually resolve? Compute it before spending one.

Holdout #9 was consumed on a configuration whose real edge, had it existed at the
measured size, had only about a 65% chance of clearing the bar with the n it ran
on. The consumption is gone either way. The fix is arithmetic that costs nothing:
work out, before asking to spend #10, what effect size the holdout can separate
from noise, and compare that to the effect actually being chased.

Nothing here reads the holdout. Variance comes from the train pool, where it is
already known and freely available; the holdout enters only as a candidate COUNT,
which is metadata this project has already published (1739 under the v6
detector). Under v9 the count will differ, so the answer is given as a curve over
n rather than a single verdict.

Three questions, because they need different tests:

  A  is the raw pool's net per trade > 0?          one-sample, sigma of net
  B  does dropping the barriers beat keeping them?  PAIRED -- same candidates,
     two exits, so the relevant spread is sigma of the per-trade DIFFERENCE,
     which is far smaller than either arm's own spread and needs far less n
  C  does a selection layer's top decile beat the pool?  two-sample, and the
     decile is only a tenth of the候选, which is where power usually dies

Reported at the project's own bar: p < 0.01, power 0.80.

Read-only, no holdout access, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_holdout_power.py
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
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
RECENT_FROM = pd.Timestamp("2026-02-20", tz="UTC")
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
ALPHA, POWER = 0.01, 0.80
Z_A = 2.5758        # two-sided 0.01
Z_B = 0.8416        # power 0.80
N_GRID = (500, 1000, 1739, 3000, 5000, 10000, 20000)


def both(ind, i: int) -> tuple[float, float] | None:
    ei = i + 1
    if ei >= len(ind):
        return None
    atr = float(ind["atr14"].iloc[i])
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    if last - ei + 1 < HORIZON:
        return None
    hi = ind["high"].to_numpy()[ei:last + 1]
    lo = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up = int(np.argmax(lo <= tp)) if (lo <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if (hi >= sl).any() else len(cl)
    if up < dn:
        b = 1 - tp / entry
    elif dn < up:
        b = 1 - sl / entry
    else:
        b = 1 - float(cl[-1]) / entry
    return b - SWAP_TAKER, (1 - float(cl[-1]) / entry) - SWAP_TAKER


def n_one_sample(sigma: float, delta: float) -> float:
    return (Z_A + Z_B) ** 2 * sigma ** 2 / delta ** 2


def n_two_sample(sigma: float, delta: float) -> float:
    return 2 * (Z_A + Z_B) ** 2 * sigma ** 2 / delta ** 2


def mde_one(sigma: float, n: float) -> float:
    return (Z_A + Z_B) * sigma / math.sqrt(n)


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT_START]
    series = list_series(bar="15m")
    cache: dict[str, object] = {}
    rows = []
    for sym, grp in d.groupby("symbol"):
        key = ("okx", sym)
        if sym not in cache:
            cache[sym] = add_indicators(add_mas(load_series(series[key]))) if key in series else None
        ind = cache[sym]
        if ind is None:
            continue
        times = pd.to_datetime(ind["open_time"], utc=True)
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if not (200 <= i < len(ind) - 2):
                continue
            v = both(ind, i)
            if v:
                rows.append({"t": r["t"], "barrier": v[0], "hold": v[1]})
    x = pd.DataFrame(rows)
    rec = x[x["t"] >= RECENT_FROM]
    print(f"训练池 {len(x)} 笔(近期块 {len(rec)} 笔)  显著性 p<{ALPHA}  功效 {POWER:.0%}\n")

    s_b = float(x["barrier"].std())
    s_h = float(x["hold"].std())
    s_d = float((x["hold"] - x["barrier"]).std())
    print(f"每笔净收益的标准差:")
    print(f"  现行障碍   {s_b*100:.3f}%")
    print(f"  纯持72根   {s_h*100:.3f}%")
    print(f"  两者之差   {s_d*100:.3f}%   ← 配对检验用这个,比单臂小很多\n")

    d_recent = float(rec["hold"].mean() - rec["barrier"].mean())
    d_pooled = float(x["hold"].mean() - x["barrier"].mean())
    net_recent = float(rec["hold"].mean())

    print("=== 问题 A:裸池净收益 > 0 吗(单样本)===")
    for label, delta in (("近期块的纯持 %+.4f%%" % (net_recent * 100), net_recent),
                         ("池化的纯持 %+.4f%%" % (x['hold'].mean() * 100),
                          float(x["hold"].mean()))):
        need = n_one_sample(s_h, abs(delta)) if delta else float("inf")
        print(f"  要证明「{label}」显著为正,需要 {need:,.0f} 笔")

    print("\n=== 问题 B:去掉障碍胜过现行(配对检验)===")
    for label, delta in (("近期块 %+.2fbp" % (d_recent * 1e4), d_recent),
                         ("池化 %+.2fbp" % (d_pooled * 1e4), d_pooled)):
        need = n_one_sample(s_d, abs(delta)) if delta else float("inf")
        print(f"  要证明「{label}」的差异显著,需要 {need:,.0f} 笔配对样本")

    print("\n=== 问题 C:某筛选层顶档胜过全池(双样本,顶档只占 1/10)===")
    for lift_bp in (5.0, 10.0, 20.0, 40.0):
        need = n_two_sample(s_h, lift_bp / 1e4)
        print(f"  顶档要比全池高 {lift_bp:>4.0f}bp 才算数 → 每组需 {need:>10,.0f} 笔"
              f"(即总候选 {need*10:,.0f} 笔)")

    print(f"\n=== 反过来问:给定样本量,能分辨多大的效应(MDE)===")
    print(f"{'样本量':>8} {'单样本MDE':>12} {'配对MDE':>12}  说明")
    out_grid = []
    for n in N_GRID:
        m1, m2 = mde_one(s_h, n), mde_one(s_d, n)
        note = ""
        if n == 1739:
            note = "← v6 检测器下的 holdout 候选数"
        out_grid.append({"n": n, "mde_one_sample_bp": round(m1 * 1e4, 2),
                         "mde_paired_bp": round(m2 * 1e4, 2)})
        print(f"{n:>8,} {m1*1e4:>11.2f}bp {m2*1e4:>11.2f}bp  {note}")

    mde_1739_one = mde_one(s_h, 1739) * 1e4
    mde_1739_pair = mde_one(s_d, 1739) * 1e4
    can_a = net_recent * 1e4 >= mde_1739_one
    can_b = abs(d_recent) * 1e4 >= mde_1739_pair
    verdict = (
        f"n=1739 时,单样本只能分辨 ≥{mde_1739_one:.1f}bp 的效应,配对能分辨 "
        f"≥{mde_1739_pair:.1f}bp。近期块里要证的是:裸池净 "
        f"{net_recent*1e4:+.1f}bp({'够' if can_a else '不够'})、"
        f"去障碍的增量 {d_recent*1e4:+.1f}bp({'够' if can_b else '不够'})。"
    )
    if not can_a and not can_b:
        verdict += (f" 两个都不够 → 现在做 holdout 是白消耗一次,"
                    f"要么等样本量到 {n_one_sample(s_h, abs(net_recent)):,.0f} 笔,"
                    f"要么先找更大的效应(例如把判断层修好再验)。")
    elif can_b and not can_a:
        verdict += " 只有配对比较够功效 → holdout 只应用来回答 B,不要顺带看 A。"
    print(f"\n判读: {verdict}")
    print("注:本脚本不读 holdout,方差取自训练池,holdout 只以候选数出现。"
          "v9 的 holdout 候选数会与 1739 不同,按上表的 n 自行对照。")

    (PROJECT / "analysis" / "output" / "diag_holdout_power.json").write_text(
        json.dumps({"alpha": ALPHA, "power": POWER,
                    "sigma": {"barrier": s_b, "hold": s_h, "paired_diff": s_d},
                    "effects_recent": {"hold_net": net_recent,
                                       "hold_minus_barrier": d_recent},
                    "effects_pooled": {"hold_net": float(x["hold"].mean()),
                                       "hold_minus_barrier": d_pooled},
                    "mde_grid": out_grid, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
