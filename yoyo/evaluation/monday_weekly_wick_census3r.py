"""Census strict first wicks beyond each NY Monday 1.618 rail, with 3R labels.

The signal universe and frozen H1 tape come from the audited Binance month-week
priority experiment. This module changes only the calendar key: one complete NY
Monday H1 range is frozen per NY week, becomes usable Tuesday 00:00 NY, and
expires the next Monday 00:00 NY. The first strict outside H1 bar is the sole
breach candidate for that week and side. A 1h, then 4h, then 12h UTC-aligned
bar must close back inside while its wick remains strictly outside the rail.

Only entered events receive a native VectorBT 3R TP/SL label. Position paths
cross later week anchors and end at the first cleaned-tape gap or tape end.
Per-symbol outputs under DATA_CACHE make long universe runs resumable.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-binance-monday-wick-score3r-20261116-v1"
DATA_CACHE = ROOT / "data/research/binance-monday-wick-score3r-20261116-v1"
SYMBOL_CACHE = DATA_CACHE / "symbols"

EXPERIMENT_ID = "exp-binance-monday-wick-score3r-20261116-v1"
BUILD_VERSION = "monday-weekly-wick-census3r-v1"
CACHE_SCHEMA_VERSION = 1
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MINUTE_MS = 60_000
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
TIMEFRAMES_HOURS = (1, 4, 12)
RANGE_MULTIPLE = 0.618
STOP_BUFFER = 0.01
ENTRY_TAKER_FEE = 0.0005
EXIT_TAKER_FEE = 0.0005
EXIT_MAKER_FEE = 0.0002
TARGET_R = 3
CONFIRMED_STATUSES = (
    "entered",
    "confirmed_no_next_open",
    "confirmed_open_beyond_stop",
)

EVENT_COLUMNS = (
    "event_id", "symbol", "week", "side", "side_name", "tf", "bar_ms",
    "close_ms", "first_breach_ms", "anchor_start_ms", "anchor_ready_ms",
    "expires_ms", "week_high", "week_low", "rail", "prior_extreme",
    "state_1h", "state_4h", "state_12h", "bar_open", "bar_high", "bar_low",
    "bar_close", "tip", "stop", "entry_i", "entry_ms", "entry",
    "risk_frac", "status", "active_window_complete", "last_observed_ms",
)
COVERAGE_COLUMNS = (
    "symbol", "week", "anchor_start_ms", "anchor_ready_ms", "expires_ms",
    "anchor_status", "week_high", "week_low", "active_start_ms",
    "active_end_ms", "last_observed_ms", "active_window_complete",
    "first_breach_lower_ms", "first_breach_upper_ms",
)
LABEL_COLUMNS = (
    "label_id", "event_id", "symbol", "week", "side", "tf", "bar_ms",
    "close_ms", "first_breach_ms", "anchor_start_ms", "anchor_ready_ms",
    "expires_ms", "entry_ms", "entry", "stop", "risk_abs", "risk_frac",
    "target", "target_price", "outcome", "resolution", "ambiguous",
    "minute_source", "exit_ms", "exit_price", "gross_r", "net_r",
    "net_return", "entry_fee_rate", "exit_fee_rate", "entry_fee",
    "exit_fee", "fees_paid", "mark_ms", "mark_price", "mark_gross_r",
    "mark_net_r", "censor_reason", "quantity", "engine",
)

_MINUTE_DIAGNOSTICS: dict[tuple[str, int], str] = {}
_MINUTE_DIAGNOSTICS_LOCK = threading.Lock()


def ny_week_window(week: str | date | int) -> dict[str, Any]:
    """Return the canonical NY Monday window through ``monday_fib.week_window``.

    ``week`` is a Monday date, an aware/unaware epoch-millisecond timestamp, or
    an ISO date. The returned ``ready_ms`` is Tuesday 00:00 NY and
    ``expires_ms`` is the following Monday 00:00 NY, so expiry spans 167, 168,
    or 169 UTC hours as DST requires.
    """
    from yoyo.monitor.monday_fib import week_window

    if isinstance(week, (int, np.integer)):
        stamp_ms = int(week)
        local_date = datetime.fromtimestamp(stamp_ms / 1000, UTC).astimezone(NY).date()
    elif isinstance(week, datetime):
        local = week.replace(tzinfo=NY) if week.tzinfo is None else week.astimezone(NY)
        local_date = local.date()
        stamp_ms = int(local.timestamp() * 1000)
    elif isinstance(week, date):
        local_date = week
        stamp_ms = int(datetime.combine(local_date, time(12), NY).timestamp() * 1000)
    else:
        local_date = date.fromisoformat(str(week))
        stamp_ms = int(datetime.combine(local_date, time(12), NY).timestamp() * 1000)
    monday = local_date - timedelta(days=local_date.weekday())
    if local_date.weekday() == 0:
        monday = local_date
    anchor = week_window(stamp_ms)
    if anchor["week"] != monday.isoformat():
        raise AssertionError("monday_fib.week_window returned a different NY week")
    start_ms, ready_ms, expires_ms = int(anchor["start"]), int(anchor["end"]), int(anchor["expires"])
    if ready_ms - start_ms != DAY_MS:
        raise AssertionError("NY Monday anchor must contain exactly 24 elapsed H1 hours")
    return {
        "week": str(anchor["week"]),
        "start_ms": start_ms,
        "ready_ms": ready_ms,
        "expires_ms": expires_ms,
        "expected_1h_bars": 24,
    }


def _validate_hourly(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(raw, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5:
        raise ValueError("Frozen H1 tape must be N x 5 [open_ms, O, H, L, C]")
    if not len(values):
        return values, np.empty(0, dtype=np.int64)
    ts = values[:, 0].astype(np.int64)
    if (
        not np.array_equal(values[:, 0], ts.astype(np.float64))
        or np.any(ts % HOUR_MS)
        or np.any(np.diff(ts) <= 0)
        or not np.isfinite(values).all()
        or np.any(values[:, 1:5] <= 0)
        or np.any(values[:, 2] < np.maximum(values[:, 1], values[:, 4]))
        or np.any(values[:, 3] > np.minimum(values[:, 1], values[:, 4]))
        or np.any(values[:, 3] > values[:, 2])
    ):
        raise ValueError("Frozen H1 tape contains invalid timestamps or OHLC")
    return values, ts


def _week_dates(ts: np.ndarray, cutoff_ms: int) -> list[date]:
    if not len(ts):
        return []
    first_local = datetime.fromtimestamp(int(ts[0]) / 1000, UTC).astimezone(NY).date()
    last_local = datetime.fromtimestamp(int(min(cutoff_ms, ts[-1] + HOUR_MS) - 1) / 1000, UTC).astimezone(NY).date()
    start = first_local - timedelta(days=first_local.weekday())
    stop = last_local - timedelta(days=last_local.weekday())
    result = []
    current = start
    while current <= stop:
        result.append(current)
        current += timedelta(days=7)
    return result


def _candle(
    raw: np.ndarray,
    ts: np.ndarray,
    breach_ms: int,
    hours: int,
    expires_ms: int,
    side: int,
    rail: float,
    tolerance: float,
) -> tuple[str, dict[str, Any] | None]:
    """Classify the UTC-aligned candle containing a first strict H1 breach."""
    span_ms = int(hours) * HOUR_MS
    start_ms = int(breach_ms) // span_ms * span_ms
    close_ms = start_ms + span_ms
    # A candle closing exactly at NY Monday 00:00 is allowed by the established
    # month-week contract; only a close after expiry is out of range.
    if close_ms > int(expires_ms):
        return "past_expiry", None
    start, stop = np.searchsorted(ts, [start_ms, close_ms])
    if stop - start != hours or start >= len(ts) or ts[start] != start_ms:
        return "incomplete", None
    block_ts = ts[start:stop]
    if len(block_ts) > 1 and np.any(np.diff(block_ts) != HOUR_MS):
        return "incomplete", None
    block = raw[start:stop]
    bar_open = float(block[0, 1])
    bar_high = float(np.max(block[:, 2]))
    bar_low = float(np.min(block[:, 3]))
    bar_close = float(block[-1, 4])
    tip = bar_low if side == 1 else bar_high
    if not (
        side * (bar_open - rail) > tolerance
        and side * (bar_close - rail) > tolerance
        and side * (tip - rail) < -tolerance
    ):
        return "failed", None
    return "wick", {
        "tf": int(hours), "bar_ms": int(start_ms), "close_ms": int(close_ms),
        "bar_open": bar_open, "bar_high": bar_high, "bar_low": bar_low,
        "bar_close": bar_close, "tip": float(tip), "entry_i": int(stop),
    }


def scan_week(
    symbol: str,
    raw: np.ndarray,
    week: str | date,
    *,
    cutoff_ms: int,
    stop_buffer: float = STOP_BUFFER,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return both first-breach side rows and weekly anchor/path coverage.

    The scan interval is half-open ``[anchor_ready_ms, expires_ms)`` and ends
    earlier at a cleaned-tape gap or the frozen cutoff. The first strict
    excursion consumes that side even if none of its 1h/4h/12h bars qualifies.
    """
    if not (0 < float(stop_buffer) < 1):
        raise ValueError("stop_buffer must be between zero and one")
    values, ts = _validate_hourly(raw)
    window = ny_week_window(week)
    week_key = window["week"]
    start_ms, ready_ms, expires_ms = (
        int(window[k]) for k in ("start_ms", "ready_ms", "expires_ms")
    )
    base_coverage = {
        "symbol": str(symbol), "week": week_key,
        "anchor_start_ms": start_ms, "anchor_ready_ms": ready_ms,
        "expires_ms": expires_ms, "anchor_status": "pending",
        "week_high": np.nan, "week_low": np.nan,
        "active_start_ms": ready_ms, "active_end_ms": min(expires_ms, int(cutoff_ms)),
        "last_observed_ms": np.nan, "active_window_complete": False,
        "first_breach_lower_ms": np.nan, "first_breach_upper_ms": np.nan,
    }
    if not len(ts) or start_ms >= int(cutoff_ms):
        base_coverage["anchor_status"] = "outside_tape"
        return [], base_coverage

    anchor_a, anchor_b = np.searchsorted(ts, [start_ms, ready_ms])
    if ready_ms > int(cutoff_ms):
        base_coverage["anchor_status"] = "anchor_not_yet_ready"
        return [], base_coverage
    anchor_ts = ts[anchor_a:anchor_b]
    if (
        len(anchor_ts) != 24
        or anchor_ts[0] != start_ms
        or anchor_ts[-1] != ready_ms - HOUR_MS
        or np.any(np.diff(anchor_ts) != HOUR_MS)
    ):
        base_coverage["anchor_status"] = "incomplete_anchor"
        return [], base_coverage

    week_high = float(np.max(values[anchor_a:anchor_b, 2]))
    week_low = float(np.min(values[anchor_a:anchor_b, 3]))
    base_coverage.update(week_high=week_high, week_low=week_low)
    if not np.isfinite(week_high - week_low) or week_high <= week_low:
        base_coverage["anchor_status"] = "flat_anchor"
        return [], base_coverage

    post = int(np.searchsorted(ts, ready_ms, side="left"))
    if post >= len(ts) or ts[post] != ready_ms:
        base_coverage["anchor_status"] = "no_post_anchor_tape"
        return [], base_coverage

    cutoff_stop = int(np.searchsorted(ts, min(expires_ms, int(cutoff_ms)), side="left"))
    gap_ends = np.flatnonzero(np.diff(ts) != HOUR_MS) + 1
    gap_pos = int(np.searchsorted(gap_ends, post + 1, side="left"))
    next_gap = int(gap_ends[gap_pos]) if gap_pos < len(gap_ends) else None
    has_gap = next_gap is not None and next_gap < cutoff_stop
    end = min(cutoff_stop, next_gap if has_gap else cutoff_stop)
    active_ts = ts[post:end]
    base_coverage["anchor_status"] = "ready"
    if len(active_ts):
        base_coverage["last_observed_ms"] = int(active_ts[-1])
        base_coverage["active_end_ms"] = int(active_ts[-1]) + HOUR_MS
        base_coverage["active_window_complete"] = bool(
            not has_gap and cutoff_ms >= expires_ms and active_ts[-1] + HOUR_MS == expires_ms
        )
    elif cutoff_ms >= expires_ms:
        base_coverage["active_end_ms"] = ready_ms

    records: list[dict[str, Any]] = []
    for side, side_name, rail in (
        (1, "lower", week_low - RANGE_MULTIPLE * (week_high - week_low)),
        (-1, "upper", week_high + RANGE_MULTIPLE * (week_high - week_low)),
    ):
        row: dict[str, Any] = {
            "event_id": f"{symbol}/{week_key}/{side_name}",
            "symbol": str(symbol), "week": week_key, "side": int(side),
            "side_name": side_name, "tf": np.nan, "bar_ms": np.nan,
            "close_ms": np.nan, "first_breach_ms": np.nan,
            "anchor_start_ms": start_ms, "anchor_ready_ms": ready_ms,
            "expires_ms": expires_ms, "week_high": week_high,
            "week_low": week_low, "rail": float(rail),
            "prior_extreme": np.nan, "state_1h": "not_reached",
            "state_4h": "not_reached", "state_12h": "not_reached",
            "bar_open": np.nan, "bar_high": np.nan, "bar_low": np.nan,
            "bar_close": np.nan, "tip": np.nan, "stop": np.nan,
            "entry_i": np.nan, "entry_ms": np.nan, "entry": np.nan,
            "risk_frac": np.nan, "status": "no_breach",
            "active_window_complete": bool(base_coverage["active_window_complete"]),
            "last_observed_ms": base_coverage["last_observed_ms"],
        }
        if rail <= 0:
            row["status"] = "invalid_rail"
            row.update(state_1h="invalid_rail", state_4h="invalid_rail", state_12h="invalid_rail")
            records.append(row)
            continue
        if end <= post:
            row["status"] = "no_active_observation"
            row.update(state_1h="no_active_observation", state_4h="no_active_observation",
                       state_12h="no_active_observation")
            records.append(row)
            continue

        tolerance = rail * 1e-12
        extreme_column = 3 if side == 1 else 2
        extremes = values[post:end, extreme_column]
        outside = np.flatnonzero(side * (extremes - rail) < -tolerance)
        if not len(outside):
            records.append(row)
            continue

        offset = int(outside[0])
        breach_i = post + offset
        breach_ms = int(ts[breach_i])
        row["first_breach_ms"] = breach_ms
        row["prior_extreme"] = (
            float(np.min(extremes[:offset])) if side == 1 and offset
            else float(np.max(extremes[:offset])) if side == -1 and offset
            else np.nan
        )
        if side == 1:
            base_coverage["first_breach_lower_ms"] = breach_ms
        else:
            base_coverage["first_breach_upper_ms"] = breach_ms
        row["status"] = "none_qualified"

        chosen: dict[str, Any] | None = None
        for hours in TIMEFRAMES_HOURS:
            state, candidate = _candle(
                values, ts, breach_ms, hours, expires_ms, side, rail, tolerance
            )
            row[f"state_{hours}h"] = state
            if chosen is None and candidate is not None:
                chosen = candidate
        if chosen is None:
            records.append(row)
            continue

        entry_i = int(chosen.pop("entry_i"))
        stop = float(chosen["tip"] * (1.0 - side * float(stop_buffer)))
        row.update(chosen, stop=stop)
        if entry_i >= len(ts) or ts[entry_i] != int(row["close_ms"]):
            row["status"] = "confirmed_no_next_open"
            records.append(row)
            continue
        entry = float(values[entry_i, 1])
        risk_frac = float(side * (entry - stop) / entry)
        if not np.isfinite(risk_frac) or risk_frac <= 0:
            row["status"] = "confirmed_open_beyond_stop"
            row.update(entry_i=entry_i, entry_ms=int(ts[entry_i]), entry=entry,
                       risk_frac=risk_frac)
            records.append(row)
            continue
        row.update(status="entered", entry_i=entry_i, entry_ms=int(ts[entry_i]),
                   entry=entry, risk_frac=risk_frac)
        records.append(row)

    return records, base_coverage


