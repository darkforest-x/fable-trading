"""Synchronized equal-weight ALT-basket trailing exits for the Monday study.

Each basket is the frozen initial ALT membership for one locked event.  The
signal is computed from the equal-weight mean of member close returns against
the same event's entry open.  Only completed 5m closes can trigger an exit;
the exit fill is the next 5m open.  No per-member peak is averaged to create
the basket high-water mark.  Missing member bars invalidate the full basket
path rather than being imputed.

Inputs are the locked ``dynamic_primary_ALT`` and ``wave_ALT`` event ledgers,
the frozen 30m initial-member bridge, and the original Sep-16 68-member asset
list.  Prices are read once per symbol through the registered local Binance
USD-M source API (REST repair disabled).  VectorBT prices every member's
independent equal-notional leg with two taker fills; funding, leverage,
slippage, market impact, and capacity are not modeled.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_payoffs as payoffs
from yoyo.evaluation.monday_breadth_constituent_scope import exclusion_reason


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
EXITS = EXPERIMENT / "exits"
FEATURES = EXPERIMENT / "features"
MEMBERS_PATH = FEATURES / "members.csv.gz"
DYNAMIC_EVENTS_PATH = EXITS / "locked_events.parquet"
WAVE_EVENTS_PATH = EXITS / "second_wave_requests/locked_events.parquet"
WAVE_RULES_PATH = EXITS / "second_wave_requests/locked_rules.csv"
SEPT16_SOURCE_PATH = EXITS / "sept16_original_funded.parquet"
BAR = pd.Timedelta(minutes=5)
BAR_COUNT = 144 * 12
OPEN_COUNT = BAR_COUNT + 1
MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
CONFIGS = tuple(
    {
        "activate_pct": float(activate),
        "trail_width_pct": float(width),
        "initial_stop_pct": None if stop is None else float(stop),
        "config_id": f"a{activate:g}_t{width:g}_s{'none' if stop is None else f'{stop:g}' }",
    }
    for activate, width, stop in itertools.product((5, 10, 20), (4, 8, 12), (None, 6, 12))
)


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _member_set_id(symbols: tuple[str, ...]) -> str:
    payload = "\n".join(symbols).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _event_id(cohort: str, decision: pd.Timestamp, set_id: str) -> str:
    return f"{cohort}_{decision.strftime('%Y%m%dT%H%MZ')}_{set_id}"


def _member_lookup() -> dict[tuple[int, int], pd.DataFrame]:
    members = pd.read_csv(MEMBERS_PATH, compression="gzip")
    members["known"] = pd.to_datetime(members["known"], utc=True, errors="coerce")
    members = members.loc[members["minutes"].eq(30)].copy()
    members = members.loc[members["known"].notna()]
    result: dict[tuple[int, int], pd.DataFrame] = {}
    for (minutes, known), group in members.groupby(["minutes", "known"], sort=False):
        result[(int(minutes), int(known.value))] = group.reset_index(drop=True)
    return result


def _load_events() -> tuple[list[dict[str, Any]], pd.DataFrame, dict[str, Any]]:
    lookup = _member_lookup()
    dynamic = pd.read_parquet(DYNAMIC_EVENTS_PATH)
    dynamic = dynamic.loc[dynamic["candidate_id"].eq("dynamic_primary_ALT") & dynamic["asset"].eq("ALT")].copy()
    dynamic["cohort"] = "dynamic_primary_ALT"
    dynamic["minutes"] = pd.to_numeric(dynamic["minutes"], errors="raise").astype(int)
    dynamic["wait_min"] = ((pd.to_datetime(dynamic["decision"], utc=True) - pd.to_datetime(dynamic["known"], utc=True)).dt.total_seconds() / 60).astype(int)

    wave = pd.read_parquet(WAVE_EVENTS_PATH)
    wave = wave.loc[wave["candidate_id"].eq("wave_ALT") & wave["asset"].eq("ALT")].copy()
    wave_rules = pd.read_csv(WAVE_RULES_PATH)
    wave_rule = wave_rules.loc[wave_rules["candidate_id"].eq("wave_ALT")]
    if len(wave_rule) != 1:
        raise AssertionError(f"expected one locked wave_ALT rule, found {len(wave_rule)}")
    wave_minutes = int(wave_rule.iloc[0]["minutes"])
    wave["cohort"] = "wave_ALT"
    wave["minutes"] = wave_minutes
    wave["wait_min"] = ((pd.to_datetime(wave["decision"], utc=True) - pd.to_datetime(wave["known"], utc=True)).dt.total_seconds() / 60).astype(int)

    all_events = pd.concat([dynamic, wave], ignore_index=True, sort=False)
    all_events["known"] = pd.to_datetime(all_events["known"], utc=True)
    all_events["decision"] = pd.to_datetime(all_events["decision"], utc=True)
    events: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], int]] = set()
    duplicate_keys = 0
    initial_mismatch = []

    for row in all_events.sort_values(["decision", "cohort"], kind="stable").itertuples(index=False):
        key = (int(row.minutes), int(row.known.value))
        group = lookup.get(key)
        if group is None or group.empty:
            initial_mismatch.append({"cohort": row.cohort, "known": row.known.isoformat(), "reason": "no_frozen_members"})
            continue
        raw_symbols = tuple(sorted(set(group["symbol"].astype(str).str.upper())))
        if len(raw_symbols) != len(group) or len(raw_symbols) != int(row.initial):
            initial_mismatch.append(
                {
                    "cohort": row.cohort,
                    "known": row.known.isoformat(),
                    "expected_initial": int(row.initial),
                    "frozen_member_rows": int(len(group)),
                    "unique_symbols": len(raw_symbols),
                    "reason": "initial_membership_mismatch",
                }
            )
            continue
        group = group.copy()
        group["symbol"] = group["symbol"].astype(str).str.upper()
        group["exclusion_reason"] = group["symbol"].map(exclusion_reason)
        group["included_in_basket"] = group["exclusion_reason"].eq("")
        eligible_group = group.loc[group["included_in_basket"]].copy()
        symbols = tuple(sorted(eligible_group["symbol"].astype(str)))
        if not symbols or len(symbols) != len(eligible_group):
            initial_mismatch.append(
                {
                    "cohort": row.cohort,
                    "known": row.known.isoformat(),
                    "raw_initial": int(row.initial),
                    "eligible_member_rows": int(len(eligible_group)),
                    "unique_eligible_symbols": len(symbols),
                    "reason": "eligible_basket_membership_mismatch",
                }
            )
            continue
        identity = (symbols, int(row.decision.value))
        if identity in seen:
            duplicate_keys += 1
            continue
        seen.add(identity)
        set_id = _member_set_id(symbols)
        item = {
            "event_id": _event_id(str(row.cohort), row.decision, set_id),
            "cohort": str(row.cohort),
            "candidate_id": str(row.candidate_id),
            "known": row.known,
            "decision": row.decision,
            "epoch": int(row.epoch),
            "minutes": int(row.minutes),
            "wait_min": int(row.wait_min),
            "initial_n": int(row.initial),
            "n_members": len(symbols),
            "n_excluded_from_basket": int(len(group) - len(eligible_group)),
            "excluded_members": tuple(sorted(group.loc[~group["included_in_basket"], "symbol"].astype(str))),
            "member_set_id": set_id,
            "symbols": symbols,
            "source_event_i": getattr(row, "event_i", np.nan),
            "close_sum": np.zeros(BAR_COUNT, dtype=np.float64),
            "close_count": np.zeros(BAR_COUNT, dtype=np.int16),
            "member_open_paths": np.full((len(symbols), OPEN_COUNT), np.nan, dtype=np.float64),
            "member_full_valid": np.zeros(len(symbols), dtype=bool),
            "member_max_close_pct": np.full(len(symbols), np.nan, dtype=np.float64),
        }
        events.append(item)
        for member in group.itertuples(index=False):
            member_rows.append(
                {
                    "event_id": item["event_id"],
                    "cohort": item["cohort"],
                    "candidate_id": item["candidate_id"],
                    "member_set_id": set_id,
                    "known": row.known,
                    "decision": row.decision,
                    "symbol": str(member.symbol).upper(),
                    "initial_n": int(row.initial),
                    "basket_n": len(symbols),
                    "included_in_basket": bool(member.included_in_basket),
                    "exclusion_reason": str(member.exclusion_reason),
                    "lower_touch_level": getattr(member, "lower_touch_level", np.nan),
                    "level_source": getattr(member, "level_source", ""),
                }
            )

    # Sep-16 is a separate explicitly requested snapshot: use its exact
    # original 5m lower-side 68-symbol basket from the frozen member-fill ledger.
    sep = pd.read_parquet(SEPT16_SOURCE_PATH)
    sep_key = pd.Timestamp("2026-09-15T19:20:00Z")
    sep_symbols = tuple(sorted(set(sep.loc[sep["entry_time"].eq(sep_key), "asset"].astype(str).str.upper()) - MAJORS))
    if len(sep_symbols) != 68:
        raise AssertionError(f"expected original Sep-16 68 ALT symbols, found {len(sep_symbols)}")
    sep_set_id = _member_set_id(sep_symbols)
    sep_event = {
        "event_id": _event_id("sept16_original_68", sep_key, sep_set_id),
        "cohort": "sept16_original_68",
        "candidate_id": "sept16_original_68",
        "known": sep_key,
        "decision": sep_key,
        "epoch": 3,
        "minutes": 5,
        "wait_min": 0,
        "initial_n": len(sep_symbols),
        "n_members": len(sep_symbols),
        "n_excluded_from_basket": 0,
        "excluded_members": (),
        "member_set_id": sep_set_id,
        "symbols": sep_symbols,
        "source_event_i": np.nan,
        "close_sum": np.zeros(BAR_COUNT, dtype=np.float64),
        "close_count": np.zeros(BAR_COUNT, dtype=np.int16),
        "member_open_paths": np.full((len(sep_symbols), OPEN_COUNT), np.nan, dtype=np.float64),
        "member_full_valid": np.zeros(len(sep_symbols), dtype=bool),
        "member_max_close_pct": np.full(len(sep_symbols), np.nan, dtype=np.float64),
    }
    events.append(sep_event)
    for symbol in sep_symbols:
        member_rows.append(
            {
                "event_id": sep_event["event_id"],
                "cohort": sep_event["cohort"],
                "candidate_id": sep_event["candidate_id"],
                "member_set_id": sep_set_id,
                "known": sep_key,
                "decision": sep_key,
                "symbol": symbol,
                "initial_n": len(sep_symbols),
                "basket_n": len(sep_symbols),
                "included_in_basket": True,
                "exclusion_reason": "",
                "lower_touch_level": np.nan,
                "level_source": "sept16_original_funded_unique_assets",
            }
        )

    eval_events = [e for e in events if e["cohort"] != "sept16_original_68"]
    if len(eval_events) != 170:
        raise AssertionError(f"expected 170 de-duplicated locked ALT events, found {len(eval_events)}")
    if initial_mismatch:
        raise AssertionError(f"locked initial membership mismatch: {initial_mismatch[:4]}")
    metadata = {
        "dynamic_primary_ALT_rows": int(len(dynamic)),
        "wave_ALT_rows": int(len(wave)),
        "locked_events_after_dedup": len(eval_events),
        "duplicate_member_set_decision_keys_removed": duplicate_keys,
        "unique_study_symbols_including_sep16": len(set().union(*(set(e["symbols"]) for e in events))),
        "membership_rows_including_sep16": sum(int(e["initial_n"]) for e in events),
        "basket_member_rows_including_sep16": sum(int(e["n_members"]) for e in events),
        "excluded_member_rows_including_sep16": sum(int(e["n_excluded_from_basket"]) for e in events),
        "sep16_members": len(sep_symbols),
        "sep16_decision": sep_key.isoformat(),
    }
    return events, pd.DataFrame(member_rows), metadata


def _load_prices_once_per_symbol(events: list[dict[str, Any]]) -> pd.DataFrame:
    by_symbol: dict[str, list[tuple[dict[str, Any], int]]] = {}
    for event in events:
        for member_index, symbol in enumerate(event["symbols"]):
            by_symbol.setdefault(symbol, []).append((event, member_index))

    receipts: list[dict[str, Any]] = []
    total = len(by_symbol)
    start_clock = time.monotonic()
    for ordinal, (symbol, bindings) in enumerate(sorted(by_symbol.items()), 1):
        starts = [binding[0]["decision"] for binding in bindings]
        ends = [binding[0]["decision"] + pd.Timedelta(hours=144) for binding in bindings]
        start, end = min(starts), max(ends)
        try:
            frame, receipt = payoffs.load_full_ohlc(symbol, start, end, repair_missing=False)
        except Exception as exc:  # Preserve the symbol as explicitly unavailable.
            receipts.append(
                {
                    "symbol": symbol,
                    "status": "load_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "rows": 0,
                    "local_source_count": 0,
                    "observed_start_utc": None,
                    "observed_end_utc": None,
                }
            )
            if ordinal % 50 == 0 or ordinal == total:
                print(f"prices {ordinal}/{total}; elapsed={time.monotonic()-start_clock:.1f}s", flush=True)
            continue

        receipts.append(
            {
                "symbol": symbol,
                "status": "loaded" if len(frame) else "empty",
                "error": "",
                "rows": int(len(frame)),
                "local_source_count": int(len(receipt.get("local_sources", []))),
                "excluded_source_count": int(len(receipt.get("excluded_source_lineage", []))),
                "observed_start_utc": receipt.get("observed_range_utc", {}).get("start"),
                "observed_end_utc": receipt.get("observed_range_utc", {}).get("end"),
                "requested_start_utc": start.isoformat(),
                "requested_end_utc": end.isoformat(),
            }
        )
        index = frame.index
        for event, member_index in bindings:
            decision = event["decision"]
            open_index = pd.date_range(decision, periods=OPEN_COUNT, freq=BAR)
            close_index = open_index[:-1]
            opens = frame["open"].reindex(open_index).to_numpy(dtype=np.float64, copy=False)
            closes = frame["close"].reindex(close_index).to_numpy(dtype=np.float64, copy=False)
            event["member_open_paths"][member_index, :] = opens
            entry = opens[0]
            if not np.isfinite(entry) or entry <= 0:
                continue
            close_returns = closes / entry - 1.0
            valid_close = np.isfinite(close_returns)
            if valid_close.any():
                event["close_sum"][valid_close] += close_returns[valid_close]
                event["close_count"][valid_close] += 1
                event["member_max_close_pct"][member_index] = float(np.nanmax(close_returns) * 100.0)
            event["member_full_valid"][member_index] = bool(np.isfinite(opens).all() and valid_close.all())

        if ordinal % 50 == 0 or ordinal == total:
            print(f"prices {ordinal}/{total}; elapsed={time.monotonic()-start_clock:.1f}s", flush=True)
    return pd.DataFrame(receipts)


def _path_summary(event: dict[str, Any]) -> dict[str, Any]:
    n = int(event["n_members"])
    valid_close_counts = event["close_count"]
    basket = np.full(BAR_COUNT, np.nan, dtype=np.float64)
    valid = valid_close_counts.eq(n) if isinstance(valid_close_counts, pd.Series) else (valid_close_counts == n)
    basket[valid] = event["close_sum"][valid] / n
    open_paths = event["member_open_paths"]
    entry_valid = np.isfinite(open_paths[:, 0]) & (open_paths[:, 0] > 0)
    full_members = int(event["member_full_valid"].sum())
    complete = bool(full_members == n and entry_valid.all() and valid.all())
    min_valid_close_bars = int(valid_close_counts.min()) if len(valid_close_counts) else 0
    missing_member_bars = int(n * BAR_COUNT - valid_close_counts.sum())
    if valid.any():
        valid_ids = np.flatnonzero(valid)
        max_id = int(valid_ids[np.argmax(basket[valid])])
        min_id = int(valid_ids[np.argmin(basket[valid])])
        max_pct = float(basket[max_id] * 100.0)
        min_pct = float(basket[min_id] * 100.0)
        max_time = event["decision"] + BAR * (max_id + 1)
        min_time = event["decision"] + BAR * (min_id + 1)
    else:
        max_id = min_id = -1
        max_pct = min_pct = np.nan
        max_time = min_time = pd.NaT
    member_peaks = event["member_max_close_pct"]
    mean_individual_peak = float(np.nanmean(member_peaks)) if np.isfinite(member_peaks).any() else np.nan
    return {
        "complete": complete,
        "full_members": full_members,
        "missing_members": n - full_members,
        "missing_member_close_bars": missing_member_bars,
        "minimum_members_per_close": min_valid_close_bars,
        "basket_close_returns": basket,
        "basket_max_close_pct": max_pct,
        "basket_max_close_time": max_time,
        "basket_min_close_pct": min_pct,
        "basket_min_close_time": min_time,
        "mean_individual_max_close_pct": mean_individual_peak,
        "member_max_close_pct": member_peaks.copy(),
        "entry_prices": open_paths[:, 0].copy(),
    }


def _simulate_exit(close_returns: np.ndarray, config: dict[str, Any]) -> tuple[int, int, str, float]:
    """Return next-open index, trigger-close index, reason, and peak close pct.

    ``close_returns[k]`` is known at the close of the bar opened at decision +
    k*5m.  The returned open index is always k+1, so no same-close fill or
    intrabar path assumption enters the rule.
    """
    high_water = -np.inf
    for close_index, value in enumerate(close_returns):
        if not np.isfinite(value):
            continue
        if value > high_water:
            high_water = float(value)
        stop = config["initial_stop_pct"]
        if stop is not None and value <= -float(stop) / 100.0:
            return close_index + 1, close_index, "initial_basket_stop", high_water * 100.0
        if high_water >= float(config["activate_pct"]) / 100.0:
            drawdown = high_water - value
            if drawdown >= float(config["trail_width_pct"]) / 100.0:
                return close_index + 1, close_index, "basket_high_water_trail", high_water * 100.0
    return BAR_COUNT, BAR_COUNT - 1, "fixed_144h_timeout", high_water * 100.0


def _event_identity(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "cohort": event["cohort"],
        "candidate_id": event["candidate_id"],
        "member_set_id": event["member_set_id"],
        "known": event["known"],
        "decision": event["decision"],
        "epoch": event["epoch"],
        "minutes": event["minutes"],
        "wait_min": event["wait_min"],
        "initial_n": event["initial_n"],
        "n_members": event["n_members"],
        "n_excluded_from_basket": event["n_excluded_from_basket"],
    }


def _calculate_event_returns(events: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries = {event["event_id"]: _path_summary(event) for event in events}
    complete = [event for event in events if summaries[event["event_id"]]["complete"]]
    baseline_records: list[dict[str, Any]] = []
    baseline_entry: list[float] = []
    baseline_exit: list[float] = []
    baseline_ranges: list[tuple[int, int]] = []
    for event in complete:
        entry = event["member_open_paths"][:, 0]
        exit_ = event["member_open_paths"][:, BAR_COUNT]
        begin = len(baseline_entry)
        baseline_entry.extend(entry.tolist())
        baseline_exit.extend(exit_.tolist())
        baseline_ranges.append((begin, len(baseline_entry)))
    baseline_legs = payoffs._vbt_net_returns(np.asarray(baseline_entry), np.asarray(baseline_exit))
    for event, (begin, end) in zip(complete, baseline_ranges):
        info = _event_identity(event)
        entry = event["member_open_paths"][:, 0]
        exit_ = event["member_open_paths"][:, BAR_COUNT]
        net_legs = baseline_legs[begin:end]
        info.update(
            {
                "status": "complete",
                "fixed6d_exit_time": event["decision"] + pd.Timedelta(hours=144),
                "fixed6d_gross_pct": float(np.mean(exit_ / entry - 1.0) * 100.0),
                "fixed6d_net_pct": float(np.mean(net_legs)),
                "basket_max_close_pct": summaries[event["event_id"]]["basket_max_close_pct"],
                "basket_max_close_time": summaries[event["event_id"]]["basket_max_close_time"],
                "basket_min_close_pct": summaries[event["event_id"]]["basket_min_close_pct"],
                "basket_min_close_time": summaries[event["event_id"]]["basket_min_close_time"],
                "mean_individual_max_close_pct": summaries[event["event_id"]]["mean_individual_max_close_pct"],
            }
        )
        baseline_records.append(info)
    baseline_df = pd.DataFrame(baseline_records)

    strategy_records: list[dict[str, Any]] = []
    for config in CONFIGS:
        entry_chunks: list[np.ndarray] = []
        exit_chunks: list[np.ndarray] = []
        exit_meta: list[tuple[dict[str, Any], int, int, str, float, int]] = []
        flattened = 0
        for event in complete:
            path = summaries[event["event_id"]]["basket_close_returns"]
            exit_index, trigger_index, reason, high_pct = _simulate_exit(path, config)
            entry = event["member_open_paths"][:, 0]
            exit_ = event["member_open_paths"][:, exit_index]
            entry_chunks.append(entry)
            exit_chunks.append(exit_)
            exit_meta.append((event, flattened, flattened + event["n_members"], reason, high_pct, exit_index))
            flattened += event["n_members"]
        if not flattened:
            continue
        net_legs = payoffs._vbt_net_returns(np.concatenate(entry_chunks), np.concatenate(exit_chunks))
        for event, begin, end, reason, high_pct, exit_index in exit_meta:
            info = _event_identity(event)
            summary = summaries[event["event_id"]]
            entry = event["member_open_paths"][:, 0]
            exit_ = event["member_open_paths"][:, exit_index]
            base = baseline_df.loc[baseline_df.event_id.eq(event["event_id"])].iloc[0]
            info.update(
                {
                    "config_id": config["config_id"],
                    "activate_pct": config["activate_pct"],
                    "trail_width_pct": config["trail_width_pct"],
                    "initial_stop_pct": config["initial_stop_pct"],
                    "exit_reason": reason,
                    "trigger_bar_open_time": event["decision"] + BAR * (exit_index - 1),
                    "trigger_close_time": event["decision"] + BAR * exit_index,
                    "exit_time": event["decision"] + BAR * exit_index,
                    "exit_open_index": exit_index,
                    "basket_peak_close_pct_at_exit": high_pct,
                    "basket_gross_pct": float(np.mean(exit_ / entry - 1.0) * 100.0),
                    "basket_net_pct": float(np.mean(net_legs[begin:end])),
                    "fixed6d_net_pct": float(base.fixed6d_net_pct),
                    "paired_delta_net_pct": float(np.mean(net_legs[begin:end]) - base.fixed6d_net_pct),
                    "basket_max_close_pct": summary["basket_max_close_pct"],
                    "basket_max_close_time": summary["basket_max_close_time"],
                    "basket_min_close_pct": summary["basket_min_close_pct"],
                    "mean_individual_max_close_pct": summary["mean_individual_max_close_pct"],
                }
            )
            strategy_records.append(info)
    return baseline_df, pd.DataFrame(strategy_records)


def _serial_max_drawdown(returns_pct: pd.Series) -> float:
    values = pd.to_numeric(returns_pct, errors="coerce").dropna().to_numpy(dtype=np.float64) / 100.0
    if not len(values):
        return np.nan
    equity = np.cumprod(1.0 + values)
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    return float(np.min(equity / peak - 1.0) * 100.0)


def _month_labels(decisions: pd.Series) -> pd.Series:
    return pd.to_datetime(decisions, utc=True).dt.tz_localize(None).dt.to_period("M").astype(str)


def _selection_table(strategy_df: pd.DataFrame, input_events: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if strategy_df.empty:
        raise AssertionError("no complete basket paths available for parameter selection")
    decision_epoch = {event["event_id"]: event["decision"] for event in input_events}
    d = strategy_df.copy()
    d["decision"] = d["event_id"].map(decision_epoch)
    d["utc_month"] = _month_labels(d["decision"])
    pre = d.loc[d["decision"].lt(pd.Timestamp("2026-01-01", tz="UTC"))].copy()
    rows: list[dict[str, Any]] = []
    for (cohort, config_id), group in pre.groupby(["cohort", "config_id"], sort=True):
        month = group.groupby("utc_month", sort=True)["paired_delta_net_pct"].mean()
        base = group.groupby("event_id", sort=False).first()
        rows.append(
            {
                "cohort": cohort,
                "config_id": config_id,
                "activate_pct": float(group.activate_pct.iloc[0]),
                "trail_width_pct": float(group.trail_width_pct.iloc[0]),
                "initial_stop_pct": group.initial_stop_pct.iloc[0],
                "n_pre2026_events": int(group.event_id.nunique()),
                "n_pre2026_month_blocks": int(month.size),
                "pre2026_mean_event_delta_pct": float(group.paired_delta_net_pct.mean()),
                "pre2026_median_event_delta_pct": float(group.paired_delta_net_pct.median()),
                "pre2026_win_vs_fixed_pct": float((group.paired_delta_net_pct > 0).mean() * 100.0),
                "pre2026_mean_month_delta_pct": float(month.mean()) if len(month) else np.nan,
                "pre2026_median_month_delta_pct": float(month.median()) if len(month) else np.nan,
                "pre2026_q25_month_delta_pct": float(month.quantile(0.25)) if len(month) else np.nan,
                "pre2026_worst_month_delta_pct": float(month.min()) if len(month) else np.nan,
                "pre2026_serial_event_max_dd_pct": _serial_max_drawdown(group.sort_values("decision").basket_net_pct),
                "eligible_for_freeze": bool(group.event_id.nunique() >= 5 and month.size >= 3),
                "selection_rule": "max q25 of UTC-month mean paired net-return deltas; tie-break median month, mean month, then smaller serial event DD",
            }
        )
    table = pd.DataFrame(rows)
    chosen = []
    for cohort, group in table.groupby("cohort", sort=True):
        eligible = group.loc[group.eligible_for_freeze]
        if eligible.empty:
            eligible = group
        best = eligible.sort_values(
            ["pre2026_q25_month_delta_pct", "pre2026_median_month_delta_pct", "pre2026_mean_month_delta_pct", "pre2026_serial_event_max_dd_pct", "config_id"],
            ascending=[False, False, False, False, True],
            kind="stable",
            na_position="last",
        ).iloc[0]
        chosen.append(best.to_dict())
    return table, pd.DataFrame(chosen)


def _sign_flip_month_p(month_deltas: np.ndarray) -> tuple[float, int, str]:
    values = np.asarray(month_deltas, dtype=np.float64)
    values = values[np.isfinite(values)]
    n = len(values)
    if not n:
        return np.nan, 0, "no_month_blocks"
    observed = abs(float(values.mean()))
    if n <= 20:
        signs = np.asarray(list(itertools.product((-1.0, 1.0), repeat=n)), dtype=np.float64)
        stats = np.abs((signs * values).mean(axis=1))
        p_value = float(np.mean(stats >= observed - 1e-12))
        return p_value, n, "exact_two_sided_month_block_sign_flip"
    rng = np.random.default_rng(20261108)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(200_000, n))
    stats = np.abs((signs * values).mean(axis=1))
    return float((np.count_nonzero(stats >= observed - 1e-12) + 1) / (len(stats) + 1)), n, "200000_draw_month_block_sign_flip_seed20261108"


def _summarize_2026(strategy_df: pd.DataFrame, frozen: pd.DataFrame, all_events: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    epoch3 = pd.DataFrame([_event_identity(e) for e in all_events if e["cohort"] != "sept16_original_68" and e["epoch"] == 3])
    if not epoch3.empty:
        epoch3["decision"] = pd.to_datetime(epoch3["decision"], utc=True)
        epoch3 = epoch3.loc[epoch3.decision.dt.year.eq(2026)]
    selected = []
    monthly_rows = []
    for rule in frozen.itertuples(index=False):
        subset = strategy_df.loc[
            strategy_df.cohort.eq(rule.cohort)
            & strategy_df.config_id.eq(rule.config_id)
            & strategy_df.epoch.eq(3)
            & pd.to_datetime(strategy_df.decision if "decision" in strategy_df.columns else strategy_df.known, utc=True).dt.year.eq(2026)
        ].copy()
        # The input event list, not result availability, defines locked coverage.
        locked = epoch3.loc[epoch3.cohort.eq(rule.cohort)] if not epoch3.empty else pd.DataFrame()
        subset["utc_month"] = _month_labels(subset.decision)
        month_delta = subset.groupby("utc_month", sort=True).paired_delta_net_pct.mean()
        p_value, p_months, p_method = _sign_flip_month_p(month_delta.to_numpy())
        row = {
            "cohort": rule.cohort,
            "config_id": rule.config_id,
            "activate_pct": rule.activate_pct,
            "trail_width_pct": rule.trail_width_pct,
            "initial_stop_pct": rule.initial_stop_pct,
            "locked_epoch3_2026_events": int(locked.event_id.nunique()) if not locked.empty else 0,
            "evaluable_epoch3_2026_events": int(subset.event_id.nunique()),
            "unavailable_epoch3_2026_events": int((locked.event_id.nunique() if not locked.empty else 0) - subset.event_id.nunique()),
            "n_month_blocks": int(p_months),
            "fixed6d_mean_net_pct": float(subset.fixed6d_net_pct.mean()) if len(subset) else np.nan,
            "basket_trail_mean_net_pct": float(subset.basket_net_pct.mean()) if len(subset) else np.nan,
            "mean_paired_delta_net_pct": float(subset.paired_delta_net_pct.mean()) if len(subset) else np.nan,
            "median_paired_delta_net_pct": float(subset.paired_delta_net_pct.median()) if len(subset) else np.nan,
            "win_pct_basket_trail": float((subset.basket_net_pct > 0).mean() * 100.0) if len(subset) else np.nan,
            "pct_events_beating_fixed6d": float((subset.paired_delta_net_pct > 0).mean() * 100.0) if len(subset) else np.nan,
            "trail_serial_event_max_dd_pct": _serial_max_drawdown(subset.sort_values("decision").basket_net_pct) if len(subset) else np.nan,
            "fixed6d_serial_event_max_dd_pct": _serial_max_drawdown(subset.sort_values("decision").fixed6d_net_pct) if len(subset) else np.nan,
            "month_block_two_sided_p": p_value,
            "month_block_test": p_method,
            "settlement_dd_note": "serial equal-notional event sequence; overlapping events are not a feasible concurrent portfolio",
        }
        selected.append(row)
        for month, group in subset.groupby("utc_month", sort=True):
            monthly_rows.append(
                {
                    "cohort": rule.cohort,
                    "config_id": rule.config_id,
                    "utc_month": month,
                    "n_events": int(group.event_id.nunique()),
                    "fixed6d_mean_net_pct": float(group.fixed6d_net_pct.mean()),
                    "basket_trail_mean_net_pct": float(group.basket_net_pct.mean()),
                    "mean_paired_delta_net_pct": float(group.paired_delta_net_pct.mean()),
                    "median_paired_delta_net_pct": float(group.paired_delta_net_pct.median()),
                }
            )
    return pd.DataFrame(selected), pd.DataFrame(monthly_rows)


def _sep16_outputs(events: list[dict[str, Any]], frozen: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    event = next(e for e in events if e["cohort"] == "sept16_original_68")
    summary = _path_summary(event)
    if not summary["complete"]:
        return pd.DataFrame(), pd.DataFrame()
    entries = event["member_open_paths"][:, 0]
    timed_exit = event["member_open_paths"][:, BAR_COUNT]
    baseline_net = float(np.mean(payoffs._vbt_net_returns(entries, timed_exit)))
    outputs = []
    for rule in frozen.itertuples(index=False):
        config = {
            "activate_pct": float(rule.activate_pct),
            "trail_width_pct": float(rule.trail_width_pct),
            "initial_stop_pct": None if pd.isna(rule.initial_stop_pct) else float(rule.initial_stop_pct),
        }
        exit_index, trigger_index, reason, high_pct = _simulate_exit(summary["basket_close_returns"], config)
        exits = event["member_open_paths"][:, exit_index]
        net_pct = float(np.mean(payoffs._vbt_net_returns(entries, exits)))
        outputs.append(
            {
                "cohort_rule_source": rule.cohort,
                "config_id": rule.config_id,
                "decision_entry_time_utc": event["decision"],
                "n_members": event["n_members"],
                "member_set_id": event["member_set_id"],
                "complete_68_member_path": True,
                "synchronous_basket_max_close_profit_pct": summary["basket_max_close_pct"],
                "synchronous_basket_max_close_time_utc": summary["basket_max_close_time"],
                "mean_member_individual_max_close_pct": summary["mean_individual_max_close_pct"],
                "sync_peak_minus_mean_individual_peak_pp": summary["basket_max_close_pct"] - summary["mean_individual_max_close_pct"],
                "exit_reason": reason,
                "trigger_close_time_utc": event["decision"] + BAR * exit_index,
                "exit_time_utc": event["decision"] + BAR * exit_index,
                "exit_open_index": exit_index,
                "basket_peak_close_pct_at_exit": high_pct,
                "basket_trail_gross_pct": float(np.mean(exits / entries - 1.0) * 100.0),
                "basket_trail_net_pct": net_pct,
                "fixed6d_exit_time_utc": event["decision"] + pd.Timedelta(hours=144),
                "same_68_fixed6d_gross_pct": float(np.mean(timed_exit / entries - 1.0) * 100.0),
                "same_68_fixed6d_net_pct": baseline_net,
                "trail_minus_fixed6d_net_pp": net_pct - baseline_net,
                "fee_per_fill": payoffs.TAKER_FEE,
                "cash_flow_boundary": "equal initial notional per member; Binance USD-M 5m open fills; no funding, leverage, slippage, impact, or capacity model",
            }
        )
    curve = pd.DataFrame(
        {
            "close_time_utc": [event["decision"] + BAR * (i + 1) for i in range(BAR_COUNT)],
            "synchronized_equal_weight_basket_close_return_pct": summary["basket_close_returns"] * 100.0,
            "synchronized_basket_high_water_close_pct": np.maximum.accumulate(summary["basket_close_returns"]) * 100.0,
        }
    )
    return pd.DataFrame(outputs), curve


def _coverage_table(events: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for event in events:
        summary = _path_summary(event)
        rows.append(
            {
                **_event_identity(event),
                "status": "complete" if summary["complete"] else "incomplete_member_path",
                "n_full_members": summary["full_members"],
                "n_missing_members": summary["missing_members"],
                "n_missing_member_close_bars": summary["missing_member_close_bars"],
                "min_members_observed_per_close": summary["minimum_members_per_close"],
                "basket_max_close_pct_if_observed": summary["basket_max_close_pct"],
                "mean_member_individual_max_close_pct_if_observed": summary["mean_individual_max_close_pct"],
            }
        )
    return pd.DataFrame(rows)


def _row_key(row: Any) -> tuple[str, int, int]:
    if isinstance(row, dict):
        return (str(row["cohort"]), int(_utc(row["known"]).value), int(_utc(row["decision"]).value))
    return (str(row.cohort), int(_utc(row.known).value), int(_utc(row.decision).value))


def _attach_current_event_metadata(frame: pd.DataFrame, events: list[dict[str, Any]]) -> pd.DataFrame:
    lookup = {_row_key(event): event for event in events}
    result = frame.copy()
    keys = [_row_key(row) for row in result.itertuples(index=False)]
    missing = [key for key in keys if key not in lookup]
    if missing:
        raise AssertionError(f"result rows do not map to current frozen events: {missing[:3]}")
    for column in (
        "event_id",
        "candidate_id",
        "member_set_id",
        "known",
        "decision",
        "epoch",
        "minutes",
        "wait_min",
        "initial_n",
        "n_members",
        "n_excluded_from_basket",
    ):
        result[column] = [lookup[key][column] for key in keys]
    return result


def _reuse_unchanged_results(
    events: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reuse committed trajectories except events whose basket membership changed.

    The committed result has no raw member-by-bar path matrix. Recompute only
    events with newly excluded members and Sep16; preserve all other event and
    config rows, then refresh their event/member metadata from current inputs.
    """
    paths = {
        "baseline": EXITS / "basket_trail_fixed6d_events.csv.gz",
        "strategy": EXITS / "basket_trail_candidate_events.csv.gz",
        "coverage": EXITS / "basket_trail_coverage.csv",
        "receipts": EXITS / "basket_trail_price_receipts.csv.gz",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"cannot reuse missing prior basket trail artifact: {path}")
    old_base = pd.read_csv(paths["baseline"], compression="gzip")
    old_strategy = pd.read_csv(paths["strategy"], compression="gzip")
    old_coverage = pd.read_csv(paths["coverage"])
    old_receipts = pd.read_csv(paths["receipts"], compression="gzip")

    affected = [e for e in events if e["cohort"] != "sept16_original_68" and e["n_excluded_from_basket"] > 0]
    sep = next(e for e in events if e["cohort"] == "sept16_original_68")
    recompute_events = affected + [sep]
    recompute_keys = {_row_key(e) for e in recompute_events}
    unaffected_n = sum(e["cohort"] != "sept16_original_68" and e["n_excluded_from_basket"] == 0 for e in events)
    print(f"incremental repair: recompute {len(affected)} filtered events + Sep16; reuse {unaffected_n} unchanged events", flush=True)

    new_receipts = _load_prices_once_per_symbol(recompute_events)
    new_coverage = _coverage_table(recompute_events)
    if not new_coverage.status.eq("complete").all():
        raise AssertionError("an affected/Sep16 basket became incomplete during scoped reload")
    new_base, new_strategy = _calculate_event_returns(recompute_events)
    if new_base.event_id.nunique() != len(recompute_events):
        raise AssertionError("scoped reload did not produce every affected/fixed baseline event")
    if new_strategy.groupby("event_id").config_id.nunique().ne(len(CONFIGS)).any():
        raise AssertionError("scoped reload did not produce all configured paths for each event")

    def keep_unaffected(frame: pd.DataFrame) -> pd.DataFrame:
        keys = [_row_key(row) for row in frame.itertuples(index=False)]
        return frame.loc[[key not in recompute_keys for key in keys]].copy()

    baseline = pd.concat([keep_unaffected(old_base), new_base], ignore_index=True, sort=False)
    strategies = pd.concat([keep_unaffected(old_strategy), new_strategy], ignore_index=True, sort=False)
    coverage = pd.concat([keep_unaffected(old_coverage), new_coverage], ignore_index=True, sort=False)
    baseline = _attach_current_event_metadata(baseline, events)
    strategies = _attach_current_event_metadata(strategies, events)
    coverage = _attach_current_event_metadata(coverage, events)

    required_symbols = set().union(*(set(e["symbols"]) for e in events))
    old_receipts["symbol"] = old_receipts["symbol"].astype(str).str.upper()
    new_receipts["symbol"] = new_receipts["symbol"].astype(str).str.upper()
    old_receipts = old_receipts.loc[old_receipts.symbol.isin(required_symbols)]
    reused_receipts = old_receipts.loc[~old_receipts.symbol.isin(set(new_receipts.symbol))]
    receipts = pd.concat([reused_receipts, new_receipts], ignore_index=True, sort=False).drop_duplicates("symbol", keep="last")
    if set(receipts.symbol) != required_symbols:
        missing = sorted(required_symbols - set(receipts.symbol))
        raise AssertionError(f"current price receipts lack required symbols: {missing[:8]}")
    return baseline, strategies, coverage, receipts


