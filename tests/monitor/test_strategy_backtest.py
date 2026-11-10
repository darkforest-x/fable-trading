"""Saved capital totals and original-source chart windows, without execution."""
import csv
import gzip
import json

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor import strategy_backtest as api


@pytest.fixture
def saved(tmp_path):
    directory = tmp_path / 'ledger'
    directory.mkdir()
    stamp = 1704186000000  # Tuesday, after the NY Monday completed.
    rows = [dict(time_ms=stamp, equity=1000, pnl=0, cashflow=1000, notional=0, side='',
                 entry_ms='', symbol='', trade_id='')]
    for n, (symbol, pnl, delay) in enumerate([('AAAUSDT', -5, 0), ('BBBUSDT', 12, 300000),
                                             ('AAAUSDT', 30, 600000), ('BBBUSDT', -8, 600000)]):
        rows.append(dict(time_ms=stamp + delay + 900000, equity=1000, pnl=pnl, cashflow=0,
                         notional=100, side=1, entry_ms=stamp + delay, symbol=symbol, trade_id=f'{symbol}:week:1'))
    with gzip.open(directory / 'allin3-ledger.csv.gz', 'wt') as f:
        w = csv.DictWriter(f, fieldnames=rows[0]); w.writeheader(); w.writerows(rows)
    meta = dict(track='allin3', start='2023-01-01', end='2024-01-03', funded=4, note='BE0', sources=['saved'])
    (directory / 'manifest.json').write_text(json.dumps(dict(accounts={'allin3': meta}, source_commit='fixed')))
    identities = tmp_path / 'symbols'
    identity = identities / 'AAAUSDT'; identity.mkdir(parents=True)
    candles = tmp_path / 'original.csv'
    times = range(stamp - 2000 * 300000, stamp + 200 * 300000, 300000)
    pd.DataFrame(dict(ts=list(times), open=10, high=12, low=9, close=11, volume=2)).to_csv(candles, index=False)
    (identity / 'identity.json').write_text(json.dumps(dict(job=dict(symbol='AAAUSDT', group='crypto_5m',
                                                                     execution_minutes=5, paths=[str(candles)]))))
    return directory, identities, candles


def test_rank_uses_whole_funded_ledger_and_excludes_cash(saved):
    directory, _, _ = saved
    p = api.page('allin3', directory=directory, limit=2)
    assert p['ranking'] == [dict(symbol='AAAUSDT', pnl=25, trades=2), dict(symbol='BBBUSDT', pnl=4, trades=2)]
    assert p['meta']['pnl'] == 29
    assert p['focus']['id'] == 3
    assert [r['id'] for r in p['rows']] == [4, 3]
    assert p['total'] == 4 and p['has_more']
    p = api.page('allin3', directory=directory, sort='pnl', direction='asc', offset=1, limit=2)
    assert [r['pnl'] for r in p['rows']] == [-5, 12]
    p = api.page('allin3', directory=directory, symbol='AAAUSDT', sort='time', direction='asc')
    assert [r['id'] for r in p['rows']] == [1, 3] and not p['has_more']
    assert api.page('allin3', directory=directory, symbol='NONEUSDT')['rows'] == []


def test_daily_settlements_exclude_cash_keep_ids_and_total_across_pages(saved):
    directory, _, _ = saved
    first = api.day_page('allin3', '2024-01-02', directory=directory, limit=2)
    second = api.day_page('allin3', '2024-01-02', directory=directory, offset=2, limit=2)
    assert first['pnl'] == second['pnl'] == 29
    assert first['total'] == 4 and first['has_more'] and not second['has_more']
    assert [r['id'] for r in first['rows'] + second['rows']] == [1, 2, 3, 4]
    assert first['timezone'] == 'UTC' and first['source'] == 'Binance'
    assert api.day_page('allin3', '2024-01-03', directory=directory)['total'] == 0


