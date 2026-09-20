"""SPIKE V11.2 trendline monitor: 突破 and 突破+spike on closed OKX bars.

Owner 2026-09-19: 「前端再加一个菜单栏 监控我们的最新的spike信号 只跟踪出了 突破➕spike信号的
包括 spike信号➕上级突破的 然后针对15min 1h 4h 1d只要出了突破信号直接监控 这两种用两个菜单」.

Two event kinds, computed with the exact research code the backtests used (no port):

* ``break``  - the chart's own V10.4 descending line closes above line + 0.2 ATR for the
  second bar (`spike_v10_4.joint_events` break winner). 15m / 1H / 4H / 1D.
* ``joint``  - V11.2 default rule 「多头框内」: while the chart's V9 long reference box is
  open, the first break seen (own line, or a higher-timeframe break visible on this
  bar) counts once per box (`spike_v10_4.box_joints`). 15m<-1H, 30m<-2H, 1H<-4H,
  4H<-1D, the Pine's automatic higher timeframe.

Every field of an event uses bars at or before that event's bar close; a higher bar is
only visible on the first chart bar opening at its close (`spike_v11_study.htf_inputs`).
Events are observations at the bar close, not fills; nothing here orders or notifies.

Joint cards carry a simulated position (owner 2026-09-19 「spike信号要和信号中心一样 模拟开仓 跟踪R」),
replayed with the engine the box-rule backtest used (`spike_v10_4_increment.attempt`,
`serial`): next-open entry, stop min(5-bar low - 0.2ATR, close - 2ATR), 4ATR close trail
armed at 2R, raw V9 short exits at the next open, 0.2% round trip, one position per
symbol/timeframe. An open position is marked at the last closed bar's close with the
same cost. The projection reads only bars after the signal and never feeds an event.
The V11.2 box rule backtested at -0.09R per trade (analysis/p1_spike_v11_box_joint_20260918.md),
so these cards are for watching, not an admitted strategy.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation import spike_joint_rsi_exit as rsi_exit
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v10_4 import V104Params, box_joints, joint_events, reference_long_exits

PROTOCOL = "spike-v11-2-lines-monitor-v1"
MINUTES = {"15m": 15, "30m": 30, "1H": 60, "2H": 120, "4H": 240, "1Dutc": 1440}
BREAK_TIMEFRAMES = ("15m", "1H", "4H", "1Dutc")
JOINT_TIMEFRAMES = ("15m", "30m", "1H", "4H")
HIGHER = {"15m": "1H", "30m": "2H", "1H": "4H", "4H": "1Dutc"}
# Lines look back 600 bars and V9's BB gate needs ~520; older bars cannot change
# a recent decision beyond indicator warm-up, and the tail bounds the CPU per cell.
TAIL_BARS = 1500
TRACK = {0: "完整影线", 1: "削尖影线"}
BASELINE_BASIS = "v11_2_box_joint_next_open_serial_net_of_round_trip_cost"
BASIS = "v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost"
PERFORMANCE_VERSION = BASIS
ROUND_TRIP_COST = ExecutionSpec.round_trip_cost


def frame_of(candles: list[dict]) -> pd.DataFrame:
    """Closed candles ``t/o/h/l/c/v`` (t = bar open, ms) -> UTC OHLCV frame."""
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
    """UTC-epoch aggregation keeping only buckets built from every source bar.

    The last bucket is usually still open (e.g. one 1H bar of a 2H bar); a partial
    bucket would be a bar that has not closed yet, so it is dropped, as are buckets
    with a missing source bar.
    """
    if frame.empty:
        return frame
    grouped = frame.resample(f"{minutes}min", origin="epoch", label="left", closed="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    count = grouped.close.count()
    return out.loc[count == minutes // source_minutes]


def _ms(stamp: pd.Timestamp) -> int:
    return int(stamp.value // 1_000_000)


def _num(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def event_id(kind: str, symbol: str, timeframe: str, bar_open_ms: int) -> str:
    return hashlib.sha1(f"{PROTOCOL}|{kind}|{symbol}|{timeframe}|{bar_open_ms}".encode()).hexdigest()[:24]


def reference_stop(frame: pd.DataFrame, i: int, tick: float) -> float | None:
    """V9's initial-stop formula at bar i's close: min(5-bar low - 0.2ATR, close - 2ATR), tick-floored."""
    atr = float(frame.atr.iloc[i])
    if not math.isfinite(atr) or atr <= 0 or i < 4:
        return None
    stop = min(float(frame.low.iloc[i - 4:i + 1].min()) - 0.2 * atr, float(frame.close.iloc[i]) - 2.0 * atr)
    return math.floor(stop / tick) * tick if stop > 0 else None


def _unopened(reason: str, *, basis: str, rsi: dict | None = None) -> dict:
    """A joint with no position (awaiting the next open, or one already running) has no R."""
    return {"status": "unknown", "reason": reason, "current_r": None, "exit_r": None, "peak_r": None,
            "stop_price": None, "trailing_active": False, "bars_held": 0, "basis": basis,
            "performance_version": basis, "round_trip_cost": ROUND_TRIP_COST, **(rsi or {})}


def _rsi_metadata(features: pd.DataFrame, mask: np.ndarray, *, minutes: int, as_of_i: int,
                  active: bool, trigger_i: int | None = None) -> dict:
    """Project one causal RSI counter snapshot without consulting later bars."""
    if not 0 <= as_of_i < len(features):
        side, count, known = 0, None, False
    else:
        row = features.iloc[as_of_i]
        side, known = int(row.last_strong_side), bool(row.counter_known)
        count = None if pd.isna(row.last_strong_run) else int(row.last_strong_run)
    result = {"rsi_exit_rule": dict(rsi_exit.RSI_EXIT_RULE), "rsi_timeframe_minutes": minutes,
              "rsi_run_side": side, "rsi_run_count": count, "rsi_counter_known": known,
              "rsi_exit_pending": bool(active and len(mask) and mask[-1]),
              "rsi_exit_trigger_close_ms": None}
    if trigger_i is not None:
        result["rsi_exit_trigger_close_ms"] = _ms(features.index[trigger_i] + pd.Timedelta(minutes=minutes))
    return result


def _position(trade: dict, frame: pd.DataFrame, step: pd.Timedelta, active: bool, *, basis: str,
              rsi_features: pd.DataFrame | None = None, rsi_mask: np.ndarray | None = None) -> dict:
    """Serialize one replayed joint trade like the V9 card projection."""
    entry, risk = _num(trade.get("entry_price")), _num(trade.get("initial_risk"))
    entry_i, exit_i = int(trade["entry_i"]), int(trade["exit_i"])
    initial_stop, protection = _num(trade.get("initial_stop")), _num(trade.get("protection"))
    if active:
        last = len(frame) - 1
        mark = float(frame.close.iloc[last])
        net_r = (mark - entry - ROUND_TRIP_COST * entry) / risk if entry and risk else None
        updated = _ms(frame.index[last] + step)
    else:
        net_r = _num(trade.get("net_r"))
        updated = _ms(pd.Timestamp(trade["exit_time"]) + step)
    status = "active" if active else ("unknown" if net_r is None else "profit" if net_r > 1e-9
                                      else "loss" if net_r < -1e-9 else "breakeven")
    reason = str(trade.get("exit_reason"))
    is_rsi_exit = reason == "rsi_seventh_reverse_next_open"
    rsi = {}
    if rsi_features is not None and rsi_mask is not None:
        trigger_i = int(trade["rsi_exit_trigger_i"]) if is_rsi_exit else None
        # Closed trades cannot consult their exit bar's close: an open or
        # intrabar fill has no claim to that future close.  An RSI exit's
        # confirmed trigger is exactly the last alive close.
        as_of_i = len(frame) - 1 if active else (trigger_i if trigger_i is not None else int(trade["exit_i"]) - 1)
        rsi = _rsi_metadata(rsi_features, rsi_mask, minutes=int(step / pd.Timedelta(minutes=1)),
                            as_of_i=as_of_i, active=active, trigger_i=trigger_i)
    return {"status": status, "current_r": net_r, "exit_r": None if active else net_r,
            "peak_r": _num(trade.get("mfe_r")), "entry_price": entry, "entry_time_ms": _ms(pd.Timestamp(trade["entry_time"])),
            "initial_stop": initial_stop, "initial_risk": risk, "stop_price": protection if active else initial_stop,
            "trailing_active": bool(active and protection is not None and initial_stop is not None
                                    and protection > initial_stop),
            "exit_price": None if active else _num(trade.get("exit_price")),
            "exit_time_ms": None if active else (_ms(pd.Timestamp(trade["exit_time"])) if is_rsi_exit else updated),
            "exit_time_precision": None if active else ("bar_open" if is_rsi_exit else "bar_close_legacy"),
            "exit_reason": None if active else reason,
            "stop_triggered": not active and reason.startswith(("initial_stop", "trailing_stop")),
            "bars_held": (len(frame) - 1 if active else exit_i) - entry_i, "updated_at_ms": updated,
            "mark": "last_closed_bar_close" if active else None, "basis": basis,
            "performance_version": basis, "round_trip_cost": ROUND_TRIP_COST, **rsi}


def positions(frame: pd.DataFrame, facts: dict, fired: np.ndarray, *, minutes: int, tick: float,
              rsi_exit_enabled: bool = False, rsi_features: pd.DataFrame | None = None) -> dict[int, dict]:
    """Serial replay of every joint (the backtest's `serial`), keyed by joint bar index."""
    identity = {"venue": "okx", "symbol": "", "asset": "", "timeframe": "", "timeframe_min": minutes}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], "okx:monitor", identity, minutes, tick)
    step = pd.Timedelta(minutes=minutes)
    basis = BASIS if rsi_exit_enabled else BASELINE_BASIS
    if rsi_exit_enabled:
        features = (rsi_exit.chartprime_strong_side(frame, gap=np.asarray(facts["gap"], dtype=bool), minutes=minutes)
                    if rsi_features is None else rsi_features)
        if not features.index.equals(frame.index):
            raise ValueError("rsi_features must align to the replay frame")
        rsi_mask = rsi_exit.rsi_exit_mask(features)
    else:
        features = None
        rsi_mask = None
    out: dict[int, dict] = {}
    flat_from = -1
    for i in np.flatnonzero(fired).tolist():
        if i < flat_from:
            rsi = (_rsi_metadata(features, rsi_mask, minutes=minutes, as_of_i=i, active=False)
                   if features is not None and rsi_mask is not None else None)
            out[i] = _unopened("serial_position_already_open", basis=basis, rsi=rsi)
            continue
        status, trade = (rsi_exit.attempt_with_rsi_exit(prepared, i, rsi_mask)
                         if rsi_mask is not None else inc.attempt(prepared, i))
        if trade is None:
            rsi = (_rsi_metadata(features, rsi_mask, minutes=minutes, as_of_i=i, active=False)
                   if features is not None and rsi_mask is not None else None)
            out[i] = _unopened({"no_next_bar": "awaiting_next_open"}.get(status, status), basis=basis, rsi=rsi)
        elif status == "closed":
            out[i] = _position(trade, frame, step, active=False, basis=basis,
                               rsi_features=features, rsi_mask=rsi_mask)
            flat_from = int(trade["exit_i"])
        elif status == "censored_boundary":
            out[i] = _position(trade, frame, step, active=True, basis=basis,
                               rsi_features=features, rsi_mask=rsi_mask)
            flat_from = len(frame) + 1
        else:
            rsi = (_rsi_metadata(features, rsi_mask, minutes=minutes, as_of_i=int(trade["exit_i"]) - 1, active=False)
                   if features is not None and rsi_mask is not None else None)
            out[i] = _unopened("data_gap_censored", basis=basis, rsi=rsi)
            flat_from = int(trade["exit_i"])
    return out


