"""Same-origin manual simulation API, backed only by public OKX market data.

Ticker fields and endpoint are documented at
https://www.okx.com/docs-v5/en/#rest-api-market-data-get-tickers.
Execution consumes bidPx/askPx; the read-only market header also exposes last,
open24h/high24h/low24h from the same ticker and timestamp. Public instrument
metadata supplies the selectable live USDT swap catalog; no candles, account
keys, authenticated trading endpoint or fallback data source are involved.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Literal

import httpx
from fastapi import HTTPException, Query, Request as HTTPRequest
from starlette.concurrency import run_in_threadpool
from urllib.parse import unquote
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .paper_desk import MAX_LEVERAGE, PaperDesk, clock_ms, fresh, positive, number, limit_fee, TAKER_FEE
from .paper_desk_risk import PublicRisk
from .paper_desk_market import PublicHTTP, MarketRefresh
from .paper_desk_limits import LimitOrders, LimitOrderWorker
from .paper_desk_journal import PositionJournal, FIELDS as JOURNAL_FIELDS
from .paper_desk_images import JournalImages, MAX_BYTES as MAX_IMAGE_BYTES

# OKX also lists one-character bases such as W and S.
SYMBOL = r"^[A-Z0-9]{1,24}-USDT-SWAP$"


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    request_id: str = Field(pattern=r"^[A-Za-z0-9-]{16,100}$")


class AccountRequest(Request):
    name: str = Field(min_length=1, max_length=80)
    initial_cash: str = Field(min_length=1, max_length=40)


class CloseRequest(Request):
    quantity: str = Field(min_length=1, max_length=40)
    quote_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class OpenRequest(CloseRequest):
    side: Literal["long", "short"]
    note: str = Field(default="", max_length=2000)
    leverage: int = Field(default=1, ge=1, le=MAX_LEVERAGE, strict=True)
    take_profit: str | None = Field(default=None, min_length=1, max_length=40)
    stop_loss: str | None = Field(default=None, min_length=1, max_length=40)


class LimitOpenRequest(OpenRequest):
    limit_price: str = Field(min_length=1, max_length=40)


class LimitCloseRequest(CloseRequest):
    limit_price: str = Field(min_length=1, max_length=40)


class ProtectionRequest(Request):
    take_profit: str | None = Field(default=None, min_length=1, max_length=40)
    stop_loss: str | None = Field(default=None, min_length=1, max_length=40)
    quote_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    stop_to_entry: bool = Field(default=False, strict=True)


class JournalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0, strict=True)
    entry_reason: str = Field(max_length=10000)
    analysis: str = Field(max_length=10000)
    plan: str = Field(max_length=10000)
    review: str = Field(max_length=10000)


class ImageVisibility(BaseModel):
    model_config = ConfigDict(extra='forbid')
    deleted: bool = Field(strict=True)


class PublicSymbols:
    """Live USDT linear swaps from OKX's unauthenticated instrument catalog.

    Contract: https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments.
    Catalog freshness is independent of the 15-second execution quote gate.
    """
    def __init__(self, quotes=None, http=None):
        self.refresh_lock = threading.Lock()
        self.background = False
        self.http = http or PublicHTTP()
        self.cache = None
        self.instruments = {}
        self.cached_at = None
        self.quotes = quotes

    def fetch(self):
        return self.http.get('public/instruments', instType='SWAP')

    def refresh(self):
        with self.refresh_lock:
            if self.cached_at is not None and time.monotonic() - self.cached_at < 300:
                return
            rows = self.fetch()
            symbols = set()
            instruments = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("OKX 合约字段结构变化")
                symbol = row.get("instId", "")
                if (isinstance(symbol, str) and re.fullmatch(SYMBOL, symbol)
                        and row.get("instType") == "SWAP" and row.get("state") == "live"
                        and row.get("settleCcy") == "USDT" and row.get("ctType") == "linear"):
                    symbols.add(symbol)
                    instruments[symbol] = dict(row)
            if not symbols:
                raise ValueError("OKX 暂无可用的 USDT 永续合约")
            ordered = sorted(symbols, key=lambda s: (s not in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"), s))
            self.instruments = instruments
            self.cache = [dict(symbol=s, base=s.removesuffix("-USDT-SWAP")) for s in ordered]
            self.cached_at = time.monotonic()

    def get(self):
        if not self.background:
            self.refresh()
        if self.cache is None or self.cached_at is None or time.monotonic()-self.cached_at >= 300:
            raise ValueError('币种列表正在加载，请稍后重试')
        result = [dict(r) for r in self.cache]
        if self.quotes:
            tickers = self.quotes.get([r['symbol'] for r in result], include_risk=False)
            for r in result:
                # Bulk read-only display fields share one fresh ticker snapshot.
                # Missing/stale quotes stay absent, never become a zero return.
                quote = tickers.get(r['symbol'], {})
                for field in ('last', 'change_24h_percent', 'volume_usdt_24h',
                              'source_ms', 'received_ms'):
                    r[field] = quote.get(field)
                r['expires_ms'] = (min(quote['source_ms'], quote['received_ms']) + 15000
                                   if quote else None)
                r['max_leverage'] = 100 if r['base'] in {'BTC', 'ETH'} else 20
            result.sort(key=lambda r: (r['volume_usdt_24h'] is None, -number(r['volume_usdt_24h'] or 0), r['symbol']))
        return result


class PublicQuotes:
    """One short-lived, timestamp-checked cache for the whole paper desk."""
    def __init__(self, risk=None, http=None):
        self.refresh_lock = threading.Lock()
        self.background = False
        self.http = http or PublicHTTP()
        self.cache = {}
        self.cached_at = None
        self.risk = risk

    def fetch(self):
        return self.http.get('market/tickers', instType='SWAP')

    def refresh(self):
        with self.refresh_lock:
            if self.cached_at is not None and time.monotonic() - self.cached_at < 3:
                return
            rows = self.fetch()
            received = clock_ms()
            cache = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("OKX 行情字段结构变化")
                if not re.fullmatch(SYMBOL, str(row.get("instId", ""))):
                    continue
                try:
                    bid, ask = positive(row["bidPx"]), positive(row["askPx"])
                    quote = dict(symbol=row["instId"], bid=str(bid), ask=str(ask),
                                 source_ms=int(row["ts"]), received_ms=received)
                    # Optional display fields must never invalidate an executable quote.
                    for source, target in [('last', 'last'), ('open24h', 'open_24h'),
                                           ('high24h', 'high_24h'), ('low24h', 'low_24h')]:
                        try:
                            quote[target] = str(positive(row[source]))
                        except (KeyError, TypeError, ValueError):
                            pass
                    if 'last' in quote and 'open_24h' in quote:
                        quote['change_24h_percent'] = str((number(quote['last']) / number(quote['open_24h']) - 1) * 100)
                    try:
                        volume = number(row['volCcy24h']) * positive(row['last'])
                        if volume >= 0:
                            quote['volume_usdt_24h'] = str(volume)
                    except (KeyError, TypeError, ValueError):
                        pass
                    if bid <= ask and fresh(quote, received):
                        cache[row["instId"]] = quote
                except (KeyError, TypeError, ValueError):
                    continue
            self.cache, self.cached_at = cache, time.monotonic()

    def get(self, symbols, include_risk=True):
        if not symbols:
            return {}
        if not self.background:
            self.refresh()
        snapshot = self.cache
        result = {s: dict(snapshot[s]) for s in symbols if s in snapshot and fresh(snapshot[s], clock_ms())}
        if self.risk and include_risk:
            try:
                risk = self.risk.get(list(result))
                for symbol, value in result.items():
                    value.update(risk.get(symbol, {}))
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                # Bid/ask exits remain possible; missing risk never fabricates liquidation.
                pass
        return result


def install(api, app, workspace_store):
    desk = PaperDesk(workspace_store.runtime)
    app.state.paper_desk = desk
    http = PublicHTTP()
    app.state.paper_desk_quotes = PublicQuotes(PublicRisk(http), http)
    app.state.paper_desk_symbols = PublicSymbols(app.state.paper_desk_quotes, http)
    app.state.paper_desk_market = MarketRefresh(desk, app.state.paper_desk_quotes,
                                               app.state.paper_desk_symbols, http)
    limits = LimitOrders(desk)
    app.state.paper_desk_limits = limits
    app.state.paper_desk_worker = LimitOrderWorker(limits, app.state.paper_desk_quotes)

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    def marks(symbols):
        try:
            return app.state.paper_desk_quotes.get(symbols)
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(503, "OKX 公开行情暂不可用，请稍后刷新；未创建模拟成交") from None

    def entry_risk(quotes, symbol):
        # Production entry needs public risk metadata; deterministic test sources
        # can supply their own quote implementation without a network dependency.
        if isinstance(app.state.paper_desk_quotes, PublicQuotes) and app.state.paper_desk_quotes.risk:
            q = quotes.get(symbol, {})
            if not q.get('risk') or not -5000 <= clock_ms() - q.get('mark_source_ms', 0) <= 15000:
                raise HTTPException(503, 'OKX 标记价或风险档位暂不可用，暂不新增仓位；已有持仓仍可手动平仓')

    @api.get("/paper-desk")
    def overview():
        error = None
        try:
            quotes = marks(desk.symbols())
        except HTTPException as exc:
            quotes, error = {}, exc.detail
        return dict(desk.snapshot(quotes), market_error=error, limit_worker=app.state.paper_desk_worker.status())

    @api.get('/paper-desk/live')
    def live(account_id: str | None = None):
        if account_id is None:
            with desk.connect() as db:
                row = db.execute('SELECT id FROM accounts ORDER BY created_ms,id LIMIT 1').fetchone()
                account_id = row['id'] if row else None
        quotes = marks(desk.symbols(account_id)) if account_id else {}
        return dict(invoke(desk.snapshot, quotes, account_id, True),
                    limit_worker=app.state.paper_desk_worker.status())

    @api.get('/paper-desk/accounts/{account_id}/records')
    def records(account_id: str, kind: Literal['closed', 'fills'],
                limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return invoke(desk.records, account_id, kind, limit, offset)

    @api.get('/paper-desk/positions/{position_id}/journal')
    def get_journal(position_id: str):
        return invoke(PositionJournal(desk).get, position_id)

    @api.post('/paper-desk/positions/{position_id}/journal')
    def save_journal(position_id: str, payload: JournalRequest):
        content = {field: getattr(payload, field) for field in JOURNAL_FIELDS}
        return invoke(PositionJournal(desk).save, position_id, payload.version, content)

    @api.post('/paper-desk/positions/{position_id}/images')
    async def upload_image(position_id: str, request: HTTPRequest):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_IMAGE_BYTES:
                raise HTTPException(413, '每张图片不得超过 6 MB')
            content.extend(chunk)
        return await run_in_threadpool(invoke, JournalImages(desk).add, position_id,
                                       unquote(request.headers.get('x-filename', '截图')), bytes(content))

    @api.get('/paper-desk/positions/{position_id}/images/{image_id}')
    def read_image(position_id: str, image_id: str):
        item = invoke(JournalImages(desk).get, position_id, image_id)
        return Response(item['data'], media_type=item['mime'], headers={
            'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"default-src 'none'",
            'Cache-Control':'private, max-age=3600', 'Content-Disposition':'inline'})

    @api.post('/paper-desk/positions/{position_id}/images/{image_id}/visibility')
    def image_visibility(position_id: str, image_id: str, payload: ImageVisibility):
        return invoke(JournalImages(desk).visibility, position_id, image_id, payload.deleted)

    def respond(value, include_snapshot):
        if not include_snapshot:
            return value
        item = value.get('position') or value.get('order') or value.get('protection') or value
        account_id = item.get('account_id') or item.get('id')
        try:
            return dict(value, snapshot=live(account_id))
        except Exception:
            logging.getLogger(__name__).exception('Paper receipt committed; live snapshot unavailable')
            # The receipt is committed; a failed display refresh cannot undo it.
            return dict(value, refresh_required=True)

    @api.get('/paper-desk/estimate')
    def estimate(symbol: str = Query(..., pattern=SYMBOL)):
        source = app.state.paper_desk_quotes
        if isinstance(source, PublicQuotes) and source.risk:
            source.risk.want([symbol])
        try:
            values = source.get([symbol], include_risk=False) if isinstance(source, PublicQuotes) else source.get([symbol])
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(503, 'OKX 公开行情暂不可用') from None
        value = values.get(symbol)
        if not value or not fresh(value, clock_ms()):
            raise HTTPException(503, '该合约暂无新鲜有效报价')
        ready = True
        if isinstance(source, PublicQuotes) and source.risk:
            # A floating ticket has no market chart polling to warm this cache.
            # Report readiness from the same snapshots as entry, without moving
            # exchange I/O into a request or weakening the mark-price gate.
            try:
                risk = source.risk.get([symbol]).get(symbol, {})
                ready = bool(risk.get('risk') and -5000 <= clock_ms()-risk.get('mark_source_ms', 0) <= 15000)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                ready = False
        return {**{k: value[k] for k in ['symbol', 'bid', 'ask', 'source_ms', 'received_ms']},
                'entry_ready': ready,
                'expires_ms': min(value['source_ms'], value['received_ms']) + 15000}

    @api.post("/paper-desk/accounts", status_code=201)
    def create(payload: AccountRequest, include_snapshot: bool = False):
        return respond(invoke(desk.create_account, payload.name, payload.initial_cash, payload.request_id), include_snapshot)

    @api.get("/paper-desk/symbols")
    def symbols():
        try:
            return dict(symbols=app.state.paper_desk_symbols.get())
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(503, "OKX 币种列表暂不可用，请重试") from None

    @api.get("/paper-desk/quote")
    def quote(symbol: str = Query(..., pattern=SYMBOL)):
        value = marks([symbol]).get(symbol)
        if value is None:
            raise HTTPException(503, "该合约没有新鲜有效的 OKX 买卖报价，请核对合约名称或稍后重试")
        return invoke(desk.save_quote, value)

    @api.get("/paper-desk/market")
    def market(symbol: str = Query(..., pattern=SYMBOL)):
        source = app.state.paper_desk_quotes
        if isinstance(source, PublicQuotes) and source.risk:
            source.risk.want([symbol])
        # Read-only display polling must not create execution receipts/quote rows.
        try:
            value = app.state.paper_desk_quotes.get([symbol], include_risk=False).get(symbol)
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(503, 'OKX 最新行情暂不可用') from None
        if not value or not fresh(value, clock_ms()) or not value.get('last'):
            raise HTTPException(503, '该合约暂无新鲜最新成交价')
        fields = ['symbol', 'last', 'bid', 'ask', 'open_24h', 'high_24h', 'low_24h',
                  'change_24h_percent', 'volume_usdt_24h', 'source_ms', 'received_ms']
        return {**{key: value.get(key) for key in fields}, 'expires_ms': value['source_ms'] + 15000}

    @api.post("/paper-desk/accounts/{account_id}/open", status_code=201)
    def open_position(account_id: str, payload: OpenRequest, include_snapshot: bool = False):
        # A retry after a lost response must return its receipt even if the
        # quote expired or the market is now unavailable.
        with desk.connect() as db:
            known = db.execute("SELECT 1 FROM receipts WHERE request_id=?", (payload.request_id,)).fetchone()
            symbol = None if known else invoke(desk.row, db, 'quotes', payload.quote_id)['symbol']
        quotes = {} if known else marks(sorted(set(desk.symbols(account_id)) | {symbol}))
        if not known:
            entry_risk(quotes, symbol)
        return respond(invoke(desk.open, account_id, payload.side, payload.quantity, payload.quote_id,
                      payload.note, payload.request_id, quotes, payload.leverage, payload.take_profit, payload.stop_loss), include_snapshot)

    @api.get("/paper-desk/open-preview")
    def open_preview(account_id: str, symbol: str = Query(..., pattern=SYMBOL),
                     side: Literal["long", "short"] = "long", leverage: int = Query(1),
                     position_multiple: int | None = Query(None),
                     notional_usdt: str | None = Query(None, min_length=1, max_length=40),
                     limit_price: str | None = Query(None, max_length=40),
                     take_profit: str | None = Query(None, min_length=1, max_length=40),
                     stop_loss: str | None = Query(None, min_length=1, max_length=40)):
        if position_multiple is not None and notional_usdt is not None:
            raise HTTPException(422, '仓位倍数和 USDT 金额只能选择一种')
        quotes = marks(sorted(set(desk.symbols(account_id)) | {symbol}))
        value = quotes.get(symbol)
        if value is None:
            raise HTTPException(503, "该合约没有新鲜有效的 OKX 买卖报价，请稍后重试")
        entry_risk(quotes, symbol)
        quote = invoke(desk.save_quote, value)
        if notional_usdt is not None:
            sizing = invoke(desk.size_by_notional, account_id, side, quote['id'], leverage,
                            notional_usdt, quotes, limit_price, take_profit, stop_loss)
        elif position_multiple is None:
            sizing = invoke(desk.size_all_in, account_id, side, quote["id"], leverage, quotes, limit_price, take_profit, stop_loss)
        else:
            sizing = invoke(desk.size_by_equity, account_id, side, quote["id"], leverage,
                            position_multiple, quotes, limit_price, take_profit, stop_loss)
        return dict(quote, **sizing)

    @api.post("/paper-desk/positions/{position_id}/close")
    def close_position(position_id: str, payload: CloseRequest, include_snapshot: bool = False):
        return respond(invoke(desk.close, position_id, payload.quantity, payload.quote_id, payload.request_id), include_snapshot)

    @api.get("/paper-desk/positions/{position_id}/close-preview")
    def close_preview(position_id: str, percentage: int = Query(..., ge=1, le=100), limit_price: str | None = Query(None, max_length=40)):
        with desk.connect() as db:
            position = invoke(desk.row, db, "positions", position_id)
        quote = marks([position["symbol"]]).get(position["symbol"])
        if quote is None:
            raise HTTPException(503, "该持仓暂无新鲜有效报价，请稍后重试")
        saved = invoke(desk.save_quote, quote)
        rate = invoke(limit_fee, position['side'], saved, limit_price, 'close') if limit_price else TAKER_FEE
        return dict(saved, fee_rate=str(rate), fee_role='taker' if rate == TAKER_FEE else 'maker',
                    **invoke(desk.size_close, position_id, percentage, saved["id"]))

    @api.post("/paper-desk/positions/{position_id}/protection")
    def update_protection(position_id: str, payload: ProtectionRequest, include_snapshot: bool = False):
        return respond(invoke(desk.update_protection, position_id, payload.take_profit, payload.stop_loss,
                      payload.quote_id, payload.request_id, stop_to_entry=payload.stop_to_entry), include_snapshot)

    @api.post("/paper-desk/accounts/{account_id}/limit-open", status_code=201)
    def limit_open(account_id: str, payload: LimitOpenRequest, include_snapshot: bool = False):
        with desk.connect() as db:
            known = db.execute("SELECT 1 FROM receipts WHERE request_id=?", (payload.request_id,)).fetchone()
            symbol = None if known else invoke(desk.row, db, 'quotes', payload.quote_id)['symbol']
        quotes = {} if known else marks(sorted(set(desk.symbols(account_id)) | {symbol}))
        if not known:
            entry_risk(quotes, symbol)
        return respond(invoke(limits.place, "open", account_id, payload.quantity, payload.limit_price,
                      payload.quote_id, payload.request_id, quotes, payload.side, payload.leverage, payload.note,
                      payload.take_profit, payload.stop_loss), include_snapshot)

    @api.post("/paper-desk/positions/{position_id}/limit-close", status_code=201)
    def limit_close(position_id: str, payload: LimitCloseRequest, include_snapshot: bool = False):
        return respond(invoke(limits.place, "close", position_id, payload.quantity, payload.limit_price,
                      payload.quote_id, payload.request_id), include_snapshot)

    @api.post("/paper-desk/orders/{order_id}/cancel")
    def cancel_limit(order_id: str, payload: Request, include_snapshot: bool = False):
        return respond(invoke(limits.cancel, order_id, payload.request_id), include_snapshot)

    @api.get("/paper-desk/accounts/{account_id}/export")
    def export(account_id: str):
        value = invoke(desk.export, account_id)
        return Response(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="paper-account.json"'})

    @api.get('/paper-desk/accounts/{account_id}/history')
    def account_history(account_id: str, month: str | None = Query(None, pattern=r'^\d{4}-\d{2}$')):
        from .paper_desk_analytics import history
        return invoke(history, desk, account_id, month)
