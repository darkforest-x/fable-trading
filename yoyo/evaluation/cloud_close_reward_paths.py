"""VectorBT execution paths for the frozen OKX V1.7 12H confirmation ledger.

This module starts a market position only at the first 1H open whose timestamp
equals the completed 12H confirmation boundary. It uses the original touched
1.618 stop for signal rows, then varies only a fixed-R resting target. A matched
non-signal control inherits the signal's realized entry-to-stop risk fraction.

Every executable column is an independent VectorBT position. VectorBT drives
the bar/order lifecycle via ``Portfolio.from_order_func``; the order callback
reads only that bar's OHLC and prior-confirmed-12H EMA cross level, with stop
priority when multiple barriers touch in one 1H bar. No precomputed/custom
trade replay is wrapped in VectorBT. These are independent one-base-unit paths,
not a shared-cash account or contract-multiplier simulation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from importlib.metadata import version
import json
import hashlib
import math
from pathlib import Path
import zlib
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation.range_framework import ROOT
from yoyo.monitor.cloud_signal_plans import atr_points


TP_R = (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 10.0)
HOUR_MS = 3_600_000
STEP_12H = 12 * HOUR_MS
TAKER = 0.0005
MAKER = 0.0002
NY = ZoneInfo("America/New_York")

REASON_NAMES = {
    1: "stop",
    2: "stop_gap_open",
    3: "ema_flip",
    4: "ema_flip_open",
    5: "take_profit",
    6: "take_profit_open",
    7: "data_gap_markout",
    8: "series_tail_close",
    9: "stale_12h_markout",
}
PHASE_NAMES = {1: "intrabar", 2: "open", 3: "close"}


@njit(cache=True)
def _order_func(c, q, bar_ms, starts, ends, sides, stops, targets,
                tp_rs, n_tp, reasons, phases, ambiguous, gap_flags, gap_hours):
    """Emit VectorBT orders at the open or the first active intrabar barrier."""
    col = c.from_col
    start = starts[col]
    side = sides[col]
    if c.i == 0 and c.call_idx == 0:
        return col, nb.order_nb(size=side,price=q[start,0],fees=TAKER,
                                allow_partial=False,raise_reject=True)
    if c.call_idx != int(c.i == 0) or c.last_position[col] == 0.:
        return -1, NoOrder
    pos = start + c.i
    op, hi, lo, close, cross, spread, stale = q[pos,:7]
    stop, target = stops[col], targets[col]
    slope = 2./21. - 2./51.
    ema50 = cross + (1.-2./21.)*spread/slope
    eps = 8.*np.finfo(np.float64).eps*max(1.,abs(ema50),abs(ema50+spread))/slope
    adverse = lo if side > 0 else hi
    favorable = hi if side > 0 else lo
    gap = c.i > 0 and bar_ms[pos]-bar_ms[pos-1] > HOUR_MS
    stop_open = (op-stop)*side <= 0.
    flip_open = (op-cross)*side < -eps
    target_open = np.isfinite(target) and (op-target)*side >= 0.
    hit_stop = (adverse-stop)*side <= 0.
    hit_flip = (adverse-cross)*side < -eps
    hit_tp = np.isfinite(target) and (favorable-target)*side >= 0.
    price, fee, phase = op, TAKER, 2
    if gap:
        reason = 7
        gap_flags[col] = True
        gap_hours[col] = (bar_ms[pos]-bar_ms[pos-1])//HOUR_MS-1
    elif c.i > 0 and stale:
        reason = 9
    elif stop_open:
        reason = 2
    elif flip_open or (c.i > 0 and side*spread < 0.):
        reason = 4
    elif target_open:
        reason, price, fee = 6, target, MAKER
    elif hit_stop or hit_flip:
        # The two protective barriers lie on the same side. Approaching from
        # the open, price reaches the nearer one first, including triple hits.
        cloud_first = hit_flip and (not hit_stop or (cross-stop)*side > 0.)
        reason, price, phase = (3,cross,1) if cloud_first else (1,stop,1)
        if hit_tp:
            ambiguous[col] = int(hit_stop) + 2*int(hit_flip) + 4
    elif hit_tp:
        reason, price, fee, phase = 5, target, MAKER, 1
    elif pos == ends[col]:
        if pos != len(q)-1:
            raise ValueError("Path cap truncated a live VectorBT position")
        reason, price, phase = 8, close, 3
    else:
        return -1, NoOrder
    reasons[col], phases[col] = reason, phase
    return col, nb.order_nb(size=-c.last_position[col],price=price,fees=fee,
                            allow_partial=False,raise_reject=True)


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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(data), ensure_ascii=False,
                               indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _parse_ms(value) -> int | None:
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError(f"Timezone required for confirmation timestamp: {value}")
    return int(stamp.tz_convert("UTC").value // 1_000_000)


def _signal_id(row: dict) -> str:
    """Stable immutable parent key: 1-based source row in frozen signals.csv."""
    return f"signal-row-{int(row['source_row_number']):05d}"


def _ny_week_window(time_ms: int) -> dict:
    """Pure equivalent of the existing Monday-New-York calendar window."""
    day = datetime.fromtimestamp(time_ms/1000, NY).date()
    monday = day - timedelta(days=day.weekday())
    stamp = lambda d: int(datetime.combine(d, time.min, NY).timestamp()*1000)
    return {"week": monday.isoformat(), "start": stamp(monday),
            "monday_end": stamp(monday+timedelta(days=1)),
            "expires": stamp(monday+timedelta(days=7))}


def _ema_points_12h(records: list[dict]) -> pd.DataFrame:
    """Pure copy of cloud_signals.ema_points, avoiding its FastAPI import.

    Uses only ``time`` and completed 12H ``close``. It resets the first-close
    seed and warmup after any missing 12H timestamp, exactly as the source.
    """
    a = b = previous = None
    count = 0
    rows = []
    for row in records:
        if previous is None or int(row["time"]) != previous+STEP_12H:
            a = b = float(row["close"])
            count = 0
        a += (float(row["close"])-a)*(2.0/21.0)
        b += (float(row["close"])-b)*(2.0/51.0)
        count += 1
        rows.append({"ema20": a, "ema50": b, "count": count,
                     "ready": count >= 50})
        previous = int(row["time"])
    return pd.DataFrame(rows)


def _load_bars(path: Path, timeframe: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    frame = pd.read_csv(path, float_precision="round_trip")
    if "time" not in frame or not {"open", "high", "low", "close"}.issubset(frame):
        raise ValueError(f"Invalid {timeframe} OHLC schema: {path}")
    frame = frame.set_index("time")
    frame.index = frame.index.astype("int64")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Nonunique or unsorted {timeframe} timestamps: {path}")
    ohlc = frame[["open", "high", "low", "close"]].astype(float)
    if ohlc.isna().any().any():
        raise ValueError(f"Null OHLC in {timeframe}: {path}")
    if ((ohlc.low <= 0) | (ohlc.low > ohlc[["open", "high", "low", "close"]].min(axis=1))
            | (ohlc.high < ohlc[["open", "high", "low", "close"]].max(axis=1))).any():
        raise ValueError(f"Invalid OHLC in {timeframe}: {path}")
    if timeframe == "12h":
        if not np.equal(frame.index.to_numpy() % STEP_12H, 0).all():
            raise ValueError(f"12H bars are not UTC aligned: {path}")
        return frame[["open", "high", "low", "close", *(["source"] if "source" in frame else [])]]
    return ohlc


def _choose_input(symbol: str, was_top20: bool, config: dict) -> tuple[Path, Path, str]:
    if was_top20:
        base = ROOT / config["top20_inputs_dir"] / symbol
        source_group = "top20_frozen_inputs"
    else:
        base = ROOT / config["allmarket_inputs_dir"] / symbol
        source_group = "allmarket_frozen_inputs"
    return base / "bars12h.csv.gz", base / "bars1h.csv.gz", source_group


def _prepare_symbol(bars12: pd.DataFrame, bars1: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return hourly OHLC+EMA-cross level and causal 12H matching features."""
    if bars12.empty or bars1.empty:
        return np.empty((0, 7), dtype=float), np.empty(0, dtype=np.int64), {
            "ema_ready_12h": 0, "hourly_gap_count": 0, "bar12h_sources": {},
        }
    rows12 = bars12.reset_index().rename(columns={"index": "time"})
    records = rows12[["time", "open", "high", "low", "close"]].to_dict("records")
    ema = _ema_points_12h(records)
    atr = pd.DataFrame(atr_points(records))
    alpha20, alpha50 = 2.0 / 21.0, 2.0 / 51.0
    close_times = rows12.time.to_numpy(dtype=np.int64) + STEP_12H
    ema20 = ema.ema20.to_numpy(float)
    ema50 = ema.ema50.to_numpy(float)
    ready = ema.ready.to_numpy(bool)

    hour_ms = bars1.index.to_numpy(dtype=np.int64)
    hour = bars1[["open", "high", "low", "close"]].to_numpy(float)
    active = np.searchsorted(close_times, hour_ms, side="right") - 1
    cross_line = np.full(len(hour_ms), np.nan, dtype=float)
    ema_diff = np.full(len(hour_ms), np.nan, dtype=float)
    valid = (active >= 0)
    valid_idx = np.flatnonzero(valid)
    valid_idx = valid_idx[ready[active[valid_idx]]]
    if len(valid_idx):
        a = active[valid_idx]
        spread_slope = alpha20 - alpha50
        cross_line[valid_idx] = (((1.0-alpha50)*ema50[a] - (1.0-alpha20)*ema20[a])
                                 / spread_slope)
        ema_diff[valid_idx] = ema20[a] - ema50[a]

    # If a 12H candle is missing, retain the last confirmed EMA only until the
    # first expected missing candle has had its full 12H to close. Mark the
    # earliest observable 1H open at/after that deadline as a data exit.
    stale_trigger_ms = []
    missing_12h_periods = []
    for i, bar_open in enumerate(rows12.time.to_numpy(dtype=np.int64)):
        expected_next_open = int(bar_open + STEP_12H)
        next_actual = (int(rows12.time.iloc[i+1]) if i+1 < len(rows12) else None)
        if next_actual is None or next_actual > expected_next_open:
            expected_close = expected_next_open + STEP_12H
            stale_trigger_ms.append(expected_close)
            missing_12h_periods.append((expected_next_open, expected_close))
    stale_flag = np.zeros(len(hour_ms), dtype=float)
    stale_indices = []
    for when in stale_trigger_ms:
        idx = int(np.searchsorted(hour_ms, when, side="left"))
        if idx < len(hour_ms):
            stale_flag[idx] = 1.0
            stale_indices.append(idx)
    q = np.column_stack((hour, cross_line, ema_diff, stale_flag))

    prior_atr_pct = np.full(len(rows12), np.nan, dtype=float)
    atr_vals = atr.atr14.to_numpy(float)
    close_vals = rows12.close.to_numpy(float)
    for i in range(1, len(rows12)):
        if np.isfinite(atr_vals[i-1]) and close_vals[i-1] > 0:
            prior_atr_pct[i] = 100.0 * atr_vals[i-1] / close_vals[i-1]
    week_keys = [_ny_week_window(int(t))["week"] for t in rows12.time]
    source_counts = (bars12.source.value_counts(dropna=False).to_dict()
                     if "source" in bars12 else {})
    contiguous_h1_missing_12h = 0
    for missing_open, missing_close in missing_12h_periods:
        expected_hours = np.arange(missing_open, missing_close, HOUR_MS, dtype=np.int64)
        at = np.searchsorted(hour_ms, expected_hours, side="left")
        if (len(at) == 12 and np.all(at < len(hour_ms))
                and np.array_equal(hour_ms[at], expected_hours)):
            contiguous_h1_missing_12h += 1
    audit = {
        "ema_ready_12h": int(ready.sum()),
        "hourly_gap_count": int(np.count_nonzero(np.diff(hour_ms) > HOUR_MS)),
        "stale_12h_trigger_count": len(stale_indices),
        "stale_12h_trigger_times_ms": [int(hour_ms[i]) for i in stale_indices],
        "missing_12h_period_count": len(missing_12h_periods),
        "missing_12h_with_complete_contiguous_1h_count": contiguous_h1_missing_12h,
        "assertion_missing_12h_with_contiguous_1h_is_zero": contiguous_h1_missing_12h == 0,
        "bar12h_sources": source_counts,
        "row12h_count": len(rows12),
        "row1h_count": len(bars1),
    }
    features = {"close_times": close_times, "ema20": ema20, "ema50": ema50,
                "ema_ready": ready, "prior_atr_pct": prior_atr_pct,
                "week_keys": np.asarray(week_keys, dtype=object),
                "time12": rows12.time.to_numpy(dtype=np.int64),
                "stale_flag": stale_flag}
    return q, hour_ms, {"audit": audit, "features": features}