def _verify_baseline_parity(baseline: pd.DataFrame, events: list[dict[str, Any]]) -> pd.DataFrame:
    """Assert event-by-event parity to the frozen eligible-member baseline."""
    checks: list[pd.DataFrame] = []
    for cohort, wait_min in (("dynamic_primary_ALT", 60), ("wave_ALT", 30)):
        source_path = EXPERIMENT / f"payoffs/event_outcomes_minutes30_wait{wait_min}.csv.gz"
        expected = pd.read_csv(
            source_path,
            compression="gzip",
            usecols=["known", "decision", "hold_hours", "basket_net_return_pct", "member_n"],
        )
        expected = expected.loc[expected.hold_hours.eq(144)].copy()
        expected["known"] = pd.to_datetime(expected.known, utc=True)
        expected["decision"] = pd.to_datetime(expected.decision, utc=True)
        actual = baseline.loc[baseline.cohort.eq(cohort)].copy()
        actual["known"] = pd.to_datetime(actual.known, utc=True)
        actual["decision"] = pd.to_datetime(actual.decision, utc=True)
        merged = actual.merge(expected, on=["known", "decision"], how="left", validate="one_to_one")
        if merged.basket_net_return_pct.isna().any():
            raise AssertionError(f"{cohort} lacks a frozen hold144 outcome row")
        merged["parity_delta_pct"] = merged.fixed6d_net_pct - merged.basket_net_return_pct
        merged["member_count_delta"] = merged.n_members - merged.member_n
        merged["baseline_source"] = "payoffs/event_outcomes hold_hours=144 basket_net_return_pct"
        if merged.member_count_delta.ne(0).any() or merged.parity_delta_pct.abs().max() > 1e-6:
            bad = merged.loc[merged.member_count_delta.ne(0) | merged.parity_delta_pct.abs().gt(1e-6)]
            raise AssertionError(f"{cohort} fixed6d parity failed: {bad.head(5).to_dict('records')}")
        if cohort == "wave_ALT":
            locked = pd.read_parquet(WAVE_EVENTS_PATH)
            locked = locked.loc[locked.candidate_id.eq("wave_ALT"), ["known", "decision", "baseline_pct"]].copy()
            locked["known"] = pd.to_datetime(locked.known, utc=True)
            locked["decision"] = pd.to_datetime(locked.decision, utc=True)
            merged = merged.merge(locked, on=["known", "decision"], how="left", validate="one_to_one")
            merged["locked_baseline_delta_pct"] = merged.fixed6d_net_pct - merged.baseline_pct
            if merged.baseline_pct.isna().any() or merged.locked_baseline_delta_pct.abs().max() > 1e-6:
                raise AssertionError("wave_ALT fixed6d fails event-by-event parity to locked baseline_pct")
        checks.append(merged)
    result = pd.concat(checks, ignore_index=True, sort=False)
    if len(result) != 170:
        raise AssertionError(f"expected 170 event-level baseline parity rows, found {len(result)}")
    return result


