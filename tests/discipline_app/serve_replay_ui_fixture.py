"""Disposable App QA with real Nautilus and synthetic historical public prices.

Build with VITE_SPIKE_SERVICE_URL=http://127.0.0.1:18927,
VITE_SPIKE_LIVE_QUOTES=off, outDir dist-replay-fixture. All non-replay writes
are rejected. The temporary database and owned native workers are closed on exit.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time

import uvicorn
from fastapi.responses import JSONResponse
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.replay_history import PERIODS, ReplayHistory, NY
from yoyo.discipline_app.replay_store import ReplayStore
from yoyo.monitor.monday_fib import week_window

TOKEN = 'replay-fixture-only-not-a-live-token-123456'
START = int(datetime(2026, 3, 10, 8, tzinfo=timezone.utc).timestamp())


class Relay:
    def read(self, symbol, period, before):
        step = PERIODS[period]
        latest = (before-1)//step*step
        data = []
        for i in range(300):
            stamp = latest-i*step
            o, h, l, c = 100, 105, 95, 100
            if stamp == START and period == '5m':
                h, l = 112, 88
            if stamp >= START+300 and period == '5m':
                o, h, l, c = 100, 104, 99, 102
            data.append([str(stamp*1000), str(o), str(h), str(l), str(c), '100', '1', '100', '1'])
        return dict(data=data)


class Feed:
    def read(self):
        now = int(time.time()*1000)
        window = week_window(now)
        return dict(now_ms=now, week=window['week'], timezone='America/New_York', week_start_ms=window['start'],
                    monday_end_ms=window['end'], next_week_ms=window['expires'], monday_complete=now>=window['end'],
                    connected=True, last_message_ms=now, items=[])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18927)
    parser.add_argument('--web-directory', default='yoyo/discipline_app/mobile/dist-replay-fixture')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='spike-replay-qa-') as directory:
        book = Book(Path(directory)/'unrelated-paper.sqlite')
        app = create_app(book, TOKEN, market_enabled=False, web_directory=args.web_directory)
        app.state.monday_fib = Feed()
        app.state.replay = ReplayStore(Path(directory)/'replay.sqlite', ReplayHistory(Relay(), roots=[], clock=lambda:START+30*86400))
        metrics = dict(non_replay_writes=0, replay_writes=0)

        @app.middleware('http')
        async def reject_other_writes(request, call_next):
            if request.method == 'POST':
                if request.url.path.startswith('/v1/replay/'):
                    metrics['replay_writes'] += 1
                else:
                    metrics['non_replay_writes'] += 1
                    return JSONResponse({'detail':'No real writes in replay fixture'}, status_code=405)
            return await call_next(request)

        @app.get('/qa/metrics')
        def qa_metrics():
            return metrics

        uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
