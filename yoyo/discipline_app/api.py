"""Authenticated API; server-owned checks cover every risk-changing command.

This module exports a separate ASGI application and never mounts routes into the
existing monitor. Policy, market prices, cash and clock have no mobile setters.
"""
import hashlib
import hmac
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal, Optional, Union
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictFloat, StrictBool, SecretStr
from fastapi.exceptions import RequestValidationError
from .book import Book
from .market import PublicMarket
from .candles import CandleHistory, CandleUnavailable
from .monday_fib import MondayFibFeed, FibUnavailable
from .monitor_feed import MonitorFeed, MonitorUnavailable, MonthTouchNotFound
from .push import PushService, PushUnavailable
from .wick_stop import WickStops
from .policy import Rejected


class Ticket(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(pattern=r'^[A-Z0-9]{1,40}-USDT-SWAP$')
    side: Literal['long', 'short']
    kind: Literal['market', 'limit']
    price: str = Field(max_length=32)
    stop: str = Field(max_length=32)
    fraction: str = Field(max_length=20)
    sizing_mode: Optional[Literal['risk', 'margin', 'exposure']] = None
    # Legacy policies still require integral leverage. Exposure policies treat
    # this as a hint only and return the resolved tier in plan.leverage.
    leverage: Union[StrictInt, StrictFloat] = Field(ge=1, le=1000)


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


class PushDevice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[0-9a-f]{64,200}$')
    environment: Literal['development', 'production']
    fib: StrictBool = True
    alarm: StrictBool = True
    month_first_week_touch: StrictBool = True


class PushPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[0-9a-f]{64,200}$')
    fib: StrictBool = True
    alarm: StrictBool = True
    month_first_week_touch: StrictBool = True


class PushRemove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[0-9a-f]{64,200}$')


class PushActivity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[0-9a-f]{64,200}$')
    environment: Literal['development', 'production']
    position_id: str = Field(pattern=r'^[\w.:-]{1,80}$')


class BarkSwitch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=30)
    key: SecretStr = Field(min_length=1, max_length=256)
    secret: SecretStr = Field(min_length=1, max_length=256)
    passphrase: SecretStr = Field(min_length=1, max_length=256)


