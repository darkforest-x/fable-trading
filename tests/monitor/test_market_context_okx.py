"""OKX candle causality, source isolation and explicit estimated chart levels."""
from datetime import date, datetime, timezone
import pytest

from yoyo.monitor.market_sync import MarketSyncError
from yoyo.monitor.market_sync_okx import PublicOKX, Observer
from yoyo.monitor.market_context import STEP, DAY, window_ms, profile, build_context, market_bias, MarketContext


def candle(ts, confirm='1'):
    return [str(ts),'100','110','90','101','42','3','303',confirm]


def test_okx_paginates_backwards_returns_ascending_closed_base_volume(monkeypatch):
    client=PublicOKX();calls=[]
    all_rows=[candle(i*STEP) for i in range(1001)]
    all_rows[-1][-1]='0'
    def get(path,params):
        calls.append(params)
        return [r for r in reversed(all_rows) if int(r[0])<int(params['after'])][:300]
    monkeypatch.setattr(client,'get',get)
    rows=client.klines('BTCUSDT','15m',0,1001*STEP,limit=1000)
    assert [r[0] for r in rows]==list(range(0,1000*STEP,STEP))
    assert len(calls)==4
    assert rows[0]==[0,100.,110.,90.,101.,3.,STEP-1,303.]
    assert calls[0]['bar']=='15m' and calls[0]['instId']=='BTC-USDT-SWAP'
    assert all(int(b['after'])<int(a['after']) for a,b in zip(calls,calls[1:]))


def test_unclosed_and_invalid_okx_rows_are_never_accepted(monkeypatch):
    client=PublicOKX()
    monkeypatch.setattr(client,'get',lambda *a:[candle(STEP,'0'),candle(0)])
    assert len(client.klines('WUSDT','15m',0,STEP+100))==1
    monkeypatch.setattr(client,'get',lambda *a:[candle(0)[:8]])
    with pytest.raises(MarketSyncError,match='schema'):client.klines('BTCUSDT','15m',0,STEP)
    bad=candle(0);bad[3]='120'
    monkeypatch.setattr(client,'get',lambda *a:[bad])
    with pytest.raises(MarketSyncError,match='values'):client.klines('BTCUSDT','15m',0,STEP)


def test_catalog_excludes_non_crypto_and_accepts_single_letter(monkeypatch):
    client=PublicOKX()
    row=dict(instId='BTC-USDT-SWAP',instType='SWAP',ctType='linear',settleCcy='USDT',state='live',instCategory='1',ruleType='normal')
    monkeypatch.setattr(client,'get',lambda *a:[row,dict(row,instId='W-USDT-SWAP'),dict(row,instId='STOCK-USDT-SWAP',instCategory='3'),dict(row,instId='GOLD-USDT-SWAP',instCategory='4'),dict(row,instId='BAD-USDT-SWAP',state='suspend')])
    assert client.coins()==['BTCUSDT','WUSDT']
    with pytest.raises(MarketSyncError):client.klines('../BTC','1h',0,STEP)


def test_okx_storage_and_backfill_start_are_separate_and_survive_restart(tmp_path):
    sentinel=tmp_path/'market-sync-v2.sqlite3';sentinel.write_bytes(b'old source untouched')
    observer=Observer(tmp_path)
    assert observer.book.path.name=='market-sync-okx-v1.sqlite3'
    assert observer.snapshot()['source']['id']=='okx-usdt-swap'
    assert Observer(tmp_path).backfill_start==observer.backfill_start
    assert sentinel.read_bytes()==b'old source untouched'


def rows_between(start,end,price=100):
    return [dict(time=t,open=price,high=price+2,low=price-2,close=price,volume=10) for t in range(start,end,STEP)]


def test_ny_calendar_uses_dst_day_boundaries():
    a,b=window_ms(date(2026,3,8),'America/New_York')
    assert b-a==23*3600000
    a,b=window_ms(date(2026,11,1),'America/New_York')
    assert b-a==25*3600000
    a,b=window_ms(date(2026,10,12),'America/New_York')
    assert datetime.fromtimestamp(a/1000,timezone.utc).hour==4


def test_monday_freezes_only_after_complete_session_and_does_not_borrow_future():
    start,end=window_ms(date(2026,10,12),'America/New_York')
    rows=rows_between(start,end)
    future=dict(rows[-1],time=end,high=10000)
    partial=build_context(rows+[future],start+12*3600000)
    assert partial['monday']['developing'] and not partial['monday']['complete']
    assert partial['monday']['high']==102
    assert not any(l['kind']=='fib' for l in partial['levels'])
    fixed=build_context(rows+[future],end)
    assert fixed['monday']['complete'] and fixed['monday']['high']==102
    assert len([l for l in fixed['levels'] if l['kind']=='fib'])==6
    gap=build_context(rows[:8]+rows[9:],end)
    assert gap['monday']['high'] is None and not gap['monday']['complete']
    assert build_context(rows,end+7*DAY)['monday']['high'] is None


