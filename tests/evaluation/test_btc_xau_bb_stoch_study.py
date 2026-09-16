"""Bar-duration handling: gaps, confirmed-close clock and hold length."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import btc_xau_bb_stoch_study as study
from yoyo.evaluation.bb_stoch_parameter_replay import compute_features
from yoyo.evaluation.eth_bb_stoch_study import enrich, match_context


def minute_bars(n=300, minutes=1):
    index = pd.date_range("2026-01-01", periods=n, freq=f"{minutes}min", tz="UTC")
    close = 100 + np.sin(np.arange(n) / 7)
    return pd.DataFrame(dict(open=close, high=close + .2, low=close - .2, close=close), index=index)


def test_one_minute_series_is_not_treated_as_all_gaps():
    frame = minute_bars()
    # Without a declared duration the engine's five-minute default would mark
    # every bar a discontinuity and no band would ever warm up.
    default = compute_features(frame)
    assert default["_data_gap"].iloc[1:].all() and default.upper.isna().all()
    frame["_data_gap"] = study.declared_gaps(frame.index, 1)
    declared = compute_features(frame)
    assert not declared["_data_gap"].iloc[1:].any()
    assert declared.upper.iloc[199:].notna().all()


def test_declared_gaps_marks_only_real_discontinuities():
    frame = minute_bars(50)
    cut = frame.drop(frame.index[20:25])
    gaps = study.declared_gaps(cut.index, 1)
    assert gaps.sum() == 1 and bool(gaps.iloc[20]) and not bool(gaps.iloc[0])


def test_confirmed_close_and_hold_length_follow_the_bar_duration():
    frame = minute_bars()
    frame["_data_gap"] = study.declared_gaps(frame.index, 1)
    features = compute_features(frame)
    one = match_context(features, 1)
    five = match_context(features, 5)
    assert (one["confirmed"] - features.index).max() == pd.Timedelta(minutes=1)
    assert (five["confirmed"] - features.index).max() == pd.Timedelta(minutes=5)
    row = dict(signal_i=10, entry_i=11, exit_i=70, censored=False)
    assert enrich(row, features, 1)["hold_hours"] == pytest.approx(60 / 60)
    assert enrich(row, features, 5)["hold_hours"] == pytest.approx(60 * 5 / 60)
    # The default must stay the five-minute behaviour every frozen study uses.
    assert enrich(row, features)["hold_hours"] == enrich(row, features, 5)["hold_hours"]


def test_one_minute_source_is_accepted_by_the_frozen_reader_without_moving_the_boundary():
    from yoyo.data.spike_fanshen_prefix import read_prefix
    with pytest.raises(ValueError, match="holdout"):
        read_prefix("unused", 1, "2026-06-01T00:00Z")
    with pytest.raises(ValueError, match="unsupported native source duration"):
        read_prefix("unused", 2, "2026-04-01T00:00Z")
