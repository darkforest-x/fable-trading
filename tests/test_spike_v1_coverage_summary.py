import pandas as pd

from yoyo.evaluation.spike_v1_coverage_summary import _source_scope, _summary


def test_source_scope_uses_frozen_start_not_the_report_clock():
    assert _source_scope("2024-09-10 00:00:00+00:00..2026-09-09 23:30:00+00:00") == "full_to_frozen_start"
    assert _source_scope("2025-01-01 00:00:00+00:00..2026-09-09 23:30:00+00:00") == "partial_after_frozen_start"
    assert _source_scope("missing") == "unknown_source_window"


def test_summary_excludes_censored_row_from_realized_fields():
    frame = pd.DataFrame([
        {"venue": "okx", "timeframe_min": 60, "censored": False, "net_return": .1, "net_r": .5},
        {"venue": "okx", "timeframe_min": 60, "censored": False, "net_return": -.2, "net_r": -1.},
        {"venue": "okx", "timeframe_min": 60, "censored": True, "net_return": .9, "net_r": 9.},
    ])
    row = _summary(frame, ["venue", "timeframe_min"]).iloc[0]
    assert row.signal_rows == 3
    assert row.realized_rows == 2
    assert row.censored_rows == 1
    assert row.realized_net_return == -.1
    assert row.realized_profit_factor == .5
