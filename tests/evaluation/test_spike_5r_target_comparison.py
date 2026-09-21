"""A target-only comparison must preserve every non-model path."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_5r_target_comparison import assert_control_parity


def test_model_paths_can_change_but_control_paths_cannot():
    old = pd.DataFrame(dict(rule=['original_all', 'risk_top5', 'logistic_top5'],
                            event_key=['a', 'b', 'c'], net_r=[6., 10., 8.]))
    new = old.copy()
    new.loc[2, 'event_key'] = 'new_model_entry'
    new.loc[2, 'net_r'] = -1.
    assert_control_parity(old, new.iloc[::-1])
    new.loc[1, 'net_r'] = 10.001
    with pytest.raises(AssertionError):
        assert_control_parity(old, new)


def test_lost_original_entry_is_detected_even_if_mean_is_same():
    old = pd.DataFrame(dict(rule=['original_all', 'original_all'],
                            event_key=['a', 'b'], net_r=[6., 6.]))
    with pytest.raises(AssertionError):
        assert_control_parity(old, old.iloc[:1])
