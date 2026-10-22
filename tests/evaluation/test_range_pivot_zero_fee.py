"""A profitable pivot reference can still exit at a losing confirmation close."""
import pandas as pd
import pytest
from yoyo.evaluation.range_pivot_zero_fee import zero_fee_pivot


def test_fee_restatement_keeps_losing_pivot_fill_and_original_commission():
    rows=pd.DataFrame([dict(reason=7,be_effective_i=-1,entry=100.,risk=1.,gross_r=-.1,
        fee_r=.2,net_r=-.3,net_notional_bp=-30.,framework_commission=.2,
        ambiguous=0,exit_i=20,exit_price=99.9)])
    original=rows.copy(deep=True)
    result=zero_fee_pivot(rows)
    pd.testing.assert_frame_equal(rows,original)
    assert result.net_r.iloc[0]==-.1 and result.exit_price.iloc[0]==99.9
    assert result.fee_r.iloc[0]==0 and result.source_fee_r.iloc[0]==.2
    assert result.framework_commission.iloc[0]==.2


def test_fee_dependent_be_cannot_reuse_pivot_accounting_path():
    rows=pd.DataFrame([dict(reason=7,be_effective_i=9)])
    with pytest.raises(ValueError,match='without BE'):
        zero_fee_pivot(rows)
