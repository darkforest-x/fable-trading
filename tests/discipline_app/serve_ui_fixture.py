"""Disposable loopback UI fixture; synthetic trades, never the user's ledger.

Run from repository root with PYTHONPATH=. and the project test environment.
Stop with Ctrl-C; TemporaryDirectory removes the fixture database on exit.
"""
import argparse
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
import uvicorn
from fastapi.responses import HTMLResponse
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.policy import Policy

TOKEN = 'ui-fixture-only-not-an-account-secret-123456'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8913)
    parser.add_argument('--legacy', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='spike-ui-fixture-') as directory:
        clock = [time.time() - 4*86400]
        book = Book(Path(directory)/'paper.sqlite3', lambda: clock[0])
        book.configure(replace(Policy(), margin_mode='isolated', risk_fraction='0.02',
            major_exposure_multiple=None, alt_exposure_multiple=None) if args.legacy else Policy())
        instruments = {f'{s}-USDT-SWAP': dict(symbol=f'{s}-USDT-SWAP', step='.01',
            minimum='.01', tick='.01', max_leverage=100) for s in ('BTC','ETH','SOL')}
        def quote(price='100'):
            book.update_market(instruments, {s:dict(last=price, bid=price, ask=price,
                time=book.clock(), source='SYNTHETIC UI TEST FIXTURE') for s in instruments})
        for symbol, exit_price in (('BTC','128'),('BTC','90'),('ETH','128')):
            quote()
            book.enter(uuid.uuid4().hex, dict(symbol=f'{symbol}-USDT-SWAP', side='long',
                kind='market', price='', stop='95', fraction='1', leverage=3), 1)
            quote(exit_price)
            clock[0] += 86401
        book.clock = time.time
        quote()
        if args.legacy:
            book.configure(Policy())
        stopped = threading.Event()
        def keep_quotes_fresh():
            while not stopped.wait(1):
                quote()
        thread = threading.Thread(target=keep_quotes_fresh, daemon=True)
        thread.start()
        try:
            app = create_app(book,TOKEN,market_enabled=False)
            @app.get('/qa/mobile', response_class=HTMLResponse)
            def mobile(width: int = 390):
                width = 320 if width == 320 else 390
                return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Mobile {width}px test fixture" src="/" style="width:{width}px;height:844px;border:0"></iframe></body></html>'
            uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
        finally:
            stopped.set()
            thread.join(3)


if __name__ == '__main__':
    main()
