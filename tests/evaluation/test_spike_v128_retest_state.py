"""Focused causal checks for the SPIKE V12.8 retest state machine."""
from __future__ import annotations

import numpy as np
import pytest

from yoyo.evaluation.spike_v128_retest_state import confirmation


def _long_path():
    # Anchor level 10; first-leg high reaches 15 before the retest. The retest
    # candle's high is 19 and must not be included in the frozen first leg.
    return {
        "open_": np.array([9.0, 10.5, 12.0, 13.0, 12.0, 12.0, 13.0]),
        "high": np.array([10.0, 12.0, 14.0, 15.0, 19.0, 17.0, 20.0]),
        "low": np.array([8.0, 10.2, 11.0, 12.0, 10.0, 11.0, 12.0]),
        "close": np.array([9.0, 11.0, 13.0, 14.0, 11.0, 16.0, 19.0]),
        "gap": np.zeros(7, dtype=bool),
        "raw_side": np.zeros(7, dtype=np.int8),
        "anchor_i": 0,
        "side": 1,
        "stop": 7.0,
    }


def _run(data, **overrides):
    values = {**data, **overrides}
    return confirmation(
        values["open_"], values["high"], values["low"], values["close"],
        values["gap"], values["raw_side"], values["anchor_i"],
        values["side"], values["stop"], values.get("max_wait_bars", 24),
    )


def _mirror_long_to_short(data):
    mirrored = dict(data)
    mirrored["open_"] = -data["open_"].copy()
    mirrored["high"] = -data["low"].copy()
    mirrored["low"] = -data["high"].copy()
    mirrored["close"] = -data["close"].copy()
    mirrored["side"] = -1
    mirrored["stop"] = -data["stop"]
    return mirrored


@pytest.mark.parametrize("source", [_long_path(), _mirror_long_to_short(_long_path())])
def test_long_and_short_require_distinct_break_retest_and_rebreak_bars(source):
    result = _run(source)

    assert result["status"] == "confirmed"
    assert (result["breakout_i"], result["retest_i"], result["confirmation_i"]) == (1, 4, 5)
    assert result["decision_i"] == 5
    assert result["level"] == pytest.approx(10.0 if source["side"] == 1 else -10.0)
    assert result["first_leg_extreme"] == pytest.approx(15.0 if source["side"] == 1 else -15.0)


def test_one_bar_cannot_be_both_breakout_and_retest():
    data = _long_path()
    result = _run(
        {key: (value[:2].copy() if isinstance(value, np.ndarray) else value) for key, value in data.items()},
        max_wait_bars=1,
    )

    assert result["status"] == "expired"
    assert result["breakout_i"] == 1
    assert result["retest_i"] is None
    assert result["confirmation_i"] is None
    assert result["decision_i"] == 1


def test_first_leg_extreme_includes_every_bar_before_retest_but_excludes_retest_bar():
    data = _long_path()
    result = _run(_long_path())

    assert result["first_leg_extreme"] == 15.0
    assert data["high"][3] == 15.0
    assert data["high"][4] == 19.0
    assert data["close"][5] > result["first_leg_extreme"]


@pytest.mark.parametrize(
    ("hazard", "expected_reason"),
    [
        ({"gap_at": 5, "opposite_at": 5, "stop_at": 5, "lost_level_at": 5}, "gap"),
        ({"opposite_at": 5, "stop_at": 5, "lost_level_at": 5}, "raw_opposite"),
        ({"stop_at": 5, "lost_level_at": 5}, "stop_touch"),
        ({"lost_level_at": 5}, "lost_level"),
    ],
)
def test_cancellation_precedence_runs_before_confirmation(hazard, expected_reason):
    data = _long_path()
    if "gap_at" in hazard:
        data["gap"][hazard["gap_at"]] = True
    if "opposite_at" in hazard:
        data["raw_side"][hazard["opposite_at"]] = -1
    if "lost_level_at" in hazard:
        data["low"][hazard["lost_level_at"]] = 9.0
        data["open_"][hazard["lost_level_at"]] = 10.0
        data["close"][hazard["lost_level_at"]] = 9.5
    if "stop_at" in hazard:
        data["low"][hazard["stop_at"]] = 6.0
        data["open_"][hazard["stop_at"]] = 6.5

    result = _run(data)

    assert result["status"] == "canceled"
    assert result["cancel_reason"] == expected_reason
    assert result["decision_i"] == 5
    assert result["confirmation_i"] is None


