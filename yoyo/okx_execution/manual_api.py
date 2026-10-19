"""Read-only exchange previews and explicit, same-origin manual confirmations.

Owner permits remote confirmation through the existing public workspace
(2026-10-17). There is deliberately no loopback-only restriction. All exchange
POSTs stay in the locked execution worker; these routes only persist intent.
Credentials never travel through the browser or a request body.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .client import Client, APIError, TransportError
from .config import Config, DEFAULT_MANUAL_RUNTIME, DEFAULT_SOURCE, read_private
from .dashboard import worker_lock
from .manual_dashboard import ManualDashboard
from .manual import SOURCE, PREVIEW_TTL_MS, total_account
from .manual_engine import ManualEngine
from .manual_ma import MovingAverages, envelope_stop
from .engine import one
from .manual_inventory import policy
from .manual_queue import Queue, ReadLedger
from .manual_readiness import EntryBlocked, entry_status
from .manual_source import detection_context
from .preview_reads import PooledOpener, ReadPacer, preview_engine
from .risk import MarketOrderLimitError, InsufficientMarginError
from .source import Source


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    detection_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    symbol: str = Field(pattern=r"^[A-Z0-9]{1,24}-USDT-SWAP$")
    rr: int = Field(default=3, strict=True)
    stop: Optional[str] = Field(default=None, pattern=r"^\d{1,12}(\.\d{1,18})?$")


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledgement: str = Field(pattern=r"^CONFIRM-LIVE-ORDER$")


def guard(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if request.method in ("GET", "HEAD"):
        return
    if (request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}"
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
            or request.headers.get("x-fable-manual") != "owner-confirmation"
            or request.headers.get("content-type", "").split(";", 1)[0] != "application/json"):
        raise HTTPException(403, "请从工作台预览并手动确认订单")


class Service:
    def __init__(self, book, runtime=DEFAULT_MANUAL_RUNTIME, engine=None, clock=None, healthy=None):
        self.book, self.runtime = book, Path(runtime)
        self.clock = clock or (lambda: int(time.time()*1000))
        self._engine, self._healthy = engine, healthy
        self.account_reader = ManualDashboard(self.runtime)
        self.preview_pacer = ReadPacer()
        self._preview_opener, self._opener_lock = None, Lock()
        self._preview_lock, self._previews = Lock(), {}
        self._ma_data = None

    def preview_opener(self):
        with self._opener_lock:
            if self._preview_opener is None:
                self._preview_opener = PooledOpener()
            return self._preview_opener

    def close(self):
        self.account_reader.close()
        if self._ma_data is not None:
            self._ma_data.close()
        if self._preview_opener is not None:
            self._preview_opener.close()

    def engine(self):
        if self._engine:
            return self._engine
        config = Config(**read_private(self.runtime / "config.json"))
        config.validate()
        if Path(config.source).resolve() != self.book.runtime.parent.resolve():
            raise ValueError("manual source differs from execution workspace")
        client = Client(config, read_private(self.runtime / "credentials.json"), writes=False)
        engine = ManualEngine(config, client, Source(Path(config.source)), ReadLedger(self.runtime), clock=self.clock)
        if self.runtime == DEFAULT_MANUAL_RUNTIME and policy(engine.store) is None:
            raise ValueError("manual account is not configured")
        return engine

    def account_status(self):
        snapshot = self.account_reader.snapshot()
        return dict(account_scope="yolo_manual", label="均线密集专用账户", account=snapshot["account"],
                    worker=snapshot["ledger"]["worker"], risk_fraction="0.03", confirmation_required=True)

    def ma_snapshot(self, symbol):
        with self._opener_lock:
            if self._ma_data is None:
                self._ma_data = MovingAverages(self.book.runtime.parent, book=self.book)
        from yoyo.monitor.okx import MarketError
        try:
            return self._ma_data.get(symbol, self.clock)
        except MarketError:
            raise HTTPException(503, '5 分钟均线行情暂不可用，请稍后重试') from None

    def chart(self, symbol):
        return self.ma_snapshot(symbol)

    def reference(self, payload):
        # Detection identity supplies direction; no credentials, healthy-worker
        # gate or balance is needed to explain the owner's proposed prices.
        from yoyo.monitor.okx import OKX, MarketError
        from .manual_chart import reference
        context = detection_context(self.book, payload.job_id, payload.symbol, payload.detection_id, now_ms=self.clock())
        try:
            return reference(OKX(runtime=self.book.runtime.parent), context, payload.rr, payload.stop, self.clock, ma_snapshot=self.ma_snapshot)
        except MarketError:
            raise HTTPException(503, '参考点位暂不可用，请更新报价') from None

    def healthy(self, engine):
        if self._healthy:
            self._healthy()
            return
        status = entry_status(engine.store, self.clock(), worker_lock(self.runtime))
        if status["status"] != "ready":
            raise EntryBlocked(status)

    def readiness(self):
        return entry_status(ReadLedger(self.runtime), self.clock(), worker_lock(self.runtime))

    def preview(self, payload):
        key = (payload.job_id, payload.symbol, payload.detection_id)
        generation = object()
        with self._preview_lock:
            self._previews[key] = generation
        try:
            engine = self.engine()
            self.healthy(engine)
            context = detection_context(self.book, payload.job_id, payload.symbol, payload.detection_id, now_ms=self.clock())
            opener = self.preview_opener() if isinstance(engine.client, Client) else None
            with preview_engine(engine, self.preview_pacer, opener) as local:
                local.client.prefetch(context["symbol"], shared=policy(local.store) is not None)
                return self._preview(payload, local, context, pending=(key, generation))
        finally:
            with self._preview_lock:
                if self._previews.get(key) is generation:
                    self._previews.pop(key)

    def _preview(self, payload, engine, context, *, pending=None):
        account = engine.account()
        engine.owned(account)
        if account["uid"] != engine.store.meta("account_uid"):
            raise ValueError("execution account changed")
        balance = total_account(engine.client)
        candle = None
        if payload.stop is None:
            instrument = one(engine.client.get("/api/v5/account/instruments", instType="SWAP", instId=context["symbol"]))
            candle = envelope_stop(self.ma_snapshot(context["symbol"]), context["proposal_side"], instrument["tickSz"])
        now, identity = self.clock(), uuid.uuid4().hex
        event = dict(id=identity, source=SOURCE, symbol=context["symbol"], timeframe=context["timeframe"],
                     side=context["proposal_side"], rr=payload.rr, risk_fraction="0.03", account_uid=account["uid"],
                     bar_close_ms=now, preview_ms=now, expires_ms=now+PREVIEW_TTL_MS,
                     initial_stop=payload.stop if payload.stop is not None else candle["price"],
                     stop_source="manual" if payload.stop is not None else "ma_envelope_5m",
                     stop_candle=candle, detection=context, approved_equity_usdt=balance["total_equity_usdt"])
        trade = engine.prepare(event, account)
        # The review binds quantity, budget, stop, target multiple and leverage.
        event.update(approved_size=trade["size"], approved_margin=trade["margin"], approved_leverage=trade["leverage"])
        draft = dict(id=identity, event=event, plan=trade, created_ms=trade["created_ms"], expires_ms=event["expires_ms"])
        self.healthy(engine)
        if self.clock() >= draft["expires_ms"]:
            raise ValueError("manual preview expired or superseded")
        retryable = [r["id"] for r in engine.store.trades() if r["state"] in ("skipped", "rejected")
                     and not r.get("entry_price") and not r.get("algo_id")
                     and not r.get("entry_ack")
                     and (r.get("entry_order") or {}).get("accFillSz", "0") == "0"]
        # Canceling fetch does not stop a synchronous server worker. A slow
        # obsolete request must not supersede the quote now visible to the owner.
        with self._preview_lock:
            if pending and self._previews.get(pending[0]) is not pending[1]:
                raise ValueError("manual preview expired or superseded")
            Queue(self.runtime).preview(draft, retryable_ids=retryable)
        return self.view(draft)

    def view(self, draft):
        plan, event = draft["plan"], draft["event"]
        result = {k: plan.get(k) for k in ("symbol", "timeframe", "side", "size", "price", "initial_stop",
                  "take_profit", "rr", "planned_risk", "target_risk", "margin", "notional", "leverage",
                  "total_equity_usdt", "quote_ms")}
        return dict(result, account_scope="yolo_manual", id=draft["id"], created_ms=draft["created_ms"], expires_ms=draft["expires_ms"],
                    risk_fraction="0.03", stop_candle=event["stop_candle"], stop_source=event["stop_source"],
                    detection=event["detection"], queue_state=draft.get("queue_state", "preview"))

    def confirm(self, identity):
        queue = Queue(self.runtime)
        previous = queue.get(identity)
        if previous["queue_state"] in ("confirmed", "finished"):
            return self.status(identity)  # Lost-response retry, even after expiry/worker stop.
        engine = self.engine()
        self.healthy(engine)
        if previous["event"]["account_uid"] != engine.store.meta("account_uid"):
            raise ValueError("execution account changed")
        queue.confirm(identity, self.clock())
        return self.status(identity)

    def status(self, identity):
        draft = Queue(self.runtime).get(identity)
        result = self.view(draft)
        rows = [r for r in self.engine().store.trades() if r["id"] == identity]
        trade = rows[0] if rows else None
        result["state"] = trade["state"] if trade else ("queued" if draft["queue_state"] == "confirmed" else draft["queue_state"])
        if result["state"] == "preview" and self.clock() >= result["expires_ms"]:
            result["state"] = "expired"
        if trade:
            result.update({k: trade.get(k) for k in ("entry_price", "take_profit", "size", "actual_risk", "stop",
                          "entry_ms", "exit_ms", "exit_price", "exit_reason", "reason", "tp_basis", "protection_checked_ms") if trade.get(k) is not None})
            result["protection_verified"] = (trade["state"] == "active" and trade.get("tp_basis") == "actual_fill"
                                             and 0 <= self.clock()-trade.get("protection_checked_ms", 0) < 60_000)
        return result


def install(app, book, *, service=None):
    service = service or Service(book)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            service.close()
    router = APIRouter(prefix="/api/manual-live", dependencies=[Depends(guard)], lifespan=lifespan)

    def call(fn, *args):
        try:
            return fn(*args)
        except EntryBlocked as exc:
            raise HTTPException(409, exc.details) from None
        except (MarketOrderLimitError, InsufficientMarginError) as exc:
            raise HTTPException(409, exc.details) from None
        except (ValueError, APIError) as exc:
            raise HTTPException(409, str(exc)) from None
        except (OSError, sqlite3.Error, TransportError, KeyError, TypeError):
            raise HTTPException(503, "账户或行情暂不可用，请稍后重新预览") from None

    @router.post("/preview")
    def preview(payload: Preview):
        return call(service.preview, payload)

    @router.post("/reference")
    def reference(payload: Preview):
        return call(service.reference, payload)

    @router.post("/{identity}/confirm")
    def confirm(identity: str, payload: Confirmation):
        return call(service.confirm, identity)

    @router.get("/recent")
    def recent():
        return call(lambda: dict(items=[service.status(i) for i in Queue(service.runtime).recent()]))

    @router.get("/account")
    def account_status():
        return call(service.account_status)

    @router.get("/readiness")
    def readiness():
        return call(service.readiness)

    @router.get("/dashboard")
    def dashboard(view: str = Query("full", pattern="^(full|summary|workspace)$")):
        return call(lambda: service.account_reader.snapshot(summary=view == "summary", workspace=view == "workspace"))

    @router.get("/chart")
    def chart(symbol: str = Query(pattern=r"^[A-Z0-9]{1,24}-USDT-SWAP$")):
        return call(service.chart, symbol)

    @router.get("/{identity}")
    def status(identity: str):
        return call(service.status, identity)

    app.include_router(router)
