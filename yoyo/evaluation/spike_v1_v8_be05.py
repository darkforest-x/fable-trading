"""Causal 0.5R price-BE sensitivity for frozen SPIKE V1-common and V8.

This study deliberately changes one exit rule only.  Once a completed bar's
favourable high/low reaches 0.5 times the *frozen actual next-open initial
risk*, protection may ratchet to the actual entry price for the next bar.  It
never uses a full-history MFE to decide an earlier stop, and it never loosens
an existing tighter protection.  A price BE still pays the frozen 0.2 percent
round-trip cost; it is not a net-of-fee break-even claim.

V1 here is the separately labelled ``v1_common_execution_long`` sensitivity,
not V1's native execution.  Native V1 is handled by its own frozen replay
seam outside this module so the two definitions cannot be mixed.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess
from time import perf_counter, time_ns
from dataclasses import dataclass
from collections.abc import Callable

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation.spike_v8_replay import v8_admissions


EXP = Path("experiments/active/exp-spike-v1-v8-be05-20260914-v1")
CONFIG = EXP / "common/config.json"
PLAN = EXP / "common/PLAN.md"
TEST = Path("tests/test_spike_v1_v8_be05.py")
RAW = Path("experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3")
V8_ORACLE = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1/replay_v1")
BE_TRIGGER_R = 0.5
ARMS = ("v1_common_execution_long", "v8")
KEY = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"]
FIXED_COLUMNS = [*base.TRADE_COLUMNS, "be_armed", "be_trigger_count"]
PAIR_COLUMNS = ["signal_i_baseline", "entry_i_baseline", "side_baseline", "net_r_baseline", "net_return_baseline", "mfe_r_baseline", "censored_baseline", "signal_i_be", "entry_i_be", "side_be", "net_r_be", "net_return_be", "mfe_r_be", "censored_be", "delta_r", "reduced_loss", "harmed_winner", "baseline_mfe_ge_10r", "baseline_realized_ge_10r", "retained_realized_ge_10r"]
LEGACY_CLEAN_COLUMNS = ["signal_i", "entry_i", "side", "legacy_exit_i", "clean_exit_i", "legacy_exit_reason", "clean_exit_reason", "legacy_censored", "clean_censored", "legacy_net_r", "clean_net_r", "delta_net_r", "legacy_net_return", "clean_net_return", "delta_net_return"]


def sha256(path: Path) -> str:
    """Return a content identity for immutable-source receipts."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def v8_context(context: base.StreamContext) -> base.StreamContext:
    """Inject only V8's frozen admission while retaining raw opposite exits."""
    gates = v8_admissions(context)
    cache = dict(context.cache)
    cache["bb"] = context.cache["bb"].copy()
    cache["bb"]["prior_squeeze_run3"] = gates.v8.reindex(cache["bb"].index).fillna(False).astype(bool)
    return replace(context, cache=cache)


def arm_context(context: base.StreamContext, arm: str) -> tuple[base.StreamContext, str]:
    """Map each frozen entry contract to its one allowed replay cohort."""
    if arm == "v1_common_execution_long":
        return context, "v1_common_long"
    if arm == "v8":
        return v8_context(context), "v7_both"
    raise ValueError(f"unsupported arm: {arm}")


@dataclass(frozen=True)
class PreparedArm:
    """One cached arm context, preventing per-entry V8 admission recomputation."""

    context: base.StreamContext
    arm: str
    cohort: str
    frame: pd.DataFrame
    gap: np.ndarray
    allowed: np.ndarray
    raw_side: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    ordinal: dict[pd.Timestamp, int]
    spec: base.ExecutionSpec


def prepare_arm(context: base.StreamContext, *, arm: str) -> PreparedArm:
    """Materialize immutable per-stream/arm arrays once for serial and paired paths."""
    arm_context_value, cohort = arm_context(context, arm)
    frame = arm_context_value.cache["bars"]
    signals, allowed = base._cohort_inputs(arm_context_value, cohort)
    long, short = signals.long_signal.fillna(False).to_numpy(bool), signals.short_signal.fillna(False).to_numpy(bool)
    if (long & short).any():
        raise ValueError("ambiguous frozen signal side")
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")}
    return PreparedArm(arm_context_value, arm, cohort, frame,
                       arm_context_value.cache["data_gap"].reindex(frame.index).fillna(True).astype(bool).to_numpy(bool),
                       allowed.to_numpy(bool), np.where(long, 1, np.where(short, -1, 0)),
                       arrays["open"], arrays["high"], arrays["low"], arrays["close"], arrays["atr"],
                       dict(zip(pd.to_datetime(arm_context_value.signals_ledger.signal_bar_open, utc=True), arm_context_value.signals_ledger.signal_i.astype(int))),
                       base.ExecutionSpec(tick=float(arm_context_value.cache["tick"])))


