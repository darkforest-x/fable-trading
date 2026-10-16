"""Read-only all-market, all-period V13.1 signal-center input.

Only immutable confirmation fields create intents. Display performance is
never consulted. Exit inputs reuse the exact closed OHLCV prefix, ATR and raw
side builder used by v130_signals; no alternative market source or candle writer.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3

from yoyo.monitor import FRESH_MS, TIMEFRAMES
from yoyo.monitor.v130_policy import SIGNAL_KIND, is_v130_signal
from yoyo.monitor.v130_signals import SOURCE_SHA256
from yoyo.research_workspace.paper_source import MonitorSource


class Source:
    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.checkpoints = MonitorSource(runtime)

    def events(self, cutoff_ms, now_ms):
        path = self.runtime / "monitor.sqlite3"
        if not path.is_file() or path.is_symlink():
            raise ValueError("monitor database unavailable")
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = db.execute("""SELECT id,symbol,timeframe,kind,side,close_ms,detected_ms,payload
                                 FROM events WHERE kind=? AND close_ms>? AND detected_ms<=?
                                 ORDER BY close_ms,id""", (SIGNAL_KIND, cutoff_ms, now_ms)).fetchall()
        finally:
            db.close()
        events = []
        for identity, symbol, timeframe, kind, side, close, detected, payload in rows:
            event = json.loads(payload)
            if (not is_v130_signal(event) or event.get("source_sha256") != SOURCE_SHA256
                    or any(event.get(k) != v for k, v in {
                        "symbol": symbol, "timeframe": timeframe, "kind": kind, "side": side,
                        "bar_close_ms": close, "detected_at_ms": detected}.items())
                    or event.get("id", identity) != identity):
                raise ValueError("signal journal identity/strategy mismatch")
            # Keep only immutable fields; no display outcome can leak downstream.
            events.append({k: v for k, v in dict(event, id=identity).items() if k != "performance"})
        return events

    def checkpoint(self, symbol, timeframe, now_ms):
        checkpoint = self.checkpoints.checkpoint(symbol, timeframe, now_ms)
        if (not checkpoint or not checkpoint["candles"]
                or not 0 <= now_ms - checkpoint["updated_ms"] <= FRESH_MS):
            raise ValueError("monitor checkpoint unavailable or stale")
        step = TIMEFRAMES[timeframe]
        last_close = checkpoint["candles"][-1]["t"] + step
        if now_ms - last_close >= step + FRESH_MS:
            raise ValueError("latest completed candle is unavailable")
        return checkpoint

    def bars(self, symbol, timeframe, after_ms, now_ms):
        from yoyo.monitor.v9_signals import _validate_frame, _decision_frame
        cp = self.checkpoint(symbol, timeframe, now_ms)
        step = TIMEFRAMES[timeframe]
        if cp["candles"][-1]["t"] + step <= after_ms:
            return []
        if cp["candles"][0]["t"] > after_ms:
            raise ValueError("checkpoint no longer covers the position history")
        frame = _validate_frame(cp["candles"], timeframe)
        built, evidence = _decision_frame(frame, timeframe, tick=cp["tick"], base_asset=None)
        rows = []
        for i, (stamp, row) in enumerate(built.iterrows()):
            close_ms = int(stamp.value // 1_000_000) + step
            if close_ms <= after_ms:
                continue
            if (built.attrs["data_gap"].iloc[i] or not math.isfinite(float(row["atr"]))
                    or row["atr"] <= 0):
                raise ValueError("management candle has a gap or undefined ATR")
            rows.append(dict(close_ms=close_ms, close=float(row["close"]), atr=float(row["atr"]),
                             raw_side=int(evidence["side"].iloc[i]), tick=cp["tick"]))
        return rows
