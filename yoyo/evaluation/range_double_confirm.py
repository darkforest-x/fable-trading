"""ETH 15m observation / 5m two-body confirmation, using the existing engine.

Read completed OHLC only: Stoch(5,3,3) on complete 15m and 5m bars, current
and immediately preceding 5m candle bodies, latest completed 15m ATR14 for
matching. A new observation cancels a pending confirmation. The source arrow
must close strictly after the observation; confirmation enters at the next
5m close, never retrospectively at the arrow close. Stops are recomputed by
range_framework at actual entry using five completed bars plus 0.05%.
Exit management remains 15m 5R/4ATR with armed H1 reverse exit and 20bp costs.
No new execution engine, Pine change, downloads, or outcome-based selection.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import aggregate, prepare, signals, entries, inference
from yoyo.evaluation.range_entry_clock import entry_context
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, prepared
from yoyo.evaluation.range_management_study import summary, subset, holm

DEFAULT = ROOT / "experiments/active/exp-eth-5m-double-confirm-20261020-v1/config.json"
CODE = ["yoyo/evaluation/range_double_confirm.py",
        "tests/evaluation/test_range_double_confirm.py"]


def admission(watch_arrows, arrows, bodies, confirm):
    """Causal chronological admission; no forward shifts or future indexing."""
    watch_arrows, arrows, bodies = map(np.asarray, (watch_arrows, arrows, bodies))
    if not (len(watch_arrows) == len(arrows) == len(bodies)):
        raise ValueError("Inputs must align")
    n = len(arrows)
    side = np.zeros(n, dtype=int)
    source = np.full(n, -1, dtype=int)
    observed = np.full(n, -1, dtype=int)
    eligible_side = np.zeros(n, dtype=int)
    watch, since = 0, -1
    for i in range(n):
        if watch_arrows[i]:
            watch, since = int(watch_arrows[i]), i
        k = i-1 if confirm else i
        if not watch or k <= since:
            continue
        # For controls the trigger is a random non-arrow bar, but both candle
        # bodies and the identical causal observation rule must still pass.
        if confirm and (watch*bodies[k] <= 0 or watch*bodies[i] <= 0):
            continue
        eligible_side[i] = watch
        if arrows[k] == watch:
            side[i], source[i], observed[i] = watch, k, since
    return dict(signal=side, source_i=source, observed=observed,
                eligible_side=eligible_side)


def candidate_context(frame, confirm=False):
    """Candidate-only dense ATR; never substitute it for sparse exit events."""
    ctx = prepare(frame)
    ctx.update(admission(ctx["quarter"], signals(frame).to_numpy(int),
                         (frame.close-frame.open).to_numpy(), confirm))
    ctx["atr"] = pd.Series(ctx["atr"], index=frame.index).ffill().to_numpy()
    return ctx


def candidates(ctx, cfg, confirm):
    """Same-symbol/week/side/segment/causal-volatility matched random controls.

    New-arm controls additionally meet the same observation and body rules.
    Matching may sample with replacement; signal timestamps are excluded.
    Segment transitions cannot carry an unconfirmed previous-segment arrow.
    """
    signal, _, _ = entries(ctx, cfg)
    times = ctx["frame"].index
    bounds = pd.to_datetime([cfg["start"], *cfg["splits"], cfg["end"]], utc=True)
    signal["source_i"] = ctx["source_i"][signal.entry_i.to_numpy(int)]
    signal["observed_i"] = ctx["observed"][signal.entry_i.to_numpy(int)]
    source_times = times[signal.source_i.to_numpy(int)]
    signal = signal.loc[source_times >= bounds[signal.segment.to_numpy(int)]].copy()
    signal["pair_id"] = np.arange(len(signal))
    vol = ctx["atr"]/ctx["bars"][:, 3]
    bucket = np.digitize(vol, cfg["volatility_edges"])
    weeks = (times-pd.to_timedelta(times.dayofweek, unit="D")).normalize().asi8
    pools = {}
    lag = 1 if confirm else 0
    for seg in range(3):
        lo = times.searchsorted(bounds[seg], side="left")+lag
        hi = times.searchsorted(bounds[seg+1], side="left")
        for i in range(max(lo, max(cfg["windows_5m"])), hi):
            s = int(ctx["eligible_side"][i])
            if s and np.isfinite(vol[i]) and ctx["signal"][i] == 0:
                pools.setdefault((seg, int(weeks[i]), int(bucket[i]), s), []).append(i)
    rng = np.random.default_rng(cfg["seed"])
    controls, unmatched = [], []
    latest_watch = np.maximum.accumulate(
        np.where(ctx["quarter"] != 0, np.arange(len(times)), -1))
    for r in signal.to_dict("records"):
        pool = pools.get((r["segment"], r["week"], r["bucket"], r["side"]), [])
        if not pool:
            unmatched.append(r["pair_id"])
            continue
        row = dict(r)
        row["entry_i"] = int(rng.choice(pool))
        row["source_i"] = row["entry_i"]-lag
        # Retain causal latest observation for random entry metadata.
        row["observed_i"] = int(latest_watch[row["source_i"]])
        controls.append(row)
    random = pd.DataFrame(controls, columns=signal.columns)
    for pop, data in (("signal", signal), ("random", random)):
        data["population"] = pop
        data["entry_time"] = times[data.entry_i.to_numpy(int)].astype(str)
        data["source_time"] = times[data.source_i.to_numpy(int)].astype(str)
    return signal, random, unmatched


def load_frame(path):
    data = pd.read_csv(path, float_precision="round_trip")
    data.index = pd.DatetimeIndex(
        pd.to_datetime(data.pop("ts"), unit="ms", utc=True)+pd.Timedelta(minutes=5)
    ).as_unit("ns")
    if not data.index.is_unique or not data.index.is_monotonic_increasing:
        raise ValueError("Unordered/duplicate source")
    if not (data.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
        raise ValueError("Missing source bars")
    return data[["open", "high", "low", "close"]]


def build(path):
    cfg = json.loads(path.read_text())
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *CODE], cwd=ROOT).returncode:
        raise RuntimeError("Commit builder before generating")
    base = json.loads((ROOT/cfg["baseline_config"]).read_text())
    prior = json.loads((ROOT/cfg["baseline_receipt"]).read_text())
    assert [file_stamp(ROOT/base[k]) for k in ("data", "entry_candidates")] == prior["identity"]["inputs"]
    assert base == prior["identity"]["config"]
    frame = load_frame(ROOT/base["data"])
    # Verify cached baseline candidate membership, without replaying its exits.
    oldctx = entry_context(frame, 5)
    baseline, _, _ = entries(oldctx, cfg)
    old = pd.read_csv(ROOT/base["entry_candidates"])
    old = old[old.population == "signal"]
    np.testing.assert_array_equal(
        baseline[["entry_i", "side", "segment"]], old[["entry_i", "side", "segment"]])
    checks = []
    for name, confirm in (("watch15", False), ("watch15_double", True)):
        work = path.parent/name
        work.mkdir(exist_ok=True)
        if (work/"config.json").exists():
            raise RuntimeError("Inputs already generated; use --phase run or analyze")
        ctx = candidate_context(frame, confirm)
        ss, rr, missing = candidates(ctx, cfg, confirm)
        pd.concat([ss, rr], ignore_index=True).to_csv(work/"entries.csv", index=False)
        native = dict(base, experiment_id=cfg["experiment_id"],
                      entry_candidates=str((work/"entries.csv").relative_to(ROOT)),
                      policies=[base["policies"][0]],
                      output=str((work/"results").relative_to(ROOT)))
        dump(work/"config.json", native)
        for cut in (5783, len(frame)//2+1, len(frame)-1):
            prefix = candidate_context(frame.iloc[:cut], confirm)
            for key in ("signal", "source_i", "observed", "eligible_side", "atr"):
                np.testing.assert_array_equal(prefix[key], ctx[key][:cut])
        checks.append(dict(arm=name, signals=len(ss), random=len(rr), unmatched=missing,
                           prefix_checks=3, real_bars=len(frame)))
        print(json.dumps(checks[-1]), flush=True)
    dump(path.parent/"input_receipt.json", dict(
        source=file_stamp(ROOT/base["data"]), cached_baseline_rows=len(baseline),
        baseline_receipt=file_stamp(ROOT/cfg["baseline_receipt"]),
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        checks=checks, no_download=True, no_content_hash=True))


def run(path):
    started = time.perf_counter()
    def one(name):
        work = path.parent/name
        with (work/"run.log").open("w") as log:
            subprocess.run([str(ROOT/"scripts/backtest-range"), "--config", str(work/"config.json")],
                           cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        r = json.loads((work/"results/receipt.json").read_text())
        return dict(arm=name, elapsed_seconds=r["elapsed_seconds"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(one, ("watch15", "watch15_double")))
    dump(path.parent/"run_receipt.json",
         dict(runs=runs, wall_seconds=time.perf_counter()-started))
    print(json.dumps(runs), flush=True)


def analyze(path):
    from yoyo.evaluation.pepe_range_breakeven import scalar, assert_equal
    cfg = json.loads(path.read_text())
    frames = {"h1_baseline": pd.read_csv(
        ROOT/cfg["baseline_ledger"], float_precision="round_trip")}
    audits, records, contrasts = [], [], []
    for name in ("watch15", "watch15_double"):
        work = path.parent/name
        rows = pd.read_csv(work/"results/baseline/trades.csv", float_precision="round_trip")
        metadata = pd.read_csv(work/"entries.csv")
        rows = rows.merge(metadata[["pair_id", "population", "source_i", "observed_i"]],
                          on=["pair_id", "population"], validate="one_to_one")
        nativecfg = json.loads((work/"config.json").read_text())
        ctx, _, _ = prepared(nativecfg, work/".cache")
        # Independent index-based checks use raw arrows and candle bodies,
        # not the admission state machine that produced the candidates.
        raw_five = signals(ctx["frame"]).to_numpy(int)
        raw_quarter = signals(aggregate(ctx["frame"], 15)).reindex(
            ctx["frame"].index, fill_value=0).to_numpy(int)
        observed_indices = np.flatnonzero(raw_quarter)
        source_idx = rows.source_i.to_numpy(int)
        entry_idx = rows.entry_i.to_numpy(int)
        latest_at_source = observed_indices[
            np.searchsorted(observed_indices, source_idx, side="right")-1]
        latest_at_entry = observed_indices[
            np.searchsorted(observed_indices, entry_idx, side="right")-1]
        np.testing.assert_array_equal(latest_at_source, rows.observed_i)
        np.testing.assert_array_equal(latest_at_entry, rows.observed_i)
        np.testing.assert_array_equal(raw_quarter[latest_at_entry], rows.side)
        signal_mask = (rows.population == "signal").to_numpy()
        np.testing.assert_array_equal(raw_five[source_idx[signal_mask]],
                                      rows.side.to_numpy()[signal_mask])
        if name.endswith("double"):
            body = ctx["bars"][:, 3]-ctx["bars"][:, 0]
            assert (rows.side.to_numpy()*body[source_idx] > 0).all()
            assert (rows.side.to_numpy()*body[entry_idx] > 0).all()
        probes = np.unique(np.r_[np.linspace(0, len(rows)-1, 16, dtype=int),
                                 np.argmax(rows.peak_r), np.argmax(rows.net_r)])
        columns = ["exit_i", "exit_price", "reason", "peak_r", "arm_i", "protection",
                   "ambiguous", "steps", "be_trigger_i", "be_effective_i", "stop_kind",
                   "be_price", "deferred_closes"]
        for i in probes:
            r = rows.iloc[i]
            event = r[["entry_i", "side", "entry", "stop", "end_i"]].to_numpy(float)
            expected = scalar(ctx, event, "baseline", .002, .01)
            actual = np.array([0 if k == "ambiguous" else r[k] for k in columns], float)
            assert_equal(actual, expected, .01)
        # Every price/stop is independently derived from actual-entry OHLC.
        for r in rows.itertuples():
            bars = ctx["bars"][r.entry_i-4:r.entry_i+1]
            entry = float(bars[-1, 3])
            extreme = bars[:, 2].min() if r.side == 1 else bars[:, 1].max()
            raw = (extreme-r.side*entry*.0005)/.01
            stop = (np.floor(raw+1e-8) if r.side == 1 else np.ceil(raw-1e-8))*.01
            assert abs(r.entry-entry) < 1e-7 and abs(r.stop-stop) < 1e-7
            assert r.observed_i < r.source_i <= r.entry_i
            assert r.entry_i-r.source_i == (1 if name.endswith("double") else 0)
        frames[name] = rows
        audits.append(dict(arm=name, scalar_paths=len(probes),
                           actual_entry_stop_rows=len(rows),
                           independent_watch_arrow_body_rows=len(rows)))
    frames["h1_baseline"]["source_i"] = frames["h1_baseline"].entry_i
    for name, rows in frames.items():
        for seg in (-1, 0, 1, 2, -2):
            part = subset(rows, seg)
            s, r = part[part.population == "signal"], part[part.population == "random"]
            for pop, data in (("signal", s), ("random", r)):
                records.append(dict(arm=name, segment=seg, population=pop, **summary(data),
                    mean_net_bp=float(data.net_notional_bp.mean()),
                    mean_fee_r=float(data.fee_r.mean()),
                    median_risk_pct=float((100*data.risk/data.entry).median()),
                    peak_10r=int((data.peak_r >= 10).sum()),
                    peak_20r=int((data.peak_r >= 20).sum())))
            paired = s.merge(r[["pair_id", "net_r", "net_notional_bp"]],
                             on="pair_id", suffixes=("", "_random"), validate="one_to_one")
            contrasts.append(dict(arm=name, segment=seg, n=len(paired),
                r_test=inference(paired, cfg["seed"]),
                bp_test=inference(paired.assign(
                    net_r=paired.net_notional_bp, net_r_random=paired.net_notional_bp_random),
                    cfg["seed"])))
    # Source-arrow linkage separates rejected trades from changed delayed outcomes.
    effects = []
    for before, after in (("h1_baseline", "watch15"), ("watch15", "watch15_double")):
        a = frames[before].query("population == 'signal'")
        b = frames[after].query("population == 'signal'")
        joined = a.merge(b, on=["source_i", "side"], how="outer",
                         suffixes=("_before", "_after"), indicator=True, validate="one_to_one")
        joined.to_csv(path.parent/f"{before}_to_{after}.csv.gz", index=False)
        both = joined._merge == "both"
        absent = joined._merge == "left_only"
        record = dict(before=before, after=after, retained=int(both.sum()),
                      removed=int(absent.sum()), added=int((joined._merge == "right_only").sum()),
                      removed_losers=int((absent & (joined.net_r_before < 0)).sum()),
                      removed_winners=int((absent & (joined.net_r_before > 0)).sum()),
                      removed_net_r=float(joined.loc[absent, "net_r_before"].sum()),
                      surviving_original_net_r=float(joined.loc[both, "net_r_before"].sum()),
                      surviving_new_net_r=float(joined.loc[both, "net_r_after"].sum()))
        for threshold in (10, 20):
            prior = joined.gross_r_before >= threshold-1e-7
            now = joined.gross_r_after >= threshold-1e-7
            record[f"old_{threshold}r"] = int(prior.sum())
            record[f"rejected_{threshold}r"] = int((prior & absent).sum())
            record[f"retained_signal_{threshold}r"] = int((prior & both).sum())
            record[f"still_final_{threshold}r"] = int((prior & now & both).sum())
            record[f"new_final_{threshold}r"] = int((~prior & now).sum())
        effects.append(record)
    for seg in (-1, 0, 1, 2, -2):
        group = [c for c in contrasts if c["segment"] == seg and c["arm"] != "h1_baseline"]
        for field in ("r_test", "bp_test"):
            for row, corrected in zip(group, holm([x[field]["p_one_sided"] for x in group])):
                row[field]["holm_two_new_arms"] = corrected
    table = pd.DataFrame(records)
    table.to_csv(path.parent/"summary.csv", index=False)
    dump(path.parent/"inference.json", contrasts)
    dump(path.parent/"source_effects.json", effects)
    dump(path.parent/"validation.json", dict(audits=audits,
         native_engine_unchanged=True, baseline_not_replayed=True,
         source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()))
    print(table.query("segment == -1 and population == 'signal'")[
        ["arm", "n", "win_rate", "net_r", "mean_net_bp", "gross_10r", "gross_20r"]].to_string(index=False))
    print(json.dumps(effects), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT)
    p.add_argument("--phase", choices=["build", "run", "analyze"], required=True)
    args = p.parse_args()
    {"build": build, "run": run, "analyze": analyze}[args.phase](args.config.resolve())


if __name__ == "__main__":
    main()
