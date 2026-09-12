"""Stream bounded summaries and matched-event controls for the frozen V7 replay.

Consumes committed replay artifacts, never recomputes or tunes entry features.
Every realized trade is included in descriptive metrics. Random controls use a
predeclared outcome-independent hash sample of up to 16 realized entries per
stream/variant/side, matched to the same market, month and causal volatility
quartile. These are paired events, not a simulated shared-capital portfolio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study_post import matched_random_controls

START = pd.Timestamp("2024-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/config.json"


def bools(series: pd.Series) -> pd.Series:
    """Parse CSV booleans explicitly; the string 'False' is not truthy here."""
    if series.dtype == bool:
        return series
    parsed = series.map({True: True, False: False, "True": True, "False": False,
                         "true": True, "false": False, 1: True, 0: False})
    if parsed.isna().any():
        raise ValueError("unrecognized boolean value")
    return parsed.astype(bool)


def event_metrics(trades: pd.DataFrame) -> dict:
    """Describe closed events without inventing a cross-market equity curve."""
    done = trades.loc[~bools(trades.censored)]
    net = pd.to_numeric(done.net_return, errors="raise")
    r = pd.to_numeric(done.net_r, errors="raise")
    if not np.isfinite(net.to_numpy()).all() or not np.isfinite(r.to_numpy()).all():
        raise ValueError("closed trades require finite net return and net R")
    positive, negative = float(net.clip(lower=0).sum()), float(-net.clip(upper=0).sum())
    result = {"entries": len(trades), "closed": len(done), "censored": len(trades)-len(done),
              "wins": int(net.gt(0).sum()), "win_rate": float(net.gt(0).mean()),
              "event_pf": positive / negative if negative else np.nan,
              "sum_net_r": float(r.sum()), "mean_net_r": float(r.mean()),
              "median_net_r": float(r.median()), "sum_event_net_return": float(net.sum()),
              "mean_event_net_return": float(net.mean()),
              "net_ge_10r": int(r.ge(10).sum()),
              "positive_net_return_sum": positive, "negative_net_return_sum": negative}
    if "mfe_r" in done:
        result["mfe_ge_10r"] = int(done.mfe_r.ge(10).sum())
    return result


def fixed_sample(trades: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Sample by entry identity, never profit, MFE, win, symbol rank or exit."""
    out = trades.loc[~bools(trades.censored)].copy()
    if out.empty:
        return out
    out["_hash"] = [hashlib.sha256(f"v7-control-v1|{t}|{int(s)}".encode()).hexdigest()
                    for t, s in zip(out.signal_bar_open, out.side)]
    return out.sort_values("_hash").groupby(["variant", "side"], sort=False).head(limit).drop(columns="_hash")


def controls_for_stream(folder: Path, limit: int) -> pd.DataFrame:
    """Reuse one control path across variants with identical entry identity.

    BB-ready arms match random opportunities with the same history-readiness
    restriction. All arms share raw V6 reversal exits. V1's common long arm
    supplies raw V6 shorts as exit-only events, as frozen in the replay config.
    """
    trades = pd.read_csv(folder / "trades.csv.gz")
    if trades.empty:
        return pd.DataFrame()
    sampled = fixed_sample(trades, limit)
    if sampled.empty:
        return pd.DataFrame()
    cache_path = folder / "control_cache.pkl.gz"
    cache_receipt = json.loads((folder / "control_cache.receipt.json").read_text())
    if hashlib.sha256(cache_path.read_bytes()).hexdigest() != cache_receipt["cache_sha256"]:
        raise ValueError(f"control cache hash mismatch: {folder.name}")
    cache = pd.read_pickle(cache_path)
    sampled["signal_bar_open"] = pd.to_datetime(sampled.signal_bar_open, utc=True)
    sampled["common_ready"] = sampled.variant.str.contains("common_ready|v7_bb", regex=True)
    rows = []
    for (ready, side), subset in sampled.groupby(["common_ready", "side"]):
        keys = ["signal_bar_open", "side"]
        # Same frozen entry time, side and execution must have the same outcome.
        ranges = subset.groupby(keys)[["net_r", "net_return"]].agg(lambda x: x.max()-x.min())
        if ranges.abs().to_numpy().max(initial=0) > 1e-9:
            raise ValueError(f"same-entry common-execution outcomes differ: {folder.name}")
        selected = subset.drop_duplicates(keys)
        context = dict(cache)
        context["bars"] = cache["bars"].copy()
        opposite = cache["signals"].short_signal if side == 1 else cache["signals"].long_signal
        context["bars"]["ready"] &= ~opposite.astype(bool)
        if ready:
            context["bars"]["ready"] &= cache["bb_ready"].astype(bool)
        pairs, _ = matched_random_controls(context, selected, tick=float(cache["tick"]),
                                            fold_start=START, fold_end=END, seeds=[0])
        pairs = pairs.rename(columns={"target_time": "signal_bar_open"})
        pairs["signal_bar_open"] = pd.to_datetime(pairs.signal_bar_open, utc=True)
        identities = subset[[*keys, "variant", "venue", "symbol", "timeframe_min", "segment"]]
        rows.append(identities.merge(pairs, on=keys, how="left", validate="many_to_one"))
    return pd.concat(rows, ignore_index=True)


