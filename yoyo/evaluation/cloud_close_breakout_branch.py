"""Compare a true 12H Monday-band breakout with the original V1.7 reclaim.

Signal and reference prices use completed native UTC 12H candles and the
completed New York Monday 1H range. Orders and exits are repriced by the
existing VectorBT path callback and shared-cash account. This module never
filters a previously funded trade list to create breakout entries.
"""
from __future__ import annotations

import argparse
import json
import math
import zlib
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_second_reclaim_paths as reclaim
from yoyo.monitor.cloud_signal_plans import fixed_stop

ROOT = engine.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-breakout-branch-20261109-v1"
CONFIG_PATH = EXP / "config.json"
HISTORICAL_EXP = ROOT / "experiments/active/exp-cloud-close-breakout-historical-20261109-v1"
HOUR = engine.HOUR_MS
STEP = engine.STEP_12H
TAKER = engine.TAKER
DEFAULT_SEED = 20261109
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00:00Z"),
    "2025": ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "2026": ("2026-01-01T00:00:00Z", None),
}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_safe(value), ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _ny_week_open(time_ms: int) -> dict:
    return engine._ny_week_window(int(time_ms))


def _input_paths(symbol: str, config: dict) -> tuple[Path | None, Path, str]:
    group = config["data"]["symbol_source_groups"][symbol]
    if group == "retired_documented_h1":
        source = config["data"]["retired_h1_sources"][symbol]
        return None, ROOT / source["bars1h_path"], group
    base = ROOT / config["data"]["source_roots"][group] / symbol
    return base / "bars12h.csv.gz", base / "bars1h.csv.gz", group


def _validate_frame(frame: pd.DataFrame, symbol: str, period: str) -> None:
    fields = ["open", "high", "low", "close"]
    if (not frame.index.is_unique or not frame.index.is_monotonic_increasing
            or not set(fields).issubset(frame.columns)):
        raise ValueError(f"Invalid {period} history schema for {symbol}")
    values = frame[fields].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or np.any(values <= 0)
            or np.any(values[:, 2] > values[:, [0, 1, 3]].min(axis=1))
            or np.any(values[:, 1] < values[:, [0, 2, 3]].max(axis=1))):
        raise ValueError(f"Invalid {period} OHLC values for {symbol}")


