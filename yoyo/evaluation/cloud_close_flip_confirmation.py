"""Vary only the V1.7 adverse EMA-close confirmation cadence.

The baseline paths come from the frozen V1.7 VectorBT build. Candidate paths
reuse that builder's VectorBT order callback: the 12H EMA state is replaced by
a causal N-consecutive-close gate, while the original OHLC, fixed stop, target,
fees, opening precedence, and gap handling stay in the existing callback.
The funded comparison delegates to the frozen scale-out and capacity runners.
"""
from __future__ import annotations

import argparse
from collections import Counter
from importlib.metadata import version
import json
import math
from pathlib import Path
import hashlib

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_priority as priority
from yoyo.evaluation import cloud_close_reward_account as account
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_slots as capacity


ROOT = engine.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-flip-confirm-20261109-v1"
PARENT_EXP = ROOT / "experiments/active/exp-cloud-close-reward-20261109-v1"
SIGNALS = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1/signals.csv"
PARENT_PRIORITY = ROOT / "experiments/active/exp-cloud-close-priority-20261109-v1"
PARENT_SLOTS = ROOT / "experiments/active/exp-cloud-close-slots-20261109-v1"
PARENT_SCALEOUT = ROOT / "experiments/active/exp-cloud-close-scaleout-20261109-v1"
PARENT_CFG = PARENT_EXP / "paths_config.json"
HOUR_MS = engine.HOUR_MS
STEP_12H = engine.STEP_12H
MODES = {"intrabar_baseline": None, "confirmed_1x12h": 1,
         "confirmed_2x12h": 2, "confirmed_3x12h": 3}
TP_R = (0.0, 2.0)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_json_safe(data), ensure_ascii=False,
                              indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _check_parent_picks(cfg: dict) -> dict:
    scale = json.loads((PARENT_SCALEOUT / "summary.json").read_text())
    slots = json.loads((PARENT_SLOTS / "summary.json").read_text())
    ordering = json.loads((PARENT_PRIORITY / "summary.json").read_text())
    expected = cfg["frozen_parent"]
    observed = {"scaleout_fraction": float(scale["pick"]),
                "slots": int(slots["pick"]), "priority": ordering["pick"]}
    if observed != {"scaleout_fraction": float(expected["scaleout_fraction"]),
                    "slots": int(expected["slots"]),
                    "priority": expected["priority"]}:
        raise ValueError(f"Frozen parent development pick changed: {observed}")
    if (float(ordering["parent_fraction"]) != observed["scaleout_fraction"]
            or int(ordering["slots"]) != observed["slots"]):
        raise ValueError("Priority pick does not inherit the frozen parent account")
    return {"scaleout": scale, "slots": slots, "priority": ordering,
            "observed": observed}


