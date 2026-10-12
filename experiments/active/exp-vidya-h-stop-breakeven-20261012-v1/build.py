"""Stop and breakeven variants for VIDYA trend-line retest configuration H (owner request 2026-10-12).

Question: on H's own entries (ETHUSDT perpetual 15m, Binance USD-M archives 2022-01-01 to 2026-10-10),
does a different stop placement, a breakeven / profit-lock stop, a trend-line trailing stop or a time
stop improve the result after costs, and does any improvement hold in both periods (split 2024-10-01),
on BTC/SOL and against matched random entries that use the same exit rule?

Single variable: entries come from yoyo/monitor/vidya_retest.py with H's parameters (strategy tl-2r);
only the exit rule changes. The primary table keeps H's entries fixed and compares exits trade by trade
(paired difference, bootstrap interval). A sequential re-run then lets earlier exits free signals that
H skipped because a position was open.

Bar-data conventions (no intrabar path is available):
- stop first: a bar touching both the stop and the target exits at the stop;
- a stop that moves (breakeven, lock, trail) moves at a bar's close and is live from the next bar;
  the trigger is that bar's high for longs / low for shorts, or its close where the variant says so;
- a stop fills at min(open, stop) for longs (max for shorts), so a gap through a moved stop fills at
  the open; a target fills at the target price; stop and time exits pay the basis slippage.
Bases: report (confirming-close fill, 0.05% per side plus 0.01% slippage; H reproduces 53 trades
+0.381R) and repo standard (next-open fill, 20bp round trip; H reproduces +0.296R). The script aborts
unless both baselines reproduce the engine exactly.

Matched random control: for every H trade, 20 random entries in the same month and trend side with the
variant's own stop distance (percent of entry) and target distance, run through the same exit rule and
costs (100 per trade, seeded, identical draws for every variant).

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-vidya-h-stop-breakeven-20261012-v1/build.py
Output: results.json next to this file.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from yoyo.monitor.vidya_retest import STRATEGIES, State, Strategy, indicator

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[2] / "data/vidya_retest"
H = STRATEGIES["tl-2r"]["params"]
WARMUP = 1000
SPLIT_MS = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
BASES = {"report": dict(fill="close", cost=0.001, slip=0.0001), "standard": dict(fill="next_open", cost=0.002, slip=0.0)}
EXPECT = {"report": (53, 0.381), "standard": (53, 0.296)}
DRAWS, RESAMPLES, BOOT = 100, 2000, 5000
SEED = 20261012


@dataclass(frozen=True)
class Exit:
    key: str
    label: str
    group: str
    stop: str = "candle"          # "candle": touch/confirm extreme -/+ k ATR(14); "line": trend line at the signal bar -/+ k ATR(14)
    k: float = 1.0
    rr: Optional[float] = 2.0     # target as a multiple of the initial risk; None = no target
    keep_h_target: bool = False   # target stays at H's own 2R price, so only the stop moves
    locks: Tuple = ()             # ((trigger_R, new_stop_R or "fee"), ...); "fee" = the price where net R is 0
    trigger: str = "extreme"      # lock trigger: bar high/low ("extreme") or bar close ("close")
    trail_line: bool = False      # stop follows the trend line while the trend holds
    time_bars: Optional[int] = None


EXITS = [
    Exit("base", "H 原样：K 线极值外 1 ATR，2R 止盈", "基准"),
    Exit("k0.3", "止损缓冲 0.3 ATR", "止损位置（止盈跟着 2R）", k=0.3),
    Exit("k0.5", "止损缓冲 0.5 ATR", "止损位置（止盈跟着 2R）", k=0.5),
    Exit("k0.75", "止损缓冲 0.75 ATR", "止损位置（止盈跟着 2R）", k=0.75),
    Exit("k1.5", "止损缓冲 1.5 ATR", "止损位置（止盈跟着 2R）", k=1.5),
    Exit("line0.5", "止损放在趋势线外 0.5 ATR", "止损位置（止盈跟着 2R）", stop="line", k=0.5),
    Exit("line1.0", "止损放在趋势线外 1 ATR", "止损位置（止盈跟着 2R）", stop="line", k=1.0),
    Exit("k0.5-ht", "止损缓冲 0.5 ATR，止盈价不变", "止损位置（止盈价不变）", k=0.5, keep_h_target=True),
    Exit("k1.5-ht", "止损缓冲 1.5 ATR，止盈价不变", "止损位置（止盈价不变）", k=1.5, keep_h_target=True),
    Exit("be0.5", "浮盈 0.5R 移到保本", "保本", locks=((0.5, 0.0),)),
    Exit("be0.75", "浮盈 0.75R 移到保本", "保本", locks=((0.75, 0.0),)),
    Exit("be1.0", "浮盈 1R 移到保本", "保本", locks=((1.0, 0.0),)),
    Exit("be1.25", "浮盈 1.25R 移到保本", "保本", locks=((1.25, 0.0),)),
    Exit("be1.5", "浮盈 1.5R 移到保本", "保本", locks=((1.5, 0.0),)),
    Exit("be1.0-fee", "浮盈 1R 移到保本 + 手续费", "保本", locks=((1.0, "fee"),)),
    Exit("be1.0-close", "收盘浮盈 1R 才移到保本", "保本", locks=((1.0, 0.0),), trigger="close"),
    Exit("lock1.5", "浮盈 1.5R 锁住 +0.5R", "锁利", locks=((1.5, 0.5),)),
    Exit("lock-step", "1R 保本，1.5R 锁 +0.5R", "锁利", locks=((1.0, 0.0), (1.5, 0.5))),
    Exit("trail-line", "止损跟着趋势线走，2R 止盈", "跟踪止损", trail_line=True),
    Exit("trail-line-run", "止损跟着趋势线走，不设止盈", "跟踪止损", trail_line=True, rr=None),
    Exit("time48", "12 小时没结果就收盘离场", "时间止损", time_bars=48),
    Exit("time96", "24 小时没结果就收盘离场", "时间止损", time_bars=96),
]


def load(sym):
    df = pd.read_csv(DATA / f"binance_um_{sym}USDT_15m.csv", parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    df["ms"] = (df["time"].astype("int64") // 1_000_000).astype("int64")
    return df


def prepare(df):
    """Indicator, every delta-passing confirmed retest (taken or skipped for an open position), H's exits."""
    bars = {k: df[c].to_numpy(float) for k, c in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}
    ind = indicator(bars["o"], bars["h"], bars["l"], bars["c"], df["volume"].to_numpy(float),
                    length=H.length, momentum=H.momentum, distance=H.distance)
    engine: dict = {}
    cands: list = []
    for name, b in BASES.items():
        strat = Strategy(H, cost=float(b["cost"]), fill=str(b["fill"]), slip=float(b["slip"]))
        st, found, exits = State(), [], []
        for i in range(WARMUP, len(df)):
            for ev in strat.on_bar(i, bars, ind, st):
                if ev["kind"] == "signal" or (ev["kind"] == "skip" and ev["reason"] == "position_open"):
                    found.append(dict(i=ev["index"], side=ev["side"], ti=ev["touch_index"], level=ev["level"]))
                elif ev["kind"] == "exit":
                    exits.append(ev)
        engine[name] = exits
        if name == "report":  # signal detection does not depend on the fill, so one candidate list serves both bases
            cands = found
    return dict(bars=bars, ind=ind, ms=df["ms"].to_numpy(), month=df["time"].dt.strftime("%Y-%m").to_numpy(),
                cands=cands, engine=engine, n=len(df))


