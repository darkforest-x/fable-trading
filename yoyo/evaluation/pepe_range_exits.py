"""Frozen PEPE V2.8 TP/SL grid, independent of chart viewport/rendering.

Source: owner-authorized 2026-10-20 study and the committed V2.8 Pine engine.
Features: completed 5m OHLC extrema (N bars), completed 15m/H1 Stoch(5,3,3),
completed 15m Wilder ATR14. Only outcomes read subsequent 5m OHLC, bounded by
the entry's time segment. Weekly controls use causal volatility buckets.
No training, account sizing, promotion, or changes to the delivered indicator.
"""
from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_range_v2_2_reference import signals, atr
from yoyo.evaluation.pepe_range_data import EXP, validate

ROOT = Path(__file__).resolve().parents[2]
CPP = ROOT / "yoyo/evaluation/pepe_range_native.cpp"


def aggregate(five, minutes):
    """Aggregate only complete native 5m bars; input/output indices are closes."""
    source = five.copy()
    source.index -= pd.Timedelta(minutes=5)
    g = source.resample(f"{minutes}min")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    out = out[g.close.count() == minutes // 5]
    out.index += pd.Timedelta(minutes=minutes)
    return out


def prepare(five, quarter=None, hour=None):
    quarter = aggregate(five, 15) if quarter is None else quarter
    hour = aggregate(five, 60) if hour is None else hour
    hs, qs = signals(hour), signals(quarter)
    h = hs.reindex(five.index, fill_value=0).to_numpy(float)
    q = qs.reindex(five.index, fill_value=0).to_numpy(float)
    a = atr(quarter).reindex(five.index).to_numpy(float)
    watch, observed = 0, -1
    prior = hs.loc[hs.index < five.index[0]]
    prior = prior[prior != 0]
    if len(prior):
        watch = int(prior.iloc[-1])
    sides = np.zeros(len(five), dtype=int)
    since = np.full(len(five), -1, dtype=int)
    for i in range(len(five)):
        if h[i]:
            watch, observed = int(h[i]), i
        if watch and q[i] == watch and i > observed:
            sides[i], since[i] = watch, observed
    return dict(frame=five, bars=np.ascontiguousarray(five[["open", "high", "low", "close"]], dtype=float),
                atr=np.ascontiguousarray(a), hour=np.ascontiguousarray(h), signal=sides,
                observed=since, quarter=q)


def native_library():
    path = EXP / "build" / "pepe_range_native.dylib"
    path.parent.mkdir(exist_ok=True)
    if not path.exists() or path.stat().st_mtime < CPP.stat().st_mtime:
        subprocess.run(["c++", "-O3", "-std=c++17", "-shared", "-fPIC", str(CPP), "-o", str(path)], check=True)
    lib = ctypes.CDLL(str(path.resolve()))
    array = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
    lib.replay.argtypes = [array, array, array, array, ctypes.c_int,
                          ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double, array]
    lib.replay.restype = None
    return lib


def replay(ctx, events, target=0., arm=5., multiple=4., tick=1e-9, lib=None):
    lib = native_library() if lib is None else lib
    rows = np.ascontiguousarray(events, dtype=float)
    result = np.empty((len(rows), 8), dtype=float)
    lib.replay(ctx["bars"], ctx["atr"], ctx["hour"], rows, len(rows),
               target, arm, multiple, tick, result)
    return result


def scalar(ctx, event, target=0., arm=5., multiple=4., tick=1e-9):
    """Readable independent oracle, including opening-gap chronology."""
    first, side, entry, stop, last = event
    first, side, last = int(first), int(side), int(last)
    risk = side * (entry - stop)
    ceiling = (np.ceil((entry + side*target*risk)/tick - 1e-8) if side == 1
               else np.floor((entry + side*target*risk)/tick + 1e-8))*tick
    peak, activated, ambiguous, steps = 0., -1, 0, 0
    price, reason, exit_i = entry, 0, last
    for j in range(first+1, last+1):
        steps += 1
        o, h, l, c = ctx["bars"][j]
        adverse, favorable = (l, h) if side == 1 else (h, l)
        stop_hit, gap = side*(adverse-stop) <= 0, side*(o-stop) < 0
        tp_hit = target > 0 and side*(favorable-ceiling) >= 0
        tp_gap = target > 0 and side*(o-ceiling) >= 0
        ambiguous += int(stop_hit and tp_hit and not gap and not tp_gap)
        if gap or (stop_hit and not tp_gap):
            price, reason, exit_i = (o if gap else stop), (2 if activated >= 0 else 1), j
            break
        if tp_hit:
            price, reason, exit_i = ceiling, 4, j
            peak = max(peak, side*(ceiling-entry)/risk)
            break
        peak = max(peak, side*(favorable-entry)/risk)
        if not target and np.isfinite(ctx["atr"][j]) and ctx["atr"][j] > 0:
            if activated < 0 and side*(c-entry)/risk >= arm:
                activated = j
            if activated >= 0:
                raw = (c-side*multiple*ctx["atr"][j])/tick
                candidate = (np.floor(raw+1e-8) if side == 1 else np.ceil(raw-1e-8))*tick
                if side*(candidate-stop) > 0:
                    stop = candidate
        if not target and activated >= 0 and ctx["hour"][j] == -side:
            price, reason, exit_i = c, 3, j
            break
        price = c
    return np.array([exit_i, price, reason, peak, activated, stop, ambiguous, steps], dtype=float)


def entries(ctx, cfg):
    index = ctx["frame"].index
    boundaries = pd.to_datetime([cfg["start"], *cfg["splits"], cfg["end"]], utc=True)
    rows, pools = [], {}
    vol = ctx["atr"] / ctx["bars"][:, 3]
    buckets = np.digitize(vol, cfg["volatility_edges"])
    week = (index - pd.to_timedelta(index.dayofweek, unit="D")).normalize().asi8
    valid_feature = np.isfinite(vol) & (np.arange(len(index)) >= max(cfg["windows_5m"]))
    all_entry = ctx["signal"] != 0
    for segment in range(3):
        mask = (index >= boundaries[segment]) & (index < boundaries[segment+1]) & valid_feature
        # The bar ending at the boundary is available as an exit, not a new entry.
        end_i = int(index.searchsorted(boundaries[segment+1], side="right") - 1)
        for i in np.flatnonzero(mask & ~all_entry):
            pools.setdefault((segment, int(week[i]), int(buckets[i])), []).append(i)
        for i in np.flatnonzero(mask & all_entry):
            rows.append(dict(pair_id=len(rows), entry_i=i, side=int(ctx["signal"][i]),
                             segment=segment, week=int(week[i]), bucket=int(buckets[i]), end_i=end_i))
    events = pd.DataFrame(rows)
    rng = np.random.default_rng(cfg["seed"])
    controls, unmatched = [], []
    for r in events.itertuples():
        candidates = pools.get((r.segment, r.week, r.bucket), [])
        if not candidates:
            unmatched.append(r.pair_id)
            continue
        row = r._asdict()
        row.pop("Index")
        row["entry_i"] = int(rng.choice(candidates))
        controls.append(row)
    return events, pd.DataFrame(controls), unmatched


def stop_events(ctx, source, window, buffer_percent, tick):
    frame = ctx["frame"]
    lows = frame.low.rolling(window).min().to_numpy()
    highs = frame.high.rolling(window).max().to_numpy()
    idx, side = source.entry_i.to_numpy(int), source.side.to_numpy(int)
    price = ctx["bars"][idx, 3]
    extreme = np.where(side == 1, lows[idx], highs[idx])
    raw = (extreme-side*price*buffer_percent/100)/tick
    stop = np.where(side == 1, np.floor(raw+1e-8), np.ceil(raw-1e-8))*tick
    risk = side*(price-stop)
    valid = (stop > 0) & (risk/tick >= 1-1e-8)
    base = source.loc[valid].copy()
    base["entry"], base["stop"], base["risk"] = price[valid], stop[valid], risk[valid]
    matrix = base[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy(float)
    return base, matrix


def policies(cfg):
    return ([(f"tp{r}", r, 0., 0.) for r in cfg["fixed_targets_r"]] +
            [(f"trail{r}_atr{a}", 0., r, a) for r in cfg["trail_arm_r"] for a in cfg["trail_atr_multiple"]])


def result_frame(base, result, cfg):
    out = base.copy()
    for i, name in enumerate(("exit_i", "exit_price", "reason", "peak_r", "arm_i", "protection", "ambiguous", "steps")):
        out[name] = result[:, i]
    out["gross_r"] = out.side*(out.exit_price-out.entry)/out.risk
    out["fee_r"] = cfg["round_trip_cost"]*out.entry/out.risk
    out["net_r"] = out.gross_r-out.fee_r
    out["net_notional_bp"] = (out.side*(out.exit_price-out.entry)/out.entry-cfg["round_trip_cost"])*10000
    out["risk_percent"] = out.risk/out.entry*100
    return out


def metrics(rows):
    natural = rows[rows.reason != 0]
    ordered = rows.sort_values(["exit_i", "entry_i"]).net_r.to_numpy()
    curve = np.r_[0., np.cumsum(ordered)]
    positive, negative = rows.loc[rows.net_r > 0, "net_r"].sum(), -rows.loc[rows.net_r < 0, "net_r"].sum()
    return dict(n=len(rows), natural_n=len(natural), boundary_n=int((rows.reason == 0).sum()),
                gross_r=float(rows.gross_r.sum()), net_r=float(rows.net_r.sum()),
                natural_net_r=float(natural.net_r.sum()),
                win_rate=float((rows.net_r > 0).mean()) if len(rows) else 0.,
                pf=float(positive/negative) if negative else None,
                realized_r_drawdown=float(np.max(np.maximum.accumulate(curve)-curve)),
                net_notional_bp_sum=float(rows.net_notional_bp.sum()),
                mean_risk_percent=float(rows.risk_percent.mean()) if len(rows) else None,
                mean_fee_r=float(rows.fee_r.mean()) if len(rows) else None,
                gross_10r=int((rows.gross_r >= 10-1e-8).sum()),
                gross_20r=int((rows.gross_r >= 20-1e-8).sum()),
                ambiguous=int(rows.ambiguous.sum()),
                boundary_net_r=float(rows.loc[rows.reason == 0, "net_r"].sum()))


def pairs(signal, random):
    return signal.merge(random, on="pair_id", suffixes=("", "_random"), validate="one_to_one")


def study(ctx, cfg):
    out = EXP / "results"
    out.mkdir(exist_ok=True)
    events, random, missing = entries(ctx, cfg)
    index = ctx["frame"].index
    for rows, name in ((events, "entries"), (random, "controls")):
        rows["entry_time"] = index[rows.entry_i.to_numpy()].astype(str)
        rows.to_csv(out/f"{name}.csv", index=False)
    (out/"matching.json").write_text(json.dumps(dict(signal=len(events), random=len(random), unmatched=missing), indent=2))
    lib, summaries, ledgers, audit_n = native_library(), [], [], 0
    for w in cfg["windows_5m"]:
        for b in cfg["buffers_percent"]:
            sb, sm = stop_events(ctx, events, w, b, cfg["tick"])
            rb, rm = stop_events(ctx, random, w, b, cfg["tick"])
            for policy, target, arm, multiple in policies(cfg):
                records = []
                for base, matrix, population in ((sb, sm, "signal"), (rb, rm, "random")):
                    z = replay(ctx, matrix, target, arm, multiple, cfg["tick"], lib)
                    # Distributed deterministic probes verify every policy/stop family.
                    probes = np.unique(np.linspace(0, len(matrix)-1, min(8, len(matrix)), dtype=int))
                    for probe in probes:
                        check = scalar(ctx, matrix[probe], target, arm, multiple, cfg["tick"])
                        np.testing.assert_allclose(z[probe], check, rtol=1e-11, atol=1e-9)
                        audit_n += 1
                    ledger = result_frame(base, z, cfg)
                    ledger["path"] = f"w{w}_b{b:g}_{policy}"
                    ledger["population"] = population
                    records.append(ledger)
                    ledgers.append(ledger)
                signal_rows, random_rows = records
                for cap in cfg["maximum_risk_percent"]:
                    s = signal_rows if cap == 0 else signal_rows[signal_rows.risk_percent <= cap+1e-10]
                    c = random_rows if cap == 0 else random_rows[random_rows.risk_percent <= cap+1e-10]
                    for seg in (-1, 0, 1, 2):
                        ss, cc = (s, c) if seg == -1 else (s[s.segment == seg], c[c.segment == seg])
                        paired = pairs(ss, cc)
                        summaries.append(dict(arm=f"w{w}_b{b:g}_{policy}_cap{cap:g}", window=w, buffer=b,
                            policy=policy, cap=cap, segment=seg, **metrics(ss), paired_n=len(paired),
                            paired_signal_net_r=float(paired.net_r.sum()),
                            paired_random_net_r=float(paired.net_r_random.sum()),
                            excess_r=float((paired.net_r-paired.net_r_random).sum())))
            print(f"replayed stop window={w}, buffer={b}", flush=True)
    summary = pd.DataFrame(summaries)
    summary.to_csv(out/"summary.csv", index=False)
    ledger = pd.concat(ledgers, ignore_index=True)
    ledger.to_csv(out/"paths.csv.gz", index=False)
    (out/"oracle_audit.json").write_text(json.dumps(dict(paths_checked=audit_n, mismatches=0), indent=2))
    return summary, ledger


def inference(paired, seed):
    values = (paired.net_r-paired.net_r_random).groupby(paired.week).sum().to_numpy()
    if not len(values):
        return dict(weeks=0)
    rng = np.random.default_rng(seed)
    null = (rng.choice([-1., 1.], size=(20000, len(values)))*values).sum(axis=1)
    samples = rng.choice(values, size=(5000, len(values)), replace=True).sum(axis=1)
    return dict(weeks=len(values), excess_r=float(values.sum()),
                p_one_sided=float((1+(null >= values.sum()-1e-10).sum())/20001),
                weekly_bootstrap_total_r_95ci=np.quantile(samples, [.025, .975]).tolist())


def select(summary, ledger, cfg):
    out = EXP/"results"
    qualified = summary[(summary.segment == 0) & (summary.n >= cfg["minimum_development_trades"])]
    selected = qualified.sort_values(["net_r", "realized_r_drawdown", "arm"], ascending=[False, True, True]).iloc[0].arm
    descriptive = summary[summary.segment == -1].sort_values(["net_r", "arm"], ascending=[False, True]).iloc[0].arm
    leaders = [cfg["baseline"], selected, descriptive]
    for family in ("tp", "trail"):
        q = qualified[qualified.policy.str.startswith(family)]
        leaders.append(q.sort_values(["net_r", "arm"], ascending=[False, True]).iloc[0].arm)
    comparisons, infer = [], []
    for name in dict.fromkeys(leaders):
        rows = summary[summary.arm == name]
        comparisons.extend(rows.to_dict("records"))
        cap = float(name.rsplit("_cap", 1)[1])
        path = name.rsplit("_cap", 1)[0]
        p = ledger[ledger.path == path]
        if cap:
            p = p[p.risk_percent <= cap+1e-10]
        p.to_csv(out/f"trades_{name}.csv", index=False)
        for seg in (-1, 0, 1, 2):
            r = p if seg == -1 else p[p.segment == seg]
            pair = pairs(r[r.population == "signal"], r[r.population == "random"])
            infer.append(dict(arm=name, segment=seg, **inference(pair, cfg["seed"])))
    pd.DataFrame(comparisons).to_csv(out/"selected_comparison.csv", index=False)
    pd.DataFrame(infer).to_json(out/"selected_inference.json", orient="records", indent=2)
    decision = dict(selected_on_development=selected, full_history_descriptive_best=descriptive,
                    total_configurations=int(summary.arm.nunique()), baseline=cfg["baseline"],
                    builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    note="No untouched holdout claim. Full-history optimum is descriptive only. No Pine defaults changed.")
    (out/"selection.json").write_text(json.dumps(decision, indent=2)+"\n")
    print(json.dumps(decision, indent=2))


def load_context():
    frame = validate(pd.read_csv(EXP/"inputs"/"okx_pepe_5m.csv.gz"))
    assert (frame.ts.diff().dropna() == 300000).all()
    frame.index = pd.to_datetime(frame.ts, unit="ms", utc=True)+pd.Timedelta(minutes=5)
    return prepare(frame)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()
    cfg = json.loads((EXP/"config.json").read_text())
    if args.analyze_only:
        summary = pd.read_csv(EXP/"results"/"summary.csv")
        ledger = pd.read_csv(EXP/"results"/"paths.csv.gz")
    else:
        summary, ledger = study(load_context(), cfg)
    select(summary, ledger, cfg)


if __name__ == "__main__":
    main()
