"""Supplied-candle-only SPIKE V9 confirmation adapter.

This monitor adapter applies the fixed V6 structural confirmation, V7 BB
squeeze, V8 three-ATR rope-distance, and V9 base/RV/calendar admissions to
closed candles supplied by its caller.  It never loads OHLC files, invokes the
serial replay, infers an instrument base from a symbol, or treats a signal as
an order.  Every event is a confirmation-close observation with a non-fill risk
reference calculated from the current and four preceding closed bars.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_progressive import features
from yoyo.evaluation.spike_v6_wvf_study import _data_gap
from yoyo.evaluation.spike_v7_fast import v6_signals, v7_diagnostics
from yoyo.evaluation.spike_v9 import VERSION as V9_STRATEGY_VERSION, v9_admissions
from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.signals import AnalysisResult


V9_PINE_SOURCE = Path(__file__).resolve().parents[1] / "evaluation/pine/spike_burst_v9.pine"
V9_SOURCE_SHA256 = hashlib.sha256(V9_PINE_SOURCE.read_bytes()).hexdigest()
# V7 itself decides when its complete BB history is available.  This lower
# bound prevents callers from presenting a short V1-sized history as V9-ready.
V7_MINIMUM_WARMUP_BARS = 520
# Existing monitor callers import ``WARMUP`` from signal adapters.
WARMUP = V7_MINIMUM_WARMUP_BARS
PROTOCOL = {
    "version": SIGNAL_PROTOCOL,
    "source": "yoyo/evaluation/pine/spike_burst_v9.pine",
    "source_sha256": V9_SOURCE_SHA256,
    "signal": "confirmed_bar_close",
    "entry_reference": "confirmation_close_reference_not_fill",
    "orders_enabled": False,
    "performance": "not_tracked",
    "warmup_bars_minimum": V7_MINIMUM_WARMUP_BARS,
}


def _json_number(value: object) -> float | None:
    """Convert a numeric indicator value to JSON-safe form without imputation."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validate_frame(candles: list[dict], timeframe: str) -> pd.DataFrame:
    """Validate continuous supplied closed candles and return UTC OHLCV.

    The adapter consumes only ``t/o/h/l/c/v``.  A row's timestamp is its open;
    the caller is responsible for passing closed-bar checkpoints rather than an
    in-progress exchange candle.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported monitored timeframe")
    step = TIMEFRAMES[timeframe]
    rows: list[tuple[pd.Timestamp, dict[str, float]]] = []
    previous: int | None = None
    for row in candles:
        try:
            stamp = int(row["t"])
            values = {key: float(row[key]) for key in ("o", "h", "l", "c", "v")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid candle") from exc
        if previous is not None and stamp - previous != step:
            raise ValueError("candles must be continuous")
        if (stamp % step or not np.isfinite(list(values.values())).all() or values["l"] <= 0
                or values["v"] < 0 or values["h"] < max(values["o"], values["c"], values["l"])
                or values["l"] > min(values["o"], values["c"], values["h"])):
            raise ValueError("invalid candle")
        rows.append((pd.Timestamp(stamp, unit="ms", tz="UTC"), values))
        previous = stamp
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    frame = pd.DataFrame([values for _, values in rows],
                         index=pd.DatetimeIndex([stamp for stamp, _ in rows]))
    frame.columns = ["open", "high", "low", "close", "volume"]
    return frame


def _decision_frame(frame: pd.DataFrame, timeframe: str, *, tick: float,
                    base_asset: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the V9 context from this supplied prefix and return its evidence.

    Feature inputs are the current/preceding OHLCV bars.  V7's BB width uses
    the current width against prior widths; V8 uses the current close and ATR;
    V9's scheduled clock is this bar's close.  ``base_asset`` is explicit
    instrument metadata from the caller, never parsed from a symbol or quote.
    """
    if isinstance(tick, bool) or not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    minutes = TIMEFRAMES[timeframe] // 60_000
    built = features(frame)
    gaps = _data_gap(built, minutes)
    raw = v6_signals(built, minutes)
    bb = v7_diagnostics(built, data_gap=gaps)
    built.attrs["v7_ready_at_end"] = bool(bb.v7_ready.iloc[-1]) if len(bb) else False
    context = SimpleNamespace(
        cache={"bars": built, "signals": raw, "bb": bb, "data_gap": gaps, "tick": float(tick)},
        minutes=minutes,
        identity={"asset": base_asset},
    )
    evidence = v9_admissions(context)
    # ``v7`` is an admitted raw event, while chart readiness is a property of
    # every bar's complete BB-history window.
    evidence["v7_ready"] = bb.v7_ready.reindex(evidence.index).fillna(False).astype(bool)
    return built, evidence


def _evidence_row(row: pd.Series) -> dict[str, object]:
    """Serialize the fixed V9 gate evidence without outcome or fill fields."""
    scheduled = row.get("scheduled_open_utc")
    return {
        "raw_side": int(row["side"]),
        "v7": bool(row["v7"]),
        "v8": bool(row["v8"]),
        "v9": bool(row["v9"]),
        "v9_reason": str(row["v9_reason"]),
        "rope_distance_atr": _json_number(row["rope_distance_atr"]),
        "base_asset": row.get("base_asset"),
        "volume_ratio": _json_number(row.get("volume_ratio")),
        "scheduled_open_utc": scheduled.isoformat() if isinstance(scheduled, pd.Timestamp) and not pd.isna(scheduled) else None,
        "risk_status": str(row["risk_status"]),
        "risk_basis": str(row["risk_basis"]),
    }


