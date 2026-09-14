"""Synthetic financial-state checks, independent of historical prices."""
import pytest
from yoyo.evaluation.spike_recovery_cash import simulate


def trade(i, r=-1., **kw):
    return dict(signal_i=i*3, entry_i=i*3+1, exit_i=i*3+2, exit_at_open=False,
                entry_time=f'2024-01-{i+1:02d}T00:00:00Z', exit_time=f'2024-01-{i+1:02d}T00:03:00Z',
                side=1, initial_risk_frac=.01, net_r=r, net_return=.01*r,
                gross_r=kw.pop('gross_r',r+.2), exit_reason=kw.pop('exit_reason','initial_stop'),
                censored=False, be_armed=kw.pop('be_armed',False), **kw)


@pytest.mark.parametrize('schedule,expected',[('debt',[1,2,6,18]),('factorial',[1,2,6,24]),('double',[1,2,4,8])])
def test_schedule_arithmetic(schedule,expected):
    out=simulate([trade(i) for i in range(4)],schedule=schedule,leverage_cap=100)
    assert [r['risk'] for r in out['ledger']]==expected
    assert out['summary']['final_balance']==1000-sum(expected)
    assert sum(c['net_pnl'] for c in out['cycles'])==-sum(expected)


def test_entry_price_protection_is_still_a_net_loss():
    rows=[trade(0),trade(1,-.2,gross_r=0,be_armed=True,exit_reason='trailing_stop'),trade(2)]
    recovery=simulate(rows,reset_mode='recovery')
    reset=simulate(rows,reset_mode='be_or_win')
    assert [r['risk'] for r in recovery['ledger']]==[1,2,4]
    assert [r['risk'] for r in reset['ledger']]==[1,2,1]
    assert reset['cycles'][0]['net_pnl']==pytest.approx(-1.4)
    assert reset['cycles'][0]['recovered'] is False


def test_cycle_limit_does_not_reset_account_loss_streak():
    out=simulate([trade(i) for i in range(8)],schedule='double',leverage_cap=100)
    assert [r['risk'] for r in out['ledger']]==[1,2,4,8,16,32,1,2]
    assert out['summary']['max_consecutive_net_loss']==8
    assert out['summary']['capped_cycles']==1


def test_capacity_rejection_restarts_without_peeking_or_shadow_occupancy():
    rows=[trade(0),trade(1),trade(2)]
    rows[1]['initial_risk_frac']=.00001
    rows[1]['net_r']=float('nan')
    rows[1]['exit_i']=999999
    out=simulate(rows)
    assert out['summary']['n_rejected_capacity']==1
    assert out['summary']['n_accepted']==2
    assert out['ledger'][-1]['risk']==1
    assert out['summary']['final_balance']==998


def test_small_win_does_not_equal_cycle_recovery():
    rows=[trade(0),trade(1,.1),trade(2)]
    assert simulate(rows,reset_mode='net_win')['ledger'][-1]['risk']==1
    assert simulate(rows,reset_mode='recovery')['ledger'][-1]['risk']==2


def test_open_reverse_can_enter_same_open_but_intrabar_exit_cannot():
    first=trade(0);first['exit_i']=4
    other=trade(1);other['side']=-1
    assert simulate([first,other])['summary']['n_accepted']==1
    first['exit_at_open']=True
    assert simulate([first,other])['summary']['n_accepted']==2


def test_blocked_candidate_outcome_does_not_matter():
    first=trade(0);first['exit_i']=10
    other=trade(1);other['net_r']=float('nan')
    assert simulate([first,other])['summary']['final_balance']==999


def test_zero_is_absorbing_and_recording_does_not_change_summary():
    rows=[trade(0),trade(1)]
    assert simulate(rows,initial_balance=0)['summary']['final_balance']==0
    assert simulate(rows)['summary']==simulate(rows,record_ledger=False)['summary']


def test_boundary_mark_is_separate_from_natural_cash_and_cannot_recover_cycle():
    boundary = trade(1, 3.)
    boundary['censored'] = True
    boundary['exit_reason'] = 'boundary_mark'
    out = simulate([trade(0), boundary])
    assert out['summary']['final_balance'] == 1005
    assert out['summary']['final_cash_excluding_boundary'] == 999
    assert out['summary']['n_natural'] == 1
    assert out['summary']['recovered_cycles'] == 0
    assert out['cycles'][-1]['finish_reason'] == 'boundary'
