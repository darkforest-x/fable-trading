"""Isolated public-data V9 scanner; only new forward V9 raw rows may seed Bark."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import math
import os
import time
from pathlib import Path
from yoyo.monitor import MONITORED_TIMEFRAMES, SIGNAL_PROTOCOL, TIMEFRAMES, V9_RESET_KEY
from yoyo.monitor.okx import OKX
from yoyo.monitor.model_gate import pending_proof
from yoyo.monitor.notification_policy import arm_v9_bark, delivery_error
from yoyo.monitor.v9_signals import analyze
from yoyo.monitor.store import Store, now_ms


def instrument_base(instrument):
    """Use OKX's underlying metadata, never a substring of an instrument ticker."""
    import re
    explicit = instrument.get("baseCcy")
    underlying = instrument.get("uly")
    if not isinstance(underlying, str) or not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+", underlying):
        return explicit if isinstance(explicit, str) and re.fullmatch(r"[A-Z0-9]+", explicit) else None
    base = underlying.split("-")[0]
    return None if explicit and explicit != base else base


def _valid_checkpoint(candles: object, timeframe: str) -> bool:
    """Accept only a complete, contiguous raw seed; malformed cache fails cold."""
    if timeframe not in TIMEFRAMES or not isinstance(candles, list) or not candles:
        return False
    previous = None
    for row in candles:
        if not isinstance(row, dict):
            return False
        try:
            raw_stamp = row["t"]
            if isinstance(raw_stamp, bool) or not isinstance(raw_stamp, int):
                return False
            stamp = raw_stamp
            o, h, l, c, v = (float(row[key]) for key in ("o", "h", "l", "c", "v"))
        except (KeyError, TypeError, ValueError):
            return False
        if (stamp % TIMEFRAMES[timeframe] or (previous is not None and stamp - previous != TIMEFRAMES[timeframe])
                or not all(math.isfinite(value) for value in (o, h, l, c, v)) or l <= 0 or v < 0
                or h < max(o, l, c) or l > min(o, h, c)):
            return False
        previous = stamp
    return True


