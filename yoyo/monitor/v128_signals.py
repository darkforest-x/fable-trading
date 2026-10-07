"""Causal SPIKE V12.8 monitor adapter for caller-supplied closed candles.

The ordinary V12.8 signal path keeps the frozen V6--V9 admission bundle and
adds the Pine default 15m direction gate from the prior completed H1 SMA60.
The H1 value is selected at each chart bar's OPEN, so a developing hour can
never affect admission. Events remain confirmation-close references, not fills.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.monitor import TIMEFRAMES
from yoyo.monitor import v9_signals as v9
from yoyo.monitor.signals import AnalysisResult
from yoyo.monitor.v9_performance import BASIS as PERFORMANCE_BASIS, project


SIGNAL_PROTOCOL = "spike-burst-v128-monitor-v1"
SIGNAL_KIND = "spike_burst_v128"
STRATEGY_VERSION = "spike-v12.8-monitor-20260923-v1"
V128_PINE_SOURCE = Path(__file__).resolve().parents[1] / "evaluation/pine/spike_burst_v12_8.pine"
V128_SOURCE_SHA256 = hashlib.sha256(V128_PINE_SOURCE.read_bytes()).hexdigest()
H1_MS = 60 * 60 * 1000
V7_MINIMUM_WARMUP_BARS = v9.V7_MINIMUM_WARMUP_BARS
WARMUP = V7_MINIMUM_WARMUP_BARS
PROTOCOL = {
    "version": SIGNAL_PROTOCOL,
    "kind": SIGNAL_KIND,
    "source": "yoyo/evaluation/pine/spike_burst_v12_8.pine",
    "source_sha256": V128_SOURCE_SHA256,
    "strategy_version": STRATEGY_VERSION,
    "signal": "confirmed_bar_close",
    "entry_reference": "confirmation_close_reference_not_fill",
    "orders_enabled": False,
    "performance": PERFORMANCE_BASIS,
    "warmup_bars_minimum": V7_MINIMUM_WARMUP_BARS,
    "h1_sma60_gate": "15m_only_prior_completed_h1_at_chart_bar_open",
}


def _json_number(value: object) -> float | None:
    """Convert a numeric indicator value to JSON-safe form without imputation."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _h1_sma60_at_chart_opens(chart_open_ms: np.ndarray, higher: list[dict] | None
                              ) -> tuple[np.ndarray, np.ndarray, np.ndarray, str | None]:
    """Return prior-H1 SMA60 values known at each chart OPEN.

    ``higher`` contains native H1 candles stamped by open time. Each value uses
    exactly 60 contiguous closed H1 closes ending at the hour boundary at or
    before the chart open. Any missing/invalid hour or insufficient history
    fails closed only for chart bars whose 60-hour window crosses it.
    """
    values = np.full(len(chart_open_ms), np.nan, dtype=float)
    ready = np.zeros(len(chart_open_ms), dtype=bool)
    close_ms = np.full(len(chart_open_ms), -1, dtype=np.int64)
    if not higher:
        return values, ready, close_ms, "higher_candles_missing"
    closes: dict[int, float] = {}
    invalid_stamps: set[int] = set()
    try:
        rows = list(higher)
    except TypeError:
        return values, ready, close_ms, "higher_candles_invalid"
    if not rows:
        return values, ready, close_ms, "higher_candles_missing"
    for row in rows:
        try:
            stamp = int(row["t"])
        except (KeyError, TypeError, ValueError, OverflowError):
            # Without a timestamp this row cannot be placed on the H1 clock.
            return values, ready, close_ms, "higher_candle_timestamp_unreadable"
        if stamp in closes or stamp in invalid_stamps:
            closes.pop(stamp, None)
            invalid_stamps.add(stamp)
            continue
        try:
            candle = {key: float(row[key]) for key in ("o", "h", "l", "c", "v")}
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid_stamps.add(stamp)
            continue
        o, h, low, close, volume = (candle[key] for key in ("o", "h", "l", "c", "v"))
        valid = (stamp % H1_MS == 0 and np.isfinite(list(candle.values())).all() and low > 0 and volume >= 0
                 and h >= max(o, close, low) and low <= min(o, close, h))
        if valid:
            closes[stamp] = close
        else:
            invalid_stamps.add(stamp)
    for i, chart_open in enumerate(chart_open_ms.tolist()):
        # At an H1 boundary, that just-closed H1 is available immediately.
        known_h1_close = (int(chart_open) // H1_MS) * H1_MS
        last_h1_open = known_h1_close - H1_MS
        first_h1_open = last_h1_open - 59 * H1_MS
        expected = range(first_h1_open, last_h1_open + H1_MS, H1_MS)
        window = [closes.get(stamp) for stamp in expected]
        if len(window) != 60 or any(item is None or not math.isfinite(item) or item <= 0 for item in window):
            close_ms[i] = known_h1_close
            continue
        average = float(sum(window) / 60.0)
        if not math.isfinite(average) or average <= 0:
            close_ms[i] = known_h1_close
            continue
        values[i] = average
        ready[i] = True
        close_ms[i] = known_h1_close
    return values, ready, close_ms, None


def _gate_evidence(row: pd.Series, *, v9_admitted: bool, v128_admitted: bool,
                   applies: bool, h1_value: float, h1_ready: bool, h1_close_ms: int,
                   direction_allowed: bool, data_error: str | None) -> dict[str, object]:
    """Serialize the V9 bundle and V12.8's additional direction-gate evidence."""
    side = int(row["side"])
    if not applies:
        reason = "not_applicable"
    elif data_error:
        reason = data_error
    elif not h1_ready:
        reason = "h1_sma60_not_ready"
    elif not direction_allowed:
        reason = "close_not_strictly_on_signal_side_of_h1_sma60"
    elif not v9_admitted:
        reason = "v9_bundle_or_reference_risk_rejected"
    else:
        reason = "passed"
    return {
        "raw_side": side,
        "v9_admitted": bool(v9_admitted),
        "v128_admitted": bool(v128_admitted),
        "v9_reason": str(row["v9_reason"]),
        "h1_sma60_applies": bool(applies),
        "h1_sma60": _json_number(h1_value),
        "h1_sma60_ready": bool(h1_ready),
        "h1_sma60_close_ms": int(h1_close_ms) if h1_close_ms >= 0 else None,
        "h1_sma60_direction_allowed": bool(direction_allowed) if side else None,
        "h1_sma60_reason": reason,
        "base_asset": row.get("base_asset"),
        "volume_ratio": _json_number(row.get("volume_ratio")),
        "rope_distance_atr": _json_number(row.get("rope_distance_atr")),
        "risk_status": str(row["risk_status"]),
        "risk_basis": str(row["risk_basis"]),
        "higher_data_error": data_error,
    }


def analyze(candles: list[dict], higher: list[dict] | None, timeframe: str, *, tick: float,
            base_asset: str | None, chart_limit: int | None = None,
            _context: dict | None = None) -> AnalysisResult:
    """Return V12.8 ordinary confirmation observations for closed candles.

    15m entries require the strict directional close versus the prior completed
    H1 SMA60. Other monitored timeframes retain the frozen V9 admission path.
    The raw opposite V6 side remains in the performance replay even when the
    H1 gate refuses it as a new entry, allowing it to close an existing reference.
    """
    if chart_limit is not None and (type(chart_limit) is not int or chart_limit < 1):
        raise ValueError("invalid chart_limit")
    frame = v9._validate_frame(candles, timeframe)
    if len(frame) < 2:
        # The frozen squeeze kernel assumes at least two rows. A just-listed
        # contract with one closed candle is valid input, but cannot be ready.
        loading_chart = [dict(row, ready=False, burst=False, burst_up=False,
                              burst_down=False, v9_admitted=False, v128_admitted=False)
                         for row in candles]
        latest_clock = ({"bar_open_ms": int(frame.index[-1].value // 1_000_000),
                         "bar_close_ms": int(frame.index[-1].value // 1_000_000) + TIMEFRAMES[timeframe],
                         "price": float(frame.close.iloc[-1])} if len(frame) else {})
        return AnalysisResult({
            "events": [], "chart": loading_chart,
            "state": {"phase": "loading", "ready": False, "bars": len(frame), "timeframe": timeframe,
                      "direction": "both", "performance": PERFORMANCE_BASIS,
                      "protocol": SIGNAL_PROTOCOL, "source_sha256": V128_SOURCE_SHA256,
                      "kind": SIGNAL_KIND, "strategy_version": STRATEGY_VERSION, **latest_clock},
            "protocol": dict(PROTOCOL),
        })

    built, evidence = v9._decision_frame(frame, timeframe, tick=tick, base_asset=base_asset)
    step = TIMEFRAMES[timeframe]
    times = built.index.asi8 // 1_000_000
    start = max(0, len(built) - chart_limit) if chart_limit is not None else 0
    chart_values = {name: built[name].to_numpy(copy=False) for name in
                    ("open", "high", "low", "close", "volume", "s20", "e20", "s60", "e60",
                     "s120", "e120", "md", "sb")}
    ready = evidence.v7_ready.to_numpy(dtype=bool, copy=False)
    side_values = evidence.side.to_numpy(dtype=int, copy=False)
    v9_admitted = (evidence.v9.to_numpy(dtype=bool, copy=False)
                   & evidence.risk_status.eq("known").to_numpy(dtype=bool, copy=False))
    applies = timeframe == "15m"
    if applies:
        h1_values, h1_ready, h1_close_ms, higher_error = _h1_sma60_at_chart_opens(times, higher)
    else:
        h1_values = np.full(len(built), np.nan, dtype=float)
        h1_ready = np.zeros(len(built), dtype=bool)
        h1_close_ms = np.full(len(built), -1, dtype=np.int64)
        higher_error = None
    direction_allowed = np.zeros(len(built), dtype=bool)
    long = side_values == 1
    short = side_values == -1
    direction_allowed[long] = built.close.to_numpy(copy=False)[long] > h1_values[long]
    direction_allowed[short] = built.close.to_numpy(copy=False)[short] < h1_values[short]
    if applies:
        h1_gate_allowed = h1_ready & direction_allowed
    else:
        h1_gate_allowed = np.ones(len(built), dtype=bool)
    v128_admitted = v9_admitted & h1_gate_allowed
    # The notification-only V13 adapter shares this exact closed prefix. Keep
    # pandas objects off the JSON/chart payload and avoid a second kernel pass.
    if _context is not None:
        _context.update(built=built, evidence=evidence, admitted=v128_admitted)

    projection = project(built, evidence, minutes=step // 60_000, tick=float(tick),
                         data_gap=built.attrs["data_gap"], admitted=v128_admitted)
    chart: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    for i in range(start, len(built)):
        burst = bool(v128_admitted[i])
        chart.append({
            "t": int(times[i]), "o": float(chart_values["open"][i]), "h": float(chart_values["high"][i]),
            "l": float(chart_values["low"][i]), "c": float(chart_values["close"][i]),
            "v": float(chart_values["volume"][i]),
            "sma20": v9._json_number(chart_values["s20"][i]), "ema20": v9._json_number(chart_values["e20"][i]),
            "sma60": v9._json_number(chart_values["s60"][i]), "ema60": v9._json_number(chart_values["e60"][i]),
            "sma120": v9._json_number(chart_values["s120"][i]), "ema120": v9._json_number(chart_values["e120"][i]),
            "md": v9._json_number(chart_values["md"][i]), "sb": v9._json_number(chart_values["sb"][i]),
            "ready": bool(ready[i]), "burst": burst, "burst_up": bool(burst and side_values[i] == 1),
            "burst_down": bool(burst and side_values[i] == -1),
            "v9_admitted": bool(v9_admitted[i]), "v128_admitted": burst,
            "raw_side": int(side_values[i]), "v9_reason": str(evidence.v9_reason.iloc[i]),
            "h1_sma60_applies": applies, "h1_sma60": v9._json_number(h1_values[i]),
            "h1_sma60_ready": bool(h1_ready[i]),
            "h1_sma60_close_ms": int(h1_close_ms[i]) if h1_close_ms[i] >= 0 else None,
            "h1_sma60_direction_allowed": bool(direction_allowed[i]) if side_values[i] else None,
        })

    for i in np.flatnonzero(v128_admitted):
        gate = evidence.iloc[i]
        side = "long" if side_values[i] == 1 else "short"
        close_ms = int(times[i]) + step
        h1_evidence = _gate_evidence(
            gate, v9_admitted=bool(v9_admitted[i]), v128_admitted=True, applies=applies,
            h1_value=float(h1_values[i]), h1_ready=bool(h1_ready[i]),
            h1_close_ms=int(h1_close_ms[i]), direction_allowed=bool(direction_allowed[i]),
            data_error=higher_error,
        )
        performance = projection.get(close_ms, {"status": "unknown", "basis": PERFORMANCE_BASIS})
        events.append({
            "protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": "live", "confirmation": "raw",
            "direction": side, "side": side, "strategy_version": STRATEGY_VERSION,
            "v9_admitted": bool(v9_admitted[i]), "v128_admitted": True,
            "timeframe": timeframe, "timeframe_min": step // 60_000,
            "bar_open_ms": int(times[i]), "bar_close_ms": close_ms, "signal_close_time": close_ms,
            "is_closed": True,
            "price": float(gate.reference_price), "risk": float(gate.reference_initial_risk),
            "initial_stop": float(gate.reference_initial_stop),
            "reference_price": float(gate.reference_price),
            "reference_initial_risk": float(gate.reference_initial_risk),
            "reference_initial_stop": float(gate.reference_initial_stop),
            "reference_cost_r": float(gate.reference_cost_r), "base_asset": gate.base_asset,
            "volume_ratio": float(gate.volume_ratio), "rope_distance_atr": float(gate.rope_distance_atr),
            "risk_basis": str(gate.risk_basis), "risk_status": str(gate.risk_status),
            "source_sha256": V128_SOURCE_SHA256, "ready": True, "confirmed": True,
            "entry_reference": "confirmation_close_reference_not_fill",
            "executable_entry_time": None, "is_trade": False,
            "performance": performance, "v9_evidence": v9._evidence_row(gate),
            "v128_evidence": h1_evidence,
            "h1_sma60": h1_evidence["h1_sma60"],
            "h1_sma60_close_ms": h1_evidence["h1_sma60_close_ms"],
        })

    latest = evidence.iloc[-1]
    latest_h1_evidence = _gate_evidence(
        latest, v9_admitted=bool(v9_admitted[-1]), v128_admitted=bool(v128_admitted[-1]),
        applies=applies, h1_value=float(h1_values[-1]), h1_ready=bool(h1_ready[-1]),
        h1_close_ms=int(h1_close_ms[-1]), direction_allowed=bool(direction_allowed[-1]),
        data_error=higher_error,
    )
    v7_ready = bool(built.attrs["v7_ready_at_end"])
    state = {
        "phase": "ready" if v7_ready and len(built) >= V7_MINIMUM_WARMUP_BARS else "loading",
        "ready": bool(v7_ready and len(built) >= V7_MINIMUM_WARMUP_BARS),
        "bars": len(built), "timeframe": timeframe, "direction": "both",
        "bar_open_ms": int(times[-1]), "bar_close_ms": int(times[-1]) + step,
        "price": float(built.close.iloc[-1]), "protocol": SIGNAL_PROTOCOL,
        "kind": SIGNAL_KIND, "strategy_version": STRATEGY_VERSION,
        "source_sha256": V128_SOURCE_SHA256, "performance": PERFORMANCE_BASIS,
        "base_asset": latest.base_asset, "v9_evidence": v9._evidence_row(latest),
        "v128_evidence": latest_h1_evidence,
    }
    return AnalysisResult({"events": events, "chart": chart, "state": state,
                           "protocol": dict(PROTOCOL)}, event_performance=projection)
