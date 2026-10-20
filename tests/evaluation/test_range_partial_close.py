"""Real Nautilus partial reductions: timing, runner protection and weighted PnL."""
import numpy as np
import pandas as pd
import pytest
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, side=1, atr=None, hour=None, trigger=1., fraction=.5):
    bars = np.asarray(bars, dtype=float)
    if side == -1:
        bars = 200-bars
        bars[:, [1, 2]] = bars[:, [2, 1]]
    frame = pd.DataFrame(bars, columns=['open','high','low','close'],
        index=pd.date_range('2026-01-01T00:00Z', periods=len(bars), freq='5min'))
    entries = pd.DataFrame([dict(trade_id='partial',entry_i=0,side=side,
        entry=100.,stop=100.-side,risk=1.,end_i=len(bars)-1)])
    return run_native(frame, np.full(len(bars),np.nan) if atr is None else atr,
        np.zeros(len(bars)) if hour is None else hour, entries,
        ExitPolicy(partial_close_r=trigger, partial_fraction=fraction),tick=.01,symbol='TEST')


@pytest.mark.parametrize('side',[1,-1])
@pytest.mark.parametrize('fraction',[.25,.5])
def test_partial_then_original_stop_is_weighted_not_full_runner_return(side,fraction):
    rows,fills,audit=replay([[100]*4,[100,103,99.5,102],[102,103,98,100]],side=side,fraction=fraction)
    r=rows.iloc[0]
    assert r.partial_i==1 and r.exit_i==2 and r.reason==1
    assert r.partial_weight==fraction
    assert r.protection==100-side and r.arm_i==-1
    assert r.gross_r==pytest.approx(fraction*2-(1-fraction))
    assert r.net_r==pytest.approx(r.gross_r-.2)
    assert r.framework_commission==pytest.approx(.002*r.entry*r.quantity)
    assert fills.phase.tolist()==['entry','partial','stop']
    assert fills.quantity.iloc[1:].sum()==fills.quantity.iloc[0]
    assert fills.position_id.nunique()==1
    assert audit['positions']==1


def test_wick_touch_and_entry_wick_do_not_execute_partial():
    rows,fills,_=replay([[100,130,90,100],[100,102,99.5,100.5],[100.5,101,99.5,100.5]])
    assert rows.iloc[0].partial_i==-1 and len(fills)==2


def test_stop_wins_before_close_recovery_and_partial():
    rows,fills,_=replay([[100]*4,[100,110,98,106],[106]*4])
    assert rows.iloc[0].reason==1 and rows.iloc[0].partial_i==-1
    assert rows.iloc[0].peak_r==0 and len(fills)==2


def test_partial_does_not_arm_trailing_or_hourly_reverse_exit():
    rows,fills,_=replay([[100]*4,[100,103,99.5,102],[102,103,101,102],[102]*4],
        atr=[np.nan,np.nan,4.,np.nan],hour=[0,0,-1,0])
    assert rows.iloc[0].partial_i==1 and rows.iloc[0].exit_i==3
    assert rows.iloc[0].reason==0 and rows.iloc[0].arm_i==-1
    assert len(fills)==3


def test_partial_and_trailing_same_close_use_resized_next_bar_stop():
    rows,fills,_=replay([[100]*4,[100,107,99.5,106],[106,107,103,105],[105]*4],
        atr=[np.nan,.5,np.nan,np.nan],trigger=5.)
    r=rows.iloc[0]
    assert r.partial_i==r.arm_i==1 and r.protection==104
    assert r.exit_i==2 and r.reason==2 and r.gross_r==5
    assert fills.quantity.tolist()==[10,5,5]


def test_reverse_or_boundary_full_exit_precedes_redundant_partial():
    rows,fills,_=replay([[100]*4,[100,107,99.5,106],[106]*4],
        atr=[np.nan,4.,np.nan],hour=[0,-1,0],trigger=5.)
    assert rows.iloc[0].reason==3 and rows.iloc[0].partial_i==-1 and len(fills)==2
    rows,fills,_=replay([[100]*4,[100,103,99.5,102]])
    assert rows.iloc[0].reason==0 and rows.iloc[0].partial_i==-1 and len(fills)==2


def test_repeated_closes_reduce_only_once_and_preserve_gap_loss():
    rows,fills,_=replay([[100]*4,[100,103,99.5,102],[102,104,101,103],[97,100,96,99]])
    r=rows.iloc[0]
    assert r.partial_i==1 and len(fills)==3 and r.exit_price==97
    assert r.gross_r==-.5


@pytest.mark.parametrize('kwargs',[dict(partial_close_r=0),dict(partial_close_r=float('nan')),
    dict(partial_fraction=0),dict(partial_fraction=1),dict(partial_fraction=float('nan'))])
def test_invalid_partial_config_fails(kwargs):
    with pytest.raises(ValueError): ExitPolicy(**kwargs)


def test_study_oracle_uses_first_close_excludes_exit_bar_and_weights_initial_risk():
    from yoyo.evaluation.range_partial_study import close_partial_oracle,drawdown
    ledger=pd.DataFrame([dict(entry_i=0,exit_i=3,side=1,entry=100,risk=1,gross_r=-1)])
    expected=close_partial_oracle(ledger,np.array([100,100.5,102,99]),1,.25,.01)
    assert expected.iloc[0].partial_i==2 and expected.iloc[0].gross_r==-.25
    terminal=close_partial_oracle(ledger,np.array([100,100.5,100.5,105]),1,.25,.01)
    assert terminal.iloc[0].partial_i==-1
    assert drawdown([-.1,2,-3,1])==3


def test_cash_accounting_includes_entry_fee_and_all_exit_shares():
    from yoyo.evaluation.range_partial_study import cash_events
    rows=pd.DataFrame([dict(trade_id='p',segment=0,population='signal',entry=100.,risk=1.,side=1,quantity=4.)])
    fills=pd.DataFrame([dict(trade_id='p',ts_ns=i,phase=phase,price=price,quantity=q,commission=fee)
        for i,phase,price,q,fee in [(1,'entry',100.,4.,.4),(2,'partial',102.,1.,.1),(3,'stop',99.,3.,.3)]])
    events=cash_events(rows,fills)
    np.testing.assert_allclose(events.cash_r,[-.1,.475,-.825])
    assert events.cash_r.sum()==pytest.approx(-.45)
    assert events.cash_bp.sum()==pytest.approx(-45)
