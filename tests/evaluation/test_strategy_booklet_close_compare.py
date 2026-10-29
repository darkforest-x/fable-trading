"""Admission is decided at the completed touch close, before later prices."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.strategy_booklet_close_compare import construct


def fixture(side, close):
    event=f'TESTUSDT:2026-01-05:{side:+d}'
    row=dict(event=event,symbol='TESTUSDT',score=.65)
    tables={k:pd.DataFrame([row]) for k in ('60','5','v3','v3lock')}
    orders=[]
    for m,mode in [(60,'near_monday_edge'),(5,'near_monday_edge'),(60,'opposite_1618')]:
        orders.append(dict(trade_id=f'{event}:m{m}:{mode}:c0',population='signal',
            touch_i_hint=2,side=side,entry=100.,target_price=100.+side*20,
            end_i=7,expire_i=7,reclaim_minutes=m))
    p=pd.DataFrame(dict(close=[100.,100.,close,99.,100.,101.,102.,103.]))
    return p,pd.DataFrame(orders),tables


@pytest.mark.parametrize('side',[-1,1])
def test_completed_close_uses_market_and_original_level(side):
    p,original,tables=fixture(side,100.-side*2)
    orders,_=construct(p,original,.01,tables,'TESTUSDT')
    parents=orders[orders.kind.ne('add0')]
    assert parents.order_mode.eq('market').all()
    assert parents.entry.eq(p.close.iloc[2]).all()
    assert parents.reclaim_ref.eq(100.).all()
    assert parents.entry_i.eq(2).all()
    assert orders.loc[orders.kind.eq('5'),'be_offset_bp'].eq(0).all()
    assert orders.loc[orders.kind.eq('5'),'stop'].isna().all()


@pytest.mark.parametrize('side',[-1,1])
def test_target_is_skipped_at_decision(side):
    p,original,tables=fixture(side,100.+side*20)
    orders,decisions=construct(p,original,.01,tables,'TESTUSDT')
    assert orders.empty
    assert decisions.status.eq('past_target_at_close').all()


@pytest.mark.parametrize('side',[-1,1])
def test_future_prices_cannot_change_admission_or_child(side):
    p,original,tables=fixture(side,100.+side*2)
    a,da=construct(p,original,.01,tables,'TESTUSDT')
    p.loc[3:,'close']=np.arange(5)*10000.
    b,db=construct(p,original,.01,tables,'TESTUSDT')
    pd.testing.assert_frame_equal(a,b)
    pd.testing.assert_frame_equal(da,db)
    child=a[a.kind.eq('add0')].iloc[0]
    parent=a[a.trade_id.eq(child.parent)].iloc[0]
    assert child.entry==100.-side*5
    assert child.reclaim_ref==parent.reclaim_ref
    assert child.be_offset_bp==parent.be_offset_bp==0
