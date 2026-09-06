"""Synthetic V17 failed-launch execution; no prices/results are read from disk."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


E = pd.Timestamp("2024-01-01T01:00:00Z")
FIVE = pd.Timedelta(minutes=5)
V16 = {"management_minutes": 15, "exit_mode": "transition_colour",
       "confirmations": 1, "fast_partial_fraction": 0.5}
V17 = {**V16, "fast_failed_launch_exit": True}


def management(minutes, sides):
    frame = pd.DataFrame({
        "open_time": pd.date_range(E-pd.Timedelta(minutes=minutes), periods=len(sides), freq=f"{minutes}min"),
        "ma": np.where(np.asarray(sides) == 1, 99., 101.), "ma_side": sides,
        "ma_slope_atr": np.nan, "high": 101., "low": 99., "close": 100.,
        "segment_id": 2 if minutes == 15 else 7,
    })
    frame.attrs.update(ma_kind="SMA", ma_length=40, bar_minutes=minutes)
    return frame


def quote(raw, minutes, price, *, invalid=False):
    row = raw.open_time.eq(E+pd.Timedelta(minutes=minutes))
    raw.loc[row, ["open", "high", "low", "close"]] = [price, max(price, 100.)+1, min(price, 100.)-1, price]
    if invalid:
        raw.loc[row, ["high", "low", "close"]] = np.nan


def fixture(direction=1):
    raw = pd.DataFrame({"open_time": pd.date_range(E-3*FIVE, periods=40, freq="5min"),
                        "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1., "segment_id": 19})
    quote(raw, 5, 100+direction*.1)
    quote(raw, 30, 100+direction*4, invalid=True)
    slow = management(15, [direction, direction, -direction])
    fast = management(5, [direction, -direction, -direction, direction, -direction, -direction, -direction])
    entries = pd.DataFrame([{"event_id": "failed", "decision_time": E, "direction": direction,
                             "initial_stop": 90. if direction == 1 else 110., "signal_atr": 2., "known_feature": .4}])
    return raw, slow, fast, entries


def run(data, policy=None, cutoff=None):
    raw, slow, fast, entries = data
    return simulate_events(raw, slow, entries, V17 if policy is None else policy,
                           end_exclusive=cutoff, fast_management_featured=fast)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("move", [-1., 0., .199, .2, .2001, 2.])
def test_strict_decimal_boundary_full_failure_or_unchanged_partial(direction, move):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*move)
    result = run(data).iloc[0]
    event = json.loads(result.partial_fast_events)[0]
    if move <= .2:
        assert result.outcome == "fast_failed_launch" and result.closed
        assert result.exit_time == E+FIVE and result.exit_price == 100+direction*move
        assert result.gross_return == pytest.approx(move/100)
        assert result.net_return == pytest.approx(move/100-.002)
        assert result.net_r == pytest.approx((move/100-.002)/.1)
        assert result.failed_launch_count == 1
        assert result.partial_fraction == result.partial_fast_fill_count == 0
        assert result.realised_partial_gross_return == result.partial_fast_realised_net_return == 0
        assert result.exit_remaining_fraction == 1 and pd.isna(result.partial_exit_time)
        assert result.failed_launch_status == result.partial_fast_status == "failed_launch_closed"
        assert event["action"] == "failed_launch_exit" and event["profit_qualified"] is False
    else:
        baseline = run(data, V16)
        pd.testing.assert_frame_equal(baseline, run(data)[baseline.columns])
        assert result.partial_fraction == .5 and result.failed_launch_count == 0
        assert event["action"] == "executed" and event["profit_qualified"] is True


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("above", [False, True])
def test_nearest_representable_prices_straddle_decimal_threshold(direction, above):
    data = fixture(direction)
    toward = np.inf if direction*(1 if above else -1) > 0 else -np.inf
    quote(data[0], 5, np.nextafter(100+direction*.2, toward))
    result = run(data).iloc[0]
    assert (result.outcome == "fast_failed_launch") is (not above)
    assert result.partial_fraction == (.5 if above else 0)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("phase", [0, 5, 10])
def test_causal_source_diagnostics_and_first_postentry_edge_all_phases(direction, phase):
    data = fixture(direction)
    data[3]["decision_time"] = E+pd.Timedelta(minutes=phase)
    quote(data[0], phase, 100.)
    quote(data[0], phase+5, 100+direction*.1)
    data[2]["ma_side"] = direction
    data[2].loc[data[2].open_time.ge(E+pd.Timedelta(minutes=phase)), "ma_side"] = -direction
    result = run(data).iloc[0]
    entry = E+pd.Timedelta(minutes=phase)
    assert result.entry_time == entry and result.exit_time == entry+FIVE
    assert result.failed_launch_trigger_previous_open_time == entry-FIVE
    assert result.failed_launch_trigger_previous_available_at == entry
    assert result.failed_launch_trigger_open_time == entry
    assert result.failed_launch_trigger_available_at == entry+FIVE
    assert result.failed_launch_trigger_previous_side == direction
    assert result.failed_launch_trigger_side == -direction
    assert result.failed_launch_trigger_open_price == result.exit_price
    assert result.failed_launch_trigger_gross_return == result.gross_return
    assert result.failed_launch_slow_available_at == result.exit_time.floor("15min")
    assert result.failed_launch_slow_open_time+3*FIVE == result.failed_launch_slow_available_at
    assert result.failed_launch_slow_side == direction and result.failed_launch_slow_state == "aligned"
    event = json.loads(result.partial_fast_events)[0]
    assert event["available_at"] == result.exit_time.isoformat()
    assert event["previous_fast"]["management_segment_id"] == "7"
    assert event["current_fast"]["raw_segment_id"] == "19"
    assert event["slow"]["management_segment_id"] == "2"
    assert event["slow"]["raw_segment_id"] == "19"
    assert result.failed_launch_profit_threshold == .002 and result.partial_fast_flip_count == 1


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("seed", ["opposite", "unknown"])
def test_seed_opposite_or_unknown_is_not_itself_a_failed_launch(direction, seed):
    data = fixture(direction)
    data[2]["ma_side"] = [-direction, -direction, direction, -direction, -direction, -direction, -direction]
    if seed == "unknown":
        data[2].loc[0, "ma_side"] = np.nan
    result = run(data).iloc[0]
    assert result.partial_fast_initial_state == seed
    assert result.partial_fast_first_armed_at == E+2*FIVE
    assert result.exit_time == E+3*FIVE and result.outcome == "fast_failed_launch"


@pytest.mark.parametrize("kind", ["missing", "invalid", "zero", "segment"])
def test_unknown_or_segment_changed_fast_stream_cannot_bridge_old_aligned_seed(kind):
    raw, slow, fast, entries = fixture()
    fast["ma_side"] = [1, 1, -1, 1, -1, -1, -1]
    if kind == "missing": fast = fast.drop(index=1)
    elif kind == "invalid": fast.loc[1, "ma"] = np.nan
    elif kind == "zero": fast.loc[1, "ma_side"] = 0
    else: fast.loc[2:, "segment_id"] = 77
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.exit_time == E+4*FIVE and result.outcome == "fast_failed_launch"
    assert result.partial_fast_reset_count >= 1
    assert len(json.loads(result.partial_fast_events)) == 1


@pytest.mark.parametrize("kind", ["opposite", "missing", "invalid", "stale"])
def test_latest_native15_gate_rejects_failure_exit_without_aligned_colour(kind):
    raw, slow, fast, entries = fixture()
    fast["ma_side"] = [1, 1, 1, -1, -1, -1, -1]
    slow["ma_side"] = -1
    if kind == "missing": slow = slow.drop(index=1)
    elif kind == "invalid": slow.loc[1, "ma"] = np.nan
    elif kind == "stale": slow.loc[1, "open_time"] += FIVE
    data = raw, slow, fast, entries
    baseline = run(data, V16)
    candidate = run(data)
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    assert candidate.failed_launch_count.iloc[0] == 0
    event = json.loads(candidate.partial_fast_events.iloc[0])[0]
    assert event["action"] == ("slow_not_aligned" if kind == "opposite" else "slow_unknown")


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("priority", ["previous_stop", "gap_stop", "slow_exit", "deadline"])
def test_higher_priority_existing_exits_keep_every_old_field(direction, priority):
    data = fixture(direction)
    raw, slow, fast, entries = data
    stop = entries.initial_stop.iloc[0]
    policy = V17
    if priority == "previous_stop":
        raw.loc[raw.open_time.eq(E), "low" if direction == 1 else "high"] = stop
    elif priority == "gap_stop": quote(raw, 5, stop-direction)
    elif priority == "slow_exit":
        fast["ma_side"] = [direction, direction, direction, -direction, -direction, -direction, -direction]
        slow.loc[1, "ma_side"] = -direction
        quote(raw, 15, 100., invalid=True)
    else: policy = {**V17, "max_minutes": 5}
    baseline = run(data, {key: value for key, value in policy.items() if key != "fast_failed_launch_exit"})
    candidate = run(data, policy)
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    assert candidate.failed_launch_count.iloc[0] == 0
    assert candidate.failed_launch_status.iloc[0] == "prior_exit"
    assert candidate.partial_fast_events.iloc[0] == "[]"


@pytest.mark.parametrize("direction", [-1, 1])
def test_default72h_exit_precedes_simultaneous_failed_launch(direction):
    raw, _, _, entries = fixture(direction)
    raw = raw.iloc[:1].reindex(range(869)).ffill()
    raw["open_time"] = pd.date_range(E-3*FIVE, periods=len(raw), freq="5min")
    slow = management(15, [direction]*290)
    fast = management(5, [direction]*866)
    fast.loc[fast.open_time.ge(E+pd.Timedelta(hours=72)-FIVE), "ma_side"] = -direction
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.outcome == "time_exit" and result.exit_time == E+pd.Timedelta(hours=72)
    assert result.failed_launch_count == result.partial_fast_flip_count == 0


@pytest.mark.parametrize("direction", [-1, 1])
def test_current_hlc_stop_cannot_overrule_executed_failed_launch_open(direction):
    data = fixture(direction)
    data[0].loc[data[0].open_time.eq(E+FIVE), "low" if direction == 1 else "high"] = data[3].initial_stop.iloc[0]
    result = run(data).iloc[0]
    assert result.outcome == "fast_failed_launch" and result.exit_time == E+FIVE
    assert result.max_adverse_r == -.1  # Only the held prior bar, not the unheld exit-bar stop.


@pytest.mark.parametrize("kind", ["missing", "segment", "segment_nan", "segment_inf", "invalid_prior", "open_nan", "open_zero", "open_negative"])
def test_source_failure_is_unknown_before_any_discretionary_exit(kind):
    raw, slow, fast, entries = fixture()
    if kind == "missing": raw = raw.loc[~raw.open_time.eq(E+FIVE)]
    elif kind.startswith("segment"):
        raw["segment_id"] = raw.segment_id.astype(float)
        value = {"segment": 20., "segment_nan": np.nan, "segment_inf": np.inf}[kind]
        raw.loc[raw.open_time.ge(E+FIVE), "segment_id"] = value
    elif kind == "invalid_prior": raw.loc[raw.open_time.eq(E), "close"] = np.nan
    else:
        value = {"open_nan": np.nan, "open_zero": 0., "open_negative": -1.}[kind]
        raw.loc[raw.open_time.eq(E+FIVE), "open"] = value
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.outcome == "data_gap_censored" and not result.closed
    assert pd.isna(result.net_return) and result.failed_launch_count == 0
    assert result.failed_launch_status == "unknown_source" and result.partial_fast_events == "[]"


@pytest.mark.parametrize("direction", [-1, 1])
def test_full_early_fill_is_prefix_invariant_and_not_censored_by_future_prices(direction):
    data = fixture(direction)
    expected = run(data)
    raw, slow, fast, entries = data
    raw.loc[raw.open_time.ge(E+FIVE), ["high", "low", "close"]] = np.nan
    raw.loc[raw.open_time.gt(E+FIVE), "open"] = -1.
    raw.loc[raw.open_time.gt(E+FIVE), "segment_id"] = 88
    slow.loc[slow.open_time.ge(E), ["ma", "ma_side", "high", "low", "close"]] = np.nan
    fast.loc[fast.open_time.ge(E+FIVE), ["ma", "ma_side", "high", "low", "close"]] = np.nan
    pd.testing.assert_frame_equal(expected, run(data))
    # Even physical EOF immediately after the executable open is sufficient.
    pd.testing.assert_frame_equal(expected, run((raw.loc[raw.open_time.le(E+FIVE)], slow, fast, entries)))


@pytest.mark.parametrize("cutoff,closed", [(5, False), (6, True)])
def test_end_exclusive_does_not_invent_open_but_complete_fill_needs_no_future_hlc(cutoff, closed):
    result = run(fixture(), cutoff=E+pd.Timedelta(minutes=cutoff)).iloc[0]
    assert bool(result.closed) is closed
    assert result.failed_launch_count == int(closed)
    assert result.outcome == ("fast_failed_launch" if closed else "right_censored")


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("final", ["slow", "stop", "censor"])
def test_already_realised_half_never_uses_later_unprofitable_fast_edge(direction, final):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*2)
    quote(data[0], 20, 100-direction)
    if final == "stop":
        data[0].loc[data[0].open_time.eq(E+4*FIVE), "low" if direction == 1 else "high"] = data[3].initial_stop.iloc[0]
    elif final == "censor":
        data = (data[0].loc[~data[0].open_time.eq(E+5*FIVE)], *data[1:])
    baseline = run(data, V16)
    candidate = run(data)
    pd.testing.assert_frame_equal(baseline, candidate[baseline.columns])
    result = candidate.iloc[0]
    assert result.failed_launch_count == 0 and result.partial_fraction == .5
    assert [item["action"] for item in json.loads(result.partial_fast_events)] == ["executed", "already_partial"]


@pytest.mark.parametrize("enabled", [False, np.bool_(False)])
@pytest.mark.parametrize("scenario", ["failed", "partial", "censor", "empty"])
def test_false_is_complete_v16_schema_value_and_dtype_parity(enabled, scenario):
    data = fixture()
    if scenario == "partial": quote(data[0], 5, 102.)
    elif scenario == "censor": data[0].loc[data[0].open_time.eq(E), "close"] = np.nan
    elif scenario == "empty": data = (*data[:3], data[3].iloc[:0])
    baseline = run(data, V16)
    result = run(data, {**V16, "fast_failed_launch_exit": enabled})
    pd.testing.assert_frame_equal(baseline, result)
    assert not any(column.startswith("failed_launch_") for column in result)


@pytest.mark.parametrize("value", [None, 0, 1, 1., "true", "false", np.nan, [], {}])
def test_new_option_rejects_nonboolean_values(value):
    with pytest.raises(ValueError, match="fast_failed_launch_exit"):
        run(fixture(), {**V16, "fast_failed_launch_exit": value})


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("override", [{"fast_partial_fraction": None}, {"management_minutes": 5},
    {"management_minutes": 60}, {"exit_mode": "colour"}, {"confirmations": 2},
    {"decision_minutes": 15}, {"frozen_ma_exit": True}, {"launch_deadline_minutes": 60, "launch_progress_r": .5}])
def test_presence_requires_valid_v16_partial_configuration(enabled, override):
    policy = {**V16, "fast_failed_launch_exit": enabled, **override}
    if override == {"fast_partial_fraction": None}: policy.pop("fast_partial_fraction")
    with pytest.raises(ValueError): run(fixture(), policy)


@pytest.mark.parametrize("fault", ["entry_missing", "open_nan", "open_zero", "open_negative", "risk", "atr", "direction"])
def test_invalid_entry_never_initialises_fast_failure_or_fakes_known_return(fault):
    raw, slow, fast, entries = fixture()
    if fault == "entry_missing": raw = raw.loc[~raw.open_time.eq(E)]
    elif fault.startswith("open_"):
        raw.loc[raw.open_time.eq(E), "open"] = {"open_nan": np.nan, "open_zero": 0., "open_negative": -1.}[fault]
    elif fault == "risk": entries["initial_stop"] = 101.
    elif fault == "atr": entries["signal_atr"] = 0.
    else: entries["direction"] = 0.
    result = run((raw, slow, fast, entries)).iloc[0]
    assert result.outcome.startswith("entry_") and not result.closed and pd.isna(result.net_return)
    assert result.failed_launch_count == 0 and result.failed_launch_status == "entry_not_validated"
    assert result.partial_fast_events == "[]"


def test_numpy_true_empty_schema_and_inputs_not_mutated():
    data = fixture()
    before = tuple(frame.copy(deep=True) for frame in data)
    pd.testing.assert_frame_equal(run(data), run(data, {**V16, "fast_failed_launch_exit": np.bool_(True)}))
    for original, current in zip(before, data): pd.testing.assert_frame_equal(original, current)
    empty = run((*data[:3], data[3].iloc[:0]))
    assert empty.empty
    assert {name for name in empty if name.startswith("failed_launch_")} == {
        name for name in run(data) if name.startswith("failed_launch_")}


def test_30bp_stress_changes_cost_only_not_fixed20bp_failed_branch():
    data = fixture()
    quote(data[0], 5, 100.2)
    normal = run(data).iloc[0]
    stressed = run(data, {**V17, "cost_fraction": .003}).iloc[0]
    assert normal.partial_fast_events == stressed.partial_fast_events
    assert normal.exit_time == stressed.exit_time and normal.outcome == stressed.outcome == "fast_failed_launch"
    assert normal.net_return-stressed.net_return == pytest.approx(.001)


@pytest.mark.parametrize("direction", [-1, 1])
def test_exact20bp_full_failure_is_zero_not_float_rounding_winner(direction):
    data = fixture(direction)
    quote(data[0], 5, 100+direction*.2)
    result = run(data).iloc[0]
    assert result.outcome == "fast_failed_launch"
    assert result.gross_return == .002
    assert result.net_return == result.net_r == result.marked_net_return == 0.
    assert result.failed_launch_trigger_gross_return == .002
    assert json.loads(result.partial_fast_events)[0]["gross_return"] == .002
    stressed = run(data, {**V17, "cost_fraction": .003}).iloc[0]
    assert stressed.net_return == -.001
