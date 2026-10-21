"""User-authorized earlier MTF events remain causal and bounded by key pairing."""
import pandas as pd
import pytest

from yoyo.evaluation.range_mtf_confluence_window import window_gate


def rows():
    return pd.DataFrame(dict(entry_time=['2026-10-01T06:15:00Z']*4,
        arrow_close=['2026-10-01T06:15:00Z','2026-10-01T06:00:00Z','2026-10-01T05:45:00Z','2026-10-01T05:45:00Z'],
        paired_arrow_was_mtf=[True,True,True,False],strict_both_now=[True,False,False,False]))


def test_same_and_prior_two_arrows_are_accepted_only_if_originally_eligible():
    d=rows();before=d.copy(deep=True)
    assert window_gate(d).tolist()==[True,True,True,False]
    pd.testing.assert_frame_equal(d,before)


@pytest.mark.parametrize('arrow',['2026-10-01T06:30:00Z','2026-10-01T05:30:00Z'])
def test_future_or_expired_arrows_rejected(arrow):
    d=rows();d.loc[0,'arrow_close']=arrow
    with pytest.raises(ValueError):window_gate(d)


def test_outcomes_cannot_change_the_gate():
    d=rows();expected=window_gate(d);d['net_r']=[100,-100,0,1000]
    pd.testing.assert_series_equal(window_gate(d),expected)
