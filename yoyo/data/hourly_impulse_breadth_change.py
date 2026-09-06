"""V22: causal adjacent-hour CHANGE of the fixed V21 four-asset rank mean.

ChartPrime Multi Asset Histogram supplies the unchanged HL2 rank50 formula:
sum(+1 if current HL2 >= lagged HL2 else -1, lag=1..50), including bullish
ties. Source KkoxM97D, Pine v5 lines31--35, SHA256
58d49892627a886094b269c7b9d7ac15ae9ba1c0844696fc0cd85ab7856b3ae5.
https://www.tradingview.com/script/KkoxM97D-Multi-Asset-Histogram-ChartPrime/

Consume saved V21 native-hour trace ranks only, without I/O or recalculating
raw5 aggregation. Exact clocks, contiguous history and rank shape are checked;
source-byte provenance and independent HL2->rank replay belong to the caller's
freeze/audit. CHANGE is one fixed research adaptation, not the source's live
ten-asset display or V21's absolute-direction gate. Positive scaling by1/2
merely keeps breadth_score in [-1,1] for existing bounded bookkeeping.

pandas2.3.3 explicit UTC conversion and copying contract:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.copy.html
"""
from __future__ import annotations

from numbers import Real

import numpy as np
import pandas as pd


BREADTH_SYMBOLS = ("ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
HOUR = pd.Timedelta(hours=1)
TRACE_COLUMNS = ("symbol", "open_time", "open", "high", "low", "close", "volume",
                 "hl2", "trscore", "count", "window_start", "available_at", "segment_id")
ASSET_FIELDS = ("score", "available_at", "window_start", "count", "segment_id", "bar_open")
BREADTH_CHANGE_COLUMNS = [
    "breadth_%s_%s%s" % (symbol, prefix, field)
    for symbol in BREADTH_SYMBOLS for prefix in ("", "previous_") for field in ASSET_FIELDS
] + ["breadth_raw_sum_change", "breadth_change", "breadth_mean_now", "breadth_mean_previous",
     "breadth_score", "breadth_known", "breadth_gate_state", "breadth_reason",
     "breadth_cutoff", "breadth_available_at", "breadth_source_count"]


def _utc(values, *, nullable=False):
    result = []
    for value in values:
        if pd.isna(value):
            if not nullable:
                raise ValueError("Finite timezone-aware clocks required")
            result.append(pd.NaT)
            continue
        if isinstance(value, (int, float, np.number, bool)):
            raise ValueError("Explicit timezone-aware clocks required; no numeric units")
        try:
            time = pd.Timestamp(value)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError("Valid timezone-aware clocks required") from error
        if time.tzinfo is None:
            raise ValueError("Explicit timezone-aware clocks required")
        result.append(time.tz_convert("UTC"))
    return pd.Series(result, dtype="datetime64[ns, UTC]")


def _numbers(values, *, nullable=False):
    if values.map(lambda x: isinstance(x, (bool, np.bool_))).any():
        raise ValueError("Boolean trace numbers forbidden")
    try:
        result = pd.to_numeric(values, errors="raise").astype(float)
    except (ValueError, TypeError) as error:
        raise ValueError("Numeric trace metadata required") from error
    allowed = np.isfinite(result.to_numpy()) | (result.isna().to_numpy() if nullable else False)
    if not allowed.all():
        raise ValueError("Finite trace metadata required")
    return result


def _validated_trace(trace, cutoff):
    """Validate native-hour metadata only through cutoff; never use future ranks.

    All source open clocks are structurally checked, even in a future suffix.
    OHLCV/HL2 are carried source evidence, not consumed or independently
    recomputed here. A segment cannot cross a missing hour or recur after exit.
    Counts begin at1 and reset at each new contiguous segment. A finite even
    rank in [-50,50] exists iff count>=51; otherwise rank/window_start are NA.
    """
    if (not isinstance(trace, pd.DataFrame) or trace.columns.duplicated().any()
            or set(trace.columns) != set(TRACE_COLUMNS)):
        raise ValueError("Exact saved V21 trace schema required")
    if not trace.symbol.isin(BREADTH_SYMBOLS).all():
        raise ValueError("Only fixed ETH/SOL/BNB/XRP trace assets allowed")
    times = _utc(trace.open_time)
    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("Trace must contain exact native UTC hour starts")
    clocks = pd.DataFrame({"symbol": trace.symbol.to_numpy(), "open_time": times.array})
    for _, part in clocks.groupby("symbol", sort=False):
        if not part.open_time.is_unique or not part.open_time.is_monotonic_increasing:
            raise ValueError("Each asset trace must be unique and chronological")
    positions = np.flatnonzero((times+HOUR).le(cutoff).to_numpy())
    prefix = trace.iloc[positions].copy().reset_index(drop=True)
    prefix["open_time"] = times.iloc[positions].array
    prefix["available_at"] = _utc(prefix.available_at).array
    prefix["window_start"] = _utc(prefix.window_start, nullable=True).array
    for field in ("count", "segment_id", "trscore"):
        prefix[field] = _numbers(prefix[field], nullable=field == "trscore")
    if (not prefix.available_at.eq(prefix.open_time+HOUR).all()
            or not prefix["count"].ge(1).all() or not prefix["count"].mod(1).eq(0).all()
            or not prefix.segment_id.ge(0).all() or not prefix.segment_id.mod(1).eq(0).all()):
        raise ValueError("Trace count/segment/availability invalid")
    for _, part in prefix.groupby("symbol", sort=False):
        previous_time = previous_segment = None
        count = 0
        seen = set()
        for row in part.itertuples(index=False):
            segment = int(row.segment_id)
            change = previous_segment is None or segment != previous_segment
            if change:
                if segment in seen:
                    raise ValueError("Trace segment cannot recur after a reset")
                seen.add(segment)
                count = 0
            elif row.open_time != previous_time+HOUR:
                raise ValueError("Missing trace hour must reset segment/history")
            count += 1
            if row.count != count:
                raise ValueError("Trace count must equal complete contiguous history")
            known = count >= 51
            if known:
                if (not np.isfinite(row.trscore) or row.trscore % 2 != 0
                        or not -50 <= row.trscore <= 50
                        or pd.isna(row.window_start) or row.window_start != row.open_time-50*HOUR):
                    raise ValueError("Known rank requires even[-50,50] and exact51-hour window")
            elif not pd.isna(row.trscore) or not pd.isna(row.window_start):
                raise ValueError("Warmup must not invent a rank/window")
            previous_time, previous_segment = row.open_time, segment
    return {symbol: prefix.loc[prefix.symbol.eq(symbol)].set_index("open_time") for symbol in BREADTH_SYMBOLS}


def add_breadth_change_context(requests: pd.DataFrame, trace: pd.DataFrame) -> pd.DataFrame:
    """Preserve all requests and attach one entry-known breadth CHANGE gate.

    Required request fields: globally unique nonempty string event_id, aware
    UTC-hour signal_time=T (K1 OPEN), decision_time=T+1h, real +/-1 direction.
    Existing breadth_*/structure_* fields are forbidden: no overwrite/stack.
    Original order, columns, duplicate index labels and attrs stay unchanged.

    Each asset uses EXACT current open T-1h (available T) and previous open
    T-2h (available T-1h), never asof/ffill. Both rank50 observations require
    one same contiguous segment: current count>=52, previous count>=51;
    their union is52 complete native hours. No K1-hour external price is used.

    Per-asset breadth_<SYMBOL>_{score,available_at,window_start,count,
    segment_id,bar_open} describes the current observation; insert previous_
    before those suffixes for the prior observation. Missing count=0, other
    missing metadata=NA. Existing warmup clocks/counts remain observable.
    breadth_source_count counts assets with BOTH complete consecutive ranks.

    If all four pairs are known, raw_sum_change=sum(now ranks)-sum(prior ranks)
    is computed in INTEGER arithmetic. breadth_raw_sum_change is nullable Int64
    [-400,400]; breadth_change=raw/200 in[-2,2]; breadth_score=raw/400 in[-1,1].
    The latter is positive scaling for bounded bookkeeping only, not another
    filter or V21 absolute score. Diagnostic breadth_mean_now/previous are
    respective four-score sums/200. All these aggregate values are NA unless
    the full four-pair support is known. Gate uses direction*raw>0, never a
    subtraction of separately normalized floats. Known zero/opposite abstain.

    breadth_known/gate_state/reason are explicit; reasons known, neutral,
    missing_external_hour, source_gap, insufficient_history. Missing exact
    observations take precedence over segment/warmup support. breadth_cutoff
    is always T, breadth_available_at=T only when known else NaT. Empty
    requests return a fixed empty output without inspecting the trace.
    """
    required = {"event_id", "signal_time", "decision_time", "direction"}
    if (not isinstance(requests, pd.DataFrame) or requests.columns.duplicated().any()
            or not required.issubset(requests)):
        raise ValueError("Unique request columns and identity/clock/direction required")
    if any(str(name).startswith(("breadth_", "structure_")) for name in requests.columns):
        raise ValueError("Existing breadth/structure columns would stack or overwrite")
    if not requests.event_id.is_unique or not requests.event_id.map(lambda x: isinstance(x, str) and bool(x.strip())).all():
        raise ValueError("Globally unique nonempty event IDs required")
    times, decisions = _utc(requests.signal_time), _utc(requests.decision_time)
    if not times.eq(times.dt.floor("h")).all() or not decisions.eq(times+HOUR).all():
        raise ValueError("Own K1 exact-hour open and next-hour decision required")
    directions = requests.direction.reset_index(drop=True)
    if (not directions.isin((-1, 1)).all() or not directions.map(
            lambda x: isinstance(x, Real) and not isinstance(x, (bool, np.bool_))).all()):
        raise ValueError("Real +/-1 direction required; no bool")
    output = {}
    for name in BREADTH_CHANGE_COLUMNS:
        if name.endswith("count"):
            output[name] = [0]*len(requests)
        elif name.endswith(("available_at", "window_start", "bar_open")):
            output[name] = [pd.NaT]*len(requests)
        else:
            output[name] = [np.nan]*len(requests)
    output.update(breadth_known=[False]*len(requests), breadth_gate_state=["unknown"]*len(requests),
        breadth_reason=["missing_external_hour"]*len(requests), breadth_cutoff=times.tolist())
    if len(requests):
        assets = _validated_trace(trace, times.max())
        for i, cutoff in enumerate(times):
            now_scores, prior_scores = [], []
            missing = discontinuous = False
            for symbol, asset in assets.items():
                now, prior = None, None
                for prefix, opened in (("", cutoff-HOUR), ("previous_", cutoff-2*HOUR)):
                    if opened not in asset.index:
                        missing = True
                        continue
                    row = asset.loc[opened]
                    values = dict(score=row.trscore, available_at=row.available_at, window_start=row.window_start,
                                  count=int(row["count"]), segment_id=int(row.segment_id), bar_open=opened)
                    for field, value in values.items():
                        output["breadth_%s_%s%s" % (symbol, prefix, field)][i] = value
                    if prefix:
                        prior = row
                    else:
                        now = row
                if now is None or prior is None:
                    continue
                if now.segment_id != prior.segment_id:
                    discontinuous = True
                    continue
                if not np.isfinite(now.trscore) or not np.isfinite(prior.trscore):
                    continue
                if now["count"] != prior["count"]+1 or now["count"] < 52:
                    raise ValueError("Adjacent rank observations lack52-hour union")
                now_scores.append(int(now.trscore))
                prior_scores.append(int(prior.trscore))
            output["breadth_source_count"][i] = len(now_scores)
            if len(now_scores) != 4:
                output["breadth_reason"][i] = ("missing_external_hour" if missing else
                    "source_gap" if discontinuous else "insufficient_history")
                continue
            raw_change = sum(now_scores)-sum(prior_scores)
            output["breadth_known"][i] = True
            output["breadth_raw_sum_change"][i] = raw_change
            output["breadth_change"][i] = raw_change/200
            output["breadth_score"][i] = raw_change/400
            output["breadth_mean_now"][i] = sum(now_scores)/200
            output["breadth_mean_previous"][i] = sum(prior_scores)/200
            output["breadth_available_at"][i] = cutoff
            output["breadth_reason"][i] = "neutral" if raw_change == 0 else "known"
            output["breadth_gate_state"][i] = "accepted" if directions.iloc[i]*raw_change > 0 else "abstain"
    result = requests.copy()
    for name, values in output.items():
        if name.endswith(("available_at", "window_start", "bar_open")) or name == "breadth_cutoff":
            result[name] = pd.array(values, dtype="datetime64[ns, UTC]")
        elif name.endswith(("count", "segment_id")) or name == "breadth_raw_sum_change":
            result[name] = pd.array(values, dtype="Int64")
        elif name == "breadth_known":
            result[name] = np.asarray(values, dtype=bool)
        else:
            result[name] = values
    return result
