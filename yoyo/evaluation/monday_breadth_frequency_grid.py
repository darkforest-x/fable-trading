"""Lock higher-frequency Monday lower-1.618 alert variants from cached VectorBT returns.

This consumes the already materialized 6.14M-rule fixed-time search under
``exp-monday-breadth-optimization-20261108-v1/search/development`` and the
cached signal/outcome partitions. It never recomputes a market fill or price
path. Admissions use the original chronological single-position rule
``max(24h, hold_hours)``; every selected event keeps its original cached net
return. 2025 is a selection period; 2026 through the source ledger end is a
locked descriptive review.
"""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_inputs as inputs
from yoyo.evaluation import monday_breadth_opt_selection as search


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
OUT = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1/fixed"
SEARCH = SOURCE / "search/development"
ASSETS = ("BTC", "ETH", "SOL", "ALT")
HOLDS = (6, 12, 24, 48, 72, 144)
TIERS = {
    "freq36": {"annual_target": 36, "two_year_target": 72},
    "freq72": {"annual_target": 72, "two_year_target": 144},
}
EARLY_MIN = 50
MONTHS_2025_MIN = 8
TOP_K = 10
RANK_STEPS = (10, 25, 50, 100, 250)
MAX_RANK = max(RANK_STEPS)
MAX_FALLBACK = 250
EXTRA_COST_STRESS_PCT = 0.20
PERIOD_STARTS = {
    0: pd.Timestamp("1900-01-01", tz="UTC"),
    1: pd.Timestamp("2023-01-01", tz="UTC"),
    2: pd.Timestamp("2025-01-01", tz="UTC"),
    3: pd.Timestamp("2026-01-01", tz="UTC"),
}
PERIOD_NAMES = {0: "pre2023", 1: "2023_2024", 2: "2025", 3: "2026_to_ledger_end"}
YEAR_SPLITS = pd.to_datetime(["2023-01-01", "2025-01-01", "2026-01-01"], utc=True)


def breadth_is_allowed(value: str) -> bool:
    """Keep only fixed N bins with lower bound >=2 or known share thresholds."""
    match = re.match(r"^N(\d+)_", str(value))
    if match:
        return int(match.group(1)) >= 2
    return str(value).startswith("share_")


def candidate_sort_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["dev_lcb"] = frame[["a_lcb", "b_lcb"]].min(axis=1)
    frame["dev_win"] = frame[["a_win", "b_win"]].min(axis=1)
    frame["dev_mean_clipped"] = frame[["a_clipped", "b_clipped"]].min(axis=1)
    frame["complexity"] = (frame.diffusion.ne("any").astype(int)
                           + frame.confirmation.ne("none").astype(int))
    return frame


