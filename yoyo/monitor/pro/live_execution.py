"""Receipt-driven live market/limit/trigger/TWAP/iceberg execution for Pro.

Only approved Pro plans are consumed, using an exclusive account. Every POST
has a durable, non-replayed intent. Partial entries are canceled then protected
at their actual filled size; another slice waits for verified protection.
Source: OKX V5 Trade API, attachAlgoOrds, clOrdId and order-algo, 2026-10-18.
"""
import hashlib
import time
from yoyo.okx_execution.client import APIError, TransportError
from yoyo.okx_execution.risk import D, amount, quantize
from yoyo.okx_execution.manual import take_profit
from .live_venue import account
from .live_store import TERMINAL

NOT_FOUND={'51603','51604'}
def identity(*parts):return 'pr'+hashlib.sha256(':'.join(map(str,parts)).encode()).hexdigest()[:30]


class Executor:
    def __init__(self,store,client,public,*,clock=lambda:int(time.time()*1000),account_reader=account):
        self.store,self.client,self.public,self.clock,self.account_reader=store,client,public,clock,account_reader

    def snapshot(self):
        value=self.account_reader(self.client)
        self.store.set_meta('account',value)
        return value

    def preview(self,spec):
        a=self.snapshot();self.same_leverage(spec.symbol,spec.leverage)
        instrument=self.client.get('/api/v5/account/instruments',instType='SWAP',instId=spec.symbol)[0]
        if any(instrument.get(k)!=v for k,v in {'instId':spec.symbol,'instType':'SWAP','ctType':'linear','settleCcy':'USDT','state':'live'}.items()):raise ValueError('只支持交易所可用的 USDT 线性永续合约')
        ticker=self.public.quote(spec.symbol);sign=1 if spec.side=='long' else -1
        price=D(spec.limit_price) if spec.kind in ('limit','iceberg','post_only') else D(spec.trigger_price) if spec.kind=='trigger' else D(ticker['askPx' if sign==1 else 'bidPx'])
        worst=price if spec.kind in ('limit','iceberg','post_only') else price*(1+sign*D(spec.max_slippage_bps)/10000)
        tick,lot,ctval,minimum=(D(instrument[k]) for k in ('tickSz','lotSz','ctVal','minSz'))
        if min(tick,lot,ctval,minimum,worst)<=0:raise ValueError('合约精度或价格无效')
        stop_evidence=self.public.stop(spec.symbol,spec.side,tick) if spec.stop is None else None
        stop=D(spec.stop or stop_evidence['price'])
        if stop%tick or (spec.limit_price and D(spec.limit_price)%tick) or (spec.trigger_price and D(spec.trigger_price)%tick):raise ValueError('价格不符合交易所最小报价单位')
        distance=sign*(worst-stop)
        if distance<=0:raise ValueError('止损方向错误或已穿过入场价格')
        risk=D(a['total_equity_usdt'])*D('0.03')
        size=D(spec.quantity) if spec.quantity else quantize(risk/distance/ctval,lot)
        if size%lot or size<minimum:raise ValueError('数量不符合合约张数精度或最小委托量')
        slices=split(size,spec.slices,lot,minimum)
        max_size=D(instrument['maxLmtSz' if spec.kind in ('limit','iceberg','post_only') else 'maxMktSz'])
        if any(s>max_size for s in slices):raise ValueError('单片数量超过交易所委托上限')
        planned=size*ctval*distance
        if planned>risk:raise ValueError('总计划风险超过当前权益 3%，不能对每个拆分片分别使用 1R')
        tiers=self.client.get('/api/v5/public/position-tiers',instType='SWAP',tdMode='cross',instFamily=instrument['instFamily'])
        existing=sum((abs(D(p['pos'])) for p in a['positions'] if p['instId']==spec.symbol),D(0))
        matches=[t for t in tiers if D(t['minSz'])<=existing+size<=D(t['maxSz'])]
        if not matches or spec.leverage>min(D(instrument['lever']),*(D(t['maxLever']) for t in matches)):raise ValueError('杠杆超过当前合约仓位档位')
        margin=size*ctval*worst/spec.leverage
        if margin>D(a['available']):raise ValueError('可用保证金不足')
        quote=dict(account_uid=a['uid'],price=amount(price),worst_price=amount(worst),size=amount(size),slices=[amount(s) for s in slices],
                   stop=amount(stop),take_profit=take_profit(price,stop,spec.side,spec.rr,tick),planned_risk=amount(planned),
                   risk_budget=amount(risk),margin=amount(margin),leverage=spec.leverage,tick=amount(tick),lot=amount(lot),
                   ctval=amount(ctval),instrument=instrument,stop_evidence=stop_evidence,quote_ms=int(ticker['ts']),
                   risk_excludes_fees_slippage_and_funding=True)
        return self.store.preview(spec.model_dump(),quote)

    def same_leverage(self,symbol,leverage):
        for p in self.store.rows():
            if p['state'] not in TERMINAL|{'preview'} and p['spec']['symbol']==symbol and p['spec']['leverage']!=leverage:
                raise ValueError('同合约已有 Pro 计划使用其他杠杆，不能改变其共享杠杆')

    def owned(self,a):
        plans=[p for p in self.store.rows() if p['state']!='preview']
        children=[c for p in plans for c in p['children']]
        ids={c['entry_id'] for c in children}|{c.get('exit_id') for c in children}
        if any(o.get('clOrdId') not in ids for o in a['orders']):raise ValueError('Pro 账户发现外部待成交委托，暂停新开仓')
        algo_ids={c['algo_id'] for c in children}
        if any(o.get('algoClOrdId') not in algo_ids for o in a['algos']):raise ValueError('Pro 账户发现外部保护或条件单，暂停新开仓')
        expected={}
        for p in plans:
            for c in p['children']:
                if c['state'] not in ('closed','rejected'):
                    key=(p['spec']['symbol'],p['spec']['side'])
                    expected[key]=expected.get(key,D(0))+D(c.get('filled','0'))-D(c.get('exited','0'))
        actual={(p['instId'],p['posSide']):abs(D(p['pos'])) for p in a['positions'] if D(p['pos'])}
        if {k:v for k,v in expected.items() if v}!=actual:raise ValueError('仓位与 Pro 成交账本不一致，等待回执核对')

    def write(self,p,c,key,path,body):
        if key in c.setdefault('writes',{}):return False
        c['writes'][key]=dict(path=path,body=body,state='unknown',at_ms=self.clock())
        self.store.save(p)
        try:
            result=self.client.post(path,body)
            c['writes'][key].update(state='acknowledged',receipt=result)
        except APIError as e:
            c['writes'][key].update(state='rejected',code=e.code)
            self.store.save(p)
            raise
        finally:self.store.save(p)
        return True

    def order(self,p,c,exit=False):
        try:row=self.client.get('/api/v5/trade/order',instId=p['spec']['symbol'],clOrdId=c['exit_id' if exit else 'entry_id'])[0]
        except APIError as e:
            if e.code in NOT_FOUND:return None
            raise
        expected_side=('sell' if p['spec']['side']=='long' else 'buy') if exit else ('buy' if p['spec']['side']=='long' else 'sell')
        if row.get('instId')!=p['spec']['symbol'] or row.get('posSide')!=p['spec']['side'] or row.get('clOrdId')!=c['exit_id' if exit else 'entry_id'] or row.get('side')!=expected_side:raise ValueError('订单回执身份不匹配')
        return row

    def algo(self,p,c):
        try:row=self.client.get('/api/v5/trade/order-algo',algoClOrdId=c['algo_id'])[0]
        except APIError as e:
            if e.code in NOT_FOUND:return None
            raise
        if row.get('instId')!=p['spec']['symbol'] or row.get('posSide')!=p['spec']['side'] or row.get('algoClOrdId')!=c['algo_id'] or row.get('side')!=('sell' if p['spec']['side']=='long' else 'buy'):raise ValueError('保护回执身份不匹配')
        return row

    def protection(self,p,c):
        return dict(instId=p['spec']['symbol'],tdMode='cross',posSide=p['spec']['side'],side='sell' if p['spec']['side']=='long' else 'buy',ordType='oco',sz=c['filled'],algoClOrdId=c['algo_id'],
                    slTriggerPx=p['quote']['stop'],slOrdPx='-1',slTriggerPxType='last',tpTriggerPx=c['target'],tpOrdPx='-1',tpTriggerPxType='last')

    def attachment(self,p,c):
        return dict(attachAlgoClOrdId=c['algo_id'],slTriggerPx=p['quote']['stop'],slOrdPx='-1',slTriggerPxType='last',tpTriggerPx=p['quote']['take_profit'],tpOrdPx='-1',tpTriggerPxType='last')

    def protect(self,p,c):
        algo=self.algo(p,c)
        if algo and algo.get('state')=='effective':
            c.update(state='exiting',algo_receipt=algo);return
        if algo and algo.get('state')=='live':
            if D(algo.get('sz','0'))!=D(c['filled']) or D(algo.get('slTriggerPx','0'))!=D(p['quote']['stop']) or algo.get('slOrdPx')!='-1' or algo.get('tpOrdPx')!='-1' or algo.get('slTriggerPxType')!='last' or algo.get('tpTriggerPxType')!='last':
                c.update(state='closing',reason='保护参数核验失败');return
            if D(algo.get('tpTriggerPx','0'))!=D(c['target']):
                if 'amend' not in c.get('writes',{}):
                    self.write(p,c,'amend','/api/v5/trade/amend-algos',dict(instId=p['spec']['symbol'],algoId=algo['algoId'],newTpTriggerPx=c['target'],newTpOrdPx='-1',cxlOnFail=False))
                elif self.clock()-c['writes']['amend']['at_ms']>10000:c.update(state='closing',reason='实际成交止盈未获核验')
                return
            c.update(state='active',protection_verified=True,algo_receipt=algo);return
        if c['entry_terminal']=='canceled' and not algo:
            self.write(p,c,'protect','/api/v5/trade/order-algo',self.protection(p,c))
        if self.clock()-c['filled_ms']>15000:c.update(state='closing',reason='保护回执未就绪')

    def child(self,p,c):
        s,q=p['spec'],p['quote']
        if c['state']=='close_attention':
            if p.get('close_generation',0)>c.get('close_generation',0):
                c.update(state='closing',close_generation=p['close_generation'])
            return
        if c['state']=='leverage_pending':
            if not self.entry_allowed(p):c.update(state='rejected',reason='启用已暂停或委托已到期');return
            info=self.client.get('/api/v5/account/leverage-info',instId=s['symbol'],mgnMode='cross')
            if info and all(D(r['lever'])==s['leverage'] for r in info):c['state']='ready'
            elif 'leverage' not in c.get('writes',{}):
                a=self.snapshot();self.owned(a);self.same_leverage(s['symbol'],s['leverage'])
                if self.entry_allowed(p):self.write(p,c,'leverage','/api/v5/account/set-leverage',dict(instId=s['symbol'],mgnMode='cross',lever=str(s['leverage'])))
            elif self.clock()-c['writes']['leverage']['at_ms']>10000:c.update(state='rejected',reason='杠杆设置未获核验')
            return
        if c['state']=='ready':
            if not self.entry_allowed(p):c['state']='rejected';return
            a=self.snapshot();self.owned(a)
            if a['uid']!=q['account_uid']:raise ValueError('账户身份已变化')
            self.same_leverage(s['symbol'],s['leverage'])
            instrument=self.client.get('/api/v5/account/instruments',instType='SWAP',instId=s['symbol'])[0]
            if instrument.get('state')!='live' or any(D(instrument[k])!=D(q['instrument'][k]) for k in ('ctVal','tickSz','lotSz')):raise ValueError('合约状态或精度已变化')
            info=self.client.get('/api/v5/account/leverage-info',instId=s['symbol'],mgnMode='cross')
            if not info or any(D(r['lever'])!=s['leverage'] for r in info):raise ValueError('杠杆核对已失效')
            ticker=self.public.quote(s['symbol']);price=D(ticker['askPx' if s['side']=='long' else 'bidPx'])
            sign=1 if s['side']=='long' else -1
            risk_price=D(s['limit_price']) if s['kind'] in ('limit','iceberg','post_only') else price
            if sign*(risk_price-D(q['worst_price']))>0 or sign*(price-D(q['stop']))<=0:
                c.update(state='rejected',reason='报价超过已批准价格或已触及止损');return
            if D(c['size'])*D(q['ctval'])*risk_price/s['leverage']>D(a['available']):raise ValueError('执行前保证金不足')
            kind='post_only' if s['kind'] in ('iceberg','post_only') else 'limit' if s['kind']=='limit' else 'market'
            request=dict(instId=s['symbol'],tdMode='cross',posSide=s['side'],side='buy' if s['side']=='long' else 'sell',ordType=kind,sz=c['size'],clOrdId=c['entry_id'],attachAlgoOrds=[self.attachment(p,c)])
            if kind!='market':request['px']=s['limit_price']
            if not self.entry_allowed(p):c.update(state='rejected',reason='提交前授权已失效');return
            c.update(state='entry_pending',submitted_ms=self.clock());self.store.save(p)
            self.write(p,c,'entry','/api/v5/trade/order',request);return
        if c['state']=='entry_pending':
            order=self.order(p,c)
            if not order:
                c['reason']='提交结果未知，只查询原 clOrdId，不重发';return
            c.update(entry_receipt=order,filled=str(order.get('accFillSz','0')))
            state=order['state']
            if state in ('live','partially_filled'):
                if p.get('cancel_requested') or not self.entry_allowed(p) or D(c['filled'])>0 or (s['kind'] in ('market','twap','trigger') and self.clock()-c['submitted_ms']>10000):
                    self.write(p,c,'cancel','/api/v5/trade/cancel-order',dict(instId=s['symbol'],clOrdId=c['entry_id']))
                return
            if state not in ('filled','canceled','mmp_canceled'):raise ValueError('未知成交状态')
            if D(c['filled'])==0:c.update(state='rejected',reason='委托未成交');return
            if D(c['filled'])>D(c['size']):raise ValueError('成交数量超过已批准数量')
            c.update(state='protecting',entry_terminal='filled' if state=='filled' else 'canceled',entry_price=order['avgPx'],filled_ms=self.clock())
            try:c['target']=take_profit(order['avgPx'],q['stop'],s['side'],s['rr'],q['tick'])
            except ValueError:c.update(state='closing',reason='实际成交穿过止损');return
            if D(c['filled'])*D(q['ctval'])*abs(D(order['avgPx'])-D(q['stop']))>D(q['planned_risk'])*D(c['size'])/D(q['size']):c.update(state='closing',reason='实际成交风险超出批准预算')
            return
        if c['state']=='protecting':self.protect(p,c);return
        if c['state']=='active':
            if p.get('close_requested'):c['state']='closing';return
            self.protect(p,c);return
        if c['state']=='exiting':
            algo=self.algo(p,c)
            if not algo or algo.get('state')!='effective':raise ValueError('保护触发回执未确认')
            ids=algo.get('ordIdList') or ([algo['ordId']] if algo.get('ordId') else [])
            if not ids:return
            exits=[self.client.get('/api/v5/trade/order',instId=s['symbol'],ordId=i)[0] for i in ids]
            if any(r.get('instId')!=s['symbol'] or r.get('posSide')!=s['side'] or r.get('side')!=('sell' if s['side']=='long' else 'buy') for r in exits):raise ValueError('保护成交身份不符')
            total=sum((D(r.get('accFillSz','0')) for r in exits),D(0));c['exited']=amount(total)
            if total==D(c['filled']) and all(r['state'] in ('filled','canceled') for r in exits):c.update(state='closed',exit_receipts=exits)
            elif any(r['state']=='canceled' for r in exits):c.update(state='closing',reason='保护仅部分成交')
            return
        if c['state']=='closing':
            # Hedge-side closes cannot create an opposite position. Keep the
            # OCO until a close receipt, and never replay an ambiguous attempt.
            remain=D(c.get('filled','0'))-D(c.get('exited','0'))
            if remain<=0:
                algo=self.algo(p,c)
                if algo and algo.get('state')=='live':
                    self.write(p,c,'cancel_protection','/api/v5/trade/cancel-algos',[dict(instId=s['symbol'],algoId=algo['algoId'])]);return
                if algo and algo.get('state') not in ('canceled','effective'):return
                c['state']='closed';return
            attempt=c.get('close_attempt',0);key='close:'+str(attempt)
            if key not in c.get('writes',{}):
                algo=self.algo(p,c)
                if algo and algo.get('state')=='effective':c['state']='exiting';return
                a=self.snapshot();self.owned(a)
                if a['uid']!=q['account_uid']:raise ValueError('账户身份已变化')
                c['exit_id']=identity(p['id'],c['index'],'close',attempt);self.store.save(p)
                self.write(p,c,key,'/api/v5/trade/order',dict(instId=s['symbol'],tdMode='cross',posSide=s['side'],side='sell' if s['side']=='long' else 'buy',ordType='market',sz=amount(remain),clOrdId=c['exit_id']))
                return
            order=self.order(p,c,exit=True)
            if not order:
                c['reason']='平仓回执未知，只查询该次平仓标识';return
            if order['state'] in ('filled','canceled','mmp_canceled'):
                filled=D(order.get('accFillSz','0'))
                if filled>remain:raise ValueError('平仓成交量超过账本剩余数量')
                c['exited']=amount(D(c.get('exited','0'))+filled)
                c.setdefault('exit_receipts',[]).append(order)
                if order['state']=='filled' and filled!=remain:raise ValueError('平仓完成回执数量不一致')
                c['close_attempt']=attempt+1
                if (c['close_attempt']%3)==0 and D(c['exited'])<D(c['filled']):
                    c.update(state='close_attention',close_generation=p.get('close_generation',0),reason='三次明确终态后仍有剩余仓位，请核对交易所；原保护继续保留')
                return

    def entry_allowed(self,p):
        session=self.store.meta('session',{})
        return (session.get('enabled') and session.get('id')==p.get('session_id') and self.clock()<session.get('expires_ms',0)
                and self.clock()<p['approved_ms']+p['spec']['lifetime_seconds']*1000 and not p.get('cancel_requested'))

    def step(self,p):
        if p['state']=='preview' or p['state'] in TERMINAL:return
        cfg=self.client.get('/api/v5/account/config')[0]
        if cfg.get('uid')!=p['quote']['account_uid'] or cfg.get('posMode')!='long_short_mode':raise ValueError('执行账户身份或持仓模式已变化')
        for c in p['children']:
            if c['state'] in ('closed','rejected'):continue
            try:self.child(p,c)
            except APIError as e:
                c['reason']=str(e)
                if c['state']=='entry_pending' and c.get('writes',{}).get('entry',{}).get('state')=='rejected':c['state']='rejected'
                elif c['state']=='protecting':c['state']='closing'
            except (ValueError,TransportError) as e:
                c['reason']=str(e) if isinstance(e,ValueError) else '交易所响应未知，继续查询回执'
            finally:self.store.save(p)
        if any(c['state'] not in ('active','closed','rejected') for c in p['children']):return
        if any(c['state']=='rejected' for c in p['children']):p['cancel_requested']=True
        if not self.entry_allowed(p) or len(p['children'])>=len(p['quote']['slices']):
            p['state']='active' if any(c['state']=='active' for c in p['children']) else 'closed' if any(D(c.get('filled','0'))>0 for c in p['children']) else 'canceled'
            self.store.save(p);return
        if self.clock()<p['next_ms']:return
        if p['spec']['kind']=='trigger':
            price=D(self.public.quote(p['spec']['symbol'])['askPx' if p['spec']['side']=='long' else 'bidPx']);target=D(p['spec']['trigger_price'])
            if (p['spec']['direction']=='above' and price<target) or (p['spec']['direction']=='below' and price>target):return
        a=self.snapshot();self.owned(a)
        if a['uid']!=p['quote']['account_uid']:raise ValueError('执行账户发生变化')
        idx=len(p['children']);c=dict(index=idx,entry_id=identity(p['id'],idx,'entry'),algo_id=identity(p['id'],idx,'stop'),size=p['quote']['slices'][idx],filled='0',state='leverage_pending',writes={})
        p['children'].append(c);p.update(state='running',next_ms=self.clock()+p['spec']['interval_seconds']*1000)
        self.store.save(p)


def split(size,count,lot,minimum):
    each=quantize(D(size)/count,lot);result=[each]*(count-1)+[D(size)-each*(count-1)]
    if min(result)<minimum:raise ValueError('拆分后数量低于合约最小委托量')
    return result
