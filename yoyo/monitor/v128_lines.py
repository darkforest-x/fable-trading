"""SPIKE V12.8 line monitor adapter for closed OKX candles.

The line and ordered-pair rules retain the causal V12.6 engine used by the
delivered V12.8 Pine source.  V12.8 adds chart-only roll hints; those hints do
not create monitor events, orders, or position exits.  Python strict-pivot tie
semantics have not been verified against TradingView's native runtime, so this
module records that boundary instead of claiming native parity.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as recent
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v10_4 import reference_long_exits
from yoyo.evaluation.spike_v9_htf_sma import side_gate
from yoyo.evaluation.spike_v7_fast import _data_gap
from yoyo.evaluation.spike_v126_engine import VERSION as ENGINE_VERSION, line_events, pair_events
from yoyo.monitor import spike_lines as legacy


PROTOCOL = "spike-v128-lines-monitor-v1"
VERSION = "spike-v128-lines-monitor-20260923-v1"
PINE_SOURCE = "spike_burst_v12_8.pine"
PINE_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "pine" / PINE_SOURCE
SOURCE_SHA256 = hashlib.sha256(PINE_PATH.read_bytes()).hexdigest()
MINUTES = dict(legacy.MINUTES)
BREAK_TIMEFRAMES = tuple(legacy.BREAK_TIMEFRAMES)
JOINT_TIMEFRAMES = tuple(legacy.JOINT_TIMEFRAMES)
HIGHER = dict(legacy.HIGHER)
TAIL_BARS = legacy.TAIL_BARS
TRACK = dict(legacy.TRACK)
ROUND_TRIP_COST = legacy.ROUND_TRIP_COST
# Uses the original V9 ATR reference exit replay and its fixed 0.2% round trip
# cost.  Pine V12.8 contains no RSI-based early-exit rule.
BASIS = "v12_8_box_joint_original_v9_exit_next_open_serial_net_of_round_trip_cost"
PARITY_NOTE = (
    "Causal V12.6 Python line-engine port; TradingView native strict-pivot tie "
    "semantics remain unverified."
)


def frame_of(candles: list[dict]) -> pd.DataFrame:
    """Closed candles ``t/o/h/l/c/v`` (t = bar open, ms) to a UTC OHLCV frame."""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    raw = pd.DataFrame(candles)
    frame = pd.DataFrame({"open": raw.o.astype(float), "high": raw.h.astype(float), "low": raw.l.astype(float),
                          "close": raw.c.astype(float), "volume": raw.v.astype(float)})
    frame.index = pd.DatetimeIndex(pd.to_datetime(raw.t.astype("int64"), unit="ms", utc=True))
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    return frame.iloc[-TAIL_BARS:]


def complete_buckets(frame: pd.DataFrame, source_minutes: int, minutes: int) -> pd.DataFrame:
    """UTC-epoch aggregation retaining buckets made from every source bar."""
    if source_minutes < 1 or minutes < source_minutes or minutes % source_minutes:
        raise ValueError("minutes must be a positive multiple of source_minutes")
    if frame.empty:
        return frame
    grouped = frame.resample(f"{minutes}min", origin="epoch", label="left", closed="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    count = grouped.close.count()
    return out.loc[count == minutes // source_minutes]


def event_id(kind: str, symbol: str, timeframe: str, bar_open_ms: int) -> str:
    """Stable event key namespaced by the V12.8 protocol."""
    return hashlib.sha1(f"{PROTOCOL}|{kind}|{symbol}|{timeframe}|{bar_open_ms}".encode()).hexdigest()[:24]


def _ms(stamp: pd.Timestamp) -> int:
    return int(stamp.value // 1_000_000)


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _h1_sma60_at_chart_open(higher: pd.DataFrame | None, chart_index: pd.DatetimeIndex) -> np.ndarray:
    """Align the last fresh, completed H1 SMA60 known at each chart bar OPEN.

    Inputs are completed native H1 OHLCV candles and chart bar-open timestamps.
    Sixty consecutive valid H1 closes are required.  A value is visible only
    inside the UTC hour immediately following its close, matching Pine's
    ``SMA[1]``/``lookahead_on`` gate and its stale-hour refusal.
    """
    out = np.full(len(chart_index), np.nan)
    if higher is None or higher.empty or len(chart_index) == 0:
        return out
    if not isinstance(higher.index, pd.DatetimeIndex) or higher.index.tz is None:
        raise ValueError("higher candles require a timezone-aware DatetimeIndex")
    if not higher.index.is_monotonic_increasing or not higher.index.is_unique:
        raise ValueError("higher candles require an ordered unique clock")
    if (higher.index.asi8 % pd.Timedelta(hours=1).value).any():
        raise ValueError("15m direction gate requires UTC-hour-aligned H1 candles")
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(higher.columns):
        raise ValueError("higher candles require OHLCV columns")

    raw = higher[required].astype(float)
    valid = (np.isfinite(raw.to_numpy()).all(axis=1) & raw.low.gt(0).to_numpy()
             & raw.high.ge(raw[["open", "close", "low"]].max(axis=1)).to_numpy()
             & raw.low.le(raw[["open", "close", "high"]].min(axis=1)).to_numpy()
             & raw.volume.ge(0).to_numpy())
    closes = raw.close.where(valid)
    sma = closes.rolling(60, min_periods=60).mean().to_numpy(float)

    contiguous = np.zeros(len(raw), dtype=np.int64)
    for i, is_valid in enumerate(valid):
        if not is_valid:
            continue
        contiguous[i] = (contiguous[i - 1] + 1
                         if i > 0 and valid[i - 1]
                         and higher.index[i] - higher.index[i - 1] == pd.Timedelta(hours=1)
                         else 1)
    h1_close_ns = higher.index.asi8 + pd.Timedelta(hours=1).value
    chart_ns = chart_index.asi8
    pos = h1_close_ns.searchsorted(chart_ns, side="right") - 1
    safe = np.maximum(pos, 0)
    age = chart_ns - h1_close_ns[safe]
    expected_hour_open = chart_index.floor("h").asi8
    available = ((pos >= 0) & (age >= 0) & (age < pd.Timedelta(hours=1).value)
                 & (h1_close_ns[safe] == expected_hour_open)
                 & (contiguous[safe] >= 60) & np.isfinite(sma[safe]))
    out[available] = sma[safe[available]]
    return out


def _facts(chart: pd.DataFrame, timeframe: str, *, tick: float, asset: str,
           higher: pd.DataFrame | None) -> dict:
    """Build causal V12.8 V9/box facts, adding its 15m-only H1 admission gate."""
    minutes = MINUTES[timeframe]
    facts = recent._generic_facts(chart, asset, tick, minutes)
    if timeframe != "15m":
        return facts

    frame = facts["frame"]
    h1_sma = _h1_sma60_at_chart_open(higher, frame.index)
    allowed = side_gate(frame.close.to_numpy(float), facts["side"], h1_sma)
    v9 = np.asarray(facts["v9"], dtype=bool) & allowed
    box: dict = {}
    ref_exit = reference_long_exits(
        frame.high, frame.low, frame.close, frame.atr, ready=facts["ready"], gap=facts["gap"],
        raw_side=facts["side"], signal_side=np.where(v9, facts["side"], 0), tick=tick, state=box,
    )
    return {**facts, "v9": v9, "v9_long": v9 & (facts["side"] == 1),
            "box": box, "ref_long_exit": ref_exit, "h1_sma60": h1_sma}


def _base(kind: str, i: int, *, timeframe: str, frame: pd.DataFrame, minutes: int, tick: float) -> dict:
    step = pd.Timedelta(minutes=minutes)
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "source_version": PINE_SOURCE,
        "source_sha256": SOURCE_SHA256,
        "engine_version": ENGINE_VERSION,
        "native_parity": False,
        "pivot_tie_status": "unverified_tradingview_native_semantics",
        "parity_note": PARITY_NOTE,
        "kind": kind,
        "timeframe": timeframe,
        "bar_open_ms": _ms(frame.index[i]),
        "bar_close_ms": _ms(frame.index[i] + step),
        "close": float(frame.close.iloc[i]),
        "atr": _num(frame.atr.iloc[i]),
        "reference_stop": legacy.reference_stop(frame, i, float(tick)),
        "tick": float(tick),
    }


def _chart_line(event: dict, frame: pd.DataFrame, *, timeframe: str, minutes: int,
                at_i: int | None = None) -> dict:
    ax, bx, cx = int(event["ax"]), int(event["bx"]), int(event["cx"])
    break_i = int(event.get("break_i", event.get("i")))
    line_i = break_i if at_i is None else at_i
    y = float(event["ap"]) + (float(event["bp"]) - float(event["ap"])) * ((line_i - ax) / (bx - ax))
    step = pd.Timedelta(minutes=minutes)
    source = str(event.get("structure_source", event.get("source", "")))
    if source not in ("raw", "soft"):
        source = {0: "raw", 1: "soft"}.get(int(event.get("source_id", -1)), "")
    return {
        "line_timeframe": timeframe,
        "track": TRACK.get({"raw": 0, "soft": 1}.get(source, -1), ""),
        "line_source": source,
        "line_kind": str(event.get("kind", "three_major")),
        "line_uid": int(event["uid"]),
        "a_ms": _ms(frame.index[ax]), "a_price": float(event["ap"]),
        "b_ms": _ms(frame.index[bx]), "b_price": float(event["bp"]),
        "c_ms": _ms(frame.index[cx]), "c_price": float(event["cp"]),
        "born_close_ms": _ms(frame.index[int(event["born_i"])] + step),
        "break_open_ms": _ms(frame.index[break_i]),
        "break_close_ms": _ms(frame.index[break_i] + step),
        "line_at_bar": y,
    }


def _higher_line(event: dict, higher: pd.DataFrame, *, timeframe: str, minutes: int) -> dict:
    ax, bx, cx = int(event["ax"]), int(event["bx"]), int(event["cx"])
    break_i = int(event.get("break_i", event.get("i")))
    step = pd.Timedelta(minutes=minutes)
    source = str(event.get("structure_source", event.get("source", "")))
    if source not in ("raw", "soft"):
        source = {0: "raw", 1: "soft"}.get(int(event.get("source_id", -1)), "")
    return {
        "line_timeframe": timeframe,
        "track": TRACK.get({"raw": 0, "soft": 1}.get(source, -1), ""),
        "line_source": source,
        "line_kind": str(event.get("kind", "three_major")),
        "line_uid": int(event["uid"]),
        "a_ms": _ms(higher.index[ax]), "a_price": float(event["ap"]),
        "b_ms": _ms(higher.index[bx]), "b_price": float(event["bp"]),
        "c_ms": _ms(higher.index[cx]), "c_price": float(event["cp"]),
        "born_close_ms": _ms(higher.index[int(event["born_i"])] + step),
        "break_open_ms": _ms(higher.index[break_i]),
        "break_close_ms": _ms(higher.index[break_i] + step),
    }


def _joint_positions(frame: pd.DataFrame, facts: dict, joints: list[dict], *, timeframe: str,
                     tick: float) -> dict[int, dict]:
    fired = np.zeros(len(frame), dtype=bool)
    for event in joints:
        fired[int(event["joint_i"])] = True
    positions = legacy.positions(frame, facts, fired, minutes=MINUTES[timeframe], tick=tick,
                                 rsi_exit_enabled=False)
    for position in positions.values():
        position["basis"] = BASIS
        position["performance_version"] = BASIS
        position["exit_source"] = "pine_v12_8_original_v9_atr_reference_exit"
        position["rsi_exit"] = "not_present_in_pine_v12_8"
        position["round_trip_cost"] = ROUND_TRIP_COST
    return positions


def analyze(chart: pd.DataFrame, timeframe: str, *, tick: float, asset: str | None,
            higher: pd.DataFrame | None = None, want_breaks: bool = True, want_joints: bool = True,
            rsi_exit_enabled: bool = False, rsi_features: pd.DataFrame | None = None) -> dict:
    """Return V12.8 break and ordered joint observations on closed bars.

    The call shape preserves the former monitor adapter.  For 15m, ``higher``
    must be the native H1 stream: it supplies both the confirmed SMA60 entry
    gate and H1 joint geometry.  The other supported charts have no added
    direction gate.  RSI arguments remain accepted for compatibility, but a
    true RSI override is rejected because Pine V12.8 has no such exit.
    """
    if isinstance(tick, bool) or not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    if timeframe not in MINUTES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if rsi_exit_enabled or rsi_features is not None:
        raise ValueError("Pine V12.8 does not define an RSI early exit")
    minutes = MINUTES[timeframe]
    if len(chart) < 200:
        return {"breaks": [], "joints": [], "bars": len(chart), "ready": False}

    facts = _facts(chart, timeframe, tick=float(tick), asset=asset or "", higher=higher)
    frame = facts["frame"]
    local = line_events(
        frame.open, frame.high, frame.low, frame.close, frame.atr,
        can_run=facts["can_run"], gap=facts["gap"], tick=float(tick),
        confirmed_long=facts["v9_long"], parent_high=facts["parent_high"], parent_low=facts["parent_low"],
        raw_side=facts["side"], long_alive=facts["long_alive"], ref_long_exit=facts["ref_long_exit"],
    )

    higher_timeframe = HIGHER.get(timeframe)
    higher_frame = None
    mapped_higher: list[dict] = []
    higher_engine = None
    if higher_timeframe is not None and higher is not None and len(higher):
        higher_minutes = MINUTES[higher_timeframe]
        higher_frame = features(higher)
        higher_gap = _data_gap(higher_frame, higher_minutes).to_numpy(bool)
        higher_engine = line_events(
            higher_frame.open, higher_frame.high, higher_frame.low, higher_frame.close, higher_frame.atr,
            can_run=~higher_gap & np.isfinite(higher_frame.atr) & higher_frame.atr.gt(0),
            gap=higher_gap, tick=float(tick), htf=True,
        )
        mapped_higher = recent._map_htf(higher_frame, frame, higher_engine.winner_events,
                                        minutes, higher_minutes)

    chart_clock = frame.index.asi8 // 60_000_000_000
    joints = pair_events(
        frame.close.to_numpy(float), bar_times=chart_clock,
        chart_breaks=[{**event, "structure_source": event.get("source")} for event in local.events],
        htf_breaks=[{**event, "structure_source": event.get("source")} for event in mapped_higher],
        box_id=facts["box"]["box_entry"], confirmed_long=facts["v9_long"], gap=facts["gap"],
    ) if want_joints and timeframe in JOINT_TIMEFRAMES else []

    breaks: list[dict] = []
    if want_breaks:
        # Pine's break signal is one score-best winner for each confirmed bar.
        for event in local.winner_events:
            i = int(event.get("i", event["break_i"]))
            breaks.append({**_base("break", i, timeframe=timeframe, frame=frame, minutes=minutes, tick=tick),
                           **_chart_line(event, frame, timeframe=timeframe, minutes=minutes),
                           "structure_source": event.get("source"), "structure_kind": event.get("kind"),
                           "structure_score": float(event["score"])})

    joint_rows: list[dict] = []
    if joints:
        performance = _joint_positions(frame, facts, joints, timeframe=timeframe, tick=float(tick))
        for event in joints:
            i = int(event["joint_i"])
            box_i = int(event["box_entry_i"])
            pair_source = str(event["pair_source"])
            source = "higher" if pair_source == "htf" else "chart"
            record = {
                **_base("joint", i, timeframe=timeframe, frame=frame, minutes=minutes, tick=tick),
                "source": source,
                "pair_source": pair_source,
                "pair_order": str(event["order"]),
                "higher_timeframe": higher_timeframe if source == "higher" else None,
                "v9_signal_open_ms": _ms(frame.index[box_i]),
                "v9_signal_close_ms": _ms(frame.index[box_i] + pd.Timedelta(minutes=minutes)),
                "v9_signal_close": float(frame.close.iloc[box_i]),
                "bars_after_v9": i - box_i,
                "side": "long",
                "performance": performance[i],
                "structure_source": event.get("structure_source", event.get("source")),
                "structure_kind": event.get("kind"),
                "structure_score": float(event["score"]),
            }
            if source == "higher":
                if higher_frame is not None:
                    record["higher_line"] = _higher_line(
                        event, higher_frame, timeframe=str(higher_timeframe), minutes=MINUTES[str(higher_timeframe)])
            else:
                record.update(_chart_line(event, frame, timeframe=timeframe, minutes=minutes, at_i=i))
            joint_rows.append(record)
    return {"breaks": breaks, "joints": joint_rows, "bars": len(frame), "ready": True}


__all__ = [
    "PROTOCOL", "VERSION", "PINE_SOURCE", "SOURCE_SHA256", "BASIS", "MINUTES", "HIGHER",
    "BREAK_TIMEFRAMES", "JOINT_TIMEFRAMES", "frame_of", "complete_buckets", "event_id", "analyze",
]
