"""Synthetic independent-audit math checks, never saved economic rows."""
import importlib.util
from pathlib import Path
import math

import pytest

spec = importlib.util.spec_from_file_location("_v32_audit_test",
    Path(__file__).resolve().parents[1]/"scripts/audit_hourly_impulse_volume_wave_economics_v32.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_unknown_gate_is_not_abstention():
    assert audit.payoff("abstain", math.nan) == 0
    assert math.isnan(audit.payoff("unknown", .1))
    assert math.isnan(audit.payoff("accepted", math.nan))


def test_linear_quantile_and_all_controls():
    assert audit.quantile([1, 2, 8, 9], .25) == 1.75
    assert audit.average([.1, -.2, .4]) == pytest.approx(.1)
    assert math.isnan(audit.average([.1, math.nan, .2]))


def test_audit_detects_cost_or_number_mismatch():
    audit.equal(.03-.002, .028)
    with pytest.raises(AssertionError):
        audit.equal(.03-.002, .029)
    with pytest.raises(AssertionError):
        audit.equal(math.inf, math.inf)


def test_saved_month_indices_not_iid_trades():
    values, months = [.04, -.02, .01], ["2023-01", "2023-01", "2023-02"]
    ci, p, sums, counts = audit.inference(values, months,
        [[0]*24, [1]*24], [[1]*24, [-1]*24])
    assert sums[:2] == pytest.approx([.02, .01])
    assert counts[:2] == [2, 1]
    assert ci == pytest.approx([.01, .01])
    assert p == pytest.approx(2/3)
