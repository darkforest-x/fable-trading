# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://mozilla.org/MPL/2.0/.
"""Fixed ex-BTC four-asset breadth, known at the START of each BTC K1.

Formula attribution: ChartPrime, Multi Asset Histogram, Pine v5 public source
KkoxM97D, lines 10-12 and 31-35, version 1, SHA256
58d49892627a886094b269c7b9d7ac15ae9ba1c0844696fc0cd85ab7856b3ae5.
https://www.tradingview.com/script/KkoxM97D-Multi-Asset-Histogram-ChartPrime/
https://pine-facade.tradingview.com/pine-facade/get/PUB%3B8de20fa748994adaa65e52f23835b475/1

Current HL2 receives +1 for each lag1..50 whose HL2 is <= current, else -1.
Ties are bullish, as in the source. Intentional research differences: require
51 complete contiguous native UTC hours (NO source nz warmup filling), exactly
ETHUSDT/SOLUSDT/BNBUSDT/XRPUSDT and exclude BTC, normalize the unrounded mean by
50, and use the external hour ending at K1 OPEN. This is a source-based causal
variant, NOT parity with the source's ten assets or live request.security bars.

Pandas 2.3.3 rolling min_periods and Pine close/lag clock documentation:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Series.rolling.html
https://www.tradingview.com/pine-script-docs/language/execution-model/
https://www.tradingview.com/pine-script-docs/language/operators/#history-referencing-operator
"""
from __future__ import annotations

from collections.abc import Mapping
from numbers import Real

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, resample_complete


