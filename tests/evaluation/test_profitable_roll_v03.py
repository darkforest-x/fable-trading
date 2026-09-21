"""Synthetic, no-network checks for the profitable-roll-v03 replay."""
from __future__ import annotations

import copy

import pandas as pd
import pytest

from yoyo.evaluation.profitable_roll_v03 import replay_profitable_roll


def bars(values):
    index = pd.date_range("2026-01-01", periods=len(values), freq="h", tz="UTC")
    return pd.DataFrame(values, index=index, columns=["open", "high", "low", "close"])


def tiers():
    return [{"max_quantity": 10_000, "mmr": .01, "max_leverage": 50}]


def replay(values, **extra):
    defaults = dict(entry_i=0, initial_stop=90, tick=.1, quantity_step=1, min_quantity=1,
                    initial_quantity_requested=10, tiers=tiers(), capital=1_000, leverage=40)
    defaults.update(extra)
    return replay_profitable_roll(bars(values), **defaults)


def rolling_path():
    return [
        (100, 102, 99, 101), (101, 110, 100, 109), (109, 109, 104, 105),
        (105, 106, 103, 104), (104, 113, 104, 112), (113, 114, 112, 113),
        (113, 115, 109, 110), (110, 122, 110, 121), (121, 122, 119, 120),
        (120, 122, 115, 116),
    ]


def test_initial_quantity_is_clipped_by_stress_maintenance_not_requested_size():
    result, events = replay([(100, 101, 99, 100)], initial_stop=95, capital=100,
                            initial_quantity_requested=100, leverage=40)
    assert result["status"] == "censored"
    assert 0 < result["initial_quantity"] < 100
    assert result["initial_quantity_clip_reason"] == "initial_stress_maintenance"
    assert next(event for event in events if event["kind"] == "entry")["protective_equity"] >= \
        next(event for event in events if event["kind"] == "entry")["protective_required"]


def test_add_is_reduced_or_rejected_when_protective_maintenance_cannot_cover_it():
    values = rolling_path()
    result, events = replay(values, capital=150, initial_quantity_requested=10,
                            tiers=[{"max_quantity": 10, "mmr": .01, "max_leverage": 50},
                                   {"max_quantity": 10_000, "mmr": .45, "max_leverage": 50}])
    adds = [event for event in events if event["kind"] == "add"]
    rejects = [event for event in events if event["kind"] == "reject"]
    assert result["adds_count"] == len(adds)
    assert adds or rejects
    assert all(event["protective_equity"] >= event["protective_required"] - 1e-8 for event in adds)


def test_tier_jump_is_applied_to_whole_position_when_adding():
    result, events = replay(rolling_path(), capital=1_000, initial_quantity_requested=10,
                            tiers=[{"max_quantity": 10, "mmr": .01, "max_leverage": 50},
                                   {"max_quantity": 10_000, "mmr": .80, "max_leverage": 1}])
    assert not [event for event in events if event["kind"] == "add"]
    assert [event for event in events if event["kind"] == "reject"]
    assert result["adds_count"] == 0


def test_future_bars_do_not_change_prior_causal_events():
    source = bars(rolling_path())
    altered = copy.deepcopy(source)
    altered.iloc[-1] = (500, 600, 400, 550)
    kwargs = dict(entry_i=0, initial_stop=90, tick=.1, quantity_step=1, min_quantity=1,
                  initial_quantity_requested=10, tiers=tiers(), capital=1_000, max_adds=None)
    _, first = replay_profitable_roll(source, **kwargs)
    _, second = replay_profitable_roll(altered, **kwargs)
    cutoff = source.index[-1].isoformat()
    assert [event for event in first if event["time_utc"] < cutoff] == [event for event in second if event["time_utc"] < cutoff]


def test_stop_update_is_effective_next_bar_not_its_confirmation_bar():
    values = rolling_path()
    values[4] = (104, 113, 95, 112)  # The confirming bar crosses the new candidate stop.
    result, events = replay(values[:6])
    update = next(event for event in events if event["kind"] == "stop_update")
    assert result["status"] == "censored"
    assert update["effective_i"] == 5


def test_fees_and_funding_are_in_the_exit_ledger_and_funding_precedes_add():
    source = bars(rolling_path())
    funding_time = int(source.index[5].value // 1_000_000)
    result, events = replay_profitable_roll(source, entry_i=0, initial_stop=90, tick=.1, quantity_step=1,
        min_quantity=1, initial_quantity_requested=10, tiers=tiers(), capital=1_000,
        funding_rates={funding_time: .01})
    funding = next(event for event in events if event["kind"] == "funding")
    assert result["funding_mode"] == "provided_historical_settlements"
    assert funding["total_quantity"] == 10
    assert result["fees_paid"] > 0 and result["funding_paid"] == pytest.approx(funding["payment"])


def test_cap_two_and_unlimited_share_the_first_two_legs():
    kwargs = dict(capital=10_000, initial_quantity_requested=10)
    _, two = replay(rolling_path(), max_adds=2, **kwargs)
    _, unlimited = replay(rolling_path(), max_adds=None, **kwargs)
    extract = lambda values: [(event["time_utc"], event["quantity"], event["price"])
                              for event in values if event["kind"] == "add"]
    assert extract(two) == extract(unlimited)[:2]


def test_minimum_quantity_rejects_unfillable_initial_order():
    result, events = replay([(100, 101, 99, 100)], initial_quantity_requested=.5, min_quantity=1)
    assert result["status"] == "rejected_initial"
    assert events[0]["kind"] == "reject"


def test_market_lot_minimum_notional_and_max_order_apply_to_each_entry_leg():
    rejected, _ = replay([(100, 101, 99, 100)], initial_quantity_requested=1, min_notional=200)
    assert rejected["status"] == "rejected_initial"
    result, events = replay(rolling_path(), initial_quantity_requested=20, max_order_quantity=10,
                            capital=10_000)
    assert result["initial_quantity"] == 10
    assert all(event["quantity"] <= 10 for event in events if event["kind"] in {"entry", "add"})
    assert result["exit_requires_splitting"]


def test_proxy_stop_above_maintenance_exits_at_stop_before_later_low_crosses_liquidation():
    result, _ = replay([(100, 101, 99, 100), (100, 105, 50, 95)], initial_stop=90,
                       capital=100, initial_quantity_requested=5)
    assert result["status"] == "complete"
    assert result["exit_reason"] == "intrabar_stop"
    assert result["exit_price"] == 90
    assert result["mark_mode"] == "last_price_proxy"


def test_independent_mark_stop_and_maintenance_same_bar_stays_unknown():
    source = bars([(100, 101, 99, 100), (100, 105, 89, 95)])
    marks = bars([(100, 101, 99, 100), (100, 101, 40, 80)])
    result, _ = replay_profitable_roll(source, entry_i=0, initial_stop=90, tick=.1, quantity_step=1,
        min_quantity=1, initial_quantity_requested=5, tiers=tiers(), capital=100, mark_frame=marks)
    assert result["status"] == "ambiguous"
    assert result["final_balance"] is None
    assert result["hypothetical_balance_at_stop"] is not None
