"""Exercise retrospective matching clocks independent of market outcomes."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import range_v236_charts as m


def fixture(monkeypatch, fpos, qpos):
    index=pd.date_range('2026-10-01',periods=72,freq='5min',tz='UTC')+pd.Timedelta(minutes=5)
    frame=pd.DataFrame(dict(open=100.,high=102.,low=98.,close=101.),index=index)
    def arrows(f):
        a=np.zeros(len(f),int)
        for i,s in (fpos if len(f)==72 else qpos):a[i]=s
        return pd.Series(a,index=f.index)
    monkeypatch.setattr(m,'signals',arrows)
    return frame


@pytest.mark.parametrize('offset,accepted',[(-6,False),(-5,True),(0,True),(5,True),(6,False)])
def test_open_clock_boundaries(monkeypatch,offset,accepted):
    frame=fixture(monkeypatch,[(30+offset,1)],[(10,1)])
    rows=m.matched_events(frame,frame.index[0],frame.index[-1],.1)
    assert len(rows)==int(accepted)
    if accepted:
        r=rows.iloc[0]
        assert r.offset_bars==offset and r.entry_i==30+offset
        assert pd.Timestamp(r.publication_time)==max(frame.index[30+offset],frame.index[32])


def test_all_arrows_one_match_each_earliest_quarter(monkeypatch):
    frame=fixture(monkeypatch,[(30,-1),(31,-1),(32,1)],[(10,-1),(11,-1)])
    rows=m.matched_events(frame,frame.index[0],frame.index[-1],.1)
    assert rows.entry_i.tolist()==[30,31]
    assert rows.quarter_time.nunique()==1
    assert rows.entry.tolist()==[101.,101.]
    assert np.allclose(rows.stop,102.1)
