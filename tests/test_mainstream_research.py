"""Portfolio/report helpers on synthetic returns only."""
import numpy as np
import pandas as pd
from yoyo.evaluation.mainstream_research import curve_metrics, trade_metrics


def test_drawdown_includes_initial_cash_peak():
    got = curve_metrics(pd.Series([.9, .95, .8]))
    assert abs(got["mdd_pct"]-20) < 1e-9
    assert abs(got["net_pct"]+20) < 1e-9


def test_trade_stats_exclude_unfilled_and_censored_winners():
    g = pd.DataFrame(dict(portfolio_selected=[True, True, True, False],
        natural_exit=[True, True, False, True], censored=[False, False, True, False],
        net_return=[.1, -.05, 1., 2.], net_r=[5., -1., 10., 20.]))
    actual = trade_metrics(g)
    assert actual["trades"] == 3 and actual["censored"] == 1
    assert actual["natural_trades"] == 2 and actual["pf"] == 2
    assert actual["win_5r"] == 1 and actual["win_pct"] == 50
