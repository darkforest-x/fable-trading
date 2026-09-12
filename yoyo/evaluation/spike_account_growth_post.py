"""Build causal regime, periodicity, concentration, and selection diagnostics.

Inputs are the pinned account-growth result bundle, the frozen V1/V7 common
execution ledger, and pinned Binance BTC/ETH 30-minute candles.  Market
features use only completed four-hour bars: a source bar stamped at ``t`` is
available at ``t + 4h``.  Launch breadth uses only V7 candidate entry
timestamps at or before each query and a trailing 24-hour window.  Outcome
columns are joined only after account admission and never define a regime or
threshold.

The development-distribution P80 breadth threshold is frozen without outcome
labels.  All calendar diagnostics use Asia/Shanghai explicitly.  The module
does not choose or alter entries, exits, costs, or production settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_account_growth_study import (
    ARM_SOURCE_CONTRACT,
    COMMON_EXECUTION_PATH,
    COMMON_EXECUTION_SHA256,
    NATIVE_V1_PATH,
    NATIVE_V1_SHA256,
    VALIDATION_START,
    load_common_execution_trades,
    load_native_v1_trades,
    select_period_scope,
    select_scope,
    sha256_file,
)
from yoyo.evaluation.spike_account_growth import simulate_shared_account


ROOT = Path(__file__).resolve().parents[2]
BTC_PATH = ROOT / (
    "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/"
    "normalized/binance/BTCUSDT_30m.csv.gz"
)
ETH_PATH = ROOT / (
    "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/"
    "normalized/binance/ETHUSDT_30m.csv.gz"
)
BTC_SHA256 = "35c7d4266605f3e39fa8bdf6518cb847bb816ba37145cd992e4f8db16bfe67a8"
ETH_SHA256 = "5753fedecf62de521cafff11d7c2c4db07ec508f80995dc1a87ec430ba6ed66e"
MATCHED_PATH = ROOT / (
    "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/"
    "post_final_v4/matched_control_metrics.csv"
)
MATCHED_SHA256 = "b4d2d64d02a9490091000b681fa16ef181b492ebaf5cd5e37ec236a0b8fcdd9b"
RESULT_NAMES = (
    "summary.csv", "accepted_ledger.csv.gz", "rejections.csv.gz",
    "equity_curve.csv.gz", "daily_realized_pnl.csv.gz",
)
SEED_SENSITIVITY_SEEDS = tuple(range(32))
SEED_SENSITIVITY_PERIODS = ("validation", "full")
_SEED_SENSITIVITY_CONFIG_COLUMNS = [
    "source_arm", "venue_scope", "timeframe", "sizing", "risk_fraction",
]
_ENGINE_COLUMNS = [
    "trade_id", "entry_time", "exit_time", "base_asset", "entry_price",
    "initial_risk", "side", "net_return", "censored",
]


def _require_hash(path: Path, expected: str) -> None:
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(f"hash mismatch for {path}: expected {expected}, got {observed}")


def verify_result_bundle(result_dir: Path) -> dict[str, Any]:
    """Verify every output named by the account replay manifest."""
    result_dir = Path(result_dir)
    manifest_path = result_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name in RESULT_NAMES:
        path = result_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        expected = manifest["outputs"].get(name)
        if expected is None:
            raise ValueError(f"manifest does not pin {name}")
        _require_hash(path, expected)
    return manifest


def _four_hour_market_frame(path: Path, expected_sha256: str, prefix: str) -> pd.DataFrame:
    _require_hash(path, expected_sha256)
    raw = pd.read_csv(path, usecols=["time", "close"])
    raw["time"] = pd.to_datetime(raw["time"], utc=True, errors="coerce")
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    if raw.isna().any().any() or raw["time"].duplicated().any():
        raise ValueError(f"invalid pinned market series: {path}")
    bars = (
        raw.set_index("time")["close"]
        .sort_index()
        .resample("4h", origin="epoch", label="left", closed="left")
        .last()
        .dropna()
        .rename(f"{prefix}_close")
        .to_frame()
    )
    bars["known_time"] = bars.index + pd.Timedelta(hours=4)
    bars[f"{prefix}_sma200"] = bars[f"{prefix}_close"].rolling(200, min_periods=200).mean()
    bars[f"{prefix}_return_7d"] = bars[f"{prefix}_close"] / bars[f"{prefix}_close"].shift(42) - 1.0
    return bars.reset_index(drop=True)


def build_btc_eth_regime(
    btc_path: Path = BTC_PATH,
    eth_path: Path = ETH_PATH,
    *,
    btc_sha256: str = BTC_SHA256,
    eth_sha256: str = ETH_SHA256,
) -> pd.DataFrame:
    """Return regimes known after each completed 4H BTC/ETH bar."""
    btc = _four_hour_market_frame(Path(btc_path), btc_sha256, "btc")
    eth = _four_hour_market_frame(Path(eth_path), eth_sha256, "eth")
    market = btc.merge(eth, on="known_time", how="inner", validate="one_to_one").sort_values("known_time")
    ready = market[["btc_sma200", "eth_sma200", "btc_return_7d", "eth_return_7d"]].notna().all(axis=1)
    bull = (
        market["btc_close"].gt(market["btc_sma200"])
        & market["eth_close"].gt(market["eth_sma200"])
        & market["btc_return_7d"].gt(0)
        & market["eth_return_7d"].gt(0)
    )
    bear = (
        market["btc_close"].lt(market["btc_sma200"])
        & market["eth_close"].lt(market["eth_sma200"])
        & market["btc_return_7d"].lt(0)
        & market["eth_return_7d"].lt(0)
    )
    market["market_regime"] = np.select(
        [ready & bull, ready & bear, ready], ["bull", "bear", "mixed"], default="unavailable"
    )
    return market.reset_index(drop=True)


def build_launch_breadth(
    common_trades: pd.DataFrame,
    query_times: pd.Series | pd.DatetimeIndex,
    *,
    window_hours: int = 24,
    development_end: pd.Timestamp = VALIDATION_START,
    quantile: float = 0.80,
    direction_share_threshold: float = 0.60,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Measure trailing unique-asset V7 launch breadth at exact query times.

    Cross-venue and duplicate-time signals are collapsed by normalized base
    asset and side.  Current-time candidates are included because their source
    signal bar is already closed at the supplied next-open entry timestamp.
    """
    required = {"variant", "entry_time", "base_asset", "side"}
    missing = required - set(common_trades)
    if missing:
        raise ValueError(f"common trades missing breadth columns: {sorted(missing)}")
    events = common_trades.loc[
        common_trades["variant"].eq("v7_bb_both"), ["entry_time", "base_asset", "side"]
    ].drop_duplicates()
    events["entry_time"] = pd.to_datetime(events["entry_time"], utc=True)
    query = pd.DatetimeIndex(pd.to_datetime(pd.Series(query_times), utc=True).dropna().unique()).sort_values()
    event_groups = {
        stamp: list(zip(group["base_asset"].astype(str), group["side"].astype(int)))
        for stamp, group in events.sort_values("entry_time").groupby("entry_time", sort=True)
    }
    query_set = set(query)
    timeline = sorted(set(event_groups) | query_set)
    active: deque[tuple[pd.Timestamp, str, int]] = deque()
    assets: Counter[str] = Counter()
    longs: Counter[str] = Counter()
    shorts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    window = pd.Timedelta(hours=window_hours)
    for stamp in timeline:
        cutoff = stamp - window
        while active and active[0][0] < cutoff:
            _, asset, side = active.popleft()
            assets[asset] -= 1
            (longs if side == 1 else shorts)[asset] -= 1
            if assets[asset] <= 0:
                del assets[asset]
            side_counter = longs if side == 1 else shorts
            if side_counter[asset] <= 0:
                del side_counter[asset]
        for asset, side in event_groups.get(stamp, []):
            active.append((stamp, asset, side))
            assets[asset] += 1
            (longs if side == 1 else shorts)[asset] += 1
        if stamp in query_set or stamp in event_groups:
            long_count, short_count = len(longs), len(shorts)
            denom = long_count + short_count
            rows.append({
                "time": stamp,
                "breadth_assets_24h": len(assets),
                "breadth_long_assets_24h": long_count,
                "breadth_short_assets_24h": short_count,
                "breadth_long_share": long_count / denom if denom else np.nan,
            })
    all_states = pd.DataFrame(rows).sort_values("time").drop_duplicates("time", keep="last")
    development_values = all_states.loc[
        all_states["time"].lt(development_end) & all_states["time"].isin(event_groups),
        "breadth_assets_24h",
    ]
    if development_values.empty:
        raise ValueError("no development breadth observations")
    threshold = int(development_values.quantile(quantile, interpolation="higher"))
    wide = all_states["breadth_assets_24h"].ge(threshold)
    up = all_states["breadth_long_share"].ge(direction_share_threshold)
    down = all_states["breadth_long_share"].le(1.0 - direction_share_threshold)
    all_states["breadth_regime"] = np.select(
        [wide & up, wide & down, wide], ["wide_up", "wide_down", "wide_mixed"], default="normal"
    )
    output = all_states.loc[all_states["time"].isin(query_set)].reset_index(drop=True)
    meta = {
        "window_hours": window_hours,
        "development_end": development_end.isoformat(),
        "quantile": quantile,
        "wide_threshold_unique_assets": threshold,
        "direction_share_threshold": direction_share_threshold,
        "development_observations": int(len(development_values)),
    }
    return output, meta