def _match_controls(signal_events: list[dict], bars12: pd.DataFrame,
                    bars1: pd.DataFrame, features: dict, config: dict) -> tuple[list[dict], dict]:
    """Sample one seeded non-signal 12H close per parent from exact match strata."""
    if bars12.empty or bars1.empty:
        return [], {"matched": 0, "unmatched": len(signal_events),
                    "unmatched_reasons": {"missing_input_bars": len(signal_events)}}
    index12 = bars12.index.to_numpy(dtype=np.int64)
    index1 = bars1.index.to_numpy(dtype=np.int64)
    map1 = {int(t): i for i, t in enumerate(index1)}
    signal_confirmations = {e["confirmation_ms"] for e in signal_events}
    eligible_by_stratum: dict[tuple[str, int], list[int]] = defaultdict(list)
    prior_atr = features["prior_atr_pct"]
    ema_side = np.sign(features["ema20"] - features["ema50"])
    ready = features["ema_ready"]
    week_keys = features["week_keys"]
    for i, bar_open in enumerate(index12):
        confirmation = int(bar_open + STEP_12H)
        if confirmation in signal_confirmations or confirmation not in map1:
            continue
        if not ready[i] or not np.isfinite(prior_atr[i]) or prior_atr[i] <= 0:
            continue
        window = _ny_week_window(int(bar_open))
        if (str(week_keys[i]) != window["week"] or int(bar_open) < window["monday_end"]
                or confirmation > window["expires"]):
            continue
        # Controls are sampled from the same NY calendar week and the same
        # direction whose EMA20/50 cloud is aligned with that direction.
        direction = int(ema_side[i])
        if direction not in (-1, 1):
            continue
        eligible_by_stratum[(str(week_keys[i]), direction)].append(i)
    candidate_groups = {key: sorted(set(rows)) for key, rows in eligible_by_stratum.items()}

    tolerance = float(config["control_matching"]["atr_relative_tolerance"])
    seed = int(config["control_matching"]["seed"])
    used: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    controls, reasons = [], Counter()
    unmatched_by_parent = {}
    for parent in sorted(signal_events, key=lambda e: (e["confirmation_ms"], e["source_row_number"])):
        if parent.get("entry_i") is None:
            reason = "signal_missing_next_hour_open"
            reasons[reason] += 1
            unmatched_by_parent[parent["parent_id"]] = reason
            continue
        if parent.get("risk_fraction") is None or parent["risk_fraction"] <= 0:
            reason = "signal_nonpositive_actual_risk"
            reasons[reason] += 1
            unmatched_by_parent[parent["parent_id"]] = reason
            continue
        signal_atr = parent.get("signal_prior_atr_pct")
        if not np.isfinite(signal_atr) or signal_atr <= 0:
            reason = "signal_prior_atr_unavailable"
            reasons[reason] += 1
            unmatched_by_parent[parent["parent_id"]] = reason
            continue
        group = candidate_groups.get((parent["week"], parent["side"]), [])
        if not group:
            reason = "no_non_signal_same_week_directional_ema_candidate"
            reasons[reason] += 1
            unmatched_by_parent[parent["parent_id"]] = reason
            continue
        possible = [i for i in group
                    if int(index12[i] + STEP_12H) > int(parent["confirmation_ms"])
                    if np.isfinite(prior_atr[i]) and prior_atr[i] > 0
                    and abs(prior_atr[i]/signal_atr - 1.0) <= tolerance]
        if not possible:
            reason = "no_later_candidate_within_atr_tolerance"
            reasons[reason] += 1
            unmatched_by_parent[parent["parent_id"]] = reason
            continue
        used_key = (parent["symbol"], parent["week"], parent["side"])
        possible = [i for i in possible if i not in used[used_key]]
        if not possible:
            reason = "later_matching_candidates_already_sampled"
            reasons[reason] += 1
            unmatched_by_parent[parent["parent_id"]] = reason
            continue
        stable_seed = (seed + zlib.crc32(parent["parent_id"].encode("utf-8"))) % (2**32)
        rng = np.random.default_rng(stable_seed)
        chosen = int(possible[int(rng.integers(0, len(possible)))])
        used[used_key].add(chosen)
        confirmation = int(index12[chosen] + STEP_12H)
        entry_i = map1[confirmation]
        entry_price = float(bars1.open.iloc[entry_i])
        side = int(parent["side"])
        risk = float(entry_price * parent["risk_fraction"])
        stop = float(entry_price - side * risk)
        controls.append({
            **{k: parent[k] for k in ("parent_id", "source_row_number", "symbol", "week",
                                      "side", "key_open_ms", "confirmation_ms", "rank",
                                      "was_top20", "is_current", "cohort", "bar_source")},
            "event_id": parent["parent_id"] + ":control",
            "is_control": True,
            "control_match_status": "matched",
            "control_confirmation_ms": confirmation,
            "control_12h_open_ms": int(index12[chosen]),
            "control_prior_atr_pct": float(prior_atr[chosen]),
            "control_atr_relative_difference": float(prior_atr[chosen]/signal_atr - 1.0),
            "entry_i": int(entry_i),
            "entry_ms": confirmation,
            "entry_price": entry_price,
            "stop_price": stop,
            "risk_distance": risk,
            "risk_fraction": float(parent["risk_fraction"]),
            "signal_prior_atr_pct": float(signal_atr),
            "signal_reference_entry": parent["signal_reference_entry"],
            "signal_reference_stop": parent["signal_reference_stop"],
            "stop_basis": "matched_signal_actual_entry_to_touched_1618_stop_risk_fraction",
            "entry_source": parent["entry_source"],
            "control_ema20": float(features["ema20"][chosen]),
            "control_ema50": float(features["ema50"][chosen]),
            "control_bar_source": (str(bars12.source.iloc[chosen]) if "source" in bars12 else ""),
        })
    return controls, {"matched": len(controls), "unmatched": len(unmatched_by_parent),
                      "unmatched_reasons": dict(reasons),
                      "unmatched_by_parent": unmatched_by_parent}


