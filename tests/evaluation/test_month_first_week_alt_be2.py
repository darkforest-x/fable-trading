from importlib.util import find_spec
from types import SimpleNamespace
from unittest import SkipTest
import numpy as np
import pandas as pd
if find_spec("vectorbt") is None:
    raise SkipTest("Run in the isolated VectorBT environment")
from yoyo.evaluation.month_first_week_alt_be2 import TAKER, cash, order_scenarios, run_path


def trial(bars, target=96., old='target', old_price=96., dual=False):
    values=np.array(bars,float)
    times=pd.date_range('2024-01-01',periods=len(bars),freq='h',tz='UTC')
    row=SimpleNamespace(entry_fill=100.,target_price=target,stop_price=105.,exit_time=times[-1],exit_reason=old,
                        exit_price=old_price,trade_id='fixture')
    be=100*(1-TAKER)/(1+TAKER)
    return [run_path(values,times,p,row,dual) for p in order_scenarios(values,105.,target,98.,be)]


def test_arm_and_revisit_before_eventual_target():
    rs=trial([[100,101,97,97.5],[97.5,99.95,96.5,97],[97,98,95,96]])
    assert all(r['exit_reason']=='breakeven' for r in rs)
    assert np.isclose(rs[0]['mfe_pct'],3)
    np.testing.assert_allclose(cash(np.array([100.]),np.array([rs[0]['exit_price']]),['breakeven']),[0.],atol=1e-12)


def test_same_minute_trigger_turn_and_target():
    rs=trial([[100,106,95,100]],dual=True)
    assert {r['exit_reason'] for r in rs}=={'stop','breakeven','target'}
    assert {(r['old_reason'],r['exit_reason']) for r in rs}>={('stop','breakeven'),('target','breakeven')}


def test_near_target_and_open_gap():
    rs=trial([[100,101,97,99]],target=99,old_price=99.)
    assert all(r['exit_reason']=='target' and r['activation_time'] is None for r in rs)
    rs=trial([[100,100,97,97.3],[103,106,95,96]],old='stop',old_price=105.,dual=True)
    assert rs[0]['exit_reason']=='breakeven' and rs[0]['exit_price']==103.


def test_timeout_open_has_no_new_activation():
    rs=trial([[100,101,99,99.2],[97.5,97.5,97.5,97.5]],old='timeout',old_price=97.5)
    assert rs[0]['exit_reason']=='timeout' and rs[0]['activation_time'] is None


def test_resting_target_executes_on_gap_through_trigger():
    rs=trial([[100,101,99,99.5],[94,106,93,99]])
    assert all(r['exit_reason']=='target' and r['exit_price']==96 and r['activation_time'] is None for r in rs)
