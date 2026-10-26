"""Compact Monday paper ticket; reuses the paper ledger and durable receipts.

Only this explicitly tagged quote opts into the owner's 10x/5x allowance and
actual exchange leverage. The full paper desk's legacy presets stay intact.
Both confirmation and delayed limit fills recheck the pool atomically.
"""
from decimal import ROUND_DOWN
import json
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field
from yoyo.contracts.manual_exposure import allowance, target
from .paper_desk import number, positive, clock_ms, limit_fee, TAKER_FEE
from .paper_desk_risk import tier_for


def exposure_budget(symbol, totals, positions, orders, marks):
    exposures = []
    for p in positions:
        if number(p['remaining']) <= 0:
            continue
        q = marks.get(p['symbol'])
        if not q:
            raise ValueError('已有持仓的行情暂不可用')
        exposures.append((p['symbol'], number(p['remaining'])*max(number(q['bid']),number(q['ask']))))
    exposures += [(o['symbol'], number(o['quantity'])*number(o['limit_price'])) for o in orders if o['action']=='open' and o['status']=='pending']
    return allowance(symbol, totals['equity'], exposures)


class Terms(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str
    symbol: str = Field(pattern=r'^[A-Z0-9]{1,32}-USDT-SWAP$')
    side: Literal['long','short'] = 'long'
    order_type: Literal['market','limit'] = 'market'
    allocation_percent: str = '100'
    notional_usdt: Optional[str] = None
    leverage: Optional[str] = None
    limit_price: Optional[str] = None
    take_profit: Optional[str] = None
    stop_loss: Optional[str] = None
    entry_policy: Optional[Literal['monday_1618']] = None
    range_week: Optional[str] = None


def install(api, app, desk, marks, entry_risk, invoke):
    @api.get('/paper-desk/quick/receipt/{identity}')
    def receipt(identity: str):
        with desk.connect() as db:
            row = db.execute('SELECT response FROM receipts WHERE request_id=?',(identity,)).fetchone()
            if not row:
                return dict(found=False)
            value = json.loads(row['response'])
            if value.get('order'):
                value['order'] = desk.row(db,'limit_orders',value['order']['id'])
            return dict(value,found=True)

    def calculate(payload, preview):
        quotes = marks(sorted(set(desk.symbols(payload.account_id)) | {payload.symbol}))
        entry_risk(quotes, payload.symbol)
        value = quotes[payload.symbol]
        targets = {}
        bound = None
        if payload.entry_policy:
            from yoyo.contracts.monday_order import bind
            reader = getattr(app.state, 'monday_chart_levels', None)
            if reader is None:
                raise ValueError('周一区间服务尚未就绪')
            context = reader.read_ticket(payload.symbol, clock_ms())
            terms, targets = bind({**payload.model_dump(), 'monday_range': context},
                                  value['risk']['instrument']['tickSz'], clock_ms(), defaults=not preview)
            payload = payload.model_copy(update={'limit_price': terms['limit_price'], 'range_week': terms['range_week']})
            bound = dict(side=payload.side, limit_price=payload.limit_price, expires_ms=context.get('ticket_until_ms',context['expires_ms']))
        with desk.connect() as db:
            db.execute('BEGIN')
            a = desk.row(db,'accounts',payload.account_id)
            positions = [dict(r) for r in db.execute('SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0',(payload.account_id,))]
            orders = desk.pending_orders(db,payload.account_id)
            totals = desk.totals(a,positions,quotes,clock_ms(),orders)
            limits = exposure_budget(payload.symbol,totals,positions,orders,quotes)
        requested = positive(payload.notional_usdt) if payload.notional_usdt is not None else target(limits,payload.allocation_percent)
        if requested > number(limits['exposure_remaining']) or requested <= 0:
            raise ValueError('仓位额度不足，请刷新账户或降低额度')
        if payload.order_type=='limit' and not payload.limit_price:
            raise ValueError('请输入限价')
        price = positive(payload.limit_price) if payload.order_type=='limit' else positive(value['ask' if payload.side=='long' else 'bid'])
        if payload.order_type=='limit' and limit_fee(payload.side,value,price)==TAKER_FEE:
            if preview and payload.entry_policy:
                raise ValueError('1.618 已越过当前盘口，请等待价格回到可挂单一侧')
            price = positive(value['ask' if payload.side=='long' else 'bid'])
        instrument = value['risk'].get('instrument',{})
        if instrument.get('instId') != payload.symbol or instrument.get('ctValCcy') != payload.symbol.split('-')[0]:
            raise ValueError('交易所合约规格暂不可用')
        tick = positive(instrument['tickSz'])
        for raw in (payload.limit_price if payload.order_type=='limit' else None, payload.stop_loss, payload.take_profit):
            if raw is not None and positive(raw) % tick:
                raise ValueError('价格须符合交易所最小报价单位')
        step = positive(instrument['ctVal'])*positive(instrument['lotSz'])
        qty = (requested/price/step).to_integral_value(rounding=ROUND_DOWN)*step
        total_qty = qty+sum(number(p['remaining']) for p in positions if p['symbol']==payload.symbol)
        total_qty += sum(number(o['quantity']) for o in orders if o['action']=='open' and o['symbol']==payload.symbol)
        _, tier_max = tier_for(value,total_qty)
        maximum = int(min(tier_max,positive(instrument['lever'])))
        leverage = number(payload.leverage) if payload.leverage is not None else number(maximum)
        if leverage != int(leverage) or not 1 <= leverage <= maximum:
            raise ValueError('杠杆超出此数量对应的交易所上限')
        result = dict(limits, **targets, max_leverage=str(maximum), leverage=str(leverage), price=str(price),
                      notional_usdt=str(requested), notional=str(qty*price), available=totals['available'],
                      tick_size=instrument['tickSz'])
        if not preview:
            return result
        quote = desk.save_quote(dict(value,risk={**value['risk'],'monday_quick':True,'monday_order_type':payload.order_type,
                                               'monday_bound':bound}))
        if bound:
            quote['expires_ms'] = min(quote['expires_ms'], bound['expires_ms'])
        sizing = desk.size_by_notional(payload.account_id,payload.side,quote['id'],int(leverage),str(requested),quotes,
                                      payload.limit_price if payload.order_type=='limit' else None,payload.take_profit,payload.stop_loss)
        return {**result,**quote,**sizing,'account_id':payload.account_id,'leverage':str(leverage)}

    @api.post('/paper-desk/quick/defaults')
    def defaults(payload: Terms):
        return invoke(calculate,payload,False)

    @api.post('/paper-desk/quick/preview')
    def preview(payload: Terms):
        return invoke(calculate,payload,True)
