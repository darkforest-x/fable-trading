"""V19 synthetic risk reduction and frozen old-policy execution parity."""
from decimal import Decimal
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


E = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
V18 = {"management_minutes": 15, "exit_mode": "transition_colour", "confirmations": 1,
       "fast_partial_fraction": .5, "fast_failed_launch_exit": True, "fast_failed_launch_confirmations": 2}
V19 = {**V18, "fast_failed_launch_fraction": .5}


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
    entries = pd.DataFrame([{"event_id": "reduce", "decision_time": E, "direction": direction,
                             "initial_stop": 90. if direction == 1 else 110., "signal_atr": 2., "feature": .4}])
    return raw, slow, fast, entries


def run(data, policy=None, cutoff=None):
    raw, slow, fast, entries = data
    return simulate_events(raw, slow, entries, V19 if policy is None else policy,
                           end_exclusive=cutoff, fast_management_featured=fast)


def logs(result):
    return json.loads(result.failed_confirm_events)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("first_move", [-2., .2])
@pytest.mark.parametrize("last_move", [-8., .2, 4.])
@pytest.mark.parametrize("cost", [.002, .003])
def test_confirmed_half_and_slow_remainder_weight_original_notional_once(direction, first_move, last_move, cost):
    data = fixture(direction)
    quote(data[0], 10, 100+direction*first_move)
    quote(data[0], 30, 100+direction*last_move, invalid=True)
    result = run(data, {**V19, "cost_fraction": cost}).iloc[0]
    assert result.outcome == "transition_colour_exit" and result.closed
    assert result.exit_time == E+6*FIVE and result.exit_price == 100+direction*last_move
    assert result.partial_exit_time == result.failed_reduce_fill_time == E+2*FIVE
    assert result.partial_exit_price == result.failed_reduce_fill_price == 100+direction*first_move
    assert result.gross_return == pytest.approx(.5*(first_move+last_move)/100)
    assert result.net_return == pytest.approx(.5*(first_move+last_move)/100-cost)
    assert result.net_r == pytest.approx(result.net_return/.1)
    assert result.failed_reduce_realised_gross_return == pytest.approx(.5*first_move/100)
    assert result.failed_reduce_realised_net_return == pytest.approx(.5*(first_move/100-cost))
    assert result.realised_partial_gross_return == result.failed_reduce_realised_gross_return
    assert result.partial_fraction == result.failed_reduce_fraction == result.exit_remaining_fraction == .5
    assert result.failed_reduce_fill_count == result.failed_confirm_confirm_count == 1
    assert result.failed_launch_count == result.partial_fast_fill_count == 0
    assert result.partial_fast_realised_net_return == 0  # Not profitable fast TP.
    assert result.failed_reduce_role == "risk_reduction"
    assert result.failed_reduce_status == result.partial_fast_status == "risk_reduced_closed"
    assert result.failed_launch_status == "risk_reduced_closed"
    assert result.failed_confirm_status == "confirmed_reduced_closed"
    assert result.initial_stop == (90 if direction == 1 else 110)
    created, confirmed = logs(result)
    assert created["action"] == "created" and confirmed["action"] == "confirmed"
    assert confirmed["observation"]["fill_action"] == "risk_reduce"
    assert confirmed["observation"]["fill_fraction"] == .5
    assert confirmed["observation"]["fill_price"] == result.partial_exit_price
    assert confirmed["observation"]["fill_available_at"] == result.partial_exit_time.isoformat()
    assert result.partial_fast_flip_count == 1
    assert result.failed_launch_trigger_available_at == E+FIVE  # Original true edge retained.
    assert result.failed_confirm_available_at == E+2*FIVE
    if first_move == last_move == .2:
        assert result.gross_return == .002
        assert result.net_return == .002-cost
        assert result.failed_reduce_realised_net_return == .001-.5*cost


