import pandas as pd

from scripts.build_owner_short_hardneg_canary_review import future_bar_count


def test_future_bar_count_caps_review_at_48_bars() -> None:
    assert future_bar_count(
        "2026-05-03T00:00:00Z", "2026-05-03T23:45:00Z"
    ) == 48


def test_future_bar_count_shortens_at_physical_prefix_end() -> None:
    assert future_bar_count(
        pd.Timestamp("2026-05-03T12:00:00Z"),
        pd.Timestamp("2026-05-03T23:45:00Z"),
    ) == 47
