from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from yoyo.monitor.cloud_signals import CloudSignals, STEP, FRESH_MS, VERSION, ema_points, signal
from yoyo.monitor.cloud_signal_plans import MONDAY_POLICY, POLICY
from yoyo.monitor.monday_fib import week_window


T = 1793318400000  # 2026-10-30 UTC


def example(side=1):
    w = week_window(T)
    anchor = dict(symbol='BTC-USDT-SWAP', state='ready', week=w['week'], start_ms=w['start'],
                  end_ms=w['end'], expires_ms=w['expires'], high=110, low=100, upper=116.18, lower=93.82)
    bar = dict(time=T, open=95, high=99, low=90, close=96)
    point = dict(time=T, ema20=99, ema50=98, ready=True)
    if side == -1:
        bar.update(open=115, high=120, low=111, close=114)
        point.update(ema20=113, ema50=114)
    return bar, point, anchor


def item(side=1):
    return signal('BTC-USDT-SWAP', *example(side), T+STEP)


def reweek(value, days=7, *, symbol=None):
    stamp = value['signal_open_ms'] + days * 86_400_000
    window = week_window(stamp)
    symbol = symbol or value['symbol']
    range_data = dict(value['range'], symbol=symbol, week=window['week'],
                      start_ms=window['start'], end_ms=window['end'],
                      expires_ms=window['expires'])
    bar = dict(value['signal_bar'], time=stamp)
    return dict(value, id=f"{VERSION}:{symbol}:{stamp}:{value['side']}",
                symbol=symbol, signal_open_ms=stamp, confirmed_ms=stamp+STEP,
                week=window['week'], range=range_data, signal_bar=bar,
                confirmation_close=bar['close'])


def next_candidate(side=-1):
    stamp = T + STEP
    bar, point, anchor = example(side)
    bar = dict(bar, time=stamp)
    point = dict(point, time=stamp)
    window = week_window(stamp)
    anchor = dict(anchor, week=window['week'], start_ms=window['start'],
                   end_ms=window['end'], expires_ms=window['expires'])
    return signal('BTC-USDT-SWAP', bar, point, anchor, stamp+STEP)


def engine(tmp_path, now, sender=None):
    client = Mock(clock=lambda:now[0])
    return CloudSignals(tmp_path, client=client, levels=Mock(), sender=sender,
                        telegram_creds=('fake-local-test-token', 'fake-chat'), bark_key='fake-device')


def test_long_and_short_frozen_reference():
    long, short = item(), item(-1)
    assert (long['entry'], long['confirmation_close'], long['stop'], long['target']) == (
        93.82, 96, pytest.approx(93.82 * .985), 116.18)
    assert (short['entry'], short['confirmation_close'], short['stop'], short['target']) == (
        116.18, 114, pytest.approx(116.18 * 1.015), 93.82)
    assert long['stop_pct'] == short['stop_pct'] == .015
    assert long['planned_rr'] == pytest.approx((116.18-93.82)/(93.82*.015))
    assert short['planned_rr'] == pytest.approx((116.18-93.82)/(116.18*.015))
    assert short['side'] == -1


@pytest.mark.parametrize(('symbol', 'stop_pct'), [
    ('BTC-USDT-SWAP', .015),
    ('ETH-USDT-SWAP', .015),
    ('SOL-USDT-SWAP', .03),
    ('BTCB-USDT-SWAP', .03),
    ('ETHW-USDT-SWAP', .03),
])
@pytest.mark.parametrize('side', [1, -1])
def test_raw_signal_uses_symbol_stop_policy_without_changing_identity(symbol, stop_pct, side):
    bar, point, anchor = example(side)
    anchor = dict(anchor, symbol=symbol)

    candidate = signal(symbol, bar, point, anchor, T+STEP)

    entry = anchor['lower'] if side == 1 else anchor['upper']
    target = anchor['upper'] if side == 1 else anchor['lower']
    assert candidate['id'] == f'{VERSION}:{symbol}:{T}:{side}'
    assert candidate['signal_open_ms'] == T and candidate['confirmed_ms'] == T+STEP
    assert candidate['entry'] == entry and candidate['target'] == target
    assert candidate['stop_pct'] == stop_pct
    assert candidate['stop'] == pytest.approx(entry * (1-side*stop_pct))
    assert candidate['planned_rr'] == pytest.approx((target-entry)*side/(entry*stop_pct))
    assert candidate['reference_policy'] == POLICY
    assert candidate['signal_bar'] == bar


