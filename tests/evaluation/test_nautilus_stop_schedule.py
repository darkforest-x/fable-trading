"""Behavioural checks for the per-bar stop-schedule NautilusTrader adapter."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("nautilus_trader")
from yoyo.layers.l3_backtest.nautilus_stop_schedule import run_schedule  # noqa: E402

TICK = 0.01
NS5 = 300 * 10**9


def frame(rows):
    idx = pd.date_range("2024-01-01 00:05", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def schedule(f, at, levels, life=3):
    idx = np.asarray(at, int)
    exp = f.index.as_unit("ns").asi8[idx] + life * NS5
    return idx, np.asarray(levels, float), exp


def run(rows, entries, schedules):
    f = frame(rows)
    sch = {k: schedule(f, *v) for k, v in schedules.items()}
    out, audit = run_schedule(f, pd.DataFrame(entries), sch, TICK, symbol="TST")
    return out.set_index("trade_id"), audit


def flat(n, p=100.0):
    return [[p, p + .2, p - .2, p]] * n


def test_fixed_stop_fills_at_trigger_and_charges_taker_twice():
    rows = flat(3) + [[100, 100.1, 96, 97]] + flat(2, 97)
    out, _ = run(rows, [dict(trade_id="a", entry_i=1, side=1, entry=100.0, stop=97.0, schedule="s")],
                 {"s": ([1, 2, 3, 4], [np.nan] * 4, 10)})
    r = out.loc["a"]
    assert r.exit_reason == "fixed_stop" and r.exit_i == 3
    assert r.exit_px == pytest.approx(97.0)
    assert r.fee_bp == pytest.approx(5 + 5 * 0.97, rel=1e-6)
    assert r.net_bp == pytest.approx(-300 - r.fee_bp, rel=1e-6)


def test_flip_level_tightens_stop_and_gap_open_fills_at_open():
    rows = flat(3) + [[98.5, 98.6, 98.4, 98.5]] + flat(2, 98.5)
    out, _ = run(rows, [dict(trade_id="b", entry_i=1, side=1, entry=100.0, stop=97.0, schedule="s")],
                 {"s": ([1, 2, 3], [np.nan, 99.0, 99.0], 10)})
    r = out.loc["b"]
    assert r.exit_reason == "flip_stop" and r.exit_i == 3 and r.stop_updates == 1
    assert r.exit_px == pytest.approx(98.5)  # opened below the 99 trigger


def test_level_through_close_exits_at_that_close():
    rows = flat(6)
    out, _ = run(rows, [dict(trade_id="c", entry_i=1, side=1, entry=100.0, stop=97.0, schedule="s")],
                 {"s": ([1, 2], [np.nan, 100.5], 10)})
    r = out.loc["c"]
    assert r.exit_reason == "flip_at_close" and r.exit_i == 2 and r.exit_px == pytest.approx(100.0)


def test_expired_schedule_forces_exit_and_last_bar_censors():
    rows = flat(8)
    out, _ = run(rows, [dict(trade_id="d", entry_i=1, side=-1, entry=100.0, stop=103.0, schedule="s"),
                        dict(trade_id="e", entry_i=2, side=1, entry=100.0, stop=97.0, schedule="t")],
                 {"s": ([1], [np.nan], 2), "t": ([2, 3, 4, 5, 6, 7], [np.nan] * 6, 2)})
    assert out.loc["d"].exit_reason == "gap" and out.loc["d"].exit_i == 4
    assert out.loc["e"].exit_reason == "data_end" and bool(out.loc["e"].censored)


def test_short_flip_level_never_looser_than_fixed():
    rows = flat(3) + [[100, 102.5, 99.9, 100]] + flat(2)
    out, _ = run(rows, [dict(trade_id="f", entry_i=1, side=-1, entry=100.0, stop=103.0, schedule="s")],
                 {"s": ([1, 2, 3, 4], [105.0, 102.0, 101.0, 101.0], 10)})
    r = out.loc["f"]
    assert r.exit_reason == "flip_stop" and r.exit_i == 3 and r.exit_px == pytest.approx(102.0)


def test_rejects_entry_not_at_close():
    with pytest.raises(ValueError):
        run(flat(4), [dict(trade_id="g", entry_i=1, side=1, entry=99.0, stop=97.0, schedule="s")],
            {"s": ([1], [np.nan], 3)})
