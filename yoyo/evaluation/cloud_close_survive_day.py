"""Test an original V1.7 full runner entered only after 24h survival.

Candidate exits reuse the frozen native V1.7 runner's exit time, price, phase,
and fee class. Only the decision-time H1 opening entry changes. The shared
cash account and sizing are recomputed with the existing VectorBT wallet.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_leadership as leadership
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-survive-day-20261109-v1"
HOUR_MS = engine.HOUR_MS
STEP_12H = engine.STEP_12H
WAIT_MS = 24 * HOUR_MS
TAKER = engine.TAKER
BASELINE_ARM = "original_v1_7_full_runner"
DELAYED_ARM = "survive_24h_then_enter"
NATIVE_12H_SOURCES = {"native12Hutc", "OKX_history_12Hutc", "shared_OKX_cache_12Hutc"}
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00:00Z"),
    "2025": ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "2026": ("2026-01-01T00:00:00Z", None),
}
PATH_FIELDS = [
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
]
FROZEN_LEDGER_FIELDS = [
    "event_id", "parent_id", "source_row_number", "symbol", "side", "tp_r", "is_control",
    "entry_ms", "exit_bar_ms", "entry_price", "exit_price", "stop_price", "exit_fee_rate",
    "exit_reason", "exit_phase", "entry_status", "cash_pnl", "notional", "net_return",
]


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if value is None or pd.isna(value):
        return None
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_safe(value), ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _integer(value) -> int | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return int(float(value))


def _number(value) -> float | None:
    if value is None or pd.isna(value) or value == "":
        return None
    return float(value)


def _bool_series(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def _exact_h1_window(anchor_ms: int, bar_ms: np.ndarray) -> tuple[np.ndarray | None, str]:
    """Require 24 complete H1 waits plus the exact decision-open observation."""
    expected = int(anchor_ms) + np.arange(25, dtype=np.int64) * HOUR_MS
    locations = np.searchsorted(bar_ms, expected, side="left")
    if np.any(locations >= len(bar_ms)) or not np.array_equal(bar_ms[locations], expected):
        return None, "missing_one_or_more_of_24_wait_hours_or_decision_open"
    return locations.astype(np.int64), "eligible"


def _event_anchor(event: dict) -> tuple[int | None, int | None, str]:
    """Get the event's own V1.7 confirmation and the 12H bar that confirmed it."""
    if bool(event.get("is_control")):
        anchor = _integer(event.get("control_confirmation_ms"))
        key_open = _integer(event.get("control_12h_open_ms"))
        if anchor is None or key_open is None or key_open + STEP_12H != anchor:
            return anchor, key_open, "invalid_control_confirmation_clock"
    else:
        anchor = _integer(event.get("confirmation_ms"))
        key_open = _integer(event.get("key_open_ms"))
        if anchor is None or key_open is None or key_open + STEP_12H != anchor:
            return anchor, key_open, "invalid_signal_confirmation_clock"
    if _integer(event.get("entry_ms")) != anchor:
        return anchor, key_open, "original_entry_not_at_own_confirmation"
    return anchor, key_open, "eligible"


def _exit_before_decision(exit_ms, exit_phase: str | None, decision_ms: int) -> bool:
    """Only a strictly earlier exit or equal-time open exit precedes the fill."""
    if exit_ms is None or pd.isna(exit_ms):
        return True
    clock = int(exit_ms)
    return clock < int(decision_ms) or (clock == int(decision_ms) and exit_phase == "open")