def _event_row(event: dict, tp_r: float, status: str, **extra) -> dict:
    row = {
        "event_id": event["event_id"], "id": event["event_id"],
        "parent_id": event["parent_id"],
        "source_row_number": event["source_row_number"],
        "symbol": event["symbol"], "week": event["week"], "side": event["side"],
        "tp_r": tp_r, "is_control": event["is_control"],
        "rank": event.get("rank"), "cohort": event.get("cohort"),
        "is_current": event.get("is_current"), "was_top20": event.get("was_top20"),
        "bar_source": event.get("bar_source"),
        "key_open_ms": event.get("key_open_ms"),
        "confirmation_ms": event.get("confirmation_ms"),
        "confirmed_bjt": event.get("confirmed_bjt"),
        "confirmation_12h_bar_present": event.get("confirmation_12h_bar_present"),
        "entry_ms": event.get("entry_ms"), "exit_bar_ms": None,
        "entry_time_utc": None, "exit_time_utc": None,
        "entry_price": event.get("entry_price"), "exit_price": None,
        "stop_price": event.get("stop_price"), "target_price": None,
        "risk_distance": event.get("risk_distance"),
        "risk_fraction": event.get("risk_fraction"),
        "entry_fee_class": "taker" if event.get("entry_price") is not None else None,
        "entry_fee_rate": TAKER if event.get("entry_price") is not None else None,
        "entry_fee_amount": None,
        "exit_fee_class": None, "exit_fee_rate": None, "exit_fee_amount": None,
        "exit_reason": status, "status": status, "exit_phase": None,
        "same_bar_ambiguous": False, "ambiguous_triggers": "",
        "gap_flag": False, "gap_hours_missing": 0,
        "gross_pnl": None, "net_pnl": None, "gross_r": None, "net_r": None,
        "entry_status": status,
        "control_match_status": event.get("control_match_status", "not_applicable"),
        "control_confirmation_ms": event.get("control_confirmation_ms"),
        "control_12h_open_ms": event.get("control_12h_open_ms"),
        "control_atr_relative_difference": event.get("control_atr_relative_difference"),
        "control_prior_atr_pct": event.get("control_prior_atr_pct"),
        "control_match_reason": event.get("control_match_reason", ""),
        "stop_basis": event.get("stop_basis"),
        "source_path_12h": event.get("source_path_12h"),
        "source_path_1h": event.get("source_path_1h"),
        "path_upper_bound_reason": event.get("path_upper_bound_reason"),
    }
    row.update(extra)
    return row


