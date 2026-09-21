"""Focused contracts for the causal winner-pyramiding replay."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.winner_pyramiding import replay_path


def _frame(rows: list[tuple[float, float, float, float]], atr: float = 1.0) -> pd.DataFrame:
    ix = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=ix).assign(atr=atr)


def _run(rows, *, max_adds=2, raw=None, gap=None, **kw):
    f = _frame(rows)
    return replay_path(f, np.zeros(len(f), dtype=bool) if raw is None else raw,
                       np.zeros(len(f), dtype=bool) if gap is None else gap,
                       entry_i=0, initial_stop=98., tick=.01, max_adds=max_adds, **kw)


def test_new_close_stop_does_not_retroactively_use_current_low() -> None:
    # Bar 1 confirms a pullback low at 99 then closes above 101, lifting the
    # stop to 98.99.  Its old low must not be tested against that new stop.
    out, audit = _run([(100, 101, 99, 100), (100, 101, 99, 99.5), (100, 102, 99, 102), (102, 103, 99, 102)])
    assert out["exit_i"] == 3 and out["exit_reason"] == "boundary_mark"
    assert [e["kind"] for e in audit].count("stop_update") == 1


def test_prefix_is_unchanged_by_later_bars() -> None:
    base = [(100, 101, 99, 100), (100, 101, 99, 99.5), (100, 102, 99.2, 102)]
    a, _ = _run(base)
    b, _ = _run(base + [(102, 160, 1, 150)])
    assert (a["common_stop"], a["net_stop_floor"], a["adds_count"]) == pytest.approx((b["common_stop"], b["net_stop_floor"], b["adds_count"]))


def test_stop_gap_and_reverse_both_cancel_pending_add() -> None:
    rows = [(100, 101, 99, 100), (100, 102, 99, 99.5), (100, 103, 99.5, 103), (98, 99, 97, 98)]
    out, audit = _run(rows)
    assert out["exit_reason"] == "stop_gap" and not any(e["kind"] == "add" for e in audit)
    raw = np.array([False, False, True, False])
    out, audit = _run([(100, 101, 99, 100), (100, 102, 99, 99.5), (100, 103, 99.5, 103), (104, 105, 103, 104)], raw=raw)
    assert out["exit_reason"] == "opposite_short_next_open" and not any(e["kind"] == "add" for e in audit)


def test_fee_contract_is_exactly_twenty_bp_of_all_entry_legs() -> None:
    out, audit = _run([(100, 101, 99, 100), (100, 102, 99, 99.5), (100, 103, 99.5, 103), (104, 105, 103, 104)], capital=1000, gross_risk_budget=100)
    entries = [e for e in audit if e["kind"] in {"entry", "add"}]
    total_notional = sum(e["quantity"] * e["price"] for e in entries)
    assert out["fees_total"] == pytest.approx(.002 * total_notional)
    assert out["entry_fees_paid"] == pytest.approx(out["exit_fees_reserved"])


def test_cash_constraint_can_prevent_addition() -> None:
    out, audit = _run([(100, 101, 99, 100), (100, 102, 99, 99.5), (100, 103, 99.5, 104), (105, 106, 104, 105)], capital=100, gross_risk_budget=100)
    assert out["adds_count"] == 0
    assert any(e["kind"] == "reject" for e in audit)


def test_max_two_and_unlimited_diverge_after_three_confirmations() -> None:
    rows = [(100, 101, 99, 100), (100, 101, 99, 99), (100, 105, 99, 105),
            (106, 107, 103, 106), (106, 107, 104, 105), (106, 111, 105, 111),
            (112, 113, 110, 112), (112, 113, 110, 111), (112, 117, 111, 117),
            (118, 119, 116, 118)]
    limited, _ = _run(rows, max_adds=2, capital=10_000, gross_risk_budget=10)
    unlimited, audit = _run(rows, max_adds=None, capital=10_000, gross_risk_budget=10)
    assert limited["adds_count"] == 2 and unlimited["adds_count"] >= 3
    floors = [e["floor_after"] for e in audit if e["kind"] == "stop_update"]
    assert floors == sorted(floors) and unlimited["net_stop_floor"] == pytest.approx(floors[-1])
    committed = -float("inf")
    for event in audit:
        if event["kind"] == "stop_update":
            assert event["floor_after"] >= committed - 1e-9
            committed = event["floor_after"]
        elif event["kind"] == "add":
            # A post-add floor is both attainable at the common stop and
            # cannot erase any promise that was committed before the add.
            assert event["net_stop_floor"] <= event["net_stop"] + 1e-9
            assert event["net_stop_floor"] >= committed - 1e-9
            committed = event["net_stop_floor"]


def test_add_limit_changes_quantity_but_not_price_path_exit() -> None:
    rows = [(100, 101, 99, 100), (100, 101, 99, 99), (100, 105, 99, 105),
            (106, 107, 103, 106), (106, 107, 104, 105), (106, 111, 105, 111),
            (112, 113, 110, 112), (112, 113, 110, 111), (112, 117, 111, 117),
            (118, 119, 116, 118), (110, 111, 109, 110)]
    paths = [_run(rows, max_adds=limit, capital=10_000, gross_risk_budget=10)[0]
             for limit in (0, 2, None)]
    assert {p["adds_count"] for p in paths} == {0, 2, 3}
    assert {(p["exit_i"], p["exit_price"], p["exit_reason"]) for p in paths} == {(10, 110.0, "stop_gap")}


def test_initial_r_is_frozen_and_gap_and_boundary_are_censored() -> None:
    rows = [(100, 101, 99, 100), (100, 102, 99, 99.5), (100, 103, 99.5, 103), (104, 105, 103, 104)]
    out, _ = _run(rows, gap=np.array([False, False, False, True]))
    assert (out["censored"], out["exit_i"], out["exit_reason"], out["initial_risk"]) == (True, 2, "data_gap_censored", 2.0)
    out, _ = _run(rows)
    assert out["censored"] and out["exit_reason"] == "boundary_mark" and out["gross_r"] == pytest.approx(out["gross_usd"] / out["initial_risk_usd"])


def test_audit_timestamps_distinguish_open_fills_and_completed_closes() -> None:
    rows = [(100, 101, 99, 100), (100, 101, 99, 99.5), (100, 102, 99.2, 102)]
    out, audit = _run(rows)
    structure = next(e for e in audit if e["kind"] == "structure")
    entry = next(e for e in audit if e["kind"] == "entry")
    terminal = audit[-1]
    assert entry["known_at"] == entry["filled_at"] == _frame(rows).index[0]
    assert structure["known_at"] == _frame(rows).index[2] + pd.Timedelta(hours=1)
    assert terminal["known_at"] == _frame(rows).index[-1] + pd.Timedelta(hours=1)
    assert terminal["filled_at"] is None and out["exit_time_precision"] == "bar_close_mark"
    stopped, stopped_events = _run([(100, 101, 97, 100)])
    assert stopped["exit_time_precision"] == "intrabar_window"
    assert stopped_events[-1]["filled_at"] is None


def test_dropped_timestamp_is_censored_by_the_callers_gap_flag() -> None:
    frame = _frame([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    # The 02:00 bar is absent from the retrieved series; its first later bar
    # is explicitly unknown rather than rejected as an irregular DataFrame.
    frame.index = frame.index.delete(2).append(pd.DatetimeIndex([pd.Timestamp("2026-01-01 03:00", tz="UTC")]))
    out, audit = replay_path(frame, np.zeros(3, dtype=bool), np.array([False, False, True]),
                             entry_i=0, initial_stop=98., tick=.01, max_adds=0)
    assert (out["censored"], out["exit_i"], out["exit_reason"]) == (True, 1, "data_gap_censored")
    assert audit[-1]["known_at"] == frame.index[1] + pd.Timedelta(hours=1)
