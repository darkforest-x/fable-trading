"""V15 initial native5m/15m exit context, frozen before any outcome replay.

The original V5 requests and old ``ltf_entry_*`` columns are immutable. Reuse
the independent data-level entry observation validator; do not import L3 or
reimplement its transition state machine. Added source diagnostics refer to the
EXACT management candle that can initialize the existing true-transition engine.

Repository pandas2.3.3 documents flooring and exact, unfilled reindex lookup:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.floor.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Series.reindex.html
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_management_context import CONTEXT_COLUMNS, attach_management_context


SOURCE_COLUMNS = [
    "mg_entry_known", "mg_entry_ma", "mg_entry_hl2",
    "mg_entry_management_segment_id", "mg_entry_raw_segment_id",
    "mg_entry_native_minutes",
]
NATIVE_CONTEXT_COLUMNS = list(CONTEXT_COLUMNS) + SOURCE_COLUMNS


def attach_native_exit_context(raw5: pd.DataFrame, management_featured: pd.DataFrame,
                               requests: pd.DataFrame, management_minutes: int) -> pd.DataFrame:
    """Preserve every request and append its own completed native entry seed.

    Call once with native5m SMA40 features and once with native15m SMA40 features,
    both built by ``add_features(resample_complete(raw5, minutes), 'SMA', 40)``.
    Require the feature builder's attrs ma_kind='SMA', ma_length=40 and matching
    bar_minutes; absent or conflicting provenance is an input-contract error.
    This wrapper does not recompute MA or alter ``ma_side``: that supplied side
    is exactly what L3 observes. Native HL2=(high+low)/2 versus SMA40 semantics
    and40-bar contiguous warmup belong to the shared feature builder. A missing
    ATR or three-bar slope does NOT invalidate an otherwise valid initial side.

    Request inputs are only decision_time (actual entry E) and own direction.
    Optional event_id must be unique and nonnull. Retain original columns, index,
    row order, attrs and old5m diagnostics; reject output-column collisions.
    No risk/stop/return filter is applied. L3 validates executable entry and risk
    before initialization, so state parity is asserted only for entries that
    pass those original checks. This helper never turns a rejected entry into
    a trade or an unknown colour into an opposite state.

    Causal clock: interval M is exactly5 or15 minutes; A=floor(E,M). Select the
    management bar with OPEN exactly A-M and availability A<=E. No as-of older
    fallback, unfinished native candle, other request's colour or future value.
    Require all raw5 times in [A-M,E] in the same known RAW segment, including
    intervening completed bars for entry phases +5/+10. Validate their complete
    OHLC only before E; at E inspect open only. Management MA, HLC, supplied side
    and its own segment must be valid. Raw and native segment counters need not
    have the same values. Source timestamp schema validation follows the shared
    validator; OHLCV after E is not used for the initial-state decision.

    Added columns:
    * mg_entry_side: nullable +1/-1; mg_entry_aligned: nullable boolean;
      mg_entry_state: aligned/opposite/unknown; mg_entry_reason: exact validator
      reason (valid, missing_management, stale_management, invalid_management,
      nonfinite_management, unknown_management_segment, missing_source,
      source_segment_change, invalid_completed_source, invalid_source_open,
      invalid_entry_time, unaligned_entry_time, invalid_entry_direction).
    * mg_entry_bar_open / available_at: selected exact native open/close, NaT
      if no candidate; candidate timestamps may remain when validation fails.
    * mg_entry_known: state is aligned or opposite, never inferred from nonnull
      diagnostics alone. mg_entry_ma / hl2: that candidate's supplied MA and
      native(high+low)/2, or NaN when absent/unparseable. Invalid/nonfinite
      candidate values remain diagnostic; they cannot make known=True.
    * mg_entry_management_segment_id: selected candidate's own segment ID;
      mg_entry_raw_segment_id: raw segment at that candidate OPEN, or missing.
      Both may remain for unknown-source diagnosis and are not forced equal.
    * mg_entry_native_minutes: the fixed5/15 management specification.

    No file access, raw fetching, outcome calculation, import of execution
    layers, entry selection or modification of the caller's data occurs here.
    """
    if requests.columns.duplicated().any():
        raise ValueError("Request columns must be unique")
    if (management_featured.attrs.get("ma_kind") != "SMA"
            or management_featured.attrs.get("ma_length") != 40
            or management_featured.attrs.get("bar_minutes") != management_minutes):
        raise ValueError("Native exit context requires labelled native SMA40 features")
    if set(SOURCE_COLUMNS).intersection(requests.columns):
        raise ValueError("Native exit context columns already exist; refuse overwrite")
    if "event_id" in requests and (requests.event_id.isna().any() or not requests.event_id.is_unique):
        raise ValueError("Request event_id must be unique and nonnull")
    result = attach_management_context(raw5, management_featured, requests, management_minutes)
    # Reindex only the exact candidates selected by the reviewed validator.
    # No fill method is supplied, so a missing newest candle stays missing.
    mg_times = pd.to_datetime(management_featured.open_time, utc=True, format="mixed")
    source_times = pd.to_datetime(raw5.open_time, utc=True, format="mixed")
    selected_times = pd.DatetimeIndex(result.mg_entry_bar_open)
    selected = management_featured[["ma", "high", "low"]].set_index(
        pd.DatetimeIndex(mg_times)).reindex(selected_times)
    ma = pd.to_numeric(selected.ma, errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(selected.high, errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(selected.low, errors="coerce").to_numpy(dtype=float)
    result["mg_entry_known"] = result.mg_entry_state.isin(["aligned", "opposite"]).to_numpy()
    result["mg_entry_ma"] = ma
    result["mg_entry_hl2"] = high / 2 + low / 2
    # Opaque IDs and missing rows must not coerce integer IDs through float or
    # change the diagnostic dtype when another request has unknown support.
    mg_segments = pd.Series(management_featured.segment_id.to_numpy(dtype=object),
                            index=pd.DatetimeIndex(mg_times), dtype=object)
    raw_segments = pd.Series(raw5.segment_id.to_numpy(dtype=object),
                             index=pd.DatetimeIndex(source_times), dtype=object)
    result["mg_entry_management_segment_id"] = mg_segments.reindex(selected_times).to_numpy(dtype=object)
    result["mg_entry_raw_segment_id"] = raw_segments.reindex(selected_times).to_numpy(dtype=object)
    result["mg_entry_native_minutes"] = int(management_minutes)
    return result
