"""Isolated public-data V1 scanner; it never creates notification outbox rows."""
from __future__ import annotations
import time
from pathlib import Path
from yoyo.monitor import MONITORED_TIMEFRAMES, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.okx import OKX
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store, now_ms


def scan_once(database: str) -> None:
    """Fetch confirmed bars, replay only changed closed candles, persist read models.

    This runs in its own process so pandas/Pine replay cannot starve FastAPI.
    Raw events are journalled with ``bark_notify=False``; a later explicit
    delivery phase may arm only newly closed live signals after its cutover.
    """
    store, client = Store(Path(database)), OKX()
    started = now_ms(); client.synchronize(); instruments = client.instruments()
    scan = {"status":"scanning","started_at_ms":started,"finished_at_ms":None,"completed":0,
            "total":len(instruments)*len(MONITORED_TIMEFRAMES),"errors":0,"error_samples":[],"isolated":True}
    store.set_meta("scan", scan)
    for instrument in instruments:
        symbol = instrument["instId"]
        for timeframe in MONITORED_TIMEFRAMES:
            try:
                candles, gaps = client.candles(symbol, timeframe, limit=720)
                close = candles[-1]["t"] + TIMEFRAMES[timeframe] if candles else None
                key = f"v1:last_closed:{symbol}:{timeframe}"
                unchanged = close is not None and store.get_meta(key) == close
                if candles and not unchanged:
                    result = analyze(candles, [], timeframe, tick=float(instrument["tickSz"]))
                    for event in result["events"]:
                        event.update(symbol=symbol, venue="okx", detected_at_ms=now_ms())
                        store.upsert_event(event, bark_notify=False)
                    state = dict(result["state"], symbol=symbol, venue="okx", active=True, stale=False,
                                 gap_count=gaps, available_bars=len(candles), tick_size=instrument["tickSz"],
                                 chart=result["chart"][-240:], events=result["events"][-100:])
                    store.upsert_market(state); store.set_meta(key, close)
                elif candles:
                    prior = next((x for x in store.list_markets() if x.get("symbol")==symbol and x.get("timeframe")==timeframe), {})
                    if prior: prior.update(last_scan_ms=now_ms(), stale=False); store.upsert_market(prior)
                else:
                    raise RuntimeError("market_data_unavailable")
            except Exception as exc:
                scan["errors"] += 1
                if len(scan["error_samples"]) < 8: scan["error_samples"].append({"symbol":symbol,"timeframe":timeframe,"error":type(exc).__name__})
            scan["completed"] += 1
            # Durable per-cell progress: a slow first pass is never reported as zero.
            store.set_meta("scan", scan)
    scan.update(status="degraded" if scan["errors"] else "idle", finished_at_ms=now_ms(),
                duration_seconds=round((now_ms()-started)/1000,2), next_scan_ms=now_ms()+120000)
    store.set_meta("scan", scan)
