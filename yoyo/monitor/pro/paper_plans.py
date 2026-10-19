"""Persistent paper-only conditional/TWAP/iceberg plans using the paper ledger.

No exchange client or credential is used. Each child submission saves its exact
payload before dispatch and reuses its request ID after a lost response. Cancelling
stops future slices, not a submission that already crossed the dispatch boundary.
"""
import asyncio
from decimal import Decimal, ROUND_DOWN
import json
import time
from typing import Literal, Optional
import uuid
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: str = Field(pattern=r"^[A-Za-z0-9-]{16,100}$")
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    symbol: str = Field(pattern=r"^[A-Z0-9]{1,24}-USDT-SWAP$")
    side: Literal["long", "short"]
    kind: Literal["trigger", "twap", "iceberg"]
    quantity: str = Field(pattern=r"^\d{1,12}(\.\d{1,12})?$")
    leverage: int = Field(ge=1, le=100)
    trigger_price: Optional[str] = Field(None, pattern=r"^\d{1,12}(\.\d{1,12})?$")
    limit_price: Optional[str] = Field(None, pattern=r"^\d{1,12}(\.\d{1,12})?$")
    direction: Literal["above", "below"] = "above"
    slices: int = Field(default=1, ge=1, le=20)
    interval_seconds: int = Field(default=60, ge=5, le=3600)
    take_profit: Optional[str] = Field(None, pattern=r"^\d{1,12}(\.\d{1,12})?$")
    stop_loss: Optional[str] = Field(None, pattern=r"^\d{1,12}(\.\d{1,12})?$")

    @model_validator(mode="after")
    def valid(self):
        from yoyo.research_workspace.paper_desk import symbol_leverage
        symbol_leverage(self.symbol, self.leverage)
        if Decimal(self.quantity) / self.slices < Decimal('0.000000000001'):
            raise ValueError("数量必须大于零")
        for v in [self.take_profit, self.stop_loss, self.trigger_price, self.limit_price]:
            if v is not None and (not Decimal(v).is_finite() or Decimal(v) <= 0):
                raise ValueError("价格必须是正数")
        if self.kind == "trigger" and (self.trigger_price is None or self.slices != 1):
            raise ValueError("条件单需要触发价格且只能一笔")
        if self.kind == "iceberg" and not self.limit_price:
            raise ValueError("冰山模拟委托需要限价")
        return self


