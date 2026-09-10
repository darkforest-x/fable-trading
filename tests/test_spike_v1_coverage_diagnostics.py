"""Contract checks for descriptive, current-V1 coverage diagnostics."""
import pandas as pd

from yoyo.evaluation.spike_v1_coverage_diagnostics import V1_TIMEFRAME_MINUTES, _v1_only


def test_coverage_diagnostics_excludes_legacy_daily_rows():
    frame = pd.DataFrame({"timeframe_min": [30, 60, 240, 1440], "event_id": list("abcd")})

    kept = _v1_only(frame)

    assert tuple(kept["timeframe_min"]) == V1_TIMEFRAME_MINUTES
    assert tuple(kept["event_id"]) == ("a", "b", "c")