@pytest.mark.parametrize("direction", [-1, 1])
def test_risk_reduction_is_not_average_of_future_partial_and_full_trade(direction):
    data = fixture(direction)
    data[2]["ma_side"] = [direction, -direction, -direction, direction]+[-direction]*9
    quote(data[0], 20, 100+direction*3)
    result = run(data).iloc[0]
    assert result.failed_reduce_fill_count == 1
    assert result.partial_exit_time == E+2*FIVE and result.partial_exit_price == 100+direction*.05
    assert result.partial_fast_fill_count == 0
    assert [edge["action"] for edge in json.loads(result.partial_fast_events)] == ["failed_launch_pending", "already_partial"]
    assert result.net_return == pytest.approx(.5*.0005+.5*.04-.002)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("move", [.20001, 2.])
def test_profitable_first_edge_half_has_all_old_fields_unchanged(direction, move):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*move)
    baseline, candidate = run(data, V18), run(data)
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    result = candidate.iloc[0]
    assert result.partial_fast_fill_count == 1 and result.failed_reduce_fill_count == 0
    assert result.failed_reduce_fraction == result.failed_reduce_realised_gross_return == 0
    assert result.failed_reduce_status == "not_reduced_exit"


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("above", [False, True])
def test_confirmation_strict_decimal_boundary_is_unchanged(direction, above):
    data = fixture(direction)
    toward = np.inf if direction*(1 if above else -1) > 0 else -np.inf
    quote(data[0], 10, np.nextafter(100+direction*.2, toward))
    result = run(data).iloc[0]
    assert result.failed_reduce_fill_count == int(not above)
    if above:
        assert logs(result)[1]["reason"] == "profit_recovered"
        assert result.partial_fraction == 0  # No fresh edge: no profitable half.


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("kind", ["aligned", "missing", "invalid", "segment"])
def test_cancelled_confirmation_requires_fresh_edge_before_reduction(direction, kind):
    raw, slow, fast, entries = fixture(direction)
    fast["ma_side"] = [direction, -direction, -direction, direction]+[-direction]*9
    if kind == "aligned": fast.loc[2, "ma_side"] = direction
    elif kind == "missing": fast = fast.drop(index=2)
    elif kind == "invalid": fast.loc[2, "ma"] = np.nan
    else: fast.loc[2:, "segment_id"] = 71
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.failed_confirm_cancel_count == 1 and result.failed_confirm_create_count == 2
    assert result.failed_confirm_confirm_count == result.failed_reduce_fill_count == 1
    assert result.partial_exit_time == E+5*FIVE
    assert [item["action"] for item in logs(result)] == ["created", "cancelled", "created", "confirmed"]


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("kind", ["gap_stop", "prior_intrabar", "slow", "deadline"])
def test_higher_priority_exit_prevents_risk_reduction_and_all_old_fields_match(direction, kind):
    data = fixture(direction)
    policy = dict(V18)
    if kind == "gap_stop": quote(data[0], 10, 89 if direction == 1 else 111, invalid=True)
    elif kind == "prior_intrabar":
        data[0].loc[data[0].open_time.eq(E+FIVE), "low" if direction == 1 else "high"] = 89 if direction == 1 else 111
    elif kind == "slow":
        data[2]["ma_side"] = [direction, direction]+[-direction]*11
        data[1].loc[1:, "ma_side"] = -direction
    else: policy["max_minutes"] = 10
    baseline = run(data, policy)
    candidate = run(data, {**policy, "fast_failed_launch_fraction": .5})
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    assert candidate.iloc[0].failed_reduce_fill_count == 0


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("kind", ["intrabar", "later_gap"])
def test_after_opening_reduction_hard_stop_only_closes_remaining_half(direction, kind):
    data = fixture(direction)
    if kind == "intrabar":
        data[0].loc[data[0].open_time.eq(E+2*FIVE), "low" if direction == 1 else "high"] = 89 if direction == 1 else 111
        fill = 90 if direction == 1 else 110
        outcome = "hard_stop"
    else:
        fill = 88 if direction == 1 else 112
        quote(data[0], 15, fill, invalid=True)
        outcome = "hard_stop_gap"
    result = run(data).iloc[0]
    assert result.closed and result.outcome == outcome and result.exit_time == E+3*FIVE
    assert result.failed_reduce_fill_count == 1 and result.exit_remaining_fraction == .5
    assert result.net_return == pytest.approx(.5*.0005+.5*direction*(fill-100)/100-.002)
    assert result.failed_confirm_priority_termination_count == 0  # Pending already consumed by reduction.


