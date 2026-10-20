"""Causal vetoes for the owner's fixed 5m entry clock, ETH only.

Read completed 5m OHLC and previously audited entry-time H1/range context.
Trailing windows: Stoch5/3/3; extreme K over 4 bars; prior arrows over 12
bars excluding entry; sweep over 3 bars against a preceding 12-bar range.
Native independent outcomes are reused unchanged, never feature inputs.
This is admission research, not another execution engine or live policy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_range_context import (
    stoch, evaluate_admissions, split, select_development, stats,
)
from yoyo.evaluation.pepe_range_exits import signals
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_management_study import holm

EXP = ROOT / "experiments/active/exp-eth-5m-rejections-20261020-v1"
CODE = ["yoyo/evaluation/eth_5m_rejections.py",
        "tests/evaluation/test_eth_5m_rejections.py"]


def five_features(bars, cfg):
    """Trailing OHLC only; the sweep reference ends before its entire test window."""
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError("Duplicate or unordered bars")
    if not (bars.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
        raise ValueError("Missing 5m bars")
    out = pd.DataFrame(index=bars.index)
    width = (bars.high-bars.low).replace(0, np.nan)
    out["body"] = bars.close-bars.open
    out["clv"] = (bars.close-bars.low)/width
    arrow = signals(bars)
    k, _ = stoch(bars)
    out["k_low4"] = k.rolling(4).min()
    out["k_high4"] = k.rolling(4).max()
    n, w = cfg["sweep_window"], cfg["structure_window"]
    ref_low = bars.low.shift(n).rolling(w).min()
    ref_high = bars.high.shift(n).rolling(w).max()
    out["ref_low"], out["ref_high"] = ref_low, ref_high
    out["sweep_long"] = (bars.low.rolling(n).min() < ref_low) & (bars.close > ref_low)
    out["sweep_short"] = (bars.high.rolling(n).max() > ref_high) & (bars.close < ref_high)
    out["arrow"] = arrow
    for side, name in ((1, "long"), (-1, "short")):
        flag = arrow == side
        out["prior_"+name] = flag.shift(1).rolling(cfg["dense_window"]).sum()
        out["cumulative_"+name] = flag.cumsum()
    return out


def attach(rows, features, cfg):
    """Project each candidate's own closed-bar features, including random controls."""
    result = rows.copy()
    i = rows.entry_i.to_numpy(int)
    mapped = features.iloc[i].reset_index(drop=True)
    side = rows.side.to_numpy(int)
    for key in mapped:
        result["five_"+key] = mapped[key].to_numpy()
    result["directional_body"] = side*mapped.body.to_numpy()
    result["directional_close"] = np.where(side == 1, mapped.clv, 1-mapped.clv)
    result["prior_arrows"] = np.where(side == 1, mapped.prior_long, mapped.prior_short)
    result["local_reclaim"] = np.where(side == 1, mapped.sweep_long, mapped.sweep_short)
    result["k_extreme"] = np.where(side == 1, mapped.k_low4 < 4, mapped.k_high4 > 96.19)
    result["cost_in_r"] = cfg["round_trip_cost"]*rows.entry/rows.risk
    result["risk_percent"] = 100*rows.risk/rows.entry
    result["watch_age_minutes"] = (
        features.index.asi8[i]-rows.h1_watch.to_numpy())/60e9
    # Count raw arrows strictly after each watch; controls need not themselves be arrows.
    watch_i = features.index.searchsorted(
        pd.to_datetime(rows.h1_watch, unit="ns", utc=True), side="right")-1
    count = np.zeros(len(rows), dtype=int)
    valid = watch_i >= 0
    for direction, name in ((1, "long"), (-1, "short")):
        chosen = valid & (side == direction)
        cumulative = features["cumulative_"+name].to_numpy()
        count[chosen] = cumulative[i[chosen]]-cumulative[watch_i[chosen]]
    result["watch_arrow_ordinal"] = count
    return result


def admissions(rows, cfg):
    """Ten fixed causal vetoes, separately evaluated; no outcome column is read."""
    return {
        "baseline": np.ones(len(rows), dtype=bool),
        "cost_at_most_1r": (rows.cost_in_r <= 1).to_numpy(),
        "cost_at_most_half_r": (rows.cost_in_r <= .5).to_numpy(),
        "directional_body": (rows.directional_body > 0).to_numpy(),
        "favorable_half_close": (rows.directional_close >= .5).to_numpy(),
        "not_dense_repeat": (rows.prior_arrows < cfg["dense_prior_count"]).to_numpy(),
        "watch_within_6h": (rows.watch_age_minutes <= cfg["watch_max_minutes"]).to_numpy(),
        "not_outside_day": ((rows.day_u >= 0) | rows.day_u.isna()).to_numpy(),
        "not_outside_monday": ((rows.mon_u >= 0) | rows.mon_u.isna()).to_numpy(),
        "local_sweep_reclaim": rows.local_reclaim.to_numpy(bool),
        "recent_k_extreme": rows.k_extreme.to_numpy(bool),
    }