@lru_cache(maxsize=69)
def _retired_frames_cached(hour_path: str, end_ms: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    from yoyo.evaluation import cloud_close_weak_rally_branch as retired
    return retired.frames((Path(hour_path), None, int(end_ms)))


def _load_symbol(symbol: str, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, str, str, str, int]:
    p12, p1, group = _input_paths(symbol, config)
    data_end_ms = int(config["data"]["cutoff_ms"])
    if group == "retired_documented_h1":
        source = config["data"]["retired_h1_sources"][symbol]
        p1 = ROOT / source["bars1h_path"]
        data_end_ms = int(source["available_end_ms"])
        bars1, bars12 = _retired_frames_cached(str(p1), data_end_ms)
        p12 = f"12H aggregated by weak_rally_branch.frames from {source['bars1h_path']}"
        bars1 = bars1[["open", "high", "low", "close"]].astype(float)
        bars12 = bars12[["open", "high", "low", "close"]].astype(float)
        if (not bars1.index.is_unique or not bars1.index.is_monotonic_increasing
                or not bars12.index.is_unique or not bars12.index.is_monotonic_increasing):
            raise ValueError(f"Invalid aggregated archived bars for {symbol}")
        if not np.equal(bars12.index.to_numpy(dtype=np.int64) % STEP, 0).all():
            raise ValueError(f"Aggregated 12H bars are not UTC aligned for {symbol}")
        _validate_frame(bars1, symbol, "1H")
        _validate_frame(bars12, symbol, "aggregated 12H")
    else:
        bars12 = engine._load_bars(p12, "12h")
        bars1 = engine._load_bars(p1, "1h")
    if bars12.empty or bars1.empty:
        raise ValueError(f"Missing native OHLC input for {symbol}: {p12}, {p1}")
    if group != "retired_documented_h1" and int(bars12.index.max()) + STEP < int(config["data"]["cutoff_ms"]):
        raise ValueError(f"12H input for {symbol} ends before frozen cutoff")
    if group != "retired_documented_h1" and int(bars1.index.max()) + HOUR < int(config["data"]["cutoff_ms"]):
        raise ValueError(f"1H input for {symbol} ends before frozen cutoff")
    p12_string = str(p12.relative_to(ROOT)) if isinstance(p12, Path) else str(p12)
    return bars12, bars1, group, p12_string, str(p1.relative_to(ROOT)), data_end_ms


def _valid_key(index: int, bars12: pd.DataFrame, features: dict,
               calendar: dict[str, dict], symbol: str, cutoff_ms: int,
               start_ms: int) -> tuple[dict, dict] | None:
    """Return completed key's week/range only when the entire 12H key is in-week."""
    key_open = int(bars12.index[index])
    confirmation = key_open + STEP
    if (key_open % STEP or confirmation < start_ms or confirmation > cutoff_ms
            or not features["ema_ready"][index]):
        return None
    window = _ny_week_open(key_open)
    week = str(window["week"])
    anchor = calendar.get(week)
    if (anchor is None or anchor["state"] != "ready"
            or int(anchor["end_ms"]) > key_open
            or confirmation > int(anchor["expires_ms"])):
        return None
    return window, anchor


def _event_template(symbol: str, arm: str, source_row: int, key_open: int,
                    confirmation: int, week: str, side: int, anchor: dict,
                    ema20: float, ema50: float, prior_atr: float,
                    bars12_path: str, bars1_path: str) -> dict:
    parent_id = f"{arm}|{symbol}|{week}|{key_open}"
    reference_anchor = float(anchor["upper"] if side > 0 else anchor["lower"])
    stop, stop_pct = fixed_stop(symbol, side, reference_anchor)
    return {
        "event_id": parent_id + ":signal", "parent_id": parent_id,
        "source_row_number": source_row, "symbol": symbol, "week": week,
        "side": int(side), "tp_r": 0.0, "is_control": False,
        "rank": None, "cohort": "fixed_10_major_OKX_panel",
        "is_current": True, "was_top20": symbol in {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"},
        "bar_source": "frozen_native_OKX_12H_and_1H",
        "key_open_ms": int(key_open), "confirmation_ms": int(confirmation),
        "entry_ms": None, "entry_i": None, "entry_price": None,
        "stop_price": float(stop) if stop is not None else None,
        "stop_pct": float(stop_pct), "reference_anchor": reference_anchor,
        "risk_distance": None, "risk_fraction": None,
        "entry_status": "pending", "control_match_status": "not_applicable",
        "stop_basis": "fixed_stop(symbol,side,completed_Monday_1.618_breakout_anchor)",
        "ema20_confirmed": float(ema20), "ema50_confirmed": float(ema50),
        "prior_atr_pct": float(prior_atr) if np.isfinite(prior_atr) else None,
        "source_path_12h": bars12_path, "source_path_1h": bars1_path,
        "path_upper_bound_reason": None,
    }


def _bind_exact_entry(event: dict, q: np.ndarray, bar_ms: np.ndarray,
                      start_ms: int, end_ms: int) -> None:
    """Bind to the exactly coincident, executable H1 open; never move forward."""
    confirmation = int(event["confirmation_ms"])
    idx = int(np.searchsorted(bar_ms, confirmation))
    if idx >= len(bar_ms) or int(bar_ms[idx]) != confirmation:
        event["entry_status"] = "no_entry_missing_exact_next_h1_open"
        return
    if confirmation < int(start_ms):
        event["entry_status"] = "no_entry_before_account_start"
        return
    if confirmation >= int(end_ms):
        event["entry_status"] = "no_entry_at_or_after_account_cutoff"
        return
    entry_price = float(q[idx, 0])
    stop = event["stop_price"]
    side = int(event["side"])
    risk = (entry_price - float(stop)) * side if stop is not None else math.nan
    if not math.isfinite(entry_price) or entry_price <= 0:
        event["entry_status"] = "no_entry_invalid_open"
        return
    if not math.isfinite(risk) or risk <= 0:
        event["entry_status"] = "no_entry_open_not_beyond_anchor_stop"
        return
    event.update(entry_i=idx, entry_ms=confirmation, entry_price=entry_price,
                 risk_distance=float(risk), risk_fraction=float(risk / entry_price),
                 entry_status="pending", entry_source="exact_next_h1_open")


def _signal_sets(symbol: str, bars12: pd.DataFrame,
                 q: np.ndarray, bar_ms: np.ndarray, features: dict,
                 calendar: dict[str, dict], config: dict,
                 source_paths: tuple[str, str], row_counter: list[int]) -> tuple[dict[str, list[dict]], list[dict], dict]:
    """Build first valid weekly breakout/reclaim signals and non-signal controls."""
    cutoff = int(config["data"]["cutoff_ms"])
    end_ms = int(config["account"]["end_ms"])
    start_ms = int(config["account"]["start_ms"])
    map_hour = {int(t): i for i, t in enumerate(bar_ms)}
    valid_keys: list[dict] = []
    counts = Counter()
    breakout_by_week: dict[str, dict] = {}
    reclaim_by_week: dict[str, dict] = {}
    for i in range(len(bars12)):
        valid = _valid_key(i, bars12, features, calendar, symbol, cutoff, start_ms)
        if valid is None:
            continue
        window, anchor = valid
        key_open = int(bars12.index[i])
        confirmation = key_open + STEP
        row = bars12.iloc[i]
        close = float(row.close)
        ema20, ema50 = float(features["ema20"][i]), float(features["ema50"][i])
        atr = float(features["prior_atr_pct"][i])
        week = str(window["week"])
        lower, upper = float(anchor["lower"]), float(anchor["upper"])
        breakout_side = (1 if close > upper and ema20 > ema50 else
                         -1 if close < lower and ema20 < ema50 else 0)
        reclaim_signal = reclaim._signal_at(i, symbol, bars12, features, calendar, cutoff)
        reclaim_side = int(reclaim_signal["side"]) if reclaim_signal else 0
        if breakout_side:
            counts["all_breakout_trigger_bars"] += 1
            if week not in breakout_by_week:
                row_counter[0] += 1
                event = _event_template(symbol, "breakout", row_counter[0], key_open,
                                        confirmation, week, breakout_side, anchor, ema20, ema50,
                                        atr, *source_paths)
                _bind_exact_entry(event, q, bar_ms, start_ms, end_ms)
                breakout_by_week[week] = event
                counts["breakout_first_weekly_keys"] += 1
        if reclaim_signal:
            counts["all_reclaim_trigger_bars"] += 1
            if week not in reclaim_by_week:
                row_counter[0] += 1
                event = _event_template(symbol, "reclaim", row_counter[0], key_open,
                                        confirmation, week, reclaim_side, anchor, ema20, ema50,
                                        atr, *source_paths)
                event["reference_anchor"] = float(reclaim_signal["signal_reference_entry"])
                stop, stop_pct = fixed_stop(symbol, reclaim_side, event["reference_anchor"])
                event["stop_price"] = float(stop) if stop is not None else None
                event["stop_pct"] = float(stop_pct)
                event["stop_basis"] = "original_V1.7_fixed_stop_at_touched_side_1.618_anchor"
                event["confirmation_close"] = float(reclaim_signal["confirmation_close"])
                event["anchor_lower"] = lower
                event["anchor_upper"] = upper
                _bind_exact_entry(event, q, bar_ms, start_ms, end_ms)
                reclaim_by_week[week] = event
                counts["reclaim_first_weekly_keys"] += 1

        if confirmation < start_ms or confirmation >= end_ms or confirmation > int(window["expires"]):
            continue
        if confirmation not in map_hour or not np.isfinite(atr) or atr <= 0:
            continue
        side = int(np.sign(ema20 - ema50))
        if side not in (-1, 1):
            continue
        # Include only candidate bars that do not themselves trigger either
        # prespecified real signal. Controls inherit a parent risk fraction.
        if not breakout_side and not reclaim_side:
            valid_keys.append({"index": i, "week": week, "side": side,
                               "key_open_ms": key_open, "confirmation_ms": confirmation,
                               "prior_atr_pct": atr, "entry_i": map_hour[confirmation],
                               "entry_price": float(q[map_hour[confirmation], 0]),
                               "ema20": ema20, "ema50": ema50, "anchor": anchor})

    by_arm: dict[str, list[dict]] = {
        "breakout": list(breakout_by_week.values()),
        "reclaim": list(reclaim_by_week.values()),
    }
    audits = []
    for arm, parents in by_arm.items():
        controls, match_stats = _match_controls(arm, parents, valid_keys,
                                               config, row_counter)
        parents.extend(controls)
        for parent in parents:
            if not parent.get("is_control"):
                audits.append(_candidate_record(arm, parent))
        for control in controls:
            if control.get("is_control"):
                audits.append(_candidate_record(arm, control))
        counts[f"{arm}_matched_controls"] = int(match_stats["matched"])
        counts[f"{arm}_unmatched_controls"] = int(match_stats["unmatched"])
    return by_arm, audits, dict(counts)


def _candidate_record(arm: str, event: dict) -> dict:
    keys = ("event_id", "parent_id", "is_control", "control_match_status", "symbol",
            "week", "side", "key_open_ms", "confirmation_ms", "entry_ms", "entry_price",
            "reference_anchor", "stop_price", "risk_distance", "risk_fraction", "prior_atr_pct",
            "ema20_confirmed", "ema50_confirmed", "entry_status", "control_match_reason")
    row = {key: event.get(key) for key in keys}
    row["arm"] = arm
    return row


def _match_controls(arm: str, parents: list[dict], candidates: list[dict],
                    config: dict, row_counter: list[int]) -> tuple[list[dict], dict]:
    controls: list[dict] = []
    used: set[int] = set()
    tolerance = float(config["controls"]["atr_relative_tolerance"])
    seed = int(config["controls"]["seed"])
    unmatched = Counter()
    matched = 0
    for parent in sorted(parents, key=lambda e: (e["confirmation_ms"], e["event_id"])):
        if parent.get("entry_i") is None or parent.get("risk_fraction") is None:
            reason = "parent_not_executable"
            unmatched[reason] += 1
            controls.append(_unmatched_control(arm, parent, reason))
            continue
        parent_atr = parent.get("prior_atr_pct")
        if parent_atr is None or not np.isfinite(parent_atr) or parent_atr <= 0:
            reason = "parent_prior_atr_unavailable"
            unmatched[reason] += 1
            controls.append(_unmatched_control(arm, parent, reason))
            continue
        possible = [candidate for candidate in candidates
                    if candidate["index"] not in used
                    and candidate["week"] == parent["week"]
                    and candidate["side"] == parent["side"]
                    and candidate["confirmation_ms"] > parent["confirmation_ms"]
                    and abs(candidate["prior_atr_pct"] / parent_atr - 1.0) <= tolerance]
        if not possible:
            reason = "no_later_same_week_direction_and_atr_candidate"
            unmatched[reason] += 1
            controls.append(_unmatched_control(arm, parent, reason))
            continue
        stable_seed = (seed + zlib.crc32((arm + "|" + parent["parent_id"]).encode())) % (2**32)
        rng = np.random.default_rng(stable_seed)
        chosen = possible[int(rng.integers(0, len(possible)))]
        used.add(int(chosen["index"]))
        row_counter[0] += 1
        control = dict(parent)
        control.update({
            "event_id": parent["parent_id"] + ":control", "is_control": True,
            "source_row_number": row_counter[0], "key_open_ms": int(chosen["key_open_ms"]),
            "confirmation_ms": int(chosen["confirmation_ms"]), "entry_ms": int(chosen["confirmation_ms"]),
            "entry_i": int(chosen["entry_i"]), "entry_price": float(chosen["entry_price"]),
            "prior_atr_pct": float(chosen["prior_atr_pct"]),
            "ema20_confirmed": float(chosen["ema20"]), "ema50_confirmed": float(chosen["ema50"]),
            "risk_distance": float(chosen["entry_price"] * parent["risk_fraction"]),
            "stop_price": float(chosen["entry_price"] - parent["side"]
                                * chosen["entry_price"] * parent["risk_fraction"]),
            "risk_fraction": float(parent["risk_fraction"]),
            "reference_anchor": None, "entry_status": "pending",
            "control_match_status": "matched",
            "control_confirmation_ms": int(chosen["confirmation_ms"]),
            "control_12h_open_ms": int(chosen["key_open_ms"]),
            "control_atr_relative_difference": float(chosen["prior_atr_pct"] / parent_atr - 1.0),
            "control_match_reason": "same symbol/week/side; strictly later; prior 12H ATR14/previous close within tolerance",
            "stop_basis": "matched_parent_actual_entry_to_stop_risk_fraction",
            "entry_source": "exact_next_h1_open",
        })
        if (control["entry_price"] - control["stop_price"]) * control["side"] <= 0:
            raise AssertionError("Inherited control risk geometry is not adverse to its side")
        matched += 1
        controls.append(control)
    return controls, {"matched": matched, "unmatched": sum(unmatched.values()),
                      "unmatched_reasons": dict(unmatched)}


def _unmatched_control(arm: str, parent: dict, reason: str) -> dict:
    event = dict(parent)
    event.update(event_id=parent["parent_id"] + ":control", is_control=True,
                 entry_i=None, entry_ms=None, entry_price=None, stop_price=None,
                 risk_distance=None, risk_fraction=None, entry_status=reason,
                 control_match_status="unmatched", control_match_reason=reason,
                 stop_basis="unmatched_no_trade")
    return event


def _event_paths(events: list[dict], q: np.ndarray,
                 bar_ms: np.ndarray, config: dict) -> tuple[list[dict], dict]:
    batch_size = int(config["execution"]["event_batch_size"])
    executable = []
    for event in events:
        if event.get("entry_i") is None:
            continue
        event["end_i"], event["path_upper_bound_reason"] = engine._safe_path_end(
            int(event["entry_i"]), int(event["side"]), q, bar_ms)
        if event["end_i"] >= len(bar_ms):
            raise AssertionError("Path endpoint exceeds the source H1 series")
        executable.append(event)
    rows, stats = engine._simulate_symbol(events, q, bar_ms, (0.0,), batch_size)
    return rows, {**stats, "priced_events": len(executable), "source_tail_ms": int(bar_ms[-1])}


def _account_scenario(label: str, events: pd.DataFrame, quote_loader) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    if events.empty:
        raise ValueError(f"Cannot run empty account scenario {label}")
    data = wallet.prepare(events, slots=9, start=wallet.START, end=wallet.END,
                          quote_loader=quote_loader)
    funded, equity = wallet.account(data, slip=0.0, exposure=1.0)
    metrics = {period: wallet.summarize(funded, equity, *bounds)
               for period, bounds in PERIODS.items()}
    meta = {"input_executed_events": len(events), "admitted": len(data["selected"]),
            "funded": len(funded), "rejected": data["skips"],
            "unquoted_position_hours": int(data["gap_marks"])}
    if not np.isclose(1000.0 + funded.cash_pnl.sum(), equity.nav.iloc[-1],
                      rtol=1e-8, atol=.005):
        raise ValueError(f"Account {label} cash does not reconcile to marked final NAV")
    return metrics, funded, equity, meta


def _json_metrics(metrics: dict) -> dict:
    return {period: _json_safe(values) for period, values in metrics.items()}


def _quote_loader(config: dict):
    retired_sources = config["data"].get("retired_h1_sources", {})
    if not retired_sources:
        return wallet.quotes
    @lru_cache(maxsize=len(retired_sources))
    def quotes(symbol: str):
        if symbol not in retired_sources:
            return wallet.quotes(symbol)
        source = retired_sources[symbol]
        h1_path = ROOT / source["bars1h_path"]
        hourly, _ = _retired_frames_cached(str(h1_path), int(source["available_end_ms"]))
        return (hourly.index.to_numpy(dtype=np.int64),
                hourly[["open", "high", "low", "close"]].to_numpy(dtype=float))

    return quotes


def _prepare_one_symbol(symbol: str, config: dict, row_counter: list[int]) -> tuple[dict, list[dict], dict]:
    bars12, source_bars1, source_group, p12, p1, source_end_ms = _load_symbol(symbol, config)
    cutoff = int(config["data"]["cutoff_ms"])
    bars1 = source_bars1.loc[source_bars1.index < cutoff]
    if bars1.empty:
        raise ValueError(f"No H1 observations through the available lifetime for {symbol}")
    if source_group != "retired_documented_h1" and int(bars1.index[-1]) + HOUR < cutoff:
        raise ValueError(f"No complete H1 observations through the frozen cutoff for {symbol}")
    if int(bars1.index[-1]) + HOUR > source_end_ms:
        raise ValueError(f"H1 row extends past source availability for {symbol}")
    q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
    features = prepared["features"]
    calendar = reclaim._anchors(bars1, bars12, symbol)
    arms, candidates, signal_counts = _signal_sets(
        symbol, bars12, q, bar_ms, features, calendar, config,
        (p12, p1), row_counter)
    path_rows, execution, candidate_rows = {}, {}, list(candidates)
    for arm in ("breakout", "reclaim"):
        rows, stats = _event_paths(arms[arm], q, bar_ms, config)
        for row in rows:
            row["arm"] = arm
            row["exchange"] = "OKX"
        path_rows[arm] = rows
        execution[arm] = stats
    total_12h_gap = int(np.count_nonzero(np.diff(bars12.index.to_numpy(dtype=np.int64)) != STEP))
    total_1h_gap = int(np.count_nonzero(np.diff(bar_ms) != HOUR))
    range_states = Counter(item["state"] for item in calendar.values())
    retired_provenance = {}
    if source_group == "retired_documented_h1":
        source = config["data"]["retired_h1_sources"][symbol]
        receipt_path = ROOT / source["source_receipt_path"]
        months_path = ROOT / source["months_manifest_path"]
        if not receipt_path.is_file() or not months_path.is_file():
            raise FileNotFoundError(f"Retired OKX source receipt missing for {symbol}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        months = json.loads(months_path.read_text(encoding="utf-8"))
        retired_provenance = {
            "source_receipt_path": source["source_receipt_path"],
            "months_manifest_path": source["months_manifest_path"],
            "source_hours": int(receipt["hours"]),
            "complete_native_minute_hours": int(receipt["from_complete_native_minutes"]),
            "supplemental_local_h1_hours": int(receipt["supplemental_local_h1"]),
            "overlap_hours": int(receipt["overlap_hours"]),
            "overlap_disagreements": int(receipt["overlap_disagreements"]),
            "month_status_counts": dict(Counter(row["status"] for row in months)),
        }
    audit = {
        "symbol": symbol, "source_group": source_group,
        "bars12h_path": p12, "bars1h_path": p1,
        "source_available_end_ms": source_end_ms,
        "retired_source_provenance": retired_provenance,
        "bars12h_rows": len(bars12), "bars1h_rows_source": len(source_bars1),
        "bars1h_rows_used_pre_cutoff": len(bars1),
        "bars12h_first_open_ms": int(bars12.index[0]), "bars12h_last_open_ms": int(bars12.index[-1]),
        "bars1h_first_open_ms": int(bar_ms[0]), "bars1h_last_open_ms": int(bar_ms[-1]),
        "bars12h_gap_count": total_12h_gap, "bars1h_gap_count": total_1h_gap,
        "bars12h_sources": bars12.source.value_counts(dropna=False).to_dict()
            if "source" in bars12 else {},
        "range_weeks_by_state": dict(range_states),
        "ema_ready_12h": int(features["ema_ready"].sum()),
        "missing_12h_period_count": int(prepared["audit"]["missing_12h_period_count"]),
        "stale_12h_trigger_count": int(prepared["audit"]["stale_12h_trigger_count"]),
        "missing_12h_with_contiguous_h1_count": int(prepared["audit"]["missing_12h_with_complete_contiguous_1h_count"]),
        "first_signal_counts": signal_counts,
        "vectorbt_paths": execution,
    }
    return path_rows, candidate_rows, audit


def run(config_path: Path = CONFIG_PATH) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    experiment_dir = config_path.parent
    if config["experiment_id"] != experiment_dir.name:
        raise ValueError("Unexpected experiment_id")
    output_dir = experiment_dir / config.get("output_subdir", ".")
    if (output_dir / config["outputs"]["summary_json"]).exists():
        raise FileExistsError("Refusing to overwrite a completed breakout-branch run")
    if float(config["fees"]["taker_per_fill"]) != TAKER:
        raise ValueError("Frozen OKX taker rate must be 5bp per fill")
    if (int(config["account"]["start_ms"]) != wallet.START
            or int(config["account"]["end_ms"]) != wallet.END
            or int(config["data"]["cutoff_ms"]) != wallet.END):
        raise ValueError("Config account bounds must match the frozen shared-wallet UTC window")
    reuse = config.get("reuse_original_paths")
    row_counter = [1_000_000] if reuse else [0]
    rows_by_arm = {"breakout": [], "reclaim": []}
    candidates, symbol_audits = [], {}
    run_symbols = config["data"]["symbols"]
    for symbol_number, symbol in enumerate(run_symbols, start=1):
        path_rows, candidate_rows, audit = _prepare_one_symbol(symbol, config, row_counter)
        for arm, arm_rows in rows_by_arm.items():
            arm_rows.extend(path_rows[arm])
        candidates.extend(candidate_rows)
        symbol_audits[symbol] = audit
        print(f"prepared {symbol_number}/{len(run_symbols)} {symbol}: "
              f"breakout={audit['first_signal_counts']['breakout_first_weekly_keys']} "
              f"reclaim={audit['first_signal_counts']['reclaim_first_weekly_keys']}", flush=True)
    incremental_paths = pd.concat([pd.DataFrame(rows_by_arm[arm]) for arm in rows_by_arm], ignore_index=True)
    incremental_candidates = pd.DataFrame(candidates)
    reused_path_rows = 0
    reused_candidate_rows = 0
    reused_symbols: list[str] = []
    reused_symbol_audits = {}
    if reuse:
        reused_symbols = list(reuse["symbols"])
        frozen_paths_path = ROOT / reuse["paths_csv_gz"]
        frozen_candidates_path = ROOT / reuse["candidate_audit_csv_gz"]
        frozen_audit_path = ROOT / reuse["audit_json"]
        frozen_paths = pd.read_csv(frozen_paths_path, dtype={"symbol": str},
                                   float_precision="round_trip", keep_default_na=False)
        frozen_candidates = pd.read_csv(frozen_candidates_path, dtype={"symbol": str},
                                        float_precision="round_trip", keep_default_na=False)
        if set(frozen_paths.symbol.unique()) != set(reused_symbols):
            raise ValueError("Frozen reuse paths do not match the explicitly preregistered ten-symbol panel")
        if set(frozen_paths.arm.unique()) != {"breakout", "reclaim"}:
            raise ValueError("Frozen ten-symbol reuse paths do not contain both prespecified arms")
        frozen_audit = json.loads(frozen_audit_path.read_text(encoding="utf-8"))
        reused_symbol_audits = frozen_audit["symbols"]
        if set(reused_symbol_audits) != set(reused_symbols):
            raise ValueError("Frozen ten-symbol audit does not match the preregistered reused panel")
        if not (incremental_paths.symbol.isin(reused_symbols).sum() == 0):
            raise ValueError("Incremental price run overlaps the frozen original ten symbols")
        reused_path_rows, reused_candidate_rows = len(frozen_paths), len(frozen_candidates)
        paths = pd.concat([frozen_paths, incremental_paths], ignore_index=True)
        candidates_frame = pd.concat([frozen_candidates, incremental_candidates], ignore_index=True)
    else:
        paths = incremental_paths
        candidates_frame = incremental_candidates
    if paths.duplicated(["arm", "event_id", "tp_r"]).any():
        raise ValueError("Duplicate native path row")
    if not paths.groupby(["arm", "event_id"]).size().eq(1).all():
        raise ValueError("Each strategy event must have one 0R native path")
    paths_out = output_dir / config["outputs"]["paths_csv_gz"]
    candidate_out = output_dir / config["outputs"]["candidate_audit_csv_gz"]
    funded_out = output_dir / config["outputs"]["funded_dir"]
    funded_out.mkdir(parents=True, exist_ok=True)
    paths_out.parent.mkdir(parents=True, exist_ok=True)
    paths.to_csv(paths_out, index=False, compression="gzip")
    candidates_frame.to_csv(candidate_out, index=False, compression="gzip")
    if reuse:
        incremental_paths.to_csv(output_dir / config["outputs"]["incremental_paths_csv_gz"],
                                 index=False, compression="gzip")
        incremental_candidates.to_csv(output_dir / config["outputs"]["incremental_candidate_audit_csv_gz"],
                                      index=False, compression="gzip")

    all_symbols = sorted(set(config["data"]["symbols"]) | set(reused_symbols))
    summary = {"experiment_id": experiment_dir.name, "symbols": all_symbols,
               "incremental_symbols": config["data"]["symbols"],
               "reused_symbols": reused_symbols,
               "starting_usdt": 1000.0, "slots": 9,
               "input_paths": {arm: int((paths.arm == arm).sum()) for arm in rows_by_arm},
               "incremental_path_rows": {arm: int((incremental_paths.arm == arm).sum()) for arm in rows_by_arm},
               "reused_original_ten_path_rows": reused_path_rows,
               "reused_original_ten_candidate_rows": reused_candidate_rows,
               "universe_counts": config["data"].get(
                   "history_counts", {"historical_source_names": len(all_symbols)}),
               "candidate_events": {}, "accounts": {},
               "symbol_audits": {**reused_symbol_audits, **symbol_audits},
               "source_config": str(config_path.relative_to(ROOT)),
               "fees": config["fees"], "periods": list(PERIODS)}
    metrics_rows = []
    quote_loader = _quote_loader(config)
    executed = paths[paths.status.eq("executed") & paths.tp_r.eq(0.0)].copy()
    if not np.array_equal(executed.exit_fee_rate.to_numpy(), np.full(len(executed), TAKER)):
        raise ValueError("Only market exits are allowed; native fee rates must all be taker")
    scenario_pools = {}
    for arm in ("breakout", "reclaim"):
        arm_rows = executed[executed.arm.eq(arm)].copy()
        real = arm_rows[~arm_rows.is_control].copy()
        controls = arm_rows[arm_rows.is_control].copy()
        matched_parent = set(controls.loc[controls.control_match_status.eq("matched"), "parent_id"])
        paired = real[real.parent_id.isin(matched_parent)].copy()
        scenarios = {"actual": real, "paired_actual": paired,
                     "matched_control": controls[controls.control_match_status.eq("matched")].copy()}
        summary["candidate_events"][arm] = {
            "signals": int((paths.arm.eq(arm) & ~paths.is_control).sum()),
            "executable_actual_paths": len(real),
            "matched_controls": len(scenarios["matched_control"]),
            "unmatched_controls": int((paths.arm.eq(arm) & paths.is_control &
                                       paths.control_match_status.eq("unmatched")).sum()),
            "paired_real_parents": len(paired),
            "actual_no_entry_reasons": paths.loc[
                paths.arm.eq(arm) & ~paths.is_control & paths.status.ne("executed"),
                "exit_reason"].value_counts().to_dict(),
            "unmatched_control_reasons": paths.loc[
                paths.arm.eq(arm) & paths.is_control & paths.control_match_status.eq("unmatched"),
                "exit_reason"].value_counts().to_dict(),
            "exit_reasons": real.exit_reason.value_counts().to_dict(),
        }
        summary["accounts"][arm] = {}
        scenario_pools[arm] = scenarios
    # Fund both complete actual accounts before paired/control diagnostics so
    # the primary two-branch result is available first on expanded runs.
    scenario_order = [("breakout", "actual"), ("reclaim", "actual"),
                      ("breakout", "paired_actual"), ("breakout", "matched_control"),
                      ("reclaim", "paired_actual"), ("reclaim", "matched_control")]
    for arm, name in scenario_order:
        sample = scenario_pools[arm][name]
        if sample.empty:
            summary["accounts"][arm][name] = {"skipped": "no funded inputs"}
            continue
        metrics, funded, equity, meta = _account_scenario(f"{arm}_{name}", sample, quote_loader)
        summary["accounts"][arm][name] = {
            "metrics": _json_metrics(metrics), "admission": meta,
            "funded_positions": len(funded),
            "exit_reasons": funded.exit_reason.value_counts().to_dict(),
        }
        funded.to_csv(funded_out / f"funded_{arm}_{name}.csv.gz", index=False, compression="gzip")
        equity.to_csv(funded_out / f"hourly_{arm}_{name}.csv.gz", index_label="hour_end_utc",
                      compression="gzip")
        metrics_rows.extend(dict(arm=arm, scenario=name, period=period, **values)
                            for period, values in metrics.items())
    pd.DataFrame(metrics_rows).to_csv(output_dir / config["outputs"]["account_metrics_csv"], index=False)
    _write_json(output_dir / config["outputs"]["audit_json"], {
        "config": config, "source_contract": config["data"],
        "symbols": {**reused_symbol_audits, **symbol_audits},
        "paths_rows_by_arm": {arm: int((paths.arm == arm).sum()) for arm in rows_by_arm},
        "incremental_paths_rows_by_arm": {arm: int((incremental_paths.arm == arm).sum()) for arm in rows_by_arm},
        "incremental_candidate_rows": len(incremental_candidates),
        "reused_frozen_paths": reuse,
        "combined_candidate_rows": len(candidates_frame),
        "vectorbt_runtime": "local VectorBT 1.2.0; existing reward_paths._order_func and reward_account.account_order",
        "path_semantics": "One native independent VectorBT column per event; one actual shared-cash nine-slot account per arm/scenario.",
    })
    _write_json(output_dir / config["outputs"]["summary_json"], summary)
    print(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2), flush=True)
    return summary


def _synthetic_event(symbol: str, side: int, source_row: int, key_open: int,
                     entry_ms: int, entry_i: int, entry: float, stop: float,
                     end_i: int, status: str = "pending") -> dict:
    parent = f"mini|{symbol}|{key_open}"
    return {
        "event_id": parent + ":signal", "parent_id": parent,
        "source_row_number": source_row, "symbol": symbol, "week": "2024-01-01",
        "side": side, "tp_r": 0.0, "is_control": False, "rank": None,
        "cohort": "mini", "is_current": True, "was_top20": True,
        "bar_source": "synthetic_fixture", "key_open_ms": key_open,
        "confirmation_ms": entry_ms, "entry_ms": entry_ms, "entry_i": entry_i,
        "entry_price": entry, "stop_price": stop,
        "risk_distance": abs(entry-stop), "risk_fraction": abs(entry-stop)/entry,
        "entry_status": status, "stop_basis": "mini",
        "path_upper_bound_reason": "mini", "end_i": end_i,
    }


def mini_check() -> dict:
    """Exercise native VBT path pricing and the shared VBT account on long/short."""
    times = np.arange(6, dtype=np.int64) * HOUR + int(pd.Timestamp("2024-01-01", tz="UTC").value // 1_000_000)
    qlong = np.array([
        [100, 101, 99, 100, np.nan, np.nan, 0],
        [100, 101, 97, 99, np.nan, np.nan, 0],
        [99, 100, 98, 99, np.nan, np.nan, 0],
        [99, 100, 98, 99, np.nan, np.nan, 0],
    ], dtype=float)
    qshort = np.array([
        [100, 101, 99, 100, np.nan, np.nan, 0],
        [100, 103, 99, 101, np.nan, np.nan, 0],
        [101, 102, 99, 100, np.nan, np.nan, 0],
        [100, 102, 99, 101, np.nan, np.nan, 0],
    ], dtype=float)
    entries = [
        _synthetic_event("BTC", 1, 1, int(times[0]-STEP), int(times[0]), 0, 100., 98., 1),
        _synthetic_event("ETH", -1, 2, int(times[0]-STEP), int(times[0]), 0, 100., 102., 1),
    ]
    output = []
    for event, q in zip(entries, (qlong, qshort), strict=True):
        event["end_i"] = len(q)-1
        rows, stats = engine._simulate_symbol([event], q, times[:len(q)], (0.0,), 1)
        if stats["orders"] != 2 or stats["trades"] != 1:
            raise AssertionError("Synthetic path did not pass through both native VectorBT fills")
        priced = rows[0]
        if priced["status"] != "executed" or priced["exit_reason"] != "stop" or priced["exit_phase"] != "intrabar":
            raise AssertionError(f"Expected intrabar fixed stop, found {priced['exit_reason']}/{priced['exit_phase']}")
        if (float(priced["exit_price"]) - event["stop_price"]) * event["side"] != 0:
            raise AssertionError("Directional stop fill price changed")
        output.append(priced)
    paths = pd.DataFrame(output)
    # Quote windows are truncated to the six synthetic bars, but keep the exact
    # entry and close timestamp contract consumed by reward_account.prepare.
    quotes = {"BTC": (times[:4], qlong[:4, :4]), "ETH": (times[:4], qshort[:4, :4])}
    events = pd.DataFrame(paths)
    prepared = wallet.prepare(events, slots=2, start=int(times[0]), end=int(times[4]),
                              quote_loader=lambda symbol: quotes[symbol])
    funded, equity = wallet.account(prepared, slip=0.0, exposure=1.0)
    if len(funded) != 2 or not np.isclose(1000.+funded.cash_pnl.sum(), equity.nav.iloc[-1], atol=.005):
        raise AssertionError("Synthetic shared cash account failed order/NAV reconciliation")
    if not funded.side.isin([1, -1]).all() or not funded.exit_reason.eq("stop").all():
        raise AssertionError("Short/long action mapping changed in shared cash account")
    return {"vectorbt_path_events": len(paths), "vectorbt_orders": 4,
            "shared_account_positions": len(funded), "account_nav_reconciled": True,
            "directions": funded.side.astype(int).tolist(),
            "stop_prices": funded.exit_price.tolist(),
            "final_nav": float(equity.nav.iloc[-1])}


def _real_smoke(config: dict) -> dict:
    """Price first real BTC breakout/reclaim if available; write no output."""
    symbol = config["mini_check"]["real_symbol"]
    row_counter = [0]
    paths, candidates, audit = _prepare_one_symbol(symbol, config, row_counter)
    found = {arm: [r for r in paths[arm] if r.get("status") == "executed" and not r.get("is_control")]
             for arm in paths}
    if not any(found.values()):
        raise ValueError(f"No real {symbol} strategy event was executable in mini smoke")
    return {"symbol": symbol, "priced_events": {k: len(v) for k,v in found.items()},
            "candidate_rows": len(candidates), "vectorbt_paths": audit["vectorbt_paths"]}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mini-check", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--run-expanded", action="store_true")
    args = parser.parse_args(argv)
    if args.mini_check:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = {"synthetic": mini_check(), "real_one_symbol": _real_smoke(config)}
        print(json.dumps(_json_safe(result), ensure_ascii=False, indent=2), flush=True)
    elif args.run_expanded:
        run(HISTORICAL_EXP / "config.json")
    else:
        run()


if __name__ == "__main__":
    main()
