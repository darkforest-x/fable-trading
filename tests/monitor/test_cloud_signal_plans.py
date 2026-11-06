import json
from copy import deepcopy

import pytest

from yoyo.monitor.cloud_signal_plans import (atr_points, reference, fixed_stop,
                                             stop_pct_for, STEP, POLICY, MONDAY_POLICY)
from yoyo.data.candle_hub import CandleHub
from test_cloud_signals import engine, item, T
from yoyo.monitor.cloud_signals import notification_text
from yoyo.monitor.monday_fib import week_window


def test_wilder_atr_seed_gap_and_prefix_are_causal():
    rows = [dict(time=i*STEP, high=102+i, low=98+i, close=100+i) for i in range(50)]
    points = atr_points(rows)
    assert points[12]['atr14'] is None
    assert points[13]['atr14'] == 4
    changed = deepcopy(rows)
    changed[-1].update(high=300, close=299)
    assert atr_points(changed)[:-1] == points[:-1]
    assert atr_points(rows[:30]) == points[:30]
    assert atr_points(rows[30:], points[29]) == points[30:]
    gap = dict(rows[-1], time=rows[-1]['time']+STEP)
    assert atr_points([gap], points[-2])[0]['atr14'] is None
    jump = dict(rows[-1], high=200, low=190, close=195)
    expected_tr = 200 - rows[-2]['close']
    assert atr_points([jump], points[-2])[0]['atr14'] == pytest.approx(4+(expected_tr-4)/14)


@pytest.mark.parametrize(('symbol', 'expected_pct'), [
    ('BTC-USDT-SWAP', 0.015),
    ('ETH-USDT-SWAP', 0.015),
    ('SOL-USDT-SWAP', 0.03),
    ('BTCB-USDT-SWAP', 0.03),
    ('ETHW-USDT-SWAP', 0.03),
])
@pytest.mark.parametrize('side', [1, -1])
def test_fixed_stop_is_symbol_tiered_and_directional(symbol, expected_pct, side):
    entry = 100.0
    stop, stop_pct = fixed_stop(symbol, side, entry)
    assert stop_pct == expected_pct
    assert stop == pytest.approx(entry * (1 - side * expected_pct))
    assert stop_pct_for(symbol) == expected_pct


def test_touched_side_entry_and_fixed_percent_stop_without_atr_or_cloud_buffer():
    assert POLICY == 'touched-side-1618-entry-symbol-fixed-stop-v5'
    long = dict(item(), entry=100)
    before = deepcopy(long)
    plan = reference(long, 2)
    assert plan['stop'] == pytest.approx(93.82 * (1 - 0.015))
    assert plan['stop_pct'] == 0.015
    assert plan['entry'] == 93.82
    assert plan['entry_basis'] == '触及侧 1.618（12H 收盘确认后展示）'
    assert plan['confirmation_close'] == 96
    assert plan['planned_rr'] == pytest.approx((116.18-93.82)/(93.82*0.015))
    assert plan['plan_state'] == 'ready' and plan['reference_policy'] == POLICY
    short = reference(item(-1), 2)
    assert short['stop'] == pytest.approx(116.18 * (1 + 0.015))
    assert short['stop_pct'] == 0.015
    assert short['entry'] == 116.18 and short['confirmation_close'] == 114
    assert short['planned_rr'] == pytest.approx((116.18-93.82)/(116.18*0.015))
    missing_range = reference({key:value for key,value in item().items() if key != 'range'}, 2)
    assert missing_range['entry'] is None and missing_range['plan_state'] == 'invalid'
    assert missing_range['stop'] is None and missing_range['stop_pct'] == 0.015
    assert missing_range['planned_rr'] is None
    assert missing_range['confirmation_close'] == 96
    assert missing_range['original_reference']['entry'] == item()['entry']
    assert reference(long)['plan_state'] == 'ready'
    assert reference(long,200)['stop'] == reference(long)['stop']
    assert plan['original_reference']['stop'] == before['stop'] and long == before


def test_legacy_reference_is_preserved_and_projection_is_idempotent():
    legacy = dict(item(), entry=93.82, stop=90, target=116.18,
                  planned_rr=(116.18-93.82)/(93.82-90),
                  reference_policy='touched-side-1618-entry-key-wick-stop-v3')

    projected = reference(legacy)
    repeated = reference(projected)

    assert projected['original_reference'] == dict(
        entry=legacy['entry'], stop=legacy['stop'], target=legacy['target'],
        planned_rr=legacy['planned_rr'], basis='stored-reference')
    assert projected['stop'] == pytest.approx(93.82 * (1 - 0.015))
    assert projected['planned_rr'] == pytest.approx((116.18-93.82)/(93.82*0.015))
    assert projected['reference_policy'] == POLICY
    assert {key: repeated[key] for key in ('entry', 'stop', 'target', 'planned_rr', 'stop_pct',
                                           'reference_policy', 'original_reference')} == {
        key: projected[key] for key in ('entry', 'stop', 'target', 'planned_rr', 'stop_pct',
                                        'reference_policy', 'original_reference')
    }


