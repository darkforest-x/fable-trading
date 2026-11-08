"""Rebuild the first OKX 4H V1.7 key and compare a frozen 50% partial account.

The OKX 4H screen is derived from archived OKX H1 OHLC. It uses each frozen
12H parent's exact same-week Monday 1.618 range, checked against a complete
Monday H1 reconstruction, and the source cloud signal's causal close/EMA/wick
rules. Fills are not regenerated: both account arms reuse the frozen VectorBT
0R cloud and 2R target paths.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import gc
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_leadership as leadership
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_slots as capacity
from yoyo.monitor.cloud_signal_plans import fixed_stop
from yoyo.monitor.cloud_signal_rules import first_eligible
from yoyo.monitor.monday_fib import baseline as monday_baseline
from yoyo.monitor.monday_fib import week_window


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-okx-agreement-20261109-v1"
BINANCE_SPLIT = ROOT / "experiments/active/exp-cloud-close-agreement-split-20261109-v1"
BINANCE_LOWER = ROOT / "experiments/active/exp-cloud-close-lower-agreement-20261109-v1"
BINANCE_REPAIR = ROOT / "experiments/active/exp-cloud-close-binance-gap-repair-20261109-v1"
SIGNALS_PATH = wallet.SOURCE / "signals.csv"
PARENT_PATHS_PATH = wallet.EXP / "paths.csv.gz"
HOUR_MS = wallet.HOUR
STEP_12H = 12 * HOUR_MS
STEP_4H = 4 * HOUR_MS
TAKER = wallet.TAKER
MAKER = engine.MAKER
FRACTION_2R = 0.50
SLOTS = 9
NY = ZoneInfo("America/New_York")
COMMON_BINANCE_END = "2026-09-24T00:00:00Z"

ARM_BASE = "okx_90d_no_4h_filter"
ARM_4H = "okx_90d_same_week_first_4h_agreement"
ARMS = (ARM_BASE, ARM_4H)

PARENT_SIGNAL_FIELDS = {
    "symbol", "week", "side", "key_open_bjt", "confirmed_bjt", "reference_entry",
    "confirmation_close", "reference_stop", "opposite_target", "ema20", "ema50",
    "signal_open", "signal_high", "signal_low", "bar_source", "prior_atr_pct",
}
CATALOG_FIELDS = {
    "event_id", "parent_id", "source_row_number", "symbol", "week", "side", "tp_r",
    "is_control", "entry_status", "entry_ms", "key_open_ms", "confirmation_ms",
    "entry_price", "stop_price", "risk_distance", "risk_fraction", "exit_bar_ms",
    "exit_phase", "exit_reason", "source_path_12h", "source_path_1h",
    "control_confirmation_ms", "control_12h_open_ms",
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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(data), ensure_ascii=False,
                               indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _ms(value) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError(f"Timezone required for frozen timestamp: {value}")
    return int(stamp.tz_convert("UTC").value // 1_000_000)


def _timestamp_column_ms(values: pd.Series) -> pd.Series:
    """Convert timezone-aware timestamps to epoch ms independent of pandas unit."""
    parsed = pd.to_datetime(values, utc=True)
    return parsed.map(lambda stamp: int(stamp.value // 1_000_000)).astype("int64")


def _ema_points(bars: pd.DataFrame, step_ms: int) -> pd.DataFrame:
    """Source recurrence: close-only EMA20/50, reset at any absent UTC bar."""
    a = b = None
    previous = None
    count = 0
    rows = []
    for stamp, close in zip(bars.index.to_numpy(dtype=np.int64),
                            bars.close.to_numpy(dtype=float)):
        stamp, close = int(stamp), float(close)
        if previous is None or stamp != previous + step_ms:
            a = b = close
            count = 0
        a += (close - a) * (2.0 / 21.0)
        b += (close - b) * (2.0 / 51.0)
        count += 1
        rows.append((stamp, a, b, count, count >= 50))
        previous = stamp
    return pd.DataFrame(rows, columns=["time", "ema20", "ema50", "count", "ready"]).set_index("time")


def _aggregate_complete_4h(bars1: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Aggregate only complete UTC 4H buckets containing their exact four H1 rows."""
    clock = bars1.index.to_numpy(dtype=np.int64)
    if not len(clock):
        return pd.DataFrame(columns=["open", "high", "low", "close"]), {
            "h1_rows": 0, "complete_4h_rows": 0, "partial_4h_buckets": 0,
        }
    if (clock % HOUR_MS).any():
        raise ValueError("Archived OKX H1 timestamps must be UTC hour opens")
    groups, starts, counts = np.unique(clock // STEP_4H * STEP_4H,
                                       return_index=True, return_counts=True)
    ohlc = bars1[["open", "high", "low", "close"]].to_numpy(dtype=float)
    eligible = np.flatnonzero(counts == 4)
    starts, groups = starts[eligible], groups[eligible]
    valid = ((clock[starts] == groups)
             & (clock[starts + 1] == groups + HOUR_MS)
             & (clock[starts + 2] == groups + 2 * HOUR_MS)
             & (clock[starts + 3] == groups + 3 * HOUR_MS))
    starts, groups = starts[valid], groups[valid]
    frame = pd.DataFrame({"time": groups,
                          "open": ohlc[starts, 0],
                          "high": np.maximum.reduce([ohlc[starts + i, 1] for i in range(4)]),
                          "low": np.minimum.reduce([ohlc[starts + i, 2] for i in range(4)]),
                          "close": ohlc[starts + 3, 3]})
    if not frame.empty:
        frame = frame.set_index("time").sort_index()
    else:
        frame = pd.DataFrame(columns=["open", "high", "low", "close"],
                             index=pd.Index([], name="time", dtype="int64"))
    return frame, {"h1_rows": len(clock), "complete_4h_rows": len(frame),
                   "partial_4h_buckets": int(np.unique(clock // STEP_4H * STEP_4H).size - len(frame))}


def _monday_window(week: str) -> dict:
    day = date.fromisoformat(str(week))
    if day.weekday() != 0:
        raise ValueError(f"NY week is not keyed to Monday: {week}")
    stamp = lambda d: int(datetime.combine(d, time.min, NY).timestamp() * 1000)
    start, end = stamp(day), stamp(day + timedelta(days=1))
    following = stamp(day + timedelta(days=7))
    source = week_window(start)
    if source["week"] != str(week) or source["start"] != start or source["end"] != end:
        raise AssertionError("Local week clock diverges from monday_fib.week_window")
    return {"week": str(week), "start_ms": start, "end_ms": end,
            "expires_ms": following}


def _parent_levels(row: dict) -> tuple[float, float]:
    """Return the frozen original Monday lower/upper 1.618 prices."""
    entry, target, side = float(row["reference_entry"]), float(row["opposite_target"]), int(row["side"])
    lower, upper = (entry, target) if side == 1 else (target, entry)
    if side not in (-1, 1) or not (0.0 < lower < upper):
        raise ValueError(f"Invalid parent weekly 1.618 references: {row['symbol']} {row['week']}")
    return lower, upper


def _anchor_from_parent_week(week: str, parent: dict, bars1: pd.DataFrame) -> dict:
    """Use monday_fib.baseline on complete H1 Monday rows, cross-check parent levels.

    If an archived H1 Monday is incomplete, invert only the frozen original
    1.618 lower/upper levels from signals.csv. This is an explicit parent-level
    fallback, never an OHLC or level imputation.
    """
    window = _monday_window(week)
    expected = np.arange(window["start_ms"], window["end_ms"], HOUR_MS, dtype=np.int64)
    clock = bars1.index.to_numpy(dtype=np.int64)
    pos = np.searchsorted(clock, expected, side="left")
    complete = (len(pos) == 24 and np.all(pos < len(clock))
                and np.array_equal(clock[pos], expected))
    parent_lower, parent_upper = _parent_levels(parent)
    if complete:
        monday = bars1.iloc[pos]
        raw = [[int(t), float(r.open), float(r.high), float(r.low), float(r.close),
                0, 0, 0, "1"] for t, r in zip(expected, monday.itertuples())]
        # At NY Tuesday 00:00 the full Monday is known. monday_fib.baseline
        # returns range levels even when its unrelated post-Monday history check
        # reports missing_history because the first Tuesday H1 is not supplied.
        source = monday_baseline(raw, window["end_ms"])
        if not all(source.get(k) is not None for k in ("high", "low", "upper", "lower")):
            raise ValueError(f"monday_fib could not form a full H1 anchor: {parent['symbol']} {week}")
        tol = max(1e-9, 1e-10 * max(abs(parent_lower), abs(parent_upper)))
        if (abs(float(source["lower"]) - parent_lower) > tol
                or abs(float(source["upper"]) - parent_upper) > tol):
            raise ValueError(f"Frozen parent Monday levels disagree with complete H1 source: {parent['symbol']} {week}")
        return {**window, "state": "ready", "high": float(source["high"]),
                "low": float(source["low"]), "lower": float(source["lower"]),
                "upper": float(source["upper"]), "source": "monday_fib.baseline_complete_archived_H1",
                "monday_h1_complete": True, "parent_reference_checked": True,
                "source_post_monday_state": str(source["state"])}

    # Parent's exact entry/opposite-target pair determines the source range:
    # upper-lower = (1 + 2*0.618) * (Monday high-low).
    range_width = (parent_upper - parent_lower) / (1.0 + 2.0 * 0.618)
    low = parent_lower + 0.618 * range_width
    high = parent_upper - 0.618 * range_width
    lower = low - 0.618 * (high - low)
    upper = high + 0.618 * (high - low)
    tol = max(1e-8, 1e-9 * max(abs(parent_lower), abs(parent_upper)))
    if abs(lower - parent_lower) > tol or abs(upper - parent_upper) > tol:
        raise ValueError(f"Frozen parent references cannot reconstruct Monday range: {parent['symbol']} {week}")
    return {**window, "state": "ready", "high": high, "low": low,
            "lower": parent_lower, "upper": parent_upper,
            "source": "inverted_frozen_parent_12h_1p618_references",
            "monday_h1_complete": False, "parent_reference_checked": True,
            "monday_h1_missing_count": int(24 - np.isin(expected, clock).sum())}


def _signal_4h(symbol: str, stamp: int, bar: dict, point: dict, anchor: dict) -> dict | None:
    """Pure adaptation of cloud_signals.signal with STEP=4H and same strict tests."""
    step = STEP_4H
    end, expires = int(anchor["end_ms"]), int(anchor["expires_ms"])
    if (not bool(point["ready"]) or int(point["time"]) != stamp or stamp % step
            or stamp + step > expires or anchor.get("state") != "ready"
            or not end <= stamp < stamp + step <= expires
            or week_window(stamp)["week"] != anchor["week"]):
        return None
    lower, upper = float(anchor["lower"]), float(anchor["upper"])
    close, low, high = float(bar["close"]), float(bar["low"]), float(bar["high"])
    ema20, ema50 = float(point["ema20"]), float(point["ema50"])
    side = (1 if ema20 > ema50 and low < lower < close < upper else
            -1 if ema20 < ema50 and lower < close < upper < high else 0)
    if not side:
        return None
    entry, target = (lower, upper) if side == 1 else (upper, lower)
    stop, _ = fixed_stop(symbol, side, entry)
    if stop is None:
        return None
    risk, reward = (entry - stop) * side, (target - entry) * side
    if not all(math.isfinite(v) and v > 0 for v in (close, entry, stop, target, risk, reward)):
        return None
    return {"id": f"okx-v1.7-4h:{symbol}:{stamp}:{side}", "symbol": symbol,
            "week": anchor["week"], "side": side, "timeframe": "4H",
            "signal_open_ms": int(stamp), "confirmed_ms": int(stamp + step),
            "ema20": ema20, "ema50": ema50, "signal_open": float(bar["open"]),
            "signal_high": high, "signal_low": low, "confirmation_close": close,
            "entry": entry, "stop": float(stop), "target": target,
            "planned_rr": float(reward / risk), "range_source": anchor["source"]}


def _read_parent(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(ROOT / config["inputs"]["signals_csv"], low_memory=False)
    missing = sorted(PARENT_SIGNAL_FIELDS - set(signals.columns))
    if missing:
        raise ValueError(f"Frozen signals.csv missing required fields: {missing}")
    signals["source_row_number"] = np.arange(1, len(signals) + 1, dtype=np.int64)
    signals["key_open_ms"] = _timestamp_column_ms(signals.key_open_bjt)
    signals["confirmation_ms"] = _timestamp_column_ms(signals.confirmed_bjt)
    signals["parent_id"] = signals.source_row_number.map(lambda n: f"signal-row-{int(n):05d}")
    if signals.duplicated(["symbol", "week"]).any():
        raise ValueError("The frozen 12H first-key ledger has multiple signal rows per symbol/NY week")

    path = ROOT / config["inputs"]["parent_paths_csv_gz"]
    header = pd.read_csv(path, nrows=0).columns
    missing = sorted(CATALOG_FIELDS - set(header))
    if missing:
        raise ValueError(f"Frozen path schema missing required fields: {missing}")
    catalog = pd.read_csv(path, usecols=sorted(CATALOG_FIELDS), low_memory=False,
                          dtype={"symbol": str, "event_id": str, "parent_id": str},
                          float_precision="round_trip")
    catalog = catalog[catalog.tp_r.eq(0.0)].copy()
    if catalog.duplicated("event_id").any():
        raise ValueError("Frozen parent runner has duplicate event identities")
    actual = catalog[~catalog.is_control].copy()
    actual["source_row_number"] = actual.source_row_number.astype("int64")
    if actual.duplicated("source_row_number").any():
        raise ValueError("Frozen parent runner has multiple actual events per source row")
    joined = signals.merge(actual[["source_row_number", "parent_id", "event_id", "symbol", "week",
                                   "side", "entry_ms", "key_open_ms", "confirmation_ms",
                                   "source_path_12h", "source_path_1h"]],
                           on="source_row_number", how="left", suffixes=("_signal", "_path"),
                           validate="one_to_one")
    expected_parent = joined.source_row_number.map(lambda n: f"signal-row-{int(n):05d}")
    if (joined.event_id.isna().any()
            or not joined.parent_id_signal.eq(expected_parent).all()
            or not joined.parent_id_path.eq(expected_parent).all()):
        raise ValueError("Parent source_row_number join is incomplete or nonunique")
    for signal_key, path_key in (("key_open_ms_signal", "key_open_ms_path"),
                                 ("confirmation_ms_signal", "confirmation_ms_path")):
        if not joined[signal_key].eq(joined[path_key]).all():
            raise ValueError(f"Frozen 12H signal clock differs from path ledger: {signal_key}")
    for left, right in (("symbol_signal", "symbol_path"), ("week_signal", "week_path"),
                        ("side_signal", "side_path")):
        if not joined[left].astype(str).eq(joined[right].astype(str)).all():
            raise ValueError(f"Frozen signal/path identity mismatch: {left}")
    joined["symbol"] = joined.symbol_signal
    joined["week"] = joined.week_signal
    joined["side"] = joined.side_signal
    if len(joined) != 12_707 or joined.symbol.nunique() != 286:
        raise ValueError(f"Unexpected frozen parent event inventory: {len(joined)} rows / {joined.symbol.nunique()} symbols")
    for symbol, rows in actual.groupby("symbol", sort=False):
        if len(set(zip(rows.source_path_12h, rows.source_path_1h))) != 1:
            raise ValueError(f"Multiple archived input paths for symbol {symbol}")
    return signals, catalog, joined


def _verify_12h_parent(joined: pd.DataFrame, parent_event_paths: pd.DataFrame,
                       bars12_by_symbol: dict[str, pd.DataFrame]) -> dict:
    path_by_symbol = parent_event_paths.drop_duplicates("symbol").set_index("symbol")
    compared = Counter()
    max_error = Counter()
    for symbol, group in joined.groupby("symbol", sort=False):
        p12 = ROOT / str(path_by_symbol.loc[symbol, "source_path_12h"])
        bars = bars12_by_symbol.get(symbol)
        if bars is None:
            bars = engine._load_bars(p12, "12h")
            bars12_by_symbol[symbol] = bars
        points = _ema_points(bars, STEP_12H)
        for row in group.to_dict("records"):
            stamp = int(row["key_open_ms_signal"])
            if stamp not in bars.index or stamp not in points.index:
                raise ValueError(f"Frozen 12H signal bar absent from selected OKX source: {symbol} {row['week']}")
            candle, point = bars.loc[stamp], points.loc[stamp]
            if not bool(point.ready):
                raise ValueError(f"Frozen 12H parent signal is not EMA-ready: {symbol} {row['week']}")
            comparisons = (("signal_open", candle.open), ("signal_high", candle.high),
                           ("signal_low", candle.low), ("confirmation_close", candle.close),
                           ("ema20", point.ema20), ("ema50", point.ema50))
            for name, observed in comparisons:
                expected = float(row[name])
                observed = float(observed)
                error = abs(expected - observed)
                tol = max(1e-10, 1e-9 * max(abs(expected), abs(observed)))
                max_error[name] = max(max_error[name], error)
                if error > tol:
                    raise ValueError(f"12H frozen signal {name} mismatch {symbol} {row['week']}: {error}")
                compared[name] += 1
            lower, upper = _parent_levels(row)
            side = int(row["side"])
            valid = ((side == 1 and point.ema20 > point.ema50
                      and float(candle.low) < lower < float(candle.close) < upper)
                     or (side == -1 and point.ema20 < point.ema50
                         and lower < float(candle.close) < upper < float(candle.high)))
            if not valid:
                raise ValueError(f"Frozen 12H source signal fails strict V1.7 wick/close/EMA rule: {symbol} {row['week']}")
    return {"event_count": len(joined), "ohlc_ema_fields_compared": dict(compared),
            "maximum_absolute_error": dict(max_error),
            "strict_side_wick_close_rule": "all frozen 12H events passed"}


def _scan_first_4h(symbol: str, decision_ms: int, bars4: pd.DataFrame,
                   points4: pd.DataFrame, anchor: dict) -> dict:
    """Scan chronologically; a later gap cannot erase an already-known week lock."""
    first = ((int(anchor["end_ms"]) + STEP_4H - 1) // STEP_4H) * STEP_4H
    last_closed = min(int(anchor["expires_ms"]), int(decision_ms))
    last = ((last_closed - STEP_4H) // STEP_4H) * STEP_4H
    if last < first:
        return {"screen_reason": "no_completed_4h_candidate_by_12h_confirmation",
                "four_h_key": None}
    bar_times = bars4.index
    for stamp in range(first, last + 1, STEP_4H):
        if stamp not in bar_times:
            return {"screen_reason": "missing_4h_bar_before_first_key",
                    "four_h_key": None, "missing_4h_before_entry": 1}
        point = points4.loc[stamp].to_dict()
        point["time"] = int(stamp)
        bar = bars4.loc[stamp]
        item = _signal_4h(symbol, stamp, bar.to_dict(), point, anchor)
        if item is not None:
            # Keep the production source's NY-week/shared-direction first-key rule.
            selected = first_eligible([item])
            if selected:
                return {"screen_reason": "first_4h_key_found", "four_h_key": selected[0],
                        "missing_4h_before_entry": 0}
    return {"screen_reason": "no_first_eligible_4h_key_by_12h_confirmation",
            "four_h_key": None, "missing_4h_before_entry": 0}


def _weekly_key_audit(symbol: str, anchor: dict, bars4: pd.DataFrame,
                      points4: pd.DataFrame, last_h1_close_ms: int) -> dict:
    decision = min(int(anchor["expires_ms"]), int(last_h1_close_ms))
    result = _scan_first_4h(symbol, decision, bars4, points4, anchor)
    first = result.get("four_h_key")
    if first is not None:
        status = "first_key"
    elif result["screen_reason"] == "missing_4h_bar_before_first_key":
        status = "unknown_gap_before_first_key"
    elif decision < int(anchor["expires_ms"]):
        status = "right_censored_at_input_tail"
    elif result["screen_reason"] == "no_completed_4h_candidate_by_12h_confirmation":
        status = "no_closed_4h_candidate"
    else:
        status = "complete_no_key"
    first = first or {}
    return {"symbol": symbol, "week": anchor["week"],
            "first_key_id": first.get("id"), "first_key_open_ms": first.get("signal_open_ms"),
            "first_key_confirm_ms": first.get("confirmed_ms"), "first_key_side": first.get("side"),
            "first_key_entry": first.get("entry"), "first_key_stop": first.get("stop"),
            "first_key_target": first.get("target"), "week_status": status,
            "scan_through_ms": decision,
            "candidate_4h_slots_scanned": len(range(
                ((int(anchor["end_ms"]) + STEP_4H - 1) // STEP_4H) * STEP_4H,
                ((min(decision, int(anchor["expires_ms"])) - STEP_4H) // STEP_4H) * STEP_4H + 1,
                STEP_4H))}


def _build_anchor_and_4h(symbol: str, parent_weeks: pd.DataFrame, bars1: pd.DataFrame,
                         config: dict) -> tuple[dict[str, dict], pd.DataFrame, dict]:
    """Build complete H1-derived 4H bars and first V1.7 keys per parent week."""
    bars4, aggregation_audit = _aggregate_complete_4h(bars1)
    points4 = _ema_points(bars4, STEP_4H)
    anchors = {}
    week_rows = []
    source_status_counts = Counter()
    max_ref_error = 0.0

    for row in parent_weeks.to_dict("records"):
        week = str(row["week"])
        anchor = _anchor_from_parent_week(week, row, bars1)
        source_status_counts[anchor["source"]] += 1
        max_ref_error = max(max_ref_error,
                            abs(anchor["lower"] - _parent_levels(row)[0]),
                            abs(anchor["upper"] - _parent_levels(row)[1]))
        anchors[week] = anchor

        last_h1_close = int(bars1.index[-1]) + HOUR_MS if len(bars1) else 0
        week_rows.append(_weekly_key_audit(symbol, anchor, bars4, points4,
                                           last_h1_close))

    audit = {**aggregation_audit,
             "monday_anchor_source_counts": dict(source_status_counts),
             "maximum_parent_level_error": max_ref_error,
             "target_symbol_week_count": len(parent_weeks),
             "week_status_counts": dict(Counter(r["week_status"] for r in week_rows)),
             "unknown_gap_before_first_key_weeks": sum(
                 r["week_status"] == "unknown_gap_before_first_key" for r in week_rows)}
    return anchors, pd.DataFrame(week_rows), {"bars4": bars4, "points4": points4, "audit": audit}


def _first_4h_by_event(event: dict, bars4: pd.DataFrame,
                       points4: pd.DataFrame, anchor: dict) -> dict:
    """Apply the source first-key lock at this event's own 12H confirmation.

    Require every expected 4H close from Tuesday 00:00 NY through the event
    clock to exist. A gap makes the historical first-key status unknown, rather
    than letting a later observed bar stand in for the missing key.
    """
    entry_ms = event.get("entry_ms")
    if entry_ms is None or pd.isna(entry_ms) or event.get("entry_status") != "executed":
        return {"screen_reason": "unmatched_or_unexecutable_control", "four_h_signal_ms": None,
                "four_h_confirm_ms": None, "four_h_side": None}
    entry_ms = int(entry_ms)
    result = _scan_first_4h(str(event["symbol"]), entry_ms, bars4, points4, anchor)
    if result.get("four_h_key") is None:
        return {**result, "four_h_signal_ms": None, "four_h_confirm_ms": None,
                "four_h_side": None}
    item = result["four_h_key"]
    side = int(event["side"])
    reason = "passes_4h_agreement" if int(item["side"]) == side else "first_4h_direction_differs"
    return {"screen_reason": reason, "four_h_signal_ms": int(item["signal_open_ms"]),
            "four_h_confirm_ms": int(item["confirmed_ms"]), "four_h_side": int(item["side"]),
            "four_h_signal_open": item["signal_open"], "four_h_signal_high": item["signal_high"],
            "four_h_signal_low": item["signal_low"], "four_h_confirmation_close": item["confirmation_close"],
            "four_h_ema20": item["ema20"], "four_h_ema50": item["ema50"],
            "four_h_entry_level": item["entry"], "four_h_stop": item["stop"],
            "four_h_target": item["target"], "four_h_planned_rr": item["planned_rr"],
            "four_h_range_source": item["range_source"], "missing_4h_before_entry": 0}


def _score_90d(events: pd.DataFrame, score: pd.Series) -> pd.DataFrame:
    result = events.copy()
    stamps = pd.to_numeric(result.entry_ms, errors="coerce")
    index = pd.MultiIndex.from_arrays([stamps.fillna(-1).astype("int64"), result.symbol])
    result["coverage_rank_90d"] = score.reindex(index).to_numpy()
    result["eligible_90d"] = result.coverage_rank_90d.notna()
    return result


def _priority(events: pd.DataFrame, signal_atr: pd.Series) -> pd.DataFrame:
    result = events.copy()
    source_rows = pd.to_numeric(result.source_row_number, errors="coerce").astype("Int64")
    result["entry_order"] = source_rows.map(signal_atr).astype(float).fillna(np.inf)
    return result


def _custom_periods(trades: pd.DataFrame, equity: pd.DataFrame) -> dict:
    return {
        "2026_to_2026-09-23": wallet.summarize(
            trades, equity, "2026-01-01T00:00:00Z", COMMON_BINANCE_END),
        "2026_after_2026-09-23": wallet.summarize(
            trades, equity, COMMON_BINANCE_END, None),
        "all_to_2026-09-23": wallet.summarize(trades, equity, None, COMMON_BINANCE_END),
    }


def _account_run(pool: pd.DataFrame, label: str, slip: float = 0.0) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    executable = pool[pool.entry_status.eq("executed")].copy()
    if executable.empty:
        return {}, pd.DataFrame(), pd.DataFrame()
    if not executable.groupby("event_id").tp_r.apply(
            lambda x: set(x.astype(float)) == {0.0, 2.0}).all():
        raise ValueError(f"{label} lacks frozen paired tp0/2 paths")
    metrics, trades, equity = capacity.run(executable, SLOTS, FRACTION_2R, slip)
    metrics.update(_custom_periods(trades, equity))
    return metrics, trades, equity


def _period_rows(arm: str, cohort: str, metrics: dict) -> list[dict]:
    return [dict(arm=arm, cohort=cohort, period=period, **row)
            for period, row in metrics.items()]


def _binance_reference() -> dict:
    table = pd.read_csv(BINANCE_REPAIR / "account_metrics.csv")
    selected = table[(table.policy == "agree4h_0.5") & table.period.isin(["all", "dev"])]
    if set(selected.period) != {"all", "dev"} or selected.duplicated("period").any():
        raise ValueError("Corrected Binance 4H agreement reference rows are missing or ambiguous")
    fields = ("final_usdt", "win_pct", "payoff", "payoff_usdt", "dd_mark_pct",
              "dd_bound_pct", "trades")
    return {"source": str(BINANCE_REPAIR.relative_to(ROOT)),
            "policy": "agree4h_0.5", "partial_2r_fraction": 0.5,
            "all": selected[selected.period.eq("all")].iloc[0][list(fields)].to_dict(),
            "dev": selected[selected.period.eq("dev")].iloc[0][list(fields)].to_dict(),
            "historical_local_sources": [str(BINANCE_SPLIT.relative_to(ROOT)),
                                         str(BINANCE_LOWER.relative_to(ROOT))],
            "comparison_note": "Corrected Binance full account is the cross-market reference. Earlier local Binance CSVs are retained as historical methods/results; OKX baseline and filter are independently funded on the same 90d OKX universe. Compare the common through-2026-09-23 interval and report OKX post-cutoff separately."}


def generate(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (float(config["account"]["partial_2r_fraction"]) != FRACTION_2R
            or int(config["account"]["slots"]) != SLOTS
            or config["account"]["entry_priority"] != "low_prior_atr"):
        raise ValueError("This transfer is frozen to 50%, 9 slots, low-prior-ATR priority")
    if (float(config["fees"]["taker_per_fill"]) != TAKER
            or float(config["fees"]["resting_maker_per_fill"]) != MAKER):
        raise ValueError("OKX fees differ from the owner-confirmed frozen V1.7 costs")
    output_paths = {k: ROOT / v for k, v in config["outputs"].items()}
    dynamic_outputs = [output_paths["funded"].with_name(f"funded_{name}.csv.gz")
                      for name in ("baseline_90d_actual", "baseline_90d_control",
                                   "baseline_90d_paired_actual", "baseline_90d_paired_control",
                                   "agreement_4h_actual", "agreement_4h_paired_actual",
                                   "agreement_4h_paired_control", "baseline_90d_actual_slip5bp",
                                   "agreement_4h_actual_slip5bp")]
    dynamic_outputs += [output_paths["equity"].with_name(f"hourly_{name}.csv.gz")
                        for name in ("baseline_90d_actual", "baseline_90d_control",
                                     "baseline_90d_paired_actual", "baseline_90d_paired_control",
                                     "agreement_4h_actual", "agreement_4h_paired_actual",
                                     "agreement_4h_paired_control", "baseline_90d_actual_slip5bp",
                                     "agreement_4h_actual_slip5bp")]
    if any(p.exists() for p in [*output_paths.values(), *dynamic_outputs]):
        raise FileExistsError("Refusing to overwrite existing OKX agreement outputs")

    signals, catalog, joined = _read_parent(config)
    actual_catalog = catalog[~catalog.is_control].copy()
    signals["source_row_number"] = signals.source_row_number.astype("int64")
    events = wallet.read_paths()
    events = events[events.tp_r.isin([0.0, 2.0])].copy()
    if not events.groupby("event_id").tp_r.apply(
            lambda x: set(x.astype(float)) == {0.0, 2.0}).all():
        raise ValueError("Frozen OKX path runner must retain matching no-TP and 2R exits")
    signal_meta = signals[["source_row_number", "symbol", "week", "side", "prior_atr_pct",
                           "reference_entry", "reference_stop", "opposite_target",
                           "key_open_ms", "confirmation_ms"]].rename(
                               columns={"symbol": "parent_symbol", "week": "parent_week",
                                        "side": "parent_side", "key_open_ms": "parent_key_open_ms",
                                        "confirmation_ms": "parent_confirmation_ms"})
    events = events.merge(signal_meta, on="source_row_number", validate="many_to_one")
    if (not events.week.eq(events.parent_week).all()
            or not events.symbol.eq(events.parent_symbol).all()
            or not events.side.eq(events.parent_side).all()):
        raise ValueError("Parent week mismatch between path ledger and frozen signal CSV")
    event_catalog = catalog.copy()
    event_catalog["source_row_number"] = pd.to_numeric(
        event_catalog.source_row_number, errors="coerce").astype("int64")
    event_catalog = event_catalog.merge(signal_meta, on="source_row_number",
                                        validate="many_to_one")
    if (not event_catalog.week.eq(event_catalog.parent_week).all()
            or not event_catalog.symbol.eq(event_catalog.parent_symbol).all()
            or not event_catalog.side.eq(event_catalog.parent_side).all()):
        raise ValueError("Actual/control identity differs from its frozen parent")
    score = leadership.leadership()
    event_catalog = _score_90d(event_catalog, score)
    event_catalog = _priority(event_catalog, signals.set_index("source_row_number").prior_atr_pct)
    events = _priority(events, signals.set_index("source_row_number").prior_atr_pct)

    by_symbol_paths = actual_catalog.drop_duplicates("symbol").set_index("symbol")
    parents_by_symbol = {str(sym): frame.sort_values("source_row_number")
                         for sym, frame in signals.groupby("symbol", sort=False)}
    screened_rows, weekly_first = [], []
    symbol_audits = {}
    validation_counts = Counter()
    validation_max_error = Counter()
    eligibility_counts = Counter()
    for symbol in sorted(parents_by_symbol):
        path_row = by_symbol_paths.loc[symbol]
        p12 = ROOT / str(path_row.source_path_12h)
        p1 = ROOT / str(path_row.source_path_1h)
        bars12 = engine._load_bars(p12, "12h")
        bars1 = engine._load_bars(p1, "1h")
        symbol_joined = joined[joined.symbol_signal.eq(symbol)]
        checked = _verify_12h_parent(symbol_joined, actual_catalog[actual_catalog.symbol.eq(symbol)],
                                     {symbol: bars12})
        validation_counts.update(checked["ohlc_ema_fields_compared"])
        for name, error in checked["maximum_absolute_error"].items():
            validation_max_error[name] = max(validation_max_error[name], error)
        anchors, weekly, prepared = _build_anchor_and_4h(
            symbol, parents_by_symbol[symbol], bars1, config)
        symbol_audits[symbol] = prepared["audit"]
        weekly_first.extend(weekly.to_dict("records"))
        group = event_catalog[event_catalog.symbol.eq(symbol)]
        for event in group.to_dict("records"):
            base = {k: event.get(k) for k in (
                "event_id", "parent_id", "source_row_number", "symbol", "week", "side",
                "is_control", "entry_status", "entry_ms", "key_open_ms", "confirmation_ms",
                "entry_price", "stop_price", "risk_distance", "control_confirmation_ms",
                "control_12h_open_ms", "prior_atr_pct", "coverage_rank_90d")}
            coverage = bool(event["eligible_90d"])
            if event.get("entry_status") != "executed" or pd.isna(event.get("entry_ms")):
                result = {"screen_reason": "unmatched_or_unexecutable_control",
                          "four_h_signal_ms": None, "four_h_confirm_ms": None,
                          "four_h_side": None}
            elif not coverage:
                result = {"screen_reason": "outside_90d_coverage", "four_h_signal_ms": None,
                          "four_h_confirm_ms": None, "four_h_side": None}
            elif str(event["week"]) not in anchors:
                result = {"screen_reason": "missing_parent_week_anchor", "four_h_signal_ms": None,
                          "four_h_confirm_ms": None, "four_h_side": None}
            else:
                result = _first_4h_by_event(event, prepared["bars4"], prepared["points4"],
                                            anchors[str(event["week"])])
            base.update(result)
            base["eligible_90d"] = coverage
            base["eligible_4h_agreement"] = (
                coverage and result.get("screen_reason") == "passes_4h_agreement")
            screened_rows.append(base)
            eligibility_counts[str(result["screen_reason"])] += 1
        del bars12, bars1, prepared, anchors
        gc.collect()

    parent_validation = {"event_count": int(len(joined)),
                         "ohlc_ema_fields_compared": dict(validation_counts),
                         "maximum_absolute_error": dict(validation_max_error),
                         "strict_side_wick_close_rule": "all frozen 12H events passed"}

    # Account cohorts require executed parent paths. The event screen retains
    # explicit unmatched controls above; they never enter a funded account.
    screen = pd.DataFrame(screened_rows)
    if screen.duplicated("event_id").any():
        raise AssertionError("The event screen must emit one row per parent/control event")
    executed = events[events.entry_status.eq("executed")].copy()
    flags = screen.set_index("event_id")[["eligible_90d", "eligible_4h_agreement"]]
    if not executed.event_id.isin(flags.index).all():
        raise ValueError("Executed parent paths missing from event screen")
    executed["eligible_90d"] = executed.event_id.map(flags.eligible_90d).fillna(False).astype(bool)
    executed["eligible_4h_agreement"] = executed.event_id.map(
        flags.eligible_4h_agreement).fillna(False).astype(bool)
    real_90 = executed[(~executed.is_control) & executed.eligible_90d]
    control_90_all = executed[executed.is_control & executed.eligible_90d]
    control_90 = control_90_all[control_90_all.parent_id.isin(real_90.parent_id)]
    pair90 = set(real_90.parent_id.astype(str)) & set(control_90.parent_id.astype(str))
    actual_90_paired = real_90[real_90.parent_id.astype(str).isin(pair90)]
    control_90_paired = control_90[control_90.parent_id.astype(str).isin(pair90)]

    real_4h = real_90[real_90.eligible_4h_agreement]
    control_4h_all = control_90_all[control_90_all.eligible_4h_agreement]
    control_4h = control_4h_all[control_4h_all.parent_id.isin(real_4h.parent_id)]
    pair4 = set(real_4h.parent_id.astype(str)) & set(control_4h.parent_id.astype(str))
    actual_4h_paired = real_4h[real_4h.parent_id.astype(str).isin(pair4)]
    control_4h_paired = control_4h[control_4h.parent_id.astype(str).isin(pair4)]

    cohorts = {
        "baseline_90d_actual": (ARM_BASE, "actual", real_90, 0.0),
        "baseline_90d_control": (ARM_BASE, "control", control_90, 0.0),
        "baseline_90d_paired_actual": (ARM_BASE, "paired_actual", actual_90_paired, 0.0),
        "baseline_90d_paired_control": (ARM_BASE, "paired_control", control_90_paired, 0.0),
        "agreement_4h_actual": (ARM_4H, "actual", real_4h, 0.0),
        "agreement_4h_paired_actual": (ARM_4H, "paired_actual", actual_4h_paired, 0.0),
        "agreement_4h_paired_control": (ARM_4H, "paired_control", control_4h_paired, 0.0),
        "baseline_90d_actual_slip5bp": (ARM_BASE, "slippage_5bp_actual", real_90, 0.0005),
        "agreement_4h_actual_slip5bp": (ARM_4H, "slippage_5bp_actual", real_4h, 0.0005),
    }
    account_metrics = []
    account_results = {}
    for name, (arm, cohort, pool, slip) in cohorts.items():
        if pool.empty:
            metrics, funded, equity = {}, pd.DataFrame(), pd.DataFrame()
        else:
            metrics, funded, equity = _account_run(pool, name, slip)
        account_results[name] = {"metrics": metrics, "funded": funded, "equity": equity,
                                 "opportunities": int(pool.event_id.nunique()),
                                 "slippage_per_market_fill": slip}
        account_metrics.extend(_period_rows(arm, cohort, metrics))
        if not funded.empty:
            funded.to_csv(output_paths["funded"].with_name(f"funded_{name}.csv.gz"),
                          index=False, compression="gzip")
            equity.to_csv(output_paths["equity"].with_name(f"hourly_{name}.csv.gz"),
                          index_label="hour_end_utc")
        gc.collect()

    metric_frame = pd.DataFrame(account_metrics)
    screen.to_csv(output_paths["event_screen"], index=False, compression="gzip", float_format="%.12g")
    pd.DataFrame(weekly_first).to_csv(output_paths["weekly_keys"], index=False,
                                      compression="gzip", float_format="%.12g")
    metric_frame.to_csv(output_paths["account_metrics"], index=False)

    baseline_actual = account_results["baseline_90d_actual"]["metrics"]
    agreement_actual = account_results["agreement_4h_actual"]["metrics"]
    screen_counts = screen.screen_reason.value_counts(dropna=False).to_dict()
    anchor_source_counts = Counter()
    for info in symbol_audits.values():
        anchor_source_counts.update(info["monday_anchor_source_counts"])
    binance_reference = _binance_reference()
    audit = {
        "experiment_id": config["id"],
        "generator": str(Path(__file__).relative_to(ROOT)),
        "vectorbt_version": vbt.__version__,
        "parent_inputs": {
            "signals_csv": str((ROOT / config["inputs"]["signals_csv"]).relative_to(ROOT)),
            "paths_csv_gz": str((ROOT / config["inputs"]["parent_paths_csv_gz"]).relative_to(ROOT)),
            "signal_rows": len(signals), "all_12h_event_weeks": int(signals.groupby(["symbol", "week"]).ngroups),
            "12h_parent_validation": parent_validation,
        },
        "signal_semantics": {
            "binance_reference": "Frozen Binance signal-center 4H keys in exp-cloud-close-lower-agreement-20261109-v1; same ticker/NY week/side; first tf=4H close_ns <= each 12H/control clock. No Binance score, OHLC fill, or parameter is transferred.",
            "okx_12h_parent": "Frozen exp-cloud-allmarket-screen-20261108-v1/signals.csv event keys, with every key/close, candle OHLC and causal EMA20/50 revalidated against its exact frozen OKX bars12h input.",
            "okx_4h_adaptation": "Pure adaptation of yoyo.monitor.cloud_signals.signal: close-only EMA20/50, alpha 2/(n+1), first-close seed, 50 consecutive completed bars, reset after missing 4H; strict same-side wick penetration and close back inside the original Monday 1.618 range.",
            "monday": "NY Monday high/low and lower/upper 1.618 use yoyo.monitor.monday_fib.week_window and baseline semantics. Complete Monday H1 input is required and checked against the original 12H parent's reference_entry/opposite_target. A missing H1 Monday falls back only by inverting that exact frozen parent level pair and is separately counted.",
            "first_key_lock": "For each OKX symbol/NY Monday week, use only the earliest eligible 4H key, shared lock across both directions. Candidate bars open after completed NY Monday, are UTC 4H aligned, and close by week expiry. No cross-week candle.",
            "control_clock": "A matched control independently qualifies only if that same first 4H key has its side and closes at or before the control's own frozen 12H entry_ms. The pair sample requires both parent signal and control to pass their own clock.",
        },
        "coverage": {
            "90d_gate": "Reuse cloud_close_leadership.leadership: 181 consecutive completed 12H closes before each event, with >=10 contemporaneously rankable contracts; use top_fraction=1.0 (coverage only).",
            "signal_and_control_scope": "Main controls belong to a 90d-eligible actual parent and separately pass their own 90d rank; 4H controls also separately pass their own 4H clock.",
        },
        "account": {"slots": SLOTS, "entry_priority": "low_prior_atr inherited from parent signal prior_atr_pct; controls inherit parent priority",
                    "partial_2r_fraction": FRACTION_2R,
                    "baseline_and_filter": "same 90d OKX parent cohort, same entry/stop/exit paths, 50% at original 2R and 50% original cloud runner; only the 4H agreement filter changes",
                    "pricing": "Frozen OKX V1.7 tp_r=0/2 rows, cloud_close_slots.run, cloud_close_scaleout and VectorBT funded account; no custom fill simulation",
                    "fees_per_fill": {"taker": TAKER, "resting_maker": MAKER},
                    "market_slippage_stress": 0.0005, "funding_and_depth": "not modeled"},
        "counts": {
            "90d_actual_opportunities": int(real_90.event_id.nunique()),
            "90d_paired_control_opportunities": int(control_90.event_id.nunique()),
            "90d_strict_parent_pairs": len(pair90),
            "4h_actual_opportunities": int(real_4h.event_id.nunique()),
            "4h_paired_control_opportunities": int(control_4h.event_id.nunique()),
            "4h_strict_parent_pairs": len(pair4),
            "4h_actual_reasons": screen[(~screen.is_control) & screen.eligible_90d].screen_reason.value_counts().to_dict(),
            "4h_control_reasons": screen[screen.is_control & screen.eligible_90d].screen_reason.value_counts().to_dict(),
            "event_screen_reasons_all": screen_counts,
            "weekly_first_key_status": dict(Counter(r["week_status"] for r in weekly_first)),
            "anchor_sources": dict(anchor_source_counts),
            "account_opportunities_and_funded_positions": {
                k: {"opportunities": v["opportunities"],
                    "funded_positions": int(v["metrics"].get("all", {}).get("trades", 0))}
                for k, v in account_results.items()},
        },
        "binance_reference": {
            **binance_reference,
        },
        "account_metrics": {"baseline_90d_actual": baseline_actual,
                            "agreement_4h_actual": agreement_actual},
        "account_runs": {k: {"opportunities": v["opportunities"],
                             "metrics": v["metrics"],
                             "slippage_per_market_fill": v["slippage_per_market_fill"]}
                         for k, v in account_results.items()},
        "outputs": {k: str(v.relative_to(ROOT)) for k, v in output_paths.items()},
        "event_screen_schema": list(screen.columns),
    }
    _write_json(output_paths["audit"], audit)
    _write_json(output_paths["summary"], {
        "experiment_id": config["id"], "counts": audit["counts"],
        "account_metrics": audit["account_runs"],
        "binance_reference": audit["binance_reference"],
    })
    return audit


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    audit = generate(config_path)
    print(json.dumps({"experiment_id": audit["experiment_id"],
                      "counts": audit["counts"],
                      "baseline_actual": audit["account_metrics"]["baseline_90d_actual"],
                      "agreement_4h_actual": audit["account_metrics"]["agreement_4h_actual"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