def be_price(entry, sgn, cost, slip):
    """Exit price at which the net result (after round-trip cost and stop slippage) is zero."""
    if sgn > 0:
        return entry * (1 + cost / 2) / ((1 - cost / 2) * (1 - slip))
    return entry * (1 - cost / 2) / ((1 + cost / 2) * (1 + slip))


def simulate(P, cand, ex, basis, override=None):
    """One trade from one candidate under one exit rule. ``override`` = (entry_index, stop_pct, target_pct) for random entries."""
    o, h, l, c = (P["bars"][k] for k in "ohlc")
    line, up, atr = P["ind"]["line"], P["ind"]["up"], P["ind"]["atr14"]
    n, sgn = P["n"], (1 if cand["side"] == "long" else -1)
    long = sgn > 0
    cost, slip = basis["cost"], basis["slip"]
    i = cand["i"]
    if basis["fill"] == "close":
        ei, entry, start = i, c[i] * (1 + sgn * slip), i + 1
    else:
        ei = i + 1
        if ei >= n:
            return None
        entry, start = o[ei], ei
    if override is not None:
        stop = entry * (1 - sgn * override[1])
        target = entry * (1 + sgn * override[2]) if override[2] is not None else None
    else:
        a = atr[i] if np.isfinite(atr[i]) else 0.0
        ti = cand["ti"]
        ext = min(l[i], l[ti]) if long else max(h[i], h[ti])
        anchor = ext if ex.stop == "candle" else cand["level"]
        stop = anchor - sgn * ex.k * a
        if ex.keep_h_target:
            h_stop = ext - sgn * H.k_atr * a
            target = entry + 2.0 * (entry - h_stop)
        else:
            target = None if ex.rr is None else entry + sgn * ex.rr * abs(entry - stop)
    risk = (entry - stop) * sgn
    if risk <= 0:
        return None
    stop0 = stop
    locks = [(entry + sgn * trig * risk, be_price(entry, sgn, cost, slip) if lvl == "fee" else entry + sgn * lvl * risk)
             for trig, lvl in ex.locks]
    kind, mfe, mae = "stop", 0.0, 0.0
    for k in range(start, n):
        mfe = max(mfe, ((h[k] if long else l[k]) - entry) * sgn / risk)
        mae = min(mae, ((l[k] if long else h[k]) - entry) * sgn / risk)
        if (l[k] <= stop) if long else (h[k] >= stop):
            px, reason = (min(o[k], stop) if long else max(o[k], stop)) * (1 - sgn * slip), kind
        elif target is not None and ((h[k] >= target) if long else (l[k] <= target)):
            px, reason = target, "target"
        elif ex.time_bars is not None and k - ei >= ex.time_bars:
            px, reason = c[k] * (1 - sgn * slip), "time"
        else:
            fav = (c[k] if ex.trigger == "close" else (h[k] if long else l[k]))
            for trig_px, new in locks:
                if ((fav >= trig_px) if long else (fav <= trig_px)) and (new - stop) * sgn > 0:
                    stop, kind = new, "lock"
            if ex.trail_line and np.isfinite(line[k]) and bool(up[k]) == long and (line[k] - stop) * sgn > 0:
                stop, kind = line[k], "trail"
            continue
        gross = (px - entry) * sgn
        return dict(i=i, ei=ei, xi=k, side=cand["side"], entry=entry, stop0=stop0, target=target, exit=px, reason=reason,
                    risk=risk, r_gross=gross / risk, r=(gross - cost * (entry + px) / 2) / risk, mfe=mfe, mae=mae,
                    stop_pct=risk / entry, target_pct=None if target is None else abs(target - entry) / entry)
    return None  # still open at the end of the data; the engine does not count it either


