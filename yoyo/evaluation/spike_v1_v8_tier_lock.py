"""Causal two-tier profit lock replay for frozen common V1 and V8 entries.

The receipt-bound input is the completed V1/V8 0.5R study.  Its baseline and
BE05 tables are copied without reinterpretation.  The only new policy is a
close-confirmed protection ladder: 0.5 initial R locks price entry and 1.5
initial R locks entry plus signed 0.5 initial R.  Each update becomes active on
the next bar; the existing stop, gap, trail, fees, raw reversal and clean
pending-intent lifetime rules remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
from time import perf_counter, time_ns

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as be05


EXP = Path("experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1")
CONFIG = EXP / "common/config.json"
TEST = Path("tests/test_spike_v1_v8_tier_lock.py")
SOURCE = Path("experiments/active/exp-spike-v1-v8-be05-20260914-v1/common/results/full_v3")
ARMS = ("v1_common_execution_long", "v8")
TIER1_R, TIER2_R, TIER2_LOCK_R = 0.5, 1.5, 0.5
TIER_COLUMNS = [*base.TRADE_COLUMNS, "stage1_armed", "stage2_armed", "stage1_trigger_count", "stage2_trigger_count", "stage1_trigger_time", "stage2_trigger_time", "stage1_lock_price", "stage2_lock_price", "exit_protection_source"]
PAIR_COLUMNS = ["signal_i_baseline", "entry_i_baseline", "side_baseline", "net_r_baseline", "net_r_tier", "delta_r", "reduced_loss", "harmed_winner", "baseline_realized_ge_10r", "retained_realized_ge_10r"]


def sha256(path: Path) -> str:
    """Return a content SHA-256 for source and per-stream receipts."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _committed(paths: tuple[Path, ...]) -> bool:
    root = Path.cwd().resolve()
    for path in paths:
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return False
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True).returncode:
            return False
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)]).returncode:
            return False
    return True


def _source_completion(key: str) -> dict[str, object]:
    """Read and hash-verify the full_v3 receipt before using any old table."""
    receipt = SOURCE / "streams" / key / "completion.json"
    if not receipt.is_file():
        raise ValueError(f"missing full_v3 receipt: {receipt}")
    item = json.loads(receipt.read_text())
    if item.get("status") != "complete" or item.get("stream_key") != key:
        raise ValueError(f"invalid full_v3 receipt: {receipt}")
    for name, digest in item.get("files", {}).items():
        path = receipt.parent / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"full_v3 receipt drift: {path}")
    return item


def _round_lock(side: int, level: float, tick: float) -> float:
    """Round lock prices conservatively: long down, short up, at source tick."""
    if not math.isfinite(level) or not math.isfinite(tick) or tick <= 0:
        raise ValueError("tier lock requires finite positive level and tick")
    return math.floor(level / tick) * tick if side == 1 else math.ceil(level / tick) * tick


def _raise_protection(position: dict[str, object], level: float) -> bool:
    """Apply a lock only when it tightens the active stop."""
    side, before = int(position["side"]), float(position["protection"])
    after = max(before, level) if side == 1 else min(before, level)
    position["protection"] = after
    return after != before


