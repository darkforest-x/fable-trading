"""Pure, receipt-neutral aggregation for the preregistered SPIKE V1 exit study.

This module only reads stream ledgers already written by the parent runner. It
does not select events, inspect OHLC, or create controls.  Closed outcomes are
the only inputs to return statistics; censored rows stay visible as counts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_market_breadth_study import canonical_asset

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v1-triple-exit-20260914-v1"
PRIMARY_ARMS = ("baseline", "triple", "filtered_triple")
ALL_ARMS = ("baseline", "adverse65", "be05", "lock50", "triple", "filters_only", "filtered_triple")
SEED, DRAWS = 20260914, 5000
CUT = pd.Timestamp("2025-09-01T00:00:00Z")
HOLDOUT = pd.Timestamp("2026-05-04T00:00:00Z")
SUMMARY_COLUMNS = ("cohort", "event_scope", "asset_scope", "period", "group_type", "timeframe_min", "venue", "symbol", "arm",
                   "events", "trades", "censored", "sum_net_r", "mean_net_r", "median_net_r", "win_rate", "pf_r",
                   "closed_event_cumulative_r_maxdd", "top5_positive_net_r", "top5_profit_share", "sum_without_top5_net_r", "realized_ge_10r", "cost_r",
                   "mean_net_r_ci95_low", "mean_net_r_ci95_high",
                   "mean_matched_excess_net_r", "excess_ci95_low", "excess_ci95_high", "excess_one_sided_p",
                   "excess_one_sided_p_bonferroni9", "cross_cut_excluded")


def _empty(columns: Iterable[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def read_stream_ledgers(results: Path, cohort: str, name: str) -> pd.DataFrame:
    """Read a named ledger from all completed stream folders, or an empty frame."""
    paths = sorted((results / cohort / "streams").glob(f"*/{name}.csv.gz"))
    return pd.concat([pd.read_csv(path) for path in paths], ignore_index=True) if paths else pd.DataFrame()


def normalize_outcomes(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize only persisted outcome fields; NaN remains missing, never zero."""
    if frame.empty:
        return frame.copy()
    required = {"event_id", "arm", "entry_time", "exit_time", "net_r", "censored", "base_asset", "timeframe_min", "dedup_keep", "entry_day"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("outcomes missing: " + ", ".join(sorted(missing)))
    out = frame.copy()
    out["entry_time"] = pd.to_datetime(out.entry_time, utc=True)
    out["exit_time"] = pd.to_datetime(out.exit_time, utc=True)
    out["censored"] = out.censored.astype(bool)
    out["dedup_keep"] = out.dedup_keep.astype(bool)
    for column in ("net_r", "net_return", "gross_return", "risk_fraction_at_entry", "mfe_r", "mae_r"):
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def identity_dedup_projection(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Project canonical asset identity and causal same-day deduplication.

    The source runner's literal ticker identity can treat exchange denomination
    wrappers (``1000PEPE`` versus ``PEPE``) as different assets.  This is a
    read-time metadata projection only: it is derived once from the native
    baseline event, before any arm return is inspected, and never changes the
    persisted ledger.  The protocol chooses the earliest executable event per
    canonical asset and UTC entry day; exact-time ties prefer larger timeframe,
    then Binance, OKX, Gate, and finally event ID.
    """
    baseline = outcomes.loc[outcomes.arm.eq("baseline")].copy()
    if baseline.empty:
        raise ValueError("native outcomes require one baseline row per event for identity deduplication")
    identity = baseline.loc[:, ["event_id", "entry_time", "entry_day", "timeframe_min", "venue", "base_asset", "dedup_keep"]].copy()
    if identity.event_id.duplicated().any():
        raise ValueError("native baseline event_id is not unique for identity deduplication")
    identity["entry_day"] = identity.entry_time.dt.strftime("%Y-%m-%d")
    identity = identity.rename(columns={"base_asset": "original_base_asset", "dedup_keep": "original_dedup_keep"})
    identity["base_asset"] = identity.original_base_asset.map(canonical_asset)
    venue_rank = {"binance": 0, "okx": 1, "gate": 2}
    identity["_venue_rank"] = identity.venue.astype(str).str.lower().map(venue_rank).fillna(len(venue_rank)).astype(int)
    identity = identity.sort_values(
        ["base_asset", "entry_day", "entry_time", "timeframe_min", "_venue_rank", "event_id"],
        ascending=[True, True, True, False, True, True], kind="mergesort")
    identity["dedup_keep"] = ~identity.duplicated(["base_asset", "entry_day"], keep="first")
    return identity.loc[:, ["event_id", "original_base_asset", "base_asset", "dedup_keep", "original_dedup_keep"]].sort_values("event_id", kind="mergesort").reset_index(drop=True)


def apply_identity_dedup(outcomes: pd.DataFrame, controls: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Attach the native identity projection to all arms and matched controls.

    Controls inherit their parent's canonical asset and dedup decision.  Their
    random entry time is deliberately absent from this selection path.
    """
    identity = identity_dedup_projection(outcomes)
    native = outcomes.copy()
    native["original_base_asset"] = native.base_asset
    native["original_dedup_keep"] = native.dedup_keep
    native = native.drop(columns=["base_asset", "dedup_keep"]).merge(
        identity[["event_id", "base_asset", "dedup_keep"]], how="left", on="event_id", validate="many_to_one")
    if native.base_asset.isna().any():
        raise ValueError("native arm is missing its baseline identity projection")
    if controls.empty:
        return native, controls.copy(), identity
    if "parent_event_id" not in controls:
        raise ValueError("controls require parent_event_id for native identity deduplication")
    control = controls.copy()
    control["original_base_asset"] = control.base_asset
    control["original_dedup_keep"] = control.dedup_keep
    control = control.drop(columns=["base_asset", "dedup_keep"]).merge(
        identity[["event_id", "base_asset", "dedup_keep"]].rename(columns={"event_id": "parent_event_id"}),
        how="left", on="parent_event_id", validate="many_to_one")
    if control.base_asset.isna().any():
        missing = int(control.base_asset.isna().sum())
        raise ValueError(f"{missing} controls have no native parent identity projection")
    return native, control, identity


def select_scope(frame: pd.DataFrame, *, dedup: bool, ex_rave: bool) -> pd.DataFrame:
    """Apply only protocol-fixed raw/dedup and RAVE sensitivity scopes."""
    out = frame.loc[frame.dedup_keep].copy() if dedup else frame.copy()
    return out.loc[~out.base_asset.eq("RAVE")].copy() if ex_rave else out


def closed(frame: pd.DataFrame) -> pd.DataFrame:
    """Return realized rows with finite net R; censoring never becomes a zero."""
    return frame.loc[~frame.censored & np.isfinite(frame.net_r)].copy()


def _drawdown(values: pd.Series) -> float:
    if not len(values):
        return np.nan
    # Concurrent exits are one event-clock point; lexical IDs must not invent
    # an order, peak, or drawdown inside the same close timestamp.
    by_exit = values.groupby(level=0, sort=True).sum()
    curve = np.r_[0.0, by_exit.to_numpy(dtype=float).cumsum()]
    return float(np.min(curve - np.maximum.accumulate(curve)))


def arm_metrics(frame: pd.DataFrame, *, group: dict[str, object] | None = None) -> dict[str, object]:
    """Summarize one arm without treating overlapping events as an account curve."""
    group = group or {}
    realized = closed(frame).sort_values(["exit_time", "entry_time", "event_id"], kind="mergesort")
    values = realized.net_r
    positives = values.loc[values > 0].sort_values(ascending=False)
    top5 = float(positives.head(5).sum()) if len(positives) else 0.0
    positive_sum, negative_sum = float(values.loc[values > 0].sum()), float(values.loc[values < 0].sum())
    cost_r = ((realized.net_return - realized.gross_return) / realized.risk_fraction_at_entry).sum() if (
        len(realized) and "net_return" in realized and "gross_return" in realized and "risk_fraction_at_entry" in realized) else np.nan
    return group | {"events": int(len(frame)), "trades": int(len(realized)), "censored": int(frame.censored.sum()),
                    "cross_cut_excluded": int(frame.cross_cut_excluded.sum()) if "cross_cut_excluded" in frame else 0,
                    "win_rate": float((values > 0).mean()) if len(values) else np.nan,
                    "pf_r": positive_sum / abs(negative_sum) if negative_sum < 0 else np.nan,
                    "sum_net_r": float(values.sum()) if len(values) else np.nan,
                    "mean_net_r": float(values.mean()) if len(values) else np.nan,
                    "median_net_r": float(values.median()) if len(values) else np.nan,
                    "top5_positive_net_r": top5, "sum_without_top5_net_r": float(values.sum() - top5) if len(values) else np.nan,
                    "top5_profit_share": top5 / positive_sum if positive_sum > 0 else np.nan,
                    "realized_ge_10r": int((values >= 10).sum()), "cost_r": float(cost_r) if np.isfinite(cost_r) else np.nan,
                    "closed_event_cumulative_r_maxdd": _drawdown(values.set_axis(realized.exit_time))}


def grouped_metrics(frame: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Produce arm metrics by an explicit identity/time grouping."""
    if frame.empty:
        return _empty([*by, "arm", "events", "trades", "censored"])
    rows = []
    for keys, part in frame.groupby([*by, "arm"], dropna=False, sort=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        rows.append(arm_metrics(part, group=dict(zip([*by, "arm"], keys))))
    return pd.DataFrame(rows)


def time_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach frozen development/OOS/holdout-era labels from entry and exit clocks."""
    out = frame.copy()
    out["period"] = np.where((out.entry_time < CUT) & (out.exit_time <= CUT), "dev",
                               np.where((out.entry_time >= CUT) & (out.entry_time < HOLDOUT), "oos_pre_holdout",
                                        np.where(out.entry_time >= HOLDOUT, "holdout_era", "cross_cut_or_censored")))
    out["entry_month"] = out.entry_time.dt.strftime("%Y-%m")
    out["entry_year"] = out.entry_time.dt.year.astype("Int64")
    out["strict_dev"] = out.entry_time.lt(CUT) & out.exit_time.le(CUT) & ~out.censored
    out["cross_cut_excluded"] = out.entry_time.lt(CUT) & ~out.exit_time.le(CUT)
    return out


def protocol_period_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Report the fixed all/dev/OOS periods without mixing a cut-crossing dev trade."""
    parts = {
        "all": frame,
        "dev": frame.loc[frame.strict_dev],
        "oos": frame.loc[frame.entry_time.ge(CUT)],
        "oos_pre_holdout": frame.loc[frame.entry_time.ge(CUT) & frame.entry_time.lt(HOLDOUT)],
        "holdout_era": frame.loc[frame.entry_time.ge(HOLDOUT)],
    }
    rows = []
    for period, part in parts.items():
        for arm, arm_part in part.groupby("arm", sort=True):
            rows.append(arm_metrics(arm_part, group={"period": period, "arm": arm}))
    return pd.DataFrame(rows)


def matched_excess(native: pd.DataFrame, controls: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Collapse each parent's closed controls before calculating one native excess.

    A parent therefore contributes at most one excess observation per arm rather
    than twenty pseudo-independent control trades.
    """
    if native.empty:
        return pd.DataFrame(), pd.DataFrame()
    if controls.empty or "parent_event_id" not in controls:
        audit = native[["event_id", "arm"]].rename(columns={"event_id": "parent_event_id"}).assign(
            control_rows=0, closed_controls=0, matched=False, match_status="no_controls")
        return pd.DataFrame(), audit
    native_closed = closed(native).rename(columns={"event_id": "parent_event_id", "net_r": "native_net_r"})
    control_closed = closed(controls)
    grouped = control_closed.groupby(["parent_event_id", "arm"], as_index=False).agg(
        closed_controls=("event_id", "size"), control_mean_net_r=("net_r", "mean"))
    total = controls.groupby(["parent_event_id", "arm"], as_index=False).agg(control_rows=("event_id", "size"))
    audit = native[["event_id", "arm"]].rename(columns={"event_id": "parent_event_id"}).merge(total, how="left", on=["parent_event_id", "arm"]).merge(grouped, how="left", on=["parent_event_id", "arm"])
    audit[["control_rows", "closed_controls"]] = audit[["control_rows", "closed_controls"]].fillna(0).astype(int)
    audit["matched"] = audit.closed_controls.gt(0)
    audit["match_status"] = np.where(audit.control_rows.eq(0), "no_controls", np.where(audit.closed_controls.eq(0), "all_controls_censored", "matched"))
    paired = native_closed.merge(grouped, how="inner", on=["parent_event_id", "arm"] if "parent_event_id" in native_closed else ["event_id", "arm"])
    if "event_id" in paired and "parent_event_id" not in paired:
        paired = paired.rename(columns={"event_id": "parent_event_id"})
    if not paired.empty:
        paired["excess_net_r"] = paired.native_net_r - paired.control_mean_net_r
    return paired, audit


def cluster_bootstrap(values: pd.DataFrame, value: str, *, draws: int = DRAWS, seed: int = SEED) -> dict[str, float]:
    """Resample asset×entry-day clusters for a mean-R 95% interval."""
    numeric = pd.to_numeric(values[value], errors="coerce")
    part = values.loc[np.isfinite(numeric)].copy()
    part[value] = pd.to_numeric(part[value], errors="coerce")
    if part.empty:
        return {"clusters": 0, "mean": np.nan, "ci95_low": np.nan, "ci95_high": np.nan}
    clusters = part.groupby(["base_asset", "entry_day"], sort=True)[value].agg(["sum", "count"])
    sums, counts = clusters["sum"].to_numpy(float), clusters["count"].to_numpy(float)
    rng, means, width = np.random.default_rng(seed), np.empty(draws), 256
    for start in range(0, draws, width):
        size = min(width, draws - start)
        picked = rng.integers(0, len(sums), size=(size, len(sums)))
        means[start:start + size] = sums[picked].sum(axis=1) / counts[picked].sum(axis=1)
    return {"clusters": len(sums), "mean": float(part[value].mean()), "ci95_low": float(np.quantile(means, .025)), "ci95_high": float(np.quantile(means, .975))}


def cluster_sign_p(values: pd.DataFrame, value: str, *, draws: int = DRAWS, seed: int = SEED) -> float:
    """One-sided cluster sign/permutation p for positive mean excess."""
    numeric = pd.to_numeric(values[value], errors="coerce")
    part = values.loc[np.isfinite(numeric)].copy()
    part[value] = pd.to_numeric(part[value], errors="coerce")
    if part.empty:
        return np.nan
    cluster_values = part.groupby(["base_asset", "entry_day"], sort=True)[value].mean().to_numpy(float)
    observed, rng = float(cluster_values.mean()), np.random.default_rng(seed)
    simulated = (rng.choice(np.array([-1., 1.]), size=(draws, len(cluster_values))) * cluster_values).mean(axis=1)
    return float((1 + np.count_nonzero(simulated >= observed)) / (draws + 1))


def primary_statistics(frame: pd.DataFrame, controls: pd.DataFrame, *, analysis_scope: str, infer_excess: bool = False,
                       timeframe_min: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return main-arm summaries, matched parent-level excess, and its inference."""
    main = frame.loc[frame.arm.isin(PRIMARY_ARMS)].copy()
    if infer_excess:
        # This is the preregistered top20 test family, never dev-plus-test.
        main = main.loc[main.entry_time.ge(CUT)].copy()
        controls = controls.loc[controls.entry_time.ge(CUT)].copy() if not controls.empty else controls
    if timeframe_min is not None:
        main = main.loc[main.timeframe_min.eq(timeframe_min)].copy()
        controls = controls.loc[controls.timeframe_min.eq(timeframe_min)].copy() if not controls.empty else controls
    paired, matching = matched_excess(main, controls)
    rows = []
    for arm, part in main.groupby("arm", sort=True):
        for scope, scoped in (("all", part), ("ex_rave", part.loc[~part.base_asset.eq("RAVE")])):
            metrics = arm_metrics(scoped, group={"scope": scope, "arm": arm})
            ci = cluster_bootstrap(closed(scoped), "net_r")
            matched = paired.loc[paired.arm.eq(arm)] if not paired.empty and "arm" in paired else pd.DataFrame()
            if scope == "ex_rave" and not matched.empty:
                matched = matched.loc[~matched.base_asset.eq("RAVE")]
            excess = cluster_bootstrap(matched, "excess_net_r") if not matched.empty else cluster_bootstrap(pd.DataFrame(columns=["base_asset", "entry_day", "excess_net_r"]), "excess_net_r")
            # The preregistered nine-test family is only top20 fixed-test
            # all-asset 15m/1h/4h × three main arms. Ex-RAVE is sensitivity.
            p = cluster_sign_p(matched, "excess_net_r") if infer_excess and scope == "all" and not matched.empty else np.nan
            rows.append(metrics | {"analysis_scope": analysis_scope, "timeframe_min": timeframe_min,
                                   "cross_cut_excluded": int(scoped.cross_cut_excluded.sum()),
                                   "mean_net_r_ci95_low": ci["ci95_low"], "mean_net_r_ci95_high": ci["ci95_high"],
                                   "mean_matched_excess_net_r": excess["mean"], "excess_ci95_low": excess["ci95_low"],
                                   "excess_ci95_high": excess["ci95_high"], "excess_clusters": excess["clusters"],
                                   "excess_one_sided_p": p, "excess_one_sided_p_bonferroni9": min(1., p * 9) if np.isfinite(p) else np.nan})
    return pd.DataFrame(rows), matching, paired


def baseline_opportunity_capture(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare each candidate exit with its event's frozen baseline opportunity.

    Candidate MFE is deliberately not the denominator: an early exit may shrink
    its own observed MFE.  Only closed baseline paths with positive baseline MFE
    define an opportunity denominator.
    """
    base = closed(frame.loc[frame.arm.eq("baseline")]).loc[:, ["event_id", "base_asset", "entry_day", "mfe_r"]].rename(columns={"mfe_r": "baseline_mfe_r"})
    candidate = closed(frame.loc[~frame.arm.eq("baseline")]).copy()
    paired = candidate.merge(base, how="inner", on="event_id", validate="many_to_one")
    if paired.empty:
        return pd.DataFrame(), _empty(("arm", "baseline_mfe_bucket", "events", "mean_capture", "median_capture"))
    paired = paired.loc[paired.baseline_mfe_r.gt(0)].copy()
    paired["baseline_opportunity_capture"] = paired.net_r / paired.baseline_mfe_r
    # Mutually exclusive conservative-baseline opportunity intervals: (0,2],
    # (2,10], and >10. Stop bars are excluded by the frozen baseline MFE path.
    paired["baseline_mfe_bucket"] = np.select([paired.baseline_mfe_r.gt(10), paired.baseline_mfe_r.gt(2)], ["gt10", "gt2_to_10"], default="gt0_to_2")
    summary = paired.groupby(["arm", "baseline_mfe_bucket"], as_index=False).agg(
        events=("event_id", "size"), mean_capture=("baseline_opportunity_capture", "mean"),
        median_capture=("baseline_opportunity_capture", "median"))
    return paired, summary


def paired_baseline_triple_delta(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconcile triple's closed-R change against the same baseline event.

    The paired delta deliberately uses only events closed in *both* arms.  It
    separately reports triple-only and baseline-only closes, so an arm closing
    a baseline-censored event cannot be presented as an exit-rule improvement.
    """
    columns = ["event_id", "base_asset", "entry_day", "baseline_net_r", "triple_net_r", "delta_net_r", "closure_case"]
    baseline = frame.loc[frame.arm.eq("baseline"), ["event_id", "base_asset", "entry_day", "censored", "net_r"]].rename(
        columns={"censored": "baseline_censored", "net_r": "baseline_net_r"})
    triple = frame.loc[frame.arm.eq("triple"), ["event_id", "base_asset", "entry_day", "censored", "net_r"]].rename(
        columns={"censored": "triple_censored", "net_r": "triple_net_r"})
    paired = baseline.merge(triple.drop(columns=["base_asset", "entry_day"]), how="outer", on="event_id", validate="one_to_one")
    if paired.empty:
        return _empty(columns), _empty(("asset_scope", "both_closed_events", "both_closed_baseline_net_r", "both_closed_triple_net_r", "both_closed_delta_net_r", "both_closed_delta_mean_net_r", "triple_only_closed_events", "triple_only_closed_net_r", "baseline_only_closed_events", "baseline_only_closed_net_r", "observed_total_delta_net_r", "reconciled_total_delta_net_r", "reconciliation_difference_net_r"))
    base_closed = ~paired.baseline_censored.fillna(True) & np.isfinite(paired.baseline_net_r)
    triple_closed = ~paired.triple_censored.fillna(True) & np.isfinite(paired.triple_net_r)
    paired["closure_case"] = np.select([base_closed & triple_closed, ~base_closed & triple_closed, base_closed & ~triple_closed], ["both_closed", "triple_only_closed", "baseline_only_closed"], default="neither_closed")
    paired["delta_net_r"] = paired.triple_net_r - paired.baseline_net_r
    events = paired.loc[:, columns]
    rows = []
    for asset_scope, scoped in (("all", paired), ("ex_rave", paired.loc[~paired.base_asset.eq("RAVE")])):
        both = scoped.loc[scoped.closure_case.eq("both_closed")]
        triple_only = scoped.loc[scoped.closure_case.eq("triple_only_closed")]
        baseline_only = scoped.loc[scoped.closure_case.eq("baseline_only_closed")]
        observed = float(closed(frame.loc[frame.arm.eq("triple") & (frame.event_id.isin(scoped.event_id))]).net_r.sum()) - float(closed(frame.loc[frame.arm.eq("baseline") & (frame.event_id.isin(scoped.event_id))]).net_r.sum())
        reconciled = float(both.delta_net_r.sum()) + float(triple_only.triple_net_r.sum()) - float(baseline_only.baseline_net_r.sum())
        rows.append({"asset_scope": asset_scope, "both_closed_events": int(len(both)),
                     "both_closed_baseline_net_r": float(both.baseline_net_r.sum()), "both_closed_triple_net_r": float(both.triple_net_r.sum()),
                     "both_closed_delta_net_r": float(both.delta_net_r.sum()), "both_closed_delta_mean_net_r": float(both.delta_net_r.mean()) if len(both) else np.nan,
                     "triple_only_closed_events": int(len(triple_only)), "triple_only_closed_net_r": float(triple_only.triple_net_r.sum()),
                     "baseline_only_closed_events": int(len(baseline_only)), "baseline_only_closed_net_r": float(baseline_only.baseline_net_r.sum()),
                     "observed_total_delta_net_r": observed, "reconciled_total_delta_net_r": reconciled,
                     "reconciliation_difference_net_r": observed - reconciled})
    return events, pd.DataFrame(rows)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _summary_rows(frame: pd.DataFrame, *, cohort: str) -> pd.DataFrame:
    """Normalize primary rows into the compact owner-facing CSV contract."""
    if frame.empty:
        return _empty(SUMMARY_COLUMNS)
    out = frame.rename(columns={"analysis_scope": "event_scope", "scope": "asset_scope"}).copy()
    out.insert(0, "cohort", cohort)
    if "period" not in out:
        out["period"] = "all"
    if "group_type" not in out:
        out["group_type"] = np.where(out.timeframe_min.notna(), "primary_timeframe", "primary")
    if "event_scope" not in out:
        out["event_scope"] = "raw"
    if "asset_scope" not in out:
        out["asset_scope"] = "all"
    out.loc[out.group_type.eq("primary_timeframe"), "period"] = "oos"
    for column in SUMMARY_COLUMNS:
        if column not in out:
            out[column] = np.nan
    return out.loc[:, SUMMARY_COLUMNS]


def build(results: Path = EXP / "results") -> dict[str, object]:
    """Aggregate available cohorts into compact CSV/JSON without final reporting."""
    output = results / "stats"; output.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, object] = {"seed": SEED, "bootstrap_draws": DRAWS, "cohorts": {}}
    summary_frames = []
    for cohort in ("original6253", "top20"):
        outcomes = normalize_outcomes(read_stream_ledgers(results, cohort, "outcomes"))
        controls = normalize_outcomes(read_stream_ledgers(results, cohort, "controls"))
        if outcomes.empty:
            receipt["cohorts"][cohort] = {"status": "no_stream_outcomes"}
            continue
        outcomes, controls, identity = apply_identity_dedup(outcomes, controls)
        _write_csv(output / f"{cohort}_identity_dedup.csv", identity)
        outcomes, controls = time_labels(outcomes), time_labels(controls) if not controls.empty else controls
        primary_frames, matching_frames, paired_frames, grouped_outputs = [], [], [], {"diagnostic_arms": [], "period": [], "month": [], "year": [], "timeframe_min": [], "venue": [], "symbol": []}
        for scope, scoped, scoped_controls in (
            ("raw", outcomes, controls),
            ("dedup", select_scope(outcomes, dedup=True, ex_rave=False), select_scope(controls, dedup=True, ex_rave=False) if not controls.empty else controls),
        ):
            primary, matching, paired = primary_statistics(scoped, scoped_controls, analysis_scope=scope)
            primary_frames.append(primary)
            matching_frames.append(matching.assign(analysis_scope=scope))
            paired_frames.append(paired.assign(analysis_scope=scope))
            diagnostic = grouped_metrics(scoped, []).assign(event_scope=scope, asset_scope="all", period="all", group_type="diagnostic")
            grouped_outputs["diagnostic_arms"].append(diagnostic)
            grouped_outputs["period"].append(protocol_period_metrics(scoped).assign(event_scope=scope, asset_scope="all", group_type="period"))
            for name, field in (("month", "entry_month"), ("year", "entry_year"), ("timeframe_min", "timeframe_min"), ("venue", "venue"), ("symbol", "symbol")):
                if field in scoped:
                    grouped_outputs[name].append(grouped_metrics(scoped, [field]).assign(event_scope=scope, asset_scope="all", period="all", group_type=name))
        if cohort == "top20":
            dedup_outcomes = select_scope(outcomes, dedup=True, ex_rave=False)
            dedup_controls = select_scope(controls, dedup=True, ex_rave=False) if not controls.empty else controls
            for timeframe in (15, 60, 240):
                primary, matching, paired = primary_statistics(dedup_outcomes, dedup_controls,
                                                               analysis_scope="dedup",
                                                               infer_excess=True, timeframe_min=timeframe)
                primary_frames.append(primary)
                matching_frames.append(matching.assign(analysis_scope="dedup", timeframe_min=timeframe))
                paired_frames.append(paired.assign(analysis_scope="dedup", timeframe_min=timeframe))
        primary_all = pd.concat(primary_frames, ignore_index=True)
        _write_csv(output / f"{cohort}_primary.csv", primary_all)
        summary_frames.append(_summary_rows(primary_all, cohort=cohort))
        for name, frames in grouped_outputs.items():
            combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            _write_csv(output / f"{cohort}_{name}.csv", combined)
            summary_frames.append(_summary_rows(combined, cohort=cohort))
        matching_all, paired_all = pd.concat(matching_frames, ignore_index=True), pd.concat(paired_frames, ignore_index=True)
        _write_csv(output / f"{cohort}_matching.csv", matching_all)
        _write_csv(output / f"{cohort}_matched_excess.csv", paired_all)
        capture_events, capture_summary = baseline_opportunity_capture(outcomes)
        _write_csv(output / f"{cohort}_baseline_opportunity_capture_events.csv", capture_events)
        _write_csv(output / f"{cohort}_baseline_opportunity_capture_summary.csv", capture_summary)
        delta_events, delta_summary = [], []
        for scope, scoped in (("raw", outcomes), ("dedup", select_scope(outcomes, dedup=True, ex_rave=False))):
            events, summary = paired_baseline_triple_delta(scoped)
            delta_events.append(events.assign(event_scope=scope))
            delta_summary.append(summary.assign(event_scope=scope))
        _write_csv(output / f"{cohort}_baseline_triple_closed_delta_events.csv", pd.concat(delta_events, ignore_index=True))
        _write_csv(output / f"{cohort}_baseline_triple_closed_delta_summary.csv", pd.concat(delta_summary, ignore_index=True))
        receipt["cohorts"][cohort] = {"status": "complete", "outcome_rows": int(len(outcomes)), "control_rows": int(len(controls)), "matched_rows": int(len(paired_all))}
    _write_csv(output / "summary.csv", pd.concat(summary_frames, ignore_index=True) if summary_frames else _empty(SUMMARY_COLUMNS))
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--results", type=Path, default=EXP / "results")
    print(json.dumps(build(parser.parse_args().results), indent=2, default=str))


if __name__ == "__main__":
    main()
