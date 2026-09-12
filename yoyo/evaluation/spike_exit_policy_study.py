"""Receipt-bound exit-policy replay for the frozen V1/V6/V7 two-year streams.

The module consumes only ``control_cache.pkl.gz`` and ``signals.csv.gz`` from
the frozen V7/V1 comparison.  It never derives indicators, reads Pine, fetches
market data, or changes the original execution sources.  Entries, stops and
raw V6 reversal exits retain the frozen next-open, five-bar 0.2-ATR/minimum
2-ATR-risk, 2R/4ATR trail and 0.2% round-trip-cost contract.  Exit policies
are evaluated on the bar close and take effect at the following observed open;
the sole exception is an already-known protective stop, which may fill within
the current bar.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import _initial_position_fast


START = pd.Timestamp("2024-09-10T00:00:00Z")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
END = pd.Timestamp("2026-09-10T00:00:00Z")
SOURCE_STREAMS = Path(
    "experiments/active/exp-spike-v7-v1-compare-20260912-v1/"
    "results/replay_two_year_20260912_v3/streams"
)
POLICIES = (
    "baseline", "no_reverse", "be095_price", "be1_price", "be1_cost",
    "partial_1_25", "partial_1_25_3_35", "partial_be1_cost",
    "fast_ma_exit", "md_cross_exit",
)
COHORTS = ("v1_common_long", "v6_both", "v7_both")
ENTRY_COST = 0.001
EXIT_COST = 0.001

FILL_COLUMNS = [
    "trade_id", "leg_no", "cohort", "policy", "stream_key", "venue", "symbol", "asset",
    "timeframe_min", "side", "entry_time", "bar_open", "event_time", "execution_phase",
    "event_time_precision", "kind", "qty_fraction", "price", "cost_return", "risk",
    "initial_risk", "tick", "reason",
]
TRADE_COLUMNS = [
    "trade_id", "cohort", "policy", "stream_key", "venue", "symbol", "asset", "timeframe_min",
    "signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop",
    "initial_risk", "initial_risk_frac", "tick", "qty_initial", "qty_realized", "qty_remaining",
    "mfe_r", "protection", "exit_i", "exit_time", "exit_price", "exit_reason", "gross_return",
    "net_return", "gross_r", "net_r", "censored", "exit_time_precision",
]
EVENT_COLUMNS = [
    "trade_id", "cohort", "policy", "stream_key", "bar_open", "event_time", "execution_phase",
    "event_kind", "reason", "protection_before", "protection_after", "qty_fraction",
]


@dataclass(frozen=True)
class StreamContext:
    """One authenticated frozen cache plus the supplied signal-ledger path."""

    path: Path
    key: str
    receipt: dict
    cache: dict
    signals_ledger: pd.DataFrame
    minutes: int
    identity: dict[str, object]


def sha256(path: Path) -> str:
    """Return a file identity without trusting its filename or timestamp."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_verified_stream(folder: Path) -> StreamContext:
    """Load a cache only after its frozen SHA-256 receipt validates it."""
    cache_path, receipt_path, ledger_path = (folder / "control_cache.pkl.gz", folder / "control_cache.receipt.json",
                                             folder / "signals.csv.gz")
    if not (cache_path.is_file() and receipt_path.is_file() and ledger_path.is_file()):
        raise FileNotFoundError(f"incomplete frozen stream: {folder}")
    receipt = json.loads(receipt_path.read_text())
    if sha256(cache_path) != receipt.get("cache_sha256"):
        raise ValueError(f"control cache hash mismatch: {folder.name}")
    cache = pd.read_pickle(cache_path)
    required = {"bars", "signals", "v1_signals", "data_gap", "bb_ready", "bb", "tick"}
    if not isinstance(cache, dict) or not required.issubset(cache):
        raise ValueError(f"unexpected cache contract: {folder.name}")
    bars, signals, v1, gap, bb = (cache[key] for key in ("bars", "signals", "v1_signals", "data_gap", "bb"))
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError(f"cache needs a UTC DatetimeIndex: {folder.name}")
    if not (signals.index.equals(bars.index) and v1.index.equals(bars.index) and gap.index.equals(bars.index)
            and bb.index.equals(bars.index)):
        raise ValueError(f"unaligned cache clocks: {folder.name}")
    if not {"open", "high", "low", "close", "atr", "s20", "e20", "md", "sb"}.issubset(bars):
        raise ValueError(f"cache bars missing execution columns: {folder.name}")
    if not {"long_signal", "short_signal"}.issubset(signals) or not {"long_signal", "short_signal"}.issubset(v1):
        raise ValueError(f"cache signals missing side columns: {folder.name}")
    if not {"prior_squeeze_run3", "v7_ready"}.issubset(bb):
        raise ValueError(f"cache BB diagnostic incomplete: {folder.name}")
    minutes = int(receipt["key"].split("_")[1].removesuffix("m"))
    if not math.isfinite(float(cache["tick"])) or float(cache["tick"]) <= 0:
        raise ValueError(f"invalid frozen tick: {folder.name}")
    completion = json.loads((folder / "completion.json").read_text())
    if completion.get("status") != "complete" or completion.get("cache_sha256") != receipt["cache_sha256"] or completion.get("key") != folder.name:
        raise ValueError("frozen completion/cache identity mismatch")
    ledger = pd.read_csv(ledger_path)
    provenance = pd.read_csv(folder / "receipt.csv").iloc[0]
    if str(provenance.source_sha256) != str(receipt.get("source_sha256")):
        raise ValueError(f"ledger/source receipt mismatch: {folder.name}")
    identity = {"venue": str(provenance.venue), "symbol": str(provenance.symbol), "asset": str(provenance.asset),
                "timeframe_min": int(provenance.minutes)}
    if identity["timeframe_min"] != minutes:
        raise ValueError(f"timeframe mismatch: {folder.name}")
    return StreamContext(folder, str(receipt["key"]), receipt, cache, ledger, minutes, identity)


