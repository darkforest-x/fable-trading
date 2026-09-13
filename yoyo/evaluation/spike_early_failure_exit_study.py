"""Receipt-bound early-failure exit replay for the frozen V7 two-year stream.

This is deliberately a one-variable study: every arm uses the frozen V7-both
admissions and the V6 execution contract from :mod:`spike_exit_policy_study`.
Only an optional close-confirmed, next-open discretionary exit changes.  It
does not fetch data, derive indicators, train, or access an unfrozen holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import _initial_position_fast


SOURCE_STREAMS = base.SOURCE_STREAMS
SPLIT = base.SPLIT
POLICIES = ("baseline", "no_new_extreme_2", "no_new_extreme_3", "back_inside_rope_2", "back_inside_rope_3")
COHORT = "v7_both"
SEED = 20260913


def sha256(path: Path) -> str:
    """Return the byte SHA-256 used by the immutable output receipt."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _early_reason(policy: str, frame: pd.DataFrame, *, entry_i: int, i: int, side: int) -> str | None:
    """Decide a rule only from the just-closed bar and entry/signal-bar facts.

    Uses ``high``, ``low``, ``close``, ``ropeHigh`` and ``ropeLow`` from the
    authenticated cached bars.  No value after ``i`` is observed here.
    """
    if policy == "baseline":
        return None
    kind, raw_n = policy.rsplit("_", 1)
    n = int(raw_n)
    # An entry fills at the bar open, so that bar's close is the first complete
    # post-entry observation; n=2 is therefore evaluated at entry_i + 1.
    held = i - entry_i + 1
    if kind == "no_new_extreme":
        if held < n:
            return None
        signal_i = entry_i - 1
        if side == 1:
            no_progress = float(frame.high.iloc[entry_i : i + 1].max()) <= float(frame.high.iloc[signal_i])
        else:
            no_progress = float(frame.low.iloc[entry_i : i + 1].min()) >= float(frame.low.iloc[signal_i])
        return f"no_new_extreme_{n}_next_open" if no_progress else None
    if kind == "back_inside_rope" and 1 <= held <= n:
        close = float(frame.close.iloc[i])
        rope = float(frame.ropeHigh.iloc[i] if side == 1 else frame.ropeLow.iloc[i])
        if math.isfinite(close) and math.isfinite(rope) and (close <= rope if side == 1 else close >= rope):
            return f"back_inside_rope_{n}_next_open"
    return None


