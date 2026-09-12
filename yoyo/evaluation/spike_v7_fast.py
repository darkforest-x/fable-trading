"""Array-backed exact replay for the frozen V7 two-year comparison.

This is an execution-equivalence accelerator, never an alternative signal or
execution specification.  It mirrors the reference state machines in
``spike_v6_wvf_study.py`` and ``spike_burst_v6_structure.py`` while moving
per-bar scalar reads from pandas into contiguous NumPy arrays.  It preserves
raw reverse events, admission-only filtering, gap censorship, next-open
fills, stop priority, boundary marks, and the reference ledger schema.

Reference source hashes at implementation time:

* spike_v6_wvf_study.py: ba8db5027680e259f0c7572e3d31b1fa709481869fd48679e004daec262d3fd4
* spike_burst_v6_structure.py: e84690c02e7b03291cc3037eab37ac495cc2ece79326e24411cfa72aa1214d4b
* spike_burst_progressive.py: cec81a33997bd39272762bd70a451ceb5bf218da0e7fc0606271d165437fde08
* spike_v6_bb_squeeze.py: 31f02fdd44f677e227deb6a5560114d2277c206c42c8400ecef3ec864d4046c0

``assert_reference_sources_unchanged`` checks those identities once per
process.  A source change therefore invalidates use of this accelerator until
its parity tests and recorded identities are deliberately refreshed.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study import (
    ExecutionSpec,
    _close_trade,
    _data_gap,
    _gap_censor,
)


_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_SOURCE_SHA256 = {
    "spike_v6_wvf_study.py": "ba8db5027680e259f0c7572e3d31b1fa709481869fd48679e004daec262d3fd4",
    "spike_burst_v6_structure.py": "e84690c02e7b03291cc3037eab37ac495cc2ece79326e24411cfa72aa1214d4b",
    "spike_burst_progressive.py": "cec81a33997bd39272762bd70a451ceb5bf218da0e7fc0606271d165437fde08",
    "spike_v6_bb_squeeze.py": "31f02fdd44f677e227deb6a5560114d2277c206c42c8400ecef3ec864d4046c0",
}
_REFERENCE_PATHS = {
    "spike_v6_wvf_study.py": _ROOT / "yoyo/evaluation/spike_v6_wvf_study.py",
    "spike_burst_v6_structure.py": _ROOT / "yoyo/evaluation/spike_burst_v6_structure.py",
    "spike_burst_progressive.py": _ROOT / "yoyo/evaluation/spike_burst_progressive.py",
    "spike_v6_bb_squeeze.py": _ROOT / "yoyo/evaluation/spike_v6_bb_squeeze.py",
}
_reference_validated = False


def assert_reference_sources_unchanged() -> None:
    """Fail closed when a source state machine changed after fast parity proof."""
    global _reference_validated
    if _reference_validated:
        return
    actual = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in _REFERENCE_PATHS.items()}
    if actual != REFERENCE_SOURCE_SHA256:
        changed = ", ".join(name for name in actual if actual[name] != REFERENCE_SOURCE_SHA256[name])
        raise RuntimeError(f"fast replay reference source changed: {changed}; refresh parity proof and hashes")
    _reference_validated = True


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _rsi_wilder_fast(close: pd.Series, period: int = 6) -> pd.Series:
    """Match the reference Wilder RSI while avoiding pandas scalar reads."""
    delta = close.diff()
    gains = delta.clip(lower=0).to_numpy(float)
    losses = (-delta.clip(upper=0)).to_numpy(float)
    out = np.full(len(close), np.nan)
    avg_gain = avg_loss = math.nan
    for i in range(len(close)):
        if i == period:
            avg_gain = float(np.nanmean(gains[1:period + 1]))
            avg_loss = float(np.nanmean(losses[1:period + 1]))
        elif i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if i >= period:
            out[i] = (100.0 if avg_loss == 0 and avg_gain > 0 else 0.0 if avg_gain == 0 and avg_loss > 0
                      else np.nan if avg_gain == avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    return pd.Series(out, index=close.index)


def _prior_squeeze_run3_fast(compressed: pd.Series) -> pd.Series:
    """Return the reference preceding-12 three-bar compression predicate once."""
    values = compressed.fillna(False).to_numpy(bool)
    run_end = values & np.r_[False, values[:-1]] & np.r_[False, False, values[:-2]]
    # At t, a complete previous run can end between t-10 and t-1 inclusive.
    prior = pd.Series(run_end, index=compressed.index).shift(1).rolling(10, min_periods=1).max().fillna(False)
    return prior.astype(bool)


_SIGNAL_LEDGER_COLUMNS = [
    "signal_i", "signal_bar_open", "signal_confirm_time", "side", "direction", "variant",
    "admitted_for_entry", "wvf_filter_reason", "wvf_current_extreme", "wvf_last_extreme_i",
]


def _make_signal_ledger_fast(signals: pd.DataFrame, reclaim: pd.DataFrame | None, *, variant: str,
                             minutes: int) -> pd.DataFrame:
    """Build the reference raw-event ledger by iterating events, never bars.

    This deliberately retains the reference ledger's admission diagnostic: it
    describes only the optional WVF reclaim and does not mirror the caller's
    broader execution admission mask.
    """
    if not {"long_signal", "short_signal"}.issubset(signals):
        raise ValueError("signals need long_signal and short_signal")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    long = signals.long_signal.fillna(False).to_numpy(bool)
    short = signals.short_signal.fillna(False).to_numpy(bool)
    if (long & short).any():
        raise ValueError("a close cannot carry both V6 sides")
    current_extreme = last_extreme_i = admitted_a = reason_a = None
    if reclaim is not None:
        admitted_a = reclaim.wvf_admitted.to_numpy(bool)
        reason_a = reclaim.wvf_filter_reason.to_numpy(object)
        current_extreme = reclaim.wvf_current_extreme.to_numpy(bool)
        last_extreme_i = reclaim.wvf_last_extreme_i.to_numpy()
    rows: list[dict[str, object]] = []
    for i in np.flatnonzero(long | short):
        side = 1 if long[i] else -1
        admitted, reason = True, "baseline_or_unfiltered_short"
        if side == 1 and reclaim is not None:
            admitted, reason = bool(admitted_a[i]), str(reason_a[i])
        stamp = signals.index[i]
        rows.append({"signal_i": int(i), "signal_bar_open": stamp,
                     "signal_confirm_time": stamp + pd.Timedelta(minutes=minutes), "side": side,
                     "direction": "long" if side == 1 else "short", "variant": variant,
                     "admitted_for_entry": admitted, "wvf_filter_reason": reason,
                     "wvf_current_extreme": None if reclaim is None else bool(current_extreme[i]),
                     "wvf_last_extreme_i": np.nan if reclaim is None else last_extreme_i[i]})
    return pd.DataFrame(rows, columns=_SIGNAL_LEDGER_COLUMNS)


def v7_diagnostics(bars: pd.DataFrame, *, data_gap: pd.Series) -> pd.DataFrame:
    """Exact BB200 V7 diagnostic without redundant RSI/run-compression work.

    Uses only each bar's close and its causal history: BB width is current
    BB200 width, threshold is the preceding 500 widths' P10, and the squeeze
    run is entirely in the preceding twelve bars.  RSI6 remains in the output
    for schema parity even though fixed V7 admission does not consume it.
    """
    from yoyo.evaluation.spike_v6_bb_squeeze import _segments

    assert_reference_sources_unchanged()
    if not {"open", "high", "low", "close"}.issubset(bars):
        raise ValueError("OHLC is required")
    out = pd.DataFrame(index=bars.index)
    out["segment_id"] = _segments(bars, data_gap)
    parts: list[pd.DataFrame] = []
    for _, ids in out.groupby("segment_id", sort=False):
        close = bars.loc[ids.index, "close"].astype(float)
        basis = close.rolling(200, min_periods=200).mean()
        std = close.rolling(200, min_periods=200).std(ddof=0)
        width = (4 * std / basis.abs()).where(basis.ne(0))
        threshold = width.shift(1).rolling(500, min_periods=500).quantile(.10)
        compressed = width.le(threshold).fillna(False)
        part = pd.DataFrame({"bb_basis": basis, "bb_std_ddof0": std, "bb_width": width,
                             "bb_width_p10_prior500": threshold, "bb_compressed": compressed,
                             "bb_prior_run3_in12": _prior_squeeze_run3_fast(compressed),
                             "rsi6": _rsi_wilder_fast(close)}, index=close.index)
        part["ready"] = threshold.notna() & part.rsi6.notna()
        parts.append(part)
    diagnostic = out.join(pd.concat(parts))
    threshold_ready = diagnostic.bb_width_p10_prior500.notna()
    diagnostic["threshold_ready_prior12"] = threshold_ready.shift(1).rolling(12, min_periods=12).min().eq(1)
    diagnostic["prior_squeeze_run3"] = _prior_squeeze_run3_fast(diagnostic.bb_compressed)
    diagnostic["v7_ready"] = diagnostic.threshold_ready_prior12.astype(bool)
    return diagnostic


def _legacy_v4(frame: pd.DataFrame, minutes: int, side: int) -> pd.DataFrame:
    """Array-backed equivalent of the reference V4-provenance reconstruction."""
    from yoyo.evaluation.spike_burst_progressive import progressive_fields

    assert_reference_sources_unchanged()
    if side not in (1, -1):
        raise ValueError("side must be ±1")
    progress, gap = progressive_fields(frame), _data_gap(frame, minutes)
    dense = frame.ready.eq(True) & frame.pastWidth.le(3.0) & frame.pastCrosses.ge(2.0)
    recent_dense = dense.astype(float).shift(1).rolling(12, min_periods=12).sum().gt(0)
    prior_high = frame.high.shift(1).rolling(12, min_periods=12).max()
    prior_low = frame.low.shift(1).rolling(12, min_periods=12).min()
    fast_high = frame[["s20", "e20"]].max(axis=1, skipna=False)
    fast_low = frame[["s20", "e20"]].min(axis=1, skipna=False)
    early = frame.ready.eq(True) & (
        (frame.close.gt(prior_high) & frame.close.gt(fast_high)) if side == 1
        else (frame.close.lt(prior_low) & frame.close.lt(fast_low))
    )
    quality = (frame.ready.eq(True) & recent_dense
               & (progress.prog_advance.ge(1.5) if side == 1 else progress.prog_advance.le(-1.5))
               & progress.prog_volume_ratio.ge(1.5)
               & (frame.md.ge(frame.sb) if side == 1 else frame.md.le(frame.sb))
               & (frame.middle.gt(frame.middle.shift(1)) if side == 1 else frame.middle.lt(frame.middle.shift(1))))

    gap_a, early_a, quality_a = gap.to_numpy(bool), early.to_numpy(bool), quality.to_numpy(bool)
    prior_high_a, prior_low_a, close_a = (series.to_numpy(float) for series in (prior_high, prior_low, frame.close))
    n = len(frame)
    confirmed = np.zeros(n, dtype=bool)
    parent_high_out = np.full(n, np.nan)
    parent_low_out = np.full(n, np.nan)
    previous_early = False
    last_accepted: int | None = None
    parent_i: int | None = None
    parent_high = parent_low = math.nan
    parent_confirmed = False
    for i in range(n):
        if gap_a[i]:
            previous_early = False
            last_accepted = parent_i = None
            parent_high = parent_low = math.nan
            parent_confirmed = False
        cooldown = last_accepted is None or i - last_accepted >= 12
        early_signal = early_a[i] and not previous_early and cooldown
        previous_early = early_a[i]
        if early_signal:
            last_accepted = parent_i = i
            parent_high, parent_low, parent_confirmed = prior_high_a[i], prior_low_a[i], False
        age = None if parent_i is None else i - parent_i
        if age is not None and age > 3:
            parent_i = None
            parent_high = parent_low = math.nan
            parent_confirmed = False
            age = None
        confirms = bool(parent_i is not None and not parent_confirmed and age is not None and 0 <= age <= 3
                        and quality_a[i] and (close_a[i] > parent_high if side == 1 else close_a[i] < parent_low))
        if confirms:
            parent_confirmed = True
            confirmed[i] = True
            parent_high_out[i] = parent_high
            parent_low_out[i] = parent_low
    return pd.DataFrame({"legacy_confirmed": confirmed, "legacy_parent_high": parent_high_out,
                         "legacy_parent_low": parent_low_out}, index=frame.index)


def _shape(open_a: np.ndarray, high_a: np.ndarray, low_a: np.ndarray, close_a: np.ndarray,
           i: int, side: int) -> bool:
    """Exact scalar V6 candle shape predicate over already-coerced arrays."""
    o, h, low, close = open_a[i], high_a[i], low_a[i], close_a[i]
    if not (np.isfinite((o, h, low, close)).all() and low > 0 and h >= max(o, close, low)
            and low <= min(o, close, h)):
        return False
    span = h - low
    if span <= 0:
        return False
    direction = close > o if side == 1 else close < o
    end = (close - low) / span if side == 1 else (h - close) / span
    return bool(direction and abs(close - o) / span >= .55 and end >= .75)


def _detect_confirmed_fast(frame: pd.DataFrame, side: int) -> np.ndarray:
    """Return the reference structural gate's ``confirmed`` field only."""
    rope_name = "ropeHigh" if side == 1 else "ropeLow"
    required = ("open", "high", "low", "close", "md", "sb", "atr", rope_name, "legacy_confirmed",
                "legacy_parent_high", "legacy_parent_low", "ready", "data_gap", "confirmed", "advance3",
                "volume_ratio3")
    missing = [name for name in required if name not in frame]
    if missing:
        raise ValueError("Missing V6 structural columns: " + ", ".join(missing))
    confirmed_input = frame.confirmed.to_numpy(bool)
    if not confirmed_input[:-1].all():
        raise ValueError("Unconfirmed rows are permitted only at the final tip")
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "md", "sb", "atr", rope_name,
                                                               "legacy_parent_high", "legacy_parent_low", "advance3", "volume_ratio3")}
    open_a, high_a, low_a, close_a = (arrays[name] for name in ("open", "high", "low", "close"))
    md_a, sb_a, atr_a, rope_a = (arrays[name] for name in ("md", "sb", "atr", rope_name))
    parent_high_a, parent_low_a = arrays["legacy_parent_high"], arrays["legacy_parent_low"]
    advance_a, volume_a = arrays["advance3"], arrays["volume_ratio3"]
    ready_a = frame.ready.to_numpy(bool)
    gap_a = frame.data_gap.to_numpy(bool)
    legacy_a = frame.legacy_confirmed.to_numpy(bool)
    out = np.zeros(len(frame), dtype=bool)
    body_support_i: int | None = None
    parent_high: float | None = None
    parent_low: float | None = None
    pending = evidence = False
    evidence_i: int | None = None
    prior_md: float | None = None
    segment_start_i = 0
    for i in range(len(frame)):
        if not confirmed_input[i]:
            continue
        valid_ohlc = (np.isfinite((open_a[i], high_a[i], low_a[i], close_a[i])).all() and low_a[i] > 0
                      and high_a[i] >= max(open_a[i], close_a[i], low_a[i])
                      and low_a[i] <= min(open_a[i], close_a[i], high_a[i]))
        valid = (not gap_a[i] and ready_a[i] and valid_ohlc
                 and np.isfinite((md_a[i], sb_a[i], atr_a[i], rope_a[i])).all() and atr_a[i] > 0)
        if not valid:
            body_support_i = None
            parent_high = parent_low = None
            pending = evidence = False
            evidence_i = None
            segment_start_i = i + 1
            prior_md = float(md_a[i]) if _finite(md_a[i]) else None
            continue
        o, close, md, sb, rope_value = open_a[i], close_a[i], md_a[i], sb_a[i], rope_a[i]
        full_body = min(o, close) > rope_value if side == 1 else max(o, close) < rope_value
        if side * (close - rope_value) <= 0:
            body_support_i = None
        elif full_body:
            body_support_i = i
        parent_valid = (legacy_a[i] and _finite(parent_high_a[i]) and _finite(parent_low_a[i])
                        and parent_low_a[i] <= parent_high_a[i])
        if parent_valid:
            parent_high, parent_low, pending = float(parent_high_a[i]), float(parent_low_a[i]), True
            window_start = max(segment_start_i, i - 2)
            shape_i = next((j for j in range(i, window_start - 1, -1)
                            if _shape(open_a, high_a, low_a, close_a, j, side)), None)
            envelope = np.isfinite((advance_a[i], volume_a[i])).all() and side * advance_a[i] >= 1.5 and volume_a[i] >= 1.5
            evidence = bool(envelope and shape_i is not None)
            evidence_i = shape_i if evidence else None
        elif pending and _shape(open_a, high_a, low_a, close_a, i, side):
            envelope = np.isfinite((advance_a[i], volume_a[i])).all() and side * advance_a[i] >= 1.5 and volume_a[i] >= 1.5
            if envelope:
                evidence, evidence_i = True, i
        invalidated = pending and (close < float(parent_low) if side == 1 else close > float(parent_high))
        if invalidated:
            parent_high = parent_low = None
            pending = evidence = False
            evidence_i = None
        elif (pending and evidence and body_support_i is not None and parent_high is not None and parent_low is not None
              and prior_md is not None and side * (close - rope_value) > 0
              and (close > parent_high if side == 1 else close < parent_low)
              and side * (md - sb) > 0 and side * (md - prior_md) > 0):
            out[i] = True
            pending = evidence = False
            evidence_i = None
        prior_md = float(md)
    return out


