"""Is this bar series continuous, and is its tail actually closed?

Adapted from darkforest-one src/darkforest_one/data/validator.py at fd36dd1.
Three changes, all forced by where it now lives:

  - it works on the pandas frames this repository's loader returns, not on a
    tuple of Bar dataclasses. C3.1 of the consolidation task book requires
    migrated modules to reach data through the canonical interface rather than
    carrying their own representation.
  - Python 3.9: no datetime.UTC, no slots=True.
  - gaps are reported, not raised. darkforest-one was ingesting one symbol and
    could refuse the whole series; this repository scans 200+ symbols whose
    listing dates differ, and a raise would mean the newest symbol takes down
    the scan. The count and the census come back; refusing is the caller's
    decision, and `assert_continuous` is there when the caller is a builder that
    should refuse.

The availability question is the one that matters at the live edge, and it is
separate from continuity: a series can be perfectly continuous and still end on
a bar that has not closed yet. `latest_closed_boundary` is the arithmetic that
says which bar the current instant permits, and it is the same arithmetic the
freshness gates derive from
(docs/learnings/freshness-gates-must-be-derived-from-pipeline-arithmetic.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd


class ContinuityError(ValueError):
    """A bar series that cannot be used as-is. Never downgraded to a warning."""


@dataclass(frozen=True)
class Gap:
    after: pd.Timestamp
    before: pd.Timestamp
    missing_bars: int


@dataclass
class ContinuityReport:
    n_bars: int
    start: Optional[pd.Timestamp]
    end: Optional[pd.Timestamp]
    interval: pd.Timedelta
    n_duplicates: int
    gaps: List[Gap] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def continuous(self) -> bool:
        return not self.gaps and self.n_duplicates == 0

    @property
    def missing_bars(self) -> int:
        return sum(gap.missing_bars for gap in self.gaps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_bars": self.n_bars,
            "start": None if self.start is None else str(self.start),
            "end": None if self.end is None else str(self.end),
            "interval_minutes": self.interval.total_seconds() / 60.0,
            "n_duplicates": self.n_duplicates,
            "n_gaps": len(self.gaps),
            "missing_bars": self.missing_bars,
            "continuous": self.continuous,
            "gaps": [
                {"after": str(g.after), "before": str(g.before), "missing_bars": g.missing_bars}
                for g in self.gaps[:50]
            ],
            "notes": list(self.notes),
        }


def check_continuity(
    bars: pd.DataFrame,
    *,
    interval: pd.Timedelta,
    time_column: str = "open_time",
    max_gaps_recorded: int = 500,
) -> ContinuityReport:
    """Census the series: duplicates, ordering, and every hole in the grid.

    Does not modify or fill anything. A filled gap is indistinguishable from
    data once it is downstream, which is the whole reason this returns a report
    rather than a repaired frame.
    """
    if interval <= pd.Timedelta(0):
        raise ContinuityError("interval must be positive")
    if time_column not in bars.columns:
        raise ContinuityError(f"no {time_column!r} column to check continuity on")

    times = pd.to_datetime(bars[time_column], utc=True)
    report = ContinuityReport(
        n_bars=len(bars),
        start=None,
        end=None,
        interval=interval,
        n_duplicates=0,
    )
    if times.empty:
        report.notes.append("empty series")
        return report

    if not times.is_monotonic_increasing:
        report.notes.append("timestamps were not sorted; census computed on a sorted copy")
        times = times.sort_values(kind="mergesort")

    n_before = len(times)
    times = times.drop_duplicates()
    report.n_duplicates = n_before - len(times)
    if report.n_duplicates:
        report.notes.append(f"{report.n_duplicates} duplicate timestamps")

    report.start = times.iloc[0]
    report.end = times.iloc[-1]

    deltas = times.diff().dropna()
    irregular = deltas[deltas != interval]
    for index, delta in list(irregular.items())[:max_gaps_recorded]:
        position = times.index.get_loc(index)
        report.gaps.append(
            Gap(
                after=times.iloc[position - 1],
                before=times.iloc[position],
                missing_bars=int(delta / interval) - 1,
            )
        )
    if len(irregular) > max_gaps_recorded:
        report.notes.append(
            f"{len(irregular)} irregular steps, first {max_gaps_recorded} recorded"
        )
    return report


def assert_continuous(report: ContinuityReport, *, what: str = "bar series") -> None:
    """Refuse a series with holes. For builders, where a hole is a wrong answer."""
    if report.continuous:
        return
    raise ContinuityError(
        f"{what} is not continuous: {len(report.gaps)} gaps totalling "
        f"{report.missing_bars} missing bars, {report.n_duplicates} duplicates. "
        "Gaps are never filled -- a filled bar is indistinguishable from data "
        "once it is downstream."
    )


def latest_closed_boundary(*, as_of: datetime, interval: timedelta) -> datetime:
    """The close time of the newest bar that has certainly finished at `as_of`.

    Floor division on the epoch rather than anything calendar-aware, which is
    what makes it agree with the exchange's own grid.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ContinuityError("as_of must be timezone-aware")
    interval_seconds = int(interval.total_seconds())
    if interval_seconds <= 0 or interval.total_seconds() != interval_seconds:
        raise ContinuityError("interval must be a whole positive number of seconds")
    epoch = int(as_of.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % interval_seconds), tz=timezone.utc)


def assert_closed_tail(
    bars: pd.DataFrame,
    *,
    as_of: datetime,
    interval: pd.Timedelta,
    time_column: str = "open_time",
    stale_after_bars: int = 2,
) -> None:
    """Refuse a series whose last bar has not closed, or closed too long ago.

    Two different failures with one check, because both mean the same thing to
    a caller about to make a decision: the newest row is not a fact yet, or the
    newest fact is too old to act on.
    """
    if time_column not in bars.columns or bars.empty:
        raise ContinuityError("cannot check the tail of an empty series")
    if stale_after_bars < 0:
        raise ContinuityError("stale_after_bars cannot be negative")

    times = pd.to_datetime(bars[time_column], utc=True)
    last_open = times.max()
    last_close = last_open + interval
    boundary = pd.Timestamp(latest_closed_boundary(as_of=as_of, interval=interval.to_pytimedelta()))

    if last_close > boundary:
        raise ContinuityError(
            f"the newest bar opens at {last_open} and closes at {last_close}, past the "
            f"latest closed boundary {boundary}: it is still forming"
        )
    lag = boundary - last_close
    if lag > interval * stale_after_bars:
        raise ContinuityError(
            f"the newest closed bar is {int(lag / interval)} bars behind the boundary "
            f"{boundary} (limit {stale_after_bars})"
        )
