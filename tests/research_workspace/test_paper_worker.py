"""Forward worker tests: no historical entry backfills or hidden source swaps."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from yoyo.research_workspace.paper_store import PaperStore
from yoyo.research_workspace import paper_worker as worker


class Source:
    def __init__(self, events): self.items=events
    def events(self, *args): return self.items
    def checkpoint(self,*args):
        return {'candles':[{'t':900000,'o':100.,'h':102.,'l':99.,'c':101.,'v':3.}], 'tick':.1, 'updated_ms':1800500}


@pytest.fixture
def setup(tmp_path,monkeypatch):
    store=PaperStore(tmp_path)
    run=store.create({'id':'spike-v128','name':'SPIKE','version':'12.8'},
                     {'source_hash':'abc','symbols':['BTC-USDT-SWAP'],'timeframes':['15m']},'test-request-12345',at=1000000)
    e=dict(id='signal1',symbol='BTC-USDT-SWAP',timeframe='15m',side='long',bar_close_ms=1800000,detected_at_ms=1800500,initial_stop=90)
    monkeypatch.setattr(worker,'get_plugin',lambda _:SimpleNamespace(evaluate_trade=lambda *args:worker._test_evaluate(*args)))
    monkeypatch.setattr(worker,'_test_evaluate',lambda *args:{'status':'pending'},raising=False)
    return store,run,Source([e])


def test_source_change_stops_before_reading_or_mutating_decisions(setup):
    store,r,src=setup
    worker.tick(store,Path('.'),src,r,at=1900000,manifest={'hash':'changed'})
    assert store.run(r['id'])['status']=='error'
    assert store.decisions(r['id'])['total']==0


def test_new_signal_only_creates_intent_first_then_evaluates_existing(setup,monkeypatch):
    store,r,src=setup
    calls=[]
    monkeypatch.setattr(worker,'_test_evaluate',lambda *args:calls.append(args) or {'status':'pending'})
    worker.tick(store,Path('.'),src,r,at=1900000,manifest={'hash':'abc'})
    assert not calls
    worker.tick(store,Path('.'),src,store.run(r['id']),at=2000000,manifest={'hash':'abc'})
    assert len(calls)==1
    assert store.decisions(r['id'])['total']==1
    assert calls[0][4]['scheduled_entry_ms']==2700000


def test_live_observation_is_after_slow_input_read(setup,monkeypatch):
    store,r,src=setup
    times=iter([1900000,2700001])
    monkeypatch.setattr(worker,'clock_ms',lambda:next(times))
    worker.tick(store,Path('.'),src,r,manifest={'hash':'abc'})
    d=store.decisions(r['id'])['decisions'][0]
    assert d['observed_ms']==2700001
    assert d['scheduled_entry_ms']==3600000


def test_paused_run_tracks_position_but_never_creates_new_intents(setup,monkeypatch):
    store,r,src=setup
    store.decide(r['id'],src.items[0],1900000,1800000)
    store.update_trade(r['id'],'signal1',{'status':'open','entry_price':100})
    store.action(r['id'],'pause',2800000)
    src.items.append(dict(src.items[0],id='signal2'))
    monkeypatch.setattr(worker,'_test_evaluate',lambda *args:{'status':'closed','net_r':-.2})
    worker.tick(store,Path('.'),src,store.run(r['id']),at=3600000,manifest={'hash':'abc'})
    assert store.run(r['id'])['metrics']['closed']==1
    assert store.decisions(r['id'])['total']==1


def test_event_ahead_of_checkpoint_waits_without_recording_an_intent(setup):
    store, r, src = setup
    src.items[0].update(bar_close_ms=2700000, detected_at_ms=2700500)
    worker.tick(store, Path('.'), src, r, at=2800000, manifest={'hash':'abc'})
    assert store.decisions(r['id'])['total'] == 0
    assert '等待信号对应' in store.run(r['id'])['error']


@pytest.mark.parametrize('full_market', [False, True])
def test_worker_preserves_universe_and_reads_only_signal_markets(setup, full_market):
    store, original, src = setup
    symbols = None if full_market else ['SOL-USDT-SWAP']
    spec = dict(original['spec'], symbols=symbols)
    if full_market:
        spec['symbol_scope'] = 'okx_all_usdt'
    # The custom case deliberately has no scope field, matching historical runs.
    run = store.create({'id':'spike-v128','name':'SPIKE','version':'12.8'},
                       spec, 'universe-request-12345', at=1000000)
    src.items = [dict(src.items[0], symbol='SOL-USDT-SWAP')]
    reads = []
    def events(plugin, cursor, selected, timeframes):
        assert selected == symbols
        assert cursor == 1000000 and timeframes == ['15m']
        return src.items
    original_checkpoint = src.checkpoint
    def checkpoint(symbol, timeframe, at):
        reads.append(symbol)
        return original_checkpoint(symbol, timeframe, at)
    src.events, src.checkpoint = events, checkpoint
    worker.tick(store, Path('.'), src, run, at=1900000, manifest={'hash':'abc'})
    decision = store.decisions(run['id'])['decisions'][0]
    assert decision['symbol'] == 'SOL-USDT-SWAP'
    assert decision['status'] == 'pending'
    assert decision['scheduled_entry_ms'] == 2700000
    assert reads == ['SOL-USDT-SWAP']
