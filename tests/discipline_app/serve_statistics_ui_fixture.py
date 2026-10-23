"""Real statistics UI with synthetic external PnL; no account credentials.

Build mobile with VITE_SPIKE_SERVICE_URL=http://127.0.0.1:18921
VITE_SPIKE_LIVE_QUOTES=off npx vite build --outDir dist-statistics-fixture.
Run PYTHONPATH=. .venv/bin/python tests/discipline_app/serve_statistics_ui_fixture.py.
The fake account has 25 USDT external floating PnL, -12 managed realized PnL
and a 1000 USDT fixed basis. Use --without-basis to verify missing configuration.
"""
import argparse
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse

from test_live_book import Exchange, quote
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.statistics_basis import set_fixed_basis

TOKEN = 'ui-fixture-only-not-an-account-secret-123456'


class ExternalEquity(Exchange):
    def get(self, path, **params):
        rows = super().get(path, **params)
        if path.endswith('/balance'):
            rows[0]['details'][0].update(eq='1025', cashBal='1000')
        return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--without-basis', action='store_true')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='spike-statistics-ui-') as directory:
        client = ExternalEquity(time.time)
        book = LiveBook(Path(directory)/'fixture.sqlite3', client)
        quote(book)
        book.reconcile()
        with book.transaction() as (_, state):
            state['closed'].append(dict(
                id='fixture-closed', symbol='BTC-USDT-SWAP', side='long',
                entry='100', remaining='0', quantity='1', stop='95', original_stop='95',
                risk='5', margin='10', leverage=10, pnl='-12.00', entry_fee='.05',
                total_fee='.10', policy=state['policy'], protection='closed',
                stages=[], breakeven=False, opened=time.time()-7200, closed=time.time()-3600))
            reference = state['account_reference']
        if not options.without_basis:
            set_fixed_basis(book.path, '1000', reference, 'Synthetic display capital')
        stopped = threading.Event()

        def refresh():
            while not stopped.wait(2):
                quote(book)
                book.reconcile()

        thread = threading.Thread(target=refresh, daemon=True)
        thread.start()
        app = create_app(book, TOKEN, market_enabled=False,
                         web_directory='yoyo/discipline_app/mobile/dist-statistics-fixture')

        @app.middleware('http')
        async def read_only(request, call_next):
            if request.method != 'GET':
                return JSONResponse({'detail': 'Read-only statistics fixture'}, status_code=405)
            return await call_next(request)

        @app.get('/qa/writes')
        def writes():
            return {'writes': len(client.writes)}

        try:
            uvicorn.run(app, host='127.0.0.1', port=18921, access_log=False)
        finally:
            stopped.set()
            thread.join(3)


if __name__ == '__main__':
    main()
