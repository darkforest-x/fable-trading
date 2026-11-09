"""Replay the frozen Binance hourly-trim account with settlement-time funding.

Funding is charged only to live native child quantities. All entries, original
child exits, and hourly reductions remain VectorBT orders. The two boundary
scenarios differ only in settlements coincident with entry/exit or whose
ownership is ambiguous inside an intrabar-exit hour.
"""
from __future__ import annotations

import argparse
import gc
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_funding_account as funding
from yoyo.evaluation import cloud_close_hourly_trim as hourly
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout

ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-hourly-trim-funding-20261109-v1"
CONFIG_PATH = EXP / "config.json"
INPUT_DIR = EXP
HOUR = wallet.HOUR
INITIAL_CASH = 1000.0
TAKER = wallet.TAKER
MAKER = 0.0002
CASH_TOL = 1e-7
TRIM_ACTION = 3


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _bool_column(series: pd.Series, name: str) -> pd.Series:
    if series.dtype == bool:
        return series
    mapped = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if mapped.isna().any():
        raise ValueError(f"Invalid boolean values in {name}")
    return mapped.astype(bool)


def _validate_config(config: dict) -> None:
    if config.get("id") != EXP.name or config.get("venue") != "Binance USD-M":
        raise ValueError("Hourly funding config identity or venue changed")
    account = config["account"]
    if (
        int(account["expected_positions"]) != 1536
        or int(account["expected_child_legs"]) != 3072
        or int(account["slots"]) != 9
        or not np.isclose(float(account["initial_cash_usdt"]), INITIAL_CASH)
        or not np.isclose(float(account["new_order_exposure_fraction"]), 0.9)
    ):
        raise ValueError("Frozen hourly-trim account sizing contract changed")
    if config["hourly_management"]["review_hours"] != 1 or not np.isclose(
        float(config["hourly_management"]["single_contract_weight_cap"]), 0.2
    ):
        raise ValueError("This builder only supports the frozen 1H / 20% trim rule")
    if config["boundary_policy"]["timestamp_resolution"] != "Exact source milliseconds; no timestamp tolerance":
        raise ValueError("Funding boundaries must use exact source milliseconds")
    expected = {
        "child_windows": "experiments/active/exp-cloud-close-hourly-trim-funding-20261109-v1/child_windows.csv.gz",
        "funding_tape": "experiments/active/exp-cloud-close-hourly-trim-funding-20261109-v1/funding_tape.csv.gz",
        "source_coverage": "experiments/active/exp-cloud-close-hourly-trim-funding-20261109-v1/source_coverage.json",
        "source_months": "experiments/active/exp-cloud-close-hourly-trim-funding-20261109-v1/source_months.csv.gz",
    }
    if any(config["inputs"].get(key) != value for key, value in expected.items()):
        raise ValueError("Funding replay must use the frozen source-builder outputs")
    if config["network_policy"] != "Offline account replay; use only the frozen source files and registered H1 quotes":
        raise ValueError("No network or funding source collection is permitted in this adapter")
    output_scope = config.get("outputs", {}).get("scope")
    if output_scope != f"experiments/active/{EXP.name}/account/":
        raise ValueError("Funding-aware hourly account outputs must remain isolated")


def _load_source_inputs(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    inputs = config["inputs"]
    child_path = ROOT / inputs["child_windows"]
    tape_path = ROOT / inputs["funding_tape"]
    audit_path = ROOT / inputs["source_coverage"]
    months_path = ROOT / inputs["source_months"]
    for path in (child_path, tape_path, audit_path, months_path):
        if not path.is_file():
            raise FileNotFoundError(f"Frozen hourly funding source is missing: {path}")
    children = pd.read_csv(child_path, float_precision="round_trip", dtype={
        "event_id": str, "parent_id": str, "symbol": str,
    })
    tape = pd.read_csv(tape_path, float_precision="round_trip", dtype={"symbol": str})
    source_audit = _read_json(audit_path)
    months = pd.read_csv(months_path, dtype={"symbol": str, "month": str})
    needed_children = {
        "event_id", "parent_id", "symbol", "side", "entry_ms", "entry_price",
        "entry_order", "exit_bar_ms", "exit_phase", "exit_price", "exit_reason",
        "exit_fee_rate", "tp_r", "slot",
    }
    needed_tape = {
        "symbol", "funding_time_ms", "funding_rate", "mark_price", "rate_source",
        "mark_source", "source_complete",
    }
    needed_months = {"symbol", "month", "range_complete", "missing_rate_rows", "missing_mark_rows"}
    if needed_children - set(children.columns) or needed_tape - set(tape.columns) or needed_months - set(months.columns):
        raise ValueError(
            "Frozen source schema changed: "
            f"children={sorted(needed_children-set(children.columns))}, "
            f"tape={sorted(needed_tape-set(tape.columns))}, "
            f"months={sorted(needed_months-set(months.columns))}"
        )
    for name in ("side", "entry_ms", "entry_order", "exit_bar_ms", "slot"):
        children[name] = pd.to_numeric(children[name], errors="raise").astype("int64")
    for name in ("entry_price", "exit_price", "exit_fee_rate", "tp_r"):
        children[name] = pd.to_numeric(children[name], errors="raise").astype(float)
    for name in ("funding_time_ms",):
        tape[name] = pd.to_numeric(tape[name], errors="raise").astype("int64")
    for name in ("funding_rate", "mark_price"):
        tape[name] = pd.to_numeric(tape[name], errors="coerce")
    children["exit_phase"] = children.exit_phase.astype(str)
    tape["source_complete"] = _bool_column(tape.source_complete, "funding_tape.source_complete")
    months["range_complete"] = _bool_column(months.range_complete, "source_months.range_complete")
    for name in ("missing_rate_rows", "missing_mark_rows"):
        months[name] = pd.to_numeric(months[name], errors="raise").astype("int64")
    if len(children) != 3072 or children.event_id.nunique() != 1536:
        raise ValueError("Expected exactly 1,536 frozen positions and 3,072 child windows")
    if children.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Frozen child windows duplicate event_id/tp_r")
    if not children.groupby("event_id").tp_r.apply(lambda x: set(x) == {0.0, 2.0}).all():
        raise ValueError("Each frozen parent must retain one native runner and one native 2R child")
    if not children.side.isin([-1, 1]).all() or not children.exit_phase.isin(["open", "intrabar", "close"]).all():
        raise ValueError("Invalid side or exit phase in frozen child windows")
    if (children.entry_ms > children.exit_bar_ms).any():
        raise ValueError("Frozen child window exits before entry")
    if not np.isfinite(children[["entry_price", "exit_price", "exit_fee_rate"]]).all().all():
        raise ValueError("Frozen child windows contain non-finite prices or fees")
    tp = children.tp_r.eq(2.0)
    if not np.array_equal(children.loc[tp, "exit_fee_rate"].to_numpy() < TAKER,
                          children.loc[tp, "exit_reason"].astype(str).str.startswith("take_profit").to_numpy()):
        raise ValueError("Frozen maker limit exits do not match their exit fee class")
    if tape.duplicated(["symbol", "funding_time_ms"]).any():
        raise ValueError("Funding tape is not unique by base symbol and settlement timestamp")
    if tape.symbol.isna().any() or not tape.source_complete.all():
        raise ValueError("Funding tape contains incomplete source rows")
    if tape.funding_rate.isna().any() or tape.mark_price.isna().any() or tape.mark_price.le(0).any():
        raise ValueError("Funding tape contains unknown rates or nonpositive settlement marks")
    mark_counts = tape.mark_source.astype(str).value_counts().to_dict()
    expected_marks = config["funding_source"]["expected_marks"]
    if mark_counts != expected_marks:
        raise ValueError(f"Funding mark-source counts differ from the frozen receipt: {mark_counts}")
    if tape.rate_source.isna().any() or tape.mark_source.isna().any():
        raise ValueError("Funding tape must preserve rate and mark source attribution")
    if months.duplicated(["symbol", "month"]).any() or not months.range_complete.all():
        raise ValueError("Source month coverage is incomplete or duplicated")
    if months.missing_rate_rows.sum() or months.missing_mark_rows.sum():
        raise ValueError("Source month receipt reports missing rates or settlement marks")
    expected_audit = {
        "complete": True,
        "positions": len(children) // 2,
        "legs": len(children),
        "symbols": int(children.symbol.nunique()),
        "symbol_months": len(months),
        "market_settlements": len(tape),
    }
    for key, value in expected_audit.items():
        if source_audit.get(key) != value:
            raise ValueError(f"Source coverage receipt mismatch for {key}: {source_audit.get(key)} != {value}")
    if set(tape.symbol.astype(str)) - set(children.symbol.astype(str)):
        raise ValueError("Funding tape contains symbols outside the actual child roster")
    months_tickers = set(months.symbol.astype(str))
    if {f"{s}USDT" for s in tape.symbol.astype(str)} - months_tickers:
        raise ValueError("Funding tape has rows outside the frozen source month receipt")
    return children, tape.sort_values(["symbol", "funding_time_ms"]).reset_index(drop=True), months, {
        "child_windows_file": str(child_path.relative_to(ROOT)),
        "funding_tape_file": str(tape_path.relative_to(ROOT)),
        "source_coverage_file": str(audit_path.relative_to(ROOT)),
        "source_months_file": str(months_path.relative_to(ROOT)),
        "positions": int(children.event_id.nunique()),
        "child_legs": int(len(children)),
        "symbols": int(children.symbol.nunique()),
        "symbol_month_windows": int(len(months)),
        "market_settlements": int(len(tape)),
        "exact_mark_rows": int(tape.mark_source.astype(str).eq("binance_fundingRate_markPrice").sum()),
        "h1_proxy_mark_rows": int(tape.mark_source.astype(str).eq("binance_vision_mark_1h_open_proxy").sum()),
        "range_coverage_complete": bool(months.range_complete.all()),
    }


def _hourly_input_data(children: pd.DataFrame, cap: float) -> tuple[dict, pd.DataFrame, dict]:
    """Load the frozen hourly-cap roster through its original native preparation path."""
    tested = hourly.tested
    archive = tested.admin.archive
    extra = set(_read_json(archive.EXP / "summary.json")["archived_additions"])
    extra.update(_read_json(tested.admin.study.EXP / "summary.json")["new_archive_cohorts"]["priced"])
    with archive.market_quotes(extra):
        data, frozen = tested.binance_inputs(extra)
        quote_loader = archive.native.quotes
        data = _expand_hourly(data, quote_loader, cap)
    if len(data["selected"]) != 3072 or data["selected"].event_id.nunique() != 1536:
        raise ValueError("Native hourly preparer changed the frozen child roster")
    if set(frozen.index.astype(str)) != set(children.event_id.astype(str)):
        raise ValueError("Source child windows and frozen hourly account parent IDs differ")
    selected = data["selected"].copy()
    if selected.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Native hourly account selected duplicate event_id/tp_r rows")
    source = children.set_index(["event_id", "tp_r"]).sort_index()
    native = selected.set_index(["event_id", "tp_r"]).sort_index()
    for col in ("parent_id", "symbol", "side", "entry_ms", "entry_price", "exit_bar_ms",
                "exit_phase", "exit_price", "exit_fee_rate", "slot"):
        left = native[col].astype(str).to_numpy()
        right = source[col].astype(str).to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f"Hourly source child windows differ from native prepared {col}")
    entry_order = native.entry_order.to_numpy(dtype=np.float64)
    if not np.array_equal(entry_order, source.entry_order.to_numpy(dtype=np.float64)):
        raise ValueError("Frozen entry priority differs from the native prepared path")
    data["funding_input_audit"] = {
        "native_preparer": "cloud_close_hourly_trim.tested.binance_inputs",
        "priority_source": "native frozen child_windows.entry_order",
        "parents_joined_by": "event_id",
        "children_joined_by": ["event_id", "tp_r"],
        "exact_child_clock_and_fee_match": True,
        "frozen_positions": int(len(frozen)),
        "native_children": int(len(selected)),
    }
    return data, frozen, {"archive_contract_additions": len(extra)}