def _load_events(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load the independently corrected original 90d cohort and native paths."""
    inputs = config["inputs"]
    path_file = ROOT / inputs["parent_paths"]
    screen_file = ROOT / inputs["corrected_90d_event_screen"]
    signal_file = ROOT / inputs["signals"]
    for path in (path_file, screen_file, signal_file):
        if not path.is_file():
            raise FileNotFoundError(path)

    screen = pd.read_csv(screen_file,
                         usecols=["event_id", "parent_id", "symbol", "side", "entry_ms",
                                  "is_control", "eligible_90d"],
                         dtype={"event_id": str, "parent_id": str}, low_memory=False)
    if screen.event_id.duplicated().any():
        raise ValueError("Corrected event screen has duplicate IDs")
    valid = _bool_series(screen.eligible_90d)
    real_screen = screen[valid & ~_bool_series(screen.is_control)].copy()
    actual_parents = set(real_screen.parent_id.astype(str))
    control_screen = screen[valid & _bool_series(screen.is_control)
                            & screen.parent_id.astype(str).isin(actual_parents)].copy()
    expected_real = int(config["eligibility"]["expected_original_eligible_actual_events"])
    expected_control = int(config["eligibility"]["expected_original_eligible_paired_controls"])
    if (len(real_screen), len(control_screen)) != (expected_real, expected_control):
        raise ValueError(f"Corrected 90d cohort changed: {(len(real_screen), len(control_screen))}")
    allowed_ids = set(real_screen.event_id.astype(str)) | set(control_screen.event_id.astype(str))

    parent_columns = set(pd.read_csv(path_file, nrows=0).columns)
    missing = sorted(set(PATH_FIELDS) - parent_columns)
    if missing:
        raise ValueError(f"Frozen V1.7 paths lack columns: {missing}")
    paths = pd.read_csv(path_file, usecols=PATH_FIELDS,
                        dtype={"event_id": str, "id": str, "parent_id": str, "symbol": str},
                        low_memory=False, float_precision="round_trip")
    runner = paths[pd.to_numeric(paths.tp_r, errors="coerce").eq(0.0)
                   & paths.event_id.astype(str).isin(allowed_ids)].copy()
    if runner.event_id.duplicated().any() or set(runner.event_id.astype(str)) != allowed_ids:
        raise ValueError("Corrected 90d event IDs do not match frozen no-TP paths")
    if not runner.entry_status.eq("executed").all():
        raise ValueError("Corrected event screen contains a nonexecuted frozen path")

    signal_features = pd.read_csv(signal_file, usecols=["prior_atr_pct"], low_memory=False,
                                  float_precision="round_trip")
    signal_features.index = pd.RangeIndex(1, len(signal_features) + 1)
    source_rows = pd.to_numeric(runner.source_row_number, errors="coerce").astype("Int64")
    runner["entry_order"] = source_rows.map(signal_features.prior_atr_pct).astype(float).fillna(np.inf)

    # Keep corrected event identity and original timing tied to the screen.
    screen_index = screen.set_index("event_id")
    checked = screen_index.loc[runner.event_id]
    if not np.array_equal(checked.parent_id.astype(str).to_numpy(), runner.parent_id.astype(str).to_numpy()):
        raise ValueError("Parent IDs differ between corrected screen and frozen paths")
    for event_col in ("entry_ms", "symbol", "side"):
        if event_col not in checked:
            continue
        if event_col == "entry_ms":
            left = pd.to_numeric(runner[event_col], errors="coerce").to_numpy(dtype=float)
            right = pd.to_numeric(checked[event_col], errors="coerce").to_numpy(dtype=float)
            if not np.array_equal(left, right):
                raise ValueError("Original entry clock differs from corrected screen")
        elif event_col in runner and not np.array_equal(
                runner[event_col].astype(str).to_numpy(), checked[event_col].astype(str).to_numpy()):
            raise ValueError(f"Frozen event identity mismatch in {event_col}")

    runner = runner.sort_values(["entry_ms", "entry_order", "symbol", "event_id"],
                                kind="stable").reset_index(drop=True)
    return runner, signal_features, {
        "corrected_original_actual_events": len(real_screen),
        "corrected_original_paired_controls": len(control_screen),
        "excluded_original_ineligible_events": int((~valid).sum()),
        "source_screen": str(screen_file.relative_to(ROOT)),
    }


def _frozen_baseline(config: dict, runner: pd.DataFrame) -> tuple[dict, dict]:
    """Summarize existing full-runner funded ledgers without replaying the account."""
    root = ROOT / config["inputs"]["frozen_full_runner_dir"]
    metrics, audits = {}, {}
    paths = runner.set_index("event_id", drop=False)
    for cohort in ("actual", "control"):
        funded_path = root / f"funded_OKX_{cohort}_shadow_full_runner.csv.gz"
        equity_path = root / f"hourly_OKX_{cohort}_shadow_full_runner.csv.gz"
        if not funded_path.is_file() or not equity_path.is_file():
            raise FileNotFoundError(f"Missing frozen {cohort} full-runner ledger or hourly marks")
        funded = pd.read_csv(funded_path, usecols=FROZEN_LEDGER_FIELDS,
                             dtype={"event_id": str, "parent_id": str, "symbol": str},
                             low_memory=False, float_precision="round_trip")
        equity = pd.read_csv(equity_path, index_col=0, parse_dates=True)
        equity.index = pd.to_datetime(equity.index, utc=True)
        if funded.event_id.duplicated().any() or not funded.entry_status.eq("executed").all():
            raise ValueError(f"Frozen {cohort} ledger has duplicate/nonexecuted rows")
        is_control = _bool_series(funded.is_control)
        if not funded.tp_r.eq(0).all() or not is_control.eq(cohort == "control").all():
            raise ValueError(f"Frozen {cohort} ledger is not the expected full-runner cohort")
        expected_key = f"frozen_full_runner_{cohort}_funded_events"
        if len(funded) != int(config["inputs"][expected_key]):
            raise ValueError(f"Frozen {cohort} ledger roster changed: {len(funded)}")
        if (funded.event_id.astype(str).isin(paths.index.astype(str))).sum() != len(funded):
            raise ValueError(f"Frozen {cohort} ledger has event IDs outside the corrected source cohort")
        source = paths.loc[funded.event_id.astype(str)].reset_index(drop=True)
        exact_cols = ("parent_id", "symbol", "side", "entry_ms", "exit_bar_ms", "entry_price",
                      "exit_price", "stop_price", "exit_fee_rate", "exit_reason", "exit_phase")
        for column in exact_cols:
            if column in {"symbol", "parent_id", "exit_reason", "exit_phase"}:
                same = np.array_equal(source[column].astype(str).to_numpy(),
                                      funded[column].astype(str).to_numpy())
            elif column in {"entry_ms", "exit_bar_ms", "side"}:
                same = np.array_equal(pd.to_numeric(source[column]).to_numpy(dtype="int64"),
                                      pd.to_numeric(funded[column]).to_numpy(dtype="int64"))
            else:
                same = np.allclose(pd.to_numeric(source[column]).to_numpy(dtype=float),
                                   pd.to_numeric(funded[column]).to_numpy(dtype=float),
                                   rtol=1e-10, atol=1e-12)
            if not same:
                raise ValueError(f"Frozen {cohort} runner differs from V1.7 source paths: {column}")
        cash = 1000.0 + float(funded.cash_pnl.sum())
        if not np.isclose(cash, float(equity.nav.iloc[-1]), rtol=1e-9, atol=0.01):
            raise ValueError(f"Frozen {cohort} cash/mark reconciliation failed")
        metrics[cohort] = {period: wallet.summarize(funded, equity, *limits)
                           for period, limits in PERIODS.items()}
        audits[cohort] = {"funded_positions": len(funded), "final_cash_usdt": cash,
                          "final_nav_usdt": float(equity.nav.iloc[-1]),
                          "cash_reconciliation_usdt": cash-float(equity.nav.iloc[-1]),
                          "funded_path_source": str(funded_path.relative_to(ROOT)),
                          "hourly_path_source": str(equity_path.relative_to(ROOT))}
    return metrics, audits


def _symbol_inputs(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    paths = {(str(row.source_path_12h), str(row.source_path_1h))
             for row in events.itertuples(index=False)}
    paths.discard(("", ""))
    if len(paths) != 1:
        raise ValueError(f"Each symbol must have one frozen input pair: {paths}")
    relative12, relative1 = next(iter(paths))
    path12, path1 = ROOT / relative12, ROOT / relative1
    if not path12.is_file() or not path1.is_file():
        raise FileNotFoundError(f"Missing frozen native bars: {path12}, {path1}")
    bars12 = engine._load_bars(path12, "12h")
    bars1 = engine._load_bars(path1, "1h")
    return bars12, bars1, bars1.index.to_numpy(dtype=np.int64)


def _decision_facts(event: dict, bars12: pd.DataFrame, bars1: pd.DataFrame,
                    bar_ms: np.ndarray, rank_keys: set[tuple[int, str]],
                    native_sources: set[str]) -> dict:
    """Apply exact 24h survival, native-bar, stop-safety, and point-time gates."""
    facts = {
        "eligible": False, "reason": "eligible",
        "original_entry_ms": _integer(event.get("entry_ms")), "decision_ms": None,
        "decision_bar_i": None, "decision_open": None,
        "first_wait_12h_close": None, "second_wait_12h_close": None,
        "first_wait_12h_source": None, "second_wait_12h_source": None,
        "survives_original_exit_clock": False,
        "original_exit_bar_ms": _integer(event.get("exit_bar_ms")),
        "original_exit_phase": event.get("exit_phase"),
        "original_exit_reason": event.get("exit_reason"),
        "delayed_eligible_90d": False,
    }
    if event.get("entry_status") != "executed" or facts["original_entry_ms"] is None:
        facts["reason"] = "original_signal_not_executable"
        return facts

    anchor, _, clock_reason = _event_anchor(event)
    if clock_reason != "eligible" or anchor != facts["original_entry_ms"]:
        facts["reason"] = clock_reason
        return facts
    if anchor % STEP_12H:
        facts["reason"] = "unaligned_original_12h_confirmation"
        return facts
    decision = int(anchor + WAIT_MS)
    facts["decision_ms"] = decision

    locations, wait_reason = _exact_h1_window(anchor, bar_ms)
    if locations is None:
        facts["reason"] = wait_reason
        return facts
    decision_i = int(locations[-1])
    facts["decision_bar_i"] = decision_i

    first_open, second_open = anchor, anchor + STEP_12H
    if first_open not in bars12.index or second_open not in bars12.index:
        facts["reason"] = "missing_one_or_more_of_two_native_12h_closes"
        return facts
    first, second = bars12.loc[first_open], bars12.loc[second_open]
    source1, source2 = str(first.get("source", "")), str(second.get("source", ""))
    facts.update(first_wait_12h_close=float(first.close),
                 second_wait_12h_close=float(second.close),
                 first_wait_12h_source=source1,
                 second_wait_12h_source=source2)
    if source1 not in native_sources or source2 not in native_sources:
        facts["reason"] = "one_or_more_wait_12h_bars_not_native"
        return facts

    if _exit_before_decision(event.get("exit_bar_ms"), event.get("exit_phase"), decision):
        facts["reason"] = "original_runner_exited_before_delayed_entry"
        return facts
    facts["survives_original_exit_clock"] = True

    symbol = str(event["symbol"])
    if (decision, symbol) not in rank_keys:
        facts["reason"] = "no_point_in_time_90d_rank_or_market_members_below_10"
        return facts
    facts["delayed_eligible_90d"] = True

    decision_open = float(bars1.open.iloc[decision_i])
    stop = _number(event.get("stop_price"))
    side = int(event["side"])
    if stop is None or side not in (-1, 1):
        facts["reason"] = "missing_fixed_stop_or_side"
        return facts
    if not np.isfinite(decision_open) or decision_open <= 0:
        facts["reason"] = "invalid_delayed_entry_open"
        return facts
    if side * (decision_open - stop) <= 0:
        facts["reason"] = "delayed_open_at_or_beyond_original_fixed_stop"
        return facts

    facts["decision_open"] = decision_open
    facts["eligible"] = True
    return facts


def _delayed_row(event: dict, facts: dict) -> dict:
    """Change the executable entry while preserving the frozen native exit."""
    row = dict(event)
    row.update(
        arm=DELAYED_ARM,
        tp_r=0.0,
        original_entry_ms=facts["original_entry_ms"],
        original_entry_price=_number(event.get("entry_price")),
        original_exit_bar_ms=facts["original_exit_bar_ms"],
        original_exit_phase=facts["original_exit_phase"],
        original_exit_reason=facts["original_exit_reason"],
        decision_ms=facts["decision_ms"],
        decision_open=facts["decision_open"],
        first_wait_12h_close=facts["first_wait_12h_close"],
        second_wait_12h_close=facts["second_wait_12h_close"],
        first_wait_12h_source=facts["first_wait_12h_source"],
        second_wait_12h_source=facts["second_wait_12h_source"],
        survives_original_exit_clock=facts["survives_original_exit_clock"],
        delayed_eligible_90d=facts["delayed_eligible_90d"],
        delay_status="entered",
        entry_ms=int(facts["decision_ms"]),
        entry_time_utc=pd.Timestamp(facts["decision_ms"], unit="ms", tz="UTC").isoformat(),
        entry_price=float(facts["decision_open"]),
        entry_status="executed",
        status="executed",
        entry_fee_class="taker",
        entry_fee_rate=TAKER,
        entry_fee_amount=float(facts["decision_open"]) * TAKER,
        target_price=None,
    )
    stop = float(row["stop_price"])
    entry, exit_price, side = float(row["entry_price"]), float(row["exit_price"]), int(row["side"])
    risk = abs(entry - stop)
    exit_fee_rate = float(row["exit_fee_rate"])
    gross = side * (exit_price - entry)
    exit_fee = exit_price * exit_fee_rate
    net = gross - float(row["entry_fee_amount"]) - exit_fee
    row.update(
        risk_distance=risk,
        risk_fraction=risk / entry if entry > 0 else None,
        exit_fee_amount=exit_fee,
        gross_pnl=gross,
        net_pnl=net,
        gross_r=gross / risk if risk > 0 else None,
        net_r=net / risk if risk > 0 else None,
        entry_order=_number(event.get("entry_order")),
    )
    if int(row["exit_bar_ms"]) < int(facts["decision_ms"]):
        raise AssertionError("Delayed candidate retained a frozen exit before its entry")
    if int(row["exit_bar_ms"]) == int(facts["decision_ms"]) and row["exit_phase"] == "open":
        raise AssertionError("Open-phase frozen exit cannot follow the delayed entry")
    return row


def _blank_delayed(event: dict, facts: dict) -> dict:
    row = dict(event)
    row.update(
        arm=DELAYED_ARM,
        delay_status=facts["reason"],
        original_entry_ms=facts["original_entry_ms"],
        original_entry_price=_number(event.get("entry_price")),
        original_exit_bar_ms=facts["original_exit_bar_ms"],
        original_exit_phase=facts["original_exit_phase"],
        original_exit_reason=facts["original_exit_reason"],
        decision_ms=facts["decision_ms"],
        decision_open=facts["decision_open"],
        first_wait_12h_close=facts["first_wait_12h_close"],
        second_wait_12h_close=facts["second_wait_12h_close"],
        first_wait_12h_source=facts["first_wait_12h_source"],
        second_wait_12h_source=facts["second_wait_12h_source"],
        survives_original_exit_clock=facts["survives_original_exit_clock"],
        delayed_eligible_90d=facts["delayed_eligible_90d"],
        entry_ms=None, entry_price=None, exit_bar_ms=None, exit_price=None,
        entry_status=facts["reason"], status=facts["reason"], exit_reason=facts["reason"],
        entry_order=_number(event.get("entry_order")),
    )
    return row


def _materialize(runner: pd.DataFrame, rank_keys: set[tuple[int, str]],
                 native_sources: set[str]) -> tuple[pd.DataFrame, dict]:
    rows, reasons, first_sources, second_sources = [], Counter(), Counter(), Counter()
    for _, group in runner.groupby("symbol", sort=True):
        bars12, bars1, bar_ms = _symbol_inputs(group)
        for event in group.to_dict("records"):
            facts = _decision_facts(event, bars12, bars1, bar_ms, rank_keys, native_sources)
            reasons[facts["reason"]] += 1
            first_sources[facts["first_wait_12h_source"] or "missing"] += 1
            second_sources[facts["second_wait_12h_source"] or "missing"] += 1
            rows.append(_delayed_row(event, facts) if facts["eligible"]
                        else _blank_delayed(event, facts))
        gc.collect()
    paths = pd.DataFrame(rows)
    if len(paths) != len(runner) or paths.event_id.duplicated().any():
        raise AssertionError("Delayed event coverage must be one row per corrected source event")
    counts = {
        "original_eligible_events": int(len(runner)),
        "delayed_entries": int(paths.entry_status.eq("executed").sum()),
        "delayed_actual_entries": int((~paths.is_control & paths.entry_status.eq("executed")).sum()),
        "delayed_control_entries": int((paths.is_control & paths.entry_status.eq("executed")).sum()),
        "status_counts": paths.groupby("entry_status", dropna=False).event_id.nunique().to_dict(),
    }
    return paths, {"counts": counts, "reason_counts": dict(reasons),
                   "first_12h_source_counts": dict(first_sources),
                   "second_12h_source_counts": dict(second_sources)}


def _run_account(events: pd.DataFrame, slots: int, slip: float = 0.0):
    executable = events[events.entry_status.eq("executed")].copy()
    if executable.empty or executable.event_id.duplicated().any():
        raise ValueError("Wallet requires unique executable full-runner paths")
    metrics, meta, funded, equity = wallet.evaluate(executable, slots, slip=slip)
    cash = 1000.0 + float(funded.cash_pnl.sum())
    if not np.isclose(cash, float(equity.nav.iloc[-1]), rtol=1e-9, atol=0.01):
        raise ValueError(f"Wallet cash reconciliation failed: ledger={cash} NAV={equity.nav.iloc[-1]}")
    return metrics, meta, funded, equity


def _passes_dev(metrics: dict, gates: dict) -> bool:
    return (
        int(metrics.get("trades") or 0) >= int(gates["minimum_trades"])
        and metrics.get("win_pct") is not None
        and metrics["win_pct"] >= float(gates["minimum_win_pct"])
        and (metrics.get("payoff") or 0.0) >= float(gates["minimum_equal_notional_payoff"])
        and (metrics.get("payoff_usdt") or 0.0) >= float(gates["minimum_funded_usdt_payoff"])
        and (metrics.get("dd_bound_pct") or math.inf) <= float(gates["maximum_conservative_dd_pct"])
    )


def _metric_rows(arm: str, cohort: str, metrics: dict) -> list[dict]:
    return [dict(arm=arm, cohort=cohort, period=period, **values)
            for period, values in metrics.items()]


def generate(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if int(config["wait"]["hours"]) != 24 or int(config["account"]["slots"]) != 9:
        raise ValueError("Study is frozen to a 24h wait and nine slots")
    if not math.isclose(float(config["fees"]["market_per_fill"]), TAKER):
        raise ValueError("Market fee diverges from the V1.7 path engine")
    outputs = {key: ROOT / value for key, value in config["outputs"].items()}
    if any(path.exists() for path in outputs.values()):
        raise FileExistsError("Refusing to overwrite an existing survive-day output")

    runner, _, input_audit = _load_events(config)
    baseline_metrics, baseline_audit = _frozen_baseline(config, runner)
    ranks = leadership.leadership()
    rank_keys = {(int(time), str(symbol)) for time, symbol in ranks.index}
    if len(rank_keys) < int(config["eligibility"]["minimum_rank_keys"]):
        raise ValueError("The point-in-time 90d ranking matrix is unexpectedly incomplete")
    delayed, path_audit = _materialize(
        runner, rank_keys, set(config["eligibility"]["native_12h_sources"]))

    actual = delayed[(~delayed.is_control) & delayed.entry_status.eq("executed")].copy()
    controls = delayed[delayed.is_control & delayed.entry_status.eq("executed")].copy()
    slots = int(config["account"]["slots"])

    print("Funding delayed full runner with the existing VectorBT cash-sharing wallet", flush=True)
    delayed_metrics, delayed_meta, delayed_funded, delayed_equity = _run_account(actual, slots)
    rows = []
    for cohort, values in baseline_metrics.items():
        rows.extend(_metric_rows(BASELINE_ARM, cohort, values))
    rows.extend(_metric_rows(DELAYED_ARM, "actual", delayed_metrics))
    dev_period = str(config["selection"]["development_period"])
    dev_pass = _passes_dev(delayed_metrics[dev_period], config["selection"]["gates"])
    checks = {}

    if dev_pass:
        signal_parents = set(actual.parent_id.astype(str))
        control_parents = set(controls.parent_id.astype(str))
        paired_parents = signal_parents & control_parents
        paired_actual = actual[actual.parent_id.astype(str).isin(paired_parents)]
        paired_control = controls[controls.parent_id.astype(str).isin(paired_parents)]
        for label, cohort, pool, slip in (
            ("paired_actual", "paired_actual", paired_actual, 0.0),
            ("paired_control", "paired_control", paired_control, 0.0),
            ("stress_5bp_actual", "slippage_5bp_actual", actual, 0.0005),
        ):
            metrics, meta, funded, equity = _run_account(pool, slots, slip=slip)
            rows.extend(_metric_rows(DELAYED_ARM, cohort, metrics))
            checks[label] = {"metrics": metrics, "wallet": meta}
            funded.to_csv(outputs[f"funded_{label}"], index=False, compression="gzip")
            equity.to_csv(outputs[f"hourly_{label}"], index_label="hour_end_utc")
            gc.collect()
        checks["paired_parent_count"] = len(paired_parents)

    delayed_funded.to_csv(outputs["funded"], index=False, compression="gzip")
    delayed_equity.to_csv(outputs["hourly"], index_label="hour_end_utc")
    parent = runner.copy()
    parent["arm"] = BASELINE_ARM
    parent["delay_status"] = "frozen_native_v1_7_runner_path"
    delayed["arm"] = DELAYED_ARM
    pd.concat([parent, delayed], ignore_index=True, sort=False).to_csv(
        outputs["paths"], index=False, compression="gzip")
    metrics_frame = pd.DataFrame(rows)
    metrics_frame.to_csv(outputs["account_metrics"], index=False)

    summary = {
        "experiment_id": config["id"],
        "generator": str(Path(__file__).relative_to(ROOT)),
        "vectorbt_version": vbt.__version__,
        "inputs": input_audit,
        "counts": {**path_audit["counts"],
                   "original_actual_funded": baseline_audit["actual"]["funded_positions"],
                   "original_control_funded": baseline_audit["control"]["funded_positions"],
                   "paired_delayed_parent_candidates": len(
                       set(actual.parent_id.astype(str)) & set(controls.parent_id.astype(str)))},
        "path_audit": path_audit,
        "frozen_baseline": {
            "source": baseline_audit,
            "metrics": baseline_metrics,
            "method": "wallet.summarize over the already-funded original V1.7 full-runner actual/control ledgers and contemporaneous hourly marks; no baseline account or exit replay.",
        },
        "delayed_actual": {"metrics": delayed_metrics, "wallet": delayed_meta},
        "selection": {
            "development_period": dev_period,
            "gates": config["selection"]["gates"],
            "development_gate_pass": dev_pass,
            "paired_and_slippage_checks_run_only_if_gate_passes": True,
        },
        "checks": checks,
        "method": {
            "decision": "Exactly 24h after each event's own frozen confirmation; fill at the exact H1 open coincident with the second completed native 12H close.",
            "observations": "Require H1 open timestamps at anchor + 0..24h and native 12H bars opening at anchor and anchor + 12h.",
            "survival": "Original runner exit must be later than the decision. At equal time, an open-phase exit precedes entry; intrabar/close exits occur after the H1 open.",
            "original_90d": "Use the independently corrected eligible_90d source roster; recheck numeric 90d leadership at the delayed time using the existing >=10-member screen.",
            "exits": "Reuse the frozen original native full-runner exit timestamp, price, phase, reason, and exit fee rate. No path-engine replay or exit repricing.",
            "stop": "Keep the frozen absolute stop and require the delayed open to be strictly on its safe side.",
            "account": "Recompute only the funded delayed account using the existing VectorBT cash-sharing wallet; nine slots, low original prior-ATR priority, at most NAV/9 and available collateral per entry.",
            "fees": {"entry_taker": TAKER, "frozen_exit_fee_rate": "preserved from the native path", "extra_stress_slippage_per_market_fill": 0.0005},
        },
        "outputs": {key: str(path.relative_to(ROOT)) for key, path in outputs.items()},
    }
    _write_json(outputs["summary"], summary)
    _write_json(outputs["audit"], {"experiment_id": config["id"], "inputs": input_audit,
                                    "counts": summary["counts"], "path_audit": path_audit,
                                    "frozen_baseline": baseline_audit})
    print(metrics_frame[metrics_frame.period.eq("all")].to_string(index=False), flush=True)
    return summary


def _self_check() -> None:
    clock = np.arange(25, dtype=np.int64) * HOUR_MS
    locations, reason = _exact_h1_window(0, clock)
    assert reason == "eligible" and np.array_equal(locations, np.arange(25))
    locations, reason = _exact_h1_window(0, np.delete(clock, 7))
    assert locations is None and reason == "missing_one_or_more_of_24_wait_hours_or_decision_open"
    assert _exit_before_decision(23 * HOUR_MS, "intrabar", 24 * HOUR_MS)
    assert _exit_before_decision(24 * HOUR_MS, "open", 24 * HOUR_MS)
    assert not _exit_before_decision(24 * HOUR_MS, "intrabar", 24 * HOUR_MS)
    assert not _exit_before_decision(24 * HOUR_MS, "close", 24 * HOUR_MS)
    assert not _exit_before_decision(25 * HOUR_MS, "open", 24 * HOUR_MS)
    signal = {"is_control": False, "entry_ms": 12 * HOUR_MS,
              "confirmation_ms": 12 * HOUR_MS, "key_open_ms": 0}
    control = {"is_control": True, "entry_ms": 12 * HOUR_MS,
               "control_confirmation_ms": 12 * HOUR_MS,
               "control_12h_open_ms": 0}
    assert _event_anchor(signal) == (12 * HOUR_MS, 0, "eligible")
    assert _event_anchor(control) == (12 * HOUR_MS, 0, "eligible")


def smoke(config_path: Path) -> dict:
    """Use one real survivor to verify new-open pricing and wallet reconciliation."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runner, _, audit = _load_events(config)
    rank = leadership.leadership()
    rank_keys = {(int(time), str(symbol)) for time, symbol in rank.index}
    native = set(config["eligibility"]["native_12h_sources"])
    selected = facts = None
    scanned = 0
    for _, group in runner[~runner.is_control].groupby("symbol", sort=True):
        bars12, bars1, bar_ms = _symbol_inputs(group)
        scanned += 1
        for event in group.to_dict("records"):
            result = _decision_facts(event, bars12, bars1, bar_ms, rank_keys, native)
            if result["eligible"]:
                selected, facts = event, result
                break
        if selected is not None:
            break
        gc.collect()
    if selected is None:
        raise ValueError("No actual 24h survivor found for the small real-data check")

    delayed = pd.DataFrame([_delayed_row(selected, facts)])
    start = int(facts["decision_ms"] // HOUR_MS * HOUR_MS)
    end = int(delayed.exit_bar_ms.iloc[0] // HOUR_MS * HOUR_MS + HOUR_MS)
    data = wallet.prepare(delayed, int(config["account"]["slots"]), start=start, end=end)
    funded, equity = wallet.account(data)
    if len(funded) != 1 or not np.isclose(1000.0 + funded.cash_pnl.sum(), equity.nav.iloc[-1],
                                          rtol=1e-9, atol=0.01):
        raise ValueError("One-position VectorBT wallet smoke did not reconcile")
    return {
        "status": "passed", "source_cohort": audit,
        "event_id": str(selected["event_id"]), "symbol": str(selected["symbol"]),
        "side": int(selected["side"]),
        "delay_hours": (facts["decision_ms"] - facts["original_entry_ms"]) / HOUR_MS,
        "h1_observations": 25,
        "two_native_12h_sources": [facts["first_wait_12h_source"], facts["second_wait_12h_source"]],
        "original_runner_survived": facts["survives_original_exit_clock"],
        "delayed_90d_eligible": facts["delayed_eligible_90d"],
        "delayed_entry_price": float(delayed.entry_price.iloc[0]),
        "frozen_exit_time_unchanged": int(delayed.exit_bar_ms.iloc[0]) == int(selected["exit_bar_ms"]),
        "frozen_exit_price_unchanged": float(delayed.exit_price.iloc[0]) == float(selected["exit_price"]),
        "exit_reason": str(delayed.exit_reason.iloc[0]),
        "cash_pnl": float(funded.cash_pnl.iloc[0]),
        "final_nav": float(equity.nav.iloc[-1]),
        "cash_reconciliation_usdt": float(1000.0 + funded.cash_pnl.sum() - equity.nav.iloc[-1]),
        "symbols_scanned": scanned,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--smoke", action="store_true",
                       help="Price/fund one real survivor without writing study outputs")
    modes.add_argument("--run", action="store_true",
                       help="Generate delayed paths and funded VectorBT account results")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    if args.self_check:
        _self_check()
        print("self-check: passed (24 exact H1 waits, native control clock, exit precedence)")
    elif args.smoke:
        _self_check()
        print(json.dumps(smoke(config_path), ensure_ascii=False, indent=2, allow_nan=False))
    else:
        _self_check()
        summary = generate(config_path)
        print(json.dumps({"experiment_id": summary["experiment_id"],
                          "counts": summary["counts"],
                          "development_gate_pass": summary["selection"]["development_gate_pass"]},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
