"""Synthetic-only evidence for delayed entry clocks and maternal accounting."""
import numpy as np
import pandas as pd
import pytest

import yoyo.evaluation.hourly_impulse_aligned_execution as aligned
from yoyo.evaluation.hourly_impulse_k2_research import (
    KNOWN_NONENTRY, direct_requests, episode_ledger, single_pending_ledger,
)
from yoyo.layers.l3_backtest.hourly_impulse import _policy, simulate_events


START = pd.Timestamp("2024-01-01T01:00:00Z")
STEP = pd.Timedelta(minutes=5)
HORIZON = pd.Timedelta(hours=72)
FOLD = ("synthetic", "2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z")


def raw_bars(count=867):
    return pd.DataFrame({
        "open_time": pd.date_range(START - STEP, periods=count, freq="5min"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "segment_id": 9,
    })


class SyntheticStudy:
    def __init__(self, raw=None, sides=1, folds=None):
        self.raw = raw_bars() if raw is None else raw
        self.management = self.raw.drop(columns="open").assign(
            ma=100.0, ma_side=sides, ma_slope_atr=np.nan, segment_id=2,
        )
        self.config = {"execution": {"max_hours": 72, "cost_fraction": 0.002}}
        self.folds = [FOLD] if folds is None else folds
        self.feature_calls = []

    def featured(self, *args):
        self.feature_calls.append(args)
        assert args == (5, "SMA", 40)
        return self.management


def requests(delays=(0,), direction=1):
    return pd.DataFrame([{
        "event_id": "m{}".format(i), "fold": FOLD[0],
        "signal_time": START - pd.Timedelta(hours=1),
        "mother_decision_time": START, "mother_deadline": START + HORIZON,
        "decision_time": START + pd.Timedelta(minutes=delay),
        "wait_minutes": delay, "wait_hours": delay / 60.0,
        "direction": direction, "initial_stop": 90.0 if direction == 1 else 110.0,
        "signal_atr": 2.0, "preserved_k1_feature": 0.68,
    } for i, delay in enumerate(delays)])


def mothers(count=3):
    result = requests([0] * count).drop(columns=["mother_decision_time", "mother_deadline", "wait_minutes", "wait_hours"])
    result["decision_time"] = pd.date_range(START, periods=count, freq="h")
    result["signal_time"] = result["decision_time"] - pd.Timedelta(hours=1)
    return result


def outcome(event_id, *, entry=START, result="transition_colour_exit", net=0.01, closed=True):
    return {"event_id": event_id, "entry_time": entry, "exit_time": entry + STEP,
            "outcome": result, "net_return": net, "closed": closed}


def test_all_ninety_seven_five_minute_delays_exit_at_exact_same_maternal_deadline():
    entries = requests(range(0, 481, 5))
    study = SyntheticStudy()
    original = entries.copy(deep=True)
    result = aligned.simulate_realign_requests(study, entries, {})
    assert len(result) == 97
    assert result.event_id.tolist() == entries.event_id.tolist()
    assert result.entry_time.tolist() == entries.decision_time.tolist()
    assert result.exit_time.eq(START + HORIZON).all()
    assert result.outcome.eq("time_exit").all() and result.closed.all()
    assert np.allclose(result.hold_minutes, 4320 - entries.wait_minutes)
    assert result.initial_stop.eq(90).all() and result.signal_atr.eq(2).all()
    assert result.preserved_k1_feature.eq(0.68).all()
    assert np.allclose(result.net_return, -0.002)
    assert study.feature_calls == [(5, "SMA", 40)]
    pd.testing.assert_frame_equal(entries, original)


def test_integer_minutes_override_hour_value_without_nanosecond_rounding():
    # The fractional-hour path really loses a nanosecond in the pinned runtime.
    exact = pd.Timedelta(minutes=4310)
    assert pd.Timedelta(hours=exact / pd.Timedelta(hours=1)) != exact
    for delay in range(0, 481, 5):
        remaining = 4320 - delay
        policy = _policy({"max_minutes": remaining, "max_hours": float("nan")})
        assert pd.Timedelta(minutes=int(policy["max_minutes"])) + pd.Timedelta(minutes=delay) == HORIZON


@pytest.mark.parametrize("value", [0, 1, -5, 6, np.nan, np.inf, True, False, np.bool_(True), 5.0, "5", None])
def test_optional_minute_horizon_rejects_noninteger_nonfinite_or_off_grid_values(value):
    with pytest.raises(ValueError, match="max_minutes"):
        _policy({"max_minutes": value})


@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf, 71.83333333333333])
def test_default_legacy_hour_validation_is_unchanged(value):
    with pytest.raises(ValueError, match="max_hours"):
        _policy({"max_hours": value})


