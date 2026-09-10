"""Isolated public-data V1 scanner; only new forward V1 raw rows may seed Bark."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import math
import time
from pathlib import Path
from yoyo.monitor import MONITORED_TIMEFRAMES, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.okx import OKX
from yoyo.monitor.model_gate import pending_proof
from yoyo.monitor.notification_policy import arm_v1_bark, delivery_error
from yoyo.monitor.signals import analyze
from yoyo.monitor.store import Store, now_ms


def _valid_checkpoint(candles: object, timeframe: str) -> bool:
    """Accept only a complete, contiguous raw seed; malformed cache fails cold."""
    if timeframe not in TIMEFRAMES or not isinstance(candles, list) or not candles:
        return False
    previous = None
    for row in candles:
        if not isinstance(row, dict):
            return False
        try:
            stamp = int(row["t"])
            o, h, l, c, v = (float(row[key]) for key in ("o", "h", "l", "c", "v"))
        except (KeyError, TypeError, ValueError):
            return False
        if (stamp % TIMEFRAMES[timeframe] or (previous is not None and stamp - previous != TIMEFRAMES[timeframe])
                or not all(math.isfinite(value) for value in (o, h, l, c, v)) or l <= 0 or v < 0
                or h < max(o, l, c) or l > min(o, h, c)):
            return False
        previous = stamp
    return True


class V1Scanner:
    """Keep the public client and complete recurrence history across passes."""
    def __init__(self, database: str):
        self.store = Store(Path(database))
        self.client = OKX()
        self.instruments = []
        self.universe_at = 0
        # A restart only reuses exact raw sequences.  Do not seed recurrence
        # from the shorter UI chart or a gapped/corrupt checkpoint.
        self.candles: dict[tuple[str, str], list[dict]] = {
            key: value for key, value in self.store.load_candle_checkpoints().items()
            if _valid_checkpoint(value, key[1])
        }
        self._checkpointed = set(self.candles)

    def scan_once(self) -> None:
        """Fetch confirmed bars, replay only changed closed candles, persist read models.

        This runs in its own process so pandas/Pine replay cannot starve FastAPI.
        Raw events may seed their own Bark stage only after the persisted
        forward cutover; historical or replay rows never acquire a receipt.
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
        cells = [(instrument, timeframe) for instrument in instruments for timeframe in MONITORED_TIMEFRAMES]
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="v1-okx") as pool:
            pending = {}
            cell_iter = iter(cells)

            def submit_next():
                try:
                    instrument, timeframe = next(cell_iter)
                except StopIteration:
                    return False
                symbol = instrument["instId"]
                cell = (symbol, timeframe)
                # The worker only fetches public OHLCV.  The main thread owns
                # recurrence state, Pine replay and all SQLite writes.
                pending[cell] = (instrument, timeframe, pool.submit(
                    client.candles, symbol, timeframe, previous=self.candles.get(cell), limit=720))
                return True

            for _ in range(min(8, len(cells))):
                submit_next()
            for instrument, timeframe in cells:
                symbol = instrument["instId"]
                cell = (symbol, timeframe)
                try:
                    _, _, future = pending.pop(cell)
                    candles, gaps = future.result()
                    self.candles[cell] = candles
                    close = candles[-1]["t"] + TIMEFRAMES[timeframe] if candles else None
                    key = f"v1:last_closed:{symbol}:{timeframe}"
                    unchanged = close is not None and store.get_meta(key) == close
                    if not candles:
                        raise RuntimeError("market_data_unavailable")
                    # Checkpoint before replay/market writes.  A later process
                    # restart may fetch only the exchange delta with this exact
                    # sequence, but no invalid payload is ever restored.  Do
                    # not rewrite an unchanged compressed seed every pass.
                    if not unchanged or cell not in self._checkpointed:
                        store.save_candle_checkpoint(symbol, timeframe, candles)
                        self._checkpointed.add(cell)
                    if not unchanged:
                        result = analyze(candles, [], timeframe, tick=float(instrument["tickSz"]), chart_limit=240)
                        for event in result["events"]:
                            event.update(symbol=symbol, venue="okx", detected_at_ms=now_ms())
                            # Raw V1 is its own Bark stage when a separately
                            # armed direct policy permits this newly closed bar.
                            raw_bark = delivery_error(store, event, client.clock(), "bark") is None
                            store.upsert_event(event, bark_notify=raw_bark)
                            # YOLO is only an extra stage for a raw event that
                            # was eligible to notify at registration time. A
                            # cold scan can rediscover an old closed bar within
                            # nine TF bars; retain that raw history but never
                            # load/infer it as if it were a new live candidate.
                            if raw_bark:
                                store.register_candidate(event, pending_proof(event))
                        state = dict(result["state"], symbol=symbol, venue="okx", active=True, stale=False,
                                     gap_count=gaps, available_bars=len(candles), tick_size=instrument["tickSz"],
                                     chart=result["chart"], events=result["events"][-100:])
                        store.upsert_market(state); store.set_meta(key, close)
                except Exception as exc:
                    scan["errors"] += 1
                    if len(scan["error_samples"]) < 8: scan["error_samples"].append({"symbol":symbol,"timeframe":timeframe,"error":type(exc).__name__})
                scan["completed"] += 1
                # Durable per-cell progress: a slow first pass is never reported as zero.
                store.set_meta("scan", scan)
                submit_next()
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
