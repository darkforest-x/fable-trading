"""Version-comparison contracts: frozen pairings and shared control times."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_v221_compare import wick_bridge,controls,candidate_rows


@pytest.mark.parametrize("side",[1,-1])
def test_wick_bridge_keeps_pair_and_entry_but_removes_buffer(side):
    q=pd.DataFrame(dict(open=[100.],high=[102.],low=[98.],close=[100.]))
    source=[dict(pair_id=8,signal_i=0,touch_i=0,entry_i=0,side=side,
                 entry=100.,stop=100.-side*2.2)]
    result=wick_bridge(q,source,.01)
    assert len(result)==1 and result[0]["pair_id"]==8
    assert result[0]["entry"]==100. and result[0]["stop"]==100.-side*2.
    assert source[0]["stop"]==100.-side*2.2


def test_wick_bridge_drops_invalid_risk_without_selecting_another_touch():
    q=pd.DataFrame(dict(open=[100.],high=[102.],low=[99.],close=[100.]))
    records=[dict(pair_id=4,signal_i=0,touch_i=0,entry_i=0,side=1,entry=98.9,stop=98.7),
             dict(pair_id=6,signal_i=0,touch_i=0,entry_i=0,side=1,entry=100.,stop=98.8)]
    got=wick_bridge(q,records,.01)
    assert [r["pair_id"] for r in got]==[6]


def test_stop_bridge_reuses_control_time_and_risk_fraction():
    q=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.),
        index=pd.date_range("2026-10-20 14:00",periods=32,freq="15min",tz="UTC"))
    five=pd.DataFrame(index=pd.date_range(q.index[0]-pd.Timedelta(minutes=10),q.index[-1],freq="5min"))
    records=[dict(pair_id=i,signal_i=20+i,touch_i=20+i,entry_i=20+i,side=1,
        entry=100.,stop=98.,signal_time=str(q.index[20+i])) for i in range(2)]
    arrows=np.zeros(len(q));arrows[[20,21]]=1
    cfg=dict(volatility_edges=[.001,.002,.004,.008,.016],midpoint="2026-10-01T00:00Z",
        start="2026-10-20T00:00Z",end="2026-10-22T00:00Z",timezone="America/New_York",seed=20261021)
    spec=dict(symbol="TEST",seed_offset=0)
    matched,half,week,bucket=controls(q,arrows,records,cfg,spec)
    assert len(set(matched.values()))==2
    assert not set(matched.values())&{20,21}
    original=candidate_rows(five,q,records,matched,(half,week,bucket),.01)
    bridge=candidate_rows(five,q,wick_bridge(q,records,.01),matched,(half,week,bucket),.01)
    assert original.entry_i.tolist()==bridge.entry_i.tolist()
    assert original.pair_id.tolist()==bridge.pair_id.tolist()
    assert original.stop.tolist()==[98.]*4
    assert bridge.stop.tolist()==[99.]*4
