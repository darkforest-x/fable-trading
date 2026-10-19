"""Authenticated API; server-owned checks cover every risk-changing command.

This module exports a separate ASGI application and never mounts routes into the
existing monitor. Policy, market prices, cash and clock have no mobile setters.
"""
import hashlib
import hmac
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal, Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, SecretStr
from fastapi.exceptions import RequestValidationError
from .book import Book
from .market import PublicMarket
from .candles import CandleHistory, CandleUnavailable
from .policy import Rejected


class Ticket(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(pattern=r'^[A-Z0-9]{1,40}-USDT-SWAP$')
    side: Literal['long', 'short']
    kind: Literal['market', 'limit']
    price: str = Field(max_length=32)
    stop: str = Field(max_length=32)
    fraction: str = Field(max_length=20)
    sizing_mode: Literal['risk', 'margin'] = 'risk'
    leverage: StrictInt = Field(ge=1, le=100)


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=16, max_length=80)
    policy_revision: StrictInt
    ticket: Ticket
    preview_id: Optional[str] = Field(default=None, max_length=80)
    confirmed_live: StrictBool = False


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=16, max_length=80)
    confirmed_live: StrictBool = False


class Tighten(Command):
    stop: str = Field(max_length=32)


class Close(Command):
    fraction: str = Field(default='1', max_length=20)


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=30)
    key: SecretStr = Field(min_length=1, max_length=256)
    secret: SecretStr = Field(min_length=1, max_length=256)
    passphrase: SecretStr = Field(min_length=1, max_length=256)