def _tier_updates(position: dict[str, object], *, high: float, low: float, close_time: pd.Timestamp,
                  tick: float, events: list[dict[str, object]], context: base.StreamContext, arm: str) -> None:
    """Promote observed MFE stages at close, never retroactively within that bar."""
    side, entry, risk = int(position["side"]), float(position["entry_price"]), float(position["initial_risk"])
    favourable = high if side == 1 else low
    mfe = side * (favourable - entry) / risk
    position["mfe_r"] = max(float(position["mfe_r"]), mfe)
    for stage, threshold, raw_level in (
        (1, TIER1_R, entry),
        (2, TIER2_R, entry + side * TIER2_LOCK_R * risk),
    ):
        armed = f"stage{stage}_armed"
        if bool(position[armed]) or mfe < threshold:
            continue
        before = float(position["protection"])
        # Stage 1 is the exact actual fill price, matching the frozen BE05
        # contract.  Only the newly introduced 0.5R lock needs conservative
        # tick quantization.
        level = raw_level if stage == 1 else _round_lock(side, raw_level, tick)
        changed = _raise_protection(position, level)
        position[armed] = True
        position[f"stage{stage}_trigger_count"] = int(position[f"stage{stage}_trigger_count"]) + 1
        position[f"stage{stage}_trigger_time"] = close_time
        position[f"stage{stage}_lock_price"] = level
        if changed:
            position["protection_source"] = f"tier{stage}"
        events.append({"trade_id": position["trade_id"], "stream_key": context.key, "arm": arm,
                       "cohort": position["cohort"], "policy": "tier_lock", "event_time": close_time,
                       "signal_i": int(position["signal_i"]), "entry_i": int(position["entry_i"]), "side": side,
                       "execution_phase": "close", "event_kind": "protection_update",
                       "reason": f"tier{stage}_{threshold:g}r_next_bar", "tier_stage": stage,
                       "mfe_r_observed": mfe, "protection_before": before,
                       "protection_after": float(position["protection"]), "changed_protection": changed})


def _close_updates(position: dict[str, object], *, high: float, low: float, close: float, atr: float,
                   i: int, prepared: be05.PreparedArm, events: list[dict[str, object]]) -> None:
    """Apply tier updates then the frozen trailing rule after this bar's stop test."""
    context, spec = prepared.context, prepared.spec
    _tier_updates(position, high=high, low=low, close_time=prepared.frame.index[i] + pd.Timedelta(minutes=context.minutes),
                  tick=spec.tick, events=events, context=context, arm=prepared.arm)
    side, entry, risk = int(position["side"]), float(position["entry_price"]), float(position["initial_risk"])
    current_r = side * (close - entry) / risk
    position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
    if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
        raw = close - side * spec.trail_atr * atr
        level = _round_lock(side, raw, spec.tick)
        if _raise_protection(position, level):
            position["protection_source"] = "trail"


def _new_position(made: dict[str, object], *, prepared: be05.PreparedArm, trade_id: str) -> dict[str, object]:
    """Build a frozen trade with tier state, preserving its actual initial risk."""
    pos = base._new_trade(made, trade_id=trade_id, cohort=prepared.cohort, policy="tier_lock", context=prepared.context)
    pos.update(protection=float(made["initial_stop"]), mfe_r=0., trail_armed=False,
               stage1_armed=False, stage2_armed=False, stage1_trigger_count=0, stage2_trigger_count=0,
               stage1_trigger_time=pd.NaT, stage2_trigger_time=pd.NaT, stage1_lock_price=math.nan,
               stage2_lock_price=math.nan, protection_source="initial", exit_protection_source="")
    return pos


def _closed(position: dict[str, object], *, i: int, stamp: pd.Timestamp, price: float, reason: str,
            fills: list[dict[str, object]], context: base.StreamContext, phase: str) -> dict[str, object]:
    """Apply the frozen two-sided 0.2% cost accounting to a terminal exit."""
    fraction, side = float(position["qty_remaining"]), int(position["side"])
    gross = fraction * side * (price / float(position["entry_price"]) - 1)
    position["qty_realized"] += fraction
    position["qty_remaining"] = 0.0
    position["realized_gross_return"] += gross
    position["realized_net_return"] += gross - fraction * base.EXIT_COST
    position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, price, reason
    base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                      phase=phase, precision="bar_open" if phase == "open" else "within_bar", kind="exit",
                      fraction=fraction, price=price, cost=fraction * base.EXIT_COST, reason=reason, context=context)
    return base._trade_row(position, censored=False, precision="bar_open_or_intrabar_window")


def _tier_fields(position: dict[str, object]) -> dict[str, object]:
    return {field: position[field] for field in TIER_COLUMNS if field in position and field not in base.TRADE_COLUMNS}


