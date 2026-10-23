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
    notional_usdt: str = Field(pattern=NUMBER)
    leverage: str = Field(pattern=r'^\d{1,3}$')
    limit_price: Optional[str] = Field(default=None, pattern=NUMBER)
    take_profit: Optional[str] = Field(default=None, pattern=NUMBER)
    stop_loss: Optional[str] = Field(default=None, pattern=NUMBER)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acknowledgement: Literal['CONFIRM-FREE-LIVE-ORDER']


def install(router, service, call):
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
                plan = make_plan(local, payload.model_dump())
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
