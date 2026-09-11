"""Build causal SPIKE V6 ETH execution inputs for Freqtrade 2026.8.

This research bridge reads one frozen OKX ETH-USDT-SWAP 30-minute OHLCV receipt.
It reconstructs V6's Pine V4 provenance on each complete 30m/1h/4h frame, supplies
only current-or-earlier OHLCV fields to the pinned V6 structure oracle, and emits
next-open entries plus the stop operative at each later candle open.  Stop updates
are calculated at a candle close and are never used on that same candle.

It is deliberately not a live strategy, a TradingView-native replay, or a funding
model.  No exchange/network/monitor/notification API is used.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_burst_progressive import progressive_fields
from yoyo.evaluation.spike_burst_v6_structure import detect as v6_detect

EXP = Path(__file__).resolve().parent
PLANS = EXP / "plans"
RESULTS = EXP / "results"
SOURCE = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/normalized/okx/ETH-USDT-SWAP_30m.csv.gz"
PINE = ROOT / "yoyo/evaluation/pine/spike_burst_v6.pine"
ORACLE = ROOT / "yoyo/evaluation/spike_burst_v6_structure.py"
START = pd.Timestamp("2024-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
DEV_END = pd.Timestamp("2025-09-10T00:00:00Z")
TICK = 0.01
COST_ROUND_TRIP = 0.002
BASELINE = (2.0, 4.0)
# One-axis development probes around frozen V1 risk constants; not a joint grid.
INITIAL_CHOICES = (1.5, 2.0, 2.5)
TRAIL_CHOICES = (3.0, 4.0, 5.0)
TIMEFRAMES = {30: "30m", 60: "1h", 240: "4h"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_source() -> pd.DataFrame:
    frame = pd.read_csv(SOURCE, index_col=0, parse_dates=True)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
    return frame


def aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate only complete 30-minute frozen source buckets."""
    if minutes == 30:
        return frame.copy()
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "quote_volume": "sum"})
    return out.loc[grouped.size().eq(minutes // 30)].copy()


def _data_gap(frame: pd.DataFrame, minutes: int) -> pd.Series:
    step = pd.Timedelta(minutes=minutes)
    values = frame.index.to_series().diff().ne(step)
    values.iloc[0] = False
    return values.astype(bool)


def _legacy_v4(frame: pd.DataFrame, minutes: int, side: int) -> pd.DataFrame:
    """Pine V6's retained V4 provenance engine, closed bars only.

    This is intentionally local to the V6 bridge.  The source Pine applies its
    12-bar density/cooldown and three-bar confirmation age separately for each
    direction.  A time discontinuity resets provenance before evaluating the
    current bar; V6's supplied gap flag then makes the structural oracle reset.
    """
    if side not in (1, -1):
        raise ValueError("side must be ±1")
    p = progressive_fields(frame)
    gap = _data_gap(frame, minutes)
    dense = frame.ready.eq(True) & frame.pastWidth.le(3.0) & frame.pastCrosses.ge(2.0)
    # Pine's recentDense comes from dense[1] over the preceding 12 bars.
    recent_dense = dense.astype(float).shift(1).rolling(12, min_periods=12).sum().gt(0)
    prior_high = frame.high.shift(1).rolling(12, min_periods=12).max()
    prior_low = frame.low.shift(1).rolling(12, min_periods=12).min()
    fast_high = frame[["s20", "e20"]].max(axis=1, skipna=False)
    fast_low = frame[["s20", "e20"]].min(axis=1, skipna=False)
    early = frame.ready.eq(True) & (
        frame.close.gt(prior_high) & frame.close.gt(fast_high)
        if side == 1 else frame.close.lt(prior_low) & frame.close.lt(fast_low)
    )
    quality = (
        frame.ready.eq(True)
        & recent_dense
        & (p.prog_advance.ge(1.5) if side == 1 else p.prog_advance.le(-1.5))
        & p.prog_volume_ratio.ge(1.5)
        & (frame.md.ge(frame.sb) if side == 1 else frame.md.le(frame.sb))
        & (frame.middle.gt(frame.middle.shift(1)) if side == 1 else frame.middle.lt(frame.middle.shift(1)))
    )
    previous_early = False
    last_accepted: int | None = None
    parent_i: int | None = None
    parent_high = parent_low = math.nan
    parent_confirmed = False
    rows: list[dict[str, object]] = []
    for i in range(len(frame)):
        if bool(gap.iloc[i]):
            previous_early = False
            last_accepted = parent_i = None
            parent_high = parent_low = math.nan
            parent_confirmed = False
        cooldown = last_accepted is None or i - last_accepted >= 12
        early_signal = bool(early.iloc[i]) and not previous_early and cooldown
        previous_early = bool(early.iloc[i])
        if early_signal:
            last_accepted = parent_i = i
            parent_high, parent_low = float(prior_high.iloc[i]), float(prior_low.iloc[i])
            parent_confirmed = False
        age = None if parent_i is None else i - parent_i
        if age is not None and age > 3:
            parent_i = None
            parent_high = parent_low = math.nan
            parent_confirmed = False
            age = None
        confirms = bool(
            parent_i is not None and not parent_confirmed and age is not None and 0 <= age <= 3
            and quality.iloc[i]
            and (frame.close.iloc[i] > parent_high if side == 1 else frame.close.iloc[i] < parent_low)
        )
        if confirms:
            parent_confirmed = True
        rows.append({
            "legacy_confirmed": confirms,
            "legacy_parent_high": parent_high if confirms else math.nan,
            "legacy_parent_low": parent_low if confirms else math.nan,
            "legacy_parent_i": parent_i if confirms else math.nan,
        })
    return pd.DataFrame(rows, index=frame.index)


def supplied_v6(frame: pd.DataFrame, minutes: int, side: int) -> pd.DataFrame:
    """Return all V6 oracle inputs, each known on the corresponding close."""
    p = progressive_fields(frame)
    legacy = _legacy_v4(frame, minutes, side)
    supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
    supplied["legacy_confirmed"] = legacy.legacy_confirmed.astype(bool)
    supplied["legacy_parent_high"] = legacy.legacy_parent_high
    supplied["legacy_parent_low"] = legacy.legacy_parent_low
    supplied["advance3"] = p.prog_advance
    supplied["volume_ratio3"] = p.prog_volume_ratio
    supplied["data_gap"] = _data_gap(frame, minutes)
    supplied["confirmed"] = True
    return supplied


def v6_events(frame: pd.DataFrame, minutes: int, side: int) -> pd.DataFrame:
    supplied = supplied_v6(frame, minutes, side)
    detected = v6_detect(supplied, side=side)
    events = detected.confirmed.astype(bool)
    return pd.DataFrame({"signal": events, "legacy_i": detected.legacy_i, "evidence_i": detected.consumed_evidence_i}, index=frame.index)


def _initial_stop(bars: pd.DataFrame, signal_i: int, side: int, floor_atr: float) -> float:
    close, atr = float(bars.close.iloc[signal_i]), float(bars.atr.iloc[signal_i])
    window = bars.iloc[max(0, signal_i - 4):signal_i + 1]
    extreme = float(window.low.min() if side == 1 else window.high.max())
    raw = min(extreme - 0.2 * atr, close - floor_atr * atr) if side == 1 else max(extreme + 0.2 * atr, close + floor_atr * atr)
    return math.floor(raw / TICK) * TICK if side == 1 else math.ceil(raw / TICK) * TICK


def active_stop_plan(bars: pd.DataFrame, signal_i: int, side: int, floor_atr: float, trail_atr: float) -> list[dict[str, object]]:
    """Emit each candle-open stop with no later OHLC influence.

    At j, ``active_stop`` is calculated from bars through j-1.  The current
    low/high can only decide whether that already-active stop exits; the close
    then ratchets a stop for j+1.  The plan has no future exit timestamps.
    """
    if signal_i + 1 >= len(bars):
        return []
    entry_time = bars.index[signal_i + 1]
    entry = float(bars.open.iloc[signal_i + 1])
    initial_stop = _initial_stop(bars, signal_i, side, floor_atr)
    risk = side * (entry - initial_stop)
    if not math.isfinite(risk) or risk <= 0:
        return []
    protection, armed = initial_stop, False
    rows: list[dict[str, object]] = []
    for j in range(signal_i + 1, len(bars)):
        b = bars.iloc[j]
        o, h, l, c, atr = map(float, (b.open, b.high, b.low, b.close, b.atr))
        rows.append({
            "entry_time": entry_time, "current_time": bars.index[j], "side": side,
            "active_stop": protection, "initial_stop": initial_stop, "entry_price": entry,
        })
        touched = l <= protection if side == 1 else h >= protection
        if touched:
            break
        armed = armed or side * (c - entry) / risk >= 2.0
        if armed and j + 1 < len(bars) and math.isfinite(atr) and atr > 0:
            raw = c - side * trail_atr * atr
            candidate = math.floor(raw / TICK) * TICK if side == 1 else math.ceil(raw / TICK) * TICK
            protection = max(protection, candidate) if side == 1 else min(protection, candidate)
    return rows


def _one_event_rows(bars: pd.DataFrame, minutes: int, signal_i: int, side: int, variant: str, floor: float, trail: float) -> tuple[dict[str, object], list[dict[str, object]]]:
    step = pd.Timedelta(minutes=minutes)
    entry_i = signal_i + 1
    if entry_i >= len(bars):
        return {}, []
    entry_time = bars.index[entry_i]
    if not START <= entry_time < END:
        return {}, []
    stops = active_stop_plan(bars, signal_i, side, floor, trail)
    if not stops:
        return {}, []
    initial = float(stops[0]["initial_stop"])
    entry = float(stops[0]["entry_price"])
    return ({
        "signal_bar_open": bars.index[signal_i], "signal_close_time": bars.index[signal_i] + step,
        "entry_time": entry_time, "side": side, "direction": "long" if side == 1 else "short",
        "initial_stop": initial, "entry_reference": "next_open", "entry_price": entry,
        "risk_fraction_at_entry": side * (entry - initial) / entry,
        "variant": variant, "floor_atr": floor, "trail_atr": trail,
    }, stops)


def build_variant(bars: pd.DataFrame, minutes: int, signals: Iterable[tuple[int, int]], variant: str, floor: float, trail: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    events: list[dict[str, object]] = []
    plans: list[dict[str, object]] = []
    for signal_i, side in signals:
        event, stop_rows = _one_event_rows(bars, minutes, signal_i, side, variant, floor, trail)
        if event:
            events.append(event)
            plans.extend(stop_rows)
    return pd.DataFrame(events), pd.DataFrame(plans)


def _prefix_positions(frame: pd.DataFrame) -> list[int]:
    wanted = [500, 2500, int(frame.index.searchsorted(DEV_END)), len(frame) - 1]
    return sorted({i for i in wanted if 350 < i < len(frame)})


def prefix_causality_check(raw: pd.DataFrame, minutes: int) -> dict[str, object]:
    """Check frozen source prefixes cannot revise past V6 events or active stops."""
    bars = features(aggregate(raw, minutes))
    full = {side: v6_events(bars, minutes, side) for side in (1, -1)}
    checked: list[dict[str, object]] = []
    for end_i in _prefix_positions(bars):
        prefix = bars.iloc[: end_i + 1].copy()
        for side in (1, -1):
            observed = v6_events(prefix, minutes, side)
            pd.testing.assert_frame_equal(observed, full[side].iloc[: end_i + 1], check_dtype=False, check_exact=True)
        # For a concrete historical event, compare the stop operative at the prefix tip.
        candidates = np.flatnonzero((full[1].signal | full[-1].signal).iloc[:end_i].to_numpy())
        if len(candidates):
            i = int(candidates[-1])
            side = 1 if bool(full[1].signal.iloc[i]) else -1
            one = active_stop_plan(bars.iloc[: end_i + 1], i, side, *BASELINE)
            all_rows = active_stop_plan(bars, i, side, *BASELINE)
            shared = min(len(one), len(all_rows))
            if shared:
                assert one[:shared] == all_rows[:shared]
        checked.append({"prefix_rows": end_i + 1})
    return {"minutes": minutes, "checks": checked}


def main() -> None:
    PLANS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    raw = read_source()
    receipt: dict[str, object] = {
        "schema": "spike-v6-freqtrade-bridge-v1",
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source": str(SOURCE.relative_to(ROOT)), "source_sha256": sha256(SOURCE),
        "pine_commit": "6c12660", "pine_sha256": sha256(PINE), "oracle_sha256": sha256(ORACLE),
        "period": {"start": START.isoformat(), "end_exclusive": END.isoformat(), "development_end": DEV_END.isoformat()},
        "execution": {"entry": "next observed open", "fee_round_trip": COST_ROUND_TRIP, "tick": TICK,
                      "active_stop": "known at preceding close; checked before same-bar close ratchet", "funding": "not modelled"},
        "variants": {"baseline": {"floor_atr": 2.0, "trail_atr": 4.0},
                     "initial_axis": list(INITIAL_CHOICES), "trail_axis": list(TRAIL_CHOICES)},
    }
    totals: list[dict[str, object]] = []
    causality: list[dict[str, object]] = []
    for minutes in TIMEFRAMES:
        bars = features(aggregate(raw, minutes))
        causality.append(prefix_causality_check(raw, minutes))
        long = v6_events(bars, minutes, 1).signal
        short = v6_events(bars, minutes, -1).signal
        overlap = long & short
        if overlap.any():
            raise RuntimeError(f"Ambiguous V6 long/short events: {overlap[overlap].index.tolist()[:3]}")
        signals = {
            "both": [(int(i), 1) for i in np.flatnonzero(long.to_numpy())] + [(int(i), -1) for i in np.flatnonzero(short.to_numpy())],
            "long_only": [(int(i), 1) for i in np.flatnonzero(long.to_numpy())],
        }
        for universe, event_list in signals.items():
            for variant, floor, trail in (
                ("baseline", *BASELINE),
                *[(f"initial_{x:.1f}", x, BASELINE[1]) for x in INITIAL_CHOICES],
                *[(f"trail_{x:.1f}", BASELINE[0], x) for x in TRAIL_CHOICES],
            ):
                events, plans = build_variant(bars, minutes, event_list, variant, floor, trail)
                suffix = f"{universe}_{minutes}m_{variant}"
                events.to_csv(PLANS / f"signals_{suffix}.csv", index=False)
                plans.to_csv(PLANS / f"stops_{suffix}.csv", index=False)
                totals.append({"universe": universe, "timeframe_min": minutes, "variant": variant,
                               "candidates": len(events), "long": int((events.side == 1).sum()) if len(events) else 0,
                               "short": int((events.side == -1).sum()) if len(events) else 0,
                               "stop_rows": len(plans)})
    pd.DataFrame(totals).to_csv(RESULTS / "bridge_candidate_counts.csv", index=False)
    receipt["prefix_causality"] = causality
    receipt["counts"] = totals
    (RESULTS / "bridge_receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
