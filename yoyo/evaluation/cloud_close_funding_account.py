"""Replay the frozen Binance NAV-tenth account with time-aware funding cashflows.

All entries/exits remain VectorBT order-func orders at the frozen native prices.
Funding is a cash-only account event applied at its observed fundingTime; it is
never represented as a synthetic market order. Exact entry/exit coincidences
are replayed twice (include/exclude), and an intrabar exit-hour settlement uses
the quantity cached immediately before that native exit order.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_current_funding as current
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-funding-account-20261109-v1"
CONFIG_PATH = EXP / "config.json"
CURRENT_OUTPUT = current.CURRENT_OUTPUT
HOUR = wallet.HOUR
TAKER = wallet.TAKER
MAKER = 0.0002
START = wallet.START
END = wallet.END
INITIAL_CASH = 1000.0
CASH_TOL = 1e-7


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_config(config: dict) -> None:
    if config.get("id") != EXP.name or config.get("venue") != "Binance USD-M":
        raise ValueError("Funding account config identity or venue changed")
    account = config["account"]
    if (int(account["slots"]) != 9 or int(account["expected_positions"]) != 1635
            or int(account["expected_child_legs"]) != 3270
            or not np.isclose(float(account["initial_cash_usdt"]), INITIAL_CASH)
            or not np.isclose(float(account["new_order_exposure_fraction"]), 0.9)):
        raise ValueError("The frozen 680-contract account sizing contract changed")
    if config["boundary_policy"]["timestamp_resolution"] != "Use exact source milliseconds; no ±1s timing inference":
        raise ValueError("Boundary timing must be decided from the original millisecond timestamps")
    if config["network_policy"] != "Read existing local execution and settlement files only; no network, archive download, or collector":
        raise ValueError("This builder is offline-only")
    expected_inputs = {
        "execution_legs": "experiments/active/exp-cloud-close-current-funding-20261109-v1/current/execution_legs.csv.gz",
        "settlement_flows": "experiments/active/exp-cloud-close-current-funding-20261109-v1/resolved/settlement_flows.csv.gz",
        "funding_audit": "experiments/active/exp-cloud-close-current-funding-20261109-v1/resolved/funding_audit.json",
        "symbol_month_coverage": "experiments/active/exp-cloud-close-current-funding-20261109-v1/resolved/symbol_month_audit.json",
    }
    if any(config["inputs"].get(key) != value for key, value in expected_inputs.items()):
        raise ValueError("Funding account must use the frozen execution legs and resolved funding receipts")
    if config["account"]["entry_priority"] != (
        "Rejoin the frozen native paths by event_id and use their original entry_order; "
        "preserve the existing wallet admission order entry_ms, entry_order, symbol CRC32, symbol, event_id."
    ):
        raise ValueError("The frozen event admission priority changed")


def _assert_committed() -> None:
    paths = [Path(__file__).resolve(), CONFIG_PATH.resolve(),
             current.CONFIG_PATH.resolve(), Path(current.__file__).resolve()]
    for path in paths:
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError(f"Commit the funding account builder and source adapter first: {relative}")


def _bool_column(series: pd.Series, name: str) -> pd.Series:
    if series.dtype == bool:
        return series
    mapped = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if mapped.isna().any():
        raise ValueError(f"Invalid boolean values in {name}")
    return mapped.astype(bool)


def _load_frozen_inputs(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    files = config["inputs"]
    execution_path = ROOT / files["execution_legs"]
    flows_path = ROOT / files["settlement_flows"]
    audit_path = ROOT / files["funding_audit"]
    coverage_path = ROOT / files["symbol_month_coverage"]
    for path in (execution_path, flows_path, audit_path, coverage_path):
        if not path.is_file():
            raise FileNotFoundError(f"Frozen funding input is not available: {path}")
    legs = pd.read_csv(
        execution_path,
        dtype={"event_id": str, "parent_id": str, "symbol": str},
        float_precision="round_trip",
    )
    flows = pd.read_csv(
        flows_path,
        dtype={"event_id": str, "parent_id": str, "symbol": str},
        float_precision="round_trip",
    )
    audit = _read_json(audit_path)
    month_coverage = _read_json(coverage_path)
    if not isinstance(month_coverage, list):
        raise ValueError("Resolved symbol-month coverage must be the ledger list receipt")
    month_frame = pd.DataFrame(month_coverage)
    required_month_fields = {"symbol", "month", "range_complete"}
    if required_month_fields - set(month_frame.columns):
        raise ValueError(f"Resolved symbol-month coverage lacks {sorted(required_month_fields - set(month_frame.columns))}")
    if month_frame.duplicated(["symbol", "month"]).any():
        raise ValueError("Resolved symbol-month coverage contains duplicate windows")
    month_frame["range_complete"] = _bool_column(
        month_frame.range_complete, "symbol_month_coverage.range_complete",
    )
    needed_legs = {
        "event_id", "tp_r", "parent_id", "symbol", "is_control", "side", "entry_ms",
        "entry_price", "exit_bar_ms", "exit_phase", "exit_price", "exit_fee_rate",
        "exit_reason", "signed_size",
    }
    needed_flows = {
        "event_id", "symbol", "tp_r", "funding_time_ms", "funding_rate", "mark_price",
        "mark_source", "boundary_ambiguous", "cashflow_known",
    }
    if needed_legs - set(legs.columns) or needed_flows - set(flows.columns):
        raise ValueError(
            f"Missing frozen input columns: legs={sorted(needed_legs-set(legs.columns))}, "
            f"flows={sorted(needed_flows-set(flows.columns))}"
        )
    for col in ("tp_r", "entry_price", "exit_price", "exit_fee_rate", "signed_size"):
        legs[col] = pd.to_numeric(legs[col], errors="raise").astype(float)
    for col in ("side", "entry_ms", "exit_bar_ms"):
        legs[col] = pd.to_numeric(legs[col], errors="raise").astype("int64")
    flows["tp_r"] = pd.to_numeric(flows.tp_r, errors="raise").astype(float)
    flows["funding_time_ms"] = pd.to_numeric(flows.funding_time_ms, errors="raise").astype("int64")
    flows["funding_rate"] = pd.to_numeric(flows.funding_rate, errors="coerce")
    flows["mark_price"] = pd.to_numeric(flows.mark_price, errors="coerce")
    flows["boundary_ambiguous"] = _bool_column(flows.boundary_ambiguous, "boundary_ambiguous")
    flows["cashflow_known"] = _bool_column(flows.cashflow_known, "cashflow_known")
    legs["is_control"] = _bool_column(legs.is_control, "execution_legs.is_control")
    if legs.is_control.any():
        raise ValueError("The current 680-account funding replay accepts actual positions only")
    if (legs.duplicated(["event_id", "tp_r"]).any()
            or not legs.groupby("event_id").size().eq(2).all()
            or not legs.groupby("event_id").tp_r.apply(lambda x: set(x) == {0.0, 2.0}).all()):
        raise ValueError("Expected one native runner and one 2R child per frozen event")
    if len(legs) != int(config["account"]["expected_child_legs"]):
        raise ValueError(f"Expected {config['account']['expected_child_legs']} frozen child legs, found {len(legs)}")
    if not legs.side.isin([-1, 1]).all():
        raise ValueError("Invalid side on frozen execution legs")
    if not legs.exit_phase.isin(["open", "intrabar", "close"]).all():
        raise ValueError("Unknown frozen exit phase")
    if (legs.entry_ms > legs.exit_bar_ms).any():
        raise ValueError("A frozen child exits before entry")
    if not np.allclose(legs.signed_size, legs.side * legs.signed_size.abs(), rtol=1e-10, atol=1e-12):
        raise ValueError("Frozen child quantity sign differs from side")
    if flows.duplicated(["event_id", "tp_r", "funding_time_ms"]).any():
        raise ValueError("Funding settlement rows are not unique by event, child and time")
    leg_keys = set(zip(legs.event_id.astype(str), legs.tp_r.astype(float)))
    flow_keys = set(zip(flows.event_id.astype(str), flows.tp_r.astype(float)))
    if flow_keys - leg_keys:
        raise ValueError("Settlement rows refer to child legs outside the frozen account")
    if not np.isfinite(legs[["entry_price", "exit_price", "exit_fee_rate", "signed_size"]]).all().all():
        raise ValueError("Frozen execution rows contain non-finite prices, fees, or quantities")
    if int(audit.get("funded_positions", -1)) != len(legs) // 2:
        raise ValueError("Funding receipt position count differs from execution legs")
    if int(audit.get("funding_settlement_rows", -1)) != len(flows):
        raise ValueError("Resolved funding settlement count differs from the funding audit receipt")
    if int(audit.get("covered_position_legs", -1)) + int(audit.get("incomplete_position_legs", -1)) != len(legs):
        raise ValueError("Resolved covered and incomplete leg counts do not partition the execution roster")
    if int(audit.get("held_symbol_month_windows", -1)) != len(month_frame):
        raise ValueError("Resolved month-window count differs from the funding audit receipt")
    if int((~month_frame.range_complete).sum()) != int(audit.get("unknown_month_windows", -1)):
        raise ValueError("Resolved incomplete month rows differ from the funding audit receipt")
    coverage_keys = set(zip(month_frame.symbol.astype(str), month_frame.month.astype(str)))
    flow_symbols = flows.symbol.astype(str)
    flow_tickers = np.where(flow_symbols.str.endswith("USDT"), flow_symbols, flow_symbols + "USDT")
    flow_months = pd.to_datetime(flows.funding_time_ms, unit="ms", utc=True).dt.strftime("%Y-%m")
    missing_coverage = sorted(set(zip(flow_tickers, flow_months)) - coverage_keys)
    if missing_coverage:
        raise ValueError(f"Resolved settlement rows lie outside their symbol-month coverage: {missing_coverage[:8]}")
    return legs, flows, audit, {
        "execution_file": str(execution_path.relative_to(ROOT)),
        "settlement_file": str(flows_path.relative_to(ROOT)),
        "execution_rows": int(len(legs)),
        "positions": int(legs.event_id.nunique()),
        "symbols": int(legs.symbol.nunique()),
        "settlement_rows": int(len(flows)),
        "symbol_month_coverage_file": str(coverage_path.relative_to(ROOT)),
        "symbol_month_windows": int(len(month_frame)),
        "incomplete_symbol_month_windows": int((~month_frame.range_complete).sum()),
        "month_coverage_complete": bool(month_frame.range_complete.all()),
        "settlement_rows_outside_symbol_month_coverage": 0,
    }


def _coverage_status(flows: pd.DataFrame, audit: dict, inputs_audit: dict) -> dict:
    unknown_rows = int((~flows.cashflow_known).sum())
    missing_rates = int(flows.funding_rate.isna().sum())
    missing_marks = int((flows.mark_price.isna() | flows.mark_price.le(0)).sum())
    incomplete_legs = int(audit.get("incomplete_position_legs", -1))
    unknown_months = int(audit.get("unknown_month_windows", -1))
    query_errors = int(audit.get("api_query_errors", -1))
    complete = (
        incomplete_legs == 0 and unknown_months == 0 and query_errors == 0
        and unknown_rows == 0 and missing_rates == 0 and missing_marks == 0
        and bool(inputs_audit["month_coverage_complete"])
        and int(inputs_audit["symbol_month_windows"]) == int(audit.get("held_symbol_month_windows", -1))
    )
    return {
        "complete_for_account_replay": bool(complete),
        "incomplete_position_legs": incomplete_legs,
        "unknown_month_windows": unknown_months,
        "api_query_errors": query_errors,
        "unknown_settlement_rows": unknown_rows,
        "missing_rate_rows": missing_rates,
        "missing_or_nonpositive_mark_rows": missing_marks,
        "proxy_mark_rows": int(flows.mark_source.astype(str).eq("binance_vision_mark_1h_open_proxy").sum()),
        "exact_mark_rows": int(flows.mark_source.astype(str).eq("binance_fundingRate_markPrice").sum()),
        "resolved_month_windows": int(inputs_audit["symbol_month_windows"]),
        "resolved_month_coverage_complete": bool(inputs_audit["month_coverage_complete"]),
    }


def _flow_relation(flow: pd.Series, leg: pd.Series) -> str:
    """Classify a settlement against the exact native entry/phase exit timestamps."""
    ts = int(flow.funding_time_ms)
    entry = int(leg.entry_ms)
    exit_bar = int(leg.exit_bar_ms)
    phase = str(leg.exit_phase)
    if ts < entry:
        return "before_entry"
    if ts == entry:
        return "entry_boundary"
    if phase == "open":
        exit_ms = exit_bar
        if ts < exit_ms:
            return "held_interior"
        if ts == exit_ms:
            return "open_exit_boundary"
        return "after_exit"
    if phase == "close":
        exit_ms = exit_bar + HOUR
        if ts < exit_ms:
            return "held_interior"
        if ts == exit_ms:
            return "close_exit_boundary"
        return "after_exit"
    if ts < exit_bar:
        return "held_interior"
    if ts <= exit_bar + HOUR:
        return "intrabar_exit_hour"
    return "after_exit"


def _attach_exact_leg_clock(flows: pd.DataFrame, legs: pd.DataFrame) -> pd.DataFrame:
    """Join by event_id/tp_r and cross-check any clock copied into the flow file."""
    frame = flows.copy().reset_index(drop=True)
    source = legs.set_index(["event_id", "tp_r"])
    if not source.index.is_unique:
        raise ValueError("Execution leg mapping is not unique")
    entry_values, exit_values, phase_values, size_values, relations = [], [], [], [], []
    for row in frame.itertuples(index=False):
        key = (str(row.event_id), float(row.tp_r))
        if key not in source.index:
            raise ValueError(f"Funding settlement has no execution leg: {key}")
        leg = source.loc[key]
        for col in ("entry_ms", "exit_bar_ms"):
            copied = getattr(row, col, None)
            if copied is not None and pd.notna(copied) and int(copied) != int(leg[col]):
                raise ValueError(f"Funding ledger {col} differs from native execution leg for {key}")
        copied_phase = getattr(row, "exit_phase", None)
        if copied_phase is not None and pd.notna(copied_phase) and str(copied_phase) != str(leg.exit_phase):
            raise ValueError(f"Funding ledger exit_phase differs from native execution leg for {key}")
        entry_values.append(int(leg.entry_ms))
        exit_values.append(int(leg.exit_bar_ms))
        phase_values.append(str(leg.exit_phase))
        size_values.append(float(leg.signed_size))
        relations.append(_flow_relation(pd.Series({"funding_time_ms": int(row.funding_time_ms)}), leg))
    frame["entry_ms"] = np.asarray(entry_values, dtype=np.int64)
    frame["exit_bar_ms"] = np.asarray(exit_values, dtype=np.int64)
    frame["exit_phase"] = np.asarray(phase_values, dtype=object)
    frame["signed_size"] = np.asarray(size_values, dtype=np.float64)
    frame["relation"] = np.asarray(relations, dtype=object)
    return frame


def _schedule_flows(data: dict, legs: pd.DataFrame, flows: pd.DataFrame, scenario: str) -> dict:
    if scenario not in {"include_boundary", "exclude_boundary"}:
        raise ValueError(f"Unknown funding boundary scenario: {scenario}")
    selected = data["selected"]
    selected_keys = set(zip(selected.event_id.astype(str), selected.tp_r.astype(float)))
    flow_frame = flows.copy().reset_index(drop=True)
    flow_frame["event_id"] = flow_frame.event_id.astype(str)
    flow_frame["tp_r"] = flow_frame.tp_r.astype(float)
    flow_frame = _attach_exact_leg_clock(flow_frame, legs)
    slot_map = {(str(r.event_id), float(r.tp_r)): int(r.slot) for r in selected.itertuples(index=False)}
    code_map = {key: i for i, key in enumerate(sorted(selected_keys))}
    flow_frame["slot"] = [slot_map[(e, t)] for e, t in zip(flow_frame.event_id, flow_frame.tp_r)]
    flow_frame["event_code"] = [code_map[(e, t)] for e, t in zip(flow_frame.event_id, flow_frame.tp_r)]
    n_steps = int(data["actions"].shape[0])
    start = int(data["start"])
    pre_buckets: list[list[int]] = [[] for _ in range(n_steps)]
    post_buckets: list[list[int]] = [[] for _ in range(n_steps)]
    flow_frame["scheduled_stage"] = -1
    flow_frame["scheduled_hook"] = "none"
    flow_frame["use_exit_cache"] = False
    flow_frame["scenario_status"] = "outside_hold"

    boundary_relations = {
        "entry_boundary", "open_exit_boundary", "close_exit_boundary", "intrabar_exit_hour",
    }
    for idx, row in flow_frame.iterrows():
        relation = str(row.relation)
        boundary = relation in boundary_relations
        if relation in {"before_entry", "after_exit"}:
            flow_frame.at[idx, "scenario_status"] = relation
            continue
        if boundary and scenario == "exclude_boundary":
            flow_frame.at[idx, "scenario_status"] = "boundary_excluded"
            continue
        ts = int(row.funding_time_ms)
        hour_idx = (ts - start) // HOUR
        if hour_idx < 0:
            raise ValueError("A settlement occurs before the account clock")
        if relation == "entry_boundary":
            stage, hook, cache = int(hour_idx * 3), 1, False
        elif relation == "open_exit_boundary":
            stage, hook, cache = int(hour_idx * 3 + 1), 1, True
        elif relation in {"close_exit_boundary", "intrabar_exit_hour"}:
            # The phase-specific exit action runs at this row's close. Apply
            # after it and recover only this just-exited child from its cache.
            exit_hour = (int(row.exit_bar_ms) - start) // HOUR
            stage, hook, cache = int(exit_hour * 3 + 2), 1, True
        elif ts % HOUR == 0:
            # A settlement before the H1 open order sequence uses the prior
            # live quantity; a same-ms entry has its own post-entry boundary.
            stage, hook, cache = int(hour_idx * 3), 0, False
        else:
            # Subhour funding is visible after same-hour open exits and before
            # the next sizing decision. Intrabar exit-hour rows are separated
            # above and handled by the include/exclude branch.
            stage, hook, cache = int(hour_idx * 3 + 1), 1, False
        if not 0 <= stage < n_steps:
            raise ValueError(f"Settlement stage lies outside the account grid: {row.funding_time_ms}")
        flow_frame.at[idx, "scheduled_stage"] = stage
        flow_frame.at[idx, "scheduled_hook"] = "post" if hook else "pre"
        flow_frame.at[idx, "use_exit_cache"] = cache
        flow_frame.at[idx, "scenario_status"] = "boundary_included" if boundary else "held_interior"
        (post_buckets if hook else pre_buckets)[stage].append(int(idx))

    def pack(buckets: list[list[int]]) -> tuple[np.ndarray, np.ndarray]:
        ptr = np.zeros(n_steps + 1, dtype=np.int64)
        ids: list[int] = []
        for i, bucket in enumerate(buckets):
            ptr[i] = len(ids)
            ids.extend(bucket)
        ptr[n_steps] = len(ids)
        return ptr, np.asarray(ids, dtype=np.int64)

    pre_ptr, pre_ids = pack(pre_buckets)
    post_ptr, post_ids = pack(post_buckets)
    cols = data["actions"].shape[1]
    event_codes = np.full(data["actions"].shape, -1, dtype=np.int32)
    for row in selected.itertuples(index=False):
        code = code_map[(str(row.event_id), float(row.tp_r))]
        first = (int(row.entry_ms) - start) // HOUR * 3
        last = (int(row.exit_bar_ms) - start) // HOUR * 3
        phase = 1 if str(row.exit_phase) == "open" else 2
        event_codes[first, int(row.slot)] = code
        event_codes[last + phase, int(row.slot)] = code
    return {
        "flows": flow_frame,
        "pre_ptr": pre_ptr, "pre_ids": pre_ids,
        "post_ptr": post_ptr, "post_ids": post_ids,
        "flow_col": flow_frame.slot.to_numpy(dtype=np.int64),
        "flow_code": flow_frame.event_code.to_numpy(dtype=np.int32),
        "flow_rate": flow_frame.funding_rate.to_numpy(dtype=np.float64),
        "flow_mark": flow_frame.mark_price.to_numpy(dtype=np.float64),
        "flow_cache": flow_frame.use_exit_cache.to_numpy(dtype=np.bool_),
        "event_codes": event_codes,
        "pre_cashflow": np.zeros((2, n_steps), dtype=np.float64),
        "flow_cashflow": np.zeros(len(flow_frame), dtype=np.float64),
        "flow_quantity": np.full(len(flow_frame), np.nan, dtype=np.float64),
        "flow_applied": np.zeros(len(flow_frame), dtype=np.bool_),
        "exit_cache_quantity": np.zeros(cols, dtype=np.float64),
        "exit_cache_event": np.full(cols, -1, dtype=np.int32),
        "nav_pre": np.full(n_steps, np.nan, dtype=np.float64),
        "nav_post": np.full(n_steps, np.nan, dtype=np.float64),
        "cash_pre": np.full(n_steps, np.nan, dtype=np.float64),
        "cash_post": np.full(n_steps, np.nan, dtype=np.float64),
        "pos_pre": np.full((n_steps, cols), np.nan, dtype=np.float64),
        "pos_post": np.full((n_steps, cols), np.nan, dtype=np.float64),
    }


@njit(cache=True)
def _apply_flows(c, begin, end, hook, flow_col, flow_code, flow_rate, flow_mark,
                 flow_ids, flow_cache, flow_cashflow, flow_quantity, flow_applied,
                 exit_cache_quantity, exit_cache_event, stage_cashflow):
    total = 0.0
    for j in range(begin, end):
        idx = int(flow_ids[j])
        col = int(flow_col[idx])
        if flow_applied[idx]:
            raise ValueError("Funding settlement applied more than once")
        quantity = c.last_position[col]
        if abs(quantity) <= 1e-15 and flow_cache[idx]:
            if exit_cache_event[col] == flow_code[idx] and abs(exit_cache_quantity[col]) > 1e-15:
                quantity = exit_cache_quantity[col]
        if abs(quantity) <= 1e-15:
            raise ValueError("Funding row has no live or just-exited cached child quantity")
        rate = flow_rate[idx]
        mark = flow_mark[idx]
        if not np.isfinite(rate) or not np.isfinite(mark) or mark <= 0.0:
            raise ValueError("Funding row lacks a finite rate or positive mark")
        cashflow = -quantity * rate * mark
        flow_cashflow[idx] = cashflow
        flow_quantity[idx] = quantity
        flow_applied[idx] = True
        total += cashflow
    cash_now = c.last_cash[c.group]
    free_now = c.last_free_cash[c.group]
    new_cash = cash_now + total
    new_free = free_now + total
    if not np.isfinite(new_cash) or not np.isfinite(new_free) or new_cash < -CASH_TOL:
        raise ValueError("Funding debit exceeds account cash; full account path is insolvent")
    if new_free < -CASH_TOL:
        raise ValueError("Funding debit exceeds free collateral; full account path is under-margined")
    c.last_cash[c.group] = max(0.0, new_cash)
    c.last_free_cash[c.group] = max(0.0, new_free)
    stage_cashflow[hook, c.i] += total
    return total


@njit(cache=True)
def _pre_segment(c, pre_ptr, pre_ids, flow_col, flow_code, flow_rate, flow_mark,
                 flow_cache, flow_cashflow, flow_quantity, flow_applied,
                 exit_cache_quantity, exit_cache_event, stage_cashflow,
                 nav_pre, cash_pre, pos_pre):
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = c.close[c.i, col]
    _apply_flows(
        c, pre_ptr[c.i], pre_ptr[c.i + 1], 0,
        flow_col, flow_code, flow_rate, flow_mark, pre_ids, flow_cache,
        flow_cashflow, flow_quantity, flow_applied,
        exit_cache_quantity, exit_cache_event, stage_cashflow,
    )
    nav = nb.get_group_value_ctx_nb(c)
    nav_pre[c.i] = nav
    cash_pre[c.i] = c.last_cash[c.group]
    for col in range(c.from_col, c.to_col):
        pos_pre[c.i, col] = c.last_position[col]
    return (nav,)


@njit(cache=True)
def _post_segment(c, post_ptr, post_ids, flow_col, flow_code, flow_rate, flow_mark,
                  flow_cache, flow_cashflow, flow_quantity, flow_applied,
                  exit_cache_quantity, exit_cache_event, stage_cashflow,
                  nav_post, cash_post, pos_post):
    _apply_flows(
        c, post_ptr[c.i], post_ptr[c.i + 1], 1,
        flow_col, flow_code, flow_rate, flow_mark, post_ids, flow_cache,
        flow_cashflow, flow_quantity, flow_applied,
        exit_cache_quantity, exit_cache_event, stage_cashflow,
    )
    nav_post[c.i] = nb.get_group_value_ctx_nb(c)
    cash_post[c.i] = c.last_cash[c.group]
    for col in range(c.from_col, c.to_col):
        pos_post[c.i, col] = c.last_position[col]


@njit(cache=True)
def _account_order(c, nav, actions, prices, fees, market_slip, exposure, weights,
                   reservation, full_slots, slot_weights, event_codes,
                   exit_cache_quantity, exit_cache_event):
    action = actions[c.i, c.col]
    if action == 2:
        exit_cache_quantity[c.col] = c.position_now
        exit_cache_event[c.col] = event_codes[c.i, c.col]
    return wallet.account_order(
        c, nav, actions, prices, fees, market_slip, exposure, weights,
        reservation, full_slots, slot_weights,
    )


def _position_and_order_records(pf, data: dict, scheduled: dict, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    selected = data["selected"].copy()
    orders = pf.orders.records.copy()
    if orders.empty:
        raise RuntimeError("VectorBT produced no orders for the frozen funded roster")
    start = int(data["start"])
    entry_stage = (selected.entry_ms.to_numpy(dtype=np.int64) - start) // HOUR * 3
    exit_phase = selected.exit_phase.astype(str).to_numpy()
    exit_stage = (selected.exit_bar_ms.to_numpy(dtype=np.int64) - start) // HOUR * 3 + np.where(exit_phase == "open", 1, 2)
    keyed = orders.set_index(["idx", "col"])
    entry = keyed.reindex(pd.MultiIndex.from_arrays([entry_stage, selected.slot.to_numpy(dtype=np.int64)]))
    exit_ = keyed.reindex(pd.MultiIndex.from_arrays([exit_stage, selected.slot.to_numpy(dtype=np.int64)]))
    if entry["size"].isna().any() or exit_["size"].isna().any():
        missing_entries = int(entry["size"].isna().sum())
        missing_exits = int(exit_["size"].isna().sum())
        raise RuntimeError(f"Frozen position did not fully fund/close: entries={missing_entries}, exits={missing_exits}")
    if not np.allclose(entry["size"].to_numpy(), exit_["size"].to_numpy(), rtol=1e-9, atol=1e-12):
        raise ValueError("VectorBT entry and exit quantities do not match by event_id/tp_r")
    quantity = entry["size"].to_numpy(dtype=float)
    side = selected.side.to_numpy(dtype=int)
    selected["signed_size"] = quantity * side
    selected["quantity_abs"] = quantity
    selected["entry_fill_price"] = entry.price.to_numpy(dtype=float)
    selected["exit_fill_price"] = exit_.price.to_numpy(dtype=float)
    selected["entry_fee_usdt"] = entry.fees.to_numpy(dtype=float)
    selected["exit_fee_usdt"] = exit_.fees.to_numpy(dtype=float)
    selected["entry_notional_usdt"] = quantity * selected.entry_fill_price
    selected["order_cash_pnl_usdt"] = (
        side * (selected.exit_fill_price - selected.entry_fill_price) * quantity
        - selected.entry_fee_usdt - selected.exit_fee_usdt
    )
    selected["funding_cashflow_usdt"] = 0.0
    flow_rows = scheduled["flows"].copy()
    flow_rows["funding_cashflow_applied_usdt"] = scheduled["flow_cashflow"]
    flow_rows["quantity_used"] = scheduled["flow_quantity"]
    flow_rows["applied"] = scheduled["flow_applied"]
    for i, row in flow_rows.loc[flow_rows.applied].iterrows():
        key_mask = selected.event_id.astype(str).eq(str(row.event_id)) & selected.tp_r.eq(float(row.tp_r))
        if int(key_mask.sum()) != 1:
            raise ValueError("Applied funding row does not map to one execution child")
        selected.loc[key_mask, "funding_cashflow_usdt"] += float(row.funding_cashflow_applied_usdt)
    selected["cash_pnl_usdt"] = selected.order_cash_pnl_usdt + selected.funding_cashflow_usdt
    selected["notional_usdt"] = selected.entry_notional_usdt
    selected["net_return"] = selected.cash_pnl_usdt / selected.notional_usdt
    grouped = selected.groupby("event_id", sort=False).agg(
        parent_id=("parent_id", "first"), symbol=("symbol", "first"), side=("side", "first"),
        entry_ms=("entry_ms", "first"), exit_bar_ms=("exit_bar_ms", "max"),
        order_cash_pnl_usdt=("order_cash_pnl_usdt", "sum"),
        funding_cashflow_usdt=("funding_cashflow_usdt", "sum"),
        cash_pnl=("cash_pnl_usdt", "sum"), notional=("notional_usdt", "sum"),
    )
    grouped["net_return"] = grouped.cash_pnl / grouped.notional
    order_cash_reconcile = float(
        INITIAL_CASH + pf.cash_flow(group_by=False).to_numpy(dtype=np.float64).sum()
    )
    trade_cash_reconcile = float(INITIAL_CASH + grouped.cash_pnl.sum())
    funding_total = float(flow_rows.loc[flow_rows.applied, "funding_cashflow_applied_usdt"].sum())
    if not np.isclose(order_cash_reconcile + funding_total, trade_cash_reconcile, rtol=1e-9, atol=0.01):
        raise ValueError("Per-child funding plus VectorBT orders do not reconcile to funded cash")
    return selected, grouped, flow_rows, {
        "orders": int(len(orders)),
        "entry_orders": int(len(entry)),
        "exit_orders": int(len(exit_)),
        "funding_rows_applied": int(flow_rows.applied.sum()),
        "funding_cashflow_usdt": funding_total,
        "cash_from_vectorbt_orders_only_usdt": order_cash_reconcile,
        "cash_from_orders_plus_funding_usdt": float(order_cash_reconcile + funding_total),
        "cash_from_child_trades_usdt": trade_cash_reconcile,
        "max_parent_notional_difference_usdt": float(
            (selected.groupby("event_id").entry_notional_usdt.sum()
             - selected.groupby("event_id").parent_notional_usdt.first()).abs().max()
        ) if "parent_notional_usdt" in selected else None,
    }


def _build_equity(pf, data: dict, scheduled: dict, config: dict) -> tuple[pd.DataFrame, dict]:
    actions = data["actions"]
    n_steps, n_cols = actions.shape
    if n_steps % 3:
        raise ValueError("Expected exactly three account stages per H1 bar")
    if (np.isnan(scheduled["nav_pre"]).any() or np.isnan(scheduled["nav_post"]).any()
            or np.isnan(scheduled["cash_pre"]).any() or np.isnan(scheduled["cash_post"]).any()
            or np.isnan(scheduled["pos_pre"]).any() or np.isnan(scheduled["pos_post"]).any()):
        raise ValueError("VectorBT did not execute every stage callback")
    order_flow_by_stage = pf.cash_flow(group_by=False).to_numpy(dtype=np.float64).sum(axis=1)
    preflow = scheduled["pre_cashflow"][0]
    postflow = scheduled["pre_cashflow"][1]
    cash_rebuilt = np.zeros(n_steps, dtype=np.float64)
    cash_nav = INITIAL_CASH
    for i in range(n_steps):
        cash_pre = cash_nav + preflow[i]
        if not np.isclose(cash_pre, scheduled["cash_pre"][i], rtol=1e-9, atol=0.01):
            raise ValueError(f"Pre-order cash mismatch at stage {i}: {cash_pre} vs {scheduled['cash_pre'][i]}")
        cash_nav = cash_pre + order_flow_by_stage[i] + postflow[i]
        cash_rebuilt[i] = cash_nav
        if not np.isclose(cash_nav, scheduled["cash_post"][i], rtol=1e-9, atol=0.01):
            raise ValueError(f"Post-order cash mismatch at stage {i}: {cash_nav} vs {scheduled['cash_post'][i]}")
        marks = data["marks"][i]
        nav_pre = cash_pre + float(np.dot(scheduled["pos_pre"][i], marks))
        nav_post = cash_nav + float(np.dot(scheduled["pos_post"][i], marks))
        if not np.isclose(nav_pre, scheduled["nav_pre"][i], rtol=1e-9, atol=0.02):
            raise ValueError(f"Pre-order NAV mismatch at stage {i}: {nav_pre} vs {scheduled['nav_pre'][i]}")
        if not np.isclose(nav_post, scheduled["nav_post"][i], rtol=1e-9, atol=0.02):
            raise ValueError(f"Post-order NAV mismatch at stage {i}: {nav_post} vs {scheduled['nav_post'][i]}")
    assets = pf.assets().to_numpy(dtype=np.float64)
    marks = data["marks"].astype(np.float64)
    nav_from_records = cash_rebuilt + (assets * marks).sum(axis=1)
    nav_from_callbacks = scheduled["nav_post"]
    if not np.allclose(nav_from_records, nav_from_callbacks, rtol=1e-9, atol=0.03):
        raise ValueError("Stage NAV reconstructed from VectorBT orders and funding differs from callbacks")
    raw_vbt_nav = np.asarray(pf.value(), dtype=np.float64).reshape(-1)
    if len(raw_vbt_nav) != n_steps:
        raise ValueError("VectorBT raw value has an unexpected stage count")
    cumulative_funding = np.cumsum(scheduled["pre_cashflow"].sum(axis=0))
    raw_error = nav_from_records - raw_vbt_nav
    if not np.allclose(raw_error, cumulative_funding, rtol=1e-8, atol=0.05):
        raise ValueError("Raw Portfolio.value omission does not equal cumulative external funding")
    hours = n_steps // 3
    nav = nav_from_records.reshape(hours, 3)
    pos = assets.reshape(hours, 3, n_cols)[:, 1, :]
    adverse, favourable = np.zeros(hours), np.zeros(hours)
    lower, upper = nav.min(axis=1).copy(), nav.max(axis=1).copy()
    flow_stage = scheduled["flows"].scheduled_stage.to_numpy(dtype=np.int64)
    applied_flow = scheduled["flow_applied"]
    flow_cash = scheduled["flow_cashflow"]
    for h in range(hours):
        stage = h * 3 + 1
        close_stage = h * 3 + 2
        q = pos[h]
        delta_hi = data["high"][h]
        delta_lo = data["low"][h]
        fav = np.where(q >= 0, delta_hi, delta_lo)
        adv = np.where(q >= 0, delta_lo, delta_hi)
        price_fav = float(np.dot(q, fav))
        price_adv = float(np.dot(q, adv))
        # Also include the H1 open value before and after all open orders and
        # the settlement cash posted at that open/interior stage.
        cash_after_orders_before_postfund = (
            scheduled["cash_pre"][stage] + order_flow_by_stage[stage]
        )
        open_asset = float(np.dot(q, marks[stage]))
        anchor_before = cash_after_orders_before_postfund + open_asset
        anchor_after = scheduled["nav_post"][stage]
        lower[h] = min(lower[h], anchor_before + price_adv, anchor_after + price_adv)
        upper[h] = max(upper[h], anchor_before + price_fav, anchor_after + price_fav)
        # Combine the open-position OHLC envelope with all funding adjustments
        # posted after open exits and by this H1 close. The sign-specific cash
        # extremes cover unknown within-hour ordering without claiming a path.
        hour_flows = flow_cash[applied_flow & (flow_stage >= stage) & (flow_stage <= close_stage)]
        negative_flow = float(np.minimum(hour_flows, 0.0).sum())
        positive_flow = float(np.maximum(hour_flows, 0.0).sum())
        lower[h] = min(lower[h], anchor_before + price_adv + negative_flow)
        upper[h] = max(upper[h], anchor_before + price_fav + positive_flow)
        adverse[h], favourable[h] = price_adv, price_fav
    idx = pd.to_datetime(START + HOUR * (np.arange(hours) + 1), unit="ms", utc=True)
    equity = pd.DataFrame(
        {
            "nav": nav[:, 2],
            "open_nav": scheduled["nav_pre"].reshape(hours, 3)[:, 0],
            "open_exit_nav": nav[:, 1],
            "lower_nav": lower,
            "upper_nav": upper,
            "accounting_funding_usdt": scheduled["pre_cashflow"].sum(axis=0).reshape(hours, 3).sum(axis=1),
        },
        index=idx,
    )
    audit = {
        "stage_rows": int(n_steps),
        "h1_rows": int(hours),
        "cash_reconstruction_max_abs_error_usdt": float(
            np.max(np.abs(cash_rebuilt - scheduled["cash_post"]))
        ),
        "nav_reconstruction_max_abs_error_usdt": float(
            np.max(np.abs(nav_from_records - nav_from_callbacks))
        ),
        "raw_vectorbt_value_excludes_external_funding": True,
        "raw_value_difference_matches_cumulative_funding": True,
        "hourly_ohlc_envelope_combines_post_open_funding_sign_extremes": True,
        "all_exit_positions_closed": bool(np.isclose(np.max(np.abs(assets[-1])), 0.0, atol=1e-12)),
    }
    if not audit["all_exit_positions_closed"]:
        raise ValueError("The frozen account ends with an open VectorBT position")
    return equity, audit


def _run_scenario(legs: pd.DataFrame, flows: pd.DataFrame, data: dict,
                  config: dict, scenario: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    scheduled = _schedule_flows(data, legs, flows, scenario)
    weights = np.asarray([2.0 / 3.0, 1.0 / 3.0], dtype=np.float64)
    reservation = np.zeros(1, dtype=np.float64)
    pf = vbt.Portfolio.from_order_func(
        data["marks"], _account_order, data["actions"], data["prices"], data["fees"],
        0.0, float(config["account"]["new_order_exposure_fraction"]), weights,
        reservation, 0, np.ones((1, 1), dtype=np.float64), scheduled["event_codes"],
        scheduled["exit_cache_quantity"], scheduled["exit_cache_event"],
        pre_segment_func_nb=_pre_segment,
        pre_segment_args=(
            scheduled["pre_ptr"], scheduled["pre_ids"], scheduled["flow_col"],
            scheduled["flow_code"], scheduled["flow_rate"], scheduled["flow_mark"],
            scheduled["flow_cache"], scheduled["flow_cashflow"], scheduled["flow_quantity"],
            scheduled["flow_applied"], scheduled["exit_cache_quantity"],
            scheduled["exit_cache_event"], scheduled["pre_cashflow"],
            scheduled["nav_pre"], scheduled["cash_pre"], scheduled["pos_pre"],
        ),
        post_segment_func_nb=_post_segment,
        post_segment_args=(
            scheduled["post_ptr"], scheduled["post_ids"], scheduled["flow_col"],
            scheduled["flow_code"], scheduled["flow_rate"], scheduled["flow_mark"],
            scheduled["flow_cache"], scheduled["flow_cashflow"], scheduled["flow_quantity"],
            scheduled["flow_applied"], scheduled["exit_cache_quantity"],
            scheduled["exit_cache_event"], scheduled["pre_cashflow"],
            scheduled["nav_post"], scheduled["cash_post"], scheduled["pos_post"],
        ),
        call_pre_segment=True, call_post_segment=True, cash_sharing=True, group_by=True,
        init_cash=float(config["account"]["initial_cash_usdt"]),
        max_orders=int(np.count_nonzero(data["actions"])) + 1,
        fill_pos_record=False,
    )
    funded_legs, grouped, flow_receipt, order_audit = _position_and_order_records(pf, data, scheduled, config)
    equity, equity_audit = _build_equity(pf, data, scheduled, config)
    funded_legs["funding_scenario"] = scenario
    grouped["funding_scenario"] = scenario
    period_limits = {
        "all": (None, None),
        "dev": (None, "2025-01-01T00:00Z"),
        "2025": ("2025-01-01T00:00Z", "2026-01-01T00:00Z"),
        "2026": ("2026-01-01T00:00Z", None),
    }
    metrics = {}
    for name, bounds in period_limits.items():
        low = equity.index[0] - pd.Timedelta(hours=1) if bounds[0] is None else pd.Timestamp(bounds[0])
        high = equity.index[-1] if bounds[1] is None else pd.Timestamp(bounds[1])
        if not ((equity.index > low) & (equity.index <= high)).any():
            metrics[name] = None
        else:
            metrics[name] = wallet.summarize(grouped, equity, *bounds)
    metrics_audit = {
        "scenario": scenario,
        "positions": int(len(grouped)),
        "child_legs": int(len(funded_legs)),
        "entry_fee_usdt": float(funded_legs.entry_fee_usdt.sum()),
        "exit_fee_usdt": float(funded_legs.exit_fee_usdt.sum()),
        "funding_cashflow_usdt": float(funded_legs.funding_cashflow_usdt.sum()),
        "proxy_funding_rows_applied": int(
            scheduled["flows"].loc[scheduled["flow_applied"]].mark_source.astype(str)
            .eq("binance_vision_mark_1h_open_proxy").sum()
        ),
        "exact_funding_rows_applied": int(
            scheduled["flows"].loc[scheduled["flow_applied"]].mark_source.astype(str)
            .eq("binance_fundingRate_markPrice").sum()
        ),
        "boundary_rows_applied": int(
            scheduled["flows"].loc[scheduled["flow_applied"], "relation"].isin(
                ["entry_boundary", "open_exit_boundary", "close_exit_boundary", "intrabar_exit_hour"]
            ).sum()
        ),
        "boundary_rows_excluded": int(scheduled["flows"].scenario_status.eq("boundary_excluded").sum()),
        "outside_hold_rows": int(scheduled["flows"].scenario_status.isin(["before_entry", "after_exit"]).sum()),
        "frozen_entry_priority": data["entry_priority_audit"],
        **order_audit,
        **equity_audit,
    }
    return funded_legs, grouped, equity, {
        "metrics": metrics, "audit": metrics_audit, "flows": flow_receipt,
    }


def _known_flow_only(flows: pd.DataFrame, legs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    joined = _attach_exact_leg_clock(flows, legs)
    finite_mark = np.isfinite(joined.mark_price.to_numpy(dtype=np.float64)) & joined.mark_price.gt(0).to_numpy()
    finite_rate = np.isfinite(joined.funding_rate.to_numpy(dtype=np.float64))
    valid_cashflow = joined.cashflow_known.to_numpy(dtype=bool) & finite_mark & finite_rate
    if (joined.cashflow_known.to_numpy(dtype=bool) & ~(finite_mark & finite_rate)).any():
        raise ValueError("Funding ledger marks a non-finite rate/mark row as known")
    known = joined.loc[valid_cashflow].copy()
    known["frozen_quantity_cashflow_usdt"] = (
        -known.signed_size.astype(float) * known.funding_rate.astype(float) * known.mark_price.astype(float)
    )
    known["known_only_interior_usdt"] = np.where(
        known.relation.eq("held_interior"), known.frozen_quantity_cashflow_usdt, 0.0
    )
    known["known_only_boundary_include_usdt"] = np.where(
        known.relation.isin(["entry_boundary", "open_exit_boundary", "close_exit_boundary", "intrabar_exit_hour"]),
        known.frozen_quantity_cashflow_usdt, 0.0,
    )
    return known, {
        "known_settlement_rows": int(len(known)),
        "unknown_settlement_rows_excluded": int(len(joined) - len(known)),
        "known_interior_cashflow_at_frozen_quantities_usdt": float(known.known_only_interior_usdt.sum()),
        "known_boundary_cashflow_at_frozen_quantities_usdt": float(known.known_only_boundary_include_usdt.sum()),
        "known_include_scenario_cashflow_at_frozen_quantities_usdt": float(
            known.known_only_interior_usdt.sum() + known.known_only_boundary_include_usdt.sum()
        ),
        "known_exclude_scenario_cashflow_at_frozen_quantities_usdt": float(
            known.known_only_interior_usdt.sum()
        ),
        "interpretation": "Known funding amounts on frozen quantities only; not NAV or dynamic replay.",
    }


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(temporary, index=False, compression=compression)
    temporary.replace(path)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def preflight(config_path: Path = CONFIG_PATH) -> dict:
    config = _read_json(config_path)
    _validate_config(config)
    legs, flows, source_audit, inputs_audit = _load_frozen_inputs(config)
    coverage = _coverage_status(flows, source_audit, inputs_audit)
    joined, known_only = _known_flow_only(flows, legs)
    result = {
        "vectorbt_version": vbt.__version__,
        "input_audit": inputs_audit,
        "funding_coverage": coverage,
        "known_frozen_quantity_flow_scenarios": known_only,
        "exact_timestamp_relations": joined.relation.value_counts().to_dict(),
        "cashflow_proxy_source_counts": joined.mark_source.value_counts(dropna=False).to_dict(),
        "preflight_made_network_request": False,
        "account_replay_started": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def _build_data(legs: pd.DataFrame, config: dict, *, start: int = START, end: int = END,
                quote_loader=None) -> dict:
    # The current funding file deliberately keeps only execution/accounting
    # fields. Recover the frozen, low-prior-ATR tie-breaker from the same local
    # native path pool used to build it; admitting only the already-funded
    # events with symbol-hash priority could alter same-hour size allocation.
    current_config = current._read_json(current.CONFIG_PATH)
    current._validate_config(current_config)
    extra, cohort_audit = current._current_contract_cohorts(current_config)
    native_paths = current.archive.pool(extra)
    legs, priority_audit = _attach_entry_priority(legs, native_paths)
    if quote_loader is None:
        quote_loader = _quote_loader_for_universe(extra)
    runner = legs.loc[legs.tp_r.eq(0.0)].copy()
    target = legs.loc[legs.tp_r.eq(2.0)].copy()
    if runner.event_id.duplicated().any() or target.event_id.duplicated().any():
        raise ValueError("Expected exactly one runner and target leg per event")
    data = scaleout.pair_data(
        runner, target, slots=int(config["account"]["slots"]),
        start=int(start), end=int(end), quote_loader=quote_loader,
    )
    selected = data["selected"]
    if len(selected) != len(legs) or set(selected.event_id.astype(str)) != set(legs.event_id.astype(str)):
        raise ValueError("VectorBT slot admission changed the frozen current event roster")
    if selected.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Prepared VectorBT child roster is not unique by event_id/tp_r")
    data["entry_priority_audit"] = {
        **priority_audit,
        "source_path_events_loaded": int(native_paths.event_id.nunique()),
        "source_contract_cohorts": cohort_audit,
        "archive_added_held_symbols": int(len(set(legs.symbol.astype(str) + "USDT") & extra)),
        "archive_added_quote_route": "cloud_close_archive_cohort.quotes(symbol)",
        "admission_order": "entry_ms, frozen entry_order, symbol CRC32, symbol, event_id",
    }
    return data


def _attach_entry_priority(legs: pd.DataFrame, native_paths: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Attach the frozen admission tie-breaker from local native paths by event_id."""
    required = {"event_id", "parent_id", "symbol", "side", "entry_ms", "entry_order", "is_control"}
    missing = required - set(native_paths.columns)
    if missing:
        raise ValueError(f"Native path source lacks frozen priority fields: {sorted(missing)}")
    selected_ids = set(legs.event_id.astype(str))
    control = _bool_column(native_paths.is_control, "native_paths.is_control")
    actual = native_paths.loc[
        (~control)
        & native_paths.event_id.astype(str).isin(selected_ids),
        ["event_id", "parent_id", "symbol", "side", "entry_ms", "entry_order"],
    ].copy()
    if actual.empty or set(actual.event_id.astype(str)) != selected_ids:
        missing_ids = sorted(selected_ids - set(actual.event_id.astype(str)))
        raise ValueError(f"Frozen actual positions lack native admission priority: {missing_ids[:8]}")
    if actual.entry_order.isna().any() or not np.isfinite(
        pd.to_numeric(actual.entry_order, errors="raise").to_numpy(dtype=np.float64)
    ).all():
        raise ValueError("Frozen native entry_order must be finite for every selected source path")
    varying = actual.groupby("event_id", sort=False).entry_order.nunique(dropna=False)
    if not varying.eq(1).all():
        raise ValueError("Native paired child paths disagree on entry_order")
    identity = actual.groupby("event_id", sort=False).agg(
        parent_id=("parent_id", "nunique"), symbol=("symbol", "nunique"),
        side=("side", "nunique"), entry_ms=("entry_ms", "nunique"),
    )
    if not identity.eq(1).all().all():
        raise ValueError("Native paired child paths disagree on the event identity or clock")
    priority = actual.groupby("event_id", sort=False).agg(
        parent_id=("parent_id", "first"), symbol=("symbol", "first"),
        side=("side", "first"), entry_ms=("entry_ms", "first"),
        entry_order=("entry_order", "first"),
    )
    mapped = legs.copy()
    mapped["event_id"] = mapped.event_id.astype(str)
    for col in ("parent_id", "symbol", "side", "entry_ms"):
        expected = mapped.event_id.map(priority[col])
        if not np.array_equal(mapped[col].astype(str).to_numpy(), expected.astype(str).to_numpy()):
            raise ValueError(f"Frozen account and native admission priority disagree on {col}")
    mapped["entry_order"] = mapped.event_id.map(priority.entry_order).astype(np.float64)
    if mapped.entry_order.isna().any() or not np.isfinite(mapped.entry_order.to_numpy()).all():
        raise ValueError("Entry priority join produced a missing or non-finite value")
    return mapped, {
        "priority_join_key": "event_id",
        "priority_source_field": "native_paths.entry_order (low-prior-ATR frozen path priority)",
        "frozen_position_events_joined": int(priority.shape[0]),
        "native_child_path_rows_joined": int(len(actual)),
        "priority_values_unique_by_event": bool(varying.eq(1).all()),
        "priority_events_not_found": 0,
    }


