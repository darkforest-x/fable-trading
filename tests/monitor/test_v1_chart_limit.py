"""Display truncation cannot change frozen V1 replay, state, or raw events."""
from __future__ import annotations

import hashlib
import json

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


def test_frozen_full_history_output_hash_survives_display_materialization_change():
    """A 720-bar V1 replay stays byte-stable while its chart uses column arrays."""
    result = analyze(candles(), [], "1H", tick=.01, chart_limit=240)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == (
        "2ecb21f572e9403dd00fd8733626e9621285e63a978d37e891a2b1da2d135bec"
    )
