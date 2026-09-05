#!/usr/bin/env python3
"""Run the one-shot V4 altcoin daily K1->K2 holdout evaluation.

At a completed K2 daily close, every signal and eligibility input uses only
OHLCV through that close. V4 keeps the frozen V3 directional breadth expansion
gate and adds one causal feature: direction times (K1 close - SMA34) / ATR14
must be at most 0.75. Entry remains the next complete UTC daily open.

Future bars are used only by the frozen V2 execution replay and outcome
columns. The authorized holdout is [2026-05-04, 2026-09-01), this exact
configuration records consumption number one, and the V3 confirmation-B
partition is excluded from warmup, breadth, signals, outcomes, and controls.
This script does not train or promote a model, write TradingView, change
ACTIVE/forward state, deploy, or touch a live account.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from scripts import research_altcoin_1d_k1k2_episode_runner as signal_parent
from scripts import research_altcoin_1d_k1k2_market_context as context_parent
from scripts import research_altcoin_1d_k1k2_risk_repair as execution_parent
from scripts.research_btcusdtp_15m_ma_state_trend import write_csv, write_json
from scripts.research_pine_eth_15m import sha256_bounded_frame
from scripts.research_two_key_candle_ma_retest_1h import sha256_file


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
READ_CHUNK_ROWS = 100_000


def utc(value: object) -> pd.Timestamp:
    """Return a timezone-aware UTC timestamp."""

    return signal_parent.utc(value)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_frozen_hash(path: Path, expected: str, label: str) -> None:
    context_parent._assert_head_frozen(path)
    if sha256_file(path) != expected:
        raise RuntimeError(f"{label} hash drifted")


def load_contracts(
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    parents = config["parents"]
    paths = {
        "signal": ROOT / str(parents["signal_config_path"]),
        "execution": ROOT / str(parents["execution_config_path"]),
        "context": ROOT / str(parents["context_config_path"]),
        "context_receipt": ROOT / str(parents["context_confirmation_receipt_path"]),
        "manifest": ROOT / str(parents["universe_manifest_path"]),
    }
    expected_hashes = {
        "signal": str(parents["signal_config_sha256"]),
        "execution": str(parents["execution_config_sha256"]),
        "context": str(parents["context_config_sha256"]),
        "context_receipt": str(parents["context_confirmation_receipt_sha256"]),
        "manifest": str(parents["universe_manifest_sha256"]),
    }
    for label, path in paths.items():
        assert_frozen_hash(path, expected_hashes[label], label)
    signal_config = load_json(paths["signal"])
    execution_config = load_json(paths["execution"])
    context_config = load_json(paths["context"])
    context_receipt = load_json(paths["context_receipt"])
    manifest = load_json(paths["manifest"])
    if context_receipt["status"] != "rejected_confirmation_a_b_remains_sealed":
        raise RuntimeError("V3 receipt status drifted")
    if dict(context_receipt["selected_context_params"]) != dict(
        config["fixed_context_params"]
    ):
        raise RuntimeError("V4 breadth gate does not match frozen V3")
    context_parent._validate_manifest(manifest)
    return signal_config, execution_config, context_config, manifest


def source_records(
    signal_config: Mapping[str, Any], manifest: Mapping[str, Any]
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Resolve only already-opened development and confirmation-A sources.

    This function reads configuration metadata only. It deliberately selects
    the current source segment for confirmation-A symbols and asserts that no
    selected path or symbol belongs to sealed confirmation B.
    """

    records: dict[str, dict[str, Any]] = {}
    for symbol, relative in sorted(signal_config["universe"]["instruments"].items()):
        records[str(symbol)] = {
            "cohort": "v1_v2_development",
            "sources": [str(relative)],
        }
    sealed_symbols = set(map(str, manifest["sealed_confirmation_b"]))
    sealed_paths = {
        str(relative)
        for record in manifest["sealed_confirmation_b"].values()
        for relative in record["sources"]
    }
    for symbol, record in sorted(manifest["confirmation_a"].items()):
        current = [
            str(relative)
            for relative in record["sources"]
            if "data/kline_fetched/" in str(relative)
        ]
        if not current:
            raise RuntimeError(f"confirmation-A symbol lacks current source: {symbol}")
        records[str(symbol)] = {
            "cohort": f"v3_confirmation_a_{record['cohort']}",
            "sources": current,
        }
    selected_paths = {
        relative for record in records.values() for relative in record["sources"]
    }
    if set(records) & sealed_symbols:
        raise RuntimeError("V4 target symbols overlap sealed confirmation B")
    if selected_paths & sealed_paths:
        raise RuntimeError("V4 target paths overlap sealed confirmation B")
    if {"BTC", "ETH"} & set(records):
        raise RuntimeError("BTC/ETH may be references only")
    return records, sealed_paths


