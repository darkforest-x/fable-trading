"""Analyze completed native Monday limit/reclaim outputs without replaying them.

The runner's per-symbol trade rows are the source for returns and exits. This
module reads those compact outcomes and the per-symbol audit status only; it
never loads candle data, rebuilds execution, or changes the runner's admission,
fee, fill, confirmation, or exit rules. Crypto returns use native net bp and
XAUUSD returns use gross bp because gold fees are unknown.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zlib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-monday-limit-reclaim-20261024-v1"
EXPECTED_ASSETS = 633
CUT = pd.Timestamp("2023-01-01T00:00:00Z")
TARGETS = [
    "near_monday_edge",
    "range_0.5",
    "range_0.618",
    "opposite_monday_edge",
    "opposite_1618",
]
CONFIRM_MINUTES = [1, 3, 5, 15, 60]
COMMON6 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
COMMON_START = pd.Timestamp("2022-11-01T00:00:00Z")
COMMON_END = pd.Timestamp("2026-10-01T00:00:00Z")
BOOTSTRAP_REPLICATES = 2000
MATRIX_FAMILY_SIZE = len(CONFIRM_MINUTES) * len(TARGETS)

TRADE_COLUMNS = [
    "trade_id", "pair_id", "symbol", "asset_group", "source_group", "week",
    "side", "execution_minutes", "reclaim_minutes", "exit_mode", "population",
    "control_id", "bucket", "entry_interval_open", "framework_exit_time",
    "reason", "closed", "basis", "gross_bp", "fee_bp", "return_bp",
    "reclaim_status", "touch_wick_only_reclaim", "next_close_inside",
    "holding_minutes_upper",
]
ORDER_COLUMNS = [
    "trade_id", "pair_id", "symbol", "asset_group", "source_group", "week",
    "side", "execution_minutes", "reclaim_minutes", "exit_mode", "population",
    "control_id", "bucket", "submission_time", "intended_entry_time",
]
MATRIX_COLUMNS = ["reclaim_minutes", "exit_mode"]
MATCH_KEY_COLUMNS = ["symbol", "actual_ny_week", "side", "bucket", "entry_period"]


def read_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    """Read one compressed per-symbol result table without touching candle data."""
    try:
        return pd.read_csv(
            path,
            usecols=usecols,
            float_precision="round_trip",
            low_memory=False,
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=usecols or [])


def _as_bool(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "1.0", "yes"})


def _ny_week(timestamps: pd.Series) -> pd.Series:
    local = pd.to_datetime(timestamps, utc=True).dt.tz_convert("America/New_York")
    monday = local.dt.normalize() - pd.to_timedelta(local.dt.dayofweek, unit="D")
    return monday.dt.strftime("%Y-%m-%d")


def prepare_trades(rows: pd.DataFrame) -> pd.DataFrame:
    """Normalize native rows and derive actual-entry time partitions."""
    if rows.empty:
        return rows.copy()
    result = rows.copy()
    for column in ("side", "execution_minutes", "reclaim_minutes", "control_id", "bucket", "reason"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in ("gross_bp", "fee_bp", "return_bp", "touch_wick_only_reclaim",
                   "next_close_inside", "holding_minutes_upper"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["raw_closed"] = _as_bool(result["closed"])
    result["entry_ts"] = pd.to_datetime(result["entry_interval_open"], utc=True, errors="coerce")
    result["exit_ts"] = pd.to_datetime(result["framework_exit_time"], utc=True, errors="coerce")
    invalid_dates = result["entry_ts"].isna() | result["exit_ts"].isna()
    if invalid_dates.any():
        bad_ids = result.loc[invalid_dates, "trade_id"].astype(str).head(5).tolist()
        raise ValueError(f"Native trade rows have invalid entry/exit timestamps: {bad_ids}")
    result["entry_period"] = np.where(result["entry_ts"] < CUT, "pre2023", "post2023")
    result["cross_cut"] = (
        result["entry_ts"].lt(CUT)
        & result["exit_ts"].ge(CUT)
    ).fillna(False)
    result["common6_in_range"] = (
        result["source_group"].eq("crypto_1m")
        & result["symbol"].isin(COMMON6)
        & result["entry_ts"].ge(COMMON_START)
        & result["entry_ts"].lt(COMMON_END)
    )
    result["actual_ny_week"] = _ny_week(result["entry_ts"])
    result["year"] = result["entry_ts"].dt.year.astype("Int64")
    # The native reason is authoritative for censoring: reason 0 is unresolved.
    result["closed"] = result["raw_closed"] & result["reason"].ne(0)
    result["censored"] = ~result["closed"]
    return result


def period_rows(rows: pd.DataFrame, period: str) -> pd.DataFrame:
    """Use actual-entry periods and drop trades spanning the split in split views."""
    if period == "all":
        return rows
    if period not in {"pre2023", "post2023"}:
        raise ValueError(f"Unknown period: {period}")
    return rows.loc[rows["entry_period"].eq(period) & ~rows["cross_cut"]]


def _longest_true(values: np.ndarray) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if bool(value) else 0
        best = max(best, current)
    return int(best)


def trade_stats(rows: pd.DataFrame, include_streak: bool = True) -> dict[str, object]:
    """Describe filled signal trades; censored rows never enter win/loss metrics."""
    if rows.empty:
        rows = rows.copy()
    signals = rows.loc[rows["population"].eq("signal")]
    closed = signals.loc[signals["closed"]].copy()
    returns = closed["return_bp"].dropna()
    gross = closed["gross_bp"].dropna()
    net_rows = closed.loc[closed["basis"].eq("net"), "return_bp"].dropna()
    wins = returns.loc[returns.gt(0)]
    losses = returns.loc[returns.lt(0)]
    mean_win = float(wins.mean()) if len(wins) else np.nan
    mean_loss = float(losses.mean()) if len(losses) else np.nan
    loss_sum = float(losses.sum()) if len(losses) else 0.0
    gross_losses = gross.loc[gross.lt(0)]
    gross_wins = gross.loc[gross.gt(0)]
    holding = closed["holding_minutes_upper"].dropna()
    reasons = signals["reason"]
    status = signals["reclaim_status"].astype(str).str.lower()
    return_bases = sorted(signals["basis"].dropna().astype(str).unique())

    result: dict[str, object] = {
        "filled_n": int(len(signals)),
        "closed_n": int(len(closed)),
        "censored_n": int(signals["censored"].sum()),
        "win_n": int(len(wins)),
        "loss_n": int(len(losses)),
        "flat_n": int(returns.eq(0).sum()),
        "gross_win_rate": float(gross.gt(0).mean()) if len(gross) else np.nan,
        "net_win_rate": float(net_rows.gt(0).mean()) if len(net_rows) else np.nan,
        "return_win_rate": float(returns.gt(0).mean()) if len(returns) else np.nan,
        "mean_gross_bp": float(gross.mean()) if len(gross) else np.nan,
        "median_gross_bp": float(gross.median()) if len(gross) else np.nan,
        "gross_mean_win_bp": float(gross_wins.mean()) if len(gross_wins) else np.nan,
        "gross_mean_loss_bp": float(gross_losses.mean()) if len(gross_losses) else np.nan,
        "mean_fee_bp": float(closed["fee_bp"].dropna().mean()) if closed["fee_bp"].notna().any() else np.nan,
        "mean_return_bp": float(returns.mean()) if len(returns) else np.nan,
        "median_return_bp": float(returns.median()) if len(returns) else np.nan,
        "mean_win_bp": mean_win,
        "mean_loss_bp": mean_loss,
        "payoff_ratio": mean_win / abs(mean_loss) if len(wins) and len(losses) and mean_loss else np.nan,
        "profit_factor": (
            float(wins.sum() / abs(loss_sum)) if loss_sum < 0 and len(wins)
            else 0.0 if loss_sum < 0
            else np.nan
        ),
        "net_mean_bp": float(net_rows.mean()) if len(net_rows) else np.nan,
        "net_median_bp": float(net_rows.median()) if len(net_rows) else np.nan,
        "tp_n": int(reasons.eq(4).sum()),
        "tp_before_confirmation_n": int((reasons.eq(4) & status.eq("pending")).sum()),
        "entry_stop_n": int(reasons.eq(5).sum()),
        "failed_reclaim_n": int(reasons.eq(11).sum()),
        "worst_return_bp": float(returns.min()) if len(returns) else np.nan,
        "worst_gross_bp": float(gross.min()) if len(gross) else np.nan,
        "mean_holding_minutes": float(holding.mean()) if len(holding) else np.nan,
        "median_holding_minutes": float(holding.median()) if len(holding) else np.nan,
        "p90_holding_minutes": float(holding.quantile(0.9)) if len(holding) else np.nan,
        "max_holding_minutes": float(holding.max()) if len(holding) else np.nan,
        "return_basis": "/".join(return_bases) if return_bases else "unknown",
    }
    if include_streak:
        ordered = closed.dropna(subset=["return_bp"]).sort_values(
            ["exit_ts", "symbol", "trade_id"], kind="stable"
        )
        result["longest_loss_streak"] = _longest_true(ordered["return_bp"].lt(0).to_numpy())
        per_symbol = [
            _longest_true(part.sort_values(["exit_ts", "trade_id"], kind="stable")["return_bp"].lt(0).to_numpy())
            for _, part in ordered.groupby("symbol", sort=True)
        ]
        result["longest_single_symbol_loss_streak"] = max(per_symbol, default=0)
        result["cross_symbol_streak_is_descriptive"] = True
    return result


def _complete_pair_rows(
    rows: pd.DataFrame,
    group_columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Vectorize the signal plus three distinct, same-key control selection."""
    signals = rows.loc[rows["population"].eq("signal")]
    controls = rows.loc[rows["population"].eq("random")]
    if signals.empty or controls.empty:
        return signals.iloc[0:0].copy(), controls.iloc[0:0].copy()

    signal_counts = signals.groupby(group_columns, dropna=False, sort=False).size().rename("signal_n")
    control_stats = controls.groupby(group_columns, dropna=False, sort=False).agg(
        control_n=("trade_id", "size"),
        control_id_n=("control_id", "nunique"),
        control_id_count=("control_id", "count"),
        control_id_min=("control_id", "min"),
        control_id_max=("control_id", "max"),
        control_id_sum=("control_id", "sum"),
    )
    candidates = signal_counts.to_frame().join(control_stats, how="inner").reset_index()
    candidates = candidates.loc[
        candidates["signal_n"].eq(1)
        & candidates["control_n"].eq(3)
        & candidates["control_id_n"].eq(3)
        & candidates["control_id_count"].eq(3)
        & candidates["control_id_min"].eq(1)
        & candidates["control_id_max"].eq(3)
        & candidates["control_id_sum"].eq(6)
    ]
    if candidates.empty:
        return signals.iloc[0:0].copy(), controls.iloc[0:0].copy()

    candidate_signals = signals.merge(
        candidates[group_columns], on=group_columns, how="inner", validate="one_to_one"
    )
    candidate_controls = controls.merge(
        candidates[group_columns], on=group_columns, how="inner", validate="many_to_one"
    )
    exact_key = list(dict.fromkeys([*group_columns, *MATCH_KEY_COLUMNS]))
    matched_controls = candidate_controls.merge(
        candidate_signals[exact_key],
        on=exact_key,
        how="inner",
        validate="many_to_one",
    )
    matched_counts = matched_controls.groupby(group_columns, dropna=False, sort=False).size()
    complete_keys = matched_counts.loc[matched_counts.eq(3)].reset_index()[group_columns]
    if complete_keys.empty:
        return signals.iloc[0:0].copy(), controls.iloc[0:0].copy()

    paired_signals = candidate_signals.merge(
        complete_keys, on=group_columns, how="inner", validate="one_to_one"
    )
    paired_controls = matched_controls.merge(
        complete_keys, on=group_columns, how="inner", validate="many_to_one"
    )
    return paired_signals, paired_controls


