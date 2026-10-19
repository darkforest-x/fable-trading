"""Slow/offline UI acceptance with two fake accounts and 494 public coin names."""
import asyncio
import argparse
import json
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse
from yoyo.discipline_app.accounts import Accounts
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.live_book import LiveBook
from test_accounts_catalog import IdentityExchange, Probe
from test_live_book import enter, quote, ticket
from test_managed_orders import ManagedExchange
from test_partial_close import FakeExchange

TOKEN = 'ui-fixture-only-not-an-account-secret-123456'

class ActivityExchange(ManagedExchange):
    """Synthetic resting orders; no exchange network or credentials are used."""
    def __init__(self, uid):
        super().__init__(time.time)
        self.uid = uid
        self.timeout_next_close = False

    def post(self, path, payload):
        if path.endswith("/order") and not payload.get("attachAlgoOrds"):
            return FakeExchange.post(self, path, payload)
        return super().post(path, payload)

    def get(self, path, **params):
        rows = super().get(path, **params)
        if path.endswith('/config'):
            rows[0]['uid'] = self.uid
        return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--position', action='store_true', help='Seed a fake position for display acceptance only.')
    parser.add_argument('--delayed-close-response', action='store_true')
    parser.add_argument('--activity', action='store_true', help='Seed a verified resting order and synthetic closed trade.')
    parser.add_argument('--delayed-order-response', action='store_true', help='Let reconciliation finish before the fake submit response.')
    options = parser.parse_args()
    stopped = threading.Event()
    with tempfile.TemporaryDirectory(prefix='spike-performance-ui-') as directory:
        root = Path(directory)
        factory = ActivityExchange if options.activity else IdentityExchange
        primary = LiveBook(root/'live.sqlite3', factory('first'))
        manager = Accounts(primary, root, lambda c: factory(c.key), Probe)
        primary.reconcile()
        if options.position:
            quote(primary)
            enter(primary, ticket(fraction='.265'))
        if options.activity:
            quote(primary)
            enter(primary, ticket(kind='limit', price='99', fraction='.1'))
            with primary.transaction() as (_, state):
                state['closed'].append(dict(id='synthetic-completed',symbol='ETH-USDT-SWAP',side='short',
                    quantity='.05',remaining='0',entry='100',stop='101',original_stop='102',risk='1',
                    margin='.5',leverage=10,pnl='1.23',breakeven=False,stages=[],protection='closed',
                    policy=state['policy'],entry_fee='.01',total_fee='.03',close_reason='exchange',opened=time.time()-7200,closed=time.time()-3600))
        alternate = manager.add('备用测试账户', 'second', 'synthetic-secret', 'synthetic-phrase')
        manager.books[alternate['id']].client.equity = '2000'
        manifest = json.loads(Path('yoyo/discipline_app/mobile/src/assets/coin-logos.json').read_text())
        coins = manifest['bundled'] + manifest['fallback']
        instruments = {f'{s.upper()}-USDT-SWAP':dict(symbol=f'{s.upper()}-USDT-SWAP', coin=s.upper(), tradable=True, step='.01', minimum='.01', tick='.01', contract_value='.01', fee_group='2', max_leverage=100) for s in coins}
        def update():
            while not stopped.is_set():
                price = '101' if options.position else '100'
                manager.update_market(instruments, {s:dict(last=price, bid=price, ask=price, time=time.time(), today_change={'BTC-USDT-SWAP':'2.5','ETH-USDT-SWAP':'-1.2','SOL-USDT-SWAP':'5.1'}.get(s,'0')) for s in instruments})
                for book in list(manager.books.values()):
                    book.reconcile()
                stopped.wait(2)
        thread = threading.Thread(target=update, daemon=True)
        thread.start()
        app = create_app(manager, TOKEN, market_enabled=False, web_directory='yoyo/discipline_app/mobile/dist-fixture')
        counts, network = Counter(), {'offline':False}
        @app.middleware('http')
        async def latency(request, call_next):
            if request.url.path.startswith('/v1/'):
                counts[request.url.path] += 1
                if network['offline']:
                    return JSONResponse({'detail':'Synthetic offline fixture'}, status_code=503)
                if options.delayed_close_response and request.method == 'GET' and request.url.path.startswith('/v1/close-requests/'):
                    await asyncio.sleep(10)
                if request.url.path == '/v1/statistics':
                    await asyncio.sleep(4)
                if options.activity and request.url.path == '/v1/auto-stop':
                    await asyncio.sleep(.6)
            response = await call_next(request)
            if options.delayed_close_response and request.method == 'POST' and request.url.path.endswith('/close'):
                await asyncio.sleep(8)
            if options.delayed_order_response and request.method == 'POST' and request.url.path == '/v1/order-requests':
                await asyncio.sleep(8)
            return response
        @app.get('/qa/offline/{value}')
        def offline(value: str):
            network['offline'] = value == 'on'
            return network
        @app.get('/qa/metrics')
        def metrics():
            return dict(reads=counts, writes=sum(len(b.client.writes) for b in manager.books.values()))
        @app.get('/qa/mobile', response_class=HTMLResponse)
        def mobile(width: int = 390, height: int = 844):
            width, height = max(320, min(width, 640)), max(480, min(height, 1000))
            return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Synthetic performance {width}px" src="/" style="width:{width}px;height:{height}px;border:0"></iframe></body></html>'
        try:
            uvicorn.run(app, host='127.0.0.1', port=8916, access_log=False)
        finally:
            stopped.set()
            thread.join(3)


if __name__ == '__main__':
    main()
