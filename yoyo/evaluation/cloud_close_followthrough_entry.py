"""Price a single delayed 12H-close entry variant on the frozen cloud ledger.

The zero-delay baseline is read from the frozen V1.7 paths. Delayed candidates
are admitted only after one complete, contiguous 12H wait and are priced once
through the existing VectorBT path callback. The A+B arm reuses the exact
VectorBT fills from A when the added favorable-close condition passes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gc
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_reward_account as account
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_scaleout as scaleout
from yoyo.evaluation import cloud_close_slots as capacity


ROOT = account.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-followthrough-entry-20261109-v1"
PARENT_EXP = ROOT / "experiments/active/exp-cloud-close-reward-20261109-v1"
PRIORITY_EXP = ROOT / "experiments/active/exp-cloud-close-priority-20261109-v1"
HOUR_MS = engine.HOUR_MS
STEP_12H = engine.STEP_12H
TAKER = engine.TAKER
TP_RS = (0.0, 2.0)
BASELINE_ARM = "zero_delay_original_v1_7"
ARM_A = "delay_12h_A_no_early_stop_or_ema_exit"
ARM_AB = "delay_12h_A_plus_B_favorable_close"
ARMS = (BASELINE_ARM, ARM_A, ARM_AB)
PROTECTIVE_REASONS = {"stop", "stop_gap_open", "ema_flip", "ema_flip_open"}

PATH_FIELDS = {
    "event_id", "id", "parent_id", "source_row_number", "symbol", "week", "side",
    "tp_r", "is_control", "rank", "cohort", "is_current", "was_top20", "bar_source",
    "key_open_ms", "confirmation_ms", "confirmed_bjt", "confirmation_12h_bar_present",
    "entry_ms", "exit_bar_ms", "entry_time_utc", "exit_time_utc", "entry_price",
    "exit_price", "stop_price", "target_price", "risk_distance", "risk_fraction",
    "entry_fee_class", "entry_fee_rate", "entry_fee_amount", "exit_fee_class",
    "exit_fee_rate", "exit_fee_amount", "exit_reason", "status", "exit_phase",
    "same_bar_ambiguous", "ambiguous_triggers", "gap_flag", "gap_hours_missing",
    "gross_pnl", "net_pnl", "gross_r", "net_r", "entry_status", "control_match_status",
    "control_confirmation_ms", "control_12h_open_ms", "control_atr_relative_difference",
    "control_prior_atr_pct", "control_match_reason", "stop_basis", "source_path_12h",
    "source_path_1h", "path_upper_bound_reason",
}
REQUIRED_PATH_FIELDS = PATH_FIELDS | {"tp_r", "source_path_12h", "source_path_1h"}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_json_safe(value), ensure_ascii=False, indent=2,
                              allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _number(value) -> float | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return float(value)


def _integer(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _prior_exit_precedes_decision(exit_ms, phase: str | None, decision_ms: int,
                                  reason: str | None) -> bool:
    """Whether an original protective exit was observable by the decision open.

    An intrabar or close exit whose bar opens exactly at the decision is later
    than the coincident 1H open. Only an open-phase exit at equality is known
    before the delayed market entry is submitted.
    """
    if reason not in PROTECTIVE_REASONS or exit_ms is None or pd.isna(exit_ms):
        return False
    clock = int(exit_ms)
    if clock < int(decision_ms):
        return True
    return clock == int(decision_ms) and phase == "open"


def _exact_window_indices(anchor_ms: int, bar_ms: np.ndarray) -> tuple[np.ndarray | None, str]:
    """Return the 12 waiting H1 bars plus coincident decision open, if present."""
    expected = int(anchor_ms) + np.arange(13, dtype=np.int64) * HOUR_MS
    locations = np.searchsorted(bar_ms, expected, side="left")
    if np.any(locations >= len(bar_ms)) or not np.array_equal(bar_ms[locations], expected):
        return None, "no_complete_contiguous_12h_h1_window_or_decision_open"
    return locations.astype(np.int64), "eligible"


def _event_anchor(event: dict) -> tuple[int | None, int | None, str]:
    """Get the event's own completed 12H confirmation and its 12H bar open."""
    if bool(event.get("is_control")):
        anchor = _integer(event.get("control_confirmation_ms"))
        key_open = _integer(event.get("control_12h_open_ms"))
        if anchor is None or key_open is None or key_open + STEP_12H != anchor:
            return anchor, key_open, "invalid_control_confirmation_clock"
        return anchor, key_open, "eligible"
    anchor = _integer(event.get("confirmation_ms"))
    key_open = _integer(event.get("key_open_ms"))
    if anchor is None or key_open is None or key_open + STEP_12H != anchor:
        return anchor, key_open, "invalid_signal_confirmation_clock"
    return anchor, key_open, "eligible"