def build_strict_matches(trades: pd.DataFrame) -> pd.DataFrame:
    """Keep only signal plus exactly three same-week, same-bucket closed controls."""
    columns = [
        "pair_id", "symbol", "asset_group", "source_group", "execution_minutes",
        "reclaim_minutes", "exit_mode", "actual_ny_week", "side", "bucket",
        "entry_period", "cross_cut", "entry_ts", "signal_return_bp",
        "control_1_return_bp", "control_2_return_bp", "control_3_return_bp",
        "control_mean_return_bp", "delta_return_bp",
        "common6_in_range",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    key = ["symbol", "execution_minutes", "reclaim_minutes", "exit_mode", "pair_id"]
    signal_rows, control_rows = _complete_pair_rows(trades, key)
    signal_rows = signal_rows.loc[signal_rows["closed"] & signal_rows["return_bp"].notna()]
    control_rows = control_rows.loc[control_rows["closed"] & control_rows["return_bp"].notna()]
    if signal_rows.empty or control_rows.empty:
        return pd.DataFrame(columns=columns)

    closed_counts = control_rows.groupby(key, dropna=False, sort=False).size()
    complete_keys = closed_counts.loc[closed_counts.eq(3)].reset_index()[key]
    signal_rows = signal_rows.merge(complete_keys, on=key, how="inner", validate="one_to_one")
    control_rows = control_rows.merge(complete_keys, on=key, how="inner", validate="many_to_one")
    if signal_rows.empty:
        return pd.DataFrame(columns=columns)

    control_wide = control_rows.pivot(index=key, columns="control_id", values="return_bp").reset_index()
    control_wide = control_wide.rename(columns={
        1: "control_1_return_bp", 2: "control_2_return_bp", 3: "control_3_return_bp"
    })
    control_flags = control_rows.groupby(key, dropna=False, sort=False).agg(
        control_cross_cut=("cross_cut", "any"),
        control_common6_in_range=("common6_in_range", "all"),
    ).reset_index()
    matched = signal_rows.merge(control_wide, on=key, how="inner", validate="one_to_one")
    matched = matched.merge(control_flags, on=key, how="inner", validate="one_to_one")
    control_columns = ["control_1_return_bp", "control_2_return_bp", "control_3_return_bp"]
    matched["control_mean_return_bp"] = matched[control_columns].mean(axis=1)
    matched["delta_return_bp"] = matched["return_bp"] - matched["control_mean_return_bp"]
    matched["cross_cut"] = matched["cross_cut"] | matched["control_cross_cut"]
    matched["common6_in_range"] = matched["common6_in_range"] & matched["control_common6_in_range"]
    output = matched.rename(columns={"return_bp": "signal_return_bp"})[
        [
            "pair_id", "symbol", "asset_group", "source_group", "execution_minutes",
            "reclaim_minutes", "exit_mode", "actual_ny_week", "side", "bucket",
            "entry_period", "cross_cut", "entry_ts", "signal_return_bp", *control_columns,
            "control_mean_return_bp", "delta_return_bp", "common6_in_range",
        ]
    ].copy()
    for column in ("execution_minutes", "reclaim_minutes", "side", "bucket"):
        output[column] = pd.to_numeric(output[column], errors="raise").astype(int)
    return output


def _cluster_inference(matches: pd.DataFrame, seed: int) -> dict[str, object]:
    if matches.empty:
        return {
            "independent_market_weeks": 0,
            "paired_ci_low_bp": np.nan,
            "paired_ci_high_bp": np.nan,
            "signflip_p": np.nan,
        }
    blocks = matches.groupby("actual_ny_week")["delta_return_bp"].agg(["sum", "count"])
    n_blocks = len(blocks)
    if n_blocks < 2:
        return {
            "independent_market_weeks": n_blocks,
            "paired_ci_low_bp": np.nan,
            "paired_ci_high_bp": np.nan,
            "signflip_p": np.nan,
        }
    sums = blocks["sum"].to_numpy(dtype=float)
    counts = blocks["count"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(BOOTSTRAP_REPLICATES, dtype=float)
    null = np.empty(BOOTSTRAP_REPLICATES, dtype=float)
    denominator = float(counts.sum())
    actual = float(sums.sum() / denominator)
    for index in range(BOOTSTRAP_REPLICATES):
        picked = rng.integers(0, n_blocks, size=n_blocks)
        bootstrap[index] = sums[picked].sum() / counts[picked].sum()
        signs = rng.choice(np.array([-1.0, 1.0]), size=n_blocks)
        null[index] = float((sums * signs).sum() / denominator)
    low, high = np.quantile(bootstrap, [0.025, 0.975])
    p_value = (1 + int((np.abs(null) >= abs(actual)).sum())) / (BOOTSTRAP_REPLICATES + 1)
    return {
        "independent_market_weeks": n_blocks,
        "paired_ci_low_bp": float(low),
        "paired_ci_high_bp": float(high),
        "signflip_p": float(p_value),
    }


def holm_fixed_matrix(metrics: pd.DataFrame) -> pd.DataFrame:
    """Apply Holm over the predeclared 5×5 timeframe/target family per scope-period."""
    if metrics.empty:
        result = metrics.copy()
        result["holm_p"] = pd.Series(dtype=float)
        result["holm_family_size"] = pd.Series(dtype=int)
        return result
    result = metrics.copy()
    result["holm_p"] = np.nan
    result["holm_family_size"] = MATRIX_FAMILY_SIZE
    for _, indices in result.groupby(["analysis_scope", "period"], sort=False).groups.items():
        p_values = result.loc[indices, "signflip_p"].dropna().sort_values(kind="stable")
        running = 0.0
        for rank, (index, value) in enumerate(p_values.items(), start=1):
            running = max(running, min(1.0, float(value) * (MATRIX_FAMILY_SIZE - rank + 1)))
            result.loc[index, "holm_p"] = running
    return result


def _common_scope_name(catalog: list[dict[str, object]]) -> str:
    available = {
        str(item["symbol"]) for item in catalog
        if item.get("execution_minutes") == 1
    }
    return "COMMON6_CRYPTO" if set(COMMON6).issubset(available) else "COMMON6_CRYPTO_PARTIAL"


def _scope_frames(rows: pd.DataFrame, common_scope_name: str) -> list[tuple[str, pd.DataFrame]]:
    scopes: list[tuple[str, pd.DataFrame]] = [
        ("CRYPTO_ALL", rows.loc[rows["asset_group"].ne("XAUUSD")])
    ]
    for name in ("BTC", "ETH", "XAUUSD", "ALTCOINS"):
        scopes.append((name, rows.loc[rows["asset_group"].eq(name)]))
    common = rows.loc[
        rows["symbol"].isin(COMMON6)
        & rows["source_group"].eq("crypto_1m")
        & rows["entry_ts"].ge(COMMON_START)
        & rows["entry_ts"].lt(COMMON_END)
    ]
    scopes.append((common_scope_name, common))
    return scopes


def _scope_coverage(scope: str, confirm_minutes: int,
                    catalog: list[dict[str, object]]) -> tuple[int, int, bool]:
    available = {str(item["symbol"]) for item in catalog}
    one_minute_available = {
        str(item["symbol"]) for item in catalog
        if item.get("execution_minutes") == 1
    }
    if scope.startswith("COMMON6_CRYPTO"):
        expected_symbols = set(COMMON6)
    elif scope == "CRYPTO_ALL" and confirm_minutes in (1, 3):
        expected_symbols = set(COMMON6)
    elif scope == "ALTCOINS":
        expected_symbols = set(COMMON6[2:]) if confirm_minutes in (1, 3) else None
    elif scope in {"BTC", "ETH", "XAUUSD"}:
        expected_symbols = {scope + "USDT" if scope in {"BTC", "ETH"} else "XAUUSD"}
    else:
        expected_symbols = None

    if expected_symbols is not None:
        expected = len(expected_symbols)
        source_available = one_minute_available if confirm_minutes in (1, 3) or scope.startswith("COMMON6_CRYPTO") else available
        present = len(source_available & expected_symbols)
    elif scope == "CRYPTO_ALL":
        expected = EXPECTED_ASSETS - 1
        present = len(available - {"XAUUSD"})
    elif scope == "ALTCOINS":
        expected = EXPECTED_ASSETS - 3
        present = len(available - {"BTCUSDT", "ETHUSDT", "XAUUSD"})
    else:
        expected = 0
        present = 0
    return present, expected, present == expected


def _source_label(rows: pd.DataFrame) -> str:
    values = sorted(pd.to_numeric(rows["execution_minutes"], errors="coerce").dropna().astype(int).unique())
    return str(values[0]) if len(values) == 1 else "mixed" if values else "unknown"


def _cell_stats(
    rows: pd.DataFrame,
    scope: str,
    period: str,
    catalog: list[dict[str, object]],
    include_streak: bool = True,
) -> list[dict[str, object]]:
    selected = period_rows(rows, period)
    if selected.empty:
        return []
    output = []
    for keys, group in selected.groupby(MATRIX_COLUMNS, sort=True, dropna=False):
        reclaim_minutes, exit_mode = keys
        present, expected, complete = _scope_coverage(scope, int(reclaim_minutes), catalog)
        output.append({
            "analysis_scope": scope,
            "period": period,
            "source_minutes": _source_label(group),
            "confirm_minutes": int(reclaim_minutes),
            "target": exit_mode,
            "assets_present_n": present,
            "assets_expected_n": expected,
            "scope_complete": complete,
            **trade_stats(group, include_streak=include_streak),
        })
    return output


def _matched_metrics(matches: pd.DataFrame, scope: str, period: str) -> list[dict[str, object]]:
    selected = matches
    if selected.empty:
        return []
    if period != "all":
        selected = selected.loc[selected["entry_period"].eq(period) & ~selected["cross_cut"]]
    if selected.empty:
        return []
    output = []
    matrix = ["reclaim_minutes", "exit_mode"]
    for keys, group in selected.groupby(matrix, sort=True, dropna=False):
        reclaim_minutes, exit_mode = keys
        seed_text = f"{scope}|{period}|{reclaim_minutes}|{exit_mode}"
        infer = _cluster_inference(group, zlib.crc32(seed_text.encode("utf-8")) + 20261024)
        output.append({
            "analysis_scope": scope,
            "period": period,
            "confirm_minutes": int(reclaim_minutes),
            "target": exit_mode,
            "strict_match_n": int(len(group)),
            "matched_signal_mean_bp": float(group["signal_return_bp"].mean()),
            "matched_control_mean_bp": float(group["control_mean_return_bp"].mean()),
            "matched_delta_bp": float(group["delta_return_bp"].mean()),
            **infer,
        })
    return output


def _slice_metrics(rows: pd.DataFrame, common_scope_name: str) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    dimensions = ("year", "side", "symbol")
    for scope, scoped in _scope_frames(rows, common_scope_name):
        for period in ("all", "pre2023", "post2023"):
            selected = period_rows(scoped, period)
            for dimension in dimensions:
                if selected.empty:
                    continue
                group_columns = [dimension, *MATRIX_COLUMNS]
                for keys, group in selected.groupby(group_columns, sort=True, dropna=False):
                    value, reclaim_minutes, target = keys
                    records.append({
                        "analysis_scope": scope,
                        "period": period,
                        "slice_dimension": dimension,
                        "slice_value": str(value),
                        "source_minutes": _source_label(group),
                        "confirm_minutes": int(reclaim_minutes),
                        "target": target,
                        **trade_stats(group, include_streak=False),
                    })
    return pd.DataFrame.from_records(records)


def _condition_metrics(rows: pd.DataFrame, common_scope_name: str) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    conditions = (
        ("touch_wick_only_reclaim", "touch_wick_only_reclaim"),
        ("next_close_inside", "next_close_inside"),
    )
    for scope, scoped in _scope_frames(rows, common_scope_name):
        for period in ("all", "pre2023", "post2023"):
            selected = period_rows(scoped, period)
            for condition, column in conditions:
                conditioned = selected.loc[selected[column].eq(1)]
                if conditioned.empty:
                    continue
                for keys, group in conditioned.groupby(MATRIX_COLUMNS, sort=True, dropna=False):
                    confirm_minutes, target = keys
                    records.append({
                        "analysis_scope": scope,
                        "period": period,
                        "condition": condition,
                        "source_minutes": _source_label(group),
                        "confirm_minutes": int(confirm_minutes),
                        "target": target,
                        "interpretation": "posthoc_description_only",
                        **trade_stats(group, include_streak=False),
                    })
    return pd.DataFrame.from_records(records)


def prepare_orders(orders: pd.DataFrame, audit_opportunities: list[dict[str, object]],
                   trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return compact attempt counts and complete attempt-pair outcomes for one symbol."""
    if orders.empty:
        return pd.DataFrame(), pd.DataFrame()
    result = orders.copy()
    for column in ("side", "execution_minutes", "reclaim_minutes", "control_id", "bucket"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    status_by_id = {str(row["trade_id"]): str(row.get("status", "unknown"))
                    for row in audit_opportunities if "trade_id" in row}
    result["attempt_status"] = result["trade_id"].astype(str).map(status_by_id).fillna("unknown")
    trade_by_id = trades.set_index("trade_id", drop=False) if not trades.empty else pd.DataFrame()
    if not trades.empty:
        for field in ("entry_ts", "exit_ts", "return_bp", "closed", "cross_cut"):
            result[field] = result["trade_id"].map(trade_by_id[field])
    else:
        result["entry_ts"] = pd.NaT
        result["exit_ts"] = pd.NaT
        result["return_bp"] = np.nan
        result["closed"] = False
        result["cross_cut"] = False
    intended = pd.to_datetime(result["intended_entry_time"], utc=True, errors="coerce")
    invalid_intended = intended.isna()
    if invalid_intended.any():
        bad_ids = result.loc[invalid_intended, "trade_id"].astype(str).head(5).tolist()
        raise ValueError(f"Native order rows have invalid intended-entry timestamps: {bad_ids}")
    result["attempt_ts"] = result["entry_ts"].where(result["attempt_status"].eq("filled"), intended)
    invalid_attempt = result["attempt_ts"].isna()
    if invalid_attempt.any():
        bad_ids = result.loc[invalid_attempt, "trade_id"].astype(str).head(5).tolist()
        raise ValueError(f"Filled order rows have no actual-entry timestamp: {bad_ids}")
    result["entry_period"] = np.where(result["attempt_ts"] < CUT, "pre2023", "post2023")
    result["actual_ny_week"] = _ny_week(result["attempt_ts"])
    result["cross_cut"] = result["cross_cut"].astype("boolean").fillna(False).astype(bool)
    result["common6_in_range"] = (
        result["source_group"].eq("crypto_1m")
        & result["symbol"].isin(COMMON6)
        & result["attempt_ts"].ge(COMMON_START)
        & result["attempt_ts"].lt(COMMON_END)
    )
    result["closed"] = result["closed"].astype("boolean").fillna(False).astype(bool)
    filled_closed = result["attempt_status"].eq("filled") & result["closed"]
    expired = result["attempt_status"].eq("expired")
    result["attempt_return_bp"] = np.nan
    result.loc[filled_closed, "attempt_return_bp"] = pd.to_numeric(
        result.loc[filled_closed, "return_bp"], errors="coerce"
    )
    result.loc[expired, "attempt_return_bp"] = 0.0
    result["role"] = np.where(result["population"].eq("signal"), "signal", "control")
    count_columns = [
        "symbol", "asset_group", "source_group", "execution_minutes", "reclaim_minutes",
        "exit_mode", "entry_period", "role", "attempt_status", "cross_cut", "common6_in_range",
    ]
    counts = result.groupby(count_columns, dropna=False).size().rename("attempt_n").reset_index()

    pair_key = ["symbol", "execution_minutes", "reclaim_minutes", "exit_mode", "pair_id"]
    signal_rows, control_rows = _complete_pair_rows(result, pair_key)
    if signal_rows.empty or control_rows.empty:
        return counts, pd.DataFrame()

    control_rows = control_rows.assign(
        _expired=control_rows["attempt_status"].eq("expired").astype(int),
        _unknown_censored=(
            control_rows["attempt_status"].eq("filled") & ~control_rows["closed"]
        ).astype(int),
    )
    control_aggregates = control_rows.groupby(pair_key, dropna=False, sort=False).agg(
        control_attempt_mean_bp=("attempt_return_bp", "mean"),
        control_known_n=("attempt_return_bp", "count"),
        control_expired_n=("_expired", "sum"),
        control_unknown_censored_n=("_unknown_censored", "sum"),
        control_cross_cut=("cross_cut", "any"),
        control_common6_in_range=("common6_in_range", "all"),
    ).reset_index()
    paired = signal_rows.merge(
        control_aggregates, on=pair_key, how="inner", validate="one_to_one"
    )
    paired["known"] = paired["attempt_return_bp"].notna() & paired["control_known_n"].eq(3)
    paired["control_attempt_mean_bp"] = paired["control_attempt_mean_bp"].where(paired["known"])
    paired["unknown_censored_n"] = paired["control_unknown_censored_n"] + (
        paired["attempt_status"].eq("filled") & ~paired["closed"]
    ).astype(int)
    paired["attempt_delta_bp"] = (
        paired["attempt_return_bp"] - paired["control_attempt_mean_bp"]
    ).where(paired["known"])
    paired["cross_cut"] = paired["cross_cut"] | paired["control_cross_cut"]
    paired["common6_in_range"] = paired["common6_in_range"] & paired["control_common6_in_range"]
    paired = paired.rename(columns={"attempt_return_bp": "signal_attempt_return_bp"})
    paired["signal_status"] = paired["attempt_status"].astype(str)
    for column in ("execution_minutes", "reclaim_minutes", "side", "bucket"):
        paired[column] = pd.to_numeric(paired[column], errors="raise").astype(int)
    return counts, paired[
        [
            "pair_id", "symbol", "asset_group", "source_group", "execution_minutes",
            "reclaim_minutes", "exit_mode", "actual_ny_week", "side", "bucket",
            "entry_period", "cross_cut", "common6_in_range", "known", "unknown_censored_n",
            "signal_attempt_return_bp", "control_attempt_mean_bp", "attempt_delta_bp",
            "signal_status", "control_expired_n",
        ]
    ].copy()


def _attempt_metrics(counts: pd.DataFrame, pairs: pd.DataFrame,
                     common_scope_name: str) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    if counts.empty:
        return pd.DataFrame()
    scopes = [("CRYPTO_ALL", counts.loc[counts["asset_group"].ne("XAUUSD")])]
    for name in ("BTC", "ETH", "XAUUSD", "ALTCOINS"):
        scopes.append((name, counts.loc[counts["asset_group"].eq(name)]))
    scopes.append((common_scope_name, counts.loc[counts["common6_in_range"]]))
    for scope, scoped_counts in scopes:
        scoped_pairs = pairs
        if not pairs.empty:
            if scope == "CRYPTO_ALL":
                scoped_pairs = pairs.loc[pairs["asset_group"].ne("XAUUSD")]
            elif scope in {"BTC", "ETH", "XAUUSD", "ALTCOINS"}:
                scoped_pairs = pairs.loc[pairs["asset_group"].eq(scope)]
            elif scope.startswith("COMMON6_CRYPTO"):
                scoped_pairs = pairs.loc[pairs["common6_in_range"]]
        for period in ("all", "pre2023", "post2023"):
            selected_counts = scoped_counts
            if period != "all":
                selected_counts = selected_counts.loc[
                    selected_counts["entry_period"].eq(period) & ~selected_counts["cross_cut"]
                ]
            selected_pairs = scoped_pairs
            if period != "all" and not selected_pairs.empty:
                selected_pairs = selected_pairs.loc[
                    selected_pairs["entry_period"].eq(period) & ~selected_pairs["cross_cut"]
                ]
            if selected_counts.empty and selected_pairs.empty:
                continue
            count_group = ["reclaim_minutes", "exit_mode", "role", "attempt_status"]
            count_table = selected_counts.groupby(count_group, dropna=False)["attempt_n"].sum() if not selected_counts.empty else pd.Series(dtype=int)
            matrix_values = set()
            if not selected_counts.empty:
                matrix_values.update(selected_counts[["reclaim_minutes", "exit_mode"]]
                                     .drop_duplicates().itertuples(index=False, name=None))
            if not selected_pairs.empty:
                matrix_values.update(selected_pairs[["reclaim_minutes", "exit_mode"]]
                                     .drop_duplicates().itertuples(index=False, name=None))
            for confirm_minutes, target in sorted(matrix_values):
                cell = selected_pairs.loc[
                    selected_pairs["reclaim_minutes"].eq(confirm_minutes)
                    & selected_pairs["exit_mode"].eq(target)
                ] if not selected_pairs.empty else pd.DataFrame()
                source_rows = selected_counts.loc[
                    selected_counts["reclaim_minutes"].eq(confirm_minutes)
                    & selected_counts["exit_mode"].eq(target)
                ] if not selected_counts.empty else pd.DataFrame()
                if source_rows.empty and not cell.empty:
                    source_rows = cell.rename(columns={"execution_minutes": "execution_minutes"})
                known = cell.loc[cell["known"]] if not cell.empty else pd.DataFrame()
                unknown = cell.loc[~cell["known"]] if not cell.empty else pd.DataFrame()
                row: dict[str, object] = {
                    "analysis_scope": scope,
                    "period": period,
                    "source_minutes": _source_label(source_rows) if not source_rows.empty else "unknown",
                    "confirm_minutes": int(confirm_minutes),
                    "target": target,
                    "attempt_known_pair_n": int(len(known)),
                    "attempt_unknown_pair_n": int(len(unknown)),
                    "attempt_unknown_censored_n": int(unknown["unknown_censored_n"].sum()) if len(unknown) else 0,
                    "attempt_signal_mean_bp": float(known["signal_attempt_return_bp"].mean()) if len(known) else np.nan,
                    "attempt_control_mean_bp": float(known["control_attempt_mean_bp"].mean()) if len(known) else np.nan,
                    "attempt_delta_bp": float(known["attempt_delta_bp"].mean()) if len(known) else np.nan,
                    "no_fill_exposure_bp": 0.0,
                    "no_fill_counts_as_win_or_loss": False,
                    "filled_censor_is_unknown": True,
                    "return_basis": "gross" if scope == "XAUUSD" else "net",
                    "attempt_expectancy_is_complete_matched_subset": True,
                }
                for role in ("signal", "control"):
                    for status in ("filled", "expired", "unknown", "cancelled"):
                        try:
                            value = count_table.loc[(confirm_minutes, target, role, status)]
                        except KeyError:
                            value = 0
                        row[f"{role}_{status}_attempt_n"] = int(value)
                    known_status = row[f"{role}_filled_attempt_n"] + row[f"{role}_expired_attempt_n"]
                    row[f"{role}_fill_rate"] = (
                        row[f"{role}_filled_attempt_n"] / known_status if known_status else np.nan
                    )
                signal_total = int(source_rows.loc[
                    source_rows["role"].eq("signal"), "attempt_n"
                ].sum()) if len(source_rows) else 0
                row["signal_attempt_n"] = signal_total
                row["unpaired_signal_attempt_n"] = signal_total - len(cell)
                if row["unpaired_signal_attempt_n"] < 0:
                    raise ValueError("Matched attempt sets exceed signal attempts")
                records.append(row)
    return pd.DataFrame.from_records(records)


def _scope_frames_for_pairs(matches: pd.DataFrame,
                            common_scope_name: str) -> list[tuple[str, pd.DataFrame]]:
    if matches.empty:
        return [(scope, matches) for scope in ("CRYPTO_ALL", "BTC", "ETH", "XAUUSD", "ALTCOINS", common_scope_name)]
    output = [("CRYPTO_ALL", matches.loc[matches["asset_group"].ne("XAUUSD")])]
    for scope in ("BTC", "ETH", "XAUUSD", "ALTCOINS"):
        output.append((scope, matches.loc[matches["asset_group"].eq(scope)]))
    common = matches.loc[matches["common6_in_range"]]
    output.append((common_scope_name, common))
    return output


def _metrics_tables(signals: pd.DataFrame, matches: pd.DataFrame,
                    attempt_counts: pd.DataFrame, attempt_pairs: pd.DataFrame,
                    catalog: list[dict[str, object]], common_scope_name: str
                    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metric_records: list[dict[str, object]] = []
    match_records: list[dict[str, object]] = []
    for scope, scoped_signals in _scope_frames(signals, common_scope_name):
        for period in ("all", "pre2023", "post2023"):
            metric_records.extend(_cell_stats(scoped_signals, scope, period, catalog))
    metrics = pd.DataFrame.from_records(metric_records)

    for scope, scoped_matches in _scope_frames_for_pairs(matches, common_scope_name):
        for period in ("all", "pre2023", "post2023"):
            match_records.extend(_matched_metrics(scoped_matches, scope, period))
    matched_metrics = pd.DataFrame.from_records(match_records)
    if not matched_metrics.empty:
        metrics = metrics.merge(
            matched_metrics,
            on=["analysis_scope", "period", "confirm_minutes", "target"],
            how="left",
        )
    metrics = holm_fixed_matrix(metrics)
    slices = _slice_metrics(signals, common_scope_name)
    conditions = _condition_metrics(signals, common_scope_name)
    attempts = _attempt_metrics(attempt_counts, attempt_pairs, common_scope_name)
    return metrics, slices, conditions, attempts, matched_metrics


def _compact_signals(trades: pd.DataFrame) -> pd.DataFrame:
    """Retain only signal outcomes needed by aggregate tables and loss streaks."""
    signal = trades.loc[trades["population"].eq("signal")]
    keep = [
        "trade_id", "symbol", "asset_group", "source_group", "population", "side", "execution_minutes",
        "reclaim_minutes", "exit_mode", "bucket", "entry_ts", "exit_ts", "entry_period",
        "cross_cut", "actual_ny_week", "year", "reason", "closed", "censored", "basis",
        "gross_bp", "fee_bp", "return_bp", "reclaim_status", "touch_wick_only_reclaim",
        "next_close_inside", "holding_minutes_upper",
    ]
    return signal[keep].copy()


def analyze(completed: list[Path], output: Path, partial: bool) -> dict[str, object]:
    """Process result symbols one at a time and write compact analysis artifacts."""
    signal_parts: list[pd.DataFrame] = []
    match_parts: list[pd.DataFrame] = []
    attempt_count_parts: list[pd.DataFrame] = []
    attempt_pair_parts: list[pd.DataFrame] = []
    done_summaries: list[dict[str, object]] = []

    for done_path in completed:
        symbol_dir = done_path.parent
        done = json.loads(done_path.read_text(encoding="utf-8"))
        symbol = str(done["symbol"])
        trades_raw = read_csv(symbol_dir / "trades.csv.gz", TRADE_COLUMNS)
        trades = prepare_trades(trades_raw)
        if len(trades):
            signal_parts.append(_compact_signals(trades))
            match_parts.append(build_strict_matches(trades))
        audit = json.loads((symbol_dir / "audit.json").read_text(encoding="utf-8"))
        orders_raw = read_csv(symbol_dir / "orders.csv.gz", ORDER_COLUMNS)
        counts, attempt_pairs = prepare_orders(orders_raw, audit.get("opportunities", []), trades)
        if len(counts):
            attempt_count_parts.append(counts)
        if len(attempt_pairs):
            attempt_pair_parts.append(attempt_pairs)
        done_summaries.append({
            "symbol": symbol,
            "execution_minutes": done.get("execution_minutes"),
            "trade_rows": int(len(trades)),
            "signal_rows": int(trades["population"].eq("signal").sum()) if len(trades) else 0,
            "source_first": done.get("first"),
            "source_last": done.get("last"),
        })

    signals = pd.concat(signal_parts, ignore_index=True) if signal_parts else pd.DataFrame()
    matches = pd.concat(match_parts, ignore_index=True) if match_parts else pd.DataFrame()
    attempt_counts = pd.concat(attempt_count_parts, ignore_index=True) if attempt_count_parts else pd.DataFrame()
    attempt_pairs = pd.concat(attempt_pair_parts, ignore_index=True) if attempt_pair_parts else pd.DataFrame()
    common_scope_name = _common_scope_name(done_summaries)
    if not signals.empty:
        signals["symbol"] = signals["symbol"].astype("category")
        signals["asset_group"] = signals["asset_group"].astype("category")
        signals["exit_mode"] = signals["exit_mode"].astype("category")
        signals["reclaim_status"] = signals["reclaim_status"].astype("category")

    metrics, slices, conditions, attempts, _ = _metrics_tables(
        signals, matches, attempt_counts, attempt_pairs, done_summaries, common_scope_name
    )
    output.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output / "metrics.csv", index=False)
    slices.to_csv(output / "year_side_coin.csv", index=False)
    conditions.to_csv(output / "conditions.csv", index=False)
    attempts.to_csv(output / "attempts.csv", index=False)
    matches.to_csv(output / "matches.csv.gz", index=False, compression="gzip")
    _write_chinese_metrics(metrics, output / "core_metrics_zh.csv")

    common_present = sorted(
        str(item["symbol"]) for item in done_summaries
        if item.get("execution_minutes") == 1 and str(item["symbol"]) in COMMON6
    )
    common_missing = sorted(set(COMMON6) - set(common_present))
    summary = {
        "experiment_id": EXP.name,
        "completion_status": "partial" if partial else "complete",
        "partial_requested": bool(partial),
        "assets_done": len(completed),
        "assets_expected": EXPECTED_ASSETS,
        "assets_missing": EXPECTED_ASSETS - len(completed),
        "trade_rows": int(sum(item["trade_rows"] for item in done_summaries)),
        "signal_rows": int(sum(item["signal_rows"] for item in done_summaries)),
        "strict_match_pairs": int(len(matches)),
        "source_groups": [
            {
                "source_minutes": int(minutes),
                "symbols_done": int(sum(item["execution_minutes"] == minutes for item in done_summaries)),
                "trade_rows": int(sum(item["trade_rows"] for item in done_summaries
                                       if item["execution_minutes"] == minutes)),
                "signal_rows": int(sum(item["signal_rows"] for item in done_summaries
                                        if item["execution_minutes"] == minutes)),
            }
            for minutes in sorted({int(item["execution_minutes"]) for item in done_summaries})
        ],
        "altcoin_coverage": {
            "confirm_minutes_1_and_3_expected": 4,
            "confirm_minutes_1_and_3_present": int(sum(
                item["symbol"] in COMMON6[2:] and item.get("execution_minutes") == 1
                for item in done_summaries
            )),
            "confirm_minutes_5_15_and_60_expected": EXPECTED_ASSETS - 3,
            "confirm_minutes_5_15_and_60_present": int(sum(
                item["symbol"] not in {"BTCUSDT", "ETHUSDT", "XAUUSD"}
                for item in done_summaries
            )),
        },
        "analysis_outputs": [
            "metrics.csv", "year_side_coin.csv", "conditions.csv", "attempts.csv",
            "matches.csv.gz", "core_metrics_zh.csv",
        ],
        "split_cut_utc": CUT.isoformat(),
        "split_policy": "entry timestamp; exclude holdings crossing the cut from pre/post rows; all retains them",
        "common6_scope": {
            "analysis_scope": common_scope_name,
            "symbols": COMMON6,
            "expected_symbols": COMMON6,
            "expected_symbol_count": len(COMMON6),
            "present_symbols": common_present,
            "present_symbol_count": len(common_present),
            "missing_symbols": common_missing,
            "complete": not common_missing,
            "source_group": "crypto_1m",
            "event_start_utc_inclusive": COMMON_START.isoformat(),
            "event_end_utc_exclusive": COMMON_END.isoformat(),
        },
        "matched_control_rule": "same symbol, actual New York week, side, volatility bucket, and entry period; signal plus exactly three closed controls",
        "matched_inference": "market-week cluster bootstrap 95% CI and two-sided cluster sign-flip p; Holm family is the fixed 5x5 timeframe/target matrix per scope and period",
        "attempt_control_rule": "expired orders contribute zero exposure to attempt expectancy and never count as wins/losses; filled censored orders remain unknown",
        "attempt_period_basis": "filled orders use actual entry time; unfilled orders use intended entry time",
        "condition_rule": "touch-wick-only and next-close-inside tables are post-hoc descriptions, not causal entry strategies",
        "return_basis": "return_bp is native net for crypto and native gross for XAUUSD; no initial R exists",
        "scope_definitions": {
            "CRYPTO_ALL": "crypto assets only; XAUUSD is reported separately because its return basis is gross",
            "ALTCOINS": "1/3-minute confirmation uses the four 1-minute-source alts; 5/15/60 combines all 630 alts",
            "COMMON6_CRYPTO": "BTC, ETH, SOL, BNB, XRP, DOGE; crypto_1m source and 2022-11-01 through 2026-09-30 entries",
            "COMMON6_CRYPTO_PARTIAL": "the same six-symbol scope with only currently completed 1-minute-source symbols present",
        },
        "backtest_executed": False,
        "raw_symbol_results_modified": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return summary


def _json_default(value: object) -> object:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def _write_chinese_metrics(metrics: pd.DataFrame, path: Path) -> None:
    names = {
        "analysis_scope": "范围", "period": "时段", "source_minutes": "源周期分钟",
        "confirm_minutes": "确认周期分钟", "target": "目标", "assets_present_n": "覆盖资产数",
        "assets_expected_n": "预期资产数", "scope_complete": "覆盖完整", "filled_n": "成交数",
        "closed_n": "已平仓数", "censored_n": "未决数", "return_basis": "收益口径",
        "gross_win_rate": "毛胜率(%)", "net_win_rate": "净胜率(%)",
        "return_win_rate": "收益口径胜率(%)", "mean_return_bp": "平均收益bp(收益口径)",
        "median_return_bp": "收益中位数bp(收益口径)", "mean_gross_bp": "平均毛收益bp",
        "median_gross_bp": "毛收益中位数bp", "gross_mean_win_bp": "平均毛盈利bp",
        "gross_mean_loss_bp": "平均毛亏损bp", "mean_fee_bp": "平均费用bp",
        "net_mean_bp": "平均净收益bp", "net_median_bp": "净收益中位数bp",
        "mean_win_bp": "平均盈利bp(收益口径)", "mean_loss_bp": "平均亏损bp(收益口径)",
        "payoff_ratio": "盈亏比", "profit_factor": "利润因子", "tp_n": "止盈数",
        "tp_before_confirmation_n": "确认前止盈数", "entry_stop_n": "移到开仓价后止损数",
        "failed_reclaim_n": "收回失败数", "worst_return_bp": "最差收益bp(收益口径)",
        "worst_gross_bp": "最差毛收益bp", "mean_holding_minutes": "平均持仓分钟",
        "median_holding_minutes": "持仓中位分钟", "p90_holding_minutes": "持仓P90分钟",
        "max_holding_minutes": "最长持仓分钟",
        "longest_loss_streak": "最长连亏", "longest_single_symbol_loss_streak": "单币最长连亏",
        "strict_match_n": "严格匹配组数", "matched_signal_mean_bp": "匹配信号均值bp",
        "matched_control_mean_bp": "匹配对照均值bp", "matched_delta_bp": "匹配差值bp",
        "paired_ci_low_bp": "簇自助CI下限bp", "paired_ci_high_bp": "簇自助CI上限bp",
        "independent_market_weeks": "独立市场周数", "signflip_p": "簇符号翻转p",
        "holm_p": "固定矩阵Holm校正p",
    }
    core = [column for column in names if column in metrics.columns]
    translated = metrics[core].copy()
    for column in ("gross_win_rate", "net_win_rate", "return_win_rate"):
        if column in translated:
            translated[column] = translated[column] * 100.0
    scope_names = {
        "CRYPTO_ALL": "加密资产总计", "BTC": "BTC", "ETH": "ETH", "XAUUSD": "XAUUSD",
        "ALTCOINS": "山寨币", "COMMON6_CRYPTO": "六币共同样本",
        "COMMON6_CRYPTO_PARTIAL": "六币共同样本(不完整)",
    }
    period_names = {"all": "全期", "pre2023": "2023年前", "post2023": "2023年起"}
    target_names = {
        "near_monday_edge": "近侧周一",
        "range_0.5": "区间0.5",
        "range_0.618": "区间0.618",
        "opposite_monday_edge": "对侧周一",
        "opposite_1618": "对侧1.618",
    }
    if "analysis_scope" in translated:
        translated["analysis_scope"] = translated["analysis_scope"].map(scope_names).fillna(translated["analysis_scope"])
    if "period" in translated:
        translated["period"] = translated["period"].map(period_names).fillna(translated["period"])
    if "target" in translated:
        translated["target"] = translated["target"].map(target_names).fillna(translated["target"])
    translated.rename(columns=names).to_csv(path, index=False, encoding="utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Analyze only symbols with done.json and label outputs as partial.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory. Defaults to analysis/ or analysis-partial/ under the experiment.",
    )
    args = parser.parse_args(argv)
    completed = sorted((EXP / "symbols").glob("*/done.json"))
    if not args.partial and len(completed) != EXPECTED_ASSETS:
        raise RuntimeError(
            f"Native coverage incomplete: {len(completed)}/{EXPECTED_ASSETS}; use --partial for explicitly partial outputs"
        )
    if not completed:
        raise RuntimeError("No completed per-symbol native results were found")
    output = args.output or EXP / ("analysis-partial" if args.partial else "analysis")
    summary = analyze(completed, output, partial=args.partial)
    print(json.dumps({
        "completion_status": summary["completion_status"],
        "assets_done": summary["assets_done"],
        "assets_expected": summary["assets_expected"],
        "output": str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