def create_app(book: Book, token: str, market_enabled=True, origins=(), web_directory=None, monitor_feed=None):
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
            app.state.push.start()
        yield
        app.state.push.stop()
        app.state.replay.stop()
        order_requests.shutdown()
        if market_enabled:
            market.stop()
            if live:
                book.stop()

    app = FastAPI(title='SPIKE Discipline', version='1.0.2', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.candle_history = CandleHistory()
    app.state.monday_fib = MondayFibFeed()
    app.state.monitor = monitor_feed or MonitorFeed()
    app.state.wick_stops = WickStops()
    from .replay_store import ReplayStore
    runtime = book.runtime if hasattr(book, 'runtime') else Path(book.path).parent
    app.state.replay = ReplayStore(runtime / 'replay' / 'sessions.sqlite3')
    # Native push (owner 2026-11-08): idle until the owner adds an APNs key under <runtime>/push.
    app.state.push = PushService(runtime / 'push', app.state.monitor,
                                 lambda account: book.resolve(account) if hasattr(book, 'resolve') else book)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=['GET', 'POST'],
                       allow_headers=['Authorization', 'Content-Type', 'X-Spike-Account'], allow_credentials=False)
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

    def auth(authorization: str = Header(default='')):
        value = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
        if not hmac.compare_digest(hashlib.sha256(value.encode()).digest(), digest):
            raise HTTPException(401, '需要有效的服务访问令牌')

    from .replay_api import install as install_replay
    install_replay(app, auth)

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
        return dict(service='spike-discipline', mode=mode, live_enabled=live and book.client.write_enabled, version='1.0.2')

    @app.get('/v1/market/monday-fib')
    def monday_fib():
        # Public market-only projection; never forwards notification/account fields.
        try:
            return app.state.monday_fib.read()
        except FibUnavailable:
            raise HTTPException(503, '1.618 监控暂不可用，请稍后重试') from None

    @app.get('/v1/market/monday-wick', dependencies=[Depends(auth)])
    def monday_wick(symbol: str = Query(pattern=r'^[A-Z0-9]{1,40}-USDT-SWAP$'),
                    period: Literal['1m', '5m', '15m', '1H', '4H', '1D'] = '15m',
                    side: Literal['long', 'short'] = 'long'):
        try:
            return app.state.wick_stops.read(app.state.monday_fib, app.state.candle_history, symbol, period, side)
        except FibUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/market/candles', dependencies=[Depends(auth)])
    def candles(symbol: str = Query(pattern=r'^[A-Z0-9]{1,40}-USDT-SWAP$'),
                period: Literal['1m', '5m', '15m', '1H', '4H', '1D'] = '15m',
                before: Optional[int] = Query(default=None, gt=0, lt=10_000_000_000)):
        try:
            return app.state.candle_history.read(symbol, period, before)
        except (CandleUnavailable, TimeoutError):
            raise HTTPException(503, '历史K线暂不可用，请重试') from None

    # Monitor workbench modules (read-only, GET only). Paper accounts are simulated;
    # no alarm ack / paper close / notification route is proxied. See monitor_feed.py.
    @app.get('/v1/monitor/paper', dependencies=[Depends(auth)])
    def monitor_paper(track: str = Query(default='live-wick6x3', pattern=r'^[a-z0-9-]{1,20}$')):
        try:
            return app.state.monitor.paper(track)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    # 2.7.0 strategy frame (owner 2026-11-09): older records, booklets and saved backtest trades. GET only;
    # all four read the monitor's published files/routes (monitor_feed.py, strategy_book.py).
    @app.get('/v1/monitor/paper/history', dependencies=[Depends(auth)])
    def monitor_paper_history(track: str = Query(pattern=r'^[a-z0-9-]{1,20}$'),
                              tab: Literal['closed', 'events'] = 'closed', offset: int = Query(default=0, ge=0, le=100000)):
        try:
            return app.state.monitor.paper_history(track, tab, offset)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/booklet', dependencies=[Depends(auth)])
    def monitor_booklet(track: str = Query(pattern=r'^[a-z0-9-]{1,20}$'),
                        view: Literal['legacy', 'native-v2'] = 'legacy'):
        try:
            return app.state.monitor.booklet(track, view=view)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/backtest', dependencies=[Depends(auth)])
    def monitor_backtest(track: str = Query(pattern=r'^[a-z0-9-]{1,20}$'), sort: Literal['time', 'pnl'] = 'time',
                         direction: Literal['asc', 'desc'] = 'desc', offset: int = Query(default=0, ge=0, le=100000)):
        try:
            return app.state.monitor.backtest(track, sort, direction, offset)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/backtest/candles', dependencies=[Depends(auth)])
    def monitor_backtest_candles(track: str = Query(pattern=r'^[a-z0-9-]{1,20}$'), trade: int = Query(gt=0, lt=10_000_000_000),
                                 period: Literal['5m', '15m', '1H', '4H', '12H'] = '1H'):
        if track == 'live-cloud-v17' and period != '12H':
            raise HTTPException(422, '云带历史仅提供原始 12H K 线')
        if track != 'live-cloud-v17' and period == '12H':
            raise HTTPException(422, '这个策略没有 12H 回放数据')
        try:
            return app.state.monitor.backtest_candles(track, trade, period)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/alerts', dependencies=[Depends(auth)])
    def monitor_alerts():
        try:
            return app.state.monitor.alerts()
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/cloud', dependencies=[Depends(auth)])
    def monitor_cloud(side: Literal['all', 'long', 'short'] = 'all', scope: Literal['live', 'all'] = 'live'):
        try:
            return app.state.monitor.cloud({'all': 0, 'long': 1, 'short': -1}[side], scope)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    # Native push registration (owner 2026-11-08). Tokens only address this phone; no order effect.
    @app.get('/v1/push/status', dependencies=[Depends(auth)])
    def push_status():
        return app.state.push.status()

    @app.post('/v1/push/device', dependencies=[Depends(auth)])
    def push_device(body: PushDevice):
        try:
            return app.state.push.register(body.token, body.environment, body.fib, body.alarm,
                                           body.month_first_week_touch)
        except PushUnavailable as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/v1/push/preferences', dependencies=[Depends(auth)])
    def push_preferences(body: PushPreferences):
        """Update this phone's notification permissions without re-registering its APNs token."""
        try:
            return app.state.push.set_preferences(body.token, body.fib, body.alarm, body.month_first_week_touch)
        except PushUnavailable as error:
            raise HTTPException(409, str(error)) from None

    @app.post('/v1/push/device/remove', dependencies=[Depends(auth)])
    def push_remove(body: PushRemove):
        return app.state.push.remove(body.token)

    @app.post('/v1/push/activity', dependencies=[Depends(auth)])
    def push_activity(body: PushActivity, x_spike_account: str = Header(default='')):
        try:
            return app.state.push.follow(body.token, body.environment, x_spike_account or 'primary', body.position_id)
        except PushUnavailable as error:
            raise HTTPException(422, str(error)) from None

    @app.get('/v1/monitor/touch-scores', dependencies=[Depends(auth)])
    def monitor_touch_scores():
        try:
            return app.state.monitor.touch_scores()
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/notifications', dependencies=[Depends(auth)])
    def monitor_notifications():
        try:
            return app.state.monitor.notifications()
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/notifications', dependencies=[Depends(auth)])
    def unified_notifications():
        """New-client inbox; the legacy monitor route intentionally remains byte-compatible."""
        try:
            snapshot = app.state.monitor.notifications()
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None
        try:
            month_snapshot = app.state.monitor.month_first_week_1618()
        except (MonitorUnavailable, AttributeError):
            # Existing alarms and weekly touches remain available while the additive monthly source starts.
            return snapshot
        return app.state.push.enrich_notifications(snapshot, month_snapshot)

    @app.get('/v1/monitor/month-first-week-1618', dependencies=[Depends(auth)])
    def monitor_month_first_week_1618():
        try:
            return app.state.monitor.month_first_week_1618()
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/month-first-week-1618/levels', dependencies=[Depends(auth)])
    def month_levels(symbol: str = Query(..., pattern=r'^[A-Z0-9]{1,32}-USDT-SWAP$')):
        try:
            return app.state.monitor.month_first_week_levels(symbol)
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.get('/v1/monitor/month-first-week-1618/chart', dependencies=[Depends(auth)])
    def month_chart(event_id: Optional[str] = Query(None, min_length=1, max_length=96),
                    symbol: Optional[str] = Query(None, pattern=r'^[A-Z0-9]{1,32}-USDT-SWAP$'),
                    period: str = Query('1H', pattern=r'^(1m|2m|3m|5m|15m|30m|1H|4H|12H|1D)$')):
        if bool(event_id) == bool(symbol):
            raise HTTPException(422, '请选择一条触价或一个合约')
        try:
            return app.state.monitor.month_first_week_chart(event_id, period, symbol)
        except MonthTouchNotFound as error:
            raise HTTPException(404, str(error)) from None
        except MonitorUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @app.post('/v1/monitor/month-first-week-1618/bark', dependencies=[Depends(auth)])
    def monitor_month_first_week_bark(body: BarkSwitch):
        """Change only the monthly-touch Bark switch; the loopback monitor owns delivery and dedupe."""
        try:
            month_snapshot = app.state.monitor.set_month_first_week_bark(body.enabled)
            snapshot = app.state.monitor.notifications()
            return app.state.push.enrich_notifications(snapshot, month_snapshot)
        except MonitorUnavailable as error:
            raise HTTPException(409, str(error)) from None

    # The single monitor write (owner 2026-11-06): switch 1.618 Bark pushes. No order or ledger effect.
    @app.post('/v1/monitor/fib-bark', dependencies=[Depends(auth)])
    def monitor_fib_bark(body: BarkSwitch):
        try:
            return app.state.monitor.set_fib_bark(body.enabled)
        except MonitorUnavailable as error:
            raise HTTPException(409, str(error)) from None

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