def read_candidate_pools() -> tuple[dict[tuple[str, str], pd.DataFrame], dict[tuple[str, str], pd.DataFrame]]:
    """Read cached search rows in bounded partitions and retain deterministic top ranks."""
    cols = ["minutes", "wait_min", "pattern", "hold_hours", "asset", "breadth",
            "diffusion", "confirmation", "a_n", "a_mean", "a_win", "a_clipped",
            "a_lcb", "b_n", "b_mean", "b_win", "b_clipped", "b_lcb"]
    primary_parts: dict[tuple[str, str], list[pd.DataFrame]] = {(t, a): [] for t in TIERS for a in ASSETS}
    fallback_parts: dict[tuple[str, str], list[pd.DataFrame]] = {(t, a): [] for t in TIERS for a in ASSETS}
    files = sorted(p for p in SEARCH.glob("*.parquet") if not p.name.endswith("_rules.parquet"))
    if len(files) != len(search.CFG["W_minutes"]) * len(search.CFG["X_minutes"]):
        raise FileNotFoundError(f"Expected {len(search.CFG['W_minutes']) * len(search.CFG['X_minutes'])} VectorBT partitions, found {len(files)}")
    for path in files:
        part = pd.read_parquet(path, columns=cols)
        part = part[part.breadth.map(breadth_is_allowed)]
        part = candidate_sort_columns(part)
        for tier, requirements in TIERS.items():
            for asset in ASSETS:
                group = part[part.asset.eq(asset)]
                # Primary rank uses the pre-2023 and 2023-24 VectorBT statistics.
                eligible_history = group[(group.a_n >= EARLY_MIN)
                                          & (group.b_n >= requirements["two_year_target"])]
                if len(eligible_history):
                    primary_parts[(tier, asset)].append(eligible_history.nlargest(MAX_RANK, "dev_lcb"))
                # A separate breadth-first fallback can be selected and labelled if
                # no candidate reaches every frequency gate after the initial>=2 rule.
                fallback_parts[(tier, asset)].append(
                    group.nlargest(MAX_FALLBACK, ["b_n", "a_n", "dev_lcb"])
                )
    primary = {}
    fallback = {}
    for key in primary_parts:
        if primary_parts[key]:
            frame = pd.concat(primary_parts[key], ignore_index=True)
            frame = frame.drop_duplicates(["minutes", "wait_min", "pattern", "hold_hours", "asset"])
            primary[key] = frame.sort_values(
                ["dev_lcb", "dev_win", "dev_mean_clipped", "complexity", "wait_min", "minutes", "pattern", "hold_hours"],
                ascending=[False, False, False, True, True, True, True, True], kind="stable").reset_index(drop=True)
        else:
            primary[key] = pd.DataFrame(columns=cols + ["dev_lcb", "dev_win", "dev_mean_clipped", "complexity"])
        frame = pd.concat(fallback_parts[key], ignore_index=True)
        frame = frame.drop_duplicates(["minutes", "wait_min", "pattern", "hold_hours", "asset"])
        # Preserve candidates which rank highly by either b_n or a_n when the
        # original search score is unavailable/negative.
        fallback[key] = frame.sort_values(
            ["b_n", "a_n", "dev_lcb", "complexity", "wait_min", "minutes", "pattern", "hold_hours"],
            ascending=[False, False, False, True, True, True, True, True], kind="stable").head(MAX_FALLBACK).reset_index(drop=True)
    return primary, fallback


def candidate_key(row) -> tuple:
    return (int(row.minutes), int(row.wait_min), int(row.pattern), int(row.hold_hours),
            str(row.asset), str(row.breadth), str(row.diffusion), str(row.confirmation))


@lru_cache(maxsize=10)
def load_partition(minutes: int, wait_min: int):
    return inputs.cached_partition(int(minutes), int(wait_min))


@lru_cache(maxsize=50000)
def candidate_events_cached(key: tuple) -> pd.DataFrame:
    minutes, wait_min, _pattern, hold_hours, asset, breadth, diffusion, confirmation = key
    frame, returns = load_partition(minutes, wait_min)
    slots, folds = search.time_axes(frame)
    indices = search.rule_indices(frame, minutes, wait_min, breadth, diffusion, confirmation)
    # Share is a causal activity-universe proxy and can admit initial=1 bars;
    # these variants require true >=2-coin initial resonance. Share NaN already
    # fails its threshold comparison and therefore is excluded here as well.
    indices = indices[frame.initial.to_numpy()[indices] >= 2]
    asset_i = ASSETS.index(asset)
    hold_i = HOLDS.index(hold_hours)
    pieces = []
    for epoch in range(4):
        chosen = search.admissions(indices[folds[indices] == epoch], slots,
                                   hold_hours * 12, search.ENDS[epoch])
        if not len(chosen):
            continue
        events = frame.iloc[chosen][["known", "decision", "initial", "new_coins"]].copy()
        events["epoch"] = epoch
        events["net_pct"] = returns[chosen, hold_i, asset_i]
        events["outcome_complete"] = np.isfinite(events.net_pct.to_numpy())
        pieces.append(events)
    if not pieces:
        return pd.DataFrame(columns=["known", "decision", "initial", "new_coins", "epoch", "net_pct", "outcome_complete"])
    return pd.concat(pieces, ignore_index=True)