def annotate_accepted_trades(
    accepted: pd.DataFrame,
    market: pd.DataFrame,
    breadth: pd.DataFrame,
) -> pd.DataFrame:
    """Join causal context to already-admitted account trades."""
    annotated = accepted.copy()
    annotated["entry_time"] = pd.to_datetime(annotated["entry_time"], utc=True)
    market_sorted = market.sort_values("known_time")
    annotated = pd.merge_asof(
        annotated.sort_values("entry_time"), market_sorted,
        left_on="entry_time", right_on="known_time", direction="backward", allow_exact_matches=True,
    )
    annotated = annotated.merge(breadth, left_on="entry_time", right_on="time", how="left", validate="many_to_one")
    annotated["entry_time_bjt"] = annotated["entry_time"].dt.tz_convert("Asia/Shanghai")
    annotated["entry_hour_bjt"] = annotated["entry_time_bjt"].dt.hour
    annotated["entry_weekday_bjt"] = annotated["entry_time_bjt"].dt.day_name()
    annotated["entry_month_bjt"] = annotated["entry_time_bjt"].dt.to_period("M").astype(str)
    return annotated


def select_on_development(summary: pd.DataFrame) -> pd.DataFrame:
    """Freeze one parameter tuple per arm/scope using development balance only."""
    keys = ["source_arm", "venue_scope"]
    params = ["timeframe", "sizing", "risk_fraction"]
    development = summary.loc[summary["period_scope"].eq("development")].copy()
    if development.empty:
        raise ValueError("development rows missing")
    development = development.sort_values(
        keys + ["final_balance", "max_drawdown_fraction", "timeframe", "sizing", "risk_fraction"],
        ascending=[True, True, False, True, True, True, True], kind="mergesort",
    )
    chosen = development.groupby(keys, as_index=False, sort=False).first()
    selected_cols = keys + params
    out = chosen[selected_cols + ["final_balance", "max_drawdown_fraction", "floor_triggered", "run_id"]].rename(columns={
        "final_balance": "development_final_balance",
        "max_drawdown_fraction": "development_closed_mdd",
        "floor_triggered": "development_floor_triggered",
        "run_id": "development_run_id",
    })
    for period in ("validation", "full"):
        source = summary.loc[summary["period_scope"].eq(period)].copy()
        cols = selected_cols + ["final_balance", "max_drawdown_fraction", "floor_triggered", "bankrupt", "reached_100k", "run_id", "accepted", "candidates"]
        renamed = {name: f"{period}_{name}" for name in cols if name not in selected_cols}
        out = out.merge(source[cols].rename(columns=renamed), on=selected_cols, how="left", validate="one_to_one")
    return out


