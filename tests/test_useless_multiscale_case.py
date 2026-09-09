"""Causality boundaries for the descriptive multiscale case."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.useless_multiscale_case import aggregate_complete, asof_closed, audit_paths, features


def bars(n=800, frequency="15min"):
    rng = np.random.default_rng(18)
    close = 10 + np.cumsum(rng.normal(0, .01, n))
    return pd.DataFrame({"open": close-.01, "high": close+.02, "low": close-.02,
                         "close": close, "volume": rng.uniform(1, 20, n)},
                        index=pd.date_range("2026-08-01", periods=n, freq=frequency, tz="UTC"))


def test_incomplete_4h_groups_are_dropped_and_asof_never_reads_partial():
    raw = bars(35)
    aggregate = aggregate_complete(raw, 240)
    assert len(aggregate) == 2
    row = asof_closed(aggregate, 240, pd.Timestamp("2026-08-01T05:00Z"))
    assert row.open_time == "2026-08-01T00:00:00+00:00"
    assert row.close_time == "2026-08-01T04:00:00+00:00"
    assert row.close == raw.close.iloc[15]


def test_gap_fails_instead_of_aggregating_missing_candles():
    with pytest.raises(ValueError, match="gap"):
        aggregate_complete(bars().drop(bars().index[20]), 60)


def test_features_prefix_invariant_and_volume_baseline_excludes_signal():
    raw = bars()
    full, prefix = features(raw), features(raw.iloc[:500])
    pd.testing.assert_frame_equal(full.iloc[:500], prefix)
    assert full.volume_mean20_prior.iloc[500] == pytest.approx(raw.volume.iloc[480:500].mean())


def test_momentum_is_current_normalized_deviation_not_return():
    raw = bars()
    result = features(raw)
    deviation = raw.close-raw.close.rolling(50).mean()
    expected = deviation/deviation.abs().rolling(50, min_periods=1).max()*100
    pd.testing.assert_series_equal(result.momentum10, expected, check_names=False)
    assert result.momentum10.dropna().abs().max() <= 100 + 1e-10


def test_protection_breach_count_stops_before_episode_termination():
    # The MD-zero bar and the later low are outside the active episode even
    # though both would breach a hypothetical continued SMA20 ratchet.
    frame = pd.DataFrame({"close": [10., 8., 10., 8., 7.], "md": [1., 1., 1., 0., 0.],
                          "release_side": [1, 0, 0, 0, 0]},
                         index=pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC"))
    frame["open"] = frame.close
    frame["high"] = frame.close + .1
    frame["low"] = frame.close - .1
    for name in ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120"):
        frame[name] = 9.
    for name in ("pine_initial_stop_v27", "legacy_signal_bar_stop", "research_stop_2atr", "release_zone_low"):
        frame[name] = 1.
    path = audit_paths(frame, frame.index[0])
    assert path["sma20_ratchet_first_breach"]["open_time"] == frame.index[1]
    assert path["pine_episode_end_ignoring_initial_stop"]["open_time"] == frame.index[3]
    assert path["sma20_ratchet_breach_bars"] == 1
    assert path["all_followup_breach_bars"] == 3