def _cohort_inputs(context: StreamContext, cohort: str) -> tuple[pd.DataFrame, pd.Series]:
    """Restore only frozen admissions; V1 remains an explicitly long-only arm."""
    index = context.cache["bars"].index
    in_window = pd.Series((index + pd.Timedelta(minutes=context.minutes) >= START) & (index + pd.Timedelta(minutes=context.minutes) < END), index=index)
    if cohort == "v1_common_long":
        signals = context.cache["v1_signals"][["long_signal", "short_signal"]].copy()
        allowed = signals.long_signal.fillna(False).astype(bool)
    elif cohort == "v6_both":
        signals = context.cache["signals"][["long_signal", "short_signal"]].copy()
        allowed = signals.long_signal.fillna(False).astype(bool) | signals.short_signal.fillna(False).astype(bool)
    elif cohort == "v7_both":
        signals = context.cache["signals"][["long_signal", "short_signal"]].copy()
        raw = signals.long_signal.fillna(False).astype(bool) | signals.short_signal.fillna(False).astype(bool)
        bb = context.cache["bb"]
        allowed = raw & bb.v7_ready.fillna(False).astype(bool) & bb.prior_squeeze_run3.fillna(False).astype(bool)
    else:
        raise ValueError(f"unknown cohort: {cohort}")
    signals.loc[~in_window, ["long_signal", "short_signal"]] = False
    return signals, (allowed & in_window).astype(bool)


def _frozen_signal_i(context: StreamContext, stamp: object) -> int | None:
    """Recover the original full-frame ordinal from the supplied signal ledger."""
    ledger = context.signals_ledger
    times = pd.to_datetime(ledger.signal_bar_open, utc=True)
    found = ledger.loc[times.eq(pd.Timestamp(stamp)), "signal_i"].dropna().unique()
    if len(found) == 0:
        return None
    if len(found) != 1:
        raise ValueError(f"ambiguous frozen signal ordinal: {context.key} {stamp}")
    return int(found[0])


def _close_time(index: pd.DatetimeIndex, i: int, minutes: int) -> pd.Timestamp:
    return index[i] + pd.Timedelta(minutes=minutes)


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def _fast_ma_wrong_side(frame: pd.DataFrame, i: int, side: int) -> bool:
    close, s20, e20 = (float(frame[name].iloc[i]) for name in ("close", "s20", "e20"))
    return _finite(close, s20, e20) and (close < min(s20, e20) if side == 1 else close > max(s20, e20))