def select_seed_zero_configurations(summary: pd.DataFrame) -> pd.DataFrame:
    """Freeze arm/venue parameters from development rows replayed with seed zero.

    Validation and full-period outcomes are deliberately absent from the
    ranking input.  They are attached by :func:`select_on_development` only as
    receipts for the already-frozen tuple.
    """
    if "seed" not in summary:
        raise ValueError("account summary missing same-timestamp seed")
    seed_zero = summary.loc[summary["seed"].astype(str).eq("0")].copy()
    if seed_zero.empty:
        raise ValueError("account summary has no seed=0 rows for development selection")
    return select_on_development(seed_zero)


def _seed_sensitivity_source(arms: set[str]) -> pd.DataFrame:
    """Load only the immutable V1/V7 ledgers required by frozen selections."""
    sources: list[pd.DataFrame] = []
    if arms - {"v1_native_long"}:
        sources.append(load_common_execution_trades(COMMON_EXECUTION_PATH, expected_sha256=COMMON_EXECUTION_SHA256))
    if "v1_native_long" in arms:
        sources.append(load_native_v1_trades(NATIVE_V1_PATH, expected_sha256=NATIVE_V1_SHA256))
    if not sources:
        raise ValueError("seed sensitivity has no selected source arms")
    return pd.concat(sources, ignore_index=True)


