"""Causal 12H Monday reclaims targeting the near NY Monday boundary.

Features use the completed 12H OHLC, EMA20/50 through that close, the completed
NY Monday range, and the next hourly open for executable geometry. Future bars
are used only by Nautilus to match orders. Inputs and clocks are UTC milliseconds.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-highwin-20261109-v1"
CFG = json.loads((EXP / "config.json").read_text())
SOURCE = EXP.parent / CFG["source"]
HOUR = 3_600_000
STEP = 12 * HOUR
HOLD = CFG["holding_hours"]
OHLC = ["open", "high", "low", "close"]
START = int(pd.Timestamp(CFG["start_bjt"], tz="Asia/Shanghai").timestamp() * 1000)
CUTOFF = int(pd.Timestamp(CFG["cutoff_utc"]).timestamp() * 1000)


def candles(symbol, period):
    path = SOURCE / "inputs" / symbol / f"bars{period}.csv.gz"
    frame = pd.read_csv(path, index_col="time", float_precision="round_trip")[OHLC]
    assert frame.index.is_monotonic_increasing and frame.index.is_unique
    return frame


def grid(price, tick, direction):
    rounding = np.ceil if direction > 0 else np.floor
    return float(round(rounding(price / tick - direction * 1e-8) * tick, 10))


def profitable_bracket(price, stop, target, side):
    return (side * (price - stop) > 0 and
            side * (target - price) > CFG["taker"] * price + CFG["maker"] * target)


def prepare():
    from yoyo.evaluation.cloud_peak10_census import anchors
    from yoyo.monitor.cloud_signals import ema_points
    from yoyo.monitor.monday_fib import week_window

    with gzip.open(SOURCE / "instruments_snapshot.json.gz", "rt") as f:
        ticks = {r["instId"]: float(r["tickSz"]) for r in json.load(f)["data"]}
    rows, counts = [], Counter()
    rng = np.random.default_rng(CFG["seed"])
    for sym in CFG["symbols"]:
        hourly, bars = candles(sym, "1h"), candles(sym, "12h")
        assert hourly.index.to_series().diff().dropna().eq(HOUR).all()
        cal = anchors(hourly, bars, sym)
        points = ema_points(bars.reset_index().to_dict("records"))
        tick, used = ticks[f"{sym}-USDT-SWAP"], set()
        for c, ema in zip(bars.itertuples(), points):
            stamp = c.Index + STEP
            if not START <= stamp <= CUTOFF - HOLD * HOUR or not ema["ready"]:
                continue
            w = cal[week_window(c.Index)["week"]]
            if (w["state"] != "ready" or not w["end_ms"] <= c.Index < stamp <= w["expires_ms"]
                    or w["week"] in used or not w["lower"] < c.close < w["upper"]):
                continue
            lower, upper = c.low < w["lower"], c.high > w["upper"]
            if lower == upper:
                if lower:
                    counts["both_extensions"] += 1
                continue
            side = 1 if lower else -1
            ext = w["lower"] if lower else w["upper"]
            target = grid(w["low"] if lower else w["high"], tick, side)
            stop = grid(ext * (1 - side * CFG["stop_fraction"]), tick, -side)
            price = hourly.at[stamp, "open"]
            if not all(profitable_bracket(v, stop, target, side) for v in (c.close, price)):
                counts["no_executable_bracket"] += 1
                continue
            if ema["ema20"] == ema["ema50"]:
                counts["flat_cloud"] += 1
                continue
            used.add(w["week"])
            year = pd.to_datetime(stamp, unit="ms", utc=True).tz_convert("Asia/Shanghai").year
            key = f"{sym}-{w['week']}"
            cloud = "aligned" if (ema["ema20"] - ema["ema50"]) * side > 0 else "counter"
            event = dict(pair=key, symbol=sym, week=w["week"], year=year, cloud=cloud,
                         side=side, tick=tick, signal_ms=stamp, extension=ext, confirmed_close=c.close)
            rows.append(dict(event, trade_id=f"{key}-signal", arm="signal", open_ms=stamp,
                             entry=price, stop=stop, target_price=target))
            # Keep the same opening session and NY week, and a complete identical horizon.
            choices = [t for t in range(stamp - 6 * 24 * HOUR, stamp + 7 * 24 * HOUR, 24 * HOUR)
                       if t != stamp and START <= t <= CUTOFF - HOLD * HOUR and
                       w["end_ms"] <= t - STEP < t <= w["expires_ms"] and
                       pd.to_datetime(t, unit="ms", utc=True).tz_convert("Asia/Shanghai").year == year]
            if not choices:
                counts["no_control"] += 1
                continue
            t = int(rng.choice(choices))
            ref = hourly.at[t, "open"]
            rows.append(dict(event, trade_id=f"{key}-control", arm="control", open_ms=t,
                             entry=ref, stop=grid(stop * ref / price, tick, -side),
                             target_price=grid(target * ref / price, tick, side)))
    frame = pd.DataFrame(rows)
    assert not frame.trade_id.duplicated().any()
    frame.to_csv(EXP / "entries.csv", index=False)
    (EXP / "coverage.json").write_text(json.dumps(dict(counts), indent=2) + "\n")


def stats(group):
    if group.empty:
        return {"n": 0}
    x = group.net_r
    return dict(n=len(x), wins=int((x > 0).sum()), win_pct=round(float((x > 0).mean()*100), 2),
                mean_net_r=round(float(x.mean()), 4), total_net_r=round(float(x.sum()), 3),
                mean_net_bp=round(float(group.net_notional_bp.mean()), 2),
                profit_factor_r=round(float(x[x > 0].sum() / -x[x < 0].sum()), 3) if (x < 0).any() else None,
                ambiguous=int(group.ambiguous.sum()),
                target=int((group.reason == 4).sum()), stop=int((group.reason == 1).sum()),
                timed=int((group.reason == 0).sum()),
                slip_win_pct=round(float((group.stress_r > 0).mean()*100), 2),
                slip_mean_r=round(float(group.stress_r.mean()), 4))


def paired(group):
    """Week-cluster resample of matched signal-minus-control net R and win rate."""
    pivot = group.pivot(index=["week","pair"], columns="arm", values="net_r").dropna()
    if not len(pivot):
        return {"n": 0}
    diff = pd.DataFrame({"r": pivot.signal - pivot.control,
                         "win": (pivot.signal > 0).astype(int) - (pivot.control > 0).astype(int)})
    weekly = diff.groupby(level="week").agg(r=("r","sum"), win=("win","sum"), n=("r","size")).to_numpy()
    rng = np.random.default_rng(CFG["seed"])
    draws = weekly[rng.integers(len(weekly),size=(2500,len(weekly)))].sum(axis=1)
    low, high = np.quantile(draws[:,:2] / draws[:,2,None], [.025,.975], axis=0)
    return dict(n=len(pivot), weeks=len(weekly), delta_r=round(float(diff.r.mean()),4),
                delta_win_pp=round(float(100*diff.win.mean()),2),
                ci_r=[round(float(low[0]),4),round(float(high[0]),4)],
                ci_win_pp=[round(float(100*low[1]),2),round(float(100*high[1]),2)])


def execute():
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    entries = pd.read_csv(EXP / "entries.csv", float_precision="round_trip")
    policy = ExitPolicy(trail_arm_r=float("inf"), opposite_hour_exit=False, target_from_entry=True,
                        round_trip_cost=0, fill_maker_fee=CFG["maker"], fill_taker_fee=CFG["taker"],
                        intrabar_path="OHLC", monotone_segments=True)
    results = []
    for sym, entries_sym in entries.groupby("symbol"):
        group = entries_sym.copy()
        frame = candles(sym, "1h").loc[group.open_ms.min():group.open_ms.max()+(HOLD-1)*HOUR]
        group["entry_i"] = frame.index.get_indexer(group.open_ms)
        group["end_i"] = group.entry_i + HOLD - 1
        group["risk"] = group.side * (group.entry - group.stop)
        assert group.entry_i.ge(0).all() and group.end_i.lt(len(frame)).all()
        assert np.allclose(group.entry, frame.open.iloc[group.entry_i], atol=1e-12, rtol=0)
        frame.index = pd.to_datetime(frame.index + HOUR, unit="ms", utc=True)
        out, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), group,
                                     policy, tick=float(group.tick.iloc[0]), symbol=sym,
                                     entry_order_mode="market_open", execution_minutes=60)
        assert audit["positions"] == len(group) and len(fills) == 2*len(group)
        assert set(out.reason) <= {0,1,4}
        # Add 10bp adverse cost to each market fill only, in original risk units.
        out["stress_r"] = out.net_r - .001*(out.entry + out.exit_price.where(out.reason.ne(4), 0)) / out.risk
        results.append(out)
    ledger = pd.concat(results, ignore_index=True)
    ledger.to_csv(EXP / "trades.csv", index=False)
    stats_by = {}
    for year, g in [("all",ledger), *ledger.groupby("year")]:
        stats_by[str(year)] = {}
        for trend, sub in [("all",g), *g.groupby("cloud")]:
            stats_by[str(year)][trend] = dict(signal=stats(sub[sub.arm=="signal"]),
                                              control=stats(sub[sub.arm=="control"]), paired=paired(sub))
    (EXP / "results.json").write_text(json.dumps(stats_by, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage",choices=["prepare","execute"])
    stage = parser.parse_args().stage
    (prepare if stage == "prepare" else execute)()
