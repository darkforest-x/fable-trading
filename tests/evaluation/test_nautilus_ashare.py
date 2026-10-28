"""Native A-share execution invariants on synthetic tapes, not market reruns."""
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.layers.l3_backtest.nautilus_ashare import run_native

CFG = json.loads(Path('experiments/active/exp-ashare-monday1618-20261028-v1/config.json').read_text())


def tape(rows):
    f = pd.DataFrame(rows, columns=['time','open','high','low','close','limit_down','limit_up'])
    f.index = pd.to_datetime(f.pop('time')).dt.tz_localize('Asia/Shanghai').dt.tz_convert('UTC')
    f['date'] = f.index.tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
    f['session_end'] = f.index.tz_convert('Asia/Shanghai').hour == 15
    f['volume'] = 1e6
    f['censor_after'] = False
    return f


def plan(target=11):
    return [dict(trade_id='s1', pair_id='p1', population='signal',
        entry=9., target=target, start_date='2026-05-05', expire_date='2026-05-08')]


def test_t1_then_next_open_target_and_fee():
    f = tape([
        ['2026-05-05 10:30',10,10,8.8,9.5,8,12],
        ['2026-05-05 15:00',9.5,11.5,9.5,11.2,8,12],
        ['2026-05-06 10:30',11.3,11.5,11,11.4,10.08,12.32],
        ['2026-05-06 15:00',11.4,11.5,11,11.4,10.08,12.32],
    ])
    t,fills,a=run_native(f,plan(),CFG,'TEST')
    assert len(t)==1 and t.iloc[0].reason=='target'
    assert t.iloc[0].entry_date=='2026-05-05' and t.iloc[0].exit_date=='2026-05-06'
    assert t.iloc[0].quantity==1100
    assert t.iloc[0].fill_price==9 and t.iloc[0].exit_price==11.3
    assert fills.fee.sum()==pytest.approx(16.44)
    assert a['t1_target_touch_lots']==1


def test_open_gap_improvement_and_stop_limit_lock():
    f=tape([
        ['2026-05-05 10:30',8.5,9,8,8.4,7,11],
        ['2026-05-05 15:00',8.4,8.4,8,8,7,11],
        ['2026-05-06 10:30',5.6,5.6,5.6,5.6,5.6,8],
        ['2026-05-06 15:00',5.6,5.6,5.6,5.6,5.6,8],
        ['2026-05-07 10:30',5.4,5.5,5.2,5.3,5.04,6.16],
        ['2026-05-07 15:00',5.3,5.4,5.2,5.3,5.04,6.16],
    ])
    t,fills,a=run_native(f,plan(12),CFG,'TEST')
    assert t.iloc[0].fill_price==8.5 and t.iloc[0].stop==5.95
    assert t.iloc[0].exit_date=='2026-05-07'
    assert t.iloc[0].exit_price==5.4 and t.iloc[0].reason=='stop'
    assert a['locked_fill_attempts']>0


def test_native_intrabar_stop_price():
    f=tape([
        ['2026-05-05 10:30',10,10,9,9.1,8,12],
        ['2026-05-05 15:00',9.1,9.2,9,9.1,8,12],
        ['2026-05-06 10:30',7,7.1,6,6.2,5.9,10],
        ['2026-05-06 15:00',6.2,6.3,6.1,6.2,5.9,10],
    ])
    t,_,_=run_native(f,plan(),CFG,'TEST')
    assert t.iloc[0].exit_price==6.3
    assert t.iloc[0].reason=='stop'


def test_terminal_position_is_unrealized_and_cash_lot_rounding():
    f=tape([
        ['2026-05-05 10:30',10,10,9,9.2,8,12],
        ['2026-05-05 15:00',9.2,9.5,9,9.3,8,12],
    ])
    t,fills,_=run_native(f,plan(),CFG,'TEST')
    assert t.iloc[0].reason=='open_at_end' and not t.iloc[0].executed
    assert len(fills)==1 and t.iloc[0].fee==5.1


def test_locked_up_entry_is_not_filled_and_unfilled_expires():
    f=tape([
        ['2026-05-05 10:30',9,9,9,9,7,9],
        ['2026-05-05 15:00',9,9,9,9,7,9],
        ['2026-05-11 10:30',8,8.5,7.8,8.2,7,10],
    ])
    t,fills,a=run_native(f,plan(),CFG,'TEST')
    assert t.empty and fills.empty and a['locked_fill_attempts']>0


def test_triggered_stop_survives_daily_cancel_even_if_open_recovers():
    f=tape([
        ['2026-05-05 10:30',8.5,9,8,8.4,7,11],
        ['2026-05-05 15:00',8.4,8.4,8,8,7,11],
        ['2026-05-06 10:30',5.6,5.6,5.6,5.6,5.6,8],
        ['2026-05-06 15:00',5.6,5.6,5.6,5.6,5.6,8],
        ['2026-05-07 10:30',6.1,6.12,6.0,6.05,5.04,6.16],
        ['2026-05-07 15:00',6.05,6.1,6.0,6.05,5.04,6.16],
    ])
    t,_,_=run_native(f,plan(12),CFG,'TEST')
    assert t.iloc[0].reason=='stop' and t.iloc[0].exit_price==6.1