def test_monday_range_mode_uses_strict_boundary_reclaim_and_scoped_identity():
    bar, point, anchor = example()
    bar = dict(bar, open=101, high=109, low=99, close=105)

    candidate = signal('BTC-USDT-SWAP', bar, point, anchor, T+STEP, range_mode='monday')

    assert candidate['id'] == f'{VERSION}:BTC-USDT-SWAP:{T}:1:monday'
    assert candidate['range_mode'] == 'monday'
    assert candidate['reference_policy'] == MONDAY_POLICY
    assert candidate['entry'] == 100 and candidate['target'] == 110
    assert candidate['stop'] == pytest.approx(100 * .985)
    assert signal('BTC-USDT-SWAP', dict(bar, low=100), point, anchor, T+STEP,
                  range_mode='monday') is None
    assert signal('BTC-USDT-SWAP', dict(bar, close=100), point, anchor, T+STEP,
                  range_mode='monday') is None
    with pytest.raises(ValueError, match='badrangemode'):
        signal('BTC-USDT-SWAP', bar, point, anchor, T+STEP, range_mode='other')


def test_no_open_candle_no_equal_touch_no_flat_cloud():
    bar, point, anchor = example()
    for now in [T, T+STEP-1]:
        assert signal('BTC-USDT-SWAP', bar, point, anchor, now) is None
    for key, value in [('low', anchor['lower']), ('close', anchor['lower'])]:
        assert signal('BTC-USDT-SWAP', dict(bar, **{key:value}), point, anchor, T+STEP) is None
    assert signal('BTC-USDT-SWAP', bar, dict(point, ema20=98), anchor, T+STEP) is None
    assert signal('BTC-USDT-SWAP', bar, dict(point, ready=False), anchor, T+STEP) is None


def test_anchor_must_exist_before_open_and_remain_valid_through_close():
    bar, point, anchor = example()
    for patch in [dict(state='developing'), dict(end_ms=T+1), dict(expires_ms=T+STEP-1), dict(symbol='ETH-USDT-SWAP')]:
        assert signal('BTC-USDT-SWAP', bar, point, dict(anchor, **patch), T+STEP) is None
    assert signal('BTC-USDT-SWAP', dict(bar, close=120, high=121), point, anchor, T+STEP) is None


def test_ema_prefix_and_gap_warmup():
    rows = [dict(time=i*STEP, close=100+i/10) for i in range(300)]
    original = ema_points(rows)
    assert original[:250] == ema_points(rows[:250])
    assert not original[48]['ready'] and original[49]['ready']
    assert ema_points(rows[250:], original[249]) == original[250:]
    changed = deepcopy(rows)
    changed[-1]['close'] = 9999
    assert ema_points(changed)[:-1] == original[:-1]
    rows[280]['time'] += STEP//2
    assert not ema_points(rows)[-1]['ready']


def test_startup_history_and_durable_channel_dedup(tmp_path):
    now = [T+STEP-1000]
    sender = Mock(return_value=Mock(status_code=200, json=lambda:dict(ok=True, code=200)))
    first = engine(tmp_path, now, sender)
    first.activate()
    now[0] += 2000
    assert first.record(item())
    assert not first.record(item())
    assert sender.call_count == 0
    restarted = engine(tmp_path, now, sender)
    restarted.activate()
    assert sender.call_count == 0
    assert restarted.snapshot()['items'][0]['notifications'] == dict(telegram='record_only', bark='record_only')
    assert not restarted.snapshot()['items'][0]['historical']