def _eligibility(event: dict, bars12: pd.DataFrame, bar_ms: np.ndarray,
                 native_sources: set[str]) -> dict:
    """Evaluate A/B from timestamps and completed source bars only."""
    base = {
        "condition_A": False, "condition_B": False,
        "anchor_entry_ms": _integer(event.get("entry_ms")), "decision_ms": None,
        "anchor_bar_open_ms": None, "original_exit_bar_ms": _integer(event.get("exit_bar_ms")),
        "original_exit_phase": event.get("exit_phase"), "original_exit_reason": event.get("exit_reason"),
        "original_confirmation_close": None, "delayed_12h_close": None,
        "delayed_12h_source": None, "favorable_close": False,
        "delayed_open_price": None, "decision_bar_i": None,
        "reason_A": "eligible", "reason_B": "eligible",
    }
    if event.get("entry_status") != "executed" or base["anchor_entry_ms"] is None:
        why = "original_entry_not_executable"
        base.update(reason_A=why, reason_B=why)
        return base

    anchor, reference_open, clock_reason = _event_anchor(event)
    if clock_reason != "eligible" or anchor != base["anchor_entry_ms"]:
        base.update(reason_A=clock_reason, reason_B=clock_reason,
                    anchor_bar_open_ms=reference_open)
        return base
    base["anchor_bar_open_ms"] = reference_open
    decision = int(anchor + STEP_12H)
    base["decision_ms"] = decision

    if anchor % STEP_12H or decision % STEP_12H:
        base.update(reason_A="unaligned_utc_12h_clock", reason_B="unaligned_utc_12h_clock")
        return base
    locations, wait_reason = _exact_window_indices(anchor, bar_ms)
    if locations is None:
        base.update(reason_A=wait_reason, reason_B=wait_reason)
        return base

    # The next 12H bar opens at the original confirmation boundary and is
    # complete at decision_ms. Its stored source must be genuinely 12H input.
    if anchor not in bars12.index:
        base.update(reason_A="missing_next_native_12h_bar", reason_B="missing_next_native_12h_bar")
        return base
    delayed_row = bars12.loc[anchor]
    source = str(delayed_row.get("source", ""))
    base["delayed_12h_source"] = source
    if source not in native_sources:
        base.update(reason_A="next_12h_bar_not_native_source", reason_B="next_12h_bar_not_native_source")
        return base

    if _prior_exit_precedes_decision(event.get("exit_bar_ms"), event.get("exit_phase"),
                                     decision, event.get("exit_reason")):
        why = "original_stop_or_ema_exit_known_by_delayed_open"
        base.update(reason_A=why, reason_B=why)
        return base

    decision_i = int(locations[-1])
    base["decision_bar_i"] = decision_i
    base["delayed_open_price"] = float(event["_q"][decision_i, 0])
    base["delayed_12h_close"] = float(delayed_row["close"])

    stop = _number(event.get("stop_price"))
    side = int(event["side"])
    if stop is None or side not in (-1, 1):
        why = "missing_fixed_stop_or_side"
        base.update(reason_A=why, reason_B=why)
        return base
    if side * (base["delayed_open_price"] - stop) <= 0.0:
        why = "delayed_open_at_or_beyond_fixed_stop"
        base.update(reason_A=why, reason_B=why)
        return base

    base["condition_A"] = True
    base["reason_A"] = "eligible"
    if reference_open not in bars12.index:
        base["reason_B"] = "original_confirmation_close_unavailable"
        return base
    original_close = float(bars12.loc[reference_open, "close"])
    base["original_confirmation_close"] = original_close
    favorable = side * (base["delayed_12h_close"] - original_close) > 0.0
    base["favorable_close"] = bool(favorable)
    base["condition_B"] = bool(favorable)
    base["reason_B"] = "eligible" if favorable else "next_12h_close_not_favorable"
    return base


def _candidate_event(event: dict, q: np.ndarray, bar_ms: np.ndarray,
                     facts: dict) -> dict:
    entry_i = int(facts["decision_bar_i"])
    entry = float(q[entry_i, 0])
    stop = float(event["stop_price"])
    risk = abs(entry - stop)
    delayed = dict(event)
    delayed.update(
        entry_i=entry_i, entry_ms=int(facts["decision_ms"]), entry_price=entry,
        stop_price=stop, risk_distance=risk,
        risk_fraction=(risk / entry if entry > 0.0 else None),
        entry_status="pending", path_upper_bound_reason=None,
    )
    delayed["end_i"], delayed["path_upper_bound_reason"] = engine._safe_path_end(
        entry_i, int(event["side"]), q, bar_ms)
    return delayed


