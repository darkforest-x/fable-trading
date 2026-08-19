"""Continuity says the history is whole; availability says the edge is real.

They are different questions and this suite keeps them apart, because a series
can be perfectly continuous and still end on a bar that has not closed yet --
which is the one that matters when a decision is about to be made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from yoyo.data.continuity import (
    ContinuityError,
    assert_closed_tail,
    assert_continuous,
    check_continuity,
    latest_closed_boundary,
)

INTERVAL = pd.Timedelta(minutes=15)


def _bars(n=100, start="2026-03-01T00:00:00Z"):
    return pd.DataFrame({"open_time": pd.date_range(start, periods=n, freq="15min", tz="UTC")})


def test_a_whole_series_reports_continuous():
    report = check_continuity(_bars(), interval=INTERVAL)
    assert report.continuous
    assert report.missing_bars == 0
    assert report.n_bars == 100


def test_a_hole_is_counted_not_filled():
    bars = _bars(20)
    bars = bars.drop(index=[10, 11]).reset_index(drop=True)
    report = check_continuity(bars, interval=INTERVAL)
    assert not report.continuous
    assert report.missing_bars == 2
    assert len(report.gaps) == 1
    assert report.gaps[0].missing_bars == 2
    # the frame is untouched: a filled bar is indistinguishable from data
    assert len(bars) == 18


def test_duplicates_are_reported():
    bars = pd.concat([_bars(10), _bars(10).iloc[[3]]], ignore_index=True)
    report = check_continuity(bars, interval=INTERVAL)
    assert report.n_duplicates == 1
    assert not report.continuous


def test_a_builder_can_refuse_a_holed_series():
    bars = _bars(20).drop(index=[7]).reset_index(drop=True)
    report = check_continuity(bars, interval=INTERVAL)
    with pytest.raises(ContinuityError, match="not continuous"):
        assert_continuous(report, what="the ETH scan window")


def test_scanning_many_symbols_does_not_raise_on_the_newest_one():
    """The deliberate divergence from darkforest-one, stated as a test.

    A symbol listed halfway through the window has a hole by construction. If
    check_continuity raised, one new listing would take down a 200-symbol scan.
    """
    bars = _bars(50).drop(index=range(0, 20)).reset_index(drop=True)
    report = check_continuity(bars, interval=INTERVAL)
    assert report.continuous  # a late start is not a hole
    assert report.n_bars == 30


def test_report_serialises_for_a_manifest():
    payload = check_continuity(_bars(30).drop(index=[5]).reset_index(drop=True), interval=INTERVAL).to_dict()
    assert payload["continuous"] is False
    assert payload["missing_bars"] == 1
    assert payload["interval_minutes"] == 15.0


# -- availability ----------------------------------------------------------

def test_the_closed_boundary_floors_to_the_grid():
    as_of = datetime(2026, 3, 1, 12, 7, 33, tzinfo=timezone.utc)
    assert latest_closed_boundary(as_of=as_of, interval=timedelta(minutes=15)) == datetime(
        2026, 3, 1, 12, 0, tzinfo=timezone.utc
    )


def test_an_exact_boundary_instant_is_its_own_boundary():
    as_of = datetime(2026, 3, 1, 12, 15, tzinfo=timezone.utc)
    assert latest_closed_boundary(as_of=as_of, interval=timedelta(minutes=15)) == as_of


def test_a_naive_as_of_is_refused():
    with pytest.raises(ContinuityError, match="timezone-aware"):
        latest_closed_boundary(as_of=datetime(2026, 3, 1, 12, 0), interval=timedelta(minutes=15))


def test_a_still_forming_tail_is_refused():
    """The bar opened but has not closed; its OHLC is not a fact yet."""
    bars = _bars(10, start="2026-03-01T00:00:00Z")
    as_of = datetime(2026, 3, 1, 2, 20, tzinfo=timezone.utc)  # last bar opens 02:15
    with pytest.raises(ContinuityError, match="still forming"):
        assert_closed_tail(bars, as_of=as_of, interval=INTERVAL)


def test_a_closed_tail_is_accepted():
    bars = _bars(10, start="2026-03-01T00:00:00Z")  # last bar 02:15-02:30
    as_of = datetime(2026, 3, 1, 2, 31, tzinfo=timezone.utc)
    assert_closed_tail(bars, as_of=as_of, interval=INTERVAL) is None


def test_a_stale_tail_is_refused_with_its_lag():
    bars = _bars(10, start="2026-03-01T00:00:00Z")
    as_of = datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)  # ~14 bars behind
    with pytest.raises(ContinuityError, match="bars behind the boundary"):
        assert_closed_tail(bars, as_of=as_of, interval=INTERVAL, stale_after_bars=2)
