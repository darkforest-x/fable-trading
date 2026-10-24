"""Fee-aware reclaim protection on the shared native limit-entry engine."""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import numpy as np
import pandas as pd
import pytest
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.objects import Money

from yoyo.layers.l3_backtest import nautilus_independent as native
from yoyo.layers.l3_backtest import nautilus_reclaim_fee as fee_adapter
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy


def replay(bars, *, side=1, minutes=1, target=150., tick=.05, batch_bars=None):
    entry = 100.
    frame = pd.DataFrame(
        bars,
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-06T00:00Z", periods=len(bars), freq="min"),
    )
    entries = pd.DataFrame([dict(
        trade_id="reclaim", entry_i=0, expire_i=1, end_i=len(frame)-1,
        entry=entry, side=side, stop=np.nan, risk=entry,
        target_price=target, reclaim_minutes=minutes,
    )])
    policy = ExitPolicy(
        trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0.,
        target_from_entry=True, intrabar_path="OHLC",
        fill_maker_fee=.0002, fill_taker_fee=.0005,
        reclaim_next_bar=True, breakeven_includes_cost=False,
    )
    return fee_adapter.run_native_fee_reclaim(
        frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries, policy,
        tick=tick, symbol="TEST", execution_minutes=1,
        entry_order_mode="limit", batch_bars=batch_bars,
    )


def delayed_bars(side=1):
    if side > 0:
        bars = [[102.]*4, [102., 103., 99., 101.]]
        bars.extend([[100., 100.1, 99.9, 100.]] * 8)  # i=2..9, still pending
        bars.extend([
            [100., 100.3, 99.8, 100.1],       # i=10: reclaim, no fee room
            [100.1, 100.3, 100.05, 100.2],   # i=11: old stop survives wick
            [100.2, 100.3, 100.1, 100.15],   # i=12: upgraded stop fills
        ])
    else:
        bars = [[98.]*4, [98., 101., 97., 99.]]
        bars.extend([[100., 100.1, 99.9, 100.]] * 8)
        bars.extend([
            [100., 100.2, 99.8, 99.9],       # i=10: reclaim, no fee room
            [99.9, 99.9, 99.7, 99.8],        # i=11: old stop survives wick
            [99.8, 99.9, 99.7, 99.85],       # i=12: upgraded stop fills
        ])
    return bars


def immediate_bars(side=1, *, gap=False):
    if side > 0:
        bars = [
            [102.]*4,
            [102., 103., 99., 101.],
            [100.1, 100.3, 99.9, 100.2],  # reclaim close leaves one tick
            [99.5, 100., 99., 99.5] if gap else [100.2, 100.3, 100.1, 100.15],
        ]
    else:
        bars = [
            [98.]*4,
            [98., 101., 97., 99.],
            [99.9, 100.1, 99.7, 99.8],    # reclaim close leaves one tick
            [100.5, 101., 100., 100.5] if gap else [99.8, 99.9, 99.7, 99.85],
        ]
    return bars


@pytest.mark.parametrize(
    "side, expected_stop, bars",
    [(1, 100.1, immediate_bars(1)), (-1, 99.9, immediate_bars(-1))],
)
def test_actual_entry_commission_and_immediate_fee_stop(side, expected_stop, bars):
    rows, fills, audit = replay(bars, side=side, target=200. if side > 0 else 50.)
    row = rows.iloc[0]
    entry_fill = fills.loc[fills.phase == "entry"].iloc[0]

    assert row.reclaim_status == "reclaimed"
    assert row.fee_be_effective_i == 2
    assert row.fee_be_deferred_closes == 0
    assert row.fee_be_price == expected_stop
    assert row.entry_commission == pytest.approx(entry_fill.commission, abs=1e-12)
    assert row.protection == expected_stop
    assert row.exit_i == 3 and row.exit_price == expected_stop and row.reason == 5
    assert audit["fee_reclaim_entry_commission"] == "native entry fill commission"
    assert list(fills.phase) == ["entry", "stop"]


@pytest.mark.parametrize("side", [1, -1])
def test_delayed_fee_stop_retries_each_execution_close_without_retrospective_wick(side):
    bars = delayed_bars(side)
    rows, fills, _ = replay(
        bars, side=side, minutes=5, target=200. if side > 0 else 50.
    )
    row = rows.iloc[0]
    expected_stop = 100.1 if side > 0 else 99.9

    assert row.reclaim_i == 10
    assert row.fee_be_effective_i == 11
    assert row.fee_be_deferred_closes == 1
    assert row.fee_be_price == expected_stop
    assert row.exit_i == 12 and row.exit_price == expected_stop
    assert row.protection == expected_stop
    assert row.reason == 5
    assert list(fills.phase) == ["entry", "stop"]
    assert row.net_r > 0


