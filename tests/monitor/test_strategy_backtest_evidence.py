"""Saved facts must identify the original leg and preserve native time precision."""
import csv
import gzip
from pathlib import Path

import pytest

from yoyo.monitor import strategy_backtest_evidence as e


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'wt') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


def saved(variant='near_P18', score=.6592223040584619, order=3):
    return dict(event='HEMIUSDT:2026-08-31:+1', side=1, entry_t='2026-09-02 03:50:00+00:00',
                exit_t='2026-09-02 04:20:00+00:00', variant=variant, score=score, order=order, net=2222.553678716531)


def ledger_row():
    r = saved()
    return dict(event=r['event'], symbol='HEMIUSDT', side=1,
                entry_ms=e.millis(r['entry_t']), exit_ms=e.millis(r['exit_t']))


def test_rules_match_recorded_event_side_times_return_and_leg(tmp_path):
    path = tmp_path / 'scores.csv'
    write(path, [saved(), saved('v3_add5')])
    row = ledger_row()
    result = e.rules('spike', row, saved()['net'], e.stamp(path))
    assert result['score'] == .6592223040584619 and result['order'] == 3
    assert result['minimum'] == .6 and result['minimum_order'] == 3
    assert result['kind'] == 'first_touch'
    assert e.rules('v3add', row, saved()['net'], e.stamp(path))['kind'] == 'add5'
    for change in [dict(event='OTHER'), dict(side=-1), dict(entry_ms=row['entry_ms']+60000), dict(exit_ms=row['exit_ms']+1)]:
        assert e.rules('spike', dict(row, **change), saved()['net'], e.stamp(path))['status'] == 'unavailable'
    assert e.rules('spike', row, 0, e.stamp(path))['status'] == 'unavailable'


@pytest.mark.parametrize('score,order', [(float('nan'),3),(.599,3),(.7,2),(1.1,4)])
def test_missing_or_nonqualifying_values_never_claim_a_pass(tmp_path, score, order):
    path = tmp_path / 'scores.csv'; write(path, [saved(score=score, order=order)])
    assert e.rules('spike', ledger_row(), saved()['net'], e.stamp(path))['status'] == 'unavailable'
    assert e.rules('spike', ledger_row(), saved()['net'], e.stamp(tmp_path/'missing'))['score'] is None


def test_ambiguous_base_and_add_rows_are_not_guessed(tmp_path):
    path = tmp_path / 'scores.csv'; write(path, [saved('v3_be0'), saved('v3_add5')])
    assert e.rules('v3add', ledger_row(), saved()['net'], e.stamp(path))['status'] == 'unavailable'


def test_native_prices_and_intervals_are_not_the_later_candle_or_level(tmp_path):
    directory = tmp_path / 'booklet'; row = ledger_row()
    source = tmp_path / 'scores.csv'; write(source, [saved()])
    row['entry_rules'] = e.rules('spike', row, saved()['net'], e.stamp(source))
    native = dict(trade_id=row['event']+':m60:near_monday_edge:c0:v_near_P18', variant='near_P18',
                  side=1, entry=.01115564, exit_price=.01364, reason=4,
                  entry_time=saved()['entry_t'], exit_time=saved()['exit_t'], net_notional_bp=saved()['net'])
    paths = e.native_paths('spike','near_P18','HEMIUSDT',directory)
    for p in paths: write(p, [native])
    f = e.fill_geometry('spike', row, directory, 5)
    assert f['entry_price'] == .01115564 and f['exit_price'] == .01364
    assert (f['entry_start_ms'],f['entry_end_ms']) == (e.millis('2026-09-02 03:45:00+00:00'), row['entry_ms'])
    assert f['exit_start_ms'] == row['exit_ms']-300000 and f['exit_kind']=='interval'
    assert e.fill_geometry('spike', ledger_row(), directory, 5) is None
    write(paths[1], [dict(native, net_notional_bp=-50)])
    with pytest.raises(ValueError, match='lineage'):
        e.fill_geometry('spike', row, directory, 5)


def test_close_exit_stays_at_its_known_boundary(tmp_path):
    directory=tmp_path/'booklet'; row=ledger_row(); source=tmp_path/'scores.csv'
    write(source,[saved()]); row['entry_rules']=e.rules('spike',row,saved()['net'],e.stamp(source))
    native=dict(trade_id=row['event']+':m60:near_monday_edge:c0:v_near_P18',variant='near_P18',side=1,
                entry=.01115564,exit_price=.012,reason=11,entry_time=saved()['entry_t'],exit_time=saved()['exit_t'],net_notional_bp=saved()['net']+10)
    for p in e.native_paths('spike','near_P18','HEMIUSDT',directory):write(p,[native])
    f=e.fill_geometry('spike',row,directory,5)
    assert f['exit_kind']=='close' and f['exit_start_ms']==f['exit_end_ms']==row['exit_ms']
