"""Causal state transitions, durable restart and notification cutover controls."""
import threading
from copy import deepcopy

import pytest

from model_fixture import model_event
from yoyo.monitor import (BARK_TIMEFRAMES, MODEL_KIND, MODEL_PROTOCOL, MONITORED_TIMEFRAMES,
                          TIMEFRAMES, FRESH_MS, MODEL_SHA256)
from yoyo.monitor.model_gate import ModelGate, pending_proof
from yoyo.monitor.store import Store
from yoyo.monitor.policy import is_model_signal


class Detector:
    def __init__(self, proposals=None):
        self.proposals = proposals or {}
        self.calls = []
        self.fail_once = False
    def predict(self, candles, symbol, timeframe, endpoint_ms):
        assert candles[-1]['t'] == endpoint_ms  # Caller cannot hand it a future bar.
        self.calls.append(endpoint_ms)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError('inference failed')
        return self.proposals.get(endpoint_ms, [])
    def status(self):
        return {'ready': True}


def scenario(tmp_path, tf='1H', wait=2, *, register=True):
    step = TIMEFRAMES[tf]
    p, end = 150 * step, (150+wait)*step
    raw = model_event(close=end+step, timeframe=tf, side='long', wait=wait)['indicator']
    # The production gate now accepts only the frozen V1 closed raw-long
    # contract.  Legacy IMACD fixture fields remain outside this focused suite.
    raw.update(source='live', confirmation='raw', direction='long', side='long', venue='okx',
               timeframe_min=TIMEFRAMES[tf] // 60_000, is_closed=True, risk=2., initial_stop=98.,
               source_sha256='a' * 64, entry_reference='next_open', executable_entry_time=None)
    core_end = min(p, end-2*step)
    proposal = dict(model_sha256=MODEL_SHA256, confidence=.65, detection_id='synthetic-1',
                    input_pixel_sha256='0'*64, side='long', structural_pass=True,
                    window_len=18, window_start_ms=end-17*step, window_end_ms=end,
                    core_start_ms=core_end-3*step, core_end_ms=core_end,
                    core_length_bars=4, post_bars=(end-core_end)//step)
    candles = [dict(t=t, o=99.4, h=101., l=99., c=99.5 if t==p else 100.5, v=10.,
                    md=.2, sb=.02,
                    sma20=100.,ema20=100.,sma60=100.,ema60=100.,sma120=100.,ema120=100.)
               for t in range(p-30*step, end+step, step)]
    store = Store(tmp_path/'model.sqlite')
    store.activate_notification_policy(0, protocol=MODEL_PROTOCOL)
    store.activate_bark_policy(0, protocol=MODEL_PROTOCOL)
    store.activate_timeframe_policy(tf, 0, protocol=MODEL_PROTOCOL)
    detector = Detector({end: [proposal]})
    clock = [end+step+1000]
    # Synthetic legacy-enabled mode preserves existing dual-channel coverage.
    gate = ModelGate(store, lambda: clock[0], threading.Event(), detector, telegram_enabled=True)
    if register:
        assert gate.register(raw)
    return gate, store, detector, raw, candles, proposal, clock


@pytest.mark.parametrize('tf', MONITORED_TIMEFRAMES)
@pytest.mark.parametrize('wait', [0,2,9])
def test_exact_frozen_wait_all_v1_timeframes(tmp_path, tf, wait):
    gate, store, detector, raw, bars, prop, clock = scenario(tmp_path, tf, wait)
    gate.process(raw['symbol'],tf,bars)
    events = store.list_events(kind=MODEL_KIND, protocol=MODEL_PROTOCOL)
    assert len(events)==1 and is_model_signal(events[0])
    assert events[0]['model']['wait_bars']==wait
    assert events[0]['indicator']['bar_close_ms']==raw['bar_close_ms']
    assert events[0]['bar_close_ms']==clock[0]-1000
    assert events[0]['notification_status']=='pending'
    assert events[0]['bark_notification_status'] == ('pending' if tf in BARK_TIMEFRAMES else 'history')
    assert store.bark_status()['pending'] == int(tf in BARK_TIMEFRAMES)
    assert store.list_candidates()[0]['model']['status']=='confirmed'
    assert len(detector.calls)==wait+1
    gate.process(raw['symbol'],tf,bars)
    assert store.event_count(MODEL_KIND, MODEL_PROTOCOL)==1
    assert len(detector.calls)==wait+1


@pytest.mark.parametrize('tf', MONITORED_TIMEFRAMES)
def test_default_gate_records_confirmation_and_only_queues_permitted_bark(tmp_path, monkeypatch, tf):
    from yoyo.monitor import snapshot
    _, store, detector, raw, bars, prop, clock = scenario(tmp_path, tf=tf, wait=2)
    renders = []
    def forbidden_render(*args, **kwargs):
        renders.append(args)
        raise AssertionError('disabled Telegram must not render its photo')
    monkeypatch.setattr(snapshot, 'render_signal', forbidden_render)
    gate = ModelGate(store, lambda: clock[0], threading.Event(), detector)
    assert gate.telegram_enabled is False
    gate.process(raw['symbol'], tf, bars)
    events = store.list_events(kind=MODEL_KIND, protocol=MODEL_PROTOCOL)
    assert len(events) == 1 and is_model_signal(events[0])
    assert events[0]['notification_status'] == 'history'
    assert events[0]['bark_notification_status'] == ('pending' if tf in BARK_TIMEFRAMES else 'history')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == int(tf in BARK_TIMEFRAMES)
    assert not renders
    assert store.telegram_media_status() == {'snapshots': 0, 'render_fallbacks': 0}
    gate.process(raw['symbol'], tf, bars)
    assert store.event_count(MODEL_KIND, MODEL_PROTOCOL) == 1
    assert store.bark_status()['pending'] == int(tf in BARK_TIMEFRAMES)


@pytest.mark.parametrize('changes', [
    {'source': 'replay'}, {'confirmation': 'yolo'}, {'direction': 'short'},
    {'side': 'short'}, {'risk': 0.},
])
def test_non_v1_raw_contract_cannot_enter_confirmation_queue(tmp_path, changes):
    """Retires legacy IMACD ``md`` invalidation: V1 has no such gate."""
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2, register=False)
    raw.update(changes)
    assert not gate.register(raw)
    gate.process(raw['symbol'],'1H',bars)
    assert not store.list_candidates()
    assert not detector.calls
    assert store.event_count(MODEL_KIND)==0


