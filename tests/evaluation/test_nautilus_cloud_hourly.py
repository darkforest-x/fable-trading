"""Focused native fixtures for the V1.9 hourly cloud stop adapter."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")
from yoyo.layers.l3_backtest.nautilus_cloud_hourly import run_cloud_hourly  # noqa: E402


HOUR = 3_600_000
TICK = 0.01
BASE = pd.Timestamp("2026-01-01T00:00:00Z")


def frame(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                        index=pd.date_range(BASE, periods=len(rows), freq="1h"))


def entry(trade_id="t", *, at=0, side=1, price=100.0, stop=90.0, fail=None):
    return dict(trade_id=trade_id, side=side,
                entry_ms=int((BASE + pd.Timedelta(hours=at)).timestamp() * 1000),
                entry_price=price, stop=stop, fail_at_ms=fail)


def crosses(rows):
    return pd.DataFrame(rows, columns=["start_ms", "cross", "prev_spread"])


def run(bars, entries, levels):
    out, audit = run_cloud_hourly(frame(bars), pd.DataFrame(entries), levels,
                                  tick=TICK, risk_usdt=100, symbol="ETH")
    return out.set_index("trade_id"), audit


def tms(hour):
    return int((BASE + pd.Timedelta(hours=hour)).timestamp() * 1000)


def test_strict_cloud_cross_equality_does_not_exit_but_next_tick_does():
    bars = [
        [100, 100, 100, 100],
        [100, 101, 95, 100],  # equality at the cloud threshold is not a flip
        [100, 101, 94.99, 99],
        [99, 100, 98, 99],
    ]
    out, audit = run(bars, [entry()], crosses([[tms(0), 95.0, 0.0]]))
    trade = out.loc["t"]
    assert trade.exit_reason == "cloud_flip"
    assert trade.exit_phase == "intrabar"
    assert trade.exit_hour_ms == tms(2)
    assert trade.exit_px == pytest.approx(94.99)
    assert audit["positions"] == 1 and audit["fills"] == 2
    assert trade.entry_fee == pytest.approx(trade.entry_px * trade.quantity * .0005)
    assert trade.exit_fee == pytest.approx(trade.exit_px * trade.quantity * .0005)


def test_non_tick_fixed_stop_keeps_raw_risk_and_rounds_long_trigger_down():
    bars = [
        [100, 100, 100, 100],
        [100, 101, 90.00, 99],
        [99, 100, 98, 99],
    ]
    out, audit = run(bars, [entry(stop=90.005)], crosses([[tms(0), np.nan, np.nan]]))
    trade = out.loc["t"]
    assert trade.exit_reason == "fixed_stop"
    assert trade.exit_phase == "intrabar"
    assert trade.exit_px == pytest.approx(90.0)
    assert trade.fixed_stop == pytest.approx(90.005)
    assert trade.actual_risk_usdt == pytest.approx(100.0, abs=1e-6)
    assert audit["quantity_precision"] >= 6


def test_opening_fail_cancels_old_stop_and_marks_out_at_open():
    bars = [
        [100, 101, 99, 100],
        [100, 101, 99, 100],
        [80, 101, 79, 90],
        [90, 91, 89, 90],
    ]
    out, _ = run(bars, [entry(stop=90, fail=tms(2))], crosses([[tms(0), np.nan, np.nan]]))
    trade = out.loc["t"]
    assert trade.exit_reason == "scheduled_fail"
    assert trade.exit_phase == "open"
    assert trade.exit_hour_ms == tms(2)
    assert trade.exit_px == pytest.approx(80.0)


def test_confirmed_cross_update_is_active_before_next_open_matching():
    bars = [
        [100, 101, 99, 100],
        [100, 101, 99, 100],
        [98.5, 101, 98, 99],
        [99, 100, 98, 99],
    ]
    levels = crosses([[tms(0), 95.0, 0.0], [tms(2), 99.0, -1.0]])
    out, _ = run(bars, [entry()], levels)
    trade = out.loc["t"]
    assert trade.exit_reason == "cloud_flip"
    assert trade.exit_phase == "open"
    assert trade.exit_hour_ms == tms(2)
    assert trade.exit_px == pytest.approx(98.5)


def test_opposite_prior_spread_does_not_reject_early_entry_and_none_fail_never_fires():
    bars = [
        [100, 101, 99, 100],
        [100, 101, 99, 100],
        [100, 101, 99, 100],
    ]
    out, _ = run(bars, [entry(fail=None)], crosses([[tms(0), 95.0, -1.0]]))
    trade = out.loc["t"]
    assert trade.entry_ms == tms(0)
    assert trade.exit_reason == "data_end"
    assert trade.exit_phase == "close"


def test_overlapping_positions_keep_independent_native_ids():
    bars = [
        [100, 101, 99, 100],
        [100, 101, 99, 100],
        [100, 101, 94.99, 99],
    ]
    entries = [entry("a", at=0), entry("b", at=1)]
    out, audit = run(bars, entries, crosses([[tms(0), 95.0, 0.0]]))
    assert set(out.index) == {"a", "b"}
    assert out.framework_position_id.is_unique
    assert audit["simultaneous_positions_max"] == 2
