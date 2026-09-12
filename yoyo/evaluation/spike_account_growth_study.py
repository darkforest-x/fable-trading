"""Replay frozen SPIKE V1/V7 trades through one causal shared account.

This module consumes the already-produced common-execution ledger and the
separately pinned original-V1 ledger.  It does not read candles, create
signals, or alter a completed exit.  The ledgers supply one candidate per row with
``entry_time``, ``exit_time``, ``side``, ``entry_price``, ``initial_risk``,
``net_return``, ``censored``, ``variant``, ``venue``, ``symbol`` and
``timeframe_min``.  Account admission sees only entry-known identity fields;
``net_return`` is consumed by ``simulate_shared_account`` only at exit.

The caller/report must record the owner's explicit authorization to consume
the post-2026-05-04 portion of this frozen ledger.  This module deliberately
does not make that authorization decision, tune any parameter, or load a
second data source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_account_growth import simulate_shared_account


ROOT = Path(__file__).resolve().parents[2]
COMMON_EXECUTION_PATH = ROOT / (
    "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/"
    "post_final_v4/common_execution_trades.csv.gz"
)
COMMON_EXECUTION_SHA256 = "cd31bf4c1a4447b211af0f4ed42f5dbfef7b56e91cbec73a75f765e4ac8d5673"
NATIVE_V1_PATH = ROOT / (
    "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/"
    "covered_trade_ledger.csv.gz"
)
NATIVE_V1_SHA256 = "b15b69b8864e5eb651f2417fc6681ea688b5fbb95dacd15af1284b42744e3578"

ARMS: Mapping[str, str] = {
    "v1_common_execution_long": "v1_common_execution_long",
    "v7_bb_long": "v7_bb_long",
    "v7_bb_both": "v7_bb_both",
    "v1_native_long": "v1_native_long",
}
ARM_SOURCE_CONTRACT = {
    "v1_common_execution_long": "common_execution_next_open_shared_exit",
    "v7_bb_long": "common_execution_next_open_shared_exit",
    "v7_bb_both": "common_execution_next_open_shared_exit",
    "v1_native_long": "original_v1_native_exit_not_fair_common_execution",
}
REQUIRED_SOURCE_COLUMNS = {
    "signal_bar_open", "entry_time", "exit_time", "side", "entry_price",
    "initial_risk", "net_return", "censored", "variant", "venue", "symbol",
    "timeframe_min",
}
DEFAULT_RISK_FRACTIONS = (0.03, 0.05, 0.10)
DEFAULT_SIZINGS = ("fixed", "compound")
DEFAULT_TIMEFRAMES: tuple[int | str, ...] = (30, 60, 240, "all")
DEFAULT_VENUE_SCOPES = ("combined", "okx")
DEFAULT_PERIOD_SCOPES = ("full", "development", "validation")
DEVELOPMENT_START = pd.Timestamp("2024-09-10T00:00:00Z")
VALIDATION_START = pd.Timestamp("2025-09-10T00:00:00Z")
VALIDATION_END = pd.Timestamp("2026-09-10T00:00:00Z")


def sha256_file(path: Path) -> str:
    """Return the byte hash of a frozen input or an output receipt."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def base_asset_from_symbol(symbol: object) -> str:
    """Map common CEX perpetual spellings to one stable base-asset key.

    ``BTCUSDT``, ``BTC-USDT-SWAP`` and ``BTCUSDT.P`` therefore reserve the
    same shared-account asset slot.  This only uses symbol text known at entry.
    """
    text = str(symbol).upper().strip()
    text = text.split(":", 1)[0]
    text = re.sub(r"\.(?:P|PERP)$", "", text)
    text = re.sub(r"(?:[-_/]?)(?:USDT|USDC|BUSD|USD)(?:[-_/]?(?:SWAP|PERP|PERPETUAL))?$", "", text)
    text = re.sub(r"[-_/]", "", text)
    for multiplier in ("1000000", "100000", "10000", "1000"):
        if text.startswith(multiplier) and len(text) > len(multiplier):
            text = text[len(multiplier):]
            break
    if not text:
        raise ValueError(f"cannot derive base asset from symbol {symbol!r}")
    return text