def test_future_reversal_does_not_cancel_earlier_confirmation(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=1)
    next_bar=dict(bars[-1],t=bars[-1]['t']+TIMEFRAMES['1H'],md=-1.)
    clock[0]+=TIMEFRAMES['1H']
    gate.process(raw['symbol'],'1H',bars+[next_bar])
    assert store.list_candidates()[0]['model']['status']=='confirmed'
    assert store.list_events(kind=MODEL_KIND)[0]['bar_open_ms']==bars[-1]['t']


@pytest.mark.parametrize('change', ['wrong_side','other_setup','oversized_core','wrong_hash'])
def test_unrelated_or_structurally_wrong_model_cannot_confirm(tmp_path,change):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2)
    if change=='wrong_side': prop['side']='short'
    elif change=='other_setup':
        prop['core_start_ms']=raw['bar_open_ms']+TIMEFRAMES['1H']
        prop['core_end_ms']=prop['core_start_ms']+3*TIMEFRAMES['1H']
    elif change=='oversized_core': prop['structural_pass']=False
    else: prop['model_sha256']='x'*64
    gate.process(raw['symbol'],'1H',bars)
    assert store.event_count(MODEL_KIND)==0
    assert store.telegram_status()['pending']==0


def test_wait_budget_expires_after_tenth_check_not_eleventh(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=9)
    detector.proposals={}
    gate.process(raw['symbol'],'1H',bars)
    assert len(detector.calls)==10
    assert store.list_candidates()[0]['model']['status']=='expired'
    detector.proposals={bars[-1]['t']:[prop]}
    gate.process(raw['symbol'],'1H',bars)
    assert store.event_count(MODEL_KIND)==0 and len(detector.calls)==10


