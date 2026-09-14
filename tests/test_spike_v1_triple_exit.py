"""Synthetic causal contracts for the eval-only native V1 triple exit replay."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from yoyo.evaluation import spike_native_v1_be05 as frozen_be05
from yoyo.evaluation.spike_v1_triple_exit import ALL_ARMS, ExclusionFilters, replay_native_v1_arms


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-05-04", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index).assign(atr=1.0)


def _event(bars: pd.DataFrame, **updates: object) -> pd.DataFrame:
    row: dict[str, object] = {"event_id": "native-1", "signal_bar_open": bars.index[0], "signal_close": 100.0,
                              "reference_signal_risk": 2.0, "initial_stop": 98.0, "volume_ratio": 5.0,
                              "base_asset": "BTC", "stock_linked": False}
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
    bars = _bars([(100, 100, 99, 100), (100, 101.1, 99, 100), (99, 1000, 98.5, 99.5)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("be05",))
    trade = result.outcomes.iloc[0]
    assert (trade.exit_reason, trade.exit_price, trade.fill_source, trade.exit_protection_source) == (
        "protective_stop_gap", pytest.approx(99), "next_open_gap", "be05",
    )
    assert result.schedule.iloc[0].next_bar_stop == pytest.approx(100)
    # The gap exits at the open; its later 1000 high is not observable MFE.
    assert trade.mfe_r == pytest.approx(.55)


def test_lock_updates_from_cumulative_mfe_and_never_lowers() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 104.1, 99, 100), (103, 106.1, 102.5, 103), (103.5, 103.6, 103.0, 103.2)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("triple",))
    schedule, trade = result.schedule, result.outcomes.iloc[0]
    assert schedule.iloc[0].next_bar_stop == pytest.approx(102.05)
    assert schedule.iloc[1].next_bar_stop == pytest.approx(103.05)
    assert schedule.iloc[1].next_bar_stop_source == "lock50"
    assert (trade.exit_price, trade.exit_protection_source) == (pytest.approx(103.05), "lock50")


def test_boundary_censor_truncates_unfinished_and_future_bars_before_replay() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 100.4, 99, 100), (100, 101.4, 99, 101), (101, 200, 50, 75)])
    end = bars.index[2]  # Only bars whose closes are <= this boundary are visible.
    original = replay_native_v1_arms(bars, _event(bars), tick=.01, end=end).outcomes.iloc[0]
    changed_future = bars.copy()
    changed_future.iloc[2:] = [(1, 1000, 1, 1, 1), (1, 1000, 1, 1, 1)]
    changed = replay_native_v1_arms(changed_future, _event(changed_future), tick=.01, end=end).outcomes.iloc[0]
    assert (original.censored, original.exit_price, original.exit_time, original.fill_source) == (True, pytest.approx(100), end, "censor_close")
    assert original[["exit_price", "exit_time", "mfe_r", "mae_r", "net_r"]].to_dict() == changed[["exit_price", "exit_time", "mfe_r", "mae_r", "net_r"]].to_dict()


def test_combined_arm_uses_lock_over_be_and_adverse_candidate() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 104.2, 98.6, 100), (103, 103.4, 102.0, 102.5)])
    result = replay_native_v1_arms(bars, _event(bars), tick=.01, arms=("triple",))
    first = result.schedule.iloc[0]
    assert (first.observed_mfe_r_after, first.observed_mae_r_after, first.next_bar_stop, first.next_bar_stop_source) == (
        pytest.approx(2.1), pytest.approx(.7), pytest.approx(102.1), "lock50",
    )


def test_exclusion_bundle_and_explicit_eligibility_apply_before_positions() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    # This event passes the actual-risk and supplied-classification checks, but
    # parent-supplied causal eligibility still keeps it outside all positions.
    event = _event(bars, initial_stop=60.0, reference_signal_risk=40.0, event_id="random-control")
    result = replay_native_v1_arms(bars, event, tick=.01, filters=ExclusionFilters(enabled=True), eligibility=set())
    assert result.outcomes.empty and result.schedule.empty
    audit = result.admission.iloc[0]
    assert (audit.accepted, audit.reason) == (False, "ineligible_before_position")
    # A supplied random event cannot evade the same risk exclusion check.
    rejected = replay_native_v1_arms(bars, _event(bars, event_id="random-high-risk", initial_stop=69.0,
                                                   reference_signal_risk=31.0), tick=.01, filters=ExclusionFilters(enabled=True))
    assert rejected.outcomes.empty
    assert rejected.admission.iloc[0].reason == "excluded_actual_entry_risk_above_threshold"


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
    event = _event(bars, initial_stop=95.0, reference_signal_risk=5.0)
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


@pytest.mark.parametrize(
    ("updates", "accepted", "reason"),
    [
        ({}, True, "accepted"),
        ({"volume_ratio": 21.0}, False, "excluded_volume_ratio_above_threshold"),
        ({"initial_stop": 69.0, "reference_signal_risk": 31.0}, False, "excluded_actual_entry_risk_above_threshold"),
        ({"base_asset": "USDC"}, False, "excluded_base_asset"),
        ({"base_asset": "PAXG"}, False, "excluded_base_asset"),
        ({"stock_linked": True}, False, "excluded_stock_linked_asset"),
    ],
)
def test_filter_bundle_excludes_only_known_disqualifying_metadata(updates: dict[str, object], accepted: bool, reason: str) -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    result = replay_native_v1_arms(bars, _event(bars, **updates), tick=.01, arms=("filters_only",))
    audit = result.admission.iloc[0]
    assert (audit.accepted, audit.reason) == (accepted, reason)
    assert bool(not result.outcomes.empty) is accepted


def test_missing_filter_metadata_is_retained_with_audit_flag_not_invented_rejection() -> None:
    bars = _bars([(100, 100, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    event = _event(bars).drop(columns=["volume_ratio", "base_asset", "stock_linked"])
    result = replay_native_v1_arms(bars, event, tick=.01, arms=("filters_only",))
    audit = result.admission.iloc[0]
    assert (audit.accepted, audit.reason, audit.metadata_complete) == (True, "accepted", False)
    assert audit.metadata_flags == "missing_volume_ratio;missing_base_asset;missing_stock_linked_classification"
