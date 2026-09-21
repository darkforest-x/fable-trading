import pandas as pd
from yoyo.data.ma_snapshot_inputs import bounded_local,aggregate,canonical

def test_future_poison_and_unclosed_bar_not_read(tmp_path):
    p=tmp_path/'x.csv';p.write_text('ts,open,high,low,close,volume\n0,1,2,1,2,3\n1800000,POISON,POISON,POISON,POISON,POISON\n')
    f,a=bounded_local(p,30,1800000,0)
    assert len(f)==1 and a['future_ohlcv_converted']==0

def test_four_hour_bin_requires_complete_and_closed():
    f=canonical([[i*1800000,1,2,1,2,3] for i in range(12)])
    g=aggregate(f,30,240,6*3600000)
    assert len(g)==1 and g.ts.iloc[0]==0
    assert aggregate(f.drop(index=3),30,240,6*3600000).empty
