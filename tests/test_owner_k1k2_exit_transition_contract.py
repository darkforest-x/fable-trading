"""V37 synthetic-only characterization of existing native5 exit semantics.

No market, report, registry, or persisted experiment is read. Management colours
are computed with the existing pure SMA40(HL2) feature transform from synthetic
OHLC, not manually injected. Parameterized mirror/positive-scale properties
are used because Hypothesis is not installed; no dependency is added.

These tests characterize colour versus transition_colour, not an assertion that
one policy earns more. Entry, initial stop, cost, and timeout are identical.
The 60-minute synthetic timeout exercises the existing deadline branch quickly;
it is not a proposal to change the research policy's maximum holding duration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse import add_features
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


ENTRY = pd.Timestamp("2023-01-03T12:00:00Z")
FIVE = pd.Timedelta(minutes=5)
MODES = ("colour", "transition_colour")


def source(relative_sides, *, direction=1, scale=1.0, scenario=None):
    """Seed forty real bars, then produce valid OHLC with HL2 above/below MA.

    relative_sides[0] is the last pre-entry bar; element1 is the first held bar.
    Every entry is 100 before scaling, so reflection about100 preserves signed
    original-notional return. Source segment0 is continuous unless a test drops
    a raw bar. All intended sides stay strictly away from the equality boundary.
    """
    n = 54
    raw = pd.DataFrame({
        "open_time": pd.date_range(ENTRY - 40 * FIVE, periods=n, freq="5min"),
        "open": 100., "high": 100.2, "low": 99.8, "close": 100.,
        "volume": 1., "segment_id": 0,
    })
    sides = list(relative_sides)
    for i in range(39, n):
        side = sides[min(i - 39, len(sides) - 1)]
        raw.loc[i, ["high", "low"]] = [103., 99.] if side == 1 else [101., 97.]
    if scenario == "stop_before_arm":
        raw.loc[40, "low"] = 94.
    elif scenario == "gap_stop_on_flip":
        raw.loc[42, ["open", "high", "low", "close"]] = [93., 94., 92., 93.]
    elif scenario == "held_bar_stop_on_flip":
        raw.loc[40, "low"] = 94.
    elif scenario == "exit_open_precedes_later_extrema":
        raw.loc[41, ["open", "high", "low", "close"]] = [100.7, 102., 94., 100.]
    elif scenario == "profitable_transition":
        raw.loc[44, ["open", "high", "low", "close"]] = [101.2, 102., 98., 101.2]
    elif scenario not in (None, "missing_raw", "missing_entry"):
        raise AssertionError("Unknown synthetic scenario")
    stop = 95.
    if direction == -1:
        original = raw.copy()
        raw["open"] = 200 - original.open
        raw["high"] = 200 - original.low
        raw["low"] = 200 - original.high
        raw["close"] = 200 - original.close
        stop = 105.
    raw[["open", "high", "low", "close"]] *= scale
    management = add_features(raw, "SMA", 40)
    management.attrs.update(bar_minutes=5, ma_kind="SMA", ma_length=40)
    if scenario == "missing_raw":
        raw = raw.drop(index=41).reset_index(drop=True)
    elif scenario == "missing_entry":
        raw = raw.drop(index=40).reset_index(drop=True)
    entry = pd.DataFrame([{
        "event_id": "synthetic_owner_k2", "decision_time": ENTRY,
        "direction": direction, "initial_stop": stop * scale,
        "signal_atr": 2. * scale, "gap_bars": 3, "immutable_key": "same_entry",
    }])
    return raw, management, entry


def execute(inputs, mode):
    raw, management, entry = inputs
    before = [item.copy(deep=True) for item in inputs]
    result = simulate_events(raw, management, entry, {
        "exit_mode": mode, "management_minutes": 5, "confirmations": 1,
        "max_hours": 1, "cost_fraction": .002,
    })
    for original, snapshot in zip(inputs, before):
        assert_frame_equal(original, snapshot)
    assert len(result) == 1
    row = result.iloc[0]
    assert row.immutable_key == "same_entry"
    assert row.initial_stop == entry.iloc[0].initial_stop
    assert row.decision_time == ENTRY
    return row


@pytest.mark.parametrize("direction", [-1, 1])
def test_initial_opposite_waits_for_alignment_then_adjacent_new_flip(direction):
    inputs = source([-1, -1, 1, 1, -1], direction=direction)
    actual_sides = inputs[1].loc[39:43, "ma_side"].to_numpy() * direction
    np.testing.assert_array_equal(actual_sides, [-1, -1, 1, 1, -1])
    state = execute(inputs, "colour")
    edge = execute(inputs, "transition_colour")
    assert state.outcome == "colour_exit"
    assert state.exit_time == ENTRY + FIVE
    assert edge.outcome == "transition_colour_exit"
    assert edge.exit_time == ENTRY + 4 * FIVE
    assert edge.transition_initial_state == "opposite"
    assert edge.transition_first_armed_at == ENTRY + 2 * FIVE
    assert edge.transition_armed_at == ENTRY + 2 * FIVE
    assert edge.transition_trigger_previous_open_time == ENTRY + 2 * FIVE
    assert edge.transition_trigger_open_time == ENTRY + 3 * FIVE
    assert edge.transition_trigger_available_at == edge.exit_time


@pytest.mark.parametrize("direction", [-1, 1])
def test_initially_aligned_seed_arms_at_entry_not_after_a_new_held_aligned_bar(direction):
    inputs = source([1, -1], direction=direction)
    state = execute(inputs, "colour")
    edge = execute(inputs, "transition_colour")
    assert edge.transition_initial_state == "aligned"
    assert edge.transition_initial_open_time == ENTRY - FIVE
    assert edge.transition_first_armed_at == ENTRY
    assert edge.transition_armed_at == ENTRY
    assert edge.exit_time == state.exit_time == ENTRY + FIVE
    assert edge.transition_trigger_previous_open_time == ENTRY - FIVE
    assert edge.transition_trigger_open_time == ENTRY
    assert edge.transition_trigger_available_at == ENTRY + FIVE
    assert edge.net_return == pytest.approx(state.net_return)


@pytest.mark.parametrize("direction", [-1, 1])
def test_continuously_opposite_does_not_arm_but_does_reach_unchanged_deadline(direction):
    inputs = source([-1], direction=direction)
    state = execute(inputs, "colour")
    edge = execute(inputs, "transition_colour")
    assert state.exit_time == ENTRY + FIVE
    assert edge.outcome == "time_exit" and edge.closed
    assert edge.exit_time == ENTRY + pd.Timedelta(hours=1)
    assert pd.isna(edge.transition_first_armed_at)
    assert pd.isna(edge.transition_trigger_available_at)
    assert edge.gross_return == 0
    assert edge.net_return == pytest.approx(-.002)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("mode", MODES)
def test_initial_hard_stop_remains_live_before_transition_arming(direction, mode):
    inputs = source([-1, -1, 1, -1], direction=direction, scenario="stop_before_arm")
    row = execute(inputs, mode)
    assert row.outcome == "hard_stop" and row.closed
    assert row.exit_time == ENTRY + FIVE
    assert row.exit_price == inputs[2].iloc[0].initial_stop
    assert row.gross_return == pytest.approx(-.05)
    assert row.net_return == pytest.approx(-.052)
    if mode == "transition_colour":
        assert pd.isna(row.transition_first_armed_at)
        assert pd.isna(row.transition_trigger_available_at)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("mode", MODES)
def test_gap_stop_beats_completed_colour_edge_at_the_same_boundary(direction, mode):
    row = execute(source([1, 1, -1], direction=direction, scenario="gap_stop_on_flip"), mode)
    assert row.outcome == "hard_stop_gap"
    assert row.exit_time == ENTRY + 2 * FIVE
    assert row.exit_price == (93 if direction == 1 else 107)
    assert row.gross_return == pytest.approx(-.07)
    assert row.net_return == pytest.approx(-.072)
    if mode == "transition_colour":
        assert row.transition_first_armed_at == ENTRY
        assert pd.isna(row.transition_trigger_available_at)


@pytest.mark.parametrize("mode", MODES)
def test_intrabar_stop_in_held_bar_wins_even_if_that_bar_closes_opposite(mode):
    inputs = source([1, -1], scenario="held_bar_stop_on_flip")
    assert inputs[1].loc[40, "ma_side"] == -1
    row = execute(inputs, mode)
    assert row.outcome == "hard_stop"
    assert row.exit_time == ENTRY + FIVE
    assert row.exit_price == 95
    if mode == "transition_colour":
        assert pd.isna(row.transition_trigger_available_at)


@pytest.mark.parametrize("mode", MODES)
def test_completed_colour_exit_precedes_unseen_extrema_of_the_new_bar(mode):
    inputs = source([1, -1], scenario="exit_open_precedes_later_extrema")
    # The low94 belongs to the new bar starting at the executable exit time.
    assert inputs[0].loc[41, "low"] < inputs[2].iloc[0].initial_stop
    row = execute(inputs, mode)
    assert row.outcome == ("colour_exit" if mode == "colour" else "transition_colour_exit")
    assert row.exit_time == ENTRY + FIVE
    assert row.exit_price == 100.7
    assert row.net_return == pytest.approx(.005)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("mode", MODES)
def test_missing_raw_bar_censors_instead_of_inventing_cash_or_a_delayed_fill(direction, mode):
    row = execute(source([-1, -1, 1, -1], direction=direction, scenario="missing_raw"), mode)
    assert row.outcome == "data_gap_censored"
    assert not row.closed
    assert row.exit_time == ENTRY + FIVE
    assert pd.isna(row.net_return) and pd.isna(row.net_r)
    assert row.marked_net_return == pytest.approx(-.002)


@pytest.mark.parametrize("mode", MODES)
def test_missing_actual_entry_open_is_unknown_not_an_execution(mode):
    row = execute(source([1, -1], scenario="missing_entry"), mode)
    assert row.outcome == "entry_missing" and not row.closed
    assert pd.isna(row.entry_price) and pd.isna(row.exit_time)
    assert pd.isna(row.net_return)


def test_missing_management_observation_resets_edge_without_bridging_unknown_colour():
    raw, management, entry = source([1, 1, 1, -1, 1, -1])
    management = management.drop(index=41)  # Missing observation due at E+10m.
    row = execute((raw, management, entry), "transition_colour")
    assert row.outcome == "transition_colour_exit"
    assert row.exit_time == ENTRY + 5 * FIVE
    assert row.transition_first_armed_at == ENTRY
    assert row.transition_armed_at == ENTRY + 4 * FIVE
    assert row.transition_reset_count == 1
    assert row.transition_last_reset_reason == "missing_management"
    assert row.transition_trigger_previous_open_time == ENTRY + 3 * FIVE
    assert row.transition_trigger_open_time == ENTRY + 4 * FIVE


SCENARIOS = [
    ([-1, -1, 1, 1, -1], None),
    ([1, -1], None),
    ([-1], None),
    ([-1, -1, 1, -1], "stop_before_arm"),
    ([1, 1, -1], "gap_stop_on_flip"),
    ([1, -1], "held_bar_stop_on_flip"),
    ([1, -1], "exit_open_precedes_later_extrema"),
    ([-1, -1, 1, 1, -1], "profitable_transition"),
    ([-1, -1, 1, -1], "missing_raw"),
]


@pytest.mark.parametrize("states,scenario", SCENARIOS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scale", [.125, 1., 7.5])
def test_mirror_and_positive_scale_preserve_execution_clocks_and_normalized_payoffs(states, scenario, mode, scale):
    original = execute(source(states, scenario=scenario), mode)
    long = execute(source(states, scale=scale, scenario=scenario), mode)
    short = execute(source(states, direction=-1, scale=scale, scenario=scenario), mode)
    for row in (long, short):
        assert row.outcome == original.outcome
        assert row.closed == original.closed
        assert row.entry_time == original.entry_time
        assert row.exit_time == original.exit_time
        assert row.hold_minutes == original.hold_minutes
        for field in ("risk_pct", "risk_atr", "gross_return", "net_return", "net_r", "max_favourable_r", "max_adverse_r"):
            if pd.isna(original[field]):
                assert pd.isna(row[field])
            else:
                assert row[field] == pytest.approx(original[field], abs=2e-13, rel=2e-13)
        if mode == "transition_colour":
            for field in ("transition_initial_state", "transition_first_armed_at", "transition_armed_at", "transition_trigger_available_at"):
                if pd.isna(original[field]):
                    assert pd.isna(row[field])
                else:
                    assert row[field] == original[field]
    assert long.exit_price == pytest.approx(original.exit_price * scale)
    assert short.exit_price == pytest.approx((200 - original.exit_price) * scale)


@pytest.mark.parametrize("mode", MODES)
def test_post_exit_future_extrema_do_not_change_already_executable_exit(mode):
    inputs = source([1, -1], scenario="exit_open_precedes_later_extrema")
    before = execute(inputs, mode)
    raw, management, entry = [frame.copy(deep=True) for frame in inputs]
    # Retain the executable OPEN; later HLC and management colours are future.
    raw.loc[raw.open_time >= before.exit_time, ["high", "low", "close"]] = np.nan
    management.loc[management.open_time >= before.exit_time, ["ma", "ma_side", "low", "high", "close"]] = np.nan
    after = execute((raw, management, entry), mode)
    for field in ("outcome", "exit_time", "exit_price", "gross_return", "net_return", "net_r", "max_favourable_r", "max_adverse_r"):
        assert after[field] == before[field]