def analyze(candles: list[dict], higher: list[dict] | None, timeframe: str, *, tick: float,
            base_asset: str | None, chart_limit: int | None = None) -> AnalysisResult:
    """Return V9 confirmation observations for a caller-supplied closed prefix.

    ``higher`` is accepted for the monitor adapter shape but V9 has no
    higher-timeframe input.  The chart limit changes only chart serialization;
    the complete supplied prefix still determines V6--V9 evidence and events.
    A missing or unknown ``base_asset`` fails V9's explicit base gate.
    """
    del higher
    if chart_limit is not None and (type(chart_limit) is not int or chart_limit < 1):
        raise ValueError("invalid chart_limit")
    frame = _validate_frame(candles, timeframe)
    if frame.empty:
        return AnalysisResult({
            "events": [], "chart": [],
            "state": {"phase": "loading", "ready": False, "bars": 0, "timeframe": timeframe,
                      "direction": "both", "performance": "not_tracked"},
            "protocol": dict(PROTOCOL),
        })
    built, evidence = _decision_frame(frame, timeframe, tick=tick, base_asset=base_asset)
    step = TIMEFRAMES[timeframe]
    times = built.index.asi8 // 1_000_000
    start = max(0, len(built) - chart_limit) if chart_limit is not None else 0
    chart: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    chart_values = {name: built[name].to_numpy(copy=False) for name in
                    ("open", "high", "low", "close", "volume", "s20", "e20", "s60", "e60",
                     "s120", "e120", "md", "sb")}
    ready = evidence.v7_ready.to_numpy(dtype=bool, copy=False)
    admitted = (evidence.v9.to_numpy(dtype=bool, copy=False)
                & evidence.risk_status.eq("known").to_numpy(dtype=bool, copy=False))
    side_values = evidence.side.to_numpy(dtype=int, copy=False)
    for i in range(start, len(built)):
        burst = bool(admitted[i])
        chart.append({
            "t": int(times[i]), "o": float(chart_values["open"][i]), "h": float(chart_values["high"][i]),
            "l": float(chart_values["low"][i]), "c": float(chart_values["close"][i]),
            "v": float(chart_values["volume"][i]),
            "sma20": _json_number(chart_values["s20"][i]), "ema20": _json_number(chart_values["e20"][i]),
            "sma60": _json_number(chart_values["s60"][i]), "ema60": _json_number(chart_values["e60"][i]),
            "sma120": _json_number(chart_values["s120"][i]), "ema120": _json_number(chart_values["e120"][i]),
            "md": _json_number(chart_values["md"][i]), "sb": _json_number(chart_values["sb"][i]),
            "ready": bool(ready[i]), "burst": burst,
            "burst_up": bool(burst and side_values[i] == 1),
            "burst_down": bool(burst and side_values[i] == -1),
        })
    # A V9 gate can pass while its five-bar reference risk is unavailable.  Such
    # a row is evidence only: suppressing it avoids a fake zero-risk signal card.
    for i in np.flatnonzero(admitted):
        gate = evidence.iloc[i]
        side = "long" if side_values[i] == 1 else "short"
        close_ms = int(times[i]) + step
        events.append({
                "protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": "live",
                "confirmation": "raw", "direction": side, "side": side,
                "strategy_version": V9_STRATEGY_VERSION, "v9_admitted": True,
                "timeframe": timeframe, "timeframe_min": step // 60_000,
                "bar_open_ms": int(times[i]), "bar_close_ms": close_ms,
                "signal_close_time": close_ms, "is_closed": True,
                "price": float(gate.reference_price), "risk": float(gate.reference_initial_risk),
                "initial_stop": float(gate.reference_initial_stop),
                "reference_price": float(gate.reference_price),
                "reference_initial_risk": float(gate.reference_initial_risk),
                "reference_initial_stop": float(gate.reference_initial_stop),
                "reference_cost_r": float(gate.reference_cost_r),
                "base_asset": gate.base_asset, "volume_ratio": float(gate.volume_ratio),
                "rope_distance_atr": float(gate.rope_distance_atr),
                "risk_basis": str(gate.risk_basis), "risk_status": str(gate.risk_status),
                "source_sha256": V9_SOURCE_SHA256, "ready": True, "confirmed": True,
                "entry_reference": "confirmation_close_reference_not_fill",
                "executable_entry_time": None, "is_trade": False,
                "performance": "not_tracked", "v9_evidence": _evidence_row(gate),
            })
    latest = evidence.iloc[-1]
    # ``v7`` is intentionally false on a non-signal bar, so retain the V7
    # diagnostic readiness calculated with this exact prefix above.
    v7_ready = bool(built.attrs["v7_ready_at_end"])
    state = {
        "phase": "ready" if v7_ready and len(built) >= V7_MINIMUM_WARMUP_BARS else "loading",
        "ready": bool(v7_ready and len(built) >= V7_MINIMUM_WARMUP_BARS),
        "bars": len(built), "timeframe": timeframe, "direction": "both",
        "bar_open_ms": int(times[-1]), "bar_close_ms": int(times[-1]) + step,
        "price": float(built.close.iloc[-1]), "protocol": SIGNAL_PROTOCOL,
        "source_sha256": V9_SOURCE_SHA256, "performance": "not_tracked",
        "base_asset": latest.base_asset,
        "v9_evidence": _evidence_row(latest),
    }
    return AnalysisResult({"events": events, "chart": chart, "state": state,
                           "protocol": dict(PROTOCOL)}, event_performance={})
