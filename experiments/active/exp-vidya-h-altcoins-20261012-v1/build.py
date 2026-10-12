"""VIDYA trend-line retest configuration H on 20 liquid alts (owner request 2026-10-12, "回测其他山寨币").

Question: H was selected on ETHUSDT 15m (Length 10, delta 50, stop 1 ATR(14) beyond the touch/confirm
extremes, 2R). Does the unchanged rule make money after costs on other alts, and do its entries beat
matched random entries there?

Single variable: the market. Rules and parameters are H's (yoyo/monitor/vidya_retest.py, tl-2r), unchanged.
Universe: the 20 alts of exp-spike-v129-alts-confirm-20261011-v1 (top 20 by median daily quote volume
2022-11..2022-12). The ranking window closes 2022-12-31, so trades count from 2023-01-01 to 2026-10-10;
earlier bars are warmup only. ETH is run on the same window as a reference outside the pooled verdict.
Data: Binance USD-M 15m archives fetched by fetch.py (checksum-verified).

Bases (same as the H studies): report (confirming-close fill, 0.05% per side + 0.01% slippage) and repo
standard (next-open fill, 20bp round trip). The trade simulator and exit rules are imported from the
committed stop/breakeven study (exp-vidya-h-stop-breakeven-20261012-v1/build.py); for every coin and basis
the script aborts unless the simulator reproduces the engine trade by trade.

Matched random control: per real trade, 100 random entries on the same coin, same calendar month and same
trend side, with the trade's stop distance (percent of entry) and 2R target, same costs. Pooled statistics
use a calendar-month block bootstrap (trades in the same month across coins are not independent).

Primary criterion, fixed before the run: pooled 20 alts on the standard basis, mean net R > 0 in both
periods (split 2024-10-01) and excess over matched random > 0 with month-block p < 0.01. Per-coin rows and
the secondary exit variants (0.75 ATR stop, breakeven at 1.25R, lock at 1.5R, 24h time stop; paired on
fixed entries) are descriptive.

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-vidya-h-altcoins-20261012-v1/build.py
Output: results.json next to this file.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from yoyo.monitor.vidya_retest import State, Strategy, indicator

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[2] / "data/vidya_retest"
_spec = importlib.util.spec_from_file_location("h_stop_study", HERE.parent / "exp-vidya-h-stop-breakeven-20261012-v1/build.py")
assert _spec is not None and _spec.loader is not None
stop = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = stop  # dataclasses on Python 3.9 look the module up while building Exit
_spec.loader.exec_module(stop)

H, BASES, EXITS = stop.H, stop.BASES, {e.key: e for e in stop.EXITS}
BASE = EXITS["base"]
VARIANTS = ["k0.75", "be1.25", "lock1.5", "time96"]
ALTS = ["DOGEUSDT", "BNBUSDT", "XRPUSDT", "LTCUSDT", "SOLUSDT", "MASKUSDT", "CHZUSDT", "LINKUSDT", "APEUSDT", "ETCUSDT",
        "DYDXUSDT", "ADAUSDT", "APTUSDT", "NEARUSDT", "DOTUSDT", "AXSUSDT", "AVAXUSDT", "CRVUSDT", "GMTUSDT", "1000SHIBUSDT"]
START_MS = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
SPLIT_MS = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
MIN_WARMUP = 1000
DRAWS, BOOT, SEED = 100, 5000, 20261013


def load(symbol):
    df = pd.read_csv(DATA / f"binance_um_{symbol}_15m.csv", parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    df["ms"] = (df["time"].astype("int64") // 1_000_000).astype("int64")
    return df


def prepare(symbol) -> dict:
    """Indicator on the full history; the engine steps from 2023-01-01 (or after MIN_WARMUP bars for later listings)."""
    df = load(symbol)
    bars = {k: df[c].to_numpy(float) for k, c in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}
    ind = indicator(bars["o"], bars["h"], bars["l"], bars["c"], df["volume"].to_numpy(float),
                    length=H.length, momentum=H.momentum, distance=H.distance)
    ms = df["ms"].to_numpy()
    i0 = max(int(np.searchsorted(ms, START_MS)), MIN_WARMUP)
    engine: dict = {}
    cands: list = []
    for name, b in BASES.items():
        strat = Strategy(H, cost=float(b["cost"]), fill=str(b["fill"]), slip=float(b["slip"]))
        st, found, exits = State(), [], []
        for i in range(i0, len(df)):
            for ev in strat.on_bar(i, bars, ind, st):
                if ev["kind"] == "signal" or (ev["kind"] == "skip" and ev["reason"] == "position_open"):
                    found.append(dict(i=ev["index"], side=ev["side"], ti=ev["touch_index"], level=ev["level"]))
                elif ev["kind"] == "exit":
                    exits.append(ev)
        engine[name] = exits
        if name == "report":
            cands = found
    P = dict(bars=bars, ind=ind, ms=ms, month=df["time"].dt.strftime("%Y-%m").to_numpy(), cands=cands, engine=engine,
             n=len(df), i0=i0, first=str(df["time"].iloc[0])[:10], last=str(df["time"].iloc[-1])[:16])
    for name, b in BASES.items():  # the imported simulator must reproduce the engine exactly
        seq, eng = stop.sequential(P, BASE, b), engine[name]
        if len(seq) != len(eng) or any(abs(a["r"] - e["r"]) > 1e-9 or a["xi"] != e["index"] for a, e in zip(seq, eng)):
            raise SystemExit(f"{symbol} {name}: simulator does not reproduce the engine ({len(seq)} vs {len(eng)})")
    return P


def random_means(P, trades, basis, rng):
    """Mean net R of DRAWS matched random entries per real trade (same coin, month, trend side, stop %, target %)."""
    up, month = P["ind"]["up"], P["month"]
    out = []
    for t in trades:
        i, long = t["i"], t["side"] == "long"
        pool = np.where((month == month[i]) & (up == long))[0]
        pool = pool[(pool != i) & (pool >= P["i0"]) & (pool < P["n"] - 2)]
        res = []
        for j in (rng.choice(pool, size=min(DRAWS, len(pool)), replace=False) if len(pool) else []):
            rt = stop.simulate(P, dict(i=int(j), side=t["side"], ti=int(j), level=np.nan), BASE, basis,
                               override=(int(j), t["stop_pct"], t["target_pct"]))
            if rt is not None:
                res.append(rt["r"])
        out.append(float(np.mean(res)) if res else np.nan)
    return out


def month_block(values, months, rng):
    """Mean, 95% interval and one-sided p (share of resampled means <= 0) with calendar months as blocks."""
    v = np.asarray(values, float)
    ok = np.isfinite(v)
    v, m = v[ok], np.asarray(months)[ok]
    keys = sorted(set(m))
    sums = np.array([v[m == k].sum() for k in keys])
    cnts = np.array([(m == k).sum() for k in keys])
    idx = rng.integers(0, len(keys), size=(BOOT, len(keys)))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return {"mean": round(float(v.mean()), 3), "ci": [round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)],
            "p_le_0": round(float((means <= 0).mean()), 4), "trades": int(len(v)), "months": len(keys)}


def portfolio(trades):
    """Descriptive: all trades compounded at 1% risk in exit-time order."""
    if not trades:
        return {}
    r = np.array([t["r"] for t in sorted(trades, key=lambda t: t["exit_ms"])])
    eq = np.cumprod(1 + 0.01 * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return {"ret_pct": round(float((eq[-1] - 1) * 100), 1), "max_dd_pct": round(float((eq / peak - 1).min() * 100), 1)}


def main():
    rng = np.random.default_rng(SEED)
    rows: list = []
    pooled: dict = {name: [] for name in BASES}
    for symbol in ["ETHUSDT"] + ALTS:
        P = prepare(symbol)
        taken = {t["i"] for t in stop.sequential(P, BASE, BASES["report"])}
        fixed = [cd for cd in P["cands"] if cd["i"] in taken]  # H's report-basis entries, held fixed for the exit variants
        row: dict = {"symbol": symbol, "first_bar": P["first"], "last_bar": P["last"], "start_bar": str(pd.to_datetime(P["ms"][P["i0"]], unit="ms"))[:16]}
        for name, b in BASES.items():
            trades = stop.sequential(P, BASE, b)
            for t in trades:
                t.update(symbol=symbol, signal_ms=int(P["ms"][t["i"]]), exit_ms=int(P["ms"][t["xi"]]), month=P["month"][t["i"]])
            rnd = random_means(P, trades, b, np.random.default_rng(SEED + 1))
            for t, rm in zip(trades, rnd):
                t["random_r"] = rm
            r = [t["r"] for t in trades]
            ex = [t["r"] - t["random_r"] for t in trades if np.isfinite(t["random_r"])]
            entry: dict = {**stop.stats(r), **portfolio(trades),
                     "before": stop.stats([t["r"] for t in trades if t["signal_ms"] < SPLIT_MS]),
                     "after": stop.stats([t["r"] for t in trades if t["signal_ms"] >= SPLIT_MS]),
                     "random_avg_r": round(float(np.nanmean(rnd)), 3) if trades else None,
                     "excess_r": round(float(np.mean(ex)), 3) if ex else None}
            entry["variants"] = {}
            for key in VARIANTS:
                pairs = [(stop.simulate(P, cd, BASE, b), stop.simulate(P, cd, EXITS[key], b)) for cd in fixed]
                pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
                entry["variants"][key] = {"delta_r": round(float(np.mean([y["r"] - x["r"] for x, y in pairs])), 3) if pairs else None,
                                          "pairs": [(round(x["r"], 4), round(y["r"], 4), P["month"][x["i"]]) for x, y in pairs]}
            row[name] = entry
            if symbol != "ETHUSDT":
                pooled[name].extend(trades)
        rows.append(row)
        s, d = row["standard"], row["report"]
        print(f"{symbol:13s} from {row['start_bar'][:10]} | report n{d['trades']:3d} avg{d.get('avg_r', 0):+.3f} ex{(d['excess_r'] or 0):+.3f} "
              f"| std n{s['trades']:3d} wr{s.get('win_rate', 0):5.1f} avg{s.get('avg_r', 0):+.3f} ret{s.get('ret_pct', 0):+6.1f} dd{s.get('max_dd_pct', 0):6.1f} "
              f"pre{s['before'].get('avg_r', 0):+.3f} post{s['after'].get('avg_r', 0):+.3f} rnd{(s['random_avg_r'] or 0):+.3f} ex{(s['excess_r'] or 0):+.3f}", flush=True)

    summary: dict = {}
    for name in BASES:
        tr: list = pooled[name]
        months = [t["month"] for t in tr]
        ex = [t["r"] - t["random_r"] for t in tr]
        summary[name] = {
            **stop.stats([t["r"] for t in tr]), "portfolio": portfolio(tr),
            "coins_positive": int(sum(1 for row in rows[1:] if row[name].get("avg_r", 0) > 0)),
            "net_r": month_block([t["r"] for t in tr], months, rng),
            "random_avg_r": round(float(np.nanmean([t["random_r"] for t in tr])), 3),
            "excess": month_block(ex, months, rng),
            "before": {"net_r": month_block([t["r"] for t in tr if t["signal_ms"] < SPLIT_MS], [t["month"] for t in tr if t["signal_ms"] < SPLIT_MS], rng),
                       "excess": month_block([e for e, t in zip(ex, tr) if t["signal_ms"] < SPLIT_MS], [t["month"] for t in tr if t["signal_ms"] < SPLIT_MS], rng)},
            "after": {"net_r": month_block([t["r"] for t in tr if t["signal_ms"] >= SPLIT_MS], [t["month"] for t in tr if t["signal_ms"] >= SPLIT_MS], rng),
                      "excess": month_block([e for e, t in zip(ex, tr) if t["signal_ms"] >= SPLIT_MS], [t["month"] for t in tr if t["signal_ms"] >= SPLIT_MS], rng)},
            "long": stop.stats([t["r"] for t in tr if t["side"] == "long"]), "short": stop.stats([t["r"] for t in tr if t["side"] == "short"]),
            "variants": {key: month_block([y - x for row in rows[1:] for x, y, _ in row[name]["variants"][key]["pairs"]],
                                          [mo for row in rows[1:] for _, _, mo in row[name]["variants"][key]["pairs"]], rng) for key in VARIANTS},
        }
        sm = summary[name]
        print(f"POOLED {name}: n{sm['trades']} wr{sm['win_rate']} avg{sm['avg_r']:+.3f} {sm['net_r']['ci']} | coins+ {sm['coins_positive']}/20 "
              f"| pre {sm['before']['net_r']['mean']:+.3f} post {sm['after']['net_r']['mean']:+.3f} | rnd {sm['random_avg_r']:+.3f} "
              f"excess {sm['excess']['mean']:+.3f} {sm['excess']['ci']} p{sm['excess']['p_le_0']} (pre {sm['before']['excess']['mean']:+.3f} "
              f"post {sm['after']['excess']['mean']:+.3f}) | long {sm['long'].get('avg_r')} short {sm['short'].get('avg_r')} | port {sm['portfolio']} "
              f"| variants " + " ".join(f"{k} {v['mean']:+.3f}{v['ci']}" for k, v in sm["variants"].items()))
    std = summary["standard"]
    verdict = {"criterion": "pooled alts, standard basis: mean net R > 0 in both periods and month-block excess p < 0.01",
               "net_positive_both_periods": bool(std["before"]["net_r"]["mean"] > 0 and std["after"]["net_r"]["mean"] > 0),
               "excess_p": std["excess"]["p_le_0"]}
    verdict["passed"] = bool(verdict["net_positive_both_periods"] and std["excess"]["mean"] > 0 and std["excess"]["p_le_0"] < 0.01)
    for row in rows:
        for name in BASES:
            for v in row[name]["variants"].values():
                v.pop("pairs")
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "params_H": stop.asdict(H), "bases": BASES,
           "window": ["2023-01-01", "2026-10-10"], "split": "2024-10-01", "universe": ALTS, "seed": SEED,
           "verdict": verdict, "pooled": summary, "coins": rows}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("verdict", verdict)
    print("wrote", HERE / "results.json")


if __name__ == "__main__":
    main()
