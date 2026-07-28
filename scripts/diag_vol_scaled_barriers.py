"""Volatility-scaled barriers vs fixed ATR multiples -- the one exit idea left untested.

Every alternative exit tried so far lost to fixed TP5xATR/SL2xATR: trailing stops
-0.190%, MA-reversal exit -0.054%, structural stop -0.006%, all against +0.031%.
The cause was consistent -- each one cut the +3.6% TP tail that carries the whole
expectancy. So the bar here is not "raises win rate", it is "keeps the tail".

What has never been tried is scaling the barrier to a volatility estimate other
than ATR14. The motivation is concrete and measured: at the lowest ATR decile the
2xATR stop sits only 0.6% away, which a single noise bar takes out. Two questions
follow, and they are different:

  SCALE   does a different sigma (EWM of returns, Parkinson high-low, Yang-Zhang)
          place the same 5:2 ratio better than ATR14 does?
  FLOOR   does putting a floor under the stop distance -- so it can never be
          tighter than X% -- rescue the low-volatility trades specifically?

Both are swept over the same candidates, and the low-ATR subgroup is reported
separately, because a change that helps only the tight-stop tail would be washed
out in the pooled number.

Reported on gross PF and net at the taker floor, plus the TP-tail share, so an
"improvement" that works by truncating winners is visible rather than hidden.

Barrier parameters are owner decisions (CLAUDE.md escalation rule); this measures
alternatives and adopts nothing. Read-only, train pool, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_vol_scaled_barriers.py
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
from src.judgment.candidates import add_indicators  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6_wide.csv"
TP_MULT, SL_MULT, HORIZON = 5.0, 2.0, 72
EWM_SPAN = 32
FLOORS_PCT = (0.0, 0.4, 0.6, 0.8, 1.2, 1.6, 2.0, 2.5, 3.0, 4.0)
# minimum stop distance, percent. Swept past the point where net turns over:
# a floor that only ever improves would mean ATR is doing no work at all and
# the honest conclusion is a flat percentage stop, not a floored ATR one.


def sigma_frame(fr: pd.DataFrame) -> dict[str, np.ndarray]:
    """Three volatility estimators, all causal (no bar beyond t is used)."""
    c = fr["close"].astype(float)
    h = fr["high"].astype(float)
    lo = fr["low"].astype(float)
    o = fr["open"].astype(float)

    r = np.log(c / c.shift(1))
    ewm = r.ewm(span=EWM_SPAN, adjust=False).std()                 # EWM of log returns
    park = np.sqrt((np.log(h / lo) ** 2).rolling(EWM_SPAN).mean() / (4 * np.log(2)))
    # Yang-Zhang: overnight + open-close + Rogers-Satchell, robust to gaps/drift
    oc = np.log(o / c.shift(1))
    co = np.log(c / o)
    rs = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)
    k = 0.34 / (1.34 + (EWM_SPAN + 1) / (EWM_SPAN - 1))
    yz = np.sqrt(oc.rolling(EWM_SPAN).var() + k * co.rolling(EWM_SPAN).var()
                 + (1 - k) * rs.rolling(EWM_SPAN).mean())
    return {"ewm": ewm.to_numpy(dtype=float),
            "parkinson": park.to_numpy(dtype=float),
            "yang_zhang": yz.to_numpy(dtype=float)}


def simulate(ind, i: int, stop_dist: float, tp_dist: float) -> dict | None:
    """One short with absolute price distances. Same horizon, same entry."""
    ei = i + 1
    if ei >= len(ind) or not np.isfinite(stop_dist) or stop_dist <= 0:
        return None
    entry = float(ind["open"].iloc[ei])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last = min(ei + HORIZON - 1, len(ind) - 1)
    hi = ind["high"].to_numpy()[ei:last + 1]
    low = ind["low"].to_numpy()[ei:last + 1]
    cl = ind["close"].to_numpy()[ei:last + 1]
    if len(cl) < 2:
        return None
    tp, sl = entry - tp_dist, entry + stop_dist
    up = int(np.argmax(low <= tp)) if (low <= tp).any() else len(cl)
    dn = int(np.argmax(hi >= sl)) if (hi >= sl).any() else len(cl)
    if up < dn:
        return {"ret": 1 - tp / entry, "why": "TP"}
    if dn < up:
        return {"ret": 1 - sl / entry, "why": "SL"}
    return {"ret": 1 - cl[-1] / entry, "why": "TIMEOUT"}


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    ret = np.array([r["ret"] for r in rows])
    w, l = ret[ret > 0].sum(), ret[ret < 0].sum()
    return {"n": len(rows),
            "tp_share": round(float(np.mean([r["why"] == "TP" for r in rows])), 4),
            "timeout_share": round(float(np.mean([r["why"] == "TIMEOUT" for r in rows])), 4),
            "gross_pf": round(float(w / -l), 3) if l < 0 else None,
            "gross_bp": round(float(ret.mean()) * 1e4, 2),
            "net_taker": round(float(ret.mean() - SWAP_TAKER), 6)}


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    series = list_series(bar="15m")
    cache: dict[str, tuple | None] = {}
    sigs = []
    for sym, grp in d.groupby("symbol"):
        if sym not in cache:
            key = ("okx", sym)
            if key in series:
                fr = add_mas(load_series(series[key]))
                ind = add_indicators(fr)
                cache[sym] = (ind, pd.to_datetime(ind["open_time"], utc=True),
                              sigma_frame(fr))
            else:
                cache[sym] = None
        e = cache[sym]
        if e is None:
            continue
        ind, times, sig = e
        for _, r in grp.iterrows():
            i = int(times.searchsorted(r["t"]))
            if 200 <= i < len(ind) - 2:
                sigs.append((ind, i, sig))
    print(f"候选 {len(sigs)}   TP {TP_MULT}x / SL {SL_MULT}x / {HORIZON}bar\n")

    # low-ATR subgroup: where the fixed stop is measurably too tight
    atrp = np.array([float(ind["atr_pct"].iloc[i]) for ind, i, _ in sigs])
    lowq = np.nanquantile(atrp, 0.30)
    is_low = atrp <= lowq
    print(f"低波动组 = atr_pct <= {lowq*100:.3f}% ({int(is_low.sum())} 笔),"
          f"该组固定止损距离中位 {np.nanmedian(atrp[is_low])*SL_MULT*100:.2f}%\n")

    results = []

    def run(name: str, dist_fn) -> None:
        rows, rows_low = [], []
        for k, (ind, i, sig) in enumerate(sigs):
            dd = dist_fn(ind, i, sig)
            if dd is None:
                continue
            out = simulate(ind, i, dd[0], dd[1])
            if out is None:
                continue
            rows.append(out)
            if is_low[k]:
                rows_low.append(out)
        results.append({"variant": name, "all": summarize(rows),
                        "low_atr": summarize(rows_low)})

    def atr_dist(ind, i, _sig):
        a = float(ind["atr14"].iloc[i])
        return (SL_MULT * a, TP_MULT * a) if np.isfinite(a) and a > 0 else None

    run("baseline ATR14 5:2", atr_dist)

    for est in ("ewm", "parkinson", "yang_zhang"):
        def f(ind, i, sig, est=est):
            s = sig[est][i]
            p = float(ind["close"].iloc[i])
            if not np.isfinite(s) or s <= 0 or not np.isfinite(p):
                return None
            # sigma is per-bar log-return vol; put it on the same footing as ATR
            # by expressing the barrier as a price distance of k*sigma*price
            return (SL_MULT * s * p, TP_MULT * s * p)
        run(f"sigma={est} 5:2", f)

    for fl in FLOORS_PCT[1:]:
        def f(ind, i, _sig, fl=fl):
            a = float(ind["atr14"].iloc[i])
            p = float(ind["close"].iloc[i])
            if not np.isfinite(a) or a <= 0 or not np.isfinite(p):
                return None
            stop = max(SL_MULT * a, fl / 100.0 * p)          # floor under the stop
            return (stop, TP_MULT / SL_MULT * stop)          # keep the 5:2 ratio
        run(f"ATR14 + 止损下限 {fl}%", f)

    def no_barrier(ind, i, _sig):
        p0 = float(ind["close"].iloc[i])
        return (p0 * 10.0, p0 * 10.0) if np.isfinite(p0) and p0 > 0 else None

    def tp_only(ind, i, _sig):
        a = float(ind["atr14"].iloc[i]); p0 = float(ind["close"].iloc[i])
        return (p0 * 10.0, TP_MULT * a) if np.isfinite(a) and a > 0 else None

    def sl_only(ind, i, _sig):
        a = float(ind["atr14"].iloc[i]); p0 = float(ind["close"].iloc[i])
        return (SL_MULT * a, p0 * 10.0) if np.isfinite(a) and a > 0 else None

    run("对照: 无障碍纯持72根", no_barrier)
    run("对照: 只止盈无止损", tp_only)
    run("对照: 只止损无止盈", sl_only)

    print(f"{'方案':<24} {'笔数':>6} {'TP率':>7} {'超时率':>8} {'毛PF':>7} {'毛bp':>8} "
          f"{'净@taker':>10} | {'低波动组净':>11}")
    base = None
    for r in results:
        a, lw = r["all"], r["low_atr"]
        if not a:
            continue
        if base is None:
            base = a
        mark = "" if r["variant"].startswith("baseline") else (
            "  ✅" if a["net_taker"] > base["net_taker"] else "")
        print(f"{r['variant']:<24} {a['n']:>6} {a['tp_share']*100:>6.1f}% "
              f"{a['timeout_share']*100:>7.1f}% {str(a['gross_pf']):>7} "
              f"{a['gross_bp']:>+8.1f} {a['net_taker']*100:>+9.4f}% "
              f"| {lw.get('net_taker',0)*100:>+10.4f}%{mark}")

    better = [r for r in results if r["all"] and base
              and not r["variant"].startswith("baseline")
              and r["all"]["net_taker"] > base["net_taker"]]
    if better:
        b = max(better, key=lambda r: r["all"]["net_taker"])
        tail = ("TP率保住 = 长尾还在" if b["all"]["tp_share"] >= base["tp_share"] * 0.8
                else f"但 TP率从 {base['tp_share']*100:.1f}% 塌到 "
                     f"{b['all']['tp_share']*100:.1f}%,超时率 "
                     f"{b['all']['timeout_share']*100:.1f}% —— 障碍基本不再触发,"
                     f"这是换了策略而非改进出场,须与「无障碍纯持72根」对照读")
        verdict = (f"净最高:{b['variant']},{b['all']['net_taker']*100:+.4f}% vs 基线 "
                   f"{base['net_taker']*100:+.4f}%;{tail}")
    else:
        verdict = "没有一个波动率缩放方案胜过固定 ATR14 5:2 —— 与此前四种出场改造同一结论"
    print(f"\n判读: {verdict}")
    print("注:障碍参数属 owner 决策,本脚本只测不改。")

    (PROJECT / "analysis" / "output" / "diag_vol_scaled_barriers.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(sigs), "ewm_span": EWM_SPAN,
                    "low_atr_cut": float(lowq), "results": results,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
