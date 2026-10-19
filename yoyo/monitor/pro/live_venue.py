"""OKX V5 read adapter for the isolated Pro account and public causal inputs.

Protocol: https://www.okx.com/docs-v5/en/ (Trade and Account, 2026-10-18).
Credentials stay in an owner-only file outside the repository. The web server
always constructs read-only clients; only the locked Pro worker enables POST.
"""
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
import time
import math
import pandas as pd
from yoyo.okx_execution.client import Client
from yoyo.okx_execution.config import read_private, DEFAULT_RUNTIME, DEFAULT_MANUAL_RUNTIME
from yoyo.okx_execution.manual_queue import ReadLedger
from yoyo.okx_execution.manual import total_account
from yoyo.okx_execution.manual_ma import envelope_stop, MA_KEYS
from yoyo.okx_execution.risk import D

DEFAULT_LIVE = Path.home()/'.local/share/spike-pro/live'
TF = {'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1D':86400000}


@dataclass
class VenueConfig:
    host: str='https://www.okx.com'
    demo: bool=False
    def validate(self):
        if self.host not in {'https://www.okx.com','https://us.okx.com','https://eea.okx.com'} or self.demo is not False:
            raise ValueError('Pro 实盘只允许已核对的 OKX 正式环境')


def client(directory=DEFAULT_LIVE, *, writes=False):
    path=Path(directory)/'credentials.json'
    if not path.exists():raise ValueError('尚未绑定 Pro 独立账户；请由 Owner 在本机配置专用凭据')
    value=read_private(path)
    if value.get('exclusive_account')!='SPIKE-PRO-ONLY':raise ValueError('需要确认该账户只由 Pro 执行器写入')
    if not all(isinstance(value.get(k),str) and value[k] for k in ('api_key','secret','passphrase')):
        raise ValueError('Pro 私有凭据字段不完整')
    return Client(VenueConfig(value.get('host','https://www.okx.com')),value,writes=writes)


def account(c, *, isolated=True):
    cfg=c.get('/api/v5/account/config')[0]
    if cfg.get('posMode')!='long_short_mode':raise ValueError('Pro 需要双向持仓账户；不会自动修改账户模式')
    uid=cfg.get('uid')
    if not uid:raise ValueError('交易所未返回账户身份')
    if isolated:
        for runtime in (DEFAULT_RUNTIME,DEFAULT_MANUAL_RUNTIME):
            if (runtime/'execution.sqlite3').exists() and ReadLedger(runtime).meta('account_uid')==uid:
                raise ValueError('该账户已由旧版执行器管理；Pro 必须使用独立交易账户')
    balances=total_account(c)
    positions=c.get('/api/v5/account/positions',instType='SWAP')
    orders=c.get('/api/v5/trade/orders-pending',instType='SWAP',limit='100')
    if len(orders)>=100:raise ValueError('待成交委托超过当前归属核验范围')
    algos=[]
    for kind in ('oco','conditional','trigger','move_order_stop'):
        rows=c.get('/api/v5/trade/orders-algo-pending',ordType=kind,instType='SWAP',limit='100')
        if len(rows)>=100:raise ValueError('保护委托超过当前归属核验范围')
        algos+=rows
    return dict(uid=uid,position_mode=cfg['posMode'],**balances,positions=positions,
                orders=orders,algos=algos,updated_ms=int(time.time()*1000))