def _load_priority_context() -> dict[str, Any]:
    """Load only the already registered 925-contract frozen input context."""
    from yoyo.evaluation import month_firstweek_wick_priority as priority

    config = json.loads((priority.EXP / "config.json").read_text())
    parent_config = json.loads((priority.base.EXP / "config.json").read_text())
    cutoff_text = str(config["cutoff_exclusive"])
    if cutoff_text != str(parent_config["data_cutoff_exclusive"]):
        # Compare instants too, to accept equivalent ISO offsets without
        # silently changing the source cutoff.
        if pd.Timestamp(cutoff_text) != pd.Timestamp(parent_config["data_cutoff_exclusive"]):
            raise ValueError("Priority and parent frozen input cutoffs differ")
    contracts = pd.read_csv(priority.base.EXP / "contracts.csv").set_index("symbol")
    roster = pd.read_csv(priority.base.REFRESH / "roster.csv")
    if roster.symbol.duplicated().any() or set(roster.symbol) != set(contracts.index):
        raise ValueError("Frozen 925-contract roster differs from parent contracts")
    ends = pd.to_numeric(roster.set_index("symbol").end_ms, errors="coerce")
    parent_reader_dir = str(priority.base.PARENT)
    if parent_reader_dir not in sys.path:
        sys.path.insert(0, parent_reader_dir)
    from census12 import read_raw

    cutoff_ms = int(pd.Timestamp(cutoff_text).timestamp() * 1000)
    return {
        "priority": priority,
        "config": config,
        "cutoff_ms": cutoff_ms,
        "contracts": contracts,
        "ends": ends,
        "read_raw": read_raw,
    }


