"""Disposable external-observation UI fixture; fake exchange, no credentials.

Build from mobile/ with VITE_SPIKE_SERVICE_URL=http://127.0.0.1:18919
and `npx vite build --outDir dist-external-fixture`, then run from repo root:
PYTHONPATH=. .venv/bin/python tests/discipline_app/serve_external_ui_fixture.py
Enter TOKEN below as the fixture's service token. Never use account credentials.
"""
import tempfile
import threading
import time
from pathlib import Path
import uvicorn
from fastapi.responses import HTMLResponse
from test_live_book import Exchange, quote
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.api import create_app

TOKEN = 'ui-fixture-only-not-an-account-secret-123456'


def main():
    with tempfile.TemporaryDirectory(prefix='spike-external-ui-') as directory:
        client = Exchange(time.time)
        client.positions = [dict(instId='ETH-USDT-SWAP', posSide='short', pos='10',
            posId='external-fixture', mgnMode='cross', avgPx='100', upl='1.20',
            notionalUsd='10', cTime=str(int(time.time()*1000)))]
        client.foreign = [dict(instId='SOL-USDT-SWAP', posSide='long', ordId='external-order',
            clOrdId='fixture-external', sz='80', accFillSz='0', px='90', cTime=str(int(time.time()*1000)))]
        book = LiveBook(Path(directory)/'fixture.sqlite3', client)
        quote(book); book.reconcile()
        stopped = threading.Event()
        def refresh():
            while not stopped.wait(2):
                quote(book); book.reconcile()
        thread = threading.Thread(target=refresh, daemon=True); thread.start()
        app = create_app(book, TOKEN, market_enabled=False, web_directory='yoyo/discipline_app/mobile/dist-external-fixture')
        @app.get('/qa/mobile', response_class=HTMLResponse)
        def mobile(width: int = 390):
            width = 320 if width == 320 else 390
            return f'<html><body style="margin:0;background:#e9eeea;display:flex;justify-content:center"><iframe title="Synthetic external records {width}px" src="/" style="width:{width}px;height:844px;border:0"></iframe></body></html>'
        @app.get('/qa/writes')
        def writes():
            return {'writes': len(client.writes)}
        try:
            uvicorn.run(app, host='127.0.0.1', port=18919, access_log=False)
        finally:
            stopped.set(); thread.join(3)


if __name__ == '__main__':
    main()