def analyze(chart: pd.DataFrame, timeframe: str, *, tick: float, asset: str | None,
            higher: pd.DataFrame | None = None, want_breaks: bool = True, want_joints: bool = True,
            rsi_exit_enabled: bool = True, rsi_features: pd.DataFrame | None = None) -> dict:
    """Break and joint events over one closed-bar prefix of one symbol/timeframe."""
    if isinstance(tick, bool) or not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    minutes = MINUTES[timeframe]
    if len(chart) < 200:
        return {"breaks": [], "joints": [], "bars": len(chart), "ready": False}
    params = V104Params()
    facts = study.v9_facts(chart, minutes, asset or "", float(tick))
    frame = facts["frame"]
    box: dict = {}
    reference_long_exits(frame.high, frame.low, frame.close, frame.atr, ready=facts["ready"], gap=facts["gap"],
                         raw_side=facts["side"], signal_side=np.where(facts["v9"], facts["side"], 0),
                         tick=float(tick), state=box)
    trace: dict = {}
    result = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr, can_run=facts["can_run"],
                          confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                          parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                          momentum=facts["momentum"], current_gate=facts["current_gate"],
                          ref_long_exit=facts["ref_long_exit"], tick=float(tick), params=params, trace=trace)
    lines = {ln["uid"]: ln for ln in trace["lines"]}
    index = frame.index
    step = pd.Timedelta(minutes=minutes)

    def chart_line(i: int) -> dict:
        ln = lines[int(trace["break_winner_uid"][i])]
        at = ln["ap"] + (ln["bp"] - ln["ap"]) * (float(i - ln["ax"]) / (ln["bx"] - ln["ax"]))
        return {"line_timeframe": timeframe, "track": TRACK.get(ln["source"], ""),
                "a_ms": _ms(index[ln["ax"]]), "a_price": ln["ap"], "b_ms": _ms(index[ln["bx"]]), "b_price": ln["bp"],
                "c_ms": _ms(index[ln["cx"]]), "c_price": ln["cp"], "born_close_ms": _ms(index[ln["born_i"]] + step),
                "line_at_bar": at}

    def base(kind: str, i: int) -> dict:
        close = float(frame.close.iloc[i])
        atr = _num(frame.atr.iloc[i])
        return {"protocol": PROTOCOL, "kind": kind, "timeframe": timeframe, "bar_open_ms": _ms(index[i]),
                "bar_close_ms": _ms(index[i] + step), "close": close, "atr": atr,
                "reference_stop": reference_stop(frame, i, float(tick)), "tick": float(tick)}

    breaks: list[dict] = []
    if want_breaks:
        for i in np.flatnonzero(result.break_event).tolist():
            breaks.append({**base("break", i), **chart_line(i)})

    joints: list[dict] = []
    if want_joints and timeframe in HIGHER:
        htf_now = np.zeros(len(frame), dtype=bool)
        H = None
        if higher is not None and len(higher) >= 60:
            H, _ = v11.htf_inputs(index, minutes, higher, MINUTES[HIGHER[timeframe]], float(tick), params)
            htf_now = H["known"]
        chart_now = np.asarray(result.break_event, dtype=bool)
        fired = box_joints(box["long_open"], box["box_entry"], htf_now | chart_now)
        # Build this same-timeframe causal series once for the whole replay,
        # never separately for individual joint entries.
        current_rsi = (rsi_exit.chartprime_strong_side(frame, gap=np.asarray(facts["gap"], dtype=bool), minutes=minutes)
                       if rsi_exit_enabled and rsi_features is None else rsi_features)
        performance = positions(frame, facts, fired, minutes=minutes, tick=float(tick),
                                rsi_exit_enabled=rsi_exit_enabled, rsi_features=current_rsi)
        for i in np.flatnonzero(fired).tolist():
            s = int(box["box_entry"][i])
            own, up = bool(chart_now[i]), bool(htf_now[i])
            record = {**base("joint", i), "source": "both" if own and up else "chart" if own else "higher",
                      "higher_timeframe": HIGHER[timeframe], "v9_signal_open_ms": _ms(index[s]),
                      "v9_signal_close_ms": _ms(index[s] + step), "v9_signal_close": float(frame.close.iloc[s]),
                      "bars_after_v9": i - s, "side": "long", "performance": performance[i]}
            if own:
                record.update(chart_line(i))
            if up and H is not None:
                to_ms = lambda minute: int(minute * 60_000)  # noqa: E731
                record["higher_line"] = {
                    "line_timeframe": HIGHER[timeframe], "a_ms": to_ms(H["ax_t"][i]), "a_price": _num(H["ap"][i]),
                    "b_ms": to_ms(H["bx_t"][i]), "b_price": _num(H["bp"][i]), "c_ms": to_ms(H["cx_t"][i]),
                    "c_price": _num(H["cp"][i]), "born_close_ms": to_ms(H["born_t"][i]),
                    "break_open_ms": to_ms(H["break_t"][i])}
            joints.append(record)
    return {"breaks": breaks, "joints": joints, "bars": len(frame), "ready": True}
