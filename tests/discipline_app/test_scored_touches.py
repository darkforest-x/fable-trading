"""Authenticated read-only history for persisted Monday 1.618 score events."""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.monitor_feed import MonitorFeed
from yoyo.discipline_app import scored_touches as source
from test_discipline_service import Clock


TOKEN = 'scored-touch-fixture-token-not-a-secret-012345'
AUTH = {'Authorization': f'Bearer {TOKEN}'}
SHANGHAI = ZoneInfo('Asia/Shanghai')
NEW_YORK = ZoneInfo('America/New_York')


def day_start_ms(now_ms):
    return int(datetime.fromtimestamp(now_ms / 1000, SHANGHAI)
               .replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def ny_week(candle_ms):
    local_day = datetime.fromtimestamp(candle_ms / 1000, NEW_YORK).date()
    return (local_day - timedelta(days=local_day.weekday())).isoformat()


def make_app(tmp_path):
    book = Book(Path(tmp_path) / 'discipline.sqlite3', Clock())
    calls = []

    def fetch(path, body=None):
        calls.append((path, body) if body is not None else path)
        raise AssertionError('scored touches must read the shared SQLite ledger directly')

    app = create_app(book, TOKEN, market_enabled=False, monitor_feed=MonitorFeed(fetch))
    return TestClient(app), book, calls


def seed_ledger(runtime, now_ms):
    runtime.mkdir(parents=True, exist_ok=True)
    start = day_start_ms(now_ms)
    rows = [
        # Exactly 0.30 is included, and a floating score is returned unrounded.
        (1, ny_week(start), 'TODAY-USDT-SWAP', 1, start, start + 1, 'scored', 0.30000004, '{"feature":1}'),
        # Same timestamp checks deterministic id DESC order at the page boundary.
        (2, ny_week(start - 60_000), 'OLDA-USDT-SWAP', -1, start - 60_000, start - 59_000,
         'scored', 0.7, '{"feature":2}'),
        (3, ny_week(start - 60_000), 'OLDB-USDT-SWAP', 1, start - 60_000, None,
         'scored', 0.31, '{"feature":3}'),
        (4, ny_week(start - 120_000), 'OLDC-USDT-SWAP', 1, start - 120_000, start - 119_000,
         'scored', 0.55, '{"feature":4}'),
        (5, ny_week(start - 180_000), 'LOW-USDT-SWAP', 1, start - 180_000, start - 179_000,
         'scored', 0.2999, None),
        (6, ny_week(start - 240_000), 'PENDING-USDT-SWAP', 1, start - 240_000, start - 239_000,
         'pending_score', 0.8, None),
        (7, ny_week(start - 300_000), 'ERROR-USDT-SWAP', 1, start - 300_000, start - 299_000,
         'feature_error', 0.9, None),
        (8, ny_week(now_ms + 60_000), 'FUTURE-USDT-SWAP', -1, now_ms + 60_000, now_ms + 61_000,
         'scored', 0.95, None),
    ]
    path = runtime / 'monday-reclaim-paper.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE signals (id INTEGER PRIMARY KEY, week TEXT, symbol TEXT, side INTEGER, '
                    'candle_ms INTEGER, observed_ms INTEGER, status TEXT, score REAL, features TEXT)')
        db.executemany('INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return path


def test_authenticated_today_and_history_pages_are_read_only_complete_and_stable(tmp_path, monkeypatch):
    runtime = Path(tmp_path) / 'ImpulseMonitor'
    now_ms = int(time.time() * 1000)
    db_path = seed_ledger(runtime, now_ms)
    monkeypatch.setenv('FABLE_IMPULSE_RUNTIME', str(runtime))
    monkeypatch.setattr(source, 'time', SimpleNamespace(time=lambda: now_ms / 1000))
    http, book, calls = make_app(tmp_path)
    before = book.snapshot()
    before_rows = sqlite3.connect(db_path).execute('SELECT COUNT(*) FROM signals').fetchone()[0]

    path = '/v1/monitor/scored-touches'
    assert http.get(path).status_code == 401
    first = http.get(path, params={'offset': 0, 'limit': 2}, headers=AUTH)
    assert first.status_code == 200
    body = first.json()
    assert body['schema'] == 'monday_reclaim_paper.scored_history.v1'
    assert body['timezone'] == 'Asia/Shanghai' and body['threshold'] == 0.30
    assert body['day_start_ms'] == day_start_ms(body['now_ms'])
    assert body['today']['total'] == 1 and len(body['today']['rows']) == 1
    today = body['today']['rows'][0]
    assert (today['week'], today['symbol'], today['side'], today['score']) == (
        ny_week(today['candle_ms']), 'TODAY-USDT-SWAP', 'lower', 0.30000004)
    assert today['candle_ms'] >= body['day_start_ms']
    assert (body['history']['offset'], body['history']['limit'], body['history']['total'],
            body['history']['has_more']) == (0, 2, 3, True)
    first_rows = body['history']['rows']
    assert [(row['id'], row['symbol'], row['side'], row['score']) for row in first_rows] == [
        (3, 'OLDB-USDT-SWAP', 'lower', 0.31), (2, 'OLDA-USDT-SWAP', 'upper', 0.7)]
    assert all(set(row) == {'id', 'week', 'symbol', 'side', 'candle_ms', 'score'}
               for row in body['today']['rows'] + first_rows)

    second = http.get(path, params={'offset': 2, 'limit': 2}, headers=AUTH)
    assert second.status_code == 200
    history = second.json()['history']
    assert (history['offset'], history['total'], history['has_more']) == (2, 3, False)
    assert [(row['id'], row['symbol']) for row in history['rows']] == [(4, 'OLDC-USDT-SWAP')]
    assert http.get(path, params={'offset': -1}, headers=AUTH).status_code == 422
    assert http.get(path, params={'limit': 101}, headers=AUTH).status_code == 422
    assert http.post(path, headers=AUTH, json={}).status_code == 405
    assert calls == []
    assert sqlite3.connect(db_path).execute('SELECT COUNT(*) FROM signals').fetchone()[0] == before_rows
    after = book.snapshot()
    assert before['positions'] == after['positions'] and before['pending'] == after['pending']
    assert before['daily'] == after['daily']


def test_missing_score_ledger_is_a_normal_503_and_is_not_created(tmp_path, monkeypatch):
    runtime = Path(tmp_path) / 'missing-monitor-runtime'
    monkeypatch.setenv('FABLE_IMPULSE_RUNTIME', str(runtime))
    http, _, calls = make_app(tmp_path)
    response = http.get('/v1/monitor/scored-touches', headers=AUTH)
    assert response.status_code == 503
    assert response.json()['detail'] == '评分历史账本暂不可用'
    assert not (runtime / 'monday-reclaim-paper.sqlite3').exists()
    assert calls == []