def test_record_keeps_only_first_same_week_signal_across_directions(tmp_path):
    now = [T+STEP-1000]
    sender = Mock(return_value=Mock(status_code=200, json=lambda:dict(ok=True, code=200)))
    service = engine(tmp_path, now, sender)
    service.activate()
    now[0] += 2000
    first, opposite = item(1), next_candidate(-1)
    assert first['week'] == opposite['week']
    assert service.record(first)
    assert not service.record(opposite)
    assert sender.call_count == 0
    assert [value['id'] for value in service.snapshot()['items']] == [first['id']]


def test_legacy_same_week_projection_filters_before_card_filters_and_redirects_deeplink(tmp_path):
    first = item(1)
    duplicate = next_candidate(-1)
    later_week = reweek(first)
    other_symbol = dict(item(-1), symbol='ETH-USDT-SWAP')
    other_symbol['range'] = dict(other_symbol['range'], symbol='ETH-USDT-SWAP')
    now = [later_week['confirmed_ms']+1]
    sender = Mock()
    service = engine(tmp_path, now, sender)
    service.activate()
    assert service.record(first)
    assert service.record(later_week)
    assert service.record(other_symbol)
    with service.db() as db:
        db.execute('INSERT INTO signals VALUES (?,?,?,?,?)',
                   (duplicate['id'], duplicate['confirmed_ms'], json.dumps(duplicate), 'sent', 'unknown'))
        before = tuple(db.execute('SELECT payload,telegram,bark FROM signals WHERE id=?',
                                  (duplicate['id'],)).fetchone())
        activated_before = db.execute("SELECT value FROM meta WHERE key='activated_ms'").fetchone()[0]

    cards = service.snapshot(limit=1, search='BTC/USDT', side=1, sort='oldest', scope='all')
    assert cards['total'] == 2
    assert cards['items'][0]['id'] == first['id']
    assert service.snapshot(search='BTC', side=-1)['total'] == 0
    selected = service.snapshot(limit=1, search='ETH', side=-1, scope='live', signal_id=duplicate['id'])
    assert selected['items'] == []
    assert selected['selected']['id'] == first['id']
    assert selected['selected_requested_id'] == duplicate['id']
    assert selected['selection_redirect_id'] == first['id']
    assert [x['id'] for x in service.history('BTC-USDT-SWAP')['items']] == [first['id'], later_week['id']]
    assert [x['id'] for x in service.history('ETH-USDT-SWAP')['items']] == [other_symbol['id']]

    with service.db() as db:
        after = tuple(db.execute('SELECT payload,telegram,bark FROM signals WHERE id=?',
                                 (duplicate['id'],)).fetchone())
        activated_after = db.execute("SELECT value FROM meta WHERE key='activated_ms'").fetchone()[0]
    assert after == before
    assert activated_after == activated_before
    assert sender.call_count == 0


def test_historical_and_expired_are_never_sent(tmp_path):
    now = [T+STEP+1]
    sender = Mock()
    service = engine(tmp_path, now, sender)
    service.activate()
    service.record(item())
    assert sender.call_count == 0
    assert service.snapshot()['items'][0]['historical']


def test_legacy_pending_does_not_send_on_restart(tmp_path):
    now = [T+STEP-1]
    sender = Mock(side_effect=TimeoutError)
    service = engine(tmp_path, now, sender)
    service.activate(); now[0] += 2
    service.record(item())
    with service.db() as db:
        db.execute("UPDATE signals SET telegram='pending',bark='pending'")
    service.activate()
    assert sender.call_count == 0
    assert set(service.snapshot()['items'][0]['notifications'].values()) == {'record_only'}


def test_expired_queue_and_interrupted_sending(tmp_path):
    now = [T+STEP-1]
    sender = Mock()
    service = engine(tmp_path, now, sender)
    service.activate(); now[0] += 2
    service.record(item())
    with service.db() as db:
        db.execute("UPDATE signals SET bark='sending'")
    now[0] += FRESH_MS
    service.activate()
    assert sender.call_count == 0
    assert service.snapshot()['items'][0]['notifications'] == dict(telegram='record_only', bark='unknown')