def _blank_candidate(event: dict, arm: str, tp_r: float, facts: dict,
                     reason: str) -> dict:
    row = dict(event)
    for key in ("entry_time_utc", "exit_time_utc", "entry_price", "exit_price", "target_price",
                "entry_fee_class", "entry_fee_rate", "entry_fee_amount", "exit_fee_class",
                "exit_fee_rate", "exit_fee_amount", "exit_bar_ms", "exit_phase", "gross_pnl",
                "net_pnl", "gross_r", "net_r"):
        row[key] = None
    row.update(
        arm=arm, tp_r=float(tp_r), entry_ms=None, exit_reason=reason, status=reason,
        entry_status=reason, risk_distance=None, risk_fraction=None,
        same_bar_ambiguous=False, ambiguous_triggers="", gap_flag=False,
        gap_hours_missing=0, condition_A=bool(facts["condition_A"]),
        condition_B=bool(facts["condition_B"]), eligibility_reason_A=facts["reason_A"],
        eligibility_reason_B=facts["reason_B"], anchor_entry_ms=facts["anchor_entry_ms"],
        decision_ms=facts["decision_ms"], anchor_bar_open_ms=facts["anchor_bar_open_ms"],
        original_exit_bar_ms=facts["original_exit_bar_ms"],
        original_exit_phase=facts["original_exit_phase"],
        original_exit_reason=facts["original_exit_reason"],
        original_confirmation_close=facts["original_confirmation_close"],
        delayed_12h_close=facts["delayed_12h_close"],
        delayed_12h_source=facts["delayed_12h_source"],
        favorable_close=bool(facts["favorable_close"]),
        entry_status_original=event.get("entry_status"),
    )
    return row


def _load_paths(path: Path) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0).columns
    missing = sorted(REQUIRED_PATH_FIELDS - set(header))
    if missing:
        raise ValueError(f"Frozen parent path schema missing fields: {missing}")
    fields = sorted(PATH_FIELDS)
    frame = pd.read_csv(path, usecols=fields, dtype={"symbol": str, "event_id": str,
                                                      "parent_id": str},
                        low_memory=False, float_precision="round_trip")
    frame = frame[frame.tp_r.isin(TP_RS)].copy()
    if frame.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Frozen parent paths duplicate event_id/tp_r")
    if not frame.groupby("event_id").tp_r.apply(lambda x: set(x.astype(float)) == set(TP_RS)).all():
        raise ValueError("Every frozen event must retain both no-TP and 2R rows")
    return frame


def _blank_for_missing_bars(event: dict, arm: str, facts: dict) -> list[dict]:
    reason_a = facts["reason_A"]
    reason_b = facts["reason_B"]
    return [
        _blank_candidate(event, arm, tp, facts,
                         reason_a if arm == ARM_A or not facts["condition_A"] else reason_b)
        for tp in TP_RS
    ]


def _sort_output(frame: pd.DataFrame) -> pd.DataFrame:
    arm_order = {arm: i for i, arm in enumerate(ARMS)}
    frame["_arm_order"] = frame.arm.map(arm_order)
    frame = frame.sort_values(["source_row_number", "is_control", "_arm_order", "tp_r"],
                              kind="stable")
    return frame.drop(columns="_arm_order").reset_index(drop=True)


def _read_source_signals(path: Path) -> pd.DataFrame:
    signals = pd.read_csv(path, usecols=["prior_atr_pct"], low_memory=False)
    signals.index = pd.RangeIndex(1, len(signals) + 1)
    return signals