def grouped_metrics(trades: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for group, part in trades.groupby(keys, dropna=False, sort=True):
        if not isinstance(group, tuple):
            group = (group,)
        rows.append({**dict(zip(keys, group)), **event_metrics(part)})
    return pd.DataFrame(rows)


def control_metrics(pairs: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    pairs = pairs.copy()
    for column in ("target_net_r", "control_net_r", "net_r_difference", "net_return_difference"):
        if column not in pairs:
            pairs[column] = np.nan
    rows = []
    for group, part in pairs.groupby(keys, dropna=False, sort=True):
        if not isinstance(group, tuple):
            group = (group,)
        good = part.loc[bools(part.matched)]
        inference = {"matched_months": 0, "equal_month_mean_net_r_difference": np.nan,
                     "exploratory_month_block_sign_flip_p": np.nan}
        if len(good):
            month = pd.to_datetime(good.signal_bar_open, utc=True).dt.strftime("%Y-%m")
            blocks = good.groupby(month).net_r_difference.mean().dropna().to_numpy(float)
            inference["matched_months"] = len(blocks)
            if len(blocks) >= 6:
                observed = float(blocks.mean())
                signs = np.random.default_rng(0).choice([-1.,1.], size=(9999,len(blocks)))
                null = (signs * blocks).mean(axis=1)
                inference.update(equal_month_mean_net_r_difference=observed,
                                 exploratory_month_block_sign_flip_p=float((1+(null>=observed).sum())/10000))
        rows.append({**dict(zip(keys, group)), **inference, "sampled_targets": len(part), "matched_targets": len(good),
                     "target_mean_net_r": good.target_net_r.mean(),
                     "control_mean_net_r": good.control_net_r.mean(),
                     "paired_mean_net_r_difference": good.net_r_difference.mean(),
                     "paired_mean_net_return_difference": good.net_return_difference.mean()})
    return pd.DataFrame(rows)


def exact_retention(trades: pd.DataFrame) -> pd.DataFrame:
    """Join executed entry identities; accepted-but-not-executed is not retained."""
    rows = []
    identity = ["venue", "symbol", "timeframe_min", "segment", "side", "signal_bar_open"]
    closed = trades.loc[~bools(trades.censored)]
    for base, target in (("v1_common_execution_long", "v7_bb_long"),
                         ("v1_common_ready_long", "v7_bb_long"),
                         ("v6_unfiltered_long", "v7_bb_long"),
                         ("v6_common_ready_long", "v7_bb_long"),
                         ("v6_unfiltered_both", "v7_bb_both"),
                         ("v6_common_ready_both", "v7_bb_both")):
        a = closed.loc[closed.variant.eq(base)]
        b = trades.loc[trades.variant.eq(target), identity].drop_duplicates().assign(retained=True)
        joined = a.merge(b, on=identity, how="left", validate="one_to_one")
        for minutes, part in joined.groupby("timeframe_min"):
            tails = part.net_r.ge(10)
            retained = part.retained.eq(True)
            rows.append({"baseline": base, "target": target, "timeframe_min": minutes,
                         "baseline_closed": len(part), "baseline_net_ge_10r": int(tails.sum()),
                         "same_entry_tails_retained": int((tails & retained).sum()),
                         "same_entry_tails_missed": int((tails & ~retained).sum())})
    return pd.DataFrame(rows)


def process(raw: Path, output: Path, *, controls: bool, allow_partial: bool) -> None:
    config = json.loads(CONFIG.read_text())
    manifest = json.loads((raw / "manifest.json").read_text())
    if manifest.get("config_sha256") != hashlib.sha256(CONFIG.read_bytes()).hexdigest():
        raise ValueError("replay and report configuration hashes differ")
    if not allow_partial and manifest.get("completed_streams") != manifest.get("covered_streams_frozen"):
        raise ValueError("full report requires all frozen streams completed")
    output.mkdir(parents=True, exist_ok=True)
    identity = {"config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
                "post_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "replay_code_sha256": manifest["source_code_sha256"], "raw": str(raw.resolve())}
    identity_path = output / "post_identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("postprocessing code/config/input changed; use a new output directory")
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True))
    control_root = output / "controls"
    control_root.mkdir(exist_ok=True)
    folders = sorted(path.parent for path in (raw / "streams").glob("*/completion.json")
                     if not path.parent.name.startswith(".")
                     and json.loads(path.read_text()).get("key") == path.parent.name)
    if len(folders) != manifest["completed_streams"]:
        raise ValueError("materialized stream count differs from replay manifest")
    trades, signal_summaries, accounts, matches, conflicts = [], [], [], [], []
    for i, folder in enumerate(folders, 1):
        needed = {"variant", "venue", "symbol", "timeframe_min", "segment", "side",
                  "signal_bar_open", "entry_time", "exit_time", "entry_price", "exit_price",
                  "exit_reason", "censored", "net_return", "net_r", "mfe_r"}
        ledger = pd.read_csv(folder / "trades.csv.gz", usecols=lambda name: name in needed)
        if len(ledger):
            trades.append(ledger)
        for name, accumulator in (("signal_summary.csv", signal_summaries), ("trade_summary.csv", accounts)):
            try:
                part = pd.read_csv(folder / name)
                if len(part):
                    accumulator.append(part)
            except pd.errors.EmptyDataError:
                pass
        conflict_path = folder / "conflicts.csv"
        if conflict_path.exists():
            try:
                conflict = pd.read_csv(conflict_path)
                if len(conflict):
                    conflicts.append(conflict)
            except pd.errors.EmptyDataError:
                pass
        if controls:
            destination = control_root / f"{folder.name}.csv.gz"
            pair_receipt_path = destination.with_suffix(".receipt.json")
            if destination.exists():
                receipt = json.loads(pair_receipt_path.read_text())
                if hashlib.sha256(destination.read_bytes()).hexdigest() != receipt["pairs_sha256"]:
                    raise ValueError(f"control pairs changed: {destination}")
                try:
                    paired = pd.read_csv(destination)
                except pd.errors.EmptyDataError:
                    paired = pd.DataFrame()
            else:
                paired = controls_for_stream(folder, config["controls"]["max_targets_per_stream_variant_side"])
                paired.to_csv(destination, index=False, compression="gzip")
                pair_receipt_path.write_text(json.dumps({"pairs_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}, indent=2))
            if len(paired):
                matches.append(paired)
        if i % 25 == 0 or i == len(folders):
            print(json.dumps({"post_streams": i, "available": len(folders), "controls": controls}), flush=True)
    if not trades:
        raise ValueError("no trade ledgers available")
    ledger = pd.concat(trades, ignore_index=True)
    ledger["signal_bar_open"] = pd.to_datetime(ledger.signal_bar_open, utc=True)
    ledger["year_block"] = np.where(ledger.signal_bar_open < pd.Timestamp("2025-09-10T00:00:00Z"),
                                    "2024-09_to_2025-09", "2025-09_to_2026-09")
    ledger.to_csv(output / "common_execution_trades.csv.gz", index=False, compression="gzip")
    for name, keys in (("metrics_overall", ["variant"]),
                       ("metrics_timeframe", ["variant", "timeframe_min"]),
                       ("metrics_side", ["variant", "timeframe_min", "side"]),
                       ("metrics_year", ["variant", "timeframe_min", "year_block"]),
                       ("metrics_venue", ["variant", "venue", "timeframe_min"])):
        grouped_metrics(ledger, keys).to_csv(output / f"{name}.csv", index=False)
    if signal_summaries:
        signal = pd.concat(signal_summaries, ignore_index=True)
        signal.groupby(["variant", "minutes", "side"])[["entry_candidates", "entry_admitted", "raw_v6_short_exit_feed"]].sum().to_csv(output / "signal_counts.csv")
    if accounts:
        pd.concat(accounts, ignore_index=True).to_csv(output / "independent_stream_closed_balance_metrics.csv.gz", index=False, compression="gzip")
    if conflicts:
        conflict = pd.concat(conflicts, ignore_index=True)
        conflict.to_csv(output / "v1_same_bar_exit_priority_conflicts.csv.gz", index=False, compression="gzip")
        conflict.groupby(["venue", "timeframe_min"]).size().rename("suppressed_v1_long_entries").to_csv(output / "v1_conflict_counts.csv")
    exact_retention(ledger).to_csv(output / "exact_entry_tail_retention.csv", index=False)
    native_info = manifest["historical_v1_native"]
    native_path = Path(native_info["path"])
    if hashlib.sha256(native_path.read_bytes()).hexdigest() != native_info["sha256"]:
        raise ValueError("native V1 reference changed")
    native = pd.read_csv(native_path)
    native = native.loc[native.timeframe_min.isin([30, 60, 240])].copy()
    native["variant"] = "historical_v1_native"
    grouped_metrics(native, ["variant", "timeframe_min"]).to_csv(output / "historical_v1_native_metrics.csv", index=False)
    if matches:
        paired = pd.concat(matches, ignore_index=True)
        paired.to_csv(output / "sampled_matched_controls.csv.gz", index=False, compression="gzip")
        control_metrics(paired, ["variant", "timeframe_min"]).to_csv(output / "matched_control_metrics.csv", index=False)
    receipt = {"raw": str(raw.resolve()), "raw_manifest_sha256": hashlib.sha256((raw / "manifest.json").read_bytes()).hexdigest(),
               "post_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
               "available_streams": len(folders), "frozen_streams": manifest["covered_streams_frozen"],
               "complete": len(folders) == manifest["covered_streams_frozen"], "controls": controls,
               "no_cross_market_portfolio": True, "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}
    (output / "post_manifest.json").write_text(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--allow-partial", action="store_true", help="diagnostic partial report, never full-coverage result")
    arguments = parser.parse_args()
    process(arguments.raw, arguments.output, controls=arguments.controls, allow_partial=arguments.allow_partial)