def gain_inference(base, keep, cfg):
    """Paired weekly bootstrap of filtered mean minus unfiltered mean.

    Aggregate all signals into common UTC weeks; resample whole weeks together.
    Null bootstrap centers the observed contrast at zero, retaining overlapping
    trade dependence inside weeks. This is not an individual-trade IID test.
    """
    frame = base[["week", "net_notional_bp"]].copy()
    frame["selected_sum"] = frame.net_notional_bp.where(keep, 0)
    frame["selected_n"] = np.asarray(keep, dtype=int)
    g = frame.groupby("week").agg(
        base_sum=("net_notional_bp", "sum"), base_n=("net_notional_bp", "size"),
        kept_sum=("selected_sum", "sum"), kept_n=("selected_n", "sum"))
    if not g.kept_n.sum():
        return dict(mean=None, ci95=[None, None], p=1., weeks=len(g))
    rng = np.random.default_rng(cfg["seed"])
    sampled = g.to_numpy()[rng.integers(0, len(g), (cfg["bootstrap_samples"], len(g)))].sum(axis=1)
    good = sampled[:, 3] > 0
    diff = sampled[good, 2]/sampled[good, 3]-sampled[good, 0]/sampled[good, 1]
    observed = g.kept_sum.sum()/g.kept_n.sum()-g.base_sum.sum()/g.base_n.sum()
    p = (1+np.count_nonzero(diff-observed >= observed))/(len(diff)+1)
    return dict(mean=float(observed), ci95=np.quantile(diff, [.025, .975]).tolist(),
                p=float(p), weeks=len(g), draws=len(diff))


def diagnostic_bins(rows):
    """Predeclared descriptive groups; these are not additional selected rules."""
    return {
        "cost_r": pd.cut(rows.cost_in_r, [-np.inf, .5, 1, 2, np.inf],
                         labels=["<=0.5", "(0.5,1]", "(1,2]", ">2"]),
        "watch_ordinal": pd.cut(rows.watch_arrow_ordinal, [-np.inf, 1, 3, 6, np.inf],
                                labels=["<=1", "2-3", "4-6", ">=7"]),
        "watch_age": pd.cut(rows.watch_age_minutes, [-np.inf, 60, 180, 360, np.inf],
                            labels=["<=1h", "1-3h", "3-6h", ">6h"]),
        "prior_arrows": pd.cut(rows.prior_arrows, [-np.inf, 0, 1, np.inf],
                               labels=["0", "1", ">=2"]),
    }