def metric_block(events: pd.DataFrame, stress_pct: float = 0.0) -> dict:
    rows = events[events.outcome_complete].sort_values("decision")
    vals = rows.net_pct.to_numpy(dtype=float) - stress_pct
    n = len(vals)
    if not n:
        return {"n": 0, "months": 0, "win_pct": np.nan, "mean_net_pct": np.nan,
                "median_net_pct": np.nan, "worst_net_pct": np.nan, "dd_pct": np.nan,
                "growth_pct": np.nan, "clipped_mean_pct": np.nan, "lcb_pct": np.nan,
                "ex_best_month_mean_pct": np.nan, "total_simple_pct": np.nan}
    clipped = np.clip(vals, -20, 20)
    lcb = clipped.mean() - 1.28 * clipped.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    equity = np.r_[1.0, np.cumprod(1.0 + vals / 100.0)]
    month = rows.decision.dt.strftime("%Y-%m").to_numpy()
    month_sums = pd.Series(vals).groupby(month).sum()
    ex_best = vals[month != month_sums.idxmax()]
    return {"n": n, "months": int(pd.Series(month).nunique()),
            "win_pct": float(100 * np.mean(vals > 0)), "mean_net_pct": float(vals.mean()),
            "median_net_pct": float(np.median(vals)), "worst_net_pct": float(vals.min()),
            "dd_pct": float(100 * (1 - np.min(equity / np.maximum.accumulate(equity)))),
            "growth_pct": float(100 * (equity[-1] - 1)), "clipped_mean_pct": float(clipped.mean()),
            "lcb_pct": float(lcb),
            "ex_best_month_mean_pct": float(ex_best.mean()) if len(ex_best) else np.nan,
            "total_simple_pct": float(vals.sum())}


def event_stats(events: pd.DataFrame) -> dict:
    finite = events[events.outcome_complete].copy()
    by_epoch = {epoch: finite[finite.epoch.eq(epoch)] for epoch in range(4)}
    s0 = metric_block(by_epoch[0])
    s1 = metric_block(by_epoch[1])
    s2 = metric_block(by_epoch[2])
    s2_stress = metric_block(by_epoch[2], EXTRA_COST_STRESS_PCT)
    s3 = metric_block(by_epoch[3])
    return {
        "pre2023_n": s0["n"], "pre2023_mean_net_pct": s0["mean_net_pct"],
        "pre2023_lcb_pct": s0["lcb_pct"],
        "2023_2024_n": s1["n"], "2023_2024_mean_net_pct": s1["mean_net_pct"],
        "2023_2024_lcb_pct": s1["lcb_pct"],
        "y2025_n": s2["n"], "y2025_months": s2["months"],
        "y2025_win_pct": s2["win_pct"], "y2025_mean_net_pct": s2["mean_net_pct"],
        "y2025_median_net_pct": s2["median_net_pct"], "y2025_dd_pct": s2["dd_pct"],
        "y2025_lcb_pct": s2["lcb_pct"], "y2025_growth_pct": s2["growth_pct"],
        "y2025_stress_mean_net_pct": s2_stress["mean_net_pct"],
        "y2025_stress_dd_pct": s2_stress["dd_pct"],
        "y2025_stress_ex_best_month_mean_pct": s2_stress["ex_best_month_mean_pct"],
        "y2026_n": s3["n"], "y2026_months": s3["months"],
        "y2026_win_pct": s3["win_pct"], "y2026_mean_net_pct": s3["mean_net_pct"],
        "y2026_median_net_pct": s3["median_net_pct"], "y2026_dd_pct": s3["dd_pct"],
        "y2026_lcb_pct": s3["lcb_pct"], "y2026_growth_pct": s3["growth_pct"],
        "frequency_attainment": min(
            s0["n"] / EARLY_MIN,
            s1["n"] / max(1, current_required_two_year),
            s2["n"] / max(1, current_required_annual),
            s2["months"] / MONTHS_2025_MIN,
        ),
    }


def add_frequency_result(stats: dict, tier: str) -> dict:
    req = TIERS[tier]
    stats = dict(stats)
    stats["frequency_pass"] = bool(
        stats["pre2023_n"] >= EARLY_MIN
        and stats["2023_2024_n"] >= req["two_year_target"]
        and stats["y2025_n"] >= req["annual_target"]
        and stats["y2025_months"] >= MONTHS_2025_MIN
    )
    stats["return_pass_2025"] = bool(
        pd.notna(stats["y2025_mean_net_pct"]) and stats["y2025_mean_net_pct"] > 0
        and stats["y2025_win_pct"] >= 50
        and stats["y2025_dd_pct"] <= 35
        and stats["y2025_stress_mean_net_pct"] > 0
        and stats["y2025_stress_ex_best_month_mean_pct"] > 0
    )
    stats["eligible"] = stats["frequency_pass"] and stats["return_pass_2025"]
    return stats


