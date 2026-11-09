"""The live-wick6x3mid booklet and history API project its saved funded account."""
from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor import strategy_backtest as api
from yoyo.monitor import strategy_live_wick as booklet


TRACK = 'live-wick6x3mid'


def test_booklet_history_keeps_historical_1000u_separate_and_counts_funded_trades():
    history_path = booklet.ROOT / 'yoyo/monitor/static/strategy-booklet/live-wick6x3mid-history.json'
    history = json.loads(history_path.read_text())
    research = json.loads((history_path.parent / 'research.json').read_text())
    account = research['accounts'][TRACK]

    assert history['summary']['start'] == 1000
    assert history['summary']['trades'] == history['meta']['funded'] == 400
    assert history['meta']['candidates'] == 416
    assert history['meta']['allocator_result']['skipped'] == 16
    assert history['meta']['start'] == '2020-01-01'
    assert history['meta']['end'] == '2026-10-27 15:00:00+00:00'
    assert history['summary']['end'] == 24384.007767566207
    assert history['summary']['cash_added'] == 0
    assert '不注入独立前向1000U模拟账户' in account['detail']
    section = account['sections'][0]
    assert account['status'] == '浅穿中点 · OKX 前向观察'
    assert '2020-01-01 至 2026-10-27 UTC' in section['rows'][0][0]
    assert section['rows'][0][1].startswith('0.958 · +153.81bp/笔')
    assert section['rows'][0][2].startswith('1.064 · +174.82bp/笔')
    assert section['rows'][1][1].startswith('0.918 · +150.40bp/笔')
    assert section['rows'][1][2].startswith('1.024 · +177.92bp/笔')
    assert section['rows'][2][1].startswith('1.046 · +157.00bp/笔')
    assert section['rows'][2][2].startswith('1.633 · +228.53bp/笔')
    assert 'Profit Factor' in section['note']
    assert '穿幅=触价收盘价与对应1.618价位之差的绝对值' in history['meta']['note']
    assert len(history['points']) == 401  # initial 1000U point plus 400 settlements


def test_history_endpoints_expose_only_the_400_funded_rows_and_saved_targets():
    value = api.ledger(TRACK)
    assert TRACK in api.TRACKS and TRACK in api.SAVED_FILLS and TRACK in api.EXTENDED
    assert value['meta']['trades'] == 400
    assert value['meta']['candidates'] == 416 and value['meta']['skipped'] == 16
    assert value['meta']['midpoint_trades'] == value['meta']['near_target_trades'] == 200
    assert len(value['rows']) == len({r['event'] for r in value['rows']}) == 400
    assert all(r['fills']['exit_price'] > 0 for r in value['rows'])
    assert sum(r['target'] == 'range_0.5' for r in value['rows']) == 200
    assert sum(r['target'] == 'near_monday_edge' for r in value['rows']) == 200
    assert sum(r['entry_rules']['target_rule'] == 'shallow_ny_monday_midpoint' for r in value['rows']) == 200
    assert all(r['entry_start_ms'] == r['entry_ms'] for r in value['rows'])
    assert all(r['fills']['entry_start_ms'] == r['fills']['entry_end_ms'] == r['fills']['score_close_ms'] == r['entry_ms']
               for r in value['rows'])

    app = FastAPI()
    api.install(app)
    client = TestClient(app)
    response = client.get('/api/strategy-backtest', params={'track': TRACK, 'limit': 100})
    assert response.status_code == 200
    page = response.json()
    assert page['total'] == page['meta']['trades'] == 400
    assert page['meta']['candidates'] == 416
    assert len(page['rows']) == 100 and page['has_more']

    row = value['rows'][0]
    date = datetime.fromtimestamp(row['exit_ms'] / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    daily = client.get('/api/strategy-backtest/day', params={'track': TRACK, 'date': date})
    assert daily.status_code == 200
    assert any(r['event'] == row['event'] for r in daily.json()['rows'])


def test_midpoint_history_rows_match_saved_account_results():
    value = api.ledger(TRACK)
    history = booklet.midpoint_history()
    assert len(value['rows']) == history['summary']['trades'] == 400
    assert abs(sum(r['pnl'] for r in value['rows']) - history['summary']['pnl']) < 1e-6
    assert abs(value['meta']['pnl'] - history['summary']['pnl']) < 1e-6
    assert value['meta']['start'] == history['meta']['start']
    assert value['meta']['end'] == history['meta']['end']


def test_chart_endpoint_uses_the_saved_midpoint_fill_price():
    value = api.ledger(TRACK)
    row = next(r for r in value['rows'] if r['target'] == 'range_0.5' and r['exit_reason'] == 4)
    chart = api.candles(TRACK, row['id'], '15m')
    assert chart['backtestTrade']['event'] == row['event']
    assert chart['backtestTrade']['target'] == 'range_0.5'
    assert chart['backtestTrade']['entry_rules']['target_rule'] == 'shallow_ny_monday_midpoint'
    assert chart['backtestTrade']['fills']['exit_price'] == row['fills']['exit_price']
    assert chart['candles'] and chart['source'] == 'Binance'