def test_explicit_integer_minutes_win_in_simulator_not_just_validation():
    study = SyntheticStudy(raw_bars(10))
    result = simulate_events(study.raw, study.management, requests([0]),
                             {"max_minutes": 15, "max_hours": -1}).iloc[0]
    assert result.exit_time == START + 3 * STEP
    assert result.outcome == "time_exit"


def test_hour_boundary_delay_matches_existing_exact_hour_replay():
    study = SyntheticStudy()
    entry = requests([120])
    actual = aligned.simulate_realign_requests(study, entry, {})
    expected = simulate_events(study.raw, study.management, entry, {
        "exit_mode": "transition_colour", "management_minutes": 5,
        "confirmations": 1, "max_hours": 70, "cost_fraction": 0.002,
    }, end_exclusive=pd.Timestamp(FOLD[2]))
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("direction", [1, -1])
def test_delayed_entry_preserves_k1_stop_and_stop_first_collision(direction):
    study = SyntheticStudy(raw_bars(30), sides=direction)
    entry = requests([65], direction)
    # A reversal is available at +70m, after this bar touches the immutable stop.
    idx = study.raw.index[study.raw.open_time.eq(START + pd.Timedelta(minutes=65))][0]
    study.raw.loc[idx, ["high", "low"]] = [120.0, 80.0]
    study.management.loc[idx, "ma_side"] = -direction
    result = aligned.simulate_realign_requests(study, entry, {}).iloc[0]
    assert result.outcome == "hard_stop"
    assert result.exit_time == START + pd.Timedelta(minutes=70)
    assert result.exit_price == (90.0 if direction == 1 else 110.0)
    assert result.initial_stop == entry.initial_stop.iloc[0]
    assert result.signal_atr == entry.signal_atr.iloc[0]
    assert result.net_r == pytest.approx(-1.02)


def test_delayed_entry_colour_edge_fills_next_grid_open_and_keeps_cost():
    study = SyntheticStudy(raw_bars(30))
    entry = requests([65])
    idx = study.management.index[study.management.open_time.eq(entry.decision_time.iloc[0])][0]
    study.management.loc[idx, "ma_side"] = -1
    study.raw.loc[idx + 1, "open"] = 102.0
    result = aligned.simulate_realign_requests(study, entry, {}).iloc[0]
    assert result.outcome == "transition_colour_exit"
    assert result.entry_time == START + pd.Timedelta(minutes=65)
    assert result.exit_time == START + pd.Timedelta(minutes=70)
    assert result.exit_price == 102
    assert result.net_return == pytest.approx(0.018)
    assert result.transition_initial_state == "aligned"


def test_fold_cutoff_is_forwarded_without_forced_time_exit():
    study = SyntheticStudy(raw_bars(30), folds=[(FOLD[0], FOLD[1], START + pd.Timedelta(minutes=70))])
    entry = requests([65])
    result = aligned.simulate_realign_requests(study, entry, {}).iloc[0]
    assert result.outcome == "right_censored"
    assert result.exit_time == START + pd.Timedelta(minutes=70)
    assert not result.closed and pd.isna(result.net_return)
    assert result.mother_deadline == START + HORIZON


def test_fold_end_equal_maternal_deadline_remains_end_exclusive():
    study = SyntheticStudy(folds=[(FOLD[0], FOLD[1], START + HORIZON)])
    result = aligned.simulate_realign_requests(study, requests([5]), {}).iloc[0]
    assert result.outcome == "right_censored"
    assert result.exit_time == START + HORIZON
    assert not result.closed


@pytest.mark.parametrize("delay", [-5, 1, 7, 481, 485])
def test_invalid_delay_rejected_before_any_feature_access(delay):
    study = SyntheticStudy(raw_bars(4))
    with pytest.raises(ValueError, match="delay"):
        aligned.simulate_realign_requests(study, requests([delay]), {})
    assert study.feature_calls == []


