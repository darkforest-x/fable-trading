"""Independent-auditor arithmetic contracts; no actual outcomes."""
import importlib.util
import math
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("_audit_v30", Path(__file__).resolve().parents[1]/"scripts/audit_hourly_impulse_classifier_economics_v30.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_unknown_and_abstention():
    assert audit.payoff("abstain", math.nan) == 0
    assert math.isnan(audit.payoff("unknown", .1))
    assert math.isnan(audit.payoff("accepted", math.nan))
    assert audit.payoff("accepted", -.1) == -.1


def test_quantile_interpolation():
    assert audit.quantile([4, 1, 2, 3], .25) == 1.75
    assert audit.quantile([], .5) is None


def test_comparison_fails_closed():
    audit.equal(math.nan, None)
    with pytest.raises(AssertionError):
        audit.equal(math.nan, 0)
    with pytest.raises(AssertionError):
        audit.equal(.002, .001)


def test_spread_check_uses_actual_summary_schema():
    saved = dict(n_total=2, n_known=2, n_unknown=0, mean=1.5, sum=3., median=1.5,
        minimum=1., maximum=2., sd=math.sqrt(.5), q05=1.05, q25=1.25, q75=1.75,
        q95=1.95, n_positive=2, n_negative=0, n_zero=0)
    audit.check_description([1., 2.], saved)
    with pytest.raises(AssertionError):
        audit.check_description([1., 2.], dict(saved, sd=0.))