def test_insufficient_room_keeps_exact_entry_stop_live_until_it_fills():
    bars = delayed_bars(1)
    bars[11] = [100.1, 100.2, 99.9, 100.]  # exact-entry stop hits before retry close
    rows, fills, _ = replay(bars, minutes=5, target=200.)
    row = rows.iloc[0]

    assert row.exit_i == 11 and row.exit_price == 100.
    assert row.protection == 100.
    assert row.fee_be_effective_i == -1
    assert row.fee_be_deferred_closes == 1
    assert row.net_r < 0
    assert list(fills.phase) == ["entry", "stop"]


def test_adverse_gap_after_fee_stop_activation_keeps_native_gap_fill():
    rows, fills, _ = replay(immediate_bars(1, gap=True), target=200.)
    row = rows.iloc[0]

    assert row.fee_be_effective_i == 2
    assert row.reason == 5
    assert row.exit_i == 3
    assert row.exit_price == 99.5
    assert row.exit_price < row.fee_be_price
    assert row.net_r < 0
    assert list(fills.phase) == ["entry", "stop"]


def test_native_target_fill_precedes_reclaim_fee_upgrade():
    bars = [
        [102.]*4,
        [102., 103., 99., 101.],
        [100., 111., 99., 100.1],
        [100.1, 100.2, 99.9, 100.],
    ]
    rows, fills, _ = replay(bars, target=110.)
    row = rows.iloc[0]

    assert row.reason == 4
    assert row.reclaim_status == "pending"
    assert row.fee_be_effective_i == -1
    assert list(fills.phase) == ["entry", "target"]


def test_streaming_matches_single_pass_for_fee_reclaim_state():
    bars = delayed_bars(1)
    args = dict(minutes=5, target=200.)
    rows, fills, _ = replay(bars, **args)
    streamed_rows, streamed_fills, _ = replay(bars, batch_bars=2, **args)

    pd.testing.assert_frame_equal(rows, streamed_rows)
    pd.testing.assert_frame_equal(
        fills.drop(columns="client_order_id"),
        streamed_fills.drop(columns="client_order_id"),
    )


@pytest.mark.parametrize("side, expected", [(1, Decimal("100.1")), (-1, Decimal("99.9"))])
def test_stop_formula_uses_actual_commission_and_rounds_outward(side, expected):
    stop = fee_adapter._commission_neutral_stop(
        entry=Decimal("100"), side=side, quantity=Decimal("10"),
        entry_commission=Decimal("0.20"), taker_fee=Decimal("0.0005"),
        tick=Decimal("0.1"),
    )

    assert stop == expected
    exit_fee = Money(stop * Decimal("10") * Decimal("0.0005"), USDT).as_decimal()
    net = Decimal(side) * Decimal("10") * (stop - Decimal("100")) - Decimal("0.20") - exit_fee
    assert net >= 0


def test_money_rounding_cannot_make_the_rounded_fee_stop_nominally_negative():
    entry, quantity = Decimal("1000"), Decimal("1")
    entry_commission, taker_fee, tick = Decimal("0.20"), Decimal("0.0005"), Decimal("1e-10")
    raw = (entry + entry_commission / quantity) / (Decimal(1) - taker_fee)
    first_tick_stop = (
        (raw / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    )
    first_exit_fee = Money(first_tick_stop * quantity * taker_fee, USDT).as_decimal()
    first_net = quantity * (first_tick_stop-entry) - entry_commission - first_exit_fee
    assert first_net < 0  # Money's 8-decimal quote rounding crosses the algebraic zero.

    stop = fee_adapter._commission_neutral_stop(
        entry=entry, side=1, quantity=quantity,
        entry_commission=entry_commission, taker_fee=taker_fee, tick=tick,
    )
    exit_fee = Money(stop * quantity * taker_fee, USDT).as_decimal()
    net = quantity * (stop-entry) - entry_commission - exit_fee
    assert stop > first_tick_stop
    assert net >= 0


def test_adapter_binds_private_strategy_without_mutating_shared_module():
    assert native.IndependentCloseStrategy is fee_adapter._native.IndependentCloseStrategy
    assert native.run_native.__globals__["IndependentCloseStrategy"] is native.IndependentCloseStrategy
    assert fee_adapter._run_native_fee_reclaim.__globals__ is not native.run_native.__globals__
    assert fee_adapter._run_native_fee_reclaim.__globals__["IndependentCloseStrategy"] is fee_adapter.FeeReclaimStrategy
