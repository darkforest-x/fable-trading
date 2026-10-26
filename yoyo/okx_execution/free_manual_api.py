"""Same-origin free-ticket previews and owner confirmations; exchange reads only.

Reuses the private manual account and its existing locked worker. The HTTP
process never changes leverage or sends/cancels exchange orders.
"""
from typing import Literal, Optional
import re
from pydantic import BaseModel, ConfigDict, Field
from .free_manual import REVISION, Tickets, clean, make_plan
from .client import Client
from .preview_reads import preview_engine

NUMBER = r'^\d{1,12}(\.\d{1,18})?$'


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticket_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    symbol: str = Field(pattern=r'^[A-Z0-9]{1,32}-USDT-SWAP$')
    side: Literal['long', 'short']
    order_type: Literal['market', 'limit']
    notional_usdt: Optional[str] = Field(default=None, pattern=NUMBER)
    leverage: Optional[str] = Field(default=None, pattern=r'^\d{1,3}$')
    allocation_percent: str = Field(default='100', pattern=NUMBER)
    limit_price: Optional[str] = Field(default=None, pattern=NUMBER)
    take_profit: Optional[str] = Field(default=None, pattern=NUMBER)
    stop_loss: Optional[str] = Field(default=None, pattern=NUMBER)
    entry_policy: Optional[Literal['monday_1618']] = None
    range_week: Optional[str] = None


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acknowledgement: Literal['CONFIRM-FREE-LIVE-ORDER']


class ManagementPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticket_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    action: Literal['close', 'stop', 'cancel', 'protection']
    source: Literal['free', 'signal'] = 'free'
    stop: Optional[str] = Field(default=None, pattern=NUMBER)
    take_profit: Optional[str] = Field(default=None, pattern=NUMBER)
    percentage: str = Field(default='100', pattern=NUMBER)


class ManagementConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acknowledgement: Literal['CONFIRM-FREE-MANAGEMENT']


def install(router, service, call, app=None):
    from .free_management import Management, REVISION as MANAGEMENT_REVISION, confirmed_tickets, public
    from .manual_commands import Commands

    def command_book(source):
        return Commands(service.runtime) if source == 'signal' else Management(service.runtime)

    def management_ready():
        service.close_ready()
        engine = service.engine()
        if engine.store.meta('free_management_revision') != MANAGEMENT_REVISION:
            raise ValueError('快捷持仓管理服务尚未更新，请稍后重试')
        return engine

    @router.get('/free/ledger')
    def ledger():
        def run():
            result = service.account_reader.snapshot()
            engine = service.engine()
            result['free_tickets'] = [clean(t) for t in confirmed_tickets(service.runtime)
                                      if t['account_uid'] == engine.store.meta('account_uid')]
            result['management_pending'] = [dict(public(c), source=source)
                for source in ('free', 'signal') for c in command_book(source).pending()]
            try:
                service.close_ready()
                result['signal_management_ready'] = True
            except ValueError:
                result['signal_management_ready'] = False
            try:
                management_ready()
                result['management_ready'] = True
            except ValueError:
                result['management_ready'] = False
            return result
        return call(run)

    @router.post('/free/manage/preview')
    def manage_preview(payload: ManagementPreview):
        def run():
            if payload.source == 'signal':
                if payload.action == 'protection' or payload.percentage != '100' or payload.take_profit is not None:
                    raise ValueError('策略分笔请从原入口管理，本页仅支持手动挂单的部分平仓与止盈修改')
                service.close_ready()
                engine = service.engine()
            else:
                engine = management_ready()
            opener = service.preview_opener() if isinstance(engine.client, Client) else None
            with preview_engine(engine, service.preview_pacer, opener) as local:
                kwargs = {} if payload.source == 'signal' else dict(percentage=payload.percentage, take_profit=payload.take_profit)
                return dict(public(command_book(payload.source).preview(local, payload.ticket_id, payload.action, payload.stop, **kwargs)), source=payload.source)
        return call(run)

    @router.post('/free/manage/{identity}/confirm')
    def manage_confirm(identity: str, payload: ManagementConfirmation, source: Literal['free', 'signal'] = 'free'):
        def run():
            if source == 'signal':
                service.close_ready()
                engine = service.engine()
            else:
                engine = management_ready()
            book = command_book(source)
            if book.get(identity)['account_uid'] != engine.store.meta('account_uid'):
                raise ValueError('执行账户已变化')
            return dict(public(book.confirm(identity, service.clock())), source=source)
        return call(run)

    @router.get('/free/manage/{identity}')
    def manage_status(identity: str, source: Literal['free', 'signal'] = 'free'):
        return call(lambda: dict(public(command_book(source).get(identity)), source=source))

    def ready():
        engine = service.engine()
        service.healthy(engine)
        if engine.store.meta('free_manual_revision') != REVISION:
            raise ValueError('自由手动执行服务尚未更新，请稍后重试')
        return engine

    def status(identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise ValueError('订单编号无效')
        value = Tickets(service.runtime).get(identity)
        if value['state'] == 'preview' and service.clock() >= value['expires_ms']:
            value = dict(value, state='expired')
        return clean(value)

    def terms(payload):
        value = payload.model_dump()
        if payload.entry_policy:
            reader = getattr(app.state, 'monday_chart_levels', None) if app else None
            if reader is None:
                raise ValueError('周一区间服务尚未就绪')
            value['monday_range'] = reader.read_ticket(payload.symbol, service.clock())
        return value

    @router.post('/free/defaults')
    def defaults(payload: Preview):
        def run():
            engine = ready()
            opener = service.preview_opener() if isinstance(engine.client, Client) else None
            with preview_engine(engine, service.preview_pacer, opener) as local:
                local.client.prefetch(payload.symbol, shared=True)
                return clean(make_plan(local, terms(payload), defaults_only=True))
        return call(run)

    @router.post('/free/preview')
    def preview(payload: Preview):
        def run():
            engine = ready()
            if payload.order_type == 'limit' and payload.limit_price is None:
                raise ValueError('请输入限价')
            if payload.order_type == 'market' and payload.limit_price is not None:
                raise ValueError('市价委托不能包含限价')
            opener = service.preview_opener() if isinstance(engine.client, Client) else None
            with preview_engine(engine, service.preview_pacer, opener) as local:
                local.client.prefetch(payload.symbol, shared=True)
                plan = make_plan(local, terms(payload))
            ready()
            return clean(Tickets(service.runtime).preview(plan))
        return call(run)

    @router.get('/free/recent')
    def recent():
        return call(lambda: dict(items=[clean(t) for t in Tickets(service.runtime).recent()]))

    @router.post('/free/{identity}/confirm')
    def confirm(identity: str, payload: Confirmation):
        def run():
            value = status(identity)
            if value.get('confirmed_ms'):
                return value
            engine = ready()
            return clean(Tickets(service.runtime).confirm(identity, service.clock(), engine.store.meta('account_uid')))
        return call(run)

    @router.get('/free/{identity}')
    def read(identity: str):
        return call(status, identity)
