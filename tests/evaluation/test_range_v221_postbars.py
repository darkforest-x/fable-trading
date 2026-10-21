"""Post-bar study contracts: Pine option bounds and delay-cost sign."""
import numpy as np
import pytest

from yoyo.evaluation.range_v221_postbars import check_config, delay_cost


@pytest.mark.parametrize("bars",[[0,1,2],[2],[1,2]])
def test_config_accepts_pine_options_with_default_anchor(bars):
    check_config(dict(post_bars=bars))


@pytest.mark.parametrize("bars",[[0,1],[0,3,2],[2,2],[-1,2]])
def test_config_rejects_missing_anchor_or_out_of_bounds(bars):
    with pytest.raises(ValueError):
        check_config(dict(post_bars=bars))


def test_delay_cost_is_positive_when_later_entry_is_worse_for_both_sides():
    bp,r=delay_cost([1,-1],[100.,100.],[101.,99.],[99.,101.])
    assert np.allclose(bp,[100.,100.]) and np.allclose(r,[.5,.5])


def test_delay_cost_is_negative_when_waiting_improves_price():
    bp,r=delay_cost([1,-1],[100.,100.],[99.,101.],[98.,102.])
    assert np.allclose(bp,[-100.,-100.]) and np.allclose(r,[-1.,-1.])