def _as_bool(values: pd.Series) -> pd.Series:
    """Accept CSV boolean spellings without treating non-empty strings as true."""
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False).astype(bool)
    normalized = values.astype(str).str.strip().str.lower()
    valid = {"true": True, "false": False, "1": True, "0": False, "nan": False, "": False}
    if not normalized.isin(valid).all():
        invalid = sorted(normalized.loc[~normalized.isin(valid)].unique())[:5]
        raise ValueError(f"censored contains unsupported boolean values: {invalid}")
    return normalized.map(valid).astype(bool)


def _stable_trade_id(row: pd.Series) -> str:
    """Hash only frozen identity/entry-known fields, never result fields."""
    fields = (
        str(row["variant"]), str(row["venue"]), str(row["symbol"]),
        str(int(row["timeframe_min"])), pd.Timestamp(row["signal_bar_open"]).isoformat(),
        pd.Timestamp(row["entry_time"]).isoformat(), str(int(row["side"])),
        format(float(row["entry_price"]), ".17g"), format(float(row["initial_risk"]), ".17g"),
        str(int(row["source_row"])),
    )
    return "trade-" + hashlib.sha256("|".join(fields).encode()).hexdigest()[:24]


def load_common_execution_trades(
    path: Path = COMMON_EXECUTION_PATH,
    *,
    expected_sha256: str = COMMON_EXECUTION_SHA256,
) -> pd.DataFrame:
    """Verify and normalise the immutable common-execution ledger.

    A changed source is rejected rather than silently accepted.  V1 and V7
    remain the original ``variant`` values until ``select_scope`` selects an
    explicit research arm.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    observed = sha256_file(path)
    if observed != expected_sha256:
        raise ValueError(f"frozen common ledger hash mismatch: expected {expected_sha256}, got {observed}")
    frame = pd.read_csv(path, float_precision="round_trip")
    missing = REQUIRED_SOURCE_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"common execution ledger missing columns: {sorted(missing)}")
    frame = frame.copy().reset_index(names="source_row")
    for name in ("signal_bar_open", "entry_time", "exit_time"):
        frame[name] = pd.to_datetime(frame[name], utc=True, errors="coerce")
        if frame[name].isna().any():
            raise ValueError(f"common execution ledger has invalid {name}")
    if frame["exit_time"].lt(frame["entry_time"]).any():
        raise ValueError("common execution ledger contains exits before entries")
    for name in ("side", "entry_price", "initial_risk", "net_return", "timeframe_min"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
        if frame[name].isna().any() or not np.isfinite(frame[name].to_numpy(float)).all():
            raise ValueError(f"common execution ledger has non-finite {name}")
    frame["side"] = frame["side"].astype(int)
    frame["timeframe_min"] = frame["timeframe_min"].astype(int)
    if not frame["side"].isin((-1, 1)).all():
        raise ValueError("common execution ledger side must be -1 or 1")
    if (frame[["entry_price", "initial_risk"]] <= 0).any().any():
        raise ValueError("common execution ledger has non-positive price or risk")
    frame["censored"] = _as_bool(frame["censored"])
    # A small number of precomputed intrabar stop rows use the same bar-open
    # timestamp for entry and exit.  The cashbook must see the entry first;
    # move only its accounting timestamp one nanosecond right, without
    # changing the frozen source exit timestamp or any PnL/exit result.
    frame["account_exit_time"] = frame["exit_time"]
    equal_exit = frame["exit_time"].eq(frame["entry_time"])
    frame.loc[equal_exit, "account_exit_time"] += pd.Timedelta(nanoseconds=1)
    frame["base_asset"] = frame["symbol"].map(base_asset_from_symbol)
    frame["trade_id"] = frame.apply(_stable_trade_id, axis=1)
    if frame["trade_id"].duplicated().any():
        raise ValueError("stable source trade identifiers unexpectedly collided")
    frame.attrs["source_path"] = str(path)
    frame.attrs["source_sha256"] = observed
    frame["source_contract"] = "common_execution_next_open_shared_exit"
    return frame


def load_native_v1_trades(
    path: Path = NATIVE_V1_PATH,
    *,
    expected_sha256: str = NATIVE_V1_SHA256,
) -> pd.DataFrame:
    """Map the frozen original-V1 ledger into the shared-account input shape.

    This keeps original V1's exit contract separate from the common-execution
    arms.  It is an operational reference, never an apples-to-apples V7
    comparison.  Native daily rows are deliberately excluded by contract.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    observed = sha256_file(path)
    if observed != expected_sha256:
        raise ValueError(f"frozen native V1 ledger hash mismatch: expected {expected_sha256}, got {observed}")
    frame = pd.read_csv(path, float_precision="round_trip")
    required = {
        "event_id", "venue", "symbol", "asset", "timeframe_min", "direction",
        "signal_bar_open", "entry_time", "entry_price", "exit_time", "exit_price",
        "exit_reason", "net_return", "reference_signal_risk", "net_r", "mfe_return", "censored",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"native V1 ledger missing columns: {sorted(missing)}")
    frame = frame.loc[frame["timeframe_min"].isin((30, 60, 240))].copy().reset_index(names="source_row")
    if not frame["direction"].astype(str).str.lower().eq("long").all():
        raise ValueError("native V1 ledger expected long-only rows")
    for name in ("signal_bar_open", "entry_time", "exit_time"):
        frame[name] = pd.to_datetime(frame[name], utc=True, errors="coerce")
        if frame[name].isna().any():
            raise ValueError(f"native V1 ledger has invalid {name}")
    if frame["exit_time"].lt(frame["entry_time"]).any():
        raise ValueError("native V1 ledger contains exits before entries")
    numeric = ("entry_price", "reference_signal_risk", "exit_price", "net_return", "net_r", "mfe_return")
    for name in numeric:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
        if frame[name].isna().any() or not np.isfinite(frame[name].to_numpy(float)).all():
            raise ValueError(f"native V1 ledger has non-finite {name}")
    if (frame[["entry_price", "reference_signal_risk", "exit_price"]] <= 0).any().any():
        raise ValueError("native V1 ledger has non-positive price or risk")
    frame["censored"] = _as_bool(frame["censored"])
    frame["variant"] = "v1_native_long"
    frame["side"] = 1
    frame["initial_risk"] = frame["reference_signal_risk"]
    # Use the same cross-venue contract normalizer as the common ledger.  The
    # archived ``asset`` field preserves exchange multiplier spellings such as
    # 1000PEPE, which must reserve the same underlying slot as PEPE.
    frame["base_asset"] = frame["symbol"].map(base_asset_from_symbol)
    if frame["base_asset"].eq("").any():
        raise ValueError("native V1 ledger has missing asset")
    frame["mfe_r"] = frame["mfe_return"] / (frame["reference_signal_risk"] / frame["entry_price"])
    frame["segment"] = "native_v1"
    frame["year_block"] = frame["entry_time"].dt.year.astype(str)
    frame["account_exit_time"] = frame["exit_time"]
    equal_exit = frame["exit_time"].eq(frame["entry_time"])
    frame.loc[equal_exit, "account_exit_time"] += pd.Timedelta(nanoseconds=1)
    frame["trade_id"] = frame["event_id"].astype(str)
    if frame["trade_id"].duplicated().any():
        raise ValueError("native V1 event_id must be unique")
    frame["source_contract"] = "original_v1_native_exit_not_fair_common_execution"
    frame.attrs["source_path"] = str(path)
    frame.attrs["source_sha256"] = observed
    return frame