class Plans:
    def __init__(self, store, client):
        self.store, self.client = store, client
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS paper_plans (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, spec TEXT, value TEXT)")
            db.commit()

    def rows(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT value FROM paper_plans ORDER BY rowid DESC")]

    def create(self, spec):
        encoded = json.dumps(spec.model_dump(), sort_keys=True)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT spec,value FROM paper_plans WHERE request_id=?", (spec.request_id,)).fetchone()
            if old:
                if old[0] != encoded:
                    raise HTTPException(409, "请求标识已被其他参数使用")
                return json.loads(old[1])
            if db.execute("SELECT count(*) FROM paper_plans").fetchone()[0] >= 1000:
                raise HTTPException(409, "已达到计划保存上限")
            value = dict(id=uuid.uuid4().hex, spec=spec.model_dump(), state="waiting", completed=0, receipts=[], created_ms=int(time.time()*1000), next_ms=0, cancel_requested=False)
            db.execute("INSERT INTO paper_plans VALUES(?,?,?,?)", (value['id'], spec.request_id, encoded, json.dumps(value)))
            db.commit()
        return value

    def update(self, value):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current=json.loads(db.execute("SELECT value FROM paper_plans WHERE id=?", (value['id'],)).fetchone()[0])
            value['cancel_requested'] = current.get('cancel_requested', False) or value.get('cancel_requested', False)
            db.execute("UPDATE paper_plans SET value=? WHERE id=?", (json.dumps(value), value['id']))
            db.commit()
        return value

    def cancel(self, identity):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT value FROM paper_plans WHERE id=?", (identity,)).fetchone()
            if not row: raise HTTPException(404)
            value=json.loads(row[0]); value['cancel_requested']=True
            if value['state']=='waiting':value['state']='canceled'
            db.execute("UPDATE paper_plans SET value=? WHERE id=?", (json.dumps(value), identity));db.commit()
        return value

    async def step(self, value):
        if value['state'] not in {'waiting','submitting'}:return
        if value['cancel_requested'] and value['state']=='waiting':
            value['state']='canceled';self.update(value);return
        spec=value['spec']
        if value['next_ms']>time.time()*1000:return
        if value['state']=='waiting':
            if spec['kind']=='iceberg' and value['receipts']:
                r=await self.client.get('/api/research/paper-desk');r.raise_for_status()
                last=value['receipts'][-1]; order=last.get('order',last)
                found=next((o for o in r.json().get('orders',[]) if o['id']==order.get('id')),None)
                if not found or found['status']=='pending':return
                if found['status']!='filled':value.update(state='canceled',reason='上一片委托未成交，停止剩余计划');self.update(value);return
            q=await self.client.get('/api/research/paper-desk/quote',params={'symbol':spec['symbol']});q.raise_for_status();quote=q.json()
            if spec['kind']=='trigger':
                price=Decimal(quote['ask' if spec['side']=='long' else 'bid']);target=Decimal(spec['trigger_price'])
                if (spec['direction']=='above' and price<target) or (spec['direction']=='below' and price>target):return
            total=Decimal(spec['quantity']);size=(total/spec['slices']).quantize(Decimal('0.000000000001'), rounding=ROUND_DOWN)
            if value['completed']==spec['slices']-1:size=total-size*(spec['slices']-1)
            payload=dict(request_id=f"pro-{value['id']}-{value['completed']}",quantity=str(size),quote_id=quote['id'],side=spec['side'],leverage=spec['leverage'],take_profit=spec['take_profit'],stop_loss=spec['stop_loss'],note=f"Pro paper {spec['kind']} {value['id']}")
            if spec['kind']=='iceberg':payload['limit_price']=spec['limit_price']
            value.update(state='submitting',payload=payload)
            # Claim after cancellation check under the same transaction.
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');current=json.loads(db.execute('SELECT value FROM paper_plans WHERE id=?',(value['id'],)).fetchone()[0])
                if current.get('cancel_requested'):return
                db.execute('UPDATE paper_plans SET value=? WHERE id=?',(json.dumps(value),value['id']));db.commit()
        endpoint='limit-open' if spec['kind']=='iceberg' else 'open'
        response=await self.client.post(f"/api/research/paper-desk/accounts/{spec['account_id']}/{endpoint}",json=value['payload'],headers={'origin':'http://127.0.0.1:8766','sec-fetch-site':'same-origin'})
        if response.status_code>=500:return  # Same persisted payload/id on recovery.
        if response.is_error:
            value.update(state='rejected',reason=str(response.json().get('detail','模拟委托被拒绝')))
        else:
            value['receipts'].append(response.json());value['completed']+=1
            value.update(state='done' if value['completed']>=spec['slices'] else 'waiting',next_ms=int(time.time()*1000)+spec['interval_seconds']*1000)
            value.pop('payload',None)
        self.update(value)

    async def run(self):
        while True:
            for value in self.rows():
                try:await self.step(value)
                except (httpx.HTTPError,ValueError,KeyError) as exc:
                    value.update(last_error=type(exc).__name__, last_error_ms=int(time.time()*1000))
                    self.update(value)  # Retain the persisted payload/id across retries.
            await asyncio.sleep(3)


import httpx

def install(app, guard):
    @app.get('/api/pro/paper-plans')
    def list_plans(request: Request):return dict(items=request.app.state.plans.rows(), dispatcher=request.app.state.paper_dispatcher)
    @app.post('/api/pro/paper-plans')
    async def create_plan(body: Plan, request: Request):
        guard(request)
        try:
            r=await request.app.state.client.get('/api/research/paper-desk');r.raise_for_status()
        except httpx.HTTPError:
            raise HTTPException(503, '模拟账户后台暂不可达，计划尚未创建') from None
        if body.account_id not in {a['id'] for a in r.json().get('accounts',[])}:raise HTTPException(404,'模拟账户不存在')
        return request.app.state.plans.create(body)
    @app.post('/api/pro/paper-plans/{identity}/cancel')
    def cancel_plan(identity: str, request: Request):
        guard(request);return request.app.state.plans.cancel(identity)
