"""Build native VectorBT paths for the first same-week second V1.7 reclaim.

The original frozen first-per-symbol/week V1.7 ledger remains the parent key.
For each parent, this module scans only later completed 12H candles inside the
same frozen New York Monday range. It prices the first same-direction 1.618
wick-and-close reclaim at the exact coincident 1H open, then delegates all
independent runner/2R path exits to the existing VectorBT order callback.

The executable input ``paths.csv.gz`` contains only own-time 90-day-eligible
events with an actual entry, with one ``tp_r=0`` and one ``tp_r=2`` row per
event. ``candidate_audit.csv.gz`` retains every frozen parent, including no
second reclaim, missing-open and ineligible cases. No account is simulated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import zlib

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_leadership as leadership
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.monitor.cloud_signal_plans import fixed_stop


ROOT = engine.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-second-reclaim-20261109-v1"
SOURCE = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1"
PATH_CONFIG = ROOT / "experiments/active/exp-cloud-close-reward-20261109-v1/paths_config.json"
BAR_MS = 12 * engine.HOUR_MS
TP_R = (0.0, 2.0)
DEFAULT_BATCH_SIZE = 8
IDENTITY_COLUMNS = [
    "rank", "symbol", "week", "side", "key_open_bjt", "confirmed_bjt",
    "reference_entry", "confirmation_close", "reference_stop", "stop_pct",
    "opposite_target", "bar_source", "prior_atr_pct", "is_current",
    "was_top20", "cohort",
]
ACCOUNT_COLUMNS = [
    "event_id", "parent_id", "is_control", "symbol", "side", "entry_ms",
    "entry_price", "stop_price", "entry_order", "exit_bar_ms", "exit_price",
    "exit_fee_rate", "exit_phase", "exit_reason", "tp_r",
]


def _ms(value) -> int | None:
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError(f"Timezone required for frozen signal timestamp: {value}")
    return int(stamp.tz_convert("UTC").value // 1_000_000)


def _bool(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_config(config_path: Path) -> tuple[dict, dict, Path, Path, Path, Path, Path]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    path_config = json.loads(PATH_CONFIG.read_text(encoding="utf-8"))
    experiment_id = config.get("experiment_id", config.get("id"))
    if experiment_id != "exp-cloud-close-second-reclaim-20261109-v1":
        raise ValueError(f"Unexpected experiment ID: {experiment_id}")
    signal_path = ROOT / path_config["signals_path"]
    output_path = ROOT / config.get(
        "output_paths_csv_gz", f"experiments/active/{experiment_id}/paths.csv.gz"
    )
    candidate_path = ROOT / config.get(
        "output_candidates_csv_gz",
        f"experiments/active/{experiment_id}/candidate_audit.csv.gz",
    )
    coverage_path = ROOT / config.get(
        "output_coverage_csv", f"experiments/active/{experiment_id}/coverage.csv"
    )
    audit_path = ROOT / config.get(
        "output_audit_json", f"experiments/active/{experiment_id}/paths_audit.json"
    )
    return config, path_config, signal_path, output_path, candidate_path, coverage_path, audit_path


def _anchors(bars1: pd.DataFrame, bars12: pd.DataFrame, symbol: str) -> dict[str, dict]:
    """Rebuild the exact complete New York Monday 1H range used by V1.7."""
    calendar: dict[str, dict] = {}
    for value in bars12.index.to_numpy(dtype=np.int64):
        window = engine._ny_week_window(int(value))
        week = window["week"]
        if week in calendar:
            continue
        expected = np.arange(window["start"], window["monday_end"], engine.HOUR_MS,
                             dtype=np.int64)
        monday = bars1.reindex(expected)
        valid = monday[["open", "high", "low", "close"]].notna().all(axis=1)
        state = "ready" if bool(valid.all()) else "missing_hours"
        high = float(monday.high.max()) if len(monday) else math.nan
        low = float(monday.low.min()) if len(monday) else math.nan
        if state == "ready" and high <= low:
            state = "empty_range"
        calendar[week] = {
            "week": week, "symbol": symbol, "state": state,
            "end_ms": int(window["monday_end"]),
            "expires_ms": int(window["expires"]),
            "high": high, "low": low,
            "lower": low - .618 * (high - low),
            "upper": high + .618 * (high - low),
            "observed_hours": int(valid.sum()),
        }
    return calendar


def _signal_at(index: int, symbol: str, bars12: pd.DataFrame, features: dict,
               calendar: dict[str, dict], cutoff_ms: int) -> dict | None:
    """Pure V1.7 single-bar rule, copied from cloud_signals.signal (fib mode).

    Uses only the completed signal 12H OHLC, its confirmed EMA20/EMA50, and the
    already completed NY Monday 1H range. No outcome, future candle or position
    state enters this function.
    """
    time_ms = int(bars12.index[index])
    if time_ms % BAR_MS or time_ms + BAR_MS > cutoff_ms:
        return None
    window = engine._ny_week_window(time_ms)
    anchor = calendar.get(window["week"])
    if (anchor is None or anchor["state"] != "ready"
            or anchor["symbol"] != symbol
            or not int(anchor["end_ms"]) <= time_ms < time_ms + BAR_MS <= int(anchor["expires_ms"])):
        return None
    if not bool(features["ema_ready"][index]):
        return None
    ema20 = float(features["ema20"][index])
    ema50 = float(features["ema50"][index])
    row = bars12.iloc[index]
    close, low, high = float(row.close), float(row.low), float(row.high)
    lower, upper = float(anchor["lower"]), float(anchor["upper"])
    if not all(math.isfinite(x) and x > 0 for x in (close, lower, upper)):
        return None
    side = (1 if ema20 > ema50 and low < lower < close < upper else
            -1 if ema20 < ema50 and lower < close < upper < high else 0)
    if not side:
        return None
    entry, target = (lower, upper) if side == 1 else (upper, lower)
    stop, stop_pct = fixed_stop(symbol, side, entry)
    if stop is None:
        return None
    risk, reward = (entry - stop) * side, (target - entry) * side
    if not all(math.isfinite(x) and x > 0 for x in (entry, stop, target, risk, reward)):
        return None
    return {
        "symbol": symbol, "week": window["week"], "side": side,
        "key_open_ms": time_ms, "confirmation_ms": time_ms + BAR_MS,
        "signal_reference_entry": float(entry),
        "signal_reference_stop": float(stop),
        "opposite_target": float(target), "stop_pct": float(stop_pct),
        "planned_rr": float(reward / risk),
        "confirmation_close": close, "ema20": ema20, "ema50": ema50,
        "signal_open": float(row.open), "signal_high": high, "signal_low": low,
        "anchor_lower": lower, "anchor_upper": upper,
        "anchor_expires_ms": int(anchor["expires_ms"]),
        "bar_index": int(index),
    }


def _score(scores: pd.Series, time_ms: int, symbol: str) -> float:
    value = scores.get((int(time_ms), str(symbol)), np.nan)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.nan


def _rank_clock_fields(entry_ms: int | None, symbol: str, side: int,
                       scores: pd.Series) -> dict:
    value = _score(scores, entry_ms, symbol) if entry_ms is not None else math.nan
    return {
        "coverage_rank_90d": value if math.isfinite(value) else None,
        "leadership_rank_90d": (value if side > 0 else 1.0 - value)
            if math.isfinite(value) else None,
        "eligible_90d": bool(math.isfinite(value)),
        "rank_source_bar_open_ms": int(entry_ms - 2 * BAR_MS) if entry_ms is not None else None,
        "rank_last_closed_ms": int(entry_ms - BAR_MS) if entry_ms is not None else None,
    }


def _parent_id(source_row_number: int) -> str:
    return f"signal-row-{int(source_row_number):05d}"


def _verify_first_keys(symbol: str, parent_rows: list[dict], first_native: dict[str, dict],
                       features: dict) -> tuple[dict[str, dict], dict]:
    """Reconcile every frozen source row against the original first-per-week key."""
    by_week = {str(row["week"]): row for row in parent_rows}
    if len(by_week) != len(parent_rows):
        raise ValueError(f"Frozen source duplicates symbol/week: {symbol}")
    if set(by_week) != set(first_native):
        absent = sorted(set(by_week) - set(first_native))[:5]
        extra = sorted(set(first_native) - set(by_week))[:5]
        raise ValueError(f"Frozen first-key weeks differ for {symbol}; absent={absent}, extra={extra}")
    index = {int(t): i for i, t in enumerate(features["time12"])}
    checks = 0
    for week, source in by_week.items():
        native = first_native[week]
        key_open = _ms(source["key_open_bjt"])
        confirm = _ms(source["confirmed_bjt"])
        if (key_open != native["key_open_ms"] or confirm != native["confirmation_ms"]
                or int(source["side"]) != native["side"]
                or week != native["week"]):
            raise ValueError(f"Frozen V1.7 parent identity differs: {symbol}/{week}")
        for field, expected in (("reference_entry", native["signal_reference_entry"]),
                                ("reference_stop", native["signal_reference_stop"]),
                                ("opposite_target", native["opposite_target"])):
            actual = float(source[field])
            if not np.isclose(actual, expected, rtol=1.e-10, atol=1.e-12):
                raise ValueError(f"Frozen {field} differs for {symbol}/{week}: {actual} != {expected}")
        i = index[key_open]
        prior_atr = float(features["prior_atr_pct"][i])
        source_atr = float(source["prior_atr_pct"]) if pd.notna(source["prior_atr_pct"]) else math.nan
        if math.isfinite(source_atr) != math.isfinite(prior_atr):
            raise ValueError(f"Frozen parent prior ATR availability differs: {symbol}/{week}")
        if math.isfinite(source_atr) and not np.isclose(source_atr, prior_atr, rtol=1.e-9, atol=1.e-10):
            raise ValueError(f"Frozen parent prior ATR differs: {symbol}/{week}")
        checks += 1
    return by_week, {"parent_rows": len(parent_rows), "native_first_keys": len(first_native),
                     "identities_exact": checks, "all_weeks_match": True}


def _candidate_row(parent: dict, candidate: dict | None, *, parent_rank: dict,
                   parent_prior_atr: float | None, sequence_status: str = "",
                   sequence_censor_ms: int | None = None,
                   expected_sequence_bars: int = 0,
                   observed_sequence_bars: int = 0,
                   later_reclaim_count: int = 0) -> dict:
    row = {
        "parent_id": parent["parent_id"],
        "source_row_number": int(parent["source_row_number"]),
        "symbol": parent["symbol"], "week": parent["week"],
        "side": int(parent["side"]), "parent_rank": parent.get("rank"),
        "was_top20": _bool(parent.get("was_top20")),
        "is_current": _bool(parent.get("is_current")),
        "cohort": parent.get("cohort"), "bar_source": parent.get("bar_source"),
        "parent_key_open_ms": int(parent["key_open_ms"]),
        "parent_confirmation_ms": int(parent["confirmation_ms"]),
        "parent_reference_entry": float(parent["reference_entry"]),
        "parent_reference_stop": float(parent["reference_stop"]),
        "parent_prior_atr_pct": parent_prior_atr,
        "parent_coverage_rank_90d": parent_rank.get("coverage_rank_90d"),
        "parent_eligible_90d": parent_rank.get("eligible_90d", False),
        "candidate_status": "candidate_found" if candidate is not None else sequence_status,
        "first_later_observed_reclaim_key_open_ms": None,
        "sequence_censor_ms": sequence_censor_ms,
        "expected_sequence_bars": int(expected_sequence_bars),
        "observed_sequence_bars": int(observed_sequence_bars),
        "later_qualifying_reclaim_count": int(later_reclaim_count),
        "candidate_event_id": None, "candidate_key_open_ms": None,
        "candidate_confirmation_ms": None, "candidate_entry_ms": None,
        "candidate_entry_price": None, "candidate_stop_price": None,
        "candidate_risk_distance": None, "candidate_risk_fraction": None,
        "candidate_entry_order": None, "candidate_coverage_rank_90d": None,
        "candidate_leadership_rank_90d": None, "candidate_eligible_90d": False,
        "candidate_entry_status": sequence_status if candidate is None else "candidate_found",
        "candidate_entry_beyond_stop": None,
        "candidate_prior_atr_pct": None, "candidate_signal_ema20": None,
        "candidate_signal_ema50": None, "candidate_control_match_status": "not_applicable",
        "candidate_control_match_reason": "",
        "control_event_id": None, "control_confirmation_ms": None,
        "control_entry_order": None, "control_coverage_rank_90d": None,
        "control_eligible_90d": None,
    }
    if candidate is not None:
        for name, source in (("candidate_key_open_ms", "key_open_ms"),
                             ("candidate_confirmation_ms", "confirmation_ms")):
            row[name] = int(candidate[source])
        row["candidate_signal_ema20"] = float(candidate["ema20"])
        row["candidate_signal_ema50"] = float(candidate["ema50"])
    return row


def _sequence_observation(parent_confirmation_ms: int, expires_ms: int,
                          bar_opens: np.ndarray, candidate: dict | None) -> dict:
    """Classify second-signal observability without treating gaps as negatives.

    A candidate can be called the first subsequent reclaim only if every
    expected 12H bar from the parent confirmation through that candidate key
    was observed. If no candidate exists, all candidate-capable bars up to the
    last confirmation strictly before expiry must be present to call the week
    complete. The final possible key is the last UTC-aligned 12H open whose
    close is still strictly earlier than the NY week expiry.
    """
    first_open = int(parent_confirmation_ms)
    # Native 12H opens are UTC aligned while the NY week expiry is usually
    # 04:00/05:00 UTC. Keep the last aligned open whose close is strictly
    # before expiry; equality to expiry is intentionally not eligible.
    last_allowed_open = ((int(expires_ms) - BAR_MS - 1) // BAR_MS) * BAR_MS
    if candidate is not None:
        limit_open = int(candidate["key_open_ms"])
        status_if_complete = "candidate_sequence_complete"
    else:
        limit_open = last_allowed_open
        status_if_complete = "complete_no_second_reclaim"
    if limit_open < first_open:
        return {"status": status_if_complete, "censor_ms": None,
                "expected": 0, "observed": 0, "missing": 0}
    expected = np.arange(first_open, limit_open + BAR_MS, BAR_MS, dtype=np.int64)
    if not len(expected):
        return {"status": status_if_complete, "censor_ms": None,
                "expected": 0, "observed": 0, "missing": 0}
    observed = np.isin(expected, bar_opens)
    missing_indices = np.flatnonzero(~observed)
    if not len(missing_indices):
        return {"status": status_if_complete, "censor_ms": None,
                "expected": int(len(expected)), "observed": int(observed.sum()), "missing": 0}
    first_missing = int(expected[int(missing_indices[0])])
    later_observed = bool(np.any((bar_opens > first_missing) & (bar_opens <= limit_open)))
    status = "right_censored_12h_gap" if later_observed else "right_censored_data_tail"
    return {"status": status, "censor_ms": first_missing,
            "expected": int(len(expected)), "observed": int(observed.sum()),
            "missing": int(len(missing_indices))}


def _make_real_event(parent: dict, native: dict, bars12: pd.DataFrame, q: np.ndarray,
                     bar_ms: np.ndarray, features: dict, hour_index: dict[int, int],
                     scores: pd.Series, p12: Path, p1: Path) -> dict:
    confirmation = int(native["confirmation_ms"])
    idx = hour_index.get(confirmation)
    i12 = int(native["bar_index"])
    prior_atr = float(features["prior_atr_pct"][i12])
    prior_atr_value = prior_atr if math.isfinite(prior_atr) else math.nan
    rank = _rank_clock_fields(confirmation, str(parent["symbol"]), int(parent["side"]), scores)
    entry_price = float(q[idx, 0]) if idx is not None else None
    stop = float(native["signal_reference_stop"])
    risk = abs(entry_price - stop) if entry_price is not None else None
    risk_fraction = risk / entry_price if entry_price is not None and entry_price > 0 else None
    entry_status = "pending"
    if idx is None:
        entry_status = "no_entry_missing_confirmation_hour_open"
    elif not rank["eligible_90d"]:
        entry_status = "not_eligible_90d_at_own_entry"
    else:
        entry_status = "eligible_zero_risk_native" if risk == 0 else "eligible_executable"
    event_id = parent["parent_id"] + ":second_reclaim"
    week = str(native["week"])
    close_stamp = pd.Timestamp(confirmation, unit="ms", tz="UTC").isoformat()
    parent_meta = {
        "parent_id": parent["parent_id"],
        "source_row_number": int(parent["source_row_number"]),
        "symbol": str(parent["symbol"]), "week": week, "side": int(native["side"]),
        "rank": parent.get("rank"), "cohort": parent.get("cohort"),
        "was_top20": _bool(parent.get("was_top20")),
        "is_current": _bool(parent.get("is_current")),
        "bar_source": parent.get("bar_source"),
        "parent_key_open_ms": int(parent["key_open_ms"]),
        "parent_confirmation_ms": int(parent["confirmation_ms"]),
        "parent_reference_entry": float(parent["reference_entry"]),
        "parent_reference_stop": float(parent["reference_stop"]),
        "parent_opposite_target": float(parent["opposite_target"]),
        "parent_rank": parent.get("rank"),
        "parent_prior_atr_pct": (float(parent["prior_atr_pct"])
                                  if pd.notna(parent.get("prior_atr_pct")) else None),
        "candidate_key_open_ms": int(native["key_open_ms"]),
        "candidate_confirmation_ms": confirmation,
        "candidate_signal_id": f"ema20-50-12h-monday-wick-v1:{parent['symbol']}:{native['key_open_ms']}:{native['side']}",
        "signal_reference_entry": float(native["signal_reference_entry"]),
        "signal_reference_stop": float(native["signal_reference_stop"]),
        "opposite_target": float(native["opposite_target"]),
        "candidate_confirmation_close": float(native["confirmation_close"]),
        "candidate_signal_ema20": float(native["ema20"]),
        "candidate_signal_ema50": float(native["ema50"]),
        "candidate_prior_atr_pct": prior_atr_value if math.isfinite(prior_atr_value) else None,
        "signal_prior_atr_pct": prior_atr_value,
        "entry_order": prior_atr_value if math.isfinite(prior_atr_value) else math.inf,
        "entry_ms": confirmation if idx is not None else None,
        "entry_price": entry_price,
        "stop_price": stop,
        "risk_distance": risk,
        "risk_fraction": risk_fraction,
        "entry_i": int(idx) if idx is not None else None,
        "entry_status": entry_status,
        "entry_source": "exact_confirmation_1h_open",
        "entry_beyond_stop": bool((entry_price - stop) * int(native["side"]) <= 0)
            if entry_price is not None else None,
        "stop_basis": "same_frozen_ny_monday_1618_reference_fixed_stop",
        "is_control": False,
        "event_id": event_id,
        "confirmed_bjt": close_stamp,
        "confirmation_ms": confirmation,
        "key_open_ms": int(native["key_open_ms"]),
        "confirmation_12h_bar_present": True,
        "source_path_12h": _relative(p12),
        "source_path_1h": _relative(p1),
        "control_match_status": "unmatched",
        "control_match_reason": "not_matched_yet",
        "eligible_at_own_time": bool(rank["eligible_90d"]),
        **rank,
    }
    # Existing leadership() deliberately indexes the prior close at entry_ms.
    # This source key must be the bar ending at the signal key's opening time.
    if confirmation % BAR_MS != 0:
        raise ValueError(f"Secondary confirmation is off the native 12H grid: {event_id}")
    return parent_meta


def _match_controls(real_events: list[dict], bars12: pd.DataFrame, q: np.ndarray,
                    bar_ms: np.ndarray, features: dict, scores: pd.Series,
                    all_v17_confirmations: set[int], config: dict) -> tuple[list[dict], dict]:
    """Sample one same-symbol/week non-signal control for each eligible event."""
    controls: list[dict] = []
    reasons: Counter = Counter()
    unmatched: dict[str, str] = {}
    if bars12.empty or len(bar_ms) == 0:
        return [], {"matched": 0, "unmatched": len(real_events),
                    "unmatched_reasons": {"missing_input_bars": len(real_events)},
                    "unmatched_by_event": {e["event_id"]: "missing_input_bars" for e in real_events}}
    index12 = bars12.index.to_numpy(dtype=np.int64)
    index1 = bar_ms
    map_hour = {int(t): i for i, t in enumerate(index1)}
    prior_atr = np.asarray(features["prior_atr_pct"], dtype=float)
    ema_side = np.sign(np.asarray(features["ema20"], dtype=float)
                       - np.asarray(features["ema50"], dtype=float))
    ready = np.asarray(features["ema_ready"], dtype=bool)
    week_keys = np.asarray(features["week_keys"], dtype=object)
    week_positions = {str(week): np.flatnonzero(week_keys == week)
                      for week in np.unique(week_keys)}
    tolerance = float(config["control_matching"]["atr_relative_tolerance"])
    seed = int(config["control_matching"]["seed"])
    used: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    signal_times = np.asarray(sorted(all_v17_confirmations), dtype=np.int64)
    for parent in sorted(real_events, key=lambda e: (e["confirmation_ms"], e["source_row_number"])):
        if parent.get("entry_i") is None:
            reason = "signal_missing_confirmation_hour_open"
            reasons[reason] += 1
            unmatched[parent["event_id"]] = reason
            continue
        risk_fraction = parent.get("risk_fraction")
        if risk_fraction is None or not math.isfinite(float(risk_fraction)) or float(risk_fraction) <= 0:
            reason = "signal_nonpositive_actual_risk"
            reasons[reason] += 1
            unmatched[parent["event_id"]] = reason
            continue
        signal_atr = parent.get("signal_prior_atr_pct")
        if signal_atr is None or not math.isfinite(float(signal_atr)) or float(signal_atr) <= 0:
            reason = "signal_prior_atr_unavailable"
            reasons[reason] += 1
            unmatched[parent["event_id"]] = reason
            continue
        event_week = str(parent["week"])
        side = int(parent["side"])
        window = engine._ny_week_window(int(parent["key_open_ms"]))
        week_idx = week_positions.get(event_week, np.empty(0, dtype=np.int64))
        week_conf = index12[week_idx] + BAR_MS
        base_mask = ((week_conf > int(parent["confirmation_ms"]))
                     & (index12[week_idx] >= int(window["monday_end"]))
                     & (week_conf <= int(window["expires"]))
                     & ready[week_idx] & (ema_side[week_idx] == side))
        if len(week_idx):
            base_mask &= np.isin(week_conf, index1)
            base_mask &= ~np.isin(week_conf, signal_times)
        base_idx = week_idx[base_mask]
        saw_later_directional = len(base_idx) > 0
        atr_mask = (np.isfinite(prior_atr[base_idx]) & (prior_atr[base_idx] > 0)
                    & (np.abs(prior_atr[base_idx] / float(signal_atr) - 1.0) <= tolerance))
        atr_idx = base_idx[atr_mask]
        saw_atr_match = len(atr_idx) > 0
        if saw_atr_match:
            rank_index = pd.MultiIndex.from_arrays(
                [index12[atr_idx] + BAR_MS,
                 np.repeat(str(parent["symbol"]), len(atr_idx))],
                names=scores.index.names,
            )
            rankable_mask = scores.reindex(rank_index).notna().to_numpy()
            candidate_indices = atr_idx[rankable_mask].tolist()
        else:
            candidate_indices = []
        saw_rankable = bool(candidate_indices)
        used_key = (str(parent["symbol"]), event_week, side)
        candidate_indices = [i for i in candidate_indices if i not in used[used_key]]
        if not candidate_indices:
            if used_key in used and saw_rankable:
                reason = "later_matching_candidates_already_sampled"
            elif not saw_later_directional:
                reason = "no_non_signal_same_week_directional_ema_candidate"
            elif not saw_atr_match:
                reason = "no_later_candidate_within_atr_tolerance"
            elif not saw_rankable:
                reason = "no_90d_eligible_later_control"
            else:
                reason = "no_exact_confirmation_hour_open"
            reasons[reason] += 1
            unmatched[parent["event_id"]] = reason
            continue
        stable_seed = (seed + zlib.crc32(parent["event_id"].encode("utf-8"))) % (2 ** 32)
        rng = np.random.default_rng(stable_seed)
        chosen = int(candidate_indices[int(rng.integers(0, len(candidate_indices)))])
        used[used_key].add(chosen)
        confirmation = int(index12[chosen] + BAR_MS)
        entry_i = map_hour[confirmation]
        entry_price = float(q[entry_i, 0])
        stop = float(entry_price - side * float(risk_fraction) * entry_price)
        risk = abs(entry_price - stop)
        control_id = parent["parent_id"] + ":second_reclaim:control"
        rank = _rank_clock_fields(confirmation, str(parent["symbol"]), side, scores)
        atr = float(prior_atr[chosen])
        control = {
            **{key: parent.get(key) for key in (
                "parent_id", "source_row_number", "symbol", "week", "side", "rank",
                "was_top20", "is_current", "cohort", "bar_source", "parent_key_open_ms",
                "parent_confirmation_ms", "parent_reference_entry", "parent_reference_stop",
                "parent_opposite_target", "candidate_key_open_ms", "candidate_confirmation_ms",
                "candidate_signal_id", "candidate_signal_ema20", "candidate_signal_ema50",
                "candidate_prior_atr_pct", "candidate_event_id", "candidate_coverage_rank_90d",
                "candidate_leadership_rank_90d", "candidate_eligible_90d")},
            "event_id": control_id, "id": control_id, "is_control": True,
            "control_match_status": "matched", "control_match_reason": "",
            "control_confirmation_ms": confirmation,
            "control_12h_open_ms": int(index12[chosen]),
            "control_prior_atr_pct": atr,
            "control_atr_relative_difference": atr / float(signal_atr) - 1.0,
            "control_match_method": "same_symbol_week_side_later_ema_non_signal_atr_25pct",
            "key_open_ms": int(index12[chosen]), "confirmation_ms": confirmation,
            "confirmed_bjt": pd.Timestamp(confirmation, unit="ms", tz="UTC").isoformat(),
            "entry_ms": confirmation, "entry_i": int(entry_i),
            "entry_price": entry_price, "stop_price": stop,
            "risk_distance": risk,
            "risk_fraction": risk / entry_price if entry_price > 0 else None,
            "signal_prior_atr_pct": atr,
            "entry_order": atr,
            "entry_status": "eligible_executable",
            "entry_source": "matched_non_signal_confirmation_1h_open",
            "entry_beyond_stop": False,
            "stop_basis": "matched_second_reclaim_actual_risk_fraction",
            "candidate_event_id": parent["event_id"],
            "source_path_12h": parent["source_path_12h"],
            "source_path_1h": parent["source_path_1h"],
            "eligible_at_own_time": True,
            **rank,
        }
        controls.append(control)
    return controls, {"matched": len(controls), "unmatched": len(unmatched),
                     "unmatched_reasons": dict(reasons), "unmatched_by_event": unmatched}


def _run_symbol(symbol: str, parent_rows: list[dict], path_config: dict,
                scores: pd.Series, cutoff_ms: int, batch_size: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    was_top20 = _bool(parent_rows[0].get("was_top20"))
    p12, p1, source_group = engine._choose_input(symbol, was_top20, path_config)
    bars12, bars1 = engine._load_bars(p12, "12h"), engine._load_bars(p1, "1h")
    q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
    features = prepared.get("features", {})
    if bars12.empty or bars1.empty:
        raise ValueError(f"Missing frozen 12H/H1 input for parent symbol {symbol}")
    calendar = _anchors(bars1, bars12, symbol)
    index12 = bars12.index.to_numpy(dtype=np.int64)
    first_native: dict[str, dict] = {}
    all_native: list[dict] = []
    for i in range(len(index12)):
        event = _signal_at(i, symbol, bars12, features, calendar, cutoff_ms)
        if event is None:
            continue
        all_native.append(event)
        first_native.setdefault(str(event["week"]), event)
    by_week, identity = _verify_first_keys(symbol, parent_rows, first_native, features)
    native_by_week: dict[str, list[dict]] = defaultdict(list)
    for event in all_native:
        native_by_week[str(event["week"])].append(event)
    hour_index = {int(t): i for i, t in enumerate(bar_ms)}
    p12_name, p1_name = _relative(p12), _relative(p1)
    candidate_audit: list[dict] = []
    real_events: list[dict] = []
    all_v17_confirmations = {int(row["confirmation_ms"]) for row in all_native}
    candidate_reclaims = 0
    observed_later_reclaims = 0
    complete_no_second = 0
    censored_before_candidate = 0
    censor_reasons: Counter = Counter()
    missing_hour = 0
    ineligible = 0
    zero_risk = 0
    beyond_stop = 0
    for week, source in sorted(by_week.items(), key=lambda kv: kv[1]["source_row_number"]):
        parent_id = _parent_id(int(source["source_row_number"]))
        source["parent_id"] = parent_id
        source["key_open_ms"] = int(_ms(source["key_open_bjt"]))
        source["confirmation_ms"] = int(_ms(source["confirmed_bjt"]))
        source["symbol"] = str(source["symbol"])
        source["week"] = str(source["week"])
        source["side"] = int(source["side"])
        parent_index = int(np.searchsorted(index12, source["key_open_ms"]))
        if parent_index >= len(index12) or int(index12[parent_index]) != source["key_open_ms"]:
            raise ValueError(f"Frozen parent 12H open missing from input: {parent_id}")
        parent_atr = float(features["prior_atr_pct"][parent_index])
        parent_atr_value = parent_atr if math.isfinite(parent_atr) else None
        parent_rank = _rank_clock_fields(source["confirmation_ms"], symbol, source["side"], scores)
        later = [event for event in native_by_week.get(week, [])
                 if event["confirmation_ms"] > source["confirmation_ms"]
                 and event["confirmation_ms"] < int(event["anchor_expires_ms"])
                 and int(event["side"]) == int(source["side"])]
        later.sort(key=lambda event: (event["confirmation_ms"], event["key_open_ms"]))
        expiry_ms = int(calendar[week]["expires_ms"])
        if not later:
            sequence = _sequence_observation(source["confirmation_ms"], expiry_ms,
                                             index12, None)
            if sequence["status"] == "complete_no_second_reclaim":
                complete_no_second += 1
            else:
                censor_reasons[sequence["status"]] += 1
            row = _candidate_row(
                source, None, parent_rank=parent_rank, parent_prior_atr=parent_atr_value,
                sequence_status=sequence["status"], sequence_censor_ms=sequence["censor_ms"],
                expected_sequence_bars=sequence["expected"],
                observed_sequence_bars=sequence["observed"], later_reclaim_count=0)
            row["candidate_entry_status"] = sequence["status"]
            candidate_audit.append(row)
            continue
        observed_later_reclaims += 1
        observed_native = later[0]
        sequence = _sequence_observation(source["confirmation_ms"], expiry_ms,
                                         index12, observed_native)
        if sequence["status"] != "candidate_sequence_complete":
            censored_before_candidate += 1
            censor_reasons["right_censored_before_observed_reclaim"] += 1
            row = _candidate_row(
                source, None, parent_rank=parent_rank, parent_prior_atr=parent_atr_value,
                sequence_status="right_censored_before_observed_reclaim",
                sequence_censor_ms=sequence["censor_ms"],
                expected_sequence_bars=sequence["expected"],
                observed_sequence_bars=sequence["observed"], later_reclaim_count=len(later))
            row["first_later_observed_reclaim_key_open_ms"] = int(observed_native["key_open_ms"])
            row["first_later_observed_reclaim_confirmation_ms"] = int(observed_native["confirmation_ms"])
            row["candidate_entry_status"] = "right_censored_before_observed_reclaim"
            candidate_audit.append(row)
            continue
        native = observed_native
        candidate_reclaims += 1
        event = _make_real_event(source, native, bars12, q, bar_ms, features,
                                 hour_index, scores, p12, p1)
        event["source_path_12h"], event["source_path_1h"] = p12_name, p1_name
        event["sequence_status"] = sequence["status"]
        event["sequence_censor_ms"] = sequence["censor_ms"]
        event["expected_sequence_bars"] = sequence["expected"]
        event["observed_sequence_bars"] = sequence["observed"]
        if event["entry_i"] is None:
            missing_hour += 1
        elif not event["eligible_90d"]:
            ineligible += 1
        if event["risk_distance"] == 0:
            zero_risk += 1
        if event["entry_beyond_stop"]:
            beyond_stop += 1
        audit_row = _candidate_row(
            source, native, parent_rank=parent_rank, parent_prior_atr=parent_atr_value,
            sequence_status=sequence["status"], sequence_censor_ms=sequence["censor_ms"],
            expected_sequence_bars=sequence["expected"],
            observed_sequence_bars=sequence["observed"], later_reclaim_count=len(later))
        audit_row.update({
            "candidate_event_id": event["event_id"],
            "candidate_status": event["entry_status"],
            "candidate_confirmation_ms": event["confirmation_ms"],
            "candidate_entry_ms": event["entry_ms"],
            "candidate_entry_price": event["entry_price"],
            "candidate_stop_price": event["stop_price"],
            "candidate_risk_distance": event["risk_distance"],
            "candidate_risk_fraction": event["risk_fraction"],
            "candidate_entry_order": event["entry_order"],
            "candidate_prior_atr_pct": event["candidate_prior_atr_pct"],
            "candidate_coverage_rank_90d": event["coverage_rank_90d"],
            "candidate_leadership_rank_90d": event["leadership_rank_90d"],
            "candidate_eligible_90d": event["eligible_90d"],
            "candidate_entry_status": event["entry_status"],
            "candidate_entry_beyond_stop": event["entry_beyond_stop"],
            "candidate_signal_ema20": event["candidate_signal_ema20"],
            "candidate_signal_ema50": event["candidate_signal_ema50"],
            "candidate_signal_reference_entry": event["signal_reference_entry"],
            "candidate_signal_reference_stop": event["signal_reference_stop"],
            "candidate_opposite_target": event["opposite_target"],
        })
        if event["entry_i"] is not None and event["eligible_90d"]:
            real_events.append(event)
        candidate_audit.append(audit_row)

    controls, control_summary = _match_controls(real_events, bars12, q, bar_ms,
                                                features, scores, all_v17_confirmations,
                                                path_config)
    controls_by_parent = {event["parent_id"]: event for event in controls}
    unmatched_by_parent = control_summary["unmatched_by_event"]
    for row in candidate_audit:
        candidate_id = row.get("candidate_event_id")
        if candidate_id is None:
            continue
        match = controls_by_parent.get(row["parent_id"])
        if match is not None:
            row.update({
                "candidate_control_match_status": "matched",
                "candidate_control_match_reason": "",
                "control_event_id": match["event_id"],
                "control_confirmation_ms": match["confirmation_ms"],
                "control_entry_order": match["entry_order"],
                "control_coverage_rank_90d": match["coverage_rank_90d"],
                "control_eligible_90d": match["eligible_90d"],
            })
        elif candidate_id in unmatched_by_parent:
            row["candidate_control_match_status"] = "unmatched"
            row["candidate_control_match_reason"] = unmatched_by_parent[candidate_id]
        elif row.get("candidate_eligible_90d") is False:
            row["candidate_control_match_status"] = "not_eligible"
            row["candidate_control_match_reason"] = "real_candidate_not_90d_eligible"
        elif row.get("candidate_entry_ms") is None:
            row["candidate_control_match_status"] = "not_executable"
            row["candidate_control_match_reason"] = "real_candidate_has_no_exact_h1_entry"
        else:
            row["candidate_control_match_status"] = "not_matched"
            row["candidate_control_match_reason"] = "no_control_record"
    for event in real_events:
        match = controls_by_parent.get(event["parent_id"])
        if match:
            event["control_match_status"] = "matched"
            event["control_match_reason"] = ""
            event["control_event_id"] = match["event_id"]
        else:
            event["control_match_status"] = "unmatched"
            event["control_match_reason"] = unmatched_by_parent.get(event["event_id"], "no_eligible_random_match")
            event["control_event_id"] = None

    events = [*real_events, *controls]
    execution = {"columns": 0, "orders": 0, "trades": 0}
    path_rows: list[dict] = []
    if events:
        metadata = {}
        for event in events:
            event["end_i"], event["path_upper_bound_reason"] = engine._safe_path_end(
                int(event["entry_i"]), int(event["side"]), q, bar_ms)
            metadata[event["event_id"]] = event
        path_rows, execution = engine._simulate_symbol(events, q, bar_ms, TP_R, batch_size)
        for row in path_rows:
            source = metadata[row["event_id"]]
            for key in (
                "entry_order", "eligible_90d", "eligible_at_own_time", "coverage_rank_90d",
                "leadership_rank_90d", "rank_source_bar_open_ms", "rank_last_closed_ms",
                "signal_prior_atr_pct", "candidate_prior_atr_pct", "candidate_event_id",
                "candidate_key_open_ms", "candidate_confirmation_ms", "parent_key_open_ms",
                "parent_confirmation_ms", "parent_reference_entry", "parent_reference_stop",
                "parent_opposite_target", "parent_rank", "parent_prior_atr_pct",
                "candidate_signal_id", "candidate_signal_ema20", "candidate_signal_ema50",
                "sequence_status", "sequence_censor_ms", "expected_sequence_bars",
                "observed_sequence_bars",
                "signal_reference_entry", "signal_reference_stop", "opposite_target",
                "control_match_status", "control_match_reason", "control_event_id",
                "control_confirmation_ms", "control_prior_atr_pct",
                "control_atr_relative_difference", "control_match_method",
                "entry_beyond_stop", "entry_source", "stop_basis",
            ):
                if key in source:
                    row[key] = source[key]
            row["eligible_90d"] = bool(source["eligible_90d"])
            row["entry_order"] = float(source["entry_order"])
            row["is_control"] = bool(source["is_control"])
    paths = pd.DataFrame(path_rows)
    candidates = pd.DataFrame(candidate_audit)
    if not paths.empty:
        if paths.duplicated(["event_id", "tp_r"]).any():
            raise AssertionError(f"Duplicate (event_id,tp_r) paths in {symbol}")
        counts_per_event = paths.groupby("event_id").tp_r.agg(lambda values: set(values.astype(float)))
        if not counts_per_event.map(lambda values: values == set(TP_R)).all():
            raise AssertionError(f"Each executable event must have tp_r={TP_R}: {symbol}")
        if not paths.eligible_90d.astype(bool).all() or not paths.entry_status.eq("executed").all():
            raise AssertionError(f"Ineligible/no-entry row reached executable paths: {symbol}")
        missing_account = sorted(set(ACCOUNT_COLUMNS) - set(paths.columns))
        if missing_account:
            raise AssertionError(f"Account path interface missing columns: {missing_account}")
    coverage = {
        "symbol": symbol, "input_group": source_group,
        "bars12h_path": p12_name, "bars1h_path": p1_name,
        "bars12h_rows": len(bars12), "bars1h_rows": len(bars1),
        "parent_signals": len(parent_rows), "parent_first_key_reconciliation": identity,
        "native_v17_reclaim_bars": len(all_native),
        "parent_weeks_with_second_reclaim": candidate_reclaims,
        "parent_weeks_without_second_reclaim_complete": complete_no_second,
        "parent_weeks_right_censored": int(sum(censor_reasons.values())),
        "parent_weeks_with_observed_later_reclaim": observed_later_reclaims,
        "observed_later_reclaim_censored_before_candidate": censored_before_candidate,
        "right_censor_reasons": dict(censor_reasons),
        "second_reclaim_candidates": candidate_reclaims,
        "candidate_missing_exact_h1_open": missing_hour,
        "candidate_not_eligible_90d": ineligible,
        "candidate_zero_actual_risk": zero_risk,
        "candidate_entry_open_at_or_beyond_stop": beyond_stop,
        "eligible_executable_real_events": len(real_events),
        "matched_control_events": len(controls),
        "unmatched_control_events": control_summary["unmatched"],
        "control_unmatched_reasons": control_summary["unmatched_reasons"],
        "vectorbt": execution,
        "hourly_gap_count": prepared.get("audit", {}).get("hourly_gap_count", 0),
        "stale_12h_trigger_count": prepared.get("audit", {}).get("stale_12h_trigger_count", 0),
        "bar12h_sources": prepared.get("audit", {}).get("bar12h_sources", {}),
    }
    return paths, candidates, coverage


def _rank_self_check(scores: pd.Series, signals: pd.DataFrame,
                     path_config: dict) -> dict:
    """Validate the existing 90-day rank's actual own-key lookup convention."""
    top = ROOT / path_config["top20_inputs_dir"]
    allmarket = ROOT / path_config["allmarket_inputs_dir"]
    sample = None
    for row in signals.itertuples(index=False):
        entry_ms = int(row.confirmation_ms)
        value = _score(scores, entry_ms, str(row.symbol))
        if not math.isfinite(value):
            continue
        base = top if _bool(row.was_top20) else allmarket
        bars = pd.read_csv(base / str(row.symbol) / "bars12h.csv.gz",
                           usecols=["time", "close"], float_precision="round_trip")
        source_open = entry_ms - 2 * BAR_MS
        lookup = bars[bars.time.eq(source_open)]
        prior_open = source_open - 180 * BAR_MS
        prior = bars[bars.time.eq(prior_open)]
        if len(lookup) != 1 or len(prior) != 1:
            continue
        calculated = float(lookup.close.iloc[0] / prior.close.iloc[0] - 1.0)
        native_return = float(leadership.trailing_return(bars).get(entry_ms, np.nan))
        if not math.isfinite(native_return) or not np.isclose(calculated, native_return,
                                                               rtol=1.e-10, atol=1.e-12):
            raise AssertionError("Existing 90-day score lookup differs from its actual single-symbol key")
        sample = {
            "symbol": str(row.symbol), "source_row_number": int(row.source_row_number),
            "entry_ms": entry_ms, "entry_utc": pd.Timestamp(entry_ms, unit="ms", tz="UTC").isoformat(),
            "coverage_rank_90d": value, "return_source_bar_open_ms": source_open,
            "return_prior_bar_open_ms": prior_open,
            "return_last_closed_ms": source_open + BAR_MS,
            "trailing_return_90d": native_return,
            "uses_only_close_at_or_before_candidate_key_open": source_open + BAR_MS <= entry_ms - BAR_MS,
        }
        if not sample["uses_only_close_at_or_before_candidate_key_open"]:
            raise AssertionError("90-day rank includes data after the candidate 12H bar opened")
        break
    if sample is None:
        raise ValueError("No rankable original key found for 90-day lookup verification")
    return sample


