"""Mechanics of the v2 sync-shock arms: trend exit, clusters and cooldown."""
import numpy as np
import pytest

from yoyo.evaluation import market_sync_shock_v2 as m


def test_initial_stop_is_two_atr_from_entry():
    gross, mfe = m.trend_exit(100.0, np.array([99.5, 97.9, 120.0]), 1.0, 1)
    # the tail metric covers the whole capped path ("did a big move happen"), not only until exit
    assert gross == pytest.approx(-0.021) and mfe == pytest.approx(0.2)


def test_trail_uses_best_close_before_the_bar_and_records_mfe():
    # best close 105 before the last bar -> trail 102; 101.9 exits.  Bar 0's stop is still 98.
    gross, mfe = m.trend_exit(100.0, np.array([101.0, 104.0, 105.0, 101.9]), 1.0, 1)
    assert gross == pytest.approx(0.019) and mfe == pytest.approx(0.05)


def test_no_hit_exits_at_the_capped_path_end_and_gaps_truncate():
    assert m.trend_exit(100.0, np.array([101.0, 102.0]), 1.0, 1)[0] == pytest.approx(0.02)
    assert m.trend_exit(100.0, np.array([101.0, np.nan, 150.0]), 1.0, 1)[0] == pytest.approx(0.01)
    assert np.isnan(m.trend_exit(100.0, np.array([np.nan]), 1.0, 1)[0])


def test_short_mirror():
    gross, mfe = m.trend_exit(100.0, np.array([99.0, 96.0, 95.0, 98.1]), 1.0, -1)
    assert gross == pytest.approx(0.019) and mfe == pytest.approx(0.05)


def test_cluster_fires_on_the_second_same_side_bar_only():
    hits = np.array([0, 1, 0, 1, 0, 0, -1, -1, 1, 0])
    ev = m.events_from_hits(hits, "cluster", cooldown=0)
    assert ev.i.tolist() == [3, 7] and ev.side.tolist() == [1, -1]
    assert m.events_from_hits(hits, "sync", cooldown=0).i.tolist() == [1, 3, 6, 7, 8]


def test_cooldown_blocks_new_events_for_the_window():
    hits = np.array([1, 1, 1, 0, 0, 1, 0, 0])
    assert m.events_from_hits(hits, "sync", cooldown=4).i.tolist() == [0, 5]
