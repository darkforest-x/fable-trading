"""Selection, censoring bounds, and full-stop denominators for exit research."""
import pandas as pd
import pytest
from yoyo.evaluation.spike_v8_ict_exit_study import choose_development,cutoff_index,outcome_stats


def test_selection_excludes_small_sample_and_does_not_require_a_positive_winner():
    rows=[dict(window='development',policy='tiny',natural=2,sum_net_r=99),
          dict(window='development',policy='original',natural=40,sum_net_r=-2),
          dict(window='development',policy='worse',natural=50,sum_net_r=-5)]
    assert choose_development(rows)['policy']=='original'


def test_selection_rejects_later_window_even_if_it_would_not_win():
    with pytest.raises(ValueError):
        choose_development([dict(window='common',policy='x',natural=50,sum_net_r=-10)])


def test_cutoff_excludes_the_cutpoint_bar():
    index=pd.date_range('2024-12-31T23:30:00Z',periods=5,freq='15min')
    assert cutoff_index(index,'2025-01-01T00:00:00Z')==2


def test_partial_residual_initial_stop_does_not_extend_full_stop_streak():
    def r(partial):
        return dict(censored=False,net_r=-.2,gross_r=-.1,mfe_r=2.,exit_reason='initial_stop',
                    full_initial_stop=not partial,partial_executed=partial)
    s=outcome_stats([r(False),r(True),r(False)])
    assert s['max_net_loss_streak']==3
    assert s['max_initial_stop_streak']==1 and s['initial_stops']==2
