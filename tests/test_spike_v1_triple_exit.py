"""Synthetic causal contracts for the eval-only native V1 triple exit replay."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from yoyo.evaluation import spike_native_v1_be05 as frozen_be05
from yoyo.evaluation.spike_v1_triple_exit import ALL_ARMS, AdmissionFilters, replay_native_v1_arms


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-05-04", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index).assign(atr=1.0)


def _event(bars: pd.DataFrame, **updates: object) -> pd.DataFrame:
    row: dict[str, object] = {"event_id": "native-1", "signal_bar_open": bars.index[0], "signal_close": 100.0,
                              "reference_signal_risk": 2.0, "initial_stop": 98.0, "volume_ratio": 21.0,
                              "base_asset": "USDC", "stock_linked": False}
    row.update(updates)
    return pd.DataFrame([row])


def test_default_baseline_matches_frozen_native_v1_path_reference() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 100, 97, 99)])
    event = _event(bars)
    result = replay_native_v1_arms(bars, event, tick=.01)
    actual = result.outcomes.iloc[0]
    expected = frozen_be05._native_exit_from_cache(
        bars, bars.index[0], 100.0, 2.0, 98.0, .01, use_be=False,
    )
    for field in ("entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "gross_return", "net_return", "censored"):
        assert actual[field] == pytest.approx(expected[field]) if isinstance(expected[field], float) else actual[field] == expected[field]


def test_same_bar_old_initial_stop_beats_new_be_and_lock_updates() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 104.5, 97.9, 100), (100, 100, 99, 100)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("triple",))
    trade = result.outcomes.iloc[0]
    assert (trade.exit_reason, trade.exit_price, trade.exit_protection_source) == ("protective_stop", pytest.approx(98), "native_initial")
    row = result.schedule.iloc[0]
    assert row.filled and row.observed_mfe_r_after == 0
    assert row.next_bar_stop_source == "native_initial"


def test_gap_past_prior_be_stop_fills_at_next_open_not_trigger_level() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101.1, 99, 100), (99, 100, 98.5, 99.5)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("be05",))
    trade = result.outcomes.iloc[0]
    assert (trade.exit_reason, trade.exit_price, trade.fill_source, trade.exit_protection_source) == (
        "protective_stop_gap", pytest.approx(99), "next_open_gap", "be05",
    )
    assert result.schedule.iloc[0].next_bar_stop == pytest.approx(100)


def test_lock_updates_from_cumulative_mfe_and_never_lowers() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 104.1, 99, 100), (103, 106.1, 102.5, 103), (103.5, 103.6, 103.0, 103.2)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("triple",))
    schedule, trade = result.schedule, result.outcomes.iloc[0]
    assert schedule.iloc[0].next_bar_stop == pytest.approx(102.05)
    assert schedule.iloc[1].next_bar_stop == pytest.approx(103.05)
    assert schedule.iloc[1].next_bar_stop_source == "lock50"
    assert (trade.exit_price, trade.exit_protection_source) == (pytest.approx(103.05), "lock50")


def test_boundary_censor_uses_last_close_and_supplied_end_cap() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 100.4, 99, 100), (100, 101.4, 99, 101)])
    end = bars.index[-1] + pd.Timedelta(minutes=30)
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, end=end)
    trade = result.outcomes.iloc[0]
    assert (trade.censored, trade.exit_price, trade.exit_time, trade.fill_source) == (True, pytest.approx(101), end, "censor_close")


def test_combined_arm_uses_lock_over_be_and_adverse_candidate() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 104.2, 98.6, 100), (103, 103.4, 102.0, 102.5)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("triple",))
    first = result.schedule.iloc[0]
    assert (first.observed_mfe_r_after, first.observed_mae_r_after, first.next_bar_stop, first.next_bar_stop_source) == (
        pytest.approx(2.1), pytest.approx(.7), pytest.approx(102.1), "lock50",
    )


def test_filter_bundle_and_explicit_eligibility_apply_before_positions() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    # This event passes the actual-risk and supplied-classification checks, but
    # parent-supplied causal eligibility still keeps it outside all positions.
    event = _event(bars, initial_stop=60.0, reference_signal_risk=40.0, event_id="random-control")
    result = replay_native_v1_arms(bars, event, tick=.01, filters=AdmissionFilters(enabled=True), eligibility=set())
    assert result.outcomes.empty and result.schedule.empty
    audit = result.admission.iloc[0]
    assert (audit.accepted, audit.reason) == (False, "ineligible_before_position")
    # A supplied random event cannot evade the same risk safety check.
    rejected = replay_native_v1_arms(bars, _event(bars, event_id="random-low-risk"), tick=.01, filters=AdmissionFilters(enabled=True))
    assert rejected.outcomes.empty
    assert rejected.admission.iloc[0].reason == "actual_entry_risk_not_above_threshold"


def test_unfiltered_baseline_retains_native_entry_gap_as_a_ledger_outcome() -> None:
    bars = _bars([(100, 100, 99, 100), (97, 100, 96, 98), (98, 100, 97, 99)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01)
    trade = result.outcomes.iloc[0]
    assert (trade.exit_reason, trade.exit_price, trade.net_return, trade.censored) == (
        "entry_gap_through_initial_stop", pytest.approx(97), pytest.approx(-.002), False,
    )
    assert math.isnan(trade.net_r)


def test_seven_arm_contract_keeps_raw_and_filtered_arms_separate_without_paths() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    event = _event(bars, initial_stop=60.0, reference_signal_risk=40.0)
    result = replay_native_v1_arms(bars, event, tick=.01, arms=ALL_ARMS, store_paths=False)
    assert set(result.outcomes.arm) == set(ALL_ARMS)
    assert result.schedule.empty
    accepted = result.admission.set_index("arm").accepted.to_dict()
    assert accepted == {arm: True for arm in ALL_ARMS}


def test_random_event_can_derive_its_own_native_risk_reference() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    random_event = pd.DataFrame([{"event_id": "random", "signal_bar_open": bars.index[0], "signal_close": 100.0,
                                  "recent_low": 95.0, "signal_atr": 1.0}])
    result = replay_native_v1_arms(bars, random_event, tick=.01)
    trade = result.outcomes.iloc[0]
    # V1 risk_reference takes min(95 - .2ATR, 100 - 2ATR), then floors to tick.
    assert (trade.initial_stop, trade.reference_signal_risk) == (pytest.approx(94.8), pytest.approx(5.2))
