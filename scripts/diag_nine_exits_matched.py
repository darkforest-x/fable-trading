"""Nine exits on identical candidates, each against a matched random control.

The repo has tested most of these exits before, but each on its own script and
its own pool, so no two were ever comparable -- trailing stops were measured on
one set of candidates and structural stops on another, and the reported numbers
were then placed side by side as if they were. This computes all nine from the
same klines, on the same candidates, with the same entry and horizon, so the only
thing that differs between columns is the exit rule.

Every row also carries a MATCHED RANDOM CONTROL: random shorts on the same
symbol, same month and same ATR bucket, run through the same exit. Without it a
falling alt window is scored as alpha -- measured on this exact pool, +17.15bp of
its +26.91bp was plain short beta and the detector's own contribution was
+8.97bp against a 10bp round trip. A permutation test cannot see that, because
shuffling rows leaves the drift underneath every row untouched.

  barrier   TP5xATR / SL2xATR / 72 bars, today's production
  hold      no barriers, the drift floor
  trend     alive while price stays under the MA bundle
  struct    the bundle's upper edge as a hard stop, no target
  trail     chandelier, 3xATR above the running low
  be        stop to entry once 1.5xATR of profit exists
  tponly    target, no stop -- isolates what the stop costs
  wide      same target, 4xATR stop
  partial   half off at 2xATR, remainder rides the trend exit

A NOTE ON THE BASELINE. Recomputing the pool's stored realized_ret from klines
reproduces the entry price exactly, atr_pct exactly and the exit type on 100% of
a 400-row sample, but the return differs by a median 0.026% (max 3.2%) on 77.5%
of rows, and no tested convention -- ATR bar, price basis, cost, gap fill --
explains it. Most likely the pool was built from a different kline snapshot. So
nothing here is compared against previously published numbers; the nine columns
are only compared with each other, which is internally consistent because one
code path computes them all.

Read-only, train pool (<2026-05-04), no holdout, no promote. Barrier parameters
are owner decisions; this measures alternatives and adopts nothing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_nine_exits_matched.py
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

from src.costs import SWAP_MAKER, SWAP_TAKER  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
TRAIL_ATR, BE_ARM_ATR, SL_WIDE_ATR, PARTIAL_ATR = 3.0, 1.5, 4.0, 2.0
N_CONTROL = 3                 # matched random shorts per real candidate
ATR_BUCKETS = 5
SEED = 20260729
EXITS = ("barrier", "hold", "trend", "struct", "trail", "be",
         "tponly", "wide", "partial")


def all_exits(hi, lo, cl, ma_hi, entry, atr) -> dict[str, float]:
    """Every exit rule on one path. Returns gross return of a short."""
    n = len(cl)
    out: dict[str, float] = {}

    def first(mask):
        idx = np.flatnonzero(mask)
        return int(idx[0]) if len(idx) else None

    hold = 1 - float(cl[-1]) / entry
    out["hold"] = hold

    tp, sl = entry - TP_MULT * atr, entry + SL_MULT * atr
    up, dn = first(lo <= tp), first(hi >= sl)
    if up is not None and (dn is None or up < dn):
        out["barrier"] = 1 - tp / entry
    elif dn is not None:
        out["barrier"] = 1 - sl / entry
    else:
        out["barrier"] = hold

    j = first(np.isfinite(ma_hi) & (cl > ma_hi))
    out["trend"] = 1 - float(cl[j]) / entry if j is not None else hold

    j = first(np.isfinite(ma_hi) & (hi >= ma_hi))
    out["struct"] = 1 - float(ma_hi[j]) / entry if j is not None else hold

    g, run_lo = hold, np.inf
    for k in range(n):
        run_lo = min(run_lo, float(lo[k]))
        stop = run_lo + TRAIL_ATR * atr
        if k > 0 and hi[k] >= stop:
            g = 1 - stop / entry
            break
    out["trail"] = g

    g, armed = hold, False
    for k in range(n):
        if not armed and lo[k] <= entry - BE_ARM_ATR * atr:
            armed = True
        elif armed and hi[k] >= entry:
            g = 0.0
            break
    out["be"] = g

    j = first(lo <= tp)
    out["tponly"] = (TP_MULT * atr / entry) if j is not None else hold

    tp_w, sl_w = entry - TP_MULT * atr, entry + SL_WIDE_ATR * atr
    up_w, dn_w = first(lo <= tp_w), first(hi >= sl_w)
    if up_w is not None and (dn_w is None or up_w < dn_w):
        out["wide"] = 1 - tp_w / entry
    elif dn_w is not None:
        out["wide"] = 1 - sl_w / entry
    else:
        out["wide"] = hold

    j = first(lo <= entry - PARTIAL_ATR * atr)
    out["partial"] = (out["trend"] if j is None
                      else 0.5 * (PARTIAL_ATR * atr / entry) + 0.5 * out["trend"])
    return out


def run_one(ind, ma_hi_all, i: int) -> dict[str, float] | None:
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
    return all_exits(ind["high"].to_numpy()[ei:last + 1],
                     ind["low"].to_numpy()[ei:last + 1],
                     ind["close"].to_numpy()[ei:last + 1],
                     ma_hi_all[ei:last + 1], entry, atr)


def main() -> int:
    import argparse
    _ap = argparse.ArgumentParser(description=__doc__)
    _ap.add_argument("--pool", default=None,
                     help="candidate pool CSV; defaults to the tip_v1b 100x6m pool")
    _ap.add_argument("--tag", default=None, help="suffix for the output json")
    _a = _ap.parse_args()
    global POOL
    if _a.pool:
        POOL = Path(_a.pool) if Path(_a.pool).is_absolute() else PROJECT / _a.pool

    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d[d["t"] < HOLDOUT].reset_index(drop=True)
    series = list_series(bar="15m")
    rng = np.random.default_rng(SEED)
    cache: dict[str, tuple | None] = {}

    def get(sym):
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                fr = add_mas(load_series(series[key]))
                ind = add_indicators(fr)
                ma = np.vstack([fr[c].to_numpy(dtype=float)
                                for c in ALL_MA_COLS if c in fr.columns])
                cache[sym] = (ind, pd.to_datetime(ind["open_time"], utc=True),
                              np.nanmax(ma, axis=0))
            else:
                cache[sym] = None
        return cache[sym]

    print(f"池 {POOL.name}  {len(d)} 行 / {d['symbol'].nunique()} 币", flush=True)
    print("① 真实候选的九种出场…", flush=True)
    real, meta = [], []
    for k, (sym, grp) in enumerate(d.groupby("symbol"), 1):
        e = get(sym)
        if e is None:
            continue
        ind, times, ma_hi = e
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if not (200 <= i < len(ind) - 2):
                continue
            v = run_one(ind, ma_hi, i)
            if v is None:
                continue
            real.append(v)
            meta.append({"sym": sym, "t": r["t"],
                         "month": r["t"].to_period("M"),
                         "atr_pct": float(ind["atr_pct"].iloc[i])})
        if k % 25 == 0:
            print(f"   [{k}/{d['symbol'].nunique()}]", flush=True)
    R = pd.DataFrame(real)
    M = pd.DataFrame(meta)
    print(f"   有效候选 {len(R)}\n")

    # matched control: same symbol, same month, same ATR bucket, random bar
    print(f"② 匹配随机对照(同币 × 同月 × 同 ATR 桶,每笔 {N_CONTROL} 个)…", flush=True)
    M["bucket"] = pd.qcut(M["atr_pct"], ATR_BUCKETS, labels=False, duplicates="drop")
    ctrl, ctrl_key = [], []
    for (sym, month), grp in M.groupby(["sym", "month"], observed=True):
        e = get(sym)
        if e is None:
            continue
        ind, times, ma_hi = e
        in_month = np.flatnonzero((times.dt.to_period("M") == month).to_numpy())
        in_month = in_month[(in_month >= 200) & (in_month < len(ind) - HORIZON - 2)]
        if len(in_month) == 0:
            continue
        atrp = ind["atr_pct"].to_numpy()
        for _, row in grp.iterrows():
            lo_b, hi_b = M.loc[M["bucket"] == row["bucket"], "atr_pct"].agg(["min", "max"])
            pool_b = in_month[(atrp[in_month] >= lo_b) & (atrp[in_month] <= hi_b)]
            if len(pool_b) == 0:
                pool_b = in_month
            for b in rng.choice(pool_b, size=min(N_CONTROL, len(pool_b)), replace=False):
                v = run_one(ind, ma_hi, int(b))
                if v is not None:
                    ctrl.append(v)
                    ctrl_key.append(row["bucket"])
    C = pd.DataFrame(ctrl)
    print(f"   对照样本 {len(C)}\n")

    print("=== 九种出场:真实候选 vs 匹配随机对照(净@taker)===")
    print(f"{'出场':<10} {'候选净':>11} {'对照净':>11} {'因果超额':>11} {'t':>7} "
          f"{'胜率':>8} {'PF':>7}")
    rows = []
    for ex in EXITS:
        a = R[ex].to_numpy() - SWAP_TAKER
        b = C[ex].to_numpy() - SWAP_TAKER
        diff = float(a.mean() - b.mean())
        se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
        t = diff / se if se > 0 else float("nan")
        w, l = a[a > 0].sum(), a[a < 0].sum()
        pf = float(w / -l) if l < 0 else None
        rows.append({"exit": ex, "n": len(a),
                     "cand_net": round(float(a.mean()), 6),
                     "ctrl_net": round(float(b.mean()), 6),
                     "excess": round(diff, 6), "t": round(t, 2),
                     "win": round(float((a > 0).mean()), 4),
                     "pf": round(pf, 3) if pf else None})
        mark = "  ✅" if t > 2.58 and diff > SWAP_TAKER else ""
        print(f"{ex:<10} {a.mean()*1e4:>+10.2f}bp {b.mean()*1e4:>+10.2f}bp "
              f"{diff*1e4:>+10.2f}bp {t:>+7.2f} {(a>0).mean()*100:>7.1f}% "
              f"{str(pf):>7}{mark}")
    print(f"\n往返成本 taker {SWAP_TAKER*1e4:.0f}bp / maker {SWAP_MAKER*1e4:.0f}bp"
          f"  —— 因果超额必须超过它才有意义")

    best = max(rows, key=lambda r: r["excess"])
    base = next(r for r in rows if r["exit"] == "barrier")
    beats = [r for r in rows if r["excess"] > SWAP_TAKER and r["t"] > 2.58]
    verdict = (
        f"最优 {best['exit']}:因果超额 {best['excess']*1e4:+.2f}bp(t={best['t']:+.2f}),"
        f"现行 barrier {base['excess']*1e4:+.2f}bp(t={base['t']:+.2f})。"
        + (f"超过 taker 成本且 t>2.58 的有 {len(beats)} 种:"
           + "、".join(r["exit"] for r in beats)
           if beats else "没有一种出场的因果超额能覆盖 10bp 往返成本 —— "
                         "换出场规则解决不了这个池子的问题。"))
    print(f"\n判读: {verdict}")
    print("注:基线与池中 realized_ret 有 0.026% 中位偏差(原因未查明),"
          "故只做九种之间横比,不与历史数字对照。")

    (PROJECT / "analysis" / "output" /
     f"diag_nine_exits_matched{'_'+_a.tag if _a.tag else ''}.json").write_text(
        json.dumps({"pool": POOL.name, "n_candidates": len(R), "n_control": len(C),
                    "n_control_per": N_CONTROL, "atr_buckets": ATR_BUCKETS,
                    "results": rows, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