class V9Scanner:
    """Keep the public client and complete recurrence history across passes."""
    def __init__(self, database: str, generation: str | None = None):
        self.store = Store(Path(database))
        self.generation = generation
        self.worker_started_at_ms = now_ms()
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
        started = now_ms()
        # Publish this process before synchronization or universe discovery.
        # A parent reload can otherwise expose the previous worker's completed
        # scan meta while this new worker is still preparing its first pass.
        scan = {"status": "starting", "generation": self.generation,
                "worker_pid": os.getpid(), "worker_started_at_ms": self.worker_started_at_ms,
                "started_at_ms": started, "finished_at_ms": None, "completed": 0,
                "total": 0, "errors": 0, "error_samples": [], "isolated": True}
        store.set_meta("scan", scan)
        # Migration runs once per worker before any HTTP overview reads.  The
        # selected-chart table remains the only source of legacy full payloads.
        if not getattr(self, "_summary_backfilled", False):
            store.set_meta("market_summary_backfill", {"status": "running", "generation": self.generation})
            receipt = store.backfill_market_summaries()
            store.set_meta("market_summary_backfill", {"status": "complete", "generation": self.generation, **receipt})
            self._summary_backfilled = True
        client.synchronize()
        reset = store.get_meta(V9_RESET_KEY)
        if not isinstance(reset, dict) or type(reset.get("activated_ms")) is not int:
            raise RuntimeError("v9_explicit_reset_required")
        cutoff = reset["activated_ms"]
        arm_v9_bark(store, client.clock())
        if not self.instruments or started - self.universe_at >= 3_600_000:
            self.instruments = client.instruments()
            self.universe_at = started
            store.set_meta("universe", {"count": len(self.instruments), "scope": "OKX all live SWAP", "updated_at_ms": started})
        instruments = self.instruments
        scan.update(status="scanning", total=len(instruments) * len(MONITORED_TIMEFRAMES))
        store.set_meta("scan", scan)
        cells = [(instrument, timeframe) for instrument in instruments for timeframe in MONITORED_TIMEFRAMES]
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="v9-okx") as pool:
            pending = {}
            cell_iter = iter(cells)

            def fetch(symbol, timeframe, previous):
                """Time public fetch only; worker state remains main-thread-owned."""
                started_at = time.monotonic()
                candles, gaps = client.candles(symbol, timeframe, previous=previous, limit=720)
                return candles, gaps, round((time.monotonic() - started_at) * 1000, 3)

            def submit_next():
                try:
                    instrument, timeframe = next(cell_iter)
                except StopIteration:
                    return False
                symbol = instrument["instId"]
                cell = (symbol, timeframe)
                # The worker only fetches public OHLCV.  The main thread owns
                # recurrence state, Pine replay and all SQLite writes.
                pending[cell] = (instrument, timeframe, pool.submit(fetch, symbol, timeframe, self.candles.get(cell)))
                return True

            for _ in range(min(8, len(cells))):
                submit_next()
            for instrument, timeframe in cells:
                symbol = instrument["instId"]
                cell = (symbol, timeframe)
                fetch_ms = analyze_ms = checkpoint_ms = 0.0
                analyze_cpu_ms = checkpoint_cpu_ms = 0.0
                unchanged = None
                has_candles = False
                try:
                    _, _, future = pending.pop(cell)
                    candles, gaps, fetch_ms = future.result()
                    has_candles = bool(candles)
                    self.candles[cell] = candles
                    close = candles[-1]["t"] + TIMEFRAMES[timeframe] if candles else None
                    key = f"v9:last_closed:{symbol}:{timeframe}"
                    unchanged = close is not None and store.get_meta(key) == close
                    if not candles:
                        raise RuntimeError("market_data_unavailable")
                    # Checkpoint before replay/market writes.  A later process
                    # restart may fetch only the exchange delta with this exact
                    # sequence, but no invalid payload is ever restored.  Do
                    # not rewrite an unchanged compressed seed every pass.
                    if not unchanged or cell not in self._checkpointed:
                        checkpoint_started = time.monotonic()
                        checkpoint_cpu_started = time.thread_time()
                        store.save_candle_checkpoint(symbol, timeframe, candles)
                        checkpoint_ms = round((time.monotonic() - checkpoint_started) * 1000, 3)
                        checkpoint_cpu_ms = round((time.thread_time() - checkpoint_cpu_started) * 1000, 3)
                        self._checkpointed.add(cell)
                    if not unchanged:
                        analyze_started = time.monotonic()
                        analyze_cpu_started = time.thread_time()
                        result = analyze(candles, [], timeframe, tick=float(instrument["tickSz"]),
                                         base_asset=instrument_base(instrument), chart_limit=240)
                        analyze_ms = round((time.monotonic() - analyze_started) * 1000, 3)
                        analyze_cpu_ms = round((time.thread_time() - analyze_cpu_started) * 1000, 3)
                        # Warmup may calculate old decisions, but they cannot
                        # return to either the event journal or chart markers.
                        events = [dict(event) for event in result["events"] if event["bar_close_ms"] > cutoff]
                        events.sort(key=lambda event: (event["bar_close_ms"], event["side"], event["kind"]))
                        for event in events:
                            event.update(symbol=symbol, venue="okx", detected_at_ms=now_ms())
                            # Raw V9 is its own Bark stage when a separately
                            # armed direct policy permits this newly closed bar.
                            raw_bark = delivery_error(store, event, client.clock(), "bark") is None
                            inserted = store.upsert_event(event, bark_notify=raw_bark)
                            # YOLO is only an extra stage for a raw event that
                            # was eligible to notify at registration time. A
                            # cold scan can rediscover an old closed bar within
                            # nine TF bars; retain that raw history but never
                            # load/infer it as if it were a new live candidate.
                            if inserted and raw_bark:
                                store.register_candidate(event, pending_proof(event))
                            # Later closed bars move an open card's projection.
                            # This narrow payload merge never reopens the
                            # immutable insert path or seeds an outbox.
                            if not inserted and isinstance(event.get("performance"), dict):
                                store.update_event_payload(store.event_id(event),
                                                           {"performance": event["performance"]})
                        state = dict(result["state"], symbol=symbol, venue="okx", active=True, stale=False,
                                     gap_count=gaps, available_bars=len(candles), tick_size=instrument["tickSz"],
                                     chart=[dict(row, burst=False, burst_up=False, burst_down=False) if row["t"] + TIMEFRAMES[timeframe] <= cutoff else row
                                            for row in result["chart"]], events=events[-100:])
                        store.upsert_market(state); store.set_meta(key, close)
                except Exception as exc:
                    scan["errors"] += 1
                    if len(scan["error_samples"]) < 8: scan["error_samples"].append({"symbol":symbol,"timeframe":timeframe,"error":type(exc).__name__})
                scan["completed"] += 1
                timing = scan.setdefault("timing_ms", {"cells": 0, "changed_cells": 0, "unchanged_cells": 0,
                                                        "fetch_total": 0.0, "analyze_total": 0.0, "checkpoint_total": 0.0,
                                                        "analyze_cpu_total": 0.0, "checkpoint_cpu_total": 0.0,
                                                        "fetch_max": 0.0, "analyze_max": 0.0, "checkpoint_max": 0.0,
                                                        "analyze_cpu_max": 0.0, "checkpoint_cpu_max": 0.0})
                timing["cells"] += 1
                if unchanged is True:
                    timing["unchanged_cells"] += 1
                elif unchanged is False and has_candles:
                    timing["changed_cells"] += 1
                for name, value in (("fetch", fetch_ms), ("analyze", analyze_ms), ("checkpoint", checkpoint_ms),
                                    ("analyze_cpu", analyze_cpu_ms), ("checkpoint_cpu", checkpoint_cpu_ms)):
                    timing[name + "_total"] = round(timing[name + "_total"] + value, 3)
                    timing[name + "_max"] = max(timing[name + "_max"], value)
                # Durable per-cell progress: a slow first pass is never reported as zero.
                store.set_meta("scan", scan)
                submit_next()
        scan.update(status="degraded" if scan["errors"] else "idle", finished_at_ms=now_ms(),
                    duration_seconds=round((now_ms()-started)/1000,2), next_scan_ms=now_ms()+120000)
        store.set_meta("scan", scan)


def scan_once(database: str) -> None:
    """One isolated pass kept for focused tests and manual diagnostics."""
    V9Scanner(database).scan_once()


def scan_forever(database: str, interval_seconds: int = 120, generation: str | None = None) -> None:
    """Run persistent V9 passes without re-fetching/replaying unchanged cells."""
    scanner = V9Scanner(database, generation=generation)
    while True:
        scanner.scan_once()
        time.sleep(interval_seconds)
