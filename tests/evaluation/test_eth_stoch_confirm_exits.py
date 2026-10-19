"""Behavioral contracts for owner task62, including adverse OHLC ordering."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.eth_stoch_confirm_exits import Policy, trace, prepare, opportunities, serial, sample_controls, metrics


def ctx(bars):
    a=np.array(bars,float)
    return dict(o=a[:,0],h=a[:,1],l=a[:,2],c=a[:,3],arrow=np.zeros(len(a)//5+1),kd=np.zeros(len(a)//5+1))


def test_same_minute_old_stop_has_priority_over_target():
    z=trace(ctx([[100,103,97,101]]),0,1,100,98,Policy('tp',target=1))
    assert z['exit_price']==98 and z['reason']=='stop'
    assert z['ambiguous_old_stop_target']==1


def test_gap_stop_fills_at_observed_open_beyond_level():
    z=trace(ctx([[100,101,99,100],[96,97,95,96]]),0,1,100,98,Policy('base'))
    assert z['exit_price']==96 and z['reason']=='stop_gap'


def test_be_observed_at_close_does_not_rewrite_same_minute_low():
    z=trace(ctx([[100,103,99,102],[102,102.5,99,100]]),0,1,100,98,Policy('be',be=1))
    assert z['exit_i']==1 and z['exit_price']==100
    assert z['net_points']==pytest.approx(-.2)


def test_fee_covering_be_waits_until_reachable_and_covers_cost():
    c=ctx([[2400,2401,2399.5,2401],[2401,2406,2400,2405],[2405,2405,2403,2404]])
    z=trace(c,0,1,2400,2399,Policy('net_be',be=.5,net_be=True))
    assert z['exit_i']==2 and z['exit_price']==2404.8
    assert z['net_points']==pytest.approx(0)


def test_new_trail_uses_close_not_future_high_and_is_monotone():
    z=trace(ctx([[100,110,99,103],[103,105,101,104],[104,105,101,102]]),0,1,100,98,Policy('trail',trail=1))
    assert z['exit_i']==2 and z['exit_price']==102


def test_split_endpoint_censors_without_looking_across_cut():
    z=trace(ctx([[100,101,99,100],[100,150,99,140]]),0,1,100,98,Policy('tp',target=2),end_i=1)
    assert z['closed'] is False and z['exit_i']==0 and z['exit_price']==100


def test_admission_uses_confirmation_close_then_actual_following_open():
    index=pd.date_range('2026-10-17',periods=150,freq='min',tz='UTC')
    f=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=1.),index=index)
    c=prepare(f);c['arrow'][20]=1
    c['frame'].iloc[21,c['frame'].columns.get_loc('close')]=101.
    c['frame'].iloc[22,c['frame'].columns.get_loc('open')]=103.
    cfg=dict(start='2026-10-17T00:00:00Z',split='2025-01-01T00:00:00Z',examples_start='2026-10-16T16:00:00Z',volatility_bin_edges=[.01])
    a=opportunities(c,cfg);r=a[a.anchor_i.eq(20)].iloc[0]
    assert r.entry_i==110 and r.entry==103 and r.stop==98 and r.risk==5 and r.eligible
    # A lower low two bars before the signal must widen risk and reject the setup.
    c['frame'].iloc[18,c['frame'].columns.get_loc('low')]=90.
    a=opportunities(c,cfg);r=a[a.anchor_i.eq(20)].iloc[0]
    assert r.risk==14 and not r.eligible


def test_future_mutation_preserves_features_and_entry():
    rng=np.random.default_rng(42);x=100+np.cumsum(rng.normal(0,.1,300))
    f=pd.DataFrame(dict(open=x,high=x+.2,low=x-.2,close=x+.05,volume=1.),index=pd.date_range('2026-10-17',periods=300,freq='min',tz='UTC'))
    a=prepare(f);g=f.copy();g.iloc[150:,:4]*=2;b=prepare(g)
    pd.testing.assert_frame_equal(a['signals'].iloc[:30],b['signals'].iloc[:30])
    np.testing.assert_allclose(a['vol'][:30],b['vol'][:30],equal_nan=True)


def test_independent_occupancy_does_not_reuse_parent_trades():
    d=pd.DataFrame(dict(entry_i=[5,10,20],exit_i=[12,15,22],pair_id=[0,1,2]))
    assert serial(d).pair_id.tolist()==[0,2]
    d.loc[0,'exit_i']=9
    assert serial(d).pair_id.tolist()==[0,1,2]


def test_short_cost_sign_and_profit_target():
    z=trace(ctx([[100,101,97,98]]),0,-1,100,102,Policy('target',target=1))
    assert z['exit_price']==98 and z['net_points']==pytest.approx(1.8)


def test_control_matching_does_not_cross_time_split_or_side():
    d=pd.DataFrame([dict(eligible=True,is_signal=True,raw_side=1,segment='development',week='W',bucket=0,side=1),
                    dict(eligible=True,is_signal=False,raw_side=0,segment='validation',week='W',bucket=0,side=1),
                    dict(eligible=True,is_signal=False,raw_side=0,segment='development',week='W',bucket=0,side=-1)])
    s,c,missing=sample_controls(d,{'control_seed':1})
    assert missing==[0] and c.empty and len(s)==1


def test_mathematical_break_even_is_not_a_floating_point_win():
    rows = pd.DataFrame(dict(closed=[True]*4, net_points=[2., -2., 2e-13, -2e-13],
        net_r=[1., -1., 1e-13, -1e-13], net_return=[.02, -.02, 1e-15, -1e-15],
        gross_points=[2.2, -1.8, .2, .2], cost_points=[.2]*4,
        ambiguous_old_stop_target=[0]*4, hold_minutes=[1]*4))
    result = metrics(rows)
    assert result['win_rate'] == .25 and result['pf'] == 1