@pytest.mark.parametrize("column", ["decision_time", "mother_decision_time", "mother_deadline"])
def test_missing_clocks_fail_closed(column):
    entry = requests([0])
    entry[column] = pd.NaT
    with pytest.raises(ValueError, match="finite"):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), entry, {})


def test_nonhourly_mother_and_one_nanosecond_drift_rejected():
    entry = requests([5])
    entry["mother_decision_time"] += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="hourly"):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), entry, {})
    entry = requests([5])
    entry["decision_time"] += pd.Timedelta(nanoseconds=1)
    with pytest.raises(ValueError, match="5m grid"):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), entry, {})


def test_absolute_deadline_cannot_restart_after_wait():
    entry = requests([65])
    entry["mother_deadline"] = entry["decision_time"] + HORIZON
    with pytest.raises(ValueError, match="absolute 72h horizon"):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), entry, {})


@pytest.mark.parametrize("field,value", [("wait_hours", 1), ("wait_minutes", 60), ("wait_minutes", np.nan)])
def test_wait_diagnostics_must_agree_with_authoritative_clocks(field, value):
    entry = requests([65])
    entry[field] = value
    with pytest.raises(ValueError, match="contradicts"):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), entry, {})


@pytest.mark.parametrize("changes", [
    {"exit_mode": "colour"}, {"management_minutes": 15}, {"confirmations": 2},
    {"confirmations": True}, {"cost_fraction": 0.001}, {"cost_fraction": np.nan},
    {"max_hours": 80}, {"max_minutes": 4320}, {"ma_kind": "EMA"}, {"ma_length": 20},
])
def test_frozen_exit_cost_and_maternal_clock_cannot_be_changed(changes):
    with pytest.raises(ValueError):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), requests(), changes)


def test_foreign_fold_duplicate_identity_and_outside_fold_fail_closed():
    study = SyntheticStudy(raw_bars(4))
    entry = requests()
    with pytest.raises(ValueError, match="known study fold"):
        aligned.simulate_realign_requests(study, entry.assign(fold="foreign"), {})
    with pytest.raises(ValueError, match="unique"):
        aligned.simulate_realign_requests(study, pd.concat([entry, entry]), {})
    study.folds = [(FOLD[0], START + STEP, FOLD[2])]
    with pytest.raises(ValueError, match="inside"):
        aligned.simulate_realign_requests(study, entry, {})


def test_mixed_fold_and_delay_groups_preserve_input_order(monkeypatch):
    study = SyntheticStudy(raw_bars(4), folds=[FOLD, ("second", "2024-02-01", "2024-02-10")])
    entry = requests([65, 0, 5])
    entry.loc[1, "fold"] = "second"
    for name in ("decision_time", "mother_decision_time", "mother_deadline"):
        entry.loc[1, name] += pd.Timedelta(days=31)
    seen = []
    def fake(raw, mg, part, policy, *, end_exclusive):
        seen.append((part.event_id.iloc[0], policy["max_minutes"], end_exclusive))
        return part.assign(outcome="synthetic")
    monkeypatch.setattr(aligned, "simulate_events", fake)
    result = aligned.simulate_realign_requests(study, entry, {})
    assert result.event_id.tolist() == entry.event_id.tolist()
    assert seen == [("m2", 4315, pd.Timestamp(FOLD[2])),
                    ("m0", 4255, pd.Timestamp(FOLD[2])),
                    ("m1", 4320, pd.Timestamp("2024-02-10", tz="UTC"))]


def test_missing_execution_result_cannot_silently_drop_request(monkeypatch):
    monkeypatch.setattr(aligned, "simulate_events", lambda raw, mg, part, policy, **kw: part.iloc[:0])
    with pytest.raises(ValueError, match="exactly one execution"):
        aligned.simulate_realign_requests(SyntheticStudy(raw_bars(4)), requests(), {})


@pytest.mark.parametrize("frame", [pd.DataFrame(), requests().iloc[:0]])
def test_empty_requests_have_complete_outcome_schema_without_feature_access(frame):
    study = SyntheticStudy(raw_bars(4))
    result = aligned.simulate_realign_requests(study, frame, {})
    assert result.empty and study.feature_calls == []
    assert set(aligned.REQUIRED_ENTRY_COLUMNS).issubset(result.columns)
    assert {"closed", "outcome", "net_return", "risk_atr", "transition_first_armed_at"}.issubset(result.columns)
    assert str(result.exit_time.dtype) == "datetime64[ns, UTC]"


