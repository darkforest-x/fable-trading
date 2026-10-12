"""Five single-variable entry filters on top of frozen SPIKE V12.10 15m (owner 2026-10-12, 「都试试」).

Each variant adds exactly one admission condition (config.json "filters") to V12.10's admitted signals and
reruns the serial V12.10/V12.9 exit ledger, so a rejected signal releases occupancy. Everything else is
imported unchanged: V12.9 15m facts and the serial engine (spike_v128_recent), the VIDYA gate, event
construction and metrics (exp-spike-v1210-volumatic-vidya-gate-20261012-v1/build.py), OKX ETH data loading
(exp-spike-v1210-eth15m-5y-20261012-v1/build.py) and the frozen alt 5m series (spike_v129_alts_confirm).

Filter features use only the signal bar and earlier bars: SMA(800) of 15m closes, the 96-bar efficiency
ratio, bars since the VIDYA state flipped, distance to the VIDYA trend line and the signal bar's range, both
in ATR14 (Wilder RMA). Thresholds came from in-sample ETH terciles and are frozen in config.json before any
filtered result exists, so ETH is descriptive and the 20 alts are the out-of-sample test.

Random control (docs/learnings/trend-filter-controls-must-match-the-gate-state.md): one deterministic draw per
trade from bars with the same symbol, side, UTC week, fold and ATR/close bin, the same VIDYA state and the
same filter condition true, replayed through the same exit engine and cost.

Checks (fail closed): V12.10 closed trades equal the saved ETH five-year ledger and the parent's alt ledgers.

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-filters-20261012-v1/build.py [--workers 6]
Output: results_v1/summary.json, results_v1/trades.csv.gz, results_v1/controls.csv.gz
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as replay
from yoyo.evaluation import spike_v129_alts_confirm as prior_alts

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
OUT = HERE / "results_v1"
MIN = 15
FILTERS = list(CFG["filters"])
VARIANTS = ["V12.10", *FILTERS]
ETH5Y = ROOT / "experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1"
PARENT = ROOT / "experiments/active/exp-spike-v1210-volumatic-vidya-gate-20261012-v1"
FIELDS = ("side", "entry_time", "entry_price", "initial_stop", "exit_time", "exit_price", "exit_reason", "net_r")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v1210 = _load("v1210_gate_study", PARENT / "build.py")
eth5y = _load("v1210_eth15m_5y", ETH5Y / "build.py")


def rma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)):
        out[i] = out[i - 1] + (x[i] - out[i - 1]) / n
    return out


def filter_masks(frame: pd.DataFrame, trend: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """(ok for longs, ok for shorts) per filter, from the signal bar and earlier bars only."""
    o, h, l, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    pc = np.r_[c[0], c[:-1]]
    atr = rma(np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))), 14)
    sma800 = pd.Series(c).rolling(800).mean().to_numpy()
    step = np.abs(np.diff(c, prepend=c[0]))
    path = pd.Series(step).rolling(96).sum().to_numpy()
    move = np.abs(c - np.r_[np.full(96, np.nan), c[:-96]])
    er96 = np.where(path > 0, move / path, np.nan)
    up = np.asarray(trend["up"], bool)
    flips = np.where(np.r_[True, up[1:] != up[:-1]], np.arange(len(up)), 0)
    age = np.arange(len(up)) - np.maximum.accumulate(flips)
    line = np.where(up, trend["lower"], trend["upper"])
    with np.errstate(invalid="ignore", divide="ignore"):
        dist_long, dist_short = (c - line) / atr, (line - c) / atr
        rng = (h - l) / atr
    both = lambda m: (np.nan_to_num(m, nan=0).astype(bool), np.nan_to_num(m, nan=0).astype(bool))
    with np.errstate(invalid="ignore"):
        return {
            "F1_trend8d": (c - sma800 > 0, c - sma800 < 0),
            "F2_vidya_fresh": both(age <= 4),
            "F3_not_chop": both(er96 >= 0.05),
            "F4a_near_vidya": (dist_long <= 4.5, dist_short <= 4.5),
            "F4b_no_big_bar": both(rng <= 1.75),
        }


def controls(prepared, trades: list[dict], window: dict, trend: dict, ok: tuple[np.ndarray, np.ndarray] | None) -> pd.DataFrame:
    """Same symbol/side/UTC week/fold/ATR-close bin, same VIDYA state and the same filter condition."""
    if not trades:
        return pd.DataFrame(columns=["trade_key", "control_net_r", "matched", "reason"])
    frame = prepared.frame
    close_time = frame.index + pd.Timedelta(minutes=MIN)
    week = np.asarray(close_time.strftime("%G-W%V"))
    fold = np.where(close_time < pd.Timestamp(window["split"]), "earlier", "later")
    bins = np.searchsorted(np.asarray(CFG["vol_bins"], float), prepared.atr / prepared.close, side="left")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    valid = (ready & trend["ready"] & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close)
             & (prepared.close > 0) & ~prepared.gap & (close_time >= pd.Timestamp(window["start"])) & (close_time < pd.Timestamp(window["end"])))
    pools: dict = {}
    cache: dict = {}
    rows = []
    for t in trades:
        i, side = int(t["signal_i"]), int(t["side"])
        key = (side, str(week[i]), str(fold[i]), int(bins[i]))
        if key not in pools:
            regime = trend["up"] if side == 1 else ~trend["up"]
            elig = np.ones(len(frame), bool) if ok is None else (ok[0] if side == 1 else ok[1])
            pools[key] = np.flatnonzero(valid & regime & elig & (week == key[1]) & (fold == key[2]) & (bins == key[3]))
        choices = pools[key][pools[key] != i]
        chosen = None
        if len(choices):
            token = f"{CFG['control_seed']}|{t['trade_key']}"
            chosen = int(choices[int(hashlib.sha256(token.encode()).hexdigest(), 16) % len(choices)])
        status, result = "empty_stratum", None
        if chosen is not None:
            if (chosen, side) not in cache:
                cache[(chosen, side)] = replay.attempt(prepared, chosen, side)
            status, result = cache[(chosen, side)]
        matched = result is not None and status == "closed" and not bool(t.get("censored", False))
        rows.append({"trade_key": t["trade_key"], "matched": bool(matched),
                     "reason": "matched" if matched else ("target_censored" if bool(t.get("censored", False)) else status),
                     "control_net_r": float(result["net_r"]) if matched else math.nan})
    return pd.DataFrame(rows)


def run_case(case: str, base5m: pd.DataFrame, venue: str, symbol: str, asset: str, tick: float,
             start: pd.Timestamp, split: pd.Timestamp, end: pd.Timestamp, alt: bool) -> dict:
    bars, _ = replay.complete_bars(base5m, MIN)
    facts = prior_alts.facts_for(base5m, asset, tick, MIN) if alt else replay.facts_for(bars, base5m, asset, tick, MIN)
    frame = facts["frame"]
    identity = {"venue": venue, "symbol": symbol, "asset": asset, "timeframe_min": MIN}
    key = f"{venue}:{symbol}:{MIN}m"
    prepared = replay.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, MIN, tick)
    events = v1210._make_events(facts, frame, identity, key, MIN, start, end)
    trend = v1210.volumatic_trend(frame)
    gated, _ = v1210._condition(events, trend)
    masks = filter_masks(frame, trend)
    window = {"start": str(start), "split": str(split), "end": str(end)}
    out: dict = {"case": case, "symbol": symbol, "variants": {}}
    for v in VARIANTS:
        ok = None if v == "V12.10" else masks[v]
        evs = gated if ok is None else [e for e in gated if (ok[0] if int(e["side"]) == 1 else ok[1])[int(e["signal_i"])]]
        trades, _ = replay.serial(prepared, evs, "ordinary", key)
        for t in trades:
            t["variant"], t["case"] = v, case
        ctl = controls(prepared, trades, window, trend, ok)
        out["variants"][v] = {"trades": pd.DataFrame(trades), "controls": ctl,
                              "candidates": sum(1 for e in evs if e["in_window"])}
    return out


def alt_job(symbol: str) -> dict:
    acfg = CFG["cases"]["alts"]
    base = prior_alts.base5m(symbol, prior_alts.config())
    meta = replay.source.symbol_meta()[symbol]
    start, end = (pd.Timestamp(x) for x in acfg["window"])
    return run_case("alts", base, "binance", symbol, str(meta["asset"]), float(meta["tick"]), start, pd.Timestamp(acfg["split"]), end, True)


def parity(ours: pd.DataFrame, saved: pd.DataFrame, label: str) -> dict:
    a = saved.loc[~saved.censored.astype(bool)].set_index("trade_key").sort_index()
    b = ours.loc[~ours.censored.astype(bool)].set_index("trade_key").sort_index()
    if set(a.index) != set(b.index):
        raise SystemExit(f"{label} parity failed: missing {sorted(set(a.index) - set(b.index))[:3]} extra {sorted(set(b.index) - set(a.index))[:3]}")
    a, b = a[list(FIELDS)].copy(), b[list(FIELDS)].copy()
    for col in ("entry_time", "exit_time"):
        a[col], b[col] = pd.to_datetime(a[col], utc=True), pd.to_datetime(b[col], utc=True)
    for col in ("entry_price", "initial_stop", "exit_price", "net_r"):
        np.testing.assert_allclose(a[col].to_numpy(float), b[col].to_numpy(float), rtol=1e-10, atol=1e-10, err_msg=f"{label} {col}")
    if not a[["side", "entry_time", "exit_time", "exit_reason"]].equals(b[["side", "entry_time", "exit_time", "exit_reason"]]):
        raise SystemExit(f"{label} parity failed on identity fields")
    return {"label": label, "closed_trades": int(len(a)), "exact": True}


def metric(trades: pd.DataFrame, ctl: pd.DataFrame, case: str) -> dict:
    ccfg = CFG["cases"]["alts" if case == "alts" else case]
    start, end = (pd.Timestamp(x) for x in ccfg["window"])
    return v1210._metric(trades, ctl, start, pd.Timestamp(ccfg["split"]), end, int(CFG["stat_seed"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    results = []
    ec = CFG["cases"]["eth_okx"]
    base5m, _ = eth5y.okx_base5m()
    results.append(run_case("eth_okx", base5m, "okx", "ETH_USDT_SWAP", "ETH", 0.01, pd.Timestamp(ec["window"][0]),
                            pd.Timestamp(ec["split"]), pd.Timestamp(ec["window"][1]), False))
    bc = CFG["cases"]["eth_binance"]
    bbase, _, bend = eth5y.binance_base5m()
    results.append(run_case("eth_binance", bbase, "binance", "ETHUSDT", "ETH", 0.01, pd.Timestamp(bc["window"][0]),
                            pd.Timestamp(bc["split"]), min(bend, pd.Timestamp(bc["window"][1])), False))
    print("eth done", f"{time.time() - t0:.0f}s", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for r in pool.map(alt_job, CFG["cases"]["alts"]["symbols"]):
            results.append(r)
            print("alt", r["symbol"], {v: len(x["trades"]) for v, x in r["variants"].items()}, f"{time.time() - t0:.0f}s", flush=True)

    checks = [parity(results[0]["variants"]["V12.10"]["trades"], pd.read_csv(ETH5Y / "results_v1/okx_vidya_gate_trades.csv.gz"), "ETH OKX V12.10 five-year")]
    saved = pd.read_csv(PARENT / "results_v1/vidya_gate_trades.csv.gz")
    saved = saved.loc[(saved.venue == "binance") & (saved.timeframe_min == MIN)]
    alt_v1210 = pd.concat([r["variants"]["V12.10"]["trades"] for r in results if r["case"] == "alts"], ignore_index=True)
    checks.append(parity(alt_v1210, saved, "20 alts V12.10 15m"))
    print("parity", checks, flush=True)

    summary: dict = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "git_head": subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(), "config": CFG, "parity": checks,
        "eth": {}, "alts_pooled": {}, "alts_per_coin": {}, "verdict": {}}
    all_trades, all_ctl = [], []
    for case in ("eth_okx", "eth_binance"):
        r = next(x for x in results if x["case"] == case)
        summary["eth"][case] = {v: {**metric(x["trades"], x["controls"], case), "candidates": x["candidates"]} for v, x in r["variants"].items()}
    alts = [r for r in results if r["case"] == "alts"]
    for v in VARIANTS:
        tr = pd.concat([r["variants"][v]["trades"] for r in alts], ignore_index=True)
        ct = pd.concat([r["variants"][v]["controls"] for r in alts], ignore_index=True)
        summary["alts_pooled"][v] = {**metric(tr, ct, "alts"), "candidates": sum(r["variants"][v]["candidates"] for r in alts)}
        summary["alts_per_coin"][v] = {r["symbol"]: metric(r["variants"][v]["trades"], r["variants"][v]["controls"], "alts")["periods"].get("full", {}).get("mean_net_r") for r in alts}
    for r in results:
        for v, x in r["variants"].items():
            all_trades.append(x["trades"])
            all_ctl.append(x["controls"].assign(variant=v, case=r["case"], symbol=r["symbol"]))
    base = summary["alts_pooled"]["V12.10"]["periods"]
    for v in FILTERS:
        p = summary["alts_pooled"][v]["periods"]
        improves = all((p[k].get("mean_net_r") or -9) > (base[k].get("mean_net_r") or -9) for k in ("before_split", "from_split"))
        passes = improves and all((p[k].get("excess_mean_r") or -9) > 0 and (p[k].get("excess_month_block_p") or 1) < 0.01 for k in ("before_split", "from_split"))
        summary["verdict"][v] = {"improves_v1210_both_alt_periods": bool(improves), "passes_random_bar_both_alt_periods": bool(passes)}
    for v in VARIANTS:
        e = summary["eth"]["eth_okx"][v]["periods"]
        a = summary["alts_pooled"][v]["periods"]
        f = lambda d: f"n{d.get('closed', 0):4d} {d.get('mean_net_r', float('nan')):+.3f}"
        print(f"{v:16s} ETH full {f(e['full'])} pre {f(e['before_split'])} post {f(e['from_split'])} | ALTS full {f(a['full'])} "
              f"pre {f(a['before_split'])} post {f(a['from_split'])} ex {a['full'].get('excess_mean_r', float('nan')):+.3f} "
              f"p {a['before_split'].get('excess_month_block_p')}/{a['from_split'].get('excess_month_block_p')} {summary['verdict'].get(v, '')}", flush=True)
    pd.concat(all_trades, ignore_index=True).to_csv(OUT / "trades.csv.gz", index=False)
    pd.concat(all_ctl, ignore_index=True).to_csv(OUT / "controls.csv.gz", index=False)
    (OUT / "summary.json").write_text(json.dumps(v1210.clean(summary), ensure_ascii=False, indent=1, default=str))
    print("wrote", OUT, f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