def replay_seed_sensitivity(
    source: pd.DataFrame,
    development_selection: pd.DataFrame,
    *,
    seeds: tuple[int, ...] = SEED_SENSITIVITY_SEEDS,
    initial_balance: float = 1000.0,
    portfolio_risk_cap: float = 0.10,
    gross_leverage_cap: float = 3.0,
    entry_floor_fraction: float = 0.20,
) -> pd.DataFrame:
    """Replay frozen development-selected tuples across order-only seeds.

    The simulator's seed enters only its outcome-free same-timestamp ordering
    hash.  This function never searches validation/full rows: every replay
    uses the exact ``timeframe``, ``sizing``, and ``risk_fraction`` selected
    from seed-zero development balance for its arm and venue scope.
    """
    required = set(_SEED_SENSITIVITY_CONFIG_COLUMNS) | {
        "development_final_balance", "development_closed_mdd", "development_run_id",
    }
    missing = required - set(development_selection.columns)
    if missing:
        raise ValueError(f"seed sensitivity selection missing columns: {sorted(missing)}")
    normalized_seeds = tuple(int(seed) for seed in seeds)
    if not normalized_seeds or len(set(normalized_seeds)) != len(normalized_seeds):
        raise ValueError("seed sensitivity seeds must be non-empty and unique")
    rows: list[dict[str, Any]] = []
    if development_selection.duplicated(["source_arm", "venue_scope"]).any():
        raise ValueError("development selection must contain one frozen tuple per arm and venue")
    selected_configs = development_selection.loc[:, sorted(required)].copy()
    for config in selected_configs.sort_values(["source_arm", "venue_scope"], kind="mergesort").to_dict("records"):
        timeframe: int | str = config["timeframe"]
        if str(timeframe) != "all":
            timeframe = int(timeframe)
        scoped_all = select_scope(
            source, arm=str(config["source_arm"]), venue_scope=str(config["venue_scope"]), timeframe=timeframe,
        )
        for period_scope in SEED_SENSITIVITY_PERIODS:
            scoped = select_period_scope(scoped_all, period_scope)
            engine_input = scoped.assign(exit_time=scoped["account_exit_time"]).loc[:, _ENGINE_COLUMNS]
            for seed in normalized_seeds:
                result = simulate_shared_account(
                    engine_input,
                    sizing=str(config["sizing"]),
                    risk_fraction=float(config["risk_fraction"]),
                    initial_balance=float(initial_balance),
                    portfolio_risk_cap=float(portfolio_risk_cap),
                    gross_leverage_cap=float(gross_leverage_cap),
                    entry_floor_fraction=float(entry_floor_fraction),
                    seed=seed,
                )
                replay = result["summary"]
                rows.append({
                    "source_arm": str(config["source_arm"]),
                    "venue_scope": str(config["venue_scope"]),
                    "timeframe": str(timeframe),
                    "sizing": str(config["sizing"]),
                    "risk_fraction": float(config["risk_fraction"]),
                    "period_scope": period_scope,
                    "seed": seed,
                    "selection_seed": 0,
                    "selection_period": "development",
                    "development_run_id": config["development_run_id"],
                    "development_final_balance": float(config["development_final_balance"]),
                    "development_closed_mdd": float(config["development_closed_mdd"]),
                    "source_contract": ARM_SOURCE_CONTRACT[str(config["source_arm"])],
                    "final_balance": float(replay["final_balance"]),
                    "net_pnl": float(replay["net_pnl"]),
                    "net_return": float(replay["net_return"]),
                    "candidates": int(replay["candidates"]),
                    "accepted": int(replay["selected"]),
                    "rejected": int(replay["rejected"]),
                    "closed": int(replay["closed"]),
                    "censored_boundary": int(replay["censored_boundary"]),
                    "floor_triggered": bool(replay["floor_triggered"]),
                    "bankrupt": bool(replay["bankrupt"]),
                    "max_gross_leverage": float(replay["max_gross_leverage"]),
                    "max_portfolio_initial_risk": float(replay["max_portfolio_initial_risk"]),
                })
    return pd.DataFrame(rows).sort_values(
        ["source_arm", "venue_scope", "period_scope", "seed"], kind="mergesort"
    ).reset_index(drop=True)


