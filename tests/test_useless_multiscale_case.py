"""Causality boundaries for the descriptive multiscale case."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.useless_multiscale_case import aggregate_complete, asof_closed, features


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
