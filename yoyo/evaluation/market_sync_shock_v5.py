"""Market-wide sync shock v5: which exit rule fits these shocks?

Owner 2026-10-08: "接进菜单，然后继续做出场方式". v2 compared fixed holds with one trend
exit; v3/v4 conditioned on the shock and its environment and kept the exit fixed. v5 keeps
the events fixed and changes only how the trade is closed.

Single variable: the exit rule. Events are v2's loosest config (z>=2, volume x2, breadth
>=60%, single sync bar, 24h cooldown) on 30m and 1H, recomputed here with v2's code and
checked against v2's events.csv. Entry is the next bar's open, cost 0.2% round trip, and
every rule is applied to the same events and to the same 20 matched random entries per
event (same month, same BTC volatility tercile, same side). Rules (pre-registered):

  hold_12h        exit at the 12h close (baseline)
  hold_24h        exit at the 24h close
  trail3_24h      stop 2ATR, trail 3ATR from the best close, 24h cap (v2's trend exit)
  trail2_24h      stop 2ATR, trail 2ATR, 24h cap
  trail3_48h      stop 2ATR, trail 3ATR, 48h cap
  be_24h          stop 2ATR; once a close is 1ATR in profit the stop moves to entry; 24h cap
  tp2_trail3_24h  half off at the first close 2ATR in profit, the rest as trail3_24h
  split_4h_24h    half off at the 4h close, half at the 24h close

All stops are close-based and the level that applies on a bar comes from closes before it.
ATR is Wilder ATR(14) on the event timeframe at the signal bar. Each instrument has one
price path for every rule: ETH, BTC, and for alts v2's rebalanced equal-weight index. An
event or control counts only if its 48h path is complete, so every rule sees the same set.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v2 as v2

EXP = Path("experiments/active/exp-market-sync-shock-20261008-v5")
CONFIG = EXP / "config.json"
V2_RESULTS = Path("experiments/active/exp-market-sync-shock-20261008-v2/results_v2")
EXITS = ("hold_12h", "hold_24h", "trail3_24h", "trail2_24h", "trail3_48h", "be_24h",
         "tp2_trail3_24h", "split_4h_24h")
INSTRUMENTS = {"eth": "ETHUSDT", "btc": "BTCUSDT", "alts": "ALTS"}
PATH_HOURS = 48


def stop_exit(entry: float, closes: np.ndarray, atr: float, side: int, *, stop: float = 2.0,
              trail: float | None = 3.0, breakeven: float | None = None) -> tuple[float, int]:
    """Close-based stop; returns (gross return in the trade direction, exit position in ``closes``).

    The level on bar j: ``stop`` ATR from entry, raised to ``trail`` ATR below the best earlier
    close, and to entry once an earlier close was ``breakeven`` ATR in profit. Without a hit the
    trade closes on the last bar of ``closes``.
    """
    c = np.asarray(closes, float)
    signed, base = side * c, side * entry
    best = np.maximum.accumulate(np.concatenate([[base], signed])[:-1])
    level = np.full(len(c), base - stop * atr)
    if trail is not None:
        level = np.maximum(level, best - trail * atr)
    if breakeven is not None:
        level = np.where(best - base >= breakeven * atr, np.maximum(level, base), level)
    hit = np.flatnonzero(signed <= level)
    j = int(hit[0]) if len(hit) else len(c) - 1
    return side * (c[j] / entry - 1.0), j


def exit_returns(entry: float, closes: np.ndarray, atr: float, side: int, per_hour: int) -> dict[str, float]:
    """Gross trade-direction return of every rule on one complete 48h path of closes after entry."""
    c = np.asarray(closes, float)
    hold = {h: side * (c[h * per_hour - 1] / entry - 1.0) for h in (4, 12, 24)}
    day = c[: 24 * per_hour]
    trail3, j3 = stop_exit(entry, day, atr, side)
    target = np.flatnonzero(side * (day - entry) >= 2 * atr)
    # Before the target the trail level sits below entry - 1ATR, so target and stop never share a bar.
    scaled = 0.5 * side * (day[target[0]] / entry - 1.0) + 0.5 * trail3 if len(target) and target[0] < j3 else trail3
    return {
        "hold_12h": hold[12],
        "hold_24h": hold[24],
        "trail3_24h": trail3,
        "trail2_24h": stop_exit(entry, day, atr, side, trail=2.0)[0],
        "trail3_48h": stop_exit(entry, c[: PATH_HOURS * per_hour], atr, side)[0],
        "be_24h": stop_exit(entry, day, atr, side, trail=None, breakeven=1.0)[0],
        "tp2_trail3_24h": scaled,
        "split_4h_24h": 0.5 * hold[4] + 0.5 * hold[24],
    }


def simulate(series: dict, i: np.ndarray, side: np.ndarray, per_hour: int) -> dict[str, np.ndarray]:
    """Every rule for signal bars ``i`` on one instrument; NaN unless the 48h path is complete."""
    cap = PATH_HOURS * per_hour
    n = len(series["close"])
    out = {name: np.full(len(i), np.nan) for name in EXITS}
    for k, (e, sd) in enumerate(zip(i, side)):
        if e + cap >= n:
            continue
        entry, atr = series["open"][e + 1], series["atr"][e]
        path = series["close"][e + 1: e + 1 + cap]
        if not (np.isfinite(entry) and entry > 0 and np.isfinite(atr) and atr > 0 and np.isfinite(path).all()):
            continue
        for name, r in exit_returns(float(entry), path, float(atr), int(sd), per_hour).items():
            out[name][k] = r
    return out


def run_timeframe(panel: v2.Panel2, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    sel = cfg["event_set"]
    btc, eth = panel.feat["BTCUSDT"], panel.feat["ETHUSDT"]
    breadth = panel.breadth(np.sign(btc.ret.to_numpy()))
    per_hour = 60 // panel.minutes
    hits = v2.sync_hits(btc, eth, breadth, z_min=sel["z_min"], v_min=sel["v_min"], b_min=sel["b_min"])
    ev = v2.events_from_hits(hits, sel["kind"], sel["cooldown_hours"] * per_hour)
    in_window = panel.times >= pd.Timestamp(cfg["start"])
    ev = ev.loc[in_window[ev.i.to_numpy()]]
    i, side = ev.i.to_numpy(), ev.side.to_numpy().astype(int)
    rng = np.random.default_rng(cfg["control_seed"] + panel.minutes)
    pool = {(m, t): np.flatnonzero((panel.month == m) & (panel.vol_tercile == t) & in_window)
            for m in np.unique(panel.month) for t in range(3)}
    ctrl = []
    for e in i:
        cand = pool.get((panel.month[e], panel.vol_tercile[e]), np.empty(0, int))
        cand = cand[cand != e]
        ctrl.append(rng.choice(cand, size=cfg["controls"]) if len(cand) else np.full(cfg["controls"], -1))
    ctrl = np.asarray(ctrl).reshape(len(i), cfg["controls"])
    flat, cside = ctrl.ravel(), np.repeat(side, cfg["controls"])
    ok = flat >= 0
    cost = cfg["round_trip_cost"]
    events = pd.DataFrame({"minutes": panel.minutes, "time": [panel.times[e].isoformat() for e in i], "side": side})
    rows = []
    for name, key in INSTRUMENTS.items():
        got = simulate(panel.lead[key], i, side, per_hour)
        cgot = simulate(panel.lead[key], flat[ok], cside[ok], per_hour)
        for rule in EXITS:
            cg = np.full(len(flat), np.nan)
            cg[ok] = cgot[rule]
            cg = cg.reshape(len(i), -1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # an event whose controls all lack a path is NaN
                cmean = np.nanmean(cg, axis=1)
            rows.append(pd.DataFrame({
                "minutes": panel.minutes, "instrument": name, "exit": rule, "time": events.time,
                "month": panel.month[i], "side": side, "gross": got[rule], "net": got[rule] - cost,
                "control_net": cmean - cost, "controls_valid": np.isfinite(cg).sum(axis=1)}))
    return events, pd.concat(rows, ignore_index=True)


def _paired(diff: np.ndarray, months: np.ndarray, seed: int, flips: int) -> dict:
    sd = diff.std(ddof=1) if len(diff) > 2 else math.nan
    return {"bp": 1e4 * diff.mean() if len(diff) else math.nan,
            "t": float(diff.mean() / (sd / math.sqrt(len(diff)))) if sd and sd > 0 else math.nan,
            "p": v1.month_block_sign_flip(diff, months, seed, flips)}


def summarize(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Per period x timeframe x side x instrument x exit: net, excess over controls, and the
    paired difference against the baseline rule on the same events (raw and excess)."""
    split, seed, flips, base = pd.Timestamp(cfg["select_before"]), cfg["stat_seed"], cfg["flips"], cfg["baseline"]
    trades = trades.loc[np.isfinite(trades.net) & np.isfinite(trades.control_net)]
    trades = trades.assign(period=np.where(pd.to_datetime(trades.time) < split, "select", "check"),
                           excess=trades.net - trades.control_net)
    keys = ["period", "minutes", "side", "instrument"]
    rows = []
    for key, g in trades.groupby(keys, sort=True):
        wide = g.pivot_table(index=["time", "month"], columns="exit", values=["net", "excess", "control_net"])
        wide = wide.dropna()  # an event counts for every rule or for none
        months = wide.index.get_level_values("month").to_numpy()
        for rule in EXITS:
            net, excess = wide[("net", rule)].to_numpy(), wide[("excess", rule)].to_numpy()
            vs_ctrl = _paired(excess, months, seed, flips)
            row = {**dict(zip(keys, key)), "exit": rule, "events": len(wide), "months": len(set(months)),
                   "mean_net_bp": 1e4 * net.mean(), "median_net_bp": 1e4 * np.median(net),
                   "win_rate": float((net > 0).mean()), "control_net_bp": 1e4 * wide[("control_net", rule)].mean(),
                   "excess_bp": vs_ctrl["bp"], "t_excess": vs_ctrl["t"], "p_excess": vs_ctrl["p"]}
            if rule != base:
                raw = _paired(net - wide[("net", base)].to_numpy(), months, seed, flips)
                exc = _paired(excess - wide[("excess", base)].to_numpy(), months, seed, flips)
                row.update({"vs_base_bp": raw["bp"], "t_vs_base": raw["t"], "p_vs_base": raw["p"],
                            "vs_base_excess_bp": exc["bp"], "p_vs_base_excess": exc["p"]})
            rows.append(row)
    return pd.DataFrame(rows)


