"""Read-only local cloud signals plus public OKX market data for live-cloud-v17."""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import time
import zlib
from typing import Any

from .cloud_inverse_account import (HOUR_MS, MINUTE_MS, STEP_12H_MS,
                                    CloudInversePaper)
from .cloud_signal_plans import atr_points
from .cloud_signal_rules import first_eligible
from .okx import OKX
from yoyo.data.candle_hub import validate as validate_candle_rows


SIGNAL_BATCH_MAX_DELAY_MS = 15 * MINUTE_MS
TICKER_MAX_AGE_MS = 15_000
RANKABLE_90D_MIN_CANDLES = 182
RANKABLE_UNIVERSE_MIN = 10
HISTORY_12H_LIMIT = 240
UNIVERSE_CACHE_MS = 60 * MINUTE_MS
CANDLE_SERIES_CACHE_MS = 30_000
ENTRY_CLOCK_POLICY = "post-confirmation public ticker; ask for long / bid for short"
EXIT_CLOCK_POLICY = "completed public 1m after quote through entry hour, then completed public 1h"


def inverse_regime(eth: list[dict[str, Any]], btc: list[dict[str, Any]],
                   signal_open_ms: int) -> float | None:
    """ETH/BTC relative return over 60 adjacent 12H intervals (61 completed closes)."""
    et = {int(r["t"]): float(r["c"]) for r in eth}
    bt = {int(r["t"]): float(r["c"]) for r in btc}
    start, end = int(signal_open_ms) - 60 * STEP_12H_MS, int(signal_open_ms)
    expected = range(start, end + 1, STEP_12H_MS)
    if len(expected) != 61 or any(t not in et or t not in bt for t in expected):
        return None
    if any(not math.isfinite(et[t]) or not math.isfinite(bt[t]) or et[t] <= 0 or bt[t] <= 0
           for t in expected):
        return None
    current = et[end] / bt[end]
    previous = et[start] / bt[start]
    value = current / previous - 1.0
    return value if math.isfinite(value) else None


def rankable_count_from_checkpoints(checkpoints: list[dict[str, Any]],
                                    symbols: list[str], boundary_ms: int) -> tuple[int, int]:
    """Count contracts with 90d history at this exact key close, not source signal rows."""
    by_symbol = {str(row["symbol"]): row for row in checkpoints}
    expected_time = int(boundary_ms) - STEP_12H_MS
    completed = 0
    rankable = 0
    for symbol in symbols:
        row = by_symbol.get(symbol)
        if row is None or int(row.get("boundary", 0)) < boundary_ms:
            continue
        completed += 1
        try:
            state = row.get("ema_state")
            state = json.loads(state) if isinstance(state, str) else state
            state = state or {}
            if int(state.get("time", -1)) == expected_time and int(state.get("count", 0)) >= RANKABLE_90D_MIN_CANDLES:
                rankable += 1
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return completed, rankable


def rankable_symbols_from_checkpoints(checkpoints: list[dict[str, Any]],
                                      symbols: list[str], boundary_ms: int) -> set[str]:
    """Return the exact synchronized symbols eligible for the 90d census."""
    by_symbol = {str(row["symbol"]): row for row in checkpoints}
    expected_time = int(boundary_ms) - STEP_12H_MS
    result = set()
    for symbol in symbols:
        row = by_symbol.get(symbol)
        if row is None or int(row.get("boundary", 0)) < boundary_ms:
            continue
        try:
            state = row.get("ema_state")
            state = json.loads(state) if isinstance(state, str) else state
            if state and int(state.get("time", -1)) == expected_time and int(state.get("count", 0)) >= RANKABLE_90D_MIN_CANDLES:
                result.add(symbol)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def prior_atr_percent(rows: list[dict[str, Any]], signal_open_ms: int) -> float | None:
    """Return prior completed 12H Wilder ATR14/close, excluding the key candle."""
    records = [{"time": int(r["t"]), "high": float(r["h"]), "low": float(r["l"]),
                "close": float(r["c"])} for r in rows]
    points = {int(p["time"]): p for p in atr_points(records)}
    prior = points.get(int(signal_open_ms) - STEP_12H_MS)
    if not prior or not isinstance(prior.get("atr14"), (float, int)):
        return None
    close, atr = float(prior["close"]), float(prior["atr14"])
    if not math.isfinite(close) or not math.isfinite(atr) or close <= 0 or atr <= 0:
        return None
    return 100.0 * atr / close