class PublicData:
    def __init__(self, c=None, clock=lambda:int(time.time()*1000)):
        self.client=c or Client(VenueConfig())
        self.clock=clock
        self.cache={}
        self.raw={}
        self.lock=RLock()
        self.server_clock=None

    def time_bounds(self):
        """Bound exchange time by request RTT; do not relax quote freshness."""
        with self.lock:
            local=self.clock()
            if not self.server_clock or not 0<=local-self.server_clock[1]<60000:
                start=self.clock();row=self.client.get('/api/v5/public/time')[0];end=self.clock()
                if not 0<=end-start<=5000:raise ValueError('交易所时钟核对延迟过大')
                self.server_clock=(start,end,int(row['ts']))
            start,end,server=self.server_clock
            local=self.clock()
            return server+local-end,server+local-start

    def candles(self, symbol, timeframe):
        """OHLCV + six close-price MAs, 1,000 closed bars; never developing bars."""
        step=TF[timeframe];cutoff=self.time_bounds()[0]//step*step;key=(symbol,timeframe,cutoff)
        with self.lock:
            if key in self.cache:return self.cache[key]
            cached=self.raw.get((symbol,timeframe),[])
            all_rows=list(cached) if cached and cutoff-int(cached[-1][0])<=200*step else []
            after=None
            for _ in range(5):
                query=dict(instId=symbol,bar='1Dutc' if timeframe=='1D' else timeframe,limit='300')
                if after:query['after']=str(after)
                rows=self.client.get('/api/v5/market/candles',**query)
                if not rows:break
                all_rows+=rows;after=min(int(r[0]) for r in rows)
                closed={int(r[0]) for r in all_rows if len(r)>=9 and r[8]=='1' and int(r[0])+step<=cutoff}
                if len(closed)>=1000:break
            rows={int(r[0]):r for r in all_rows if len(r)>=9 and r[8]=='1' and int(r[0])+step<=cutoff}
            ts=sorted(rows)[-1000:]
            if len(ts)!=1000 or ts[-1]+step!=cutoff or any(b-a!=step for a,b in zip(ts,ts[1:])):
                raise ValueError('收盘 K 线窗口缺失；未用不完整数据产生信号')
            frame=pd.DataFrame([dict(t=t,open=float(rows[t][1]),high=float(rows[t][2]),low=float(rows[t][3]),close=float(rows[t][4]),volume=float(rows[t][5])) for t in ts])
            if not frame.apply(lambda col: col.map(math.isfinite)).all().all():raise ValueError('K 线包含非有限数值')
            if not ((frame.low>0)&(frame.high>=frame[['open','close']].max(axis=1))&(frame.low<=frame[['open','close']].min(axis=1))&(frame.volume>=0)).all():raise ValueError('K 线价格或成交量无效')
            for n in (20,60,120):
                frame['sma'+str(n)]=frame.close.rolling(n).mean()
                frame['ema'+str(n)]=frame.close.ewm(span=n,adjust=False).mean()
            result=frame.iloc[119:].to_dict('records')
            self.cache={k:v for k,v in self.cache.items() if k[:2]!=(symbol,timeframe)}
            self.cache[key]=result
            self.raw[(symbol,timeframe)]=[rows[t] for t in ts]
            while len(self.raw)>64:self.raw.pop(next(iter(self.raw)))
            while len(self.cache)>64:self.cache.pop(next(iter(self.cache)))
            return result

    def stop(self, symbol, side, tick):
        rows=self.candles(symbol,'5m');last=rows[-1]
        return envelope_stop(dict(ma={k:str(last[k]) for k in MA_KEYS},cutoff_ms=last['t']+300000,observed_ms=self.clock()),side,tick)

    def quote(self,symbol):
        self.time_bounds()
        book=self.client.get('/api/v5/market/books',instId=symbol,sz='5')[0]
        if not book.get('bids') or not book.get('asks'):raise ValueError('交易所盘口为空')
        row=dict(instId=symbol,bidPx=book['bids'][0][0],askPx=book['asks'][0][0],ts=book['ts'])
        lower,upper=self.time_bounds()
        if int(row['ts'])>upper or upper-int(row['ts'])>5000 or min(D(row['bidPx']),D(row['askPx']))<=0:
            raise ValueError('交易所买卖报价不可用或过期')
        if D(row['bidPx'])>D(row['askPx']):raise ValueError('买卖报价交叉')
        return row

    def trades(self,symbol):
        self.time_bounds()
        rows=self.client.get('/api/v5/market/trades',instId=symbol,limit='500')
        unique={r['tradeId']:r for r in rows if r.get('tradeId')}
        result=sorted(unique.values(),key=lambda r:int(r['ts']))
        lower,upper=self.time_bounds()
        if not result or not 0<=upper-int(result[-1]['ts'])<=15000:raise ValueError('成交样本过期')
        if any(r.get('side') not in ('buy','sell') or D(r['sz'])<=0 or int(r['ts'])>upper for r in result):raise ValueError('成交样本字段无效')
        return result
