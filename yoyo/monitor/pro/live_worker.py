"""Single Pro writer. Disabled without a fresh, bounded owner activation.

Market observations run independently of the execution loop and never modify
Standard scanners, forward pulses or ledgers. A process lock owns dispatch.
"""
import fcntl
import hashlib
import os
import threading
import time
from .live_store import LiveStore, TERMINAL
from .live_venue import DEFAULT_LIVE, client, PublicData
from .live_execution import Executor
from .live_models import Order
from .strategy_sources import Sources

NOW=lambda:int(time.time()*1000)


class Worker:
    def __init__(self,store,*,client_factory=client,public=None,clock=NOW):
        self.store,self.factory,self.public,self.clock=store,client_factory,public or PublicData(),clock

    def follow(self,engine):
        session=self.store.meta('session',{});now=self.clock()
        if not session.get('enabled') or not session.get('activated_ms',now+1)<=now<session.get('expires_ms',0):return
        settings=self.store.meta('settings',{'revision':0,'sources':[],'follows':[]})
        if settings['revision']!=session['revision']:return
        sources={s['id']:s for s in settings['sources'] if s['enabled']}
        used={p['spec']['request_id'] for p in self.store.rows()};errors=[]
        events=self.store.events()
        for rule in settings['follows']:
            config=sources.get(rule['source_id'])
            if not rule['enabled'] or rule['id'] not in session['follow_ids'] or not config:continue
            import json
            version=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:16]
            for event in reversed(events):
                if (event['source_id']!=rule['source_id'] or event['source_version']!=version or not event.get('execution_eligible')
                    or not session['activated_ms']<=event['decision_ms']<=now
                    or now-event['decision_ms']>30*60000
                    or event['symbol'] not in config['symbols']
                    or rule['side'] not in ('both',event['side'])):continue
                rid='follow-'+hashlib.sha256((session['id']+rule['id']+event['id']).encode()).hexdigest()
                if rid in used:continue
                attempt=dict(id=rid,follow_id=rule['id'],event_id=event['id'],state='checking',at_ms=self.clock())
                if not self.store.claim_follow(rid,attempt):continue
                try:
                    spec=Order(request_id=rid,symbol=event['symbol'],side=event['side'],kind=event.get('kind','market'),
                               quantity=rule['quantity'],leverage=rule['leverage'],rr=rule['rr'],
                               limit_price=event.get('limit_price'),lifetime_seconds=rule['lifetime_seconds'],
                               max_slippage_bps=rule['max_slippage_bps'],follow_id=rule['id'],event_id=event['id'])
                    p=engine.preview(spec);used.add(rid)
                    p['source_event']=event;self.store.save(p)
                    self.store.approve(p['id'],session,automatic=True,now=self.clock())
                    self.store.finish_follow(rid,{**attempt,'state':'queued','plan_id':p['id']})
                except Exception as e:
                    self.store.finish_follow(rid,{**attempt,'state':'rejected','message':str(e) if isinstance(e,ValueError) else '账户或报价核验失败，未开仓'})
        self.store.set_meta('follow_status',dict(checked_ms=self.clock(),errors=[a for a in self.store.follow_attempts() if a['state']!='queued']))

    def tick(self):
        now=self.clock();plans=[p for p in self.store.rows() if p['state'] not in TERMINAL|{'preview'}]
        session=self.store.meta('session',{})
        active=session.get('enabled') and now<session.get('expires_ms',0)
        if not plans and not active:return
        # No write-capable client is created merely by loading a page or config.
        c=self.factory(self.store.directory,writes=True)
        engine=Executor(self.store,c,self.public,clock=self.clock)
        a=engine.snapshot()
        for p in plans:
            if a['uid']!=p['quote']['account_uid']:raise ValueError('账户已变化，不能处理既有实盘委托')
        for p in reversed(plans):
            try:
                engine.step(p)
                p.pop('error',None)
            except Exception as e:p['error']=str(e) if isinstance(e,ValueError) else '交易所回执读取失败，未重发未知请求'
            finally:self.store.save(p)
        if active:
            if a['uid']!=session['account_uid']:raise ValueError('启用账户与当前绑定账户不一致')
            self.follow(engine)


def run(directory=DEFAULT_LIVE,stop=None):
    store=LiveStore(directory);stop=stop or threading.Event()
    fd=os.open(store.directory/'worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state={'pid':os.getpid(),'started_ms':NOW(),'stage':'disabled'}
        def beat():
            while not stop.is_set():
                store.set_meta('worker',{**state,'heartbeat_ms':NOW()});stop.wait(2)
        def observe():
            sources=Sources(store,PublicData())
            while not stop.is_set():
                try:sources.scan()
                except Exception:store.set_meta('source_status',dict(checked_ms=NOW(),errors=[{'message':'来源检查未完成'}]))
                stop.wait(15)
        heartbeat=threading.Thread(target=beat,daemon=True);observer=threading.Thread(target=observe,daemon=True)
        heartbeat.start();observer.start();worker=Worker(store)
        try:
            while not stop.is_set():
                state.update(stage='checking',cycle_ms=NOW())
                try:worker.tick();state.update(stage='waiting',error=None,completed_ms=NOW())
                except Exception as e:state.update(stage='attention',error=str(e) if isinstance(e,ValueError) else '账户读取失败，未重发未知请求')
                stop.wait(1)
        finally:
            stop.set();heartbeat.join(3);observer.join(3)
            store.set_meta('worker',{**state,'stage':'stopped','heartbeat_ms':0})
    finally:os.close(fd)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--directory',default=str(DEFAULT_LIVE))
    run(parser.parse_args().directory)
