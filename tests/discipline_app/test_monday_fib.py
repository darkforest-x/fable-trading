"""Market projection must not leak notification/account state or re-time history."""
from datetime import datetime
from zoneinfo import ZoneInfo
import copy
import json
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.monday_fib import project_snapshot, FibUnavailable
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book


def source(week='2026-10-19'):
    stamp = int(datetime.fromisoformat(week+'T12:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1000)
    event = dict(week=week, symbol='BTC-USDT-SWAP', side='upper', source='history',
                 candle_ms=stamp+86400000, observed_ms=stamp+2*86400000,
                 message_id='PRIVATE', error='PRIVATE', status='sent')
    return dict(week=week, now_ms=stamp+2*86400000, timezone='America/New_York', monday_complete=True,
                telegram=dict(secret='PRIVATE'), events=[event], stream=dict(connected=True, last_message_ms=stamp+2*86400000),
                items=[dict(symbol='BTC-USDT-SWAP', high=110, low=90, price=120, stale=False, touches=dict(upper=event))])


def test_market_projection_drops_receipts_and_keeps_occurrence_time():
    raw=source(); out=project_snapshot(raw,raw['now_ms'])
    assert 'PRIVATE' not in json.dumps(out)
    assert out['items'][0]['touches']['upper']['occurred_ms']==raw['items'][0]['touches']['upper']['candle_ms']
    assert out['items'][0]['upper']==pytest.approx(122.36)
    assert out['items'][0]['lower']==pytest.approx(77.64)
    assert out['next_week_ms']-out['week_start_ms']==7*86400000


def test_stale_week_missing_anchors_and_future_events_fail_honestly():
    raw=source()
    with pytest.raises(FibUnavailable):project_snapshot(raw,raw['now_ms']+61000)
    invalid=copy.deepcopy(raw);invalid['items'][0]['touches']['upper']['candle_ms']=raw['now_ms']+1
    assert project_snapshot(invalid,raw['now_ms'])['items']==[]
    invalid=copy.deepcopy(raw);invalid['items'][0]['high']=None
    assert project_snapshot(invalid,raw['now_ms'])['items']==[]


def test_new_york_dst_week_uses_calendar_boundaries():
    raw=source('2026-10-26');out=project_snapshot(raw,raw['now_ms'])
    assert out['next_week_ms']-out['week_start_ms']==169*3600000


def test_public_market_route_does_not_unlock_account_or_writes(tmp_path):
    book=Book(tmp_path/'paper.sqlite3');app=create_app(book,'x'*32,market_enabled=False)
    raw=source()
    class Feed:
        def read(self):return project_snapshot(raw,raw['now_ms'])
    app.state.monday_fib=Feed()
    with TestClient(app) as client:
        assert client.get('/v1/market/monday-fib').status_code==200
        assert client.get('/v1/state').status_code==401
        assert client.post('/v1/market/monday-fib',json={}).status_code==405
        assert client.post('/v1/auto-stop',json={}).status_code==401
