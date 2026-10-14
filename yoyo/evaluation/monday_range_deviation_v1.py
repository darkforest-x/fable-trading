"""Monday-range deviation v1: the owner's weekly range system written as rules and replayed.

Owner 2026-10-14, Notion task 建立一周区间交易系统 (indicator 周一高低点区间 v1.8): wait for the
Monday high/low to form, trade only Tuesday-Friday, go long after a break below the Monday low is
reclaimed (short after a break above the high is reclaimed), enter only at key Fibonacci levels
once 5m stabilises, take 80% at the 0.618 level and 20% at the 1.13 extension outside the range,
stop at the latest 5m low. The task lists 「做一些手动回测 todo」; this is the mechanical first
pass of the rules as written, before any discretion ("当然这个不是固定的").

Rules (config.json, America/New_York like the indicator):
  range     H / L of the complete 5m bars dated Monday NY (>= 276 of 288), R = H - L
  window    Tuesday 00:00 to Saturday 00:00 NY; open positions close at the last Friday 1m close
  long      arm on the first window 5m bar with low < L, signal on the first 5m close > L at or
            after it, enter at the next 5m open, stop = lowest low from arming through the signal
            bar, TP1 = L + 0.618 R (80%), TP2 = H + 0.13 R (20%); the short side mirrors it
  per week  first signal per side; skipped when the entry is not between stop and TP1

Variants change one thing: fib_113 needs the deviation to reach the 1.13 extension first
(只在关键斐波那契点位开仓), be_after_tp1 moves the last 20% to break-even after TP1, tp1_only
exits everything at TP1, tp1_near puts TP1 at the near 0.618 (H - 0.618 R for a long, because
"止盈在0.618" can mean either line; added after v1 results, flagged post-hoc). fib_113_tp1_near
combines the two and is read against fib_113.

Exits run on the 1m path from the entry minute: stop first when stop and target print in the same
minute, an open beyond the stop fills at the open, targets fill at their price. Cost is 0.2% of
notional per round trip, charged in R as 0.002 * entry / risk. Each event gets 20 matched random
entries in the same symbol, week and side (a window 5m open up to Friday 20:00 NY) with the stop
and targets at the event's distances in percent of entry, the same Friday exit and cost.

Data: the Binance USD-M 1m archives for six majors of exp-ma6-knot-breakout-20261011-v1.
Everything decided at a 5m close uses only bars up to that close; the Monday range is complete
before the window opens.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import ma6_knot_breakout_v1 as knot
from yoyo.evaluation import market_sync_shock as v1

EXP = Path("experiments/active/exp-monday-range-deviation-20261014-v1")
CONFIG = EXP / "config.json"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT")
NY = "America/New_York"
VARIANTS = ("primary", "fib_113", "be_after_tp1", "tp1_only", "tp1_near", "fib_113_tp1_near")
MIN_MONDAY_BARS = 276
MS_5M = 300_000


def ny_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """5m complete-bucket bars with NY weekday and the NY Monday date of their week."""
    bars = knot.bars_from(raw, 1, 5).dropna()
    local = pd.to_datetime(bars.index, unit="ms", utc=True).tz_convert(NY)
    bars["dow"] = local.dayofweek
    bars["week"] = (local.normalize() - pd.to_timedelta(local.dayofweek, unit="D")).tz_localize(None)
    return bars


def signal(lo: np.ndarray, hi: np.ndarray, cl: np.ndarray, level: float, depth: float, side: int):
    """First reclaim after a break of ``level``; returns (signal bar, deviation extreme) or None.

    Long (side 1): arm on low < level - depth, signal on close > level. Short mirrors it.
    ``depth`` is 0 for primary and 0.13 R for fib_113. Everything uses bars up to the signal close.
    """
    broke = lo < level - depth if side == 1 else hi > level + depth
    if not broke.any():
        return None
    arm = int(np.argmax(broke))
    back = cl[arm:] > level if side == 1 else cl[arm:] < level
    if not back.any():
        return None
    sig = arm + int(np.argmax(back))
    extreme = lo[arm:sig + 1].min() if side == 1 else hi[arm:sig + 1].max()
    return sig, float(extreme)


def exit_r(h: np.ndarray, l: np.ndarray, o: np.ndarray, c: np.ndarray, entry: float, stop: float,
           tp1: float, tp2: float, side: int, variant: str) -> tuple[float, str]:
    """Gross R of one trade on the 1m path from the entry minute to the Friday close."""
    risk = (entry - stop) * side
    sgn = side
    hit_stop = (l <= stop) if side == 1 else (h >= stop)
    hit_tp1 = (h >= tp1) if side == 1 else (l <= tp1)
    i_stop = int(np.argmax(hit_stop)) if hit_stop.any() else len(c)
    i_tp1 = int(np.argmax(hit_tp1)) if hit_tp1.any() else len(c)
    if i_stop <= i_tp1 and i_stop < len(c):
        fill = o[i_stop] if (o[i_stop] - stop) * sgn < 0 else stop
        return (fill - entry) * sgn / risk, "stop"
    if i_tp1 >= len(c):
        return (c[-1] - entry) * sgn / risk, "time"
    r1 = (tp1 - entry) * sgn / risk
    if variant == "tp1_only":
        return r1, "tp1"
    rest_stop = entry if variant == "be_after_tp1" else stop
    k = i_tp1 + 1  # the rest is managed from the next minute; same-minute TP2 is not credited
    h2, l2, o2, c2 = h[k:], l[k:], o[k:], c[k:]
    if not len(c2):
        return 0.8 * r1 + 0.2 * (c[-1] - entry) * sgn / risk, "tp1_time"
    hs = (l2 <= rest_stop) if side == 1 else (h2 >= rest_stop)
    ht = (h2 >= tp2) if side == 1 else (l2 <= tp2)
    j_s = int(np.argmax(hs)) if hs.any() else len(c2)
    j_t = int(np.argmax(ht)) if ht.any() else len(c2)
    if j_s <= j_t and j_s < len(c2):
        fill = o2[j_s] if (o2[j_s] - rest_stop) * sgn < 0 else rest_stop
        return 0.8 * r1 + 0.2 * (fill - entry) * sgn / risk, "tp1_stop"
    if j_t < len(c2):
        return 0.8 * r1 + 0.2 * (tp2 - entry) * sgn / risk, "tp2"
    return 0.8 * r1 + 0.2 * (c2[-1] - entry) * sgn / risk, "tp1_time"


def scan_symbol(args: tuple) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbol, cfg = args
    start, end = pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"])
    raw = knot.read_1m(symbol, start - pd.Timedelta(days=1), end)
    m1 = raw.set_index("ts")
    bars = ny_frame(raw)
    cost = cfg["round_trip_cost"]
    rng = np.random.default_rng(cfg["controls"]["seed"] + sum(map(ord, symbol)))
    rows, weeks = [], []
    for week, wk in bars.groupby("week", sort=True):
        if week < start.tz_convert(NY).tz_localize(None).normalize():
            continue
        mon = wk.loc[wk.dow.eq(0)]
        win = wk.loc[wk.dow.between(1, 4)]
        if len(mon) < MIN_MONDAY_BARS or win.empty or win.index[-1] + MS_5M > end.value // 10**6:
            continue
        hi_m, lo_m = float(mon.high.max()), float(mon.low.min())
        rng_m = hi_m - lo_m
        if rng_m <= 0:
            continue
        path = m1.loc[win.index[0]:win.index[-1] + MS_5M - 1]
        p_ts = path.index.to_numpy()
        ph, pl, po, pc = (path[k].to_numpy(float) for k in ("high", "low", "open", "close"))
        lo, hi, cl, op = (win[k].to_numpy(float) for k in ("low", "high", "close", "open"))
        t5 = win.index.to_numpy()
        wd = wk.loc[wk.dow.le(4)]  # Monday-Friday: which weekday printed the week's high / low
        dow = win.dow.to_numpy()
        hour = pd.to_datetime(t5, unit="ms", utc=True).tz_convert(NY).hour.to_numpy()
        ctrl_pool = np.flatnonzero((dow < 4) | ((dow == 4) & (hour < 20)))
        weeks.append({"symbol": symbol, "week": week.date().isoformat(), "range_pct": rng_m / lo_m,
                      "broke_low": bool((lo < lo_m).any()), "broke_high": bool((hi > hi_m).any()),
                      "high_dow": int(wk.dow.loc[wd.high.idxmax()]), "low_dow": int(wk.dow.loc[wd.low.idxmin()])})
        for side in (1, -1):
            level = lo_m if side == 1 else hi_m
            tp2 = hi_m + 0.13 * rng_m if side == 1 else lo_m - 0.13 * rng_m
            for variant in VARIANTS:
                # "止盈在0.618" is ambiguous: the far 0.618 (L + 0.618 R for a long) or the near one
                # drawn from the other end (H - 0.618 R = L + 0.382 R); tp1_near tests the latter.
                r1 = 0.382 if variant.endswith("tp1_near") else 0.618
                tp1 = lo_m + r1 * rng_m if side == 1 else hi_m - r1 * rng_m
                depth = 0.13 * rng_m if variant.startswith("fib_113") else 0.0
                found = signal(lo, hi, cl, level, depth, side)
                if found is None:
                    continue
                sig, stop = found
                if sig + 1 >= len(win):
                    continue
                e_ts, entry = int(t5[sig + 1]), float(op[sig + 1])
                if not ((entry - stop) * side > 0 and (tp1 - entry) * side > 0):
                    continue
                k0 = int(np.searchsorted(p_ts, e_ts))
                gross, how = exit_r(ph[k0:], pl[k0:], po[k0:], pc[k0:], entry, stop, tp1, tp2, side, variant)
                risk_pct = (entry - stop) * side / entry
                d1, d2 = (tp1 - entry) * side / entry, (tp2 - entry) * side / entry
                ctrl = []
                for ci in rng.choice(ctrl_pool, size=cfg["controls"]["n"], replace=True):
                    c_ts, c_entry = int(t5[ci]), float(op[ci])
                    c_stop = c_entry * (1 - side * risk_pct)
                    c_tp1, c_tp2 = c_entry * (1 + side * d1), c_entry * (1 + side * d2)
                    j0 = int(np.searchsorted(p_ts, c_ts))
                    g, _ = exit_r(ph[j0:], pl[j0:], po[j0:], pc[j0:], c_entry, c_stop, c_tp1, c_tp2, side, variant)
                    ctrl.append(g - cost / risk_pct)
                rows.append({
                    "symbol": symbol, "week": week.date().isoformat(), "side": "long" if side == 1 else "short",
                    "variant": variant, "signal_time": pd.Timestamp(int(t5[sig]) + MS_5M, unit="ms", tz="UTC").isoformat(),
                    "entry_time": pd.Timestamp(e_ts, unit="ms", tz="UTC").isoformat(),
                    "entry_dow": int(win.dow.iloc[sig + 1]), "monday_high": hi_m, "monday_low": lo_m,
                    "range_pct": rng_m / lo_m, "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
                    "risk_pct": risk_pct, "tp1_r": d1 / risk_pct, "gross_r": gross, "cost_r": cost / risk_pct,
                    "net_r": gross - cost / risk_pct, "net_pct": gross * risk_pct - cost, "exit": how,
                    "control_net_r": float(np.mean(ctrl)), "control_net_pct": float(np.mean(ctrl)) * risk_pct})
    return pd.DataFrame(rows), pd.DataFrame(weeks)


def summarize(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    split = pd.Timestamp(cfg["select_before"])
    t = trades.assign(period=np.where(pd.to_datetime(trades.entry_time) < split, "2023-2024", "2025-2026"),
                      month=pd.to_datetime(trades.entry_time).dt.strftime("%Y-%m"),
                      excess=trades.net_r - trades.control_net_r)
    out = []
    for (variant, side, period), g in t.groupby(["variant", "side", "period"], sort=True):
        out.append({"variant": variant, "side": side, "period": period, "events": len(g),
                    "win_rate": float((g.net_r > 0).mean()), "mean_net_r": float(g.net_r.mean()),
                    "mean_gross_r": float(g.gross_r.mean()), "cost_r": float(g.cost_r.mean()),
                    "median_risk_pct": float(g.risk_pct.median()), "mean_net_pct": float(g.net_pct.mean()),
                    "control_net_r": float(g.control_net_r.mean()), "excess_r": float(g.excess.mean()),
                    "p_excess": v1.month_block_sign_flip(g.excess.to_numpy(), g.month.to_numpy(),
                                                          cfg["stat_seed"], cfg["flips"]),
                    "tp1_rate": float(g.exit.isin(["tp1", "tp1_stop", "tp1_time", "tp2"]).mean()),
                    "tp2_rate": float(g.exit.eq("tp2").mean()), "stop_rate": float(g.exit.eq("stop").mean())})
    return pd.DataFrame(out)


def run(output: Path, *, limit: int | None = None, workers: int = 6) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_monday_range_deviation_v1.py"))
    if limit is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    began = time.perf_counter()
    symbols = SYMBOLS[:limit] if limit else SYMBOLS
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(scan_symbol, [(s, cfg) for s in symbols], chunksize=1))
    trades = pd.concat([p for p, _ in parts if len(p)], ignore_index=True)
    weeks = pd.concat([w for _, w in parts if len(w)], ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    weeks.to_csv(output / "weeks.csv", index=False)
    summary = summarize(trades, cfg)
    summary.to_csv(output / "summary.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "limit": limit, "symbols": list(symbols),
        "trade_rows": len(trades), "symbol_weeks": len(weeks),
        "broke_low_rate": float(weeks.broke_low.mean()), "broke_high_rate": float(weeks.broke_high.mean()),
        "week_high_dow": weeks.high_dow.value_counts(normalize=True).sort_index().round(3).to_dict(),
        "week_low_dow": weeks.low_dow.value_counts(normalize=True).sort_index().round(3).to_dict(),
        "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1, ensure_ascii=False))
    cols = ["variant", "side", "period", "events", "win_rate", "mean_net_r", "mean_gross_r", "cost_r",
            "median_risk_pct", "control_net_r", "excess_r", "p_excess", "tp1_rate", "stop_rate"]
    print(summary[cols].round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    run(args.output, limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
