"""Path attribution excludes pre-entry highs and post-stop recoveries."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_fib_diagnostics import path_mfe,stop_candidates


def test_entry_high_before_limit_is_not_favorable_excursion():
    bars=np.array([[102,110,99,101],[101,103,97,102]])
    row=SimpleNamespace(entry=100,stop=98,side=1,risk=2,entry_i=0,exit_i=1)
    assert path_mfe(bars,row,1)==1.5
    assert path_mfe(bars,row,1,'OLHC')==5


def test_short_stop_precedes_later_same_bar_recovery():
    bars=np.array([[98,101,99,100],[100,103,95,99]])
    row=SimpleNamespace(entry=100,stop=102,side=-1,risk=2,entry_i=0,exit_i=1)
    assert path_mfe(bars,row,1)==.5


def test_wider_stop_does_not_modify_entry_or_target():
    original=pd.DataFrame([dict(trade_id='a',entry=100,stop=99.3,side=1,risk=.7,tp80=104,tp20=106)])
    changed=stop_candidates(original,.02,'stop2',.1)
    assert changed.stop.iloc[0]==98
    assert changed.risk.iloc[0]==2
    assert changed.base_risk.iloc[0]==.7
    assert changed.entry.iloc[0]==100 and changed.tp80.iloc[0]==104 and changed.tp20.iloc[0]==106
    assert original.stop.iloc[0]==99.3