def assert_seed_zero_reproduces_summary(
    sensitivity: pd.DataFrame,
    source_summary: pd.DataFrame,
) -> None:
    """Fail closed unless the frozen seed-zero rows reproduce the study summary."""
    source = source_summary.loc[
        source_summary["seed"].astype(str).eq("0")
        & source_summary["period_scope"].isin(SEED_SENSITIVITY_PERIODS)
    ].copy()
    actual = sensitivity.loc[sensitivity["seed"].eq(0)].copy()
    keys = _SEED_SENSITIVITY_CONFIG_COLUMNS + ["period_scope"]
    expected_columns = keys + [
        "final_balance", "net_pnl", "net_return", "candidates", "accepted", "rejected",
        "closed", "censored_boundary", "floor_triggered", "bankrupt",
    ]
    missing = set(expected_columns) - set(source.columns)
    if missing:
        raise ValueError(f"source summary missing seed-zero comparison columns: {sorted(missing)}")
    source["timeframe"] = source["timeframe"].astype(str)
    source["risk_fraction"] = source["risk_fraction"].astype(float)
    actual["timeframe"] = actual["timeframe"].astype(str)
    actual["risk_fraction"] = actual["risk_fraction"].astype(float)
    selected_keys = actual.loc[:, _SEED_SENSITIVITY_CONFIG_COLUMNS].drop_duplicates()
    expected = source.loc[:, expected_columns].merge(
        selected_keys, on=_SEED_SENSITIVITY_CONFIG_COLUMNS, how="inner", validate="many_to_one",
    )
    merged = actual.merge(expected, on=keys, how="outer", suffixes=("_actual", "_expected"), indicator=True)
    if not merged["_merge"].eq("both").all():
        raise ValueError("seed-zero sensitivity rows do not match the frozen study configuration")
    numeric = ("final_balance", "net_pnl", "net_return")
    for name in numeric:
        if not np.isclose(
            merged[f"{name}_actual"].astype(float), merged[f"{name}_expected"].astype(float), rtol=0.0, atol=1e-10,
        ).all():
            raise ValueError(f"seed-zero sensitivity failed to reproduce summary {name}")
    exact = ("candidates", "accepted", "rejected", "closed", "censored_boundary", "floor_triggered", "bankrupt")
    for name in exact:
        if not merged[f"{name}_actual"].eq(merged[f"{name}_expected"]).all():
            raise ValueError(f"seed-zero sensitivity failed to reproduce summary {name}")


