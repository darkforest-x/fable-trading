"""Economic identity checks on saved-output BE comparisons, without market data."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_be05_report import metrics, paired_stats, periods


def test_censoring_and_exit_order_prevent_false_profit_and_drawdown():
    frame = pd.DataFrame({"entry_time":pd.to_datetime(["2025-01-01"]*4, utc=True),
        "exit_time":pd.to_datetime(["2025-01-03","2025-01-02","2025-01-04","2025-01-05"],utc=True),
        "event_key":["a","b","c","d"],"net_r":[-2.,3.,-1.,100.],
        "net_return":[-.02,.03,-.01,1.],"censored":[False,False,False,True]})
    got=metrics(frame)
    assert got["total_r"]==pytest.approx(0.)
    assert got["closed"]==3 and got["censored"]==1
    assert got["event_drawdown_r"]==pytest.approx(3.)
    assert got["realized_ge10"]==0


def test_retention_uses_original_realized_winner_and_costs_count_as_loss():
    frame=pd.DataFrame({"joint_closed":[True]*3,"net_r_baseline":[12.,-1.,2.],
        "net_r_be05":[-.1,-.1,2.],"delta_r":[-12.1,.9,0.],
        "entry_time":pd.to_datetime(["2025-01-01"]*3,utc=True)})
    got=paired_stats(frame)
    assert got["original_realized_ge10"]==1 and got["retained_original_ge10"]==0
    assert got["rescued_losers"]==1 and got["harmed_winners"]==1
    assert got["delta_r"]==pytest.approx(-11.2)


def test_earlier_year_never_scores_an_exit_after_the_cut():
    frame=pd.DataFrame({"entry_time":pd.to_datetime(["2025-09-09","2025-09-09","2025-09-11"],utc=True),
        "exit_time":pd.to_datetime(["2025-09-09","2025-09-11","2025-09-12"],utc=True)})
    groups=dict(periods(frame))
    assert len(groups["full"])==3 and len(groups["earlier"])==1 and len(groups["later"])==1
