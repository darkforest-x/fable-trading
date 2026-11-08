"""Freeze and evaluate a small set of single-feature screens for cloud signals.

This is a descriptive signal-path screen. It does not simulate fills, returns,
or trade execution. Development thresholds use signals confirmed by 2024 only;
development outcome labels are limited to paths whose exit bar had closed by
2025-01-01 00:00 BJT.
One candidate is frozen from the development cohort and then reported on 2025
and 2026. If no rule passes the gates, the closest rule is reported as a
diagnostic candidate and is explicitly not admitted.

Run from the repository root::

    python -m yoyo.evaluation.cloud_allmarket_screen [--input PATH]

The input is the frozen ``signals.csv`` from the parent census. All original
columns are copied to the Chinese-named detail CSV, with ``selected`` and
symbol/week/confirmation fields available for independent downstream checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENT = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1"
DEFAULT_INPUT = DEFAULT_EXPERIMENT / "signals.csv"
TRAIN_CUTOFF_YEAR = 2024
TRAIN_LABEL_END = pd.Timestamp("2025-01-01", tz="Asia/Shanghai")
TEST_YEARS = (2025, 2026)
BOOTSTRAPS = 1000
RANDOM_DRAWS = 1000
SEED = 20261108
MAJOR_PREFIXES = ("BTC", "ETH")
OUTCOMES = ("hit", "miss", "possible", "censored")

# One direction per feature is frozen before reading outcomes. Continuous
# cutoffs remove the least favorable training tertile; missing values always
# pass. The boolean rule keeps BTC-aligned signals and also keeps missing data.
RULES = (
    {"id": "close_distance_r_high", "feature": "close_distance_r", "keep": "ge_q33",
     "label": "确认距离R较高", "kind": "continuous"},
    {"id": "prior_atr_pct_low", "feature": "prior_atr_pct", "keep": "le_q67",
     "label": "确认前ATR百分比较低", "kind": "continuous"},
    {"id": "trend_eff_14_high", "feature": "trend_eff_14", "keep": "ge_q33",
     "label": "14期趋势效率较高", "kind": "continuous"},
    {"id": "btc_same_side_true", "feature": "btc_same_side", "keep": "true",
     "label": "与BTC同向", "kind": "boolean"},
    {"id": "rel_strength_14_high", "feature": "rel_strength_14", "keep": "ge_q33",
     "label": "14期相对BTC强度较高", "kind": "continuous"},
    {"id": "atr_expansion_high", "feature": "atr_expansion", "keep": "ge_q33",
     "label": "波动扩张较高", "kind": "continuous"},
    {"id": "ema_spread_atr_high", "feature": "ema_spread_atr", "keep": "ge_q33",
     "label": "EMA分离较高", "kind": "continuous"},
    {"id": "prior_stop_rate_6_low", "feature": "prior_stop_rate_6", "keep": "le_q67",
     "label": "本币近六笔历史止损率较低", "kind": "continuous"},
)

LEGACY_CENSUS_COLUMNS = (
    "rank", "symbol", "week", "side", "key_open_bjt", "confirmed_bjt",
    "reference_entry", "confirmation_close", "reference_stop", "stop_pct",
    "opposite_target", "status", "peak_lower_r", "peak_upper_r",
    "first_gt10_bjt", "peak_time_bjt", "path_end_bar_bjt", "exit_reason",
    "exit_reference_r", "first_12h_exit", "hour_order_if_possible",
    "pre_current_listing", "same_side_stoch", "confirmation_basis_lower_r",
    "confirmation_basis_upper_r", "close_distance_r", "penetration_atr",
    "ema_spread_atr", "wick_share", "monday_width_pct", "prior_atr_pct",
    "planned_r", "pure_wick", "signal_open", "signal_high", "signal_low",
    "ema20", "ema50", "bar_source", "evaluation_cutoff_bjt",
)
REQUIRED_COLUMNS = (*LEGACY_CENSUS_COLUMNS, "is_current", "was_top20",
                    "trend_eff_14", "btc_same_side", "rel_strength_14",
                    "atr_expansion", "prior_stop_rate_6")

BOOL_MAP = {
    "true": True, "1": True, "yes": True, "y": True, "t": True,
    "1.0": True, "false": False, "0": False, "0.0": False,
    "no": False, "n": False, "f": False,
}

EXIT_STOP = {"stop", "stopped", "fixed_stop", "固定止损", "止损"}
EXIT_CLOUD = {"cloud_flip", "cloud_flipped", "盘中云带反向", "云带反向"}
EXIT_OPEN = {"open", "仍在观察"}
EXIT_UNKNOWN = {"gap", "unavailable", "missing_hours", "行情缺口", "后续行情缺失", "小时数据不足"}


def _json_default(value: Any) -> Any:
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize value of type {type(value).__name__}")


def _dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default,
                              allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_bool(series: pd.Series) -> pd.Series:
    normalized = series.astype("string").str.strip().str.lower()
    return normalized.map(BOOL_MAP).astype("boolean")


def _parse_bjt(series: pd.Series) -> pd.Series:
    """Parse ISO timestamps to Beijing time; blank values remain NaT."""
    parsed = pd.to_datetime(series.replace(r"^\s*$", pd.NA, regex=True), errors="coerce", utc=True)
    return parsed.dt.tz_convert("Asia/Shanghai")


def _parse_exit(reason: Any) -> str:
    text = "" if pd.isna(reason) else str(reason).strip().lower()
    if text in EXIT_STOP:
        return "stop"
    if text in EXIT_CLOUD:
        return "cloud_flip"
    if text in EXIT_OPEN:
        return "open"
    if text in EXIT_UNKNOWN:
        return "unknown"
    return "unknown"


def _is_major(symbol: Any) -> bool:
    raw = str(symbol).strip().upper()
    base = raw.split("-")[0].split("/")[0].split("_")[0]
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if base.endswith(quote):
            base = base[:-len(quote)]
            break
    return base in MAJOR_PREFIXES


def load_signals(path: Path) -> tuple[pd.DataFrame, list[str]]:
    frame = pd.read_csv(path, low_memory=False)
    observed_flag_provided = "observed_first_24h_bars" in frame.columns
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required signals.csv columns: {missing}")
    if frame.empty:
        raise ValueError("signals.csv has no rows")
    frame = frame.copy()
    frame["_row_id"] = np.arange(len(frame), dtype=np.int64)
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["week"] = frame["week"].astype("string").str.strip()
    if frame["symbol"].isna().any() or frame["symbol"].eq("").any():
        raise ValueError("symbol must be populated on every signal")
    if frame["week"].isna().any() or frame["week"].eq("").any():
        raise ValueError("week must be populated for week-cluster summaries")

    side = pd.to_numeric(frame["side"], errors="coerce")
    if side.isna().any() or not side.isin([-1, 1]).all():
        raise ValueError("side must contain only -1 or 1")
    frame["side"] = side.astype(int)
    status = frame["status"].astype("string").str.strip().str.lower()
    invalid = sorted(set(status.dropna()) - set(OUTCOMES))
    if status.isna().any() or invalid:
        raise ValueError(f"status must be one of {OUTCOMES}; invalid={invalid}")
    frame["status"] = status

    for column in ("confirmed_bjt", "first_gt10_bjt", "path_end_bar_bjt", "evaluation_cutoff_bjt"):
        if column in frame:
            frame[column + "_parsed"] = _parse_bjt(frame[column])
    if frame["confirmed_bjt_parsed"].isna().any():
        raise ValueError("confirmed_bjt must be a valid timestamp on every signal")
    frame["year"] = frame["confirmed_bjt_parsed"].dt.year.astype(int)

    numeric_columns = (
        "peak_lower_r", "peak_upper_r", "confirmation_basis_lower_r",
        "confirmation_basis_upper_r", "close_distance_r", "prior_atr_pct",
        "trend_eff_14", "rel_strength_14", "atr_expansion", "ema_spread_atr",
        "prior_stop_rate_6",
    )
    warnings: list[str] = []
    for column in numeric_columns:
        raw = frame[column]
        converted = pd.to_numeric(raw, errors="coerce").astype(float)
        invalid_numeric = raw.notna() & raw.astype(str).str.strip().ne("") & converted.isna()
        if invalid_numeric.any():
            warnings.append(f"{column}: {int(invalid_numeric.sum())} nonnumeric value(s) treated as missing")
        frame[column] = converted
    reversed_bounds = (frame["peak_lower_r"].notna() & frame["peak_upper_r"].notna()
                       & frame["peak_lower_r"].gt(frame["peak_upper_r"]))
    if reversed_bounds.any():
        raise ValueError(f"peak_lower_r exceeds peak_upper_r on {int(reversed_bounds.sum())} row(s)")

    if not observed_flag_provided:
        frame["observed_first_24h_bars"] = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    for column in ("was_top20", "is_current", "btc_same_side", "observed_first_24h_bars"):
        parsed = _parse_bool(frame[column])
        invalid_bool = frame[column].notna() & frame[column].astype(str).str.strip().ne("") & parsed.isna()
        if invalid_bool.any():
            warnings.append(f"{column}: {int(invalid_bool.sum())} unrecognized boolean value(s) treated as missing")
        frame[column] = parsed

    frame["exit_kind"] = frame["exit_reason"].map(_parse_exit)
    frame["is_hit"] = frame["status"].eq("hit")
    frame["is_miss"] = frame["status"].eq("miss")
    frame["is_possible"] = frame["status"].eq("possible")
    frame["is_censored"] = frame["status"].eq("censored")
    frame["confirmed_outcome"] = frame["status"].isin(["hit", "miss"])

    # Fixed stops are exact path endpoints. "Early" means the first fixed stop
    # arrives in one of the first two 12H bars, [confirmed, confirmed+24h).
    confirmed = frame["confirmed_bjt_parsed"]
    exit_at = frame.get("path_end_bar_bjt_parsed", pd.Series(pd.NaT, index=frame.index))
    cutoff_at = frame.get("evaluation_cutoff_bjt_parsed", pd.Series(pd.NaT, index=frame.index))
    delta_hours = (exit_at - confirmed).dt.total_seconds() / 3600
    frame["fixed_stop"] = frame["exit_kind"].eq("stop")
    frame["early_fixed_stop_2bars"] = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    frame.loc[frame["fixed_stop"] & delta_hours.ge(0) & delta_hours.lt(24), "early_fixed_stop_2bars"] = True
    frame.loc[frame["fixed_stop"] & delta_hours.ge(24), "early_fixed_stop_2bars"] = False
    frame.loc[frame["exit_kind"].eq("cloud_flip"), "early_fixed_stop_2bars"] = False
    if observed_flag_provided:
        observed_two_bars = frame["observed_first_24h_bars"].eq(True)
    else:
        # Compatibility for small/older fixtures; production census supplies
        # the per-symbol archived-candle observation flag above.
        observed_two_bars = cutoff_at.notna() & cutoff_at.ge(confirmed + pd.Timedelta(hours=24))
    nonterminal = ~frame["exit_kind"].isin(["stop", "cloud_flip"])
    frame.loc[nonterminal & observed_two_bars.fillna(False), "early_fixed_stop_2bars"] = False
    # A stop that occurs inside the first two bars is annotated for whether
    # the path had definitely crossed 10R before the stop. Same-bar upper-only
    # crossing stays unknown because OHLC cannot order it against the stop.
    frame["early_stop_before_10r"] = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    early = frame["early_fixed_stop_2bars"].fillna(False)
    lower = frame["peak_lower_r"]
    upper = frame["peak_upper_r"]
    early_rows = frame["fixed_stop"] & early
    frame.loc[frame["early_fixed_stop_2bars"].eq(False), "early_stop_before_10r"] = False
    frame.loc[early_rows & lower.gt(10), "early_stop_before_10r"] = False
    frame.loc[early_rows & upper.le(10), "early_stop_before_10r"] = True
    frame.loc[early_rows & lower.le(10) & upper.gt(10), "early_stop_before_10r"] = pd.NA
    frame["early_stop_pre10_possible"] = (
        early_rows & lower.le(10) & upper.gt(10)
    )

    # Path timestamps are 12H bar opens. Require the exit bar itself to have
    # closed by the first validation boundary, not merely to have opened.
    ended_by_train_cutoff = (frame["exit_kind"].isin(["stop", "cloud_flip"])
                             & exit_at.notna()
                             & (exit_at + pd.Timedelta(hours=12)).le(TRAIN_LABEL_END))
    frame["train_label_eligible"] = frame["year"].le(TRAIN_CUTOFF_YEAR) & ended_by_train_cutoff & frame["confirmed_outcome"]
    frame["confirmation_basis_status"] = "censored"
    frame.loc[frame["confirmation_basis_upper_r"].gt(10), "confirmation_basis_status"] = "possible"
    frame.loc[frame["confirmation_basis_lower_r"].gt(10), "confirmation_basis_status"] = "hit"
    ended_path = frame["exit_kind"].isin(["stop", "cloud_flip"])
    frame.loc[ended_path & frame["confirmation_basis_upper_r"].le(10), "confirmation_basis_status"] = "miss"
    frame["major_symbol"] = frame["symbol"].map(_is_major)
    frame["altcoin"] = ~frame["major_symbol"]
    frame["primary_non_top20_alt"] = frame["altcoin"] & frame["was_top20"].eq(False)
    frame["all_altcoins"] = frame["altcoin"]
    frame["direction"] = frame["side"].map({1: "多", -1: "空"})
    return frame, warnings


def freeze_thresholds(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    development = frame.loc[frame["primary_non_top20_alt"] & frame["year"].le(TRAIN_CUTOFF_YEAR)]
    if development.empty:
        raise ValueError("Need signals confirmed by 2024 to freeze screening thresholds")
    frozen: dict[str, dict[str, Any]] = {}
    for rule in RULES:
        feature = rule["feature"]
        if rule["kind"] == "boolean":
            vals = development[feature].dropna().astype(bool)
            frozen[rule["id"]] = {
                "rule": dict(rule), "threshold": True,
                "training_n": int(len(vals)), "training_median": None,
                "quantile": None, "quantile_value": None,
            }
            continue
        vals = pd.to_numeric(development[feature], errors="coerce").dropna()
        q = 1 / 3 if rule["keep"] == "ge_q33" else 2 / 3
        threshold = float(vals.quantile(q)) if len(vals) else None
        frozen[rule["id"]] = {
            "rule": dict(rule), "threshold": threshold,
            "training_n": int(len(vals)),
            "training_median": float(vals.median()) if len(vals) else None,
            "quantile": q, "quantile_value": threshold,
        }
    return frozen


def apply_rule(frame: pd.DataFrame, frozen: dict[str, Any]) -> pd.Series:
    rule = frozen["rule"]
    feature = rule["feature"]
    values = frame[feature]
    missing = values.isna()
    if rule["kind"] == "boolean":
        passed = values.eq(True)
    elif rule["keep"] == "ge_q33":
        cutoff = frozen["threshold"]
        passed = values.ge(cutoff) if cutoff is not None else pd.Series(False, index=frame.index)
    else:
        cutoff = frozen["threshold"]
        passed = values.le(cutoff) if cutoff is not None else pd.Series(False, index=frame.index)
    return (passed | missing).astype(bool)


def _known_rates(frame: pd.DataFrame) -> dict[str, Any]:
    statuses = frame["status"].value_counts()
    hit = int(statuses.get("hit", 0))
    miss = int(statuses.get("miss", 0))
    possible = int(statuses.get("possible", 0))
    censored = int(statuses.get("censored", 0))
    known = hit + miss
    bounded = known + possible
    early = frame["early_fixed_stop_2bars"].dropna().astype(bool)
    early_n = int(early.sum())
    early_assessable = int(len(early))
    early_pre10 = frame["early_stop_before_10r"].dropna().astype(bool)
    early_pre10_n = int(early_pre10.sum())
    early_pre10_assessable = int(len(early_pre10))
    stop_n = int(frame["fixed_stop"].sum())
    hit_rate = hit / known if known else None
    basis = frame["confirmation_basis_status"].value_counts()
    basis_hit = int(basis.get("hit", 0))
    basis_miss = int(basis.get("miss", 0))
    basis_possible = int(basis.get("possible", 0))
    basis_censored = int(basis.get("censored", 0))
    basis_known = basis_hit + basis_miss
    return {
        "signals_n": int(len(frame)),
        "hit_n": hit, "miss_n": miss, "possible_n": possible, "censored_n": censored,
        "confirmed_n": known,
        "hit_precision_pct": 100 * hit_rate if hit_rate is not None else None,
        "bounded_hit_rate_low_pct": 100 * hit / bounded if bounded else None,
        "bounded_hit_rate_high_pct": 100 * (hit + possible) / bounded if bounded else None,
        "confirmation_basis_hit_n": basis_hit,
        "confirmation_basis_miss_n": basis_miss,
        "confirmation_basis_possible_n": basis_possible,
        "confirmation_basis_censored_n": basis_censored,
        "confirmation_basis_known_n": basis_known,
        "confirmation_basis_hit_precision_pct": 100 * basis_hit / basis_known if basis_known else None,
        "confirmation_basis_hit_rate_low_pct": 100 * basis_hit / (basis_known + basis_possible) if basis_known + basis_possible else None,
        "confirmation_basis_hit_rate_high_pct": 100 * (basis_hit + basis_possible) / (basis_known + basis_possible) if basis_known + basis_possible else None,
        "fixed_stop_n": stop_n,
        "fixed_stop_rate_all_pct": 100 * stop_n / len(frame) if len(frame) else None,
        "early_fixed_stop_2bars_n": early_n,
        "early_stop_assessable_n": early_assessable,
        "early_fixed_stop_2bars_rate_pct": 100 * early_n / early_assessable if early_assessable else None,
        "early_fixed_stop_2bars_share_all_pct": 100 * early_n / len(frame) if len(frame) else None,
        "early_stop_before_10r_n": early_pre10_n,
        "early_stop_before_10r_assessable_n": early_pre10_assessable,
        "early_stop_before_10r_rate_pct": 100 * early_pre10_n / early_pre10_assessable if early_pre10_assessable else None,
        "early_stop_pre10_order_ambiguous_n": int(frame["early_stop_pre10_possible"].sum()),
        "week_clusters_n": int(frame["week"].nunique()),
    }


def _matched_expected(frame: pd.DataFrame, selected: pd.DataFrame, metric: str) -> dict[str, Any]:
    """Expected equal-size random cohort, matched by symbol × side.

    Each stratum contributes as many random raw signals as the screen selected.
    Rates condition on labels that are observable for the requested metric.
    This is the exact expectation under within-stratum random sampling; a
    Monte-Carlo tail probability is added for the single frozen candidate.
    """
    if metric == "early":
        event = frame["early_fixed_stop_2bars"].eq(True)
        known = frame["early_fixed_stop_2bars"].notna()
    elif metric == "early_pre10":
        event = frame["early_stop_before_10r"].eq(True)
        known = frame["early_stop_before_10r"].notna()
    elif metric == "hit":
        event = frame["status"].eq("hit")
        known = frame["status"].isin(["hit", "miss"])
    elif metric == "robust_hit":
        event = frame["confirmation_basis_status"].eq("hit")
        known = frame["confirmation_basis_status"].isin(["hit", "miss"])
    else:
        raise ValueError(metric)

    strata_cols = ["symbol", "side"] + (["year"] if frame["year"].nunique() > 1 else [])
    selected_counts = selected.groupby(strata_cols, observed=False).size()
    if selected_counts.empty:
        return {"expected_event_n": 0.0, "expected_assessable_n": 0.0, "rate_pct": None}
    work = frame.assign(_metric_known=known.astype(int),
                        _metric_event=event.fillna(False).astype(int))
    base = work.groupby(strata_cols, observed=False).agg(
        raw_n=("_row_id", "size"), known_n=("_metric_known", "sum"), event_n=("_metric_event", "sum")
    )
    matched = base.join(selected_counts.rename("selected_raw_n"), how="inner").fillna(0)
    raw_n = matched["raw_n"].replace(0, np.nan)
    expected_known = (matched["selected_raw_n"] * matched["known_n"] / raw_n).sum()
    expected_event = (matched["selected_raw_n"] * matched["event_n"] / raw_n).sum()
    rate = expected_event / expected_known if expected_known else None
    return {"expected_event_n": float(expected_event), "expected_assessable_n": float(expected_known),
            "rate_pct": 100 * float(rate) if rate is not None else None}


def _matched_random_draws(frame: pd.DataFrame, selected: pd.DataFrame, *, metric: str,
                          reps: int, seed: int) -> dict[str, Any]:
    """Draw same-size coin/side-matched random cohorts without replacement."""
    if metric == "early":
        values = frame["early_fixed_stop_2bars"].astype("Int64").to_numpy(dtype=float, na_value=np.nan)
        selected_values = selected["early_fixed_stop_2bars"].astype("Int64").to_numpy(dtype=float, na_value=np.nan)
    elif metric == "hit":
        values = frame["status"].map({"hit": 1.0, "miss": 0.0}).to_numpy(dtype=float, na_value=np.nan)
        selected_values = selected["status"].map({"hit": 1.0, "miss": 0.0}).to_numpy(dtype=float, na_value=np.nan)
    else:
        raise ValueError(metric)
    selected_rate = float(np.nanmean(selected_values)) if np.isfinite(selected_values).any() else None
    if selected_rate is None or reps <= 0:
        return {"observed_rate_pct": 100 * selected_rate if selected_rate is not None else None,
                "random_mean_rate_pct": None, "random_p_lower_tail": None,
                "valid_draws": 0, "replicates": reps}

    strata_cols = ["symbol", "side"] + (["year"] if frame["year"].nunique() > 1 else [])
    selected_n = selected.groupby(strata_cols, observed=False).size()
    groups: list[tuple[np.ndarray, int]] = []
    frame_positions = pd.Series(np.arange(len(frame)), index=frame.index)
    for key, n_selected in selected_n.items():
        if not isinstance(key, tuple):
            key = (key,)
        mask = pd.Series(True, index=frame.index)
        for col, value in zip(strata_cols, key):
            mask &= frame[col].eq(value)
        positions = frame_positions.loc[mask].to_numpy(dtype=int)
        if len(positions) >= int(n_selected):
            groups.append((positions, int(n_selected)))
    if not groups:
        return {"observed_rate_pct": 100 * selected_rate, "random_mean_rate_pct": None,
                "random_p_lower_tail": None, "valid_draws": 0, "replicates": reps}
    rng = np.random.default_rng(seed)
    random_rates: list[float] = []
    for _ in range(reps):
        sampled: list[np.ndarray] = []
        for positions, n in groups:
            sampled.append(rng.choice(positions, size=n, replace=False))
        picked = values[np.concatenate(sampled)]
        known = np.isfinite(picked)
        if known.any():
            random_rates.append(float(np.mean(picked[known])))
    if not random_rates:
        return {"observed_rate_pct": 100 * selected_rate, "random_mean_rate_pct": None,
                "random_p_lower_tail": None, "valid_draws": 0, "replicates": reps}
    arr = np.asarray(random_rates)
    return {
        "observed_rate_pct": 100 * selected_rate,
        "random_mean_rate_pct": 100 * float(arr.mean()),
        "random_p_lower_tail": float((1 + np.count_nonzero(arr <= selected_rate)) / (len(arr) + 1)),
        "random_p_upper_tail": float((1 + np.count_nonzero(arr >= selected_rate)) / (len(arr) + 1)),
        "random_q025_rate_pct": float(np.quantile(arr, .025) * 100),
        "random_q975_rate_pct": float(np.quantile(arr, .975) * 100),
        "valid_draws": int(len(arr)), "replicates": reps,
    }


def _week_cluster_ci(frame: pd.DataFrame, selected: pd.DataFrame, *, metric: str,
                     reps: int, seed: int) -> dict[str, Any]:
    """Week-cluster CI for selected minus same-coin/side matched expected rate."""
    if metric == "early":
        known_col, event_col = "early_fixed_stop_2bars", "early_fixed_stop_2bars"
        known_mask = frame[known_col].notna()
    elif metric == "hit":
        known_col, event_col = "confirmed_outcome", "is_hit"
        known_mask = frame[known_col].fillna(False)
    else:
        raise ValueError(metric)
    all_rows = frame.loc[known_mask].copy()
    chosen = selected.loc[selected[known_col].notna() if metric == "early"
                          else selected[known_col].fillna(False)].copy()
    if all_rows["week"].nunique() < 2 or chosen.empty or reps <= 0:
        return {"delta_ci95_pp": None, "week_clusters_n": int(all_rows["week"].nunique()),
                "valid_replicates": 0, "replicates": reps}
    event_values = all_rows[event_col].astype(bool).to_numpy(dtype=int)
    chosen_ids = set(chosen["_row_id"].astype(int))
    chosen_mask = all_rows["_row_id"].astype(int).isin(chosen_ids).to_numpy()
    # Compact week and symbol×side strata indices for fast bootstrap draws.
    weeks, week_idx = np.unique(all_rows["week"].astype(str).to_numpy(), return_inverse=True)
    strata_cols = ["symbol", "side"] + (["year"] if all_rows["year"].nunique() > 1 else [])
    strata = all_rows[strata_cols].astype(str).agg("|".join, axis=1)
    _, stratum_idx = np.unique(strata.to_numpy(), return_inverse=True)
    n_strata = int(stratum_idx.max()) + 1
    week_stratum = week_idx * n_strata + stratum_idx
    base_n = np.bincount(week_stratum, minlength=len(weeks) * n_strata).reshape(len(weeks), n_strata)
    base_k = np.bincount(week_stratum, weights=event_values, minlength=len(weeks) * n_strata).reshape(len(weeks), n_strata)
    chosen_n = np.bincount(week_stratum, weights=chosen_mask.astype(int), minlength=len(weeks) * n_strata).reshape(len(weeks), n_strata)
    chosen_k = np.bincount(week_stratum, weights=event_values * chosen_mask, minlength=len(weeks) * n_strata).reshape(len(weeks), n_strata)
    rng = np.random.default_rng(seed)
    deltas: list[float] = []
    for _ in range(reps):
        week_weights = rng.multinomial(len(weeks), np.full(len(weeks), 1 / len(weeks)))
        bn = week_weights @ base_n
        bk = week_weights @ base_k
        cn = week_weights @ chosen_n
        ck = week_weights @ chosen_k
        total = float(cn.sum())
        valid = (bn > 0) & (cn > 0)
        if total and valid.any():
            matched_event = float(np.sum(cn[valid] * bk[valid] / bn[valid]))
            deltas.append(float(ck.sum() / total - matched_event / total))
    ci = np.quantile(deltas, [.025, .975]) * 100 if deltas else None
    return {"delta_ci95_pp": [float(ci[0]), float(ci[1])] if ci is not None else None,
            "week_clusters_n": int(len(weeks)), "valid_replicates": int(len(deltas)),
            "replicates": reps}


def _cohort_rows(frame: pd.DataFrame, cohort: str, year: int | None,
                 train_labels_only: bool = False) -> pd.DataFrame:
    subset = frame.loc[frame[cohort]]
    if year is not None:
        subset = subset.loc[subset["year"].eq(year)]
    if train_labels_only:
        subset = subset.loc[subset["train_label_eligible"]]
    return subset


def _evaluate_group(frame: pd.DataFrame, selected: pd.DataFrame, *, cohort: str,
                    year_label: str, rule_id: str, training: bool,
                    random_draws: int = 0, bootstraps: int = 0, seed: int = SEED) -> dict[str, Any]:
    base_stats = _known_rates(frame)
    selected_stats = _known_rates(selected)
    base_hits = int(frame["status"].eq("hit").sum())
    kept_hits = int(selected["status"].eq("hit").sum())
    all_early = int(frame["early_fixed_stop_2bars"].fillna(False).sum())
    kept_early = int(selected["early_fixed_stop_2bars"].fillna(False).sum())
    removed_early = all_early - kept_early
    hit_recall = kept_hits / base_hits if base_hits else None
    early_count_reduction = removed_early / all_early if all_early else None
    selected_early_rate = selected_stats["early_fixed_stop_2bars_rate_pct"]
    base_early_rate = base_stats["early_fixed_stop_2bars_rate_pct"]
    relative_rate_reduction = (
        1 - selected_early_rate / base_early_rate
        if selected_early_rate is not None and base_early_rate not in (None, 0) else None
    )
    matched_early = _matched_expected(frame, selected, "early")
    matched_hit = _matched_expected(frame, selected, "hit")
    matched_robust = _matched_expected(frame, selected, "robust_hit")
    matched_rate = matched_early["rate_pct"]
    matched_beaten = (selected_early_rate is not None and matched_rate is not None
                      and selected_early_rate < matched_rate)
    baseline_beaten = (selected_early_rate is not None and base_early_rate is not None
                       and selected_early_rate < base_early_rate)

    result: dict[str, Any] = {
        "cohort": cohort, "period": year_label, "rule_id": rule_id,
        "training_label_restricted": bool(training),
        "baseline": base_stats, "selected": selected_stats,
        "winner_recall": hit_recall,
        "false_drop_winner_n": base_hits - kept_hits,
        "false_drop_winner_share_pct": 100 * (base_hits - kept_hits) / base_hits if base_hits else None,
        "early_stop_n_removed": removed_early,
        "early_stop_count_reduction": early_count_reduction,
        "early_stop_rate_relative_reduction": relative_rate_reduction,
        "matched_random": {
            "early_stop_equal_size_expected": matched_early,
            "hit_equal_size_expected": matched_hit,
            "confirmation_basis_hit_equal_size_expected": matched_robust,
            "selected_early_stop_rate_below_expected": matched_beaten,
            "selected_early_stop_rate_below_unfiltered_baseline": baseline_beaten,
            "early_stop_delta_vs_unfiltered_baseline_pp": (
                selected_early_rate - base_early_rate
                if selected_early_rate is not None and base_early_rate is not None else None
            ),
            "early_stop_delta_vs_expected_pp": (selected_early_rate - matched_rate
                                                  if selected_early_rate is not None and matched_rate is not None else None),
            "hit_precision_delta_vs_expected_pp": (
                selected_stats["hit_precision_pct"] - matched_hit["rate_pct"]
                if selected_stats["hit_precision_pct"] is not None and matched_hit["rate_pct"] is not None else None
            ),
        },
    }
    if random_draws:
        result["matched_random"]["early_stop_monte_carlo"] = _matched_random_draws(
            frame, selected, metric="early", reps=random_draws, seed=seed + 17,
        )
    if bootstraps:
        result["week_cluster_bootstrap"] = {
            "early_stop_delta_vs_matched_expected": _week_cluster_ci(
                frame, selected, metric="early", reps=bootstraps, seed=seed + 23,
            ),
            "hit_rate_delta_vs_matched_expected": _week_cluster_ci(
                frame, selected, metric="hit", reps=bootstraps, seed=seed + 29,
            ),
        }
    return result


def _decision_metrics(result: dict[str, Any]) -> dict[str, Any]:
    selected = result["selected"]
    return {
        "winner_recall": result["winner_recall"],
        "early_stop_count_reduction": result["early_stop_count_reduction"],
        "candidate_early_stop_rate_pct": selected["early_fixed_stop_2bars_rate_pct"],
        "baseline_early_stop_rate_pct": result["baseline"]["early_fixed_stop_2bars_rate_pct"],
        "matched_early_stop_rate_pct": result["matched_random"]["early_stop_equal_size_expected"]["rate_pct"],
        "early_stop_rate_delta_vs_baseline_pp": result["matched_random"]["early_stop_delta_vs_unfiltered_baseline_pp"],
        "early_rate_beats_random": result["matched_random"]["selected_early_stop_rate_below_expected"],
        "early_rate_below_baseline": result["matched_random"]["selected_early_stop_rate_below_unfiltered_baseline"],
        "early_stop_n_removed": result["early_stop_n_removed"],
        "false_drop_winner_n": result["false_drop_winner_n"],
        "candidate_signals_n": selected["signals_n"],
        "candidate_early_stop_assessable_n": selected["early_stop_assessable_n"],
        "baseline_early_stop_n": result["baseline"]["early_fixed_stop_2bars_n"],
        "baseline_winner_n": result["baseline"]["hit_n"],
        "candidate_weeks_n": selected["week_clusters_n"],
    }


def _gate_decision(metrics: dict[str, Any]) -> dict[str, Any]:
    recall = metrics["winner_recall"]
    stop_reduction = metrics["early_stop_count_reduction"]
    beats = metrics["early_rate_beats_random"]
    min_data = (
        metrics["candidate_signals_n"] >= 30
        and metrics["candidate_early_stop_assessable_n"] >= 20
        and metrics["baseline_early_stop_n"] >= 5
        and metrics["baseline_winner_n"] >= 5
        and metrics["candidate_weeks_n"] >= 8
    )
    checks = {
        "retain_at_least_70pct_winners": recall is not None and recall >= .70,
        "remove_at_least_30pct_early_stop_count": stop_reduction is not None and stop_reduction >= .30,
        "early_stop_rate_below_matched_random": beats is True,
        "early_stop_rate_below_unfiltered_baseline": metrics["early_rate_below_baseline"] is True,
        "minimum_development_support": bool(min_data),
    }
    return {"checks": checks, "passed": all(checks.values()),
            "failed": [name for name, passed in checks.items() if not passed]}


def _closest_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    rule_id, info = item
    metrics = info["metrics"]
    gate = info["gate"]
    passed_n = sum(gate["checks"].values())
    recall = metrics["winner_recall"]
    early_reduction = metrics["early_stop_count_reduction"]
    candidate_rate = metrics["candidate_early_stop_rate_pct"]
    matched_rate = metrics["matched_early_stop_rate_pct"]
    rate_gap = (matched_rate - candidate_rate
                if candidate_rate is not None and matched_rate is not None else -1e9)
    # Deterministic predeclared ordering: number of gates passed, normalized
    # deficit, early-stop count reduction, winner recall, then rule ID.
    deficit = max(0.0, .70 - (recall if recall is not None else 0.0))
    deficit += max(0.0, .30 - (early_reduction if early_reduction is not None else 0.0))
    deficit += max(0.0, -rate_gap / 100 if rate_gap > -1e8 else 1.0)
    return (-passed_n, deficit, -(early_reduction if early_reduction is not None else -1.0),
            -(recall if recall is not None else -1.0), -rate_gap, rule_id)


def _jsonable_counts(series: pd.Series) -> dict[str, int]:
    return {str(key): int(value) for key, value in series.value_counts(dropna=False).items()}


def _decision_and_metrics(frame: pd.DataFrame, thresholds: dict[str, Any], *,
                          bootstraps: int, random_draws: int) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, pd.Series]]:
    selection_masks = {rid: apply_rule(frame, rule) for rid, rule in thresholds.items()}
    main_train = _cohort_rows(frame, "primary_non_top20_alt", None, train_labels_only=True)
    if main_train.empty:
        raise ValueError("Primary development cohort has no completed hit/miss training labels")
    main_train = main_train.loc[main_train["year"].le(TRAIN_CUTOFF_YEAR)]
    decision_rows: dict[str, dict[str, Any]] = {}
    detailed: list[dict[str, Any]] = []
    for rid, frozen in thresholds.items():
        all_mask = selection_masks[rid]
        rows = _evaluate_group(main_train, main_train.loc[all_mask.reindex(main_train.index).fillna(False)],
                               cohort="primary_non_top20_alt", year_label="development_through_2024",
                               rule_id=rid, training=True, random_draws=0,
                               bootstraps=0, seed=SEED + list(thresholds).index(rid) * 101)
        metrics = _decision_metrics(rows)
        gate = _gate_decision(metrics)
        decision_rows[rid] = {"metrics": metrics, "gate": gate, "result": rows,
                              "threshold": frozen["threshold"], "rule": frozen["rule"]}
        detailed.append({"rule_id": rid, "rule_label": frozen["rule"]["label"],
                         "rule": frozen["rule"], "threshold": frozen["threshold"],
                         "threshold_training_median": frozen["training_median"],
                         "threshold_training_n": frozen["training_n"],
                         "development_metrics": metrics, "gate": gate})

    eligible = [(rid, row) for rid, row in decision_rows.items() if row["gate"]["passed"]]
    if eligible:
        # Primary ranking is the absolute early-stop count removed, then lower
        # false-drop count, then lower candidate early-stop rate, then stable ID.
        chosen_id, chosen = sorted(eligible, key=lambda item: (
            -(item[1]["metrics"]["early_stop_n_removed"]),
            item[1]["metrics"]["false_drop_winner_n"],
            item[1]["metrics"]["candidate_early_stop_rate_pct"]
            if item[1]["metrics"]["candidate_early_stop_rate_pct"] is not None else float("inf"),
            item[0],
        ))[0]
        admitted = True
    else:
        chosen_id, chosen = sorted(decision_rows.items(), key=_closest_key)[0]
        admitted = False
    decision = {
        "primary_cohort": "altcoins where was_top20=false; BTC and ETH excluded",
        "label_cutoff": "Only paths whose exit bar closed by 2025-01-01 00:00 BJT are development labels; confirmed year <= 2024; only definite hit/miss outcomes.",
        "rule_count": len(RULES), "candidate_rule_id": chosen_id,
        "candidate_rule": chosen["rule"], "candidate_threshold": chosen["threshold"],
        "candidate_admitted": admitted,
        "candidate_status": "admitted_from_development" if admitted else "diagnostic_only_no_rule_passed_all_development_gates",
        "candidate_development_metrics": chosen["metrics"],
        "candidate_development_gate": chosen["gate"],
        "fallback_order": "If no rule passes, rank by most development gates passed, smallest combined gate deficit, largest early-stop count reduction, highest winner recall, then stable rule ID.",
        "all_rules_development": detailed,
    }
    return decision, detailed, selection_masks


def _summary_row(result: dict[str, Any], *, candidate: bool, candidate_admitted: bool) -> dict[str, Any]:
    b, s = result["baseline"], result["selected"]
    random_early = result["matched_random"]["early_stop_equal_size_expected"]
    random_hit = result["matched_random"]["hit_equal_size_expected"]
    random_robust = result["matched_random"]["confirmation_basis_hit_equal_size_expected"]
    ci = result.get("week_cluster_bootstrap", {})
    early_ci = ci.get("early_stop_delta_vs_matched_expected", {})
    hit_ci = ci.get("hit_rate_delta_vs_matched_expected", {})
    mc = result.get("matched_random", {}).get("early_stop_monte_carlo", {})
    return {
        "队列": result["cohort"], "确认年份/阶段": result["period"], "规则ID": result["rule_id"],
        "是否唯一冻结候选": candidate, "候选是否过开发门": candidate_admitted,
        "原信号数": b["signals_n"], "入选信号数": s["signals_n"],
        "入选比例%": 100 * s["signals_n"] / b["signals_n"] if b["signals_n"] else None,
        "明确>10R数": b["hit_n"], "入选明确>10R数": s["hit_n"],
        ">10R召回%": 100 * result["winner_recall"] if result["winner_recall"] is not None else None,
        "误丢明确赢家数": result["false_drop_winner_n"],
        "误丢赢家占比%": result["false_drop_winner_share_pct"],
        "原组明确命中精确率%": b["hit_precision_pct"], "入选组明确命中精确率%": s["hit_precision_pct"],
        "入选组有界命中率下界%": s["bounded_hit_rate_low_pct"],
        "入选组有界命中率上界%": s["bounded_hit_rate_high_pct"],
        "原组确认价口径明确>10R数": b["confirmation_basis_hit_n"],
        "入选确认价口径明确>10R数": s["confirmation_basis_hit_n"],
        "确认价口径>10R召回%": (100 * s["confirmation_basis_hit_n"] / b["confirmation_basis_hit_n"]
                                    if b["confirmation_basis_hit_n"] else None),
        "入选确认价口径精确率%": s["confirmation_basis_hit_precision_pct"],
        "入选确认价口径有界命中率下界%": s["confirmation_basis_hit_rate_low_pct"],
        "入选确认价口径有界命中率上界%": s["confirmation_basis_hit_rate_high_pct"],
        "入选确认价口径未决数": s["confirmation_basis_possible_n"],
        "入选确认价口径截尾数": s["confirmation_basis_censored_n"],
        "入选可能数": s["possible_n"], "入选未决数": s["censored_n"],
        "全部固定止损数": b["fixed_stop_n"], "入选固定止损数": s["fixed_stop_n"],
        "剔除固定止损数": b["fixed_stop_n"] - s["fixed_stop_n"],
        "原组两根内早止损数": b["early_fixed_stop_2bars_n"],
        "入选两根内早止损数": s["early_fixed_stop_2bars_n"],
        "剔除两根内早止损数": result["early_stop_n_removed"],
        "早止损笔数减少比例%": 100 * result["early_stop_count_reduction"] if result["early_stop_count_reduction"] is not None else None,
        "原组早止损率%": b["early_fixed_stop_2bars_rate_pct"],
        "入选早止损率%": s["early_fixed_stop_2bars_rate_pct"],
        "早止损率相对降幅%": 100 * result["early_stop_rate_relative_reduction"] if result["early_stop_rate_relative_reduction"] is not None else None,
        "早止损率同币同向同年等量随机期望%": random_early["rate_pct"],
        "入选早止损率减匹配随机pp": result["matched_random"]["early_stop_delta_vs_expected_pp"],
        "早止损率低于匹配随机": result["matched_random"]["selected_early_stop_rate_below_expected"],
        "入选早止损率低于未筛选基线": result["matched_random"]["selected_early_stop_rate_below_unfiltered_baseline"],
        "入选早止损率减未筛选基线pp": result["matched_random"]["early_stop_delta_vs_unfiltered_baseline_pp"],
        "入选早止损数/可判数": f"{s['early_fixed_stop_2bars_n']}/{s['early_stop_assessable_n']}",
        "入选早止损前未达10R数": s["early_stop_before_10r_n"],
        "入选早止损前未达10R判定数": s["early_stop_before_10r_assessable_n"],
        "入选早止损同K顺序不明数": s["early_stop_pre10_order_ambiguous_n"],
        "匹配随机命中精确率期望%": random_hit["rate_pct"],
        "确认价口径匹配随机>10R期望%": random_robust["rate_pct"],
        "确认价口径入选明确>10R数": int(s.get("confirmation_basis_hit_n", 0)),
        "周聚类早止损差95区间pp": early_ci.get("delta_ci95_pp"),
        "周聚类命中率差95区间pp": hit_ci.get("delta_ci95_pp"),
        "候选匹配随机早止损率%": mc.get("random_mean_rate_pct"),
        "候选匹配随机早止损95区间下界%": mc.get("random_q025_rate_pct"),
        "候选匹配随机早止损95区间上界%": mc.get("random_q975_rate_pct"),
        "候选匹配随机早止损单侧p": mc.get("random_p_lower_tail"),
        "周簇数": s["week_clusters_n"],
    }


def _add_confirmation_basis_counts(result: dict[str, Any], selected: pd.DataFrame) -> None:
    basis = selected["confirmation_basis_status"].value_counts()
    result["selected"]["confirmation_basis_hit_n"] = int(basis.get("hit", 0))
    result["selected"]["confirmation_basis_miss_n"] = int(basis.get("miss", 0))
    result["selected"]["confirmation_basis_possible_n"] = int(basis.get("possible", 0))
    result["selected"]["confirmation_basis_known_n"] = int(basis.get("hit", 0) + basis.get("miss", 0))
    result["selected"]["confirmation_basis_hit_precision_pct"] = (
        100 * basis.get("hit", 0) / (basis.get("hit", 0) + basis.get("miss", 0))
        if (basis.get("hit", 0) + basis.get("miss", 0)) else None
    )


def _write_detail(frame: pd.DataFrame, candidate_id: str, candidate_mask: pd.Series,
                  output_dir: Path) -> None:
    out = frame.copy()
    out["selected"] = candidate_mask.reindex(out.index).fillna(False).astype(bool)
    out["symbol_for_dedup"] = out["symbol"]
    out["week_for_dedup"] = out["week"]
    out["confirmed_bjt_for_dedup"] = out["confirmed_bjt"]
    out["frozen_candidate_rule"] = candidate_id
    out["primary_non_top20_alt"] = out["primary_non_top20_alt"].astype(bool)
    out["all_altcoins"] = out["all_altcoins"].astype(bool)
    out["early_fixed_stop_2bars"] = out["early_fixed_stop_2bars"].astype("string")
    out["early_stop_before_10r"] = out["early_stop_before_10r"].astype("string")
    out["confirmation_basis_status"] = out["confirmation_basis_status"].astype(str)
    out = out.drop(columns=[c for c in out.columns if c.startswith("_") or c in {
        "confirmed_bjt_parsed", "first_gt10_bjt_parsed", "path_end_bar_bjt_parsed",
        "evaluation_cutoff_bjt_parsed", "exit_kind", "is_hit", "is_miss",
        "is_possible", "is_censored", "confirmed_outcome", "train_label_eligible",
        "major_symbol", "altcoin", "direction",
    }], errors="ignore")
    # Keep every input column and append translated operational fields. The
    # requested exact `selected`, symbol, week and confirmation fields remain.
    chinese = out.rename(columns={
        "selected": "selected",
        "symbol_for_dedup": "去重币种",
        "week_for_dedup": "去重周",
        "confirmed_bjt_for_dedup": "去重确认时间_北京时间",
        "primary_non_top20_alt": "主队列_非旧Top20山寨",
        "all_altcoins": "次队列_全部山寨",
        "early_fixed_stop_2bars": "确认后两根内首次固定止损",
        "early_stop_before_10r": "两根内止损发生前未达10R",
        "early_stop_pre10_possible": "止损与10R同K顺序不明",
        "confirmation_basis_status": "确认价至原止损口径状态",
        "frozen_candidate_rule": "冻结候选规则",
    })
    chinese.to_csv(output_dir / "筛选信号明细.csv", index=False, encoding="utf-8-sig", float_format="%.10g")


def run(input_path: Path, output_dir: Path, *, bootstraps: int = BOOTSTRAPS,
        random_draws: int = RANDOM_DRAWS) -> dict[str, Any]:
    frame, warnings = load_signals(input_path)
    thresholds = freeze_thresholds(frame)
    decision, _, masks = _decision_and_metrics(frame, thresholds,
                                                bootstraps=bootstraps,
                                                random_draws=random_draws)
    candidate_id = decision["candidate_rule_id"]
    candidate_mask = masks[candidate_id]
    long_rows: list[dict[str, Any]] = []
    cohort_specs = (("primary_non_top20_alt", "主队列_非旧Top20山寨"),
                    ("all_altcoins", "次队列_全部山寨"))
    frozen_candidate_for_main = candidate_id
    for cohort_col, cohort_name in cohort_specs:
        cohort = frame.loc[frame[cohort_col]]
        periods: list[tuple[str, pd.DataFrame, bool]] = []
        periods.append(("全期", cohort, False))
        periods.append(("开发期_<=2024且首个验证年开始前已结束", cohort.loc[cohort["train_label_eligible"]], True))
        for year in TEST_YEARS:
            periods.append((str(year), cohort.loc[cohort["year"].eq(year)], False))
        for period, base, training in periods:
            if base.empty:
                continue
            rule_ids = [frozen_candidate_for_main] if period in {"2025", "2026"} else list(thresholds)
            # All eight screens are reported in each cohort/year, while the
            # unique candidate is separately flagged as the only confirmatory
            # 2025/2026 evaluation. Other holdout rows are descriptive only.
            if period in {"2025", "2026"}:
                rule_ids = list(thresholds)
            for rule_index, rid in enumerate(rule_ids):
                mask = masks[rid].reindex(base.index).fillna(False)
                selected = base.loc[mask]
                do_inference = (rid == candidate_id and cohort_col == "primary_non_top20_alt"
                                and period in {"2025", "2026"})
                result = _evaluate_group(
                    base, selected, cohort=cohort_name, year_label=period,
                    rule_id=rid, training=training,
                    random_draws=random_draws if do_inference else 0,
                    bootstraps=bootstraps if do_inference else 0,
                    seed=SEED + 1009 + rule_index * 37 + (int(period) if period.isdigit() else 0),
                )
                _add_confirmation_basis_counts(result, selected)
                row = _summary_row(result, candidate=rid == candidate_id,
                                   candidate_admitted=bool(decision["candidate_admitted"]))
                row["队列"] = cohort_name
                row["规则名称"] = thresholds[rid]["rule"]["label"]
                row["固定阈值方向"] = thresholds[rid]["rule"]["keep"]
                row["开发阈值"] = thresholds[rid]["threshold"]
                row["是否仅描述性"] = bool(period in {"2025", "2026"} and rid != candidate_id)
                row["周聚类早止损差95区间pp"] = result.get("week_cluster_bootstrap", {}).get(
                    "early_stop_delta_vs_matched_expected", {}).get("delta_ci95_pp")
                row["周聚类命中率差95区间pp"] = result.get("week_cluster_bootstrap", {}).get(
                    "hit_rate_delta_vs_matched_expected", {}).get("delta_ci95_pp")
                long_rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_frame = pd.DataFrame(long_rows)
    metrics_frame.to_csv(output_dir / "筛选规则评估.csv", index=False, encoding="utf-8-sig", float_format="%.10g")
    rules_rows = []
    for rid, item in thresholds.items():
        rule = item["rule"]
        rules_rows.append({
            "规则ID": rid, "规则": rule["label"], "特征": rule["feature"],
            "通过方向": rule["keep"], "开发期样本数": item["training_n"],
            "训练期中位数": item["training_median"], "冻结阈值": item["threshold"],
            "阈值分位数": item["quantile"], "缺失值处理": "保留",
            "候选": rid == candidate_id,
            "候选准入": bool(decision["candidate_admitted"]) if rid == candidate_id else False,
        })
    pd.DataFrame(rules_rows).to_csv(output_dir / "冻结规则清单.csv", index=False,
                                    encoding="utf-8-sig", float_format="%.10g")
    _write_detail(frame, candidate_id, candidate_mask, output_dir)

    # Dedup-ready candidate inventory. Keep all candidate rows, including
    # unresolved paths, so the parent can calculate unique symbol/week/peak bars.
    dedup = frame.loc[candidate_mask, ["symbol", "week", "confirmed_bjt", "side", "status",
                                       "peak_time_bjt", "exit_reason"]].copy()
    dedup["selected"] = True
    dedup.to_csv(output_dir / "冻结候选入选明细.csv", index=False, encoding="utf-8-sig", float_format="%.10g")

    status_summary = {
        "input_status_counts": _jsonable_counts(frame["status"]),
        "exit_reason_counts": _jsonable_counts(frame["exit_reason"].astype("string")),
        "early_stop_two_bar_labels": _jsonable_counts(frame["early_fixed_stop_2bars"].astype("string")),
        "early_stop_pre10_labels": _jsonable_counts(frame["early_stop_before_10r"].astype("string")),
    }
    hash_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    summary = {
        "experiment": str(output_dir),
        "input": str(input_path), "input_sha256": hash_digest,
        "input_rows": int(len(frame)), "input_status": status_summary,
        "training_thresholds": thresholds,
        "frozen_decision": decision,
        "decision_gates": {
            "winner_recall_min": .70,
            "early_fixed_stop_count_reduction_min": .30,
            "early_stop_rate_must_be_below_equal_size_symbol_side_year_random": True,
            "minimum_support": {"candidate_signals": 30, "candidate_early_stop_assessable": 20,
                                "baseline_early_stops": 5, "baseline_winners": 5,
                                "candidate_week_clusters": 8},
            "fallback": "If none passes, freeze one closest diagnostic rule by the predeclared development-only ordering; do not call it admitted.",
        },
        "cohort_counts": {
            "all_altcoins": int(frame["all_altcoins"].sum()),
            "primary_non_top20_alt": int(frame["primary_non_top20_alt"].sum()),
            "major_excluded": int(frame["major_symbol"].sum()),
            "was_top20_missing": int(frame["was_top20"].isna().sum()),
            "is_current_true": int(frame["is_current"].eq(True).sum()),
            "observed_first_24h_bars_true": int(frame["observed_first_24h_bars"].eq(True).sum()),
            "observed_first_24h_bars_false_or_missing": int((~frame["observed_first_24h_bars"].eq(True)).fillna(True).sum()),
        },
        "analysis_contract": {
            "thresholds": "Feature thresholds use only confirmation years <= 2024. Continuous screens retain the training 2/3 side by using Q33 or Q67; missing features pass. Boolean BTC alignment keeps true and missing.",
            "development_labels": "Only definite hit/miss rows confirmed <= 2024 whose 12H exit bar closed by 2025-01-01 00:00 BJT (exit bar open plus 12H <= cutoff). Open, gap, unavailable, possible and censored rows are not misses and are excluded from development outcomes.",
            "early_stop": "First fixed-stop path endpoint in [confirmed time, confirmed time + 24 hours), i.e. the first two 12H bars. For non-stop/flip paths, observed_first_24h_bars=true establishes a negative; false/missing stays unassessable. If the column is absent in a fixture only, evaluation_cutoff_bjt is the compatibility fallback. Whether a same-bar 10R upper-only excursion occurred before the stop is marked indeterminate.",
            "matched_random": "Equal-size random cohorts sampled within symbol x side x year strata; summary table gives exact expected rates, and the frozen candidate also gets a without-replacement Monte Carlo comparison.",
            "week_cluster": "Bootstrap resamples the full market week as a cluster and compares the selected rate against the same-size expected rate within symbol x side strata.",
            "confirmation_basis": "Robustness reclassifies the same excursion path using confirmation_close-to-original_stop risk from confirmation_basis_lower_r/upper_r. It does not replace the touched-price reference result.",
            "no_economics": "No simulated fills, returns, fees, training, model fitting, or strategy recommendation.",
            "candidate_rows": "筛选信号明细.csv and 冻结候选入选明细.csv preserve selected, symbol, week and confirmed_bjt for deduplication of unique peak candles and stop exposure.",
        },
        "warnings": warnings,
    }
    _dump(output_dir / "summary.json", summary)
    print(json.dumps({"candidate_rule_id": candidate_id,
                      "candidate_admitted": decision["candidate_admitted"],
                      "input_rows": len(frame), "output_dir": str(output_dir),
                      "warnings": warnings}, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Frozen all-market signals.csv")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--bootstraps", type=int, default=BOOTSTRAPS)
    parser.add_argument("--random-draws", type=int, default=RANDOM_DRAWS)
    args = parser.parse_args()
    if args.bootstraps < 0 or args.random_draws < 0:
        parser.error("--bootstraps and --random-draws must be >= 0")
    run(args.input, args.output_dir, bootstraps=args.bootstraps,
        random_draws=args.random_draws)


if __name__ == "__main__":
    main()
