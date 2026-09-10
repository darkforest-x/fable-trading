"""Display truncation cannot change frozen V1 replay, state, or raw events."""
from __future__ import annotations

from yoyo.monitor.signals import analyze


def candles(count=720, step=3_600_000):
    return [{"t": index * step, "o": 100. + index / 1000, "h": 101. + index / 1000,
             "l": 99. + index / 1000, "c": 100. + index / 1000, "v": 10.}
            for index in range(count)]


def test_chart_limit_only_skips_discarded_display_rows():
    full = analyze(candles(), [], "1H", tick=.01)
    compact = analyze(candles(), [], "1H", tick=.01, chart_limit=240)
    assert compact["state"] == full["state"]
    assert compact["events"] == full["events"]
    assert compact["chart"] == full["chart"][-240:]
