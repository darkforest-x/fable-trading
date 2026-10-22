"""Behavioral tests for native-fill hedged martingale account accounting."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_martingale_account import event_tape, replay

TIERS = [dict(maxSz='1000', imr='.01', mmr='.004'),
         dict(maxSz='5000', imr='.015', mmr='.005'),
         dict(maxSz='20000', imr='.02', mmr='.0075')]


def trade(name, entry=100., risk=1., side=1):
    return dict(trade_id=name,entry=entry,risk=risk,side=side)


def test_every_realized_loss_doubles_and_small_win_resets():
    a,b,c = [trade(x) for x in 'abc']
    tape=[('entry',0,100.,a),('exit',1,99.5,a),
          ('entry',2,100.,b),('exit',3,100.1,b),('entry',4,100.,c),('exit',5,100.,c)]
    s,l,_=replay(tape,[1.],TIERS,record=True)
    assert l[l.action=='entry'].quantity.tolist()==[1.,2.,1.]
    assert s.ending_equity.iloc[0]==pytest.approx(999.7)


def test_overlaps_size_only_from_already_realized_exits():
    a,b,c=[trade(x) for x in 'abc']
    tape=[('entry',0,100.,a),('entry',1,100.,b),('exit',2,99.,a),
          ('entry',3,100.,c),('exit',4,101.,b),('exit',5,99.,c)]
    s,l,_=replay(tape,[2.],TIERS,record=True)
    assert l[l.action=='entry'].quantity.tolist()==[2.,2.,4.]
    assert s.ending_equity.iloc[0]==pytest.approx(996.)


def test_rejected_entries_do_not_reset_or_advance():
    a,b,c=trade('a'),trade('b',risk=.0001),trade('c')
    tape=[('entry',0,100.,a),('exit',1,99.,a),('entry',2,100.,b),
          ('exit',3,120.,b),('entry',4,100.,c),('exit',5,101.,c)]
    s,l,_=replay(tape,[1.],TIERS,record=True)
    assert l[l.action=='entry'].quantity.tolist()==[1.,0.,2.]
    assert s.rejected.iloc[0]==1
    assert s.ending_equity.iloc[0]==1001.


def test_intrabar_maintenance_breach_cannot_be_cured_by_close():
    a=trade('a')
    tape=[('entry',0,100.,a),('mark',1,90.,None),('exit',2,110.,a)]
    s,_,_=replay(tape,[100.],TIERS)
    assert s.liquidated.iloc[0]
    assert np.isnan(s.ending_equity.iloc[0])
    assert s.first_breach_i.iloc[0]==1


def test_tier_initial_margin_and_lot_round_down():
    a=trade('a')
    tape=[('entry',0,100.,a),('exit',1,101.,a)]
    s,l,_=replay(tape,[10.00009],TIERS,record=True)
    assert l.iloc[0].quantity==10.
    assert l.iloc[0].initial_margin==10.
    _,l,_=replay(tape,[10.00011],TIERS,record=True)
    assert l.iloc[0].initial_margin==pytest.approx(15.00015)


def test_adverse_mark_basis_can_fail_a_proxy_survivor():
    a=trade('a')
    tape=[('entry',0,100.,a),('exit',1,100.1,a)]
    plain,_,_=replay(tape,[9.],TIERS,initial_cash=10.)
    stress,_,_=replay(tape,[9.],TIERS,initial_cash=10.,mark_stress=.01)
    assert not plain.liquidated.iloc[0]
    assert stress.liquidated.iloc[0]


def test_vectorized_matches_scalar_and_constant_risk_identity():
    a,b=trade('a'),trade('b',side=-1)
    tape=[('entry',0,100.,a),('exit',1,99.,a),('entry',2,100.,b),('exit',3,98.,b)]
    many,_,_=replay(tape,[1.,2.,3.],TIERS)
    for i,base in enumerate([1.,2.,3.]):
        one,_,_=replay(tape,[base],TIERS)
        pd.testing.assert_series_equal(many.iloc[i],one.iloc[0],check_names=False)
    fixed,_,_=replay(tape,[1.,2.,3.],TIERS,multiplier=1.)
    np.testing.assert_allclose(fixed.ending_equity,[1001.,1002.,1003.])


def test_tape_enters_at_close_and_stops_before_later_low():
    f=pd.DataFrame([[100,120,80,100],[100,110,90,105]],columns=['open','high','low','close'])
    t=pd.DataFrame([dict(trade_id='a',entry_i=0,exit_i=1,side=1,entry=100.,
                        stop=95.,risk=5.,exit_price=95.,reason=1,gross_r=-1.)])
    tape=event_tape(f,t)
    assert tape[0][0]=='entry'
    assert [e[2] for e in tape if e[0]=='mark']==[100.,100.,110.,95.]
    s,_,_=replay(tape,[5.],TIERS)
    assert s.ending_equity.iloc[0]==995.


def test_tape_rejects_invented_or_backdated_fill():
    f=pd.DataFrame([[100,101,99,100],[100,101,96,100]],columns=['open','high','low','close'])
    t=pd.DataFrame([dict(trade_id='a',entry_i=0,exit_i=1,side=1,entry=100.,
                        stop=95.,risk=5.,exit_price=95.,reason=1,gross_r=-1.)])
    with pytest.raises(ValueError,match='did not cross'):
        event_tape(f,t)


def test_global_hedge_margin_is_gross_not_unlimited_netting():
    a,b=trade('a'),trade('b',side=-1)
    tape=[('entry',0,100.,a),('entry',1,100.,b),('exit',2,100.,a),('exit',3,100.,b)]
    s,_,_=replay(tape,[9.],TIERS,initial_cash=20.)
    # 18 BTC crosses into tier 2: 27 U margin, so the second leg is rejected.
    assert s.accepted.iloc[0]==1
    assert s.rejected.iloc[0]==1
