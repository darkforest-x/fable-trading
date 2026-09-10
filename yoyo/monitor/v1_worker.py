"""Isolated public-data V1 scanner; it never creates notification outbox rows."""
from __future__ import annotations
import time
from pathlib import Path
from yoyo.monitor import MONITORED_TIMEFRAMES, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.okx import OKX
from yoyo.monitor.model_gate import pending_proof
from yoyo.monitor.notification_policy import arm_v1_bark, delivery_error
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store, now_ms


class V1Scanner:
    """Keep the public client and complete recurrence history across passes."""
    def __init__(self, database: str):
        self.store = Store(Path(database))
        self.client = OKX()
        self.instruments = []
        self.universe_at = 0
        self.candles: dict[tuple[str, str], list[dict]] = {}

    def scan_once(self) -> None:
        """Fetch confirmed bars, replay only changed closed candles, persist read models.

        This runs in its own process so pandas/Pine replay cannot starve FastAPI.
        Raw events are journalled with ``bark_notify=False``; a later explicit
        delivery phase may arm only newly closed live signals after its cutover.
        """
        store, client = self.store, self.client
        started = now_ms(); client.synchronize()
        arm_v1_bark(store, client.clock())
        if not self.instruments or started - self.universe_at >= 3_600_000:
            self.instruments = client.instruments()
            self.universe_at = started
            store.set_meta("universe", {"count": len(self.instruments), "scope": "OKX all live SWAP", "updated_at_ms": started})
        instruments = self.instruments
        scan = {"status":"scanning","started_at_ms":started,"finished_at_ms":None,"completed":0,
                "total":len(instruments)*len(MONITORED_TIMEFRAMES),"errors":0,"error_samples":[],"isolated":True}
        store.set_meta("scan", scan)
        for instrument in instruments:
            symbol = instrument["instId"]
            for timeframe in MONITORED_TIMEFRAMES:
                try:
                    cell = (symbol, timeframe)
                    candles, gaps = client.candles(symbol, timeframe, previous=self.candles.get(cell), limit=720)
                    self.candles[cell] = candles
                    close = candles[-1]["t"] + TIMEFRAMES[timeframe] if candles else None
                    key = f"v1:last_closed:{symbol}:{timeframe}"
                    unchanged = close is not None and store.get_meta(key) == close
                    if not candles:
                        raise RuntimeError("market_data_unavailable")
                    if not unchanged:
                        result = analyze(candles, [], timeframe, tick=float(instrument["tickSz"]))
                        for event in result["events"]:
                            event.update(symbol=symbol, venue="okx", detected_at_ms=now_ms())
                            # Raw V1 is its own Bark stage when a separately
                            # armed direct policy permits this newly closed bar.
                            raw_bark = delivery_error(store, event, client.clock(), "bark") is None
                            store.upsert_event(event, bark_notify=raw_bark)
                            if 0 <= client.clock() - event["bar_close_ms"] <= 9 * TIMEFRAMES[timeframe]:
                                store.register_candidate(event, pending_proof(event))
                        state = dict(result["state"], symbol=symbol, venue="okx", active=True, stale=False,
                                     gap_count=gaps, available_bars=len(candles), tick_size=instrument["tickSz"],
                                     chart=result["chart"][-240:], events=result["events"][-100:])
                        store.upsert_market(state); store.set_meta(key, close)
                except Exception as exc:
                    scan["errors"] += 1
                    if len(scan["error_samples"]) < 8: scan["error_samples"].append({"symbol":symbol,"timeframe":timeframe,"error":type(exc).__name__})
                scan["completed"] += 1
                # Durable per-cell progress: a slow first pass is never reported as zero.
                store.set_meta("scan", scan)
        scan.update(status="degraded" if scan["errors"] else "idle", finished_at_ms=now_ms(),
                    duration_seconds=round((now_ms()-started)/1000,2), next_scan_ms=now_ms()+120000)
        store.set_meta("scan", scan)


def scan_once(database: str) -> None:
    """One isolated pass kept for focused tests and manual diagnostics."""
    V1Scanner(database).scan_once()


def scan_forever(database: str, interval_seconds: int = 120) -> None:
    """Run persistent V1 passes without re-fetching/replaying unchanged cells."""
    scanner = V1Scanner(database)
    while True:
        scanner.scan_once()
        time.sleep(interval_seconds)
