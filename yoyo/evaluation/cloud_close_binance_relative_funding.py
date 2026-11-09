"""Replay the frozen Binance contrarian-half account with actual funding.

The account keeps the original four VectorBT child legs and 1x/2x exposure
map. Realized Binance funding is posted at its source millisecond timestamp;
the cash-sharing account can therefore change later order size. Exact clock
boundaries are replayed as include/exclude scenarios, and 5bp market slippage
is kept as a separate stress.
"""
from __future__ import annotations

import argparse
import gc
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_binance_relative_minutes as minute_source
from yoyo.evaluation import cloud_close_binance_relative_size as sizing
from yoyo.evaluation import cloud_close_double_exposure as four_leg
from yoyo.evaluation import cloud_close_funding_account as funding
from yoyo.evaluation import cloud_close_hourly_trim_funding as hourly_funding

ROOT = sizing.base.wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-binance-relative-funding-20261109-v1"
CONFIG_PATH = EXP / "config.json"
REFERENCE = ROOT / "experiments/active/exp-cloud-close-binance-relative-minutes-20261109-v1"
FUNDING_SOURCE = ROOT / "experiments/active/exp-cloud-close-fvg-priority-funding-20261109-v1"
HOUR = sizing.base.wallet.HOUR
INITIAL_CASH = 1000.0
TAKER = sizing.base.wallet.TAKER
MAKER = 0.0002
CAP = 0.20
WEIGHTS = np.asarray([1 / 3, 1 / 3, 1 / 6, 1 / 6], dtype=np.float64)
CHILD_LABELS = ("tp2_doubling", "tp2_native", "runner_doubling", "runner_native")
CASH_TOL = 1e-7


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _bool_column(series: pd.Series, name: str) -> pd.Series:
    if series.dtype == bool:
        return series
    values = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if values.isna().any():
        raise ValueError(f"Invalid boolean in {name}")
    return values.astype(bool)


def _validate_config(config: dict) -> None:
    if config.get("id") != EXP.name or config.get("venue") != "Binance USD-M":
        raise ValueError("Relative funding config identity or venue changed")
    account = config["account"]
    if (
        int(account["expected_positions"]) != 1586
        or int(account["expected_child_legs"]) != 6344
        or int(account["slots"]) != 9
        or not np.isclose(float(account["initial_cash_usdt"]), INITIAL_CASH)
        or not np.isclose(float(account["single_contract_weight_cap"]), CAP)
    ):
        raise ValueError("Frozen relative-size account sizing contract changed")
    if account["child_weights"] != [1 / 3, 1 / 3, 1 / 6, 1 / 6]:
        raise ValueError("Frozen four-child weights changed")
    expected_inputs = {
        "funded_parent_reference": "experiments/active/exp-cloud-close-binance-relative-minutes-20261109-v1/funded_contrarian_half.csv.gz",
        "hourly_reference": "experiments/active/exp-cloud-close-binance-relative-minutes-20261109-v1/hourly_contrarian_half.csv.gz",
        "minutes_receipt": "experiments/active/exp-cloud-close-binance-relative-minutes-20261109-v1/summary.json",
        "native_children": "experiments/active/exp-cloud-close-fvg-priority-funding-20261109-v1/child_windows.csv.gz",
        "funding_tape": "experiments/active/exp-cloud-close-fvg-priority-funding-20261109-v1/funding_tape.csv.gz",
        "source_months": "experiments/active/exp-cloud-close-fvg-priority-funding-20261109-v1/source_months.csv.gz",
        "source_coverage": "experiments/active/exp-cloud-close-fvg-priority-funding-20261109-v1/source_coverage.json",
    }
    if any(config["inputs"].get(key) != value for key, value in expected_inputs.items()):
        raise ValueError("Funding replay inputs differ from the frozen Binance artifacts")
    if config["network_policy"] != "Offline replay only; no funding collection, archive download, or cross-venue prices":
        raise ValueError("This account adapter is offline-only")
    if config["funding_source"]["mark_sources"] != {
        "binance_vision_mark_1h_open_proxy": 76844,
        "binance_fundingRate_markPrice": 1587,
    }:
        raise ValueError("Funding source mark counts differ from the frozen coverage receipt")
    expected_counts = {
        "tape_rows": 78431, "covered_positions": 1586, "source_child_rows": 3172,
        "covered_symbol_months": 2021, "required_four_child_symbol_months": 2003,
        "four_child_settlement_rows_scanned_in_source_intervals": 181714,
        "actual_four_child_settlement_rows_checked": 181393,
        "boundary_sensitive_four_child_rows_checked": 3376,
    }
    if any(int(config["funding_source"].get(key, -1)) != value for key, value in expected_counts.items()):
        raise ValueError("Funding source settlement coverage counts changed")


def _assert_committed() -> None:
    for path in (Path(__file__).resolve(), CONFIG_PATH.resolve()):
        relative = str(path.relative_to(ROOT))
        import subprocess

        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT,
                          check=False).returncode:
            raise RuntimeError(f"Commit the relative funding builder/config first: {relative}")


def _load_funding_inputs(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict, pd.DataFrame, pd.DataFrame]:
    paths = config["inputs"]
    tape_path, months_path = ROOT / paths["funding_tape"], ROOT / paths["source_months"]
    coverage_path = ROOT / paths["source_coverage"]
    parent_path, equity_path = ROOT / paths["funded_parent_reference"], ROOT / paths["hourly_reference"]
    for path in (tape_path, months_path, coverage_path, parent_path, equity_path):
        if not path.is_file():
            raise FileNotFoundError(f"Frozen relative funding input is missing: {path}")
    tape = pd.read_csv(tape_path, dtype={"symbol": str}, float_precision="round_trip")
    months = pd.read_csv(months_path, dtype={"symbol": str, "month": str})
    coverage = _json(coverage_path)
    parents = pd.read_csv(parent_path, dtype={"event_id": str, "parent_id": str, "symbol": str},
                          float_precision="round_trip")
    equity = pd.read_csv(equity_path, index_col=0, parse_dates=True, float_precision="round_trip")
    equity.index = pd.to_datetime(equity.index, utc=True)
    needed_tape = {"symbol", "funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete"}
    needed_months = {"symbol", "month", "query_start_ms", "query_end_exclusive_ms", "range_complete",
                     "missing_rate_rows", "missing_mark_rows", "funding_settlement_rows_in_query_window"}
    needed_parents = {"event_id", "parent_id", "symbol", "side", "entry_ms", "exit_bar_ms",
                      "notional", "cash_pnl", "net_return", "exposure"}
    if needed_tape - set(tape) or needed_months - set(months) or needed_parents - set(parents):
        raise ValueError("Frozen funding or relative-account schema changed")
    months["range_complete"] = _bool_column(months.range_complete, "source_months.range_complete")
    tape["source_complete"] = _bool_column(tape.source_complete, "funding_tape.source_complete")
    if len(parents) != 1586 or parents.event_id.nunique() != 1586 or parents.event_id.duplicated().any():
        raise ValueError("Frozen contrarian_half parent roster is not unique at 1,586")
    if not np.isclose(1000.0 + parents.cash_pnl.sum(), float(equity.nav.iloc[-1]), atol=0.02, rtol=1e-9):
        raise ValueError("Frozen relative account parent cash does not reconcile to its H1 NAV")
    if not coverage.get("complete") or coverage.get("positions") != 1586 or coverage.get("legs") != 3172:
        raise ValueError("Donor funding receipt is incomplete or does not cover the same V1.7 roster")
    if len(tape) != int(coverage["market_settlements"]):
        raise ValueError("Funding tape row count differs from the frozen source coverage receipt")
    if tape.duplicated(["symbol", "funding_time_ms"]).any() or not tape.source_complete.all():
        raise ValueError("Funding tape is duplicated or has incomplete source rows")
    if tape.funding_rate.isna().any() or tape.mark_price.isna().any() or tape.mark_price.le(0).any():
        raise ValueError("Funding tape has unknown rate or mark values")
    if not months.range_complete.all() or months.missing_rate_rows.sum() or months.missing_mark_rows.sum():
        raise ValueError("Funding source-month receipt reports an incomplete rate or mark interval")
    if months.duplicated(["symbol", "month"]).any():
        raise ValueError("Funding source-month receipt contains duplicate symbol-months")
    return tape, months, coverage, parents, equity