def test_monday_reference_uses_anchor_boundaries_with_the_same_fixed_stop():
    fib_long = item()
    monday_long = reference(fib_long, 2, range_mode='monday')
    fib_short = item(-1)
    monday_short = reference(fib_short, 2, range_mode='monday')

    assert monday_long['range_mode'] == monday_short['range_mode'] == 'monday'
    assert monday_long['reference_policy'] == monday_short['reference_policy'] == MONDAY_POLICY
    assert monday_long['entry'] == 100 and monday_long['target'] == 110
    assert monday_long['stop'] == pytest.approx(100 * .985)
    assert monday_long['stop_pct'] == .015
    assert monday_long['entry_basis'] == '触及侧周一边界（12H 收盘确认后展示）'
    assert '周一边界入场为基准固定止损 1.5%' in monday_long['stop_basis']
    assert monday_short['entry'] == 110 and monday_short['target'] == 100
    assert monday_short['stop'] == pytest.approx(110 * 1.015)
    assert monday_short['planned_rr'] == pytest.approx(10 / (110 * .015))
    assert monday_long['original_reference']['entry'] == fib_long['entry']
    assert reference(fib_long)['entry'] == fib_long['entry']


def test_monday_reference_without_anchor_boundaries_is_invalid():
    value = item()
    value['range'] = {key: val for key, val in value['range'].items()
                      if key not in ('low', 'high')}

    plan = reference(value, range_mode='monday')

    assert plan['plan_state'] == 'invalid'
    assert plan['entry'] is plan['stop'] is plan['target'] is None
    assert plan['reference_policy'] == MONDAY_POLICY


def test_cached_backfill_preserves_observation_bytes_channels_and_activation(tmp_path):
    now = [T+STEP+1]
    service = engine(tmp_path, now)
    service.activate()
    service.record(item(-1))
    hub = CandleHub(tmp_path)
    service.client.hub = hub
    key = '|'.join((hub.origin, item()['symbol'], '12Hutc'))
    with hub.db() as db:
        for i in range(60):
            t = T-(59-i)*STEP
            raw = [str(t), '100', '103', '98', '101', '1', '1', '1', '1']
            db.execute('INSERT INTO candles VALUES (?,?,?)', (key,t,json.dumps(raw)))
        # Later volatility must not alter a reference at T.
        raw = [str(T+STEP), '101', '900', '10', '800', '1', '1', '1', '1']
        db.execute('INSERT INTO candles VALUES (?,?,?)', (key,T+STEP,json.dumps(raw)))
    with service.db() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM signals')]
        activated = list(db.execute('SELECT value FROM meta'))[0][0]
    service.prepare_references()
    service.prepare_references()
    plan = service.snapshot()['items'][0]
    assert plan['atr14'] == 5 and plan['stop'] == pytest.approx(116.18 * 1.015)
    assert plan['reference_policy'] == POLICY and plan['provenance'] == 'backfill'
    with service.db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM signals')] == before
        assert list(db.execute('SELECT value FROM meta'))[0][0] == activated
        assert db.execute('SELECT COUNT(*) FROM reference_inputs').fetchone()[0] == 1


def test_rr_sort_and_symbol_history_use_projected_plans_not_old_wick_r(tmp_path):
    a = dict(item(-1), id='a', planned_rr=999)
    next_time = T+7*86_400_000
    next_window = week_window(next_time)
    next_range = dict(a['range'], week=next_window['week'], start_ms=next_window['start'],
                      end_ms=next_window['end'], expires_ms=next_window['expires'],
                      lower=80, upper=119)
    b = dict(a, id='b', signal_open_ms=next_time, confirmed_ms=next_time+STEP,
             week=next_window['week'], range=next_range, planned_rr=.1)
    other = dict(a, id='x', symbol='ETH-USDT-SWAP', range=dict(a['range'], symbol='ETH-USDT-SWAP'))
    future = dict(b, id='future', confirmed_ms=next_time+2*STEP)
    service = engine(tmp_path, [next_time+2*STEP+1])
    for value, atr in [(a,20),(b,2),(other,10),(future,10)]:
        service.record(value, atr=atr)
    assert service.snapshot(sort='rr', limit=1)['items'][0]['id'] == 'b'
    history = service.history('BTC-USDT-SWAP')
    assert [x['id'] for x in history['items']] == ['a','b']
    assert history['latest_id'] == 'b'
    assert service.history('BTC2-USDT-SWAP')['items'] == []


def test_notification_uses_same_projected_stop_and_rr_as_cards(tmp_path):
    service = engine(tmp_path, [T+STEP+1])
    service.record(item(-1), atr=2)
    plan = service.snapshot()['items'][0]
    text = notification_text(plan)
    assert '1.618 参考入场 116.18' in text
    assert f"固定止损 {plan['stop']:.8g}（1.5%）" in text
    assert f"计划 {plan['planned_rr']:.2f}R" in text
    assert '1.618 参考入场为基准固定止损 1.5%' in text
    assert 'ATR' not in text
    assert plan['id'] in text
