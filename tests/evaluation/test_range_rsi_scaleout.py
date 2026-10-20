"""Timed fee-BE and repeated, profitable-only original-size RSI reductions."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
from yoyo.evaluation.pepe_range_breakeven import replay_timed,assert_equal


def fixture(bars, side=1, events=None, timer=None, minutes=1, atr=None, hour=None):
    bars=np.array(bars,float)
    if side < 0:
        bars=200-bars[:,[0,2,1,3]]
    n=len(bars)
    frame=pd.DataFrame(bars,columns=["open","high","low","close"],
        index=pd.date_range("2026-01-01",periods=n,freq=f"{minutes}min",tz="UTC"))
    atr=np.full(n,np.nan) if atr is None else np.array(atr,float)
    hour=np.zeros(n) if hour is None else np.array(hour,float)
    rsi=None if events is None else np.array(events)*side
    entry=dict(trade_id="s",entry_i=0,entry=100.,stop=100.-side,risk=1.,
               side=side,end_i=n-1)
    rows,fills,audit=run_native(frame,atr,hour,pd.DataFrame([entry]),
        ExitPolicy(time_breakeven_minutes=timer,rsi_scaleout=rsi is not None),
        .01,"TEST",execution_minutes=minutes,rsi_side=rsi)
    return rows.iloc[0],fills,audit,frame,atr,hour


@pytest.mark.parametrize("side",[1,-1])
def test_time_be_waits_for_fifth_bar_then_eligible_five_minute_close(side):
    bars=[[100,100.1,99.5,100]]*33
    bars[26]=[100,101,99.5,100]  # A wick after deadline cannot arm BE.
    bars[30]=[100,100.5,99.5,100.4]  # Old low cannot retroactively hit new stop.
    bars[31]=[100.4,100.5,100.1,100.3]
    r,f,_,frame,atr,hour=fixture(bars,side,timer=25)
    assert (r.be_trigger_i,r.be_effective_i,r.exit_i,r.reason)==(25,30,31,5)
    assert r.deferred_closes==1 and r.net_r==pytest.approx(0,abs=1e-8)
    ctx=dict(bars=np.ascontiguousarray(frame.to_numpy()),atr=atr,hour=hour,
             be_clock=np.array(frame.index.minute%5==0,float))
    expected=replay_timed(ctx,[[0,side,100.,100.-side,32]],delay=25)[0]
    fields=["exit_i","exit_price","reason","peak_r","arm_i","protection","ambiguous",
            "steps","be_trigger_i","be_effective_i","stop_kind","be_price","deferred_closes"]
    assert_equal(expected,r[fields].to_numpy(float),.01)


@pytest.mark.parametrize("side",[1,-1])
def test_five_diamonds_reduce_original_fifths_and_skip_losing_events(side):
    bars=[[100,100,100,100],[100,100.1,99.5,99.9]]
    bars += [[100,102,99.5,102],[102,103,99.5,103],
             [103,104,99.5,104],[104,105,99.5,105],
             [105,106,99.5,106],[106,107,99.5,107],[107]*4]
    r,f,_,_,_,_=fixture(bars,side,events=[-1,-1,-1,1,-1,-1,-1,-1,0])
    assert r.reason==6 and r.exit_i==7 and r.rsi_partial_count==5
    assert r.rsi_skipped_unprofitable==1
    assert r.rsi_first_i==2 and r.rsi_realized_weight==1
    exits=f[f.phase=="rsi_partial"]
    assert len(exits)==5 and exits.quantity.eq(r.quantity/5).all()
    assert r.gross_r==pytest.approx(4.8)
    assert r.net_r==pytest.approx(4.6)
    assert f.commission.sum()==pytest.approx(r.quantity*100*.002)


@pytest.mark.parametrize("terminal",["stop","boundary","reverse"])
def test_original_terminal_exit_wins_over_same_close_diamond(terminal):
    bars=[[100]*4,[100,107,99.5,106],[106,107,99.5,105]]
    atr=[np.nan,1.,np.nan] if terminal=="reverse" else None
    hour=[0,0,-1] if terminal=="reverse" else None
    if terminal=="stop":
        bars[2]=[106,107,98,105]
    if terminal=="reverse":
        bars[2]=[106,107,103,105]
    r,f,_,_,_,_=fixture(bars,events=[0,0,-1],atr=atr,hour=hour)
    assert r.rsi_partial_count==0 and len(f)==2
    assert r.reason=={"stop":1,"boundary":0,"reverse":3}[terminal]


def test_partial_keeps_remaining_stop_and_timer_never_forces_loss_exit():
    bars=[[100]*4,[100,101,99.5,101],[101,101,98,100],[100]*4]
    r,f,_,_,_,_=fixture(bars,events=[0,-1,0,0],timer=25)
    assert r.rsi_partial_count==1 and r.reason==1
    assert r.gross_r==pytest.approx(-.6)
    assert r.net_r==pytest.approx(-.8)
    assert f.quantity.tolist()==[r.quantity,r.quantity*.2,r.quantity*.8]


def test_rsi_requires_events_and_rejects_combined_partial_policy():
    with pytest.raises(ValueError,match="cannot combine"):
        ExitPolicy(rsi_scaleout=True,partial_close_r=3)
    with pytest.raises(ValueError,match="cannot be combined"):
        ExitPolicy(time_breakeven_minutes=25,breakeven_r=3)
    with pytest.raises(ValueError,match="whole number"):
        ExitPolicy(time_breakeven_minutes=24)


@pytest.mark.parametrize("minutes",[3,5])
def test_diamond_features_are_close_aligned_and_prefix_causal(minutes):
    from yoyo.evaluation.range_rsi_scaleout import strong_features
    rng=np.random.default_rng(17)
    close=100+np.cumsum(rng.normal(0,.5,300))
    frame=pd.DataFrame(dict(open=close,high=close+.1,low=close-.1,close=close),
        index=pd.date_range("2026-01-01 00:01",periods=300,freq="1min",tz="UTC"))
    all_features=strong_features(frame,minutes)
    prefix=strong_features(frame.iloc[:157],minutes)
    pd.testing.assert_frame_equal(prefix,all_features.loc[prefix.index])
    assert (all_features.index.minute%minutes==0).all()
    assert prefix.index[-1]<=frame.index[156]