@pytest.mark.parametrize("kind", ["raw_missing", "raw_segment", "open_nan", "open_zero", "open_negative", "segment_inf"])
def test_bad_confirmation_source_does_not_execute_half(kind):
    raw, slow, fast, entries = fixture()
    mask = raw.open_time.eq(E+2*FIVE)
    if kind == "raw_missing": raw = raw.loc[~mask]
    elif kind == "raw_segment": raw.loc[mask, "segment_id"] = 91
    elif kind == "segment_inf": raw.loc[mask, "segment_id"] = np.inf
    else: raw.loc[mask, "open"] = {"open_nan": np.nan, "open_zero": 0., "open_negative": -1.}[kind]
    baseline = run((raw, slow, fast, entries), V18)
    candidate = run((raw, slow, fast, entries))
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    assert candidate.iloc[0].failed_reduce_fill_count == 0
    assert not candidate.iloc[0].closed and pd.isna(candidate.iloc[0].net_return)


@pytest.mark.parametrize("kind", ["same_hlc", "later_gap", "later_open", "later_hlc", "cutoff", "eof"])
def test_known_reduction_never_turns_unknown_remainder_into_known_total(kind):
    raw, slow, fast, entries = fixture()
    cutoff = None
    if kind == "same_hlc": raw.loc[raw.open_time.eq(E+2*FIVE), ["high", "low", "close"]] = np.nan
    elif kind == "later_gap": raw = raw.loc[~raw.open_time.eq(E+3*FIVE)]
    elif kind == "later_open": raw.loc[raw.open_time.eq(E+3*FIVE), "open"] = np.nan
    elif kind == "later_hlc": raw.loc[raw.open_time.eq(E+3*FIVE), "close"] = np.nan
    elif kind == "cutoff": cutoff = E+pd.Timedelta(minutes=11)
    else: raw = raw.loc[raw.open_time.le(E+2*FIVE)]
    result = run((raw, slow, fast, entries), cutoff=cutoff).iloc[0]
    assert result.failed_reduce_fill_count == 1 and result.partial_fraction == .5
    assert result.partial_exit_time == E+2*FIVE
    assert result.failed_reduce_realised_net_return == pytest.approx(.5*(.0005-.002))
    assert result.failed_reduce_realised_gross_return == result.realised_partial_gross_return
    assert not result.closed and pd.isna(result.net_return) and pd.isna(result.gross_return) and pd.isna(result.net_r)
    assert result.failed_reduce_status == result.partial_fast_status == "risk_reduced_censored"
    assert result.failed_confirm_status == "confirmed_reduced_censored"
    assert logs(result)[-1]["action"] == "confirmed"  # Actual fill cannot be undone by later bad HLC.


@pytest.mark.parametrize("kind", ["opposite", "missing", "invalid"])
def test_latest_slow_colour_still_required_on_confirmation(kind):
    raw, slow, fast, entries = fixture()
    fast["ma_side"] = [1, 1]+[-1]*11
    if kind == "opposite":
        slow.loc[1:, "ma_side"] = -1
        slow.loc[1:, "segment_id"] = 22
    elif kind == "missing": slow = slow.drop(index=1)
    else: slow.loc[1, "ma"] = np.nan
    baseline = run((raw, slow, fast, entries), V18)
    candidate = run((raw, slow, fast, entries))
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    assert candidate.iloc[0].failed_reduce_fill_count == 0