def _md_cross_wrong_side(frame: pd.DataFrame, i: int, side: int) -> bool:
    if i == 0:
        return False
    prev_md, prev_sb, md, sb = (float(frame[name].iloc[j]) for j in (i - 1, i) for name in ("md", "sb"))
    if not _finite(prev_md, prev_sb, md, sb):
        return False
    return (prev_md >= prev_sb and md < sb) if side == 1 else (prev_md <= prev_sb and md > sb)


def _policy_exit(policy: str, frame: pd.DataFrame, i: int, side: int) -> str | None:
    if policy == "fast_ma_exit" and _fast_ma_wrong_side(frame, i, side):
        return "fast_ma_other_side_next_open"
    if policy == "md_cross_exit" and _md_cross_wrong_side(frame, i, side):
        return "md_sb_reverse_cross_next_open"
    return None


def _be_level(policy: str, entry: float, side: int, close_r: float) -> tuple[float, str] | None:
    if policy == "be095_price" and close_r >= .95:
        return entry, "be095_price"
    if policy == "be1_price" and close_r >= 1.0:
        return entry, "be1_price"
    if policy in {"be1_cost", "partial_be1_cost"} and close_r >= 1.0:
        return entry + side * .002 * entry, "be1_cost"
    return None


def _partial_request(policy: str, close_r: float, done_1: bool, done_3: bool) -> tuple[float, str, bool, bool] | None:
    """Schedule fractions in original-position units; a 3R jump sells 60% once."""
    if policy not in {"partial_1_25", "partial_1_25_3_35", "partial_be1_cost"}:
        return None
    has_3 = policy in {"partial_1_25_3_35", "partial_be1_cost"}
    if has_3 and close_r >= 3.0 and not done_3:
        if not done_1:
            return .60, "partial_1r_25_and_3r_35_next_open", True, True
        return .35, "partial_3r_35_next_open", True, True
    if close_r >= 1.0 and not done_1:
        return .25, "partial_1r_25_next_open", True, done_3
    return None


def _new_trade(pos: dict[str, object], *, trade_id: str, cohort: str, policy: str,
               context: StreamContext) -> dict[str, object]:
    return {**pos, "trade_id": trade_id, "cohort": cohort, "policy": policy, "stream_key": context.key,
            **context.identity, "tick": float(context.cache["tick"]), "qty_initial": 1.0, "qty_realized": 0.0,
            "qty_remaining": 1.0, "realized_gross_return": 0.0, "realized_net_return": -ENTRY_COST,
            "last_exit_price": math.nan, "last_exit_reason": None, "last_exit_i": None, "last_exit_time": None}


def _append_fill(fills: list[dict[str, object]], pos: dict[str, object], *, leg_no: int, bar_open: object,
                 event_time: object, phase: str, precision: str, kind: str, fraction: float, price: float,
                 cost: float, reason: str, context: StreamContext) -> None:
    pos["fill_count"] = leg_no
    fills.append({"trade_id": pos["trade_id"], "leg_no": leg_no, "cohort": pos["cohort"], "policy": pos["policy"],
                  "stream_key": context.key, **context.identity, "side": int(pos["side"]), "entry_time": pos["entry_time"],
                  "bar_open": bar_open, "event_time": event_time, "execution_phase": phase,
                  "event_time_precision": precision, "kind": kind, "qty_fraction": fraction, "price": price,
                  "cost_return": cost, "risk": float(pos["initial_risk"]), "initial_risk": float(pos["initial_risk"]),
                  "tick": float(context.cache["tick"]), "reason": reason})


def _trade_row(pos: dict[str, object], *, censored: bool, precision: str) -> dict[str, object]:
    realized = float(pos["qty_realized"])
    gross, net = float(pos["realized_gross_return"]), float(pos["realized_net_return"])
    risk_frac = float(pos["initial_risk_frac"])
    return {key: pos.get(key) for key in TRADE_COLUMNS} | {
        "qty_realized": realized, "qty_remaining": float(pos["qty_remaining"]),
        "exit_i": (None if pos["last_exit_i"] is None else int(pos["last_exit_i"]) + int(pos.get("frozen_index_offset", 0))),
        "exit_time": pos["last_exit_time"], "exit_price": pos["last_exit_price"],
        "exit_reason": pos["last_exit_reason"], "gross_return": math.nan if censored else gross,
        "net_return": math.nan if censored else net, "gross_r": math.nan if censored else gross / risk_frac,
        "net_r": math.nan if censored else net / risk_frac, "censored": censored,
        "exit_time_precision": precision,
    }