def _group_trade_metrics(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    realized = frame.loc[~frame["boundary_mark"].astype(bool)].copy()
    realized["win"] = realized["realized_pnl"].gt(0)
    realized["ten_r"] = realized["exit_r_multiple"].ge(10)
    grouped = realized.groupby(group_columns, dropna=False, as_index=False).agg(
        closed=("trade_id", "size"),
        wins=("win", "sum"),
        realized_pnl=("realized_pnl", "sum"),
        mean_account_r=("exit_r_multiple", "mean"),
        median_account_r=("exit_r_multiple", "median"),
        ten_r_winners=("ten_r", "sum"),
    )
    grouped["win_rate"] = grouped["wins"] / grouped["closed"]
    return grouped


def _risk_summary(summary: pd.DataFrame) -> pd.DataFrame:
    frame = summary.copy()
    frame["positive"] = frame["final_balance"].gt(frame["initial_balance"])
    return frame.groupby(["period_scope", "sizing", "risk_fraction"], as_index=False).agg(
        paths=("run_id", "size"),
        median_final_balance=("final_balance", "median"),
        best_final_balance=("final_balance", "max"),
        worst_final_balance=("final_balance", "min"),
        positive_paths=("positive", "sum"),
        floor_paths=("floor_triggered", "sum"),
        bankrupt_paths=("bankrupt", "sum"),
        reached_100k_paths=("reached_100k", "sum"),
        median_closed_mdd=("max_drawdown_fraction", "median"),
    )


def _beijing_daily(curve: pd.DataFrame) -> pd.DataFrame:
    frame = curve.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame["date_bjt"] = frame["time"].dt.tz_convert("Asia/Shanghai").dt.date.astype(str)
    daily = frame.groupby([
        "run_id", "source_arm", "venue_scope", "timeframe", "period_scope", "sizing", "risk_fraction", "date_bjt"
    ], as_index=False).agg(
        realized_pnl=("realized_pnl", "sum"),
        end_balance=("balance", "last"),
        exit_events=("exits", "sum"),
    )
    daily["start_balance"] = daily["end_balance"] - daily["realized_pnl"]
    daily["realized_day_return"] = np.where(
        daily["start_balance"].gt(0), daily["realized_pnl"] / daily["start_balance"], np.nan
    )
    return daily


def _best_days(daily: pd.DataFrame) -> pd.DataFrame:
    full = daily.loc[daily["period_scope"].eq("full")].copy()
    if full.empty:
        return full
    return (
        full.sort_values(["run_id", "realized_pnl", "realized_day_return"], ascending=[True, False, False])
        .groupby("run_id", as_index=False, sort=False).first()
    )


def _milestones(curve: pd.DataFrame) -> pd.DataFrame:
    thresholds = (2_000.0, 5_000.0, 10_000.0, 25_000.0, 50_000.0, 100_000.0)
    rows: list[dict[str, Any]] = []
    for run_id, group in curve.loc[curve["period_scope"].eq("full")].groupby("run_id", sort=False):
        group = group.sort_values("time")
        context = group.iloc[0]
        for threshold in thresholds:
            reached = group.loc[group["balance"].ge(threshold)]
            if len(reached):
                first = reached.iloc[0]
                rows.append({
                    "run_id": run_id, "source_arm": context["source_arm"],
                    "venue_scope": context["venue_scope"], "timeframe": context["timeframe"],
                    "sizing": context["sizing"], "risk_fraction": context["risk_fraction"],
                    "threshold": threshold, "first_reached_time": first["time"], "balance": first["balance"],
                })
    return pd.DataFrame(rows)


def build_post_diagnostics(result_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Verify the replay and write reusable diagnostic tables."""
    result_dir, output_dir = Path(result_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    replay_manifest = verify_result_bundle(result_dir)
    summary = pd.read_csv(result_dir / "summary.csv")
    accepted = pd.read_csv(result_dir / "accepted_ledger.csv.gz", float_precision="round_trip")
    curve = pd.read_csv(result_dir / "equity_curve.csv.gz", float_precision="round_trip")
    for name in ("entry_time", "exit_time"):
        if name in accepted:
            accepted[name] = pd.to_datetime(accepted[name], utc=True)
    curve["time"] = pd.to_datetime(curve["time"], utc=True)
    market = build_btc_eth_regime()
    common = load_common_execution_trades(COMMON_EXECUTION_PATH, expected_sha256=COMMON_EXECUTION_SHA256)
    breadth, breadth_meta = build_launch_breadth(common, accepted["entry_time"])
    annotated = annotate_accepted_trades(accepted, market, breadth)
    regime = _group_trade_metrics(annotated, [
        "run_id", "source_arm", "venue_scope", "timeframe", "period_scope", "sizing", "risk_fraction",
        "market_regime", "breadth_regime",
    ])
    hourly = _group_trade_metrics(annotated, [
        "run_id", "source_arm", "venue_scope", "timeframe", "period_scope", "sizing", "risk_fraction", "entry_hour_bjt",
    ])
    weekday = _group_trade_metrics(annotated, [
        "run_id", "source_arm", "venue_scope", "timeframe", "period_scope", "sizing", "risk_fraction", "entry_weekday_bjt",
    ])
    monthly = _group_trade_metrics(annotated, [
        "run_id", "source_arm", "venue_scope", "timeframe", "period_scope", "sizing", "risk_fraction", "entry_month_bjt",
    ])
    selection = select_seed_zero_configurations(summary)
    sensitivity_source = _seed_sensitivity_source(set(selection["source_arm"]))
    seed_sensitivity = replay_seed_sensitivity(sensitivity_source, selection)
    assert_seed_zero_reproduces_summary(seed_sensitivity, summary)
    risk = _risk_summary(summary)
    daily = _beijing_daily(curve)
    best_days = _best_days(daily)
    milestones = _milestones(curve)
    _require_hash(MATCHED_PATH, MATCHED_SHA256)
    matched = pd.read_csv(MATCHED_PATH)
    matched = matched.loc[matched["variant"].isin(("v1_common_execution_long", "v7_bb_long", "v7_bb_both"))]

    tables = {
        "annotated_accepted.csv.gz": annotated,
        "regime_metrics.csv": regime,
        "entry_hour_metrics.csv": hourly,
        "entry_weekday_metrics.csv": weekday,
        "entry_month_metrics.csv": monthly,
        "development_selection.csv": selection,
        "seed_sensitivity.csv": seed_sensitivity,
        "risk_summary.csv": risk,
        "daily_realized_bjt.csv.gz": daily,
        "best_days.csv": best_days,
        "milestone_chain.csv": milestones,
        "matched_control_reference.csv": matched,
        "btc_eth_regime.csv.gz": market,
        "breadth_at_accepted_entries.csv.gz": breadth,
    }
    outputs: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    for name, table in tables.items():
        path = output_dir / name
        compression = {"method": "gzip", "mtime": 0} if name.endswith(".gz") else None
        table.to_csv(path, index=False, compression=compression)
        outputs[name] = sha256_file(path)
        row_counts[name] = int(len(table))
    manifest = {
        "account_replay_manifest_sha256": sha256_file(result_dir / "run_manifest.json"),
        "account_replay_sources": replay_manifest.get("sources"),
        "btc_source": {"path": str(BTC_PATH), "sha256": BTC_SHA256},
        "eth_source": {"path": str(ETH_PATH), "sha256": ETH_SHA256},
        "matched_control_source": {"path": str(MATCHED_PATH), "sha256": MATCHED_SHA256},
        "common_source": {"path": str(COMMON_EXECUTION_PATH), "sha256": COMMON_EXECUTION_SHA256},
        "breadth_contract": breadth_meta,
        "seed_sensitivity": {
            "file": "seed_sensitivity.csv",
            "sha256": outputs["seed_sensitivity.csv"],
            "rows": row_counts["seed_sensitivity.csv"],
        },
        "seed_sensitivity_contract": {
            "selection_seed": 0,
            "selection_period": "development",
            "replay_seeds": list(SEED_SENSITIVITY_SEEDS),
            "replay_periods": list(SEED_SENSITIVITY_PERIODS),
            "selection_rule": (
                "One timeframe/sizing/risk_fraction tuple per source_arm and venue_scope is selected "
                "from seed=0 development rows only; validation and full rows are replay-only."
            ),
            "ordering_rule": (
                "simulate_shared_account uses seed only in its outcome-independent stable hash to order "
                "candidates sharing an entry timestamp."
            ),
            "seed_zero_summary_reproduced": True,
        },
        "timezone": "Asia/Shanghai",
        "module_sha256": sha256_file(Path(__file__)),
        "outputs": outputs,
        "row_counts": row_counts,
        "notes": [
            "Regimes and the P80 breadth threshold use no outcome label.",
            "Matched controls are event-level references, not shared-account simulations.",
            "Full-period best days and milestones are hindsight descriptions, not a forecast.",
        ],
    }
    (output_dir / "post_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SPIKE account-growth post diagnostics.")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    build_post_diagnostics(args.result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