@pytest.mark.parametrize("direction", [-1, 1])
def test_future_changes_cannot_change_reduction_decision_but_may_change_remainder(direction):
    data = fixture(direction)
    reference = run(data).iloc[0]
    quote(data[0], 30, 100-direction*3, invalid=True)
    changed = run(data).iloc[0]
    for field in [name for name in reference.index if name.startswith("failed_reduce_") and name != "failed_reduce_status"]:
        assert changed[field] == reference[field]
    assert changed.failed_confirm_events == reference.failed_confirm_events
    assert changed.partial_exit_time == reference.partial_exit_time and changed.net_return != reference.net_return
    assert changed.max_favourable_r != reference.max_favourable_r


def test_final_exit_open_does_not_read_current_or_later_hlc():
    data = fixture()
    reference = run(data)
    data[0].loc[data[0].open_time.ge(E+6*FIVE), ["high", "low", "close"]] = np.nan
    data[0].loc[data[0].open_time.gt(E+6*FIVE), ["open", "segment_id"]] = np.nan
    pd.testing.assert_frame_equal(reference, run(data))


def test_remainder_keeps_exact_original_72h_deadline_and_stop():
    _, _, fast, entries = fixture()
    raw = pd.DataFrame({"open_time": pd.date_range(E-3*FIVE, periods=870, freq="5min"),
                        "open": 100., "high": 101., "low": 99., "close": 100., "segment_id": 19})
    slow = management(15, [1]*290)
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.partial_exit_time == E+2*FIVE
    assert result.exit_time == E+pd.Timedelta(hours=72) and result.hold_minutes == 4320
    assert result.outcome == "time_exit" and result.initial_stop == 90
    assert result.net_return == -.002 and result.exit_remaining_fraction == .5


@pytest.mark.parametrize("fraction", [1, 1., np.int64(1), np.float64(1)])
@pytest.mark.parametrize("variant", ["normal", "partial", "censor", "empty", "invalid", "legacy"])
def test_explicit_one_preserves_all_old_fields_and_output_schema(fraction, variant):
    data = fixture()
    policy = dict(V18)
    if variant == "partial": quote(data[0], 5, 101.)
    elif variant == "censor": data[0].loc[data[0].open_time.eq(E+2*FIVE), "open"] = np.nan
    elif variant == "empty": data = (*data[:3], data[3].iloc[:0])
    elif variant == "invalid": data[3]["initial_stop"] = 101
    elif variant == "legacy":
        policy.pop("fast_failed_launch_confirmations")
        policy["fast_failed_launch_exit"] = False
    baseline, explicit = run(data, policy), run(data, {**policy, "fast_failed_launch_fraction": fraction})
    pd.testing.assert_frame_equal(baseline, explicit)
    assert not any(name.startswith("failed_reduce_") for name in explicit)


@pytest.mark.parametrize("value", [True, False, np.bool_(True), "0.5", "1", None, .25, .75, 0, -1, 2, np.nan, np.inf, -np.inf, Decimal("0.5")])
def test_invalid_fraction_fails_even_with_empty_entries(value):
    data = fixture()
    data = (*data[:3], data[3].iloc[:0])
    with pytest.raises(ValueError, match="fast_failed_launch_fraction"):
        run(data, {**V18, "fast_failed_launch_fraction": value})


@pytest.mark.parametrize("kind", ["launch_missing", "launch_false", "count_missing", "count_one", "partial_missing", "partial_quarter", "native5", "state", "sampled"])
def test_half_requires_exact_v18_native15_contract(kind):
    policy = dict(V19)
    if kind == "launch_missing": policy.pop("fast_failed_launch_exit")
    elif kind == "launch_false": policy["fast_failed_launch_exit"] = False
    elif kind == "count_missing": policy.pop("fast_failed_launch_confirmations")
    elif kind == "count_one": policy["fast_failed_launch_confirmations"] = 1
    elif kind == "partial_missing": policy.pop("fast_partial_fraction")
    elif kind == "partial_quarter": policy["fast_partial_fraction"] = .25
    elif kind == "native5": policy["management_minutes"] = 5
    elif kind == "state": policy["exit_mode"] = "colour"
    else: policy["decision_minutes"] = 15
    with pytest.raises(ValueError): run(fixture(), policy)


