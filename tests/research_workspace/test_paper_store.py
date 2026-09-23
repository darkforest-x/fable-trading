"""Causal intent timing, durable run isolation and evidence immutability."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from yoyo.research_workspace.paper_store import PaperStore


@pytest.fixture
def book(tmp_path):
    return PaperStore(tmp_path)


def make(book, at=1_000_000, request='request-1234567890'):
    return book.create({'id':'spike-v128','name':'SPIKE','version':'12.8'}, {'symbols':['BTC-USDT-SWAP'],'timeframes':['15m'],'source_hash':'abc'}, request, at)


def event(key='event1', close=1_800_000):
    return dict(id=key, symbol='BTC-USDT-SWAP',timeframe='15m',side='long',bar_close_ms=close,
                detected_at_ms=close+500,initial_stop=90,source_payload={'source':'live'})


def test_first_observation_cannot_fill_at_missed_historical_open(book):
    r=make(book); e=event()
    assert book.decide(r['id'],e,1_900_000,1_800_000)
    row=book.decisions(r['id'])['decisions'][0]
    assert row['scheduled_entry_ms']==2_700_000 > row['observed_ms'] > row['signal_close_ms']
    assert not book.decide(r['id'],e,2_000_000,1_800_000)
    assert book.decisions(r['id'])['total']==1


def test_precise_boundary_still_schedules_future_open(book):
    r=make(book)
    assert book.decide(r['id'],event(),2_700_000,1_800_000)
    assert book.decisions(r['id'])['decisions'][0]['scheduled_entry_ms']==3_600_000


@pytest.mark.parametrize('e,seen,reason',[(event(close=900_000),1_900_000,'before_activation'),
    (event(),4_000_001,'signal_stale_at_observation'),(event(),1_800_100,'invalid_signal_clock')])
def test_bad_observations_are_receipted_but_not_filled(book,e,seen,reason):
    r=make(book)
    assert not book.decide(r['id'],e,seen,1_800_000)
    row=book.decisions(r['id'])['decisions'][0]
    assert row['status']=='skipped' and row['reason']==reason


def test_same_stream_serial_positions_but_independent_runs(book):
    a=make(book);b=make(book,request='other-request-12345')
    assert book.decide(a['id'],event(),1_900_000,1_800_000)
    assert not book.decide(a['id'],event('event2'),1_900_000,1_800_000)
    assert book.decide(b['id'],event(),1_900_000,1_800_000)
    book.update_trade(a['id'],'event1',dict(status='closed',net_r=-1.2),at=2_800_000)
    book.update_trade(a['id'],'event1',dict(status='closed',net_r=99),at=2_900_000)
    assert book.run(a['id'])['metrics']['net_r']==-1.2
    assert book.run(b['id'])['metrics']['net_r'] is None


def test_pause_skips_new_entries_and_resume_sets_new_cut(book):
    r=make(book);book.decide(r['id'],event(),1_900_000,1_800_000)
    book.action(r['id'],'pause',2_000_000)
    assert book.decisions(r['id'])['decisions'][0]['reason']=='paused_before_fill'
    assert not book.decide(r['id'],event('paused'),2_100_000,1_800_000)
    book.action(r['id'],'resume',3_000_000)
    assert not book.decide(r['id'],event('old',2_700_000),3_100_000,1_800_000)
    assert book.run(r['id'])['admit_after_ms']==3_000_000


def test_pause_after_scheduled_open_does_not_cancel_already_due_intent(book):
    r=make(book);book.decide(r['id'],event(),1_900_000,1_800_000)
    book.action(r['id'],'pause',2_800_000)
    assert book.decisions(r['id'])['decisions'][0]['status']=='pending'
    book.update_trade(r['id'],'event1',dict(status='open',entry_price=100),at=3_600_000)
    assert book.decisions(r['id'])['decisions'][0]['status']=='open'


def test_stop_censors_and_does_not_invent_exit_or_allow_resurrection(book):
    r=make(book);book.decide(r['id'],event(),1_900_000,1_800_000)
    book.update_trade(r['id'],'event1',dict(status='open',entry_price=100,unrealized_r=.4))
    book.action(r['id'],'stop',2_800_000)
    book.update_trade(r['id'],'event1',dict(status='closed',net_r=4))
    row=book.decisions(r['id'])['decisions'][0]
    assert row['status']=='stopped' and 'net_r' not in row['trade']
    with pytest.raises(ValueError): book.action(r['id'],'resume',3_000_000)
    assert book.action(r['id'],'stop')['status']=='stopped'


def test_input_prefix_cannot_be_rewritten_and_export_authenticates_it(book):
    r=make(book);bar=dict(t=0,o=100.,h=102.,l=99.,c=101.,v=3.)
    assert book.merge_bars(r['id'],'BTC','15m',[bar],1)==[bar]
    assert book.merge_bars(r['id'],'BTC','15m',[bar],2)==[bar]
    sha=book.export(r['id'])['input_sha256']
    with pytest.raises(ValueError):book.merge_bars(r['id'],'BTC','15m',[dict(bar,c=100.)],3)
    assert book.export(r['id'])['input_sha256']==sha
    assert book.export(r['id'])['input_streams'][0]['n']==1


def test_duplicate_submit_concurrency_is_idempotent(book):
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _:make(book)['id'],range(4)))
    assert len(set(ids))==1 and len(book.runs())==1
    assert PaperStore(book.runtime).run(ids[0])['spec']['source_hash']=='abc'


def test_fatal_version_failure_is_visible_and_cannot_resume(book):
    r=make(book);book.heartbeat(r['id'],'changed',fatal=True,at=9)
    assert book.run(r['id'])['status']=='error'
    assert not book.runs(active_only=True)
    with pytest.raises(ValueError):book.action(r['id'],'resume')
    assert book.action(r['id'],'stop')['status']=='stopped'
