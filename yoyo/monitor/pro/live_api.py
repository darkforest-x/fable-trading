"""Pro-only live configuration and explicit owner authorization endpoints.

The gateway uses read-only venue clients. It can reserve approved work, but
only the separate single-writer worker may send an exchange mutation.
"""
import time
import uuid
from fastapi import HTTPException, Request
from .live_models import Strict, Settings, Activation, Order
from .live_store import LiveStore
from .live_venue import DEFAULT_LIVE, client, PublicData
from .live_execution import Executor
from .strategy_sources import CATALOG
from yoyo.okx_execution.risk import D

class Confirmation(Strict):
    acknowledgement: str


def install(app, guard, *, directory=DEFAULT_LIVE, client_factory=client, public=None):
    store=LiveStore(directory);public=public or PublicData()
    app.state.pro_live=store
    def executor():return Executor(store,client_factory(directory,writes=False),public)
    def check(request,body,phrase):
        guard(request)
        if body.acknowledgement!=phrase:raise HTTPException(422,'请逐项核对后由 Owner 确认')
    def call(fn):
        try:return fn()
        except (ValueError,KeyError) as e:raise HTTPException(409,str(e)) from None
        except Exception:raise HTTPException(503,'交易所核对暂不可用；未自动重试任何交易写入') from None
    def healthy():
        status=store.meta('worker',{})
        if not 0<=int(time.time()*1000)-status.get('heartbeat_ms',0)<30000:raise ValueError('Pro 执行服务未就绪，不能启用或确认委托')
    def preflight():
        engine=executor();a=engine.snapshot();engine.owned(a)
        return a

    @app.get('/api/pro/live/status')
    def status():
        session=store.meta('session',{'enabled':False})
        session={**session,'effective':bool(session.get('enabled') and int(time.time()*1000)<session.get('expires_ms',0))}
        return dict(session=session,settings=store.meta('settings',{'revision':0,'sources':[],'follows':[]}),
                    worker=store.meta('worker',{}),account=store.meta('account',{}),
                    configured=(store.directory/'credentials.json').is_file(),credential_path=str(store.directory/'credentials.json'),
                    source_status=store.meta('source_status',{}),follow_status=store.meta('follow_status',{}))

    @app.get('/api/pro/sources')
    def sources():return dict(catalog=CATALOG,settings=store.meta('settings',{'revision':0,'sources':[],'follows':[]}),items=store.events(),status=store.meta('source_status',{}))

    @app.put('/api/pro/live/settings')
    def settings(body:Settings,request:Request):
        guard(request);return call(lambda:store.settings(body.model_dump()))

    @app.post('/api/pro/live/preflight')
    def readiness(request:Request):
        guard(request);return call(preflight)

    @app.post('/api/pro/live/activation/preview')
    def activation(body:Activation,request:Request):
        guard(request)
        def make():
            healthy();a=preflight();settings=store.meta('settings',{'revision':0,'sources':[],'follows':[]})
            if body.revision!=settings['revision'] or body.account_uid!=a['uid']:raise ValueError('配置或账户已变化')
            if D(body.risk_budget_usdt)<=0:raise ValueError('累计风险预算必须大于零')
            if len(set(body.follow_ids))!=len(body.follow_ids):raise ValueError('跟单规则重复')
            follows={f['id']:f for f in settings['follows']};sources={s['id']:s for s in settings['sources']}
            for identity in body.follow_ids:
                f=follows.get(identity);s=sources.get(f['source_id']) if f else None
                if not f or not f['enabled'] or not s or not s['enabled']:raise ValueError('启用的跟单规则或来源不存在/未开启')
                if not next(c for c in CATALOG if c['id']==s['kind'])['live_eligible']:raise ValueError('该信号源尚未通过实盘准入，不能启用自动跟单')
            now=int(time.time()*1000)
            value=dict(**body.model_dump(),id=uuid.uuid4().hex,created_ms=now,preview_expires_ms=now+60000,
                       sources=settings['sources'],follows=[follows[i] for i in body.follow_ids],
                       protection_policy='3% total plan risk; 3R/5R; protect actual fills; unknown writes never replay',
                       pause_policy='stop new entries and cancel pending entries; retain protection for filled positions')
            store.set_meta('activation',value);return value
        return call(make)

    @app.post('/api/pro/live/activation/{identity}/confirm')
    def enable(identity:str,body:Confirmation,request:Request):
        check(request,body,'ENABLE-PRO-LIVE')
        def confirm():
            healthy();a=preflight();return store.activate(identity,a['uid'])
        return call(confirm)

    @app.post('/api/pro/live/pause')
    def pause(body:Confirmation,request:Request):
        check(request,body,'PAUSE-PRO-LIVE');return store.pause()

    @app.get('/api/pro/live/plans')
    def plans():return dict(items=store.rows())

    @app.get('/api/pro/live/plans/{identity}')
    def plan(identity:str):return call(lambda:store.get(identity))

    @app.post('/api/pro/live/preview')
    def preview(body:Order,request:Request):
        guard(request)
        if body.follow_id or body.event_id:raise HTTPException(422,'自动跟单标识仅由执行服务创建')
        return call(lambda:executor().preview(body))

    @app.post('/api/pro/live/plans/{identity}/confirm')
    def confirm(identity:str,body:Confirmation,request:Request):
        check(request,body,'CONFIRM-PRO-LIVE-ORDER')
        def reserve():
            healthy();return store.approve(identity,store.meta('session',{}))
        return call(reserve)

    @app.post('/api/pro/live/plans/{identity}/cancel')
    def cancel(identity:str,body:Confirmation,request:Request):
        check(request,body,'CANCEL-PRO-LIVE-PLAN');return call(lambda:store.request(identity,'cancel'))

    @app.post('/api/pro/live/plans/{identity}/close')
    def close(identity:str,body:Confirmation,request:Request):
        check(request,body,'CLOSE-PRO-LIVE-POSITION');return call(lambda:store.request(identity,'close'))
