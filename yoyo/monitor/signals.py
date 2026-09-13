"""Causal SPIKE Burst V1 adapters with independent long and short replay state.

Only confirmed, aligned 15m/30m/1H/4H bars are accepted.  The V1 source emits a
signal at a bar close; this monitor records that close as raw signal evidence,
never a fill.  The frozen long adapter is unchanged; the short display adapter
replays Pine's short setting separately and never enters notification delivery.
Closed bars after an event may update display-only V1 path state; that state
never feeds signal generation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256, features, replay
from yoyo.monitor import (SHORT_SIGNAL_KIND, SHORT_SIGNAL_PROTOCOL, SIGNAL_KIND,
                          SIGNAL_PROTOCOL, TIMEFRAMES, TV_SHORT_PROFILE_ID)
from yoyo.monitor.v1_short_replay import replay_short

WARMUP = 340
PROTOCOL = {"version": SIGNAL_PROTOCOL, "source": "yoyo/evaluation/pine/spike_burst_v1.pine",
            "source_sha256": SOURCE_SHA256, "direction": "long_only", "signal": "confirmed_bar_close",
            "entry_reference": "next_bar_open_not_known_at_signal", "warmup_bars": WARMUP}


class AnalysisResult(dict):
    """Frozen JSON result plus a non-serialized mutable card projection."""

    __slots__ = ("event_performance",)

    def __init__(self, payload: dict, event_performance: dict | None = None):
        super().__init__(payload)
        self.event_performance = event_performance or {}


def _json_number(value):
    """Keep an unavailable pre-warmup feature explicit in persisted chart JSON."""
    number = float(value)
    return number if np.isfinite(number) else None


def _path_performance(replayed: pd.DataFrame, times: np.ndarray, step: int,
                      position: int, next_position: int, *, basis: str = "v1_signal_close_reference") -> dict:
    """Summarize V1's closed-bar path after one signal without feeding it back.

    Columns used are replay outputs ``exit``, ``exit_price``, ``current_r``,
    ``peak_r``, ``protection``, ``active_protection`` and ``trail_armed`` from
    the signal bar through the bar before the next signal.  These are outcome
    display fields only; signal generation never reads this dictionary.
    """
    segment = replayed.iloc[position:next_position]
    exits = np.flatnonzero(segment["exit"].to_numpy(dtype=bool, copy=False))
    stopped = bool(len(exits))
    final_offset = int(exits[0]) if stopped else len(segment) - 1
    final_position = position + final_offset
    final = replayed.iloc[final_position]

    current_r = _json_number(final["current_r"])
    peak_r = _json_number(final["peak_r"])
    exit_r = current_r if stopped else None
    if stopped:
        status = "profit" if current_r is not None and current_r > 1e-9 else "loss" if current_r is not None and current_r < -1e-9 else "breakeven"
        stop_price = _json_number(final["active_protection"])
    else:
        status = "active"
        stop_price = _json_number(final["protection"])
    return {
        "status": status,
        "stop_triggered": stopped,
        "current_r": current_r,
        "peak_r": peak_r,
        "exit_r": exit_r,
        "exit_price": _json_number(final["exit_price"]) if stopped else None,
        "stop_price": stop_price,
        "trailing_active": bool(final["trail_armed"]),
        "bars_held": final_position - position,
        "updated_at_ms": int(times[final_position]) + step,
        "exit_time_ms": int(times[final_position]) + step if stopped else None,
        "basis": basis,
    }


def analyze(candles: list[dict], higher: list[dict] | None, timeframe: str, *, tick: float,
            chart_limit: int | None = None) -> dict:
    """Return causal V1 events and an optional trailing chart window.

    Features and replay always consume the full supplied history.  ``chart_limit``
    only avoids materializing display records which the persistent worker would
    immediately discard; events and state remain computed from every bar.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported monitored timeframe")
    step = TIMEFRAMES[timeframe]
    rows = []
    prior = None
    for i, row in enumerate(candles):
        try:
            t = int(row["t"]); values = {k: float(row[k]) for k in ("o", "h", "l", "c", "v")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid candle") from exc
        if prior is not None and t - prior != step:
            raise ValueError("candles must be continuous")
        if t % step or not np.isfinite(list(values.values())).all() or values["l"] <= 0 or values["v"] < 0:
            raise ValueError("invalid candle")
        if values["h"] < max(values["o"], values["c"], values["l"]) or values["l"] > min(values["o"], values["c"], values["h"]):
            raise ValueError("invalid candle")
        rows.append((pd.Timestamp(t, unit="ms", tz="UTC"), values)); prior = t
    if not rows:
        return AnalysisResult({"events": [], "chart": [],
                               "state": {"phase":"loading","ready":False,"bars":0,"timeframe":timeframe},
                               "protocol":dict(PROTOCOL)})
    frame = pd.DataFrame([x[1] for x in rows], index=pd.DatetimeIndex([x[0] for x in rows]))
    frame.columns = ["open", "high", "low", "close", "volume"]
    feature_frame = features(frame)
    replayed = replay(feature_frame, float(tick))
    if chart_limit is not None and (type(chart_limit) is not int or chart_limit < 1):
        raise ValueError("invalid chart_limit")
    chart_start = max(0, len(frame) - chart_limit) if chart_limit is not None else 0
    chart, events, event_positions, event_performance = [], [], [], {}
    # Pull immutable V1 outputs once.  Repeated Series ``.iloc`` calls made
    # the monitor spend most of a cell replay in pandas indexing rather than
    # the frozen feature/replay functions; these arrays retain every input and
    # every bar's value exactly while avoiding display-loop reconstruction.
    times = frame.index.asi8 // 1_000_000
    ohlcv = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float, copy=False)
    feature_values = {name: feature_frame[name].to_numpy(copy=False) for name in
                      ("md", "sb", "s20", "e20", "s60", "e60", "s120", "e120", "ready", "rv", "expansion")}
    replay_values = {name: replayed[name].to_numpy(copy=False) for name in ("burst", "burst_up", "risk_valid", "risk", "initial_stop")}
    for i, stamp in enumerate(times):
        close_ms = int(stamp) + step
        o, h, l, c, v = ohlcv[i]
        if i >= chart_start:
            chart.append({"t": int(stamp), "o":o,"h":h,"l":l,"c":c,"v":v,
                          "md":_json_number(feature_values["md"][i]), "sb":_json_number(feature_values["sb"][i]),
                          "sma20":_json_number(feature_values["s20"][i]), "ema20":_json_number(feature_values["e20"][i]),
                          "sma60":_json_number(feature_values["s60"][i]), "ema60":_json_number(feature_values["e60"][i]),
                          "sma120":_json_number(feature_values["s120"][i]), "ema120":_json_number(feature_values["e120"][i]),
                          "burst":bool(replay_values["burst"][i]), "ready":bool(feature_values["ready"][i])})
        if bool(replay_values["burst_up"][i]) and bool(replay_values["risk_valid"][i]):
            events.append({"protocol":SIGNAL_PROTOCOL,"kind":SIGNAL_KIND,"source":"live","confirmation":"raw","direction":"long",
                           "side":"long","timeframe":timeframe,"timeframe_min":step//60000,"bar_open_ms":int(stamp),
                           "bar_close_ms":close_ms,"signal_close_time":close_ms,"is_closed":True,
                           "price":float(c),"risk":float(replay_values["risk"][i]),"initial_stop":float(replay_values["initial_stop"][i]),"source_sha256":SOURCE_SHA256,
                           "entry_reference":"next_open","executable_entry_time":None,"ready":True,"confirmed":True,
                           "volume_ratio":float(feature_values["rv"][i]),"tr_atr_expansion":float(feature_values["expansion"][i])})
            event_positions.append(i)
    for index, (event, position) in enumerate(zip(events, event_positions)):
        next_position = event_positions[index + 1] if index + 1 < len(event_positions) else len(replayed)
        performance = _path_performance(replayed, times, step, position, next_position)
        performance.update(entry_price=event["price"], initial_stop=event["initial_stop"])
        # Outcome state is a mutable read-model keyed by immutable signal time.
        # Keeping it outside ``events`` preserves prefix equality for the
        # causal signal contract while allowing the monitor to refresh cards.
        event_performance[event["bar_close_ms"]] = performance
    state={"phase":"ready","ready":bool(feature_values["ready"][-1]),"bars":len(frame),"timeframe":timeframe,
           "bar_open_ms":chart[-1]["t"],"bar_close_ms":chart[-1]["t"]+step,"price":chart[-1]["c"],"direction":"long_only",
           "protocol":SIGNAL_PROTOCOL,"source_sha256":SOURCE_SHA256}
    return AnalysisResult({"events":events,"chart":chart,"state":state,
                           "protocol":dict(PROTOCOL)}, event_performance)


