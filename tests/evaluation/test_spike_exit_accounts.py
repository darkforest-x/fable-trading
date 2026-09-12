import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_exit_accounts import marked_account,period_summary


def fixture(side=1,partial=False):
    ix=pd.date_range('2024-09-10',periods=4,freq='h',tz='UTC')
    bars=pd.DataFrame({'open':[100,102,103,104],'close':[101,102,104,104]},index=ix)
    trade=pd.DataFrame([dict(trade_id='a',entry_time=ix[0],entry_price=100,initial_risk=2,side=side)])
    if partial:
        prices=[102,104];fracs=[.25,.75];idx=[1,3]
    else:prices=[104];fracs=[1.0];idx=[3]
    fills=pd.DataFrame([dict(trade_id='a',kind='exit' if j==len(idx)-1 else 'partial',bar_open=ix[i],execution_phase='open',qty_fraction=f,price=p) for j,(i,f,p) in enumerate(zip(idx,fracs,prices))])
    return bars,trade,fills


def test_partial_costs_and_frozen_qty():
    b,t,f=fixture(partial=True)
    path,a,m=marked_account(b,t,f,risk_fraction=.01,notional_cap=None)
    assert a.quantity.iloc[0]==50
    assert path.equity.iloc[-1]==pytest.approx(10165)
    assert path.equity.iloc[0]==pytest.approx(10040)
    assert not m['invalid']


def test_capped_exposure_and_short_direction():
    b,t,f=fixture(side=-1)
    path,a,m=marked_account(b,t,f,risk_fraction=.1)
    assert a.entry_notional_ratio.iloc[0]<1
    assert a.effective_gross_stop_risk.iloc[0]<.1
    assert path.equity.iloc[-1]==pytest.approx(10000-(10000/100.1)*4.2)


def test_no_trades_idle_and_invalid_gap():
    b,t,f=fixture()
    path,a,m=marked_account(b,t.iloc[:0],f.iloc[:0],risk_fraction=.03)
    assert path.equity.eq(10000).all()
    f.loc[0,'price']=np.nan
    _,_,m=marked_account(b,t,f,risk_fraction=.03)
    assert m['invalid']


def test_quantity_overfill_rejected():
    b,t,f=fixture();f.loc[0,'qty_fraction']=1.2
    with pytest.raises(ValueError,match='quantity'):marked_account(b,t,f,risk_fraction=.03)


def test_marked_drawdown_precedes_exit():
    b,t,f=fixture();b.loc[b.index[1],'close']=80
    path,a,m=marked_account(b,t,f,risk_fraction=.1,notional_cap=None)
    assert m['ruined'] and path.equity.iloc[-1]==0


def test_period_return_keeps_inherited_equity():
    p=pd.DataFrame({'time':pd.to_datetime(['2025-09-09T23:00Z','2025-09-10T00:00Z','2026-09-10T00:00Z']),'equity':[12000,13000,15600]})
    rows=period_summary(p)
    assert rows[2]['opening_equity']==13000
    assert rows[2]['net_return']==pytest.approx(.2)


def test_next_open_reversal_close_belongs_to_new_trade():
    b,t,f=fixture();f.loc[0,'bar_open']=b.index[1];f.loc[0,'price']=102
    t2=t.iloc[0].to_dict();t2.update(trade_id='b',entry_time=b.index[1],entry_price=102,side=-1)
    t=pd.concat([t,pd.DataFrame([t2])],ignore_index=True)
    f2=f.iloc[0].to_dict();f2.update(trade_id='b',bar_open=b.index[3],price=104)
    f=pd.concat([f,pd.DataFrame([f2])],ignore_index=True)
    p,a,m=marked_account(b,t,f,risk_fraction=.01,notional_cap=None)
    assert len(a)==2
    assert p.equity.iloc[1]==pytest.approx(a.entry_equity.iloc[1]-a.quantity.iloc[1]*102*.002)
