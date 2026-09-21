"""Exact local-window adapter for the frozen Perfect Filter profile extractor.

``extract_profile`` already receives a feature-enriched frame: its six moving
averages and ATR carry their full historical warmup and are never recomputed
here.  The profile itself reads only twelve bars before the shifted core, the
four/five core bars, five post-core bars, and (when later) the comparison ATR
anchor.  This adapter supplies that positional slice to the original function
and restores the returned core coordinates to the source-frame index space.

It is intentionally not wired into a miner or queue.  Invalid geometry and an
anchor before the required prelude fall back to the original full-frame call so
their established error behavior remains the authority.
"""
from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from yoyo.datasets.ma_launch_owner_perfect_filter import (
    PerfectFilterError,
    ShapeProfile,
    _row_indices,
    extract_profile as _original_extract_profile,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS


_FULL_ARRAY_COLUMNS = ("open", "high", "low", "close", *SIX_MA_COLUMNS)


def extract_profile_window(
    frame: pd.DataFrame,
    row: Mapping[str, Any],
    *,
    core_shift: int = 0,
    bar_minutes: int = 15,
    visibility_end_exclusive: pd.Timestamp | None = None,
) -> ShapeProfile:
    """Call the frozen extractor on its complete local dependency window.

    The source-index resolver is deliberately the original private helper, so
    all three historical row schemas retain their precedence and coercion
    semantics.  The bound import of the extractor prevents a later caller's
    monkeypatch of the original module global from changing this adapter.
    """

    start_i, end_i, anchor_i = _row_indices(row)
    shift = int(core_shift)
    shifted_start = start_i + shift
    shifted_end = end_i + shift
    core_len = shifted_end - shifted_start + 1

    # These conditions are precisely the normal extractor's geometry boundary.
    # Delegating before slicing preserves its message and unusual negative-index
    # behavior instead of accidentally normalizing it in a new implementation.
    if (
        core_len not in {4, 5}
        or shifted_start - 12 < 0
        or shifted_end + 5 >= len(frame)
        or anchor_i < 0
        or anchor_i >= len(frame)
        or anchor_i < shifted_start - 12
    ):
        return _original_extract_profile(
            frame,
            row,
            core_shift=core_shift,
            bar_minutes=bar_minutes,
            visibility_end_exclusive=visibility_end_exclusive,
        )

    # The frozen function coerces these *entire* columns to float before it
    # takes its local windows.  Numeric dtypes let the adapter safely avoid
    # that large conversion; an object/string poison anywhere must retain the
    # original conversion error rather than silently becoming a success.
    try:
        has_non_numeric_column = any(
            not pd.api.types.is_numeric_dtype(frame[column])
            for column in _FULL_ARRAY_COLUMNS
        )
    except KeyError:
        has_non_numeric_column = True
    if has_non_numeric_column:
        return _original_extract_profile(
            frame,
            row,
            core_shift=core_shift,
            bar_minutes=bar_minutes,
            visibility_end_exclusive=visibility_end_exclusive,
        )

    local_start = shifted_start - 12
    local_end = max(shifted_end + 5, anchor_i)
    window = frame.iloc[local_start : local_end + 1].reset_index(drop=True)

    # Direct source fields have documented precedence in ``_row_indices``.  Set
    # the unshifted local edges and retain core_shift, rather than baking the
    # shift into the row, so the pre-registered null semantics are unchanged.
    local_row = dict(row)
    local_row.update(
        {
            "source_core_start_i": start_i - local_start,
            "source_core_end_i": end_i - local_start,
            "source_comparison_anchor_i": anchor_i - local_start,
        }
    )
    profile = _original_extract_profile(
        window,
        local_row,
        core_shift=core_shift,
        bar_minutes=bar_minutes,
        visibility_end_exclusive=visibility_end_exclusive,
    )
    return ShapeProfile(
        metrics=profile.metrics,
        sequence=profile.sequence,
        core_start_i=profile.core_start_i + local_start,
        core_end_i=profile.core_end_i + local_start,
    )


__all__ = ["extract_profile_window", "PerfectFilterError", "ShapeProfile"]
