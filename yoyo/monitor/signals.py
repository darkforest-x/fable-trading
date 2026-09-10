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


def analyze(candles: list[dict], higher: list[dict] | None, timeframe: str, *, tick: float) -> dict:
    """Return V1 raw events and chart data using no bar after each signal close."""
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
    chart, events = [], []
    for i, (ts, row) in enumerate(frame.iterrows()):
        close_ms = int(ts.value // 1_000_000) + step
        r = replayed.iloc[i]
        chart.append({"t": int(ts.value // 1_000_000), "o":row.open,"h":row.high,"l":row.low,"c":row.close,"v":row.volume,
                      "md":float(feature_frame.md.iloc[i]), "burst":bool(r.burst), "ready":bool(feature_frame.ready.iloc[i])})
        if bool(r.burst_up) and bool(r.risk_valid):
            events.append({"protocol":SIGNAL_PROTOCOL,"kind":SIGNAL_KIND,"source":"live","confirmation":"raw","direction":"long",
                           "side":"long","timeframe":timeframe,"timeframe_min":step//60000,"bar_open_ms":int(ts.value//1_000_000),
                           "bar_close_ms":close_ms,"signal_close_time":pd.Timestamp(close_ms,unit="ms",tz="UTC").isoformat(),"is_closed":True,
                           "price":float(row.close),"risk":float(r.risk),"initial_stop":float(r.initial_stop),"source_sha256":SOURCE_SHA256,
                           "entry_reference":"next_open","executable_entry_time":None,"ready":True,"confirmed":True,
                           "volume_ratio":float(replayed.rv.iloc[i]),"tr_atr_expansion":float(replayed.expansion.iloc[i])})
    state={"phase":"ready","ready":bool(feature_frame.ready.iloc[-1]),"bars":len(frame),"timeframe":timeframe,
           "bar_open_ms":chart[-1]["t"],"bar_close_ms":chart[-1]["t"]+step,"price":chart[-1]["c"],"direction":"long_only",
           "protocol":SIGNAL_PROTOCOL,"source_sha256":SOURCE_SHA256}
    return {"events":events,"chart":chart,"state":state,"protocol":dict(PROTOCOL)}