def _safe_path_end(start_i: int, side: int, q: np.ndarray, bar_ms: np.ndarray) -> tuple[int, str]:
    """Allocate only as many rows as could possibly keep the runner open."""
    if not 0 <= start_i < len(q):
        raise ValueError("Entry does not exist on the hourly grid")
    following = q[start_i+1:]
    gap = np.diff(bar_ms[start_i:]) > HOUR_MS
    stale = following[:,6] > 0
    opposite = side*following[:,5] < 0
    forced = np.flatnonzero(gap | stale | opposite)
    if not len(forced):
        return len(q)-1, "actual_hourly_series_tail"
    k = forced[0]
    reason = ("first_hour_gap_open" if gap[k] else "first_observable_stale_12h_open"
              if stale[k] else "first_opposite_confirmed_12h_open")
    return int(start_i+k+1), reason


def _simulate_symbol(events: list[dict], q: np.ndarray, bar_ms: np.ndarray,
                     tp_rs: tuple[float, ...], batch_size: int) -> tuple[list[dict], dict]:
    executable = [e for e in events if e.get("entry_i") is not None
                  and e.get("entry_price") is not None and e.get("stop_price") is not None]
    pre_rows = []
    for event in events:
        if (event.get("entry_i") is None or event.get("entry_price") is None
                or event.get("stop_price") is None):
            status = event.get("entry_status", event.get("control_match_status", "no_entry"))
            for target_r in tp_rs:
                pre_rows.append(_event_row(event, target_r, status))
    if not executable:
        return pre_rows, {"columns": 0, "orders": 0, "trades": 0}

    all_rows = list(pre_rows)
    total_columns = total_orders = total_trades = 0
    n_tp = len(tp_rs)
    for begin in range(0, len(executable), batch_size):
        batch = executable[begin:begin+batch_size]
        n_events = len(batch)
        starts_event = np.asarray([int(e["entry_i"]) for e in batch], dtype=np.int64)
        ends_event = np.asarray([int(e["end_i"]) for e in batch], dtype=np.int64)
        max_len = int(np.max(ends_event-starts_event)+1)
        n_cols = n_events*n_tp
        event_for_col = np.repeat(np.arange(n_events, dtype=np.int64), n_tp)
        starts = np.repeat(starts_event, n_tp)
        ends = np.repeat(ends_event, n_tp)
        sides = np.repeat(np.asarray([e["side"] for e in batch], dtype=np.float64), n_tp)
        stops = np.repeat(np.asarray([e["stop_price"] for e in batch], dtype=np.float64), n_tp)
        risk = np.repeat(np.asarray([e["risk_distance"] for e in batch], dtype=np.float64), n_tp)
        tp_col = np.tile(np.asarray(tp_rs, dtype=np.float64), n_events)
        targets = np.full(n_cols, np.nan, dtype=np.float64)
        for col in range(n_cols):
            if tp_col[col] > 0.0 and np.isfinite(risk[col]) and risk[col] > 0:
                targets[col] = q[starts[col], 0] + sides[col]*tp_col[col]*risk[col]

        marked = np.empty((max_len, n_cols), dtype=np.float64)
        offsets = np.arange(max_len, dtype=np.int64)
        for col in range(n_cols):
            positions = np.minimum(starts[col] + offsets, ends[col])
            marked[:, col] = q[positions, 3]

        reason_codes = np.zeros(n_cols, dtype=np.int8)
        phase_codes = np.zeros(n_cols, dtype=np.int8)
        ambiguous = np.zeros(n_cols, dtype=np.int8)
        gap_flags = np.zeros(n_cols, dtype=np.bool_)
        gap_hours = np.zeros(n_cols, dtype=np.int16)
        capital = np.maximum(np.repeat(np.asarray([e["entry_price"] for e in batch]), n_tp), 1.0)*10.0
        portfolio = vbt.Portfolio.from_order_func(
            marked, _order_func, q, bar_ms, starts, ends, sides, stops, targets,
            tp_col, n_tp, reason_codes, phase_codes, ambiguous, gap_flags, gap_hours,
            flexible=True, init_cash=capital, cash_sharing=False, group_by=False,
            max_orders=2*n_cols, freq="1h")
        order_records = pd.DataFrame(portfolio.orders.records).sort_values(["col", "id"])
        trade_records = pd.DataFrame(portfolio.trades.records).sort_values("col")
        if len(order_records) != 2*n_cols or len(trade_records) != n_cols:
            raise AssertionError("VectorBT must fill and close every independent event/TP column")
        if not trade_records.status.eq(1).all():
            raise AssertionError("A VectorBT event column ended without a closed position")
        if not order_records.groupby("col").size().eq(2).all():
            raise AssertionError("Each VectorBT column must have exactly entry and exit fills")
        total_columns += n_cols
        total_orders += len(order_records)
        total_trades += len(trade_records)

        for col, event_index in enumerate(event_for_col):
            event = batch[int(event_index)]
            tp_r = float(tp_col[col])
            entry_fill, exit_fill = order_records.iloc[col*2], order_records.iloc[col*2+1]
            trade = trade_records.iloc[col]
            entry_price, exit_price = float(entry_fill.price), float(exit_fill.price)
            entry_bar_ms = int(bar_ms[starts[col]])
            exit_bar_ms = int(bar_ms[starts[col]+int(exit_fill.idx)])
            if (int(reason_codes[col]) == 8
                    and starts[col]+int(exit_fill.idx) != len(bar_ms)-1):
                raise AssertionError("series_tail_close fired before the actual 1H file tail")
            gross_pnl = float(trade.pnl + trade.entry_fees + trade.exit_fees)
            net_pnl = float(trade.pnl)
            denom = float(risk[col])
            reason = REASON_NAMES[int(reason_codes[col])]
            fee_class = "maker" if reason in ("take_profit", "take_profit_open") else "taker"
            target_price = float(targets[col]) if np.isfinite(targets[col]) else None
            mask = int(ambiguous[col])
            trigger_names = [name for bit, name in ((1, "stop"), (2, "ema_flip"), (4, "take_profit"))
                             if mask & bit]
            ambiguous_triggers = "+".join(trigger_names)
            row = _event_row(event, tp_r, "executed")
            row.update({
                "entry_ms": entry_bar_ms,
                "exit_bar_ms": exit_bar_ms,
                "entry_time_utc": pd.Timestamp(entry_bar_ms, unit="ms", tz="UTC").isoformat(),
                "exit_time_utc": pd.Timestamp(exit_bar_ms, unit="ms", tz="UTC").isoformat(),
                "entry_price": entry_price, "exit_price": exit_price,
                "stop_price": float(stops[col]), "target_price": target_price,
                "risk_distance": denom,
                "risk_fraction": denom/entry_price if entry_price > 0 else None,
                "entry_fee_amount": float(entry_fill.fees),
                "exit_fee_class": fee_class,
                "exit_fee_rate": MAKER if fee_class == "maker" else TAKER,
                "exit_fee_amount": float(exit_fill.fees),
                "exit_reason": reason,
                "exit_phase": PHASE_NAMES[int(phase_codes[col])],
                "same_bar_ambiguous": bool(ambiguous[col]),
                "ambiguous_triggers": ambiguous_triggers,
                "gap_flag": bool(gap_flags[col]),
                "gap_hours_missing": int(gap_hours[col]),
                "gross_pnl": gross_pnl, "net_pnl": net_pnl,
                "gross_r": gross_pnl/denom if denom > 0 else None,
                "net_r": net_pnl/denom if denom > 0 else None,
                "entry_status": "executed",
                "status": "executed",
                "path_upper_bound_reason": event.get("path_upper_bound_reason"),
            })
            all_rows.append(row)
    return all_rows, {"columns": total_columns, "orders": total_orders,
                      "trades": total_trades}


