"""Quick tickets anchor both sides to the original excursion, including multi-minute wicks."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.monday_fib import FibUnavailable
from yoyo.discipline_app.wick_stop import MINUTE, PAGE_MINUTES, WickStops
from test_mobile_flow import margin_book
from test_discipline_service import ticket


SYMBOL = 'BTC-USDT-SWAP'
START = 1794651780000  # DYDX first touch at 18:23 Beijing, Nov 14.
WEEK = '2026-11-09'


def candle(t, low, high, close, confirmed='1'):
    return [str(v) for v in (t, close, high, low, close, 10, 10, 100, confirmed)]


class History:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def read(self, symbol, period, before):
        self.calls.append((symbol, period, before))
        return dict(data=sorted((r for r in self.rows if before is None or int(r[0]) < before * 1000),
                                key=lambda r: int(r[0]), reverse=True)[:300])


def read(rows, side='long', level=95, *, search_start=START, span=1, now=None, stops=None):
    anchor = WEEK, level, search_start, search_start + span * MINUTE
    return (stops or WickStops()).read(rows, SYMBOL, side, anchor, now or START + 10 * MINUTE)


@pytest.mark.parametrize('side,level,prices,stop', [
    ('long', 95, [(94, 100, 94), (90, 96, 93), (92, 98, 96), (80, 100, 98)], '90'),
    ('short', 105, [(100, 106, 106), (103, 110, 107), (100, 109, 104), (100, 120, 101)], '110'),
])
def test_first_excursion_extreme_includes_recovery_bar_and_ignores_a_deeper_retest(side, level, prices, stop):
    rows = [candle(START+i*MINUTE, *price) for i, price in enumerate(prices)]
    for subset in (rows[:3], rows):
        result = read(History(subset), side, level)
        assert result['stop_loss'] == stop and result['timeframe'] == '1m'
        assert result['first_touch_ms'] == START and result['candle_ms'] == START+MINUTE
        assert result['recovered_ms'] == START+3*MINUTE and result['week'] == WEEK


def test_dydx_first_touch_body_crossed_and_following_minute_recovered():
    history = History([candle(START, '.08206', '.08434', '.08329'),
                       candle(START+MINUTE, '.08316', '.08388', '.08357'),
                       candle(START+36*MINUTE, '.08332', '.08465', '.08433')])
    result = read(history, level='.08332479999999999', now=START+120*MINUTE)
    assert result['stop_loss'] == '0.08206' and result['candle_ms'] == START
    assert result['recovered_ms'] == START+2*MINUTE


def test_historical_hour_refined_chronologically_and_requires_all_earlier_minutes():
    start = START//3_600_000*3_600_000
    rows = [candle(t, 96, 100, 98) for t in range(start, START, MINUTE)]
    rows += [candle(START, 94, 99, 94), candle(START+MINUTE, 92, 98, 95)]
    found = read(History(rows), search_start=start, span=60)
    assert found['first_touch_ms'] == START and found['stop_loss'] == '92'
    with pytest.raises(FibUnavailable, match='尚未到齐'):
        read(History(rows[1:]), search_start=start, span=60)


def test_missing_anchor_candle_or_early_gap_cannot_be_replaced_by_a_later_wick():
    retest = candle(START+2*MINUTE, 90, 99, 97)
    with pytest.raises(FibUnavailable, match='不符'):
        read(History([candle(START, 96, 100, 98), retest]))
    with pytest.raises(FibUnavailable, match='到齐'):
        read(History([candle(START, 94, 99, 94), retest]))
    with pytest.raises(FibUnavailable, match='到齐'):
        read(History([candle(START, 94, 99, 94), candle(START+MINUTE, 90, 99, 97, '0')]))


def test_open_excursion_can_resume_only_after_the_recovery_minute_closes():
    stops = WickStops()
    history = History([candle(START, 94, 99, 94), candle(START+MINUTE, 90, 99, 97, '0')])
    with pytest.raises(FibUnavailable, match='尚未确认收回'):
        read(history, stops=stops, now=START+MINUTE+15000)
    history.rows[1][-1] = '1'
    assert read(history, stops=stops, now=START+2*MINUTE)['stop_loss'] == '90'


def test_long_excursion_crosses_pages_and_resolved_wick_stays_locked():
    rows = [candle(START+i*MINUTE, 92, 95, 94) for i in range(PAGE_MINUTES+2)]
    rows[-1] = candle(START+(len(rows)-1)*MINUTE, 89, 97, 96)
    history = History(rows)
    now = START+len(rows)*MINUTE
    stops = WickStops()
    result = read(history, now=now, stops=stops)
    assert result['stop_loss'] == '89' and result['recovered_ms'] == now
    assert history.calls == [(SYMBOL, '1m', (START+PAGE_MINUTES*MINUTE)//1000), (SYMBOL, '1m', None)]
    assert read(History([]), now=now+MINUTE, stops=stops) == result


def test_authenticated_stop_from_all_chart_periods_precedes_risk_sizing(tmp_path, monkeypatch):
    now = START+5*MINUTE
    monkeypatch.setattr('yoyo.discipline_app.api.time.time', lambda: now/1000)
    book = margin_book(tmp_path)
    app = create_app(book, 'x'*32, market_enabled=False)
    class Feed:
        def wick_anchor(self, symbol, side, now_ms):
            assert symbol == SYMBOL and side == 'long' and now_ms == now
            return WEEK, 95, START, START+MINUTE

    app.state.monday_fib = Feed()
    history = app.state.candle_history = History([candle(START, 94, 99, 98)])
    url = '/v1/market/monday-wick'
    headers = {'Authorization':'Bearer '+'x'*32}
    with book.read_transaction() as (_, state):
        before = state
    with TestClient(app) as client:
        assert client.get(url, params={'symbol': SYMBOL}).status_code == 401
        for period in ('1m', '5m', '15m', '1H', '4H', '12H', '1D'):
            result = client.get(url, headers=headers, params=dict(symbol=SYMBOL, period=period))
            assert result.status_code == 200 and result.json()['stop_loss'] == '94'
        assert client.get(url, headers=headers, params=dict(symbol=SYMBOL, period='bogus')).status_code == 422
        assert client.post(url, headers=headers, json={}).status_code == 405
    assert history.calls == [(SYMBOL, '1m', None)]
    with book.read_transaction() as (_, state):
        assert state == before
    plans = [book.preview(ticket(stop=s, fraction='.1', leverage=100, sizing_mode='margin'))['plan'] for s in ('94','90')]
    assert [p['stop'] for p in plans] == ['94','90']
    assert Decimal(plans[0]['quantity']) > Decimal(plans[1]['quantity'])
    assert all(Decimal(p['risk']) <= 50 and Decimal(p['notional']) <= 10000 for p in plans)


def test_month_and_week_routes_never_share_a_first_touch(tmp_path, monkeypatch):
    now_ms = START + 10 * MINUTE
    monkeypatch.setattr('yoyo.discipline_app.api.time.time', lambda: now_ms/1000)
    app = create_app(margin_book(tmp_path), 'x'*32, market_enabled=False)
    class Weekly:
        def wick_anchor(self, symbol, side, now):
            assert (symbol, side, now) == (SYMBOL, 'long', now_ms)
            return WEEK, 95, START, START+MINUTE
    def monthly(monitor, symbol, side, now):
        assert monitor is app.state.monitor and (symbol, side, now) == (SYMBOL, 'long', now_ms)
        return '2026-11', 90, START+MINUTE, START+2*MINUTE
    monkeypatch.setattr('yoyo.discipline_app.month_wick.month_wick_anchor', monthly)
    app.state.monday_fib = Weekly()
    app.state.candle_history = History([candle(START, 94, 99, 98), candle(START+MINUTE, 88, 99, 93)])
    with TestClient(app) as client:
        for source, expected in [('week', '94'), ('month', '88'), ('week', '94')]:
            result = client.get('/v1/market/monday-wick', headers={'Authorization':'Bearer '+'x'*32},
                                params=dict(symbol=SYMBOL, source=source, side='long', period='12H'))
            assert result.status_code == 200 and result.json()['stop_loss'] == expected
