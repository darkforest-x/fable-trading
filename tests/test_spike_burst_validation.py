"""Verify family-size and empty-candidate behavior without market outcomes."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_burst_validation import primary_holm, evaluate_account, event_summary


def test_missing_primary_tests_do_not_shrink_family():
    values=np.array([.001]+[np.nan]*15)
    result=primary_holm(values)
    assert result[0]==pytest.approx(.016)
    assert np.isnan(result[1:]).all()


def test_primary_family_requires_sixteen():
    with pytest.raises(ValueError,match='sixteen'):
        primary_holm([.01,.02])


def test_empty_account_is_flat_and_not_a_win():
    result,curve,ledger=evaluate_account(pd.DataFrame(),{},240)
    assert result['return_pct']==0
    assert result['max_drawdown_pct']==0
    assert result['trades']==0
    assert np.isnan(result['win_rate'])
    assert curve.equity.eq(100000).all()
    assert ledger.empty


def test_empty_object_event_schema_reports_unavailable():
    columns='event_id valid net_bp gross_bp net_r net_return peak_r initial_risk_frac relative_volume tr_expansion natural_exit censored exit_reason'.split()
    actual=pd.DataFrame(columns=columns)
    controls=pd.DataFrame(columns=columns+['matched_event_id','control_number'])
    result,scores=event_summary(actual,controls)
    assert result['valid']==0
    assert np.isnan(result['permutation_p'])
    assert scores==[]