def replay_policy(context: StreamContext, *, cohort: str, policy: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay one policy from raw candidates so exit timing can alter later entries."""
    if policy not in POLICIES or cohort not in COHORTS:
        raise ValueError("unknown policy or cohort")
    frame = context.cache["bars"]
    frame.attrs["minutes"] = context.minutes
    signals, allowed = _cohort_inputs(context, cohort)
    gap = context.cache["data_gap"].reindex(frame.index).fillna(True).astype(bool)
    spec = ExecutionSpec(tick=float(context.cache["tick"]))
    raw_side = np.where(signals.long_signal.fillna(False), 1, np.where(signals.short_signal.fillna(False), -1, 0))
    if (signals.long_signal.fillna(False).astype(bool) & signals.short_signal.fillna(False).astype(bool)).any():
        raise ValueError("ambiguous frozen signal side")
    fills: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_exit: tuple[int, int, str] | None = None
    pending_partial: tuple[float, str] | None = None
    next_id = 0
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr", "s20", "e20", "md", "sb")}
    oa,ha,la,ca,aa = (arrays[k] for k in ("open","high","low","close","atr"))
    gap_a, allowed_a = gap.to_numpy(bool), allowed.to_numpy(bool)
    ordinal = {}
    if len(context.signals_ledger):
        ledger = context.signals_ledger
        ordinal = dict(zip(pd.to_datetime(ledger.signal_bar_open,utc=True),ledger.signal_i.astype(int)))
    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if bool(gap_a[i]):
            if position is not None:
                _append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1,
                             bar_open=stamp, event_time=stamp, phase="close", precision="unknown_gap", kind="censor",
                             fraction=float(position["qty_remaining"]), price=math.nan, cost=0.0, reason="data_gap_censored", context=context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"
                trades.append(_trade_row(position, censored=True, precision="unknown_gap"))
            position = None
            pending_entry = pending_exit = pending_partial = None
            continue
        # A stop known at the prior close has priority over a next-open discretionary action.
        if position is not None:
            side, protection, opening = int(position["side"]), float(position["protection"]), float(oa[i])
            stop_at_open = opening <= protection if side == 1 else opening >= protection
            if stop_at_open:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                fraction = float(position["qty_remaining"])
                cost = fraction * EXIT_COST
                gross = fraction * side * (opening / float(position["entry_price"]) - 1.0)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - cost
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, opening, reason
                _append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1,
                             bar_open=stamp, event_time=stamp, phase="open", precision="bar_open", kind="exit", fraction=fraction,
                             price=opening, cost=cost, reason=reason, context=context)
                trades.append(_trade_row(position, censored=False, precision="bar_open_or_intrabar_window")); ended_side = side
                position = None; pending_exit = pending_partial = None
        if position is not None and pending_exit is not None:
            _, old_side, reason = pending_exit
            if int(position["side"]) == old_side:
                opening, fraction = float(oa[i]), float(position["qty_remaining"])
                cost = fraction * EXIT_COST; gross = fraction * old_side * (opening / float(position["entry_price"]) - 1.0)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - cost
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, opening, reason
                _append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1,
                             bar_open=stamp, event_time=stamp, phase="open", precision="bar_open", kind="exit", fraction=fraction,
                             price=opening, cost=cost, reason=reason, context=context)
                trades.append(_trade_row(position, censored=False, precision="bar_open_or_intrabar_window")); ended_side = old_side
                position = None; pending_partial = None
            pending_exit = None
        if position is not None and pending_partial is not None:
            fraction, reason = pending_partial
            fraction = min(float(fraction), float(position["qty_remaining"]))
            if fraction > 0:
                opening, side = float(oa[i]), int(position["side"])
                cost = fraction * EXIT_COST; gross = fraction * side * (opening / float(position["entry_price"]) - 1.0)
                position["qty_realized"] += fraction; position["qty_remaining"] -= fraction
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - cost
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, opening, reason
                _append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1,
                             bar_open=stamp, event_time=stamp, phase="open", precision="bar_open", kind="partial", fraction=fraction,
                             price=opening, cost=cost, reason=reason, context=context)
            pending_partial = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = _initial_position_fast(frame.index, oa,ha,la,ca,aa,gap_a,signal_i,side,spec)
                if made is not None:
                    made["initial_risk_frac"] = float(made["initial_risk"])/float(made["entry_price"])
                    original_i = ordinal.get(frame.index[signal_i])
                    if original_i is not None:
                        # The cache retains only a short authenticated prefix;
                        # outputs retain the old full-frame ordinal for ledger parity.
                        made["signal_i"], made["entry_i"] = original_i, original_i + 1
                        made["frozen_index_offset"] = original_i - signal_i
                    next_id += 1
                    position = _new_trade(made, trade_id=f"{context.key}:{cohort}:{policy}:{next_id}", cohort=cohort, policy=policy, context=context)
                    _append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open",
                                 kind="entry", fraction=1.0, price=float(position["entry_price"]), cost=ENTRY_COST, reason="next_open_entry", context=context)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            opening, high, low, close, atr = (arr[i] for arr in (oa,ha,la,ca,aa))
            stopped = low <= protection if side == 1 else high >= protection
            if stopped:
                price = min(opening, protection) if side == 1 else max(opening, protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if opening <= protection if side == 1 else opening >= protection:
                    reason += "_gap"
                fraction = float(position["qty_remaining"]); cost = fraction * EXIT_COST
                gross = fraction * side * (price / float(position["entry_price"]) - 1.0)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - cost
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, price, reason
                phase = "open" if reason.endswith("_gap") else "intrabar"
                _append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1,
                             bar_open=stamp, event_time=stamp, phase=phase, precision="bar_open" if phase == "open" else "within_bar",
                             kind="exit", fraction=fraction, price=price, cost=cost, reason=reason, context=context)
                trades.append(_trade_row(position, censored=False, precision="bar_open_or_intrabar_window")); ended_side = side
                position = None; pending_exit = pending_partial = None
            else:
                position["mfe_r"] = max(float(position["mfe_r"]), side * ((high if side == 1 else low) - float(position["entry_price"])) / float(position["initial_risk"]))
                close_r = side * (close - float(position["entry_price"])) / float(position["initial_risk"])
                before = float(position["protection"])
                be = _be_level(policy, float(position["entry_price"]), side, close_r)
                if be is not None:
                    candidate, reason = be
                    position["protection"] = max(before, candidate) if side == 1 else min(before, candidate)
                    if float(position["protection"]) != before:
                        events.append({"trade_id": position["trade_id"], "cohort": cohort, "policy": policy, "stream_key": context.key,
                                       "bar_open": stamp, "event_time": _close_time(frame.index, i, context.minutes), "execution_phase": "close",
                                       "event_kind": "protection_update", "reason": reason, "protection_before": before,
                                       "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"])})
                position["trail_armed"] = bool(position["trail_armed"]) or close_r >= spec.arm_r
                if bool(position["trail_armed"]) and _finite(atr) and atr > 0:
                    candidate_raw = close - side * spec.trail_atr * atr
                    candidate = math.floor(candidate_raw / spec.tick) * spec.tick if side == 1 else math.ceil(candidate_raw / spec.tick) * spec.tick
                    before = float(position["protection"])
                    position["protection"] = max(before, candidate) if side == 1 else min(before, candidate)
                    position["trail_armed"] = True
                    if float(position["protection"]) != before:
                        events.append({"trade_id": position["trade_id"], "cohort": cohort, "policy": policy, "stream_key": context.key,
                                       "bar_open": stamp, "event_time": _close_time(frame.index, i, context.minutes), "execution_phase": "close",
                                       "event_kind": "protection_update", "reason": "trail_2r_4atr", "protection_before": before,
                                       "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"])})
                request = _partial_request(policy, close_r, bool(position.get("partial_1_done", False)), bool(position.get("partial_3_done", False)))
                if request is not None:
                    fraction, reason, done_1, done_3 = request
                    position["partial_1_done"], position["partial_3_done"] = done_1, done_3
                    pending_partial = (fraction, reason)
                    events.append({"trade_id": position["trade_id"], "cohort": cohort, "policy": policy, "stream_key": context.key,
                                   "bar_open": stamp, "event_time": _close_time(frame.index, i, context.minutes), "execution_phase": "close",
                                   "event_kind": "partial_scheduled", "reason": reason, "protection_before": float(position["protection"]),
                                   "protection_after": float(position["protection"]), "qty_fraction": fraction})
                extra = None
                if policy == "fast_ma_exit" and (close < min(arrays["s20"][i],arrays["e20"][i]) if side==1 else close > max(arrays["s20"][i],arrays["e20"][i])):
                    extra = "fast_ma_other_side_next_open"
                if policy == "md_cross_exit" and i > 0:
                    previous = arrays["md"][i-1]-arrays["sb"][i-1]
                    current = arrays["md"][i]-arrays["sb"][i]
                    if (previous >= 0 and current < 0) if side==1 else (previous <= 0 and current > 0):
                        extra = "md_sb_reverse_cross_next_open"
                if extra is not None:
                    pending_exit = (i, side, extra)
                    events.append({"trade_id": position["trade_id"], "cohort": cohort, "policy": policy, "stream_key": context.key,
                                   "bar_open": stamp, "event_time": _close_time(frame.index, i, context.minutes), "execution_phase": "close",
                                   "event_kind": "exit_scheduled", "reason": extra, "protection_before": float(position["protection"]),
                                   "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"])})
        signal_side = int(raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]) and policy != "no_reverse":
                pending_exit = (i, int(position["side"]), "opposite_v6_next_open")
                if bool(allowed_a[i]):
                    pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and bool(allowed_a[i]):
                pending_entry = (i, signal_side)
    if position is not None:
        last, stamp = len(frame) - 1, frame.index[-1]
        _append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1,
                     bar_open=stamp, event_time=_close_time(frame.index, last, context.minutes), phase="close", precision="last_complete_close",
                     kind="censor", fraction=float(position["qty_remaining"]), price=float(ca[last]), cost=0.0,
                     reason="boundary_mark", context=context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = last, stamp, float(ca[last]), "boundary_mark"
        trades.append(_trade_row(position, censored=True, precision="last_complete_close"))
    return (pd.DataFrame(trades, columns=TRADE_COLUMNS), pd.DataFrame(fills, columns=FILL_COLUMNS),
            pd.DataFrame(events, columns=EVENT_COLUMNS))


def replay_stream(context: StreamContext, policies: Iterable[str] = POLICIES) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay all frozen cohorts/policies for one stream and retain elapsed time."""
    started = perf_counter(); tables = []
    for cohort in COHORTS:
        for policy in policies:
            trades, fills, events = replay_policy(context, cohort=cohort, policy=policy)
            tables.append((trades, fills, events))
    trades = pd.concat([x[0] for x in tables], ignore_index=True) if tables else pd.DataFrame(columns=TRADE_COLUMNS)
    fills = pd.concat([x[1] for x in tables], ignore_index=True) if tables else pd.DataFrame(columns=FILL_COLUMNS)
    events = pd.concat([x[2] for x in tables], ignore_index=True) if tables else pd.DataFrame(columns=EVENT_COLUMNS)
    summary = pd.DataFrame([{"stream_key": context.key, **context.identity, "timeframe_min": context.minutes,
                             "source_sha256": context.receipt["source_sha256"], "cache_sha256": context.receipt["cache_sha256"],
                             "trades": len(trades), "fills": len(fills), "events": len(events),
                             "elapsed_seconds": perf_counter() - started}])
    return trades, fills, events, summary


def validate_baseline(context, trades):
    """Every completed stream must reproduce its frozen unmodified ledger."""
    from pandas.testing import assert_frame_equal
    old=pd.read_csv(context.path/"trades.csv.gz")
    for cohort,variant in zip(COHORTS,["v1_common_execution_long","v6_unfiltered_both","v7_bb_both"]):
        a=old.loc[old.variant.eq(variant)&~old.censored.astype(bool)]
        b=trades.loc[trades.cohort.eq(cohort)&trades.policy.eq("baseline")&~trades.censored.astype(bool)]
        if a.empty and b.empty:continue
        cols=["signal_i","entry_i","side","exit_i","exit_reason","entry_price","exit_price","initial_stop","initial_risk","net_return","net_r"]
        assert_frame_equal(a[cols].sort_values("signal_i").reset_index(drop=True),b[cols].sort_values("signal_i").reset_index(drop=True),check_dtype=False,rtol=1e-9,atol=1e-9)


def run_streams(streams_root: Path, output: Path, *, stream_limit: int | None = None, resume: bool = True) -> pd.DataFrame:
    """Write atomic per-stream engine artifacts; resume never treats a partial stream as complete."""
    config_path=Path("experiments/active/exp-spike-exit-policy-20260912-v1/config.json")
    config=json.loads(config_path.read_text())
    manifest_path=streams_root.parent/"manifest.json"
    if sha256(manifest_path)!=config["raw_manifest_sha256"]:
        raise ValueError("frozen upstream manifest mismatch")
    identity={"config_sha256":sha256(config_path),"engine_sha256":sha256(Path(__file__)),"upstream_manifest_sha256":sha256(manifest_path)}
    for name in ["spike_v6_wvf_study.py","spike_v7_fast.py"]:
        identity[name]=sha256(Path(__file__).with_name(name))
    output.mkdir(parents=True, exist_ok=True)
    identity_path=output/"engine_identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text())!=identity:
        raise ValueError("source/config changed; use new output directory")
    identity_path.write_text(json.dumps(identity,indent=2))
    stream_output = output / "streams"; stream_output.mkdir(exist_ok=True)
    summaries = []
    folders = sorted(path.parent for path in streams_root.glob("*/completion.json") if not path.parent.name.startswith("."))
    if len(folders)!=config["expected_streams"]:raise ValueError("incomplete upstream pool")
    if stream_limit is not None:
        folders = folders[:stream_limit]
    for folder in folders:
        complete = stream_output / f"{folder.name}.completion.json"
        if resume and complete.is_file():
            row=json.loads(complete.read_text())
            for name,digest in row["output_sha256"].items():
                if sha256(stream_output/name)!=digest:raise ValueError("completed stream output changed")
            summaries.append(row); continue
        context = load_verified_stream(folder)
        trades, fills, events, summary = replay_stream(context)
        validate_baseline(context,trades)
        for name, table in (("trades", trades), ("fills", fills), ("events", events)):
            temporary = stream_output / f".{context.key}.{name}.csv.gz.tmp"
            destination = stream_output / f"{context.key}.{name}.csv.gz"
            table.to_csv(temporary, index=False, compression={"method":"gzip","compresslevel":1,"mtime":0})
            temporary.replace(destination)
        row = summary.iloc[0].to_dict()
        row["output_sha256"]={f"{context.key}.{name}.csv.gz":sha256(stream_output/f"{context.key}.{name}.csv.gz") for name in ("trades","fills","events")}
        temp_complete = stream_output / f".{context.key}.completion.json.tmp"
        temp_complete.write_text(json.dumps(row, indent=2, default=str)); temp_complete.replace(complete)
        summaries.append(row)
        if len(summaries)%25==0 or len(summaries)==len(folders):
            print(json.dumps({"completed":len(summaries),"target":len(folders)}),flush=True)
    result = pd.DataFrame(summaries)
    if len(result): result.to_csv(output / "engine_stream_summary.csv", index=False)
    (output/"engine_manifest.json").write_text(json.dumps({**identity,"completed_streams":len(summaries),"expected_streams":config["expected_streams"],"complete":len(summaries)==config["expected_streams"],"summary_sha256":sha256(output/"engine_stream_summary.csv")},indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams-root", type=Path, default=SOURCE_STREAMS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stream-limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_streams(args.streams_root, args.output, stream_limit=args.stream_limit, resume=not args.no_resume)
    print(json.dumps({"streams": len(result), "elapsed_seconds": float(result.elapsed_seconds.sum()) if len(result) else 0.0}))


if __name__ == "__main__":
    main()