def test_new_expiry_zero_is_local_and_exact_status_is_restored():
    m = mothers()
    _, statuses = direct_requests(m)
    statuses["status"] = ["request_emitted", "expired_no_alignment", "data_gap"]
    statuses.loc[1, "terminal_time"] += pd.Timedelta(hours=8)
    original = statuses.copy(deep=True)
    known = set(KNOWN_NONENTRY)
    trades = pd.DataFrame([outcome("m0")])
    result = aligned.realign_episode_ledger(m, statuses, trades)
    assert result.episode_net_return.iloc[:2].tolist() == [0.01, 0.0]
    assert result.observed.tolist() == [True, True, False]
    assert result.executed.tolist() == [True, False, False]
    assert result.status.iloc[1] == result.episode_status.iloc[1] == "expired_no_alignment"
    assert result.occupied_until.iloc[1] == statuses.terminal_time.iloc[1]
    assert result.occupied_until.iloc[2] == statuses.mother_deadline.iloc[2]
    assert KNOWN_NONENTRY == known and "expired_no_alignment" not in known
    assert pd.isna(episode_ledger(m, statuses, trades).episode_net_return.iloc[1])
    pd.testing.assert_frame_equal(statuses, original)


@pytest.mark.parametrize("bad_outcome", ["entry_missing", "entry_invalid", "data_gap_censored", "right_censored"])
def test_invalid_risk_is_observed_zero_but_invalid_price_or_missing_path_stays_unknown(bad_outcome):
    m = mothers(2)
    _, statuses = direct_requests(m)
    trades = pd.DataFrame([
        outcome("m0", result="entry_invalid_risk", net=np.nan, closed=False),
        outcome("m1", result=bad_outcome, net=np.nan, closed=False),
    ])
    result = aligned.realign_episode_ledger(m, statuses, trades)
    assert result.episode_net_return.iloc[0] == 0
    assert pd.isna(result.episode_net_return.iloc[1])
    assert result.observed.tolist() == [True, False]
    assert result.occupied_until.iloc[1] == result.mother_deadline.iloc[1]
    assert not result.executed.iloc[0]


def test_all_emitted_requests_require_exact_execution_identity():
    m = mothers(1)
    _, statuses = direct_requests(m)
    for trades in (pd.DataFrame(), pd.DataFrame([outcome("foreign")]), pd.DataFrame([outcome("m0"), outcome("m0")])):
        with pytest.raises(ValueError):
            aligned.realign_episode_ledger(m, statuses, trades)
    with pytest.raises(ValueError):
        aligned.realign_episode_ledger(m, pd.concat([statuses, statuses]), pd.DataFrame())
    statuses["status"] = "expired_no_alignment"
    with pytest.raises(ValueError, match="without a waiting request"):
        aligned.realign_episode_ledger(m, statuses, pd.DataFrame([outcome("m0")]))


def test_pending_occupancy_starts_at_mother_not_delayed_entry():
    m = mothers(4)
    _, statuses = direct_requests(m)
    statuses["status"] = "expired_no_alignment"
    statuses.loc[0, "terminal_time"] = m.decision_time.iloc[2]
    episodes = aligned.realign_episode_ledger(m, statuses, pd.DataFrame())
    serial = single_pending_ledger(episodes)
    assert serial.portfolio_selected.tolist() == [True, False, True, True]


def test_unknown_pending_occupies_full_maternal_horizon():
    m = mothers(3)
    _, statuses = direct_requests(m)
    statuses["status"] = ["data_gap", "expired_no_alignment", "expired_no_alignment"]
    episodes = aligned.realign_episode_ledger(m, statuses, pd.DataFrame())
    assert single_pending_ledger(episodes).portfolio_selected.tolist() == [True, False, False]


def test_empty_episode_inputs_have_usable_serial_schema():
    result = aligned.realign_episode_ledger(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert result.empty
    assert {"event_id", "fold", "observed", "episode_net_return", "occupied_until"}.issubset(result.columns)
    assert single_pending_ledger(result).empty
