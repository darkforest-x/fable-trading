"""Clock, matching and nonoverlap counterexamples for IMACD profit exploration."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.imacd_profit_mechanism import features,exit_arrays,outcome,match_controls,single_position,inference


def bars(n=12):
    x=np.arange(n,dtype=float)+100
    return pd.DataFrame(dict(open=x,high=x+2,low=x-2,close=x+1),index=pd.date_range('2024-01-01',periods=n,freq='1h',tz='UTC'))


def exits():
    f=pd.DataFrame({'md':[0,1,2,2,1,0,0,-1,-2,0,1,2],
                    'up':[False,True,False,False,False,False,False,False,False,True,False,False],
                    'dn':[False,False,False,True,False,False,False,True,False,False,False,False]})
    return exit_arrays(f)


def test_causal_entry_and_three_exit_clocks():
    b=bars();e=exits()
    for mode,expected in [('signal',4),('neutral',6),('opposite',8)]:
        r=outcome(b,e,1,1,mode,11)
        assert r['entry_i']==2 and r['entry_price']==102
        assert r['exit_i']==expected and r['exit_price']==100+expected
        assert r['net_bp']==pytest.approx((r['exit_price']/102-1)*10000-20)


def test_exit_bar_high_is_not_held_after_open_exit():
    b=bars();a=outcome(b,exits(),1,1,'signal',11)
    b.loc[b.index[4],'high']=10000
    z=outcome(b,exits(),1,1,'signal',11)
    assert a['mfe_bp']==z['mfe_bp']


def test_boundary_mark_and_short_pnl():
    b=bars();r=outcome(b,exits(),1,1,'opposite',5)
    assert r['exit_kind']=='boundary_mark' and r['exit_price']==106
    q=outcome(b,exits(),7,-1,'opposite',11)
    assert q['entry_price']==108 and q['exit_price']==111
    assert q['net_bp']==pytest.approx(-3/108*10000-20)
    with pytest.raises(ValueError):outcome(b,exits(),11,1,'signal',11)


def test_prefix_features_and_departure_flat_run():
    b=bars(600);b['close']=100+np.sin(np.arange(600)/10)*4;b['open']=b.close;b['high']=b.close+.1;b['low']=b.close-.1
    f=features(b);pd.testing.assert_frame_equal(features(b.iloc[:451]),f.iloc[:451])
    dep=f.departure.ne(0)
    assert (f.md.shift().loc[dep]==0).all()
    assert (f.zero_before.loc[dep]>=1).all()


def test_controls_are_unique_same_month_bucket_zone():
    ix=pd.date_range('2024-01-01',periods=20,freq='1h',tz='UTC')
    f=pd.DataFrame(dict(cross=[1,0,0,0,1]+[0]*15,volbin=[1]*20,zone=[1]*20),index=ix)
    m=match_controls(f,np.arange(20),'cross',np.random.default_rng(1))
    assert set(m)=={0,4}
    chosen=sum(m.values(),[])
    assert len(chosen)==len(set(chosen))==6 and not ({0,4}&set(chosen))


def test_single_position_blocks_overlap_but_allows_exit_open_reentry():
    b=bars();e=exits();rows=[]
    for i in [1,2,3]:
        r=outcome(b,e,i,1,'neutral',11);r.update(side=1,event_id=str(i));rows.append(r)
    metrics,accepted=single_position(b,pd.DataFrame(rows))
    assert metrics['trades']==1 and metrics['blocked']==2
    assert metrics['fixed_notional_return_pct']==pytest.approx(accepted[0]['net_bp']/100)


def test_zero_and_positive_cluster_nulls():
    z=inference([0]*12,list(range(12)))
    assert z['p']==1 and z['excess_bp']==0
    p=inference([1]*6,list(range(6)))
    assert p['p']==1/64 and p['ci_low']==p['ci_high']==1