def replay_policy(context: base.StreamContext, *, policy: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay V7-both with exactly one optional early-failure exit rule.

    Protective stops are processed before a discretionary action scheduled at
    the prior close.  A signal is allowed to open only after the exit consumes
    the next open, preserving the frozen one-position execution semantics.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown early-failure policy: {policy}")
    if policy == "baseline":
        return base.replay_policy(context, cohort=COHORT, policy="baseline")
    frame = context.cache["bars"]
    required = {"open", "high", "low", "close", "atr", "s20", "e20", "md", "sb", "ropeHigh", "ropeLow"}
    if not required.issubset(frame):
        raise ValueError(f"missing early-exit columns: {context.key}")
    frame.attrs["minutes"] = context.minutes
    signals, allowed = base._cohort_inputs(context, COHORT)
    gap = context.cache["data_gap"].reindex(frame.index).fillna(True).astype(bool)
    raw_side = np.where(signals.long_signal.fillna(False), 1, np.where(signals.short_signal.fillna(False), -1, 0))
    if (signals.long_signal.fillna(False).astype(bool) & signals.short_signal.fillna(False).astype(bool)).any():
        raise ValueError("ambiguous frozen signal side")
    spec = ExecutionSpec(tick=float(context.cache["tick"]))
    arrays = {name: frame[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")}
    oa, ha, la, ca, aa = (arrays[x] for x in ("open", "high", "low", "close", "atr"))
    gap_a, allowed_a = gap.to_numpy(bool), allowed.to_numpy(bool)
    ledger = context.signals_ledger
    ordinal = dict(zip(pd.to_datetime(ledger.signal_bar_open, utc=True), ledger.signal_i.astype(int))) if len(ledger) else {}
    fills: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_exit: tuple[int, int, str] | None = None
    next_id = 0

    def close_position(pos: dict[str, object], i: int, stamp: pd.Timestamp, price: float, reason: str, phase: str) -> int:
        side, fraction = int(pos["side"]), float(pos["qty_remaining"])
        cost = fraction * base.EXIT_COST
        gross = fraction * side * (price / float(pos["entry_price"]) - 1.0)
        pos["qty_realized"] += fraction; pos["qty_remaining"] = 0.0
        pos["realized_gross_return"] += gross; pos["realized_net_return"] += gross - cost
        pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_price"], pos["last_exit_reason"] = i, stamp, price, reason
        base._append_fill(fills, pos, leg_no=int(pos.get("fill_count", 0)) + 1, bar_open=stamp, event_time=stamp,
                          phase=phase, precision="bar_open" if phase == "open" else "within_bar", kind="exit",
                          fraction=fraction, price=price, cost=cost, reason=reason, context=context)
        trades.append(base._trade_row(pos, censored=False, precision="bar_open_or_intrabar_window"))
        return side

    for i, stamp in enumerate(frame.index):
        ended_side = 0
        if gap_a[i]:
            if position is not None:
                base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                                  event_time=stamp, phase="close", precision="unknown_gap", kind="censor",
                                  fraction=float(position["qty_remaining"]), price=math.nan, cost=0.0,
                                  reason="data_gap_censored", context=context)
                position["last_exit_i"], position["last_exit_time"], position["last_exit_reason"] = i, stamp, "data_gap_censored"
                trades.append(base._trade_row(position, censored=True, precision="unknown_gap"))
            position = None; pending_entry = pending_exit = None
            continue
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            opening = float(oa[i])
            if opening <= protection if side == 1 else opening >= protection:
                reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                ended_side = close_position(position, i, stamp, opening, reason, "open")
                position = None; pending_exit = None
        if position is not None and pending_exit is not None:
            _, old_side, reason = pending_exit
            if int(position["side"]) == old_side:
                ended_side = close_position(position, i, stamp, float(oa[i]), reason, "open")
                position = None
            pending_exit = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                made = _initial_position_fast(frame.index, oa, ha, la, ca, aa, gap_a, signal_i, side, spec)
                if made is not None:
                    made["initial_risk_frac"] = float(made["initial_risk"]) / float(made["entry_price"])
                    original_i = ordinal.get(frame.index[signal_i])
                    if original_i is not None:
                        made["signal_i"], made["entry_i"], made["frozen_index_offset"] = original_i, original_i + 1, original_i - signal_i
                    next_id += 1
                    position = base._new_trade(made, trade_id=f"{context.key}:{COHORT}:{policy}:{next_id}", cohort=COHORT, policy=policy, context=context)
                    base._append_fill(fills, position, leg_no=1, bar_open=stamp, event_time=stamp, phase="open",
                                      precision="bar_open", kind="entry", fraction=1.0, price=float(position["entry_price"]),
                                      cost=base.ENTRY_COST, reason="next_open_entry", context=context)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            opening, high, low, close, atr = (float(x[i]) for x in (oa, ha, la, ca, aa))
            stopped = low <= protection if side == 1 else high >= protection
            if stopped:
                price = min(opening, protection) if side == 1 else max(opening, protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if opening <= protection if side == 1 else opening >= protection:
                    reason += "_gap"
                ended_side = close_position(position, i, stamp, price, reason, "open" if reason.endswith("_gap") else "intrabar")
                position = None; pending_exit = None
            else:
                position["mfe_r"] = max(float(position["mfe_r"]), side * ((high if side == 1 else low) - float(position["entry_price"])) / float(position["initial_risk"]))
                close_r = side * (close - float(position["entry_price"])) / float(position["initial_risk"])
                position["trail_armed"] = bool(position["trail_armed"]) or close_r >= spec.arm_r
                if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
                    raw = close - side * spec.trail_atr * atr
                    candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
                    before = float(position["protection"])
                    position["protection"] = max(before, candidate) if side == 1 else min(before, candidate)
                    if float(position["protection"]) != before:
                        events.append({"trade_id": position["trade_id"], "cohort": COHORT, "policy": policy, "stream_key": context.key,
                                       "bar_open": stamp, "event_time": base._close_time(frame.index, i, context.minutes), "execution_phase": "close",
                                       "event_kind": "protection_update", "reason": "trail_2r_4atr", "protection_before": before,
                                       "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"])})
                # ``entry_i`` is the authenticated full-frame ordinal in output; use the
                # cache-relative ordinal retained by the offset to evaluate bar count.
                entry_cache_i = int(position["entry_i"]) - int(position.get("frozen_index_offset", 0))
                reason = _early_reason(policy, frame, entry_i=entry_cache_i, i=i, side=side)
                if reason is not None:
                    pending_exit = (i, side, reason)
                    events.append({"trade_id": position["trade_id"], "cohort": COHORT, "policy": policy, "stream_key": context.key,
                                   "bar_open": stamp, "event_time": base._close_time(frame.index, i, context.minutes), "execution_phase": "close",
                                   "event_kind": "exit_scheduled", "reason": reason, "protection_before": float(position["protection"]),
                                   "protection_after": float(position["protection"]), "qty_fraction": float(position["qty_remaining"])})
        signal_side = int(raw_side[i])
        if signal_side:
            if position is not None and signal_side != int(position["side"]):
                pending_exit = (i, int(position["side"]), "opposite_v6_next_open")
                if allowed_a[i]:
                    pending_entry = (i, signal_side)
            elif position is None and signal_side != ended_side and allowed_a[i]:
                pending_entry = (i, signal_side)
    if position is not None:
        last, stamp = len(frame) - 1, frame.index[-1]
        base._append_fill(fills, position, leg_no=int(position.get("fill_count", 0)) + 1, bar_open=stamp,
                          event_time=base._close_time(frame.index, last, context.minutes), phase="close",
                          precision="last_complete_close", kind="censor", fraction=float(position["qty_remaining"]),
                          price=float(ca[last]), cost=0.0, reason="boundary_mark", context=context)
        position["last_exit_i"], position["last_exit_time"], position["last_exit_price"], position["last_exit_reason"] = last, stamp, float(ca[last]), "boundary_mark"
        trades.append(base._trade_row(position, censored=True, precision="last_complete_close"))
    return (pd.DataFrame(trades, columns=base.TRADE_COLUMNS), pd.DataFrame(fills, columns=base.FILL_COLUMNS),
            pd.DataFrame(events, columns=base.EVENT_COLUMNS))


def _period(frame: pd.DataFrame) -> pd.Series:
    return np.where(pd.to_datetime(frame.signal_bar_open, utc=True) < SPLIT, "development", "validation_reused_history")


def _drawdown_r(values: pd.Series) -> float:
    curve = values.cumsum(); return float((curve.cummax() - curve).max()) if len(curve) else math.nan


def _metrics(trades: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    closed["period"] = _period(closed); closed["direction"] = np.where(closed.side.eq(1), "long", "short")
    signals = signals.copy()
    rows: list[dict[str, object]] = []
    for keys, part in closed.groupby(["policy", "period", "timeframe_min", "direction"], dropna=False):
        policy, period, timeframe, direction = keys
        wins, losses = part.loc[part.net_r > 0, "net_r"], part.loc[part.net_r < 0, "net_r"]
        gain, loss = float(wins.sum()), float(-losses.sum())
        rows.append({"policy": policy, "period": period, "timeframe_min": int(timeframe), "direction": direction,
                     "signals": int(signals.loc[(signals.period == period) & (signals.timeframe_min == timeframe) & (signals.direction == direction)].shape[0]),
                     "trades": len(part), "win_rate": float((part.net_r > 0).mean()), "profit_factor": gain / loss if loss else math.inf,
                     "net_r": float(part.net_r.sum()), "mean_loss_r": float(losses.mean()) if len(losses) else math.nan,
                     "closed_trade_max_drawdown_r": _drawdown_r(part.sort_values("exit_time").net_r),
                     "realized_ge_10r_count": int((part.net_r >= 10).sum()), "exit_reasons": json.dumps(part.exit_reason.value_counts().to_dict(), sort_keys=True)})
    return pd.DataFrame(rows)


def _overall_gate(trades: pd.DataFrame, policy: str) -> dict[str, object]:
    closed = trades.loc[(trades.policy == policy) & ~trades.censored.astype(bool)].copy()
    closed["period"] = _period(closed)
    dev = closed.loc[closed.period.eq("development")]
    stream_accounts = []
    for stream, part in dev.groupby("stream_key"):
        values = part.sort_values("exit_time").net_r.to_numpy(float)
        account = float(np.prod(1 + .01 * values) - 1) if len(values) else math.nan
        stream_accounts.append((stream, account, _drawdown_r(pd.Series(values))))
    return {"policy": policy, "development_trades": len(dev), "development_net_r": float(dev.net_r.sum()),
            "mean_loss_r": float(dev.loc[dev.net_r < 0, "net_r"].mean()),
            "mean_stream_account_return_1pct_risk": float(np.nanmean([x[1] for x in stream_accounts])),
            "mean_stream_closed_trade_max_drawdown_r": float(np.nanmean([x[2] for x in stream_accounts])),
            "realized_ge_10r_count": int((dev.net_r >= 10).sum())}


def _paired_null(trades: pd.DataFrame, policy: str, draws: int = 10000) -> dict[str, object]:
    """Same-entry monthly/asset paired sign-flip null; no random-entry claim."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    left = closed.loc[closed.policy.eq("baseline"), ["stream_key", "signal_i", "side", "net_r", "entry_time", "asset"]]
    right = closed.loc[closed.policy.eq(policy), ["stream_key", "signal_i", "side", "net_r"]]
    paired = left.merge(right, on=["stream_key", "signal_i", "side"], suffixes=("_baseline", "_candidate"))
    paired["delta_r"] = paired.net_r_candidate - paired.net_r_baseline
    paired["month"] = pd.to_datetime(paired.entry_time, utc=True).dt.strftime("%Y-%m")
    blocks = paired.groupby(["month", "asset"], dropna=False).delta_r.sum().to_numpy(float)
    observed = float(blocks.sum())
    if len(blocks) == 0:
        p = math.nan
    else:
        rng = np.random.default_rng(SEED)
        simulated = (rng.choice(np.array([-1.0, 1.0]), size=(draws, len(blocks))) * blocks).sum(axis=1)
        p = float((np.abs(simulated) >= abs(observed)).mean())
    return {"policy": policy, "null": "same-entry paired month_asset sign_flip; changed-exit reentries are excluded",
            "paired_trades": len(paired), "blocks": len(blocks), "delta_net_r": observed, "two_sided_p": p, "draws": draws}


def _write_gzip_csv(path: Path, table: pd.DataFrame) -> None:
    table.to_csv(path, index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0})


def validate_v7_baseline(context: base.StreamContext, trades: pd.DataFrame, *, before: pd.Timestamp | None = None) -> None:
    """Require exact closed-trade parity with the frozen V7 ledger only."""
    from pandas.testing import assert_frame_equal
    old = pd.read_csv(context.path / "trades.csv.gz")
    expected = old.loc[old.variant.eq("v7_bb_both") & ~old.censored.astype(bool)]
    actual = trades.loc[~trades.censored.astype(bool)]
    if before is not None and not expected.empty:
        expected = expected.loc[(pd.to_datetime(expected.signal_bar_open, utc=True) < before) &
                                (pd.to_datetime(expected.exit_time, utc=True) < before)]
    if expected.empty:
        if not actual.empty:
            raise AssertionError(f"frozen V7 ledger has no closed trades but replay produced {len(actual)}: {context.key}")
        return
    columns = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"]
    assert_frame_equal(expected[columns].sort_values("signal_i").reset_index(drop=True),
                       actual[columns].sort_values("signal_i").reset_index(drop=True), check_dtype=False, rtol=1e-9, atol=1e-9)


def _development_context(context: base.StreamContext) -> base.StreamContext:
    """Cut authenticated cache state at the predeclared decision boundary."""
    bars = context.cache["bars"]
    keep = bars.index < SPLIT
    cache = {key: (value.loc[keep].copy() if isinstance(value, (pd.Series, pd.DataFrame)) and value.index.equals(bars.index) else value)
             for key, value in context.cache.items()}
    ledger_times = pd.to_datetime(context.signals_ledger.signal_bar_open, utc=True)
    ledger = context.signals_ledger.loc[ledger_times < SPLIT].copy()
    return base.StreamContext(context.path, context.key, context.receipt, cache, ledger, context.minutes, context.identity)


def _signal_rows(context: base.StreamContext) -> list[dict[str, object]]:
    signals, allowed = base._cohort_inputs(context, COHORT)
    sides = np.where(signals.long_signal.fillna(False), 1, np.where(signals.short_signal.fillna(False), -1, 0))
    return [{"stream_key": context.key, "timeframe_min": context.minutes, "direction": "long" if side == 1 else "short",
             "period": "development" if context.cache["bars"].index[i] < SPLIT else "validation_reused_history"}
            for i, side in enumerate(sides) if side and bool(allowed.iloc[i])]


def run(streams_root: Path, output: Path, *, stream_limit: int | None = None) -> dict[str, object]:
    """Authenticate, smoke/full replay, select on development, then summarize validation."""
    started = perf_counter()
    if output.exists():
        raise FileExistsError(f"output exists; preserve immutable receipt: {output}")
    folders = sorted(x.parent for x in streams_root.glob("*/completion.json"))
    if not folders:
        raise FileNotFoundError(f"no frozen streams: {streams_root}")
    if stream_limit is not None:
        folders = folders[:stream_limit]
    output.mkdir(parents=True)
    # Phase one reads only the prefix, writes the fixed decision, and does not
    # aggregate the authorized historical validation segment during selection.
    development_trades: list[pd.DataFrame] = []; development_signals: list[dict[str, object]] = []
    for folder in folders:
        context = base.load_verified_stream(folder)
        prefix = _development_context(context)
        baseline, _, _ = replay_policy(prefix, policy="baseline")
        validate_v7_baseline(context, baseline, before=SPLIT)
        for policy in POLICIES:
            trades, _, _ = (baseline, pd.DataFrame(), pd.DataFrame()) if policy == "baseline" else replay_policy(prefix, policy=policy)
            development_trades.append(trades)
        development_signals.extend(_signal_rows(prefix))
    development_table = pd.concat(development_trades, ignore_index=True)
    development_signal_table = pd.DataFrame(development_signals)
    gates = pd.DataFrame([_overall_gate(development_table, policy) for policy in POLICIES])
    baseline_gate = gates.loc[gates.policy.eq("baseline")].iloc[0]
    candidates = gates.loc[gates.policy.ne("baseline")].copy()
    candidates["loss_or_drawdown_improved"] = (candidates.mean_loss_r > float(baseline_gate.mean_loss_r)) | (candidates.mean_stream_closed_trade_max_drawdown_r < float(baseline_gate.mean_stream_closed_trade_max_drawdown_r))
    candidates["account_not_worse"] = candidates.mean_stream_account_return_1pct_risk >= float(baseline_gate.mean_stream_account_return_1pct_risk)
    denominator = int(baseline_gate.realized_ge_10r_count)
    candidates["realized_ge_10r_retention"] = np.where(denominator == 0, 1.0, candidates.realized_ge_10r_count / denominator)
    candidates["passes_gate"] = candidates.loss_or_drawdown_improved & candidates.account_not_worse & (candidates.realized_ge_10r_retention >= .95)
    eligible = candidates.loc[candidates.passes_gate]
    selected = None if eligible.empty else str(eligible.sort_values(["mean_loss_r", "development_net_r"], ascending=[False, False]).iloc[0].policy)
    selection = {"decision_period": "signal_bar_open < 2025-09-10T00:00:00Z; incomplete boundary positions censored", "status": "selected" if selected else "reject_no_candidate_passed",
                 "selected_policy": selected, "gate": {"mean_loss_or_closed_trade_mdd_improves": True, "account_return_not_worse": True,
                 "realized_ge_10r_retention_min": .95, "baseline_realized_ge_10r_denominator": denominator},
                 "validation_note": "Validation is explicitly authorized reused historical data, not blind."}
    (output / "selected_rule.json").write_text(json.dumps(selection, indent=2, sort_keys=True))

    all_trades: list[pd.DataFrame] = []; all_events: list[pd.DataFrame] = []; signal_rows = []; parity = []; input_hashes = {}
    for index, folder in enumerate(folders, 1):
        context = base.load_verified_stream(folder)
        input_hashes[context.key] = {"control_cache_sha256": context.receipt["cache_sha256"],
                                     "signals_csv_gz_sha256": sha256(folder / "signals.csv.gz"),
                                     "receipt_sha256": sha256(folder / "control_cache.receipt.json"),
                                     "completion_sha256": sha256(folder / "completion.json"),
                                     "source_sha256": context.receipt["source_sha256"]}
        baseline, _, _ = replay_policy(context, policy="baseline")
        validate_v7_baseline(context, baseline)
        parity.append({"stream_key": context.key, "baseline_parity": "passed"})
        for policy in POLICIES:
            trades, _, events = (baseline, pd.DataFrame(), pd.DataFrame()) if policy == "baseline" else replay_policy(context, policy=policy)
            all_trades.append(trades); all_events.append(events)
        signal_rows.extend(_signal_rows(context))
        if index % 25 == 0 or index == len(folders):
            print(json.dumps({"completed_streams": index, "target": len(folders)}), flush=True)
    trades = pd.concat(all_trades, ignore_index=True); events = pd.concat(all_events, ignore_index=True)
    signal_frame = pd.DataFrame(signal_rows)
    validation = trades.loc[_period(trades) == "validation_reused_history"].copy()
    validation_signals = signal_frame.loc[signal_frame.period.eq("validation_reused_history")].copy()
    metrics = pd.concat([_metrics(development_table, development_signal_table), _metrics(validation, validation_signals)], ignore_index=True)
    nulls = pd.DataFrame([_paired_null(trades, p) for p in POLICIES if p != "baseline"])
    _write_gzip_csv(output / "trades.csv.gz", trades)
    _write_gzip_csv(output / "events.csv.gz", events)
    metrics.to_csv(output / "metrics_by_period_timeframe_direction.csv", index=False)
    gates.to_csv(output / "development_gate_metrics.csv", index=False)
    candidates.to_csv(output / "development_selection.csv", index=False)
    nulls.to_csv(output / "same_entry_paired_block_null.csv", index=False)
    pd.DataFrame(parity).to_csv(output / "baseline_parity.csv", index=False)
    manifest = {"source_streams": str(streams_root), "source_streams_manifest_sha256": sha256(streams_root.parent / "manifest.json"),
                "stream_count": len(folders), "inputs": input_hashes,
                "code_sha256": {"study": sha256(Path(__file__)), "exit_policy_engine": sha256(Path(base.__file__)),
                                "v6_execution": sha256(Path(base.__file__).with_name("spike_v6_wvf_study.py")),
                                "v7_entry": sha256(Path(base.__file__).with_name("spike_v7_fast.py"))},
                "data_range": {"start": "2024-09-10T00:00:00Z", "development_end": "2025-09-10T00:00:00Z", "end": "2026-09-10T00:00:00Z"},
                "policies": list(POLICIES), "cohort": COHORT, "reproduction": f"python3 -m yoyo.evaluation.spike_early_failure_exit_study --streams-root {streams_root} --output {output}",
                "elapsed_seconds": perf_counter() - started, "validation": "authorized reused historical data; not blind; no new holdout"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    receipt = {"complete": True, "manifest_sha256": sha256(output / "manifest.json"), "selection_sha256": sha256(output / "selected_rule.json"),
               "outputs": {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()}, "elapsed_seconds": manifest["elapsed_seconds"]}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return {"streams": len(folders), "selected_policy": selected, "elapsed_seconds": manifest["elapsed_seconds"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams-root", type=Path, default=SOURCE_STREAMS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stream-limit", type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.streams_root, args.output, stream_limit=args.stream_limit), sort_keys=True))


if __name__ == "__main__":
    main()
