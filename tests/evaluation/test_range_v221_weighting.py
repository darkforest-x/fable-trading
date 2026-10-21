"""Risk weighting and notional weighting can honestly have opposite signs."""
import pandas as pd
import pytest
from yoyo.evaluation.range_v221_weighting import weighting


def test_equal_notional_positive_does_not_imply_equal_risk_positive():
    rows=[]
    for population in ["signal","random"]:
        for i,(risk,bp) in enumerate([(2.,100.),(.1,-40.)]):
            net=bp/100 if population=="signal" else -.2
            rows.append(dict(pair_id=i,population=population,week=i,entry=100.,risk=risk,
                net_r=net/risk,net_notional_bp=net*100,fee_r=.2/risk))
    result=weighting(pd.DataFrame(rows),20261021)
    assert result["net_r"]==pytest.approx(-3.5)
    assert result["mean_net_bp"]==pytest.approx(30.)
    assert result["random_mean_net_bp"]==pytest.approx(-20.)
    assert result["wins"]==1