def _revision(config_path: Path, signal_path: Path) -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__), config_path, PATH_CONFIG, signal_path):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def generate(config_path: Path, *, dry_run: bool = False,
             symbols: list[str] | None = None) -> dict:
    config, path_config, signal_path, output_path, candidate_path, coverage_path, audit_path = _load_config(config_path)
    if not dry_run:
        existing = [p for p in (output_path, candidate_path, coverage_path, audit_path) if p.exists()]
        if existing:
            raise FileExistsError(f"Refusing to overwrite frozen outputs: {existing}")
    expected = int(path_config["source_contract"]["signal_rows_expected"])
    signals = pd.read_csv(signal_path, usecols=IDENTITY_COLUMNS, float_precision="round_trip",
                          low_memory=False)
    if len(signals) != expected:
        raise ValueError(f"Frozen parent signal count changed: expected {expected}, found {len(signals)}")
    if signals.duplicated(["symbol", "week"]).any():
        raise ValueError("Frozen parent ledger is not unique by symbol/week")
    required = {"symbol", "week", "side", "key_open_bjt", "confirmed_bjt",
                "reference_entry", "reference_stop", "opposite_target", "prior_atr_pct",
                "was_top20", "is_current", "cohort", "bar_source"}
    missing = sorted(required - set(signals.columns))
    if missing:
        raise ValueError(f"Frozen parent ledger missing identity columns: {missing}")
    signals["source_row_number"] = np.arange(1, len(signals) + 1, dtype=np.int64)
    signals["parent_id"] = signals.source_row_number.map(_parent_id)
    signals["key_open_ms"] = signals.key_open_bjt.map(_ms).astype("int64")
    signals["confirmation_ms"] = signals.confirmed_bjt.map(_ms).astype("int64")
    signals["symbol"] = signals.symbol.astype(str)
    signals["week"] = signals.week.astype(str)
    signals["side"] = pd.to_numeric(signals.side, errors="raise").astype("int8")
    if (signals.confirmation_ms != signals.key_open_ms + BAR_MS).any():
        raise ValueError("Frozen parent signal confirmation is not the exact 12H close")
    if set(signals.side.unique()) - {-1, 1}:
        raise ValueError("Unexpected parent signal direction")
    if (float(config.get("fees", {}).get("taker_per_fill", engine.TAKER)) != engine.TAKER
            or float(config.get("fees", {}).get("resting_maker_per_fill", engine.MAKER)) != engine.MAKER):
        raise ValueError("V1.7 path fees must remain taker 5bp / resting maker 2bp per fill")

    scores = leadership.leadership()
    if not isinstance(scores.index, pd.MultiIndex) or list(scores.index.names) != ["entry_ms", "symbol"]:
        raise ValueError("Existing leadership() must provide an (entry_ms,symbol) score index")
    rank_check = _rank_self_check(scores, signals, path_config)
    if symbols is None and dry_run:
        input_order = pd.read_csv(SOURCE / "input_coverage.csv", keep_default_na=False).symbol.astype(str).tolist()
        symbols = input_order[:3]
    selected = set(str(x) for x in symbols) if symbols is not None else None
    grouped = {str(k): v.to_dict("records") for k, v in signals.groupby("symbol", sort=False)}
    if selected is not None:
        missing_symbols = selected - set(grouped)
        if missing_symbols:
            raise ValueError(f"Requested dry symbols have no frozen parents: {sorted(missing_symbols)}")
        grouped = {symbol: grouped[symbol] for symbol in symbols}
    batch_size = int(config.get("execution", {}).get("event_batch_size", DEFAULT_BATCH_SIZE))
    if batch_size <= 0:
        raise ValueError("event_batch_size must be positive")
    cutoff_ms = int(pd.Timestamp(path_config["source_contract"]["input_cutoff_utc"]).value // 1_000_000)
    revision = _revision(config_path, signal_path)
    checkpoint = output_path.parent / "work" / revision
    if not dry_run:
        checkpoint.mkdir(parents=True, exist_ok=True)
    all_paths: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    coverages: list[dict] = []
    total_vectorbt = Counter()
    for n, (symbol, rows) in enumerate(sorted(grouped.items()), start=1):
        cache_paths = checkpoint / (symbol + "_paths.csv.gz")
        cache_candidates = checkpoint / (symbol + "_candidates.csv.gz")
        cache_meta = checkpoint / (symbol + ".json")
        if (not dry_run and cache_paths.is_file() and cache_candidates.is_file() and cache_meta.is_file()):
            meta = json.loads(cache_meta.read_text(encoding="utf-8"))
            paths = pd.read_csv(cache_paths, float_precision="round_trip")
            candidates = pd.read_csv(cache_candidates, float_precision="round_trip")
        else:
            paths, candidates, meta = _run_symbol(symbol, rows, path_config, scores,
                                                  cutoff_ms, batch_size)
            meta["cache_revision"] = revision
            if not dry_run:
                paths.to_csv(cache_paths, index=False, compression="gzip")
                candidates.to_csv(cache_candidates, index=False, compression="gzip")
                cache_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2,
                                                 allow_nan=False, default=str) + "\n", encoding="utf-8")
        all_paths.append(paths)
        all_candidates.append(candidates)
        coverages.append(meta)
        total_vectorbt.update(meta.get("vectorbt", {}))
        if not dry_run and (n % 25 == 0 or n == len(grouped)):
            print(f"Completed {n}/{len(grouped)} parent symbols", flush=True)
    paths = pd.concat(all_paths, ignore_index=True, sort=False) if all_paths else pd.DataFrame()
    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    coverage = pd.DataFrame(coverages)
    if not paths.empty:
        if paths.duplicated(["event_id", "tp_r"]).any():
            raise AssertionError("Output path identity (event_id,tp_r) is not unique")
        if not paths.groupby("event_id").tp_r.agg(lambda x: set(x.astype(float))).map(
                lambda x: x == set(TP_R)).all():
            raise AssertionError("An executable event lacks either tp_r=0 or tp_r=2")
        if not paths.eligible_90d.astype(bool).all() or not paths.entry_status.eq("executed").all():
            raise AssertionError("Noneligible or unexecutable candidate leaked into account paths")
        missing_account = sorted(set(ACCOUNT_COLUMNS) - set(paths.columns))
        if missing_account:
            raise AssertionError(f"Funded account input schema mismatch: {missing_account}")
        paths = paths.sort_values(["entry_ms", "entry_order", "symbol", "event_id", "tp_r"],
                                  kind="stable").reset_index(drop=True)
    counts = {
        "frozen_parent_rows": len(signals),
        "parent_rows_in_run": int(coverage.parent_signals.sum()) if not coverage.empty else 0,
        "parent_first_key_exact_matches": int(coverage.parent_first_key_reconciliation.map(
            lambda x: int(x.get("identities_exact", 0)) if isinstance(x, dict) else 0).sum())
            if not coverage.empty else 0,
        "parents_with_second_reclaim": int(coverage.second_reclaim_candidates.sum()) if not coverage.empty else 0,
        "parents_without_second_reclaim_complete": int(
            coverage.parent_weeks_without_second_reclaim_complete.sum()) if not coverage.empty else 0,
        "parents_right_censored": int(coverage.parent_weeks_right_censored.sum()) if not coverage.empty else 0,
        "observed_later_reclaims_censored_before_candidate": int(
            coverage.observed_later_reclaim_censored_before_candidate.sum()) if not coverage.empty else 0,
        "eligible_executable_real_events": int(coverage.eligible_executable_real_events.sum()) if not coverage.empty else 0,
        "candidate_missing_exact_h1_open": int(coverage.candidate_missing_exact_h1_open.sum()) if not coverage.empty else 0,
        "candidate_not_eligible_90d": int(coverage.candidate_not_eligible_90d.sum()) if not coverage.empty else 0,
        "candidate_zero_actual_risk": int(coverage.candidate_zero_actual_risk.sum()) if not coverage.empty else 0,
        "candidate_entry_open_at_or_beyond_stop": int(
            coverage.candidate_entry_open_at_or_beyond_stop.sum()) if not coverage.empty else 0,
        "matched_control_events": int(coverage.matched_control_events.sum()) if not coverage.empty else 0,
        "path_events": int(paths.event_id.nunique()) if not paths.empty else 0,
        "path_rows": len(paths),
        "candidate_audit_rows": len(candidates),
        "entry_status_counts": candidates.candidate_entry_status.value_counts(dropna=False).to_dict()
            if "candidate_entry_status" in candidates else {},
        "vectorbt_execution_totals": dict(total_vectorbt),
    }
    if dry_run:
        result = {
            "experiment_id": config.get("experiment_id", config.get("id")),
            "dry_run": True, "symbols": list(grouped), "counts": counts,
            "rank_self_check": rank_check,
            "coverage": coverage[["symbol", "parent_signals", "second_reclaim_candidates",
                                   "parent_weeks_without_second_reclaim_complete",
                                   "parent_weeks_right_censored",
                                   "observed_later_reclaim_censored_before_candidate",
                                   "candidate_missing_exact_h1_open", "candidate_not_eligible_90d",
                                   "candidate_zero_actual_risk",
                                   "candidate_entry_open_at_or_beyond_stop",
                                   "eligible_executable_real_events", "matched_control_events",
                                   "unmatched_control_events", "vectorbt"]].to_dict("records")
                if not coverage.empty else [],
            "paths_schema": list(paths.columns),
            "account_schema_present": set(ACCOUNT_COLUMNS).issubset(paths.columns) if not paths.empty else True,
            "wrote_files": False,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False, default=str))
        return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths.to_csv(output_path, index=False, compression="gzip")
    candidates.to_csv(candidate_path, index=False, compression="gzip")
    coverage.to_csv(coverage_path, index=False)
    audit = {
        "experiment_id": config.get("experiment_id", config.get("id")),
        "generator": "yoyo/evaluation/cloud_close_second_reclaim_paths.py",
        "vectorbt_version": vbt.__version__,
        "numpy_version": np.__version__, "pandas_version": pd.__version__,
        "source_signal_path": _relative(signal_path),
        "source_signal_rows": len(signals),
        "source_contract_expected_rows": expected,
        "parent_identity": {
            "parent_id": "signal-row-{source_row_number:05d}",
            "source_row_number": "1-based frozen signals.csv data-row ordinal",
            "join_columns": ["symbol", "week", "side", "key_open_bjt", "confirmed_bjt"],
            "reconciliation": "Recompute original V1.7 first signal per symbol/NY week from the chosen frozen 12H/H1 input and compare every parent key, side, reference entry/stop/target and prior ATR before searching later bars.",
            "outcome_columns_read": False,
        },
        "candidate_rule": {
            "parent": "original first-per-symbol/week frozen V1.7 key; its source row and Monday anchor are immutable",
            "repeat": "first later native 12H V1.7 same-side strict wick-through and close-inside reclaim of the same Monday 1.618 boundary, with confirmed EMA20/50 aligned to the original side",
            "time": "candidate confirmation strictly after parent confirmation and strictly before that same NY week expires; no next-week anchor",
            "continuous_outside": "excluded by the source strict close-inside rule; at most one repeat per parent week",
            "entry": "the H1 open at exactly candidate confirmation_ms; missing exact hour remains a candidate audit status and is never shifted",
            "lookahead": "candidate scan uses only each closed 12H bar, completed Monday anchor and that bar's closed EMA; no exit or P&L field is read from the parent ledger",
        },
        "stop_and_risk": {
            "stop": "source fixed_stop on the same touched-side 1.618 reference from the parent's frozen NY Monday range; recomputed at the second key",
            "risk": "abs(actual candidate H1 entry open - recomputed stop); 2R target uses actual entry plus side*2*risk",
            "entry_at_or_beyond_stop": "retained and sent to the VectorBT callback for immediate native open-stop handling; separately counted",
            "zero_risk": "retained in native real path behavior; existing control matcher semantics leave it unmatched",
        },
        "own_time_90d": {
            "source": "existing cloud_close_leadership.leadership(), exact (entry_ms,symbol) index and membership gate",
            "eligibility": "coverage rank is nonnull only when the frozen 90-day return has the existing minimum cross-sectional coverage (10 contracts); controls must also qualify at their own time",
            "rank_check": rank_check,
            "priority": "entry_order is the event's own previous-confirmed 12H Wilder ATR14 / prior close percentage; ascending means lower ATR first; missing values sort last as +inf",
        },
        "control_matching": {
            "source_policy": _relative(PATH_CONFIG),
            "method": "seeded random non-signal 12H confirmation, same symbol/NY week/side and aligned EMA20/50; strictly later than its second-reclaim event; own-time eligible and previous confirmed ATR14/close within the frozen relative tolerance; one without-replacement draw",
            "atr_relative_tolerance": path_config["control_matching"]["atr_relative_tolerance"],
            "seed": path_config["control_matching"]["seed"],
            "future_path_completeness_filter": False,
            "unmatched_controls": "recorded in candidate audit and coverage; never added as an executable account row",
            "interpretation": "matched controls are retrospective diagnostics, not executable primary-entry gating",
        },
        "execution": {
            "engine": "existing VectorBT Portfolio.from_order_func callback in cloud_close_reward_paths._simulate_symbol",
            "targets_r": list(TP_R),
            "entry_fee": {"class": "taker", "rate": engine.TAKER},
            "resting_2r_fee": {"class": "maker", "rate": engine.MAKER},
            "runner_exit": "original cloud/stop/stale/gap/tail rules from frozen V1.7 path callback",
            "independent_paths": "one-base-unit columns, cash_sharing=false; funding/account/capacity not simulated here",
            "slippage_funding_multiplier": "not modeled in this independent path builder",
        },
        "counts": counts,
        "coverage_path": _relative(coverage_path),
        "candidate_audit_path": _relative(candidate_path),
        "output_paths_csv_gz": _relative(output_path),
        "output_schema": list(paths.columns),
        "funded_account_compatibility": {
            "required_columns": ACCOUNT_COLUMNS,
            "tp_r_rows_per_event": {str(event): 1 for event in TP_R},
            "eligible_actual_entries_only": True,
            "unpaired_real_events_retained": True,
        },
        "coverage_by_symbol": coverage.to_dict("records"),
    }
    temp = audit_path.with_suffix(audit_path.suffix + ".tmp")
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(audit, ensure_ascii=False, indent=2,
                               allow_nan=False, default=str) + "\n", encoding="utf-8")
    temp.replace(audit_path)
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    parser.add_argument("--dry-mini", action="store_true",
                        help="Run in memory on the first three frozen input symbols; write no study outputs")
    parser.add_argument("--symbols", nargs="*", help="Optional bounded symbol list for a no-write dry run")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    result = generate(config_path, dry_run=args.dry_mini,
                      symbols=args.symbols if args.dry_mini else None)
    if not args.dry_mini:
        print(json.dumps({"experiment_id": result["experiment_id"],
                          "counts": result["counts"],
                          "output_paths_csv_gz": result["output_paths_csv_gz"]},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