def test_scanner_excludes_forming_bar_and_resumes_ema_from_checkpoint(tmp_path):
    now = [T+STEP+5000]
    service = engine(tmp_path, now)
    raw = []
    for i in range(299):
        t, c = T-(298-i)*STEP, 95+i*.05
        raw.append([str(t), str(c), str(c+1), str(90 if i==298 else c-1), str(c), '1', '1', '1', '1'])
    raw.append([str(T+STEP), '110', '112', '80', '111', '1', '1', '1', '0'])
    service.client.get = Mock(return_value=raw)
    service.levels.read = lambda symbol, at: dict(example()[2], **{
        'week':week_window(at)['week'], 'start_ms':week_window(at)['start'],
        'end_ms':week_window(at)['end'], 'expires_ms':week_window(at)['expires']})
    service.activate()
    service.scan_symbol('BTC-USDT-SWAP', T+STEP)
    snap = service.snapshot()
    assert snap['total'] == 1 and snap['items'][0]['confirmed_ms'] == T+STEP
    assert snap['items'][0]['historical']
    with service.db() as db:
        state = db.execute('SELECT ema_state FROM checkpoints').fetchone()[0]
    service.scan_symbol('BTC-USDT-SWAP', T+STEP)
    with service.db() as db:
        assert state == db.execute('SELECT ema_state FROM checkpoints').fetchone()[0]
    assert service.snapshot()['total'] == 1


