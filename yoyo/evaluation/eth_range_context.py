"""ETH admission-only context research using frozen native independent trades.

Owner 2026-10-20: investigate range location and higher timeframe environment.
Features read completed OHLC through each entry: previous complete NY day,
current developing/frozen Monday, completed UTC4H Stoch5/3/3 and H1 watch.
Outcomes are reused from the committed native ledger, never feature inputs.
No stop/exit changes, fresh market data, training, or new execution engine.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import aggregate, signals
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_management_study import holm

EXP = ROOT / "experiments/active/exp-eth-range-context-20261020-v1"
CODE = ["yoyo/evaluation/eth_range_context.py",
        "tests/evaluation/test_eth_range_context.py"]


def stoch(frame):
    """Read trailing5 OHLC, SMA3 K and SMA3 D only; same rounding as signals()."""
    lo, hi = frame.low.rolling(5).min(), frame.high.rolling(5).max()
    k = (100 * (frame.close-lo)/(hi-lo)).rolling(3).mean().round(10)
    return k, k.rolling(3).mean().round(10)


def context_features(five, timezone="America/New_York"):
    """Only completed5m OHLC/index are read; all feature availability is explicit."""
    if not five.index.is_unique or not five.index.is_monotonic_increasing:
        raise ValueError("Duplicate or unsorted bars")
    if not (five.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
        raise ValueError("Cannot bridge missing bars")
    times = five.index.as_unit("ns")
    opened = times-pd.Timedelta(minutes=5)
    local = opened.tz_convert(timezone)
    days = local.tz_localize(None).normalize()
    temp = five.assign(day=days, opened=opened, closed=times)
    daily = temp.groupby("day").agg(hi=("high", "max"), lo=("low", "min"),
                                    first=("opened", "first"), last=("closed", "last"),
                                    n=("close", "size"))
    starts = daily.index.tz_localize(timezone).tz_convert("UTC")
    ends = (daily.index+pd.Timedelta(days=1)).tz_localize(timezone).tz_convert("UTC")
    expected = np.asarray((ends-starts)/pd.Timedelta(minutes=5), dtype=int)
    good = (daily.n.to_numpy() == expected)
    good &= daily["first"].to_numpy() == starts.to_numpy()
    good &= daily["last"].to_numpy() == ends.to_numpy()
    daily.index = ends
    day_values = daily.loc[good, ["hi", "lo"]].copy()
    day_values["asof"] = day_values.index.as_unit("ns").asi8
    mapped = day_values.reindex(times, method="ffill")
    out = pd.DataFrame(index=times)
    for key in ("hi", "lo", "asof"):
        out["day_"+key] = mapped[key]

    monday = local.dayofweek == 0
    out["mon_hi"] = five.high.where(monday).groupby(days).cummax().ffill()
    out["mon_lo"] = five.low.where(monday).groupby(days).cummin().ffill()
    out["mon_asof"] = pd.Series(times.asi8, index=times).where(monday).ffill()
    out["mon_developing"] = monday
    for minutes, prefix in ((60, "h1"), (240, "h4")):
        bars = aggregate(five, minutes)
        arrow = signals(bars)
        state = arrow.replace(0, np.nan).ffill()
        seen = pd.Series(bars.index.as_unit("ns").asi8, index=bars.index).where(arrow != 0).ffill()
        out[prefix+"_arrow"] = state.reindex(times, method="ffill")
        out[prefix+"_watch"] = seen.reindex(times, method="ffill")
        if minutes == 240:
            k, d = stoch(bars)
            out["h4_momentum"] = np.sign(k-d).reindex(times, method="ffill")
            out["h4_asof"] = pd.Series(
                bars.index.as_unit("ns").asi8, index=bars.index).reindex(times, method="ffill")
    return out


def attach_context(ledger, features):
    """Join solely by frozen entry index; compute side-oriented range positions."""
    out = ledger.copy()
    mapped = features.iloc[out.entry_i.to_numpy(int)].reset_index(drop=True)
    for key in mapped:
        out[key] = mapped[key].to_numpy()
    for prefix in ("day", "mon"):
        z = (out.entry-out[prefix+"_lo"])/(out[prefix+"_hi"]-out[prefix+"_lo"])
        out[prefix+"_u"] = np.where(out.side == 1, z, 1-z)
    required = ["day_u", "mon_u", "h4_arrow", "h4_momentum", "h1_watch"]
    out["observable"] = np.isfinite(out[required]).all(axis=1)
    return out


def masks(rows, edge=.382):
    """Admission uses no outcome columns. Four range, two environment, eight joint arms."""
    known = rows.observable.to_numpy(bool)
    locations = {}
    for prefix in ("day", "mon"):
        locations[prefix+"_edge"] = rows[prefix+"_u"].to_numpy() <= edge
        locations[prefix+"_breakout"] = rows[prefix+"_u"].to_numpy() >= 1
    environments = {
        "h4_arrow": rows.h4_arrow.to_numpy() == rows.side.to_numpy(),
        "h4_momentum": rows.h4_momentum.to_numpy() == rows.side.to_numpy(),
    }
    result = {"baseline": known}
    for key, mask in {**locations, **environments}.items():
        result[key] = known & mask
    for a, ma in locations.items():
        for b, mb in environments.items():
            result[a+"__"+b] = known & ma & mb
    return result


def union_masks(rows, edge=.382):
    """Exploratory supplement: one supportive context suffices, unknowns still fail."""
    original = masks(rows, edge)
    return {a+"__or__"+b: original[a] | original[b]
            for a in ("day_edge", "day_breakout", "mon_edge", "mon_breakout")
            for b in ("h4_arrow", "h4_momentum")}


def match_candidates(signal, controls):
    """Deterministic without-replacement matching; never inspect trade outcomes."""
    keys = ["segment", "week", "side", "bucket"]
    pools = {key: group for key, group in controls.groupby(keys, sort=True)}
    pairs = []
    for key, group in signal.groupby(keys, sort=True):
        candidates = pools.get(key)
        if candidates is None:
            continue
        available = set(candidates.index)
        for i, row in group.sort_values(["entry_i", "trade_id"]).iterrows():
            if not available:
                break
            preferred = candidates.index[
                (candidates.pair_id == row.pair_id) & candidates.index.isin(available)]
            if len(preferred):
                j = preferred[0]
            else:
                j = min(available, key=lambda k: (abs(int(candidates.loc[k, "entry_i"])-int(row.entry_i)),
                                                 int(candidates.loc[k, "entry_i"]), str(k)))
            available.remove(j)
            pairs.append((i, j))
    return pairs


def stats(rows, reference):
    """Every retained row has its original native exit and initial-risk denominator."""
    n = len(rows)
    wins = rows.net_r > 1e-9
    losses = -rows.loc[rows.net_r < 0, "net_r"].sum()
    out = dict(n=n, watches=int(rows.h1_watch.nunique()), net_r=float(rows.net_r.sum()),
               mean_net_r=float(rows.net_r.mean()) if n else None,
               mean_net_bp=float(rows.net_notional_bp.mean()) if n else None,
               fixed1000_pnl=float(rows.net_notional_bp.sum()/10),
               win_rate=float(wins.mean()) if n else None,
               profit_factor=float(rows.loc[wins, "net_r"].sum()/losses) if losses else None,
               boundary_n=int((rows.reason == 0).sum()),
               natural_mean_net_bp=float(rows.loc[rows.reason != 0, "net_notional_bp"].mean())
                   if (rows.reason != 0).any() else None,
               removed_losses=int((reference.net_r < 0).sum()-(rows.net_r < 0).sum()),
               removed_winners=int((reference.net_r > 1e-9).sum()-wins.sum()),
               kept_fraction=n/len(reference) if len(reference) else None)
    for threshold in (10, 20):
        before = reference.gross_r >= threshold-1e-7
        after = rows.gross_r >= threshold-1e-7
        out[f"gross_{threshold}r"] = int(after.sum())
        out[f"retain_{threshold}r"] = float(after.sum()/before.sum()) if before.any() else None
        out[f"watches_{threshold}r"] = int(rows.loc[after, "h1_watch"].nunique())
        out[f"peak_{threshold}r"] = int((rows.peak_r >= threshold-1e-7).sum())
    return out


def weekly_inference(rows, value, cfg):
    """Cluster whole UTC weeks, retaining unequal week sizes in mean estimates."""
    if rows.empty:
        return dict(weeks=0, mean=None, ci95=[None, None], p=None)
    grouped = rows.groupby("week")[value].agg(["sum", "count"])
    sums, counts = grouped["sum"].to_numpy(), grouped["count"].to_numpy()
    rng = np.random.default_rng(cfg["seed"])
    draws = rng.integers(0, len(sums), (cfg["bootstrap_samples"], len(sums)))
    estimates = sums[draws].sum(axis=1)/counts[draws].sum(axis=1)
    null = (rng.choice([-1., 1.], (cfg["sign_flips"], len(sums)))*sums).sum(axis=1)
    return dict(weeks=len(sums), mean=float(sums.sum()/counts.sum()),
                ci95=np.quantile(estimates, [.025, .975]).tolist(),
                p=float((1+(null >= sums.sum()-1e-10).sum())/(len(null)+1)))


def select_development(summary, cfg):
    """Selection reads segment0 alone, including its winner-retention denominator."""
    development = summary[summary.segment == 0]
    baseline = development[development.arm == "baseline"].iloc[0]
    eligible = development[
        (development.arm != "baseline") &
        (development.n >= cfg["minimum_development_trades"]) &
        (development.watches >= cfg["minimum_development_watches"]) &
        (development.retain_10r >= cfg["minimum_winner_retention"]) &
        (development.retain_20r >= cfg["minimum_winner_retention"]) &
        (development.mean_net_bp > baseline.mean_net_bp)]
    ordered = eligible.sort_values(["mean_net_bp", "arm"], ascending=[False, True])
    return dict(selected=str(ordered.iloc[0].arm) if len(ordered) else "baseline",
                qualified_arms=ordered.arm.tolist(), selected_from_segment=0,
                requirement="100trades,20watches,70%10R and20R retention,better mean netbp than baseline",
                profitability_not_implied=True)


def split(rows, segment):
    return rows if segment == -1 else rows[rows.segment > 0] if segment == -2 else rows[rows.segment == segment]


def evaluate_admissions(rows, base, admission, cfg):
    """Shared admission analysis; no feature or execution recomputation."""
    summary, inference, all_pairs, decisions = [], [], [], []
    for arm, mask in admission.items():
        kept = rows[mask]
        ss, rr = kept[kept.population == "signal"], kept[kept.population == "random"]
        matched = match_candidates(ss, rr)
        si, ri = zip(*matched) if matched else ([], [])
        paired = ss.loc[list(si), ["trade_id", "segment", "week", "h1_watch"]].reset_index(drop=True)
        paired["control_id"] = rr.loc[list(ri), "trade_id"].to_numpy()
        for metric in ("net_r", "net_notional_bp"):
            paired[metric] = ss.loc[list(si), metric].to_numpy()
            paired[metric+"_random"] = rr.loc[list(ri), metric].to_numpy()
        paired["excess_bp"] = paired.net_notional_bp-paired.net_notional_bp_random
        paired["arm"] = arm
        all_pairs.append(paired)
        decisions.append(rows[["trade_id", "population"]].assign(arm=arm, admitted=mask))
        for segment in (-1, 0, 1, 2, -2):
            a, b, pp = split(ss, segment), split(base, segment), split(paired, segment)
            control = split(rr, segment)
            record = dict(arm=arm, segment=segment, **stats(a, b),
                          random_eligible_n=len(control), paired_n=len(pp),
                          matching_coverage=len(pp)/len(a) if len(a) else None,
                          matched_signal_mean_bp=float(pp.net_notional_bp.mean()) if len(pp) else None,
                          matched_random_mean_bp=float(pp.net_notional_bp_random.mean()) if len(pp) else None,
                          matched_excess_bp=float(pp.excess_bp.mean()) if len(pp) else None,
                          matched_signal_net_r=float(pp.net_r.sum()),
                          matched_random_net_r=float(pp.net_r_random.sum()))
            summary.append(record)
            inference.append(dict(arm=arm, segment=segment,
                                  net=weekly_inference(a, "net_notional_bp", cfg),
                                  excess=weekly_inference(pp, "excess_bp", cfg)))
    return summary, inference, all_pairs, decisions


def run(config_file):
    started = time.perf_counter()
    cfg = json.loads(config_file.read_text())
    out = config_file.parent
    if (out/"receipt.json").exists():
        raise FileExistsError("Completed output exists; reuse it or create a new experiment")
    committed = CODE+[str(config_file.relative_to(ROOT))]
    for path in committed:
        subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL)
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *committed], cwd=ROOT).returncode:
        raise ValueError("Commit builder/config/tests before producing results")
    receipt = json.loads((ROOT/cfg["source_receipt"]).read_text())
    source_inputs = [file_stamp(ROOT/cfg[k]) for k in ("data", "candidates")]
    if source_inputs != receipt["identity"]["inputs"]:
        raise ValueError("Frozen source inputs changed")
    source_config = receipt["identity"]["config"]
    assert source_config["symbol"] == "ETH" and source_config["buffer_percent"] == .05
    assert source_config["round_trip_cost"] == .002 and source_config["stop_window_5m"] == 5
    candles = pd.read_csv(ROOT/cfg["data"], float_precision="round_trip")
    candles.index = pd.DatetimeIndex(pd.to_datetime(candles.pop("ts"), unit="ms", utc=True)
                                    +pd.Timedelta(minutes=5)).as_unit("ns")
    ledger = pd.read_csv(ROOT/cfg["ledger"], float_precision="round_trip")
    assert ledger.trade_id.is_unique
    features = context_features(candles, cfg["timezone"])
    rows = attach_context(ledger, features)
    np.testing.assert_array_equal(pd.to_datetime(rows.entry_time, utc=True).dt.as_unit("ns").astype("int64"),
                                  candles.index.asi8[rows.entry_i.to_numpy(int)])
    for key in ledger:
        pd.testing.assert_series_equal(ledger[key], rows[key], check_names=False)
    for key in ("day_asof", "mon_asof", "h4_asof", "h4_watch", "h1_watch"):
        good = rows[key].notna()
        assert (rows.loc[good, key] <= candles.index.asi8[rows.loc[good, "entry_i"].to_numpy(int)]).all()
    signal = rows[rows.population == "signal"]
    previous = next(p for p in receipt["results"] if p["name"] == "baseline")
    for population in ("signal", "random"):
        frozen = next(x for x in previous["summary"] if x["population"] == population and x["segment"] == -1)
        source_rows = rows[rows.population == population]
        assert len(source_rows) == frozen["n"]
        np.testing.assert_allclose(source_rows.net_r.sum(), frozen["net_r"], rtol=0, atol=1e-7)
        np.testing.assert_allclose(source_rows.gross_r.sum(), frozen["gross_r"], rtol=0, atol=1e-7)
    assert (signal.h1_arrow == signal.side).all()
    assert (signal.h1_watch < candles.index.asi8[signal.entry_i.to_numpy(int)]).all()
    # Full-data/prefix parity at DST, arbitrary off-quarter and late-period cuts.
    cuts = [1234, 27000, 63500, 171500, 220001]
    for stop in cuts:
        earlier = context_features(candles.iloc[:stop], cfg["timezone"])
        pd.testing.assert_frame_equal(earlier, features.iloc[:stop], check_freq=False)

    admission = masks(rows, cfg["edge_fraction"])
    base = signal[admission["baseline"][signal.index]]
    buckets = []
    summary, inference, all_pairs, decisions = evaluate_admissions(rows, base, admission, cfg)
    for segment in (-1, 0, 1, 2, -2):
        tests = [x for x in inference if x["segment"] == segment and x["arm"] != "baseline"]
        adjusted = holm([x["excess"]["p"] if x["excess"]["p"] is not None else 1 for x in tests])
        for record, p in zip(tests, adjusted):
            record["excess"]["holm_p_14"] = p
    table = pd.DataFrame(summary)
    selection = select_development(table, cfg)
    # Descriptive bins use their own causal control context; no bin is selected here.
    for prefix in ("day", "mon"):
        labels = ["outside_countertrend", "inside_edge", "middle", "inside_far", "outside_breakout"]
        binned = rows.assign(zone=pd.cut(rows[prefix+"_u"], [-np.inf, 0, .382, .618, 1, np.inf],
                                        labels=labels, right=False))
        for (zone, population), group in binned[binned.observable].groupby(["zone", "population"], observed=True):
            for segment in (-1, 0, 1, 2, -2):
                g = split(group, segment)
                reference = split(rows[(rows.population == population) & rows.observable], segment)
                buckets.append(dict(reference=prefix, zone=str(zone), population=population,
                                    segment=segment, **stats(g, reference)))

    rows.to_csv(out/"features.csv.gz", index=False)
    pd.concat(decisions).to_csv(out/"decisions.csv.gz", index=False)
    pd.concat(all_pairs).to_csv(out/"matched_pairs.csv.gz", index=False)
    large = set(signal.loc[signal.gross_r >= 10-1e-7, "trade_id"])
    pd.concat(decisions).query("trade_id in @large").merge(
        signal[["trade_id", "entry_time", "side", "gross_r", "net_r", "peak_r", "h1_watch"]],
        on="trade_id", validate="many_to_one").to_csv(out/"winner_decisions.csv.gz", index=False)
    table.to_csv(out/"summary.csv", index=False)
    pd.DataFrame(buckets).to_csv(out/"location_buckets.csv", index=False)
    dump(out/"inference.json", inference)
    dump(out/"selection.json", selection)
    original = stats(signal, signal)
    excluded = signal[~admission["baseline"][signal.index]]
    dump(out/"coverage.json", dict(original=original, common_observable=stats(base, signal),
                                   excluded=excluded[["trade_id", "entry_time", "gross_r", "net_r"]].to_dict("records")))
    dump(out/"receipt.json", dict(
        experiment_id=cfg["experiment_id"], elapsed_seconds=time.perf_counter()-started,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        inputs=[file_stamp(ROOT/cfg[k]) for k in ("data", "candidates", "ledger", "source_receipt")],
        native_outcomes_reused=True, unchanged_native_fields=list(ledger.columns),
        baseline_rows=len(ledger), arms=len(admission), prefixes_checked=cuts,
        common_observable_signal_n=len(base), selection=selection,
        raw_outputs=[file_stamp(out/name) for name in
                     ("features.csv.gz", "decisions.csv.gz", "matched_pairs.csv.gz", "winner_decisions.csv.gz")],
        training_eligible=False, production_eligible=False,
        limitations="Known historical data; not untouched validation. Independent overlapping trades, not account returns. "
                    "20bp frozen costs; no funding. Filtered controls are rematched within same week/side/volatility; "
                    "coverage is incomplete, and unmatched trades are not silently assigned zero controls." ))
    print(json.dumps(dict(selection=selection, seconds=time.perf_counter()-started,
                         baseline=original, common_observable=len(base)), ensure_ascii=False))


def exploratory_unions(config_file):
    """Reuse saved features and old statistics; explicitly post-hoc, not a new validation."""
    started = time.perf_counter()
    cfg = json.loads(config_file.read_text())
    out = config_file.parent/"exploratory_unions"
    if (out/"receipt.json").exists():
        raise FileExistsError("Supplement already complete")
    spec_file = out/"config.json"
    spec = json.loads(spec_file.read_text())
    committed = CODE+[str(spec_file.relative_to(ROOT))]
    for path in committed:
        subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL)
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *committed], cwd=ROOT).returncode:
        raise ValueError("Commit supplement before producing results")
    old_receipt = json.loads((config_file.parent/"receipt.json").read_text())
    feature_file = config_file.parent/"features.csv.gz"
    assert file_stamp(feature_file) == next(x for x in old_receipt["raw_outputs"]
                                            if x["path"].endswith("/features.csv.gz"))
    rows = pd.read_csv(feature_file, float_precision="round_trip")
    primary = masks(rows, cfg["edge_fraction"])
    base = rows[primary["baseline"] & (rows.population == "signal")]
    admission = union_masks(rows, cfg["edge_fraction"])
    records, tests, pairs, decisions = evaluate_admissions(rows, base, admission, cfg)
    old_summary = pd.read_csv(config_file.parent/"summary.csv")
    combined = pd.concat([old_summary, pd.DataFrame(records)], ignore_index=True)
    combined["exploratory_union"] = combined.arm.str.contains("__or__")
    infer = json.loads((config_file.parent/"inference.json").read_text())+tests
    for segment in (-1, 0, 1, 2, -2):
        family = [x for x in infer if x["segment"] == segment and x["arm"] != "baseline"]
        adjusted = holm([x["excess"]["p"] if x["excess"]["p"] is not None else 1 for x in family])
        for row, value in zip(family, adjusted):
            row["excess"]["holm_p_22_exploratory_family"] = value
    combined.to_csv(out/"combined_summary.csv", index=False)
    pd.concat(decisions).to_csv(out/"decisions.csv.gz", index=False)
    pd.concat(pairs).to_csv(out/"matched_pairs.csv.gz", index=False)
    dump(out/"combined_inference.json", infer)
    dump(out/"receipt.json", dict(
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        elapsed_seconds=time.perf_counter()-started, spec=spec, new_arms=8,
        primary_selection_unchanged=old_receipt["selection"],
        inputs=[file_stamp(feature_file), file_stamp(config_file.parent/"summary.csv")],
        raw_outputs=[file_stamp(out/name) for name in ("decisions.csv.gz", "matched_pairs.csv.gz")],
        training_eligible=False, production_eligible=False,
        limitation="Exploratory definitions were frozen after seeing the primary results. No untouched validation claim."))
    print(json.dumps(dict(supplement_arms=8, seconds=time.perf_counter()-started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP/"config.json")
    parser.add_argument("--exploratory-unions", action="store_true")
    args = parser.parse_args()
    (exploratory_unions if args.exploratory_unions else run)(args.config.resolve())


if __name__ == "__main__":
    main()