def test_daily_settlements_use_exit_utc_boundary_not_entry_or_beijing_date(monkeypatch):
    start, end = api.day_bounds('2024-02-29')
    rows = [dict(id=i+1, entry_ms=start-86400000, exit_ms=t, pnl=i) for i,t in
            enumerate([start-1, start, end-1, end])]
    monkeypatch.setattr(api, 'ledger', lambda *a, **k: dict(rows=rows))
    p = api.day_page('allin3', '2024-02-29')
    assert [r['id'] for r in p['rows']] == [2, 3] and p['pnl'] == 3
    with pytest.raises(ValueError): api.day_bounds('2023-02-29')


def test_day_endpoint_validates_dates_and_does_not_disclose_paths(monkeypatch):
    app = FastAPI(); api.install(app); client = TestClient(app)
    for query in ['track=unknown&date=2024-01-02', 'track=spike&date=2023-02-29',
                  'track=spike&date=2024-1-2', 'track=spike&date=2024-01-02&limit=101']:
        assert client.get('/api/strategy-backtest/day?' + query).status_code == 422
    monkeypatch.setattr(api, 'day_page', lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('/private')))
    r = client.get('/api/strategy-backtest/day?track=spike&date=2024-01-02')
    assert r.status_code == 503 and '/private' not in r.text


def test_chart_uses_trade_identity_and_rejects_missing_or_conflicting_data(saved):
    directory, identities, path = saved
    value = api.candles('allin3', 3, '15m', directory=directory, identities=identities)
    assert value['symbol'] == 'AAAUSDT' and value['source'] == 'Binance'
    assert all(b['time'] % 900000 == 0 and b['volume'] == 6 for b in value['candles'])
    assert value['candles'][0]['time'] < value['backtestTrade']['entry_ms'] < value['candles'][-1]['time']
    assert value['weekly_ranges'][0]['high'] == 12
    with pytest.raises(KeyError):
        api.candles('allin3', 999, '5m', directory=directory, identities=identities)
    f = pd.read_csv(path)
    conflicting = f.iloc[[-100]].assign(high=50)
    pd.concat([f, conflicting]).to_csv(path, index=False)
    with pytest.raises(ValueError, match='conflicting'):
        api.candles('allin3', 3, '15m', directory=directory, identities=identities)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        api.candles('allin3', 3, '15m', directory=directory, identities=identities)


def test_aggregation_never_bridges_a_missing_native_bar():
    f = pd.DataFrame(dict(open=[1]*5, high=[2]*5, low=[1]*5, close=[2]*5, volume=[3]*5),
                     index=[0, 300000, 600000, 900000, 1500000])
    result = api.aggregate(f, 900000, 300000)
    assert list(result.index) == [0] and result.iloc[0].volume == 9


def test_endpoint_allowlists_and_bounded_concurrency(monkeypatch):
    app = FastAPI(); api.install(app); client = TestClient(app)
    for url in ['/api/strategy-backtest?track=../secret', '/api/strategy-backtest?track=allin3&limit=1000',
                '/api/strategy-backtest/candles?track=allin3&trade=1&period=1s']:
        assert client.get(url).status_code == 422
    monkeypatch.setattr(api, 'page', lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('/private')))
    r = client.get('/api/strategy-backtest?track=allin3')
    assert r.status_code == 503 and '/private' not in r.text
    api.SLOTS.acquire(); api.SLOTS.acquire()
    try:
        assert client.get('/api/strategy-backtest/candles?track=allin3&trade=1').status_code == 503
    finally:
        api.SLOTS.release(); api.SLOTS.release()


def test_fast_responses_preserve_saved_trade_and_chart_contract(saved, monkeypatch):
    directory, identities, _ = saved
    payloads = [
        ('page', '/api/strategy-backtest?track=allin3', api.page('allin3', directory=directory)),
        ('day_page', '/api/strategy-backtest/day?track=allin3&date=2024-01-02',
         api.day_page('allin3', '2024-01-02', directory=directory)),
        ('candles', '/api/strategy-backtest/candles?track=allin3&trade=3',
         api.candles('allin3', 3, '15m', directory=directory, identities=identities)),
    ]
    app = FastAPI(); api.install(app); client = TestClient(app)
    for name, url, value in payloads:
        monkeypatch.setattr(api, name, lambda *a, result=value, **k: result)
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/json'
        assert response.json() == value


