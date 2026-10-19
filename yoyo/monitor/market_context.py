"""Causal perpetual context from closed OKX 15m candles, refreshed independently.

Weekly ranges use a selectable calendar day boundary (America/New_York by
existing project convention). Volume profiles are explicitly an estimate:
15m base volume is spread uniformly over each candle's high-low range, 80
price bins, contiguous 70% value area expanding from the largest bin.
Reference: https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/
This is not TradingView's intrabar profile or a probability of a profitable trade.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import logging
import math
import re
from collections import OrderedDict
import threading
import time

from .market_sync import Book
from .market_sync_okx import PublicOKX

STEP = 900000
DAY = 86400000
ZONES = ('America/New_York','UTC','Asia/Shanghai')
LOG = logging.getLogger(__name__)


def window_ms(day, zone):
    start = datetime.combine(day, datetime.min.time(), ZoneInfo(zone))
    return int(start.timestamp()*1000), int((start+timedelta(days=1)).timestamp()*1000)


def complete(rows, start, end):
    selected = [r for r in rows if start <= r['time'] < end]
    return selected if [r['time'] for r in selected] == list(range(start,end,STEP)) else []


def profile(rows, bins=80, area=.70):
    if not rows:
        return None
    low, high = min(r['low'] for r in rows), max(r['high'] for r in rows)
    if high <= low:
        return None
    width=(high-low)/bins
    volumes=[0.0]*bins
    for r in rows:
        if r['high']==r['low']:
            volumes[min(bins-1,max(0,int((r['close']-low)/width)))]+=r['volume']
            continue
        first=max(0,int((r['low']-low)/width));last=min(bins-1,int((r['high']-low)/width))
        for i in range(first,last+1):
            overlap=max(0,min(r['high'],low+(i+1)*width)-max(r['low'],low+i*width))
            volumes[i]+=r['volume']*overlap/(r['high']-r['low'])
    total=sum(volumes)
    if total<=0:
        return None
    point=max(range(bins),key=lambda i:volumes[i]);a=b=point;covered=volumes[point]
    while covered < total*area and (a>0 or b<bins-1):
        left=volumes[a-1] if a>0 else -1;right=volumes[b+1] if b<bins-1 else -1
        if right>left:
            b+=1;covered+=volumes[b]
        else:
            a-=1;covered+=volumes[a]
    return {'poc':low+(point+.5)*width,'vah':low+(b+1)*width,'val':low+a*width,
            'bins':bins,'area':area,'volume':total,'covered_volume':covered}


def build_context(rows, now, zone='America/New_York', symbol='BTC-USDT-SWAP'):
    if zone not in ZONES:
        raise ValueError('unsupported timezone')
    rows=sorted((r for r in rows if r['time']+STEP<=now),key=lambda r:r['time'])
    today=datetime.fromtimestamp(now/1000,ZoneInfo(zone)).date()
    monday=today-timedelta(days=today.weekday())
    start,end=window_ms(monday,zone)
    monday_rows=complete(rows,start,end) if now>=end else [r for r in rows if start<=r['time']<end]
    # A partial Monday is developing; never substitute the prior week's range.
    weekly={'start_ms':start,'end_ms':end,'complete':bool(monday_rows) and now>=end,'developing':now<end,'high':None,'low':None}
    levels=[]
    if monday_rows:
        hi=max(r['high'] for r in monday_rows);lo=min(r['low'] for r in monday_rows);width=hi-lo
        weekly.update(high=hi,low=lo)
        levels.extend([{'label':'周一高','price':hi,'kind':'range'}, {'label':'周一低','price':lo,'kind':'range'}, {'label':'EQ 0.5','price':(hi+lo)/2,'kind':'range'}])
        if weekly['complete']:
            for ratio in (.618,.705):
                levels.extend([{'label':f'Fib {ratio} ↑','price':lo+ratio*width,'kind':'fib'}, {'label':f'Fib {ratio} ↓','price':hi-ratio*width,'kind':'fib'}])
            levels.extend([{'label':'1.13 上扩展','price':hi+.13*width,'kind':'fib'}, {'label':'1.13 下扩展','price':lo-.13*width,'kind':'fib'}])
    profiles=[]
    for days_ago in range(1,15):
        day=today-timedelta(days=days_ago);a,b=window_ms(day,zone)
        subset=complete(rows,a,b);value=profile(subset)
        if value:
            later=[r for r in rows if r['time']>=b]
            # Any missing post-session candle makes untouched status unknown.
            end_closed=(now//STEP)*STEP
            continuous=[r['time'] for r in later]==list(range(b,end_closed,STEP))
            touched=any(r['low']<=value['poc']<=r['high'] for r in later)
            profiles.append(dict(value,date=str(day),end_ms=b,naked=continuous and not touched,coverage_complete=continuous))
    previous=next((p for p in profiles if p['date']==str(today-timedelta(days=1))),None)
    if previous:
        levels.extend({'label':'昨日 '+key.upper(),'price':previous[key],'kind':'profile'} for key in ('poc','vah','val'))
    for p in profiles:
        if p['naked']:
            levels.append({'label':f"nPOC {p['date'][5:]}",'price':p['poc'],'kind':'npoc'})
    # Prior complete week extremes add context without changing the Monday rule.
    prior_start=window_ms(monday-timedelta(days=7),zone)[0]
    prior=complete(rows,prior_start,start)
    if prior:
        levels.extend([{'label':'上周高','price':max(r['high'] for r in prior),'kind':'prior'}, {'label':'上周低','price':min(r['low'] for r in prior),'kind':'prior'}])
    last=rows[-1] if rows else None
    recent=rows[-96:]
    closes=[r['close'] for r in recent]
    def trend():
        if len(closes)<96 or [r['time'] for r in recent]!=list(range(recent[0]['time'],recent[-1]['time']+STEP,STEP)):
            return None
        mean20=sum(closes[-20:])/20;mean96=sum(closes)/96
        def sign(value):
            return (value>0)-(value<0)
        value=sign(closes[-1]-mean20)+sign(mean20-mean96)
        return value/2
    structure=trend()
    return {'symbol':symbol,'source':'OKX','bar':'15m','timezone':zone,'generated_ms':now,
            'last_close_ms':last['time']+STEP if last else None,'candles':rows[-672:],
            'monday':weekly,'levels':levels,'previous_profile':previous,'profiles':profiles,
            'structure':{'score':structure,'label':'上行排列' if structure==1 else '下行排列' if structure==-1 else '结构分歧' if structure is not None else '数据不足'},
            'profile_method':'15m K线量在高低价间均匀分配 · 80档 · 70%价值区 · 估算值',
            'npoc_method':'近14个已完成日内剖面的 POC，后续已收盘15m K线从未覆盖该价；缺K线时不标 nPOC'}


def market_bias(context, observation, timeframe='60', now=None):
    """Transparent display score, not calibrated probability or entry advice.

    Uses the latest closed 1h breadth/leader returns and 15m closes already
    available. Three equally weighted signed components; no future prices.
    """
    now=int(time.time()*1000) if now is None else now
    close=context.get('last_close_ms')
    fresh=close is not None and 0<=now-close<=STEP+120000
    health=observation.get('freshness',{}).get(timeframe,{})
    latest=observation.get('snapshot') or {}
    rows=latest.get('latest',{}).get(timeframe,[])
    last=rows[-1] if rows else {}
    values=[last.get(key) for key in ('breadth_up','breadth_down','btc_z','eth_z')]+[context.get('structure',{}).get('score')]
    valid=all(isinstance(value,(int,float)) and math.isfinite(value) for value in values)
    if not fresh or not health.get('ready') or health.get('stale') or not valid:
        return {'score':None,'label':'等待完整新鲜行情','components':[]}
    if not (0<=last['breadth_up']<=1 and 0<=last['breadth_down']<=1 and -1<=values[-1]<=1):
        return {'score':None,'label':'等待完整新鲜行情','components':[]}
    breadth=(last['breadth_up']-last['breadth_down'])*100
    leaders=sum(max(-1,min(1,last[k]/3)) for k in ('btc_z','eth_z'))/2*100
    structure=context['structure']['score']*100
    score=round((breadth+leaders+structure)/3)
    return {'score':score,'label':'强多倾向' if score>=60 else '偏多' if score>=20 else '强空倾向' if score<=-60 else '偏空' if score<=-20 else '多空分歧',
            'components':[{'name':'市场广度','score':round(breadth)},{'name':'BTC / ETH 动量','score':round(leaders)},{'name':'BTC 结构','score':round(structure)}],
            'meaning':'-100 至 +100 的条件倾向，非胜率；三项等权，±100 不代表梭哈信号'}


class MarketContext:
    """Bounded demand cache: HTTP reads enqueue symbols; only the worker fetches OKX.

    BTC stays warm for the homepage. At most six other contracts refresh while
    viewed (15 minute idle expiry). Published snapshots are replaced atomically.
    """
    DEFAULT = 'BTC-USDT-SWAP'
    MAX_EXTRA = 6
    IDLE_SECONDS = 900

    def __init__(self, runtime, client=None):
        self.book = Book(Path(runtime) / 'market-context-okx-v1.sqlite3')
        self.client = client or PublicOKX(runtime=runtime)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._thread = None
        self._snapshots = {}
        self._errors = {}
        self._wanted = OrderedDict()
        self._due = {}
        self.rebuild()

    @staticmethod
    def validate(symbol):
        if not re.fullmatch(r'[A-Z0-9]{1,24}-USDT-SWAP', symbol):
            raise ValueError('unsupported perpetual symbol')
        return symbol.replace('-USDT-SWAP', 'USDT')

    @property
    def error(self):
        return self._errors.get(self.DEFAULT)

    def rebuild(self, symbol=DEFAULT):
        ticker = self.validate(symbol)
        now = int(time.time() * 1000)
        with self.book.connect() as db:
            data = db.execute('SELECT open_ms,open,high,low,close,volume FROM bars WHERE minutes=15 AND symbol=? AND open_ms>=? ORDER BY open_ms', (ticker, now-21*DAY)).fetchall()
        rows = [dict(zip(('time','open','high','low','close','volume'), r)) for r in data]
        values = {zone: build_context(rows, now, zone, symbol) for zone in ZONES}
        with self._lock:
            # A request evicted while its fetch was in flight must not regrow memory.
            if symbol == self.DEFAULT or symbol in self._wanted:
                self._snapshots[symbol] = values

    def cycle(self, symbol=DEFAULT):
        ticker = self.validate(symbol)
        server = self.client.server_ms()
        last = self.book.last_open(15, ticker)
        start = max(last+STEP if last is not None else 0, ((server-21*DAY)//STEP)*STEP)
        while start+STEP <= server and not self._stop.is_set():
            rows = self.client.klines(ticker, '15m', start, server)
            if not rows:
                break
            next_start = int(rows[-1][0])+STEP
            if next_start <= start:
                raise ValueError('OKX candles did not advance')
            self.book.put_bars(15, ticker, [(int(r[0]), *map(float, r[1:6])) for r in rows])
            start = next_start
        self.rebuild(symbol)
        with self._lock:
            self._errors.pop(symbol, None)

    def _evict(self, symbol):
        self._wanted.pop(symbol, None)
        self._snapshots.pop(symbol, None)
        self._errors.pop(symbol, None)
        self._due.pop(symbol, None)

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            now = time.monotonic()
            with self._lock:
                for symbol, seen in list(self._wanted.items()):
                    if now-seen > self.IDLE_SECONDS:
                        self._evict(symbol)
                symbols = [s for s in [self.DEFAULT, *self._wanted] if self._due.get(s, 0) <= now]
            for symbol in symbols:
                if self._stop.is_set():
                    break
                try:
                    self.cycle(symbol)
                except Exception as error:
                    with self._lock:
                        if symbol == self.DEFAULT or symbol in self._wanted:
                            self._errors[symbol] = str(error)[:200]
                    LOG.warning('market context %s: %s', symbol, error)
                finally:
                    with self._lock:
                        if symbol == self.DEFAULT or symbol in self._wanted:
                            self._due[symbol] = time.monotonic()+60
            self._wake.wait(5)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='market-context', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def snapshot(self, zone='America/New_York', symbol=DEFAULT):
        self.validate(symbol)
        if zone not in ZONES:
            raise ValueError('unsupported timezone')
        now = int(time.time()*1000)
        with self._lock:
            if symbol != self.DEFAULT:
                first = symbol not in self._wanted
                self._wanted[symbol] = time.monotonic()
                self._wanted.move_to_end(symbol)
                while len(self._wanted) > self.MAX_EXTRA:
                    self._evict(next(iter(self._wanted)))
                if first:
                    self._wake.set()
            value = self._snapshots.get(symbol, {}).get(zone)
            error = self._errors.get(symbol)
        # Empty state has the requested identity; never reuse another coin's chart.
        if value is None:
            value = dict(symbol=symbol, source='OKX', bar='15m', timezone=zone,
                         candles=[], levels=[], last_close_ms=None)
        close = value.get('last_close_ms')
        return dict(value, error=error, loading=not value.get('candles') and not error,
                    stale=close is None or not 0 <= now-close <= STEP+120000, read_ms=now)