def _reasons(row: dict, tier: str) -> str:
    req = TIERS[tier]
    reasons = []
    if row.get("pre2023_n", 0) < EARLY_MIN: reasons.append(f"pre2023<{EARLY_MIN}")
    if row.get("2023_2024_n", 0) < req["two_year_target"]: reasons.append(f"2023-24<{req['two_year_target']}")
    if row.get("y2025_n", 0) < req["annual_target"]: reasons.append(f"2025<{req['annual_target']}")
    if row.get("y2025_months", 0) < MONTHS_2025_MIN: reasons.append(f"2025_months<{MONTHS_2025_MIN}")
    mean = row.get("y2025_mean_net_pct", np.nan)
    win = row.get("y2025_win_pct", np.nan)
    dd = row.get("y2025_dd_pct", np.nan)
    stress_mean = row.get("y2025_stress_mean_net_pct", np.nan)
    stress_ex_best = row.get("y2025_stress_ex_best_month_mean_pct", np.nan)
    if pd.isna(mean) or mean <= 0: reasons.append("2025_mean_missing_or_nonpositive")
    if pd.isna(win) or win < 50: reasons.append("2025_win_missing_or_below_50%")
    if pd.isna(dd) or dd > 35: reasons.append("2025_DD_missing_or_above_35%")
    if pd.isna(stress_mean) or stress_mean <= 0: reasons.append("2025_stress_mean_missing_or_nonpositive")
    if pd.isna(stress_ex_best) or stress_ex_best <= 0: reasons.append("2025_stress_ex_best_month_missing_or_nonpositive")
    return ";".join(reasons) if reasons else ""


def ranked_eval_row(row, tier: str, asset: str, rank: int, events: pd.DataFrame) -> dict:
    key = candidate_key(row)
    out = {"tier": tier, "asset": asset, "rank_dev": rank,
           "minutes": int(row.minutes), "wait_min": int(row.wait_min), "breadth": row.breadth,
           "diffusion": row.diffusion, "confirmation": row.confirmation,
           "hold_hours": int(row.hold_hours), "pattern": int(row.pattern),
           "dev_lcb_pct": float(row.dev_lcb), "dev_win_min_pct": float(row.dev_win),
           "dev_clipped_mean_min_pct": float(row.dev_mean_clipped), "dev_source_a_n": int(row.a_n),
           "dev_source_b_n": int(row.b_n), "candidate_key": json.dumps(key)}
    stats = add_frequency_result(event_stats(events), tier)
    out.update(stats)
    out["failure_reasons"] = _reasons(out, tier)
    return out