def _expand_hourly(data: dict, quote_loader, cap: float) -> dict:
    base = data["actions"]
    if base.ndim != 2 or base.shape[0] % 3:
        raise ValueError("Native account arrays must have exactly three H1 stages")
    hours, cols = base.shape[0] // 3, base.shape[1]
    nlegs = 2
    eligible = np.zeros((hours, cols // nlegs), dtype=np.bool_)
    runners = data["selected"].loc[data["selected"].slot.mod(nlegs).eq(nlegs - 1)]
    if len(runners) != data["selected"].event_id.nunique() or not runners.tp_r.eq(0).all():
        raise ValueError("Last child of each slot pair must be the native runner")
    for row in runners.itertuples(index=False):
        clock = quote_loader(row.symbol)[0]
        i, j = clock.searchsorted([row.entry_ms, row.exit_bar_ms + HOUR])
        times = clock[i:j]
        next_bars = times[1:]
        present = next_bars[(next_bars - times[:-1] == HOUR) & (next_bars % HOUR == 0)]
        indices = (present - data["start"]) // HOUR
        if (indices < 0).any() or (indices >= hours).any():
            raise ValueError("Hourly review index lies outside the frozen account clock")
        eligible[indices, int(row.slot) // nlegs] = True

    def expand(name: str) -> np.ndarray:
        values = data[name].reshape(hours, 3, cols)[:, [0, 1, 1, 2], :]
        return values.reshape(-1, cols).copy()

    expanded = dict(data)
    for name in ("marks", "prices", "fees", "actions"):
        expanded[name] = expand(name)
    expanded["actions"][2::4] = 0
    expanded["eligible"] = eligible
    expanded["review_target"] = np.full(cols // nlegs, -1.0, dtype=np.float64)
    expanded["trim_fraction"] = np.zeros(cols // nlegs, dtype=np.float64)
    expanded["trim_cap"] = float(cap)
    expanded["nlegs"] = nlegs
    expanded["hours"] = hours
    return expanded


def _relation(ts: int, row: pd.Series) -> str:
    entry = int(row.entry_ms)
    exit_bar = int(row.exit_bar_ms)
    phase = str(row.exit_phase)
    if ts < entry:
        return "before_entry"
    if ts == entry:
        return "entry_boundary"
    if phase == "open":
        if ts < exit_bar:
            return "held_interior"
        if ts == exit_bar:
            return "open_exit_boundary"
        return "after_exit"
    if phase == "close":
        exit_time = exit_bar + HOUR
        if ts < exit_time:
            return "held_interior"
        if ts == exit_time:
            return "close_exit_boundary"
        return "after_exit"
    if ts <= exit_bar:
        return "held_interior"
    if ts < exit_bar + HOUR:
        return "intrabar_exit_hour"
    return "after_exit"


def _schedule_flows(data: dict, children: pd.DataFrame, tape: pd.DataFrame, scenario: str) -> dict:
    if scenario not in {"include_boundary", "exclude_boundary"}:
        raise ValueError(f"Unknown funding boundary scenario: {scenario}")
    selected = data["selected"].copy().reset_index(drop=True)
    if selected.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Selected children are not unique")
    selected["event_id"] = selected.event_id.astype(str)
    selected["tp_r"] = selected.tp_r.astype(float)
    selected_keys = set(zip(selected.event_id, selected.tp_r))
    child_index = children.set_index(["event_id", "tp_r"])
    code_map = {key: i for i, key in enumerate(sorted(selected_keys))}
    slots = {(str(r.event_id), float(r.tp_r)): int(r.slot) for r in selected.itertuples(index=False)}
    tape_by_symbol = {s: g for s, g in tape.groupby("symbol", sort=False)}
    flow_records: list[dict] = []
    for row in selected.itertuples(index=False):
        key = (str(row.event_id), float(row.tp_r))
        source_row = child_index.loc[key]
        symbol = str(source_row.symbol)
        source = tape_by_symbol.get(symbol)
        if source is None:
            raise ValueError(f"Missing funding tape for selected held contract {symbol}")
        exit_bound = int(source_row.exit_bar_ms)
        phase = str(source_row.exit_phase)
        if phase in {"close", "intrabar"}:
            exit_bound += HOUR
        stamps = source.funding_time_ms.to_numpy(dtype=np.int64)
        lo = int(source_row.entry_ms)
        left = int(np.searchsorted(stamps, lo, side="left"))
        right = int(np.searchsorted(stamps, exit_bound, side="right"))
        if right <= left:
            continue
        for event in source.iloc[left:right].itertuples(index=False):
            ts = int(event.funding_time_ms)
            relation = _relation(ts, source_row)
            if relation in {"before_entry", "after_exit"}:
                continue
            flow_records.append({
                "event_id": key[0], "parent_id": str(row.parent_id), "symbol": symbol,
                "tp_r": key[1], "side": int(row.side), "slot": slots[key],
                "funding_time_ms": ts, "funding_rate": float(event.funding_rate),
                "mark_price": float(event.mark_price), "rate_source": str(event.rate_source),
                "mark_source": str(event.mark_source), "relation": relation,
                "boundary_ambiguous": relation in {
                    "entry_boundary", "open_exit_boundary", "close_exit_boundary", "intrabar_exit_hour",
                },
            })
    flow_frame = pd.DataFrame(flow_records)
    if flow_frame.empty:
        raise ValueError("No funding settlements fall inside the frozen child holding windows")
    if flow_frame.duplicated(["event_id", "tp_r", "funding_time_ms"]).any():
        raise ValueError("Funding settlement duplicated inside a frozen child window")
    flow_frame["scheduled_stage"] = -1
    flow_frame["scheduled_hook"] = "none"
    flow_frame["use_exit_cache"] = False
    flow_frame["scenario_status"] = "held_interior"
    n_steps = int(data["actions"].shape[0])
    start = int(data["start"])
    pre_buckets: list[list[int]] = [[] for _ in range(n_steps)]
    post_buckets: list[list[int]] = [[] for _ in range(n_steps)]
    for idx, row in flow_frame.iterrows():
        relation = str(row.relation)
        boundary = bool(row.boundary_ambiguous)
        if boundary and scenario == "exclude_boundary":
            flow_frame.at[idx, "scenario_status"] = "boundary_excluded"
            continue
        ts = int(row.funding_time_ms)
        hour = (ts - start) // HOUR
        if relation == "entry_boundary":
            stage, hook, cache = int(hour * 4), 1, False
        elif relation == "open_exit_boundary":
            stage, hook, cache = int(hour * 4 + 1), 1, True
        elif relation == "intrabar_exit_hour":
            exit_hour = (int(child_index.loc[(str(row.event_id), float(row.tp_r))].exit_bar_ms) - start) // HOUR
            stage, hook, cache = int(exit_hour * 4 + 2), 1, False
        elif relation == "close_exit_boundary":
            exit_hour = (int(child_index.loc[(str(row.event_id), float(row.tp_r))].exit_bar_ms) - start) // HOUR
            stage, hook, cache = int(exit_hour * 4 + 3), 1, True
        elif ts % HOUR == 0:
            # Exact H1 settlement precedes that hour's open order sequence.
            if hour < 0 or hour >= n_steps // 4:
                raise ValueError(f"Funding timestamp lies outside the H1 account grid: {ts}")
            stage, hook, cache = int(hour * 4), 0, False
        else:
            # The hourly reduction is an open order. A settlement a few ms
            # after that open is posted after the reduction and before H1 exits.
            if hour < 0 or hour >= n_steps // 4:
                raise ValueError(f"Funding timestamp lies outside the H1 account grid: {ts}")
            stage, hook, cache = int(hour * 4 + 2), 1, False
        if not 0 <= stage < n_steps:
            raise ValueError(f"Funding stage lies outside the four-stage grid: {ts}")
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

    for bucket in pre_buckets + post_buckets:
        bucket.sort(key=lambda idx: int(flow_frame.at[idx, "funding_time_ms"]))
    pre_ptr, pre_ids = pack(pre_buckets)
    post_ptr, post_ids = pack(post_buckets)
    flow_slots = np.asarray([slots[(str(e), float(t))] for e, t in zip(flow_frame.event_id, flow_frame.tp_r)], dtype=np.int64)
    flow_codes = np.asarray([code_map[(str(e), float(t))] for e, t in zip(flow_frame.event_id, flow_frame.tp_r)], dtype=np.int32)
    event_codes = np.full(data["actions"].shape, -1, dtype=np.int32)
    for row in selected.itertuples(index=False):
        code = code_map[(str(row.event_id), float(row.tp_r))]
        entry_stage = (int(row.entry_ms) - start) // HOUR * 4
        exit_stage = ((int(row.exit_bar_ms) - start) // HOUR * 4
                      + (1 if str(row.exit_phase) == "open" else 3))
        event_codes[entry_stage, int(row.slot)] = code
        event_codes[exit_stage, int(row.slot)] = code
    cols = data["actions"].shape[1]
    return {
        "flows": flow_frame,
        "pre_ptr": pre_ptr, "pre_ids": pre_ids,
        "post_ptr": post_ptr, "post_ids": post_ids,
        "flow_col": flow_slots, "flow_code": flow_codes,
        "flow_rate": flow_frame.funding_rate.to_numpy(dtype=np.float64),
        "flow_mark": flow_frame.mark_price.to_numpy(dtype=np.float64),
        "flow_time": flow_frame.funding_time_ms.to_numpy(dtype=np.int64),
        "flow_cache": flow_frame.use_exit_cache.to_numpy(dtype=np.bool_),
        "event_codes": event_codes,
        "pre_cashflow": np.zeros((2, n_steps), dtype=np.float64),
        "cash_adjustment": np.zeros((2, n_steps), dtype=np.float64),
        "funding_cash_adjustment": np.zeros((2, n_steps), dtype=np.float64),
        "margin_funding_added": np.zeros((2, n_steps), dtype=np.float64),
        "margin_liability_repaid_by_funding": np.zeros((2, n_steps), dtype=np.float64),
        "margin_liability_repaid_by_release": np.zeros(n_steps, dtype=np.float64),
        "margin_liability": np.zeros(1, dtype=np.float64),
        "liability_stage": np.zeros((2, n_steps), dtype=np.float64),
        "flow_cashflow": np.zeros(len(flow_frame), dtype=np.float64),
        "flow_quantity": np.full(len(flow_frame), np.nan, dtype=np.float64),
        "flow_applied": np.zeros(len(flow_frame), dtype=np.bool_),
        "exit_cache_quantity": np.zeros(cols, dtype=np.float64),
        "exit_cache_event": np.full(cols, -1, dtype=np.int32),
        "nav_pre": np.full(n_steps, np.nan, dtype=np.float64),
        "nav_post": np.full(n_steps, np.nan, dtype=np.float64),
        "nav_order_post": np.full(n_steps, np.nan, dtype=np.float64),
        "cash_pre": np.full(n_steps, np.nan, dtype=np.float64),
        "cash_post": np.full(n_steps, np.nan, dtype=np.float64),
        "cash_order_post": np.full(n_steps, np.nan, dtype=np.float64),
        "free_pre": np.full(n_steps, np.nan, dtype=np.float64),
        "free_post": np.full(n_steps, np.nan, dtype=np.float64),
        "pos_pre": np.full((n_steps, cols), np.nan, dtype=np.float64),
        "pos_post": np.full((n_steps, cols), np.nan, dtype=np.float64),
        "pos_order_post": np.full((n_steps, cols), np.nan, dtype=np.float64),
        "liability_order_post": np.full(n_steps, np.nan, dtype=np.float64),
        "first_underflow": np.zeros(14, dtype=np.float64),
        "first_underflow_positions": np.zeros((cols, 2), dtype=np.float64),
        "trim_fee": TAKER,
    }


@njit(cache=True)
def _segment(c, eligible, cap, target, fraction, nlegs,
             pre_ptr, pre_ids, flow_col, flow_code, flow_rate, flow_mark, flow_ids, flow_time,
             flow_cache, flow_cashflow, flow_quantity, flow_applied, exit_cache_quantity,
             exit_cache_event, pre_cashflow, cash_adjustment, funding_cash_adjustment,
             margin_funding_added, margin_liability_repaid_by_funding, margin_liability,
             liability_stage, nav_pre, cash_pre, free_pre, pos_pre, first_underflow,
             first_underflow_positions):
    phase = c.i % 4
    if phase == 0:
        # Preserve the existing manager's prior-close target calculation.
        previous_nav = nb.get_group_value_ctx_nb(c) - margin_liability[c.group]
        for col in range(c.from_col, c.to_col, nlegs):
            target[col // nlegs] = -1.0
            if c.i > 0 and eligible[c.i // 4, col // nlegs]:
                previous_price = c.close[c.i - 1, col + nlegs - 1]
                quantity = np.abs(c.last_position[col:col + nlegs]).sum()
                if quantity * previous_price > previous_nav * cap:
                    target[col // nlegs] = previous_nav * cap / previous_price
        # Exact-hour settlements precede all current-open account orders.
        funding._apply_flows(
            c, pre_ptr[c.i], pre_ptr[c.i + 1], 0, flow_col, flow_code, flow_rate, flow_mark,
            flow_ids, flow_time, flow_cache, flow_cashflow, flow_quantity, flow_applied,
            exit_cache_quantity, exit_cache_event, pre_cashflow, cash_adjustment,
            funding_cash_adjustment, margin_funding_added, margin_liability_repaid_by_funding,
            margin_liability, first_underflow, first_underflow_positions,
        )
    if phase == 2:
        for col in range(c.from_col, c.to_col, nlegs):
            goal = target[col // nlegs]
            quantity = np.abs(c.last_position[col:col + nlegs]).sum()
            fraction[col // nlegs] = 1.0 - goal / quantity if 0.0 <= goal < quantity else 0.0
    nav = wallet.pre_segment(c)[0] - margin_liability[c.group]
    liability_stage[0, c.i] = margin_liability[c.group]
    nav_pre[c.i] = nb.get_group_value_ctx_nb(c) - margin_liability[c.group]
    cash_pre[c.i] = c.last_cash[c.group]
    free_pre[c.i] = c.last_free_cash[c.group]
    for col in range(c.from_col, c.to_col):
        pos_pre[c.i, col] = c.last_position[col]
    if nav_pre[c.i] <= 0.0:
        raise ValueError("Marked economic NAV is nonpositive before hourly order stage")
    return (nav,)


@njit(cache=True)
def _post_segment(c, post_ptr, post_ids, flow_col, flow_code, flow_rate, flow_mark,
                  flow_time, flow_cache, flow_cashflow, flow_quantity, flow_applied,
                  exit_cache_quantity, exit_cache_event, pre_cashflow, cash_adjustment,
                  funding_cash_adjustment, margin_funding_added,
                  margin_liability_repaid_by_funding, margin_liability,
                  margin_liability_repaid_by_release, liability_stage, nav_post,
                  cash_post, free_post, pos_post, nav_order_post, cash_order_post,
                  pos_order_post, liability_order_post, first_underflow, first_underflow_positions):
    # Capture the exact post-order/pre-settlement mark. This is the anchor for
    # hourly OHLC bounds so stage-two/three funding is included exactly once.
    nav_order_post[c.i] = nb.get_group_value_ctx_nb(c) - margin_liability[c.group]
    cash_order_post[c.i] = c.last_cash[c.group]
    liability_order_post[c.i] = margin_liability[c.group]
    for col in range(c.from_col, c.to_col):
        pos_order_post[c.i, col] = c.last_position[col]
    funding._apply_flows(
        c, post_ptr[c.i], post_ptr[c.i + 1], 1, flow_col, flow_code, flow_rate, flow_mark,
        post_ids, flow_time, flow_cache, flow_cashflow, flow_quantity, flow_applied,
        exit_cache_quantity, exit_cache_event, pre_cashflow, cash_adjustment,
        funding_cash_adjustment, margin_funding_added, margin_liability_repaid_by_funding,
        margin_liability, first_underflow, first_underflow_positions,
    )
    funding._settle_margin_liability(c, margin_liability, cash_adjustment,
                                     margin_liability_repaid_by_release)
    liability_stage[1, c.i] = margin_liability[c.group]
    nav_post[c.i] = nb.get_group_value_ctx_nb(c) - margin_liability[c.group]
    if nav_post[c.i] <= 0.0:
        raise ValueError("Marked economic NAV is nonpositive after hourly order stage")
    cash_post[c.i] = c.last_cash[c.group]
    free_post[c.i] = c.last_free_cash[c.group]
    for col in range(c.from_col, c.to_col):
        pos_post[c.i, col] = c.last_position[col]


@njit(cache=True)
def _order(c, nav, actions, prices, fees, exposure, weights, reservation, slot_weights,
           fraction, nlegs, trim_fee, event_codes, exit_cache_quantity, exit_cache_event):
    if c.i % 4 == 2:
        ratio = fraction[c.col // nlegs]
        if c.position_now == 0.0 or ratio <= 1.0e-10:
            return NoOrder
        return nb.order_nb(size=-c.position_now * ratio, price=c.close[c.i, c.col],
                           fees=trim_fee, lock_cash=True, allow_partial=False, raise_reject=True)
    action = actions[c.i, c.col]
    if action == 2:
        exit_cache_quantity[c.col] = c.position_now
        exit_cache_event[c.col] = event_codes[c.i, c.col]
    return wallet.account_order(c, nav, actions, prices, fees, 0.0, exposure, weights,
                                reservation, 0, slot_weights)


def _run_scenario(data: dict, flows: pd.DataFrame, config: dict, scenario: str):
    scheduled = _schedule_flows(data, data["selected"], flows, scenario)
    weights = np.asarray([2.0 / 3.0, 1.0 / 3.0], dtype=np.float64)
    reservation = np.zeros(1, dtype=np.float64)
    slot_weights = np.ones((1, 1), dtype=np.float64)
    max_orders = int(np.count_nonzero(data["actions"])) + int(
        data["nlegs"] * data["eligible"].sum()
    ) + 1
    pf = vbt.Portfolio.from_order_func(
        data["marks"], _order, data["actions"], data["prices"], data["fees"],
        float(config["account"]["new_order_exposure_fraction"]), weights, reservation,
        slot_weights, data["trim_fraction"], int(data["nlegs"]), float(scheduled["trim_fee"]),
        scheduled["event_codes"], scheduled["exit_cache_quantity"], scheduled["exit_cache_event"],
        pre_segment_func_nb=_segment,
        pre_segment_args=(
            data["eligible"], float(data["trim_cap"]), data["review_target"],
            data["trim_fraction"], int(data["nlegs"]), scheduled["pre_ptr"], scheduled["pre_ids"],
            scheduled["flow_col"], scheduled["flow_code"], scheduled["flow_rate"],
            scheduled["flow_mark"], scheduled["pre_ids"], scheduled["flow_time"],
            scheduled["flow_cache"], scheduled["flow_cashflow"], scheduled["flow_quantity"],
            scheduled["flow_applied"], scheduled["exit_cache_quantity"],
            scheduled["exit_cache_event"], scheduled["pre_cashflow"], scheduled["cash_adjustment"],
            scheduled["funding_cash_adjustment"], scheduled["margin_funding_added"],
            scheduled["margin_liability_repaid_by_funding"], scheduled["margin_liability"],
            scheduled["liability_stage"], scheduled["nav_pre"], scheduled["cash_pre"],
            scheduled["free_pre"], scheduled["pos_pre"], scheduled["first_underflow"],
            scheduled["first_underflow_positions"],
        ),
        post_segment_func_nb=_post_segment,
        post_segment_args=(
            scheduled["post_ptr"], scheduled["post_ids"], scheduled["flow_col"],
            scheduled["flow_code"], scheduled["flow_rate"], scheduled["flow_mark"],
            scheduled["flow_time"], scheduled["flow_cache"], scheduled["flow_cashflow"],
            scheduled["flow_quantity"], scheduled["flow_applied"], scheduled["exit_cache_quantity"],
            scheduled["exit_cache_event"], scheduled["pre_cashflow"], scheduled["cash_adjustment"],
            scheduled["funding_cash_adjustment"], scheduled["margin_funding_added"],
            scheduled["margin_liability_repaid_by_funding"], scheduled["margin_liability"],
            scheduled["margin_liability_repaid_by_release"], scheduled["liability_stage"],
            scheduled["nav_post"], scheduled["cash_post"], scheduled["free_post"],
            scheduled["pos_post"], scheduled["nav_order_post"], scheduled["cash_order_post"],
            scheduled["pos_order_post"], scheduled["liability_order_post"],
            scheduled["first_underflow"], scheduled["first_underflow_positions"],
        ),
        call_pre_segment=True, call_post_segment=True, cash_sharing=True, group_by=True,
        init_cash=float(config["account"]["initial_cash_usdt"]), max_orders=max_orders,
        fill_pos_record=False,
    )
    legs, parents, flows_out, order_audit = _position_records(pf, data, scheduled)
    equity, equity_audit = _build_equity(pf, data, scheduled)
    metrics = _period_summary(parents, equity)
    audit = {
        "scenario": scenario,
        "positions": int(len(parents)),
        "child_legs": int(len(legs)),
        "funding_rows_applied": int(flows_out.applied.sum()),
        "funding_cashflow_usdt": float(flows_out.loc[flows_out.applied, "funding_cashflow_usdt"].sum()),
        "proxy_funding_rows_applied": int(flows_out.loc[flows_out.applied, "mark_source"].astype(str)
                                           .eq("binance_vision_mark_1h_open_proxy").sum()),
        "exact_funding_rows_applied": int(flows_out.loc[flows_out.applied, "mark_source"].astype(str)
                                           .eq("binance_fundingRate_markPrice").sum()),
        "boundary_rows_applied": int(flows_out.loc[flows_out.applied, "boundary_ambiguous"].sum()),
        "boundary_rows_excluded": int(flows_out.scenario_status.eq("boundary_excluded").sum()),
        "trim_order_count": int(order_audit["trim_order_count"]),
        "trimmed_positions": int(order_audit["trimmed_positions"]),
        "trim_turnover_usdt": float(order_audit["trim_turnover_usdt"]),
        "cash_from_orders_and_funding_usdt": float(order_audit["cash_from_orders_and_funding_usdt"]),
        "terminal_margin_funding_liability_usdt": float(scheduled["margin_liability"][0]),
        "minimum_free_cash_usdt": float(min(scheduled["free_pre"].min(), scheduled["free_post"].min())),
        "frozen_entry_priority": data["funding_input_audit"],
        "orders_frame": order_audit["orders_frame"],
        **equity_audit,
    }
    if len(parents) != int(config["account"]["expected_positions"]):
        raise ValueError("Dynamic funding replay failed to fund every frozen parent")
    if audit["terminal_margin_funding_liability_usdt"] > CASH_TOL:
        raise ValueError("Closed hourly-trim account retained a funding liability")
    return legs, parents, equity, flows_out, {"metrics": metrics, "audit": audit}


def _position_records(pf, data: dict, scheduled: dict):
    selected = data["selected"].copy().reset_index(drop=True)
    orders = pf.orders.records.copy()
    if orders.empty:
        raise RuntimeError("VectorBT produced no fills for the frozen hourly account")
    start = int(data["start"])
    grouped_by_col = {int(slot): frame.sort_values("entry_ms") for slot, frame in selected.groupby("slot")}
    fills = orders.copy()
    event_ids, tp_values, hours, stages, actions, trim_flags = [], [], [], [], [], []
    for row in fills.itertuples(index=False):
        idx, col = int(row.idx), int(row.col)
        hour = start + (idx // 4) * HOUR
        stage = idx % 4
        frame = grouped_by_col.get(col)
        if frame is None:
            raise ValueError(f"Order uses unassigned slot {col}")
        candidates = frame.loc[frame.entry_ms <= hour]
        if candidates.empty:
            raise ValueError(f"VectorBT order precedes any child entry at hour {hour}, slot {col}")
        child = candidates.iloc[-1]
        if hour > int(child.exit_bar_ms) and not (stage == 3 and str(child.exit_phase) in {"close", "intrabar"}):
            raise ValueError("VectorBT fill falls outside its selected child window")
        is_trim = stage == 2
        action = TRIM_ACTION if is_trim else int(data["actions"][idx, col])
        event_ids.append(str(child.event_id)); tp_values.append(float(child.tp_r))
        hours.append(hour); stages.append(stage); actions.append(action); trim_flags.append(is_trim)
    fills["event_id"] = event_ids
    fills["tp_r"] = tp_values
    fills["hour_open_ms"] = hours
    fills["stage"] = stages
    fills["action"] = actions
    fills["is_trim"] = trim_flags
    fills["order_cashflow_usdt"] = np.where(
        fills.side.eq(0), -fills["size"] * fills.price, fills["size"] * fills.price,
    ) - fills.fees
    if fills.duplicated(["idx", "col"]).any():
        raise ValueError("VectorBT emitted duplicate order records at the same stage and slot")
    entry_stage = (selected.entry_ms.to_numpy(dtype=np.int64) - start) // HOUR * 4
    exit_stage = ((selected.exit_bar_ms.to_numpy(dtype=np.int64) - start) // HOUR * 4
                  + np.where(selected.exit_phase.astype(str).to_numpy() == "open", 1, 3))
    entry_records = fills.set_index(["idx", "col"]).reindex(
        pd.MultiIndex.from_arrays([entry_stage, selected.slot.to_numpy(dtype=np.int64)])
    )
    exit_records = fills.set_index(["idx", "col"]).reindex(
        pd.MultiIndex.from_arrays([exit_stage, selected.slot.to_numpy(dtype=np.int64)])
    )
    if entry_records["size"].isna().any() or exit_records["size"].isna().any():
        raise RuntimeError("Every frozen child must have one entry and one native exit order")
    trim_rows = fills.loc[fills.is_trim].copy()
    trim_sums = trim_rows.groupby(["event_id", "tp_r"]).agg(
        trim_size=("size", "sum"), trim_fee_usdt=("fees", "sum"),
        trim_turnover_usdt=("size", lambda x: 0.0),
        trim_cashflow_usdt=("order_cashflow_usdt", "sum"), trim_count=("size", "size"),
    )
    if len(trim_rows):
        trim_turn = (trim_rows["size"].abs() * trim_rows.price)
        trim_rows = trim_rows.assign(trim_turnover=trim_turn)
        trim_sums["trim_turnover_usdt"] = trim_rows.groupby(["event_id", "tp_r"]).trim_turnover.sum()
    per_child = selected.copy().set_index(["event_id", "tp_r"])
    per_child["entry_quantity"] = entry_records["size"].to_numpy(dtype=float)
    per_child["native_exit_quantity"] = exit_records["size"].to_numpy(dtype=float)
    per_child["entry_fill_price"] = entry_records.price.to_numpy(dtype=float)
    per_child["native_exit_fill_price"] = exit_records.price.to_numpy(dtype=float)
    per_child["entry_fee_usdt"] = entry_records.fees.to_numpy(dtype=float)
    per_child["native_exit_fee_usdt"] = exit_records.fees.to_numpy(dtype=float)
    per_child["entry_notional_usdt"] = per_child.entry_quantity.abs() * per_child.entry_fill_price
    per_child["order_cash_pnl_usdt"] = fills.groupby(["event_id", "tp_r"]).order_cashflow_usdt.sum()
    per_child["trim_size"] = trim_sums.trim_size.reindex(per_child.index).fillna(0.0)
    per_child["trim_fee_usdt"] = trim_sums.trim_fee_usdt.reindex(per_child.index).fillna(0.0)
    per_child["trim_turnover_usdt"] = trim_sums.trim_turnover_usdt.reindex(per_child.index).fillna(0.0)
    if not np.allclose(per_child.entry_quantity.abs(),
                       per_child.native_exit_quantity.abs() + per_child.trim_size.abs(),
                       rtol=1e-8, atol=1e-8):
        raise ValueError("Dynamic child position did not fully close after hourly reductions")
    flow_out = scheduled["flows"].copy()
    flow_out["funding_cashflow_usdt"] = scheduled["flow_cashflow"]
    flow_out["quantity_used"] = scheduled["flow_quantity"]
    flow_out["applied"] = scheduled["flow_applied"]
    funding_by_child = flow_out.loc[flow_out.applied].groupby(["event_id", "tp_r"]).funding_cashflow_usdt.sum()
    per_child["funding_cashflow_usdt"] = funding_by_child.reindex(per_child.index).fillna(0.0)
    per_child["cash_pnl_usdt"] = per_child.order_cash_pnl_usdt + per_child.funding_cashflow_usdt
    per_child["notional_usdt"] = per_child.entry_notional_usdt
    per_child["net_return"] = per_child.cash_pnl_usdt / per_child.notional_usdt
    per_child["exit_fee_usdt"] = per_child.native_exit_fee_usdt + per_child.trim_fee_usdt
    per_child = per_child.reset_index()
    parent = per_child.groupby("event_id", sort=False).agg(
        parent_id=("parent_id", "first"), symbol=("symbol", "first"), side=("side", "first"),
        entry_ms=("entry_ms", "first"), exit_bar_ms=("exit_bar_ms", "max"),
        order_cash_pnl_usdt=("order_cash_pnl_usdt", "sum"),
        funding_cashflow_usdt=("funding_cashflow_usdt", "sum"),
        cash_pnl=("cash_pnl_usdt", "sum"), notional=("notional_usdt", "sum"),
        trim_turnover_usdt=("trim_turnover_usdt", "sum"),
        trim_order_count=("trim_size", lambda x: int((x > 1e-12).sum())),
    )
    parent["net_return"] = parent.cash_pnl / parent.notional
    order_audit = {
        "order_count": int(len(fills)),
        "entry_orders": int(len(entry_records)),
        "native_exit_orders": int(len(exit_records)),
        "trim_order_count": int(fills.is_trim.sum()),
        "trimmed_positions": int(fills.loc[fills.is_trim, "event_id"].nunique()),
        "trim_turnover_usdt": float(trim_rows["trim_turnover"].sum()) if len(trim_rows) else 0.0,
        "entry_fee_usdt": float(per_child.entry_fee_usdt.sum()),
        "native_exit_fee_usdt": float(per_child.native_exit_fee_usdt.sum()),
        "trim_fee_usdt": float(per_child.trim_fee_usdt.sum()),
        "cash_from_orders_and_funding_usdt": float(INITIAL_CASH + parent.cash_pnl.sum()),
    }
    flow_out = flow_out.merge(
        per_child[["event_id", "tp_r", "symbol", "entry_ms", "exit_bar_ms", "exit_phase"]],
        on=["event_id", "tp_r", "symbol"], how="left", validate="many_to_one",
    )
    return per_child, parent, flow_out, order_audit | {"orders_frame": fills}


def _build_equity(pf, data: dict, scheduled: dict) -> tuple[pd.DataFrame, dict]:
    n_steps, n_cols = data["actions"].shape
    if n_steps % 4:
        raise ValueError("Hourly trim account must have four native stages per H1 bar")
    for key in ("nav_pre", "nav_post", "nav_order_post", "cash_pre", "cash_post", "cash_order_post",
                "free_pre", "free_post", "pos_pre", "pos_post", "pos_order_post", "liability_order_post"):
        if np.isnan(scheduled[key]).any():
            raise ValueError(f"VectorBT did not execute every funding-account callback: {key}")
    order_flow = pf.cash_flow(group_by=False).to_numpy(dtype=np.float64).sum(axis=1)
    cash_rebuilt = np.zeros(n_steps, dtype=np.float64)
    cash = INITIAL_CASH
    for i in range(n_steps):
        before_order = cash + scheduled["cash_adjustment"][0, i]
        if not np.isclose(before_order, scheduled["cash_pre"][i], rtol=1e-9, atol=0.02):
            raise ValueError(f"Hourly pre-order cash mismatch at stage {i}")
        cash = before_order + order_flow[i] + scheduled["cash_adjustment"][1, i]
        cash_rebuilt[i] = cash
        if not np.isclose(cash, scheduled["cash_post"][i], rtol=1e-9, atol=0.02):
            raise ValueError(f"Hourly post-order cash mismatch at stage {i}")
        marks = data["marks"][i]
        nav_pre = before_order + float(np.dot(scheduled["pos_pre"][i], marks)) - scheduled["liability_stage"][0, i]
        nav_post = cash + float(np.dot(scheduled["pos_post"][i], marks)) - scheduled["liability_stage"][1, i]
        if not np.isclose(nav_pre, scheduled["nav_pre"][i], rtol=1e-9, atol=0.03):
            raise ValueError(f"Hourly pre-order NAV mismatch at stage {i}")
        if not np.isclose(nav_post, scheduled["nav_post"][i], rtol=1e-9, atol=0.03):
            raise ValueError(f"Hourly post-order NAV mismatch at stage {i}")
    assets = np.asarray(pf.assets(), dtype=np.float64)
    nav_rows = cash_rebuilt + (assets * data["marks"]).sum(axis=1) - scheduled["liability_stage"][1]
    callbacks = scheduled["nav_post"]
    if not np.allclose(nav_rows, callbacks, rtol=1e-9, atol=0.04):
        raise ValueError("VectorBT fills, cashflows, liabilities and stage NAV do not reconcile")
    raw_nav = np.asarray(pf.value(), dtype=np.float64).reshape(-1)
    if len(raw_nav) != n_steps:
        raise ValueError("VectorBT hourly trim value has the wrong stage count")
    cumulative_funding = np.cumsum(scheduled["pre_cashflow"].sum(axis=0))
    if not np.allclose(nav_rows - raw_nav, cumulative_funding, rtol=1e-8, atol=0.05):
        raise ValueError("Raw VectorBT value difference is not explained by exact funding settlements")
    hours = n_steps // 4
    nav = nav_rows.reshape(hours, 4)
    order_nav = scheduled["nav_order_post"].reshape(hours, 4)
    pos_after_trim = scheduled["pos_order_post"].reshape(hours, 4, n_cols)[:, 2, :]
    low_delta = data["low"]
    high_delta = data["high"]
    lower = np.minimum(np.minimum(nav.min(axis=1), order_nav.min(axis=1)),
                       order_nav[:, 2] + (pos_after_trim * np.where(pos_after_trim >= 0, low_delta, high_delta)).sum(axis=1))
    upper = np.maximum(np.maximum(nav.max(axis=1), order_nav.max(axis=1)),
                       order_nav[:, 2] + (pos_after_trim * np.where(pos_after_trim >= 0, high_delta, low_delta)).sum(axis=1))
    flow_stage = scheduled["flows"].scheduled_stage.to_numpy(dtype=np.int64)
    applied = scheduled["flow_applied"]
    flow_cash = scheduled["flow_cashflow"]
    post_review = applied & (flow_stage >= 0) & np.isin(flow_stage % 4, np.array([2, 3], dtype=np.int64))
    hour_ids = flow_stage[post_review] // 4
    negative_by_hour = np.bincount(
        hour_ids, weights=np.minimum(flow_cash[post_review], 0.0), minlength=hours,
    )[:hours]
    positive_by_hour = np.bincount(
        hour_ids, weights=np.maximum(flow_cash[post_review], 0.0), minlength=hours,
    )[:hours]
    adverse = (pos_after_trim * np.where(pos_after_trim >= 0, low_delta, high_delta)).sum(axis=1)
    favorable = (pos_after_trim * np.where(pos_after_trim >= 0, high_delta, low_delta)).sum(axis=1)
    lower = np.minimum(lower, order_nav[:, 2] + adverse + negative_by_hour)
    upper = np.maximum(upper, order_nav[:, 2] + favorable + positive_by_hour)
    idx = pd.to_datetime(int(data["start"]) + HOUR * (np.arange(hours) + 1), unit="ms", utc=True)
    equity = pd.DataFrame({
        "nav": nav[:, 3], "open_nav": nav[:, 0], "open_exit_nav": nav[:, 1],
        "post_trim_nav": nav[:, 2], "post_trim_pre_funding_nav": order_nav[:, 2],
        "lower_nav": lower, "upper_nav": upper,
        "funding_cashflow_usdt": scheduled["pre_cashflow"].sum(axis=0).reshape(hours, 4).sum(axis=1),
        "cash_close_usdt": scheduled["cash_post"].reshape(hours, 4)[:, 3],
        "free_cash_close_usdt": scheduled["free_post"].reshape(hours, 4)[:, 3],
        "margin_funding_liability_close_usdt": scheduled["liability_stage"][1].reshape(hours, 4)[:, 3],
    }, index=idx)
    if not np.isclose(equity.nav.iloc[-1], INITIAL_CASH + (scheduled["flow_cashflow"] * scheduled["flow_applied"]).sum()
                      + float(pf.cash_flow(group_by=False).to_numpy(dtype=np.float64).sum()), rtol=1e-8, atol=0.05):
        raise ValueError("Terminal dynamic NAV differs from all VectorBT order and funding cashflows")
    if not np.isclose(np.max(np.abs(assets[-1])), 0.0, atol=1e-12):
        raise ValueError("The hourly-trim account ends with an open position")
    return equity, {
        "stage_rows": int(n_steps), "h1_rows": int(hours),
        "cash_reconstruction_max_abs_error_usdt": float(np.max(np.abs(cash_rebuilt - scheduled["cash_post"]))),
        "nav_reconstruction_max_abs_error_usdt": float(np.max(np.abs(nav_rows - callbacks))),
        "raw_value_difference_matches_settlements": True,
        "hourly_bounds_include_funding_cash_extremes": True,
        "all_positions_closed": True,
        "terminal_margin_funding_liability_usdt": float(scheduled["margin_liability"][0]),
    }


def _period_summary(parents: pd.DataFrame, equity: pd.DataFrame) -> dict:
    bounds = {
        "all": (None, None), "dev": (None, "2025-01-01T00:00Z"),
        "2025": ("2025-01-01T00:00Z", "2026-01-01T00:00Z"),
        "2026": ("2026-01-01T00:00Z", None),
    }
    result = {}
    for name, (lo, hi) in bounds.items():
        start = equity.index[0] - pd.Timedelta(hours=1) if lo is None else pd.Timestamp(lo)
        end = equity.index[-1] if hi is None else pd.Timestamp(hi)
        result[name] = wallet.summarize(parents, equity, lo, hi) if ((equity.index > start) & (equity.index <= end)).any() else None
    return result


def _assert_committed() -> None:
    paths = [Path(__file__).resolve(), CONFIG_PATH.resolve()]
    for path in paths:
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError(f"Commit the hourly funding adapter and config first: {relative}")


def _write_frame(path: Path, frame: pd.DataFrame, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(temporary, compression=compression, **kwargs)
    temporary.replace(path)


def preflight(config_path: Path = CONFIG_PATH) -> dict:
    config = _read_json(config_path)
    _validate_config(config)
    children, tape, months, source_audit = _load_source_inputs(config)
    cap = float(config["hourly_management"]["single_contract_weight_cap"])
    data, frozen, account_audit = _hourly_input_data(children, cap)
    expected_child_keys = set(zip(children.event_id.astype(str), children.tp_r.astype(float)))
    selected_child_keys = set(zip(data["selected"].event_id.astype(str), data["selected"].tp_r.astype(float)))
    if expected_child_keys != selected_child_keys:
        raise ValueError("Source child windows and native selected account child keys differ")
    result = {
        "vectorbt_version": vbt.__version__, "source_audit": source_audit,
        "account_audit": account_audit,
        "funding_source_counts": tape.mark_source.value_counts(dropna=False).to_dict(),
        "funding_rows_inside_frozen_child_windows": _count_window_rows(children, tape),
        "month_receipt_complete": bool(months.range_complete.all()),
        "frozen_parent_count": int(len(frozen)),
        "native_child_count": int(len(data["selected"])),
        "source_to_account_join_complete": True,
        "preflight_started_funding_account": False,
        "network_requests_started": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def _count_window_rows(children: pd.DataFrame, tape: pd.DataFrame) -> dict:
    by_symbol = {s: g.funding_time_ms.to_numpy(dtype=np.int64) for s, g in tape.groupby("symbol", sort=False)}
    included_boundary, total = 0, 0
    for row in children.itertuples(index=False):
        stamps = by_symbol[str(row.symbol)]
        end = int(row.exit_bar_ms) + (HOUR if str(row.exit_phase) in {"close", "intrabar"} else 0)
        left = int(np.searchsorted(stamps, int(row.entry_ms), side="left"))
        right = int(np.searchsorted(stamps, end, side="right"))
        for timestamp in stamps[left:right]:
            relation = _relation(int(timestamp), row)
            if relation in {"before_entry", "after_exit"}:
                continue
            total += 1
            if relation in {"entry_boundary", "open_exit_boundary", "close_exit_boundary", "intrabar_exit_hour"}:
                included_boundary += 1
    return {"child_settlement_rows": int(total), "boundary_sensitive_rows": int(included_boundary),
            "interior_rows": int(total - included_boundary)}


def _mini_check() -> dict:
    """Small VectorBT smoke for trims, exact stage ordering, and funding cash."""
    start = int(pd.Timestamp("2024-12-31T21:00:00Z").value // 1_000_000)
    clock = np.arange(start, start + 6 * HOUR, HOUR, dtype=np.int64)
    ohlc = {
        "AAA": np.asarray([
            [100, 101, 99, 100], [100, 200, 100, 200], [200, 205, 195, 200],
            [200, 210, 190, 200], [200, 210, 190, 200], [200, 210, 190, 200],
        ], dtype=np.float64),
        "BBB": np.asarray([
            [100, 101, 99, 100], [100, 110, 95, 100], [100, 110, 95, 100],
            [200, 400, 200, 400], [400, 410, 390, 400], [400, 410, 390, 400],
        ], dtype=np.float64),
        "CCC": np.asarray([
            [100, 101, 99, 100], [100, 110, 95, 100], [100, 110, 95, 100],
            [100, 110, 95, 100], [200, 210, 190, 200], [200, 210, 190, 200],
        ], dtype=np.float64),
    }

    def quote_loader(symbol: str):
        return clock, ohlc[symbol]

    events = pd.DataFrame([
        {"event_id": "a", "parent_id": "a", "symbol": "AAA", "side": 1,
         "entry_ms": start, "entry_order": 0, "entry_price": 100.0,
         "exit_bar_ms": start + 3 * HOUR, "exit_phase": "open", "exit_price": 200.0,
         "exit_fee_rate": TAKER, "exit_reason": "ema_flip", "tp_r": 0.0},
        {"event_id": "a", "parent_id": "a", "symbol": "AAA", "side": 1,
         "entry_ms": start, "entry_order": 0, "entry_price": 100.0,
         "exit_bar_ms": start + 2 * HOUR, "exit_phase": "open", "exit_price": 200.0,
         "exit_fee_rate": MAKER, "exit_reason": "take_profit", "tp_r": 2.0},
        {"event_id": "b", "parent_id": "b", "symbol": "BBB", "side": 1,
         "entry_ms": start + 3 * HOUR, "entry_order": 1, "entry_price": 200.0,
         "exit_bar_ms": start + 4 * HOUR, "exit_phase": "intrabar", "exit_price": 390.0,
         "exit_fee_rate": TAKER, "exit_reason": "ema_flip", "tp_r": 0.0},
        {"event_id": "b", "parent_id": "b", "symbol": "BBB", "side": 1,
         "entry_ms": start + 3 * HOUR, "entry_order": 1, "entry_price": 200.0,
         "exit_bar_ms": start + 4 * HOUR, "exit_phase": "open", "exit_price": 400.0,
         "exit_fee_rate": MAKER, "exit_reason": "take_profit", "tp_r": 2.0},
        {"event_id": "c", "parent_id": "c", "symbol": "CCC", "side": -1,
         "entry_ms": start + 4 * HOUR, "entry_order": 2, "entry_price": 200.0,
         "exit_bar_ms": start + 5 * HOUR, "exit_phase": "close", "exit_price": 200.0,
         "exit_fee_rate": TAKER, "exit_reason": "ema_flip", "tp_r": 0.0},
        {"event_id": "c", "parent_id": "c", "symbol": "CCC", "side": -1,
         "entry_ms": start + 4 * HOUR, "entry_order": 2, "entry_price": 200.0,
         "exit_bar_ms": start + 5 * HOUR, "exit_phase": "open", "exit_price": 200.0,
         "exit_fee_rate": MAKER, "exit_reason": "take_profit", "tp_r": 2.0},
    ])
    data = scaleout.pair_data(
        events[events.tp_r.eq(0.0)], events[events.tp_r.eq(2.0)], slots=2,
        start=start, end=start + 6 * HOUR, quote_loader=quote_loader,
    )
    data["funding_input_audit"] = {
        "priority_join_key": "event_id", "child_join_key": ["event_id", "tp_r"],
        "priority_source_field": "synthetic entry_order", "frozen_position_events_joined": 3,
        "native_child_path_rows_joined": 6, "priority_values_unique_by_event": True,
        "priority_events_not_found": 0,
    }
    data = _expand_hourly(data, quote_loader, 0.2)
    tape_rows = [
        ("AAA", start, 0.0002, 100.0),
        ("AAA", start + 2 * HOUR, 0.0003, 200.0),
        ("AAA", start + 2 * HOUR + 2, 0.0010, 200.0),
        ("AAA", start + 3 * HOUR, 0.0004, 200.0),
        ("BBB", start + 3 * HOUR, 0.0002, 200.0),
        ("BBB", start + 4 * HOUR, 0.0003, 400.0),
        ("BBB", start + 4 * HOUR + 30 * 60_000, 0.0010, 395.0),
        ("BBB", start + 5 * HOUR, 0.0005, 390.0),
        ("CCC", start + 4 * HOUR, -0.0002, 200.0),
        ("CCC", start + 5 * HOUR, 0.0003, 200.0),
        ("CCC", start + 6 * HOUR, 0.0004, 200.0),
    ]
    tape = pd.DataFrame(tape_rows, columns=["symbol", "funding_time_ms", "funding_rate", "mark_price"])
    tape["rate_source"] = "mini_exact_rate"
    tape["mark_source"] = "mini_exact_mark"
    tape["source_complete"] = True
    cfg = {"account": {"expected_positions": 3, "initial_cash_usdt": INITIAL_CASH,
                       "new_order_exposure_fraction": 0.9}}
    inc = _run_scenario(data, tape, cfg, "include_boundary")
    exc = _run_scenario(data, tape, cfg, "exclude_boundary")
    inc_legs, _, inc_eq, inc_flows, inc_result = inc
    exc_legs, _, exc_eq, exc_flows, exc_result = exc
    # Entry, open exit, and strict intrabar-hour rows at t4 coexist. The entry
    # posts after stage 0; the open exit posts after stage 1; the exact t4 start
    # on the intrabar leg is known-held before its eventual exit.
    b_open = inc_flows.loc[(inc_flows.event_id.eq("b")) & inc_flows.tp_r.eq(2.0)
                           & inc_flows.relation.eq("open_exit_boundary")].iloc[0]
    c_entry = inc_flows.loc[(inc_flows.event_id.eq("c")) & inc_flows.tp_r.eq(0.0)
                            & inc_flows.relation.eq("entry_boundary")].iloc[0]
    b_start = inc_flows.loc[(inc_flows.event_id.eq("b")) & inc_flows.tp_r.eq(0.0)
                            & inc_flows.funding_time_ms.eq(start + 4 * HOUR)].iloc[0]
    b_inside = inc_flows.loc[(inc_flows.event_id.eq("b")) & inc_flows.tp_r.eq(0.0)
                             & inc_flows.relation.eq("intrabar_exit_hour")].iloc[0]
    a_runner_entry_qty = float(inc_legs.loc[(inc_legs.event_id.eq("a")) & inc_legs.tp_r.eq(0.0), "entry_quantity"].iloc[0])
    a_runner_after_trim = inc_flows.loc[(inc_flows.event_id.eq("a")) & inc_flows.tp_r.eq(0.0)
                                        & inc_flows.funding_time_ms.eq(start + 2 * HOUR + 2)].iloc[0]
    a_target_open_exit = inc_flows.loc[(inc_flows.event_id.eq("a")) & inc_flows.tp_r.eq(2.0)
                                       & inc_flows.relation.eq("open_exit_boundary")].iloc[0]
    a_target_exit_qty = float(inc_legs.loc[(inc_legs.event_id.eq("a")) & inc_legs.tp_r.eq(2.0), "native_exit_quantity"].iloc[0])
    a_target_entry_qty = float(inc_legs.loc[(inc_legs.event_id.eq("a")) & inc_legs.tp_r.eq(2.0), "entry_quantity"].iloc[0])
    c_terminal_exit = inc_flows.loc[(inc_flows.event_id.eq("c")) & inc_flows.tp_r.eq(0.0)
                                    & inc_flows.relation.eq("close_exit_boundary")].iloc[0]
    if (int(c_entry.scheduled_stage) != 4 * 4 or int(b_open.scheduled_stage) != 4 * 4 + 1
            or c_entry.scheduled_hook != "post" or b_open.scheduled_hook != "post"):
        raise AssertionError("Same-time entry/open-exit boundary did not follow native four-stage order")
    if b_start.relation != "held_interior" or b_start.boundary_ambiguous or int(b_start.scheduled_stage) != 4 * 4:
        raise AssertionError("Intrabar start endpoint must be held before the intrabar exit")
    if b_inside.scheduled_hook != "post" or int(b_inside.scheduled_stage) != 4 * 4 + 2 or bool(b_inside.use_exit_cache):
        raise AssertionError("Strict intrabar-hour funding must post after reduction and before native exit")
    if (int(c_terminal_exit.scheduled_stage) != 5 * 4 + 3 or not bool(c_terminal_exit.use_exit_cache)):
        raise AssertionError("Terminal close boundary did not post after the actual native close exit")
    if len(inc_flows.loc[(inc_flows.event_id.eq("b")) & inc_flows.tp_r.eq(0.0)
                         & inc_flows.funding_time_ms.eq(start + 5 * HOUR)]) != 0:
        raise AssertionError("Exact next-open settlement after an intrabar exit was not excluded")
    if (not bool(a_target_open_exit.use_exit_cache)
            or not np.isclose(a_target_open_exit.quantity_used, a_target_exit_qty,
                              rtol=1e-8, atol=1e-10)
            or not a_target_exit_qty < a_target_entry_qty):
        raise AssertionError(
            "Open-exit boundary did not use its exact pre-exit native quantity: "
            f"cache={a_target_open_exit.use_exit_cache}, used={a_target_open_exit.quantity_used}, "
            f"native_exit={a_target_exit_qty}, entry={a_target_entry_qty}"
        )
    if not (abs(float(a_runner_after_trim.quantity_used)) < a_runner_entry_qty):
        raise AssertionError("Post-reduction funding did not use the reduced live runner quantity")
    if (inc_flows.applied.sum() <= exc_flows.applied.sum()
            or inc_result["metrics"]["all"]["final_usdt"] == exc_result["metrics"]["all"]["final_usdt"]):
        raise AssertionError("Boundary scenarios did not produce distinct settled cash accounts")
    if (not np.isfinite(inc_eq[["nav", "lower_nav", "upper_nav"]]).all().all()
            or not np.isfinite(exc_eq[["nav", "lower_nav", "upper_nav"]]).all().all()):
        raise AssertionError("Mini H1 NAV and high-low bounds are non-finite")
    if inc_result["audit"]["trim_order_count"] <= 0:
        raise AssertionError("Hourly weight-cap smoke did not execute a real VectorBT reduction")
    if (abs(inc_eq.nav.iloc[-1] - inc_result["audit"]["cash_from_orders_and_funding_usdt"]) >= 0.01
            or abs(exc_eq.nav.iloc[-1] - exc_result["audit"]["cash_from_orders_and_funding_usdt"]) >= 0.01):
        raise AssertionError("Order and settlement cash did not reconcile to terminal NAV within cents")
    result = {
        "vectorbt_version": vbt.__version__, "parents": 3, "child_legs": 6,
        "mini_funding_rows_include": int(inc_flows.applied.sum()),
        "mini_funding_rows_exclude": int(exc_flows.applied.sum()),
        "hourly_trim_orders": int(inc_result["audit"]["trim_order_count"]),
        "same_timestamp_c_entry_before_b_open_exit": True,
        "intrabar_exit_hour_start_and_end_chronology": True,
        "strict_in_hour_uses_post_trim_live_quantity": True,
        "terminal_cash_reconcile_abs_usdt": float(abs(inc_eq.nav.iloc[-1] - inc_result["audit"]["cash_from_orders_and_funding_usdt"])),
        "drawdown_bounds_are_finite": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def run(config_path: Path = CONFIG_PATH) -> dict:
    _assert_committed()
    config = _read_json(config_path)
    _validate_config(config)
    output_dir = ROOT / config["outputs"]["scope"].rstrip("/")
    receipt = output_dir / "funding_account_audit.json"
    if receipt.exists():
        raise FileExistsError(f"Hourly funding account output already exists: {receipt}")
    children, tape, months, source_audit = _load_source_inputs(config)
    data, frozen, account_audit = _hourly_input_data(
        children, float(config["hourly_management"]["single_contract_weight_cap"]),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    scenarios = {}
    for scenario in ("include_boundary", "exclude_boundary"):
        child, parent, equity, applied, result = _run_scenario(data, tape, config, scenario)
        child["funding_scenario"] = scenario
        parent["funding_scenario"] = scenario
        scenario_dir = output_dir / scenario
        scenario_dir.mkdir(parents=True, exist_ok=True)
        _write_frame(scenario_dir / "funded_legs.csv.gz", child, index=False)
        _write_frame(scenario_dir / "funded_positions.csv.gz", parent.reset_index(names="event_id"), index=False)
        _write_frame(scenario_dir / "hourly_equity.csv.gz", equity, index_label="hour_end_utc")
        _write_frame(scenario_dir / "applied_funding.csv.gz", applied, index=False)
        orders = result["audit"].pop("orders_frame")
        _write_frame(scenario_dir / "orders.csv.gz", orders, index=False)
        _write_frame(scenario_dir / "account_metrics.csv",
                     pd.DataFrame([{"period": p, **m} for p, m in result["metrics"].items()]), index=False)
        scenarios[scenario] = result
        del child, parent, equity, applied, orders
        gc.collect()
    final = {
        "id": config["id"], "status": "complete", "vectorbt_version": vbt.__version__,
        "source_coverage": source_audit, "account_input_audit": data["funding_input_audit"],
        "native_hourly_review": {"review_hours": 1, "cap": 0.2, "entry_exposure": 0.9,
                                 "positions": int(len(frozen)), "child_legs": int(len(children))},
        "boundary_scenarios": {k: {"metrics": v["metrics"], "audit": v["audit"]} for k, v in scenarios.items()},
        "drawdown_interpretation": "H1 stage NAV and conservative OHLC plus funding sign envelopes; not a proven intrabar path.",
        "funding_marks": "1,954 exact Binance fundingRate markPrice rows and 75,995 official H1 mark-open proxies remain separately counted.",
        "network_requests_started": False,
    }
    tmp = receipt.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(final, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(receipt)
    print(json.dumps(final, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--mini-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if sum((args.preflight, args.mini_check, args.run)) != 1:
        parser.error("choose exactly one of --preflight, --mini-check, or --run")
    if args.preflight:
        preflight(args.config)
    elif args.mini_check:
        _mini_check()
    else:
        run(args.config)


if __name__ == "__main__":
    main()
