"""Causality and event ordering for the second-round exit policies."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import ma_stoch_exit_v2 as e
from yoyo.evaluation import ma_stoch_exit_engine as old
from yoyo.evaluation.ma_stoch_exit_v2_study import select,validation_arms
from test_ma_stoch_exit_engine import fixture as old_fixture


def fixture():
    ctx,start,end=old_fixture()
    ctx['atr15']=np.full(len(ctx['index']),2.)
    ctx['k']=np.full(len(ctx['index']),50.)
    for n in (6,12,24):
        ctx[f'low_{n}']=np.full(len(ctx['index']),97.)
        ctx[f'high_{n}']=np.full(len(ctx['index']),103.)
    return ctx,start,end


def test_percent_target_without_initial_stop_can_take_profit():
    c,s,t=fixture();c['high'][131]=101.2
    tr=e.simulate(c,s,t,'tp_pct_1')['trades'].iloc[0]
    assert tr.initial_risk==0 and tr.exit_i==131
    assert tr.exit_price_weighted==101 and tr.net_return==pytest.approx(.008)


def test_close_stop_ignores_wick_and_requires_prior_close():
    c,s,t=fixture();c['low'][131]=97;c['close'][131]=99
    c['low'][132]=97;c['close'][132]=97.5
    c['open'][133]=96
    tr=e.simulate(c,s,t,'close_stop_2')['trades'].iloc[0]
    assert tr.exit_i==133 and tr.exit_price_weighted==96 and tr.exit_reason=='close_stop'
    assert old.simulate(c,s,t,'stop_2')['trades'].iloc[0].exit_i==131


def test_structure_uses_prior_window_with_atr_floor_on_gap():
    c,s,t=fixture();c['low'][131]=96
    tr=e.simulate(c,s,t,'structure_12')['trades'].iloc[0]
    assert tr.initial_stop==96.75
    c['low_12'][130]=101
    tr=e.simulate(c,s,t,'structure_12')['trades'].iloc[0]
    assert tr.initial_stop==99


def test_half_reverse_exits_conserve_original_quantity_and_cost():
    c,s,t=fixture();c['arrow'][133]=-1;c['arrow'][134]=1;c['arrow'][137]=-1
    c['admission'][133]=-1;c['admission'][137]=-1
    r=e.simulate(c,s,t,'wide_arrow_half');tr=r['trades'].iloc[0]
    exits=r['fills'].query('trade_id==1 and kind=="exit"')
    assert exits.fraction.tolist()==[.5,.5]
    assert exits.time.tolist()==[c['index'][134],c['index'][138]]
    assert tr.net_return==pytest.approx(-.002)
    assert r['fills'].query('trade_id==1').cost_return.sum()==pytest.approx(.002)
    assert r['open_positions'].iloc[0].side==-1


def test_no_arrow_policy_keeps_position_until_barrier():
    c,s,t=fixture();c['arrow'][133]=-1
    r=e.simulate(c,s,t,'wide_no_arrow')
    assert r['trades'].empty and len(r['open_positions'])==1
    c['low'][135]=93
    assert e.simulate(c,s,t,'wide_no_arrow')['trades'].iloc[0].exit_i==135


def test_zone_touch_and_cross_use_only_during_holding_state():
    c,s,t=fixture();c['k'][129]=90;c['kd'][132]=-1
    c['k'][136]=82;c['kd'][137]=-1
    touch=e.simulate(c,s,t,'zone_touch')['trades'].iloc[0]
    cross=e.simulate(c,s,t,'zone_cross')['trades'].iloc[0]
    assert touch.exit_i==137 and cross.exit_i==138


def test_short_zone_and_percent_geometry():
    c,s,t=fixture();c['admission'][130]=-1;c['k'][132]=18
    assert e.simulate(c,s,t,'zone_touch')['trades'].iloc[0].exit_i==133
    c['high'][131]=102
    tr=e.simulate(c,s,t,'sl_pct_1')['trades'].iloc[0]
    assert tr.initial_stop==101 and tr.net_return==pytest.approx(-.012)


def test_completed_atr15_and_structures_are_prefix_causal():
    idx=pd.date_range('2025-12-20',periods=600,freq='5min',tz='UTC')
    base=100+np.sin(np.arange(600)/10)
    f=pd.DataFrame(dict(open=base,high=base+1,low=base-1,close=base+.1,volume=1),index=idx)
    a=e.prepare(f);changed=f.copy();changed.iloc[500:,:4]+=50;b=e.prepare(changed)
    for k in ['atr15','k','low_6','high_24','arrow','admission']:
        np.testing.assert_allclose(a[k][:500],b[k][:500],equal_nan=True)
    # 14 complete 15m bars are first known at the 42nd 5m close.
    assert np.isnan(a['atr15'][40]) and np.isfinite(a['atr15'][41])
    assert a['atr15'][42]==a['atr15'][41]==a['atr15'][43]


def test_old_anchors_are_exactly_equivalent_on_same_context():
    c,s,t=fixture();c['arrow'][135]=-1;c['low'][132]=97.8
    for name in ['baseline','stop_2','stop_3']:
        a=old.simulate(c,s,t,name);b=e.simulate(c,s,t,name)
        pd.testing.assert_frame_equal(a['trades'],b['trades'])
        pd.testing.assert_frame_equal(a['curve'],b['curve'])


def test_selection_ignores_anchors_and_under_sampled_new_arms():
    row=lambda equity,n:dict(final_equity=equity,mtm_max_drawdown_pct=10,n=n)
    arms={'baseline':row(1500,100),'stop_2':row(1600,100),'stop_3':row(1550,100),'wide_no_arrow':row(2000,3),'zone_touch':row(900,100)}
    assert select(arms)=='zone_touch'
    assert validation_arms({'selected':'zone_touch'})==['baseline','stop_2','zone_touch']
    assert len(e.POLICIES)==31