def evaluate_group(tier: str, asset: str, primary: pd.DataFrame, fallback: pd.DataFrame):
    """Progressively check top dev rows, then use an explicit frequency fallback."""
    evaluated: dict[str, tuple[dict, pd.DataFrame]] = {}
    top = primary.head(MAX_RANK)
    for boundary in RANK_STEPS:
        for rank, row in enumerate(top.head(boundary).itertuples(index=False), start=1):
            key = candidate_key(row)
            ident = json.dumps(key)
            if ident in evaluated:
                continue
            events = candidate_events_cached(key)
            evaluated[ident] = (ranked_eval_row(row, tier, asset, rank, events), events)
        complete = [item[0] for item in evaluated.values() if item[0]["eligible"]]
        if complete:
            # The first dev-ranked tranche that contains a 2025 frequency and
            # risk-qualified rule defines the 2025 selection family.
            eligible_df = pd.DataFrame(complete)
            chosen = eligible_df.sort_values(
                ["y2025_lcb_pct", "y2025_mean_net_pct", "y2025_dd_pct", "dev_lcb_pct", "rank_dev"],
                ascending=[False, False, True, False, True], kind="stable").iloc[0].to_dict()
            scope = f"development_top_{boundary}_2025_risk_selected"
            break
    else:
        scope = ""
        chosen = None

    if chosen is None:
        # If frequency gates pass but no 2025 return/risk gate does, keep the
        # most frequent candidate as a clearly descriptive, ineligible option.
        freq_rows = [item[0] for item in evaluated.values() if item[0]["frequency_pass"]]
        if freq_rows:
            chosen = pd.DataFrame(freq_rows).sort_values(
                ["y2025_n", "y2025_months", "y2025_lcb_pct", "dev_lcb_pct", "rank_dev"],
                ascending=[False, False, False, False, True], kind="stable").iloc[0].to_dict()
            scope = f"development_top_{MAX_RANK}_frequency_only_descriptive"
        else:
            # No candidate in the dev-ranked family reached the full frequency
            # gate. Search the per-partition highest-volume list and select the
            # closest frequency attainment, never marking it eligible.
            fallback = fallback.drop_duplicates(["minutes", "wait_min", "pattern", "hold_hours", "asset"])
            fallback = fallback.sort_values(
                ["b_n", "a_n", "dev_lcb", "complexity", "wait_min", "minutes", "pattern", "hold_hours"],
                ascending=[False, False, False, True, True, True, True, True], kind="stable").head(MAX_FALLBACK)
            for rank, row in enumerate(fallback.itertuples(index=False), start=MAX_RANK + 1):
                key = candidate_key(row)
                ident = json.dumps(key)
                if ident not in evaluated:
                    events = candidate_events_cached(key)
                    evaluated[ident] = (ranked_eval_row(row, tier, asset, rank, events), events)
            all_rows = [item[0] for item in evaluated.values()]
            chosen = pd.DataFrame(all_rows).sort_values(
                ["frequency_attainment", "y2025_n", "2023_2024_n", "pre2023_n", "y2025_lcb_pct", "dev_lcb_pct"],
                ascending=[False, False, False, False, False, False], kind="stable").iloc[0].to_dict()
            scope = f"frequency_fallback_top_{MAX_FALLBACK}_by_history_volume"

    audit = pd.DataFrame([item[0] for item in evaluated.values()])
    audit["selected"] = audit.candidate_key.eq(chosen["candidate_key"])
    audit["selected_scope"] = scope
    chosen["selection_scope"] = scope
    chosen["failure_reasons"] = _reasons(chosen, tier)
    # If a frequency/risk-qualified candidate wasn't found within the development
    # cap, make the bounded search visible instead of implying global impossibility.
    if not chosen["eligible"] and not chosen["failure_reasons"]:
        chosen["failure_reasons"] = f"not_eligible_within_rank_cap_{MAX_RANK}"
    return chosen, evaluated, audit


def calendar_exposure(year: int) -> int:
    if year == 2026:
        return int((pd.Timestamp("2026-10-28", tz="UTC") - pd.Timestamp("2026-01-01", tz="UTC")).days)
    return 366 if pd.Timestamp(year=year, month=1, day=1).is_leap_year else 365


def materialize_event_frame(selected, evaluated) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    all_events = []
    event_lookup = {}
    for rule in selected:
        key = tuple(json.loads(rule["candidate_key"]))
        events = candidate_events_cached(key).copy()
        candidate_id = rule["candidate_id"]
        events.insert(0, "candidate_id", candidate_id)
        events.insert(1, "role", rule["role"])
        events.insert(2, "asset", rule["asset"])
        events.insert(3, "minutes", rule["minutes"])
        events.insert(4, "wait_min", rule["wait_min"])
        events.insert(5, "breadth", rule["breadth"])
        events.insert(6, "diffusion", rule["diffusion"])
        events.insert(7, "confirmation", rule["confirmation"])
        events.insert(8, "hours", rule["hold_hours"])
        events["epoch_name"] = events.epoch.map(PERIOD_NAMES)
        events["year"] = events.decision.dt.year.astype(int)
        events["month"] = events.decision.dt.strftime("%Y-%m")
        events["iso_week"] = events.decision.dt.strftime("%G-%V")
        events["day"] = events.decision.dt.strftime("%Y-%m-%d")
        events["initial"] = events.initial.astype(int)
        events["new_coins"] = events.new_coins.astype(int)
        # Preserve NA outcomes in the event ledger. They consume the original
        # admission slot but do not count as priced/realized trades in metrics.
        event_lookup[candidate_id] = events
        all_events.append(events)
    frame = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    if not frame.empty:
        frame = frame.sort_values(["candidate_id", "decision"], kind="stable").reset_index(drop=True)
    return frame, event_lookup


