"""Owner-reviewed Pro contracts; no default live activation or strategy preset."""
from typing import Literal, Optional
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = str
SOURCES = ('ma','retest','divergence','orderflow','trend','maker')


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Order(Strict):
    request_id: str=Field(pattern=r'^[A-Za-z0-9-]{16,100}$')
    symbol: str=Field(pattern=r'^[A-Z0-9]{1,24}-USDT-SWAP$')
    side: Literal['long','short']
    kind: Literal['market','limit','trigger','twap','iceberg','post_only']
    quantity: Optional[str]=Field(None,pattern=r'^\d{1,12}(\.\d{1,12})?$')
    leverage: int=Field(ge=1,le=100)
    stop: Optional[str]=Field(None,pattern=r'^\d{1,12}(\.\d{1,12})?$')
    rr: Literal[3,5]=3
    limit_price: Optional[str]=Field(None,pattern=r'^\d{1,12}(\.\d{1,12})?$')
    trigger_price: Optional[str]=Field(None,pattern=r'^\d{1,12}(\.\d{1,12})?$')
    direction: Literal['above','below']='above'
    slices: int=Field(default=1,ge=1,le=20)
    interval_seconds: int=Field(default=60,ge=5,le=3600)
    lifetime_seconds: int=Field(ge=10,le=86400)
    max_slippage_bps: float=Field(ge=0,le=500)
    follow_id: Optional[str]=None
    event_id: Optional[str]=None

    @model_validator(mode='after')
    def check(self):
        for value in (self.quantity,self.stop,self.limit_price,self.trigger_price):
            if value is not None and Decimal(value)<=0:raise ValueError('价格与数量必须大于零')
        if self.kind in ('limit','iceberg','post_only') and self.limit_price is None:raise ValueError('限价委托需要价格')
        if self.kind=='trigger' and self.trigger_price is None:raise ValueError('条件单需要触发价')
        if self.kind not in ('twap','iceberg') and self.slices!=1:raise ValueError('只有拆单计划可以多片执行')
        if self.interval_seconds*(self.slices-1)>=self.lifetime_seconds:raise ValueError('委托期限短于拆单计划')
        return self


class SourceConfig(Strict):
    id: str=Field(pattern=r'^[a-z0-9-]{1,60}$')
    kind: Literal['ma','retest','divergence','orderflow','trend','maker']
    enabled: bool=False
    symbols: list[str]=Field(min_length=1,max_length=20)
    timeframe: Literal['5m','15m','30m','1H','4H','1D']='15m'
    lookback: Optional[int]=Field(None,ge=3,le=120)
    imbalance: Optional[float]=Field(None,gt=0.5,lt=1)
    min_trades: Optional[int]=Field(None,ge=2,le=500)
    min_spread_bps: Optional[float]=Field(None,ge=0,le=500)
    max_spread_bps: Optional[float]=Field(None,gt=0,le=1000)
    @model_validator(mode='after')
    def check(self):
        import re
        if len(set(self.symbols))!=len(self.symbols) or any(not re.fullmatch(r'[A-Z0-9]{1,24}-USDT-SWAP',s) for s in self.symbols):raise ValueError('币种列表不合法')
        required={'divergence':['lookback'],'orderflow':['imbalance','min_trades'],'maker':['min_spread_bps','max_spread_bps']}.get(self.kind,[])
        if any(getattr(self,k) is None for k in required):raise ValueError('请填写该策略必需的参数')
        if self.kind=='maker' and self.min_spread_bps>=self.max_spread_bps:raise ValueError('价差上下限错误')
        return self


class Follow(Strict):
    id: str=Field(pattern=r'^[a-z0-9-]{1,60}$')
    source_id: str
    enabled: bool=False
    side: Literal['both','long','short']='both'
    quantity: Optional[str]=Field(None,pattern=r'^\d{1,12}(\.\d{1,12})?$')
    leverage: int=Field(ge=1,le=100)
    rr: Literal[3,5]=3
    lifetime_seconds: int=Field(ge=10,le=86400)
    max_slippage_bps: float=Field(ge=0,le=500)


class Settings(Strict):
    revision: int=Field(ge=0)
    sources: list[SourceConfig]=Field(default_factory=list,max_length=30)
    follows: list[Follow]=Field(default_factory=list,max_length=30)
    @model_validator(mode='after')
    def check(self):
        if len({(symbol,s.timeframe) for s in self.sources if s.enabled for symbol in s.symbols})>40:raise ValueError('同时观察最多 40 组币种与周期，请分批配置')
        if len({s.id for s in self.sources})!=len(self.sources) or len({f.id for f in self.follows})!=len(self.follows):raise ValueError('规则标识重复')
        if any(f.source_id not in {s.id for s in self.sources} for f in self.follows):raise ValueError('跟单来源不存在')
        return self


class Activation(Strict):
    revision: int=Field(ge=0)
    risk_budget_usdt: str=Field(pattern=r'^\d{1,12}(\.\d{1,8})?$')
    max_plans: int=Field(ge=1,le=1000)
    hours: int=Field(ge=1,le=24)
    follow_ids: list[str]=Field(default_factory=list,max_length=30)
    account_uid: str=Field(min_length=1,max_length=80)