def v6_signals(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Return exact V6 long/short signals without pandas scalar reads per bar."""
    from yoyo.evaluation.spike_burst_progressive import progressive_fields

    assert_reference_sources_unchanged()
    progress, gap = progressive_fields(frame), _data_gap(frame, minutes)
    output = pd.DataFrame(index=frame.index)
    for side, label in ((1, "long_signal"), (-1, "short_signal")):
        legacy = _legacy_v4(frame, minutes, side)
        supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
        supplied["legacy_confirmed"] = legacy.legacy_confirmed.astype(bool)
        supplied["legacy_parent_high"] = legacy.legacy_parent_high
        supplied["legacy_parent_low"] = legacy.legacy_parent_low
        supplied["advance3"] = progress.prog_advance
        supplied["volume_ratio3"] = progress.prog_volume_ratio
        supplied["data_gap"] = gap
        supplied["confirmed"] = True
        output[label] = _detect_confirmed_fast(supplied, side)
    if (output.long_signal & output.short_signal).any():
        raise ValueError("V6 oracle yielded an ambiguous two-sided close")
    return output


def _initial_position_fast(index: pd.Index, open_a: np.ndarray, high_a: np.ndarray, low_a: np.ndarray,
                           close_a: np.ndarray, atr_a: np.ndarray, gaps: np.ndarray, signal_i: int,
                           side: int, spec: ExecutionSpec) -> dict[str, object] | None:
    """Build the reference next-open position using array slices only."""
    if signal_i + 1 >= len(index):
        return None
    tick = float(spec.tick)
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")
    start = signal_i - spec.stop_bars + 1
    if signal_i < spec.stop_bars - 1 or gaps[start:signal_i + 1].any():
        return None
    values = np.column_stack((open_a[start:signal_i + 1], high_a[start:signal_i + 1],
                               low_a[start:signal_i + 1], close_a[start:signal_i + 1]))
    if (not np.isfinite(values).all() or np.any(values[:, 2] <= 0)
            or np.any(values[:, 1] < np.maximum(values[:, 0], values[:, 3]))
            or np.any(values[:, 2] > np.minimum(values[:, 0], values[:, 3]))):
        return None
    atr, close, entry = atr_a[signal_i], close_a[signal_i], open_a[signal_i + 1]
    if not all(math.isfinite(float(x)) for x in (atr, close, entry)) or atr <= 0 or entry <= 0:
        return None
    extreme = float(low_a[start:signal_i + 1].min() if side == 1 else high_a[start:signal_i + 1].max())
    raw = (min(extreme - spec.stop_buffer_atr * atr, close - spec.risk_floor_atr * atr)
           if side == 1 else max(extreme + spec.stop_buffer_atr * atr, close + spec.risk_floor_atr * atr))
    stop = math.floor(raw / tick) * tick if side == 1 else math.ceil(raw / tick) * tick
    risk = side * (entry - stop)
    if not math.isfinite(stop) or stop <= 0 or not math.isfinite(risk) or risk <= 0:
        return None
    return {"signal_i": signal_i, "signal_bar_open": index[signal_i], "entry_i": signal_i + 1,
            "entry_time": index[signal_i + 1], "side": side, "entry_price": float(entry), "initial_stop": stop,
            "initial_risk": float(risk), "initial_risk_frac": float(risk / entry), "protection": stop,
            "mfe_r": 0.0, "trail_armed": False}


def simulate_v6_variant(frame: pd.DataFrame, signals: pd.DataFrame, *, admission: pd.Series,
                        variant: str, data_gap: pd.Series | None = None,
                        reclaim: pd.DataFrame | None = None,
                        spec: ExecutionSpec = ExecutionSpec()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Array-backed equivalent of the reference V6 execution replay."""
    assert_reference_sources_unchanged()
    required = {"open", "high", "low", "close", "atr"}
    if not required.issubset(frame) or not signals.index.equals(frame.index):
        raise ValueError("aligned OHLC/ATR bars and signals are required")
    minutes = frame.attrs.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("frame.attrs minutes must be a positive integer")
    ledger = _make_signal_ledger_fast(signals, reclaim, variant=variant, minutes=minutes)
    allowed = pd.Series(admission, index=frame.index).fillna(False).to_numpy(bool)
    gap = (pd.Series(False, index=frame.index) if data_gap is None
           else pd.Series(data_gap, index=frame.index).fillna(True).astype(bool))
    gap_a = gap.to_numpy(bool)
    long_a, short_a = signals.long_signal.fillna(False).to_numpy(bool), signals.short_signal.fillna(False).to_numpy(bool)
    raw_side = np.where(long_a, 1, np.where(short_a, -1, 0))
    if (long_a & short_a).any():
        raise ValueError("ambiguous signal side")
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")}
    open_a, high_a, low_a, close_a, atr_a = (arrays[name] for name in ("open", "high", "low", "close", "atr"))
    index = frame.index
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_reverse: tuple[int, int] | None = None
    trades: list[dict[str, object]] = []
    for i in range(len(frame)):
        ended_side = 0
        if gap_a[i]:
            if position is not None:
                trades.append(_gap_censor(position, exit_i=i, exit_time=index[i]))
            position = None
            pending_entry = pending_reverse = None
            continue
        if pending_reverse is not None and position is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                opening, protection = float(open_a[i]), float(position["protection"])
                stop_at_open = opening <= protection if old_side == 1 else opening >= protection
                reason = ("trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap") if stop_at_open else "opposite_v6_next_open"
                trades.append(_close_trade(position, exit_i=i, exit_time=index[i], exit_price=opening, reason=reason, spec=spec))
                ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                position = _initial_position_fast(index, open_a, high_a, low_a, close_a, atr_a, gap_a, signal_i, side, spec)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            o, h, low, close, atr = open_a[i], high_a[i], low_a[i], close_a[i], atr_a[i]
            stopped = low <= protection if side == 1 else h >= protection
            if stopped:
                price = min(o, protection) if side == 1 else max(o, protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if o <= protection if side == 1 else o >= protection:
                    reason += "_gap"
                trades.append(_close_trade(position, exit_i=i, exit_time=index[i], exit_price=float(price), reason=reason, spec=spec))
                ended_side, position = side, None
            else:
                favorable = h if side == 1 else low
                position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - float(position["entry_price"])) / float(position["initial_risk"]))
                current_r = side * (close - float(position["entry_price"])) / float(position["initial_risk"])
                position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
                if bool(position["trail_armed"]) and math.isfinite(float(atr)) and atr > 0:
                    raw = close - side * spec.trail_atr * atr
                    candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
                    position["protection"] = max(protection, candidate) if side == 1 else min(protection, candidate)
        side = int(raw_side[i])
        if side:
            if position is not None and side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if allowed[i]:
                    pending_entry = (i, side)
            elif position is None and side != ended_side and allowed[i]:
                pending_entry = (i, side)
    if position is not None:
        last = len(frame) - 1
        trade = _close_trade(position, exit_i=last, exit_time=index[last], exit_price=float(close_a[last]),
                             reason="boundary_mark", spec=spec)
        trade["censored"] = True
        trade["exit_time_precision"] = "last_complete_close"
        trades.append(trade)
    trades_frame = pd.DataFrame(trades)
    if len(trades_frame):
        equity, before, after = 1.0, [], []
        for trade in trades_frame.itertuples():
            before.append(equity)
            if not bool(trade.censored):
                equity *= 1.0 + float(trade.net_return)
            after.append(equity)
        trades_frame["account_equity_before"] = before
        trades_frame["account_equity_after"] = after
    return ledger, trades_frame
