"""Read-only owner-selected-universe, all-period V13.1 signal-center input.

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
from yoyo.contracts.signal_universe import exclusion_reason
from .preparation import RetryableRead


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
            if exclusion_reason(symbol, timeframe=timeframe, side=side) is not None:
                continue
            events.append({k: v for k, v in dict(event, id=identity).items() if k != "performance"})
        return events

    def checkpoint(self, symbol, timeframe, now_ms):
        checkpoint = self.checkpoints.checkpoint(symbol, timeframe, now_ms)
        if not checkpoint or not checkpoint["candles"]:
            raise RetryableRead("monitor checkpoint unavailable or stale", "checkpoint_unavailable")
        step = TIMEFRAMES[timeframe]
        last_close = checkpoint["candles"][-1]["t"] + step
        observed_ms = self.checkpoint_observation(symbol, timeframe, checkpoint, last_close)
        # The scanner rewrites a seed only when closed candles change. Its
        # matching successful-read receipt ages independently; both retain the
        # original 30-minute limit. Old writers fall back to seed-write time.
        fresh_ms = checkpoint["updated_ms"] if observed_ms is None else observed_ms
        if not 0 <= now_ms - fresh_ms <= FRESH_MS:
            raise RetryableRead("monitor checkpoint unavailable or stale", "checkpoint_unavailable")
        if now_ms - last_close >= step + FRESH_MS:
            raise RetryableRead("latest completed candle is unavailable", "checkpoint_unavailable")
        return checkpoint

    def checkpoint_observation(self, symbol, timeframe, checkpoint, last_close):
        """Read only a successful observation bound to this exact closed seed."""
        path = self.runtime / "monitor.sqlite3"
        if not path.is_file() or path.is_symlink():
            return None
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = db.execute("SELECT payload FROM meta WHERE key=?",
                             (f"candle_observation:{symbol}:{timeframe}",)).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        receipt = json.loads(row[0])
        if (not isinstance(receipt, dict)
                or any(type(receipt.get(k)) is not int
                       for k in ("observed_ms", "checkpoint_updated_ms", "close_ms"))
                or receipt["checkpoint_updated_ms"] != checkpoint["updated_ms"]
                or receipt["close_ms"] != last_close
                or receipt["observed_ms"] < checkpoint["updated_ms"]):
            return None
        return receipt["observed_ms"]

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
