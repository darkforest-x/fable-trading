"""Describe signal-time features associated with confirmed SPIKE peaks above 10R.

The input is the frozen OKX Top20 signal ledger produced by the parent study.
Continuous feature cutoffs use signal-time values through 2024 only; the two
known stop-risk levels are fixed at 0.015 and 0.03. 2025 and 2026 are summarized
against the frozen feature cutoffs. ``possible`` and
``censored`` are never counted as misses. This is a multi-feature exploratory
screen, not a confirmatory model or a strategy backtest.

Run from the repository root::

    python -m yoyo.evaluation.cloud_peak10_patterns [--input .../signals.csv]

Outputs ``patterns.json`` and ``feature_windows.csv`` beside the input unless
``--output-dir`` is supplied. Seven continuous confirmation-time features are
compared: close_distance_r, penetration_atr, ema_spread_atr, wick_share,
monday_width_pct, prior_atr_pct, planned_r. stop_pct is summarized only in its
two fixed risk tiers. Outcome timestamps are used only for timing summaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "experiments/active/exp-cloud-top20-peak10r-20261108-v1/signals.csv"
FEATURES = (
    "close_distance_r",
    "penetration_atr",
    "ema_spread_atr",
    "wick_share",
    "monday_width_pct",
    "prior_atr_pct",
    "planned_r",
)
OPTIONAL_BOOLEAN_FIELDS = ("same_side_stoch", "pure_wick")
REQUIRED_COLUMNS = (
    "symbol", "week", "side", "confirmed_bjt", "status", "peak_lower_r",
    "peak_upper_r", "stop_pct", *FEATURES, "first_gt10_bjt", "peak_time_bjt",
)
OUTCOMES = ("hit", "miss", "possible", "censored")
TRAIN_CUTOFF_YEAR = 2024
DEFAULT_BOOTSTRAPS = 1000
DEFAULT_SEED = 20261108


def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize value of type {type(value).__name__}")


def _parse_bjt(value: Any) -> pd.Timestamp | pd.NaT:
    """Parse an ISO timestamp; naive values are interpreted as Beijing time."""
    if pd.isna(value) or str(value).strip() == "":
        return pd.NaT
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("Asia/Shanghai")
    return stamp.tz_convert("Asia/Shanghai")


def load_signals(path: Path) -> tuple[pd.DataFrame, list[str]]:
    frame = pd.read_csv(path, low_memory=False)
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required signals.csv columns: {missing}")
    if frame.empty:
        raise ValueError("signals.csv has no rows")

    frame = frame.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["week"] = frame["week"].astype("string").str.strip()
    if frame["symbol"].isna().any() or frame["symbol"].eq("").any():
        raise ValueError("symbol must be populated on every signal")
    if frame["week"].isna().any() or frame["week"].eq("").any():
        raise ValueError("week must be populated for week-cluster summaries")

    frame["side"] = pd.to_numeric(frame["side"], errors="coerce")
    if frame["side"].isna().any() or not frame["side"].isin([-1, 1]).all():
        raise ValueError("side must contain only -1 or 1")
    frame["side"] = frame["side"].astype(int)
    frame["direction"] = frame["side"].map({1: "long", -1: "short"})

    status = frame["status"].astype("string").str.strip().str.lower()
    invalid_status = sorted(set(status.dropna()) - set(OUTCOMES))
    if status.isna().any() or invalid_status:
        raise ValueError(f"status must be one of {OUTCOMES}; invalid={invalid_status}")
    frame["status_input"] = status
    frame["status"] = status

    timestamp_errors = []
    for column in ("confirmed_bjt", "first_gt10_bjt", "peak_time_bjt"):
        values = []
        for index, value in frame[column].items():
            try:
                values.append(_parse_bjt(value))
            except (TypeError, ValueError, OverflowError):
                values.append(pd.NaT)
                if column == "confirmed_bjt":
                    timestamp_errors.append(int(index))
        frame[column + "_parsed"] = pd.Series(values, index=frame.index, dtype="object")
    if timestamp_errors:
        raise ValueError(f"Invalid confirmed_bjt values at CSV rows: {timestamp_errors[:10]}")
    if frame["confirmed_bjt_parsed"].isna().any():
        raise ValueError("confirmed_bjt must be populated on every signal")

    frame["year"] = frame["confirmed_bjt_parsed"].map(lambda x: int(x.year))
    frame["period"] = frame["year"].map(
        lambda year: "through_2024" if year <= TRAIN_CUTOFF_YEAR else str(year)
    )
    frame["confirmed_outcome"] = frame["status"].isin(["hit", "miss"])
    frame["is_hit"] = frame["status"].eq("hit")
    frame["is_possible"] = frame["status"].eq("possible")
    frame["is_censored"] = frame["status"].eq("censored")

    numeric_warnings = []
    for column in ("peak_lower_r", "peak_upper_r", "stop_pct", *FEATURES):
        raw = frame[column]
        converted = pd.to_numeric(raw, errors="coerce")
        invalid = raw.notna() & raw.astype(str).str.strip().ne("") & converted.isna()
        if invalid.any():
            numeric_warnings.append(f"{column}: {int(invalid.sum())} nonnumeric value(s) treated as missing")
        frame[column] = converted.astype(float)

    for column in OPTIONAL_BOOLEAN_FIELDS:
        was_provided = column in frame.columns
        if column not in frame:
            frame[column] = pd.NA
            frame[column + "_provided"] = False
            continue
        frame[column + "_provided"] = was_provided
        raw = frame[column]
        normalized = raw.astype("string").str.strip().str.lower()
        mapping = {"true": "true", "1": "true", "yes": "true", "y": "true",
                   "false": "false", "0": "false", "no": "false", "n": "false"}
        parsed = normalized.map(mapping)
        invalid = raw.notna() & normalized.ne("") & parsed.isna()
        if invalid.any():
            numeric_warnings.append(f"{column}: {int(invalid.sum())} unrecognized boolean value(s) treated as missing")
        frame[column] = parsed.astype("string")

    reversed_bounds = frame["peak_lower_r"].notna() & frame["peak_upper_r"].notna() & frame["peak_lower_r"].gt(frame["peak_upper_r"])
    if reversed_bounds.any():
        raise ValueError(f"peak_lower_r exceeds peak_upper_r on {int(reversed_bounds.sum())} row(s)")

    # Use complete peak bounds to resolve the three known outcome states. In
    # particular, an upper bound above 10R cannot support a definite miss.
    warnings = list(numeric_warnings)
    was_status = frame["status"].copy()
    lower_hit = frame["peak_lower_r"].notna() & frame["peak_lower_r"].gt(10)
    upper_miss = frame["peak_upper_r"].notna() & frame["peak_upper_r"].le(10)
    upper_can_cross = frame["peak_upper_r"].notna() & frame["peak_upper_r"].gt(10)
    # A lower bound already above 10R establishes a hit even if observation
    # later became censored. An upper bound alone cannot establish a miss.
    frame.loc[lower_hit, "status"] = "hit"
    bounded = ~frame["status_input"].eq("censored")
    frame.loc[bounded & ~lower_hit & upper_miss, "status"] = "miss"
    frame.loc[bounded & ~lower_hit & ~upper_miss & upper_can_cross, "status"] = "possible"
    changed = was_status.ne(frame["status"])
    if changed.any():
        warnings.append(
            f"{int(changed.sum())} status value(s) reconciled to peak bounds; upper-bound-only >10R is possible, not miss"
        )
    frame["confirmed_outcome"] = frame["status"].isin(["hit", "miss"])
    frame["is_hit"] = frame["status"].eq("hit")
    frame["is_possible"] = frame["status"].eq("possible")
    frame["is_censored"] = frame["status"].eq("censored")

    frame["time_to_gt10_hours"] = np.nan
    frame["time_to_peak_hours"] = np.nan
    hit_rows = frame["status"].eq("hit") & frame["first_gt10_bjt_parsed"].notna()
    gt10_indices = frame.index[hit_rows]
    delta = pd.Series(
        [(frame.at[index, "first_gt10_bjt_parsed"] - frame.at[index, "confirmed_bjt_parsed"]).total_seconds() / 3600
         for index in gt10_indices], index=gt10_indices, dtype=float,
    )
    # A negative delay is impossible for a forward event; keep it missing and warn.
    negative = delta.lt(0)
    if negative.any():
        warnings.append(f"{int(negative.sum())} hit row(s) have first_gt10_bjt before confirmed_bjt; delay omitted")
    frame.loc[delta.index[~negative], "time_to_gt10_hours"] = delta.loc[~negative]

    peak_rows = frame["peak_time_bjt_parsed"].notna()
    peak_indices = frame.index[peak_rows]
    peak_delta = pd.Series(
        [(frame.at[index, "peak_time_bjt_parsed"] - frame.at[index, "confirmed_bjt_parsed"]).total_seconds() / 3600
         for index in peak_indices], index=peak_indices, dtype=float,
    )
    frame.loc[peak_delta.index[peak_delta.ge(0)], "time_to_peak_hours"] = peak_delta[peak_delta.ge(0)]
    if peak_delta.lt(0).any():
        warnings.append(f"{int(peak_delta.lt(0).sum())} peak_time_bjt value(s) precede confirmation; delay omitted")
    return frame, warnings


def _rate_summary(frame: pd.DataFrame) -> dict[str, Any]:
    counts = frame["status"].value_counts()
    hits = int(counts.get("hit", 0))
    misses = int(counts.get("miss", 0))
    possible = int(counts.get("possible", 0))
    censored = int(counts.get("censored", 0))
    confirmed_n = hits + misses
    bounded_n = hits + misses + possible
    confirmed_rate = hits / confirmed_n if confirmed_n else None
    return {
        "signals_n": int(len(frame)),
        "hit_n": hits,
        "miss_n": misses,
        "possible_n": possible,
        "censored_n": censored,
        "confirmed_n": confirmed_n,
        "confirmed_hit_rate": confirmed_rate,
        "confirmed_hit_rate_pct": confirmed_rate * 100 if confirmed_rate is not None else None,
        "assessable_bounds_n": bounded_n,
        "bounded_hit_rate_lower": hits / bounded_n if bounded_n else None,
        "bounded_hit_rate_upper": (hits + possible) / bounded_n if bounded_n else None,
        "censored_share": censored / len(frame) if len(frame) else None,
        "possible_share": possible / len(frame) if len(frame) else None,
        "week_clusters_n": int(frame["week"].nunique()),
    }


def _timing_summary(frame: pd.DataFrame) -> dict[str, Any]:
    hits = frame.loc[frame["status"].eq("hit")]
    gt10 = hits["time_to_gt10_hours"].dropna()
    peak = frame["time_to_peak_hours"].dropna()
    def quantiles(values: pd.Series) -> dict[str, Any]:
        return {
            "n": int(values.size),
            "median_hours": float(values.median()) if len(values) else None,
            "p25_hours": float(values.quantile(0.25)) if len(values) else None,
            "p75_hours": float(values.quantile(0.75)) if len(values) else None,
        }
    return {"first_gt10_after_confirmation": quantiles(gt10), "peak_after_confirmation": quantiles(peak)}


def _concentration(frame: pd.DataFrame) -> dict[str, Any]:
    hits = frame.loc[frame["status"].eq("hit")]
    result: dict[str, Any] = {"hit_n": int(len(hits))}
    for name, column in (("symbol", "symbol"), ("week", "week")):
        counts = hits[column].value_counts()
        total = int(counts.sum())
        shares = counts / total if total else counts.astype(float)
        result[name] = {
            "groups_n": int(counts.size),
            "top_group": str(counts.index[0]) if len(counts) else None,
            "top_group_hits": int(counts.iloc[0]) if len(counts) else 0,
            "top1_share": float(shares.iloc[0]) if len(shares) else None,
            "top3_share": float(shares.iloc[:3].sum()) if len(shares) else None,
            "hhi": float((shares ** 2).sum()) if len(shares) else None,
            "counts": {str(k): int(v) for k, v in counts.items()},
        }
    return result


def _category_tables(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    periods = [("all_years", frame), ("through_2024", frame.loc[frame.year.le(2024)])]
    periods += [(str(year), frame.loc[frame.year.eq(year)]) for year in (2025, 2026)]
    for label, column in (("base_coin", "symbol"), ("direction", "direction"),
                          ("year", "year"), ("risk_tier", "risk_tier")):
        rows = []
        for period, period_frame in periods:
            values = period_frame[column].dropna().unique().tolist()
            for value in sorted(values, key=lambda x: (str(type(x)), str(x))):
                subset = period_frame.loc[period_frame[column].eq(value)]
                rows.append({"period": period,
                             "group": value.item() if isinstance(value, np.generic) else value,
                             **_rate_summary(subset)})
        tables[label] = rows
    coin_direction_rows = []
    for period, period_frame in periods:
        for (symbol, side), subset in period_frame.groupby(["symbol", "side"], sort=True):
            coin_direction_rows.append({"period": period, "symbol": str(symbol), "side": int(side),
                                        "direction": "long" if side == 1 else "short",
                                        **_rate_summary(subset)})
    tables["base_coin_by_direction"] = coin_direction_rows
    return tables


def _optional_boolean_summaries(frame: pd.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    periods = [("all_years", frame), ("through_2024", frame.loc[frame.year.le(2024)])]
    periods += [(str(year), frame.loc[frame.year.eq(year)]) for year in (2025, 2026)]
    summaries: dict[str, Any] = {}
    csv_rows: list[dict[str, Any]] = []
    for field in OPTIONAL_BOOLEAN_FIELDS:
        summaries[field] = {"column_provided": bool(frame[field + "_provided"].any()), "periods": {}}
        for period, subset in periods:
            buckets = {}
            for bucket in ("true", "false", "missing"):
                select = subset[field].isna() if bucket == "missing" else subset[field].eq(bucket)
                group = subset.loc[select]
                summary = _rate_summary(group)
                buckets[bucket] = summary
                csv_rows.append({"kind": "boolean_category", "feature": field,
                                 "threshold_train_through_2024": None, "period": period,
                                 "bucket": bucket, "split": f"{field}={bucket}", **summary,
                                 "delta_high_minus_low_pp": None,
                                 "week_cluster_ci95_low_pp": None,
                                 "week_cluster_ci95_high_pp": None,
                                 "coin_side_adjusted_delta_pp": None})
            summaries[field]["periods"][period] = buckets
    return summaries, csv_rows


def _stable_seed(seed: int, label: str) -> int:
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return (seed + int.from_bytes(digest[:4], "big")) % (2 ** 32)


def _pooled_delta(frame: pd.DataFrame, high_flag: pd.Series) -> tuple[float | None, dict[str, Any]]:
    known = frame["confirmed_outcome"] & high_flag.notna()
    high_bool = high_flag.fillna(False).astype(bool)
    high = frame.loc[known & high_bool, "is_hit"]
    low = frame.loc[known & ~high_bool, "is_hit"]
    high_n, low_n = len(high), len(low)
    high_rate = float(high.mean()) if high_n else None
    low_rate = float(low.mean()) if low_n else None
    delta = high_rate - low_rate if high_rate is not None and low_rate is not None else None
    return delta, {"high": {"hit_n": int(high.sum()), "confirmed_n": int(high_n), "hit_rate": high_rate},
                   "low": {"hit_n": int(low.sum()), "confirmed_n": int(low_n), "hit_rate": low_rate},
                   "delta_high_minus_low": delta,
                   "delta_high_minus_low_pp": delta * 100 if delta is not None else None}


def _week_bootstrap_ci(frame: pd.DataFrame, high_flag: pd.Series, *, reps: int, seed: int) -> dict[str, Any]:
    known = frame.loc[frame["confirmed_outcome"] & high_flag.notna(), ["week", "is_hit"]].copy()
    known["high"] = high_flag.loc[known.index].astype(bool)
    if known["week"].nunique() < 2 or reps <= 0:
        return {"ci95_pp": None, "week_clusters_n": int(known.week.nunique()), "valid_replicates": 0, "replicates": reps}
    grouped = known.groupby(["week", "high"], observed=False)["is_hit"].agg(["sum", "count"]).unstack("high", fill_value=0)
    # Reindex both buckets so weeks with one side still retain zero counts for the other.
    weeks = known["week"].drop_duplicates().tolist()
    arrays = []
    for column, statistic in ((True, "sum"), (True, "count"), (False, "sum"), (False, "count")):
        if (statistic, column) in grouped.columns:
            arrays.append(grouped[(statistic, column)].reindex(weeks, fill_value=0).to_numpy(float))
        else:
            arrays.append(np.zeros(len(weeks), dtype=float))
    high_hits, high_n, low_hits, low_n = arrays
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(reps):
        draw = rng.integers(0, len(weeks), size=len(weeks))
        hh, hn = high_hits[draw].sum(), high_n[draw].sum()
        lh, ln = low_hits[draw].sum(), low_n[draw].sum()
        if hn and ln:
            deltas.append(hh / hn - lh / ln)
    ci = np.quantile(deltas, [0.025, 0.975]) * 100 if deltas else None
    return {"ci95_pp": [float(ci[0]), float(ci[1])] if ci is not None else None,
            "week_clusters_n": int(len(weeks)), "valid_replicates": int(len(deltas)), "replicates": int(reps)}


def _coin_side_adjustment(frame: pd.DataFrame, high_flag: pd.Series) -> dict[str, Any]:
    known = frame.loc[frame["confirmed_outcome"] & high_flag.notna()].copy()
    known["high"] = high_flag.loc[known.index].astype(bool)
    deltas = []
    strata = []
    for (symbol, side), group in known.groupby(["symbol", "side"], sort=True):
        high = group.loc[group.high, "is_hit"]
        low = group.loc[~group.high, "is_hit"]
        if len(high) and len(low):
            difference = float(high.mean() - low.mean())
            deltas.append(difference)
            strata.append({"symbol": str(symbol), "side": int(side), "high_n": int(len(high)),
                           "low_n": int(len(low)), "delta_pp": difference * 100})
    return {"method": "equal_weight_mean_of_symbol_by_side_stratum_differences",
            "strata_n": len(deltas), "delta_pp": float(np.mean(deltas) * 100) if deltas else None,
            "strata": strata}


def _leave_one_coin_out(frame: pd.DataFrame, high_flag: pd.Series, full_delta: float | None) -> dict[str, Any]:
    deltas = {}
    for symbol in sorted(frame["symbol"].dropna().astype(str).unique()):
        keep = frame["symbol"].astype(str).ne(symbol)
        delta, _ = _pooled_delta(frame.loc[keep], high_flag.loc[keep])
        if delta is not None:
            deltas[symbol] = delta * 100
    signs_match = None
    if full_delta is not None:
        sign = np.sign(full_delta)
        signs_match = sum(np.sign(value) == sign for value in deltas.values())
    return {"coins_omitted_n": len(deltas), "delta_pp_min": min(deltas.values()) if deltas else None,
            "delta_pp_max": max(deltas.values()) if deltas else None,
            "omissions_same_sign_as_full": signs_match, "delta_pp_by_omitted_coin": deltas}


def _coin_side_year_permutation(frame: pd.DataFrame, high_flag: pd.Series, *, reps: int,
                                seed: int) -> dict[str, Any]:
    """Shuffle confirmed hit/miss labels only within symbol × side × year strata."""
    selected = frame.loc[frame["confirmed_outcome"] & high_flag.notna(),
                         ["symbol", "side", "year", "is_hit"]].copy()
    if selected.empty:
        return {"method": "within_symbol_side_year_label_permutation", "n": 0,
                "observed_delta_pp": None, "null_mean_delta_pp": None,
                "p_two_sided": None, "replicates": int(reps), "valid_replicates": 0,
                "strata_n": 0, "shuffleable_strata_n": 0}
    high = high_flag.loc[selected.index].astype(bool).to_numpy()
    labels = selected["is_hit"].astype(bool).to_numpy()
    high_n, low_n = int(high.sum()), int((~high).sum())
    observed = (labels[high].mean() - labels[~high].mean()) if high_n and low_n else None
    groups = []
    shuffleable = 0
    for _, group in selected.groupby(["symbol", "side", "year"], sort=True):
        indices = selected.index.get_indexer(group.index)
        group_labels = labels[indices]
        groups.append((indices, group_labels))
        if group_labels.any() and (~group_labels).any():
            shuffleable += 1
    if observed is None or reps <= 0:
        return {"method": "within_symbol_side_year_label_permutation", "n": int(len(selected)),
                "observed_delta_pp": observed * 100 if observed is not None else None,
                "null_mean_delta_pp": None, "p_two_sided": None, "replicates": int(reps),
                "valid_replicates": 0, "strata_n": int(len(groups)),
                "shuffleable_strata_n": int(shuffleable)}
    rng = np.random.default_rng(seed)
    null = np.empty(reps, dtype=float)
    for replicate in range(reps):
        shuffled = labels.copy()
        for indices, group_labels in groups:
            shuffled[indices] = rng.permutation(group_labels)
        null[replicate] = shuffled[high].mean() - shuffled[~high].mean()
    null_center = float(null.mean())
    p_value = (1 + int(np.count_nonzero(np.abs(null - null_center) >= abs(observed - null_center)))) / (reps + 1)
    return {"method": "within_symbol_side_year_label_permutation", "n": int(len(selected)),
            "observed_delta_pp": float(observed * 100),
            "null_mean_delta_pp": float(null.mean() * 100),
            "p_two_sided": float(p_value), "replicates": int(reps),
            "valid_replicates": int(reps), "strata_n": int(len(groups)),
            "shuffleable_strata_n": int(shuffleable),
            "note": "Two-sided tail is centered on the empirical permutation mean because fixed stratum composition can make the null mean nonzero; no multiplicity correction across screened features."}


def _feature_report(frame: pd.DataFrame, *, reps: int, seed: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    training = frame.loc[frame["year"].le(TRAIN_CUTOFF_YEAR)]
    if training.empty:
        raise ValueError("Need at least one signal confirmed by 2024-12-31 to freeze thresholds")
    results: dict[str, Any] = {}
    csv_rows: list[dict[str, Any]] = []
    periods = [("all_years", frame), ("through_2024", frame.loc[frame.year.le(2024)])]
    periods += [(str(year), frame.loc[frame.year.eq(year)]) for year in (2025, 2026)]

    for feature in FEATURES:
        training_values = training[feature].dropna()
        cutoff = float(training_values.median()) if len(training_values) else None
        results[feature] = {
            "threshold_rule": "training median through 2024; high means strictly greater, low means less than or equal",
            "threshold": cutoff,
            "threshold_training_n": int(len(training_values)),
            "periods": {},
        }
        permutation_summary = None
        for period, subset in periods:
            if cutoff is None:
                continue
            usable = subset[feature].notna()
            high_flag = subset[feature].gt(cutoff).where(usable)
            low_high: dict[str, Any] = {}
            for bucket, select in (("low_or_equal", usable & ~high_flag.fillna(False)),
                                   ("high", usable & high_flag.fillna(False))):
                group = subset.loc[select]
                summary = _rate_summary(group)
                low_high[bucket] = summary
                csv_rows.append({
                    "kind": "feature_window", "feature": feature, "threshold_train_through_2024": cutoff,
                    "period": period, "bucket": bucket,
                    "split": f"{feature} {'>' if bucket == 'high' else '<='} {cutoff:.12g}",
                    **summary,
                    "delta_high_minus_low_pp": None,
                    "week_cluster_ci95_low_pp": None,
                    "week_cluster_ci95_high_pp": None,
                    "coin_side_adjusted_delta_pp": None,
                })
            delta, pooled = _pooled_delta(subset.loc[usable], high_flag.loc[usable])
            bootstrap = _week_bootstrap_ci(
                subset.loc[usable], high_flag.loc[usable], reps=reps,
                seed=_stable_seed(seed, f"{feature}|{period}|week_bootstrap"),
            )
            adjusted = _coin_side_adjustment(subset.loc[usable], high_flag.loc[usable])
            leave_one = _leave_one_coin_out(subset.loc[usable], high_flag.loc[usable], delta)
            feature_period = {
                "feature_value_n": int(usable.sum()),
                "missing_feature_n": int((~usable).sum()),
                "low_or_equal": low_high["low_or_equal"],
                "high": low_high["high"],
                "confirmed_hit_rate_difference": pooled,
                "week_cluster_bootstrap": bootstrap,
                "within_coin_direction": adjusted,
                "leave_one_coin_out": leave_one,
            }
            results[feature]["periods"][period] = feature_period
            if period == "all_years":
                permutation_summary = _coin_side_year_permutation(
                    subset.loc[usable], high_flag.loc[usable], reps=reps,
                    seed=_stable_seed(seed, f"{feature}|all_years|permutation"),
                )
            for row in csv_rows[-2:]:
                row["delta_high_minus_low_pp"] = pooled["delta_high_minus_low_pp"]
                ci = bootstrap["ci95_pp"]
                if ci is not None:
                    row["week_cluster_ci95_low_pp"], row["week_cluster_ci95_high_pp"] = ci
                row["coin_side_adjusted_delta_pp"] = adjusted["delta_pp"]
                if period == "all_years" and permutation_summary is not None:
                    row["coin_side_year_permutation_p_two_sided"] = permutation_summary["p_two_sided"]
                    row["permutation_replicates"] = permutation_summary["valid_replicates"]
        results[feature]["all_years_coin_side_year_permutation"] = permutation_summary
    return results, csv_rows


def _risk_tiers(frame: pd.DataFrame) -> tuple[pd.Series, dict[str, Any]]:
    values = frame["stop_pct"]
    tier = pd.Series(pd.NA, index=frame.index, dtype="string")
    raw_values = values.to_numpy(dtype=float)
    tier.loc[np.isclose(raw_values, 0.015, atol=1e-9, rtol=0)] = "risk_1.5pct"
    tier.loc[np.isclose(raw_values, 0.03, atol=1e-9, rtol=0)] = "risk_3pct"
    other_mask = values.notna() & ~pd.Series(np.isclose(raw_values, 0.015, atol=1e-9, rtol=0), index=values.index) & ~pd.Series(np.isclose(raw_values, 0.03, atol=1e-9, rtol=0), index=values.index)
    tier.loc[other_mask] = "other_value"
    return tier, {"source_feature": "stop_pct", "training_freeze": False,
                  "fixed_mapping": {"0.015": "risk_1.5pct", "0.03": "risk_3pct"},
                  "assignment": "fixed stop_pct tiers; stop_pct is excluded from continuous feature screening",
                  "other_value_n": int(other_mask.sum())}


def _risk_csv_rows(frame: pd.DataFrame, thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for period, subset in [("all_years", frame), ("through_2024", frame.loc[frame.year.le(2024)])] + [
        (str(year), frame.loc[frame.year.eq(year)]) for year in (2025, 2026)
    ]:
        for tier in ("risk_1.5pct", "risk_3pct", "other_value"):
            group = subset.loc[subset.risk_tier.eq(tier)]
            rows.append({"kind": "risk_tier", "feature": "stop_pct", "threshold_train_through_2024": None,
                         "period": period, "bucket": tier,
                         "split": "fixed stop_pct groups: 0.015 / 0.03",
                         **_rate_summary(group), "delta_high_minus_low_pp": None,
                         "week_cluster_ci95_low_pp": None, "week_cluster_ci95_high_pp": None,
                         "coin_side_adjusted_delta_pp": None})
    return rows


def _direction_discovery(frame: pd.DataFrame) -> dict[str, Any]:
    training = frame.loc[frame.year.le(TRAIN_CUTOFF_YEAR)]
    by_side = {}
    for side, label in ((1, "long"), (-1, "short")):
        group = training.loc[training.side.eq(side)]
        by_side[label] = _rate_summary(group)
    long_rate = by_side["long"]["confirmed_hit_rate"]
    short_rate = by_side["short"]["confirmed_hit_rate"]
    if long_rate is None or short_rate is None or long_rate == short_rate:
        discovered = None
    else:
        discovered = "long" if long_rate > short_rate else "short"
    checks = {}
    for year in (2025, 2026):
        subset = frame.loc[frame.year.eq(year)]
        side_summaries = {label: _rate_summary(subset.loc[subset.side.eq(side)])
                          for side, label in ((1, "long"), (-1, "short"))}
        rates = {label: side_summaries[label]["confirmed_hit_rate"] for label in ("long", "short")}
        if discovered is None or rates["long"] is None or rates["short"] is None:
            holds = None
            difference = None
        else:
            other = "short" if discovered == "long" else "long"
            difference = rates[discovered] - rates[other]
            holds = difference > 0
        checks[str(year)] = {"by_direction": side_summaries,
                             "frozen_direction_hit_rate_difference_pp": difference * 100 if difference is not None else None,
                             "frozen_direction_remains_higher": holds}
    return {"rule": "choose the higher confirmed hit rate through 2024; tie or missing direction means no direction discovered",
            "through_2024_by_direction": by_side,
            "discovered_direction": discovered,
            "later_year_checks": checks,
            "exploratory_only": True}


def build_patterns(input_path: Path, output_dir: Path, *, reps: int = DEFAULT_BOOTSTRAPS,
                   seed: int = DEFAULT_SEED) -> dict[str, Any]:
    frame, warnings = load_signals(input_path)
    training = frame.loc[frame.year.le(TRAIN_CUTOFF_YEAR)]
    if training.empty:
        raise ValueError("Need at least one signal confirmed by 2024-12-31")
    frame["risk_tier"], risk_thresholds = _risk_tiers(frame)
    feature_reports, feature_rows = _feature_report(frame, reps=reps, seed=seed)
    feature_rows.extend(_risk_csv_rows(frame, risk_thresholds))
    boolean_summaries, boolean_rows = _optional_boolean_summaries(frame)
    feature_rows.extend(boolean_rows)

    period_summaries = {}
    period_frames = [("all_years", frame), ("through_2024", training)] + [
        (str(year), frame.loc[frame.year.eq(year)]) for year in (2025, 2026)
    ]
    for name, subset in period_frames:
        period_summaries[name] = {
            **_rate_summary(subset),
            "timing": _timing_summary(subset),
            "hit_concentration": _concentration(subset),
        }

    patterns = {
        "study": "Frozen OKX Top20 SPIKE 云带 V1.7 signal-time pattern screen for 12H peaks >10R",
        "input": str(input_path.resolve()),
        "output_files": ["patterns.json", "feature_windows.csv"],
        "multi_factor_exploration": {
            "is_exploratory": True,
            "feature_screen_count": len(FEATURES),
            "multivariable_model_fit": False,
            "multiple_testing_adjustment": False,
            "confirmatory_claim": False,
            "note": "Seven confirmation-time features are screened independently; results are descriptive hypotheses, not significance or strategy evidence.",
        },
        "method": {
            "threshold_freeze": "Continuous feature medians use signals confirmed on or before 2024-12-31, independent of outcome; 2025 and 2026 use the unchanged thresholds. stop_pct uses fixed 0.015 and 0.03 tiers.",
            "feature_comparison": "For each feature, high is strictly above its frozen training median and low is less than or equal; confirmed hit-rate difference is high minus low.",
            "outcome": "Peak bounds reconcile labels: lower bound >10R establishes a hit (including a censored row); for non-censored rows, upper bound <=10R establishes a miss and upper bound >10R without a lower-bound hit is possible. Possible never counts as a miss. Remaining censored rows are reported separately and excluded from the confirmed n/N and bounded rates.",
            "bounded_rate": "Lower bound = hits/(hits+misses+possible); upper bound = (hits+possible)/(hits+misses+possible). Censored observations are excluded because their status does not identify the peak outcome.",
            "risk_tier": "stop_pct groups are fixed at 0.015 and 0.03 as supplied; unrecognized values are reported separately.",
            "uncertainty": f"95% percentile interval from {reps} week-cluster bootstrap replicates for pooled high-minus-low feature differences; intervals are descriptive, not adjusted for the feature screen.",
            "coin_direction_adjustment": "Equal-weight mean of within-symbol-by-side high-minus-low differences among strata containing both feature groups; one-sided strata are omitted and counted by the reported strata_n.",
            "single_coin_sensitivity": "Report the high-minus-low difference after removing each coin in turn.",
            "timing": "first_gt10_bjt minus confirmed_bjt is summarized for confirmed hits only; peak_time_bjt is descriptive and never a predictor.",
            "features_used_for_comparison": list(FEATURES),
            "stop_pct_note": "stop_pct is summarized only as fixed 0.015 and 0.03 risk tiers; it is excluded from continuous screening.",
            "conditional_null": f"For each overall feature difference, hit/miss labels are permuted within symbol × side × year strata ({reps} replicates), with feature thresholds fixed; possible and censored rows are excluded from label shuffling. The two-sided tail is centered on the empirical permutation mean.",
            "outcome_or_future_fields_not_used_as_predictors": ["status", "peak_lower_r", "peak_upper_r", "first_gt10_bjt", "peak_time_bjt"],
        },
        "risk_tier_thresholds": risk_thresholds,
        "warnings": warnings,
        "status_reconciliation": {
            f"{source}->{target}": int(count)
            for (source, target), count in frame.loc[frame.status_input.ne(frame.status)]
                .groupby(["status_input", "status"], dropna=False).size().items()
        },
        "rows_n": int(len(frame)),
        "period_summaries": period_summaries,
        "category_summaries": _category_tables(frame),
        "optional_boolean_summaries": boolean_summaries,
        "direction_discovery": _direction_discovery(frame),
        "feature_comparisons": feature_reports,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "patterns.json").write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2, allow_nan=False, default=_json_default) + "\n", encoding="utf-8"
    )
    columns = [
        "kind", "feature", "threshold_train_through_2024", "period", "bucket", "split",
        "signals_n", "hit_n", "miss_n", "possible_n", "censored_n", "confirmed_n",
        "confirmed_hit_rate", "confirmed_hit_rate_pct", "assessable_bounds_n",
        "bounded_hit_rate_lower", "bounded_hit_rate_upper", "possible_share", "censored_share",
        "week_clusters_n", "delta_high_minus_low_pp", "week_cluster_ci95_low_pp",
        "week_cluster_ci95_high_pp", "coin_side_adjusted_delta_pp",
        "coin_side_year_permutation_p_two_sided", "permutation_replicates",
    ]
    pd.DataFrame(feature_rows).reindex(columns=columns).to_csv(output_dir / "feature_windows.csv", index=False)
    return patterns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Frozen signals.csv")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Defaults to the input directory")
    parser.add_argument("--bootstrap-reps", type=int, default=DEFAULT_BOOTSTRAPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    if args.bootstrap_reps < 0:
        parser.error("--bootstrap-reps must be >= 0")
    output_dir = args.output_dir or args.input.parent
    patterns = build_patterns(args.input, output_dir, reps=args.bootstrap_reps, seed=args.seed)
    print(json.dumps({"rows_n": patterns["rows_n"], "output_dir": str(output_dir.resolve()),
                      "periods": {name: value["confirmed_n"] for name, value in patterns["period_summaries"].items()},
                      "warnings": patterns["warnings"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
