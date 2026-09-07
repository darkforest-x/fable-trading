"""Independent V38 pending-entry tests using synthetic SMA40(HL2) observations.

No real price, outcome, historical report, or experiment is loaded. Native5
features are calculated from synthetic OHLC with the existing pure feature
transform. Finite state enumeration and pytest parameterization provide
metamorphic/oracle coverage; this is not a Hypothesis or random-search suite.

An eligible request is only a causal entry-clock observation, never an executed
trade or a profitability label. Pending at cutoff is administrative censoring.
"""

from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse import add_features
from yoyo.data.k1k2_pending_entry import audit_pending_entries


E = pd.Timestamp("2023-01-03T12:00:00Z")
STEP = pd.Timedelta(minutes=5)
REQUEST_FIELDS = ["event_id", "decision_time", "direction", "initial_stop", "signal_atr", "audit_cutoff"]
EVENT_FIELDS = ["status", "status_known_at", "eligible_at", "reference_open", "risk_pct",
                "risk_atr", "seed_state", "waiting_bars", "invalidated_interval_start"]
TRACE_FIELDS = ["event_id", "observed_at", "completed_bar_open", "state", "colour_known",
                "side", "wait_bar_stop_touched", "boundary_open"]
TERMINAL_STATUSES = {"eligible", "invalidated_open", "invalidated_wait_bar", "unknown_raw",
                     "unknown_management", "pending_at_cutoff"}


def synthetic(states, *, cutoff_steps=6, direction=1, scale=1., scenario=None):
    """states[0] is seed[E-5,E); states[1] is the first completed wait bar.

    Forty prior real bars establish SMA40. At each requested state, HL2 is
    strictly above/below its causal SMA, while unedited OPEN stays100. Price
    reflection about100 swaps directions; positive scaling changes units only.
    """
    count = 52
    raw = pd.DataFrame({
        "open_time": pd.date_range(E - 40 * STEP, periods=count, freq="5min"),
        "open": 100., "high": 100.2, "low": 99.8, "close": 100., "volume": 1.,
        "segment_id": 0,
    })
    for i in range(39, count):
        side = states[min(i - 39, len(states) - 1)]
        raw.loc[i, ["high", "low"]] = [103., 99.] if side == 1 else [101., 97.]
    if scenario == "seed_touch":
        raw.loc[39, ["high", "low"]] = [107., 95.] if states[0] == 1 else [103., 95.]
    elif scenario == "wait_touch":
        raw.loc[40, ["high", "low"]] = [107., 95.]
    elif scenario == "wait_cross":
        raw.loc[40, ["high", "low"]] = [108., 94.]
    elif scenario == "boundary_touch":
        raw.loc[41, ["open", "high", "low", "close"]] = [95., 102., 94., 100.]
    elif scenario == "boundary_cross":
        raw.loc[41, ["open", "high", "low", "close"]] = [94., 102., 93., 100.]
    elif scenario == "seed_boundary_touch":
        raw.loc[40, ["open", "high", "low", "close"]] = [95., 102., 94., 100.]
    elif scenario == "seed_boundary_cross":
        raw.loc[40, ["open", "high", "low", "close"]] = [94., 102., 93., 100.]
    elif scenario not in (None, "raw_gap", "management_gap", "unknown_seed"):
        raise AssertionError("Unknown synthetic scenario")
    if direction == -1:
        old = raw.copy()
        raw["open"], raw["close"] = 200 - old.open, 200 - old.close
        raw["high"], raw["low"] = 200 - old.low, 200 - old.high
    raw[["open", "high", "low", "close"]] *= scale
    raw.attrs["bar_minutes"] = 5
    native = add_features(raw, "SMA", 40)
    native.attrs.update(bar_minutes=5, ma_kind="SMA", ma_length=40)
    # Faults are injected after computing the complete synthetic oracle prefix:
    # no later valid colour is allowed to heal one missing earlier observation.
    if scenario == "raw_gap":
        raw = raw.drop(index=41).reset_index(drop=True)
    elif scenario == "management_gap":
        native = native.drop(index=40).reset_index(drop=True)
    elif scenario == "unknown_seed":
        native.loc[39, "ma"] = np.nan
    requests = pd.DataFrame([{
        "event_id": "synthetic_k2", "decision_time": E, "direction": direction,
        "initial_stop": (95. if direction == 1 else 105.) * scale,
        "signal_atr": 2. * scale, "audit_cutoff": E + cutoff_steps * STEP,
        "unchanged_identity": "keep-me",
    }])
    return requests, native, raw


