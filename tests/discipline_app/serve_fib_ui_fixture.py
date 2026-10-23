"""Synthetic-only mobile 1.618 acceptance, fake exchange, no private credentials.

VITE_SPIKE_SERVICE_URL / VITE_SPIKE_CANDLES_HTTP=http://127.0.0.1:18922,
VITE_SPIKE_CANDLES_WS=ws://127.0.0.1:18922/ws/v5/business,
VITE_SPIKE_LIVE_QUOTES=off. Uses the existing entry fixture exchange and real
server-owned wick selection and 1R sizing, never the live service.
"""
import argparse
import asyncio
from datetime import datetime, timedelta
import math
from pathlib import Path
import tempfile
import threading
import time
from zoneinfo import ZoneInfo
import uvicorn
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from test_live_book import quote, enter, ticket
from test_managed_orders import ManagedExchange
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.monday_fib import FibUnavailable, project_snapshot

TOKEN='ui-fixture-only-not-an-account-secret-123456'
STEPS={'1m':60,'5m':300,'15m':900,'1H':3600,'4H':14400,'1D':86400}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--web-directory',required=True)
    parser.add_argument('--activity',action='store_true',help='Seed synthetic positions/history for visual acceptance only.')
    options=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='spike-fib-ui-') as directory:
        exchange=ManagedExchange(time.time);book=LiveBook(Path(directory)/'fixture.sqlite3',exchange)
        quote(book);book.reconcile();stopped=threading.Event()
        if options.activity:
            enter(book,ticket(fraction='.265'))
            with book.transaction() as (_,state):
                state['closed'].append(dict(id='synthetic-completed',symbol='ETH-USDT-SWAP',side='short',
                    quantity='.05',remaining='0',entry='100',stop='101',original_stop='102',risk='1',
                    margin='.5',leverage=10,pnl='1.23',breakeven=False,stages=[],protection='closed',
                    policy=state['policy'],entry_fee='.01',total_fee='.03',close_reason='exchange',opened=time.time()-7200,closed=time.time()-3600))
        seed_orders=sum(path.endswith('/order') for path,_ in exchange.writes)
        def refresh():
            while not stopped.wait(2):quote(book);book.reconcile()
        thread=threading.Thread(target=refresh,daemon=True);thread.start()
        app=create_app(book,TOKEN,market_enabled=False,web_directory=options.web_directory)
        control=dict(offline=False,empty=False,latest='BTC');started=int(time.time()*1000)
        metrics=dict(streams=0,active=0,auto_stop=0,wick_stop=0)
        class Feed:
            def read(self):
                if control['offline']:raise FibUnavailable()
                now=int(time.time()*1000);date=datetime.now(ZoneInfo('America/New_York'));monday=(date-timedelta(days=date.weekday())).replace(hour=0,minute=0,second=0,microsecond=0);week=monday.date().isoformat()
                rows=[]
                for coin,side,high,low in [('BTC','upper',101,97),('ETH','lower',2100,1900)]:
                    symbol=coin+'-USDT-SWAP';event=dict(week=week,symbol=symbol,side=side,source='live',observed_ms=started-(1000 if coin==control['latest'] else 60000))
                    rows.append(dict(symbol=symbol,high=high,low=low,price=100 if coin=='BTC' else 2000,stale=False,touches={side:event}))
                return project_snapshot(dict(now_ms=now,week=week,timezone='America/New_York',monday_complete=True,stream=dict(connected=True,last_message_ms=now),items=[] if control['empty'] else rows),now)
        app.state.monday_fib=Feed()
        def candle(stamp,step,symbol):
            base=2000 if symbol.startswith('ETH') else 100;i=stamp//step;o=base*(1+.005*math.sin(i/9));c=o+base*.002*math.cos(i/4)
            high,low=max(o,c)+base*.001,min(o,c)-base*.001
            if stamp==int(time.time())//step*step-2*step:high,low=(2300,1750) if symbol.startswith('ETH') else (106,94)
            return [str(stamp*1000),str(o),str(high),str(low),str(c),'100','20','2000','1' if stamp+step<=time.time() else '0']
        def candles(instId='BTC-USDT-SWAP',bar='15m',after=0):
            step=STEPS[bar];newest=int(after)//1000-step if after else int(time.time())//step*step
            return dict(code='0',data=[candle(newest-i*step,step,instId) for i in range(300)])
        class History:
            def read(self,symbol,period,before):return candles(symbol,period,before*1000 if before else 0)
        app.state.candle_history=History()
        app.get('/api/v5/market/candles')(candles);app.get('/api/v5/market/history-candles')(candles)
        @app.websocket('/ws/v5/business')
        async def stream(ws:WebSocket):
            await ws.accept();metrics['active']+=1;metrics['streams']+=1
            try:
                sub=(await ws.receive_json())['args'][0];step=STEPS[sub['channel'].removeprefix('candle')]
                while True:
                    await ws.send_json(dict(arg=sub,data=[candle(int(time.time())//step*step,step,sub['instId'])]))
                    try:
                        if await asyncio.wait_for(ws.receive_text(),timeout=1)=='ping':await ws.send_text('pong')
                    except asyncio.TimeoutError:pass
            except (WebSocketDisconnect,RuntimeError):pass
            finally:metrics['active']-=1
        @app.middleware('http')
        async def count(request,call_next):
            if request.url.path=='/v1/auto-stop':metrics['auto_stop']+=1
            if request.url.path=='/v1/market/monday-wick':metrics['wick_stop']+=1
            return await call_next(request)
        @app.get('/qa/metrics')
        def stats():return dict(**metrics,seed_orders=seed_orders,order_writes=sum(path.endswith('/order') for path,_ in exchange.writes)-seed_orders)
        @app.post('/qa/control/{name}/{value}')
        def update(name:str,value:str):
            if name in ('empty','offline'):control[name]=value=='true'
            elif name=='latest':control[name]=value
            return control
        try:uvicorn.run(app,host='127.0.0.1',port=18922,access_log=False)
        finally:stopped.set();thread.join(3)


if __name__=='__main__':main()