def _favourable_r(side: int, entry: float, risk: float, high: float, low: float) -> float:
    """Return this observed bar's favourable excursion in frozen initial-R units."""
    favourable = high if side == 1 else low
    return side * (favourable - entry) / risk


def _be_touched(side: int, entry: float, risk: float, high: float, low: float) -> bool:
    """Use only the current completed OHLC bar to test the fixed 0.5R trigger."""
    return math.isfinite(risk) and risk > 0 and _favourable_r(side, entry, risk, high, low) >= BE_TRIGGER_R


def _raise_to_entry(position: dict[str, object]) -> bool:
    """Ratchet protection to price BE without weakening a tighter frozen stop."""
    before, entry, side = float(position["protection"]), float(position["entry_price"]), int(position["side"])
    after = max(before, entry) if side == 1 else min(before, entry)
    position["protection"] = after
    return after != before


def _close_updates(position: dict[str, object], *, high: float, low: float, close: float, atr: float,
                   spec: base.ExecutionSpec, enable_be: bool, events: list[dict[str, object]],
                   context: base.StreamContext, arm: str, policy: str, i: int) -> None:
    """Apply close-confirmed BE then unchanged trail, both effective next bar.

    Inputs are high/low/close from the observed bar.  The current bar's stop
    was already tested by the caller, so a trigger cannot create a same-bar
    retroactive exit.
    """
    side, entry, risk = int(position["side"]), float(position["entry_price"]), float(position["initial_risk"])
    position["mfe_r"] = max(float(position["mfe_r"]), _favourable_r(side, entry, risk, high, low))
    if enable_be and not bool(position.get("be_armed", False)) and _be_touched(side, entry, risk, high, low):
        position["be_armed"] = True
        position["be_trigger_count"] = int(position.get("be_trigger_count", 0)) + 1
        before = float(position["protection"])
        changed = _raise_to_entry(position)
        events.append({"trade_id": position["trade_id"], "cohort": position["cohort"], "policy": policy,
                       "stream_key": context.key, "bar_open": context.cache["bars"].index[i],
                       "event_time": context.cache["bars"].index[i] + pd.Timedelta(minutes=context.minutes),
                       "execution_phase": "close", "event_kind": "protection_update",
                       "reason": "be05_price_next_bar", "protection_before": before,
                       "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"]),
                       "arm": arm, "changed_protection": changed})
    current_r = side * (close - entry) / risk
    position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
    if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
        raw = close - side * spec.trail_atr * atr
        candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
        before = float(position["protection"])
        position["protection"] = max(before, candidate) if side == 1 else min(before, candidate)


