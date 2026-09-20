"""Selection must not peek at validation or switch to ETH after seeing returns."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v9_5m_15m_ma_report import select_ma, assign_period, paired_controls


def test_selection_ignores_later_eth_direction_and_actual_scope():
    cfg = {"primary_scope": "common", "arms": ["none", "sma20", "ema120"]}
    rows = [dict(scope="common", universe="all", direction="both", period="earlier", ma=ma, net_r=value)
            for ma, value in zip(cfg["arms"], [1., 2., -1.])]
    for field, value in [("scope", "actual"), ("universe", "ETH"), ("direction", "short"), ("period", "later")]:
        rows.append({**rows[0], field: value, "ma": "ema120", "net_r": 1000000.})
    assert select_ma(pd.DataFrame(rows), cfg) == "sma20"


def test_ties_choose_none_and_missing_arm_refuses_selection():
    cfg = {"primary_scope": "common", "arms": ["none", "sma20"]}
    rows = [dict(scope="common", universe="all", direction="both", period="earlier", ma=ma, net_r=0.) for ma in cfg["arms"]]
    assert select_ma(pd.DataFrame(rows[::-1]), cfg) == "none"
    with pytest.raises(ValueError):
        select_ma(pd.DataFrame(rows[:1]), cfg)


def test_trade_crossing_split_cannot_be_development_outcome():
    split = pd.Timestamp("2025-09-10", tz="UTC")
    rows = pd.DataFrame({"entry_time": ["2025-09-09", "2025-09-09", "2025-09-10"],
                         "exit_time": ["2025-09-09 23:59", "2025-09-10 00:00", "2025-09-11 00:00"]})
    assert assign_period(rows, split).tolist() == ["earlier", "cross_split", "later"]


def test_future_control_exit_is_not_early_evidence():
    split = pd.Timestamp("2025-09-10", tz="UTC")
    g = pd.DataFrame({"censored": [False, False], "matched": [True, True],
                      "control_censored": [False, False], "net_r": [1., 100.],
                      "control_net_r": [.2, -100.], "control_exit_time": ["2025-09-09", "2025-09-11"]})
    result = paired_controls(g, split, "earlier")
    assert result["random_pairs"] == 1
    assert result["random_excess_r"] == pytest.approx(.8)