def test_smaller_periods_use_real_minutes_not_split_five_minute_candles(saved,monkeypatch):
    from yoyo.data import binance_minute_window
    directory,identities,path=saved
    calls=[]
    def read(symbol,start,end):
        calls.append((symbol,start,end))
        return [dict(time=t,open=10,high=12,low=9,close=11,volume=.4) for t in range(start,end,60000)]
    monkeypatch.setattr(binance_minute_window,'read',read)
    for period,step,volume in [('1m',60000,.4),('2m',120000,.8),('3m',180000,1.2)]:
        v=api.candles('allin3',3,period,directory=directory,identities=identities)
        assert all(b['time']%step==0 and b['volume']==pytest.approx(volume) for b in v['candles'])
        assert v['weekly_ranges'][0]['high']==12
    assert len(calls)==3
    with pytest.raises(ValueError,match='cannot_split'):
        api.aggregate(pd.DataFrame(),60000,300000)


def test_live_wick_sorts_by_saved_entry_score():
    desc = api.page('live-wick6x3', sort='score', direction='desc', limit=100)
    scores = [r['entry_rules']['score'] for r in desc['rows']]
    assert desc['sort'] == 'score' and scores == sorted(scores, reverse=True) and scores[0] >= .5
    asc = api.page('live-wick6x3', sort='score', direction='asc', limit=5)
    assert asc['rows'][0]['entry_rules']['score'] == min(r['entry_rules']['score'] for r in api.ledger('live-wick6x3')['rows'])


def test_live_wick_october_trade_charts_read_the_extension_bars():
    rows = api.ledger('live-wick6x3')['rows']
    october = [r for r in rows if r['entry_ms'] >= 1790812800000]  # 2026-10-01 UTC
    assert len(october) == 17
    value = api.candles('live-wick6x3', october[0]['id'], '15m')
    times = [c['time'] for c in value['candles']]
    assert times[0] <= october[0]['entry_ms'] and times[-1] >= october[0]['exit_ms'] - 900000



def test_cloud_shared_replay_uses_frozen_okx_window_and_all_source_exits():
    app = FastAPI()
    api.install(app)
    client = TestClient(app)
    base = '/api/strategy-backtest'
    track = 'live-cloud-v17'
    page = client.get(base, params=dict(track=track, trade=171, limit=1)).json()
    assert page['total'] == 1252 and page['focus']['symbol'] == 'SANDUSDT'
    assert page['focus']['id'] != page['rows'][0]['id']
    chart = client.get(base+'/candles', params=dict(track=track, trade=171, period='12H')).json()
    legs = chart['backtestTrade']['fills']['exit_legs']
    assert chart['source'] == 'OKX' and len(chart['candles']) == chart['window']['availableBars']
    assert chart['candles'][0]['volume'] is None
    assert legs[0]['price'] == pytest.approx(1.51654) and legs[0]['share'] == pytest.approx(.5)
    assert legs[0]['end_ms'] == legs[0]['start_ms'] + 3600000
    assert legs[-1]['start_ms'] == legs[-1]['end_ms'] == page['focus']['exit_ms']
    assert sum(leg['share'] for leg in legs if leg['action']=='trim') == pytest.approx(.17181, abs=1e-5)
    assert sum(leg['share'] for leg in legs) == pytest.approx(1)
    day = client.get(base+'/day',params=dict(track=track,date='2021-12-17')).json()
    assert day['source']=='OKX' and 171 in [r['id'] for r in day['rows']]
    assert client.get(base+'/candles',params=dict(track=track,trade=171,period='1m')).status_code==422