def audit(inputs):
    before = [frame.copy(deep=True) for frame in inputs]
    events, trace = audit_pending_entries(*inputs)
    for original, saved in zip(inputs, before):
        assert_frame_equal(original, saved)
    assert set(EVENT_FIELDS).issubset(events)
    assert set(TRACE_FIELDS).issubset(trace)
    assert events.event_id.tolist() == inputs[0].event_id.tolist()
    assert events.status.isin(TERMINAL_STATUSES).all()
    for row in events.itertuples():
        if row.status != "eligible":
            assert pd.isna(row.eligible_at)
            assert pd.isna(row.reference_open)
            assert pd.isna(row.risk_pct) and pd.isna(row.risk_atr)
        else:
            assert row.eligible_at == row.status_known_at
            assert row.risk_pct > 0 and row.risk_atr > 0
        if row.status != "invalidated_wait_bar":
            assert pd.isna(row.invalidated_interval_start)
    assert not {"gross_return", "net_return", "pnl", "exit_time", "exit_price"}.intersection(events)
    return events, trace


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("state", [-1, 1])
def test_seed_stop_touch_is_not_a_waiting_invalidation(direction, state):
    inputs = synthetic([state, 1], direction=direction, scenario="seed_touch")
    assert inputs[1].loc[39, "ma_side"] == state * direction
    events, trace = audit(inputs)
    row = events.iloc[0]
    expected_at = E if state == 1 else E + STEP
    assert row.status == "eligible"
    assert row.seed_state == ("aligned" if state == 1 else "opposite")
    assert row.eligible_at == expected_at
    assert row.waiting_bars == int(state == -1)
    assert row.reference_open == 100.
    assert row.risk_pct == pytest.approx(.05)
    assert row.risk_atr == pytest.approx(2.5)
    assert trace.iloc[0].observed_at == E
    assert trace.iloc[0].completed_bar_open == E - STEP


@pytest.mark.parametrize("direction", [-1, 1])
def test_first_single_aligned_state_is_sufficient_without_extra_confirmation(direction):
    inputs = synthetic([-1, -1, 1, -1], direction=direction)
    np.testing.assert_array_equal(inputs[1].loc[39:42, "ma_side"] * direction, [-1, -1, 1, -1])
    events, trace = audit(inputs)
    row = events.iloc[0]
    assert row.status == "eligible" and row.eligible_at == E + 2 * STEP
    assert row.waiting_bars == 2
    assert trace.observed_at.tolist() == [E, E + STEP, E + 2 * STEP]
    assert trace.state.tolist() == ["opposite", "opposite", "aligned"]
    assert trace.colour_known.all()
    assert trace.iloc[-1].completed_bar_open == E + STEP


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("scenario", ["wait_touch", "wait_cross"])
def test_complete_wait_bar_touch_or_cross_invalidates_before_its_aligned_confirmation(direction, scenario):
    inputs = synthetic([-1, 1], direction=direction, scenario=scenario)
    assert inputs[1].loc[40, "ma_side"] == direction
    events, trace = audit(inputs)
    row = events.iloc[0]
    assert row.status == "invalidated_wait_bar"
    assert row.status_known_at == E + STEP
    assert row.invalidated_interval_start == E
    assert row.waiting_bars == 1
    assert trace.iloc[-1].wait_bar_stop_touched
    # Complete waiting-bar invalidation is known before the new OPEN is needed.
    assert pd.isna(trace.iloc[-1].boundary_open)


@pytest.mark.parametrize("scenario", ["wait_touch", "wait_cross"])
def test_wait_bar_invalidation_does_not_require_later_open_or_management(scenario):
    requests, native, raw = synthetic([-1, 1], scenario=scenario)
    raw.loc[41, ["open", "high", "low", "close"]] = np.nan
    native = native.drop(index=40)
    events, _ = audit((requests, native, raw))
    assert events.iloc[0].status == "invalidated_wait_bar"
    assert events.iloc[0].status_known_at == E + STEP


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("scenario", ["boundary_touch", "boundary_cross", "seed_boundary_touch", "seed_boundary_cross"])
def test_current_boundary_open_at_or_beyond_stop_is_invalid_not_eligible(direction, scenario):
    seed = scenario.startswith("seed_")
    inputs = synthetic([1] if seed else [-1, 1], direction=direction, scenario=scenario)
    events, trace = audit(inputs)
    row = events.iloc[0]
    assert row.status == "invalidated_open"
    assert row.status_known_at == E if seed else row.status_known_at == E + STEP
    assert direction * (trace.iloc[-1].boundary_open - row.initial_stop) <= 0


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("seed_aligned", [False, True])
def test_eligible_uses_only_current_open_not_unseen_high_low_close(direction, seed_aligned):
    inputs = synthetic([1] if seed_aligned else [-1, 1], direction=direction)
    expected_at = E if seed_aligned else E + STEP
    requests, native, raw = inputs
    current = raw.open_time.eq(expected_at)
    raw.loc[current, ["high", "low", "close"]] = np.nan
    # These management values become available only five minutes later.
    native.loc[native.open_time.eq(expected_at), ["high", "low", "close", "ma", "ma_side", "hl2"]] = np.nan
    events, trace = audit(inputs)
    assert events.iloc[0].status == "eligible"
    assert events.iloc[0].eligible_at == expected_at
    assert events.iloc[0].reference_open == 100.
    assert trace.iloc[-1].boundary_open == 100.