def stats(rs):
    r = np.asarray(rs, float)
    if not len(r):
        return {"trades": 0}
    eq = np.cumprod(1 + 0.01 * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": int(len(r)), "win_rate": round(float((r > 0).mean() * 100), 1), "avg_r": round(float(r.mean()), 3),
            "total_r": round(float(r.sum()), 1), "pf": round(float(gains / losses), 2) if losses > 0 else None,
            "ret_pct": round(float((eq[-1] - 1) * 100), 1), "max_dd_pct": round(float((eq / peak - 1).min() * 100), 1)}


def sequential(P, ex, basis):
    """Engine-equivalent one-position run over every candidate: a candidate is taken if flat at its bar."""
    out, free = [], -1
    for cand in P["cands"]:
        if cand["i"] < free:
            continue
        t = simulate(P, cand, ex, basis)
        if t is not None:
            out.append(t)
            free = t["xi"]
    return out


def boot_ci(d, rng):
    d = np.asarray(d, float)
    means = d[rng.integers(0, len(d), size=(BOOT, len(d)))].mean(axis=1)
    return [round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)]


def draw_random(P, fixed, rng):
    """Per real trade: DRAWS random bars in the same month and trend side (identical for every variant)."""
    up, month = P["ind"]["up"], P["month"]
    draws = []
    for cand in fixed:
        i, long = cand["i"], cand["side"] == "long"
        pool = np.where((month == month[i]) & (up == long))[0]
        pool = pool[(pool != i) & (pool >= WARMUP) & (pool < P["n"] - 2)]
        draws.append(rng.choice(pool, size=min(DRAWS, len(pool)), replace=False) if len(pool) else np.array([], int))
    return draws


