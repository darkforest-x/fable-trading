"""Synthetic causal/price-path contracts for the frozen iFVG research model."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.ifvg_lrl import Config, generate, resolve, replay, _lrl, metrics


def frame(values, start='2025-01-06T14:00Z'):
    f=pd.DataFrame(values,columns=['open','high','low','close'])
    f['volume']=1.
    f.index=pd.date_range(start, periods=len(f),freq='3min')
    return f


def test_next_open_recomputes_r_and_charges_notional_cost():
    f=frame([[100,101,99,100],[102,104,101,103],[103,106,102,105]])
    t=resolve(f,0,1,99)
    assert t['entry_price']==102 and t['target']==105 and t['exit_i']==2
    assert t['gross_r']==pytest.approx(1)
    assert t['net_r']==pytest.approx(1-.002*102/3)


def test_entry_bar_dual_touch_chooses_stop():
    f=frame([[100,101,99,100],[100,103,97,100]])
    t=resolve(f,0,1,98)
    assert t['exit_reason']=='sl_ambiguous' and t['gross_r']==-1


def test_adverse_gap_is_worse_than_stop():
    f=frame([[100,101,99,100],[100,101,99,100],[96,97,95,96]])
    t=resolve(f,0,1,98)
    assert t['exit_price']==96 and t['gross_r']==-2


def test_invalid_open_risk_and_censoring():
    f=frame([[100,101,99,100],[100,101,99,100]])
    assert resolve(f,0,1,101) is None
    t=resolve(f,0,1,90)
    assert t['censored'] and np.isnan(t['net_r'])
    assert metrics(pd.DataFrame([t]))['n']==0


def test_short_uses_linear_return():
    f=frame([[100,101,99,100],[100,101,97,98]])
    t=resolve(f,0,-1,102)
    assert t['target']==98 and t['gross_return']==pytest.approx(.02)
    assert t['net_r']==pytest.approx(.9)


def test_line_uses_latest_three_and_target_side():
    p=[(1,105.,3),(4,104.,6),(7,103.,9)]
    assert _lrl(p,1,10,100,.01,20)[0]
    assert not _lrl(p,1,10,104,.01,20)[0]
    assert not _lrl(p+[(10,110.,12)],1,13,100,.01,20)[0]


def test_fvg_is_consumed_once_and_prefix_is_invariant():
    f=frame([[100,101,99,100],[102,104,102,103],[104,106,104,105],
             [101,102,100,100.5],[100,101,99,100],[100,101,99,100]])
    cfg=Config(session_only=False,range_bars=3)
    a,_=generate(f.iloc[:4],cfg); b,_=generate(f,cfg)
    short=b.loc[b.side.eq(-1)]
    assert len(short)==1 and short.iloc[0].signal_i==3
    pd.testing.assert_frame_equal(a,b.loc[b.signal_i<4].reset_index(drop=True))


def test_pivot_is_not_known_until_two_right_bars_close():
    f=frame([[100,101,99,100],[100,102,99,100],[102,110,100,105],
             [104,106,100,102],[102,104,100,103]])
    _,ctx=generate(f,Config(session_only=False,range_bars=3))
    assert ctx.stop_short.iloc[:4].isna().all()
    assert ctx.stop_short.iloc[4]==pytest.approx(110.01)


def test_future_mutations_do_not_change_past_events():
    rng=np.random.default_rng(7); x=100+np.cumsum(rng.normal(0,.5,200))
    f=frame(np.c_[x,x+1,x-1,x])
    cfg=Config(session_only=False)
    a,_=generate(f,cfg)
    mutated=f.copy(); mutated.iloc[120:,:4]*=3
    b,_=generate(mutated,cfg)
    pd.testing.assert_frame_equal(a.loc[a.signal_i<120].reset_index(drop=True),b.loc[b.signal_i<120].reset_index(drop=True))


def test_gap_and_duplicate_fail_closed():
    f=frame([[100,101,99,100]]*10)
    with pytest.raises(ValueError): generate(f.drop(f.index[4]))
    with pytest.raises(ValueError): generate(pd.concat([f,f.iloc[-1:]]))


def test_serial_does_not_retroactively_enter_skipped_candidate():
    f=frame([[100,101,99,100]]*5+[[100,104,99,103]])
    ev=pd.DataFrame([dict(event_id=str(i),signal_i=i,signal_time=f.index[i]+pd.Timedelta(minutes=3),
        side=1,stop=97,has_lrl=True,lrl_target=110,lrl_residual=0.,range_fraction=.02,reason='candidate') for i in [0,1,2]])
    trades,rejects=replay(f,ev,serial=True)
    assert len(trades)==1 and list(rejects.reason)==['position_busy','position_busy']
    independent,_=replay(f,ev,serial=False)
    assert len(independent)==3