def test_eligible_risk_uses_own_confirmation_open_and_original_k2_stop_atr():
    inputs = synthetic([-1, 1])
    requests, native, raw = inputs
    raw.loc[41, "open"] = 100.7
    # The same current bar's geometry is intentionally not yet validated.
    events, _ = audit(inputs)
    row = events.iloc[0]
    assert row.reference_open == 100.7
    assert row.initial_stop == 95. and row.signal_atr == 2.
    assert row.risk_pct == pytest.approx(5.7 / 100.7)
    assert row.risk_atr == pytest.approx(5.7 / 2)


@pytest.mark.parametrize("scenario,status,at", [
    ("raw_gap", "unknown_raw", E + STEP),
    ("management_gap", "unknown_management", E + STEP),
    ("unknown_seed", "unknown_management", E),
])
def test_earlier_unknown_is_terminal_even_with_later_valid_alignment(scenario, status, at):
    inputs = synthetic([-1, -1, 1, 1], scenario=scenario)
    events, trace = audit(inputs)
    row = events.iloc[0]
    assert row.status == status and row.status_known_at == at
    assert trace.observed_at.max() == at
    assert not trace.observed_at.gt(at).any()


@pytest.mark.parametrize("where", ["seed", "waiting"])
@pytest.mark.parametrize("fault", ["side", "hl2", "open", "close", "ma", "segment"])
def test_source_mismatch_or_unknown_management_is_not_a_false_colour(where, fault):
    inputs = synthetic([-1, 1])
    requests, native, raw = inputs
    at_index = 39 if where == "seed" else 40
    if fault == "side":
        native.loc[at_index, "ma_side"] *= -1
    elif fault == "hl2":
        native.loc[at_index, "hl2"] += .25
    elif fault in ("open", "close"):
        native.loc[at_index, fault] += .1
    elif fault == "ma":
        native.loc[at_index, "ma"] = np.nan
    else:
        if where == "seed":
            native.loc[at_index, "segment_id"] = np.nan
        else:
            native.loc[at_index:, "segment_id"] = 1
    events, trace = audit(inputs)
    assert events.iloc[0].status == "unknown_management"
    assert events.iloc[0].status_known_at == (E if where == "seed" else E + STEP)
    assert not trace.iloc[-1].colour_known


@pytest.mark.parametrize("fault", ["nan_ohlc", "bad_geometry", "segment_change", "nan_segment"])
def test_invalid_complete_raw_wait_bar_is_terminal_unknown(fault):
    inputs = synthetic([-1, 1])
    requests, native, raw = inputs
    if fault == "nan_ohlc":
        raw.loc[40, "close"] = np.nan
    elif fault == "bad_geometry":
        raw.loc[40, "low"] = 102.
    elif fault == "segment_change":
        raw.loc[40:, "segment_id"] = 1
    else:
        raw.loc[40, "segment_id"] = np.nan
    events, _ = audit(inputs)
    assert events.iloc[0].status == "unknown_raw"
    # Segment changes may already be observable at E, unlike complete HLC.
    assert events.iloc[0].status_known_at <= E + STEP


def test_raw_and_management_segment_namespaces_are_independent():
    inputs = synthetic([-1, 1])
    inputs[1]["segment_id"] = "native-segment-17"
    inputs[2]["segment_id"] = "raw-segment-9"
    events, _ = audit(inputs)
    assert events.iloc[0].status == "eligible"
    no_raw_ids = (inputs[0], inputs[1], inputs[2].drop(columns="segment_id"))
    derived, _ = audit(no_raw_ids)
    assert_frame_equal(events, derived)