def _record_unmatched_control(parent: dict, reason: str) -> dict:
    return {
        **{k: parent.get(k) for k in ("parent_id", "source_row_number", "symbol", "week", "side",
                                     "key_open_ms", "confirmation_ms", "confirmed_bjt", "rank",
                                     "was_top20", "is_current", "cohort", "bar_source",
                                     "source_path_12h", "source_path_1h")},
        "event_id": parent["parent_id"] + ":control",
        "is_control": True,
        "control_match_status": "unmatched",
        "control_match_reason": reason,
        "entry_i": None, "entry_ms": None, "entry_price": None,
        "stop_price": None, "risk_distance": None, "risk_fraction": None,
    }


def generate(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(float(x) for x in config["targets_r"]) != TP_R:
        raise ValueError(f"Frozen V1.7 target set must remain {TP_R}")
    if (float(config["fees"]["taker_per_fill"]) != TAKER
            or float(config["fees"]["resting_maker_per_fill"]) != MAKER):
        raise ValueError("Frozen OKX fees must remain taker 5bp / resting maker 2bp per fill")
    signal_path = ROOT / config["signals_path"]
    out_path = ROOT / config["output_paths_csv_gz"]
    audit_path = ROOT / config["output_audit_json"]
    if out_path.exists() or audit_path.exists():
        raise FileExistsError("Refusing to overwrite existing frozen path outputs")

    signals = pd.read_csv(signal_path, low_memory=False)
    expected = int(config["expected_signal_rows"])
    if len(signals) != expected:
        raise ValueError(f"Expected {expected} original signal rows, found {len(signals)}")
    required = {"symbol", "week", "side", "key_open_bjt", "confirmed_bjt",
                "reference_entry", "reference_stop", "stop_pct", "prior_atr_pct",
                "rank", "was_top20", "is_current", "cohort", "bar_source"}
    missing = sorted(required - set(signals.columns))
    if missing:
        raise ValueError(f"signals.csv missing required columns: {missing}")
    if signals.duplicated(["symbol", "week", "side", "key_open_bjt", "confirmed_bjt"]).any():
        raise ValueError("Original signal join key is not unique")

    events_by_symbol: dict[str, list[dict]] = defaultdict(list)
    signal_events_by_symbol, signal_sources = defaultdict(list), Counter()
    for row_number, raw in enumerate(signals.to_dict("records"), start=1):
        row = dict(raw)
        row["symbol"] = str(row["symbol"]).strip()
        row["week"] = str(row["week"]).strip()
        row["side"] = int(row["side"])
        row["source_row_number"] = row_number
        row["key_open_ms"] = _parse_ms(row["key_open_bjt"])
        row["confirmation_ms"] = _parse_ms(row["confirmed_bjt"])
        row["parent_id"] = _signal_id(row)
        row["event_id"] = row["parent_id"] + ":signal"
        row["is_control"] = False
        row["entry_status"] = "pending"
        row["signal_reference_entry"] = float(row["reference_entry"])
        row["signal_reference_stop"] = float(row["reference_stop"])
        row["signal_prior_atr_pct"] = float(row["prior_atr_pct"]) if pd.notna(row["prior_atr_pct"]) else np.nan
        row["stop_price"] = float(row["reference_stop"])
        row["entry_i"] = None
        row["entry_ms"] = None
        row["entry_price"] = None
        row["risk_distance"] = None
        row["risk_fraction"] = None
        row["stop_basis"] = "original_touched_1618_reference_stop"
        row["control_match_status"] = "parent_signal"
        row["source_path_12h"] = None
        row["source_path_1h"] = None
        events_by_symbol[row["symbol"]].append(row)
        signal_events_by_symbol[row["symbol"]].append(row)
        signal_sources[str(row.get("bar_source", "missing"))] += 1

    # Only code and configuration identify this run; historical data are the
    # frozen source contract. Finished symbols survive interruption or retry.
    revision = hashlib.sha256(Path(__file__).read_bytes()+config_path.read_bytes()).hexdigest()[:16]
    checkpoint = out_path.parent / 'work' / revision
    checkpoint.mkdir(parents=True,exist_ok=True)
    vbt_totals = Counter()
    source_selection_counts = Counter()
    symbol_audits = {}
    result_frames = []
    batch_size = int(config["execution"]["event_batch_size"])

    for symbol in sorted(events_by_symbol):
        symbol_events = signal_events_by_symbol[symbol]
        was_top20 = bool(symbol_events[0]["was_top20"])
        p12, p1, source_group = _choose_input(symbol, was_top20, config)
        source_selection_counts[source_group] += 1
        cache_data = checkpoint / (symbol+'.csv.gz')
        cache_meta = checkpoint / (symbol+'.json')
        if cache_meta.is_file() and cache_data.is_file():
            receipt = json.loads(cache_meta.read_text())
            symbol_audits[symbol] = receipt
            vbt_totals.update(receipt['vectorbt'])
            result_frames.append(pd.read_csv(cache_data,float_precision='round_trip'))
            continue
        bars12, bars1 = _load_bars(p12, "12h"), _load_bars(p1, "1h")
        q, bar_ms, prepared = _prepare_symbol(bars12, bars1)
        feature = prepared.get("features", {})
        sym_audit = {
            "input_group": source_group,
            "bars12h_path": str(p12.relative_to(ROOT)),
            "bars1h_path": str(p1.relative_to(ROOT)),
            "bars12h_rows": len(bars12), "bars1h_rows": len(bars1),
            "bars12h_first_ms": int(bars12.index[0]) if len(bars12) else None,
            "bars12h_last_open_ms": int(bars12.index[-1]) if len(bars12) else None,
            "bars1h_first_ms": int(bars1.index[0]) if len(bars1) else None,
            "bars1h_last_open_ms": int(bars1.index[-1]) if len(bars1) else None,
            "signals": len(symbol_events),
            "bar12h_sources": prepared.get("audit", {}).get("bar12h_sources", {}),
            "hourly_gap_count": prepared.get("audit", {}).get("hourly_gap_count", 0),
            "stale_12h_trigger_count": prepared.get("audit", {}).get("stale_12h_trigger_count", 0),
            "missing_12h_period_count": prepared.get("audit", {}).get("missing_12h_period_count", 0),
            "missing_12h_with_complete_contiguous_1h_count": prepared.get("audit", {}).get(
                "missing_12h_with_complete_contiguous_1h_count", 0),
        }

        if len(q) == 0:
            for event in symbol_events:
                event["entry_status"] = "missing_input_bars"
                event["source_path_12h"] = str(p12.relative_to(ROOT))
                event["source_path_1h"] = str(p1.relative_to(ROOT))
            rows = []
            for event in [*symbol_events, *[_record_unmatched_control(e, "missing_input_bars") for e in symbol_events]]:
                for target_r in TP_R:
                    rows.append(_event_row(event, target_r, event.get("entry_status", "unmatched_control")))
            result_frames.append(pd.DataFrame(rows))
            sym_audit["entry_missing_next_hour_open"] = len(symbol_events)
            sym_audit["control_matches"] = 0
            sym_audit["control_unmatched"] = len(symbol_events)
            sym_audit["control_unmatched_reasons"] = {"missing_input_bars": len(symbol_events)}
            sym_audit["vectorbt"] = {"columns": 0, "orders": 0, "trades": 0}
            symbol_audits[symbol] = sym_audit
            continue

        map_hour = {int(t): i for i, t in enumerate(bar_ms)}
        map_12h = {int(t): i for i, t in enumerate(bars12.index.to_numpy(dtype=np.int64))}
        # Bind each actual signal to the exact next-hour open. A missing hour is
        # retained as no-entry; no later open is substituted.
        missing_entries = 0
        for event in symbol_events:
            event["source_path_12h"] = str(p12.relative_to(ROOT))
            event["source_path_1h"] = str(p1.relative_to(ROOT))
            confirmation = int(event["confirmation_ms"])
            key_open = int(event["key_open_ms"])
            if key_open % STEP_12H != 0 or confirmation != key_open+STEP_12H:
                raise ValueError(f"Signal row is not a closed UTC12H event: {event['parent_id']}")
            event["confirmation_12h_bar_present"] = key_open in map_12h
            idx = map_hour.get(confirmation)
            if idx is None:
                event["entry_status"] = "no_entry_missing_next_hour_open"
                missing_entries += 1
                continue
            entry = float(q[idx, 0])
            stop = float(event["reference_stop"])
            risk = abs(entry-stop)
            event["entry_i"] = int(idx)
            event["entry_ms"] = confirmation
            event["entry_price"] = entry
            event["risk_distance"] = risk
            event["risk_fraction"] = risk/entry if entry > 0 else None
            event["stop_price"] = stop
            event["entry_source"] = "next_observed_1h_open"
            event["entry_status"] = "pending"

        controls, control_summary = _match_controls(symbol_events, bars12, bars1,
                                                    feature, config)
        matched_ids = {c["parent_id"] for c in controls}
        unmatched_for_symbol = []
        for event in symbol_events:
            if event["parent_id"] in matched_ids:
                event["stop_basis"] = "original_touched_1618_reference_stop"
            else:
                reason = control_summary.get("unmatched_by_parent", {}).get(
                    event["parent_id"], "no_eligible_random_match")
                unmatched = _record_unmatched_control(event, reason)
                unmatched_for_symbol.append(unmatched)
        all_symbol_events = [*symbol_events, *controls, *unmatched_for_symbol]
        for event in all_symbol_events:
            if event.get("entry_i") is not None:
                event["source_path_12h"] = str(p12.relative_to(ROOT))
                event["source_path_1h"] = str(p1.relative_to(ROOT))
                event.setdefault("entry_source", "next_observed_1h_open")
            if event.get("entry_status") == "pending":
                event.pop("entry_status", None)
            if event.get("entry_i") is not None:
                event["end_i"], event["path_upper_bound_reason"] = _safe_path_end(
                    int(event["entry_i"]), int(event["side"]), q, bar_ms)
        rows, execution = _simulate_symbol(all_symbol_events, q, bar_ms, TP_R, batch_size)
        result_frames.append(pd.DataFrame(rows))
        vbt_totals.update(execution)
        sym_audit["entry_missing_next_hour_open"] = missing_entries
        sym_audit["control_matches"] = len(controls)
        sym_audit["control_unmatched"] = len(unmatched_for_symbol)
        sym_audit["control_unmatched_reasons"] = control_summary["unmatched_reasons"]
        sym_audit["control_match_method"] = (
            "same NY week, strictly after parent confirmation, same side and aligned EMA20/50; "
            "previous 12H Wilder ATR14/close within configured relative tolerance; one seeded draw without replacement"
        )
        sym_audit["safe_path_upper_bound_counts"] = dict(Counter(
            e.get("path_upper_bound_reason", "not_executable") for e in all_symbol_events))
        sym_audit["vectorbt"] = execution
        symbol_audits[symbol] = sym_audit
        result_frames[-1].to_csv(cache_data,index=False,compression='gzip')
        _write_json(cache_meta,sym_audit)
        if len(symbol_audits)%25==0 or len(symbol_audits)==len(events_by_symbol):
            print(f"Completed {len(symbol_audits)}/{len(events_by_symbol)} symbols",flush=True)

    paths = pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame()
    if paths.empty:
        raise ValueError("No path rows produced")
    if paths.duplicated(["event_id", "tp_r"]).any():
        raise AssertionError("Path identity (event_id,tp_r) is not unique")
    paths = paths.sort_values(["source_row_number", "is_control", "tp_r"],
                              ascending=[True, True, True], kind="stable")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    paths.to_csv(out_path, index=False, compression="gzip", float_format="%.12g")

    count_summary = {
        "signal_rows": len(signals),
        "signal_paths_expected": len(signals)*len(TP_R),
        "signal_paths_written": int((~paths.is_control).sum()),
        "control_events_written": int(paths[paths.is_control].event_id.nunique()),
        "control_paths_written": int(paths.is_control.sum()),
        "control_matched_events": int(paths[paths.is_control].query("control_match_status == 'matched'").event_id.nunique()),
        "control_unmatched_events": int(paths[paths.is_control].query("control_match_status == 'unmatched'").event_id.nunique()),
        "rows_total": len(paths),
        "entry_status_counts": paths.entry_status.value_counts(dropna=False).to_dict(),
        "exit_reason_counts": paths.exit_reason.value_counts(dropna=False).to_dict(),
        "fee_class_exit_counts": paths.exit_fee_class.value_counts(dropna=False).to_dict(),
        "same_bar_ambiguous_rows": int(paths.same_bar_ambiguous.sum()),
        "gap_exit_rows": int(paths.gap_flag.sum()),
        "tail_exit_rows": int(paths.exit_reason.eq("series_tail_close").sum()),
    }
    audit = {
        "experiment_id": config["experiment_id"],
        "generator": "yoyo/evaluation/cloud_close_reward_paths.py",
        "vectorbt_version": vbt.__version__,
        "numpy_version": version("numpy"), "pandas_version": version("pandas"),
        "numba_version": version("numba"),
        "input_signal_path": str(signal_path.relative_to(ROOT)),
        "input_signal_rows": len(signals),
        "signal_source_counts": dict(signal_sources),
        "signal_parent_join_key": {
            "parent_id": "signal-row-{source_row_number:05d}",
            "source_row_number": "1-based CSV data-row ordinal in the frozen signals.csv",
            "join": "paths.source_row_number = signals.csv 1-based data-row ordinal; parent_id is the same row identity",
            "cross_check_columns": ["symbol", "week", "side", "key_open_bjt", "confirmed_bjt"],
        },
        "entry_timing": {
            "confirmation": "12Hutc bar close; confirmed_bjt converted to UTC milliseconds",
            "entry": "the 1H bar whose open time equals confirmation_ms, filled at its open",
            "order": "12H close confirmation is processed before the next 1H open at the same UTC boundary",
            "no_entry": "if that exact 1H open is absent, retain an explicit no-entry row; never delay to a later hour",
        },
        "ema_exit": {
            "source": "pure local recurrence identical to yoyo.monitor.cloud_signals.ema_points (avoids importing its FastAPI service module)",
            "ema_periods": [20, 50],
            "intrabar_level": "price at which EMA20 and EMA50 would strictly reverse given the prior confirmed 12H EMA state",
            "strict_cross": "long low < level; short high > level; equality is not a flip",
            "dynamic_update": "refresh at each newly closed 12H boundary; no unfinished 12H close enters the EMA state",
        },
        "risk_and_targets": {
            "signal_stop": "reference_stop from the original touched-side 1.618 default plan; absolute stop price retained",
            "target_levels_r": list(float(x) for x in config["targets_r"]),
            "target_price": "actual 1H-open entry plus side * tp_r * abs(actual entry - fixed stop)",
            "control_stop": "control entry adjusted to the matched parent's actual entry-to-reference-stop risk fraction",
            "only_variable": "fixed potential take-profit R; no TP/1R/2R/3R/4R/6R/10R",
        },
        "fill_policy": {
            "engine": "VectorBT Portfolio.from_order_func flexible callback; independent columns, one base unit each",
            "same_bar_priority": "opening triggers are processed first; with no opening trigger, choose the nearer same-direction adverse barrier (EMA/stop) before TP; adverse plus opposite-side TP double touch is adverse-first and flagged",
            "same_bar_ambiguity": "flag only when no opening trigger occurred and adverse plus opposite-side target are both touched in the same OHLC bar",
            "open_target": "an open-through resting target fills at the target before later wicks are observable",
            "stop_market_fill": "bar open if already through at open, otherwise exact stop level",
            "ema_market_fill": "bar open if already strictly through at open, otherwise EMA cross level",
            "tp_limit_fill": "stated target level, maker fee, including an open-through",
            "unexpected_gap": "next observed 1H open markout before processing that bar's OHLC; taker fee and gap flag",
            "series_tail": "last observed 1H close, taker fee, exit_phase=close",
            "fees_per_fill": {"taker": TAKER, "resting_maker": MAKER},
            "funding_slippage_contract_multiplier": "not modeled",
        },
        "control_matching": {
            "method": "seeded random non-signal 12H confirmation, same symbol/NY week/side and EMA20-50 alignment",
            "atr_feature": "previous confirmed 12H Wilder ATR14 / previous close, matching census definition",
            "atr_relative_tolerance": config["control_matching"]["atr_relative_tolerance"],
            "sampling": "one without-replacement control per symbol/week/side stratum where possible",
            "future_path_completeness_filter": False,
            "unmatched": "explicit unmatched control path rows and per-symbol audit reason counts",
        },
        "input_selection_counts": dict(source_selection_counts),
        "counts": count_summary,
        "vectorbt_execution_totals": dict(vbt_totals),
        "symbols": symbol_audits,
        "output_paths_csv_gz": str(out_path.relative_to(ROOT)),
        "output_schema": list(paths.columns),
    }
    _write_json(audit_path, audit)
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=ROOT/"experiments/active/exp-cloud-close-reward-20261109-v1/paths_config.json")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else ROOT/args.config
    audit = generate(config_path)
    print(json.dumps({"experiment_id": audit["experiment_id"],
                      "counts": audit["counts"],
                      "vectorbt_execution_totals": audit["vectorbt_execution_totals"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