@pytest.mark.parametrize("value", [.5, np.float32(.5), np.float64(.5)])
def test_exact_supported_half_types_and_empty_and_rejected_schema(value):
    data = fixture()
    policy = {**V19, "fast_failed_launch_fraction": value}
    assert run(data, policy).iloc[0].failed_reduce_fill_count == 1
    empty = run((*data[:3], data[3].iloc[:0]), policy)
    assert empty.empty and "failed_reduce_fill_time" in empty
    data[3]["initial_stop"] = 101
    rejected = run(data, policy).iloc[0]
    assert rejected.outcome == "entry_invalid_risk" and rejected.failed_reduce_fill_count == 0
    assert rejected.failed_reduce_status == "entry_not_validated"
    assert pd.isna(rejected.failed_reduce_fill_time) and pd.isna(rejected.failed_reduce_full_notional_gross_return)


def test_multiple_entry_replays_do_not_share_reduction_state():
    raw, slow, fast, entries = fixture()
    second = entries.copy()
    second["event_id"] = "independent"
    combined = pd.concat([entries, second], ignore_index=True)
    result = run((raw, slow, fast, combined))
    assert result.failed_reduce_fill_count.tolist() == [1, 1]
    pd.testing.assert_series_equal(result.iloc[0].drop("event_id"), result.iloc[1].drop("event_id"), check_names=False)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_all_entry_phases_preserve_completed_native_clock(direction, phase):
    data = fixture(direction)
    entry = E+pd.Timedelta(minutes=phase)
    data[3]["decision_time"] = entry
    quote(data[0], phase, 100.)
    quote(data[0], phase+5, 100+direction*.1)
    quote(data[0], phase+10, 100+direction*.05)
    data[2]["ma_side"] = direction
    data[2].loc[data[2].open_time.ge(entry), "ma_side"] = -direction
    result = run(data).iloc[0]
    assert result.failed_reduce_fill_time == entry+2*FIVE
    assert result.failed_launch_trigger_available_at == entry+FIVE
    assert result.failed_confirm_slow_available_at == result.partial_exit_time.floor("15min")


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("scenario", ["normal", "profitable_first", "cancelled", "same_bar_stop", "later_gap_stop", "same_bar_unknown", "later_unknown", "deadline"])
def test_terminal_structure_is_v16_slow_path_not_borrowed_v16_returns(direction, scenario):
    data = fixture(direction)
    v16 = {key: value for key, value in V18.items() if key not in {"fast_failed_launch_exit", "fast_failed_launch_confirmations"}}
    candidate_policy = dict(V19)
    if scenario == "profitable_first": quote(data[0], 5, 100+direction*1)
    elif scenario == "cancelled": quote(data[0], 10, 100+direction*1)
    elif scenario == "same_bar_stop":
        data[0].loc[data[0].open_time.eq(E+2*FIVE), "low" if direction == 1 else "high"] = 89 if direction == 1 else 111
    elif scenario == "later_gap_stop": quote(data[0], 15, 89 if direction == 1 else 111, invalid=True)
    elif scenario == "same_bar_unknown": data[0].loc[data[0].open_time.eq(E+2*FIVE), "close"] = np.nan
    elif scenario == "later_unknown": data[0].loc[data[0].open_time.eq(E+3*FIVE), "open"] = np.nan
    elif scenario == "deadline":
        v16["max_minutes"] = candidate_policy["max_minutes"] = 20
    original, candidate = run(data, v16), run(data, candidate_policy)
    structure = ["entry_time", "entry_price", "exit_time", "exit_price", "outcome", "closed", "hold_minutes",
                 "risk_pct", "risk_atr", "initial_stop", "max_favourable_r", "max_adverse_r", "bars_to_first_positive"]
    structure += [name for name in original if name.startswith("transition_")]
    pd.testing.assert_frame_equal(original[structure], candidate[structure])
    if scenario == "normal":
        assert original.iloc[0].net_return != candidate.iloc[0].net_return
        assert candidate.iloc[0].failed_reduce_fill_count == 1
