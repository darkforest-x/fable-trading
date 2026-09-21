"""Exact, synthetic parity coverage for the local Perfect Filter adapter."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

import yoyo.datasets.ma_launch_owner_perfect_filter as original_module
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_launch_owner_perfect_filter import PerfectFilterError, extract_profile
from yoyo.datasets.ma_profit_profile_window import extract_profile_window


def _frame(*, periods: int = 240, minutes: int = 15) -> pd.DataFrame:
    times = pd.date_range("2025-02-01T00:00:00Z", periods=periods, freq=f"{minutes}min")
    phase = np.linspace(0.0, 12.0, periods)
    close = 100.0 + np.linspace(0.0, 20.0, periods) + 1.3 * np.sin(phase)
    open_ = close - 0.35 * np.cos(phase * 1.7)
    return add_candidate_features(pd.DataFrame({
        "open_time": times,
        "open": open_,
        "high": np.maximum(open_, close) + 1.1,
        "low": np.minimum(open_, close) - 1.2,
        "close": close,
        "volume": 100.0 + np.arange(periods),
    }))


def _row(schema: str, *, core_bars: int = 4, direction: str = "LONG", anchor: int | None = None) -> dict[str, Any]:
    start, end = 160, 160 + core_bars - 1
    anchor = end + 2 if anchor is None else anchor
    base: dict[str, Any] = {"direction": direction, "box": {"h_norm": 0.12}}
    if schema == "source":
        return {**base, "source_core_start_i": start, "source_core_end_i": end,
                "source_comparison_anchor_i": anchor}
    if schema == "reference":
        # The anchor lies inside the 12-bar prelude for this legacy shape.
        anchor = 150 if anchor == end + 2 else anchor
        return {**base, "core_start_source_i": start, "core_end_source_i": end,
                "core_start_offset": start - anchor, "core_end_offset": end - anchor}
    if schema == "offset":
        anchor = 150 if anchor == end + 2 else anchor
        return {**base, "source_anchor_i": anchor,
                "core_start_offset": start - anchor, "core_end_offset": end - anchor}
    raise AssertionError(schema)


def _assert_same(frame: pd.DataFrame, row: dict[str, Any], **kwargs: Any) -> None:
    expected = extract_profile(frame, row, **kwargs)
    actual = extract_profile_window(frame, row, **kwargs)
    assert actual.core_start_i == expected.core_start_i
    assert actual.core_end_i == expected.core_end_i
    # Dict equality and array_equal deliberately permit no numerical tolerance.
    assert actual.metrics == expected.metrics
    np.testing.assert_array_equal(actual.sequence, expected.sequence)


@pytest.mark.parametrize("schema", ["source", "reference", "offset"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("core_bars", [4, 5])
def test_all_row_index_schemas_directions_and_core_lengths_are_bit_exact(
    schema: str, direction: str, core_bars: int
) -> None:
    _assert_same(_frame(), _row(schema, direction=direction, core_bars=core_bars))


@pytest.mark.parametrize("shift", [-1, 1])
def test_core_shift_keeps_original_anchor_and_restores_global_indices(shift: int) -> None:
    frame = _frame()
    row = _row("source", core_bars=4)
    _assert_same(frame, row, core_shift=shift)


@pytest.mark.parametrize("anchor", [165, 185, 140])
def test_comparison_anchor_inside_after_and_before_window_are_exact(anchor: int) -> None:
    _assert_same(_frame(), _row("source", anchor=anchor))


def test_non_range_index_and_numeric_poison_outside_window_do_not_change_profile() -> None:
    frame = _frame()
    frame.index = pd.Index(np.arange(10_000, 10_000 + len(frame)) * 3)
    # These rows are outside both the source profile window and the later ATR
    # anchor.  The original ignores numeric NaN there; so must the adapter.
    frame.iloc[10, frame.columns.get_loc("open")] = np.nan
    frame.iloc[220, frame.columns.get_loc("sma20")] = np.nan
    _assert_same(frame, _row("source"))


@pytest.mark.parametrize("column, position", [("open", 10), ("sma20", 220)])
def test_object_poison_outside_window_retains_original_full_conversion_error(column: str, position: int) -> None:
    frame = _frame()
    frame[column] = frame[column].astype(object)
    frame.iloc[position, frame.columns.get_loc(column)] = "POISON"
    _assert_same_error(frame, _row("source"))


def _assert_same_error(frame: pd.DataFrame, row: dict[str, Any], **kwargs: Any) -> None:
    with pytest.raises(Exception) as expected:
        extract_profile(frame, row, **kwargs)
    with pytest.raises(type(expected.value)) as actual:
        extract_profile_window(frame, row, **kwargs)
    assert str(actual.value) == str(expected.value)


def test_gap_and_nan_inside_window_keep_original_rejection() -> None:
    row = _row("source")
    gapped = _frame()
    gapped.loc[161, "open_time"] += pd.Timedelta(minutes=15)
    _assert_same_error(gapped, row)

    nan_inside = _frame()
    nan_inside.loc[160, "sma20"] = np.nan
    _assert_same_error(nan_inside, row)


def test_visibility_boundary_unknown_period_and_invalid_indices_delegate_exactly() -> None:
    frame = _frame()
    row = _row("source")
    cutoff = frame["open_time"].iloc[168]
    _assert_same_error(frame, row, visibility_end_exclusive=cutoff)
    _assert_same(frame, row, visibility_end_exclusive=cutoff + pd.Timedelta(nanoseconds=1))
    _assert_same_error(frame, row, bar_minutes=7)
    _assert_same(frame, _row("source", anchor=-1))
    _assert_same_error(frame, {**row, "source_core_start_i": 3, "source_core_end_i": 6})


def test_adapter_uses_bound_original_function_not_mutable_module_global(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, row = _frame(), _row("source")
    expected = extract_profile(frame, row)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("adapter consulted mutable module global")

    monkeypatch.setattr(original_module, "extract_profile", forbidden)
    actual = extract_profile_window(frame, row)
    assert actual.metrics == expected.metrics
    np.testing.assert_array_equal(actual.sequence, expected.sequence)
