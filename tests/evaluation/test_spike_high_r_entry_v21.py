"""Causal contracts for V2.1's three-complete-calendar-month risk gate."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_high_r_entry_v21 as study
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9_full_replay import prepare_v9


def _context(*, start: str = "2025-01-04", periods: int = 48) -> base.StreamContext:
    """Create a V9-admissible hourly stream whose raw candidates are supplied below."""
    index = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "atr": 1., "rv": 50.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ready": True,
                         "ropeHigh": 100., "ropeLow": 100.}, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    cache = {"bars": bars, "signals": signals.copy(), "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "bb_ready": pd.Series(True, index=index), "data_gap": pd.Series(False, index=index), "tick": .01}
    return base.StreamContext(Path("."), "high-r-entry-v21-synthetic", {}, cache,
                              pd.DataFrame(columns=["signal_bar_open", "signal_i"]), 60,
                              {"asset": "ETH", "symbol": "ETHUSDC", "venue": "synthetic", "timeframe_min": 60})


def _prepared(context: base.StreamContext, entries: dict[int, int]):
    """Return a real original V9 prepared arm and its candidate decisions."""
    index, signals = context.cache["bars"].index, context.cache["signals"]
    for i, side in entries.items():
        signals.loc[index[i], "long_signal" if side == 1 else "short_signal"] = True
    context.cache["v1_signals"] = signals.copy()
    context = replace(context, signals_ledger=pd.DataFrame({"signal_bar_open": [index[i] for i in entries],
                                                             "signal_i": list(entries)}))
    return context, *prepare_v9(engine.prepare_arm(context, arm="v8"))


def _decisions(v9: pd.DataFrame, risks: dict[int, float]) -> pd.DataFrame:
    """Supply explicit signal-close risks without deriving or reading fills."""
    result = v9.copy(deep=True)
    for i, risk in risks.items():
        result.loc[result.local_i.eq(i), "reference_risk_fraction"] = risk
    return result


def _threshold(month: str, *, cutoff: float = .01, known: bool = True, n_history: int = 100,
               history_end: object | None = None, history_start: object | None = None) -> pd.DataFrame:
    """Build one root-panel row for exactly the required three complete months."""
    end = pd.Timestamp(month + "-01T00:00:00Z") if history_end is None else history_end
    start = pd.Timestamp(end) - pd.DateOffset(months=study.LOOKBACK_MONTHS) if history_start is None else history_start
    return pd.DataFrame([{"timeframe_min": 60, "month": month, "cutoff": cutoff, "history_end": end,
                          "history_start": start, "n_history": n_history, "known": known}])


def test_reference_risk_equality_passes_without_reading_actual_next_open() -> None:
    context = _context(); context, prepared, v9 = _prepared(context, {20: 1})
    decisions = _decisions(v9, {20: .01})
    gated, result = study.prepare_entry_v21(prepared, decisions, _threshold("2025-01", cutoff=.01))
    row = result.iloc[0]
    assert (bool(gated.allowed[20]), row.score, row.risk_cutoff, row.reference_risk_fraction) == (True, pytest.approx(-.01), pytest.approx(.01), pytest.approx(.01))
    altered_open = prepared.open.copy(); altered_open[21] = 9_999.
    _, altered = study.prepare_entry_v21(replace(prepared, open=altered_open), decisions, _threshold("2025-01", cutoff=.01))
    pd.testing.assert_frame_equal(result, altered)


@pytest.mark.parametrize("bad", ["future_end", "wrong_start"])
def test_future_or_cross_month_threshold_is_rejected(bad: str) -> None:
    context = _context(); _, prepared, v9 = _prepared(context, {20: 1})
    if bad == "future_end":
        panel = _threshold("2025-01", history_end=pd.Timestamp("2025-01-05T00:00:00Z"))
    else:
        panel = _threshold("2025-01", history_start=pd.Timestamp("2024-11-01T00:00:00Z"))
    with pytest.raises(ValueError, match="threshold"):
        study.prepare_entry_v21(prepared, _decisions(v9, {20: .01}), panel)


def test_missing_or_insufficient_history_is_unknown_and_short_is_unchanged() -> None:
    context = _context(); _, prepared, v9 = _prepared(context, {20: 1, 21: -1})
    gated, result = study.prepare_entry_v21(prepared, _decisions(v9, {20: .01}), _threshold("2025-01", n_history=99))
    long = result.loc[result.local_i.eq(20)].iloc[0]
    short = result.loc[result.local_i.eq(21)].iloc[0]
    assert (bool(gated.allowed[20]), bool(long.gate_known), long.reason) == (False, False, "insufficient_history")
    assert (bool(gated.allowed[21]), bool(short.gate_known), bool(short.gate_passed), short.reason) == (True, True, True, "short_unchanged")


def test_string_false_known_flag_cannot_permit_a_long() -> None:
    context = _context(); _, prepared, v9 = _prepared(context, {20: 1})
    panel = _threshold("2025-01", cutoff=.01, known="False")
    gated, result = study.prepare_entry_v21(prepared, _decisions(v9, {20: .001}), panel)
    assert not gated.allowed[20]
    assert (bool(result.iloc[0].gate_known), result.iloc[0].reason) == (False, "insufficient_history")


def test_available_at_month_is_the_closed_bar_month_boundary() -> None:
    context = _context(start="2025-01-31T03:00:00Z"); _, prepared, v9 = _prepared(context, {20: 1})
    gated, result = study.prepare_entry_v21(prepared, _decisions(v9, {20: .01}), _threshold("2025-02", cutoff=.01))
    row = result.iloc[0]
    assert (row.signal_bar_open, row.available_at, row.threshold_month, bool(gated.allowed[20])) == (
        pd.Timestamp("2025-01-31T23:00:00Z"), pd.Timestamp("2025-02-01T00:00:00Z"), "2025-02", True)


def test_input_is_immutable_and_later_candidates_or_prices_cannot_change_prior_decisions() -> None:
    context = _context(); _, prepared, v9 = _prepared(context, {20: 1, 25: 1})
    decisions = _decisions(v9, {20: .01, 25: .02})
    panel = _threshold("2025-01", cutoff=.015)
    arrays = {name: getattr(prepared, name).copy() for name in ("allowed", "raw_side", "gap", "open", "high", "low", "close", "atr")}
    v9_before, panel_before = decisions.copy(deep=True), panel.copy(deep=True)
    _, original = study.prepare_entry_v21(prepared, decisions, panel)
    for name, before in arrays.items():
        np.testing.assert_equal(getattr(prepared, name), before)
    pd.testing.assert_frame_equal(decisions, v9_before); pd.testing.assert_frame_equal(panel, panel_before)
    later = decisions.copy(deep=True)
    later.loc[later.local_i.eq(25), "reference_risk_fraction"] = .5
    future_prices = prepared.close.copy(); future_prices[30:] = 100_000.
    _, changed = study.prepare_entry_v21(replace(prepared, close=future_prices), later, panel)
    pd.testing.assert_frame_equal(original.loc[original.local_i.eq(20)].reset_index(drop=True),
                                  changed.loc[changed.local_i.eq(20)].reset_index(drop=True))


def test_duplicate_threshold_lookup_is_rejected() -> None:
    context = _context(); _, prepared, v9 = _prepared(context, {20: 1})
    panel = pd.concat([_threshold("2025-01"), _threshold("2025-01")], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate threshold"):
        study.prepare_entry_v21(prepared, _decisions(v9, {20: .01}), panel)
    duplicated_candidates = pd.concat([_decisions(v9, {20: .01}), _decisions(v9, {20: .01})], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate V9 candidate"):
        study.prepare_entry_v21(prepared, duplicated_candidates, _threshold("2025-01"))