def select_and_check(summary: pd.DataFrame, min_events: int) -> pd.DataFrame:
    """Per timeframe, side and instrument: the exit with the largest excess t before the split, read after."""
    keys = ["minutes", "side", "instrument"]
    pick = summary.loc[summary.period.eq("select") & summary.events.ge(min_events) & np.isfinite(summary.t_excess)]
    check = summary.loc[summary.period.eq("check")].set_index(keys + ["exit"])
    rows = []
    for key, g in pick.groupby(keys):
        best = g.sort_values("t_excess", ascending=False).iloc[0]
        later = check.loc[key + (best.exit,)] if key + (best.exit,) in check.index else pd.Series(dtype=float)
        rows.append({**dict(zip(keys, key)), "exit": best.exit,
                     **{f"select_{c}": best[c] for c in ("events", "mean_net_bp", "excess_bp", "t_excess", "p_excess", "vs_base_bp")},
                     **{f"check_{c}": later.get(c, math.nan) for c in ("events", "mean_net_bp", "excess_bp", "t_excess", "p_excess", "vs_base_bp")}})
    return pd.DataFrame(rows)


def reconcile(events: pd.DataFrame, trades: pd.DataFrame, cfg: dict) -> dict:
    """Events must equal v2's; shared rules must reproduce v2's gross returns."""
    sel = cfg["event_set"]
    key = f"z{sel['z_min']}_v{sel['v_min']}_b{sel['b_min']}_{sel['kind']}"
    ref = pd.read_csv(V2_RESULTS / "events.csv")
    ref = ref.loc[ref.config.eq(key) & ref.minutes.isin(sel["timeframes"])]
    same = (sorted(zip(ref.minutes, ref.time, ref.side)) == sorted(zip(events.minutes, events.time, events.side)))
    if not same:
        raise ValueError("events differ from v2 events.csv")
    old = pd.read_csv(V2_RESULTS / "trades.csv.gz")
    old = old.loc[old.config.eq(key) & old.minutes.isin(sel["timeframes"])]
    out: dict = {"v2_events": len(ref)}
    for ours, theirs, names in (("trail3_24h", "trend", ("eth", "btc", "alts")),
                                ("hold_12h", "time_12h", ("eth", "btc")), ("hold_24h", "time_24h", ("eth", "btc"))):
        a = trades.loc[trades.exit.eq(ours) & trades.instrument.isin(names), ["minutes", "instrument", "time", "gross"]]
        b = old.loc[old.exit.eq(theirs) & old.instrument.isin(names), ["minutes", "instrument", "time", "gross"]]
        m = a.merge(b, on=["minutes", "instrument", "time"], suffixes=("", "_v2")).dropna()
        gap = float((m.gross - m.gross_v2).abs().max()) if len(m) else math.nan
        if not gap < 1e-9:
            raise ValueError(f"{ours} differs from v2 {theirs}: max |diff| {gap}")
        out[f"{ours}_vs_v2_{theirs}"] = {"rows": len(m), "max_abs_diff": gap}
    return out


