"""Stop-before-excursion and endpoint coverage regressions."""
import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_review import excursions
from yoyo.evaluation.btc_range_wick_fanshen import monday_levels


def test_excludes_stop_bar_and_later_rebound():
    bars = np.array([[100,101,99,100], [100,103,99,102],
                     [102,110,94,95], [95,120,94,119]], float)
    rows = pd.DataFrame([dict(trade_id="long",entry_i=0,end_i=3,
                             side=1,entry=100,stop=95,risk=5)])
    result = excursions(bars, rows).iloc[0]
    assert result.mfe_r == .6
    assert result.mfe_upper_r == 2
    assert result.stop_i == 2
    assert result.bars_before_stop == 1


def test_short_mirrors_long_and_ignores_entry_bar():
    bars = np.array([[100,150,50,100], [100,101,97,98], [98,106,90,105]],float)
    rows = pd.DataFrame([dict(trade_id="short",entry_i=0,end_i=2,
                             side=-1,entry=100,stop=105,risk=5)])
    result = excursions(bars,rows).iloc[0]
    assert result.mfe_r == .6 and result.mfe_upper_r == 2


def test_no_stop_is_censored_at_segment_end():
    bars = np.array([[100,101,99,100],[100,103,99,102],[102,120,99,119]],float)
    rows = pd.DataFrame([dict(trade_id="censored",entry_i=0,end_i=1,
                             side=1,entry=100,stop=95,risk=5)])
    result=excursions(bars,rows).iloc[0]
    assert result.mfe_r == .6 and not result.initial_stop_hit


def test_zero_ratio_adds_both_known_monday_endpoints_once():
    q=pd.DataFrame(dict(open=[105]*4,high=[110]*4,low=[100]*4,close=[106]*4),
                   index=pd.date_range("2026-10-19T04:15Z",periods=4,freq="15min"))
    prices,labels,_,_=monday_levels(q,[0,.5],"America/New_York")
    assert labels==["0:up","0:down","0.5:up"]
    np.testing.assert_allclose(prices[1],[100,110,105])
    assert np.isnan(prices[0]).all()
