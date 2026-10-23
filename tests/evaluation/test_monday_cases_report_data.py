"""Report arithmetic preserves censored rows and separates cost bases."""
import pandas as pd
from yoyo.evaluation import monday_cases_report_data as m


def test_closed_trade_denominator_and_gross_basis():
    f=pd.DataFrame({'closed':[True,True,False],'gross_r':[2.,-1.,999.], 'basis_r':[1.9,-1.1,999.], 'gross_bp':[200.,-100.,999.], 'basis_bp':[190.,-110.,999.], 'symbol':['BTCUSDT']*3,'reason':[4,1,0], 'entry_dt':pd.to_datetime(['2020-01-01','2020-01-02','2020-01-03'],utc=True),'exit_dt':pd.to_datetime(['2020-01-01','2020-01-02','2020-01-03'],utc=True),'fee_r':[.1,.1,.1],'holding_h':[1.,2.,3.],'risk_pct':[1.,1.,1.],'planned_rr':[2.,2.,2.],'ambiguous_bar':[False,False,False]})
    a=m.stats(f);assert a['n']==2 and a['censored']==1 and a['win_pct']==50
    assert abs(a['mean_r']-.4)<1e-12 and abs(a['ledger_max_drawdown_r']-1.1)<1e-12
    b=m.stats(f,True);assert b['mean_r']==.5 and b['mean_fee_r'] is None