class CloudInverseFeed:
    """Feed adapter with recoverable health; only the public OKX client is used."""

    def __init__(self, runtime: str | Path, *, client=None, source_path: str | Path | None = None,
                 clock=None):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.source_path = Path(source_path) if source_path else self.runtime / "cloud-signals.sqlite3"
        self.client = client or OKX(runtime=runtime, priority="background", include_open=False)
        self.clock = clock or self.client.clock
        self._symbols: list[str] = []
        self._symbols_at_ms = 0
        self._series_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
        self._ema_cache: dict[str, tuple[int, list[tuple[int, float, float, bool]]]] = {}
        self._health: dict[str, Any] = {
            "state": "warming", "source_db": "unknown", "public_market": "unknown",
            "public_only": True, "private_or_order_api_used": False,
            "last_error": None, "signal_batch_max_delay_ms": SIGNAL_BATCH_MAX_DELAY_MS,
            "ticker_max_age_ms": TICKER_MAX_AGE_MS,
        }
        self.market_state: dict[str, Any] = {}
        self._last_sync_ms: int | None = None

    def synchronize(self) -> int:
        """Synchronize only via public OKX server time."""
        offset = int(self.client.synchronize())
        self._last_sync_ms = int(self.clock())
        self._health.update(public_market="healthy", clock_offset_ms=offset,
                            clock_synchronized_ms=self._last_sync_ms, last_error=None)
        return offset

    def health(self) -> dict[str, Any]:
        return dict(self._health)

    def _source_db(self):
        if not self.source_path.is_file():
            raise FileNotFoundError(f"local cloud signal database unavailable: {self.source_path}")
        conn = sqlite3.connect(self.source_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _candidate(item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            event_id = str(item["id"])
            symbol = str(item["symbol"])
            side = int(item["side"])
            signal_open = int(item["signal_open_ms"])
            confirmed = int(item["confirmed_ms"])
            detected = int(item["detected_ms"])
            stop = float(item["stop"])
            if not event_id or not symbol.endswith("-USDT-SWAP") or side not in (-1, 1):
                return None
            if not all(math.isfinite(x) and x > 0 for x in (stop, float(item["entry"]))):
                return None
            return {"id": event_id, "event_id": event_id, "symbol": symbol,
                    "side": side, "signal_open_ms": signal_open, "confirmed_ms": confirmed,
                    "source_detected_ms": detected, "historical": item.get("historical"),
                    "stop_price": stop, "signal_entry_reference": float(item["entry"]),
                    "signal_target_reference": item.get("target"),
                    "week": item.get("week"), "range_mode": item.get("range_mode"),
                    "ema20": item.get("ema20"), "ema50": item.get("ema50")}
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def queue_new_events(self, account: CloudInversePaper, now_ms: int | None = None) -> dict[str, int]:
        """Queue rows first observed after account activation, without touching source DB."""
        now = int(self.clock() if now_ms is None else now_ms)
        started = account.started_ms()
        conn = self._source_db()
        try:
            rows = conn.execute("SELECT id,confirmed_ms,payload FROM signals WHERE confirmed_ms>=? ORDER BY confirmed_ms,id",
                                (started,)).fetchall()
        finally:
            conn.close()
        inserted = ignored = malformed = 0
        for row in rows:
            try:
                item = json.loads(row["payload"])
            except (TypeError, ValueError, json.JSONDecodeError):
                malformed += 1
                continue
            candidate = self._candidate(item) if isinstance(item, dict) else None
            if candidate is None:
                malformed += 1
                continue
            if account.queue_source_event(candidate, first_observed_ms=now):
                inserted += 1
            else:
                ignored += 1
        self._health.update(source_db="healthy", source_rows_seen=len(rows), queued_new=inserted,
                            ignored_old_or_duplicate=ignored, malformed_source_rows=malformed,
                            source_path=str(self.source_path), last_error=None)
        self.refresh_source_health(now)
        return {"seen": len(rows), "queued": inserted, "ignored": ignored, "malformed": malformed}

    def refresh_source_health(self, now_ms: int | None = None) -> dict[str, Any]:
        """Expose current scanner progress even when the account sees no signals."""
        now = int(self.clock() if now_ms is None else now_ms)
        boundary = now // STEP_12H_MS * STEP_12H_MS
        state = self._checkpoint_state(boundary, now)
        if state["source_complete"]:
            status = "healthy"
            error = None
        elif now > boundary + SIGNAL_BATCH_MAX_DELAY_MS:
            status = "degraded_source_scan_stalled"
            error = "current_12h_checkpoint_incomplete_after_15m"
        else:
            status = "waiting_source_scan"
            error = None
        self._health.update(state=status, last_error=error)
        return dict(state)

    def _universe(self, now_ms: int) -> list[str]:
        if self._symbols and now_ms - self._symbols_at_ms < UNIVERSE_CACHE_MS:
            return self._symbols
        rows = self.client.instruments()
        symbols = sorted({str(row["instId"]) for row in rows
                          if row.get("instType") == "SWAP" and row.get("state") == "live"
                          and str(row.get("instId", "")).endswith("-USDT-SWAP")})
        if not symbols:
            raise RuntimeError("empty_public_usdt_swap_universe")
        self._symbols, self._symbols_at_ms = symbols, int(now_ms)
        return symbols

    def _checkpoint_state(self, boundary_ms: int, now_ms: int) -> dict[str, Any]:
        symbols = self._universe(now_ms)
        conn = self._source_db()
        try:
            rows = [dict(r) for r in conn.execute("SELECT symbol,boundary,ema_state FROM checkpoints")]
        finally:
            conn.close()
        completed, rankable = rankable_count_from_checkpoints(rows, symbols, boundary_ms)
        state = {"boundary_ms": int(boundary_ms), "source_boundary_ms": int(boundary_ms),
                 "expected_symbols": len(symbols), "completed_symbols": completed,
                 "rankable_count": rankable,
                 "rankable_symbols": sorted(rankable_symbols_from_checkpoints(rows, symbols, boundary_ms)),
                 "rankable_minimum": RANKABLE_UNIVERSE_MIN,
                 "rankable_threshold_count": RANKABLE_90D_MIN_CANDLES,
                 "source_complete": completed == len(symbols)}
        self.market_state = {key: value for key, value in state.items() if key != "rankable_symbols"}
        self._health.update(source_boundary_ms=int(boundary_ms), source_expected=state["expected_symbols"],
                            source_completed=completed, rankable_count=rankable,
                            rankable_minimum=RANKABLE_UNIVERSE_MIN,
                            source_complete=state["source_complete"])
        return state

    def _source_event_canonical_ids(self) -> set[str]:
        conn = self._source_db()
        try:
            rows = conn.execute("SELECT payload FROM signals").fetchall()
        finally:
            conn.close()
        items = []
        for row in rows:
            try:
                item = json.loads(row["payload"])
                if isinstance(item, dict):
                    items.append(item)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return {str(item["id"]) for item in first_eligible(items) if item.get("id")}

    def _series12(self, symbol: str, now_ms: int) -> list[dict[str, Any]]:
        cached = self._series_cache.get(symbol)
        latest_boundary = now_ms // STEP_12H_MS * STEP_12H_MS
        if cached and cached[1] == latest_boundary and now_ms-cached[0] < CANDLE_SERIES_CACHE_MS:
            return cached[2]
        rows = self._public_candles(symbol, "12Hutc", HISTORY_12H_LIMIT, now_ms)
        self._ema_cache.pop(symbol, None)
        self._series_cache[symbol] = (int(now_ms), latest_boundary, rows)
        return rows

    def _candidate_facts(self, candidate: dict[str, Any], now_ms: int) -> tuple[float | None, float | None]:
        symbol = candidate["symbol"]
        own = self._series12(symbol, now_ms)
        signal_open = int(candidate["signal_open_ms"])
        if not own or max(int(r["t"]) for r in own) < signal_open:
            raise CandleBoundaryNotReady(f"12h signal bar not cached yet for {symbol}")
        atr = prior_atr_percent(own, candidate["signal_open_ms"])
        eth = self._series12("ETH-USDT-SWAP", now_ms)
        btc = self._series12("BTC-USDT-SWAP", now_ms)
        if (not eth or not btc or max(int(r["t"]) for r in eth) < signal_open
                or max(int(r["t"]) for r in btc) < signal_open):
            raise CandleBoundaryNotReady("ETH/BTC signal-clock candles have not arrived")
        regime = inverse_regime(eth, btc, candidate["signal_open_ms"])
        return regime, atr

    def process_queued(self, account: CloudInversePaper, now_ms: int | None = None) -> dict[str, Any]:
        """Drain complete same-clock signal batches with a 15 minute hard deadline."""
        now = int(self.clock() if now_ms is None else now_ms)
        counters = {"batches_waiting": 0, "batches_processed": 0, "opened": 0,
                    "skipped": 0, "missed": 0, "rankable_count": None}
        for boundary in account.queued_boundaries():
            queued = account.queued_candidates(boundary)
            if not queued:
                continue
            age = now - int(boundary)
            if age < 0:
                for row in queued:
                    account.mark_missed(row["event_id"], "source_clock_ahead_of_runner", now_ms=now)
                    counters["missed"] += 1
                continue
            if age > SIGNAL_BATCH_MAX_DELAY_MS:
                state = self._checkpoint_state(boundary, now)
                if state["source_complete"]:
                    self.queue_new_events(account, now)
                    state = self._checkpoint_state(boundary, now)
                    queued = account.queued_candidates(boundary)
                reason = "source_scan_incomplete_at_deadline" if not state["source_complete"] else "signal_batch_expired"
                for row in queued:
                    account.mark_missed(row["event_id"], reason, rankable_count=state["rankable_count"], now_ms=now)
                    counters["missed"] += 1
                continue
            state = self._checkpoint_state(boundary, now)
            counters["rankable_count"] = state["rankable_count"]
            if not state["source_complete"]:
                counters["batches_waiting"] += 1
                self._health.update(state="waiting_source_scan", last_error=None)
                continue
            # A scanner writes the last signals before publishing its completed
            # checkpoint. Drain that same committed boundary once completion is
            # visible, then rank the complete set rather than a pre-completion
            # snapshot of the local queue.
            self.queue_new_events(account, now)
            state = self._checkpoint_state(boundary, now)
            if not state["source_complete"]:
                counters["batches_waiting"] += 1
                self._health.update(state="waiting_source_scan", last_error=None)
                continue
            queued = account.queued_candidates(boundary)
            if not queued:
                continue
            canonical_ids = self._source_event_canonical_ids()
            facts: list[dict[str, Any]] = []
            decisions: list[dict[str, Any]] = []
            public_data_wait = False
            for row in queued:
                candidate = json.loads(row["payload"])
                if str(candidate["symbol"]) not in state["rankable_symbols"]:
                    decisions.append(dict(row=row, status="skipped", reason="signal_contract_not_rankable_at_same_close"))
                    continue
                if row["event_id"] not in canonical_ids:
                    decisions.append(dict(row=row, status="skipped", reason="not_first_eligible_signal_for_symbol_week"))
                    continue
                if int(row["source_detected_ms"]) < int(row["confirmed_ms"]):
                    decisions.append(dict(row=row, status="missed", reason="source_clock_before_confirmed_close"))
                    continue
                if state["rankable_count"] < RANKABLE_UNIVERSE_MIN:
                    decisions.append(dict(row=row, status="skipped", reason="same_time_rankable_universe_below_10"))
                    continue
                try:
                    regime, atr = self._candidate_facts(candidate, now)
                except CandleBoundaryNotReady:
                    public_data_wait = True
                    continue
                if regime is None:
                    decisions.append(dict(row=row, status="skipped", reason="missing_contiguous_eth_btc_61_close_window"))
                    continue
                if atr is None:
                    decisions.append(dict(row=row, status="skipped", reason="missing_preceding_12h_atr14",
                                          relative_30d=regime))
                    continue
                relative = int(candidate["side"]) * regime
                if not math.isfinite(regime) or relative == 0:
                    decisions.append(dict(row=row, status="skipped", reason="exact_eth_btc_regime_tie",
                                          relative_30d=regime, prior_atr_pct=atr))
                    continue
                if relative > 0:
                    decisions.append(dict(row=row, status="skipped", reason="inverse_regime_direction_gate",
                                          relative_30d=regime, prior_atr_pct=atr))
                    continue
                facts.append(dict(row=row, candidate=candidate, regime=regime, atr=atr,
                                  priority=zlib.crc32(str(candidate["symbol"]).split("-")[0].encode("utf-8"))))
            if public_data_wait:
                counters["batches_waiting"] += 1
                self._health.update(state="waiting_public_candle", last_error=None)
                continue
            for decision in decisions:
                args = dict(rankable_count=state["rankable_count"],
                            relative_30d=decision.get("relative_30d"),
                            prior_atr_pct=decision.get("prior_atr_pct"), now_ms=now)
                if decision["status"] == "missed":
                    account.mark_missed(decision["row"]["event_id"], decision["reason"], **args)
                    counters["missed"] += 1
                else:
                    account.mark_skipped(decision["row"]["event_id"], decision["reason"], **args)
                    counters["skipped"] += 1
            facts.sort(key=lambda x: (int(x["row"]["confirmed_ms"]), float(x["atr"]),
                                      int(x["priority"]), str(x["candidate"]["symbol"]),
                                      str(x["row"]["event_id"])))
            for order, fact in enumerate(facts):
                row, candidate = fact["row"], fact["candidate"]
                # The batch can expire while quotes for earlier ranked members are being read.
                observed_now = int(self.clock())
                if observed_now - boundary > SIGNAL_BATCH_MAX_DELAY_MS:
                    account.mark_missed(row["event_id"], "signal_batch_expired_during_quote_collection",
                                        rankable_count=state["rankable_count"], relative_30d=fact["regime"],
                                        prior_atr_pct=fact["atr"], entry_order=order, now_ms=observed_now)
                    counters["missed"] += 1
                    continue
                quote = self.public_ticker(candidate["symbol"])
                observed_now = int(self.clock())
                if quote is None:
                    account.mark_missed(row["event_id"], "missing_public_ticker_quote",
                        rankable_count=state["rankable_count"], relative_30d=fact["regime"],
                        prior_atr_pct=fact["atr"], now_ms=observed_now)
                    counters["missed"] += 1
                    continue
                result = account.attempt_entry(candidate, quote,
                    rankable_count=state["rankable_count"], relative_30d=fact["regime"],
                    prior_atr_pct=fact["atr"], entry_order=order, now_ms=observed_now,
                    max_signal_delay_ms=SIGNAL_BATCH_MAX_DELAY_MS,
                    max_quote_age_ms=TICKER_MAX_AGE_MS)
                if result["status"] == "opened":
                    counters["opened"] += 1
                elif result["status"] == "missed":
                    counters["missed"] += 1
                else:
                    counters["skipped"] += 1
            counters["batches_processed"] += 1
            self._health.update(last_processed_boundary_ms=boundary)
        if counters["batches_waiting"] and not counters["batches_processed"]:
            self._health.setdefault("state", "waiting_source_scan")
        return counters

    def public_ticker(self, symbol: str) -> dict[str, Any] | None:
        """Read one public ticker; never fall back to last trade or a historical candle."""
        rows = self.client.get("/api/v5/market/ticker", {"instId": symbol})
        if not rows:
            return None
        row = rows[0]
        try:
            quote = {"ts_ms": int(row["ts"]), "bid": float(row["bidPx"]), "ask": float(row["askPx"])}
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(quote[k]) for k in ("bid", "ask")):
            return None
        return quote

    def advance_open_positions(self, account: CloudInversePaper, now_ms: int | None = None) -> dict[str, Any]:
        """Apply only confirmed public 1m/1h bars; replay bars with both clocks recorded."""
        now = int(self.clock() if now_ms is None else now_ms)
        positions = account.open_positions()
        if not positions:
            return {"minute_bars": 0, "hour_bars": 0, "processed": 0, "open_positions": 0}
        minute_events: dict[int, dict[str, dict[str, Any]]] = {}
        hour_events: dict[int, dict[str, dict[str, Any]]] = {}
        symbols = sorted({p["symbol"] for p in positions})
        for position in positions:
            symbol = position["symbol"]
            protection_end = int(position["minute_protection_until_ms"])
            next_minute = int(position["next_minute_open_ms"])
            latest_minute = (now // MINUTE_MS) * MINUTE_MS - MINUTE_MS
            if next_minute < protection_end and next_minute <= latest_minute:
                end_minute = min(protection_end-MINUTE_MS, latest_minute)
                count = min(720, max(1, (end_minute-next_minute)//MINUTE_MS+1))
                minutes = self._public_candles(symbol, "1m", count, now,
                                               start_ms=next_minute, end_ms=end_minute)
                for bar in minutes:
                    t = int(bar["t"])
                    if next_minute <= t < protection_end:
                        minute_events.setdefault(t, {})[symbol] = self._ohlc(bar)
            next_hour = int(position["next_bar_open_ms"])
            latest_hour = (now // HOUR_MS) * HOUR_MS - HOUR_MS
            if next_hour <= latest_hour:
                count = min(720, max(1, (latest_hour-next_hour)//HOUR_MS+1))
                hours = self._public_candles(symbol, "1H", count, now,
                                             start_ms=next_hour, end_ms=latest_hour)
                for bar in hours:
                    t = int(bar["t"])
                    if next_hour <= t <= latest_hour:
                        hour_events.setdefault(t, {})[symbol] = self._ohlc(bar)

        processed_minutes = processed_hours = fills = 0
        entry_ms_by_symbol = {p["symbol"]: int(p["entry_ms"]) for p in positions}
        for event_ms, rows_by_symbol in sorted(minute_events.items()):
            features = {s: self._ema_feature(s, event_ms, now, entry_ms_by_symbol.get(s, 0))
                        for s in rows_by_symbol}
            result = account.process_minute_bars(event_ms, rows_by_symbol, observed_ms=now,
                                                  ema_by_symbol=features)
            processed_minutes += result["processed"]
            fills += result["fills"]
        stale_protection_quotes = 0
        for position in account.open_positions():
            if now >= int(position["minute_protection_until_ms"]):
                continue
            quote = self.public_ticker(position["symbol"])
            observed = int(self.clock())
            if quote is None:
                stale_protection_quotes += 1
                continue
            feature = self._ema_feature(position["symbol"], observed, observed, int(position["entry_ms"]))
            result = account.process_public_quote(position["symbol"], quote, observed_ms=observed,
                                                 ema=feature, max_age_ms=TICKER_MAX_AGE_MS)
            stale_protection_quotes += int(result.get("reason") == "stale_or_invalid_quote")
            fills += result["fills"]
        pending = account.post_entry_protection_pending(now)
        if pending:
            raise RuntimeError("post_entry_minute_history_incomplete:" + ",".join(p["symbol"] for p in pending))
        for event_ms, rows_by_symbol in sorted(hour_events.items()):
            # Only ask features for positions still open at this hour; closed positions need no data.
            live_symbols = {p["symbol"] for p in account.open_positions()}
            data = {s:bar for s,bar in rows_by_symbol.items() if s in live_symbols}
            if not data:
                continue
            live_positions = account.open_positions()
            entry_times = {p["symbol"]: int(p["entry_ms"]) for p in live_positions}
            features = {s: self._ema_feature(s, event_ms, now, entry_times.get(s, 0)) for s in data}
            result = account.process_hour_bars(event_ms, data, observed_ms=now, ema_by_symbol=features)
            processed_hours += result["processed"]
            fills += result["fills"]
        self._health.update(public_market="healthy", last_market_observed_ms=now,
                            open_position_symbols=symbols, minute_bars_processed=processed_minutes,
                            hour_bars_processed=processed_hours,
                            stale_protection_quotes=stale_protection_quotes, last_error=None)
        return {"minute_bars": processed_minutes, "hour_bars": processed_hours,
                "processed": processed_minutes+processed_hours, "fills": fills,
                "open_positions": len(account.open_positions()), "symbols": symbols}

    @staticmethod
    def _ohlc(row: dict[str, Any]) -> dict[str, float]:
        return {"open": float(row["o"]), "high": float(row["h"]),
                "low": float(row["l"]), "close": float(row["c"])}

    def _ema_feature(self, symbol: str, bar_open_ms: int, now_ms: int,
                     entry_ms: int = 0) -> dict[str, Any]:
        rows = self._series12(symbol, now_ms)
        key = int(now_ms // STEP_12H_MS * STEP_12H_MS)
        cached = self._ema_cache.get(symbol)
        if cached and cached[0] == key:
            data = cached[1]
        else:
            records = [{"time": int(r["t"]), "open": float(r["o"]), "high": float(r["h"]),
                        "low": float(r["l"]), "close": float(r["c"])} for r in rows]
            # Exact recurrence used by the local causal cloud scanner.
            from .cloud_signals import ema_points
            points = ema_points(records)
            data = [(int(r["t"]), float(p["ema20"]), float(p["ema50"]), bool(p["ready"]))
                    for r, p in zip(rows, points)]
            self._ema_cache[symbol] = (key, data)
        eligible = [p for p in data if p[0]+STEP_12H_MS <= int(bar_open_ms)]
        feature: dict[str, Any] = {"cross_line": None, "ema_spread": None, "stale": False}
        if eligible:
            t, ema20, ema50, ready = eligible[-1]
            if ready:
                alpha20, alpha50 = 2./21., 2./51.
                feature["cross_line"] = ((1.-alpha50)*ema50-(1.-alpha20)*ema20)/(alpha20-alpha50)
                feature["ema_spread"] = ema20-ema50
            stale_deadlines = []
            for idx, point in enumerate(data):
                next_expected = point[0]+STEP_12H_MS
                next_actual = data[idx+1][0] if idx+1 < len(data) else None
                if next_actual is None or next_actual > next_expected:
                    deadline = next_expected+STEP_12H_MS
                    if deadline > int(entry_ms):
                        stale_deadlines.append(deadline)
            feature["stale"] = any(int(bar_open_ms) >= deadline for deadline in stale_deadlines)
        return feature

    def _public_candles(self, symbol: str, timeframe: str, limit: int, now_ms: int, *,
                        start_ms: int | None = None, end_ms: int | None = None) -> list[dict[str, Any]]:
        """Get validated confirmed candles via CandleHub, including 1m/12Hutc.

        ``OKX.candles`` is intentionally scoped to the monitor's four public
        polling frames. This reader uses the same public CandleHub contract for
        the account's native minute and 12H exit/signal frames.
        """
        remaining = max(1, min(720, int(limit)))
        raw_rows: list[list[Any]] = []
        after: int | None = None
        while remaining > 0:
            count = min(300, remaining)
            params: dict[str, str] = {"instId": symbol, "bar": timeframe, "limit": str(count)}
            if after is not None:
                params["after"] = str(after)
            elif end_ms is not None:
                params["after"] = str(int(end_ms)+1)
            if start_ms is not None:
                params["before"] = str(int(start_ms)-1)
            page = self.client.get("/api/v5/market/candles", params)
            if not page:
                break
            raw_rows.extend(page)
            oldest = min(int(row[0]) for row in page)
            if oldest == after or len(page) < count:
                break
            remaining -= len(page)
            after = oldest
            if len(raw_rows) >= limit:
                break
        validated = validate_candle_rows(raw_rows, timeframe, int(self.client.clock()))
        rows = []
        for timestamp, raw in sorted(validated.items()):
            if raw[8] != "1":
                continue
            rows.append({"t": int(timestamp), "o": float(raw[1]), "h": float(raw[2]),
                         "l": float(raw[3]), "c": float(raw[4]), "v": float(raw[5])})
        return rows[-max(1, min(720, int(limit))):]


class CandleBoundaryNotReady(RuntimeError):
    """A confirmed close is expected but not yet present in public cache/source."""
