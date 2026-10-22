"""Public OKX Monday-range monitor with isolated IO threads and opt-in Telegram.

Sources: OKX REST candlesticks/instruments and WS candle1m (business channel).
https://www.okx.com/docs-v5/en/#order-book-trading-market-data
Uses the shared background candle budget; never joins the forward scan pulse.
One all-market socket retains intraminute wicks. Reconnects reconcile history
before rearming; observations from an unknown interval are never live alerts.
"""
from __future__ import annotations

import json
import re
import threading
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool
import requests

from yoyo.data.candle_hub import validate
from .monday_fib import FibBook, baseline, deliver_once, week_window, HOUR, MINUTE, FRESH_MS
from .okx import OKX
from .telegram import credentials


class MondayFibService:
    def __init__(self, runtime, client=None):
        self.book = FibBook(runtime)
        self.client = client or OKX(runtime=runtime, include_open=True)
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.threads = []
        self.socket = None
        self.symbols = ()
        self.levels = {}
        self.quotes = {}
        self.consumed = set()
        self.epoch = 0
        self.connected = False
        self.last_message_ms = 0
        self.catalog_ms = 0
        self.catalog_error = None
        self.stream_error = None
        self.attempts = {}

    def start(self):
        if self.threads:
            return
        self.book.recover()
        for target, name in ((self._maintain, "fib-anchors"), (self._stream, "fib-quotes"), (self._send, "fib-telegram")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        socket = self.socket
        if socket is not None:
            socket.close()
        for thread in self.threads:
            thread.join(timeout=2)

    def _maintain(self):
        while not self.stop.is_set():
            now = self.client.clock()
            if now - self.catalog_ms > HOUR:
                try:
                    self.client.synchronize()
                    rows = self.client.instruments()
                    symbols = tuple(sorted(r["instId"] for r in rows
                                           if r.get("state") == "live" and r.get("instType") == "SWAP"
                                           and r.get("settleCcy") == "USDT"
                                           and re.fullmatch(r"[A-Z0-9]{1,32}-USDT-SWAP", r.get("instId", ""))))
                    if not symbols:
                        raise ValueError("empty_universe")
                    with self.lock:
                        self.symbols = symbols
                        self.catalog_ms = self.client.clock()
                        self.catalog_error = None
                except Exception:
                    with self.lock:
                        self.catalog_error = "合约目录暂不可用，正在重试"
                    self.stop.wait(15)
                    continue
            with self.lock:
                symbols, epoch = self.symbols, self.epoch
            for symbol in symbols:
                if self.stop.is_set():
                    return
                now = self.client.clock()
                window = week_window(now)
                with self.lock:
                    current = self.levels.get(symbol, {})
                    if (current.get("week") == window["week"] and current.get("epoch") == epoch
                            and current.get("state") == "ready"):
                        continue
                    if self.attempts.get((symbol, window["week"], epoch), 0) > now:
                        continue
                    self.attempts[(symbol, window["week"], epoch)] = now + 120_000
                try:
                    if now < window["end"]:
                        result = {**window, "state": "developing"}
                    else:
                        count = (now // HOUR * HOUR - window["start"]) // HOUR + 1
                        raw = self.client.get("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": str(count)})
                        result = baseline(raw, now)
                    with self.lock:
                        if epoch != self.epoch or result["week"] != week_window(self.client.clock())["week"]:
                            continue
                        self.levels[symbol] = {**result, "epoch": epoch}
                        for side, candle_ms in result.get("historical_touches", {}).items():
                            self._touch(result, symbol, side, result[side], now, candle_ms, "history")
                        # Preserve a wick arriving during the REST request. It is
                        # warmup evidence, not a retroactive push notification.
                        quote = self.quotes.get(symbol)
                        if quote and result["state"] == "ready":
                            self._check(result, symbol, quote, "history")
                except Exception:
                    with self.lock:
                        if epoch == self.epoch:
                            self.levels[symbol] = {**window, "state": "fetch_error", "epoch": epoch}
            self.stop.wait(3)

    def _touch(self, levels, symbol, side, price, now, candle_ms, source, known_after_ms=None):
        key = (levels["week"], symbol, side)
        if key in self.consumed:
            return
        self.book.touch(levels, symbol, side, price, now, candle_ms, source, known_after_ms=known_after_ms)
        self.consumed.add(key)

    def _check(self, levels, symbol, quote, source, known_after_ms=None):
        if not levels["end"] <= quote["candle_ms"] < levels["expires"]:
            return
        for side, hit in (("upper", quote["high"] >= levels["upper"]), ("lower", quote["low"] <= levels["lower"])):
            if hit:
                self._touch(levels, symbol, side, quote["price"], quote["received_ms"], quote["candle_ms"], source, known_after_ms)

    def on_candle(self, symbol, raw, now):
        parsed = validate([raw], "1m", now)
        stamp, row = next(iter(parsed.items()))
        if not 0 <= now - stamp <= 2 * MINUTE:
            return
        quote = {"price": float(row[4]), "high": float(row[2]), "low": float(row[3]),
                 "candle_ms": stamp, "received_ms": now}
        with self.lock:
            if symbol not in self.symbols:
                return
            prior = self.quotes.get(symbol)
            if prior and stamp < prior["candle_ms"]:
                return
            self.quotes[symbol] = quote
            self.last_message_ms = now
            levels = self.levels.get(symbol, {})
            if (levels.get("state") != "ready" or levels.get("epoch") != self.epoch
                    or levels.get("week") != week_window(now)["week"]):
                return
            if prior and (now - prior["received_ms"] > FRESH_MS or stamp - prior["candle_ms"] > MINUTE):
                # A symbol-specific delivery gap also requires reconciliation.
                levels["state"] = "recovering"
                self.attempts.pop((symbol, levels["week"], self.epoch), None)
                return
            known_after = stamp
            if prior and prior["candle_ms"] == stamp:
                known_after = prior["received_ms"]
            self._check(levels, symbol, quote, "live", known_after_ms=known_after)

    def _stream(self):
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.stream_error = "实时行情组件未安装"
            return
        while not self.stop.is_set():
            with self.lock:
                symbols = self.symbols
            if not symbols:
                self.stop.wait(1)
                continue
            try:
                with connect("wss://ws.okx.com:8443/ws/v5/business", open_timeout=12, close_timeout=2,
                             ping_interval=None, max_queue=2048) as socket:
                    with self.lock:
                        self.socket = socket
                        self.connected = True
                        self.stream_error = None
                    # One subscription message stays well below OKX's 64KB limit.
                    socket.send(json.dumps({"op": "subscribe", "args": [
                        {"channel": "candle1m", "instId": s} for s in symbols]}))
                    pinged = False
                    while not self.stop.is_set() and symbols == self.symbols:
                        try:
                            message = socket.recv(timeout=10)
                        except TimeoutError:
                            if pinged:
                                raise ValueError("heartbeat_timeout")
                            socket.send("ping")
                            pinged = True
                            continue
                        pinged = False
                        if message == "pong":
                            continue
                        payload = json.loads(message)
                        if payload.get("event") == "error":
                            raise ValueError("subscription_error")
                        if payload.get("arg", {}).get("channel") != "candle1m":
                            continue
                        symbol = payload["arg"].get("instId")
                        for raw in payload.get("data", []):
                            self.on_candle(symbol, raw, self.client.clock())
            except Exception:
                with self.lock:
                    self.stream_error = "实时行情断开，正在重连并核对遗漏区间"
            finally:
                with self.lock:
                    self.socket = None
                    self.connected = False
                    self.epoch += 1
                    self.attempts.clear()
                    self.quotes.clear()
            self.stop.wait(5)

    def _send(self):
        while not self.stop.is_set():
            try:
                if self.book.subscription()["enabled"]:
                    deliver_once(self.book, credentials(), requests.post, self.client.clock())
            except Exception:
                # No exception string can expose the token-bearing request URL.
                pass
            self.stop.wait(1)

    def snapshot(self):
        now = self.client.clock()
        window = week_window(now)
        events = self.book.records(window["week"])
        touches = {(e["symbol"], e["side"]): e for e in events}
        with self.lock:
            rows = []
            for symbol in self.symbols:
                levels = self.levels.get(symbol, {})
                if levels.get("week") != window["week"]:
                    levels = {"state": "developing" if now < window["end"] else "loading"}
                elif levels.get("epoch") != self.epoch:
                    levels = {**levels, "state": "recovering"}
                quote = self.quotes.get(symbol, {})
                stale = not self.connected or not 0 <= now - quote.get("received_ms", 0) <= FRESH_MS
                item = {"symbol": symbol, "state": levels.get("state", "loading"), "stale": stale,
                        **{k: levels.get(k) for k in ("high", "low", "upper", "lower")},
                        **{k: quote.get(k) for k in ("price", "candle_ms", "received_ms")},
                        "touches": {s: touches[(symbol, s)] for s in ("upper", "lower") if (symbol, s) in touches}}
                distances = [(abs(levels[s] / quote["price"] - 1) * 100, s) for s in ("upper", "lower")
                             if levels.get(s, 0) > 0 and quote.get("price", 0) > 0]
                item["distance_pct"], item["nearest"] = min(distances) if distances else (None, None)
                rows.append(item)
            rows.sort(key=lambda r: (r["stale"] or r["state"] != "ready", r["distance_pct"] if r["distance_pct"] is not None else float("inf"), r["symbol"]))
            stream = {"connected": self.connected, "last_message_ms": self.last_message_ms,
                      "error": self.stream_error, "catalog_error": self.catalog_error, "catalog_ms": self.catalog_ms}
        return {"now_ms": now, "week": window["week"], "timezone": "America/New_York",
                "monday_complete": now >= window["end"], "stream": stream, "items": rows,
                "telegram": {**self.book.subscription(), "configured": bool(credentials())},
                "counts": {"total": len(rows), "ready": sum(r["state"] == "ready" and not r["stale"] for r in rows),
                           "touched": len(events), "unavailable": sum(r["state"] != "ready" or r["stale"] for r in rows)},
                "events": events[:40]}


class FibSubscription(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def install(app, service):
    app.state.monday_fib = service

    @app.get("/api/monday-fib")
    def snapshot():
        return service.snapshot()

    @app.put("/api/monday-fib/telegram")
    def subscribe(payload: FibSubscription, request: Request):
        if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
                or request.headers.get("x-spike-action") != "update-monday-fib-telegram"):
            raise HTTPException(403, "请从周一 1.618 页面修改报警开关")
        if payload.enabled and not credentials():
            raise HTTPException(409, "本机尚未配置 Telegram 通道")
        service.book.set_enabled(payload.enabled, service.client.clock())
        return service.snapshot()