def _read_inputs(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    parent = pd.read_csv(PARENT_EXP / "paths.csv.gz", low_memory=False,
                         float_precision="round_trip")
    parent = parent[parent.tp_r.isin(TP_R)].copy()
    if parent.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Frozen parent paths have duplicate event/TP identities")
    event_sets = {float(r): set(g.event_id) for r, g in parent.groupby("tp_r")}
    if event_sets.get(0.0) != event_sets.get(2.0):
        raise ValueError("Frozen runner and 2R target event sets differ")
    expected_rows = int(cfg["frozen_source"]["expected_signal_rows"])
    source = pd.read_csv(SIGNALS, low_memory=False)
    if len(source) != expected_rows:
        raise ValueError(f"Expected {expected_rows} frozen signals, found {len(source)}")
    source.index = pd.RangeIndex(1, len(source) + 1)
    source_rows = set(pd.to_numeric(parent.source_row_number, errors="coerce").dropna().astype(int))
    if not source_rows.issubset(source.index):
        raise ValueError("Frozen path source_row_number does not join to signals.csv")
    if parent.groupby("event_id").parent_id.nunique().gt(1).any():
        raise ValueError("Frozen event_id maps to multiple parents")
    if parent["is_control"].dtype != bool:
        parent["is_control"] = parent.is_control.astype(str).str.lower().map(
            {"true": True, "false": False})
        if parent.is_control.isna().any():
            raise ValueError("Invalid is_control values in frozen paths")
    if not parent.entry_status.notna().all():
        raise ValueError("Frozen path entry_status contains nulls")
    source_counts = parent[parent.tp_r.eq(0)].entry_status.value_counts(dropna=False).to_dict()
    return parent, source, {"source_counts": source_counts,
                            "event_count": int(parent[parent.tp_r.eq(0)].event_id.nunique()),
                            "signal_event_count": int(parent[(parent.tp_r.eq(0))
                                                             & ~parent.is_control].event_id.nunique()),
                            "control_event_count": int(parent[(parent.tp_r.eq(0))
                                                              & parent.is_control].event_id.nunique())}


def _confirmation_q(q: np.ndarray, bar_ms: np.ndarray, features: dict,
                    confirmations: int) -> tuple[np.ndarray, dict]:
    """Gate the existing EMA-exit callback on completed same-sign 12H closes.

    The EMA state is read only at each actual 12H close. A missing 12H open
    breaks the streak; unready EMA values and exact zero spreads also reset it.
    The output is causal at each H1 open: streak N becomes visible only at or
    after that 12H bar's close time.
    """
    if confirmations not in (1, 2, 3):
        raise ValueError(f"Unsupported confirmation count: {confirmations}")
    if len(q) != len(bar_ms):
        raise ValueError("OHLC and hourly clock lengths differ")
    time12 = np.asarray(features["time12"], dtype=np.int64)
    close_times = np.asarray(features["close_times"], dtype=np.int64)
    ema20 = np.asarray(features["ema20"], dtype=float)
    ema50 = np.asarray(features["ema50"], dtype=float)
    ready = np.asarray(features["ema_ready"], dtype=bool)
    if not (len(time12) == len(close_times) == len(ema20) == len(ema50) == len(ready)):
        raise ValueError("12H EMA feature arrays have inconsistent lengths")
    if len(time12) > 1 and np.any(np.diff(time12) <= 0):
        raise AssertionError("12H timestamps must be strictly increasing")

    spreads = ema20 - ema50
    run_count = np.zeros(len(time12), dtype=np.int16)
    run_sign = np.zeros(len(time12), dtype=np.int8)
    previous_count, previous_sign = 0, 0
    previous_open = None
    for j, (bar_open, spread) in enumerate(zip(time12, spreads)):
        contiguous = previous_open is None or int(bar_open) == previous_open + STEP_12H
        if not contiguous:
            previous_count, previous_sign = 0, 0
        sign = 1 if spread > 0 else -1 if spread < 0 else 0
        if not ready[j] or sign == 0:
            previous_count, previous_sign = 0, 0
        else:
            previous_count = previous_count + 1 if sign == previous_sign else 1
            previous_sign = sign
        run_count[j] = previous_count
        run_sign[j] = previous_sign
        previous_open = int(bar_open)

    # Same callback, same OHLC and stop/TP arrays. NaN disables only the
    # intrabar price-cross level; spread carries the gated confirmed EMA side.
    result = q.copy()
    result[:, 4] = np.nan
    result[:, 5] = 0.0
    active = np.searchsorted(close_times, bar_ms, side="right") - 1
    valid = active >= 0
    rows = np.flatnonzero(valid)
    if len(rows):
        indices = active[rows]
        enabled = ((run_count[indices] >= confirmations) & ready[indices]
                   & (run_sign[indices] != 0))
        selected = rows[enabled]
        result[selected, 5] = q[selected, 5]
    return result, {
        "confirmation_count": confirmations,
        "actual_12h_close_rows": len(time12),
        "ema_ready_12h_close_rows": int(ready.sum()),
        "rows_with_confirmed_streak": int(np.count_nonzero(run_count >= confirmations)),
        "hourly_rows_with_active_confirmed_streak": int(np.count_nonzero(result[:, 5])),
        "streak_resets_at_missing_12h_open": True,
        "cross_level_disabled": bool(np.isnan(result[:, 4]).all()),
        "stale_12h_flags_preserved": bool(np.array_equal(result[:, 6], q[:, 6])),
    }


def _first_adverse_open(q: np.ndarray, start_i: int, side: int) -> int | None:
    if start_i + 1 >= len(q):
        return None
    found = np.flatnonzero(side * q[start_i + 1:, 5] < 0.0)
    return None if not len(found) else int(start_i + 1 + found[0])


def _check_path_bound(start_i: int, side: int, end_i: int, reason: str,
                      q: np.ndarray, bar_ms: np.ndarray,
                      adverse_i: int | None) -> None:
    if reason == "actual_hourly_series_tail":
        if end_i != len(q) - 1:
            raise AssertionError("Actual series-tail bound is not the physical 1H tail")
    elif reason == "first_opposite_confirmed_12h_open":
        if adverse_i != end_i or side * q[end_i, 5] >= 0.0:
            raise AssertionError("EMA-confirmation safe bound is not its first adverse open")
    elif reason == "first_hour_gap_open":
        if end_i <= start_i or bar_ms[end_i] - bar_ms[end_i - 1] <= HOUR_MS:
            raise AssertionError("Gap bound does not point to the next observed open")
    elif reason == "first_observable_stale_12h_open":
        if end_i <= start_i or q[end_i, 6] <= 0.0:
            raise AssertionError("Stale-data bound does not point to its observed open")
    else:
        raise AssertionError(f"Unknown safe path bound: {reason}")


def _entry_events_for_symbol(parent0: pd.DataFrame, q: np.ndarray,
                             bar_ms: np.ndarray) -> list[dict]:
    events = []
    for raw in parent0.to_dict("records"):
        if raw["entry_status"] != "executed":
            continue
        entry_ms = int(raw["entry_ms"])
        start = int(np.searchsorted(bar_ms, entry_ms, side="left"))
        if start >= len(bar_ms) or int(bar_ms[start]) != entry_ms:
            raise ValueError(f"Frozen entry is absent from 1H opens: {raw['event_id']}")
        if not np.isclose(float(q[start, 0]), float(raw["entry_price"]),
                          rtol=1.e-9, atol=1.e-14):
            raise ValueError(f"Frozen entry price differs from its 1H open: {raw['event_id']}")
        if not np.isfinite(q[start, 5]) or float(raw["side"]) * q[start, 5] <= 0.0:
            raise ValueError(f"Event is not strictly EMA-aligned at entry: {raw['event_id']}")
        event = dict(raw)
        event["entry_i"] = start
        event["risk_distance"] = float(raw["risk_distance"])
        events.append(event)
    return events


def _prepare_mode_events(events: list[dict], q: np.ndarray, bar_ms: np.ndarray,
                         confirmations: int) -> tuple[list[dict], dict]:
    prepared = []
    bounds = Counter()
    candidate_count = 0
    for original in events:
        event = dict(original)
        adverse_i = _first_adverse_open(q, int(event["entry_i"]), int(event["side"]))
        end_i, reason = engine._safe_path_end(int(event["entry_i"]), int(event["side"]),
                                              q, bar_ms)
        _check_path_bound(int(event["entry_i"]), int(event["side"]), end_i,
                          reason, q, bar_ms, adverse_i)
        if adverse_i is not None and adverse_i > end_i:
            # A prior gap or stale-data markout closes the position before a
            # later cadence confirmation can be reached.
            adverse_i = None
        if adverse_i is not None:
            candidate_count += 1
            if adverse_i <= int(event["entry_i"]):
                raise AssertionError("A confirmed exit may not occur at the entry open")
            if int(bar_ms[adverse_i]) <= int(event["entry_ms"]):
                raise AssertionError("Confirmation exit is not strictly after the entry time")
        event["end_i"] = end_i
        event["path_upper_bound_reason"] = reason
        event["confirmed_close_count_required"] = confirmations
        event["confirmed_close_candidate_ms"] = (int(bar_ms[adverse_i])
                                                   if adverse_i is not None else None)
        event["path_cap_before_series_tail"] = end_i < len(q) - 1
        bounds[reason] += 1
        prepared.append(event)
    return prepared, {"safe_path_bound_counts": dict(bounds),
                      "events_with_confirmed_exit_candidate": candidate_count,
                      "events_without_confirmed_exit_before_bound": len(events)-candidate_count,
                      "series_tail_bound_is_physical": all(
                          e["end_i"] == len(q)-1 for e in prepared
                          if e["path_upper_bound_reason"] == "actual_hourly_series_tail")}


def _variant_paths_for_symbol(events: list[dict], q: np.ndarray, bar_ms: np.ndarray,
                              features: dict, confirmations: int,
                              batch_size: int) -> tuple[pd.DataFrame, dict]:
    mode_q, q_audit = _confirmation_q(q, bar_ms, features, confirmations)
    prepared, bounds = _prepare_mode_events(events, mode_q, bar_ms, confirmations)
    rows, execution = engine._simulate_symbol(prepared, mode_q, bar_ms, TP_R, batch_size)
    candidates = {e["event_id"]: e.get("confirmed_close_candidate_ms") for e in prepared}
    end_details = {e["event_id"]: e for e in prepared}
    reason_counts = Counter()
    tail_count = 0
    for row in rows:
        event_id = row["event_id"]
        event = end_details[event_id]
        vbt_reason = str(row["exit_reason"])
        row["vectorbt_exit_reason"] = vbt_reason
        row["cadence"] = f"confirmed_{confirmations}x12h"
        row["confirmed_close_count_required"] = confirmations
        row["confirmed_close_candidate_ms"] = candidates[event_id]
        row["safe_path_bound_reason"] = event["path_upper_bound_reason"]
        row["safe_path_end_i"] = int(event["end_i"])
        row["safe_path_cap_before_series_tail"] = bool(event["path_cap_before_series_tail"])
        if vbt_reason == "ema_flip_open":
            if candidates[event_id] is None or int(row["exit_bar_ms"]) != int(candidates[event_id]):
                raise AssertionError("Confirmed EMA exit is not at its next available H1 open")
            row["exit_reason"] = f"adverse_ema_{confirmations}_confirmed_12h_close_open"
        elif vbt_reason == "ema_flip":
            raise AssertionError("Intrabar EMA exit must be disabled in close-confirmation modes")
        row["forced_end_flag"] = bool(vbt_reason == "series_tail_close")
        if row["forced_end_flag"]:
            tail_count += 1
            if int(row["exit_bar_ms"]) != int(bar_ms[-1]):
                raise AssertionError("series_tail_close fired before the actual hourly file tail")
        if row["exit_reason"].startswith("adverse_ema_"):
            if int(row["exit_bar_ms"]) <= int(row["entry_ms"]):
                raise AssertionError("EMA confirmation exit must follow the entry open")
        reason_counts[row["exit_reason"]] += 1
    frame = pd.DataFrame(rows)
    expected_rows = len(prepared) * len(TP_R)
    if len(frame) != expected_rows or frame.duplicated(["event_id", "tp_r"]).any():
        raise AssertionError("VectorBT did not return two unique fills per executable event")
    audit = {
        "confirmation_q": q_audit,
        **bounds,
        "vectorbt": execution,
        "exit_reason_counts": dict(reason_counts),
        "series_tail_close_count": tail_count,
        "series_tail_exits_are_physical": True,
        "executable_event_count": len(prepared),
        "path_rows": len(frame),
    }
    return frame, audit


def _annotate_unexecuted(rows: pd.DataFrame, cadence: str,
                         confirmations: int | None) -> pd.DataFrame:
    out = rows.copy()
    out["cadence"] = cadence
    out["confirmed_close_count_required"] = confirmations or 0
    out["confirmed_close_candidate_ms"] = np.nan
    out["safe_path_bound_reason"] = out.get("path_upper_bound_reason", "")
    out["safe_path_end_i"] = np.nan
    out["safe_path_cap_before_series_tail"] = False
    out["forced_end_flag"] = False
    return out


def _build_variant_paths(parent: pd.DataFrame, cfg: dict,
                         path_cfg: dict) -> tuple[pd.DataFrame, dict, dict[str, int]]:
    parent0 = parent[parent.tp_r.eq(0)].copy()
    parent2 = parent[parent.tp_r.eq(2)].copy()
    parent2 = parent2.set_index("event_id", drop=False)
    source_counts = Counter()
    output_frames = [_annotate_unexecuted(parent.copy(), "intrabar_baseline", None)]
    output_frames[0]["vectorbt_exit_reason"] = output_frames[0]["exit_reason"]
    output_frames[0]["safe_path_bound_reason"] = output_frames[0]["path_upper_bound_reason"]
    output_frames[0]["safe_path_end_i"] = np.nan
    output_frames[0]["safe_path_cap_before_series_tail"] = (
        output_frames[0]["path_upper_bound_reason"].notna()
        & output_frames[0]["path_upper_bound_reason"].ne("actual_hourly_series_tail"))
    output_frames[0]["forced_end_flag"] = output_frames[0]["exit_reason"].eq("series_tail_close")
    audit_by_mode = {"intrabar_baseline": {
        "input": "frozen parent paths, tp_r 0 and 2",
        "path_rows": len(output_frames[0]),
        "exit_reason_counts": output_frames[0].exit_reason.value_counts(dropna=False).to_dict(),
        "series_tail_close_count": int(output_frames[0].exit_reason.eq("series_tail_close").sum()),
    }}
    variant_frames = {n: [] for n in (1, 2, 3)}
    mode_audits = {n: {} for n in (1, 2, 3)}
    tail_by_symbol = {}
    batch_size = int(cfg["execution"]["event_batch_size"])
    for symbol, group in parent0.groupby("symbol", sort=True):
        executable = group[group.entry_status.eq("executed")]
        untouched = group[~group.entry_status.eq("executed")]
        if len(untouched):
            target_untouched = parent2.loc[untouched.event_id]
            unexecuted_pair = pd.concat([untouched, target_untouched], ignore_index=True)
            for confirmations in (1, 2, 3):
                mode_name = f"confirmed_{confirmations}x12h"
                variant_frames[confirmations].append(
                    _annotate_unexecuted(unexecuted_pair, mode_name, confirmations))
        if executable.empty:
            continue
        was_top20 = bool(executable.was_top20.iloc[0])
        p12, p1, source_group = engine._choose_input(str(symbol), was_top20, path_cfg)
        source_counts[source_group] += 1
        bars12, bars1 = engine._load_bars(p12, "12h"), engine._load_bars(p1, "1h")
        if bars12.empty or bars1.empty:
            raise ValueError(f"Executed frozen paths have missing bars: {symbol}")
        q, bar_ms, prepared_data = engine._prepare_symbol(bars12, bars1)
        features = prepared_data.get("features", {})
        if not features:
            raise ValueError(f"EMA features missing for executable symbol: {symbol}")
        events = _entry_events_for_symbol(executable, q, bar_ms)
        tail_by_symbol[str(symbol)] = int(bars1.index[-1])
        for confirmations in (1, 2, 3):
            mode_name = f"confirmed_{confirmations}x12h"
            mode_rows, mode_audit = _variant_paths_for_symbol(
                events, q, bar_ms, features, confirmations, batch_size)
            mode_rows["source_group"] = source_group
            mode_audit.update({
                "source_group": source_group,
                "bars12h_path": str(p12.relative_to(ROOT)),
                "bars1h_path": str(p1.relative_to(ROOT)),
                "bars12h_rows": len(bars12),
                "bars1h_rows": len(bars1),
                "bars1h_last_open_ms": int(bars1.index[-1]),
                "hourly_gap_count": int(prepared_data["audit"]["hourly_gap_count"]),
                "stale_12h_trigger_count": int(prepared_data["audit"]["stale_12h_trigger_count"]),
                "missing_12h_period_count": int(prepared_data["audit"]["missing_12h_period_count"]),
                "missing_12h_with_complete_contiguous_1h_count": int(
                    prepared_data["audit"]["missing_12h_with_complete_contiguous_1h_count"]),
            })
            mode_audits[confirmations][str(symbol)] = mode_audit
            variant_frames[confirmations].append(mode_rows)

    for confirmations in (1, 2, 3):
        mode_name = f"confirmed_{confirmations}x12h"
        pieces = variant_frames[confirmations]
        mode_frame = pd.concat(pieces, ignore_index=True, sort=False) if pieces else parent.iloc[:0].copy()
        mode_frame["cadence"] = mode_name
        mode_frame["confirmed_close_count_required"] = confirmations
        if mode_frame.duplicated(["event_id", "tp_r"]).any():
            raise AssertionError(f"Duplicate path identity in {mode_name}")
        if set(mode_frame.event_id) != set(parent.event_id):
            raise AssertionError(f"Frozen event set changed in {mode_name}")
        if set(pd.to_numeric(mode_frame.tp_r).unique()) != set(TP_R):
            raise AssertionError(f"Frozen TP pair changed in {mode_name}")
        mode_frame = mode_frame.sort_values(["source_row_number", "is_control", "tp_r"],
                                            kind="stable")
        output_frames.append(mode_frame)
        audit_by_mode[mode_name] = {
            "per_symbol": mode_audits[confirmations],
            "symbols_processed": len(mode_audits[confirmations]),
            "unmatched_or_no_entry_rows": int((mode_frame.entry_status != "executed").sum()),
            "executed_path_rows": int((mode_frame.entry_status == "executed").sum()),
            "exit_reason_counts": mode_frame.exit_reason.value_counts(dropna=False).to_dict(),
            "same_bar_ambiguous_rows": int(mode_frame.same_bar_ambiguous.fillna(False).sum()),
            "gap_exit_rows": int(mode_frame.gap_flag.fillna(False).sum()),
            "forced_end_rows": int(mode_frame.forced_end_flag.fillna(False).sum()),
            "safe_bounds": dict(Counter(mode_frame.safe_path_bound_reason.dropna().astype(str))),
        }

    combined = pd.concat(output_frames, ignore_index=True, sort=False)
    if combined.duplicated(["cadence", "event_id", "tp_r"]).any():
        raise AssertionError("Combined cadence/event/TP identity is not unique")
    expected = len(parent) * len(MODES)
    if len(combined) != expected:
        raise AssertionError(f"Expected {expected} path rows, created {len(combined)}")
    return combined, {"per_mode": audit_by_mode,
                      "input_group_symbol_counts": dict(source_counts),
                      "mode_count": len(MODES), "path_rows": len(combined)}, tail_by_symbol


def _set_priority(paths: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    field, sign = priority.POLICIES["low_prior_atr"]
    values = source[field]
    out = paths.copy()
    out["entry_order"] = (pd.to_numeric(out.source_row_number, errors="coerce")
                           .map(values) * sign).fillna(np.inf)
    return out


def _parent_baseline_metrics(parent_picks: dict) -> tuple[dict, dict]:
    metrics = pd.read_csv(PARENT_PRIORITY / "account_metrics.csv")
    rows = metrics[metrics.policy.eq("low_prior_atr")].drop(columns="policy")
    needed = {"all", "dev", "2025", "2026"}
    if set(rows.period) != needed:
        raise ValueError("Frozen priority baseline is missing required periods")
    summary = parent_picks["priority"]
    check = summary.get("checks", {}).get("low_prior_atr")
    if check is None:
        raise ValueError("Frozen priority baseline lacks control/sensitivity checks")
    return {str(row.period): row.drop(labels="period").to_dict()
            for _, row in rows.iterrows()}, check


def _candidate_metrics(paths: pd.DataFrame, cfg: dict, source: pd.DataFrame,
                       parent_picks: dict) -> tuple[pd.DataFrame, dict]:
    fraction = float(cfg["frozen_parent"]["scaleout_fraction"])
    slots = int(cfg["frozen_parent"]["slots"])
    paths = _set_priority(paths, source)
    baseline_metrics, baseline_checks = _parent_baseline_metrics(parent_picks)
    output_rows = [dict(cadence="intrabar_baseline", confirmations=0,
                        period=period, **values)
                   for period, values in baseline_metrics.items()]
    execution_by_mode = {}
    for confirmations in (1, 2, 3):
        cadence = f"confirmed_{confirmations}x12h"
        events = paths[(paths.cadence == cadence)
                       & (paths.entry_status == "executed")
                       & (~paths.is_control)].copy()
        if events.empty:
            raise ValueError(f"No executable frozen signals for {cadence}")
        metrics, meta, trades, equity = capacity.run(events, slots, fraction)
        execution_by_mode[cadence] = meta
        output_rows.extend(dict(cadence=cadence, confirmations=confirmations,
                                period=period, **values)
                           for period, values in metrics.items())
    frame = pd.DataFrame(output_rows)
    dev = frame[frame.period.eq("dev")].copy()
    eligible = dev[dev.apply(account.qualifies, axis=1) & dev.trades.ge(100)]
    best_row = dev.sort_values("multiple", ascending=False, kind="stable").iloc[0]
    selected_row = (eligible.sort_values("multiple", ascending=False, kind="stable").iloc[0]
                    if len(eligible) else dev[dev.cadence.eq("intrabar_baseline")].iloc[0])
    baseline_row = dev[dev.cadence.eq("intrabar_baseline")].iloc[0]
    baseline_eligible = bool(account.qualifies(baseline_row) and baseline_row.trades >= 100)
    if baseline_eligible and selected_row.cadence != "intrabar_baseline":
        if float(selected_row.multiple) <= float(baseline_row.multiple):
            raise AssertionError("Dev selection did not prefer the qualifying baseline tie")

    checks = {"intrabar_baseline": baseline_checks}
    modes_to_check = sorted({str(selected_row.cadence), str(best_row.cadence)} -
                            {"intrabar_baseline"})
    control_frame = paths[(paths.cadence == "confirmed_1x12h")
                          & paths.is_control & (paths.entry_status == "executed")]
    for cadence in modes_to_check:
        variant = paths[(paths.cadence == cadence) & (paths.entry_status == "executed")]
        actual = variant[~variant.is_control]
        controls = variant[variant.is_control]
        paired = actual[actual.parent_id.isin(controls.parent_id)]
        checks[cadence] = {}
        for label, pool, slip in (("matched_controls", controls, 0.0),
                                  ("paired_signals", paired, 0.0),
                                  ("slippage_5bp", actual, 0.0005)):
            m, details, _, _ = capacity.run(pool, slots, fraction, slip)
            checks[cadence][label] = {"metrics": m, "admission": details}
    if not control_frame.empty and control_frame.parent_id.nunique() != control_frame.event_id.nunique():
        raise ValueError("Frozen matched controls have duplicated parent IDs")
    selection = {
        "development_period": "through 2024-12-31 UTC exclusive at 2025-01-01",
        "eligible_modes": eligible.cadence.tolist(),
        "baseline_eligible": baseline_eligible,
        "pick": str(selected_row.cadence),
        "best_development_growth": str(best_row.cadence),
        "pick_multiple": float(selected_row.multiple),
        "best_development_multiple": float(best_row.multiple),
        "fallback": "intrabar_baseline when no mode meets unchanged gates",
    }
    return frame, {"selection": selection, "checks": checks,
                   "execution_by_mode": execution_by_mode}


def _audit_parent_tail_rows(parent: pd.DataFrame, tail_by_symbol: dict[str, int]) -> dict:
    tails = parent[parent.exit_reason.eq("series_tail_close")]
    bad_clock = []
    bad_reason = []
    for row in tails.itertuples():
        expected_tail = tail_by_symbol.get(str(row.symbol))
        if expected_tail is None or int(row.exit_bar_ms) != expected_tail:
            bad_clock.append(str(row.event_id))
        if str(row.path_upper_bound_reason) != "actual_hourly_series_tail":
            bad_reason.append(str(row.event_id))
    if bad_clock or bad_reason:
        raise AssertionError(f"Frozen parent has anomalous tail bounds: {len(bad_clock)} clock, "
                             f"{len(bad_reason)} reason")
    return {"parent_series_tail_exits": len(tails),
            "parent_tail_timestamps_match_physical_files": True,
            "parent_tail_bound_reasons_match": True,
            "parent_non_tail_rows_with_series_tail_reason": int(
                ((parent.path_upper_bound_reason == "actual_hourly_series_tail")
                 & (parent.exit_reason != "series_tail_close")).sum())}


def _write_csv_atomic(frame: pd.DataFrame, path: Path, compression=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False, compression=compression)
    tmp.replace(path)


def generate(config_path: Path = EXP / "config.json") -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if cfg.get("experiment_id") != EXP.name:
        raise ValueError("Experiment ID must match the fixed output directory")
    if tuple(float(v) for v in cfg["target_r"]) != TP_R:
        raise ValueError(f"The 2R scale-out pair is frozen at {TP_R}")
    outputs = cfg["outputs"]
    output_paths = [ROOT / outputs[key] for key in ("paths_csv_gz",
                                                    "paths_audit_json",
                                                    "account_metrics_csv",
                                                    "summary_json")]
    if any(path.exists() for path in output_paths):
        raise FileExistsError("Refusing to overwrite frozen confirmation outputs")
    picks = _check_parent_picks(cfg)
    parent, source, input_audit = _read_inputs(cfg)
    parent_audit = json.loads((PARENT_EXP / "paths_audit.json").read_text(encoding="utf-8"))
    path_cfg = json.loads(PARENT_CFG.read_text(encoding="utf-8"))
    baseline_parent = parent[parent.exit_reason.eq("series_tail_close")]
    # Build candidate paths; executable entries must remain the original exact-open ledger.
    print("Building causal 12H confirmation paths with the frozen VectorBT callback", flush=True)
    all_paths, path_audit, tail_by_symbol = _build_variant_paths(parent, cfg, path_cfg)
    tail_audit = _audit_parent_tail_rows(parent, tail_by_symbol)
    if len(baseline_parent) != tail_audit["parent_series_tail_exits"]:
        raise AssertionError("Parent series-tail audit count changed while reading")
    account_metrics, account_audit = _candidate_metrics(all_paths, cfg, source, picks)

    revision = hashlib.sha256(Path(__file__).read_bytes() + config_path.read_bytes()).hexdigest()
    audit = {
        "experiment_id": cfg["experiment_id"],
        "generator": "yoyo/evaluation/cloud_close_flip_confirmation.py",
        "source_sha256": revision,
        "vectorbt_version": vbt.__version__,
        "numpy_version": version("numpy"),
        "pandas_version": version("pandas"),
        "numba_version": version("numba"),
        "input_parent_paths": str((PARENT_EXP / "paths.csv.gz").relative_to(ROOT)),
        "input_source_signals": str(SIGNALS.relative_to(ROOT)),
        "input_signal_rows": len(source),
        "original_signal_source_counts": parent_audit.get("signal_source_counts", {}),
        "frozen_parent_picks": picks["observed"],
        "frozen_path_counts": input_audit,
        "path_build": path_audit,
        "parent_tail_bounds": tail_audit,
        "account_selection": account_audit["selection"],
        "account_checks": account_audit["checks"],
        "account_execution": account_audit["execution_by_mode"],
        "fees": {"market_or_stop_per_fill": engine.TAKER,
                 "resting_2r_limit_per_fill": engine.MAKER},
        "control_join": {
            "key": "parent_id; frozen event_id is parent_id + ':signal' or parent_id + ':control'",
            "frozen_control_candidates": input_audit["control_event_count"],
            "unmatched_and_no_entry_rows_retained": True,
        },
        "chronology": {
            "signal_entry": "exact H1 open at the completed UTC12H confirmation timestamp",
            "confirmation_streak_start": "first completed 12H close strictly after the frozen entry_ms",
            "next_open": "first observed H1 open timestamp >= the Nth adverse 12H close timestamp",
            "same_timestamp_entry_confirmation": "cannot exit at entry; strictly post-entry closes only",
            "gap_precedence": "the original VectorBT callback marks the first next observed open before reading that bar OHLC",
            "stale_12h": "original stale flag and callback precedence retained",
        },
        "output_rows": len(all_paths),
        "output_cadence_counts": all_paths.cadence.value_counts(dropna=False).to_dict(),
    }
    summary = {
        "experiment_id": cfg["experiment_id"],
        "development_pick": account_audit["selection"],
        "account_metrics": account_metrics.to_dict("records"),
        "checks": account_audit["checks"],
        "frozen_parent_picks": picks["observed"],
    }
    _write_csv_atomic(all_paths, output_paths[0], compression="gzip")
    _write_json(output_paths[1], audit)
    _write_csv_atomic(account_metrics, output_paths[2])
    _write_json(output_paths[3], summary)
    print(account_metrics[account_metrics.period.eq("all")].to_string(index=False), flush=True)
    return audit


def _synthetic_event(event_id: str, q: np.ndarray, side: int = 1,
                     stop: float = 95.0) -> dict:
    entry = float(q[0, 0])
    return {
        "event_id": event_id, "id": event_id, "parent_id": event_id,
        "source_row_number": 1, "symbol": "SYNTH", "week": "2026-01-05",
        "side": side, "tp_r": 0.0, "is_control": False, "entry_i": 0,
        "entry_ms": 0, "entry_price": entry, "stop_price": stop,
        "risk_distance": abs(entry-stop), "risk_fraction": abs(entry-stop)/entry,
        "end_i": len(q)-1, "path_upper_bound_reason": "actual_hourly_series_tail",
        "entry_status": "executed", "control_match_status": "not_applicable",
    }


def self_check() -> dict:
    """Exercise close counters and the actual VectorBT stop/open callback."""
    h12 = STEP_12H
    time12 = np.array([-h12, 0, h12, 2*h12], dtype=np.int64)
    close_times = time12 + h12
    spreads = np.array([1.0, -1.0, -2.0, 1.0])
    ready = np.ones(4, dtype=bool)
    hourly = np.arange(0, 3*h12+HOUR_MS, HOUR_MS, dtype=np.int64)
    active = np.searchsorted(close_times, hourly, side="right") - 1
    q = np.zeros((len(hourly), 7), dtype=float)
    q[:, :4] = 100.0
    q[:, 4] = 101.0
    q[:, 5] = np.where(active >= 0, spreads[np.maximum(active, 0)], np.nan)
    q[:, 6] = 0.0
    features = {"time12": time12, "close_times": close_times,
                "ema20": spreads, "ema50": np.zeros(4), "ema_ready": ready}
    gated_one, _ = _confirmation_q(q, hourly, features, 1)
    gated, counters = _confirmation_q(q, hourly, features, 2)
    gated_three, _ = _confirmation_q(q, hourly, features, 3)
    candidate_one = _first_adverse_open(gated_one, 0, 1)
    candidate = _first_adverse_open(gated, 0, 1)
    candidate_three = _first_adverse_open(gated_three, 0, 1)
    if candidate_one is None or int(hourly[candidate_one]) != h12:
        raise AssertionError("One-close cadence did not arm at the first confirmed close")
    if candidate is None or int(hourly[candidate]) != 2*h12:
        raise AssertionError("Two adverse closes did not arm at the second confirmed close")
    if candidate_three is not None:
        raise AssertionError("Three-close cadence armed before three adverse closes")
    if gated[12, 5] != 0.0 or gated[24, 5] >= 0.0:
        raise AssertionError("Confirmation streak became active before close two")
    confirmed_event = _synthetic_event("synthetic-confirmed-open-exit", gated, stop=90.0)
    confirmed_event["end_i"] = candidate
    confirmed_event["path_upper_bound_reason"] = "first_opposite_confirmed_12h_open"
    confirmed_rows, _ = engine._simulate_symbol([confirmed_event], gated, hourly,
                                                 (0.0,), 1)
    if (confirmed_rows[0]["exit_reason"] != "ema_flip_open"
            or confirmed_rows[0]["exit_bar_ms"] != int(hourly[candidate])
            or confirmed_rows[0]["exit_phase"] != "open"):
        raise AssertionError("Confirmed close did not exit at its next H1 open")

    # A missing 12H open resets the consecutive-close counter.
    gapped_time12 = np.array([-h12, 0, 2*h12, 3*h12], dtype=np.int64)
    gapped_close_times = gapped_time12 + h12
    gapped_spreads = np.array([1.0, -1.0, -2.0, -3.0])
    gap_hourly = np.arange(0, 5*h12+HOUR_MS, HOUR_MS, dtype=np.int64)
    gapped_active = np.searchsorted(gapped_close_times, gap_hourly, side="right") - 1
    qg = np.zeros((len(gap_hourly), 7), dtype=float)
    qg[:, :4] = 100.0
    qg[:, 4] = 101.0
    qg[:, 6] = 0.0
    qg[:, 5] = np.where(gapped_active >= 0,
                         gapped_spreads[np.maximum(gapped_active, 0)], np.nan)
    gapped_features = {"time12": gapped_time12, "close_times": gapped_close_times,
                       "ema20": gapped_spreads, "ema50": np.zeros(4),
                       "ema_ready": ready}
    gated_gap, _ = _confirmation_q(qg, gap_hourly, gapped_features, 2)
    gap_candidate = _first_adverse_open(gated_gap, 0, 1)
    if gap_candidate is None or int(gap_hourly[gap_candidate]) != 4*h12:
        raise AssertionError("Missing 12H close did not reset the adverse-close counter")

    stop_q = np.array([
        [100., 100., 100., 100., np.nan, 0., 0.],
        [100., 110., 95., 100., np.nan, 0., 0.],
        [100., 102., 99., 101., np.nan, 0., 0.],
    ])
    stop_ms = np.arange(3, dtype=np.int64) * HOUR_MS
    event = _synthetic_event("synthetic-stop-target-double-touch", stop_q)
    rows, execution = engine._simulate_symbol([event], stop_q, stop_ms, TP_R, 1)
    by_r = {float(row["tp_r"]): row for row in rows}
    if by_r[0.0]["exit_reason"] != "stop" or by_r[0.0]["exit_price"] != 95.0:
        raise AssertionError("Protective stop did not win a stop-only path")
    if (by_r[2.0]["exit_reason"] != "stop" or by_r[2.0]["exit_price"] != 95.0
            or not by_r[2.0]["same_bar_ambiguous"]):
        raise AssertionError("Same-bar stop/TP ambiguity did not retain stop priority")

    # Entry open already beyond the fixed stop: VectorBT enters, then exits at
    # that same observed open, matching the callback's entry-bar precedence.
    entry_stop_q = np.array([
        [90., 90., 90., 90., np.nan, 0., 0.],
        [91., 92., 89., 90., np.nan, 0., 0.],
    ])
    entry_event = _synthetic_event("synthetic-entry-open-stop", entry_stop_q, stop=95.0)
    entry_rows, _ = engine._simulate_symbol([entry_event], entry_stop_q,
                                             stop_ms[:2], (0.0,), 1)
    if (entry_rows[0]["exit_reason"] != "stop_gap_open"
            or entry_rows[0]["exit_bar_ms"] != entry_rows[0]["entry_ms"]
            or entry_rows[0]["exit_price"] != 90.0):
        raise AssertionError("Entry-open stop precedence changed")

    # A gap outranks an armed confirmation and exits at the next observed open.
    gap_q = np.array([
        [100., 101., 99., 100., np.nan, 0., 0.],
        [100., 101., 99., 100., np.nan, 0., 0.],
        [103., 105., 101., 104., np.nan, -1., 0.],
    ])
    gap_ms = np.array([0, HOUR_MS, 3*HOUR_MS], dtype=np.int64)
    gap_event = _synthetic_event("synthetic-gap-before-open-confirm", gap_q, stop=95.0)
    gap_rows, _ = engine._simulate_symbol([gap_event], gap_q, gap_ms, (0.0,), 1)
    if (gap_rows[0]["exit_reason"] != "data_gap_markout"
            or gap_rows[0]["exit_bar_ms"] != 3*HOUR_MS
            or gap_rows[0]["exit_price"] != 103.0 or not gap_rows[0]["gap_flag"]):
        raise AssertionError("Data-gap next-open precedence changed")
    return {"one_close_candidate_ms": int(hourly[candidate_one]),
            "two_close_candidate_ms": int(hourly[candidate]),
            "three_close_not_armed": candidate_three is None,
            "counter_resets_across_missing_12h": True,
            "confirmed_exit_next_open": True,
            "synthetic_stop_target_rows": len(rows),
            "vectorbt_orders_and_trades": execution,
            "entry_open_stop_precedence": True,
            "gap_open_precedence": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true",
                        help="run small VectorBT synthetic boundary and counter checks only")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(_json_safe(self_check()), indent=2, allow_nan=False))
        return
    generate()


if __name__ == "__main__":
    main()