BREADTH_SYMBOLS = ("ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
LOOKBACK = 50
HOUR = pd.Timedelta(hours=1)
ASSET_FIELDS = ("score", "available_at", "window_start", "count")
BREADTH_COLUMNS = [
    "breadth_%s_%s" % (symbol, field)
    for symbol in BREADTH_SYMBOLS for field in ASSET_FIELDS
] + ["breadth_known", "breadth_score", "breadth_gate_state", "breadth_reason",
     "breadth_available_at", "breadth_cutoff", "breadth_source_count"]
TRACE_COLUMNS = ["symbol"] + BAR_COLUMNS + [
    "hl2", "trscore", "count", "window_start", "available_at", "segment_id",
]


def _utc(values: pd.Series) -> pd.Series:
    """Do not guess epoch units/timezones or silently reorder the source."""
    output = []
    for value in values:
        if isinstance(value, (int, float, np.number, bool)):
            raise ValueError("Explicit timezone-aware timestamps required")
        try:
            time = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Finite timezone-aware timestamps required") from error
        if pd.isna(time) or time.tzinfo is None:
            raise ValueError("Finite timezone-aware timestamps required")
        output.append(time.tz_convert("UTC"))
    return pd.Series(output, dtype="datetime64[ns, UTC]")


def _empty_trace() -> pd.DataFrame:
    result = pd.DataFrame({name: pd.Series(dtype="float64") for name in TRACE_COLUMNS})
    result["symbol"] = pd.Series(dtype=object)
    for name in ("open_time", "window_start", "available_at"):
        result[name] = pd.Series(dtype="datetime64[ns, UTC]")
    for name in ("count", "segment_id"):
        result[name] = pd.Series(dtype="int64")
    return result


def _asset_trace(raw: pd.DataFrame, symbol: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Use raw OHLCV strictly before cutoff; complete hours only, no imputation."""
    if not isinstance(raw, pd.DataFrame) or raw.columns.duplicated().any() or not set(BAR_COLUMNS).issubset(raw):
        raise ValueError("%s requires unique raw5 OHLCV columns" % symbol)
    times = _utc(raw.open_time)
    if (not times.is_unique or not times.is_monotonic_increasing
            or not times.eq(times.dt.floor("5min")).all()):
        raise ValueError("%s raw clock must be chronological unique five-minute starts" % symbol)
    positions = np.flatnonzero(times.lt(cutoff).to_numpy())
    prefix = raw.iloc[positions][BAR_COLUMNS].copy()
    prefix["open_time"] = times.iloc[positions].array
    if prefix[BAR_COLUMNS[1:]].map(lambda x: isinstance(x, (bool, np.bool_))).any().any():
        raise ValueError("%s raw OHLCV must be numeric, not bool" % symbol)
    # Unique UTC5m timestamps plus the existing exact12 count ensure complete
    # native hours. Its timestamp-derived segment IDs restart after dropped
    # incomplete hours, irrespective of supplied raw segment labels.
    hourly = resample_complete(prefix, 60)
    if hourly.empty:
        return _empty_trace()
    hourly["symbol"] = symbol
    hourly["hl2"] = (hourly.high + hourly.low) / 2
    if not np.isfinite(hourly.hl2.to_numpy(dtype=float)).all():
        raise ValueError("%s derived HL2 must be finite" % symbol)
    hourly["count"] = hourly.groupby("segment_id", sort=False).cumcount() + 1
    hourly["trscore"] = hourly.groupby("segment_id", sort=False).hl2.transform(
        lambda values: values.rolling(LOOKBACK + 1, min_periods=LOOKBACK + 1).apply(
            lambda window: float(2 * np.count_nonzero(window[-1] >= window[:-1]) - LOOKBACK),
            raw=True))
    hourly["available_at"] = hourly.open_time + HOUR
    hourly["window_start"] = (hourly.open_time - LOOKBACK * HOUR).where(hourly.trscore.notna())
    return hourly[TRACE_COLUMNS]


def add_breadth_context(requests: pd.DataFrame, raw_by_symbol: Mapping) -> tuple:
    """Return (all original requests + breadth fields, long native-hour trace).

    Required request columns: globally unique nonempty string event_id,
    signal_time (explicit timezone-aware exact UTC hour K1 OPEN), decision_time
    (exactly signal_time+1h), and real +/-1 direction, never bool. Keep original
    columns/index/attrs/order, including duplicate index labels and requests at
    the same time. No BTC price, signal_close, MA, structure or outcome is read.
    Existing breadth_* OR structure_* columns are rejected to prevent stacking.

    raw_by_symbol must have exactly the four BREADTH_SYMBOLS keys. Each frame
    has explicit chronological unique UTC5m starts and finite valid OHLCV.
    Only OHLCV with raw open_time < max(request.signal_time) is selected and
    validated. Global raw timestamps are nevertheless validated, including the
    future suffix. Missing/incomplete hours are not filled; the next complete
    hour restarts the 51-hour history. No supplied raw segment label is used.

    Each request selects EXACT external open_time=signal_time-1h, whose
    availability is signal_time, one hour before the BTC entry decision.
    There is NO asof/ffill fallback and no use of the external hour coinciding
    with K1. A score needs current HL2 plus all 50 prior contiguous hourly HL2s.
    Scores are integers [-50,50] in increments of 2; no rounding of the mean.
    Four known scores are required. breadth_score=sum(scores)/(4*50), and the
    gate accepts iff direction*breadth_score>0. A known zero is neutral abstain,
    not unknown. Any missing score makes breadth_score/available_at unavailable.

    Per-asset breadth_<SYMBOL>_score is NaN unless fully warmed; available_at
    records the exact selected hour's close even during warmup (NaT if absent),
    window_start is the first OPEN of the full51 window only when known, and
    count is consecutive complete hours since reset (0 if the exact row is
    absent; never an invented score). breadth_source_count counts known scores,
    not merely present hours. breadth_cutoff is always the required signal_time;
    breadth_available_at equals it only when all four scores are known.
    Reasons: known (nonzero known breadth), neutral, missing_external_hour, or
    insufficient_history. Missing exact hours take precedence over warmup.

    Trace is ordered by BREADTH_SYMBOLS then hour, contains only complete native
    hours with availability <= max(signal_time), and carries raw-derived OHLCV,
    HL2/trscore/count/window_start/available_at/hourly segment_id for auditing.
    Empty requests return a fixed empty schema/trace without inspecting prices.
    No files, markets, outcomes, tests of profitability, or tuning are accessed.
    """
    required = {"event_id", "signal_time", "decision_time", "direction"}
    if not isinstance(requests, pd.DataFrame) or requests.columns.duplicated().any() or not required.issubset(requests):
        raise ValueError("Unique request event_id/signal_time/decision_time/direction required")
    if any(str(name).startswith(("breadth_", "structure_")) for name in requests.columns):
        raise ValueError("Existing breadth/structure columns: refuse stacked or overwritten gate")
    if (not requests.event_id.is_unique or not requests.event_id.map(
            lambda value: isinstance(value, str) and bool(value.strip())).all()):
        raise ValueError("event_id must be globally unique nonempty strings")
    times = _utc(requests.signal_time)
    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("signal_time must be the exact UTC hour OPEN")
    if not _utc(requests.decision_time).eq(times + HOUR).all():
        raise ValueError("decision_time must equal own signal_time+1h")
    direction = requests.direction.reset_index(drop=True)
    if not direction.isin([-1, 1]).all() or not direction.map(
            lambda x: isinstance(x, Real) and not isinstance(x, (bool, np.bool_))).all():
        raise ValueError("Each direction must be real +1/-1, not bool")
    if not isinstance(raw_by_symbol, Mapping) or set(raw_by_symbol) != set(BREADTH_SYMBOLS):
        raise ValueError("Exactly ETHUSDT/SOLUSDT/BNBUSDT/XRPUSDT required; BTC excluded")
    context = requests.copy()
    for symbol in BREADTH_SYMBOLS:
        context["breadth_%s_score" % symbol] = np.nan
        for field in ("available_at", "window_start"):
            context["breadth_%s_%s" % (symbol, field)] = pd.array([pd.NaT] * len(context), dtype="datetime64[ns, UTC]")
        context["breadth_%s_count" % symbol] = pd.array([0] * len(context), dtype="Int64")
    context["breadth_known"] = False
    context["breadth_score"] = np.nan
    context["breadth_gate_state"] = "unknown"
    context["breadth_reason"] = "missing_external_hour"
    context["breadth_available_at"] = pd.array([pd.NaT] * len(context), dtype="datetime64[ns, UTC]")
    context["breadth_cutoff"] = times.array
    context["breadth_source_count"] = pd.array([0] * len(context), dtype="Int64")
    if context.empty:
        return context, _empty_trace()
    parts = [_asset_trace(raw_by_symbol[symbol], symbol, times.max()) for symbol in BREADTH_SYMBOLS]
    nonempty = [part for part in parts if not part.empty]
    trace = pd.concat(nonempty, ignore_index=True) if nonempty else _empty_trace()
    assets = {symbol: part.set_index("open_time") for symbol, part in zip(BREADTH_SYMBOLS, parts)}
    output = {name: context[name].tolist() for name in BREADTH_COLUMNS}
    for position, cutoff in enumerate(times):
        scores = []
        missing = False
        for symbol in BREADTH_SYMBOLS:
            asset = assets[symbol]
            if cutoff - HOUR not in asset.index:
                missing = True
                continue
            row = asset.loc[cutoff - HOUR]
            for field, value in (("score", row.trscore), ("count", int(row["count"])),
                                 ("available_at", row.available_at), ("window_start", row.window_start)):
                output["breadth_%s_%s" % (symbol, field)][position] = value
            if np.isfinite(row.trscore):
                scores.append(float(row.trscore))
        output["breadth_source_count"][position] = len(scores)
        if len(scores) < len(BREADTH_SYMBOLS):
            output["breadth_reason"][position] = "missing_external_hour" if missing else "insufficient_history"
            continue
        score = sum(scores) / (len(BREADTH_SYMBOLS) * LOOKBACK)
        output["breadth_known"][position] = True
        output["breadth_score"][position] = score
        output["breadth_available_at"][position] = cutoff
        output["breadth_reason"][position] = "neutral" if score == 0 else "known"
        output["breadth_gate_state"][position] = "accepted" if direction.iloc[position] * score > 0 else "abstain"
    for name, data in output.items():
        if name.endswith(("available_at", "window_start")) or name == "breadth_cutoff":
            context[name] = pd.array(data, dtype="datetime64[ns, UTC]")
        elif name.endswith("count"):
            context[name] = pd.array(data, dtype="Int64")
        else:
            context[name] = data
    return context, trace
