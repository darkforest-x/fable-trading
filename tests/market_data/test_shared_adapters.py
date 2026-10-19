"""Adapters retain units and caller contracts while sharing one public series."""
from yoyo.monitor.okx import OKX
from yoyo.monitor.market_sync_okx import PublicOKX
from yoyo.copier.paper.market import PublicMarket


def test_monitor_observer_and_chart_share_rows(tmp_path):
    clock=9_000_001
    monitor=OKX(runtime=tmp_path)
    monitor.clock=lambda:clock
    calls=[]
    def fetch(path,params):
        calls.append(params)
        end=(int(params['after'])-1)//900_000*900_000
        return [[str(t),'100','110','90','101','42','3','303','1']
                for t in range(end,end-int(params['limit'])*900_000,-900_000)]
    monitor._get=fetch
    bars,gaps=monitor.candles('BTC-USDT-SWAP','15m',limit=5)
    assert gaps==0 and bars[-1]['v']==42
    observer=PublicOKX(runtime=tmp_path)
    observer.shared.clock=lambda:clock
    observer.shared._get=lambda *_: (_ for _ in ()).throw(AssertionError('duplicate HTTP'))
    rows=observer.klines('BTCUSDT','15m',bars[-5]['t'],9_000_000-1,limit=5)
    assert len(rows)==5 and rows[-1][5]==3 and rows[-1][7]==303
    chart=OKX(runtime=tmp_path)
    chart.clock=lambda:clock
    chart._get=observer.shared._get
    assert len(chart.get('/api/v5/market/candles',{'instId':'BTC-USDT-SWAP','bar':'15m','limit':'5'}))==5
    assert len(calls)==1


def test_paper_candles_use_shared_reader_but_quotes_keep_original_transport(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        def get(self,path,params):
            calls.append((path,params))
            return [['60000','1','2','.5','1','10','20','30','1']]
    monkeypatch.setattr('yoyo.monitor.okx.OKX',Client)
    market=PublicMarket()
    assert market.candles_1m('BTC-USDT-SWAP','okx',now_ms=120_000)==[(60000,1.,2.,.5,1.)]
    assert len(calls)==1
    assert market.candles_1m('BTC-USDT-SWAP','gate')==[]


def test_status_endpoint_is_read_only_and_uses_selected_runtime(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app
    app=create_app(tmp_path,start_monitor=False)
    client=TestClient(app)
    data=client.get('/api/candles/status').json()
    assert data['summary']==dict(series=0,requests=0,hits=0,syncing=0,errors=0)
    assert (tmp_path/'shared-candles/cache.sqlite3').exists()
    assert client.get('/api/candles/status?limit=201').status_code==422