@pytest.mark.parametrize("cutoff_steps", [0, 1, 3])
def test_cutoff_boundary_can_confirm_otherwise_pending_is_administrative(cutoff_steps):
    always_opposite = synthetic([-1], cutoff_steps=cutoff_steps)
    events, trace = audit(always_opposite)
    row = events.iloc[0]
    assert row.status == "pending_at_cutoff"
    assert row.status_known_at == E + cutoff_steps * STEP
    assert row.waiting_bars == cutoff_steps
    assert trace.iloc[-1].state == "opposite"
    assert len(trace) == cutoff_steps + 1
    states = [-1] * cutoff_steps + [1]
    events, _ = audit(synthetic(states, cutoff_steps=cutoff_steps))
    assert events.iloc[0].status == "eligible"
    assert events.iloc[0].eligible_at == E + cutoff_steps * STEP


@pytest.mark.parametrize("states", list(product([-1, 1], repeat=4)))
def test_finite_state_oracle_and_exact_prefix_trace(states):
    inputs = synthetic(states, cutoff_steps=3)
    expected_step = next((i for i, side in enumerate(states) if side == 1), None)
    full_events, full_trace = audit(inputs)
    row = full_events.iloc[0]
    assert row.status == ("pending_at_cutoff" if expected_step is None else "eligible")
    assert row.status_known_at == E + (3 if expected_step is None else expected_step) * STEP
    for cutoff in range(4):
        requests, native, raw = [x.copy(deep=True) for x in inputs]
        boundary = E + cutoff * STEP
        requests["audit_cutoff"] = boundary
        # Include only the boundary OPEN row and completed management prefix.
        native = native.loc[native.open_time < boundary].copy()
        raw = raw.loc[raw.open_time <= boundary].copy()
        raw.loc[raw.open_time.eq(boundary), ["high", "low", "close"]] = np.nan
        short_events, short_trace = audit((requests, native, raw))
        assert_frame_equal(short_trace, full_trace.loc[full_trace.observed_at <= boundary].reset_index(drop=True))
        if expected_step is not None and expected_step <= cutoff:
            for field in EVENT_FIELDS:
                actual, expected = short_events.iloc[0][field], row[field]
                assert (pd.isna(actual) and pd.isna(expected)) or actual == expected
        else:
            assert short_events.iloc[0].status == "pending_at_cutoff"


@pytest.mark.parametrize("scenario", [None, "wait_touch", "boundary_cross", "raw_gap", "management_gap"])
@pytest.mark.parametrize("scale", [.125, 1., 7.5])
def test_mirror_and_positive_scale_preserve_states_clocks_and_risk_units(scenario, scale):
    baseline, _ = audit(synthetic([-1, 1], scenario=scenario))
    original = baseline.iloc[0]
    for direction in (-1, 1):
        events, _ = audit(synthetic([-1, 1], direction=direction, scale=scale, scenario=scenario))
        row = events.iloc[0]
        for field in ("status", "status_known_at", "eligible_at", "seed_state", "waiting_bars", "invalidated_interval_start"):
            assert (pd.isna(row[field]) and pd.isna(original[field])) or row[field] == original[field]
        if row.status == "eligible":
            assert row.reference_open == pytest.approx(100 * scale)
            assert row.risk_pct == pytest.approx(original.risk_pct, abs=1e-13)
            assert row.risk_atr == pytest.approx(original.risk_atr, abs=1e-13)


@pytest.mark.parametrize("scenario", [None, "wait_touch", "boundary_cross", "raw_gap", "management_gap", "unknown_seed"])
def test_future_edit_and_append_cannot_change_established_terminal_observation(scenario):
    inputs = synthetic([-1, 1], scenario=scenario)
    events, trace = audit(inputs)
    stop = events.iloc[0].status_known_at
    requests, native, raw = [x.copy(deep=True) for x in inputs]
    # Preserve the terminal boundary OPEN but nothing later may be consulted.
    raw.loc[raw.open_time > stop, ["open", "high", "low", "close"]] = np.nan
    native.loc[native.open_time >= stop, ["open", "high", "low", "close", "hl2", "ma", "ma_side"]] = np.nan
    last = raw.iloc[[-1]].copy()
    last["open_time"] += STEP
    raw = pd.concat([raw, last], ignore_index=True)
    raw.attrs["bar_minutes"] = 5
    same_events, same_trace = audit((requests, native, raw))
    assert_frame_equal(events, same_events)
    assert_frame_equal(trace, same_trace)


@pytest.mark.parametrize("fault", ["duplicate_id", "empty_id", "space_id", "numeric_id", "bool_id", "null_id", "bool_direction", "zero_direction",
    "nan_stop", "zero_stop", "zero_atr", "naive_decision", "numeric_decision", "off_grid_decision",
    "cutoff_before_decision", "off_grid_cutoff", "out_of_order_raw"])
