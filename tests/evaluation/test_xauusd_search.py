"""End-to-end synthetic entry through controls and report row assembly."""
import pandas as pd
from yoyo.evaluation.xauusd_execution import simulate
from yoyo.evaluation.xauusd_controls import matched_controls
from yoyo.evaluation.xauusd_search import merge_summary,audit_ledger,gap_audit


def test_trade_controls_summary_cost_contract():
    ix=pd.date_range('2024-01-02',periods=15,freq='min',tz='UTC')
    m=pd.DataFrame({'open':100.,'high':102.,'low':99.,'close':101.},index=ix)
    b=m.copy();b['time_close']=b.index+pd.Timedelta(minutes=1)
    f=pd.DataFrame({'ready':True,'volbin':2,'md':1.,'atr':1.},index=ix)
    entry=[1]+[0]*14;xl=xs=[False]*15
    r=simulate(m,b,entry,xl,xs,'2024-01-02','2024-01-03')
    c=matched_controls(m,b,f,entry,xl,xs,r['ledger'],'2024-01-02','2024-01-03')
    merged=merge_summary('C00',5,r['stats'],c['summary'])
    assert merged['cost_bp']==20 and merged['trades']==1 and merged['matched_n']==1
    assert audit_ledger(m,r['ledger'],'2024-01-02','2024-01-03')==11


def test_sunday_reopening_is_possible_weekend_only():
    m=pd.DataFrame(index=pd.to_datetime(['2024-01-05T22:00Z','2024-01-07T23:00Z']))
    assert gap_audit(m).possible_weekend.tolist()==[True]
