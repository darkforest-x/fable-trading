"""Contract tests for the fixed 15-day 4h YOLO experiment."""

from scripts.scan_4h_ma_launch_yolo_half_month import (
    HOLDOUT_CONSUMPTION_NUMBER,
    LOOKBACK_ENDPOINTS,
    daily_event_counts,
)


def test_half_month_is_exactly_ninety_four_hour_endpoints() -> None:
    assert LOOKBACK_ENDPOINTS == 15 * 24 // 4 == 90
    assert HOLDOUT_CONSUMPTION_NUMBER == 6


def test_daily_event_onset_uses_beijing_close_date() -> None:
    counts = daily_event_counts(
        [
            {
                "first_available_at": "2026-08-17T16:00:00+00:00",
                "class_name": "dense_long",
            },
            {
                "first_available_at": "2026-08-18T15:59:59+00:00",
                "class_name": "dense_short",
            },
        ]
    )

    assert counts == {
        "2026-08-18": {"events": 2, "long": 1, "short": 1},
    }