def replay_serial(context: base.StreamContext, *, arm: str, enable_be: bool, prepared: PreparedArm | None = None,
                  initial_transform: Callable[[dict[str, object], int, int], dict[str, object] | None] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay the clean serial contract, clearing an intent when its owner closes.

    ``pending_reverse`` belongs to the position that scheduled it.  Every
    terminal close, including an opening gap, therefore clears that intent
    before a subsequent entry can be considered.  This preserves the frozen
    source engine's intent lifetime while allowing the BE treatment to alter
    the sequence of later entries causally.
    """
    prepared = prepare_arm(context, arm=arm) if prepared is None else prepared
    if prepared.arm != arm:
        raise ValueError("prepared arm differs from requested arm")
    context, cohort, frame, spec = prepared.context, prepared.cohort, prepared.frame, prepared.spec
    gap, allowed, raw_side = prepared.gap, prepared.allowed, prepared.raw_side
    oa, ha, la, ca, aa, ordinal = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr, prepared.ordinal
    trades: list[dict[str, object]] = []
    fills: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_reverse: tuple[int, int] | None = None
    next_id = 0
    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if bool(gap[i]):
            if position is not None:
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="close", precision="unknown_gap", kind="censor", fraction=float(position["qty_remaining"]),
                                  price=math.nan, cost=0.0, reason="data_gap_censored", context=context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"
                trades.append(base._trade_row(position, censored=True, precision="unknown_gap"))
            position = None; pending_entry = pending_reverse = None
            continue
        # A protection that was active before this open always wins a scheduled reverse.
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            stop_open = oa[i] <= protection if side == 1 else oa[i] >= protection
            if stop_open:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                fraction = float(position["qty_remaining"]); gross = fraction * side * (oa[i] / float(position["entry_price"]) - 1)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, oa[i], reason
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="open", precision="bar_open", kind="exit", fraction=fraction, price=oa[i], cost=fraction * base.EXIT_COST, reason=reason, context=context)
                trades.append(base._trade_row(position, censored=False, precision="bar_open_or_intrabar_window"))
                ended_side, position, pending_reverse = side, None, None
        if position is not None and pending_reverse is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                fraction = float(position["qty_remaining"]); gross = fraction * old_side * (oa[i] / float(position["entry_price"]) - 1)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, oa[i], "opposite_v6_next_open"
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="open", precision="bar_open", kind="exit", fraction=fraction, price=oa[i], cost=fraction * base.EXIT_COST, reason="opposite_v6_next_open", context=context)
                trades.append(base._trade_row(position, censored=False, precision="bar_open_or_intrabar_window"))
                ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = base._initial_position_fast(frame.index, oa, ha, la, ca, aa, gap, signal_i, side, spec)
                if made is not None and initial_transform is not None:
                    made = initial_transform(made, signal_i, side)
                if made is not None:
                    made["initial_risk_frac"] = float(made["initial_risk"]) / float(made["entry_price"])
                    original_i = ordinal.get(frame.index[signal_i])
                    if original_i is not None:
                        made["signal_i"], made["entry_i"], made["frozen_index_offset"] = original_i, original_i + 1, original_i - signal_i
                    next_id += 1
                    position = base._new_trade(made, trade_id=f"{context.key}:{arm}:{'be05' if enable_be else 'baseline'}:{next_id}", cohort=cohort,
                                               policy="be05" if enable_be else "baseline", context=context)
                    position.update(be_armed=False, be_trigger_count=0)
                    base._append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open",
                                      kind="entry", fraction=1., price=float(position["entry_price"]), cost=base.ENTRY_COST, reason="next_open_entry", context=context)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            stopped = la[i] <= protection if side == 1 else ha[i] >= protection
            if stopped:
                price = min(oa[i], protection) if side == 1 else max(oa[i], protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if oa[i] <= protection if side == 1 else oa[i] >= protection: reason += "_gap"
                fraction = float(position["qty_remaining"]); gross = fraction * side * (price / float(position["entry_price"]) - 1)
                position["qty_realized"] += fraction; position["qty_remaining"] = 0.0
                position["realized_gross_return"] += gross; position["realized_net_return"] += gross - fraction * base.EXIT_COST
                position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, price, reason
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="open" if reason.endswith("_gap") else "intrabar", precision="bar_open" if reason.endswith("_gap") else "within_bar",
                                  kind="exit", fraction=fraction, price=price, cost=fraction * base.EXIT_COST, reason=reason, context=context)
                trades.append(base._trade_row(position, censored=False, precision="bar_open_or_intrabar_window")); ended_side, position, pending_reverse = side, None, None
            else:
                _close_updates(position, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, enable_be=enable_be,
                               events=events, context=context, arm=arm, policy="be05" if enable_be else "baseline", i=i)
        signal_side = int(raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if bool(allowed[i]): pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and bool(allowed[i]):
                pending_entry = (i, signal_side)
    if position is not None:
        last, stamp = len(frame) - 1, frame.index[-1]
        base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                          event_time=stamp + pd.Timedelta(minutes=context.minutes), phase="close", precision="last_complete_close",
                          kind="censor", fraction=float(position["qty_remaining"]), price=ca[last], cost=0., reason="boundary_mark", context=context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = last, stamp, ca[last], "boundary_mark"
        trades.append(base._trade_row(position, censored=True, precision="last_complete_close"))
    return (pd.DataFrame(trades, columns=base.TRADE_COLUMNS), pd.DataFrame(fills, columns=base.FILL_COLUMNS),
            pd.DataFrame(events))


def replay_fixed_entry(context: base.StreamContext, row: pd.Series, *, arm: str, enable_be: bool, prepared: PreparedArm | None = None) -> dict[str, object]:
    """Re-evaluate exactly one original entry; no BE-created re-entry is possible."""
    prepared = prepare_arm(context, arm=arm) if prepared is None else prepared
    if prepared.arm != arm:
        raise ValueError("prepared arm differs from requested arm")
    context, cohort, frame, gap, raw_side, spec = prepared.context, prepared.cohort, prepared.frame, prepared.gap, prepared.raw_side, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    side = int(row.side); start = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    pos = {key: row[key] for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    # Serial output preserves full-frame ordinals even though each cache begins
    # at a shorter authenticated prefix.  Fixed exits need the same offset.
    pos["frozen_index_offset"] = int(row.entry_i) - start
    pos = base._new_trade(pos, trade_id=f"{context.key}:{arm}:fixed", cohort=cohort,
                          policy="be05" if enable_be else "baseline", context=context)
    pos.update(protection=float(row.initial_stop), mfe_r=0., trail_armed=False, be_armed=False, be_trigger_count=0)
    pending_reverse: int | None = None
    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if gap[i]:
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, stamp, "data_gap_censored"
            return base._trade_row(pos, censored=True, precision="unknown_gap") | {"be_armed": pos["be_armed"], "be_trigger_count": pos["be_trigger_count"]}
        protection = float(pos["protection"])
        if pending_reverse is not None:
            if side == pending_reverse:
                price = oa[i]; reason = ("trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap") if (price <= protection if side == 1 else price >= protection) else "opposite_v6_next_open"
                pos["qty_realized"], pos["qty_remaining"] = 1., 0.; pos["realized_gross_return"] = side * (price / float(pos["entry_price"]) - 1); pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
                pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
                return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | {"be_armed": pos["be_armed"], "be_trigger_count": pos["be_trigger_count"], "protection": protection}
            pending_reverse = None
        if la[i] <= protection if side == 1 else ha[i] >= protection:
            price = min(oa[i], protection) if side == 1 else max(oa[i], protection); reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if oa[i] <= protection if side == 1 else oa[i] >= protection: reason += "_gap"
            pos["qty_realized"], pos["qty_remaining"] = 1., 0.; pos["realized_gross_return"] = side * (price / float(pos["entry_price"]) - 1); pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
            return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | {"be_armed": pos["be_armed"], "be_trigger_count": pos["be_trigger_count"], "protection": protection}
        _close_updates(pos, high=ha[i], low=la[i], close=ca[i], atr=aa[i], spec=spec, enable_be=enable_be, events=[], context=context, arm=arm, policy="be05", i=i)
        if int(raw_side[i]) == -side:
            pending_reverse = side
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = len(frame)-1, frame.index[-1], ca[-1], "boundary_mark"
    return base._trade_row(pos, censored=True, precision="last_complete_close") | {"be_armed": pos["be_armed"], "be_trigger_count": pos["be_trigger_count"], "protection": float(pos["protection"])}


def replay_legacy_baseline(context: base.StreamContext, *, arm: str, prepared: PreparedArm | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay the archived baseline engine verbatim for a parity-only receipt.

    This is intentionally not a BE arm.  The canonical study tables use the
    clean replay above; this ledger makes the frozen-source comparison and any
    archived-to-clean divergence explicit.
    """
    prepared = prepare_arm(context, arm=arm) if prepared is None else prepared
    if prepared.arm != arm:
        raise ValueError("prepared arm differs from requested arm")
    return base.replay_policy(prepared.context, cohort=prepared.cohort, policy="baseline")


def _oracle_path(context: base.StreamContext, arm: str) -> Path:
    """Resolve the exact frozen per-stream baseline oracle for one arm."""
    if arm == "v1_common_execution_long":
        return context.path / "trades.csv.gz"
    if arm == "v8":
        return V8_ORACLE / "streams" / f"{context.key}.trades.csv.gz"
    raise ValueError(arm)


def validate_baseline(context: base.StreamContext, trades: pd.DataFrame, *, arm: str) -> dict[str, str]:
    """Require exact closed baseline parity against the arm's frozen oracle."""
    from pandas.testing import assert_frame_equal
    path = _oracle_path(context, arm)
    expected = pd.read_csv(path)
    expected = expected.query("variant == 'v1_common_execution_long'") if arm == "v1_common_execution_long" else expected.query("arm == 'v8'")
    actual = trades.loc[~trades.censored.astype(bool)]
    expected = expected.loc[~expected.censored.astype(bool)]
    assert_frame_equal(expected.reindex(columns=KEY).sort_values("signal_i").reset_index(drop=True),
                       actual.reindex(columns=KEY).sort_values("signal_i").reset_index(drop=True), check_dtype=False, rtol=1e-9, atol=1e-9)
    return {"arm": arm, "path": str(path), "sha256": sha256(path)}


def validate_fixed_baseline(serial: pd.DataFrame, fixed: pd.DataFrame) -> None:
    """Require paired baseline exits to retain serial baseline fees and fills exactly."""
    from pandas.testing import assert_frame_equal
    fields = [*KEY, "censored"]
    left = serial.loc[:, fields].sort_values("signal_i").reset_index(drop=True)
    right = fixed.loc[:, fields].sort_values("signal_i").reset_index(drop=True)
    assert_frame_equal(left, right, check_dtype=False, rtol=1e-9, atol=1e-9)


def legacy_clean_events(legacy: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    """Record every archived-to-clean outcome difference, including zero rows."""
    fields = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "censored", "net_r", "net_return"]
    left = legacy.reindex(columns=fields).add_prefix("legacy_")
    right = clean.reindex(columns=fields).add_prefix("clean_")
    keys_left, keys_right = [f"legacy_{x}" for x in ("signal_i", "entry_i", "side")], [f"clean_{x}" for x in ("signal_i", "entry_i", "side")]
    both = left.merge(right, left_on=keys_left, right_on=keys_right, how="outer", indicator=True, validate="one_to_one")
    for name in ("signal_i", "entry_i", "side"):
        both[name] = both[f"legacy_{name}"].combine_first(both[f"clean_{name}"])
    equal_float = lambda a, b: np.isclose(a.astype(float), b.astype(float), equal_nan=True)
    same = (both._merge.eq("both") & both.legacy_exit_i.eq(both.clean_exit_i) & both.legacy_exit_reason.eq(both.clean_exit_reason)
            & both.legacy_censored.eq(both.clean_censored) & equal_float(both.legacy_net_r, both.clean_net_r)
            & equal_float(both.legacy_net_return, both.clean_net_return))
    changed = both.loc[~same].copy()
    changed["delta_net_r"] = changed.clean_net_r - changed.legacy_net_r
    changed["delta_net_return"] = changed.clean_net_return - changed.legacy_net_return
    return changed.reindex(columns=LEGACY_CLEAN_COLUMNS)


def summarize(trades: pd.DataFrame, *, label: str) -> dict[str, object]:
    """Keep closed/censored, nominal return and initial-R measures separate."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    r, nominal = closed.net_r.astype(float), closed.net_return.astype(float)
    def pf(values: pd.Series) -> float:
        loss = -values.loc[values < 0].sum(); return float(values.loc[values > 0].sum() / loss) if loss else math.inf
    return {"label": label, "trades": len(trades), "closed": len(closed), "censored": int(trades.censored.astype(bool).sum()),
            "net_win_rate": float((r > 0).mean()) if len(r) else math.nan, "pf_net_r": pf(r), "pf_net_return": pf(nominal),
            "sum_net_r": float(r.sum()), "sum_net_return": float(nominal.sum()), "realized_ge_10r": int((r >= 10).sum()),
            "mfe_ge_10r": int((closed.mfe_r >= 10).sum())}


def paired_decomposition(baseline: pd.DataFrame, be: pd.DataFrame) -> pd.DataFrame:
    """Decompose same-entry BE deltas without mixing serial re-entry outcomes."""
    if baseline.empty or be.empty:
        return pd.DataFrame(columns=PAIR_COLUMNS)
    cols = ["signal_i", "entry_i", "side", "net_r", "net_return", "mfe_r", "censored"]
    left, right = baseline.loc[:, cols].add_suffix("_baseline"), be.loc[:, cols].add_suffix("_be")
    pairs = left.merge(right, left_on=["signal_i_baseline", "entry_i_baseline", "side_baseline"], right_on=["signal_i_be", "entry_i_be", "side_be"], validate="one_to_one")
    pairs = pairs.loc[~pairs.censored_baseline.astype(bool) & ~pairs.censored_be.astype(bool)].copy()
    pairs["delta_r"] = pairs.net_r_be - pairs.net_r_baseline
    pairs["reduced_loss"] = pairs.net_r_baseline.le(0) & pairs.delta_r.gt(0)
    pairs["harmed_winner"] = pairs.net_r_baseline.gt(0) & pairs.delta_r.lt(0)
    pairs["baseline_mfe_ge_10r"] = pairs.mfe_r_baseline.ge(10)
    pairs["baseline_realized_ge_10r"] = pairs.net_r_baseline.ge(10)
    pairs["retained_realized_ge_10r"] = pairs.net_r_be.ge(10)
    return pairs.reindex(columns=PAIR_COLUMNS)


def _committed(paths: tuple[Path, ...]) -> bool:
    root = Path.cwd().resolve()
    for path in paths:
        try: relative = path.resolve().relative_to(root)
        except ValueError: return False
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True).returncode: return False
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)]).returncode: return False
    return True


def _completed_stream(streams: Path, key: str) -> dict[str, object] | None:
    """Accept a resumable stream only when every receipt-bound output still hashes."""
    receipt = streams / key / "completion.json"
    if not receipt.is_file():
        return None
    item = json.loads(receipt.read_text())
    if item.get("status") != "complete" or item.get("stream_key") != key:
        raise ValueError(f"invalid completion receipt: {receipt}")
    for name, digest in item.get("files", {}).items():
        path = receipt.parent / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"completed stream output drift: {path}")
    return item


def run(output: Path, *, limit: int | None = None, official: bool = False) -> pd.DataFrame:
    """Write paired per-stream results; official evidence requires a committed builder."""
    config = json.loads(CONFIG.read_text())
    if sha256(RAW / "manifest.json") != config["raw_manifest_sha256"] or sha256(V8_ORACLE / "manifest.json") != config["v8_oracle_manifest_sha256"]:
        raise ValueError("frozen source manifest changed")
    if official and not _committed((Path(__file__), TEST, CONFIG, PLAN)):
        raise ValueError("official replay requires committed clean source, tests, config, and plan")
    if official and EXP not in output.parents: raise ValueError("official output must stay in this experiment")
    folders = sorted(p for p in (RAW / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != int(config["expected_streams"]): raise ValueError("unexpected raw stream count")
    folders = folders if limit is None else folders[:limit]
    output.mkdir(parents=True, exist_ok=True)
    identity = {"study_sha256": sha256(Path(__file__)), "test_sha256": sha256(TEST), "config_sha256": sha256(CONFIG),
                "plan_sha256": sha256(PLAN), "raw_manifest_sha256": sha256(RAW / "manifest.json"),
                "v8_oracle_manifest_sha256": sha256(V8_ORACLE / "manifest.json")}
    identity_path = output / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("output identity differs; preserve prior receipt-bound output and choose a new directory")
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True))
    streams, failures = output / "streams", output / "failures"
    streams.mkdir(exist_ok=True); failures.mkdir(exist_ok=True)
    summaries: list[dict[str, object]] = []; oracle_receipts: list[dict[str, str]] = []
    for number, folder in enumerate(folders, 1):
        existing = _completed_stream(streams, folder.name)
        if existing is not None:
            summaries.extend(existing["summaries"]); oracle_receipts.extend(existing["oracle_ledgers"])
            print(json.dumps({"stream": number, "target": len(folders), "stream_key": folder.name, "resumed": True,
                              "stream_wall_seconds": existing["stream_wall_seconds"]}), flush=True)
            continue
        staging = streams / f".{folder.name}.staging"
        if staging.exists():
            raise ValueError(f"incomplete stream staging preserved for inspection: {staging}")
        staging.mkdir()
        started = perf_counter()
        context = base.load_verified_stream(folder)
        stream_summaries: list[dict[str, object]] = []; stream_oracles: list[dict[str, str]] = []
        try:
            for arm in ARMS:
                prepared = prepare_arm(context, arm=arm)
                # The source replay is receipt-bound archival evidence only.
                # It is never paired with a clean BE result.
                legacy_base, _, _ = replay_legacy_baseline(context, arm=arm, prepared=prepared)
                stream_oracles.append(validate_baseline(context, legacy_base, arm=arm))
                legacy_fixed = pd.DataFrame([replay_fixed_entry(context, row, arm=arm, enable_be=False, prepared=prepared) for _, row in legacy_base.iterrows()], columns=FIXED_COLUMNS)
                validate_fixed_baseline(legacy_base, legacy_fixed)
                baseline, _, _ = replay_serial(context, arm=arm, enable_be=False, prepared=prepared)
                be, _, _ = replay_serial(context, arm=arm, enable_be=True, prepared=prepared)
                fixed_base = pd.DataFrame([replay_fixed_entry(context, row, arm=arm, enable_be=False, prepared=prepared) for _, row in baseline.iterrows()], columns=FIXED_COLUMNS)
                fixed_be = pd.DataFrame([replay_fixed_entry(context, row, arm=arm, enable_be=True, prepared=prepared) for _, row in baseline.iterrows()], columns=FIXED_COLUMNS)
                for table in (fixed_base, fixed_be):
                    table["stream_key"] = context.key
                    for key, value in context.identity.items(): table[key] = value
                validate_fixed_baseline(baseline, fixed_base)
                audit = legacy_clean_events(legacy_base, baseline)
                for kind, table in (("legacy_baseline", legacy_base), ("legacy_to_clean_events", audit),
                                    ("serial_baseline", baseline), ("serial_be05", be),
                                    ("fixed_baseline", fixed_base), ("fixed_be05", fixed_be)):
                    table.to_csv(staging / f"{arm}.{kind}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
                paired = paired_decomposition(fixed_base, fixed_be)
                row = {"stream_key": context.key, "arm": arm, **context.identity, **summarize(baseline, label="serial_baseline"),
                       **{f"be05_{k}": v for k, v in summarize(be, label="serial_be05").items() if k != "label"},
                       "fixed_pairs": len(paired), "delta_net_r_same_entry": float(paired.delta_r.sum()),
                       "reduced_loss_count": int(paired.reduced_loss.sum()), "reduced_loss_delta_r": float(paired.loc[paired.reduced_loss, "delta_r"].sum()),
                       "harmed_winner_count": int(paired.harmed_winner.sum()), "harmed_winner_delta_r": float(paired.loc[paired.harmed_winner, "delta_r"].sum()),
                       "baseline_mfe_ge_10r_pairs": int(paired.baseline_mfe_ge_10r.sum()), "mfe_ge_10r_retained": int((paired.baseline_mfe_ge_10r & paired.retained_realized_ge_10r).sum()),
                       "baseline_realized_ge_10r_pairs": int(paired.baseline_realized_ge_10r.sum()), "realized_ge10r_retained": int((paired.baseline_realized_ge_10r & paired.retained_realized_ge_10r).sum()),
                       "legacy_to_clean_event_count": len(audit), "legacy_to_clean_delta_net_r": float(audit.delta_net_r.sum()),
                       "legacy_to_clean_delta_net_return": float(audit.delta_net_return.sum())}
                stream_summaries.append(row)
            pd.DataFrame(stream_summaries).to_csv(staging / "stream_summary.csv", index=False)
            files = {path.name: sha256(path) for path in staging.glob("*.csv.gz")}
            wall = perf_counter() - started
            completion = {"status": "complete", "stream_key": context.key, "stream_wall_seconds": wall, "files": files,
                          "summaries": stream_summaries, "oracle_ledgers": stream_oracles}
            (staging / "completion.json").write_text(json.dumps(completion, indent=2, default=str))
            staging.replace(streams / folder.name)
            summaries.extend(stream_summaries); oracle_receipts.extend(stream_oracles)
            print(json.dumps({"stream": number, "target": len(folders), "stream_key": folder.name, "resumed": False,
                              "stream_wall_seconds": round(wall, 4)}), flush=True)
        except Exception as exc:
            failure = {"status": "failed", "stream_key": folder.name, "stream_wall_seconds": perf_counter() - started,
                       "error_type": type(exc).__name__, "error": str(exc), "source_cache_sha256": context.receipt.get("cache_sha256")}
            (staging / "failure.json").write_text(json.dumps(failure, indent=2, default=str))
            staging.replace(failures / f"{folder.name}.{time_ns()}.failed")
            raise
    summary = pd.DataFrame(summaries); summary.to_csv(output / "stream_summary.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({"complete": limit is None, "streams": len(folders), "expected_streams": config["expected_streams"],
        "configuration_exposure": 1, "history": "authorized reused nonblind history",
        "native_v1": "implemented separately; excluded from this common-execution/V8 module", "source": {"raw": sha256(RAW / "manifest.json"), "v8": sha256(V8_ORACLE / "manifest.json")},
        "oracle_ledgers": oracle_receipts}, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--limit", type=int); parser.add_argument("--official", action="store_true")
    args = parser.parse_args(); run(args.output, limit=args.limit, official=args.official)