def _prepare_account(reference: pd.DataFrame) -> tuple[dict, tuple, object, pd.Series, dict]:
    archive = sizing.archive
    bundle = sizing.bundle
    extra = set(_json(archive.EXP / "summary.json")["archived_additions"])
    extra.update(_json(bundle.admin.study.EXP / "summary.json")["new_archive_cohorts"]["priced"])
    with archive.market_quotes(extra):
        rows, loader, _ = bundle.paths("Binance")
        quotes = lru_cache(maxsize=None)(loader)
        data, doubled = sizing.make_account(rows, quotes)
        market = sizing.base.underlying(data, quotes)
        runners = market[-1].set_index("event_id")
        state = runners.entry_ms.map(sizing.relative(quotes))
        sign = state * runners.side
        multipliers = pd.Series(1.5 - 0.5 * sign, index=runners.index, name="exposure")
        if multipliers.isna().any() or not multipliers.isin([1.0, 2.0]).all():
            raise ValueError("Relative ETH/BTC exposure is not fully observed at frozen entries")
        saved = reference.set_index("event_id").exposure.sort_index()
        actual = multipliers.reindex(saved.index).sort_index()
        if actual.isna().any() or not np.array_equal(actual.to_numpy(), saved.to_numpy()):
            raise ValueError("Rebuilt causal ETH/BTC 1x/2x exposure map differs from frozen output")
        if len(runners) != 1586 or data["selected"].event_id.nunique() != 1586 or len(data["selected"]) != 6344:
            raise ValueError("Rebuilt four-child V1.7 roster changed")
        child_counts = data["selected"].groupby("event_id").size()
        if not child_counts.eq(4).all() or data["selected"].duplicated(["event_id", "slot"]).any():
            raise ValueError("Every frozen parent must have four unique child slots")
        # Minute-source review is the frozen tradability gate for the two 2x stops.
        minute_audit = _json(minute_source.EXP / "summary.json")["arms"]["contrarian_half"]["margin"]
        one = minute_audit["1.0"]
        two = minute_audit["2.0"]
        if one["maintenance_boundary_hits"] != 0 or not two["all_flags_resolved"]:
            raise ValueError("Frozen Binance stop-hour margin evidence is not resolved")
        if any(row.get("status") != "stop_strictly_earlier" for row in two.get("checked_hours", [])):
            raise ValueError("A 2x flagged stop was not proven strictly earlier by Binance minutes")
        if len(two.get("checked_hours", [])) != 2:
            raise ValueError("Expected the two frozen 2x minute-resolved stop flags")
        data["expected_positions"] = 1586
        data["expected_child_legs"] = 6344
        audit = {
            "positions": len(runners), "four_child_rows": len(data["selected"]),
            "exposure_counts": {str(k): int(v) for k, v in multipliers.value_counts().sort_index().items()},
            "double_limit_child_exits": len(doubled), "parent_roster_matches_reference": True,
            "exposure_map_matches_reference": True,
            "minute_margin_flags": {"1x": int(one["maintenance_boundary_hits"]),
                                    "2x": int(two["maintenance_boundary_hits"])},
            "minute_resolved_2x_stops": [{"event_id": x["event_id"], "status": x["status"]}
                                          for x in two.get("checked_hours", [])],
        }
        return data, market, quotes, multipliers, audit


