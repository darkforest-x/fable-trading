"""Causal live adapter for the frozen long-only SPIKE Burst V1 Pine replay.

Only confirmed, aligned 30m/1H/4H bars are accepted.  The V1 source emits a
signal at a bar close; this monitor records that close as raw signal evidence,
never a fill.  There is no mirrored short signal and no outcome lookup.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256, features, replay
from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES

WARMUP = 340
PROTOCOL = {"version": SIGNAL_PROTOCOL, "source": "yoyo/evaluation/pine/spike_burst_v1.pine",
            "source_sha256": SOURCE_SHA256, "direction": "long_only", "signal": "confirmed_bar_close",
            "entry_reference": "next_bar_open_not_known_at_signal", "warmup_bars": WARMUP}


def _json_number(value):
    """Keep an unavailable pre-warmup feature explicit in persisted chart JSON."""
    number = float(value)
    return number if np.isfinite(number) else None


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
        return {"events": [], "chart": [], "state": {"phase":"loading","ready":False,"bars":0,"timeframe":timeframe}, "protocol":dict(PROTOCOL)}
    frame = pd.DataFrame([x[1] for x in rows], index=pd.DatetimeIndex([x[0] for x in rows]))
    frame.columns = ["open", "high", "low", "close", "volume"]
    feature_frame = features(frame)
    replayed = replay(feature_frame, float(tick))
    if chart_limit is not None and (type(chart_limit) is not int or chart_limit < 1):
        raise ValueError("invalid chart_limit")
    chart_start = max(0, len(frame) - chart_limit) if chart_limit is not None else 0
    chart, events = [], []
    # Pull immutable V1 outputs once.  Repeated Series ``.iloc`` calls made
    # the monitor spend most of a cell replay in pandas indexing rather than
    # the frozen feature/replay functions; these arrays retain every input and
    # every bar's value exactly while avoiding display-loop reconstruction.
    times = frame.index.asi8 // 1_000_000
    ohlcv = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float, copy=False)
    feature_values = {name: feature_frame[name].to_numpy(copy=False) for name in
                      ("md", "s20", "e20", "s60", "e60", "s120", "e120", "ready", "rv", "expansion")}
    replay_values = {name: replayed[name].to_numpy(copy=False) for name in ("burst", "burst_up", "risk_valid", "risk", "initial_stop")}
    for i, stamp in enumerate(times):
        close_ms = int(stamp) + step
        o, h, l, c, v = ohlcv[i]
        if i >= chart_start:
            chart.append({"t": int(stamp), "o":o,"h":h,"l":l,"c":c,"v":v,
                          "md":_json_number(feature_values["md"][i]), "sma20":_json_number(feature_values["s20"][i]), "ema20":_json_number(feature_values["e20"][i]),
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
    state={"phase":"ready","ready":bool(feature_values["ready"][-1]),"bars":len(frame),"timeframe":timeframe,
           "bar_open_ms":chart[-1]["t"],"bar_close_ms":chart[-1]["t"]+step,"price":chart[-1]["c"],"direction":"long_only",
           "protocol":SIGNAL_PROTOCOL,"source_sha256":SOURCE_SHA256}
    return {"events":events,"chart":chart,"state":state,"protocol":dict(PROTOCOL)}