def test_model_error_retries_exact_endpoint_across_restart(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=0)
    detector.fail_once=True
    gate.process(raw['symbol'],'1H',bars)
    assert store.list_candidates()[0]['model']['status']=='error'
    assert store.list_candidates()[0]['model']['last_checked_close_ms'] is None
    again=ModelGate(Store(store.path),lambda:clock[0],threading.Event(),detector,telegram_enabled=True)
    again.process(raw['symbol'],'1H',bars)
    assert detector.calls==[raw['bar_open_ms']]*2
    assert store.event_count(MODEL_KIND)==1
    assert store.telegram_status()['pending']==1


def test_restart_resumes_next_unchecked_endpoint_and_no_old_notifications(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2)
    gate.process(raw['symbol'],'1H',bars[:-1])
    assert len(detector.calls)==2
    clock[0]+=FRESH_MS
    again=ModelGate(Store(store.path),lambda:clock[0],threading.Event(),detector,telegram_enabled=True)
    again.process(raw['symbol'],'1H',bars)
    assert len(detector.calls)==3
    assert store.event_count(MODEL_KIND)==1
    assert store.telegram_status()['pending']==0
    assert store.bark_status()['pending']==0


def test_same_endpoint_candidates_share_inference_and_distinct_identity(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2)
    second=deepcopy(raw)
    second.update(bar_open_ms=raw['bar_open_ms']+TIMEFRAMES['1H'],
                  bar_close_ms=raw['bar_close_ms']+TIMEFRAMES['1H'],near_zero_bars=13)
    gate.register(second)
    gate.process(raw['symbol'],'1H',bars)
    assert len(detector.calls)==3
    events=store.list_events(kind=MODEL_KIND)
    assert len(events)==2 and len({e['id'] for e in events})==2
    assert len({e['source_event_id'] for e in events})==2


def test_earliest_endpoint_then_confidence_and_window_length(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=0)
    prop19=dict(prop,window_len=19,window_start_ms=prop['window_start_ms']-TIMEFRAMES['1H'])
    prop_low=dict(prop,confidence=.5)
    detector.proposals[bars[-1]['t']]=[prop19,prop_low,prop]
    gate.process(raw['symbol'],'1H',bars)
    chosen=store.list_events(kind=MODEL_KIND)[0]['model']
    assert chosen['confidence']==.65 and chosen['window_len']==18


def test_forming_bar_is_not_scored(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=0)
    clock[0]=raw['bar_close_ms']-1
    gate.process(raw['symbol'],'1H',bars)
    assert not detector.calls and store.event_count(MODEL_KIND)==0


def test_lost_history_retires_honestly_after_wait_budget(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=0)
    late=[dict(bars[-1],t=raw['bar_open_ms']+20*TIMEFRAMES['1H'])]
    clock[0]=late[-1]['t']+TIMEFRAMES['1H']
    gate.process(raw['symbol'],'1H',late)
    state=store.list_candidates()[0]['model']
    assert state['status']=='expired' and state['reason']=='confirmation_history_unavailable'
    assert not detector.calls and store.event_count(MODEL_KIND)==0


def test_short_gap_fails_closed_then_recovers_same_endpoint(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2)
    gate.process(raw['symbol'],'1H',bars[:-2]+bars[-1:])
    assert store.list_candidates()[0]['model']['status']=='error'
    gate.process(raw['symbol'],'1H',bars)
    assert store.event_count(MODEL_KIND)==1


def test_channel_cutovers_and_raw_history_never_replayed(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2)
    store.set_meta('notification_policy:'+MODEL_PROTOCOL,{'activated_ms':raw['bar_close_ms']})
    gate.process(raw['symbol'],'1H',bars)
    assert store.telegram_status()['pending']==0
    assert store.bark_status()['pending']==1
    raw_rows=store.list_events(kind='tv_start')
    assert all(r['notification_status']=='history' for r in raw_rows)


def test_atomic_confirmation_rolls_back_invalid_source_and_preserves_pending(tmp_path):
    gate,store,detector,raw,bars,prop,clock=scenario(tmp_path,wait=2)
    event=model_event(close=clock[0]-1000)
    event['source_event_id']='incorrect'
    with pytest.raises(ValueError):
        store.confirm_candidate(store.event_id(raw),event,True,True)
    assert store.list_candidates()[0]['model']['status']=='pending'
    assert store.event_count(MODEL_KIND)==0