def random_control(P, fixed, real, ex, basis, draws, rng):
    per = []
    for cand, t, js in zip(fixed, real, draws):
        res = []
        for j in js:
            rt = simulate(P, dict(i=int(j), side=cand["side"], ti=int(j), level=np.nan), ex, basis,
                          override=(int(j), t["stop_pct"], t["target_pct"]))
            if rt is not None:
                res.append(rt["r"])
        per.append(res)
    flat = np.concatenate([np.asarray(x) for x in per if x])
    real_mean = float(np.mean([t["r"] for t in real]))
    means = np.array([np.mean([x[rng.integers(len(x))] for x in per if x]) for _ in range(RESAMPLES)])
    return {"random_avg_r": round(float(flat.mean()), 3), "excess_r": round(real_mean - float(flat.mean()), 3),
            "p_value": round(float((means >= real_mean).mean()), 4), "draws": int(len(flat))}


def main():
    rng = np.random.default_rng(SEED)
    coins = {sym: prepare(load(sym)) for sym in ("ETH", "BTC", "SOL")}
    base = EXITS[0]

    # 1) both baselines must reproduce the engine exactly, trade by trade
    for sym, P in coins.items():
        for name, b in BASES.items():
            seq = sequential(P, base, b)
            eng = P["engine"][name]
            same = len(seq) == len(eng) and all(abs(a["r"] - e["r"]) < 1e-9 and a["xi"] == e["index"] for a, e in zip(seq, eng))
            if not same:
                raise SystemExit(f"{sym} {name}: simulator does not reproduce the engine ({len(seq)} vs {len(eng)} trades)")
            if sym == "ETH" and (len(seq), round(float(np.mean([t['r'] for t in seq])), 3)) != EXPECT[name]:
                raise SystemExit(f"ETH {name} baseline changed: {stats([t['r'] for t in seq])}")
    print("baselines reproduce the engine on ETH/BTC/SOL, both bases")

    # fixed entry sets: the candidates H actually traded (report basis), per coin
    fixed = {}
    for sym, P in coins.items():
        taken = {e["signal_index"] for e in P["engine"]["report"]}
        fixed[sym] = [cd for cd in P["cands"] if cd["i"] in taken]

    # 2) H's own path diagnostics (report basis): how far losers ran first, how deep winners dipped
    P = coins["ETH"]
    hb = [simulate(P, cd, base, BASES["report"]) for cd in fixed["ETH"]]
    losers, winners = [t for t in hb if t["r"] <= 0], [t for t in hb if t["r"] > 0]
    diag = {"losers": len(losers), "winners": len(winners),
            "losers_mfe_reached": {f"{x}R": int(sum(t["mfe"] >= x for t in losers)) for x in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5)},
            "winners_mae_reached": {f"-{x}R": int(sum(t["mae"] <= -x for t in winners)) for x in (0.25, 0.5, 0.75)},
            "losers_mfe": sorted(round(t["mfe"], 2) for t in losers), "winners_mae": sorted(round(t["mae"], 2) for t in winners)}
    print("diagnostics", {k: v for k, v in diag.items() if not isinstance(v, list)})

    draws = draw_random(P, fixed["ETH"], np.random.default_rng(SEED + 1))
    ms = P["ms"]
    rows = []
    for ex in EXITS:
        row = {"key": ex.key, "label": ex.label, "group": ex.group, "spec": asdict(ex)}
        for name, b in BASES.items():
            res = {}
            for sym, Pc in coins.items():
                base_r = [simulate(Pc, cd, base, b) for cd in fixed[sym]]
                var_r = [simulate(Pc, cd, ex, b) for cd in fixed[sym]]
                keep = [k for k, (x, y) in enumerate(zip(base_r, var_r)) if x is not None and y is not None]
                pairs = [(base_r[k], var_r[k]) for k in keep]
                d = [y["r"] - x["r"] for x, y in pairs]
                entry = {"fixed": stats([y["r"] for _, y in pairs]), "delta_r": round(float(np.mean(d)), 3),
                         "delta_ci": boot_ci(d, rng),
                         "loss_to_win": int(sum(x["r"] <= 0 < y["r"] for x, y in pairs)),
                         "win_to_loss": int(sum(y["r"] <= 0 < x["r"] for x, y in pairs)),
                         "win_to_smaller_win": int(sum(0 < y["r"] < x["r"] - 0.05 for x, y in pairs))}
                if sym == "ETH":
                    vr = [y for _, y in pairs]
                    before = [y["r"] - x["r"] for x, y in pairs if Pc["ms"][y["i"]] < SPLIT_MS]
                    after = [y["r"] - x["r"] for x, y in pairs if Pc["ms"][y["i"]] >= SPLIT_MS]
                    entry["split"] = {"before": {**stats([y["r"] for x, y in pairs if Pc["ms"][y["i"]] < SPLIT_MS]), "delta_r": round(float(np.mean(before)), 3)},
                                      "after": {**stats([y["r"] for x, y in pairs if Pc["ms"][y["i"]] >= SPLIT_MS]), "delta_r": round(float(np.mean(after)), 3)}}
                    entry["reasons"] = {k: int(sum(t["reason"] == k for t in vr)) for k in ("target", "stop", "lock", "trail", "time")}
                    entry["avg_hold_h"] = round(float(np.mean([(t["xi"] - t["ei"]) * 0.25 for t in vr])), 1)
                    entry["avg_stop_pct"] = round(float(np.mean([t["stop_pct"] for t in vr]) * 100), 2)
                    seq = sequential(Pc, ex, b)
                    entry["sequential"] = stats([t["r"] for t in seq])
                    entry["control"] = random_control(Pc, [fixed[sym][k] for k in keep], vr, ex, b, [draws[k] for k in keep],
                                                      np.random.default_rng(SEED + 2))
                res[sym] = entry
            row[name] = res
        e = row["report"]["ETH"]
        print(f"{ex.key:15s} n{e['fixed']['trades']:3d} wr{e['fixed']['win_rate']:5.1f} avg{e['fixed']['avg_r']:+.3f} "
              f"d{e['delta_r']:+.3f} {e['delta_ci']} L>W {e['loss_to_win']} W>L {e['win_to_loss']} W>w {e['win_to_smaller_win']} | split d {e['split']['before']['delta_r']:+.3f}/{e['split']['after']['delta_r']:+.3f} "
              f"| BTC d{row['report']['BTC']['delta_r']:+.3f} SOL d{row['report']['SOL']['delta_r']:+.3f} | std avg{row['standard']['ETH']['fixed']['avg_r']:+.3f} "
              f"| seq n{e['sequential']['trades']} {e['sequential']['avg_r']:+.3f} ret{e['sequential']['ret_pct']:+.1f} dd{e['sequential']['max_dd_pct']} "
              f"| rnd {e['control']['random_avg_r']:+.3f} ex{e['control']['excess_r']:+.3f} p{e['control']['p_value']}")
        rows.append(row)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "params_H": asdict(H), "bases": BASES,
           "split": "2024-10-01", "seed": SEED, "diagnostics_eth_report": diag, "variants": rows,
           "data": {sym: {"bars": Pc["n"], "first": str(pd.to_datetime(Pc["ms"][0], unit="ms"))[:10],
                          "last": str(pd.to_datetime(Pc["ms"][-1], unit="ms"))[:10], "h_trades": len(fixed[sym])} for sym, Pc in coins.items()}}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("wrote", HERE / "results.json")


if __name__ == "__main__":
    main()
