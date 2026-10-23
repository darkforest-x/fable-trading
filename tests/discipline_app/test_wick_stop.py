"""Synthetic chart stops precede sizing; no live account or exchange writes."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.monday_fib import FibUnavailable
from yoyo.discipline_app.wick_stop import WickStops, STEPS
from test_mobile_flow import margin_book
from test_discipline_service import ticket

NOW = int(datetime(2026, 10, 23, 12, tzinfo=ZoneInfo('UTC')).timestamp()*1000)
MONDAY = int(datetime(2026, 10, 19, tzinfo=ZoneInfo('America/New_York')).timestamp()*1000)
SYMBOL = 'BTC-USDT-SWAP'


class Feed:
    def read(self):
        return dict(now_ms=NOW, week='2026-10-19', week_start_ms=MONDAY,
                    monday_end_ms=MONDAY+86400000, next_week_ms=MONDAY+7*86400000,
                    monday_complete=True, items=[dict(symbol=SYMBOL, lower=95, upper=105)])


def candle(t, o=100, h=102, l=98, c=101, closed='1'):
    return [str(v) for v in (t,o,h,l,c,10,10,100,closed)]


class History:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def read(self, symbol, period, before=None):
        self.calls.append((symbol,period,before))
        return dict(data=self.pages[len(self.calls)-1])


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('yoyo.discipline_app.wick_stop.time.time', lambda: NOW/1000)


@pytest.mark.parametrize('side,low,high,stop', [('long',94,102,'94'),('short',98,106,'106')])
def test_latest_closed_wick_precedes_body_crossing_and_open_bar(side,low,high,stop):
    step=STEPS['15m']; head=NOW-step
    history=History([[candle(NOW,h=110,l=90,closed='0'),
                      candle(head,o=94,h=110,l=90,c=106),
                      candle(head-step,h=high,l=low), candle(head-2*step,h=111,l=89)]])
    found=WickStops().read(Feed(),history,SYMBOL,'15m',side)
    assert found['stop_loss']==stop and found['candle_ms']==head-step
    assert found['timeframe']=='15m' and found['week']=='2026-10-19'


def test_pages_back_to_current_week_and_never_uses_gap_or_old_week():
    step=STEPS['5m']; head=NOW//step*step-step
    history=History([[candle(head),candle(head-step)], [candle(head-2*step,l=94)]])
    assert WickStops().read(Feed(),history,SYMBOL,'5m','long')['stop_loss']=='94'
    assert history.calls[1] == (SYMBOL,'5m',(head-step)//1000)
    with pytest.raises(FibUnavailable,match='尚未到齐'):
        WickStops().read(Feed(),History([[candle(head-step,l=94)]]),SYMBOL,'5m','long')
    with pytest.raises(FibUnavailable,match='尚未到齐'):
        WickStops().read(Feed(),History([[candle(head),candle(head-2*step,l=94)]]),SYMBOL,'5m','long')
    step=STEPS['1H']; head=NOW-step
    rows=[candle(t) for t in range(head,MONDAY+86400000-step,-step)]
    rows.append(candle(MONDAY-86400000,l=94))
    with pytest.raises(FibUnavailable,match='暂无影线'):
        WickStops().read(Feed(),History([rows]),SYMBOL,'1H','long')


def test_daily_stop_uses_app_utc8_boundary_and_tiny_price_stays_decimal():
    step=STEPS['1D']; boundary=(NOW+8*3600000)//step*step-8*3600000
    history=History([[candle(boundary,l=94,closed='0'),candle(boundary-step,l=93)]])
    found=WickStops().read(Feed(),history,SYMBOL,'1D','long')
    assert found['candle_ms']==boundary-step and found['stop_loss']=='93'
    from yoyo.data.monday_wick import wick_stop
    small=wick_stop([dict(time=NOW-60000,open='0.00000012',high='0.00000013',low='0.00000009',close='0.00000011')],
                    period='1m',side='long',level='0.00000010',window=dict(end=MONDAY+86400000,expires=MONDAY+7*86400000),now=NOW)
    assert small['stop_loss']=='0.00000009'


def test_missing_or_stale_week_never_falls_back_to_reverse_r():
    class Stale(Feed):
        def read(self):return dict(super().read(),now_ms=NOW-61000)
    class Missing(Feed):
        def read(self):return dict(super().read(),items=[])
    for feed in (Stale(),Missing()):
        history=History([])
        with pytest.raises(FibUnavailable):WickStops().read(feed,history,SYMBOL,'15m','long')
        assert not history.calls


def test_authenticated_default_has_no_account_writes_and_size_follows_fixed_stop(tmp_path):
    book=margin_book(tmp_path);app=create_app(book,'x'*32,market_enabled=False)
    app.state.monday_fib=Feed();app.state.candle_history=History([[candle(NOW-STEPS['15m'],l=94)]])
    query='/v1/market/monday-wick?symbol=BTC-USDT-SWAP&period=15m&side=long'
    with book.read_transaction() as (_,state):before=state
    with TestClient(app) as client:
        assert client.get(query).status_code==401
        headers={'Authorization':'Bearer '+'x'*32}
        result=client.get(query,headers=headers);assert result.status_code==200
        stop=result.json()['stop_loss'];assert stop=='94'
        assert client.post(query,headers=headers,json={}).status_code==405
        assert client.get(query.replace('15m','bogus'),headers=headers).status_code==422
    with book.read_transaction() as (_,state):assert state==before
    # The chosen stop remains exact. Wider distance reduces size, not stop quality.
    plans=[book.preview(ticket(stop=s,fraction='.1',leverage=100,sizing_mode='margin'))['plan'] for s in (stop,'90')]
    assert plans[0]['stop']=='94' and plans[1]['stop']=='90'
    assert Decimal(plans[0]['quantity'])>Decimal(plans[1]['quantity'])
    assert all(Decimal(p['risk'])<=50 and Decimal(p['notional'])<=10000 for p in plans)
