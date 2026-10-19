"""Market display is read-only and history is computed from complete fills."""
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from yoyo.research_workspace import paper_desk as book, paper_desk_api as api


def ticker(**changes):
    return dict(instId='ETH-USDT-SWAP',bidPx='99',askPx='101',last='100.5',open24h='100',
                high24h='105',low24h='95',volCcy24h='2000',ts=str(book.clock_ms()),**changes)


def test_ticker_last_is_not_bid_ask_midpoint_and_uses_rolling_24h(monkeypatch):
    quotes=api.PublicQuotes();monkeypatch.setattr(quotes,'fetch',lambda:[ticker()])
    q=quotes.get(['ETH-USDT-SWAP'],include_risk=False)['ETH-USDT-SWAP']
    assert q['last']=='100.5' and q['change_24h_percent']=='0.500'
    assert q['high_24h']=='105' and q['low_24h']=='95'
    assert D(q['volume_usdt_24h'])==201000


@pytest.mark.parametrize('value',['NaN','Infinity','0','-1','bad'])
def test_optional_market_fields_cannot_break_execution_quotes(monkeypatch,value):
    row=ticker();row.update(last=value,high24h=value,open24h=value)
    quotes=api.PublicQuotes();monkeypatch.setattr(quotes,'fetch',lambda:[row])
    q=quotes.get(['ETH-USDT-SWAP'])['ETH-USDT-SWAP']
    assert q['bid']=='99' and q['ask']=='101' and 'last' not in q


@pytest.mark.parametrize('symbol', ['ETH-USDT-SWAP', 'W-USDT-SWAP'])
def test_market_polling_never_writes_execution_quotes(tmp_path,monkeypatch,symbol):
    app=FastAPI();router=APIRouter(prefix='/api/research');api.install(router,app,SimpleNamespace(runtime=tmp_path));app.include_router(router)
    quotes=api.PublicQuotes();monkeypatch.setattr(quotes,'fetch',lambda:[dict(ticker(),instId=symbol)]);app.state.paper_desk_quotes=quotes
    with TestClient(app) as client:
        for _ in range(3):
            response=client.get('/api/research/paper-desk/market',params={'symbol':symbol});assert response.status_code==200
            assert response.json()['last']=='100.5'
        assert client.get('/api/research/paper-desk/market?symbol=ETHUSDT').status_code==422
        assert client.get('/api/research/paper-desk/market?symbol=DOGE-USDT-SWAP').status_code==503
    with app.state.paper_desk.connect() as db:
        for table in ['accounts','quotes','fills','receipts']:
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_stale_market_or_missing_last_does_not_fabricate_a_display_price(tmp_path,monkeypatch):
    app=FastAPI();router=APIRouter(prefix='/api/research');api.install(router,app,SimpleNamespace(runtime=tmp_path));app.include_router(router)
    quotes=api.PublicQuotes();row=ticker();row['ts']=str(book.clock_ms()-16000)
    monkeypatch.setattr(quotes,'fetch',lambda:[row]);app.state.paper_desk_quotes=quotes
    with TestClient(app) as client:
        assert client.get('/api/research/paper-desk/market?symbol=ETH-USDT-SWAP').status_code==503
        quotes.cached_at=None;row['ts']=str(book.clock_ms());row.pop('last')
        assert client.get('/api/research/paper-desk/market?symbol=ETH-USDT-SWAP').status_code==503


def test_closed_history_averages_all_entry_exit_fills_after_additions_and_partial_close(tmp_path):
    desk=book.PaperDesk(tmp_path);a=desk.create_account('History','1000','create-history-test')['id']
    def q(px):return desk.save_quote(dict(symbol='BTC-USDT-SWAP',bid=px,ask=px,source_ms=book.clock_ms(),received_ms=book.clock_ms()))
    first=q('100');p=desk.open(a,'long','2.00',first['id'],'','open-history-test',{},100)['position']
    desk.close(p['id'],'1.00',q('110')['id'],'partial-history-test')
    second=q('120');desk.open(a,'long','3.00',second['id'],'','add-history-test',{'BTC-USDT-SWAP':second},100)
    desk.close(p['id'],'4.00',q('130')['id'],'final-history-test')
    summary=desk.snapshot({})['positions'][0]
    assert D(summary['remaining'])==0
    assert D(summary['entry_price'])==115
    assert D(summary['average_open_price'])==112  # All entries: (200+360)/5.
    assert D(summary['average_close_price'])==126  # All exits: (110+520)/5.
    assert D(summary['opened_notional'])==560 and D(summary['closed_notional'])==630
    assert D(summary['cumulative_entry_margin'])==D('5.6') and len(summary['related_fills'])==4


def test_bulk_catalog_contains_only_fresh_display_values_without_risk_or_receipts(tmp_path, monkeypatch):
    class Risk:
        def get(self, symbols):
            raise AssertionError('display catalog must not load risk tiers')
    now=book.clock_ms()
    def row(symbol, change, stamp):
        return dict(ticker(),instId=symbol,last=str(100+change),ts=str(stamp))
    quotes=api.PublicQuotes(risk=Risk())
    monkeypatch.setattr(quotes,'fetch',lambda:[row('BTC-USDT-SWAP',5,now),row('ETH-USDT-SWAP',-3,now),row('SOL-USDT-SWAP',9,now-16000)])
    symbols=api.PublicSymbols(quotes)
    monkeypatch.setattr(symbols,'fetch',lambda:[dict(instId=s+'-USDT-SWAP',instType='SWAP',state='live',settleCcy='USDT',ctType='linear') for s in ['BTC','ETH','SOL']])
    app=FastAPI();router=APIRouter(prefix='/api/research');api.install(router,app,SimpleNamespace(runtime=tmp_path));app.include_router(router)
    app.state.paper_desk_symbols=symbols
    with TestClient(app) as client:
        response=client.get('/api/research/paper-desk/symbols')
        assert response.status_code==200
        rows={r['base']:r for r in response.json()['symbols']}
        assert D(rows['BTC']['change_24h_percent'])==5
        assert D(rows['ETH']['change_24h_percent'])==-3
        assert rows['BTC']['expires_ms']==now+15000
        assert rows['SOL']['last'] is None and rows['SOL']['change_24h_percent'] is None
        assert rows['SOL']['expires_ms'] is None
        assert 'bid' not in rows['BTC'] and 'ask' not in rows['BTC']
    with app.state.paper_desk.connect() as db:
        for table in ['accounts','quotes','fills','receipts']:
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