def create_app(book: Book, token: str, market_enabled=True, origins=(), web_directory=None):
    if len(token) < 32:
        raise ValueError('Service authentication token is too short')
    digest = hashlib.sha256(token.encode()).digest()
    market = PublicMarket(book)
    mode = getattr(book, 'mode', 'paper')
    live = mode == 'okx_live'
    from .order_requests import OrderRequests
    order_requests = OrderRequests()

    @asynccontextmanager
    async def lifespan(_):
        if market_enabled:
            market.start()
            if live:
                book.start()
        yield
        order_requests.shutdown()
        if market_enabled:
            market.stop()
            if live:
                book.stop()

    app = FastAPI(title='SPIKE Discipline', version='0.8.14', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.candle_history = CandleHistory()
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=['GET', 'POST'],
                       allow_headers=['Authorization', 'Content-Type', 'X-Spike-Account'], allow_credentials=False)
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

    def auth(authorization: str = Header(default='')):
        value = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
        if not hmac.compare_digest(hashlib.sha256(value.encode()).digest(), digest):
            raise HTTPException(401, '需要有效的服务访问令牌')

    def selected(request: Request, x_spike_account: str = Header(default='')):
        if hasattr(book, 'resolve'):
            if request.method == 'POST' and not x_spike_account:
                raise Rejected('请选择交易账户，旧版本 App 请更新后重新连接')
            return book.resolve(x_spike_account)
        if x_spike_account not in ('', 'primary'):
            raise Rejected('交易账户不存在')
        return book

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_, exc):
        # Pydantic's default error payload includes raw input, including secrets.
        return JSONResponse(status_code=422, content={'detail':'请求参数无效，请检查填写内容'})

    @app.middleware('http')
    async def limit_body(request: Request, call_next):
        if request.method == 'POST':
            length = request.headers.get('content-length', '')
            if not length.isdigit() or int(length) > 8192:
                return JSONResponse(status_code=413, content={'detail': '请求体过大或缺少长度'})
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(Rejected)
    async def rejected(_, exc):
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    from .okx_execution import ExchangeUncertain
    @app.exception_handler(ExchangeUncertain)
    async def uncertain(_, exc):
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    @app.get('/health')
    def health():
        return dict(service='spike-discipline', mode=mode, live_enabled=live and book.client.write_enabled, version='0.8.14')

    @app.get('/v1/market/candles', dependencies=[Depends(auth)])
    def candles(symbol: str = Query(pattern=r'^[A-Z0-9]{1,40}-USDT-SWAP$'),
                period: Literal['1m', '5m', '15m', '1H', '4H', '1D'] = '15m',
                before: Optional[int] = Query(default=None, gt=0, lt=10_000_000_000)):
        try:
            return app.state.candle_history.read(symbol, period, before)
        except (CandleUnavailable, TimeoutError):
            raise HTTPException(503, '历史K线暂不可用，请重试') from None

    @app.get('/v1/state', dependencies=[Depends(auth)])
    def state(current=Depends(selected), x_spike_account: str = Header(default='')):
        identity = x_spike_account or 'primary'
        name = next((r['name'] for r in getattr(book, 'records', []) if r['id']==identity), '主账户')
        # The ledger already emits JSON-safe primitives. Avoid recursively
        # re-encoding ~500 contracts through FastAPI on every polling request.
        return JSONResponse(dict(current.snapshot(), account_id=identity, account_name=name))

    @app.get('/v1/events', dependencies=[Depends(auth)])
    def events(current=Depends(selected)):
        return dict(items=current.events())

    @app.get('/v1/exchange-status', dependencies=[Depends(auth)])
    def exchange_status(current=Depends(selected)):
        if live:
            state = current.snapshot()
            return dict(connected=not state['private_error'],environment=mode,execution_enabled=current.client.write_enabled,
                verified_at=state['private_at'],trade_permission=True,position_mode='long_short_mode',error=state['private_error'])
        from .okx_demo import read_status, DemoError
        try:
            return read_status(current.path.parent)
        except (DemoError, ValueError, OSError):
            return dict(connected=False, environment='okx_live', execution_enabled=False,
                        error='暂时无法读取账户核验记录')

    @app.get('/v1/statistics', dependencies=[Depends(auth)])
    def statistics(days: int = Query(default=30), current=Depends(selected), x_spike_account: str = Header(default='')):
        return dict(current.statistics(days), account_id=x_spike_account or 'primary')

    @app.post('/v1/preview', dependencies=[Depends(auth)])
    def preview(body: Ticket, current=Depends(selected)):
        return current.preview(body.model_dump())

    @app.post('/v1/auto-stop', dependencies=[Depends(auth)])
    def auto_stop(body: Ticket, current=Depends(selected)):
        return current.auto_stop(body.model_dump())

    @app.post('/v1/orders', dependencies=[Depends(auth)])
    def enter(body: Entry, current=Depends(selected)):
        if live:
            return current.enter(body.request_id, body.ticket.model_dump(), body.policy_revision, body.preview_id, body.confirmed_live)
        if body.confirmed_live:
            raise Rejected('服务当前为模拟模式，不接受实盘指令')
        return current.enter(body.request_id, body.ticket.model_dump(), body.policy_revision)

    @app.post('/v1/order-requests', dependencies=[Depends(auth)])
    def submit_order(body: Entry, current=Depends(selected)):
        if not live:
            raise Rejected('异步请求仅适用于实盘服务')
        return JSONResponse(order_requests.submit(current, body.model_dump()), status_code=202)

    @app.get('/v1/order-requests/{identity}', dependencies=[Depends(auth)])
    def order_status(identity: str, current=Depends(selected)):
        if not live or not 16 <= len(identity) <= 80 or not identity.isalnum():
            raise Rejected('请求编号无效')
        return order_requests.status(current, identity)

    @app.post('/v1/order-requests/{identity}/abandon-unreceived', dependencies=[Depends(auth)])
    def abandon_unreceived(identity: str, current=Depends(selected)):
        if not live or not 16 <= len(identity) <= 80 or not identity.isalnum():
            raise Rejected('请求编号无效')
        return order_requests.abandon_unreceived(current, identity)

    @app.post('/v1/orders/{identity}/cancel', dependencies=[Depends(auth)])
    def cancel(identity: str, body: Command, current=Depends(selected)):
        return current.cancel(body.request_id, identity)

    @app.post('/v1/positions/{identity}/close', dependencies=[Depends(auth)])
    def close(identity: str, body: Close, current=Depends(selected)):
        if live and not body.confirmed_live:
            raise Rejected('请在 App 明确确认本次实盘平仓')
        return current.close(body.request_id, identity, body.fraction)

    @app.get('/v1/close-requests/{identity}', dependencies=[Depends(auth)])
    def close_status(identity: str, current=Depends(selected)):
        if not live or not 16 <= len(identity) <= 80 or not identity.isalnum():
            raise Rejected('平仓请求编号无效')
        return current.close_status(identity)

    @app.post('/v1/close-requests/{identity}/abandon-unreceived', dependencies=[Depends(auth)])
    def abandon_close(identity: str, current=Depends(selected)):
        if not live or not 16 <= len(identity) <= 80 or not identity.isalnum():
            raise Rejected('平仓请求编号无效')
        return current.abandon_unreceived_close(identity)

    @app.post('/v1/positions/{identity}/stop', dependencies=[Depends(auth)])
    def tighten(identity: str, body: Tighten, current=Depends(selected)):
        return current.tighten(body.request_id, identity, body.stop)

    @app.post('/v1/positions/{identity}/entry-stop', dependencies=[Depends(auth)])
    def entry_stop(identity: str, body: Command, current=Depends(selected)):
        if live and not body.confirmed_live:
            raise Rejected('请在 App 明确发起本次实盘止损调整')
        return current.entry_stop(body.request_id, identity)

    @app.get('/v1/accounts', dependencies=[Depends(auth)])
    def accounts():
        if hasattr(book, 'list'):
            return book.list()
        return dict(items=[dict(id='primary',name='主账户',environment=mode)],default_account_id='primary')

    @app.post('/v1/accounts', dependencies=[Depends(auth)])
    def add_account(body: AccountInput):
        if not hasattr(book, 'add'):
            raise Rejected('当前服务不支持添加实盘账户')
        return book.add(body.name, body.key.get_secret_value(),body.secret.get_secret_value(),body.passphrase.get_secret_value())

    web = Path(web_directory) if web_directory else Path(__file__).parent / 'mobile' / 'dist'
    if web.is_dir():
        app.mount('/assets', StaticFiles(directory=web / 'assets'), name='mobile-assets')

        @app.get('/')
        def mobile_preview():
            return FileResponse(web / 'index.html')
    return app
