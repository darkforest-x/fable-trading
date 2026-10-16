"""Strict wick definition, complete confirmation, timing and no future leakage."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import key_level_wick_reclaim as study


def raw_data():
    return pd.DataFrame(dict(ts=np.arange(20) * study.MINUTE,
        open=101., high=102., low=100.5, close=101., volume=1.))


def contact(side=1, price=100., touch=2):
    return pd.DataFrame([dict(level_id="test", price=price, side=side,
        touch_ms=touch * study.MINUTE, touch_index=touch, active_ms=0,
        expires_ms=20 * study.MINUTE, entry_index=touch + 1)])


def test_long_wick_confirms_at_close_and_enters_next_bar():
    raw = raw_data()
    raw.loc[2, "low"] = 99
    found, audit = study.signal_candles(raw, contact())
    assert found.wick_reclaim.iloc[0]
    assert audit == {"wick_reclaim": 1}
    assert found.confirmed_ms.iloc[0] == 5 * study.MINUTE
    assert found.entry_index.iloc[0] == 5
    assert found.signal_low.iloc[0] == 99


@pytest.mark.parametrize("field,row,value", [("low", 2, 100.), ("close", 4, 100.),
    ("close", 4, 99.), ("open", 0, 100.), ("open", 0, 99.)])
def test_equal_touch_no_reclaim_or_gap_body_is_not_a_wick(field, row, value):
    raw = raw_data()
    raw.loc[2, "low"] = 99
    raw.loc[row, field] = value
    found, _ = study.signal_candles(raw, contact())
    assert not found.wick_reclaim.iloc[0]
    assert found.entry_index.iloc[0] == 5  # Comparator waits equally long.


def test_short_is_mirror_and_later_candles_cannot_change_signal():
    raw = raw_data()
    raw.loc[2, "high"] = 104
    expected, _ = study.signal_candles(raw, contact(-1, 103.))
    assert expected.wick_reclaim.iloc[0]
    raw.loc[5:, ["open", "high", "low", "close"]] = 1e6
    actual, _ = study.signal_candles(raw, contact(-1, 103.))
    pd.testing.assert_frame_equal(expected, actual)


def test_incomplete_signal_and_missing_next_open_are_rejected():
    raw = raw_data()
    found, audit = study.signal_candles(raw.drop(index=3), contact())
    assert found.empty and audit == {"incomplete_signal_bar": 1}
    found, audit = study.signal_candles(raw.drop(index=5), contact())
    assert found.empty and audit == {"missing_next_open": 1}


def test_contact_at_last_minute_still_uses_next_five_minute_open():
    raw = raw_data()
    raw.loc[9, "low"] = 99
    found, _ = study.signal_candles(raw, contact(touch=9))
    assert found.entry_index.iloc[0] == 10
    assert found.signal_ms.iloc[0] == 5 * study.MINUTE


def test_confirmation_bar_extrema_are_not_post_entry_profit():
    raw = raw_data()
    raw.loc[2, ["high", "low"]] = [10000, 1]
    found, _ = study.signal_candles(raw, contact())
    path = study.base.forward_paths(raw, [5])[5]
    j = int(found.entry_index.iloc[0])
    gross, mfe, mae = study.base.response(path, j, 1, raw.open.iloc[j])
    assert gross == 0
    assert mfe < 100 and mae < 100
