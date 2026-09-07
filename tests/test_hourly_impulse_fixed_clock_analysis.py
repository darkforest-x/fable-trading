"""Synthetic distribution checks; never open real labels or market data."""
import json
import numpy as np
import pytest
from yoyo.evaluation.hourly_impulse_fixed_clock_analysis import diagnose


def check(x, **kwargs):
    assert kwargs["plot"] is False
    return {"test":"Shapiro-Wilk","n":len(x),"statistic":.9,"p_value":.03}


def test_all_tails_and_missing_retained():
    r=diagnose([.001,.002,.003,5.,np.nan],["a","b","c","tail","unknown"],check)
    assert r["total"]==5 and r["known"]==4 and r["unknown"]==1
    assert r["mean_bp"]==pytest.approx(12515.)
    assert r["outlier_event_ids"]==["tail"] and r["outliers_removed"]==0
    json.dumps(r,allow_nan=False)


@pytest.mark.parametrize("values", [[np.inf],[-np.inf],[1e308]])
def test_invalid_numeric_not_silent_unknown(values):
    with np.errstate(over="ignore"), pytest.raises(ValueError):
        diagnose(values,["a"],check)


def test_empty_and_constant_have_honest_diagnostic_status():
    assert diagnose([np.nan],["a"],check)["normality"]["status"]=="insufficient_observations"
    assert diagnose([0.,0.,0.],["a","b","c"],check)["normality"]["status"]=="constant"


def test_wrong_utility_population_rejected():
    with pytest.raises(ValueError):
        diagnose([1.,2.,3.],["a","b","c"],lambda *args,**kwargs:{"test":"Shapiro-Wilk","n":2})
