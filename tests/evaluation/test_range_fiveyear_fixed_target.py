"""Cost-free cache reuse must preserve fills and reject cost-dependent policies."""
import pandas as pd
import pytest
from yoyo.evaluation.range_fiveyear_fixed_target import restate_static_fees, validate_static_boundaries


def test_zero_fee_restatement_preserves_original_evidence_and_boundary_weight():
    old = pd.DataFrame([dict(entry=100., risk=1., gross_r=10., fee_r=.2, net_r=9.8,
        net_notional_bp=980., boundary_weight=0., boundary_net_r=0., exit_i=55),
        dict(entry=100., risk=1., gross_r=.1, fee_r=.2, net_r=-.1,
        net_notional_bp=-10., boundary_weight=1., boundary_net_r=-.1, exit_i=99)])
    original = old.copy(deep=True)
    result = restate_static_fees(old, {'name':'fixed_10r', 'target_r':10}, 0.)
    pd.testing.assert_frame_equal(old, original)
    assert result.exit_i.tolist() == [55,99]
    assert result.net_r.tolist() == [10.,.1]
    assert result.fee_r.tolist() == [0.,0.]
    assert result.source_fee_r.tolist() == [.2,.2]
    assert result.source_net_r.tolist() == [9.8,-.1]
    assert result.boundary_net_r.tolist() == [0.,.1]


def test_cost_dependent_breakeven_cannot_be_relabelled_as_zero_fee_replay():
    with pytest.raises(ValueError, match='static targets only'):
        restate_static_fees(pd.DataFrame(), {'name':'be_cost', 'target_r':10, 'be_r':1}, 0.)


def test_cutoff_mark_remains_distinct_from_target_when_horizon_is_unchanged():
    rows = pd.DataFrame([dict(boundary_weight=1., exit_i=99, end_i=99, gross_r=3.187)])
    validate_static_boundaries(rows)
    assert rows.boundary_weight.iloc[0] == 1. and rows.gross_r.iloc[0] == 3.187


def test_earlier_forced_exit_cannot_be_reused_as_full_horizon_execution():
    rows = pd.DataFrame([dict(boundary_weight=1., exit_i=98, end_i=99)])
    with pytest.raises(AssertionError, match='precedes'):
        validate_static_boundaries(rows)
