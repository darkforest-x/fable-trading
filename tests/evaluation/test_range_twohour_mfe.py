"""Validate two-hour deadlines and three-tranche native accounting."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.range_twohour_mfe import mix_ledgers
from yoyo.evaluation.btc_range_wick_review import excursions
from yoyo.evaluation.range_v230_exit_compare import make_legs, aggregate_legs, oracle
from yoyo.evaluation.pepe_range_breakeven import native
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


@pytest.mark.parametrize('side',[1,-1])
@pytest.mark.parametrize('stop_remaining',[True,False])
def test_three_tranches_match_native_orders_and_exact_120m_deadline(side,stop_remaining):
    bars=np.array([[100,150,50,100],[100,101.2,99.5,101],[101,103.2,100.2,103]]+
        [[103,104,102,103]]*21+[[103,104,98 if stop_remaining else 102,102.5]]+
        [[102.5,150,50,140]],dtype=float)
    if side==-1:bars=200-bars[:,[0,2,1,3]]
    frame=pd.DataFrame(bars,columns=['open','high','low','close'],
        index=pd.date_range('2026-01-01',periods=len(bars),freq='5min',tz='UTC'))
    base=pd.DataFrame([dict(trade_id='s',entry_id='s',pair_id=0,population='signal',
        entry_i=0,entry=100.,side=side,stop=100.-side,risk=1.,end_i=24)])
    legs=[]
    for i,(target,w) in enumerate(zip([1,3,5],[.25,.25,.5])):
        one=make_legs(base,dict(target_r=target),{'s':[]},.01)
        one['trade_id']=f's_leg{i}';one['weight']=w;one['leg']=i;legs.append(one)
    legs=pd.concat(legs,ignore_index=True)
    ctx=dict(bars=np.ascontiguousarray(bars),atr=np.zeros(len(bars)),hour=np.zeros(len(bars)))
    rows,fills,_=run_native(frame,ctx['atr'],ctx['hour'],legs,
        ExitPolicy(target_from_entry=True,opposite_hour_exit=False),tick=.01,symbol='TEST')
    _,check=oracle(ctx,legs,{},.01,.002,native(),rows)
    assert check['native_price_time_parity'] and len(fills)==6
    assert sorted(rows.exit_i)==[1,2,24]
    assert frame.index[24]-frame.index[0]==pd.Timedelta(hours=2)
    direct=aggregate_legs(rows,base,.002).iloc[0]
    assert direct.gross_r==pytest.approx(.5 if stop_remaining else 2.25)
    assert direct.fee_r==pytest.approx(.2)
    elementary={}
    for target,i in zip([1,3,5],range(3)):
        one=rows[rows.leg==i].copy();one['weight']=1.
        elementary[f'fixed_{target}r']=aggregate_legs(one,base,.002)
    mixed=mix_ledgers(elementary,dict(name='three',targets=[1,3,5],weights=[.25,.25,.5])).iloc[0]
    for col in ['gross_r','fee_r','net_r','net_notional_bp','exit_i','boundary_weight']:
        assert mixed[col]==pytest.approx(direct[col])


def test_mfe_excludes_entry_and_stop_bar_and_caps_deadline():
    bars=np.array([[100,200,1,100],[100,102,99.5,101],[101,110,98,99],[99,150,98,140]],float)
    row=pd.DataFrame([dict(trade_id='s',entry_i=0,end_i=2,side=1,entry=100.,stop=99.,risk=1.)])
    result=excursions(bars,row).iloc[0]
    assert result.mfe_r==2 and result.mfe_upper_r==10 and result.stop_i==2
