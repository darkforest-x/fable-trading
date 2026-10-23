"""Native sibling fills and actual-notional fee protection for Monday TP1."""
import numpy as np
import pandas as pd
import pytest
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, side=1, target1=1., be=True, cost=True):
    f=pd.DataFrame(bars,columns=['open','high','low','close'],
                   index=pd.date_range('2026-01-06T00:00Z',periods=len(bars),freq='5min'))
    entries=pd.DataFrame([dict(trade_id=f'p-{j}',parent_id='p',entry_i=0,
        entry=100.,stop=100-side,risk=1.,side=side,target_price=100+side*t,
        end_i=len(f)-1) for j,t in enumerate([target1,4.])])
    return run_native(f,np.full(len(f),np.nan),np.zeros(len(f)),entries,
        ExitPolicy(target_from_entry=True,target_breakeven=be,opposite_hour_exit=False,
                   round_trip_cost=0,fill_maker_fee=.0002 if cost else 0.,
                   fill_taker_fee=.0005 if cost else 0.),tick=.000001,symbol='TEST')


@pytest.mark.parametrize('side',[1,-1])
def test_tp1_fill_protects_remainder_same_candle_and_covers_actual_fees(side):
    b=[100,102,99.5,100] if side==1 else [100,100.5,98,100]
    rows,fills,audit=replay([[100]*4,b],side)
    a=rows.set_index('trade_id');assert a.loc['p-0','reason']==4
    assert a.loc['p-1','reason']==5
    assert -1e-10<=a.loc['p-1','net_r']<.000002
    assert a.loc['p-1','be_effective_i']==a.loc['p-0','exit_i']==1
    for r in rows.itertuples():
        ff=fills[fills.trade_id.eq(r.trade_id)]
        rate=np.where(ff.phase.eq('target'),.0002,.0005)
        assert (ff.price*ff.quantity*rate).sum()==pytest.approx(r.framework_commission)
        assert r.gross_r-r.fee_r==pytest.approx(r.net_r)
    assert audit['positions']==2 and len(fills)==4


def test_no_be_remainder_keeps_original_stop():
    rows,_,_=replay([[100]*4,[100,102,98,100]],be=False)
    r=rows.set_index('trade_id');assert r.loc['p-0','reason']==4
    assert r.loc['p-1','reason']==1 and r.loc['p-1','exit_price']==99


def test_short_stop_happens_before_later_tp1_low():
    rows,_,_=replay([[100]*4,[100,102,98,100]],side=-1)
    assert rows.reason.eq(1).all() and rows.be_trigger_i.eq(-1).all()


@pytest.mark.parametrize('side',[1,-1])
def test_open_gap_crosses_both_targets_before_reversal(side):
    b=[105,106,98,100] if side==1 else [95,102,94,100]
    rows,_,_=replay([[100]*4,b],side)
    assert rows.reason.eq(4).all()


def test_tiny_tp1_does_not_reject_or_install_an_impossible_stop():
    rows,_,_=replay([[100]*4,[100,100.05,99.5,100.04],
        [100.04,100.2,100.03,100.15],[100.15,100.2,99.5,100]],target1=.02)
    r=rows.set_index('trade_id').loc['p-1']
    assert r.be_trigger_i==1 and r.be_effective_i==2 and r.reason==5
    assert r.deferred_closes>0 and r.net_r>=-1e-10


def test_deferred_fee_be_does_not_rescue_an_original_stop_first():
    rows,_,_=replay([[100]*4,[100,100.05,98,100]],target1=.02)
    r=rows.set_index('trade_id').loc['p-1']
    assert r.reason==1 and r.be_trigger_i==1 and r.be_effective_i==-1


def test_stop_opening_gap_is_not_guaranteed_fee_breakeven():
    rows,_,_=replay([[100]*4,[101.5,102,101.2,101.5],[99,100,98,99]])
    r=rows.set_index('trade_id').loc['p-1']
    assert r.reason==5 and r.exit_price==99 and r.net_r<0


def test_gold_has_price_be_only_and_unresolved_runner_is_retained():
    rows,_,_=replay([[100]*4,[101.5,102,101.2,101.5]],cost=False)
    r=rows.set_index('trade_id').loc['p-1']
    assert r.reason==0 and r.be_price==100 and r.fee_r==0


@pytest.mark.parametrize('kw',[dict(fill_maker_fee=.0002),
    dict(fill_maker_fee=-1,fill_taker_fee=.0005,round_trip_cost=0),
    dict(fill_maker_fee=.0002,fill_taker_fee=.0005)])
def test_invalid_fee_policy_fails(kw):
    with pytest.raises(ValueError):ExitPolicy(**kw)
