"""Synthetic checks for streak boundaries and stop-versus-loss semantics."""
import pandas as pd
from yoyo.evaluation.spike_v8_eth_baseline_stats import runs, masks


def test_runs_preserve_terminal_streak_and_breaks():
    assert runs([True, True, False, True, False, True, True, True]) == [(0,1),(3,3),(5,7)]
    assert runs([]) == []
    assert runs([False, False]) == []


def test_profitable_trailing_stop_is_not_losing_stop():
    frame = pd.DataFrame({'exit_reason':['initial_stop','trailing_stop','opposite_v6_next_open','trailing_stop'],
                          'net_r':[-1.2,3.0,-0.4,-0.2], 'gross_r':[-1.0,3.2,-0.2,0.1]})
    result=masks(frame)
    assert result['net_loss_stop'].tolist() == [True,False,False,True]
    assert result['gross_loss_stop'].tolist() == [True,False,False,False]
    assert result['initial_stop'].tolist() == [True,False,False,False]


def test_fold_streaks_are_not_joined():
    assert max(b-a+1 for fold in [[True,True],[True,True,True]] for a,b in runs(fold)) == 3
