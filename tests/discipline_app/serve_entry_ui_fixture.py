"""Loopback-only entry-flow QA: fake exchange and delayed receipts, no keys.

Build mobile with VITE_SPIKE_SERVICE_URL=http://127.0.0.1:18920 and
VITE_SPIKE_LIVE_QUOTES=off; pass its output directory with --web-directory.
Run from repo root with PYTHONPATH=. .venv/bin/python <this file>.
"""
import argparse
import asyncio
import tempfile
import threading
import time
from pathlib import Path
import uvicorn
from fastapi.responses import HTMLResponse
from test_live_book import quote
from test_managed_orders import ManagedExchange
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.api import create_app

TOKEN = 'ui-fixture-only-not-an-account-secret-123456'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-directory', required=True)
    parser.add_argument('--delay-receipt', type=float, default=0)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='spike-entry-ui-') as directory:
        exchange = ManagedExchange(time.time)
        book = LiveBook(Path(directory)/'fixture.sqlite3', exchange)
        quote(book); book.reconcile()
        stopped = threading.Event()
        def refresh():
            while not stopped.wait(2):
                quote(book); book.reconcile()
        thread = threading.Thread(target=refresh, daemon=True)
        thread.start()
        app = create_app(book, TOKEN, market_enabled=False, web_directory=options.web_directory)
        @app.middleware('http')
        async def latency(request, call_next):
            if request.url.path == '/v1/auto-stop':
                await asyncio.sleep(.6)
            response = await call_next(request)
            if request.url.path == '/v1/order-requests' and request.method == 'POST':
                await asyncio.sleep(options.delay_receipt)
            return response
        @app.get('/qa/metrics')
        def metrics():
            return dict(order_writes=sum(path.endswith('/order') for path, _ in exchange.writes),
                        order_sides=[payload.get('posSide') for path, payload in exchange.writes if path.endswith('/order')],
                        orders=[dict(side=row['posSide'], kind=row['ordType'], status=row['state']) for row in exchange.orders.values()])
        @app.get('/qa/mobile', response_class=HTMLResponse)
        def mobile(width: int = 390, height: int = 844):
            width, height = max(320, min(width, 640)), max(480, min(height, 1000))
            return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Synthetic entry {width}px" src="/" style="width:{width}px;height:{height}px;border:0"></iframe></body></html>'
        try:
            uvicorn.run(app, host='127.0.0.1', port=18920, access_log=False)
        finally:
            stopped.set(); thread.join(3)


if __name__ == '__main__':
    main()
