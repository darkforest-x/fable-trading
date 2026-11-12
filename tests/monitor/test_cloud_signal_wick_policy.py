from unittest.mock import Mock

import pytest

from test_cloud_signals import T, engine, example
from yoyo.monitor.cloud_signals import STEP, signal
from yoyo.monitor.cloud_signal_plans import POLICY
from yoyo.monitor.cloud_signal_rules import POSITION_POLICY


@pytest.mark.parametrize('side', [1, -1])
def test_whole_candle_beyond_cloud_is_allowed_and_uses_symbol_fixed_stop(side, tmp_path):
    bar, point, anchor = example(side)
    if side == 1:
        bar['high'] = 97.9  # Entire long candle below min EMA=98.
    else:
        bar.update(open=115, close=115, low=114.1)  # Entire short above max EMA=114.
    item = signal('BTC-USDT-SWAP',bar,point,anchor,T+STEP)
    assert item is not None and item['eligibility_policy'] == POSITION_POLICY
    service = engine(tmp_path, [T+STEP+1])
    service.record(item)
    plan = service.snapshot(signal_id=item['id'])['selected']
    assert plan['stop_pct'] == .015
    assert plan['stop'] == pytest.approx(plan['entry'] * (1-side*.015))
    assert plan['reference_policy'] == POLICY and plan['plan_state'] == 'ready'
    assert service.history(item['symbol'])['items'][0]['id'] == item['id']


def test_old_rejection_rows_are_ignored_without_rewriting_original_events(tmp_path):
    bar,point,anchor=example()
    bar['high']=97.9
    candidate=signal('BTC-USDT-SWAP',bar,point,anchor,T+STEP)
    service=engine(tmp_path,[T+STEP+1])
    service.record(candidate)
    with service.db() as db:
        db.execute('CREATE TABLE signal_positions(signal_id TEXT,policy TEXT,eligible INTEGER,high REAL,low REAL)')
        db.execute('INSERT INTO signal_positions VALUES (?,?,?,?,?)',(candidate['id'],'12h-candle-cloud-contact-v1',0,bar['high'],bar['low']))
        before=[tuple(r) for r in db.execute('SELECT * FROM signals')]
    assert service.snapshot()['total']==1
    assert service.snapshot(limit=1,sort='rr')['items'][0]['planned_rr']>0
    assert service.history(candidate['symbol'])['latest_id']==candidate['id']
    with service.db() as db:
        assert before==[tuple(r) for r in db.execute('SELECT * FROM signals')]
        assert db.execute('SELECT eligible FROM signal_positions').fetchone()[0]==0


def test_fresh_whole_candle_beyond_cloud_remains_viewable_without_notification(tmp_path):
    now=[T+STEP-1]
    sender=Mock(return_value=Mock(status_code=200,json=lambda:dict(ok=True,code=200)))
    service=engine(tmp_path,now,sender)
    service.activate();now[0]+=2
    bar,point,anchor=example();bar['high']=97.9
    service.record(signal('BTC-USDT-SWAP',bar,point,anchor,now[0]))
    plan = service.snapshot()['items'][0]
    assert plan['notifications'] == {'telegram': 'record_only', 'bark': 'record_only'}
    assert plan['stop'] == pytest.approx(92.4127)
    sender.assert_not_called()