def yearly_tables(events: pd.DataFrame, selected: list[dict]):
    monthly = []
    daily = []
    weekly = []
    annual = []
    period_summary = []
    for rule in selected:
        cid = rule["candidate_id"]
        ev = events[events.candidate_id.eq(cid)].copy()
        finite = ev[ev.outcome_complete].copy()
        available_start = pd.Timestamp(f"{int(finite.decision.min().year) if len(finite) else 2020}-01-01", tz="UTC")
        # Keep one row per calendar month, including explicit zero-event months.
        end_month = pd.Timestamp("2026-10-01", tz="UTC")
        month_index = pd.date_range(available_start, end_month, freq="MS", tz="UTC")
        if not len(month_index):
            month_index = pd.date_range("2020-01-01", "2026-10-01", freq="MS", tz="UTC")
        month_counts = finite.groupby(finite.decision.dt.strftime("%Y-%m")).size()
        for month_start in month_index:
            month_key = month_start.strftime("%Y-%m")
            subset = finite[finite.month.eq(month_key)]
            m = metric_block(subset)
            monthly.append({"candidate_id": cid, "asset": rule["asset"], "year": month_start.year,
                            "month": month_key, "events": int(month_counts.get(month_key, 0)),
                            "mean_net_pct": m["mean_net_pct"], "win_pct": m["win_pct"],
                            "sum_net_pct": m["total_simple_pct"], "dd_pct": m["dd_pct"]})
        for bucket, key_col, target in (("daily", "day", daily), ("weekly", "iso_week", weekly)):
            group = finite.groupby(key_col, sort=True)
            for key, part in group:
                m = metric_block(part)
                target.append({"candidate_id": cid, "asset": rule["asset"], "year": int(part.decision.dt.year.iloc[0]),
                               key_col: key, "events": len(part), "mean_net_pct": m["mean_net_pct"],
                               "sum_net_pct": m["total_simple_pct"], "win_pct": m["win_pct"]})
        for year, year_events in finite.groupby(finite.decision.dt.year, sort=True):
            year_stats = metric_block(year_events)
            exposure = calendar_exposure(int(year))
            year_row = {"candidate_id": cid, "asset": rule["asset"], "year": int(year),
                        "events": year_stats["n"], "months_with_events": year_stats["months"],
                        "annualized_events_per_year": (year_stats["n"] * 365.2425 / exposure),
                        "win_pct": year_stats["win_pct"], "mean_net_pct": year_stats["mean_net_pct"],
                        "median_net_pct": year_stats["median_net_pct"], "worst_net_pct": year_stats["worst_net_pct"],
                        "dd_pct": year_stats["dd_pct"], "growth_pct": year_stats["growth_pct"],
                        "lcb_pct": year_stats["lcb_pct"],
                        "stress_mean_net_pct": metric_block(year_events, EXTRA_COST_STRESS_PCT)["mean_net_pct"],
                        "extra_cost_stress_pct_per_round_trip": EXTRA_COST_STRESS_PCT,
                        "target_36_rate_met": (year_stats["n"] * 365.2425 / exposure) >= 36,
                        "target_72_rate_met": (year_stats["n"] * 365.2425 / exposure) >= 72}
            annual.append(year_row)
        for epoch, name in PERIOD_NAMES.items():
            part = finite[finite.epoch.eq(epoch)]
            m = metric_block(part)
            period_summary.append({"candidate_id": cid, "asset": rule["asset"], "period": name,
                                   "events": m["n"], "months_with_events": m["months"],
                                   "win_pct": m["win_pct"], "mean_net_pct": m["mean_net_pct"],
                                   "median_net_pct": m["median_net_pct"], "dd_pct": m["dd_pct"],
                                   "growth_pct": m["growth_pct"], "lcb_pct": m["lcb_pct"],
                                   "stress_mean_net_pct": metric_block(part, EXTRA_COST_STRESS_PCT)["mean_net_pct"]})
    annual_df = pd.DataFrame(annual)
    cadence = []
    for rule in selected:
        cid = rule["candidate_id"]
        ev = events[(events.candidate_id.eq(cid)) & events.outcome_complete].copy()
        for year in sorted(ev.year.unique()) if len(ev) else []:
            subset = ev[ev.year.eq(year)]
            day_counts = subset.groupby("day").size()
            week_counts = subset.groupby("iso_week").size()
            exposure = calendar_exposure(int(year))
            cadence.append({"candidate_id": cid, "asset": rule["asset"], "year": int(year),
                            "events": len(subset), "exposure_days": exposure,
                            "active_days": int(day_counts.size), "events_per_calendar_day": len(subset) / exposure,
                            "mean_events_per_active_day": float(day_counts.mean()),
                            "median_events_per_active_day": float(day_counts.median()),
                            "p90_events_per_active_day": float(day_counts.quantile(.9)),
                            "max_events_per_day": int(day_counts.max()),
                            "active_weeks": int(week_counts.size),
                            "events_per_calendar_week": len(subset) / (exposure / 7),
                            "mean_events_per_active_week": float(week_counts.mean()),
                            "median_events_per_active_week": float(week_counts.median()),
                            "p90_events_per_active_week": float(week_counts.quantile(.9)),
                            "max_events_per_week": int(week_counts.max())})
    return (pd.DataFrame(annual), pd.DataFrame(monthly), pd.DataFrame(daily),
            pd.DataFrame(weekly), pd.DataFrame(cadence), pd.DataFrame(period_summary))


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    primary, fallback = read_candidate_pools()
    selected = []
    audits = []
    evaluated_store = {}
    global current_required_two_year, current_required_annual
    for tier, requirement in TIERS.items():
        current_required_two_year = requirement["two_year_target"]
        current_required_annual = requirement["annual_target"]
        for asset in ASSETS:
            chosen, evaluated, audit = evaluate_group(tier, asset, primary[(tier, asset)], fallback[(tier, asset)])
            chosen["tier"] = tier
            chosen["role"] = f"frequency_{requirement['annual_target']}_per_year"
            chosen["candidate_id"] = f"{tier}_{asset}"
            chosen["asset"] = asset
            chosen["target_per_year"] = requirement["annual_target"]
            chosen["minimum_pre2023_events"] = EARLY_MIN
            chosen["minimum_2023_2024_events"] = requirement["two_year_target"]
            chosen["minimum_2025_events"] = requirement["annual_target"]
            chosen["minimum_2025_active_months"] = MONTHS_2025_MIN
            chosen["eligible"] = bool(chosen["eligible"])
            chosen["failure_reasons"] = _reasons(chosen, tier)
            if not chosen["eligible"] and not chosen["failure_reasons"]:
                chosen["failure_reasons"] = f"not_eligible_within_rank_cap_{MAX_RANK}"
            selected.append(chosen)
            audits.append(audit.assign(candidate_id_prefix=f"{tier}_{asset}"))
            evaluated_store.update(evaluated)

    # Persist the first requested reusable tables immediately so matched-control
    # work can begin before the descriptive tables are finished.
    rules = pd.DataFrame(selected)
    rules = rules.rename(columns={"candidate_key": "_candidate_key_internal"})
    public_cols = ["candidate_id", "role", "asset", "minutes", "wait_min", "breadth",
                   "diffusion", "confirmation", "hold_hours", "eligible"]
    additional = [c for c in rules.columns if c not in public_cols and c != "_candidate_key_internal"]
    rules[public_cols + additional].to_csv(OUT / "locked_rules.csv", index=False)
    rules_for_events = selected
    event_frame, event_lookup = materialize_event_frame(rules_for_events, evaluated_store)
    event_frame.to_parquet(OUT / "locked_events.parquet", index=False)
    print(f"LOCKED_RULES_READY {OUT / 'locked_rules.csv'}", flush=True)
    print(f"LOCKED_EVENTS_READY rows={len(event_frame)} path={OUT / 'locked_events.parquet'}", flush=True)

    audit_frame = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame()
    audit_frame.to_csv(OUT / "candidate_selection_audit.csv", index=False)
    annual, monthly, daily, weekly, cadence, periods = yearly_tables(event_frame, selected)
    annual.to_csv(OUT / "annual_summary.csv", index=False)
    monthly.to_csv(OUT / "monthly_distribution.csv", index=False)
    daily.to_csv(OUT / "daily_frequency.csv", index=False)
    weekly.to_csv(OUT / "weekly_frequency.csv", index=False)
    cadence.to_csv(OUT / "cadence_summary.csv", index=False)
    periods.to_csv(OUT / "period_summary.csv", index=False)

    contract = {
        "experiment": "exp-monday-breadth-frequency-20261108-v1",
        "source_experiment": "exp-monday-breadth-optimization-20261108-v1",
        "source_search": "search/development VectorBT fixed-time grid (6,143,760 searched rule combinations, 3,721,896 distinct development-path rows; source search_counts.csv)",
        "source_outcomes": "payoffs/event_outcomes_minutes{W}_wait{X}.csv.gz; 5m-open entry/exit, existing two-sided 0.05% taker fees",
        "holds_hours": list(HOLDS),
        "frequency_tiers": TIERS,
        "early_minimum_pre2023_events": EARLY_MIN,
        "minimum_2025_active_months": MONTHS_2025_MIN,
        "selection": {
            "development_rank": "min(pre-2023, 2023-24) clipped +/-20pp net-return mean minus 1.28 SE; existing VectorBT returns, sorted robust score, win and simpler rule",
            "rank_family": f"progressively check the top {RANK_STEPS} development ranks; choose among first tranche with full 2025 frequency/risk-qualified candidates by 2025 clipped-return LCB, mean, lower DD",
            "2025_return_gate": "positive net mean; win >=50%; compounded one-position DD <=35%; mean remains positive after additional 0.20 percentage-point round-trip stress; remains positive after removing best stressed-return month",
            "fallback": "If no fully qualified candidate exists, choose the highest-frequency development-ranked rule that meets all frequency gates as descriptive only. If none does, choose the candidate with greatest normalized attainment across early, 2023-24, 2025 count and 2025 month coverage; eligible=false and failure reasons retained.",
        },
        "frequency_gate": "pre-2023 count >=50; 2023-24 combined count >=72/144; 2025 count >=36/72 and >=8 active months",
        "single_position": "chronological signal admissions per asset reset by source evaluation epoch; each next entry waits max(24h, hold_hours) from prior accepted entry; no overlapping positions",
        "initial_breadth": "fixed-N lower bound >=2; share rules additionally filter initial>=2; unknown share is NaN and fails share thresholds",
        "asset_scope": "BTC, ETH, SOL, ALT are evaluated separately; ALT is the existing equal-dollar initial-altcoin basket; asset event counts are not summed as independent opportunities",
        "cost_stress": f"additional {EXTRA_COST_STRESS_PCT:.2f} percentage points per round trip, after source event net outcomes",
        "2026_review": "2026 event counts/returns are locked descriptive results through 2026-10-28 exclusive; annualized run rate is shown using available days, not treated as a completed calendar year",
        "source_feature_caveats": "market_share_pct is a trailing-28-day any-touch proxy, not a listing census; raw first-touch ledger left censoring remains; no funding or extra slippage in source base returns",
    }
    (OUT / "selection_contract.json").write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
    manifest = {"builder": "yoyo/evaluation/monday_breadth_frequency_grid.py",
                "outputs": sorted(p.name for p in OUT.iterdir() if p.is_file()),
                "locked_rules": len(rules), "locked_event_rows_including_unpriced": len(event_frame),
                "locked_priced_events": int(event_frame.outcome_complete.sum()),
                "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(rules[public_cols + ["frequency_pass", "return_pass_2025", "pre2023_n", "2023_2024_n", "y2025_n", "y2025_months", "y2025_mean_net_pct", "y2025_dd_pct", "y2025_stress_mean_net_pct", "y2026_n", "eligible", "failure_reasons"]].to_string(index=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    run()