def _scaled_market(data: dict, market: tuple, multipliers: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    native_open, previous_close, directions, runners = market
    hours = len(data["actions"]) // 3
    ratio_grid = np.zeros((hours, native_open.shape[1]), dtype=np.float64)
    for row in runners.itertuples(index=False):
        first = (int(row.entry_ms) - int(data["start"])) // HOUR
        last = (int(row.exit_bar_ms) - int(data["start"])) // HOUR
        if not 0 <= first <= last < hours:
            raise ValueError(f"Frozen exposure is outside the H1 grid: {row.event_id}")
        ratio_grid[first:last + 1, int(row.slot) // 4] = float(multipliers.at[str(row.event_id)])
    return native_open * ratio_grid, previous_close * ratio_grid, directions.copy()


def _check_covered_windows(selected: pd.DataFrame, tape: pd.DataFrame, months: pd.DataFrame) -> tuple[dict, dict]:
    no_rows = hourly_funding.verified_empty_windows(months)
    tape_by_symbol = {str(sym): grp.funding_time_ms.to_numpy(dtype=np.int64)
                      for sym, grp in tape.groupby("symbol", sort=False)}
    month_by_symbol = {str(sym).removesuffix("USDT"): grp.set_index("month")
                       for sym, grp in months.groupby("symbol", sort=False)}
    uncovered_months: list[dict] = []
    uncovered_empty: list[dict] = []
    required_month_keys: set[tuple[str, str]] = set()
    scanned_rows = 0
    held_rows = 0
    boundary_rows = 0
    for row in selected.itertuples(index=False):
        start = int(row.entry_ms)
        end = int(row.exit_bar_ms) + (HOUR if str(row.exit_phase) in {"close", "intrabar"} else 0)
        symbol = str(row.symbol)
        start_ts = pd.Timestamp(start, unit="ms", tz="UTC")
        end_ts = pd.Timestamp(end, unit="ms", tz="UTC")
        month = start_ts.replace(day=1).normalize()
        end_month = end_ts.replace(day=1).normalize()
        while month <= end_month:
            month_text = month.strftime("%Y-%m")
            required_month_keys.add((symbol, month_text))
            group = month_by_symbol.get(symbol)
            if group is None or month_text not in group.index:
                uncovered_months.append({"event_id": row.event_id, "symbol": symbol, "month": month_text})
            else:
                receipt = group.loc[month_text]
                if not bool(receipt.range_complete) or int(receipt.missing_rate_rows) or int(receipt.missing_mark_rows):
                    uncovered_months.append({"event_id": row.event_id, "symbol": symbol, "month": month_text})
                covered_left = max(start, int(month.timestamp() * 1000))
                next_month = (month + pd.offsets.MonthBegin(1)).normalize()
                covered_right = min(end + 1, int(next_month.timestamp() * 1000))
                if int(receipt.query_start_ms) > covered_left or int(receipt.query_end_exclusive_ms) < covered_right:
                    uncovered_months.append({"event_id": row.event_id, "symbol": symbol,
                                             "month": month_text, "reason": "query_interval_not_contiguous"})
            month = (month + pd.offsets.MonthBegin(1)).normalize()
        stamps = tape_by_symbol.get(symbol)
        if stamps is None:
            if not any(left <= start and end < right for left, right in no_rows.get(symbol, [])):
                uncovered_empty.append({"event_id": row.event_id, "symbol": symbol,
                                        "entry_ms": start, "exit_bound_ms": end})
        else:
            left = int(np.searchsorted(stamps, start, side="left"))
            right = int(np.searchsorted(stamps, end, side="right"))
            window = stamps[left:right]
            scanned_rows += len(window)
            for timestamp in window:
                relation = hourly_funding._relation(int(timestamp), row)
                if relation in {"before_entry", "after_exit"}:
                    continue
                held_rows += 1
                boundary_rows += int(relation in {
                    "entry_boundary", "open_exit_boundary", "close_exit_boundary", "intrabar_exit_hour",
                })
    if uncovered_months or uncovered_empty:
        raise ValueError(f"Funding coverage is not proven: months={len(uncovered_months)}, empty={len(uncovered_empty)}")
    return no_rows, {
        "covered_child_legs": len(selected), "covered_symbol_months": len(required_month_keys),
        "settlement_rows_scanned_in_interval": int(scanned_rows),
        "settlement_rows_inside_native_child_hold": int(held_rows),
        "boundary_sensitive_child_settlement_rows": int(boundary_rows),
        "uncovered_symbol_months": 0, "unproven_zero_settlement_windows": 0,
        "verified_zero_settlement_symbols": len(no_rows),
        "verified_zero_settlement_spans": int(sum(map(len, no_rows.values()))),
        "funding_tape_has_no_SC_rows": "SC" not in tape.symbol.unique(),
        "source_interval_check": "every four-child hold is contained in a complete donor symbol-month query interval",
    }


def _make_model(data: dict, multipliers: pd.Series, slip: float) -> dict:
    model = dict(data)
    model["actions"] = data["actions"].copy()
    selected = data["selected"].copy()
    event_multiple = selected.event_id.map(multipliers)
    if event_multiple.isna().any():
        raise ValueError("A child cannot join to the frozen parent exposure map")
    for multiple in sorted(multipliers.unique()):
        model["selected"] = selected.loc[event_multiple.eq(multiple)].copy()
        model, _ = four_leg.study.leverage._syntheticize(model, float(multiple), float(slip))
    model["selected"] = selected
    return model


def _expand_four_stage(model: dict, selected: pd.DataFrame) -> dict:
    rows, cols = model["actions"].shape
    if rows % 3 or cols % 4:
        raise ValueError("Four-child account inputs need three stages and a multiple of four slots")
    hours = rows // 3

    def expand(name: str) -> np.ndarray:
        return model[name].reshape(hours, 3, cols)[:, [0, 1, 1, 2], :].reshape(-1, cols).copy()

    expanded = dict(model)
    for field in ("marks", "prices", "fees", "actions"):
        expanded[field] = expand(field)
    expanded["actions"][2::4] = 0
    expanded["hours"] = hours
    expanded["nlegs"] = 4
    expanded["selected"] = selected.copy()
    return expanded


def _empty_schedule(data: dict, selected: pd.DataFrame) -> dict:
    """Zero-funding schedule with the same four-stage VBT callbacks."""
    n_steps, cols = data["actions"].shape
    keys = sorted(set(zip(selected.event_id.astype(str), selected.tp_r.astype(float))))
    code_map = {key: i for i, key in enumerate(keys)}
    event_codes = np.full((n_steps, cols), -1, dtype=np.int32)
    start = int(data["start"])
    for row in selected.itertuples(index=False):
        code = code_map[(str(row.event_id), float(row.tp_r))]
        entry_stage = (int(row.entry_ms) - start) // HOUR * 4
        exit_stage = (int(row.exit_bar_ms) - start) // HOUR * 4 + (1 if str(row.exit_phase) == "open" else 3)
        event_codes[entry_stage, int(row.slot)] = code
        event_codes[exit_stage, int(row.slot)] = code
    empty_int = np.empty(0, dtype=np.int64)
    empty_float = np.empty(0, dtype=np.float64)
    empty_bool = np.empty(0, dtype=np.bool_)
    return {
        "flows": pd.DataFrame(columns=["event_id", "tp_r", "symbol", "slot", "funding_time_ms",
                                       "funding_rate", "mark_price", "mark_source", "relation",
                                       "boundary_ambiguous", "scheduled_stage", "scenario_status"]),
        "pre_ptr": np.zeros(n_steps + 1, dtype=np.int64), "pre_ids": empty_int,
        "post_ptr": np.zeros(n_steps + 1, dtype=np.int64), "post_ids": empty_int,
        "flow_col": empty_int, "flow_code": np.empty(0, dtype=np.int32),
        "flow_rate": empty_float, "flow_mark": empty_float, "flow_time": empty_int,
        "flow_cache": empty_bool, "event_codes": event_codes,
        "pre_cashflow": np.zeros((2, n_steps)), "cash_adjustment": np.zeros((2, n_steps)),
        "funding_cash_adjustment": np.zeros((2, n_steps)),
        "margin_funding_added": np.zeros((2, n_steps)),
        "margin_liability_repaid_by_funding": np.zeros((2, n_steps)),
        "margin_liability_repaid_by_release": np.zeros(n_steps),
        "margin_liability": np.zeros(1), "liability_stage": np.zeros((2, n_steps)),
        "flow_cashflow": np.zeros(0), "flow_quantity": empty_float, "flow_applied": empty_bool,
        "exit_cache_quantity": np.zeros(cols), "exit_cache_event": np.full(cols, -1, dtype=np.int32),
        "nav_pre": np.full(n_steps, np.nan), "nav_post": np.full(n_steps, np.nan),
        "nav_order_post": np.full(n_steps, np.nan), "cash_pre": np.full(n_steps, np.nan),
        "cash_post": np.full(n_steps, np.nan), "cash_order_post": np.full(n_steps, np.nan),
        "free_pre": np.full(n_steps, np.nan), "free_post": np.full(n_steps, np.nan),
        "pos_pre": np.full((n_steps, cols), np.nan), "pos_post": np.full((n_steps, cols), np.nan),
        "pos_order_post": np.full((n_steps, cols), np.nan),
        "liability_order_post": np.full(n_steps, np.nan),
        "first_underflow": np.zeros(14), "first_underflow_positions": np.zeros((cols, 2)),
    }


def _schedule(data: dict, tape: pd.DataFrame | None, scenario: str | None,
              no_settlement_windows: dict) -> dict:
    selected = data["selected"].copy()
    if "source_tp_r" not in selected:
        selected["source_tp_r"] = selected.tp_r.astype(float)
    selected["child_leg"] = selected.slot.astype(int) % 4
    selected["child_role"] = selected.child_leg.map(dict(enumerate(CHILD_LABELS)))
    # Hourly funding uses event_id/tp_r as a unique child key; the four native
    # scale-out rows retain their actual tp_r in source_tp_r and get a local key.
    selected["tp_r"] = selected.child_leg.astype(float)
    data["selected"] = selected
    data["funding_no_settlement_windows"] = no_settlement_windows
    if scenario is None:
        scheduled = _empty_schedule(data, selected)
        scheduled["flow_rate_effective"] = np.empty(0, dtype=np.float64)
        return scheduled
    scheduled = hourly_funding._schedule_flows(data, selected, tape, scenario)
    lookup = selected.set_index(["event_id", "tp_r"])
    flow_index = pd.MultiIndex.from_frame(scheduled["flows"][["event_id", "tp_r"]])
    flow_child = lookup.loc[flow_index].reset_index()
    multiple = flow_child.event_id.map(data["exposure_map"]).to_numpy(dtype=np.float64)
    entry_price = flow_child.entry_price.to_numpy(dtype=np.float64)
    side = flow_child.side.to_numpy(dtype=np.float64)
    if np.any(entry_price <= 0) or not np.isfinite(multiple).all():
        raise ValueError("Cannot convert actual funding units to the synthetic VectorBT share")
    scale = side * multiple / entry_price
    scheduled["flow_rate_effective"] = scheduled["flow_rate"] * scale
    scheduled["flows"]["exposure_multiple"] = multiple
    scheduled["flows"]["entry_price"] = entry_price
    scheduled["flows"]["effective_funding_rate_per_synthetic_share"] = scheduled["flow_rate_effective"]
    scheduled["flows"]["native_quantity_conversion"] = scale
    return scheduled


@njit(cache=True)
def _funded_segment(c, previous_close, target, trim_fraction, cap,
                    pre_ptr, pre_ids, flow_col, flow_code, flow_rate, flow_mark, flow_time,
                    flow_cache, flow_cashflow, flow_quantity, flow_applied,
                    exit_cache_quantity, exit_cache_event, pre_cashflow, cash_adjustment,
                    funding_cash_adjustment, margin_funding_added,
                    margin_liability_repaid_by_funding, margin_liability,
                    liability_stage, nav_pre, cash_pre, free_pre, pos_pre,
                    first_underflow, first_underflow_positions):
    phase = c.i % 4
    if phase == 0:
        # The 20% single-contract target uses the last completed H1 NAV/close.
        prior_nav = nb.get_group_value_ctx_nb(c) - margin_liability[c.group]
        for col in range(c.from_col, c.to_col, 4):
            slot = col // 4
            target[slot] = -1.0
            notional_per_share = previous_close[c.i // 4, slot]
            shares = 0.0
            for leg in range(4):
                shares += abs(c.last_position[col + leg])
            if notional_per_share > 0.0 and shares * notional_per_share > prior_nav * cap:
                target[slot] = prior_nav * cap / notional_per_share
        # Exact H1-open settlements precede open orders. Entry-boundary rows are
        # deliberately assigned to the post hook in the include scenario.
        funding._apply_flows(
            c, pre_ptr[c.i], pre_ptr[c.i + 1], 0,
            flow_col, flow_code, flow_rate, flow_mark, pre_ids, flow_time, flow_cache,
            flow_cashflow, flow_quantity, flow_applied,
            exit_cache_quantity, exit_cache_event, pre_cashflow, cash_adjustment,
            funding_cash_adjustment, margin_funding_added,
            margin_liability_repaid_by_funding, margin_liability,
            first_underflow, first_underflow_positions,
        )
    if phase == 2:
        for col in range(c.from_col, c.to_col, 4):
            slot = col // 4
            shares = 0.0
            for leg in range(4):
                shares += abs(c.last_position[col + leg])
            goal = target[slot]
            trim_fraction[slot] = 1.0 - goal / shares if 0.0 <= goal < shares else 0.0
    nav = sizing.base.wallet.pre_segment(c)[0] - margin_liability[c.group]
    liability_stage[0, c.i] = margin_liability[c.group]
    nav_pre[c.i] = nb.get_group_value_ctx_nb(c) - margin_liability[c.group]
    cash_pre[c.i] = c.last_cash[c.group]
    free_pre[c.i] = c.last_free_cash[c.group]
    for col in range(c.from_col, c.to_col):
        pos_pre[c.i, col] = c.last_position[col]
    if nav_pre[c.i] <= 0.0:
        raise ValueError("Marked economic NAV is nonpositive before the four-leg H1 order stage")
    return (nav,)


@njit(cache=True)
def _funded_order(c, nav, actions, prices, fees, weights, reservation, slot_weights,
                  trim_fraction, native_open_multiple, directions, slip, event_codes,
                  exit_cache_quantity, exit_cache_event):
    if c.i % 4 == 2:
        slot = c.col // 4
        fraction = trim_fraction[slot]
        if c.position_now <= 0.0 or fraction <= 1e-10:
            return NoOrder
        native_ratio = native_open_multiple[c.i // 4, slot]
        side = directions[c.i // 4, slot]
        if native_ratio <= 0.0 or side == 0:
            raise ValueError("20% cap trim lacks its actual native H1 open")
        price = c.close[c.i, c.col] - native_ratio * slip
        fee_rate = TAKER * native_ratio * (1.0 - side * slip) / price
        return nb.order_nb(size=-c.position_now * fraction, price=price, fees=fee_rate,
                           slippage=0.0, lock_cash=True, allow_partial=False, raise_reject=True)
    action = actions[c.i, c.col]
    if action == 2:
        exit_cache_quantity[c.col] = c.position_now
        exit_cache_event[c.col] = event_codes[c.i, c.col]
    return sizing.base.wallet.account_order(
        c, nav, actions, prices, fees, 0.0, 1.0, weights,
        reservation, 0, slot_weights,
    )


def _run_scenario(data: dict, tape: pd.DataFrame | None, scenario: str | None,
                  slip: float, exposure_map: pd.Series, previous_close: np.ndarray,
                  native_open: np.ndarray, directions: np.ndarray,
                  no_settlement_windows: dict):
    model = _make_model(data, exposure_map, slip)
    selected = model["selected"].copy()
    selected["source_tp_r"] = selected.tp_r.astype(float)
    selected["child_leg"] = selected.slot.astype(int) % 4
    selected["child_role"] = selected.child_leg.map(dict(enumerate(CHILD_LABELS)))
    selected["tp_r"] = selected.child_leg.astype(float)
    run_data = _expand_four_stage(model, selected)
    run_data["exposure_map"] = exposure_map
    scheduled = _schedule(run_data, tape, scenario, no_settlement_windows)
    _, cols = run_data["actions"].shape
    target = np.full(cols // 4, -1.0, dtype=np.float64)
    trim_fraction = np.zeros(cols // 4, dtype=np.float64)
    reservation = np.zeros(1, dtype=np.float64)
    slot_weights = np.ones((1, 1), dtype=np.float64)
    max_orders = int(np.count_nonzero(run_data["actions"])) + 4 * int(np.count_nonzero(previous_close)) + 1
    pf = vbt.Portfolio.from_order_func(
        run_data["marks"], _funded_order, run_data["actions"], run_data["prices"], run_data["fees"],
        WEIGHTS, reservation, slot_weights, trim_fraction, native_open, directions,
        float(slip), scheduled["event_codes"], scheduled["exit_cache_quantity"],
        scheduled["exit_cache_event"],
        pre_segment_func_nb=_funded_segment,
        pre_segment_args=(
            previous_close, target, trim_fraction, CAP,
            scheduled["pre_ptr"], scheduled["pre_ids"], scheduled["flow_col"],
            scheduled["flow_code"], scheduled.get("flow_rate_effective", scheduled["flow_rate"]),
            scheduled["flow_mark"], scheduled["flow_time"], scheduled["flow_cache"],
            scheduled["flow_cashflow"], scheduled["flow_quantity"], scheduled["flow_applied"],
            scheduled["exit_cache_quantity"], scheduled["exit_cache_event"],
            scheduled["pre_cashflow"], scheduled["cash_adjustment"],
            scheduled["funding_cash_adjustment"], scheduled["margin_funding_added"],
            scheduled["margin_liability_repaid_by_funding"], scheduled["margin_liability"],
            scheduled["liability_stage"], scheduled["nav_pre"], scheduled["cash_pre"],
            scheduled["free_pre"], scheduled["pos_pre"], scheduled["first_underflow"],
            scheduled["first_underflow_positions"],
        ),
        post_segment_func_nb=hourly_funding._post_segment,
        post_segment_args=(
            scheduled["post_ptr"], scheduled["post_ids"], scheduled["flow_col"],
            scheduled["flow_code"], scheduled.get("flow_rate_effective", scheduled["flow_rate"]),
            scheduled["flow_mark"], scheduled["flow_time"], scheduled["flow_cache"],
            scheduled["flow_cashflow"], scheduled["flow_quantity"], scheduled["flow_applied"],
            scheduled["exit_cache_quantity"], scheduled["exit_cache_event"],
            scheduled["pre_cashflow"], scheduled["cash_adjustment"],
            scheduled["funding_cash_adjustment"], scheduled["margin_funding_added"],
            scheduled["margin_liability_repaid_by_funding"], scheduled["margin_liability"],
            scheduled["margin_liability_repaid_by_release"], scheduled["liability_stage"],
            scheduled["nav_post"], scheduled["cash_post"], scheduled["free_post"],
            scheduled["pos_post"], scheduled["nav_order_post"], scheduled["cash_order_post"],
            scheduled["pos_order_post"], scheduled["liability_order_post"],
            scheduled["first_underflow"], scheduled["first_underflow_positions"],
        ),
        call_pre_segment=True, call_post_segment=True, cash_sharing=True, group_by=True,
        init_cash=INITIAL_CASH, max_orders=max_orders, fill_pos_record=False,
    )
    children, parents, flow_rows, order_audit = _records(pf, run_data, scheduled, exposure_map)
    orders_frame = order_audit.pop("orders_frame")
    equity, equity_audit = hourly_funding._build_equity(pf, run_data, scheduled)
    metrics = hourly_funding._period_summary(parents, equity)
    audit = {
        "scenario": "zero_funding" if scenario is None else scenario,
        "market_slip": float(slip), "positions": len(parents),
        "child_legs": len(children), "applied_funding_rows": int(flow_rows.applied.sum()),
        "funding_cashflow_usdt": float(flow_rows.loc[flow_rows.applied, "funding_cashflow_usdt"].sum()),
        "exact_mark_rows_applied": int(flow_rows.loc[flow_rows.applied, "mark_source"].astype(str)
                                      .eq("binance_fundingRate_markPrice").sum()),
        "official_1h_mark_proxy_rows_applied": int(flow_rows.loc[flow_rows.applied, "mark_source"].astype(str)
                                                 .eq("binance_vision_mark_1h_open_proxy").sum()),
        "boundary_rows_applied": int(flow_rows.loc[flow_rows.applied, "boundary_ambiguous"].sum()),
        "boundary_rows_excluded": int(flow_rows.scenario_status.eq("boundary_excluded").sum()),
        "trim_order_count": int(order_audit["trim_order_count"]),
        "trimmed_parents": int(order_audit["trimmed_parents"]),
        "trim_turnover_usdt": float(order_audit["trim_turnover_usdt"]),
        "funding_cashflow_by_child_matches_native_size": True,
        "cash_from_orders_and_funding_usdt": float(order_audit["cash_from_orders_and_funding_usdt"]),
        "source_coverage": data.get("funding_source_audit", {}),
        **equity_audit,
    }
    expected_parents = int(data.get("expected_positions", 1586))
    expected_children = int(data.get("expected_child_legs", expected_parents * 4))
    if len(parents) != expected_parents or len(children) != expected_children:
        raise ValueError("Dynamic funding replay failed to fund the frozen four-child parent roster")
    if audit["terminal_margin_funding_liability_usdt"] > CASH_TOL:
        raise ValueError("Closed relative-size account retained an unpaid funding liability")
    return children, parents, equity, flow_rows, {"metrics": metrics, "audit": audit, "orders": orders_frame}


def _records(pf, data: dict, scheduled: dict, multipliers: pd.Series):
    selected = data["selected"].copy().reset_index(drop=True)
    raw_orders = pf.orders.records.copy()
    if raw_orders.empty:
        raise RuntimeError("VectorBT returned no orders for the frozen Binance relative-size account")
    start = int(data["start"])
    selected_by_slot = {int(slot): frame.sort_values("entry_ms")
                        for slot, frame in selected.groupby("slot", sort=False)}
    fills = raw_orders.copy()
    assigned = []
    for row in fills.itertuples(index=False):
        idx, col = int(row.idx), int(row.col)
        hour_open = start + (idx // 4) * HOUR
        stage = idx % 4
        candidates = selected_by_slot.get(col)
        if candidates is None:
            raise ValueError(f"VectorBT used an unassigned child slot {col}")
        prior = candidates.loc[candidates.entry_ms <= hour_open]
        if prior.empty:
            raise ValueError(f"VectorBT fill precedes the child entry at {hour_open}, slot {col}")
        child = prior.iloc[-1]
        if hour_open > int(child.exit_bar_ms) and not (
            stage == 3 and str(child.exit_phase) in {"close", "intrabar"}
        ):
            raise ValueError(f"VectorBT fill falls outside the native child window: {child.event_id}")
        assigned.append(child)
    fills["event_id"] = [str(row.event_id) for row in assigned]
    fills["parent_id"] = [str(row.parent_id) for row in assigned]
    fills["symbol"] = [str(row.symbol) for row in assigned]
    fills["tp_r"] = [float(row.source_tp_r) for row in assigned]
    fills["child_leg"] = [int(row.child_leg) for row in assigned]
    fills["child_role"] = [str(row.child_role) for row in assigned]
    fills["child_slot"] = fills.col.astype(int)
    fills["hour_open_ms"] = start + (fills.idx // 4).astype(np.int64) * HOUR
    fills["stage"] = fills.idx.astype(int) % 4
    side = np.asarray([int(row.side) for row in assigned], dtype=np.float64)
    entry = np.asarray([float(row.entry_price) for row in assigned], dtype=np.float64)
    multiple = fills.event_id.map(multipliers).to_numpy(dtype=np.float64)
    opened = fills.side.eq(0).to_numpy()
    fee_rate = np.asarray([
        TAKER if bool(is_open) or int(stage) == 2 else float(child.exit_fee_rate)
        for is_open, stage, child in zip(opened, fills.stage, assigned)
    ], dtype=np.float64)
    native_quantity_abs = fills["size"].to_numpy(dtype=np.float64) * multiple / entry
    spot_price = entry * (1.0 + side * (fills.price.to_numpy(dtype=np.float64) - 1.0) / multiple)
    real_notional = native_quantity_abs * spot_price
    expected_fees = real_notional * fee_rate
    if not np.allclose(fills.fees.to_numpy(dtype=np.float64), expected_fees, rtol=1e-8, atol=1e-7):
        raise ValueError("Synthetic VectorBT fees do not match native leveraged notionals")
    fills["native_quantity_abs"] = native_quantity_abs
    fills["native_fill_price"] = spot_price
    fills["native_notional_usdt"] = real_notional
    fills["native_fee_rate"] = fee_rate
    fills["native_cashflow_usdt"] = np.where(opened, -side, side) * real_notional - fills.fees
    fills["synthetic_cashflow_usdt"] = np.where(opened, -1.0, 1.0) * fills["size"] * fills.price - fills.fees
    fills["is_trim"] = fills.stage.eq(2)
    fills["is_entry"] = opened
    fills["is_native_exit"] = (~opened) & (~fills.is_trim)
    if fills.duplicated(["idx", "col"]).any():
        raise ValueError("VectorBT emitted duplicate child orders at one account stage")

    flow_rows = scheduled["flows"].copy().reset_index(drop=True)
    flow_rows["quantity_used_synthetic_shares"] = scheduled["flow_quantity"]
    flow_rows["signed_native_quantity"] = (
        scheduled["flow_quantity"] * flow_rows.native_quantity_conversion.to_numpy(dtype=np.float64)
    ) if len(flow_rows) else np.empty(0, dtype=np.float64)
    flow_rows["funding_cashflow_usdt"] = scheduled["flow_cashflow"]
    flow_rows["applied"] = scheduled["flow_applied"]
    if len(flow_rows):
        expected_flow = -flow_rows.loc[flow_rows.applied, "signed_native_quantity"] * flow_rows.loc[flow_rows.applied, "funding_rate"] * flow_rows.loc[flow_rows.applied, "mark_price"]
        if not np.allclose(expected_flow.to_numpy(), flow_rows.loc[flow_rows.applied, "funding_cashflow_usdt"].to_numpy(),
                           rtol=1e-9, atol=1e-7):
            raise ValueError("Settled funding is not based on the actual signed Binance contract quantity")
        flow_rows["tp_r"] = flow_rows["tp_r"].map(lambda x: float(x))
        flow_rows = flow_rows.rename(columns={"tp_r": "account_child_key"})
        child_info = selected[["event_id", "slot", "source_tp_r", "child_role"]].rename(
            columns={"source_tp_r": "tp_r"}
        )
        flow_rows = flow_rows.merge(child_info, on=["event_id", "slot"], how="left", validate="many_to_one")
        if flow_rows.tp_r.isna().any() or flow_rows.child_role.isna().any():
            raise ValueError("Funding settlement did not map to one of four native child legs")
        flow_rows["quantity_unit"] = "signed Binance USD-M base-asset contracts"

    child_rows = []
    for row in selected.itertuples(index=False):
        key = (str(row.event_id), int(row.slot))
        trades = fills.loc[fills.event_id.eq(str(row.event_id)) & fills.child_slot.eq(int(row.slot))]
        entries = trades.loc[trades.is_entry]
        exits = trades.loc[trades.is_native_exit]
        trims = trades.loc[trades.is_trim]
        native_cash = float(trades.native_cashflow_usdt.sum())
        synthetic_cash = float(trades.synthetic_cashflow_usdt.sum())
        if not np.isclose(native_cash, synthetic_cash, rtol=1e-8, atol=0.00002):
            raise ValueError(
                f"Child gross native execution cash does not reconcile to synthetic collateral PnL: "
                f"event={row.event_id}, slot={row.slot}, native={native_cash}, synthetic={synthetic_cash}"
            )
        if len(entries) != 1 or len(exits) != 1:
            raise ValueError(f"Child does not have exactly one VectorBT entry and native exit: {key}")
        entry_fill, exit_fill = entries.iloc[0], exits.iloc[0]
        entry_quantity = float(entry_fill.native_quantity_abs)
        exit_quantity = float(exit_fill.native_quantity_abs)
        trim_quantity = float(trims.native_quantity_abs.sum())
        if not np.isclose(entry_quantity, exit_quantity + trim_quantity, rtol=1e-8, atol=1e-10):
            raise ValueError(f"Native child quantity did not close after hourly reductions: {key}")
        per_flow = flow_rows.loc[flow_rows.applied & flow_rows.event_id.eq(str(row.event_id))
                                 & flow_rows.slot.eq(int(row.slot))]
        funding_pnl = float(per_flow.funding_cashflow_usdt.sum())
        record = {
            "event_id": str(row.event_id), "parent_id": str(row.parent_id), "symbol": str(row.symbol),
            "is_control": bool(row.is_control) if "is_control" in row._fields else False,
            "side": int(row.side), "slot": int(row.slot), "child_leg": int(row.child_leg),
            "child_role": str(row.child_role), "tp_r": float(row.source_tp_r),
            "exposure_multiple": float(multipliers.at[str(row.event_id)]),
            "entry_ms": int(row.entry_ms), "entry_price": float(row.entry_price),
            "entry_fill_price": float(entry_fill.native_fill_price),
            "entry_signed_size": float(int(row.side) * entry_quantity),
            "entry_notional_usdt": float(entry_fill.native_notional_usdt),
            "entry_fee_usdt": float(entry_fill.fees),
            "exit_bar_ms": int(row.exit_bar_ms), "exit_phase": str(row.exit_phase),
            "exit_price": float(row.exit_price), "exit_fill_price": float(exit_fill.native_fill_price),
            "exit_reason": str(row.exit_reason), "exit_fee_rate": float(row.exit_fee_rate),
            "exit_signed_size": float(-int(row.side) * exit_quantity),
            "native_exit_fee_usdt": float(exit_fill.fees),
            "trimmed_native_quantity_abs": trim_quantity,
            "trim_fee_usdt": float(trims.fees.sum()),
            "trim_turnover_usdt": float(trims.native_notional_usdt.sum()),
            "order_cash_pnl_usdt": native_cash,
            "funding_cashflow_usdt": funding_pnl,
            "funding_rows": int(per_flow.applied.sum()),
            "cash_pnl": float(native_cash + funding_pnl),
            "notional": float(entry_fill.native_notional_usdt),
        }
        record["net_return"] = record["cash_pnl"] / record["notional"]
        child_rows.append(record)
    children = pd.DataFrame(child_rows)
    expected_children = int(data.get("expected_child_legs", 6344))
    if children.duplicated(["event_id", "slot"]).any() or len(children) != expected_children:
        raise ValueError("Funded child receipt lost a unique four-leg event/slot")
    grouped = children.groupby("event_id", sort=False).agg(
        parent_id=("parent_id", "first"), symbol=("symbol", "first"), side=("side", "first"),
        entry_ms=("entry_ms", "first"), exit_bar_ms=("exit_bar_ms", "max"),
        exposure=("exposure_multiple", "first"), cash_pnl=("cash_pnl", "sum"),
        notional=("notional", "sum"), order_cash_pnl_usdt=("order_cash_pnl_usdt", "sum"),
        funding_cashflow_usdt=("funding_cashflow_usdt", "sum"),
        trim_turnover_usdt=("trim_turnover_usdt", "sum"),
    )
    grouped["net_return"] = grouped.cash_pnl / grouped.notional
    grouped["trim_order_count"] = fills.loc[fills.is_trim].groupby("event_id").size().reindex(grouped.index, fill_value=0)
    expected_cash = INITIAL_CASH + grouped.cash_pnl.sum()
    if not np.isclose(expected_cash,
                      INITIAL_CASH + fills.native_cashflow_usdt.sum() + flow_rows.loc[flow_rows.applied, "funding_cashflow_usdt"].sum(),
                      rtol=1e-9, atol=0.02):
        raise ValueError("Native child cash does not reconcile to VectorBT execution and funding cash")
    order_audit = {
        "order_count": len(fills), "entry_order_count": int(fills.is_entry.sum()),
        "native_exit_order_count": int(fills.is_native_exit.sum()),
        "trim_order_count": int(fills.is_trim.sum()),
        "trimmed_parents": int(fills.loc[fills.is_trim, "event_id"].nunique()),
        "trim_turnover_usdt": float(fills.loc[fills.is_trim, "native_notional_usdt"].sum()),
        "entry_fee_usdt": float(fills.loc[fills.is_entry, "fees"].sum()),
        "native_exit_fee_usdt": float(fills.loc[fills.is_native_exit, "fees"].sum()),
        "trim_fee_usdt": float(fills.loc[fills.is_trim, "fees"].sum()),
        "cash_from_orders_and_funding_usdt": float(expected_cash),
        "orders_frame": fills,
    }
    return children, grouped, flow_rows, order_audit


def _preflight() -> dict:
    config = _json(CONFIG_PATH)
    _validate_config(config)
    tape, months, coverage, parents, _equity = _load_funding_inputs(config)
    data, _market, _, _multipliers, audit = _prepare_account(parents)
    selected = data["selected"].copy()
    empty, window_audit = _check_covered_windows(selected, tape, months)
    expected = set(parents.event_id)
    if set(selected.event_id) != expected:
        raise ValueError("Four-leg native parent IDs differ from the frozen contrarian_half result")
    result = {
        "vectorbt_version": vbt.__version__, "frozen_source_receipt": coverage,
        "account_roster_audit": audit, "funding_window_audit": window_audit,
        "funding_mark_source_counts": tape.mark_source.value_counts().to_dict(),
        "funding_rate_sources": tape.rate_source.value_counts().to_dict(),
        "funding_time_utc": {
            "first": str(pd.Timestamp(int(tape.funding_time_ms.min()), unit="ms", tz="UTC")),
            "last": str(pd.Timestamp(int(tape.funding_time_ms.max()), unit="ms", tz="UTC")),
            "last_child_exit_bound": str(pd.Timestamp(int(selected.exit_bar_ms.max() + HOUR), unit="ms", tz="UTC")),
        },
        "no_settlement_symbol_count": len(empty), "preflight_started_funding_account": False,
        "network_requests_started": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    temporary.replace(path)


def _write_frame(path: Path, frame: pd.DataFrame, *, index: bool = False, index_label: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(temporary, index=index, index_label=index_label, compression=compression)
    temporary.replace(path)


def _read_parent_reference(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"event_id": str, "parent_id": str, "symbol": str},
                        float_precision="round_trip")
    if "event_id" not in frame:
        frame = frame.rename(columns={frame.columns[0]: "event_id"})
    return frame.set_index("event_id", drop=True)


def _read_equity_reference(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip")
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame


def _reference_parity(parents: pd.DataFrame, equity: pd.DataFrame,
                      reference_parents: pd.DataFrame, reference_equity: pd.DataFrame) -> dict:
    if not parents.index.is_unique or not reference_parents.index.is_unique:
        raise ValueError("Parent settlement ledgers must be unique by event_id")
    if set(parents.index) != set(reference_parents.index):
        raise ValueError("Zero-funding replay changed the frozen 1,586-event parent IDs")
    parent_join = parents.sort_index().reindex(reference_parents.sort_index().index)
    old_parent = reference_parents.sort_index()
    parent_errors = {}
    for column in ("cash_pnl", "notional", "net_return"):
        error = np.abs(parent_join[column].to_numpy(dtype=float) - old_parent[column].to_numpy(dtype=float))
        parent_errors[column] = float(error.max(initial=0.0))
        if not np.allclose(parent_join[column], old_parent[column], rtol=1e-8, atol=0.002):
            raise ValueError(f"Zero-funding VectorBT parent {column} does not reproduce frozen account")
    if not equity.index.equals(reference_equity.index):
        raise ValueError("Zero-funding H1 NAV index differs from frozen account")
    curve_errors = {}
    for column in ("nav", "open_nav", "open_exit_nav", "post_trim_nav", "lower_nav", "upper_nav"):
        if column not in reference_equity or column not in equity:
            raise ValueError(f"Frozen or reconstructed H1 account is missing {column}")
        error = np.abs(equity[column].to_numpy(dtype=float) - reference_equity[column].to_numpy(dtype=float))
        curve_errors[column] = float(error.max(initial=0.0))
        if not np.allclose(equity[column], reference_equity[column], rtol=1e-8, atol=0.02):
            raise ValueError(f"Zero-funding VectorBT H1 {column} does not reproduce frozen account")
    return {
        "parent_ids_match": True, "parent_cash_rows": len(parents),
        "parent_cash_pnl_max_abs_error_usdt": parent_errors["cash_pnl"],
        "parent_notional_max_abs_error_usdt": parent_errors["notional"],
        "hourly_nav_rows": len(equity), "hourly_max_abs_error_usdt": curve_errors,
        "reproduced_frozen_native_four_child_zero_funding_account": True,
    }


def _run() -> dict:
    if (EXP / "summary.json").exists():
        raise FileExistsError(f"Completed Binance relative funding account already exists: {EXP}")
    config = _json(CONFIG_PATH)
    _validate_config(config)
    _assert_committed()
    tape, months, source_coverage, reference, reference_equity = _load_funding_inputs(config)
    data, market, _quotes, multipliers, account_audit = _prepare_account(reference)
    selected = data["selected"].copy()
    if set(selected.event_id) != set(reference.event_id):
        raise ValueError("Four-child native paths do not match the frozen relative-account parent IDs")
    no_settlement, coverage_audit = _check_covered_windows(selected, tape, months)
    data["funding_no_settlement_windows"] = no_settlement
    data["funding_source_audit"] = coverage_audit
    data["expected_positions"] = 1586
    data["expected_child_legs"] = 6344
    data["exposure_map"] = multipliers
    native_open, previous_close, directions = _scaled_market(data, market, multipliers)

    EXP.mkdir(parents=True, exist_ok=True)
    zero_dir = EXP / "zero_funding"
    children0, parents0, equity0, _flows0, result0 = _run_scenario(
        data, None, None, 0.0, multipliers, previous_close, native_open, directions, no_settlement,
    )
    parity = _reference_parity(parents0, equity0, reference.set_index("event_id", drop=True),
                               reference_equity)
    _write_frame(zero_dir / "funded_legs.csv.gz", children0)
    _write_frame(zero_dir / "funded_positions.csv.gz", parents0.reset_index(names="event_id"))
    _write_frame(zero_dir / "hourly_equity.csv.gz", equity0, index=True, index_label="hour_end_utc")
    _write_frame(zero_dir / "orders.csv.gz", result0["orders"])
    zero_receipt = {"metrics": result0["metrics"], "audit": result0["audit"], "reference_parity": parity}
    _write_json(zero_dir / "metrics.json", zero_receipt)
    print("zero-funding parity", json.dumps(parity, ensure_ascii=False), flush=True)

    reference_stress = _read_parent_reference(REFERENCE / "funded_contrarian_half_stress.csv.gz")
    reference_stress_equity = _read_equity_reference(REFERENCE / "hourly_contrarian_half_stress.csv.gz")
    if "post_trim_pre_funding_nav" not in reference_stress_equity:
        # Frozen pre-funding hourly artifacts expose the identical mark as
        # post_trim_nav. The dynamic funding writer emits both stage columns.
        reference_stress_equity["post_trim_pre_funding_nav"] = reference_stress_equity["post_trim_nav"]
    if set(reference_stress.index) != set(reference.event_id) or not reference_stress_equity.index.equals(reference_equity.index):
        raise ValueError("Frozen 5bp source account is not the same parent/H1 series")
    baselines = {
        "zero_funding": result0["metrics"],
        "zero_funding_stress_reference": hourly_funding._period_summary(reference_stress, reference_stress_equity),
    }
    scenarios = {}
    for slip, boundary in ((0.0, "include_boundary"), (0.0, "exclude_boundary"),
                           (0.0005, "include_boundary"), (0.0005, "exclude_boundary")):
        name = ("stress_" if slip else "") + boundary
        folder = EXP / name
        children, parents, equity, flows, result = _run_scenario(
            data, tape, boundary, slip, multipliers, previous_close, native_open, directions, no_settlement,
        )
        matching = baselines["zero_funding_stress_reference"] if slip else baselines["zero_funding"]
        cash_delta = float(parents.cash_pnl.sum() - (reference_stress.cash_pnl.sum() if slip else reference.cash_pnl.sum()))
        final_delta = float(result["metrics"]["all"]["final_usdt"]
                            - matching["all"]["final_usdt"])
        result["audit"]["delta_vs_matching_zero_funding_usdt"] = {
            "parent_cash_pnl": cash_delta, "final_nav": final_delta,
            "note": "Funding changes later available cash and order sizes; NAV delta is not assumed equal to funding cashflow.",
        }
        _write_frame(folder / "funded_legs.csv.gz", children)
        _write_frame(folder / "funded_positions.csv.gz", parents.reset_index(names="event_id"))
        _write_frame(folder / "applied_funding.csv.gz", flows)
        _write_frame(folder / "hourly_equity.csv.gz", equity, index=True, index_label="hour_end_utc")
        _write_frame(folder / "orders.csv.gz", result["orders"])
        scenario_receipt = {"metrics": result["metrics"], "audit": result["audit"]}
        _write_json(folder / "metrics.json", scenario_receipt)
        scenarios[name] = scenario_receipt
        print(name, json.dumps({"funding_usdt": result["audit"]["funding_cashflow_usdt"],
                                "final": result["metrics"]["all"]["final_usdt"],
                                "dd_bound": result["metrics"]["all"]["dd_bound_pct"],
                                "positions": result["metrics"]["all"]["trades"]},
                               ensure_ascii=False), flush=True)
        del children, parents, equity, flows, result
        gc.collect()

    summary = {
        "status": "complete", "config_id": config["id"],
        "source_coverage_receipt": source_coverage,
        "account_roster_audit": account_audit, "funding_window_audit": coverage_audit,
        "zero_funding_reference_parity": parity, "baseline_metrics": baselines,
        "scenarios": scenarios,
        "coverage_limits": {
            "funding_time_start_utc": str(pd.Timestamp(int(tape.funding_time_ms.min()), unit="ms", tz="UTC")),
            "funding_time_end_utc": str(pd.Timestamp(int(tape.funding_time_ms.max()), unit="ms", tz="UTC")),
            "last_required_settlement_time_utc": str(pd.Timestamp(
                int(selected.exit_bar_ms.max() + HOUR), unit="ms", tz="UTC")),
            "rate_source_counts": tape.rate_source.value_counts().to_dict(),
            "mark_source_counts": tape.mark_source.value_counts().to_dict(),
            "uncovered_symbol_months": coverage_audit["uncovered_symbol_months"],
            "unproven_zero_settlement_windows": coverage_audit["unproven_zero_settlement_windows"],
        },
    }
    _write_json(EXP / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return summary


def _mini_check() -> dict:
    """Compile and exercise four child legs, 1x/2x funding, cap trims and boundary ordering."""
    start = int(pd.Timestamp("2024-12-31T20:00:00Z").value // 1_000_000)
    clock = np.arange(start, start + 6 * HOUR, HOUR, dtype=np.int64)
    raw = {
        "AAA": np.asarray([[100, 101, 99, 100], [100, 151, 99, 150], [150, 161, 140, 155],
                           [155, 161, 150, 155], [155, 161, 100, 150], [150, 156, 145, 150]], dtype=float),
        "BBB": np.asarray([[100, 101, 99, 100], [100, 105, 99, 100], [100, 101, 90, 95],
                           [95, 101, 90, 95], [95, 106, 90, 95], [95, 101, 90, 95]], dtype=float),
        "CCC": np.asarray([[100, 101, 99, 100], [100, 101, 99, 100], [100, 101, 99, 100],
                           [100, 120, 99, 115], [115, 121, 110, 120], [120, 126, 115, 120]], dtype=float),
    }

    def quote_loader(symbol: str):
        return clock, raw[symbol]

    rows = []
    specs = [
        ("mini-long", "AAA", 1, 0, [(2, "intrabar", 150.0, "take_profit", MAKER),
                                     (3, "close", 155.0, "take_profit", MAKER),
                                     (4, "intrabar", 150.0, "ema_flip", TAKER),
                                     (4, "close", 150.0, "ema_flip", TAKER)]),
        ("mini-short", "BBB", -1, 1, [(5, "close", 95.0, "ema_flip", TAKER)] * 4),
        ("mini-later", "CCC", 1, 3, [(5, "close", 120.0, "ema_flip", TAKER)] * 4),
    ]
    for event_id, symbol, side, entry_hour, exits in specs:
        for child in range(4):
            exit_hour, phase, price, reason, fee = exits[child]
            rows.append({
                "event_id": event_id, "parent_id": event_id, "symbol": symbol,
                "is_control": False, "side": side, "entry_ms": start + entry_hour * HOUR,
                "entry_order": len(rows), "entry_price": 100.0,
                "exit_bar_ms": start + exit_hour * HOUR, "exit_phase": phase,
                "exit_price": price, "exit_fee_rate": fee, "exit_reason": reason,
                "tp_r": 2.0 if child < 2 else 0.0, "slot": (len(rows) // 4) * 4 + child,
                "same_bar_ambiguous": False,
            })
    selected = pd.DataFrame(rows)
    data = sizing.base.wallet.prepare(None, 12, start=start, end=start + 6 * HOUR,
                                      assigned=(selected, {"same_contract": 0, "all_slots_busy": 0}),
                                      quote_loader=quote_loader)
    data["expected_positions"] = 3
    data["expected_child_legs"] = 12
    # Each row is one of four real children; preserve the same 1x/2x per-parent
    # sizing rule as the production run.
    multipliers = pd.Series({"mini-long": 1.0, "mini-short": 2.0, "mini-later": 1.0})
    native = np.zeros((6, 3)); previous = np.zeros_like(native); directions = np.zeros_like(native, dtype=np.int8)
    for row in selected[selected.slot.mod(4).eq(3)].itertuples(index=False):
        slot = int(row.slot) // 4
        first, last = int(row.entry_ms - start) // HOUR, int(row.exit_bar_ms - start) // HOUR
        native[first:last + 1, slot] = raw[row.symbol][first:last + 1, 0] / row.entry_price * multipliers.at[row.event_id]
        directions[first:last + 1, slot] = row.side
        if first < last:
            previous[first + 1:last + 1, slot] = raw[row.symbol][first:last, 3] / row.entry_price * multipliers.at[row.event_id]
    tape_rows = []
    for symbol, stamps in [
        ("AAA", [start, start + 4 * HOUR + HOUR // 2, start + 5 * HOUR]),
        ("BBB", [start + 2 * HOUR + 1, start + 5 * HOUR]),
        ("CCC", [start + 4 * HOUR + 1]),
    ]:
        for stamp in stamps:
            tape_rows.append({"symbol": symbol, "funding_time_ms": stamp,
                              "funding_rate": 0.0005, "mark_price": 100.0,
                              "rate_source": "mini_exact", "mark_source": "mini_exact",
                              "source_complete": True})
    tape = pd.DataFrame(tape_rows)
    no_settlement = {}
    data["funding_source_audit"] = {"mini_fixture": True}
    zero = _run_scenario(data, None, None, 0.0, multipliers, previous, native,
                         directions, no_settlement)
    include = _run_scenario(data, tape, "include_boundary", 0.0, multipliers,
                            previous, native, directions, no_settlement)
    exclude = _run_scenario(data, tape, "exclude_boundary", 0.0, multipliers,
                            previous, native, directions, no_settlement)
    stress = _run_scenario(data, tape, "include_boundary", 0.0005, multipliers,
                           previous, native, directions, no_settlement)
    if len(zero[0]) != 12 or zero[1].index.nunique() != 3:
        raise AssertionError("Four-leg mini account did not produce its full parent roster")
    if not np.isclose(include[4]["audit"]["funding_cashflow_usdt"],
                      include[2]["funding_cashflow_usdt"].sum(), atol=1e-8):
        raise AssertionError("Mini funding cashflows did not reconcile by leg and account")
    if include[4]["audit"]["boundary_rows_applied"] <= 0 or exclude[4]["audit"]["boundary_rows_excluded"] <= 0:
        raise AssertionError("Mini include/exclude scenarios did not exercise same-hour boundaries")
    if include[4]["audit"]["trim_order_count"] <= 0 or stress[4]["audit"]["trim_order_count"] <= 0:
        raise AssertionError("Mini did not exercise the four-child 20% H1 notional cap")
    if np.isclose(include[4]["audit"]["funding_cashflow_usdt"],
                  exclude[4]["audit"]["funding_cashflow_usdt"], atol=1e-9):
        raise AssertionError("Boundary choice did not alter the mini funding cashflow")
    if not include[0].loc[include[0].event_id.eq("mini-short"), "entry_signed_size"].abs().sum() > 0:
        raise AssertionError("Mini 2x short account did not retain an actual native position size")
    later_zero_size = float(zero[0].loc[zero[0].event_id.eq("mini-later"), "entry_signed_size"].abs().sum())
    later_funded_size = float(include[0].loc[include[0].event_id.eq("mini-later"), "entry_signed_size"].abs().sum())
    if np.isclose(later_zero_size, later_funded_size, rtol=1e-10, atol=1e-10):
        raise AssertionError("Mini funding did not change a subsequent VectorBT-funded native position size")
    result = {
        "status": "pass", "mini_parents": 3, "mini_children": 12,
        "boundary_rows_include": int(include[4]["audit"]["boundary_rows_applied"]),
        "boundary_rows_exclude": int(exclude[4]["audit"]["boundary_rows_excluded"]),
        "trim_orders": int(include[4]["audit"]["trim_order_count"]),
        "funding_include_usdt": float(include[4]["audit"]["funding_cashflow_usdt"]),
        "funding_exclude_usdt": float(exclude[4]["audit"]["funding_cashflow_usdt"]),
        "later_parent_native_size_zero_usdt": later_zero_size,
        "later_parent_native_size_funded_usdt": later_funded_size,
        "five_bp_stress_run": True, "vectorbt_version": vbt.__version__,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true", help="Check real frozen inputs without replaying the account")
    group.add_argument("--mini-check", action="store_true", help="Compile the VBT four-child funding fixture")
    group.add_argument("--run", action="store_true", help="Run zero-funding parity, funding boundaries, and 5bp stress")
    args = parser.parse_args()
    if args.preflight:
        _preflight()
    elif args.mini_check:
        _validate_config(_json(CONFIG_PATH))
        _mini_check()
    else:
        _run()


if __name__ == "__main__":
    main()