def _apply_priority(frame: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    source_rows = pd.to_numeric(result.source_row_number, errors="coerce").astype("Int64")
    result["entry_order"] = source_rows.map(signals.prior_atr_pct).astype(float).fillna(np.inf)
    return result


def _run_capacity(frame: pd.DataFrame, slots: int, fraction: float,
                  slip: float = 0.0) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    executable = frame[frame.entry_status.eq("executed")].copy()
    if executable.empty:
        raise ValueError("Cannot run capacity account on an empty executable cohort")
    if not executable.groupby("event_id").tp_r.apply(
            lambda x: set(x.astype(float)) == set(TP_RS)).all():
        raise ValueError("Capacity input is missing an entry's paired no-TP/2R paths")
    return capacity.run(executable, slots, fraction, slip)


def _baseline_metrics() -> tuple[dict, dict]:
    metrics_path = PRIORITY_EXP / "account_metrics.csv"
    summary_path = PRIORITY_EXP / "summary.json"
    metrics = pd.read_csv(metrics_path)
    rows = metrics[metrics.policy.eq("low_prior_atr")]
    if set(rows.period) != {"all", "dev", "2025", "2026"}:
        raise ValueError("Frozen low-prior-ATR baseline metrics are incomplete")
    result = {str(row.period): {k: _json_safe(v) for k, v in row.drop(labels="policy").to_dict().items()}
              for _, row in rows.iterrows()}
    checks = json.loads(summary_path.read_text(encoding="utf-8"))["checks"]["low_prior_atr"]
    return result, checks


def _metric_eligible(row: dict, gates: dict) -> bool:
    return (int(row.get("trades") or 0) >= int(gates["minimum_trades"])
            and row.get("win_pct") is not None and row["win_pct"] >= float(gates["minimum_win_pct"])
            and (row.get("payoff") or 0.0) >= float(gates["minimum_equal_notional_payoff"])
            and (row.get("payoff_usdt") or 0.0) >= float(gates["minimum_funded_usdt_payoff"])
            and (row.get("dd_bound_pct") or math.inf) <= float(gates["maximum_dd_bound_pct"]))


def generate(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(float(x) for x in config["take_profit_r"]) != TP_RS:
        raise ValueError(f"Frozen delayed-entry targets must remain {TP_RS}")
    if config["account"]["entry_priority"] != "low_prior_atr":
        raise ValueError("This test is frozen to low-prior-ATR priority")
    if (float(config["fees"]["taker_per_fill"]) != TAKER
            or float(config["fees"]["resting_maker_per_fill"]) != engine.MAKER):
        raise ValueError("Fee schedule diverges from the frozen V1.7 path engine")

    output_paths = {name: ROOT / value for name, value in config["outputs"].items()}
    materialized_paths = [*output_paths.values()]
    for arm in (ARM_A, ARM_AB):
        materialized_paths.extend((
            output_paths["funded"].with_name(f"funded_{arm}.csv.gz"),
            output_paths["equity"].with_name(f"hourly_{arm}.csv.gz"),
        ))
    if any(path.exists() for path in materialized_paths):
        raise FileExistsError("Refusing to overwrite an existing followthrough experiment output")
    source_path = ROOT / config["inputs"]["parent_paths"]
    signal_path = ROOT / config["inputs"]["signals"]
    if not source_path.is_file() or not signal_path.is_file():
        raise FileNotFoundError("Frozen source paths or signal ledger are missing")

    frozen = _load_paths(source_path)
    runner = frozen[frozen.tp_r.eq(0.0)].copy()
    targets = frozen[frozen.tp_r.eq(2.0)].copy().set_index("event_id", drop=False)
    if runner.event_id.nunique() != len(runner):
        raise ValueError("One no-TP anchor row per original event is required")
    signals = _read_source_signals(signal_path)
    if runner.source_row_number.max() > len(signals):
        raise ValueError("Frozen path source_row_number exceeds signals.csv")
    native_sources = set(config["eligibility"]["native_12h_sources"])
    batch_size = int(config["execution"]["event_batch_size"])
    if batch_size < 1:
        raise ValueError("event_batch_size must be positive")

    # Start with the two already-priced rows per event. No baseline repricing.
    output_rows: list[dict] = []
    event_facts: dict[str, dict] = {}
    priced_event_rows: dict[str, dict[float, dict]] = defaultdict(dict)
    source_counts = Counter()
    reason_a_counts = Counter()
    reason_b_counts = Counter()
    vectorbt_totals = Counter()
    candidate_events_by_symbol: dict[str, list[dict]] = defaultdict(list)
    event_rows_by_symbol: dict[str, list[dict]] = defaultdict(list)

    for base in runner.to_dict("records"):
        event_id = str(base["event_id"])
        for tp in TP_RS:
            row = dict(base if tp == 0.0 else targets.loc[event_id].to_dict())
            row["arm"] = BASELINE_ARM
            row["decision_ms"] = (_integer(base.get("entry_ms")) + STEP_12H
                                  if _integer(base.get("entry_ms")) is not None else None)
            row["condition_A"] = None
            row["condition_B"] = None
            row["eligibility_reason_A"] = "frozen_baseline"
            row["eligibility_reason_B"] = "frozen_baseline"
            row["entry_status_original"] = base.get("entry_status")
            output_rows.append(row)
        event_rows_by_symbol[str(base["symbol"])].append(base)

    for symbol in sorted(event_rows_by_symbol):
        events = event_rows_by_symbol[symbol]
        paths = {(str(e.get("source_path_12h")), str(e.get("source_path_1h"))) for e in events}
        paths.discard(("", ""))
        if len(paths) != 1:
            raise ValueError(f"Multiple or missing frozen input path pairs for {symbol}: {paths}")
        rel12, rel1 = next(iter(paths))
        p12, p1 = ROOT / rel12, ROOT / rel1
        if not p12.is_file() or not p1.is_file():
            for base in events:
                facts = {
                    "condition_A": False, "condition_B": False,
                    "anchor_entry_ms": _integer(base.get("entry_ms")),
                    "decision_ms": None, "anchor_bar_open_ms": None,
                    "original_exit_bar_ms": _integer(base.get("exit_bar_ms")),
                    "original_exit_phase": base.get("exit_phase"),
                    "original_exit_reason": base.get("exit_reason"),
                    "original_confirmation_close": None, "delayed_12h_close": None,
                    "delayed_12h_source": None, "favorable_close": False,
                    "delayed_open_price": None, "decision_bar_i": None,
                    "reason_A": "missing_frozen_input_bars", "reason_B": "missing_frozen_input_bars",
                }
                event_facts[str(base["event_id"])] = facts
                reason_a_counts[facts["reason_A"]] += 1
                reason_b_counts[facts["reason_B"]] += 1
                for arm in (ARM_A, ARM_AB):
                    output_rows.extend(_blank_for_missing_bars(base, arm, facts))
            continue

        bars12 = engine._load_bars(p12, "12h")
        bars1 = engine._load_bars(p1, "1h")
        q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
        if len(q) == 0:
            for base in events:
                facts = {
                    "condition_A": False, "condition_B": False,
                    "anchor_entry_ms": _integer(base.get("entry_ms")),
                    "decision_ms": None, "anchor_bar_open_ms": None,
                    "original_exit_bar_ms": _integer(base.get("exit_bar_ms")),
                    "original_exit_phase": base.get("exit_phase"),
                    "original_exit_reason": base.get("exit_reason"),
                    "original_confirmation_close": None, "delayed_12h_close": None,
                    "delayed_12h_source": None, "favorable_close": False,
                    "delayed_open_price": None, "decision_bar_i": None,
                    "reason_A": "empty_frozen_input_bars", "reason_B": "empty_frozen_input_bars",
                }
                event_facts[str(base["event_id"])] = facts
                reason_a_counts[facts["reason_A"]] += 1
                reason_b_counts[facts["reason_B"]] += 1
                for arm in (ARM_A, ARM_AB):
                    output_rows.extend(_blank_for_missing_bars(base, arm, facts))
            continue

        hour_map = {int(t): i for i, t in enumerate(bar_ms)}
        for base in events:
            base["_q"] = q
            facts = _eligibility(base, bars12, bar_ms, native_sources)
            event_facts[str(base["event_id"])] = facts
            source_counts[facts.get("delayed_12h_source") or "missing"] += 1
            reason_a_counts[facts["reason_A"]] += 1
            reason_b_counts[facts["reason_B"]] += 1
            if not facts["condition_A"]:
                continue
            idx = facts["decision_bar_i"]
            when = int(facts["decision_ms"])
            if hour_map.get(when) != idx or int(bar_ms[idx]) != when:
                raise AssertionError("Delayed entry is not at its exact observed H1 open")
            candidate_events_by_symbol[symbol].append(
                _candidate_event(base, q, bar_ms, facts))

        if candidate_events_by_symbol[symbol]:
            priced_rows, totals = engine._simulate_symbol(
                candidate_events_by_symbol[symbol], q, bar_ms, TP_RS, batch_size)
            vectorbt_totals.update(totals)
            for row in priced_rows:
                eid, tp = str(row["event_id"]), float(row["tp_r"])
                facts = event_facts[eid]
                row.update(
                    arm=ARM_A, condition_A=True, condition_B=bool(facts["condition_B"]),
                    eligibility_reason_A=facts["reason_A"],
                    eligibility_reason_B=facts["reason_B"],
                    anchor_entry_ms=facts["anchor_entry_ms"], decision_ms=facts["decision_ms"],
                    anchor_bar_open_ms=facts["anchor_bar_open_ms"],
                    original_exit_bar_ms=facts["original_exit_bar_ms"],
                    original_exit_phase=facts["original_exit_phase"],
                    original_exit_reason=facts["original_exit_reason"],
                    original_confirmation_close=facts["original_confirmation_close"],
                    delayed_12h_close=facts["delayed_12h_close"],
                    delayed_12h_source=facts["delayed_12h_source"],
                    favorable_close=bool(facts["favorable_close"]),
                    entry_status_original="executed",
                )
                row["entry_order"] = _number(signals.loc[int(row["source_row_number"]),
                                                          "prior_atr_pct"])
                if row["entry_order"] is None:
                    row["entry_order"] = np.inf
                priced_event_rows[eid][tp] = row

    # Explicit mode rows retain every source event, including unmatched controls.
    for base in runner.to_dict("records"):
        eid = str(base["event_id"])
        facts = event_facts.get(eid)
        if facts is None:
            facts = {
                "condition_A": False, "condition_B": False,
                "anchor_entry_ms": _integer(base.get("entry_ms")), "decision_ms": None,
                "anchor_bar_open_ms": None, "original_exit_bar_ms": _integer(base.get("exit_bar_ms")),
                "original_exit_phase": base.get("exit_phase"), "original_exit_reason": base.get("exit_reason"),
                "original_confirmation_close": None, "delayed_12h_close": None,
                "delayed_12h_source": None, "favorable_close": False,
                "delayed_open_price": None, "decision_bar_i": None,
                "reason_A": "missing_frozen_input_bars", "reason_B": "missing_frozen_input_bars",
            }
        for tp in TP_RS:
            row_a = priced_event_rows.get(eid, {}).get(tp)
            if row_a is None:
                why = facts["reason_A"]
                output_rows.append(_blank_candidate(base, ARM_A, tp, facts, why))
            else:
                output_rows.append(row_a)
            row_ab = priced_event_rows.get(eid, {}).get(tp) if facts["condition_B"] else None
            if row_ab is None:
                why = facts["reason_A"] if not facts["condition_A"] else facts["reason_B"]
                output_rows.append(_blank_candidate(base, ARM_AB, tp, facts, why))
            else:
                copy = dict(row_ab)
                copy["arm"] = ARM_AB
                output_rows.append(copy)

    paths_out = _sort_output(pd.DataFrame(output_rows))
    if paths_out.duplicated(["arm", "event_id", "tp_r"]).any():
        raise AssertionError("Output arm/event_id/tp_r key is not unique")
    if not paths_out.groupby(["arm", "event_id"]).tp_r.apply(
            lambda x: set(x.astype(float)) == set(TP_RS)).all():
        raise AssertionError("Every arm/event must have explicit no-TP and 2R rows")

    # Attach the frozen low-ATR priority to every arm without altering its
    # original timestamp ordering or allowing a control to borrow future data.
    paths_out["entry_order"] = pd.to_numeric(paths_out.source_row_number, errors="coerce").map(
        signals.prior_atr_pct).astype(float).fillna(np.inf)

    slots = int(config["account"]["slots"])
    fraction = float(config["account"]["partial_2r_fraction"])
    base_metrics, base_checks = _baseline_metrics()
    account_metrics: dict[str, dict] = {
        BASELINE_ARM: {"actual": base_metrics, "source": str(
            (PRIORITY_EXP / "account_metrics.csv").relative_to(ROOT))}
    }
    account_check_metrics: dict[str, dict] = {BASELINE_ARM: {}}

    baseline_exec = paths_out[(paths_out.arm == BASELINE_ARM)
                              & paths_out.entry_status.eq("executed")]
    delayed_frames: dict[str, pd.DataFrame] = {}
    for arm in (ARM_A, ARM_AB):
        delay = paths_out[paths_out.arm.eq(arm)].copy()
        delayed_frames[arm] = delay
        account_metrics[arm] = {}
        for cohort, mask in (("actual", ~delay.is_control), ("control", delay.is_control)):
            pool = _apply_priority(delay[mask], signals)
            if pool.entry_status.eq("executed").any():
                metrics, funded, equity = _run_capacity(pool, slots, fraction)
                account_metrics[arm][cohort] = metrics
                if cohort == "actual":
                    funded.to_csv(output_paths["funded"].with_name(
                        f"funded_{arm}.csv.gz"), index=False, compression="gzip")
                    equity.to_csv(output_paths["equity"].with_name(
                        f"hourly_{arm}.csv.gz"), index_label="hour_end_utc")
            else:
                account_metrics[arm][cohort] = {}
            gc.collect()

    # Pair strict subsets independently: a signal/control parent is included
    # only when both events pass the same arm's own wait and entry conditions.
    checks: dict[str, dict] = {}
    for arm in ARMS:
        arm_frame = paths_out[paths_out.arm.eq(arm)]
        signal_ids = set(arm_frame[(~arm_frame.is_control)
                                   & arm_frame.entry_status.eq("executed")].parent_id.astype(str))
        control_ids = set(arm_frame[arm_frame.is_control & arm_frame.entry_status.eq("executed")]
                          .parent_id.astype(str))
        paired_ids = signal_ids & control_ids
        checks[arm] = {
            "paired_parent_count": len(paired_ids),
            "actual_paired": {}, "control_paired": {}, "control_all": {},
            "slippage_5bp_actual": {},
        }
        for cohort, pool in (
                ("actual_paired", arm_frame[(~arm_frame.is_control)
                                            & arm_frame.parent_id.astype(str).isin(paired_ids)]),
                ("control_paired", arm_frame[arm_frame.is_control
                                             & arm_frame.parent_id.astype(str).isin(paired_ids)]),
                ("control_all", arm_frame[arm_frame.is_control]),
                ("slippage_5bp_actual", arm_frame[~arm_frame.is_control])):
            runnable = _apply_priority(pool, signals)
            runnable = runnable[runnable.entry_status.eq("executed")]
            if runnable.empty:
                result = {}
            else:
                slip = .0005 if cohort == "slippage_5bp_actual" else 0.0
                result, _, _ = _run_capacity(runnable, slots, fraction, slip)
            checks[arm][cohort] = result
            gc.collect()

    # The original account is already frozen and was not repriced. Only use
    # existing baseline metrics for the full signal cohort; matched subsets
    # are account-only projections of those saved VectorBT paths.
    baseline_ids_actual = set(baseline_exec[~baseline_exec.is_control].parent_id.astype(str))
    baseline_ids_control = set(baseline_exec[baseline_exec.is_control].parent_id.astype(str))
    baseline_pair_ids = baseline_ids_actual & baseline_ids_control
    checks[BASELINE_ARM] = {
        "paired_parent_count": len(baseline_pair_ids),
        "actual_paired": {}, "control_paired": {}, "control_all": {},
        "slippage_5bp_actual": base_checks.get("slippage_5bp", {}),
    }
    for cohort, mask in (
            ("actual_paired", (~baseline_exec.is_control)
             & baseline_exec.parent_id.astype(str).isin(baseline_pair_ids)),
            ("control_paired", baseline_exec.is_control
             & baseline_exec.parent_id.astype(str).isin(baseline_pair_ids)),
            ("control_all", baseline_exec.is_control)):
        runnable = _apply_priority(baseline_exec[mask], signals)
        if runnable.empty:
            result = {}
        else:
            result, _, _ = _run_capacity(runnable, slots, fraction)
        checks[BASELINE_ARM][cohort] = result
        gc.collect()

    dev_period = str(config["selection"]["development_period"])
    gates = config["selection"]["gates"]
    selection_rows = {}
    for arm in ARMS:
        metrics = (account_metrics[arm]["actual"] if arm != BASELINE_ARM
                   else account_metrics[BASELINE_ARM]["actual"])
        row = metrics.get(dev_period, {})
        selection_rows[arm] = row
    eligible = [arm for arm in ARMS if _metric_eligible(selection_rows[arm], gates)]
    if eligible:
        pick = max(eligible, key=lambda arm: float(selection_rows[arm]["multiple"]))
    else:
        pick = config["selection"]["fallback_arm"]
    best_dev = max(ARMS, key=lambda arm: float(selection_rows[arm].get("multiple", -math.inf)))

    paths_out = paths_out.drop(columns=["entry_order"], errors="ignore")
    paths_out.to_csv(output_paths["paths"], index=False, compression="gzip", float_format="%.12g")
    account_frame_rows = []
    for arm in ARMS:
        if arm == BASELINE_ARM:
            source_metrics = account_metrics[arm]["actual"]
        else:
            source_metrics = account_metrics[arm].get("actual", {})
        for period, metrics in source_metrics.items():
            account_frame_rows.append({"arm": arm, "cohort": "actual", "period": period,
                                       **metrics})
        for cohort in ("actual_paired", "control_paired", "control_all", "slippage_5bp_actual"):
            source_metrics = checks[arm].get(cohort, {})
            for period, metrics in source_metrics.items():
                account_frame_rows.append({"arm": arm, "cohort": cohort, "period": period,
                                           **metrics})
    account_frame = pd.DataFrame(account_frame_rows)
    account_frame.to_csv(output_paths["account_metrics"], index=False)

    counts = {}
    for arm in ARMS:
        subset = paths_out[paths_out.arm.eq(arm)]
        counts[arm] = {
            "event_count": int(subset.event_id.nunique()),
            "path_rows": len(subset),
            "executed_events": int(subset[subset.entry_status.eq("executed")].event_id.nunique()),
            "actual_executed_events": int(subset[(~subset.is_control)
                                                  & subset.entry_status.eq("executed")].event_id.nunique()),
            "control_executed_events": int(subset[subset.is_control
                                                   & subset.entry_status.eq("executed")].event_id.nunique()),
            "status_counts": subset.groupby("entry_status", dropna=False).event_id.nunique().to_dict(),
        }
    audit = {
        "experiment_id": config["id"], "generator": str(Path(__file__).relative_to(ROOT)),
        "vectorbt_version": vbt.__version__,
        "inputs": {"parent_paths": str(source_path.relative_to(ROOT)),
                   "signals": str(signal_path.relative_to(ROOT)),
                   "parent_rows_tp0_tp2": len(frozen),
                   "event_count": int(runner.event_id.nunique())},
        "decision_clock": {
            "anchor": "each signal/control's own executed 12H confirmation and exact coincident H1 open",
            "decision": "anchor + 12 hours, after the 12H candle whose open is anchor has completed",
            "required_h1_opens": 13,
            "meaning": "12 contiguous H1 candles during waiting plus exact H1 open at decision",
            "exit_clock_gate": "frozen tp_r=0 VectorBT exit timestamp and phase are used only to determine whether a protective stop/EMA exit was observable by decision; earlier exits reject; equality rejects only phase=open",
        },
        "eligibility": {
            "A": "complete contiguous H1 wait, exact native 12H candle, exact decision H1 open, no original stop or EMA exit known by that open, and delayed open remains on the safe side of the inherited absolute stop",
            "B": "A and side * (completed next 12H close - event's own original confirmation close) > 0",
            "native_12h_source_counts": dict(source_counts),
            "A_reason_counts": dict(reason_a_counts), "B_reason_counts": dict(reason_b_counts),
            "same_decision_timestamp_open_precedence": "stop/EMA exit at decision timestamp is known only when exit_phase=open; intrabar/close at equal exit_bar_ms occur after the open and do not reject entry",
        },
        "pricing": {
            "baseline": "frozen parent V1.7 no-TP and 2R paths; not repriced",
            "delayed": "existing VectorBT Portfolio.from_order_func callback through cloud_close_reward_paths._simulate_symbol, tp_r 0 and 2 in one run per A-eligible event",
            "A_plus_B": "copies the same already-priced A rows for events passing B; no additional fills or simulator",
            "fixed_stop": "original absolute touched-extension stop retained for signal; matched control retains its parent-derived absolute stop",
            "new_risk": "abs(delayed executable H1 open - retained fixed stop); 2R target derived from delayed entry and new risk",
            "fees_per_fill": {"taker": TAKER, "resting_maker": engine.MAKER},
        },
        "account": {"slots": slots, "partial_2r_fraction": fraction,
                    "priority": "low_prior_atr, inherited from parent source row for matched signals and controls",
                    "control_pairing": "paired actual/control account includes only parent_id values whose signal and matched control both pass the same arm's eligibility and execute",
                    "baseline_metric_source": str((PRIORITY_EXP / "account_metrics.csv").relative_to(ROOT)),
                    "all_account_paths": "existing cloud_close_slots.run and VectorBT capacity runner"},
        "selection": {"development_period": dev_period, "gates": gates,
                      "eligible_arms": eligible, "pick": pick, "best_dev_multiple": best_dev,
                      "fallback_arm": config["selection"]["fallback_arm"],
                      "metrics": selection_rows},
        "counts": counts, "checks": checks,
        "vectorbt_execution_totals": dict(vectorbt_totals),
        "outputs": {k: str(v.relative_to(ROOT)) for k, v in output_paths.items()},
        "path_schema": list(paths_out.columns),
    }
    _write_json(output_paths["audit"], audit)
    _write_json(output_paths["summary"], {
        "experiment_id": config["id"], "pick": pick, "best_dev_multiple": best_dev,
        "eligible_arms": eligible, "metrics": account_metrics, "checks": checks,
        "counts": counts,
    })
    return audit


def _self_check() -> None:
    decision = 12 * HOUR_MS
    assert _prior_exit_precedes_decision(decision - HOUR_MS, "intrabar", decision, "stop")
    assert _prior_exit_precedes_decision(decision, "open", decision, "ema_flip_open")
    assert not _prior_exit_precedes_decision(decision, "intrabar", decision, "stop")
    assert not _prior_exit_precedes_decision(decision, "close", decision, "ema_flip")
    assert not _prior_exit_precedes_decision(decision + HOUR_MS, "open", decision, "stop")
    assert not _prior_exit_precedes_decision(decision, "open", decision, "take_profit")

    clock = np.arange(13, dtype=np.int64) * HOUR_MS
    loc, reason = _exact_window_indices(0, clock)
    assert reason == "eligible" and np.array_equal(loc, np.arange(13))
    gap_clock = np.delete(clock, 6)
    loc, reason = _exact_window_indices(0, gap_clock)
    assert loc is None and reason == "no_complete_contiguous_12h_h1_window_or_decision_open"

    common = {"event_id": "fixture:signal", "id": "fixture:signal", "parent_id": "fixture",
              "source_row_number": 1, "symbol": "TEST", "week": "2026-01-05", "side": 1,
              "is_control": False, "entry_i": 0, "entry_ms": 0, "entry_price": 100.,
              "stop_price": 95., "risk_distance": 5., "risk_fraction": .05,
              "end_i": 0, "entry_status": "pending", "path_upper_bound_reason": "fixture"}
    # On the entry H1 bar, the VectorBT callback sees stop and TP at the same
    # time; adverse stop priority wins and the opposite-side TP touch is marked.
    q = np.array([[100., 111., 94., 100., 50., 0., 0.]], dtype=np.float64)
    rows, _ = engine._simulate_symbol([dict(common)], q, np.array([0], dtype=np.int64),
                                      TP_RS, 1)
    tp2 = next(row for row in rows if row["tp_r"] == 2.0)
    assert tp2["exit_reason"] == "stop" and tp2["same_bar_ambiguous"]
    assert tp2["ambiguous_triggers"] == "stop+take_profit"
    assert tp2["exit_phase"] == "intrabar"

    # A true hourly gap is marked out at the next observed open before the
    # current bar's stop/TP OHLC, using the frozen engine's gap contract.
    qgap = np.array([[100., 101., 99., 100., 50., 0., 0.],
                     [90., 91., 89., 90., 50., 0., 0.]], dtype=np.float64)
    rows, _ = engine._simulate_symbol([dict(common, end_i=1)], qgap,
                                      np.array([0, 2*HOUR_MS], dtype=np.int64), TP_RS, 1)
    for row in rows:
        assert row["exit_reason"] == "data_gap_markout"
        assert row["exit_phase"] == "open" and row["gap_flag"]
        assert row["exit_price"] == 90.0


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=EXP / "config.json")
    parser.add_argument("--self-check", action="store_true",
                        help="Run synthetic clock and VectorBT execution fixtures only")
    args = parser.parse_args(argv)
    if args.self_check:
        _self_check()
        print("self-check: passed (clock equality, contiguous wait, same-bar adverse, gap open)")
        return
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    audit = generate(config_path)
    print(json.dumps({"experiment_id": audit["experiment_id"],
                      "counts": audit["counts"], "selection": audit["selection"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