def test_read_only_api_contract(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from yoyo.monitor import cloud_signals
    saved_item = item()
    service = engine(tmp_path, [T+8*86_400_000+STEP+1])
    service.record(saved_item)
    newer = reweek(saved_item)
    service.record(newer)
    monkeypatch.setattr(cloud_signals, 'CloudSignals', lambda runtime:service)
    app = FastAPI()
    cloud_signals.install(app, tmp_path)
    snapshot_route = next(route for route in app.routes if getattr(route, 'path', None) == '/api/cloud-signals')
    history_route = next(route for route in app.routes if getattr(route, 'path', None) == '/api/cloud-signals/history')
    data = snapshot_route.endpoint(limit=300, signal_id='', search='', side=0, sort='newest', scope='all')
    assert data['schema'] == 1 and len(data['items']) == 2
    assert data['items'][0]['reference_only'] is True
    selected = snapshot_route.endpoint(limit=1, signal_id=saved_item['id'], search='', side=0,
                                       sort='newest', scope='all')
    assert selected['items'][0]['id'] == newer['id']
    assert selected['selected']['id'] == saved_item['id']
    assert selected['selected']['target'] == saved_item['target']
    assert selected['selected_requested_id'] == saved_item['id']
    assert selected['selection_redirect_id'] is None
    eth = dict(item(-1), id='eth', symbol='ETH-USDT-SWAP', target=80, planned_rr=99)
    eth['range'] = dict(eth['range'], symbol='ETH-USDT-SWAP', lower=50, upper=120)
    eth['signal_bar'] = dict(eth['signal_bar'], high=122)
    service.record(eth, atr=1)
    filtered = snapshot_route.endpoint(limit=1, signal_id='', search='BTC/USDT', side=1,
                                      sort='oldest', scope='all')
    assert filtered['total'] == 2 and filtered['items'][0]['id'] == saved_item['id']
    assert snapshot_route.endpoint(limit=1, signal_id='', search='', side=0,
                                   sort='rr', scope='all')['items'][0]['id'] == 'eth'
    assert snapshot_route.endpoint(limit=300, signal_id='', search='ETH', side=1,
                                   sort='newest', scope='all')['total'] == 0
    history = history_route.endpoint(symbol='BTC-USDT-SWAP', week='')
    assert history['symbol'] == 'BTC-USDT-SWAP'
    assert [x['id'] for x in history['items']] == [saved_item['id'], newer['id']]
    assert history['latest_id'] == newer['id']
    monday_empty = history_route.endpoint(symbol='BTC-USDT-SWAP', week='', range_mode='monday')
    assert {key: monday_empty[key] for key in (
        'schema', 'symbol', 'items', 'latest_id', 'reconstruction_supported', 'range_mode'
    )} == dict(schema=1, symbol='BTC-USDT-SWAP', items=[], latest_id=None,
               reconstruction_supported=True, range_mode='monday')
    assert monday_empty['reference_policy'] == MONDAY_POLICY
    with pytest.raises(HTTPException) as error:
        history_route.endpoint(symbol='BTC-USDT-SWAP', week='', range_mode='other')
    assert error.value.status_code == 400
    app.state.cloud_signal_history.read = Mock(return_value=dict(symbol='BTC-USDT-SWAP',
        week='2026-08-24', state='ready', complete=True, items=[]))
    replay = history_route.endpoint(symbol='BTC-USDT-SWAP', week='2026-08-24', range_mode='monday')
    assert replay['complete'] is True
    app.state.cloud_signal_history.read.assert_called_once_with(
        'BTC-USDT-SWAP', '2026-08-24', range_mode='monday')


def test_origin_and_recency_are_separate_and_time_boundaries_are_exact(tmp_path):
    now = [T+STEP-1]
    service = engine(tmp_path, now)
    service.activate()
    backfill = reweek(item(), days=-7)
    backfill['id'] = 'backfill'
    service.record(backfill)
    now[0] = T+STEP+1
    service.record(item())
    assert service.snapshot(scope='live')['items'][0]['freshness'] == 'new'
    now[0] = T+STEP+FRESH_MS
    assert service.snapshot(scope='live')['items'][0]['freshness'] == 'recent'
    delayed = dict(item(), id='delayed', symbol='ETH-USDT-SWAP')
    delayed['range'] = dict(delayed['range'], symbol='ETH-USDT-SWAP')
    service.record(delayed)
    late = service.snapshot(signal_id='delayed', scope='live')['selected']
    assert late['provenance'] == 'delayed' and late['freshness'] == 'archived'
    assert service.snapshot(scope='live')['counts'] == dict(live=1, history=2, all=3)
    now[0] = T+2*STEP-1
    assert service.snapshot(scope='live')['total'] == 1
    now[0] += 1
    assert service.snapshot(scope='live')['total'] == 0
    history = service.snapshot(scope='history')
    assert {x['id']:x['provenance'] for x in history['items']} == {item()['id']:'live', 'backfill':'backfill', 'delayed':'delayed'}
    assert history['activated_ms'] == T+STEP-1 and history['fresh_ms'] == FRESH_MS
    assert history['next_close_ms'] == T+3*STEP
    assert all(x['freshness'] == 'archived' for x in history['items'])


def test_scope_counts_follow_search_direction_and_selected_bypasses_scope(tmp_path):
    now = [T+STEP-1]
    service = engine(tmp_path, now); service.activate()
    now[0] = T+STEP+1
    service.record(item())
    service.record(dict(item(-1), id='eth', symbol='ETH-USDT-SWAP'))
    old = reweek(item(), days=-7)
    old['id'] = 'old'
    service.record(old)
    result = service.snapshot(limit=1, search='BTC/USDT', side=1, scope='live', signal_id='old')
    assert result['counts'] == dict(live=1, history=1, all=2)
    assert result['total'] == 1 and result['selected']['provenance'] == 'backfill'
    assert result['selected']['target'] == item()['target']
    assert service.snapshot(scope='history', search='ETH')['total'] == 0
    before = service.snapshot(signal_id=item()['id'])['selected']
    now[0] = T+STEP-100
    assert service.snapshot(scope='live')['total'] == 0
    after = service.snapshot(signal_id=item()['id'])['selected']
    assert after is None  # A clock before confirmation cannot expose future data.
    now[0] = T+STEP+1
    restored = service.snapshot(signal_id=item()['id'])['selected']
    assert not restored['historical'] and restored['detected_ms'] == before['detected_ms']
