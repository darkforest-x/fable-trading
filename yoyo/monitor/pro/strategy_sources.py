"""Causal, versioned Pro sources; observation is separate from live permission.

Divergence uses only the last closed OHLCV bar and its preceding lookback bars:
a new high with lower OBV is short, a new low with higher OBV is long. Trend
uses the last two closed bars, SMA20/60/120 alignment and an EMA20 recross.
Order-flow uses explicitly labelled recent-trade samples, never a claimed full
CVD window. Maker emits passive bid/ask proposals, not fabricated fill results.
No strategy parameters or performance claims are supplied by a design mockup.
"""
import hashlib
import json
import time
import httpx
from .live_venue import TF

CATALOG=[
 dict(id='ma',name='均线密集突破',description='复用已保存 YOLO 检测；模型生产资格独立核对',parameters=[],live_eligible=False),
 dict(id='retest',name='V13.1 回踩',description='现有 V13.1 观察事件；订单与图表一致性准入尚未通过',parameters=[],live_eligible=False),
 dict(id='divergence',name='量价背离',description='收盘价格新高/新低与 OBV 不确认',parameters=['lookback'],live_eligible=True),
 dict(id='orderflow',name='订单流失衡',description='最新最多 500 笔去重成交的主动买卖量比例',parameters=['imbalance','min_trades'],live_eligible=True),
 dict(id='trend',name='趋势中继',description='SMA20/60/120 排列与收盘重新穿越 EMA20',parameters=[],live_eligible=True),
 dict(id='maker',name='做市报价',description='符合指定价差范围的双边 post-only 报价；REST 节拍，不宣称低延迟 HFT',parameters=['min_spread_bps','max_spread_bps'],live_eligible=True),
]


def evaluate(config,rows=(),trades=(),quote=None):
    """Return proposed sides with evidence; all numeric thresholds are owner supplied."""
    kind=config['kind']
    if kind=='divergence':
        window=rows[-config['lookback']-1:]
        if len(window)!=config['lookback']+1:return []
        obv=[0.0]
        for old,current in zip(window,window[1:]):
            obv.append(obv[-1]+(1 if current['close']>old['close'] else -1 if current['close']<old['close'] else 0)*current['volume'])
        last=window[-1];old=window[:-1]
        high=max(range(len(old)),key=lambda i:old[i]['high']);low=min(range(len(old)),key=lambda i:old[i]['low'])
        out=[]
        if last['high']>old[high]['high'] and obv[-1]<obv[high]:out.append(dict(side='short'))
        if last['low']<old[low]['low'] and obv[-1]>obv[low]:out.append(dict(side='long'))
        for r in out:r['evidence']=dict(lookback=config['lookback'],obv=obv[-1],prior_high_obv=obv[high],prior_low_obv=obv[low],last_closed_ms=last['t']+TF[config['timeframe']])
        return out
    if kind=='trend':
        if len(rows)<2:return []
        prev,last=rows[-2:];side=None
        if last['sma20']>last['sma60']>last['sma120'] and prev['close']<=prev['ema20'] and last['close']>last['ema20']:side='long'
        if last['sma20']<last['sma60']<last['sma120'] and prev['close']>=prev['ema20'] and last['close']<last['ema20']:side='short'
        return [dict(side=side,evidence={k:last[k] for k in ('t','close','sma20','sma60','sma120','ema20')})] if side else []
    if kind=='orderflow':
        if len(trades)<config['min_trades']:return []
        buy=sum(float(r['sz']) for r in trades if r['side']=='buy');sell=sum(float(r['sz']) for r in trades if r['side']=='sell')
        if buy+sell<=0:return []
        ratio=buy/(buy+sell);side='long' if ratio>=config['imbalance'] else 'short' if ratio<=1-config['imbalance'] else None
        return [dict(side=side,evidence=dict(buy_ratio=ratio,count=len(trades),start_ms=int(trades[0]['ts']),end_ms=int(trades[-1]['ts']),scope='recent_trade_sample_not_full_window'))] if side else []
    if kind=='maker' and quote:
        bid,ask=float(quote['bidPx']),float(quote['askPx']);spread=(ask-bid)/((ask+bid)/2)*10000
        if config['min_spread_bps']<=spread<=config['max_spread_bps']:
            return [dict(side=side,kind='post_only',limit_price=price,evidence=dict(spread_bps=spread,quote_ms=int(quote['ts']),scope='passive_quote_not_fill')) for side,price in [('long',quote['bidPx']),('short',quote['askPx'])]]
    return []


class Sources:
    def __init__(self,store,public,*,upstream=None,clock=lambda:int(time.time()*1000)):
        self.store,self.public,self.clock=store,public,clock
        self.upstream=upstream

    def shared(self,path):
        if self.upstream:return self.upstream(path)
        with httpx.Client(base_url='http://127.0.0.1:8766',trust_env=False,timeout=15) as c:
            r=c.get(path);r.raise_for_status();return r.json()

    def scan(self):
        settings=self.store.meta('settings',{'revision':0,'sources':[]});out=[];errors=[]
        for config in settings['sources']:
            if not config['enabled']:continue
            version=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:16]
            try:
                if config['kind'] in ('ma','retest'):
                    path='/api/yolo-detect/discoveries?limit=500' if config['kind']=='ma' else '/api/v13/signals?limit=200'
                    for row in self.shared(path).get('items',[]):
                        if row['symbol'] not in config['symbols'] or row['timeframe']!=config['timeframe']:continue
                        decision=int(row.get('bar_close_ms') or row.get('first_confirmed_ms') or row.get('detected_at_ms') or 0)
                        out.append(self.record(config,version,row['symbol'],decision,dict(side=row['side'],evidence=dict(original_id=row.get('id') or row.get('detection_id'),decision_ms=decision),execution_eligible=False)))
                    continue
                for symbol in config['symbols']:
                    rows=trades=();quote=None
                    if config['kind'] in ('divergence','trend'):rows=self.public.candles(symbol,config['timeframe']);decision=int(rows[-1]['t'])+TF[config['timeframe']]
                    else:
                        quote=self.public.quote(symbol);decision=int(quote['ts'])//15000*15000
                        if config['kind']=='orderflow':trades=self.public.trades(symbol);decision=int(trades[-1]['ts'])//15000*15000
                    for proposed in evaluate(config,rows,trades,quote):out.append(self.record(config,version,symbol,decision,proposed))
            except Exception as e:
                errors.append(dict(source_id=config['id'],message=str(e) if isinstance(e,ValueError) else '数据读取暂不可用',at_ms=self.clock()))
        self.store.set_meta('source_status',dict(checked_ms=self.clock(),errors=errors,generated=len(out)))
        return out

    def record(self,config,version,symbol,decision,proposed):
        key=f"{config['id']}:{version}:{symbol}:{decision}:{proposed['side']}"
        value=dict(id=hashlib.sha256(key.encode()).hexdigest(),source_id=config['id'],source_version=version,
                   symbol=symbol,timeframe=config['timeframe'],source_kind=config['kind'],first_observed_ms=self.clock(),
                   detected_at_ms=decision,decision_ms=decision,confidence=None,production_claim=False,
                   execution_eligible=proposed.get('execution_eligible',True))
        value.update(proposed)
        self.store.event(value);return value