def _self_test() -> None:
    sample = np.array([0.02, 0.06, 0.04, 0.01])
    cfg = {"activate_pct": 5.0, "trail_width_pct": 4.0, "initial_stop_pct": None}
    exit_i, trigger_i, reason, peak = _simulate_exit(sample, cfg)
    assert (exit_i, trigger_i, reason) == (4, 3, "basket_high_water_trail")
    assert peak == 6.0
    no_trigger = {"activate_pct": 20.0, "trail_width_pct": 4.0, "initial_stop_pct": 12.0}
    assert _simulate_exit(np.array([0.01, -0.02]), no_trigger)[0] == BAR_COUNT
    stop_cfg = {"activate_pct": 20.0, "trail_width_pct": 4.0, "initial_stop_pct": 6.0}
    assert _simulate_exit(np.array([-0.061, 0.2]), stop_cfg)[:3] == (1, 0, "initial_basket_stop")
    try:
        import vectorbt as vbt  # noqa: F401
    except Exception as exc:
        raise RuntimeError("isolated vectorbt-python is required") from exc
    net = payoffs._vbt_net_returns(np.array([100.0]), np.array([110.0]))
    assert 9.8 < net[0] < 10.0, net


def run(*, reuse_existing: bool = False) -> dict[str, Any]:
    events, members_df, metadata = _load_events()
    print(json.dumps(metadata, indent=2), flush=True)
    if reuse_existing:
        baseline, strategies, coverage, receipt_df = _reuse_unchanged_results(events)
    else:
        receipt_df = _load_prices_once_per_symbol(events)
        coverage = _coverage_table(events)
        baseline, strategies = _calculate_event_returns(events)
    if len(baseline) != len(events) or len(strategies) != len(events) * len(CONFIGS):
        raise AssertionError(f"unexpected result cardinality: baseline={len(baseline)}, candidate={len(strategies)}")
    parity = _verify_baseline_parity(baseline, events)
    selection_table, frozen = _selection_table(strategies, events)
    # Add UTC decision to the result table for transparent monthly aggregation.
    decision_map = {e["event_id"]: e["decision"] for e in events}
    strategies["decision"] = strategies["event_id"].map(decision_map)
    summary_2026, monthly_2026 = _summarize_2026(strategies, frozen, events)
    sep_results, sep_curve = _sep16_outputs(events, frozen)

    EXITS.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(EXITS / "basket_trail_coverage.csv", index=False)
    receipt_df.to_csv(EXITS / "basket_trail_price_receipts.csv.gz", index=False, compression="gzip")
    members_df.to_csv(EXITS / "basket_trail_members.csv.gz", index=False, compression="gzip")
    baseline.to_csv(EXITS / "basket_trail_fixed6d_events.csv.gz", index=False, compression="gzip")
    strategies.to_csv(EXITS / "basket_trail_candidate_events.csv.gz", index=False, compression="gzip")
    selection_table.to_csv(EXITS / "basket_trail_pre2026_selection.csv", index=False)
    frozen.to_csv(EXITS / "basket_trail_frozen_rules.csv", index=False)
    parity.to_csv(EXITS / "basket_trail_baseline_parity.csv", index=False)
    summary_2026.to_csv(EXITS / "basket_trail_2026_summary.csv", index=False)
    monthly_2026.to_csv(EXITS / "basket_trail_2026_monthly.csv", index=False)
    sep_results.to_csv(EXITS / "basket_trail_sep16.csv", index=False)
    sep_curve.to_csv(EXITS / "basket_trail_sep16_curve.csv.gz", index=False, compression="gzip")
    manifest = {
        "builder": str(Path(__file__).relative_to(ROOT)),
        "selection": "pre-2026 highest q25 UTC-month mean paired net delta versus same-event 144h hold; tie-break median month, mean month, smaller serial-event DD",
        "parameter_grid": [{k: v for k, v in c.items() if k != "config_id"} | {"config_id": c["config_id"]} for c in CONFIGS],
        "bar_interval": "5m",
        "entry": "decision timestamp 5m open",
        "trigger": "completed 5m close of synchronous equal-weight basket return versus common entry open",
        "exit": "next 5m open after trigger close; otherwise 144h open timeout",
        "price_api": "monday_breadth_opt_payoffs.load_full_ohlc(repair_missing=False)",
        "fees": {"entry_taker": payoffs.TAKER_FEE, "exit_taker": payoffs.TAKER_FEE},
        "modeled": ["equal initial notional per member", "Binance USD-M 5m open/close", "two taker fills per member"],
        "not_modeled": ["funding", "leverage", "slippage", "market impact", "capacity", "margin/liquidation"],
        "basket_membership": "count raw frozen initial N, but exclude any symbol with monday_breadth_constituent_scope.exclusion_reason from the priced ALT basket; exclusion reason is retained in basket_trail_members",
        "incremental_reuse": {
            "enabled": bool(reuse_existing),
            "recomputed_filtered_events": int(((coverage.cohort != "sept16_original_68") & coverage.n_excluded_from_basket.gt(0)).sum()) if reuse_existing else None,
            "reused_unaffected_events": int(((coverage.cohort != "sept16_original_68") & coverage.n_excluded_from_basket.eq(0)).sum()) if reuse_existing else None,
        },
        "baseline_parity": {
            "events": int(len(parity)),
            "max_abs_payoff_baseline_delta_pct": float(parity.parity_delta_pct.abs().max()),
            "max_abs_wave_locked_baseline_delta_pct": float(parity.loc[parity.cohort.eq("wave_ALT"), "locked_baseline_delta_pct"].abs().max()),
            "member_count_mismatches": int(parity.member_count_delta.ne(0).sum()),
        },
        "coverage": {
            "locked_events": int((coverage.cohort != "sept16_original_68").sum()),
            "complete_locked_events": int(((coverage.cohort != "sept16_original_68") & coverage.status.eq("complete")).sum()),
            "complete_sep16": bool(coverage.loc[coverage.cohort.eq("sept16_original_68"), "status"].eq("complete").all()),
            "loaded_symbols": int(receipt_df.status.eq("loaded").sum()),
            "requested_symbols": int(len(receipt_df)),
        },
        "input_counts": metadata,
    }
    (EXITS / "basket_trail_manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    print("2026 summary:")
    print(summary_2026.to_string(index=False))
    print("Sep-16:")
    print(sep_results.to_string(index=False))
    print("coverage:")
    print(coverage.groupby(["cohort", "status"]).size().to_string())
    print("baseline parity:")
    print(parity.groupby("cohort").parity_delta_pct.agg(["count", "max", "min"]).to_string())
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="check close-to-next-open logic and native VectorBT fee fills")
    parser.add_argument("--preflight-only", action="store_true", help="validate locked events and member bridge without loading bars")
    parser.add_argument("--reuse-existing", action="store_true", help="recompute filtered baskets and Sep16 only; reuse committed unaffected trajectories")
    args = parser.parse_args(argv)
    if args.self_test:
        _self_test()
        print("self-test passed")
        if args.preflight_only:
            return 0
    if args.preflight_only:
        events, _, metadata = _load_events()
        print(json.dumps(metadata, indent=2))
        print("cohort counts:", pd.Series([e["cohort"] for e in events]).value_counts().to_dict())
        return 0
    run(reuse_existing=args.reuse_existing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