def _quote_loader_for_universe(extra_contracts: set[str]):
    """Route archive-added contracts to their registered H1 arrays."""
    def load(symbol: str):
        if symbol + "USDT" in extra_contracts:
            return current.archive.quotes(symbol)
        return binance.quotes(symbol)
    return load


def _assert_committed_for_run(config_path: Path) -> None:
    for path in (Path(__file__).resolve(), config_path.resolve(),
                 current.CONFIG_PATH.resolve(), Path(current.__file__).resolve()):
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError(f"Commit the account adapter/config and frozen current-input builder first: {relative}")


def run(config_path: Path = CONFIG_PATH) -> dict:
    _assert_committed_for_run(config_path)
    config = _read_json(config_path)
    _validate_config(config)
    output_dir = EXP / "current"
    receipt_path = output_dir / "account_funding_audit.json"
    if receipt_path.exists():
        raise FileExistsError(f"Funding-account completion receipt already exists: {receipt_path}")
    legs, flows, source_audit, inputs_audit = _load_frozen_inputs(config)
    coverage = _coverage_status(flows, source_audit, inputs_audit)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not coverage["complete_for_account_replay"]:
        known, known_audit = _known_flow_only(flows, legs)
        _write_frame(output_dir / "known_funding_only.csv.gz", known)
        result = {
            "id": config["id"], "status": "incomplete_funding_known_amounts_only",
            "vectorbt_version": vbt.__version__, "inputs": inputs_audit,
            "coverage": coverage, "known_frozen_quantity_scenarios": known_audit,
            "account_replay_started": False, "full_nav_metrics": None,
            "interpretation": "Incomplete rates/marks/month coverage; no complete NAV or dynamic-position result is reported.",
        }
        _write_json(receipt_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
        return result
    if not flows.cashflow_known.all() or flows.funding_rate.isna().any() or flows.mark_price.isna().any():
        raise ValueError("Funding receipt claims completeness but settlement rows contain unknown rates or marks")
    data = _build_data(legs, config)
    scenarios = {}
    try:
        for scenario in ("include_boundary", "exclude_boundary"):
            funded_legs, grouped, equity, output = _run_scenario(legs, flows, data, config, scenario)
            scenario_dir = output_dir / scenario
            _write_frame(scenario_dir / "funded_legs.csv.gz", funded_legs)
            _write_frame(scenario_dir / "funded_positions.csv.gz", grouped.reset_index(names="event_id"))
            _write_frame(scenario_dir / "hourly_equity.csv.gz", equity.reset_index(names="hour_end_utc"))
            _write_frame(scenario_dir / "applied_funding.csv.gz", output["flows"])
            metrics_frame = pd.DataFrame(
                [{"period": period, **values} for period, values in output["metrics"].items()]
            )
            _write_frame(scenario_dir / "account_metrics.csv", metrics_frame)
            scenarios[scenario] = {
                "metrics": output["metrics"], "audit": output["audit"],
                "outputs": {
                    "legs": str((scenario_dir / "funded_legs.csv.gz").relative_to(ROOT)),
                    "positions": str((scenario_dir / "funded_positions.csv.gz").relative_to(ROOT)),
                    "hourly_equity": str((scenario_dir / "hourly_equity.csv.gz").relative_to(ROOT)),
                    "applied_funding": str((scenario_dir / "applied_funding.csv.gz").relative_to(ROOT)),
                    "account_metrics": str((scenario_dir / "account_metrics.csv").relative_to(ROOT)),
                },
            }
    except Exception as exc:
        result = {
            "id": config["id"], "status": "account_replay_failed",
            "vectorbt_version": vbt.__version__, "inputs": inputs_audit,
            "coverage": coverage, "account_replay_started": True,
            "failure_type": type(exc).__name__, "failure": str(exc),
            "full_nav_metrics": None,
        }
        _write_json(receipt_path, result)
        raise
    result = {
        "id": config["id"], "status": "complete",
        "vectorbt_version": vbt.__version__, "inputs": inputs_audit,
        "coverage": coverage, "account_parameters": config["account"],
        "boundary_scenarios": scenarios,
        "drawdown_interpretation": "H1 open/close NAV and conservative OHLC high-low envelopes; not proof of intrabar event order.",
        "network_requests_started": False,
    }
    _write_json(receipt_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def _mini_check() -> None:
    """Exercise VectorBT orders, exact-ms funding order, exit cache, and stage NAV."""
    t0 = int(pd.Timestamp("2024-01-01T00:00:00Z").value // 1_000_000)
    end = t0 + 5 * HOUR
    clock = np.arange(t0, end, HOUR, dtype=np.int64)
    ohlc = np.array(
        [
            [100.0, 101.0, 99.0, 100.0],
            [100.0, 104.0, 99.0, 103.0],
            [103.0, 105.0, 101.0, 104.0],
            [104.0, 106.0, 100.0, 103.0],
            [103.0, 105.0, 101.0, 104.0],
        ], dtype=np.float64,
    )

    def mini_quotes(_symbol):
        return clock, ohlc

    events = pd.DataFrame([
        {"event_id": "a", "parent_id": "a", "symbol": "BTCUSDT", "side": 1,
         "entry_ms": t0, "exit_bar_ms": t0 + 3 * HOUR, "exit_phase": "intrabar",
         "entry_price": 100.0, "exit_price": 103.0, "exit_fee_rate": TAKER,
         "exit_reason": "ema_flip", "tp_r": 0.0, "is_control": False, "signed_size": 1.0},
        {"event_id": "a", "parent_id": "a", "symbol": "BTCUSDT", "side": 1,
         "entry_ms": t0, "exit_bar_ms": t0 + HOUR, "exit_phase": "open",
         "entry_price": 100.0, "exit_price": 103.0, "exit_fee_rate": MAKER,
         "exit_reason": "take_profit", "tp_r": 2.0, "is_control": False, "signed_size": 1.0},
        {"event_id": "b", "parent_id": "b", "symbol": "ETHUSDT", "side": 1,
         "entry_ms": t0 + 2 * HOUR, "exit_bar_ms": t0 + 4 * HOUR, "exit_phase": "close",
         "entry_price": 103.0, "exit_price": 104.0, "exit_fee_rate": TAKER,
         "exit_reason": "ema_flip", "tp_r": 0.0, "is_control": False, "signed_size": 1.0},
        {"event_id": "b", "parent_id": "b", "symbol": "ETHUSDT", "side": 1,
         "entry_ms": t0 + 2 * HOUR, "exit_bar_ms": t0 + 3 * HOUR, "exit_phase": "open",
         "entry_price": 103.0, "exit_price": 104.0, "exit_fee_rate": MAKER,
         "exit_reason": "take_profit", "tp_r": 2.0, "is_control": False, "signed_size": 1.0},
    ])
    runner = events[events.tp_r.eq(0.0)]
    target = events[events.tp_r.eq(2.0)]
    data = scaleout.pair_data(
        runner, target, slots=2, start=t0, end=end, quote_loader=mini_quotes,
    )
    data["entry_priority_audit"] = {
        "priority_join_key": "event_id", "priority_source_field": "synthetic mini fixture",
        "frozen_position_events_joined": int(data["selected"].event_id.nunique()),
        "native_child_path_rows_joined": int(len(data["selected"])),
        "priority_values_unique_by_event": True, "priority_events_not_found": 0,
    }
    base_rows = []
    for event_id, tp_r, funding_time, rate, relation in [
        ("a", 0.0, t0 - 1, 0.001, "before_entry"),
        ("a", 2.0, t0 - 1, 0.001, "before_entry"),
        ("a", 0.0, t0 + 5, 0.001, "held_interior"),
        ("a", 2.0, t0 + 5, 0.001, "held_interior"),
        ("a", 2.0, t0 + HOUR, 0.0005, "open_exit_boundary"),
        ("a", 0.0, t0 + 3 * HOUR + 30 * 60_000, 0.0007, "intrabar_exit_hour"),
        ("b", 0.0, t0 + 5 * HOUR, 0.0003, "close_exit_boundary"),
    ]:
        leg = events.loc[(events.event_id.eq(event_id)) & (events.tp_r.eq(tp_r))].iloc[0]
        row = {
            "event_id": event_id, "parent_id": event_id, "symbol": leg.symbol,
            "tp_r": float(tp_r), "funding_time_ms": int(funding_time),
            "funding_rate": float(rate), "mark_price": 100.0,
            "mark_source": "binance_fundingRate_markPrice",
            "boundary_ambiguous": relation != "held_interior",
            "cashflow_known": True,
        }
        base_rows.append(row)
    flows = pd.DataFrame(base_rows)
    cfg = {"account": {"slots": 2, "initial_cash_usdt": INITIAL_CASH,
                       "new_order_exposure_fraction": 0.9}}
    include_legs, include_pos, include_eq, include_output = _run_scenario(
        events, flows, data, cfg, "include_boundary",
    )
    exclude_legs, exclude_pos, exclude_eq, exclude_output = _run_scenario(
        events, flows, data, cfg, "exclude_boundary",
    )
    inc_flow = include_output["flows"]
    exc_flow = exclude_output["flows"]
    if (int(inc_flow.applied.sum()) != 5 or int(exc_flow.applied.sum()) != 2
            or not inc_flow.loc[inc_flow.relation.eq("before_entry"), "applied"].eq(False).all()):
        raise AssertionError("Exact-ms settlement inclusion/exclusion did not match the synthetic schedule")
    cached = inc_flow.loc[
        inc_flow.relation.isin(["open_exit_boundary", "intrabar_exit_hour", "close_exit_boundary"])
    ]
    if (len(cached) != 3 or not np.isfinite(cached.quantity_used).all()
            or cached.quantity_used.abs().le(0).any()):
        raise AssertionError("Just-exited funding rows did not use their cached pre-exit quantities")
    target_qty = include_legs.loc[include_legs.event_id.eq("a") & include_legs.tp_r.eq(2.0), "quantity_abs"].iloc[0]
    target_boundary = inc_flow.loc[inc_flow.relation.eq("open_exit_boundary")].iloc[0]
    if not np.isclose(target_boundary.funding_cashflow_applied_usdt, -target_qty * 0.0005 * 100.0, atol=1e-8):
        raise AssertionError("Open-exit funding did not use the actual frozen-at-exit VectorBT quantity")
    b_in = include_legs.loc[include_legs.event_id.eq("b")].quantity_abs.sum()
    b_ex = exclude_legs.loc[exclude_legs.event_id.eq("b")].quantity_abs.sum()
    if not b_in < b_ex:
        raise AssertionError("Included adverse funding did not reduce the subsequent child's funded size")
    for equity in (include_eq, exclude_eq):
        if not np.isfinite(equity[["nav", "open_nav", "open_exit_nav", "lower_nav", "upper_nav"]]).all().all():
            raise AssertionError("Synthetic stage NAV or conservative H1 bounds are non-finite")

    huge = flows.copy()
    huge.loc[(huge.event_id.eq("a")) & (huge.tp_r.eq(0.0))
             & (huge.funding_time_ms.eq(t0 + 5)), "funding_rate"] = 100.0
    failed = False
    try:
        _run_scenario(events, huge, data, cfg, "exclude_boundary")
    except ValueError as exc:
        failed = "Funding debit exceeds" in str(exc)
    except Exception as exc:
        failed = "Funding debit exceeds" in str(exc)
    if not failed:
        raise AssertionError("Funding cash shortfall did not fail closed")

    close_leg = events.loc[events.event_id.eq("b") & events.tp_r.eq(0.0)].iloc[0]
    close_row = pd.Series({"funding_time_ms": int(close_leg.exit_bar_ms) + HOUR})
    if _flow_relation(close_row, close_leg) != "close_exit_boundary":
        raise AssertionError("Close-phase settlement timestamp classification is wrong")
    priority_legs = pd.DataFrame([
        {"event_id": event, "parent_id": event, "symbol": symbol, "is_control": False,
         "side": 1, "entry_ms": t0, "entry_price": 100.0, "exit_bar_ms": t0 + HOUR,
         "exit_phase": "close", "exit_price": 101.0, "exit_fee_rate": TAKER,
         "exit_reason": "mini", "tp_r": tp}
        for event, symbol in (("priority_z", "ZZZ"), ("priority_a", "AAA"))
        for tp in (0.0, 2.0)
    ])
    priority_paths = priority_legs.copy()
    priority_paths["entry_order"] = priority_paths.event_id.map({"priority_z": 1.0, "priority_a": 2.0})
    prioritized, priority_audit = _attach_entry_priority(priority_legs, priority_paths)
    priority_data = scaleout.pair_data(
        prioritized[prioritized.tp_r.eq(0.0)], prioritized[prioritized.tp_r.eq(2.0)],
        slots=2, start=t0, end=t0 + 2 * HOUR, quote_loader=mini_quotes,
    )
    runner_slots = priority_data["selected"].loc[
        priority_data["selected"].tp_r.eq(0.0), ["event_id", "slot"]
    ].set_index("event_id").slot
    if int(runner_slots["priority_z"]) != 1 or int(runner_slots["priority_a"]) != 3:
        raise AssertionError("Native entry_order did not determine same-time frozen slot priority")
    if priority_audit["priority_events_not_found"] != 0:
        raise AssertionError("Native entry_order mini join did not preserve all selected events")
    for eq, output in ((include_eq, include_output), (exclude_eq, exclude_output)):
        expected_cash = output["audit"]["cash_from_orders_plus_funding_usdt"]
        if not np.isclose(eq.nav.iloc[-1], expected_cash, atol=0.05):
            raise AssertionError("Scenario terminal NAV does not reconcile to VectorBT orders plus funding")
    print(
        "VectorBT funding account mini-check: PASS "
        f"(include_NAV={include_eq.nav.iloc[-1]:.6f}, exclude_NAV={exclude_eq.nav.iloc[-1]:.6f}, "
        f"next_event_size={b_in:.8f}/{b_ex:.8f}, priority_slots={int(runner_slots['priority_z'])}/{int(runner_slots['priority_a'])}, rows={len(flows)})",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mini-check", action="store_true", help="Run in-memory VectorBT stage/funding fixtures")
    group.add_argument("--preflight", action="store_true", help="Read frozen local inputs and coverage only")
    group.add_argument("--run", action="store_true", help="Run complete include/exclude VectorBT funding scenarios")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    config_path = args.config.resolve()
    if args.mini_check:
        _mini_check()
    elif args.preflight:
        preflight(config_path)
    else:
        run(config_path)


if __name__ == "__main__":
    main()
