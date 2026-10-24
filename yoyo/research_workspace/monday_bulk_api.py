"""Paper-only API for explicit Monday 1.618 universe order campaigns.

The existing Monday monitor supplies completed New York Monday levels; the
paper desk supplies public quotes, collateral checks and native paper fills.
No exchange execution client or account credential participates in this API.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notional_usdt: str = Field(min_length=1, max_length=40)
    leverage: int = Field(strict=True, ge=1, le=125)
    direction: Literal['both', 'long', 'short']
    mode: Literal['once', 'weekly']
    take_profit: Literal['none', 'near', 'far']
    stop_loss_percent: Optional[str] = Field(default=None, max_length=40)


class Configure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings: Optional[Settings] = None
    enabled: bool = Field(strict=True)
    version: int = Field(strict=True, ge=0)
    request_id: str = Field(min_length=16, max_length=100)


def install(api, app, desk, limits, invoke):
    from .monday_bulk import MondayBulk

    def snapshot():
        service = getattr(app.state, 'monday_fib', None)
        if service is None:
            raise ValueError('周一 1.618 行情服务暂不可用')
        return service.snapshot(compact=True)

    service = MondayBulk(
        desk, limits, snapshot,
        lambda symbols: app.state.paper_desk_quotes.get(symbols),
        lambda: app.state.paper_desk_symbols.get(),
    )
    app.state.paper_monday_bulk = service

    @api.get('/paper-desk/accounts/{account_id}/monday-bulk')
    def status(account_id: str):
        return invoke(service.status, account_id)

    @api.post('/paper-desk/accounts/{account_id}/monday-bulk/preview')
    def preview(account_id: str, payload: Settings):
        return invoke(service.preview, account_id, payload.model_dump())

    @api.post('/paper-desk/accounts/{account_id}/monday-bulk/configure')
    def configure(account_id: str, payload: Configure):
        return invoke(service.configure, account_id,
                      payload.settings.model_dump() if payload.settings else None,
                      payload.enabled, payload.request_id, payload.version)