def test_opposite_signal_before_breakout_cancels_without_recording_breakout():
    data = _long_path()
    data["raw_side"][1] = -1

    result = _run(data)

    assert result["status"] == "canceled"
    assert result["cancel_reason"] == "raw_opposite"
    assert result["decision_i"] == 1
    assert result["breakout_i"] is None


def test_retest_close_must_hold_strictly_beyond_level():
    data = _long_path()
    data["close"][4] = 10.0
    data["open_"][4] = 10.0

    result = _run(data)

    assert result["status"] == "canceled"
    assert result["cancel_reason"] == "lost_level"
    assert result["breakout_i"] == 1
    assert result["retest_i"] is None


def _long_path_with_retest_at(index=23, length=26):
    data = {
        "open_": np.full(length, 11.0),
        "high": np.full(length, 12.0),
        "low": np.full(length, 11.0),
        "close": np.full(length, 11.5),
        "gap": np.zeros(length, dtype=bool),
        "raw_side": np.zeros(length, dtype=np.int8),
        "anchor_i": 0,
        "side": 1,
        "stop": 7.0,
    }
    data["open_"][0], data["high"][0], data["low"][0], data["close"][0] = 9.0, 10.0, 8.0, 9.0
    data["high"][1] = 13.0
    data["close"][1] = 12.0  # First breakout.
    data["high"][index - 1] = 15.0  # Later pre-retest extreme.
    data["close"][index - 1] = 14.0
    data["low"][index] = 10.0
    data["high"][index] = 100.0  # Excluded from the frozen first leg.
    data["close"][index] = 11.0
    data["open_"][index] = 12.0
    return data


def test_confirmation_at_max_wait_boundary_is_allowed():
    data = _long_path_with_retest_at(index=23, length=26)
    data["close"][24] = 16.0
    data["open_"][24] = 15.0
    data["high"][24] = 16.0
    data["low"][24] = 11.0

    result = _run(data, max_wait_bars=24)

    assert result["status"] == "confirmed"
    assert result["retest_i"] == 23
    assert result["confirmation_i"] == 24
    assert result["decision_i"] == 24
    assert result["first_leg_extreme"] == 15.0


def test_confirmation_after_max_wait_expires_at_boundary():
    data = _long_path_with_retest_at(index=23, length=26)
    data["close"][24] = 15.0  # Equality is not a strict re-break.
    data["open_"][24] = 14.0
    data["high"][24] = 15.0
    data["low"][24] = 11.0
    data["close"][25] = 16.0  # Too late; must never be inspected.
    data["open_"][25] = 15.0
    data["high"][25] = 16.0
    data["low"][25] = 11.0

    result = _run(data, max_wait_bars=24)

    assert result["status"] == "expired"
    assert result["confirmation_i"] is None
    assert result["decision_i"] == 24
    assert result["first_leg_extreme"] == 15.0


def test_short_data_window_returns_pending_boundary_with_last_observed_index():
    data = _long_path()
    prefix = {key: (value[:3].copy() if isinstance(value, np.ndarray) else value) for key, value in data.items()}

    result = _run(prefix, max_wait_bars=24)

    assert result["status"] == "pending_boundary"
    assert result["breakout_i"] == 1
    assert result["decision_i"] == 2
    assert result["boundary_reason"] == "insufficient_bars"


def test_invalid_intermediate_bar_is_a_boundary_and_later_bars_are_ignored():
    data = _long_path()
    data["close"][2] = np.nan
    data["high"][5] = 1000.0

    result = _run(data)

    assert result["status"] == "pending_boundary"
    assert result["decision_i"] == 2
    assert result["boundary_reason"] == "invalid_bar"
    assert result["breakout_i"] == 1
    assert result["retest_i"] is None


def test_decided_result_is_prefix_invariant_and_future_values_are_not_read():
    data = _long_path()
    expected = _run(data)
    prefix = {key: (value[:6].copy() if isinstance(value, np.ndarray) else value) for key, value in data.items()}
    changed_future = {key: (value.copy() if isinstance(value, np.ndarray) else value) for key, value in data.items()}
    for name in ("open_", "high", "low", "close"):
        changed_future[name][6] = np.nan
    changed_future["gap"][6] = True
    changed_future["raw_side"][6] = -1

    assert _run(prefix) == expected
    assert _run(changed_future) == expected


def test_malformed_shapes_and_direction_are_rejected():
    data = _long_path()
    with pytest.raises(ValueError, match="same length"):
        _run({**data, "close": data["close"][:-1]})
    with pytest.raises(ValueError, match="side"):
        _run({**data, "side": 0})