def load_intraday_window(
    path: Path,
    *,
    start_inclusive: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    holdout_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read authorized OHLCV and retain only the frozen evaluation window.

    The input columns are open_time, open, high, low, close, and volume. The
    physical CSV is chunked; rows outside the frozen materialized window never
    enter indicators, signals, or outcomes.
    """

    pieces: list[pd.DataFrame] = []
    rows_parsed = 0
    rows_in_authorized_holdout = 0
    reached_right_boundary = False
    for chunk in pd.read_csv(path, chunksize=READ_CHUNK_ROWS):
        rows_parsed += int(len(chunk))
        if "open_time" in chunk:
            times = pd.to_datetime(chunk["open_time"], utc=True, errors="coerce")
        elif "ts" in chunk:
            times = pd.to_datetime(chunk["ts"], unit="ms", utc=True, errors="coerce")
        else:
            raise RuntimeError(f"source lacks time column: {path}")
        if times.isna().any():
            raise RuntimeError(f"source has invalid timestamps: {path}")
        authorized = times.ge(holdout_start) & times.lt(end_exclusive)
        rows_in_authorized_holdout += int(authorized.sum())
        keep = times.ge(start_inclusive) & times.lt(end_exclusive)
        if bool(keep.any()):
            current = chunk.loc[
                keep, ["open", "high", "low", "close", "volume"]
            ].copy()
            current.insert(0, "open_time", times.loc[keep].to_numpy())
            pieces.append(current)
        if bool(times.ge(end_exclusive).any()):
            reached_right_boundary = True
            break
    if not pieces:
        return pd.DataFrame(), {
            "path": str(path.relative_to(ROOT)),
            "rows_parsed": rows_parsed,
            "rows_retained": 0,
            "authorized_holdout_rows_retained": 0,
            "reached_right_boundary": reached_right_boundary,
            "bounded_sha256": None,
        }
    frame = pd.concat(pieces, ignore_index=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise RuntimeError(f"source has invalid OHLCV: {path}")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.sort_values("open_time", kind="mergesort").reset_index(drop=True)
    if frame["open_time"].duplicated().any():
        raise RuntimeError(f"source has duplicate timestamps: {path}")
    holdout_rows = int(frame["open_time"].ge(holdout_start).sum())
    if holdout_rows != rows_in_authorized_holdout:
        raise RuntimeError("authorized holdout row accounting drifted")
    return frame, {
        "path": str(path.relative_to(ROOT)),
        "rows_parsed": rows_parsed,
        "rows_retained": int(len(frame)),
        "authorized_holdout_rows_retained": holdout_rows,
        "reached_right_boundary": reached_right_boundary,
        "bounded_sha256": sha256_bounded_frame(frame),
    }


def load_daily_universe(
    records: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    opened_paths: set[str],
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict[str, Any]]:
    contract = config["time_contract"]
    start = utc(contract["warmup_start_inclusive"])
    end = utc(contract["evaluation_end_exclusive"])
    holdout = utc(contract["holdout_start_inclusive"])
    frames: dict[str, pd.DataFrame] = {}
    quality_rows: list[dict[str, Any]] = []
    for symbol, record in sorted(records.items()):
        pieces: list[pd.DataFrame] = []
        path_receipts: list[dict[str, Any]] = []
        for relative in record["sources"]:
            opened_paths.add(str(relative))
            raw, receipt = load_intraday_window(
                ROOT / str(relative),
                start_inclusive=start,
                end_exclusive=end,
                holdout_start=holdout,
            )
            path_receipts.append(receipt)
            if len(raw):
                pieces.append(raw)
        if not pieces:
            quality_rows.append(
                {
                    "symbol": symbol,
                    "cohort": record["cohort"],
                    "status": "no_rows_in_frozen_window",
                    "source_rows_retained": 0,
                    "holdout_rows_retained": 0,
                    "complete_days": 0,
                    "discarded_days": 0,
                    "first_daily_bar": pd.NaT,
                    "last_daily_bar": pd.NaT,
                    "daily_prefix_sha256": None,
                    "path_receipts": json.dumps(
                        path_receipts, sort_keys=True, separators=(",", ":")
                    ),
                }
            )
            continue
        raw = pd.concat(pieces, ignore_index=True).sort_values(
            "open_time", kind="mergesort"
        )
        if raw["open_time"].duplicated().any():
            raise RuntimeError(f"overlapping current sources for {symbol}")
        daily, aggregate = signal_parent.aggregate_complete_utc_days(
            raw,
            expected_bars=int(contract["intraday_bars_per_complete_day"]),
        )
        daily = daily[
            daily["open_time"].ge(start) & daily["open_time"].lt(end)
        ].reset_index(drop=True)
        eligible = bool(
            len(daily) >= int(contract["minimum_daily_history_bars"])
            and daily["open_time"].ge(holdout).any()
        )
        if eligible:
            frames[symbol] = daily
        quality_rows.append(
            {
                "symbol": symbol,
                "cohort": record["cohort"],
                "status": "eligible" if eligible else "insufficient_daily_history",
                "source_rows_retained": int(len(raw)),
                "holdout_rows_retained": int(raw["open_time"].ge(holdout).sum()),
                **aggregate,
                "first_daily_bar": daily["open_time"].iloc[0] if len(daily) else pd.NaT,
                "last_daily_bar": daily["open_time"].iloc[-1] if len(daily) else pd.NaT,
                "daily_prefix_sha256": (
                    context_parent._frame_sha256(
                        daily,
                        ["open_time", "open", "high", "low", "close", "volume"],
                    )
                    if len(daily)
                    else None
                ),
                "path_receipts": json.dumps(
                    path_receipts, sort_keys=True, separators=(",", ":")
                ),
            }
        )
    quality = pd.DataFrame(quality_rows)
    summary = {
        "configured_symbols": int(len(records)),
        "eligible_symbols": int(len(frames)),
        "source_rows_retained": int(quality["source_rows_retained"].sum()),
        "authorized_holdout_rows_retained": int(
            quality["holdout_rows_retained"].sum()
        ),
        "complete_daily_bars": int(quality["complete_days"].sum()),
        "discarded_days": int(quality["discarded_days"].sum()),
        "first_daily_bar": quality["first_daily_bar"].min(),
        "last_daily_bar": quality["last_daily_bar"].max(),
    }
    return frames, quality, summary


def execution_adapter(
    execution_config: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    adapted = deepcopy(dict(execution_config))
    adapted["splits"]["holdout"] = {
        "start_inclusive": config["time_contract"]["holdout_start_inclusive"],
        "end_exclusive": config["time_contract"]["evaluation_end_exclusive"],
        "folds": deepcopy(config["time_contract"]["folds"]),
    }
    adapted["matched_control"] = deepcopy(config["matched_control"])
    adapted["confirmation_minimums"] = deepcopy(config["sample_minimums"])
    return adapted


def context_adapter(
    context_config: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    adapted = deepcopy(dict(context_config))
    adapted["splits"]["holdout"] = {
        "start_inclusive": config["time_contract"]["holdout_start_inclusive"],
        "end_exclusive": config["time_contract"]["evaluation_end_exclusive"],
        "folds": deepcopy(config["time_contract"]["folds"]),
    }
    adapted["matched_control"] = deepcopy(config["matched_control"])
    adapted["confirmation_minimums"] = deepcopy(config["sample_minimums"])
    return adapted


def build_holdout_setups(
    daily_frames: Mapping[str, pd.DataFrame],
    reference_daily: Mapping[str, pd.DataFrame],
    cohort_by_symbol: Mapping[str, str],
    signal_config: Mapping[str, Any],
    context_config: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    """Build causal signal-day fields and then restrict rows to holdout."""

    params = config["fixed_signal_params"]
    start = utc(config["time_contract"]["holdout_start_inclusive"])
    end = utc(config["time_contract"]["evaluation_end_exclusive"])
    frames = {
        symbol: signal_parent.build_profile(
            daily, signal_config, str(params["ma_profile"])
        )
        for symbol, daily in sorted(daily_frames.items())
    }
    panel, _ = context_parent.build_context_panel(
        frames, reference_daily, context_config
    )
    lookup = panel.set_index(["symbol", "open_time"], verify_integrity=True)
    setups_by_symbol: dict[str, pd.DataFrame] = {}
    attempts: list[pd.DataFrame] = []
    pairs: list[pd.DataFrame] = []
    setups_all: list[pd.DataFrame] = []
    for symbol, frame in sorted(frames.items()):
        current_attempts, current_pairs = signal_parent.build_episode_signals(
            frame, symbol, signal_config, params
        )
        setups = signal_parent._setup_rows(
            current_pairs, frame, start, end, signal_config
        )
        if len(setups):
            rows: list[dict[str, Any]] = []
            for event in setups.to_dict("records"):
                key = (symbol, utc(event["signal_time"]))
                if key not in lookup.index:
                    raise RuntimeError(f"missing context for {symbol} {event['signal_time']}")
                rows.append(
                    {
                        **event,
                        **context_parent.directional_context(
                            lookup.loc[key].to_dict(),
                            int(event["direction"]),
                            context_config,
                        ),
                        "source_cohort": cohort_by_symbol[symbol],
                    }
                )
            setups = pd.DataFrame(rows)
            setups_all.append(setups)
        setups_by_symbol[symbol] = setups
        if len(current_attempts):
            attempts.append(current_attempts)
        if len(current_pairs):
            pairs.append(current_pairs)
    return (
        frames,
        setups_by_symbol,
        pd.concat(attempts, ignore_index=True) if attempts else pd.DataFrame(),
        pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(),
        pd.concat(setups_all, ignore_index=True) if setups_all else pd.DataFrame(),
        panel,
    )


def filter_setups(
    setups_by_symbol: Mapping[str, pd.DataFrame],
    context_params: Mapping[str, Any],
    *,
    extension_max: float | None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Apply frozen breadth first, then the sole V4 K1 extension cap."""

    after_context, context_rejections = context_parent.filter_setups(
        setups_by_symbol, context_params
    )
    if extension_max is None:
        return after_context, context_rejections
    kept: dict[str, pd.DataFrame] = {}
    rejected: list[dict[str, Any]] = (
        context_rejections.to_dict("records") if len(context_rejections) else []
    )
    for symbol, setups in sorted(after_context.items()):
        rows: list[dict[str, Any]] = []
        for row in setups.to_dict("records") if len(setups) else []:
            if float(row["k1_signed_slow_side_atr"]) <= extension_max:
                rows.append(row)
            else:
                rejected.append(
                    {**row, "context_rejection_reason": "k1_signed_slow_side_atr_max"}
                )
        kept[symbol] = pd.DataFrame(rows, columns=setups.columns)
    return kept, pd.DataFrame(rejected)


def evaluate_policy(
    frames: Mapping[str, pd.DataFrame],
    setups_by_symbol: Mapping[str, pd.DataFrame],
    signal_config: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    use_breadth: bool,
    extension_max: float | None,
) -> dict[str, Any]:
    context_params = (
        config["fixed_context_params"]
        if use_breadth
        else {key: -1.0 for key in config["fixed_context_params"]}
    )
    filtered, rejections = filter_setups(
        setups_by_symbol,
        context_params,
        extension_max=extension_max,
    )
    result = execution_parent.evaluate(
        frames,
        filtered,
        signal_config,
        execution_config,
        config["fixed_execution_params"],
        phase="holdout",
    )
    result["eligibility_rejections"] = rejections
    result["summary"] = {
        **result["summary"],
        "setups_before_eligibility": int(
            sum(len(frame) for frame in setups_by_symbol.values())
        ),
        "setups_after_eligibility": int(sum(len(frame) for frame in filtered.values())),
        "eligibility_rejections": int(len(rejections)),
    }
    return result


def largest_winner_sensitivity(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "largest_winner_symbol": None,
            "largest_winner_net_bp": np.nan,
            "largest_winner_share_of_total": np.nan,
            "leave_largest_winner_out_events": 0,
            "leave_largest_winner_out_mean_net_bp": np.nan,
        }
    largest_index = trades["net_return"].idxmax()
    largest = trades.loc[largest_index]
    remaining = trades.drop(index=largest_index)
    total = float(trades["net_return"].sum())
    return {
        "largest_winner_symbol": str(largest["symbol"]),
        "largest_winner_net_bp": float(largest["net_return"] * 10_000.0),
        "largest_winner_share_of_total": (
            float(largest["net_return"] / total) if total != 0 else np.nan
        ),
        "leave_largest_winner_out_events": int(len(remaining)),
        "leave_largest_winner_out_mean_net_bp": (
            float(remaining["net_return"].mean() * 10_000.0)
            if len(remaining)
            else np.nan
        ),
    }


def acceptance_checks(
    result: Mapping[str, Any],
    matched: Mapping[str, Any],
    concentration: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, bool]:
    summary = result["summary"]
    portfolio = result["portfolio_summary"]
    gates = config["acceptance_gates"]
    return {
        "sample_eligible": bool(summary["eligible"]),
        "mean_net_positive": bool(float(summary["mean_net_bp"]) > 0),
        "capped_mean_net_r_positive": bool(
            float(summary["mean_capped_net_r"]) > 0
        ),
        "profit_factor_above_one": bool(float(summary["profit_factor"]) > 1),
        "positive_fold_share": bool(
            int(summary["positive_folds"]) / max(1, int(summary["total_folds"]))
            >= float(gates["positive_fold_share_min"])
        ),
        "positive_symbol_share": bool(
            float(summary["positive_symbol_share"])
            >= float(gates["positive_symbol_share_min"])
        ),
        "week_cluster_signflip_p": bool(
            float(summary["week_cluster_signflip_p"])
            <= float(gates["week_cluster_signflip_p_max"])
        ),
        "matched_excess_positive": bool(float(matched["excess_bp"]) > 0),
        "matched_random_p": bool(
            float(matched["week_cluster_signflip_p"])
            <= float(gates["matched_random_p_max"])
        ),
        "leave_largest_winner_out_mean_net_positive": bool(
            float(concentration["leave_largest_winner_out_mean_net_bp"]) > 0
        ),
        "portfolio_total_return_positive": bool(
            float(portfolio["total_return"]) > 0
        ),
        "portfolio_drawdown": bool(
            abs(float(portfolio["closed_equity_max_drawdown"]))
            <= float(gates["portfolio_closed_equity_max_drawdown_max"])
        ),
    }


def write_policy_results(results: Path, name: str, result: Mapping[str, Any]) -> None:
    write_csv(result["trades"], results / f"holdout_{name}_trades.csv.gz")
    write_csv(result["folds"], results / f"holdout_{name}_folds.csv")
    write_csv(
        result["portfolio_trades"],
        results / f"holdout_{name}_portfolio_trades.csv.gz",
    )
    write_csv(
        result["portfolio_curve"],
        results / f"holdout_{name}_portfolio_equity.csv",
    )
    write_csv(
        result["eligibility_rejections"],
        results / f"holdout_{name}_eligibility_rejections.csv.gz",
    )


def run(config_path: Path) -> dict[str, Any]:
    experiment = config_path.parent
    prereg_path = experiment / "preregistration.json"
    for path in (config_path, prereg_path, SCRIPT_PATH):
        context_parent._assert_head_frozen(path)
    config = load_json(config_path)
    prereg = load_json(prereg_path)
    if not prereg["registered_before_holdout_open"]:
        raise RuntimeError("V4 preregistration is not frozen")
    if int(config["authorization"]["holdout_consumption_number_for_this_configuration"]) != 1:
        raise RuntimeError("V4 holdout consumption count must be one")

    signal_config, raw_execution, raw_context, manifest = load_contracts(config)
    records, sealed_paths = source_records(signal_config, manifest)
    reference_records = {
        symbol: {"cohort": "reference", "sources": [relative]}
        for symbol, relative in raw_context["reference_markets"].items()
    }
    selected_sources = {
        str(relative)
        for record in [*records.values(), *reference_records.values()]
        for relative in record["sources"]
    }
    missing_sources = [
        relative for relative in sorted(selected_sources) if not (ROOT / relative).is_file()
    ]
    if missing_sources:
        raise RuntimeError(f"V4 frozen sources missing: {missing_sources}")

    results = experiment / "results"
    started_path = results / "holdout_consumption_started.json"
    receipt_path = results / "holdout_receipt.json"
    if started_path.exists() or receipt_path.exists():
        raise RuntimeError("V4 final holdout has already started or completed")
    write_json(
        started_path,
        {
            "experiment_id": config["experiment_id"],
            "status": "holdout_consumption_started",
            "holdout_consumption_number_for_this_configuration": 1,
            "holdout_interval": {
                "start_inclusive": config["time_contract"]["holdout_start_inclusive"],
                "end_exclusive": config["time_contract"]["evaluation_end_exclusive"],
            },
            "selected_target_sources": len(selected_sources) - len(reference_records),
            "selected_reference_sources": len(reference_records),
            "sealed_confirmation_b_paths_allowed": 0,
            "config_sha256": sha256_file(config_path),
            "preregistration_sha256": sha256_file(prereg_path),
            "script_sha256": sha256_file(SCRIPT_PATH),
        },
    )

    opened_paths: set[str] = set()
    daily, source_quality, source_summary = load_daily_universe(
        records, config, opened_paths=opened_paths
    )
    if opened_paths & sealed_paths:
        raise RuntimeError("sealed confirmation B source opened")

    reference_daily, reference_quality, reference_summary = load_daily_universe(
        reference_records, config, opened_paths=opened_paths
    )
    if set(reference_daily) != {"BTC", "ETH"}:
        raise RuntimeError("BTC/ETH reference history unavailable")

    execution_config = execution_adapter(raw_execution, config)
    context_config = context_adapter(raw_context, config)
    cohort = {symbol: str(record["cohort"]) for symbol, record in records.items()}
    frames, setups_by_symbol, attempts, pairs, setups, panel = build_holdout_setups(
        daily,
        reference_daily,
        cohort,
        signal_config,
        context_config,
        config,
    )

    baseline = evaluate_policy(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        use_breadth=False,
        extension_max=None,
    )
    breadth = evaluate_policy(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        use_breadth=True,
        extension_max=None,
    )
    candidate = evaluate_policy(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        use_breadth=True,
        extension_max=float(config["single_new_variable"]["value"]),
    )
    controls, matched_pairs, matched = context_parent.matched_random_context(
        candidate["trades"],
        frames,
        panel,
        signal_config,
        execution_config,
        context_config,
        config["fixed_context_params"],
        phase="holdout",
    )
    concentration = largest_winner_sensitivity(candidate["trades"])
    gates = acceptance_checks(candidate, matched, concentration, config)
    accepted = bool(all(gates.values()))

    write_csv(source_quality, results / "holdout_source_quality.csv")
    write_csv(reference_quality, results / "holdout_reference_quality.csv")
    write_csv(attempts, results / "holdout_signal_attempts.csv.gz")
    write_csv(pairs, results / "holdout_signal_pairs.csv.gz")
    write_csv(setups, results / "holdout_all_setups.csv.gz")
    write_csv(panel, results / "holdout_context_panel.csv.gz")
    write_policy_results(results, "baseline", baseline)
    write_policy_results(results, "breadth", breadth)
    write_policy_results(results, "candidate", candidate)
    write_csv(controls, results / "holdout_matched_controls.csv.gz")
    write_csv(matched_pairs, results / "holdout_matched_pairs.csv")

    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "authorized_final_holdout",
        "status": (
            "accepted_research_only_all_holdout_gates_pass"
            if accepted
            else "rejected_final_holdout"
        ),
        "holdout_consumed": True,
        "holdout_consumption_number_for_this_configuration": 1,
        "holdout_interval": {
            "start_inclusive": config["time_contract"]["holdout_start_inclusive"],
            "end_exclusive": config["time_contract"]["evaluation_end_exclusive"],
        },
        "frozen_context_params": config["fixed_context_params"],
        "frozen_extension_max_atr": config["single_new_variable"]["value"],
        "fixed_signal_params": config["fixed_signal_params"],
        "fixed_execution_params": config["fixed_execution_params"],
        "source": source_summary,
        "reference_source": reference_summary,
        "selected_target_symbols": int(len(records)),
        "eligible_target_symbols": int(len(frames)),
        "opened_target_or_reference_paths": int(len(opened_paths)),
        "sealed_confirmation_b_paths_opened": 0,
        "baseline": baseline["summary"],
        "breadth_only": breadth["summary"],
        "candidate": candidate["summary"],
        "candidate_portfolio": candidate["portfolio_summary"],
        "matched_random": matched,
        "winner_concentration": concentration,
        "gate_checks": gates,
        "all_registered_gates_pass": accepted,
        "hashes": {
            "config_sha256": sha256_file(config_path),
            "preregistration_sha256": sha256_file(prereg_path),
            "script_sha256": sha256_file(SCRIPT_PATH),
        },
        "training_or_model_changed": False,
        "tradingview_active_forward_deployment_or_live_changed": False,
    }
    write_json(receipt_path, receipt)
    return receipt


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment-dir",
        type=Path,
        default=Path(
            "experiments/active/"
            "exp-altcoin-1d-k1k2-early-launch-holdout-20260905-v4"
        ),
    )
    parser.add_argument(
        "--eval-holdout",
        action="store_true",
        help="required explicit acknowledgement for the registered holdout run",
    )
    args = parser.parse_args(argv)
    if not args.eval_holdout:
        parser.error("--eval-holdout is required")
    return args


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    config_path = (
        args.experiment_dir
        if args.experiment_dir.name == "config.json"
        else args.experiment_dir / "config.json"
    )
    receipt = run(config_path.resolve())
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