def test_invalid_identity_and_clock_contracts_raise(fault):
    requests, native, raw = synthetic([-1, 1])
    if fault == "duplicate_id":
        requests = pd.concat([requests, requests], ignore_index=True)
    elif fault == "empty_id": requests.loc[0, "event_id"] = ""
    elif fault == "space_id": requests.loc[0, "event_id"] = "   "
    elif fault == "numeric_id": requests.loc[0, "event_id"] = 7
    elif fault == "bool_id": requests.loc[0, "event_id"] = True
    elif fault == "null_id": requests.loc[0, "event_id"] = None
    elif fault == "bool_direction": requests["direction"] = True
    elif fault == "zero_direction": requests["direction"] = 0
    elif fault == "nan_stop": requests["initial_stop"] = np.nan
    elif fault == "zero_stop": requests["initial_stop"] = 0.
    elif fault == "zero_atr": requests["signal_atr"] = 0.
    elif fault == "naive_decision": requests["decision_time"] = E.tz_localize(None)
    elif fault == "numeric_decision": requests["decision_time"] = E.value
    elif fault == "off_grid_decision": requests["decision_time"] = E + pd.Timedelta(minutes=1)
    elif fault == "cutoff_before_decision": requests["audit_cutoff"] = E - STEP
    elif fault == "off_grid_cutoff": requests["audit_cutoff"] = E + pd.Timedelta(minutes=1)
    else: raw = raw.iloc[::-1]
    with pytest.raises(ValueError):
        audit_pending_entries(requests, native, raw)


@pytest.mark.parametrize("source_name", ["raw", "native"])
@pytest.mark.parametrize("duplicate_future", [False, True])
def test_duplicate_source_is_unknown_when_observed_but_unvisited_future_is_irrelevant(source_name, duplicate_future):
    requests, native, raw = synthetic([1])
    expected_events, expected_trace = audit((requests, native, raw))
    original = raw if source_name == "raw" else native
    i = 44 if duplicate_future else 39
    duplicated = pd.concat([original.iloc[:i + 1], original.iloc[[i]], original.iloc[i + 1:]], ignore_index=True)
    duplicated.attrs.update(original.attrs)
    actual = (requests, native, duplicated) if source_name == "raw" else (requests, duplicated, raw)
    events, trace = audit(actual)
    if duplicate_future:
        assert_frame_equal(events, expected_events)
        assert_frame_equal(trace, expected_trace)
    else:
        assert events.iloc[0].status == ("unknown_raw" if source_name == "raw" else "unknown_management")
        assert events.iloc[0].status_known_at == E
        assert trace.iloc[-1].state == "unknown"


def test_empty_inputs_keep_full_schema_and_missing_sources_are_terminal_unknown():
    requests, native, raw = synthetic([-1, 1])
    empty, trace = audit((requests.iloc[:0], native.iloc[:0], raw.iloc[:0]))
    assert empty.empty and trace.empty
    events, trace = audit((requests, native.iloc[:0], raw.iloc[:0]))
    assert events.iloc[0].status == "unknown_raw"
    assert events.iloc[0].status_known_at == E
    events, _ = audit((requests, native.iloc[:0], raw))
    assert events.iloc[0].status == "unknown_management"


def test_input_row_order_and_duplicate_decision_with_distinct_ids_are_preserved():
    requests, native, raw = synthetic([-1, 1])
    requests = pd.concat([requests.assign(event_id="z-last"), requests.assign(event_id="a-first")], ignore_index=True)
    requests.index = [70, 12]
    requests.attrs["audit_owner"] = "synthetic"
    events, trace = audit((requests, native, raw))
    assert events.event_id.tolist() == ["z-last", "a-first"]
    assert events.index.tolist() == [70, 12]
    assert events.attrs == requests.attrs
    assert events.eligible_at.eq(E + STEP).all()
    assert trace.groupby("event_id", sort=False).size().to_dict() == {"z-last": 2, "a-first": 2}


@pytest.mark.parametrize("field,value", [("bar_minutes", 15), ("ma_kind", "EMA"), ("ma_length", 20)])
def test_native_feature_metadata_cannot_silently_change_management_policy(field, value):
    requests, native, raw = synthetic([-1, 1])
    native.attrs[field] = value
    with pytest.raises(ValueError):
        audit_pending_entries(requests, native, raw)


def test_no_silent_overwrite_of_existing_audit_output_fields():
    requests, native, raw = synthetic([-1, 1])
    requests["status"] = "old_status"
    with pytest.raises(ValueError):
        audit_pending_entries(requests, native, raw)