def run(output: Path) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_market_sync_shock_v5.py"),
               Path(v2.__file__), Path(v1.__file__))
    if not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    if tuple(cfg["exits"]) != EXITS or cfg["baseline"] not in EXITS:
        raise ValueError("config exits differ from the builder's rules")
    start, end = pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"])
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    began = time.perf_counter()
    universe = v1.monthly_universe(symbols, pd.Timestamp(cfg["start"]), end, cfg["universe_size"])
    needed = sorted(set(v1.LEADERS) | {s for names in universe.values() for s in names})
    raw = {s: v1.read_5m(s, start, end) for s in needed}
    events, trades = [], []
    for minutes in cfg["event_set"]["timeframes"]:
        ev, tr = run_timeframe(v2.Panel2(raw, minutes, cfg["lookback"], universe), cfg)
        events.append(ev); trades.append(tr)
        print(json.dumps({"minutes": minutes, "events": len(ev), "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    events, trades = pd.concat(events, ignore_index=True), pd.concat(trades, ignore_index=True)
    checks = reconcile(events, trades, cfg)
    summary = summarize(trades, cfg)
    selected = select_and_check(summary, cfg["min_events"])
    output.mkdir(parents=True, exist_ok=True)
    events.to_csv(output / "events.csv", index=False)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    selected.to_csv(output / "selected.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    complete = trades.loc[trades.exit.eq(cfg["baseline"])].groupby("instrument").net.apply(lambda s: int(np.isfinite(s).sum()))
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "symbols_read": len(needed),
        "events": len(events), "events_with_complete_path": complete.to_dict(), "v2_reconciliation": checks,
        "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    pc = cfg["primary"]
    view = summary.loc[(summary.minutes == pc["minutes"]) & (summary.side == pc["side"]) & (summary.instrument == pc["instrument"])]
    cols = ["period", "exit", "events", "mean_net_bp", "win_rate", "excess_bp", "p_excess", "vs_base_bp", "p_vs_base", "vs_base_excess_bp"]
    print(view[cols].round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v5")
    run(parser.parse_args().output)


if __name__ == "__main__":
    main()
