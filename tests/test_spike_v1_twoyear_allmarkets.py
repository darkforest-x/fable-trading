import pandas as pd

from yoyo.evaluation.spike_v1_twoyear_allmarkets import END, START, WARMUP_START, aggregate, normalize


def test_frozen_two_year_window_ends_after_latest_closed_utc_day():
    assert START == pd.Timestamp("2024-09-11T00:00:00Z")
    assert END == pd.Timestamp("2026-09-11T00:00:00Z")
    assert WARMUP_START <= pd.Timestamp("2023-08-31T00:00:00Z")


def test_complete_utc_aggregation_discards_partial_group():
    index = pd.date_range("2025-01-01", periods=9, freq="30min", tz="UTC")
    frame = pd.DataFrame({"open":range(1,10), "high":range(2,11), "low":range(1,10), "close":range(1,10), "volume":1., "quote_volume":1.}, index=index)
    out = aggregate(frame, 240)
    assert len(out) == 1 and out.iloc[0].open == 1 and out.iloc[0].close == 8


def test_okx_unconfirmed_bar_is_excluded():
    left = pd.Timestamp("2025-01-01T00:00:00Z"); right = left + pd.Timedelta(hours=1)
    payload = {"data": [[str(left.value // 10**6), "1", "2", "1", "2", "3", "4", "5", "0"], [str((left + pd.Timedelta(minutes=30)).value // 10**6), "2", "3", "2", "3", "4", "5", "6", "1"]]}
    frame = normalize("okx", payload, left, right)
    assert len(frame) == 1 and frame.index[0] == left + pd.Timedelta(minutes=30)