SHORT_PROTOCOL = {
    "version": SHORT_SIGNAL_PROTOCOL,
    "source": "yoyo/evaluation/pine/spike_burst_v1.pine",
    "source_sha256": SOURCE_SHA256,
    "direction": "short_only",
    "pine_direction_setting": "空头",
    "tradingview_default_direction": "多头",
    "tradingview_default_is_short": False,
    "tv_profile_id": TV_SHORT_PROFILE_ID,
    "signal": "confirmed_bar_close",
    "entry_reference": "next_bar_open_not_known_at_signal",
    "warmup_bars": WARMUP,
}


def analyze_short(candles: list[dict], higher: list[dict] | None, timeframe: str, *, tick: float,
                  chart_limit: int | None = None) -> AnalysisResult:
    """Return independent Pine ``方向 = 空头`` V1 observations.

    This does not alter the frozen long replay or its state.  ``higher`` and
    ``chart_limit`` are accepted for adapter parity; the current V1 Pine source
    reads no higher-timeframe inputs and short events always use the full closed
    prefix regardless of display truncation.
    """
    del higher, chart_limit
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported monitored timeframe")
    step = TIMEFRAMES[timeframe]
    rows = []
    prior = None
    for row in candles:
        try:
            t = int(row["t"]); values = {k: float(row[k]) for k in ("o", "h", "l", "c", "v")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid candle") from exc
        if prior is not None and t - prior != step:
            raise ValueError("candles must be continuous")
        if t % step or not np.isfinite(list(values.values())).all() or values["l"] <= 0 or values["v"] < 0:
            raise ValueError("invalid candle")
        if values["h"] < max(values["o"], values["c"], values["l"]) or values["l"] > min(values["o"], values["c"], values["h"]):
            raise ValueError("invalid candle")
        rows.append((pd.Timestamp(t, unit="ms", tz="UTC"), values)); prior = t
    if not rows:
        return AnalysisResult({"events": [], "chart": [],
                               "state": {"phase": "loading", "ready": False, "bars": 0, "timeframe": timeframe,
                                         "direction": "short_only"},
                               "protocol": dict(SHORT_PROTOCOL)})
    frame = pd.DataFrame([x[1] for x in rows], index=pd.DatetimeIndex([x[0] for x in rows]))
    frame.columns = ["open", "high", "low", "close", "volume"]
    feature_frame = features(frame)
    replayed = replay_short(feature_frame, float(tick))
    times = frame.index.asi8 // 1_000_000
    replay_values = {name: replayed[name].to_numpy(copy=False) for name in
                     ("burst_down", "risk_valid", "risk", "initial_stop")}
    events, positions, performance_by_close = [], [], {}
    for i, stamp in enumerate(times):
        if bool(replay_values["burst_down"][i]) and bool(replay_values["risk_valid"][i]):
            close_ms = int(stamp) + step
            events.append({"protocol": SHORT_SIGNAL_PROTOCOL, "kind": SHORT_SIGNAL_KIND,
                           "source": "live", "confirmation": "raw", "direction": "short", "side": "short",
                           "timeframe": timeframe, "timeframe_min": step // 60000, "bar_open_ms": int(stamp),
                           "bar_close_ms": close_ms, "signal_close_time": close_ms, "is_closed": True,
                           "price": float(frame.close.iloc[i]), "risk": float(replay_values["risk"][i]),
                           "initial_stop": float(replay_values["initial_stop"][i]), "source_sha256": SOURCE_SHA256,
                           "pine_direction_setting": "空头", "tradingview_default_direction": "多头",
                           "tradingview_default_is_short": False, "tv_profile_id": TV_SHORT_PROFILE_ID,
                           "display_only": True,
                           "entry_reference": "next_open", "executable_entry_time": None, "ready": True,
                           "confirmed": True, "volume_ratio": float(feature_frame.rv.iloc[i]),
                           "tr_atr_expansion": float(feature_frame.expansion.iloc[i])})
            positions.append(i)
    for index, (event, position) in enumerate(zip(events, positions)):
        next_position = positions[index + 1] if index + 1 < len(positions) else len(replayed)
        performance = _path_performance(replayed, times, step, position, next_position,
                                        basis="v1_short_signal_close_reference")
        performance.update(entry_price=event["price"], initial_stop=event["initial_stop"])
        performance_by_close[event["bar_close_ms"]] = performance
    state = {"phase": "ready", "ready": bool(feature_frame.ready.iloc[-1]), "bars": len(frame),
             "timeframe": timeframe, "direction": "short_only", "protocol": SHORT_SIGNAL_PROTOCOL,
             "source_sha256": SOURCE_SHA256, "pine_direction_setting": "空头",
             "tradingview_default_is_short": False}
    return AnalysisResult({"events": events, "chart": [], "state": state,
                           "protocol": dict(SHORT_PROTOCOL)}, performance_by_close)