def select_scope(
    trades: pd.DataFrame,
    *,
    arm: str,
    venue_scope: str,
    timeframe: int | str,
) -> pd.DataFrame:
    """Select one frozen arm, venue scope, and listed timeframe without tuning."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; expected one of {sorted(ARMS)}")
    if venue_scope not in DEFAULT_VENUE_SCOPES:
        raise ValueError(f"unknown venue scope {venue_scope!r}")
    if timeframe != "all":
        try:
            timeframe = int(timeframe)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid timeframe {timeframe!r}") from error
        if timeframe not in (30, 60, 240):
            raise ValueError("timeframe must be 30, 60, 240, or 'all'")
    selected = trades.loc[trades["variant"].eq(ARMS[arm])].copy()
    if venue_scope == "okx":
        selected = selected.loc[selected["venue"].str.lower().eq("okx")].copy()
    if timeframe != "all":
        selected = selected.loc[selected["timeframe_min"].eq(timeframe)].copy()
    if selected.empty:
        raise ValueError(f"scope has no candidates: {arm}/{venue_scope}/{timeframe}")
    return selected.sort_values(["entry_time", "trade_id"], kind="mergesort").reset_index(drop=True)


def select_period_scope(trades: pd.DataFrame, period_scope: str) -> pd.DataFrame:
    """Apply the frozen entry-time split; each selected period starts a new account.

    ``full`` retains the complete pinned ledger.  Development is
    ``[2024-09-10, 2025-09-10)`` and validation is
    ``[2025-09-10, 2026-09-10)``.  A development trade that remains open at
    the split becomes a zero-PnL boundary censor, so its later outcome cannot
    enter development selection.
    """
    if period_scope == "full":
        selected = trades.copy()
    elif period_scope == "development":
        selected = trades.loc[
            trades["entry_time"].ge(DEVELOPMENT_START) & trades["entry_time"].lt(VALIDATION_START)
        ].copy()
        crosses_boundary = selected["exit_time"].gt(VALIDATION_START)
        selected["period_boundary_censored"] = crosses_boundary
        # The boundary mark has no realized PnL.  Original exit/PnL fields
        # remain only in source metadata, outside the engine input's outcome
        # handling, so a later exit cannot change development equity.
        selected.loc[crosses_boundary, "censored"] = True
        selected.loc[crosses_boundary, "account_exit_time"] = VALIDATION_START
    elif period_scope == "validation":
        selected = trades.loc[
            trades["entry_time"].ge(VALIDATION_START) & trades["entry_time"].lt(VALIDATION_END)
        ].copy()
    else:
        raise ValueError(f"unknown period scope {period_scope!r}")
    if selected.empty:
        raise ValueError(f"period scope has no candidates: {period_scope}")
    if "period_boundary_censored" not in selected:
        selected["period_boundary_censored"] = False
    return selected.copy()


def _max_drawdown(curve: pd.DataFrame, initial_balance: float) -> tuple[float, float]:
    if curve.empty:
        return 0.0, 0.0
    balances = pd.concat([pd.Series([initial_balance]), curve["balance"].astype(float)], ignore_index=True)
    peaks = balances.cummax()
    drawdowns = peaks - balances
    maximum = float(drawdowns.max())
    denominator = float(peaks.loc[drawdowns.idxmax()]) if len(drawdowns) else initial_balance
    return maximum, maximum / denominator if denominator else 0.0


def _run_summary(
    result: Mapping[str, Any],
    *,
    run_id: str,
    arm: str,
    venue_scope: str,
    timeframe: int | str,
    period_scope: str,
    source_contract: str,
    period_boundary_censored: int,
    seed: int | str,
) -> dict[str, Any]:
    summary = dict(result["summary"])
    ledger = result["ledger"]
    selected = ledger.loc[ledger["selected"]].copy() if len(ledger) else ledger.copy()
    realized = selected.loc[~selected["boundary_mark"]].copy() if len(selected) else selected
    positive = realized.loc[realized["realized_pnl"].gt(0), "realized_pnl"]
    max_pnl = float(positive.max()) if len(positive) else 0.0
    positive_total = float(positive.sum()) if len(positive) else 0.0
    mdd_dollars, mdd_fraction = _max_drawdown(result["equity_curve"], float(summary["initial_balance"]))
    curve = result["equity_curve"]
    reached = curve.loc[curve["balance"].ge(100_000)] if len(curve) else curve
    reason_counts = summary.pop("rejection_reasons", {})
    row: dict[str, Any] = {
        "run_id": run_id,
        "source_arm": arm,
        "venue_scope": venue_scope,
        "timeframe": str(timeframe),
        "period_scope": period_scope,
        "source_contract": source_contract,
        "period_boundary_censored": int(period_boundary_censored),
        "seed": str(seed),
        "return_multiple": float(summary["final_balance"]) / float(summary["initial_balance"]),
        "realized_pnl": float(summary["net_pnl"]),
        "max_drawdown_dollars": mdd_dollars,
        "max_drawdown_fraction": mdd_fraction,
        "reached_100k": bool(len(reached)),
        "reached_100k_time": reached["time"].iloc[0].isoformat() if len(reached) else None,
        "ten_r_winners": int(realized["exit_r_multiple"].ge(10).sum()) if len(realized) else 0,
        "max_single_positive_pnl": max_pnl,
        "max_positive_pnl_concentration": max_pnl / positive_total if positive_total else 0.0,
        **summary,
    }
    row["accepted"] = int(row["selected"])
    for reason, count in sorted(reason_counts.items()):
        row[f"rejected_{reason}"] = int(count)
    return row


def _with_run_context(table: pd.DataFrame, context: Mapping[str, object]) -> pd.DataFrame:
    result = table.copy()
    for name, value in context.items():
        result[name] = value
    return result


def run_account_growth_study(
    output_dir: Path,
    *,
    source_path: Path = COMMON_EXECUTION_PATH,
    expected_sha256: str = COMMON_EXECUTION_SHA256,
    native_source_path: Path = NATIVE_V1_PATH,
    native_expected_sha256: str = NATIVE_V1_SHA256,
    arms: Iterable[str] = tuple(ARMS),
    venue_scopes: Iterable[str] = DEFAULT_VENUE_SCOPES,
    timeframes: Iterable[int | str] = DEFAULT_TIMEFRAMES,
    period_scopes: Iterable[str] = DEFAULT_PERIOD_SCOPES,
    sizings: Iterable[str] = DEFAULT_SIZINGS,
    risk_fractions: Iterable[float] = DEFAULT_RISK_FRACTIONS,
    initial_balance: float = 1000.0,
    portfolio_risk_cap: float = 0.10,
    gross_leverage_cap: float = 3.0,
    entry_floor_fraction: float = 0.20,
    seed: int | str = 0,
) -> pd.DataFrame:
    """Run the fixed account grid and write one immutable-style result bundle.

    ``all`` is a truly shared cross-timeframe account; it is not the sum of
    three independent accounts.  Outputs include all candidates in the
    rejection ledger so portfolio-cap omissions remain auditable.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    arms = tuple(arms)
    venue_scopes = tuple(venue_scopes)
    timeframes = tuple(timeframes)
    period_scopes = tuple(period_scopes)
    sizings = tuple(sizings)
    risk_fractions = tuple(float(value) for value in risk_fractions)
    needs_common = any(arm != "v1_native_long" for arm in arms)
    needs_native = "v1_native_long" in arms
    sources: list[pd.DataFrame] = []
    source_receipts: dict[str, dict[str, object]] = {}
    if needs_common:
        common = load_common_execution_trades(Path(source_path), expected_sha256=expected_sha256)
        sources.append(common)
        source_receipts["common_execution"] = {
            "path": common.attrs["source_path"], "sha256": common.attrs["source_sha256"], "rows": int(len(common)),
        }
    if needs_native:
        native = load_native_v1_trades(Path(native_source_path), expected_sha256=native_expected_sha256)
        sources.append(native)
        source_receipts["v1_native"] = {
            "path": native.attrs["source_path"], "sha256": native.attrs["source_sha256"], "rows": int(len(native)),
        }
    source = pd.concat(sources, ignore_index=True)
    rows: list[dict[str, Any]] = []
    ledgers: list[pd.DataFrame] = []
    curves: list[pd.DataFrame] = []
    dailies: list[pd.DataFrame] = []
    run_number = 0
    for arm in arms:
        for venue_scope in venue_scopes:
            for timeframe in timeframes:
                scoped_all = select_scope(source, arm=arm, venue_scope=venue_scope, timeframe=timeframe)
                for period_scope in period_scopes:
                    scoped = select_period_scope(scoped_all, period_scope)
                    engine_columns = [
                        "trade_id", "entry_time", "exit_time", "base_asset", "entry_price",
                        "initial_risk", "side", "net_return", "censored",
                    ]
                    # Exit-only metadata below never enters the engine input:
                    # it is joined back after the causal account replay.
                    engine_input = scoped.assign(exit_time=scoped["account_exit_time"]).loc[:, engine_columns]
                    source_meta_columns = [
                        "variant", "venue", "symbol", "timeframe_min", "signal_bar_open", "source_row",
                        "exit_time", "entry_price", "initial_risk", "exit_price", "exit_reason",
                        "net_return", "net_r", "mfe_r", "segment", "year_block", "censored",
                        "period_boundary_censored",
                    ]
                    missing_meta = set(source_meta_columns) - set(scoped.columns)
                    if missing_meta:
                        raise ValueError(f"source ledger missing audit metadata: {sorted(missing_meta)}")
                    source_meta = scoped.set_index("trade_id")[source_meta_columns]
                    for sizing in sizings:
                        for risk_fraction in risk_fractions:
                            run_number += 1
                            run_id = f"{run_number:04d}_{arm}_{venue_scope}_{timeframe}_{period_scope}_{sizing}_{int(round(float(risk_fraction) * 100))}pct"
                            result = simulate_shared_account(
                                engine_input,
                                sizing=str(sizing),
                                risk_fraction=float(risk_fraction),
                                initial_balance=float(initial_balance),
                                portfolio_risk_cap=float(portfolio_risk_cap),
                                gross_leverage_cap=float(gross_leverage_cap),
                                entry_floor_fraction=float(entry_floor_fraction),
                                seed=seed,
                            )
                            context = {
                                "run_id": run_id, "source_arm": arm, "venue_scope": venue_scope,
                                "timeframe": str(timeframe), "period_scope": period_scope,
                                "source_contract": ARM_SOURCE_CONTRACT[arm], "sizing": str(sizing),
                                "risk_fraction": float(risk_fraction), "seed": str(seed),
                            }
                            rows.append(_run_summary(result, run_id=run_id, arm=arm, venue_scope=venue_scope,
                                                     timeframe=timeframe, period_scope=period_scope,
                                                     source_contract=ARM_SOURCE_CONTRACT[arm], seed=seed,
                                                     period_boundary_censored=int(scoped["period_boundary_censored"].sum())))
                            renamed_meta = source_meta.rename(columns={
                                name: f"source_{name}" for name in source_meta.columns
                                if name in set(result["ledger"].columns)
                            })
                            ledger = result["ledger"].join(renamed_meta, on="trade_id", how="left")
                            ledgers.append(_with_run_context(ledger, context))
                            curves.append(_with_run_context(result["equity_curve"], context))
                            dailies.append(_with_run_context(result["daily_realized_pnl"], context))

    summary = pd.DataFrame(rows)
    all_ledgers = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    accepted = all_ledgers.loc[all_ledgers["selected"]].copy() if len(all_ledgers) else all_ledgers
    rejected = all_ledgers.loc[
        ~all_ledgers["selected"] & all_ledgers["period_scope"].eq("full")
    ].copy() if len(all_ledgers) else all_ledgers
    all_curves = pd.concat(curves, ignore_index=True) if curves else pd.DataFrame()
    all_dailies = pd.concat(dailies, ignore_index=True) if dailies else pd.DataFrame()
    summary.to_csv(output_dir / "summary.csv", index=False)
    accepted.to_csv(output_dir / "accepted_ledger.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    rejected.to_csv(output_dir / "rejections.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    all_curves.to_csv(output_dir / "equity_curve.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    all_dailies.to_csv(output_dir / "daily_realized_pnl.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    source_time_range = {
        "entry_time_min": source["entry_time"].min().isoformat(),
        "entry_time_max": source["entry_time"].max().isoformat(),
    }
    output_row_counts = {
        "summary": int(len(summary)), "accepted_ledger": int(len(accepted)),
        "rejections": int(len(rejected)), "equity_curve": int(len(all_curves)),
        "daily_realized_pnl": int(len(all_dailies)),
    }
    manifest = {
        "source_rows": int(len(source)), "source_time_range": source_time_range,
        "sources": source_receipts, "source_contracts": ARM_SOURCE_CONTRACT,
        "module_sha256": sha256_file(Path(__file__)),
        "account_engine_sha256": sha256_file(Path(__file__).with_name("spike_account_growth.py")),
        "arms": list(arms), "venue_scopes": list(venue_scopes), "timeframes": list(timeframes),
        "period_scopes": list(period_scopes),
        "sizings": list(sizings), "risk_fractions": [float(value) for value in risk_fractions],
        "initial_balance": float(initial_balance), "portfolio_risk_cap": float(portfolio_risk_cap),
        "gross_leverage_cap": float(gross_leverage_cap), "entry_floor_fraction": float(entry_floor_fraction),
        "seed": str(seed), "runs": int(len(summary)),
        "same_timestamp_exit_accounting_adjustments": int(source["account_exit_time"].ne(source["exit_time"]).sum()),
        "same_timestamp_exit_adjustment_contract": (
            "For same-timestamp entry/exit rows only, account_exit_time is +1ns so admission happens before "
            "the supplied exit. Source exit_time, prices, net_return, net_r, mfe_r and all realized PnL are unchanged."
        ),
        "detail_retention": {
            "accepted_ledger": "full, development, and validation accepted trades",
            "equity_curve": "full, development, and validation account curves",
            "daily_realized_pnl": "full, development, and validation daily realized PnL",
            "rejections": "full only; development/validation rejection counts remain in summary.csv",
        },
        "period_boundary_censoring": {
            "development_policy": "entry before validation split with supplied exit after split is marked censored at split with zero PnL",
            "summary_total_across_runs": int(summary["period_boundary_censored"].sum()),
        },
        "output_row_counts": output_row_counts,
        "outputs": {
            name: sha256_file(output_dir / name) for name in (
                "summary.csv", "accepted_ledger.csv.gz", "rejections.csv.gz",
                "equity_curve.csv.gz", "daily_realized_pnl.csv.gz",
            )
        },
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the complete preregistered grid from the command line."""
    parser = argparse.ArgumentParser(description="Replay the frozen SPIKE V1/V7 common ledger as one account.")
    parser.add_argument("--output", type=Path, required=True, help="New or empty output directory for this run.")
    parser.add_argument("--source", type=Path, default=COMMON_EXECUTION_PATH, help="Pinned common-execution CSV.GZ.")
    parser.add_argument("--expected-sha256", default=COMMON_EXECUTION_SHA256, help="Required source file SHA-256.")
    parser.add_argument("--native-source", type=Path, default=NATIVE_V1_PATH, help="Pinned original-V1 CSV.GZ.")
    parser.add_argument("--native-expected-sha256", default=NATIVE_V1_SHA256, help="Required native V1 source SHA-256.")
    parser.add_argument("--seed", default="0", help="Stable same-timestamp ordering seed.")
    args = parser.parse_args(argv)
    run_account_growth_study(
        args.output, source_path=args.source, expected_sha256=args.expected_sha256,
        native_source_path=args.native_source, native_expected_sha256=args.native_expected_sha256,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
