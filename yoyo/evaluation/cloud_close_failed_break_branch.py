"""Native VectorBT study of same-week 12H failed-break reversals.

The frozen NY Monday range uses the 24 completed Monday H1 bars. Starting at
the first complete 12H bar opened after that range closes, a long setup begins
on a 12H close below the lower 1.618 extension and confirms on the first later
close strictly back inside both extensions. Shorts are symmetric. A fresh
outside close after a reclaim may begin another distinct episode. The stop is
the observed extreme across the outside-to-reclaim segment, including the
reclaim bar. The target is the opposite 1.618 extension. An unfilled trade is
closed at the next NY Monday H1 open; if history ends first, it is marked out
at the final observed H1 close. Same-hour stop/target collisions resolve
conservatively stop-first. No V1.7 signal/outcome is used to select events.

All new signal/path prices come from the frozen OKX native H1 and 12H OHLC
files. VectorBT generates each single-unit path and the existing shared-wallet
adapter independently funds the actual/control opportunity pools from 1000U.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.monitor.cloud_signal_plans import atr_points


ROOT = engine.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-failed-break-branch-20261109-v1"
RAW = ROOT / "data/research/cloud_close_failed_break_branch_20261109"
CONFIG = EXP / "config.json"
HOUR = engine.HOUR_MS
STEP12 = engine.STEP_12H
NY = ZoneInfo("America/New_York")
TAKER = engine.TAKER
MAKER = engine.MAKER

REASON = {1: "stop", 2: "stop_gap_open", 3: "target", 4: "target_open",
          5: "data_gap_open", 6: "week_expiry_open", 7: "history_tail_close"}
PHASE = {1: "intrabar", 2: "open", 3: "close"}


@njit(cache=True)
def _failed_break_order(c, q, bar_ms, starts, ends, sides, stops, targets,
                        expiry_ms, reasons, phases, ambiguous):
    """VectorBT flexible callback: long/short stop, opposite extension TP, expiry."""
    col = c.from_col
    start = starts[col]
    side = sides[col]
    if c.i == 0 and c.call_idx == 0:
        return col, nb.order_nb(size=side, price=q[start, 0], fees=TAKER,
                                allow_partial=False, raise_reject=True)
    if c.call_idx != int(c.i == 0) or c.last_position[col] == 0.0:
        return -1, NoOrder

    pos = start + c.i
    op, hi, lo, close = q[pos, 0], q[pos, 1], q[pos, 2], q[pos, 3]
    stop, target = stops[col], targets[col]
    gap = c.i > 0 and bar_ms[pos] - bar_ms[pos - 1] > HOUR
    stop_open = (op - stop) * side <= 0.0
    target_open = (op - target) * side >= 0.0
    adverse = lo if side > 0 else hi
    hit_stop = (adverse - stop) * side <= 0.0
    favorable = hi if side > 0 else lo
    hit_target = (favorable - target) * side >= 0.0

    price, fee, phase = op, TAKER, 2
    if gap:
        reason = 5
    elif stop_open:
        reason = 2
    elif target_open:
        # A resting target crossed at a later bar open fills at the better open.
        reason, fee = 4, MAKER
    elif hit_stop and hit_target:
        reason, price, ambiguous[col] = 1, stop, True
        phase = 1
    elif hit_stop:
        reason, price, phase = 1, stop, 1
    elif hit_target:
        reason, price, fee, phase = 3, target, MAKER, 1
    elif bar_ms[pos] >= expiry_ms[col]:
        reason = 6
    elif pos == len(bar_ms) - 1:
        reason, price, phase = 7, close, 3
    else:
        return -1, NoOrder

    reasons[col], phases[col] = reason, phase
    return col, nb.order_nb(size=-c.last_position[col], price=price, fees=fee,
                            allow_partial=False, raise_reject=True)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(x) for x in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if pd.isna(value):
        return None
    return str(value)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(value), indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def _load_config() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("id") != "exp-cloud-close-failed-break-branch-20261109-v1":
        raise ValueError("Unexpected experiment configuration")
    return config


def _read_bars(symbol: str, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
    for group, root_key in (("allmarket", "allmarket_inputs_dir"),
                            ("top20", "top20_inputs_dir")):
        base = ROOT / config["sources"][root_key] / symbol
        p12, p1 = base / "bars12h.csv.gz", base / "bars1h.csv.gz"
        if p12.is_file() and p1.is_file():
            b12 = engine._load_bars(p12, "12h")
            b1 = engine._load_bars(p1, "1h")
            return b12, b1, str(p12.relative_to(ROOT)), str(p1.relative_to(ROOT))
    raise FileNotFoundError(f"No frozen OKX H1/12H bars for {symbol}")


def _complete_window(index: np.ndarray, start: int, count: int, step: int) -> bool:
    loc = int(np.searchsorted(index, start, side="left"))
    if loc + count > len(index):
        return False
    expected = start + step * np.arange(count, dtype=np.int64)
    return bool(np.array_equal(index[loc:loc + count], expected))


def _entry_point(bars1: pd.DataFrame, entry_ms: int) -> tuple[int | None, float | None]:
    i = int(bars1.index.searchsorted(entry_ms, side="left"))
    if i >= len(bars1) or int(bars1.index[i]) != entry_ms:
        return None, None
    return i, float(bars1.open.iloc[i])


def verify_frozen_anchor_geometry(config: dict, bars: dict[str, tuple[pd.DataFrame, pd.DataFrame]]) -> dict:
    """Compare our per-coin Monday extensions with frozen V1.7 reference prices.

    Reads only signal identity and Monday reference columns. It never reads
    outcome/status/PnL fields and does not select the new branch's events.
    """
    path = ROOT / config["sources"]["frozen_v17_signals"]
    signals = pd.read_csv(path, usecols=["symbol", "week", "side", "reference_entry", "opposite_target"])
    signals = signals[signals.symbol.isin(bars)].copy()
    matched, missing, mismatches = 0, 0, []
    max_lower_abs = max_upper_abs = 0.0
    for row in signals.itertuples(index=False):
        week_date = date.fromisoformat(str(row.week))
        monday_ms = int(datetime.combine(week_date, time.min, NY).timestamp() * 1000)
        window = engine._ny_week_window(monday_ms)
        bars1 = bars[str(row.symbol)][1]
        t1 = bars1.index.to_numpy(dtype=np.int64)
        start, end = int(window["start"]), int(window["monday_end"])
        if not _complete_window(t1, start, 24, HOUR):
            missing += 1
            continue
        loc = int(np.searchsorted(t1, start, side="left"))
        monday = bars1.iloc[loc:loc + 24]
        lo0, hi0 = float(monday.low.min()), float(monday.high.max())
        width = hi0 - lo0
        lower, upper = lo0 - .618 * width, hi0 + .618 * width
        expected_lower = float(row.reference_entry if int(row.side) > 0 else row.opposite_target)
        expected_upper = float(row.opposite_target if int(row.side) > 0 else row.reference_entry)
        lower_diff, upper_diff = abs(lower - expected_lower), abs(upper - expected_upper)
        max_lower_abs, max_upper_abs = max(max_lower_abs, lower_diff), max(max_upper_abs, upper_diff)
        if not (np.isclose(lower, expected_lower, rtol=1e-10, atol=1e-8)
                and np.isclose(upper, expected_upper, rtol=1e-10, atol=1e-8)):
            mismatches.append({"symbol": row.symbol, "week": row.week,
                               "lower_abs_diff": lower_diff, "upper_abs_diff": upper_diff})
        matched += 1
    if mismatches:
        raise ValueError(f"Native Monday extension differs from frozen V1.7 geometry: {mismatches[:3]}")
    return {"frozen_v17_signal_rows_in_scope": len(signals), "fully_anchored_rows": matched,
            "missing_monday_h1_rows": missing, "geometry_mismatch_rows": len(mismatches),
            "max_lower_abs_difference": max_lower_abs,
            "max_upper_abs_difference": max_upper_abs,
            "identity_only_input": config["sources"]["frozen_v17_signals"]}


def _new_event(*, symbol: str, week: str, side: int, sequence: int,
               first_bar_ms: int, confirm_ms: int, first_outside_close: float,
               failure_extreme: float, lower: float, upper: float,
               monday_high: float, monday_low: float, target: float,
               risk_atr_pct: float | None, source12: str, source1: str) -> dict:
    direction = "long" if side > 0 else "short"
    event_id = f"fb:{symbol}:{week}:{direction}:{sequence:02d}:{confirm_ms}"
    entry_i, entry_price = _entry_point(_CURRENT_BARS1, confirm_ms)
    row = {
        "event_id": event_id, "parent_id": event_id, "is_control": False,
        "symbol": symbol, "week": week, "side": side, "sequence": sequence,
        "first_outside_bar_ms": first_bar_ms, "confirmation_ms": confirm_ms,
        "first_outside_close": first_outside_close, "entry_ms": confirm_ms,
        "entry_price": entry_price, "stop_price": failure_extreme,
        "target_price": target, "reference_lower": lower, "reference_upper": upper,
        "monday_high": monday_high, "monday_low": monday_low,
        "risk_atr_pct": risk_atr_pct, "source_path_12h": source12,
        "source_path_1h": source1,
        "entry_status": "eligible", "entry_i": entry_i,
    }
    if entry_i is None:
        row["entry_status"] = "missing_confirmation_hour_open"
        return row
    risk = abs(entry_price - failure_extreme)
    favorable_target = (target - entry_price) * side > 0.0
    adverse_stop = (entry_price - failure_extreme) * side > 0.0
    if not adverse_stop or not np.isfinite(risk) or risk <= 0:
        row["entry_status"] = "nonpositive_or_nonadverse_stop_distance"
    elif not favorable_target:
        row["entry_status"] = "opposite_extension_not_ahead_at_entry"
    else:
        row["risk_distance"] = risk
        row["risk_fraction"] = risk / entry_price
        row["target_r"] = abs(target - entry_price) / risk
        row["entry_order"] = risk_atr_pct if risk_atr_pct is not None else float("inf")
    return row


# Current bar table is passed only inside _new_event to keep its row constructor small.
_CURRENT_BARS1: pd.DataFrame


def scan_symbol(symbol: str, config: dict) -> tuple[list[dict], dict, pd.DataFrame, pd.DataFrame]:
    global _CURRENT_BARS1
    bars12, bars1, source12, source1 = _read_bars(symbol, config)
    _CURRENT_BARS1 = bars1
    t12 = bars12.index.to_numpy(dtype=np.int64)
    t1 = bars1.index.to_numpy(dtype=np.int64)
    h12 = bars12[["high", "low", "close"]].to_numpy(dtype=float)
    rows12 = bars12.reset_index().rename(columns={"index": "time"})
    atr = np.asarray([r["atr14"] if r["atr14"] is not None else np.nan
                      for r in atr_points(rows12[["time", "open", "high", "low", "close"]].to_dict("records"))],
                     dtype=float)
    close12 = rows12.close.to_numpy(dtype=float)
    atr_pct = np.divide(atr * 100.0, close12, out=np.full(len(atr), np.nan),
                        where=np.isfinite(atr) & (close12 > 0))
    atr_pct[~np.isfinite(atr_pct) | (atr_pct <= 0)] = np.nan
    complete12 = np.asarray([_complete_window(t1, int(t) , 12, HOUR) for t in t12], dtype=bool)
    week_windows: dict[str, dict] = {}
    audit: list[dict] = []
    actual: list[dict] = []
    counts = Counter()
    anchor_reason_counted: set[tuple[str, str]] = set()
    episode_number: dict[tuple[str, int], int] = defaultdict(int)

    for i, bar_open in enumerate(t12):
        window = engine._ny_week_window(int(bar_open))
        week = str(window["week"])
        if week not in week_windows:
            week_windows[week] = window
        if int(bar_open) < int(window["monday_end"]):
            continue
        confirm_ms = int(bar_open + STEP12)
        if confirm_ms >= int(window["expires"]):
            continue
        if not complete12[i]:
            counts["incomplete_12h_bar_skipped"] += 1
            continue
        week_anchor = week_windows[week]
        if "anchor" not in week_anchor:
            expected = int(week_anchor["start"])
            if _complete_window(t1, expected, 24, HOUR):
                loc = int(np.searchsorted(t1, expected, side="left"))
                monday = bars1.iloc[loc:loc + 24]
                monday_high = float(monday.high.max())
                monday_low = float(monday.low.min())
                width = monday_high - monday_low
                if width > 0 and monday_low > 0:
                    week_anchor.update({"anchor": True, "monday_high": monday_high,
                                        "monday_low": monday_low,
                                        "lower": monday_low - 0.618 * width,
                                        "upper": monday_high + 0.618 * width})
                else:
                    week_anchor.update({"anchor": False, "reason": "invalid_monday_range"})
            else:
                week_anchor.update({"anchor": False, "reason": "incomplete_monday_h1_range"})
        if not week_anchor.get("anchor", False):
            reason_key = (week, week_anchor.get("reason", "missing_monday_anchor"))
            if reason_key not in anchor_reason_counted:
                counts[reason_key[1]] += 1
                anchor_reason_counted.add(reason_key)
            continue

        # Keep only closed 12H candles fully after the Monday-range boundary.
        low_ref = float(week_anchor["lower"])
        high_ref = float(week_anchor["upper"])
        close = float(h12[i, 2])
        low = float(h12[i, 1])
        high = float(h12[i, 0])
        if not (np.isfinite(low_ref) and np.isfinite(high_ref) and low_ref < high_ref):
            continue

        # Track one active excursion per side. Staying outside only updates the
        # failed-break extreme; only a later strict inside close confirms it.
        for side, outside, inside in ((1, close < low_ref, low_ref < close < high_ref),
                                      (-1, close > high_ref, low_ref < close < high_ref)):
            state_key = (week, side)
            state = _ACTIVE.get((symbol, state_key))
            if not outside and state is not None:
                if side > 0:
                    state["extreme"] = min(float(state["extreme"]), low)
                else:
                    state["extreme"] = max(float(state["extreme"]), high)
            if outside:
                if state is None:
                    state = {"first_bar_ms": int(bar_open), "first_outside_close": close,
                             "extreme": low if side > 0 else high,
                             "bars": 0, "last_confirmation_ms": confirm_ms}
                    _ACTIVE[(symbol, state_key)] = state
                else:
                    if side > 0:
                        state["extreme"] = min(float(state["extreme"]), low)
                    else:
                        state["extreme"] = max(float(state["extreme"]), high)
                state["bars"] += 1
                state["last_confirmation_ms"] = confirm_ms
                continue
            if inside and state is not None:
                episode_number[state_key] += 1
                target = high_ref if side > 0 else low_ref
                row = _new_event(
                    symbol=symbol, week=week, side=side,
                    sequence=episode_number[state_key],
                    first_bar_ms=int(state["first_bar_ms"]), confirm_ms=confirm_ms,
                    first_outside_close=float(state["first_outside_close"]),
                    failure_extreme=float(state["extreme"]), lower=low_ref, upper=high_ref,
                    monday_high=float(week_anchor["monday_high"]),
                    monday_low=float(week_anchor["monday_low"]), target=target,
                    risk_atr_pct=float(atr_pct[i]) if np.isfinite(atr_pct[i]) else None,
                    source12=source12, source1=source1)
                row.update({"failure_bar_count": int(state["bars"] + 1),
                            "confirmation_bar_open_ms": int(bar_open),
                            "confirmation_close": close,
                            "week_expires_ms": int(window["expires"]),
                            "complete_confirmation_12h": bool(complete12[i])})
                if row["entry_status"] == "eligible":
                    actual.append(row)
                    counts["confirmed_executable"] += 1
                else:
                    counts[row["entry_status"]] += 1
                audit.append(row.copy())
                _ACTIVE.pop((symbol, state_key), None)

    coverage = {
        "symbol": symbol, "source_path_12h": source12, "source_path_1h": source1,
        "rows_12h": len(bars12), "rows_1h": len(bars1),
        "first_12h_ms": int(t12[0]) if len(t12) else None,
        "last_12h_open_ms": int(t12[-1]) if len(t12) else None,
        "first_1h_ms": int(t1[0]) if len(t1) else None,
        "last_1h_open_ms": int(t1[-1]) if len(t1) else None,
        "gaps_12h": int(np.count_nonzero(np.diff(t12) != STEP12)),
        "gaps_1h": int(np.count_nonzero(np.diff(t1) != HOUR)),
        "complete_12h_bars": int(complete12.sum()),
        "counts": dict(counts),
    }
    censored = []
    for (active_symbol, (week, side)), state in list(_ACTIVE.items()):
        if active_symbol != symbol:
            continue
        window = week_windows.get(week)
        expired = bool(window and int(t12[-1] + STEP12) >= int(window["expires"]))
        censored.append({
            "event_id": f"censored:{symbol}:{week}:{'long' if side > 0 else 'short'}:{state['first_bar_ms']}",
            "parent_id": None, "is_control": False, "symbol": symbol, "week": week,
            "side": side, "first_outside_bar_ms": int(state["first_bar_ms"]),
            "first_outside_close": float(state["first_outside_close"]),
            "failure_extreme_so_far": float(state["extreme"]),
            "observed_outside_bars": int(state["bars"]),
            "sequence_status": "right_censored_week_end_without_reclaim" if expired
                               else "right_censored_history_end_without_reclaim",
            "week_expires_ms": int(window["expires"]) if window else None,
            "source_path_12h": source12, "source_path_1h": source1,
        })
        counts["right_censored_week_end_without_reclaim" if expired
               else "right_censored_history_end_without_reclaim"] += 1
        _ACTIVE.pop((symbol, (week, side)), None)

    coverage["counts"] = dict(counts)
    return actual, coverage, bars12, bars1, audit + censored


_ACTIVE: dict[tuple[str, tuple[str, int]], dict] = {}


def generate(config: dict, symbols: list[str] | None = None) -> tuple[list[dict], list[dict], dict[str, tuple[pd.DataFrame, pd.DataFrame]], dict]:
    global _ACTIVE
    _ACTIVE = {}
    symbols = symbols or list(config["symbols"])
    events: list[dict] = []
    audit_rows: list[dict] = []
    coverage: list[dict] = []
    bar_cache: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for symbol in symbols:
        actual, cov, bars12, bars1, symbol_audit = scan_symbol(symbol, config)
        events.extend(actual)
        audit_rows.extend(symbol_audit)
        coverage.append(cov)
        bar_cache[symbol] = (bars12, bars1)
        print(f"{symbol}: candidates={len(actual)} coverage={cov['first_1h_ms']}..{cov['last_1h_open_ms']} gaps={cov['gaps_1h']}/{cov['gaps_12h']}", flush=True)
    controls, control_audit = match_controls(events, bar_cache, config)
    anchor_audit = verify_frozen_anchor_geometry(config, bar_cache)
    return events, controls, bar_cache, {"coverage": coverage, "control_audit": control_audit,
                                         "candidate_audit": audit_rows,
                                         "frozen_anchor_audit": anchor_audit}


def match_controls(events: list[dict], bar_cache: dict, config: dict) -> tuple[list[dict], dict]:
    tolerance = float(config["control_matching"]["atr_relative_tolerance"])
    max_hours = int(config["control_matching"]["max_time_distance_hours"])
    seed = int(config["control_matching"]["seed"])
    real_by_symbol_week = {(e["symbol"], e["week"], int(e["side"])): [] for e in events}
    for event in events:
        real_by_symbol_week[(event["symbol"], event["week"], int(event["side"]))].append(event)
    result, audit = [], Counter()
    used: set[tuple[str, str, int, int]] = set()
    real_entry_times = {(e["symbol"], int(e["confirmation_ms"])) for e in events}
    derived: dict[str, dict] = {}
    for symbol, (bars12, bars1) in bar_cache.items():
        t12 = bars12.index.to_numpy(dtype=np.int64)
        t1 = bars1.index.to_numpy(dtype=np.int64)
        h12 = bars12[["high", "low", "close"]].to_numpy(dtype=float)
        rows = bars12.reset_index().rename(columns={"index": "time"})
        atr_rows = atr_points(rows[["time", "open", "high", "low", "close"]].to_dict("records"))
        atr_pct = np.asarray([r["atr14"] / r["close"] * 100.0
                              if r["atr14"] is not None and r["close"] > 0 else np.nan
                              for r in atr_rows])
        entry_times = t12 + STEP12
        windows = [engine._ny_week_window(int(t)) for t in t12]
        week_keys = np.asarray([w["week"] for w in windows], dtype=object)
        locs = np.searchsorted(t1, t12, side="left")
        complete12 = (locs + 11 < len(t1))
        valid_locs = np.flatnonzero(complete12)
        complete12[valid_locs] = (t1[locs[valid_locs]] == t12[valid_locs]) & \
            (t1[locs[valid_locs] + 11] == t12[valid_locs] + 11 * HOUR)
        derived[symbol] = dict(t12=t12, h12=h12, atr_pct=atr_pct,
                               entry_times=entry_times, windows=windows,
                               week_keys=week_keys, complete12=complete12,
                               t1=t1, bars1=bars1)

    # Controls are same-symbol/week/direction inside-range confirmations, chosen
    # nearest in clock and with <=25% difference in then-known 12H ATR/close.
    for key, parents in sorted(real_by_symbol_week.items()):
        symbol, week, side = key
        cache = derived[symbol]
        t12, h12 = cache["t12"], cache["h12"]
        atr_pct, entry_times = cache["atr_pct"], cache["entry_times"]
        windows, week_keys = cache["windows"], cache["week_keys"]
        complete12, bars1 = cache["complete12"], cache["bars1"]
        parent_week = engine._ny_week_window(int(parents[0]["first_outside_bar_ms"]))
        expected = int(parent_week["start"])
        if not _complete_window(bars1.index.to_numpy(dtype=np.int64), expected, 24, HOUR):
            for p in parents:
                p["control_match_status"] = "incomplete_monday_anchor"
            audit["incomplete_monday_anchor"] += len(parents)
            continue
        lo, hi = float(parents[0]["reference_lower"]), float(parents[0]["reference_upper"])
        week_expiry = int(parent_week["expires"])
        mask = ((week_keys == week) & (t12 >= int(parent_week["monday_end"]))
                & (entry_times < week_expiry) & (lo < h12[:, 2]) & (h12[:, 2] < hi)
                & complete12 & np.isfinite(atr_pct) & (atr_pct > 0))
        candidate_indices = np.flatnonzero(mask).tolist()
        if candidate_indices:
            actual_mask = np.asarray([(symbol, int(entry_times[i])) in real_entry_times
                                      for i in candidate_indices], dtype=bool)
            candidate_indices = [i for i, is_actual in zip(candidate_indices, actual_mask) if not is_actual]
        for parent in sorted(parents, key=lambda e: (int(e["confirmation_ms"]), e["event_id"])):
            event_atr = parent.get("risk_atr_pct")
            if event_atr is None or not np.isfinite(event_atr) or event_atr <= 0:
                parent["control_match_status"] = "parent_atr_unavailable"
                audit["parent_atr_unavailable"] += 1
                continue
            possible = []
            for i in candidate_indices:
                c_ms = int(entry_times[i])
                slot = (symbol, week, side, c_ms)
                if slot in used or abs(c_ms - int(parent["confirmation_ms"])) > max_hours * HOUR:
                    continue
                atr_diff = atr_pct[i] / float(event_atr) - 1.0
                if abs(atr_diff) > tolerance:
                    continue
                possible.append((abs(c_ms-int(parent["confirmation_ms"])),
                                 (i * 2654435761 + seed) & 0xffffffff, i, atr_diff))
            if not possible:
                parent["control_match_status"] = "no_same_week_time_atr_control"
                audit["unmatched"] += 1
                continue
            _, _, chosen, atr_diff = min(possible)
            c_ms = int(entry_times[chosen])
            entry_i, entry_price = _entry_point(bars1, c_ms)
            risk_fraction = float(parent["risk_fraction"])
            risk = entry_price * risk_fraction
            target_r = float(parent["target_r"])
            stop = entry_price - side * risk
            target = entry_price + side * risk * target_r
            if not (entry_price > 0 and risk > 0 and (entry_price-stop)*side > 0
                    and (target-entry_price)*side > 0):
                parent["control_match_status"] = "invalid_control_geometry"
                audit["invalid_control_geometry"] += 1
                continue
            event_id = f"control:{parent['event_id']}"
            result.append({
                **{k: parent.get(k) for k in ("symbol", "week", "side")},
                "event_id": event_id, "parent_id": parent["event_id"], "is_control": True,
                "sequence": None, "first_outside_bar_ms": None,
                "confirmation_ms": c_ms, "entry_ms": c_ms, "entry_i": entry_i,
                "entry_price": entry_price, "stop_price": stop, "target_price": target,
                "risk_distance": risk, "risk_fraction": risk_fraction,
                "target_r": target_r, "entry_order": float(atr_pct[chosen]),
                "risk_atr_pct": float(atr_pct[chosen]), "control_atr_relative_difference": float(atr_diff),
                "control_match_status": "matched", "reference_lower": lo, "reference_upper": hi,
                "monday_high": float(parents[0]["monday_high"]),
                "monday_low": float(parents[0]["monday_low"]),
                "week_expires_ms": week_expiry,
                "source_path_12h": parent["source_path_12h"],
                "source_path_1h": parent["source_path_1h"],
            })
            parent["control_match_status"] = "matched"
            parent["control_event_id"] = event_id
            parent["control_confirmation_ms"] = c_ms
            parent["control_atr_relative_difference"] = float(atr_diff)
            used.add((symbol, week, side, c_ms))
            audit["matched"] += 1
    return result, dict(audit)


def _simulate_symbol(events: list[dict], bars1: pd.DataFrame, batch_size: int = 128) -> list[dict]:
    if not events:
        return []
    bar_ms = bars1.index.to_numpy(dtype=np.int64)
    q = bars1[["open", "high", "low", "close"]].to_numpy(dtype=float)
    out: list[dict] = []
    for begin in range(0, len(events), batch_size):
        batch = events[begin:begin + batch_size]
        executable = [e for e in batch if e.get("entry_i") is not None]
        if not executable:
            continue
        starts = np.asarray([int(e["entry_i"]) for e in executable], dtype=np.int64)
        sides = np.asarray([int(e["side"]) for e in executable], dtype=np.float64)
        stops = np.asarray([float(e["stop_price"]) for e in executable], dtype=np.float64)
        targets = np.asarray([float(e["target_price"]) for e in executable], dtype=np.float64)
        expiry_ms = np.asarray([int(e["week_expires_ms"]) for e in executable], dtype=np.int64)
        ends = np.asarray([min(int(bar_ms.searchsorted(expiry, side="left")), len(bar_ms)-1)
                           for expiry in expiry_ms], dtype=np.int64)
        # Ensure at least the entry hour exists before the weekly expiry.
        ends = np.maximum(ends, starts)
        max_len = int(np.max(ends - starts) + 1)
        marked = np.empty((max_len, len(executable)), dtype=np.float64)
        offsets = np.arange(max_len, dtype=np.int64)
        for col in range(len(executable)):
            positions = np.minimum(starts[col] + offsets, ends[col])
            marked[:, col] = q[positions, 3]
        reasons = np.zeros(len(executable), dtype=np.int8)
        phases = np.zeros(len(executable), dtype=np.int8)
        ambiguous = np.zeros(len(executable), dtype=np.bool_)
        init_cash = np.maximum(q[starts, 0] * 10.0, 1.0)
        pf = vbt.Portfolio.from_order_func(
            marked, _failed_break_order, q, bar_ms, starts, ends, sides, stops,
            targets, expiry_ms, reasons, phases, ambiguous,
            flexible=True, init_cash=init_cash, cash_sharing=False, group_by=False,
            max_orders=2 * len(executable), freq="1h")
        orders = pd.DataFrame(pf.orders.records).sort_values(["col", "id"])
        trades = pd.DataFrame(pf.trades.records).sort_values("col")
        if len(orders) != 2 * len(executable) or len(trades) != len(executable):
            raise ValueError(f"VectorBT did not close all failed-break paths for {executable[0]['symbol']}")
        if not trades.status.eq(1).all() or not orders.groupby("col").size().eq(2).all():
            raise ValueError("Failed-break VectorBT path ended unclosed")
        for col, event in enumerate(executable):
            entry, exit_ = orders.iloc[2*col], orders.iloc[2*col+1]
            trade = trades.iloc[col]
            exit_pos = starts[col] + int(exit_.idx)
            reason = REASON[int(reasons[col])]
            out.append({
                **event,
                "entry_ms": int(bar_ms[starts[col]]), "entry_price": float(entry.price),
                "entry_fee_class": "taker", "entry_fee_rate": TAKER,
                "entry_fee_amount": float(entry.fees),
                "exit_bar_ms": int(bar_ms[exit_pos]), "exit_price": float(exit_.price),
                "exit_fee_rate": MAKER if int(reasons[col]) in (3, 4) else TAKER,
                "exit_fee_class": "maker" if int(reasons[col]) in (3, 4) else "taker",
                "exit_fee_amount": float(exit_.fees), "exit_phase": PHASE[int(phases[col])],
                "exit_reason": reason, "same_bar_ambiguous": bool(ambiguous[col]),
                "gross_pnl": float(trade.pnl + trade.entry_fees + trade.exit_fees),
                "net_pnl": float(trade.pnl), "gross_r": float((trade.pnl + trade.entry_fees + trade.exit_fees)/event["risk_distance"]),
                "net_r": float(trade.pnl/event["risk_distance"]),
                "entry_status": "executed", "status": "executed",
                "target_r": float(event["target_r"]),
            })
    return out


def simulate_paths(events: list[dict], controls: list[dict], bars: dict[str, tuple[pd.DataFrame, pd.DataFrame]], config: dict) -> pd.DataFrame:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for e in [*events, *controls]:
        by_symbol[e["symbol"]].append(e)
    rows = []
    batch = int(config["execution"]["event_batch_size"])
    for symbol in config["symbols"]:
        if symbol not in by_symbol:
            continue
        _, bars1 = bars[symbol]
        rows.extend(_simulate_symbol(by_symbol[symbol], bars1, batch_size=batch))
        print(f"{symbol}: VectorBT closed {len(by_symbol[symbol])} independent paths", flush=True)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if frame.duplicated("event_id").any() or not frame.exit_phase.isin(["open", "intrabar", "close"]).all():
        raise ValueError("Invalid/duplicate executable VectorBT path output")
    if not np.isclose(frame.net_pnl.sum(), frame.gross_pnl.sum() - frame.entry_fee_amount.sum()
                      - frame.exit_fee_amount.sum(), rtol=1e-9, atol=1e-6):
        raise ValueError("Per-path fee reconciliation failed")
    return frame


def _account_metrics(paths: pd.DataFrame, start: int, end: int, slip: float = 0.0) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if paths.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"opportunities": 0, "funded": 0}
    events = paths.copy()
    # reward_account.prepare consumes frozen exit events only; the new branch
    # supplies the exit price, fee class and exit timing from native VectorBT.
    data = wallet.prepare(events, slots=9, start=start, end=end)
    trades, equity = wallet.account(data, slip=slip)
    periods = {"all": (None, None), "development_through_2024": (None, "2025-01-01T00:00:00Z"),
               **{str(y): (f"{y}-01-01T00:00:00Z", f"{y+1}-01-01T00:00:00Z")
                  for y in range(2020, 2026)},
               "2026": ("2026-01-01T00:00:00Z", None)}
    metric_rows = []
    for period, bounds in periods.items():
        metric_rows.append({"period": period, **wallet.summarize(trades, equity, *bounds)})
    meta = {"opportunities": len(events), "admitted": int(len(data["selected"])),
            "funded": int(len(trades)), "unquoted_position_hours": int(data["gap_marks"]),
            **data["skips"]}
    return pd.DataFrame(metric_rows), trades, equity, meta


def _run(config: dict, *, symbols: list[str] | None = None, write: bool = True) -> dict:
    events, controls, bars, audit = generate(config, symbols=symbols)
    all_paths = simulate_paths(events, controls, bars, config)
    actual = all_paths[~all_paths.is_control].copy() if not all_paths.empty else all_paths
    control_paths = all_paths[all_paths.is_control].copy() if not all_paths.empty else all_paths
    matched_ids = set(control_paths.parent_id) if not control_paths.empty else set()
    paired_actual = actual[actual.parent_id.isin(matched_ids)].copy() if not actual.empty else actual
    scope_start = min(int(t) for _, b1 in bars.values() for t in b1.index[:1])
    scope_end = max(int(b1.index[-1]) + HOUR for _, b1 in bars.values())
    metric_frames = []
    trade_sets, equity_sets, admissions = {}, {}, {}
    for name, pool in (("actual", actual), ("paired_actual", paired_actual), ("matched_control", control_paths)):
        if pool.empty:
            continue
        metrics, trades, equity, meta = _account_metrics(pool, scope_start, scope_end)
        metrics.insert(0, "scenario", name)
        metric_frames.append(metrics)
        trade_sets[name], equity_sets[name], admissions[name] = trades, equity, meta
        print(f"{name}: {len(trades)}/{len(pool)} funded", flush=True)
    out_metrics = pd.concat(metric_frames, ignore_index=True) if metric_frames else pd.DataFrame()
    if write:
        EXP.mkdir(parents=True, exist_ok=True)
        RAW.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(audit["candidate_audit"] + controls).to_csv(EXP / "candidate_audit.csv.gz", index=False)
        all_paths.to_csv(EXP / "paths.csv.gz", index=False)
        pd.DataFrame(audit["coverage"]).to_csv(EXP / "coverage.csv", index=False)
        out_metrics.to_csv(EXP / "account_metrics.csv", index=False)
        for name, trades in trade_sets.items():
            trades.to_csv(EXP / f"funded_{name}.csv.gz", index=False)
            equity_sets[name].to_csv(EXP / f"hourly_{name}.csv.gz", index_label="hour_end_utc")
        summary = {
            "engine": "VectorBT Portfolio.from_order_func independent native OHLC paths + cash-sharing funded wallet",
            "vbt_version": vbt.__version__, "starting_usdt": 1000,
            "events": len(events), "matched_controls": len(controls),
            "control_audit": audit["control_audit"], "coverage": audit["coverage"],
            "frozen_anchor_audit": audit["frozen_anchor_audit"],
            "admission": admissions,
            "source_limitations": config["limitations"],
            "path_assumptions": config["execution"],
            "target_rule": config["target"],
        }
        _write_json(EXP / "summary.json", summary)
        _write_json(RAW / "source_receipt.json", {"coverage": audit["coverage"],
                                                     "input_roots": config["sources"]})
        return summary
    return {"events": events, "controls": controls, "paths": all_paths,
            "coverage": audit["coverage"], "control_audit": audit["control_audit"],
            "frozen_anchor_audit": audit["frozen_anchor_audit"],
            "metrics": out_metrics, "admission": admissions}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight", action="store_true", help="Enumerate events/coverage only")
    action.add_argument("--smoke", action="store_true", help="Run a small native VectorBT and wallet check")
    action.add_argument("--run", action="store_true", help="Build paths and funded actual/control accounts")
    parser.add_argument("--symbols", nargs="+", help="Optional frozen symbol subset for smoke")
    args = parser.parse_args(argv)
    config = _load_config()
    if args.preflight:
        events, controls, _, audit = generate(config, symbols=args.symbols)
        print(json.dumps({"actual_candidates": len(events), "matched_controls": len(controls),
                          "coverage": audit["coverage"], "control_audit": audit["control_audit"],
                          "frozen_anchor_audit": audit["frozen_anchor_audit"]},
                         indent=2, allow_nan=False), flush=True)
        return
    if args.smoke:
        events, controls, bars, audit = generate(config, symbols=args.symbols or config["symbols"][:2])
        if not events:
            raise ValueError("Smoke subset has no confirmed reversal candidate")
        events = events[:min(4, len(events))]
        selected_ids = {e["event_id"] for e in events}
        controls = [c for c in controls if c["parent_id"] in selected_ids]
        paths = simulate_paths(events, controls, bars, config)
        if paths.empty:
            raise ValueError("Smoke found no VectorBT path")
        pool = paths[~paths.is_control]
        start = min(int(b1.index[0]) for _, b1 in bars.values())
        end = max(int(b1.index[-1]) + HOUR for _, b1 in bars.values())
        metrics, funded, equity, admission = _account_metrics(pool, start, end)
        if not np.isfinite(funded.cash_pnl).all() or not np.isfinite(equity.nav).all():
            raise ValueError("Smoke wallet produced non-finite funded cash/NAV")
        print(json.dumps(_json_safe({"smoke_paths": len(paths), "actual_candidates": len(events),
                          "matched_controls": len(controls), "funded": len(funded),
                          "account_metrics": metrics.to_dict("records"), "admission": admission,
                          "censored_audit_rows": sum(str(r.get("sequence_status", "")).startswith("right_censored")
                                                      for r in audit["candidate_audit"])}),
                         indent=2, allow_nan=False), flush=True)
        return
    _run(config, write=True)


if __name__ == "__main__":
    main()
