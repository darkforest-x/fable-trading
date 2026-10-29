"""Actual-drawdown recovery is distinct from return to the original price."""
import json
import numpy as np
from yoyo.evaluation.alarm_laggard_selection import OUT as PARENT,STEP
from yoyo.evaluation.alarm_laggard_recovery import half_drawdown_bounds


def cfg(): return json.loads((PARENT/'config.json').read_text())


def test_deep_needle_can_recover_half_without_returning_near_original_price():
    t=np.arange(14)*STEP;lo=np.full(14,85.);close=np.full(14,85.)
    lo[0]=99.;close[0]=100.;lo[1]=60.;close[1]=65.
    result=half_drawdown_bounds(t,lo,close,60001,100.,cfg())
    assert result['deep_lower']==1
    assert result['joint_lower']==result['joint_upper']==1
    assert close.max()==100.  # Only the pre-touch bar reaches the original price.


def test_interval_bounds_cover_actual_second_path_trough_and_later_close():
    rng=np.random.default_rng(20261029);t=np.arange(14)*STEP
    for _ in range(200):
        p=100*np.exp(np.cumsum(rng.normal(0,.003,4200)))
        lo=p.reshape(14,300).min(axis=1);cl=p.reshape(14,300)[:,-1]
        alarm=int(rng.integers(1,300000));time=(np.arange(4200)+1)*1000
        result=half_drawdown_bounds(t,lo,cl,alarm,100.,cfg())
        touches=np.flatnonzero(p<=95.)
        first=time[touches[0]] if len(touches) else None
        deep=bool(first is not None and alarm<first<=alarm+900000)
        mask=(time>alarm)&(time<=alarm+900000)
        trough_indices=np.flatnonzero(mask)
        index=trough_indices[np.argmin(p[mask])]
        close_times=(np.arange(14)+1)*STEP
        recovered=bool(deep and ((close_times>time[index])&(close_times<=alarm+3600000)&(cl>=(100+p[index])/2)).any())
        assert result['deep_lower']<=int(deep)<=result['deep_upper']
        assert result['joint_lower']<=int(recovered)<=result['joint_upper']
