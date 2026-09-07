"""Carry reconstruction is checked against the execution ledger daily marks."""
import numpy as np
import pandas as pd
from yoyo.evaluation.xauusd_execution import simulate
from yoyo.evaluation.xauusd_carry_audit import reconstruct_marks


def test_carry_marks_account_for_exit_and_reverse_and_boundary_fee():
    idx=pd.date_range('2024-01-01',periods=9,freq='min',tz='UTC')
    price=np.array([100.,100.,103.,102.,99.,101.,100.,98.,102.])
    m=pd.DataFrame({'open':price,'high':price+2,'low':price-2,'close':price+1},index=idx)
    b=m.copy();b['time_close']=idx+pd.Timedelta(minutes=1)
    entry=[1,0,0,-1,0,0,0,0,0]
    xl=[False,False,False,True,False,False,False,False,False]
    r=simulate(m,b,entry,xl,[False]*9,'2024-01-01','2024-01-02')
    mark=reconstruct_marks(m,r['ledger'],'2024-01-01','2024-01-02')
    np.testing.assert_allclose(mark.iloc[-1],r['stats']['final_equity'])
    x=np.r_[1,mark.to_numpy()]
    np.testing.assert_allclose((1-x/np.maximum.accumulate(x)).max()*100,r['stats']['max_drawdown_pct'])
