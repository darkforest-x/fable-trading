"""Synthetic execution chronology and causal feature contracts for exit research."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import ma_stoch_exit_engine as e
from yoyo.evaluation.ma_shift_stoch import run_backtest


def fixture(n=180):
    idx=pd.date_range('2026-01-01',periods=n,freq='5min',tz='UTC')
    frame=pd.DataFrame(dict(open=100.,high=100.1,low=99.9,close=100.,volume=1.),index=idx)
    ctx=e.prepare(frame)
    for key in ('arrow','admission','kd','direction'):ctx[key][:]=0
    ctx['atr'][:]=1
    ctx['admission'][130]=1
    return ctx,idx[131],idx[-1]+e.BAR


def test_stop_before_target_when_ohlc_order_unknown():
    c,s,t=fixture();c['low'][131]=97;c['high'][131]=103
    r=e.simulate(c,s,t,'tp_1')
    assert r['trades'].iloc[0].exit_price_weighted==98
    assert r['ambiguous_bars']==1
    assert r['trades'].iloc[0].exit_timing=='intrabar'
    assert r['trades'].iloc[0].net_return==pytest.approx(-.022)


def test_stop_gap_gets_open_and_no_retroactive_reentry():
    c,s,t=fixture();c['open'][132]=97;c['low'][132]=96;c['high'][132]=98;c['close'][132]=97
    r=e.simulate(c,s,t,'stop_2')
    assert len(r['trades'])==1
    assert r['trades'].iloc[0].exit_price_weighted==97
    assert r['trades'].iloc[0].exit_reason=='stop_gap'
    assert r['fills'].query('kind=="entry"').shape[0]==1


def test_partial_then_remaining_stop_charges_exactly_one_round_trip():
    c,s,t=fixture();c['high'][131]=102.1;c['close'][131]=101
    c['low'][132]=97
    r=e.simulate(c,s,t,'partial_1r');tr=r['trades'].iloc[0]
    exits=r['fills'].query('kind=="exit"')
    assert exits.fraction.tolist()==[.5,.5]
    assert exits.price.tolist()==[102,98]
    assert tr.gross_return==pytest.approx(0)
    assert tr.net_return==pytest.approx(-.002)
    assert r['fills'].cost_return.sum()==pytest.approx(.002)
    assert tr.net_r==pytest.approx(-.1)


def test_break_even_uses_close_then_next_bar_only():
    c,s,t=fixture();c['high'][131]=102.5;c['low'][131]=99;c['close'][131]=102.2
    c['open'][132]=101;c['low'][132]=100;c['high'][132]=101.5;c['close'][132]=100.5
    r=e.simulate(c,s,t,'break_even');tr=r['trades'].iloc[0]
    assert tr.exit_i==132
    assert tr.exit_price_weighted==pytest.approx(100.2)
    assert tr.net_return==pytest.approx(0,abs=1e-12)


def test_break_even_gap_does_not_guarantee_flat_net():
    c,s,t=fixture();c['high'][131]=102.5;c['close'][131]=102.2
    c['open'][132]=99;c['low'][132]=98.5
    tr=e.simulate(c,s,t,'break_even')['trades'].iloc[0]
    assert tr.exit_price_weighted==99
    assert tr.net_return<0


def test_trail_never_moves_backward_and_only_uses_prior_close():
    c,s,t=fixture();c['high'][131]=103.2;c['close'][131]=103
    c['open'][132]=102.8;c['high'][132]=102.9;c['low'][132]=102.1;c['close'][132]=102.3
    c['open'][133]=102.2;c['low'][133]=101.8
    tr=e.simulate(c,s,t,'trail_1')['trades'].iloc[0]
    assert tr.exit_i==133 and tr.exit_price_weighted==102


def test_short_target_and_partial_cost_mirror():
    c,s,t=fixture();c['admission'][130]=-1;c['low'][131]=97.9;c['high'][132]=102.1
    tr=e.simulate(c,s,t,'partial_1r')['trades'].iloc[0]
    assert tr.side==-1 and tr.net_return==pytest.approx(-.002)


def test_extra_exit_does_not_relax_entry_and_timeout_counts_complete_bars():
    c,s,t=fixture();c['kd'][132]=-1
    r=e.simulate(c,s,t,'reverse_kd')
    assert r['trades'].iloc[0].exit_i==133
    assert len(r['trades'])==1 and r['open_positions'].empty
    tr=e.simulate(c,s,t,'time_6')['trades'].iloc[0]
    assert tr.exit_i-tr.entry_i==6


def test_forced_control_ignores_admission_and_remains_single_trade():
    c,s,t=fixture();c['admission'][:]=1;c['arrow'][135]=-1
    r=e.simulate(c,s,t,'baseline',forced=(132,1))
    assert len(r['trades'])==1 and len(r['fills'])==2
    assert r['trades'].iloc[0].entry_i==133 and r['trades'].iloc[0].exit_i==136


def test_censored_trade_stays_open_and_reserves_full_cost():
    c,s,t=fixture();r=e.simulate(c,s,t,'baseline')
    assert r['trades'].empty and len(r['open_positions'])==1
    assert r['final_equity']==998
    assert r['curve'].iloc[-1].time==t
    with pytest.raises(ValueError,match='holdout'):
        e.simulate(c,s,pd.Timestamp('2026-05-05T00:00Z'),'baseline')


def test_features_and_trade_prefix_causal_and_baseline_parity():
    idx=pd.date_range('2025-12-20',periods=2400,freq='5min',tz='UTC')
    rng=np.random.default_rng(19);price=100+np.cumsum(rng.normal(0,.3,len(idx)))
    frame=pd.DataFrame(dict(open=price,high=price+.6,low=price-.6,close=price+rng.uniform(-.5,.5,len(idx)),volume=1.),index=idx)
    ctx=e.prepare(frame);changed=frame.copy();changed.iloc[1800:,:4]+=7
    after=e.prepare(changed)
    for key in ('atr','vol','arrow','admission','direction','kd'):
        np.testing.assert_allclose(ctx[key][:1800],after[key][:1800],equal_nan=True)
    s,t=idx[200],idx[-1]+e.BAR
    old=run_backtest(frame,s,t);new=e.simulate(ctx,s,t,'baseline')
    assert len(new['trades'])>0
    for col in ('entry_time','exit_time','side'):
        assert old.trades[col].tolist()==new['trades'][col].tolist()
    np.testing.assert_allclose(old.trades.net_return,new['trades'].net_return,atol=1e-12)
    assert old.equity_curve.iloc[-1].equity==pytest.approx(new['final_equity'],rel=1e-12)
    a=e.simulate(ctx,s,idx[1800],'trail_1');b=e.simulate(after,s,idx[1800],'trail_1')
    pd.testing.assert_frame_equal(a['trades'],b['trades'])
    pd.testing.assert_frame_equal(a['curve'],b['curve'])