def replay_serial(context: base.StreamContext, *, arm: str, prepared: be05.PreparedArm | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Serial tier-lock replay; early tier exits may create later real entries."""
    prepared = be05.prepare_arm(context, arm=arm) if prepared is None else prepared
    if prepared.arm != arm:
        raise ValueError("prepared arm differs from requested arm")
    frame, context, spec = prepared.frame, prepared.context, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    trades: list[dict[str, object]] = []; fills: list[dict[str, object]] = []; events: list[dict[str, object]] = []
    position: dict[str, object] | None = None; pending_entry: tuple[int, int] | None = None; pending_reverse: tuple[int, int] | None = None; next_id = 0
    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if bool(prepared.gap[i]):
            if position is not None:
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                                  phase="close", precision="unknown_gap", kind="censor", fraction=float(position["qty_remaining"]),
                                  price=math.nan, cost=0., reason="data_gap_censored", context=context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"
                position["exit_protection_source"] = "data_gap"
                trades.append(base._trade_row(position, censored=True, precision="unknown_gap") | _tier_fields(position))
            position = None; pending_entry = pending_reverse = None
            continue
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            if oa[i] <= protection if side == 1 else oa[i] >= protection:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                position["exit_protection_source"] = str(position["protection_source"])
                trades.append(_closed(position, i=i, stamp=stamp, price=oa[i], reason=reason, fills=fills, context=context, phase="open") | _tier_fields(position))
                ended_side, position, pending_reverse = side, None, None
        if position is not None and pending_reverse is not None:
            _, old_side = pending_reverse
            if int(position["side"]) == old_side:
                position["exit_protection_source"] = "opposite"
                trades.append(_closed(position, i=i, stamp=stamp, price=oa[i], reason="opposite_v6_next_open", fills=fills, context=context, phase="open") | _tier_fields(position))
                ended_side, position = old_side, None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = base._initial_position_fast(frame.index, oa, ha, la, ca, aa, prepared.gap, signal_i, side, spec)
                if made is not None:
                    made["initial_risk_frac"] = float(made["initial_risk"]) / float(made["entry_price"])
                    original_i = prepared.ordinal.get(frame.index[signal_i])
                    if original_i is not None:
                        made.update(signal_i=original_i, entry_i=original_i + 1, frozen_index_offset=original_i - signal_i)
                    next_id += 1; position = _new_position(made, prepared=prepared, trade_id=f"{context.key}:{arm}:tier_lock:{next_id}")
                    base._append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open", precision="bar_open",
                                      kind="entry", fraction=1., price=float(position["entry_price"]), cost=base.ENTRY_COST, reason="next_open_entry", context=context)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            if la[i] <= protection if side == 1 else ha[i] >= protection:
                price = min(oa[i], protection) if side == 1 else max(oa[i], protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if oa[i] <= protection if side == 1 else oa[i] >= protection:
                    reason += "_gap"
                position["exit_protection_source"] = str(position["protection_source"])
                trades.append(_closed(position, i=i, stamp=stamp, price=price, reason=reason, fills=fills, context=context,
                                      phase="open" if reason.endswith("_gap") else "intrabar") | _tier_fields(position))
                ended_side, position, pending_reverse = side, None, None
            else:
                _close_updates(position, high=ha[i], low=la[i], close=ca[i], atr=aa[i], i=i, prepared=prepared, events=events)
        signal_side = int(prepared.raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if bool(prepared.allowed[i]):
                    pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and bool(prepared.allowed[i]):
                pending_entry = (i, signal_side)
    if position is not None:
        i, stamp = len(frame) - 1, frame.index[-1]
        base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                          event_time=stamp + pd.Timedelta(minutes=context.minutes), phase="close", precision="last_complete_close",
                          kind="censor", fraction=float(position["qty_remaining"]), price=ca[i], cost=0., reason="boundary_mark", context=context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = i, stamp, ca[i], "boundary_mark"
        position["exit_protection_source"] = "boundary"
        trades.append(base._trade_row(position, censored=True, precision="last_complete_close") | _tier_fields(position))
    return pd.DataFrame(trades, columns=TIER_COLUMNS), pd.DataFrame(fills, columns=base.FILL_COLUMNS), pd.DataFrame(events)


def replay_fixed_entry(context: base.StreamContext, row: pd.Series, *, arm: str, prepared: be05.PreparedArm | None = None) -> dict[str, object]:
    """Apply the tier policy to one receipt-bound original baseline entry."""
    prepared = be05.prepare_arm(context, arm=arm) if prepared is None else prepared
    frame, context, spec = prepared.frame, prepared.context, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    side = int(row.side); start = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    made = {key: row[key] for key in ("signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price", "initial_stop", "initial_risk", "initial_risk_frac")}
    made["frozen_index_offset"] = int(row.entry_i) - start
    pos = _new_position(made, prepared=prepared, trade_id=f"{context.key}:{arm}:tier_fixed")
    pending_reverse: int | None = None
    for i in range(start, len(frame)):
        stamp = frame.index[i]
        if bool(prepared.gap[i]):
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = i, stamp, "data_gap_censored"
            pos["exit_protection_source"] = "data_gap"
            return base._trade_row(pos, censored=True, precision="unknown_gap") | _tier_fields(pos)
        protection = float(pos["protection"])
        if pending_reverse is not None:
            if side == pending_reverse:
                stop_at_open = oa[i] <= protection if side == 1 else oa[i] >= protection
                reason = ("trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap") if stop_at_open else "opposite_v6_next_open"
                pos["qty_realized"], pos["qty_remaining"] = 1., 0.; pos["realized_gross_return"] = side * (oa[i] / float(pos["entry_price"]) - 1); pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
                pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, oa[i], reason
                pos["exit_protection_source"] = str(pos["protection_source"]) if stop_at_open else "opposite"
                return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | _tier_fields(pos)
            pending_reverse = None
        if la[i] <= protection if side == 1 else ha[i] >= protection:
            price = min(oa[i], protection) if side == 1 else max(oa[i], protection)
            reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if oa[i] <= protection if side == 1 else oa[i] >= protection:
                reason += "_gap"
            pos["qty_realized"], pos["qty_remaining"] = 1., 0.; pos["realized_gross_return"] = side * (price / float(pos["entry_price"]) - 1); pos["realized_net_return"] = pos["realized_gross_return"] - base.ENTRY_COST - base.EXIT_COST
            pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
            pos["exit_protection_source"] = str(pos["protection_source"])
            return base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window") | _tier_fields(pos)
        _close_updates(pos, high=ha[i], low=la[i], close=ca[i], atr=aa[i], i=i, prepared=prepared, events=[])
        if int(prepared.raw_side[i]) == -side:
            pending_reverse = side
    i, stamp = len(frame) - 1, frame.index[-1]
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, ca[i], "boundary_mark"
    pos["exit_protection_source"] = "boundary"
    return base._trade_row(pos, censored=True, precision="last_complete_close") | _tier_fields(pos)


def paired_decomposition(baseline: pd.DataFrame, tier: pd.DataFrame) -> pd.DataFrame:
    """Same-entry tier delta, retaining original realized >=10R as its own metric."""
    if baseline.empty or tier.empty:
        return pd.DataFrame(columns=PAIR_COLUMNS)
    cols = ["signal_i", "entry_i", "side", "net_r", "censored"]
    left, right = baseline.loc[:, cols].add_suffix("_baseline"), tier.loc[:, cols].add_suffix("_tier")
    out = left.merge(right, left_on=["signal_i_baseline", "entry_i_baseline", "side_baseline"], right_on=["signal_i_tier", "entry_i_tier", "side_tier"], validate="one_to_one")
    out = out.loc[~out.censored_baseline.astype(bool) & ~out.censored_tier.astype(bool)].copy()
    out["delta_r"] = out.net_r_tier - out.net_r_baseline
    out["reduced_loss"] = out.net_r_baseline.le(0) & out.delta_r.gt(0)
    out["harmed_winner"] = out.net_r_baseline.gt(0) & out.delta_r.lt(0)
    out["baseline_realized_ge_10r"] = out.net_r_baseline.ge(10)
    out["retained_realized_ge_10r"] = out.net_r_tier.ge(10)
    return out.reindex(columns=PAIR_COLUMNS)


def validate_fixed_identity(baseline: pd.DataFrame, tier: pd.DataFrame) -> None:
    """Reject a fixed-entry run that silently loses or changes an old entry."""
    from pandas.testing import assert_frame_equal
    fields = ["signal_i", "entry_i", "side"]
    assert_frame_equal(baseline.loc[:, fields].sort_values(fields).reset_index(drop=True),
                       tier.loc[:, fields].sort_values(fields).reset_index(drop=True), check_dtype=False)


def _old_fixed_baseline(key: str, arm: str) -> pd.DataFrame:
    """Load the receipt-verified original fixed baseline without relabelling it."""
    _source_completion(key)
    return pd.read_csv(SOURCE / "streams" / key / f"{arm}.fixed_baseline.csv.gz")


def _completed_stream(streams: Path, key: str) -> dict[str, object] | None:
    receipt = streams / key / "completion.json"
    if not receipt.is_file():
        return None
    item = json.loads(receipt.read_text())
    if item.get("status") != "complete" or item.get("stream_key") != key:
        raise ValueError(f"invalid tier receipt: {receipt}")
    for name, digest in item.get("files", {}).items():
        path = receipt.parent / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"tier receipt drift: {path}")
    return item


def run(output: Path, *, limit: int | None = None, official: bool = False) -> pd.DataFrame:
    """Write receipt-bound old controls and causal tier fixed/serial outcomes."""
    config = json.loads(CONFIG.read_text())
    identity_source = json.loads((SOURCE / "identity.json").read_text())
    if identity_source.get("study_sha256") != config["source_identity_sha256"]:
        raise ValueError("full_v3 study identity differs from approved source")
    source_manifest = json.loads((SOURCE / "manifest.json").read_text())
    if not source_manifest.get("complete") or int(source_manifest.get("streams", 0)) != int(config["expected_streams"]):
        raise ValueError("full_v3 is not a complete approved source")
    if official and not _committed((Path(__file__), TEST, CONFIG)):
        raise ValueError("official tier replay requires committed source, tests, and config")
    if official and EXP not in output.parents:
        raise ValueError("official output must stay in this experiment")
    folders = sorted(path for path in (be05.RAW / "streams").iterdir() if (path / "completion.json").is_file())
    if len(folders) != int(config["expected_streams"]):
        raise ValueError("unexpected raw stream count")
    folders = folders if limit is None else folders[:limit]
    output.mkdir(parents=True, exist_ok=True)
    identity = {"study_sha256": sha256(Path(__file__)), "test_sha256": sha256(TEST), "config_sha256": sha256(CONFIG),
                "source_identity": identity_source, "source_manifest_sha256": sha256(SOURCE / "manifest.json")}
    ip = output / "identity.json"
    if ip.exists() and json.loads(ip.read_text()) != identity:
        raise ValueError("output identity differs; choose a new directory")
    ip.write_text(json.dumps(identity, indent=2, sort_keys=True))
    streams, failures = output / "streams", output / "failures"; streams.mkdir(exist_ok=True); failures.mkdir(exist_ok=True)
    summaries: list[dict[str, object]] = []
    for number, folder in enumerate(folders, 1):
        previous = _completed_stream(streams, folder.name)
        if previous is not None:
            summaries.extend(previous["summaries"]); print(json.dumps({"stream":number,"target":len(folders),"stream_key":folder.name,"resumed":True,"stream_wall_seconds":previous["stream_wall_seconds"]}),flush=True); continue
        staging = streams / f".{folder.name}.staging"
        if staging.exists(): raise ValueError(f"incomplete staging preserved: {staging}")
        staging.mkdir(); started = perf_counter(); context = base.load_verified_stream(folder)
        stream_rows: list[dict[str, object]] = []
        try:
            source_receipt = _source_completion(context.key)
            for arm in ARMS:
                old_fixed = _old_fixed_baseline(context.key, arm)
                prepared = be05.prepare_arm(context, arm=arm)
                serial, _, events = replay_serial(context, arm=arm, prepared=prepared)
                fixed = pd.DataFrame([replay_fixed_entry(context, row, arm=arm, prepared=prepared) for _, row in old_fixed.iterrows()], columns=TIER_COLUMNS)
                for table in (fixed, serial):
                    table["stream_key"] = context.key
                    for key, value in context.identity.items(): table[key] = value
                validate_fixed_identity(old_fixed, fixed)
                pairs = paired_decomposition(old_fixed, fixed)
                tables = {"serial_tier": serial, "fixed_tier": fixed, "tier_events": events, "fixed_pairs": pairs}
                for kind, table in tables.items():
                    table.to_csv(staging / f"{arm}.{kind}.csv.gz", index=False, compression={"method":"gzip","mtime":0})
                stream_rows.append({"stream_key":context.key,"arm":arm,**context.identity,
                    "source_fixed_baseline_rows":len(old_fixed),
                    "tier_fixed_rows":len(fixed),"tier_serial_rows":len(serial),"tier_fixed_closed":int((~fixed.censored.astype(bool)).sum()),"tier_fixed_censored":int(fixed.censored.astype(bool).sum()),
                    "tier_serial_closed":int((~serial.censored.astype(bool)).sum()),"tier_serial_censored":int(serial.censored.astype(bool).sum()),
                    "stage1_fixed_triggered":int(fixed.stage1_trigger_count.astype(int).gt(0).sum()),"stage2_fixed_triggered":int(fixed.stage2_trigger_count.astype(int).gt(0).sum()),
                    "stage1_serial_triggered":int(serial.stage1_trigger_count.astype(int).gt(0).sum()),"stage2_serial_triggered":int(serial.stage2_trigger_count.astype(int).gt(0).sum()),
                    "fixed_pairs":len(pairs),"delta_net_r_same_entry":float(pairs.delta_r.sum()),"reduced_loss_count":int(pairs.reduced_loss.sum()),"harmed_winner_count":int(pairs.harmed_winner.sum()),
                    "baseline_realized_ge_10r_pairs":int(pairs.baseline_realized_ge_10r.sum()),"realized_ge10r_retained":int((pairs.baseline_realized_ge_10r & pairs.retained_realized_ge_10r).sum()),
                    "source_completion_sha256":sha256(SOURCE / "streams" / context.key / "completion.json")})
            pd.DataFrame(stream_rows).to_csv(staging / "stream_summary.csv", index=False)
            files = {path.name:sha256(path) for path in staging.glob("*.csv.gz")}; wall=perf_counter()-started
            receipt={"status":"complete","stream_key":context.key,"stream_wall_seconds":wall,"files":files,"summaries":stream_rows,
                     "source_completion_sha256":sha256(SOURCE / "streams" / context.key / "completion.json"),"source_files":source_receipt["files"]}
            (staging / "completion.json").write_text(json.dumps(receipt,indent=2,default=str)); staging.replace(streams / context.key)
            summaries.extend(stream_rows); print(json.dumps({"stream":number,"target":len(folders),"stream_key":context.key,"resumed":False,"stream_wall_seconds":round(wall,4)}),flush=True)
        except Exception as exc:
            failure={"status":"failed","stream_key":folder.name,"stream_wall_seconds":perf_counter()-started,"error_type":type(exc).__name__,"error":str(exc),"source_cache_sha256":context.receipt.get("cache_sha256")}
            (staging / "failure.json").write_text(json.dumps(failure,indent=2,default=str)); staging.replace(failures / f"{folder.name}.{time_ns()}.failed"); raise
    result=pd.DataFrame(summaries); result.to_csv(output / "stream_summary.csv",index=False)
    (output / "manifest.json").write_text(json.dumps({"complete":limit is None,"streams":len(folders),"expected_streams":config["expected_streams"],"configuration_exposure":1,"history":config["history"],"source_result":str(SOURCE),"source_manifest_sha256":sha256(SOURCE / "manifest.json")},indent=2))
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,required=True); parser.add_argument("--limit",type=int); parser.add_argument("--official",action="store_true")
    args=parser.parse_args(); run(args.output,limit=args.limit,official=args.official)
