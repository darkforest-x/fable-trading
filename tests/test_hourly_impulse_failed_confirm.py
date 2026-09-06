"""V18 synthetic pending lifecycles; no external prices or outcomes are read."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


E = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
V17 = {"management_minutes": 15, "exit_mode": "transition_colour", "confirmations": 1,
       "fast_partial_fraction": .5, "fast_failed_launch_exit": True}
V18 = {**V17, "fast_failed_launch_confirmations": 2}


def management(minutes, sides):
    sides = np.asarray(sides)
    frame = pd.DataFrame({
        "open_time": pd.date_range(E-pd.Timedelta(minutes=minutes), periods=len(sides), freq=f"{minutes}min"),
        "ma": np.where(sides == 1, 99., 101.), "ma_side": sides,
        "ma_slope_atr": np.nan, "high": 101., "low": 99., "close": 100.,
        "segment_id": 2 if minutes == 15 else 7,
    })
    frame.attrs.update(ma_kind="SMA", ma_length=40, bar_minutes=minutes)
    return frame


def quote(raw, minutes, price, invalid=False):
    row = raw.open_time.eq(E+pd.Timedelta(minutes=minutes))
    raw.loc[row, ["open", "high", "low", "close"]] = [price, max(price, 100.)+1, min(price, 100.)-1, price]
    if invalid:
        raw.loc[row, ["high", "low", "close"]] = np.nan


def fixture(direction=1):
    raw = pd.DataFrame({"open_time": pd.date_range(E-3*FIVE, periods=40, freq="5min"),
                        "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1., "segment_id": 19})
    raw["segment_id"] = raw["segment_id"].astype(object)
    quote(raw, 5, 100+direction*.1)
    quote(raw, 10, 100+direction*.05)
    quote(raw, 30, 100+direction*4, invalid=True)
    slow = management(15, [direction, direction, -direction, -direction])
    fast = management(5, [direction]+[-direction]*12)
    entries = pd.DataFrame([{"event_id": "confirmation", "decision_time": E, "direction": direction,
                             "initial_stop": 90. if direction == 1 else 110., "signal_atr": 2., "feature": .4}])
    return raw, slow, fast, entries


def run(data, policy=None, cutoff=None):
    raw, slow, fast, entries = data
    return simulate_events(raw, slow, entries, V18 if policy is None else policy,
                           end_exclusive=cutoff, fast_management_featured=fast)


def logs(result):
    return json.loads(result.failed_confirm_events)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("move", [-1., 0., .199, .2])
def test_two_complete_opposites_fill_second_actual_open_and_keep_true_edge(direction, move):
    data = fixture(direction)
    quote(data[0], 10, 100+direction*move)
    result = run(data).iloc[0]
    assert result.outcome == "fast_failed_launch" and result.closed
    assert result.entry_price == 100 and result.exit_time == E+2*FIVE
    assert result.exit_price == 100+direction*move
    assert result.net_return == pytest.approx(move/100-.002)
    assert result.failed_launch_count == result.failed_confirm_confirm_count == 1
    assert result.failed_confirm_create_count == 1
    assert result.failed_confirm_cancel_count == result.failed_confirm_priority_termination_count == 0
    assert result.failed_confirm_created_at == E+FIVE
    assert result.failed_confirm_due_at == result.failed_confirm_available_at == result.exit_time
    assert result.failed_launch_trigger_previous_open_time == E-FIVE
    assert result.failed_launch_trigger_open_time == E
    assert result.failed_launch_trigger_available_at == E+FIVE
    assert result.failed_launch_trigger_open_price == 100+direction*.1
    assert result.failed_confirm_previous_open_time == E
    assert result.failed_confirm_open_time == E+FIVE
    assert result.failed_confirm_open_price == result.exit_price
    assert result.failed_confirm_gross_return == result.gross_return
    assert result.failed_confirm_slow_side == direction
    assert result.failed_confirm_slow_state == "aligned"
    assert result.partial_fast_flip_count == 1  # Confirmation is NOT a fresh edge.
    assert result.partial_fast_fill_count == result.partial_fraction == result.realised_partial_gross_return == 0
    assert result.exit_remaining_fraction == 1
    assert result.failed_confirm_status == "confirmed_closed"
    created, confirmed = logs(result)
    assert created["action"] == "created" and created["observation"] is None
    assert confirmed["action"] == "confirmed" and confirmed["terminal"] is None
    assert created["edge"] == confirmed["edge"]
    assert created["edge"]["action"] == "failed_launch_pending"
    assert confirmed["observation"]["previous_fast"]["side"] == -direction
    assert confirmed["observation"]["current_fast"]["side"] == -direction
    assert confirmed["observation"]["fast_consecutive"] is True
    assert confirmed["observation"]["slow_available_at"] == E.isoformat()


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("cost", [.002, .003])
def test_exact_fee_equality_is_confirmed_and_accounting_is_exact_zero(direction, cost):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*.2)
    quote(data[0], 10, 100+direction*.2)
    result = run(data, {**V18, "cost_fraction": cost}).iloc[0]
    assert result.gross_return == .002
    assert result.net_return == .002-cost
    assert result.net_return <= 0  # No floating-point pseudo winner.
    assert logs(result)[1]["observation"]["profit_qualified"] is False


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("above", [False, True])
def test_second_open_nextafter_straddles_exact_decimal_boundary(direction, above):
    data = fixture(direction)
    toward = np.inf if direction*(1 if above else -1) > 0 else -np.inf
    quote(data[0], 10, np.nextafter(100+direction*.2, toward))
    result = run(data).iloc[0]
    assert (result.outcome == "fast_failed_launch") is (not above)
    if above:
        assert logs(result)[1]["reason"] == "profit_recovered"
        assert result.partial_fraction == result.failed_launch_count == 0
        assert result.partial_fast_flip_count == 1


@pytest.mark.parametrize("direction", [-1, 1])
def test_profitable_first_edge_half_is_immediate_and_all_old_fields_unchanged(direction):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*1)
    baseline, candidate = run(data, V17), run(data)
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    result = candidate.iloc[0]
    assert result.partial_exit_time == E+FIVE and result.partial_fraction == .5
    assert result.failed_confirm_events == "[]" and result.failed_confirm_create_count == 0


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_entry_phase_and_completed_bar_clock(direction, phase):
    data = fixture(direction)
    data[3]["decision_time"] = E+pd.Timedelta(minutes=phase)
    quote(data[0], phase, 100)
    quote(data[0], phase+5, 100+direction*.1)
    quote(data[0], phase+10, 100+direction*.05)
    data[2]["ma_side"] = direction
    data[2].loc[data[2].open_time.ge(E+pd.Timedelta(minutes=phase)), "ma_side"] = -direction
    result = run(data).iloc[0]
    assert result.exit_time == result.entry_time+2*FIVE
    assert result.failed_confirm_slow_available_at == result.exit_time.floor("15min")


@pytest.mark.parametrize("seed", ["opposite", "unknown"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_initial_opposite_or_unknown_requires_new_aligned_then_real_edge(seed, direction):
    data = fixture(direction)
    data[2]["ma_side"] = [-direction, -direction, direction]+[-direction]*10
    if seed == "unknown": data[2].loc[0, "ma_side"] = np.nan
    result = run(data).iloc[0]
    assert result.partial_fast_initial_state == seed
    assert result.failed_confirm_created_at == E+3*FIVE
    assert result.exit_time == E+4*FIVE


@pytest.mark.parametrize("kind", ["missing", "invalid", "zero", "segment", "aligned"])
def test_confirmation_failure_consumes_edge_never_latches_or_synthesizes_half(kind):
    raw, slow, fast, entries = fixture()
    if kind == "missing": fast = fast.drop(index=2)
    elif kind == "invalid": fast.loc[2, "ma"] = np.nan
    elif kind == "zero": fast.loc[2, "ma_side"] = 0
    elif kind == "segment": fast.loc[2:, "segment_id"] = 70
    else: fast.loc[2:, "ma_side"] = 1
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.outcome == "transition_colour_exit" and result.exit_time == E+6*FIVE
    assert result.failed_confirm_create_count == result.failed_confirm_cancel_count == 1
    assert result.failed_confirm_confirm_count == result.partial_fraction == 0
    reason = {"missing": "missing_management", "invalid": "nonfinite_management",
              "zero": "invalid_management", "segment": "management_sequence_change", "aligned": "fast_not_opposite"}[kind]
    assert logs(result)[1]["reason"] == reason
    assert result.partial_fast_flip_count == 1


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("recovery", [False, True])
def test_cancelled_pending_requires_rearm_then_fresh_edge(direction, recovery):
    data = fixture(direction)
    if recovery:
        quote(data[0], 10, 100+direction*.4)
        data[2]["ma_side"] = [direction, -direction, -direction, direction]+[-direction]*9
        expected_create, expected_exit = 20, 25
    else:
        data[2]["ma_side"] = [direction, -direction, direction]+[-direction]*10
        expected_create, expected_exit = 15, 20
    result = run(data).iloc[0]
    assert result.failed_confirm_create_count == 2 and result.failed_confirm_cancel_count == 1
    assert result.failed_confirm_confirm_count == 1
    assert result.failed_confirm_created_at == E+pd.Timedelta(minutes=expected_create)
    assert result.exit_time == E+pd.Timedelta(minutes=expected_exit)
    assert [event["action"] for event in logs(result)] == ["created", "cancelled", "created", "confirmed"]
    assert [event["pending_id"] for event in logs(result)] == [1, 1, 2, 2]
    assert result.partial_fast_flip_count == 2


@pytest.mark.parametrize("kind", ["missing", "invalid", "opposite_new_segment"])
def test_confirmation_rechecks_latest_complete_slow_not_unfinished_bar(kind):
    raw, slow, fast, entries = fixture()
    fast["ma_side"] = [1, 1]+[-1]*11  # first edge+10, confirmation+15
    if kind == "missing": slow = slow.drop(index=1)
    elif kind == "invalid": slow.loc[1, "ma"] = np.nan
    else:
        slow.loc[1:, "ma_side"] = -1
        slow.loc[1:, "segment_id"] = 33  # resets slow; not a true slow exit
    result = run((raw, slow, fast, entries), {**V18, "max_minutes": 30}).iloc[0]
    assert result.failed_confirm_create_count == result.failed_confirm_cancel_count == 1
    assert result.failed_confirm_confirm_count == 0
    observation = logs(result)[1]["observation"]
    assert observation["slow_available_at"] == (E+3*FIVE).isoformat()
    assert logs(result)[1]["reason"] == ("slow_not_aligned" if kind == "opposite_new_segment" else "slow_unknown")


@pytest.mark.parametrize("direction", [-1, 1])
def test_unfinished_slow_future_colour_cannot_cancel_confirmation(direction):
    data = fixture(direction)
    data[1].loc[data[1].open_time.ge(E), ["ma", "ma_side", "high", "low", "close"]] = np.nan
    assert run(data).iloc[0].exit_time == E+2*FIVE


@pytest.mark.parametrize("kind", ["gap_stop", "intrabar_stop", "slow_exit", "deadline"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_existing_priority_terminates_pending_without_confirming(kind, direction):
    data = fixture(direction)
    policy = dict(V18)
    expected_time = E+2*FIVE
    expected = "hard_stop_gap" if kind == "gap_stop" else "hard_stop" if kind == "intrabar_stop" else "time_exit" if kind == "deadline" else "transition_colour_exit"
    if kind == "gap_stop": quote(data[0], 10, 89 if direction == 1 else 111, invalid=True)
    elif kind == "intrabar_stop":
        data[0].loc[data[0].open_time.eq(E+FIVE), "low" if direction == 1 else "high"] = 89 if direction == 1 else 111
    elif kind == "deadline": policy["max_minutes"] = 10
    else:
        data[2]["ma_side"] = [direction, direction]+[-direction]*11
        data[1].loc[1:, "ma_side"] = -direction
        expected_time = E+3*FIVE
    result = run(data, policy).iloc[0]
    assert result.outcome == expected and result.exit_time == expected_time
    assert result.failed_confirm_create_count == result.failed_confirm_priority_termination_count == 1
    assert result.failed_confirm_confirm_count == result.failed_confirm_cancel_count == 0
    terminal = logs(result)[1]
    assert terminal["action"] == "terminated" and terminal["reason"] == expected
    assert terminal["observation"] is None and terminal["terminal"]["closed"] is True


@pytest.mark.parametrize("kind", ["raw_missing", "raw_segment", "segment_nan", "segment_inf", "open_nan", "open_zero", "open_negative", "prior_hlc"])
def test_invalid_raw_source_censors_pending_without_synthetic_fill(kind):
    raw, slow, fast, entries = fixture()
    current = raw.open_time.eq(E+2*FIVE)
    if kind == "raw_missing": raw = raw.loc[~current]
    elif kind == "raw_segment": raw.loc[current, "segment_id"] = 45
    elif kind == "segment_nan": raw.loc[current, "segment_id"] = np.nan
    elif kind == "segment_inf": raw.loc[current, "segment_id"] = np.inf
    elif kind.startswith("open_"):
        raw.loc[current, "open"] = {"open_nan": np.nan, "open_zero": 0., "open_negative": -1.}[kind]
    else: raw.loc[raw.open_time.eq(E+FIVE), "close"] = np.nan
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.outcome == "data_gap_censored" and not result.closed
    assert pd.isna(result.net_return)
    assert result.failed_launch_count == result.failed_confirm_confirm_count == 0
    assert result.failed_confirm_priority_termination_count == 1
    assert logs(result)[1]["terminal"]["closed"] is False
    assert logs(result)[1]["observation"] is None


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("kind", ["nan_hlc", "later_stop", "suffix_gap"])
def test_confirmation_open_fill_cannot_be_cancelled_by_current_hlc_or_future(kind, direction):
    data = fixture(direction)
    reference = run(data)
    raw = data[0]
    if kind == "nan_hlc": raw.loc[raw.open_time.eq(E+2*FIVE), ["high", "low", "close"]] = np.nan
    elif kind == "later_stop": raw.loc[raw.open_time.eq(E+2*FIVE), "low" if direction == 1 else "high"] = 1 if direction == 1 else 999
    else:
        raw.loc[raw.open_time.gt(E+2*FIVE), ["open", "high", "low", "close", "segment_id"]] = np.nan
    pd.testing.assert_frame_equal(reference, run(data))


@pytest.mark.parametrize("cutoff_minutes,closed", [(9, False), (10, False), (11, True)])
def test_cutoff_never_invents_next_open(cutoff_minutes, closed):
    result = run(fixture(), cutoff=E+pd.Timedelta(minutes=cutoff_minutes)).iloc[0]
    assert result.closed == closed
    assert result.outcome == ("fast_failed_launch" if closed else "right_censored")
    assert result.failed_confirm_confirm_count == int(closed)
    assert result.failed_confirm_priority_termination_count == int(not closed)


def test_eof_pending_remains_unknown_not_zero():
    raw, slow, fast, entries = fixture()
    result = run((raw.loc[raw.open_time.lt(E+2*FIVE)], slow, fast, entries)).iloc[0]
    assert result.outcome == "right_censored" and pd.isna(result.net_return)
    assert result.failed_confirm_status == "unknown_source"
    assert logs(result)[-1]["action"] == "terminated"


@pytest.mark.parametrize("direction", [-1, 1])
def test_after_profitable_half_later_failed_edges_cannot_create_pending(direction):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*1)
    data[2]["ma_side"] = [direction, -direction, direction]+[-direction]*10
    result = run(data).iloc[0]
    assert result.partial_fraction == .5 and result.failed_confirm_events == "[]"
    assert [event["action"] for event in json.loads(result.partial_fast_events)] == ["executed", "already_partial"]


@pytest.mark.parametrize("direction", [-1, 1])
def test_profit_recovery_cancellation_may_only_half_on_later_fresh_edge(direction):
    data = fixture(direction)
    quote(data[0], 10, 100+direction*1)
    quote(data[0], 20, 100+direction*.4)
    data[2]["ma_side"] = [direction, -direction, -direction, direction]+[-direction]*9
    result = run(data).iloc[0]
    assert result.failed_confirm_cancel_count == result.failed_confirm_create_count == 1
    assert result.failed_confirm_confirm_count == 0
    assert result.partial_exit_time == E+4*FIVE and result.partial_fraction == .5
    assert result.partial_fast_flip_count == 2
    assert [event["action"] for event in json.loads(result.partial_fast_events)] == ["failed_launch_pending", "executed"]


@pytest.mark.parametrize("kind", ["unknown", "opposite", "source_reset"])
def test_no_real_first_edge_means_no_pending_or_confirmation(kind):
    data = fixture()
    data[2]["ma_side"] = np.nan if kind == "unknown" else -1
    if kind == "source_reset":
        data[2].loc[0, "ma_side"] = 1
        data[2].loc[1:, "segment_id"] = 44
    result = run(data).iloc[0]
    assert result.failed_confirm_events == "[]"
    assert result.failed_confirm_create_count == result.failed_confirm_confirm_count == 0
    assert result.partial_fast_flip_count == 0


@pytest.mark.parametrize("missing_segment", [pd.NA, np.nan, None, np.inf])
def test_invalid_management_segment_cancels_and_json_stays_nullable(missing_segment):
    data = fixture()
    data[2]["segment_id"] = data[2]["segment_id"].astype(object)
    data[2].loc[2, "segment_id"] = missing_segment
    result = run(data).iloc[0]
    assert result.failed_confirm_cancel_count == 1 and result.failed_confirm_confirm_count == 0
    cancelled = logs(result)[1]
    assert cancelled["reason"] == "unknown_management_segment"
    assert cancelled["observation"]["current_fast"]["side"] is None
    assert cancelled["observation"]["fast_consecutive"] is False


def test_opaque_numpy_management_segment_serializes_without_changing_clock():
    data = fixture()
    data[2]["segment_id"] = pd.Series([np.int64(7)]*len(data[2]), dtype=object)
    result = run(data).iloc[0]
    assert result.outcome == "fast_failed_launch"
    assert logs(result)[1]["observation"]["fast_consecutive"] is True


@pytest.mark.parametrize("target", ["raw", "fast"])
def test_one_nanosecond_clock_change_cannot_bridge_pending_confirmation(target):
    raw, slow, fast, entries = fixture()
    if target == "raw":
        raw.loc[raw.open_time.eq(E+2*FIVE), "open_time"] += pd.Timedelta(nanoseconds=1)
    else:
        fast.loc[fast.open_time.eq(E+FIVE), "open_time"] += pd.Timedelta(nanoseconds=1)
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.failed_confirm_confirm_count == 0
    if target == "raw":
        assert not result.closed and result.failed_confirm_priority_termination_count == 1
    else:
        assert result.failed_confirm_cancel_count == 1
        assert logs(result)[1]["reason"] == "missing_management"


def test_future_management_suffix_cannot_change_confirmed_prefix_or_seed():
    data = fixture()
    reference = run(data)
    for frame in (data[1], data[2]):
        frame.loc[frame.open_time.ge(E+2*FIVE), ["ma", "ma_side", "high", "low", "close"]] = np.nan
    pd.testing.assert_frame_equal(reference, run(data))


def test_entry_only_open_at_incomplete_cutoff_does_not_use_entry_hlc():
    data = fixture()
    cutoff = E+pd.Timedelta(minutes=1)
    reference = run(data, cutoff=cutoff)
    data[0].loc[data[0].open_time.eq(E), ["high", "low", "close"]] = np.nan
    pd.testing.assert_frame_equal(reference, run(data, cutoff=cutoff))
    assert reference.iloc[0].failed_confirm_events == "[]"


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("variant", ["normal", "partial", "gap", "invalid", "empty", "off"])
def test_explicit_integer_one_is_full_column_v17_parity(direction, variant):
    data = fixture(direction)
    policy = dict(V17)
    if variant == "partial": quote(data[0], 5, 100+direction*1)
    elif variant == "gap": data[0].loc[data[0].open_time.eq(E+FIVE), "open"] = np.nan
    elif variant == "invalid": data[3]["initial_stop"] = 100
    elif variant == "empty": data = (*data[:3], data[3].iloc[:0])
    elif variant == "off": policy["fast_failed_launch_exit"] = False
    baseline = run(data, policy)
    explicit = run(data, {**policy, "fast_failed_launch_confirmations": 1})
    pd.testing.assert_frame_equal(baseline, explicit)
    assert not any(name.startswith("failed_confirm_") for name in explicit)


@pytest.mark.parametrize("value", [True, False, np.bool_(True), 0, 3, -1, 1., 2., np.float64(2), np.nan, np.inf, "2", None])
def test_invalid_confirmation_option_rejected_even_with_empty_requests(value):
    data = fixture()
    data = (*data[:3], data[3].iloc[:0])
    with pytest.raises(ValueError, match="fast_failed_launch_confirmations"):
        run(data, {**V17, "fast_failed_launch_confirmations": value})


@pytest.mark.parametrize("option", ["missing_failed", "failed_false", "missing_partial", "native5", "colour", "decision15"])
def test_confirmation_two_requires_unchanged_v17_native15_contract(option):
    policy = dict(V18)
    if option == "missing_failed": policy.pop("fast_failed_launch_exit")
    elif option == "failed_false": policy["fast_failed_launch_exit"] = False
    elif option == "missing_partial": policy.pop("fast_partial_fraction")
    elif option == "native5": policy["management_minutes"] = 5
    elif option == "colour": policy["exit_mode"] = "colour"
    else: policy["decision_minutes"] = 15
    with pytest.raises(ValueError): run(fixture(), policy)


def test_numpy_integer_two_and_empty_schema_and_invalid_risk_preserved():
    data = fixture()
    candidate = run(data, {**V18, "fast_failed_launch_confirmations": np.int64(2)})
    assert candidate.iloc[0].failed_confirm_confirm_count == 1
    empty = run((*data[:3], data[3].iloc[:0]))
    assert empty.empty and "failed_confirm_events" in empty
    data[3]["initial_stop"] = 101
    invalid = run(data).iloc[0]
    assert invalid.outcome == "entry_invalid_risk" and invalid.failed_confirm_status == "entry_not_validated"
    assert invalid.failed_confirm_events == "[]"


def test_actual_72h_horizon_wins_pending_confirmation_without_clock_drift():
    raw, slow, fast, entries = fixture()
    raw = pd.DataFrame({"open_time": pd.date_range(E-3*FIVE, periods=870, freq="5min"),
                        "open": 100., "high": 101., "low": 99., "close": 100., "segment_id": 19})
    slow = management(15, [1]*290)
    fast = management(5, [1]*863+[-1]*5)
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.failed_confirm_created_at == E+pd.Timedelta(minutes=4315)
    assert result.failed_confirm_due_at == result.exit_time == E+pd.Timedelta(hours=72)
    assert result.outcome == "time_exit" and result.failed_confirm_priority_termination_count == 1
    assert result.hold_minutes == 4320