def test_profile_conserves_volume_and_returns_contiguous_value_area():
    rows=rows_between(0,3*STEP)
    rows[1].update(low=100,high=100,close=100,volume=100)
    value=profile(rows)
    assert value['volume']==pytest.approx(120)
    assert value['val']<=value['poc']<=value['vah']
    assert value['covered_volume']>=.7*120
    assert profile([]) is None
    assert profile([dict(rows[0],volume=0)]) is None


def test_npoc_requires_complete_untouched_history_and_rejects_missing_candles():
    start,end=window_ms(date(2026,10,12),'UTC');now=end+4*STEP
    prior=rows_between(start,end);later=rows_between(end,now,price=200)
    result=build_context(prior+later,now,'UTC')
    assert result['previous_profile']['naked']
    poc=result['previous_profile']['poc']
    touch=[dict(r) for r in later];touch[-1].update(low=poc,high=202)
    assert not build_context(prior+touch,now,'UTC')['previous_profile']['naked']
    gap=build_context(prior+later[1:],now,'UTC')['previous_profile']
    assert not gap['naked'] and not gap['coverage_complete']


def test_structure_flat_is_neutral_and_missing_bars_are_not_a_trend():
    rows=rows_between(0,100*STEP)
    assert build_context(rows,100*STEP,'UTC')['structure']['score']==0
    assert build_context(rows[:50]+rows[51:],100*STEP,'UTC')['structure']['score'] is None
    for i,r in enumerate(rows):r.update(close=100+i)
    assert build_context(rows,100*STEP,'UTC')['structure']['score']==1


@pytest.mark.parametrize('side', [-1,1])
def test_bias_is_bounded_and_extreme_only_when_all_three_agree(side):
    now=100*STEP
    context=dict(last_close_ms=now,structure={'score':side})
    row=dict(breadth_up=1 if side==1 else 0,breadth_down=0 if side==1 else 1,btc_z=side*100,eth_z=side*100)
    observation=dict(freshness={'60':{'ready':True,'stale':False}},snapshot={'latest':{'60':[row]}})
    assert market_bias(context,observation,now=now)['score']==100*side
    assert market_bias(context,observation,now=now+STEP+120001)['score'] is None
    observation['freshness']['60']['stale']=True
    assert market_bias(context,observation,now=now)['score'] is None
    observation['freshness']['60']['stale']=False;row['btc_z']=None
    assert market_bias(context,observation,now=now)['score'] is None


def test_context_snapshot_reads_never_fetch_network(tmp_path):
    context=MarketContext(tmp_path,client=object())
    assert context.snapshot()['stale']
    assert context.snapshot('UTC')['timezone']=='UTC'


def test_requested_contract_queues_without_network_and_has_its_own_identity(tmp_path):
    context=MarketContext(tmp_path,client=object())
    value=context.snapshot('UTC','ETH-USDT-SWAP')
    assert value['symbol']=='ETH-USDT-SWAP' and value['loading']
    assert value['candles']==[] and context._wake.is_set()
    assert context.snapshot()['symbol']=='BTC-USDT-SWAP'
    assert context.snapshot()['candles']==[]
    with pytest.raises(ValueError):context.snapshot('UTC','../ETH')
    with pytest.raises(ValueError):context.snapshot('other','ETH-USDT-SWAP')


def test_demand_cache_is_bounded_and_retains_homepage(tmp_path):
    context=MarketContext(tmp_path,client=object())
    for i in range(10):context.snapshot('UTC',f'COIN{i}-USDT-SWAP')
    assert len(context._wanted)==context.MAX_EXTRA
    assert 'COIN0-USDT-SWAP' not in context._wanted
    assert context.DEFAULT in context._snapshots
    context.snapshot('UTC','COIN4-USDT-SWAP')
    context.snapshot('UTC','NEW-USDT-SWAP')
    assert 'COIN4-USDT-SWAP' in context._wanted
    assert 'COIN5-USDT-SWAP' not in context._wanted


def test_worker_publishes_only_the_requested_symbol_and_retains_it_on_restart(tmp_path):
    import time
    now=int(time.time()*1000)//STEP*STEP
    class Client:
        calls=[]
        def server_ms(self):return now
        def klines(self,symbol,bar,start,end):
            self.calls.append(symbol)
            return [[t,2000,2002,1998,2001,10] for t in range(start,end,STEP)]
    client=Client();context=MarketContext(tmp_path,client)
    context.snapshot('UTC','ETH-USDT-SWAP');context.cycle('ETH-USDT-SWAP')
    value=context.snapshot('UTC','ETH-USDT-SWAP')
    assert value['symbol']=='ETH-USDT-SWAP' and not value['stale'] and not value['loading']
    assert value['candles'][-1]['close']==2001
    assert context.snapshot()['candles']==[]
    assert client.calls==['ETHUSDT']
    restarted=MarketContext(tmp_path,client)
    restarted.snapshot('UTC','ETH-USDT-SWAP');restarted.cycle('ETH-USDT-SWAP')
    assert restarted.snapshot('UTC','ETH-USDT-SWAP')['candles']==value['candles']
    assert client.calls==['ETHUSDT']
