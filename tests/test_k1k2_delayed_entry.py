"""Independent finite synthetic tests for the V39 delayed-entry adapter.

Only constructed OHLC and causal SMA40(HL2) features are used; no price files,
experiment results, cached returns, or original adapter helpers are read.
Finite pytest parameterization exercises clock, reflection and identity
invariants without installing a property-testing dependency. The underlying
engine is exercised rather than mocked for entry, stop, exit and deadline tests.

Sources for test APIs (pandas is the repository's pinned 2.3.3):
https://docs.pytest.org/en/stable/how-to/parametrize.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.date_range.html
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.testing.assert_frame_equal.html
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.hourly_impulse import add_features
from yoyo.evaluation.k1k2_delayed_entry import entry_ledger, replay_delayed


E = pd.Timestamp("2023-01-03T12:00:00Z")
STEP = pd.Timedelta(minutes=5)
POLICY = {"management_minutes": 5, "exit_mode": "transition_colour",
          "confirmations": 1, "max_hours": 72, "cost_fraction": .002}


def fixture(first_alignment=5, *, direction=1, flip_at=None, scale=1.):
    """Create the requested observed colour at each boundary using past data.

    A bar's midpoint is the preceding 39 midpoints' mean plus/minus .25, so
    the sign against its own completed SMA40 is prescribed without copying the
    feature implementation. Observation zero is the seed ending at E. Later
    increasing/decreasing midpoints prevent accidental equality/colour exits.
    """
    times = pd.date_range(E - 40 * STEP, E + pd.Timedelta(hours=73), freq="5min")
    mids = [100.] * len(times)
    for i in range(39, len(times)):
        observed_minutes = (i - 39) * 5
        aligned = first_alignment is not None and observed_minutes >= first_alignment
        if flip_at is not None and observed_minutes >= flip_at:
            aligned = False
        mids[i] = float(np.mean(mids[i - 39:i])) + (.25 if aligned else -.25)
    mid = np.asarray(mids)
    raw = pd.DataFrame({"open_time": times, "open": mid, "high": mid + .2,
                        "low": mid - .2, "close": mid, "volume": 1., "segment_id": 0})
    if direction == -1:
        original = raw.copy()
        raw["open"] = 200 - original.open
        raw["close"] = 200 - original.close
        raw["high"] = 200 - original.low
        raw["low"] = 200 - original.high
    raw[["open", "high", "low", "close"]] *= scale
    raw.attrs["bar_minutes"] = 5
    native = add_features(raw, "SMA", 40)
    native.attrs.update(bar_minutes=5, ma_kind="SMA", ma_length=40)
    requests = pd.DataFrame([{
        "event_id": "synthetic-case", "decision_time": E,
        "signal_time": E - pd.Timedelta(hours=1), "fold": "2023H1",
        "direction": direction, "initial_stop": (95. if direction == 1 else 105.) * scale,
        "signal_atr": 2. * scale, "gap_bars": 3, "body_ratio": .7,
        "ma": 100. * scale, "owner_identity": "unchanged",
    }])
    return raw, native, requests


def replay(inputs, **kwargs):
    before = [x.copy(deep=True) for x in inputs]
    result = replay_delayed(*inputs, dict(POLICY), **kwargs)
    assert len(result) == 3
    for current, original in zip(inputs, before):
        assert_frame_equal(current, original)
        assert current.attrs == original.attrs
    rows, audit, trace = result
    assert rows.event_id.tolist() == inputs[2].event_id.tolist()
    assert audit.event_id.tolist() == inputs[2].event_id.tolist()
    return rows, audit, trace


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("delay", [0, 5, 55])
def test_first_completed_alignment_enters_without_an_extra_bar(direction, delay):
    inputs = fixture(delay, direction=direction)
    rows, audit, trace = replay(inputs)
    row = rows.iloc[0]
    at = E + pd.Timedelta(minutes=delay)
    assert row.entry_policy_status == "eligible"
    assert row.entry_time == at and row.decision_time == E
    assert row.original_decision_time == E
    assert row.entry_deadline == E + pd.Timedelta(hours=1)
    assert row.absolute_deadline == E + pd.Timedelta(hours=72)
    assert row.remaining_minutes == 4320 - delay
    assert audit.iloc[0].eligible_at == at
    assert trace.observed_at.max() == at
    assert row.entry_price == inputs[0].loc[inputs[0].open_time.eq(at), "open"].iloc[0]
    assert row.transition_initial_state == "aligned"


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("delay", [0, 5, 55])
def test_delayed_entry_keeps_the_original_72_hour_absolute_deadline(direction, delay):
    rows, _, _ = replay(fixture(delay, direction=direction))
    row = rows.iloc[0]
    assert row.closed and row.outcome == "time_exit"
    assert row.exit_time == E + pd.Timedelta(hours=72)
    assert row.hold_minutes == 4320 - delay
    assert row.request_known
    assert row.request_net == pytest.approx(row.net_return)
    assert row.request_gross == pytest.approx(row.gross_return)
    assert row.request_gross - row.request_net == pytest.approx(.002)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("first_alignment", [60, None])
def test_first_alignment_at_expiry_is_not_an_entry(direction, first_alignment):
    rows, audit, trace = replay(fixture(first_alignment, direction=direction))
    row = rows.iloc[0]
    assert row.entry_policy_status == "expired_no_entry"
    assert row.entry_policy_known_at == E + pd.Timedelta(hours=1)
    assert row.outcome == "no_fill_expired" and not row.closed
    assert row.request_known and row.request_net == 0 and row.request_gross == 0
    assert pd.isna(row.entry_time) and pd.isna(row.exit_time)
    assert pd.isna(row.net_return) and pd.isna(row.gross_return)
    assert audit.iloc[0].status == "pending_at_cutoff"
    assert trace.observed_at.max() == E + pd.Timedelta(minutes=55)


def test_expiry_needs_no_price_or_colour_from_the_excluded_60_minute_boundary():
    raw, native, requests = fixture(60)
    raw = raw.loc[raw.open_time.le(E + pd.Timedelta(minutes=55))].copy()
    native = native.loc[native.open_time.lt(E + pd.Timedelta(minutes=55))].copy()
    rows, _, trace = replay((raw, native, requests))
    assert rows.iloc[0].entry_policy_status == "expired_no_entry"
    assert rows.iloc[0].request_known and rows.iloc[0].request_net == 0
    assert trace.observed_at.max() == E + pd.Timedelta(minutes=55)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("missing_later_observation", [False, True])
def test_wait_stop_wins_over_same_bar_alignment_and_missing_confirmation_open(direction, missing_later_observation):
    raw, native, requests = fixture(5, direction=direction)
    at = raw.open_time.eq(E)
    stop = requests.initial_stop.iloc[0]
    middle = (raw.loc[at, "high"].iloc[0] + raw.loc[at, "low"].iloc[0]) / 2
    if direction == 1:
        raw.loc[at, "low"] = stop
        raw.loc[at, "high"] = 2 * middle - stop
    else:
        raw.loc[at, "high"] = stop
        raw.loc[at, "low"] = 2 * middle - stop
    native = add_features(raw, "SMA", 40)
    native.attrs.update(bar_minutes=5, ma_kind="SMA", ma_length=40)
    assert native.loc[native.open_time.eq(E), "ma_side"].iloc[0] == direction
    if missing_later_observation:
        raw.loc[raw.open_time.eq(E + STEP), ["open", "high", "low", "close"]] = np.nan
        native = native.loc[native.open_time.ne(E)].copy()
    rows, audit, trace = replay((raw, native, requests))
    row = rows.iloc[0]
    assert row.entry_policy_status == "invalidated_wait_bar"
    assert row.outcome == "no_fill_stop" and row.request_known
    assert row.request_net == row.request_gross == 0
    assert pd.isna(row.net_return) and pd.isna(row.entry_time)
    assert audit.iloc[0].status_known_at == E + STEP
    assert trace.iloc[-1].wait_bar_stop_touched


@pytest.mark.parametrize("direction", [-1, 1])
def test_confirmation_open_at_stop_is_known_no_fill_not_a_reanchored_stop(direction):
    raw, native, requests = fixture(5, direction=direction)
    stop = requests.initial_stop.iloc[0]
    raw.loc[raw.open_time.eq(E + STEP), "open"] = stop
    rows, _, _ = replay((raw, native, requests))
    row = rows.iloc[0]
    assert row.entry_policy_status == "invalidated_open"
    assert row.outcome == "no_fill_stop" and row.request_known
    assert row.request_net == 0 and pd.isna(row.entry_time)
    assert row.initial_stop == stop


@pytest.mark.parametrize("fault", ["seed_colour", "wait_colour", "wait_raw", "current_open"])
def test_earlier_unknown_never_turns_into_cash_or_recovers_on_later_confirmation(fault):
    raw, native, requests = fixture(10)
    if fault == "seed_colour":
        native.loc[native.open_time.eq(E - STEP), "ma"] = np.nan
    elif fault == "wait_colour":
        native.loc[native.open_time.eq(E), "ma"] = np.nan
    elif fault == "wait_raw":
        raw.loc[raw.open_time.eq(E), "close"] = np.nan
    else:
        raw.loc[raw.open_time.eq(E + STEP), "open"] = np.nan
    rows, audit, trace = replay((raw, native, requests))
    row = rows.iloc[0]
    assert row.entry_policy_status.startswith("unknown_")
    assert not row.request_known
    assert pd.isna(row.request_net) and pd.isna(row.request_gross)
    assert pd.isna(row.entry_time)
    assert row.request_uncertainty_at == audit.iloc[0].status_known_at
    assert trace.observed_at.max() <= E + STEP


@pytest.mark.parametrize("delay", [0, 5, 55])
def test_unseen_current_hlc_cannot_change_entry_decision(delay):
    baseline_inputs = fixture(delay)
    baseline_rows, baseline_audit, baseline_trace = replay(baseline_inputs)
    raw, native, requests = fixture(delay)
    at = E + pd.Timedelta(minutes=delay)
    raw.loc[raw.open_time.ge(at), ["high", "low", "close"]] = np.nan
    native.loc[native.open_time.ge(at), ["high", "low", "close", "ma", "ma_side", "hl2"]] = np.nan
    rows, audit, trace = replay((raw, native, requests))
    assert_frame_equal(audit, baseline_audit)
    assert_frame_equal(trace, baseline_trace)
    assert rows.iloc[0].entry_time == baseline_rows.iloc[0].entry_time == at
    assert rows.iloc[0].entry_price == baseline_rows.iloc[0].entry_price
    assert rows.iloc[0].entry_policy_status == "eligible"
    # Unknown post-entry HLC can censor labels without erasing a causal fill.
    assert not rows.iloc[0].closed and not rows.iloc[0].request_known
    assert pd.isna(rows.iloc[0].request_net)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("scale", [.01, 1., 100.])
def test_original_request_identity_and_risk_survive_replay(direction, scale):
    raw, native, requests = fixture(5, direction=direction, flip_at=20, scale=scale)
    rows, _, _ = replay((raw, native, requests))
    row = rows.iloc[0]
    for column in requests:
        assert row[column] == requests.iloc[0][column]
    assert row.entry_time == E + STEP and row.exit_time == E + 4 * STEP
    assert row.closed and row.outcome == "transition_colour_exit"
    risk = direction * (row.entry_price - requests.initial_stop.iloc[0])
    assert row.risk_pct == pytest.approx(risk / row.entry_price)
    assert row.risk_atr == pytest.approx(risk / requests.signal_atr.iloc[0])
    assert row.request_gross == pytest.approx(direction * (row.exit_price / row.entry_price - 1))
    assert row.request_net == pytest.approx(row.request_gross - .002)


def test_end_exclusive_is_censoring_not_a_strategy_exit_or_new_deadline():
    cutoff = E + pd.Timedelta(hours=2)
    rows, _, _ = replay(fixture(55), end_exclusive=cutoff)
    row = rows.iloc[0]
    assert row.absolute_deadline == E + pd.Timedelta(hours=72)
    assert row.remaining_minutes == 4265
    assert not row.closed and row.outcome == "right_censored"
    assert not row.request_known and pd.isna(row.request_net)
    # The last held bar closes at the cutoff; this is a mark, not an exit fill.
    assert row.exit_time == cutoff
    raw, native, requests = fixture(55)
    raw.loc[raw.open_time.ge(cutoff), ["open", "high", "low", "close"]] = np.nan
    native.loc[native.open_time.ge(cutoff), ["ma", "ma_side"]] = np.nan
    changed, _, _ = replay((raw, native, requests), end_exclusive=cutoff)
    assert_frame_equal(rows, changed)


@pytest.mark.parametrize("key,value", [
    ("management_minutes", 15), ("management_minutes", True),
    ("exit_mode", "colour"), ("confirmations", 2), ("confirmations", True),
    ("max_hours", 71), ("max_hours", np.nan), ("cost_fraction", 0),
    ("decision_minutes", 5), ("max_minutes", 4320),
    ("frozen_ma_exit", False), ("entry_expiry_minutes", 60),
])
def test_no_exit_cost_clock_or_extra_policy_drift(key, value):
    policy = dict(POLICY)
    policy[key] = value
    with pytest.raises(ValueError):
        replay_delayed(*fixture(5), policy)


@pytest.mark.parametrize("missing", list(POLICY))
def test_all_frozen_policy_keys_are_required(missing):
    policy = dict(POLICY)
    del policy[missing]
    with pytest.raises(ValueError):
        replay_delayed(*fixture(5), policy)


def test_missing_or_duplicate_request_identity_fails_closed():
    raw, native, requests = fixture(5)
    for altered in [pd.concat([requests, requests], ignore_index=True),
                    requests.assign(event_id=None)]:
        with pytest.raises(ValueError):
            replay_delayed(raw, native, altered, dict(POLICY))


def test_empty_request_table_has_empty_outputs():
    raw, native, requests = fixture(5)
    rows, audit, trace = replay((raw, native, requests.iloc[:0].copy()))
    assert rows.empty and audit.empty and trace.empty
    assert {"request_known", "request_net", "entry_policy_status", "entry_time"}.issubset(rows)


@pytest.mark.parametrize("field", ["net_return", "request_known", "winner", "audit_cutoff",
                                    "transition_armed_at", "portfolio_selected"])
@pytest.mark.parametrize("where", ["column", "attrs"])
def test_outcome_or_prior_acceptance_metadata_cannot_enter_requests(field, where):
    raw, native, requests = fixture(5)
    if where == "column":
        requests[field] = 1.
    else:
        requests.attrs[field] = 1.
    with pytest.raises(ValueError):
        replay_delayed(raw, native, requests, dict(POLICY))


@pytest.mark.parametrize("bad_clock", [E + STEP, E.tz_localize(None), pd.NaT, 1672747200])
def test_original_hourly_clock_cannot_be_shifted_ambiguous_or_missing(bad_clock):
    raw, native, requests = fixture(5)
    requests["decision_time"] = [bad_clock]
    with pytest.raises(ValueError):
        replay_delayed(raw, native, requests, dict(POLICY))


@pytest.mark.parametrize("cutoff", [E, E + 10 * STEP, E + 11 * STEP])
def test_source_cutoff_cannot_shrink_the_frozen_entry_window(cutoff):
    with pytest.raises(ValueError):
        replay_delayed(*fixture(0), dict(POLICY), end_exclusive=cutoff)


def ledger_row(event_id, entry_minute, exit_minute, *, known=True, closed=True,
               uncertainty_minute=None, outcome="transition_colour_exit"):
    """A hand-constructed scheduling record, not a real price/outcome sample."""
    return {"event_id": event_id, "decision_time": E,
            "entry_time": pd.NaT if entry_minute is None else E + entry_minute * pd.Timedelta(minutes=1),
            "exit_time": pd.NaT if exit_minute is None else E + exit_minute * pd.Timedelta(minutes=1),
            "closed": closed, "outcome": outcome, "request_known": known,
            "request_uncertainty_at": (pd.NaT if uncertainty_minute is None else
                                       E + uncertainty_minute * pd.Timedelta(minutes=1)),
            "entry_policy_known_at": E,
            "request_net": .001 if known else np.nan}


def test_ledger_sorts_actual_entry_not_request_order_or_id_and_preserves_all_rows():
    inputs = pd.DataFrame([ledger_row("a-later", 5, 20), ledger_row("z-earlier", 0, 10)],
                          index=[17, 3])
    inputs.attrs["synthetic_identity"] = "preserve"
    before = inputs.copy(deep=True)
    out = entry_ledger(inputs)
    assert_frame_equal(inputs, before)
    assert_frame_equal(out[inputs.columns], inputs)
    assert out.attrs == inputs.attrs
    by_id = out.set_index("event_id")
    assert not by_id.loc["a-later", "portfolio_selected"]
    assert by_id.loc["a-later", "portfolio_skip_reason"] == "position_open"
    assert by_id.loc["a-later", "portfolio_blocker_id"] == "z-earlier"
    assert by_id.loc["z-earlier", "portfolio_selected"]
    permuted = entry_ledger(inputs.iloc[::-1]).sort_values("event_id").reset_index(drop=True)
    assert_frame_equal(out.sort_values("event_id").reset_index(drop=True), permuted)


def test_known_exact_exit_and_next_entry_boundary_may_coincide():
    out = entry_ledger(pd.DataFrame([ledger_row("a", 0, 20), ledger_row("b", 20, 25)]))
    assert out.portfolio_selected.tolist() == [True, True]
    assert not out.portfolio_uncertainty_block.any()


@pytest.mark.parametrize("outcome", ["no_fill_stop", "no_fill_expired"])
def test_known_no_fill_has_no_occupancy_and_no_fabricated_entry(outcome):
    out = entry_ledger(pd.DataFrame([
        ledger_row("no-fill", None, None, closed=False, outcome=outcome),
        ledger_row("later", 5, 20),
    ]))
    assert out.portfolio_selected.tolist() == [False, True]
    assert out.portfolio_skip_reason.iloc[0] == "known_no_fill"
    assert pd.isna(out.entry_time.iloc[0])
    assert not out.portfolio_uncertainty_block.any()


def test_unknown_first_entry_blocks_later_without_inventing_a_position():
    out = entry_ledger(pd.DataFrame([
        ledger_row("unknown", None, None, known=False, closed=False,
                   uncertainty_minute=5, outcome="entry_policy_unknown"),
        ledger_row("later", 10, 20),
    ]))
    assert out.portfolio_selected.tolist() == [False, False]
    assert out.portfolio_uncertainty_block.tolist() == [True, False]
    assert out.portfolio_skip_reason.tolist() == ["entry_unknown", "uncertain_entry"]
    assert out.portfolio_blocker_id.tolist() == ["unknown", "unknown"]
    assert pd.isna(out.entry_time.iloc[0]) and pd.isna(out.request_net.iloc[0])


def test_unknown_inside_an_existing_position_still_blocks_after_that_position_exits():
    out = entry_ledger(pd.DataFrame([
        ledger_row("position", 0, 20),
        ledger_row("unknown", None, None, known=False, closed=False,
                   uncertainty_minute=5, outcome="entry_policy_unknown"),
        ledger_row("later", 25, 30),
    ]))
    assert out.portfolio_selected.tolist() == [True, False, False]
    assert out.portfolio_uncertainty_block.tolist() == [False, True, False]
    assert out.portfolio_skip_reason.iloc[2] == "uncertain_entry"
    assert out.portfolio_blocker_id.iloc[2] == "unknown"
    # An unknown candidate was not automatically cancelled while occupied.
    assert pd.isna(out.entry_time.iloc[1])


def test_entered_censored_position_occupies_beyond_its_last_available_mark():
    out = entry_ledger(pd.DataFrame([
        ledger_row("censored", 0, 10, known=False, closed=False,
                   uncertainty_minute=10, outcome="right_censored"),
        ledger_row("later", 15, 20),
    ]))
    assert out.portfolio_selected.tolist() == [True, False]
    assert out.portfolio_skip_reason.iloc[1] == "uncertain_position"
    assert out.portfolio_blocker_id.iloc[1] == "censored"


def test_actual_adapter_nofill_and_unknown_schedule_without_mask_reuse():
    known, _, _ = replay(fixture(None))
    raw, native, requests = fixture(10)
    requests["event_id"] = "unknown"
    native.loc[native.open_time.eq(E - STEP), "ma"] = np.nan
    unknown, _, _ = replay((raw, native, requests))
    rows = pd.concat([known, unknown], ignore_index=True)
    out = entry_ledger(rows)
    assert out.portfolio_skip_reason.tolist() == ["known_no_fill", "entry_unknown"]
    assert not out.portfolio_selected.any()
    with pytest.raises(ValueError):
        entry_ledger(out)
