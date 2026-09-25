"""Deterministic execution checks for the manual gross 4R SPIKE treatment."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_manual_four_r as four_r
from yoyo.evaluation import spike_v128_recent as recent


def _prepared(*, side: int = 1, changes: dict[int, tuple[float, float, float]] | None = None,
              raw_side_changes: dict[int, int] | None = None):
    index = pd.date_range("2026-08-01", periods=12, freq="15min", tz="UTC")
    open_a = np.full(12, 100.0)
    high = np.full(12, 100.5)
    low = np.full(12, 99.5)
    close = np.full(12, 100.0)
    atr = np.ones(12)
    for i, (h, l, c) in (changes or {}).items():
        high[i], low[i], close[i] = h, l, c
    frame = pd.DataFrame({"open": open_a, "high": high, "low": low, "close": close, "atr": atr}, index=index)
    raw_side = np.zeros(12, dtype=int)
    for i, value in (raw_side_changes or {}).items():
        raw_side[i] = value
    prepared = recent.source.prepared_arm(frame, np.zeros(12, dtype=bool), raw_side, "test",
                                           {"venue": "test", "symbol": "X", "asset": "BTC", "timeframe_min": 15},
                                           15, .1)
    row = recent._initial_position_fast(index, prepared.open, prepared.high, prepared.low, prepared.close,
                                        prepared.atr, prepared.gap, 5, side, prepared.spec)
    assert row is not None
    return prepared, row


@pytest.mark.parametrize(("side", "changes", "expected_target"), [
    (1, {7: (120.0, 99.0, 107.0)}, 108.0),
    (-1, {7: (100.5, 80.0, 93.0)}, 92.0),
])
def test_long_and_short_resting_target_fill_at_fixed_four_r(side, changes, expected_target):
    prepared, initial = _prepared(side=side, changes=changes)

    result = four_r.replay_four_r(prepared, initial)

    assert result["exit_i"] == 7
    assert result["exit_reason"] == "target_4r"
    assert result["exit_price"] == pytest.approx(expected_target)
    assert result["gross_r"] == pytest.approx(4.0)
    assert result["four_r_target_price"] == pytest.approx(expected_target)
    assert result["mfe_known_r"] == pytest.approx(4.0)
    assert result["mfe_upper_r"] == pytest.approx(4.0)
    assert not result["censored"]


def test_same_bar_stop_and_target_is_stop_first_and_marked_ambiguous():
    prepared, initial = _prepared(changes={7: (100.0, 97.0, 99.0)})
    prepared.high[7] = 108.0
    prepared.frame.loc[prepared.frame.index[7], "high"] = 108.0

    result = four_r.replay_four_r(prepared, initial)

    assert result["exit_i"] == 7
    assert result["exit_reason"] == "initial_stop"
    assert result["exit_price"] == pytest.approx(98.0)
    assert result["four_r_stop_target_ambiguous"]
    checkpoints = four_r.four_r_checkpoints(prepared, result)
    assert checkpoints["first4_observation"] == "ambiguous"
    assert checkpoints["first4_ambiguous"]
    assert checkpoints["max_giveback_r"] != checkpoints["max_giveback_r"]


def test_future_extreme_cannot_change_exit_before_it():
    first, first_row = _prepared(changes={7: (108.0, 99.0, 106.0), 10: (400.0, 1.0, 200.0)})
    second, second_row = _prepared(changes={7: (108.0, 99.0, 106.0), 10: (1_000_000.0, .01, 500_000.0)})

    a = four_r.replay_four_r(first, first_row)
    b = four_r.replay_four_r(second, second_row)

    assert (a["exit_i"], a["exit_reason"], a["exit_price"]) == (b["exit_i"], b["exit_reason"], b["exit_price"])
    assert a["exit_i"] == 7


def test_pending_reverse_at_open_precedes_intrabar_target():
    prepared, initial = _prepared(changes={7: (108.0, 99.0, 106.0)}, raw_side_changes={6: -1})

    result = four_r.replay_four_r(prepared, initial)

    assert result["exit_i"] == 7
    assert result["exit_reason"] == "opposite_v6_next_open"
    assert result["exit_price"] == pytest.approx(100.0)


def test_no_target_path_matches_frozen_attempt_accounting():
    prepared, initial = _prepared(changes={7: (103.0, 99.0, 102.0), 8: (103.0, 97.0, 99.0)})
    baseline_status, baseline = recent.attempt(prepared, 5, 1)

    result = four_r.replay_four_r(prepared, initial)

    assert baseline_status == "closed"
    assert baseline is not None
    for key in recent.source.TRADE_KEEP:
        if key in {"policy", "trade_id"}:
            continue
        assert result[key] == baseline[key], key
    for key in ("mfe_known_r", "mfe_upper_r", "close_peak_r", "stop_bar_excursion_ambiguous"):
        assert result[key] == baseline[key], key
    checkpoints = four_r.four_r_checkpoints(prepared, baseline)
    assert checkpoints["first_touch_i"] is None
    assert checkpoints["first_close4_i"] is None
    assert checkpoints["first4_observation"] == "not_reached"


def test_checkpoint_tracks_known_first_four_r_close_features_and_later_giveback():
    prepared, _ = _prepared(changes={7: (108.0, 99.0, 101.0),
                                     8: (110.0, 105.0, 108.0),
                                     9: (109.0, 105.0, 107.0)})
    prepared.open[10], prepared.high[10], prepared.close[10] = 104.5, 105.0, 104.6
    prepared.frame.loc[prepared.frame.index[10], ["open", "high", "close"]] = [104.5, 105.0, 104.6]
    status, baseline = recent.attempt(prepared, 5, 1)
    assert status == "closed" and baseline is not None
    assert baseline["exit_reason"] == "trailing_stop"

    checkpoints = four_r.four_r_checkpoints(prepared, baseline)

    assert checkpoints["first_touch_i"] == 7
    assert checkpoints["first_close4_i"] == 8
    assert checkpoints["first4_observation"] == "known"
    assert checkpoints["first4_bar_close_features"]["observed_at"] == prepared.frame.index[7] + pd.Timedelta(minutes=15)
    assert checkpoints["max_giveback_r"] == pytest.approx(3.0)


def test_opening_target_gap_fills_conservatively_at_target_even_with_reverse_pending():
    prepared, initial = _prepared(raw_side_changes={6: -1})
    prepared.open[7] = 110.0
    prepared.high[7] = 111.0
    prepared.low[7] = 109.0
    prepared.close[7] = 110.0
    prepared.frame.loc[prepared.frame.index[7], ["open", "high", "low", "close"]] = [110.0, 111.0, 109.0, 110.0]

    result = four_r.replay_four_r(prepared, initial)

    assert result["exit_reason"] == "target_4r_open_gap"
    assert result["exit_price"] == pytest.approx(108.0)
    assert result["gross_r"] == pytest.approx(4.0)
    assert result["mfe_known_r"] == pytest.approx(4.0)
    checkpoints = four_r.four_r_checkpoints(prepared, result)
    assert checkpoints["first_touch_i"] == 7
    assert checkpoints["first_touch_phase"] == "open"
    assert checkpoints["first_touch_clock"] == prepared.frame.index[7]


def test_later_opening_touch_does_not_replace_earlier_intrabar_touch():
    prepared, _ = _prepared(changes={7: (108.0, 99.0, 101.0)}, raw_side_changes={8: -1})
    prepared.open[9], prepared.high[9], prepared.close[9] = 110.0, 111.0, 110.0
    prepared.low[9] = 109.0
    prepared.frame.loc[prepared.frame.index[9], ["open", "high", "low", "close"]] = [110.0, 111.0, 109.0, 110.0]
    status, baseline = recent.attempt(prepared, 5, 1)
    assert status == "closed" and baseline is not None
    assert baseline["exit_reason"] == "opposite_v6_next_open"

    checkpoints = four_r.four_r_checkpoints(prepared, baseline)

    assert checkpoints["first_touch_i"] == 7
    assert checkpoints["first_touch_phase"] == "intrabar"
    assert checkpoints["first_touch_clock"] == prepared.frame.index[7] + pd.Timedelta(minutes=15)