def run(config_path):
    started = time.perf_counter()
    cfg = json.loads(config_path.read_text())
    out = config_path.parent
    if (out/"receipt.json").exists():
        raise FileExistsError("Reuse completed results; do not overwrite")
    assert cfg["entry_minutes"] == 5 and cfg["round_trip_cost"] == .002
    committed = CODE+[str(config_path.relative_to(ROOT))]
    for path in committed:
        subprocess.run(["git", "ls-files", "--error-unmatch", path], check=True,
                       cwd=ROOT, stdout=subprocess.DEVNULL)
    if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *committed], cwd=ROOT).returncode:
        raise ValueError("Commit builder/config/tests first")
    prior = json.loads((ROOT/cfg["prior_receipt"]).read_text())
    for key in ("data", "candidates", "ledger", "source_receipt"):
        stamp = file_stamp(ROOT/cfg[key])
        assert stamp in prior["inputs"], key
    assert file_stamp(ROOT/cfg["prior_features"]) in prior["raw_outputs"]
    original = pd.read_csv(ROOT/cfg["prior_features"], float_precision="round_trip")
    bars = pd.read_csv(ROOT/cfg["data"], float_precision="round_trip")
    bars.index = pd.DatetimeIndex(pd.to_datetime(bars.pop("ts"), unit="ms", utc=True)
                                  +pd.Timedelta(minutes=5)).as_unit("ns")
    features = five_features(bars, cfg)
    cuts = [3901, 63500, 171503]
    for n in cuts:
        pd.testing.assert_frame_equal(five_features(bars.iloc[:n], cfg), features.iloc[:n],
                                      check_freq=False)
    rows = attach(original, features, cfg)
    for key in original:
        pd.testing.assert_series_equal(rows[key], original[key])
    np.testing.assert_allclose(rows.cost_in_r, rows.fee_r, rtol=0, atol=1e-9)
    signal = rows[rows.population == "signal"]
    assert len(signal) == 4101
    assert (signal.five_arrow == signal.side).all()
    assert (signal.h1_arrow == signal.side).all()
    assert (signal.watch_age_minutes > 0).all()
    assert (signal.watch_arrow_ordinal >= 1).all()
    assert signal[["directional_body", "directional_close", "prior_arrows",
                   "five_ref_low", "five_ref_high", "five_k_low4"]].notna().all().all()
    np.testing.assert_array_equal(pd.to_datetime(rows.entry_time, utc=True).dt.as_unit("ns").astype("int64"),
                                  bars.index.asi8[rows.entry_i.to_numpy(int)])
    masks = admissions(rows, cfg)
    records, inference, pairs, decisions = evaluate_admissions(rows, signal, masks, cfg)
    table = pd.DataFrame(records)
    selection = select_development(table, cfg)
    selection["requirement"] = "2024 only:100trades,20watches,80% gross10R/20R retention,better meanbp"
    contrasts, rejected, binned = [], [], []
    for arm, mask in masks.items():
        for segment in (-1, 0, 1, 2, -2):
            s = split(signal, segment)
            keep = mask[s.index]
            if arm != "baseline":
                contrasts.append(dict(arm=arm, segment=segment, **gain_inference(s, keep, cfg)))
                bad = s[~keep]
                rejected.append(dict(arm=arm, segment=segment, **stats(bad, s),
                                     initial_stop_15m=int(((bad.reason == 1) & (bad.exit_i-bad.entry_i <= 3)).sum())))
    for segment in (-1, 0, 1, 2, -2):
        family = [x for x in contrasts if x["segment"] == segment]
        for record, adjusted in zip(family, holm([x["p"] for x in family])):
            record["holm_p_10"] = adjusted
        family = [x for x in inference if x["segment"] == segment and x["arm"] != "baseline"]
        for record, adjusted in zip(family, holm([x["excess"]["p"] or 1 for x in family])):
            record["excess"]["holm_p_10"] = adjusted
    # Every diagnostic bin has its own eligible rematched controls, using the same evaluator.
    for name, values in diagnostic_bins(rows).items():
        arms = {str(label): (values == label).to_numpy() for label in values.cat.categories}
        bin_records, _, _, _ = evaluate_admissions(rows, signal, arms, cfg)
        for record in bin_records:
            binned.append(dict(feature=name, **record))
    pd.concat(decisions).to_csv(out/"decisions.csv.gz", index=False)
    pd.concat(pairs).to_csv(out/"matched_pairs.csv.gz", index=False)
    rows.to_csv(out/"features.csv.gz", index=False)
    winner_ids = set(signal.loc[signal.gross_r >= 10-1e-7, "trade_id"])
    pd.concat(decisions).query("trade_id in @winner_ids").merge(
        signal[["trade_id", "entry_time", "side", "gross_r", "net_r", "peak_r", "h1_watch"]],
        on="trade_id", validate="many_to_one").to_csv(out/"winner_decisions.csv.gz", index=False)
    table.to_csv(out/"summary.csv", index=False)
    pd.DataFrame(rejected).to_csv(out/"rejected_cohorts.csv", index=False)
    pd.DataFrame(binned).to_csv(out/"diagnostic_bins.csv", index=False)
    dump(out/"gain_inference.json", contrasts)
    dump(out/"inference.json", inference)
    dump(out/"selection.json", selection)
    dump(out/"receipt.json", dict(
        experiment_id=cfg["experiment_id"], generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        elapsed_seconds=time.perf_counter()-started,
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        inputs=[file_stamp(ROOT/cfg[k]) for k in ("data", "prior_features", "ledger", "prior_receipt")],
        unchanged_input_fields=list(original.columns), native_outcomes_reused=True,
        signal_n=len(signal), control_n=len(rows)-len(signal), arms=len(masks), prefixes=cuts,
        unknown_range_passed={key: int(signal[key+"_u"].isna().sum()) for key in ("day", "mon")},
        selection=selection, training_eligible=False, production_eligible=False,
        raw_outputs=[file_stamp(out/name) for name in (
            "features.csv.gz", "decisions.csv.gz", "matched_pairs.csv.gz", "winner_decisions.csv.gz")],
        limits="Previously examined history, no untouched validation. Independent overlapping R, not account returns. "
               "20bp cost, no funding. Native exits unchanged. Random matching incomplete. "
               "Fixed binary vetoes have no trained AUC or top-decile scores. No Pine or live change."))
    print(json.dumps(dict(seconds=time.perf_counter()-started, selection=selection,
                         baseline=stats(signal, signal)), ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP/"config.json")
    run(parser.parse_args().config.resolve())