@lru_cache(maxsize=1)
def _source_context() -> dict[str, Any]:
    return _load_priority_context()


def load_frozen_tape(symbol: str) -> np.ndarray:
    """Return the cleaned existing source tape as N x 5 [open_ms, O, H, L, C].

    No download is performed here. The function delegates to the audited
    ``month_firstweek_wick_priority.frozen_tape`` and parent ``census12`` reader.
    """
    context = _source_context()
    symbol = str(symbol)
    if symbol not in context["contracts"].index:
        raise KeyError(f"{symbol} is outside the frozen USDT-perpetual roster")
    end_ms = context["ends"].get(symbol, np.nan)
    return context["priority"].frozen_tape(
        symbol, context["contracts"].loc[symbol], end_ms,
        context["cutoff_ms"], context["read_raw"],
    )


def _experiment_minute_path(symbol: str, start_ms: int) -> Path:
    return DATA_CACHE / "minutes" / str(symbol) / f"{int(start_ms)}.npy"


def _save_minute(path: Path, raw: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    with tmp.open("wb") as handle:
        np.save(handle, np.asarray(raw, dtype=np.float64), allow_pickle=False)
    os.replace(tmp, path)


@lru_cache(maxsize=8192)
def _minute_for_hour(symbol: str, start_ms: int, reference: tuple[float, ...]) -> np.ndarray | None:
    """Check experiment/shared/day caches, then request the existing public cache path."""
    from yoyo.data import binance_monthweek_disputed_minutes as existing

    symbol = str(symbol)
    path = _experiment_minute_path(symbol, start_ms)
    expected = np.asarray(reference, dtype=np.float64)
    if path.is_file():
        try:
            candidate = np.load(path, allow_pickle=False)
            if existing.valid(candidate, start_ms, expected):
                with _MINUTE_DIAGNOSTICS_LOCK:
                    _MINUTE_DIAGNOSTICS[(symbol, start_ms)] = "experiment_minute_cache"
                return candidate
        except (OSError, ValueError):
            pass

    shared = existing.CACHE / symbol / f"{int(start_ms)}.npy"
    day = datetime.fromtimestamp(start_ms / 1000, UTC).strftime("%Y-%m-%d")
    day_file = existing.DAYS / symbol / f"{day}.csv"
    source_before = (
        "shared_minute_cache" if shared.is_file()
        else "registered_touchday_cache" if day_file.is_file()
        else "public_rest_or_archive"
    )
    raw = existing.minute_lookup(symbol, np.asarray([start_ms, *expected], dtype=np.float64))
    if raw is None or not existing.valid(raw, start_ms, expected):
        source = "minute_unavailable"
        raw = None
    else:
        _save_minute(path, raw)
        source = source_before
    with _MINUTE_DIAGNOSTICS_LOCK:
        _MINUTE_DIAGNOSTICS[(symbol, start_ms)] = source
    return raw


def minute_lookup(symbol: str, hourly_bar: np.ndarray) -> np.ndarray | None:
    """Get a validated complete minute tape for one disputed H1 bar, on demand."""
    values = np.asarray(hourly_bar, dtype=np.float64)
    if values.shape != (5,) or not np.isfinite(values).all():
        raise ValueError("hourly_bar must be one finite [open_ms,O,H,L,C] row")
    start_ms = int(values[0])
    return _minute_for_hour(str(symbol), start_ms, tuple(float(x) for x in values[1:]))


def run_3r_outcomes(
    raw: np.ndarray,
    events: list[Mapping[str, Any]],
    *,
    minute_lookup_fn: Callable[[str, np.ndarray], np.ndarray | None] | None = minute_lookup,
) -> list[dict[str, Any]]:
    """Resolve only a 3R target versus the signal wick stop using VectorBT.

    H1 barrier order, gap fills, and 1m dispute resolution use the existing
    ``monday_wick_r_engine.first_hit`` kernel through the month-week exit
    adapter. Its leading sentinel means native ``start=entry_i+1`` maps to the
    source entry H1 bar, so the position can resolve during its fill candle. A
    missing minute tape for an H1 dual-touch
    keeps the engine's stop-first fill, records ``h1_fallback``, and remains
    explicitly ambiguous.
    """
    bars = np.asarray(raw, dtype=np.float64)
    if bars.ndim != 2 or bars.shape[1] != 5:
        raise ValueError("raw must be N x 5 [open_ms, O, H, L, C]")
    if not events:
        return []
    bars, timestamps_i = _validate_hourly(bars)
    if not len(bars):
        raise ValueError("raw cannot be empty when events are supplied")

    from yoyo.evaluation import month_firstweek_12h_wick_exits as adapter
    from yoyo.evaluation.monday_wick_r_engine import first_hit
    import vectorbt as vbt

    o, h, low, c, po, ty, scratch = adapter._native_arrays(
        np.ascontiguousarray(bars[:, 1:5])
    )
    gap_after = np.flatnonzero(np.diff(timestamps_i) != HOUR_MS) + 1
    rows: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []
    for row_i, event in enumerate(events):
        event_id = str(event["event_id"])
        try:
            entry_i = int(event["entry_i"])
            side = float(event["side"])
            entry = float(event["entry"])
            stop = float(event["stop"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid entered event at row {row_i}: {event_id}") from exc
        if (
            isinstance(event.get("entry_i"), (bool, np.bool_))
            or entry_i != event["entry_i"]
            or not 0 <= entry_i < len(bars)
            or side not in (-1.0, 1.0)
            or not np.isfinite([entry, stop]).all()
            or entry <= 0
            or stop <= 0
            or side * (entry - stop) <= 0
            or int(timestamps_i[entry_i]) != int(event.get("entry_ms", timestamps_i[entry_i]))
            or not np.isclose(entry, bars[entry_i, 1], rtol=1e-12, atol=0)
        ):
            raise ValueError(f"Invalid frozen entry/stop for {event_id}")

        risk = abs(entry - stop)
        target = entry + side * TARGET_R * risk
        target_possible = target > 0 and side * (target - entry) > 0
        active_target = target if target_possible else np.nan
        gap_pos = int(np.searchsorted(gap_after, entry_i + 1, side="left"))
        has_gap = gap_pos < len(gap_after)
        end_i = int(gap_after[gap_pos] - 1) if has_gap else len(bars) - 1
        censor_reason = "gap" if has_gap else "end"
        hit_i, fill_price, hit_kind, ambiguous = first_hit(
            o, h, low, c, entry_i + 1, end_i + 1, entry, stop,
            active_target, side, po, ty, scratch,
        )
        outcome = "censored"
        resolution = "h1"
        exit_i: int | None = None
        exit_ms: int | None = None
        exit_price: float | None = None
        minute_source = "not_needed"
        ambiguous_flag = bool(ambiguous)

        if hit_i >= 0:
            if hit_kind == 4:
                outcome = "tp"
            elif hit_kind == 1:
                outcome = "sl"
            else:
                raise RuntimeError(f"Unexpected native first_hit kind {hit_kind}")
            exit_i = int(hit_i - 1)
            exit_ms = int(timestamps_i[exit_i])
            exit_price = float(fill_price)
            if ambiguous and minute_lookup_fn is not None:
                minute_raw = minute_lookup_fn(str(event["symbol"]), bars[exit_i])
                if minute_raw is None:
                    resolution = "h1_fallback"
                    minute_source = _minute_source(str(event["symbol"]), int(timestamps_i[exit_i]))
                else:
                    minute_bars = adapter._validate_minute_tape(minute_raw, bars[exit_i])
                    minute_hit = adapter._minute_first_hit(
                        minute_bars, entry, stop, active_target, side
                    )
                    if minute_hit is None:
                        raise RuntimeError(f"H1 dual-touch has no minute-level hit: {event_id}")
                    minute_i, minute_price, minute_kind, minute_ambiguous = minute_hit
                    if minute_kind not in (1, 4):
                        raise RuntimeError(f"Unexpected minute first_hit kind {minute_kind}")
                    outcome = "sl" if minute_kind == 1 else "tp"
                    exit_ms = int(minute_bars[minute_i, 0])
                    exit_price = float(minute_price)
                    ambiguous_flag = bool(minute_ambiguous)
                    resolution = "1m_stop_first" if minute_ambiguous else "1m"
                    minute_source = _minute_source(str(event["symbol"]), int(timestamps_i[exit_i]))
            elif ambiguous:
                resolution = "h1_fallback"
                minute_source = "minute_lookup_disabled"
        else:
            minute_source = "not_needed"

        closed = outcome in ("tp", "sl")
        mark_ms = int(timestamps_i[end_i])
        mark_price = float(bars[end_i, 4])
        exit_fee_rate = (
            EXIT_MAKER_FEE if outcome == "tp" else EXIT_TAKER_FEE if outcome == "sl" else np.nan
        )
        entry_fee = entry * ENTRY_TAKER_FEE
        exit_fee = float(exit_price) * float(exit_fee_rate) if closed else 0.0
        row = {
            "label_id": f"{event_id}/3R", "event_id": event_id,
            "symbol": str(event["symbol"]), "week": str(event["week"]),
            "side": int(side), "tf": int(event["tf"]),
            "bar_ms": int(event["bar_ms"]), "close_ms": int(event["close_ms"]),
            "first_breach_ms": int(event["first_breach_ms"]),
            "anchor_start_ms": int(event["anchor_start_ms"]),
            "anchor_ready_ms": int(event["anchor_ready_ms"]),
            "expires_ms": int(event["expires_ms"]),
            "entry_ms": int(timestamps_i[entry_i]), "entry": entry,
            "stop": stop, "risk_abs": risk, "risk_frac": float(event.get("risk_frac", risk / entry)),
            "target": "3R", "target_price": float(target), "outcome": outcome,
            "resolution": resolution, "ambiguous": bool(ambiguous_flag),
            "minute_source": minute_source, "exit_ms": exit_ms,
            "exit_price": exit_price,
            "gross_r": float(side * (exit_price - entry) / risk) if closed else np.nan,
            "net_r": np.nan, "net_return": np.nan,
            "entry_fee_rate": ENTRY_TAKER_FEE,
            "exit_fee_rate": float(exit_fee_rate) if closed else np.nan,
            "entry_fee": entry_fee, "exit_fee": exit_fee,
            "fees_paid": entry_fee + exit_fee,
            "mark_ms": mark_ms, "mark_price": mark_price,
            "mark_gross_r": float(side * (mark_price - entry) / risk),
            "mark_net_r": float((side * (mark_price - entry) - entry_fee) / risk),
            "censor_reason": censor_reason if not closed else None,
            "quantity": 1.0, "engine": "VectorBT.from_orders+monday_wick_r_engine.first_hit",
        }
        rows.append(row)
        private.append({
            "entry": entry, "side": side, "risk": risk, "entered": True,
            "closed": closed, "exit_price": exit_price if closed else mark_price,
            "outcome": outcome, "mark_price": mark_price,
            "entry_fee_rate": ENTRY_TAKER_FEE,
            "exit_fee_rate": exit_fee_rate if closed else 0.0,
        })

    count = len(rows)
    entries_array = np.asarray([item["entry"] for item in private], dtype=np.float64)
    sides = np.asarray([item["side"] for item in private], dtype=np.float64)
    closed_array = np.asarray([item["closed"] for item in private], dtype=bool)
    exits = np.asarray([item["exit_price"] for item in private], dtype=np.float64)
    sizes = np.vstack((sides, np.where(closed_array, -sides, 0.0)))
    order_prices = np.vstack((entries_array, exits))
    close_prices = order_prices.copy()
    fees = np.vstack((
        np.full(count, ENTRY_TAKER_FEE, dtype=np.float64),
        np.asarray([item["exit_fee_rate"] for item in private], dtype=np.float64),
    ))
    init_cash = np.maximum.reduce((entries_array, exits, np.asarray(
        [item["mark_price"] for item in private], dtype=np.float64
    ))) * 4.0
    portfolio = vbt.Portfolio.from_orders(
        close_prices, size=sizes, price=order_prices, size_type="amount",
        direction="both", fees=fees, init_cash=init_cash, cash_sharing=False,
        allow_partial=False, raise_reject=True,
    )
    trade_records = portfolio.trades.records
    closed_records = trade_records[trade_records.status.eq(1)]
    if len(closed_records) != int(closed_array.sum()):
        raise RuntimeError("VectorBT closed trade count differs from 3R TP/SL hits")
    for record in closed_records.itertuples(index=False):
        column = int(record.col)
        pnl = float(record.pnl)
        expected = (
            private[column]["side"] * (float(rows[column]["exit_price"]) - private[column]["entry"])
            - private[column]["entry"] * ENTRY_TAKER_FEE
            - float(rows[column]["exit_price"]) * float(private[column]["exit_fee_rate"])
        )
        if not np.isclose(pnl, expected, rtol=1e-9, atol=1e-10):
            raise RuntimeError(f"VectorBT net PnL differs from fee arithmetic: {rows[column]['event_id']}")
        rows[column]["net_return"] = pnl / private[column]["entry"]
        rows[column]["net_r"] = pnl / private[column]["risk"]
    return rows


def _minute_source(symbol: str, start_ms: int) -> str:
    with _MINUTE_DIAGNOSTICS_LOCK:
        return _MINUTE_DIAGNOSTICS.get((str(symbol), int(start_ms)), "minute_source_unknown")


def _cache_paths(symbol: str) -> dict[str, Path]:
    root = SYMBOL_CACHE / str(symbol)
    return {
        "root": root,
        "events": root / "first_breaches.csv.gz",
        "coverage": root / "week_coverage.csv.gz",
        "labels": root / "outcomes_3r.csv.gz",
        "contract": root / "contract.json",
        "receipt": root / "receipt.json",
    }


def _nullable_int(value: Any) -> int | None:
    """Convert a finite numeric contract field to int while preserving nulls."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if np.isfinite(number) else None


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


def _atomic_frame(path: Path, frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    complete = frame.reindex(columns=columns)
    complete.to_csv(tmp, index=False, compression={"method": "gzip", "compresslevel": 1})
    os.replace(tmp, path)


def _cache_receipt_matches(
    paths: Mapping[str, Path], symbol: str, cutoff_ms: int,
    contract: Mapping[str, Any],
) -> bool:
    if not all(paths[key].is_file() for key in ("events", "coverage", "labels", "contract", "receipt")):
        return False
    try:
        receipt = json.loads(paths["receipt"].read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return all(receipt.get(key) == value for key, value in {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "build_version": BUILD_VERSION,
        "experiment": EXPERIMENT_ID,
        "symbol": str(symbol),
        "cutoff_ms": int(cutoff_ms),
        "contract_hours": _nullable_int(contract.get("hours")),
        "contract_first_ms": _nullable_int(contract.get("first_ms")),
        "contract_end_ms": _nullable_int(contract.get("end_ms")),
    }.items())


def _read_symbol_cache(paths: Mapping[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    return (
        pd.read_csv(paths["events"], float_precision="round_trip"),
        pd.read_csv(paths["coverage"], float_precision="round_trip"),
        pd.read_csv(paths["labels"], float_precision="round_trip"),
        json.loads(paths["contract"].read_text()),
    )


def _write_symbol_cache(
    paths: Mapping[str, Path], symbol: str, cutoff_ms: int,
    contract: Mapping[str, Any], events: list[dict[str, Any]],
    coverage: list[dict[str, Any]], labels: list[dict[str, Any]],
    contract_summary: Mapping[str, Any],
) -> None:
    event_frame = pd.DataFrame(events, columns=EVENT_COLUMNS)
    coverage_frame = pd.DataFrame(coverage, columns=COVERAGE_COLUMNS)
    label_frame = pd.DataFrame(labels, columns=LABEL_COLUMNS)
    _atomic_frame(paths["events"], event_frame, EVENT_COLUMNS)
    _atomic_frame(paths["coverage"], coverage_frame, COVERAGE_COLUMNS)
    _atomic_frame(paths["labels"], label_frame, LABEL_COLUMNS)
    _atomic_json(paths["contract"], dict(contract_summary))
    _atomic_json(paths["receipt"], {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "build_version": BUILD_VERSION,
        "experiment": EXPERIMENT_ID,
        "symbol": str(symbol),
        "cutoff_ms": int(cutoff_ms),
        "contract_hours": _nullable_int(contract.get("hours")),
        "contract_first_ms": _nullable_int(contract.get("first_ms")),
        "contract_end_ms": _nullable_int(contract.get("end_ms")),
        "events": len(event_frame), "coverage_weeks": len(coverage_frame),
        "entered": int(event_frame.status.eq("entered").sum()),
        "labels": len(label_frame),
    })


def _weeks_for_raw(raw: np.ndarray, cutoff_ms: int) -> list[date]:
    _, ts = _validate_hourly(raw)
    return _week_dates(ts, cutoff_ms)


def process_symbol(
    symbol: str,
    contract: Mapping[str, Any] | pd.Series,
    end_ms: Any,
    cutoff_ms: int,
    read_raw: Callable[[str], np.ndarray],
    *,
    use_symbol_cache: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build/cache one frozen contract's weekly first-breach and 3R rows."""
    contract_data = dict(contract)
    paths = _cache_paths(str(symbol))
    if use_symbol_cache and _cache_receipt_matches(paths, str(symbol), cutoff_ms, contract_data):
        return _read_symbol_cache(paths)

    from yoyo.evaluation import month_firstweek_wick_priority as priority

    raw = priority.frozen_tape(str(symbol), contract, end_ms, cutoff_ms, read_raw)
    values, ts = _validate_hourly(raw)
    all_events: list[dict[str, Any]] = []
    all_coverage: list[dict[str, Any]] = []
    for week_date in _week_dates(ts, cutoff_ms):
        rows, coverage = scan_week(str(symbol), values, week_date, cutoff_ms=cutoff_ms)
        all_events.extend(rows)
        all_coverage.append(coverage)
    entered = [row for row in all_events if row["status"] == "entered"]
    labels = run_3r_outcomes(values, entered, minute_lookup_fn=minute_lookup)
    contract_summary = {
        "symbol": str(symbol),
        "hours": int(len(values)),
        "removed_hours": int(contract_data.get("removed_hours", 0)),
        "first_ms": int(values[0, 0]) if len(values) else None,
        "end_ms": int(values[-1, 0]) + HOUR_MS if len(values) else None,
        "calendar_weeks": len(all_coverage),
        "anchor_ready_weeks": sum(row["anchor_status"] == "ready" for row in all_coverage),
        "first_breaches": sum(pd.notna(row.get("first_breach_ms")) for row in all_events),
        "entered": len(entered),
        "labels": len(labels),
    }
    expected_hours = _nullable_int(contract_data.get("hours"))
    expected_first = _nullable_int(contract_data.get("first_ms"))
    expected_end = _nullable_int(contract_data.get("end_ms"))
    if len(values) and (
        expected_hours != len(values)
        or expected_first != int(values[0, 0])
        or expected_end != int(values[-1, 0]) + HOUR_MS
    ):
        raise ValueError(f"Frozen cleaned lifecycle differs from registered contract row {symbol}")
    if use_symbol_cache:
        _write_symbol_cache(paths, str(symbol), cutoff_ms, contract_data,
                            all_events, all_coverage, labels, contract_summary)
        return _read_symbol_cache(paths)
    return (
        pd.DataFrame(all_events, columns=EVENT_COLUMNS),
        pd.DataFrame(all_coverage, columns=COVERAGE_COLUMNS),
        pd.DataFrame(labels, columns=LABEL_COLUMNS),
        contract_summary,
    )


def _config(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "experiment": EXPERIMENT_ID,
        "build_version": BUILD_VERSION,
        "parent_priority_experiment": context["priority"].EXP.name,
        "parent_frozen_contract_experiment": context["priority"].base.EXP.name,
        "cutoff_exclusive_ms": int(context["cutoff_ms"]),
        "cutoff_exclusive": str(context["config"]["cutoff_exclusive"]),
        "roster_contracts": int(len(context["contracts"])),
        "calendar": "monday_fib.week_window; America/New_York Monday 00:00 anchor; Tuesday 00:00 ready; next Monday 00:00 expiry",
        "range": "NY Monday high/low plus/minus 0.618 times Monday range",
        "first_breach": "first strict H1 extreme beyond rail in [ready_ms, expires_ms); previous equality does not consume; terminate at first cleaned-tape gap",
        "confirmation": "UTC-aligned 1h then 4h then 12h containing first strict breach; strictly inward open and close, strictly outward tip; close_ms == expires_ms allowed",
        "entry": "next H1 open at close_ms; frozen priority semantics permit close_ms == expires_ms when that next open exists and passes the stop",
        "stop": "signal bar tip extended outward by 1 percent",
        "labels": "only 3R TP versus signal-tip stop; VectorBT native fills/fees; first_hit native OHLC ordering; complete minute tape for dual-touch H1s; positions cross weeks; gap/end censored",
        "fees": {"entry_taker": ENTRY_TAKER_FEE, "stop_exit_taker": EXIT_TAKER_FEE, "target_exit_maker": EXIT_MAKER_FEE},
        "slippage": 0,
        "funding": 0,
    }


def main() -> None:
    context = _source_context()
    EXP.mkdir(parents=True, exist_ok=True)
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    (SYMBOL_CACHE).mkdir(parents=True, exist_ok=True)
    config = _config(context)
    (EXP / "census_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")

    contracts = context["contracts"]
    ends = context["ends"]
    symbols = list(contracts.index.astype(str))
    output_events: list[pd.DataFrame] = []
    output_coverage: list[pd.DataFrame] = []
    output_labels: list[pd.DataFrame] = []
    output_contracts: list[dict[str, Any]] = []

    def run_one(symbol: str):
        row = contracts.loc[symbol]
        events, coverage, labels, contract_summary = process_symbol(
            symbol, row, ends.get(symbol, np.nan), int(context["cutoff_ms"]),
            context["read_raw"], use_symbol_cache=True,
        )
        return events, coverage, labels, contract_summary

    # Four workers bound CSV/NPY IO and public minute-archive concurrency while
    # keeping per-contract Numba/VectorBT buffers isolated.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run_one, symbol): symbol for symbol in symbols}
        for completed, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            events, coverage, labels, contract_summary = future.result()
            output_events.append(events)
            output_coverage.append(coverage)
            output_labels.append(labels)
            output_contracts.append(contract_summary)
            if completed % 50 == 0 or completed == len(symbols):
                print(
                    f"{completed}/{len(symbols)} contracts; "
                    f"{sum(map(len, output_events))} first-breach rows; "
                    f"{sum(map(len, output_labels))} 3R labels",
                    flush=True,
                )

    events_frame = pd.concat(output_events, ignore_index=True) if output_events else pd.DataFrame(columns=EVENT_COLUMNS)
    coverage_frame = pd.concat(output_coverage, ignore_index=True) if output_coverage else pd.DataFrame(columns=COVERAGE_COLUMNS)
    labels_frame = pd.concat(output_labels, ignore_index=True) if output_labels else pd.DataFrame(columns=LABEL_COLUMNS)
    contracts_frame = pd.DataFrame(output_contracts).sort_values("symbol")
    if len(contracts_frame) != len(contracts) or contracts_frame.symbol.duplicated().any():
        raise AssertionError("Output universe does not cover the exact frozen contract roster")
    if events_frame.event_id.duplicated().any():
        raise AssertionError("Weekly symbol/side event identity is not unique")
    entered = events_frame.loc[events_frame.status.eq("entered"), "event_id"]
    if set(entered) != set(labels_frame.event_id):
        raise AssertionError("Every entered event must have exactly one 3R label")
    if labels_frame.label_id.duplicated().any() or not labels_frame.target.eq("3R").all():
        raise AssertionError("3R outcomes are duplicated or include another target")

    _atomic_frame(EXP / "contracts.csv.gz", contracts_frame, tuple(contracts_frame.columns))
    _atomic_frame(EXP / "week_coverage.csv.gz", coverage_frame, COVERAGE_COLUMNS)
    _atomic_frame(EXP / "first_breaches.csv.gz", events_frame, EVENT_COLUMNS)
    _atomic_frame(EXP / "outcomes_3r.csv.gz", labels_frame, LABEL_COLUMNS)
    _atomic_json(EXP / "receipt.json", {
        "experiment": EXPERIMENT_ID,
        "build_version": BUILD_VERSION,
        "roster_contracts": len(contracts_frame),
        "coverage_weeks": int(len(coverage_frame)),
        "ready_anchor_weeks": int(coverage_frame.anchor_status.eq("ready").sum()),
        "anchor_status_counts": coverage_frame.anchor_status.value_counts().to_dict(),
        "event_rows": int(len(events_frame)),
        "first_strict_breaches": int(events_frame.first_breach_ms.notna().sum()),
        "confirmed_rows": int(events_frame.status.isin(CONFIRMED_STATUSES).sum()),
        "entered_rows": int(events_frame.status.eq("entered").sum()),
        "label_rows_3r": int(len(labels_frame)),
        "outcome_counts": labels_frame.outcome.value_counts().to_dict(),
        "resolution_counts": labels_frame.resolution.value_counts().to_dict(),
        "boundary_rows_close_at_expiry": int(events_frame.close_ms.eq(events_frame.expires_ms).sum()),
        "outcome_boundary_rows_entry_at_expiry": int(labels_frame.entry_ms.eq(labels_frame.expires_ms).sum()),
        "ambiguous_rows": int(labels_frame.ambiguous.sum()),
        "ambiguous_h1_fallback_rows": int(labels_frame.resolution.eq("h1_fallback").sum()),
        "minute_source_counts": labels_frame.minute_source.value_counts().to_dict(),
        "fee_rates": {
            "entry_taker": ENTRY_TAKER_FEE,
            "stop_exit_taker": EXIT_TAKER_FEE,
            "target_exit_maker": EXIT_MAKER_FEE,
        },
    })


if __name__ == "__main__":
    main()
