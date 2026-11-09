"""Replay cap_10pct Binance entries with the archived native funding tape.

The account preserves two distinct constraints at once: each new parent's
underlying notional is capped at 10% of the median USDT turnover in the 60
complete minutes before entry, and every open contract is still trimmed at
the inherited 20% of prior completed H1 NAV cap. Funding and fills are posted
inside the same VectorBT cash-sharing account so funding can affect later size.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_relative_funding as relative

ROOT = relative.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-binance-capacity-funding-20261109-v1"
CONFIG_PATH = EXP / "config.json"
CAPACITY_EXP = ROOT / "experiments/active/exp-cloud-close-binance-entry-capacity-20261109-v1"
HOUR = relative.HOUR
INITIAL_CASH = relative.INITIAL_CASH
ENTRY_FRACTION = 0.10
HOURLY_CAP = 0.20
EXPECTED_CAPACITY_UNKNOWN = {
    "12H|WAVESUSDT|1606694400000000000|1",
    "12H|WAVESUSDT|1606867200000000000|1",
    "12H|XTZUSDT|1606867200000000000|1",
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_config(config: dict) -> None:
    account = config.get("account", {})
    inputs = config.get("inputs", {})
    if config.get("id") != EXP.name or config.get("venue") != "Binance USD-M":
        raise ValueError("Capacity funding config identity or venue changed")
    expected = {
        "original_parent_denominator": 1586,
        "expected_positions": 1583,
        "expected_child_legs": 6332,
        "slots": 9,
    }
    if any(int(account.get(key, -1)) != value for key, value in expected.items()):
        raise ValueError("Frozen cap10 parent/child/slot roster changed")
    if not np.isclose(float(account.get("entry_capacity_fraction", -1)), ENTRY_FRACTION):
        raise ValueError("Prior-hour turnover entry capacity changed")
    if not np.isclose(float(account.get("single_contract_hourly_cap_fraction", -1)), HOURLY_CAP):
        raise ValueError("The original 20% per-contract hourly trim must remain enabled")
    if not np.isclose(float(account.get("initial_cash_usdt", -1)), INITIAL_CASH):
        raise ValueError("Initial cash differs from the frozen capacity account")
    if set(account.get("excluded_event_ids", [])) != EXPECTED_CAPACITY_UNKNOWN:
        raise ValueError("Unknown capacity events changed; no substitute entries are permitted")
    if config.get("funding", {}).get("boundary_scenarios") != ["include_boundary", "exclude_boundary"]:
        raise ValueError("Funding boundary scenarios changed")
    if config.get("execution", {}).get("network_policy") != (
        "Offline replay only; no funding collection, archive download, alternate venue, or substitute price source."
    ):
        raise ValueError("Capacity funding replay must remain offline")
    required_inputs = {
        "capacity_panel": CAPACITY_EXP / "entry_capacity.csv.gz",
        "cap10_funded_parent_reference": CAPACITY_EXP / "funded_cap_10pct.csv.gz",
        "cap10_hourly_reference": CAPACITY_EXP / "hourly_cap_10pct.csv.gz",
        "cap10_stress_funded_reference": CAPACITY_EXP / "funded_cap_10pct_stress.csv.gz",
        "cap10_stress_hourly_reference": CAPACITY_EXP / "hourly_cap_10pct_stress.csv.gz",
    }
    for key, expected_path in required_inputs.items():
        if inputs.get(key) != str(expected_path.relative_to(ROOT)):
            raise ValueError(f"Frozen capacity input path changed: {key}")
    funding_config = relative._json(relative.CONFIG_PATH)
    funding_paths = funding_config["inputs"]
    for key in ("funding_tape", "source_months", "source_coverage"):
        if inputs.get(key) != funding_paths[key]:
            raise ValueError(f"Funding tape source differs from its frozen coverage receipt: {key}")
    if config.get("fees") != {
        "taker": 0.0005,
        "resting_maker": 0.0002,
        "additional_market_slippage_stress": 0.0005,
    }:
        raise ValueError("Fee and 5bp stress assumptions changed")


def _assert_committed() -> None:
    paths = [Path(__file__).resolve(), CONFIG_PATH.resolve(),
             (ROOT / "yoyo/evaluation/cloud_close_binance_relative_funding.py").resolve()]
    for path in paths:
        rel = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT,
                          check=False).returncode:
            raise RuntimeError(f"Commit the capacity funding builder/config first: {rel}")


def _funding_config(config: dict) -> dict:
    result = dict(config)
    inputs = dict(config["inputs"])
    inputs["funded_parent_reference"] = inputs["cap10_funded_parent_reference"]
    inputs["hourly_reference"] = inputs["cap10_hourly_reference"]
    result["inputs"] = inputs
    return result


def _load_capacity_inputs(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    paths = config["inputs"]
    panel_path = ROOT / paths["capacity_panel"]
    funded_path = ROOT / paths["cap10_funded_parent_reference"]
    hourly_path = ROOT / paths["cap10_hourly_reference"]
    stress_funded_path = ROOT / paths["cap10_stress_funded_reference"]
    stress_hourly_path = ROOT / paths["cap10_stress_hourly_reference"]
    original_path = ROOT / paths["original_funded_parent_reference"]
    for path in (panel_path, funded_path, hourly_path, stress_funded_path, stress_hourly_path, original_path):
        if not path.is_file():
            raise FileNotFoundError(f"Frozen cap10 capacity reference missing: {path}")
    panel = pd.read_csv(panel_path, dtype={"event_id": str, "symbol": str}, float_precision="round_trip")
    funded = pd.read_csv(funded_path, dtype={"event_id": str, "parent_id": str, "symbol": str},
                         float_precision="round_trip")
    original = pd.read_csv(original_path, dtype={"event_id": str, "parent_id": str, "symbol": str},
                           float_precision="round_trip")
    hourly = pd.read_csv(hourly_path, index_col=0, parse_dates=True, float_precision="round_trip")
    stress_funded = pd.read_csv(stress_funded_path,
                                dtype={"event_id": str, "parent_id": str, "symbol": str},
                                float_precision="round_trip")
    stress_hourly = pd.read_csv(stress_hourly_path, index_col=0, parse_dates=True,
                                float_precision="round_trip")
    hourly.index = pd.to_datetime(hourly.index, utc=True)
    stress_hourly.index = pd.to_datetime(stress_hourly.index, utc=True)
    required_panel = {
        "event_id", "symbol", "side", "entry_ms", "window_start_ms", "window_end_exclusive_ms",
        "required_minutes", "prior_minutes", "status", "prior_median_quote_usdt", "h1_ohlc_match",
        "baseline_entry_price",
    }
    required_funded = {
        "event_id", "parent_id", "symbol", "side", "entry_ms", "exit_bar_ms", "notional",
        "cash_pnl", "net_return", "exposure", "entry_limit_usdt", "prior_median_quote_usdt", "status",
    }
    if required_panel - set(panel) or required_funded - set(funded):
        raise ValueError("Frozen capacity panel or funded-parent schema changed")
    if panel.empty or panel.event_id.duplicated().any() or len(panel) != 1586:
        raise ValueError("The capacity denominator must be exactly 1,586 unique original parents")
    statuses = panel.status.astype(str).value_counts().to_dict()
    expected_statuses = {"observed": 1583, "missing_archive": 3}
    if statuses != expected_statuses:
        raise ValueError(f"Capacity source status counts changed: {statuses}")
    observed = panel.loc[panel.status.eq("observed")].copy().set_index("event_id", drop=False)
    unknown = set(panel.loc[panel.status.eq("missing_archive"), "event_id"].astype(str))
    if unknown != EXPECTED_CAPACITY_UNKNOWN:
        raise ValueError("Missing capacity IDs changed; no later-source reconstruction is allowed here")
    if set(funded.event_id.astype(str)) != set(observed.index.astype(str)) or len(funded) != 1583:
        raise ValueError("The cap10 funded ledger must contain the 1,583 positive observed-capacity parents")
    if funded.event_id.duplicated().any() or original.event_id.duplicated().any() or len(original) != 1586:
        raise ValueError("Frozen actual roster must contain 1,586 distinct original parents")
    if (len(stress_funded) != 1583 or stress_funded.event_id.duplicated().any()
            or set(stress_funded.event_id.astype(str)) != set(funded.event_id.astype(str))):
        raise ValueError("Frozen cap10 +5bp parent reference does not match the same 1,583 eligible parents")
    base_stress = funded.set_index("event_id").sort_index()
    stressed = stress_funded.set_index("event_id").sort_index()
    for field in ("parent_id", "symbol", "side", "entry_ms", "exit_bar_ms", "exposure", "entry_limit_usdt"):
        if not np.array_equal(base_stress[field].to_numpy(), stressed[field].to_numpy()):
            raise ValueError(f"Frozen cap10 +5bp reference changed the parent roster field {field}")
    if not stress_hourly.index.equals(hourly.index):
        raise ValueError("Frozen cap10 and cap10 +5bp H1 reference clocks differ")
    if set(panel.event_id.astype(str)) != set(original.event_id.astype(str)):
        raise ValueError("Capacity panel no longer covers the original 1,586 account roster")
    if set(funded.event_id.astype(str)) != set(observed.index.astype(str)):
        raise ValueError("Funding selection is not exactly the observed positive capacity subset")
    observed["h1_ohlc_match"] = relative._bool_column(observed.h1_ohlc_match, "capacity.h1_ohlc_match")
    if (not observed.h1_ohlc_match.all()
            or not observed.prior_minutes.eq(60).all()
            or not observed.required_minutes.eq(60).all()
            or not observed.window_end_exclusive_ms.eq(observed.entry_ms).all()
            or not np.isfinite(observed.prior_median_quote_usdt.to_numpy(dtype=float)).all()
            or not observed.prior_median_quote_usdt.gt(0).all()):
        raise ValueError("Observed entry capacity lacks complete causal prior-window or native H1-open evidence")
    cap_by_id = observed.prior_median_quote_usdt.astype(float).mul(ENTRY_FRACTION).rename("entry_limit_usdt")
    funded_indexed = funded.set_index("event_id", drop=True)
    if not np.allclose(cap_by_id.reindex(funded_indexed.index).to_numpy(dtype=float),
                       funded_indexed.entry_limit_usdt.to_numpy(dtype=float), rtol=1e-10, atol=1e-8):
        raise ValueError("Frozen cap10 entry limits do not equal 10% of the causal prior-hour median")
    if (funded_indexed.notional > funded_indexed.entry_limit_usdt + 1e-6).any():
        raise ValueError("Frozen cap10 account contains an entry above its 10% turnover limit")
    if not np.isclose(INITIAL_CASH + funded.cash_pnl.sum(), float(hourly.nav.iloc[-1]), atol=0.02, rtol=1e-9):
        raise ValueError("Frozen cap10 parent cash does not reconcile to its H1 hourly account")
    if not np.isclose(INITIAL_CASH + stress_funded.cash_pnl.sum(),
                      float(stress_hourly.nav.iloc[-1]), atol=0.02, rtol=1e-9):
        raise ValueError("Frozen cap10 +5bp parent cash does not reconcile to its H1 hourly account")
    return panel, funded, hourly, {
        "capacity_rows": int(len(panel)), "status_counts": {str(k): int(v) for k, v in statuses.items()},
        "eligible_parent_ids": set(funded.event_id.astype(str)),
        "unknown_parent_ids": sorted(unknown), "entry_limits": cap_by_id,
        "original_parent_ids": set(original.event_id.astype(str)),
        "stress_funded": stress_funded, "stress_hourly": stress_hourly,
    }


def _entry_limit_matrix(data: dict, market: tuple, previous_close: np.ndarray,
                        limits: pd.Series) -> tuple[np.ndarray, dict]:
    runners = market[-1]
    grid = np.full(previous_close.shape, np.inf, dtype=np.float64)
    used: set[tuple[int, int]] = set()
    if not runners.event_id.astype(str).is_unique or set(runners.event_id.astype(str)) != set(limits.index.astype(str)):
        raise ValueError("Native child path roster and positive capacity parent IDs differ")
    for row in runners.itertuples(index=False):
        event_id = str(row.event_id)
        hour = (int(row.entry_ms) - int(data["start"])) // HOUR
        slot = int(row.slot) // 4
        if not (0 <= hour < len(grid) and 0 <= slot < grid.shape[1]):
            raise ValueError(f"Capacity entry time/slot is outside the VBT H1 grid: {event_id}")
        key = (int(hour), int(slot))
        if key in used:
            raise ValueError("Two entry limits collide in one native VBT slot/open stage")
        value = float(limits.at[event_id])
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError(f"Native entry lacks positive prior-hour capacity: {event_id}")
        grid[key] = value
        used.add(key)
    if len(used) != len(runners):
        raise ValueError("Not every eligible native parent received exactly one entry limit")
    return grid, {
        "entry_capacity_cells": int(np.isfinite(grid).sum()),
        "entry_capacity_parents": int(len(runners)),
        "native_grid_shape": list(grid.shape),
        "slot_entries_preserve_frozen_parent_assignment": True,
    }


def _prepare_real_account(config: dict):
    """Load and wire the exact native parent, funding, slot and capacity inputs.

    Shared by preflight and the sole official run so the validation route
    exercises the same actual input joins and matrix dimensions as generation.
    """
    panel, reference, reference_equity, capacity_audit = _load_capacity_inputs(config)
    tape, months, source_coverage, loaded_reference, _loaded_equity = relative._load_funding_inputs(
        _funding_config(config), expected_parent_count=1583,
    )
    if set(loaded_reference.event_id.astype(str)) != capacity_audit["eligible_parent_ids"]:
        raise ValueError("Funding source loader used a different cap10 parent subset")
    _assert_capacity_metadata(reference, panel)
    data, market, quotes, multipliers, account_audit = relative._prepare_account(
        reference, expected_event_ids=capacity_audit["eligible_parent_ids"],
    )
    if account_audit["positions"] != 1583 or account_audit["source_parent_ids"] != 1586:
        raise ValueError("Native builder failed to retain the original roster denominator and eligible subset")
    if set(account_audit["capacity_excluded_parent_ids"]) != EXPECTED_CAPACITY_UNKNOWN:
        raise ValueError("The account adapter excluded different parents than the capacity audit")
    selected = data["selected"].copy()
    if set(selected.event_id.astype(str)) != capacity_audit["eligible_parent_ids"]:
        raise ValueError("Prepared native V1.7 child paths differ from observed cap10 capacity entries")
    if not selected.groupby("event_id").size().eq(4).all() or selected.duplicated(["event_id", "slot"]).any():
        raise ValueError("Eligible native roster must keep exactly four unique child legs per parent")
    no_settlement, coverage_audit = relative._check_covered_windows(selected, tape, months)
    data["funding_no_settlement_windows"] = no_settlement
    data["funding_source_audit"] = coverage_audit
    data["expected_positions"] = 1583
    data["expected_child_legs"] = 6332
    data["exposure_map"] = multipliers
    native_open, previous_close, directions = relative._scaled_market(data, market, multipliers)
    entry_matrix, entry_matrix_audit = _entry_limit_matrix(
        data, market, previous_close, capacity_audit["entry_limits"],
    )
    return {
        "panel": panel, "reference": reference, "reference_equity": reference_equity,
        "capacity_audit": capacity_audit, "tape": tape, "months": months,
        "source_coverage": source_coverage, "data": data, "market": market,
        "quotes": quotes, "multipliers": multipliers, "account_audit": account_audit,
        "selected": selected, "no_settlement": no_settlement,
        "coverage_audit": coverage_audit, "native_open": native_open,
        "previous_close": previous_close, "directions": directions,
        "entry_matrix": entry_matrix, "entry_matrix_audit": entry_matrix_audit,
    }


def _assert_capacity_metadata(reference: pd.DataFrame, capacity: pd.DataFrame) -> None:
    old = reference.set_index("event_id").sort_index()
    panel = capacity.loc[capacity.status.eq("observed")].set_index("event_id").sort_index()
    if not old.index.equals(panel.index):
        raise ValueError("Cap10 reference IDs no longer equal the observed capacity subset")
    for field in ("symbol", "side", "entry_ms"):
        if not np.array_equal(old[field].to_numpy(), panel[field].to_numpy()):
            raise ValueError(f"Cap10 reference {field} differs from its causal capacity row")
    if "entry_price" in old and not np.allclose(
        old.entry_price.to_numpy(dtype=float), panel.baseline_entry_price.to_numpy(dtype=float), rtol=1e-10, atol=1e-12
    ):
        raise ValueError("Observed capacity entry price differs from the frozen H1 parent open")


def _cash_bridge(parents: pd.DataFrame, flows: pd.DataFrame, equity: pd.DataFrame) -> dict:
    applied = flows.loc[flows.applied] if len(flows) else flows
    order_cash = float(parents.order_cash_pnl_usdt.sum())
    funding_cash = float(applied.funding_cashflow_usdt.sum()) if len(applied) else 0.0
    terminal_nav = float(equity.nav.iloc[-1])
    parent_cash = float(parents.cash_pnl.sum())
    expected = INITIAL_CASH + order_cash + funding_cash
    error = expected - terminal_nav
    ledger_error = parent_cash - (order_cash + funding_cash)
    if abs(error) > 0.02 or abs(ledger_error) > 0.02:
        raise ValueError(f"Order/funding cash bridge failed: account={error:.6f}, parent={ledger_error:.6f}")
    return {
        "initial_cash_usdt": INITIAL_CASH,
        "native_order_cash_pnl_usdt": order_cash,
        "applied_funding_cashflow_usdt": funding_cash,
        "parent_cash_pnl_usdt": parent_cash,
        "terminal_nav_usdt": terminal_nav,
        "account_bridge_error_usdt": float(error),
        "parent_ledger_bridge_error_usdt": float(ledger_error),
        "funding_is_a_cashflow_not_a_terminal_nav_addition": True,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _write_frame(path: Path, frame: pd.DataFrame, *, index: bool = False,
                 index_label: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(temporary, index=index, index_label=index_label, compression=compression)
    temporary.replace(path)


def _run() -> dict:
    if (EXP / "summary.json").exists():
        raise FileExistsError(f"Completed cap10 funding account already exists: {EXP}")
    config = _json(CONFIG_PATH)
    _validate_config(config)
    _assert_committed()
    prepared = _prepare_real_account(config)
    panel = prepared["panel"]
    reference = prepared["reference"]
    reference_equity = prepared["reference_equity"]
    capacity_audit = prepared["capacity_audit"]
    tape = prepared["tape"]
    source_coverage = prepared["source_coverage"]
    data = prepared["data"]
    multipliers = prepared["multipliers"]
    account_audit = prepared["account_audit"]
    selected = prepared["selected"]
    no_settlement = prepared["no_settlement"]
    coverage_audit = prepared["coverage_audit"]
    native_open = prepared["native_open"]
    previous_close = prepared["previous_close"]
    directions = prepared["directions"]
    entry_matrix = prepared["entry_matrix"]
    entry_matrix_audit = prepared["entry_matrix_audit"]

    zero = relative._run_scenario(
        data, None, None, 0.0, multipliers, previous_close, native_open, directions,
        no_settlement, hourly_cap_fraction=HOURLY_CAP, entry_limits=entry_matrix,
    )
    children0, parents0, equity0, flows0, result0 = zero
    if len(parents0) != 1583 or len(children0) != 6332:
        raise ValueError("Zero-funding capacity replay did not fund the exact observed 1,583 parent set")
    parity = relative._reference_parity(
        parents0, equity0, reference.set_index("event_id", drop=True), reference_equity,
    )
    _assert_capacity_metadata(parents0.reset_index(names="event_id"), panel)
    if (parents0.notional > parents0.index.map(capacity_audit["entry_limits"]) + 1e-6).any():
        raise ValueError("Zero-funding VectorBT parent notional exceeds its prior-minute cap")
    if result0["audit"]["hourly_single_contract_cap_fraction"] != HOURLY_CAP:
        raise ValueError("Zero-funding parity run did not preserve the 20% hourly single-contract trim")

    EXP.mkdir(parents=True, exist_ok=True)
    zero_dir = EXP / "zero_funding_cap10_parity"
    _write_frame(zero_dir / "funded_legs.csv.gz", children0)
    _write_frame(zero_dir / "funded_positions.csv.gz", parents0.reset_index(names="event_id"))
    _write_frame(zero_dir / "hourly_equity.csv.gz", equity0, index=True, index_label="hour_end_utc")
    _write_frame(zero_dir / "orders.csv.gz", result0["orders"])
    _write_json(zero_dir / "metrics.json", {
        "metrics": result0["metrics"], "audit": result0["audit"],
        "cash_bridge": _cash_bridge(parents0, flows0, equity0), "cap10_reference_parity": parity,
    })
    print("zero-funding cap10 parity", json.dumps(parity, ensure_ascii=False), flush=True)
    baseline_stress = capacity_audit["stress_funded"].set_index("event_id", drop=True)
    baseline_stress_equity = capacity_audit["stress_hourly"]
    baseline_metrics = {
        "zero_funding_cap10": result0["metrics"],
        "zero_funding_cap10_stress_reference": relative.hourly_funding._period_summary(
            baseline_stress, baseline_stress_equity,
        ),
    }
    del children0, parents0, equity0, flows0, zero
    gc.collect()

    scenarios = {}
    for slip, boundary in ((0.0, "include_boundary"), (0.0, "exclude_boundary"),
                           (0.0005, "include_boundary"), (0.0005, "exclude_boundary")):
        name = ("stress_" if slip else "") + boundary
        children, parents, equity, flows, result = relative._run_scenario(
            data, tape, boundary, slip, multipliers, previous_close, native_open, directions,
            no_settlement, hourly_cap_fraction=HOURLY_CAP, entry_limits=entry_matrix,
        )
        if len(parents) != 1583 or len(children) != 6332:
            raise ValueError(f"Funding scenario {name} changed the frozen eligible parent/child roster")
        actual_cap = parents.notional - parents.index.map(capacity_audit["entry_limits"])
        if actual_cap.gt(1e-6).any():
            raise ValueError(f"Funding scenario {name} exceeded a parent's observed 10% turnover limit")
        if result["audit"]["hourly_single_contract_cap_fraction"] != HOURLY_CAP:
            raise ValueError(f"Funding scenario {name} failed to preserve the 20% hourly single-contract trim")
        bridge = _cash_bridge(parents, flows, equity)
        result["audit"]["cash_bridge"] = bridge
        baseline_parents = reference.set_index("event_id", drop=True) if not slip else baseline_stress
        baseline_metrics_for_scenario = (baseline_metrics["zero_funding_cap10"] if not slip
                                         else baseline_metrics["zero_funding_cap10_stress_reference"])
        result["audit"]["delta_vs_matching_zero_funding_reference"] = {
            "parent_cash_pnl_usdt": float(parents.cash_pnl.sum() - baseline_parents.cash_pnl.sum()),
            "final_nav_usdt": float(result["metrics"]["all"]["final_usdt"]
                                     - baseline_metrics_for_scenario["all"]["final_usdt"]),
            "funding_cashflow_usdt": bridge["applied_funding_cashflow_usdt"],
            "nav_change_need_not_equal_funding_cashflow": True,
            "note": "Funding cash changes later shared-cash size and hourly trims; do not treat it as a terminal NAV adjustment.",
        }
        result["audit"]["entry_capacity"] = {
            "fraction": ENTRY_FRACTION,
            "parents_at_or_above_cap": int(np.isclose(
                parents.notional.to_numpy(dtype=float),
                parents.index.map(capacity_audit["entry_limits"]).to_numpy(dtype=float),
                rtol=1e-8, atol=1e-6,
            ).sum()),
            "maximum_parent_notional_to_entry_limit": float(
                (parents.notional / parents.index.to_series().map(capacity_audit["entry_limits"])).max()
            ),
            "unknown_capacity_events_skipped_without_replacement": sorted(EXPECTED_CAPACITY_UNKNOWN),
        }
        folder = EXP / name
        _write_frame(folder / "funded_legs.csv.gz", children)
        _write_frame(folder / "funded_positions.csv.gz", parents.reset_index(names="event_id"))
        _write_frame(folder / "applied_funding.csv.gz", flows)
        _write_frame(folder / "hourly_equity.csv.gz", equity, index=True, index_label="hour_end_utc")
        _write_frame(folder / "orders.csv.gz", result["orders"])
        receipt = {"metrics": result["metrics"], "audit": result["audit"]}
        _write_json(folder / "metrics.json", receipt)
        scenarios[name] = receipt
        print(name, json.dumps({
            "funding_usdt": result["audit"]["funding_cashflow_usdt"],
            "final_usdt": result["metrics"]["all"]["final_usdt"],
            "dd_bound_pct": result["metrics"]["all"]["dd_bound_pct"],
            "positions": result["metrics"]["all"]["trades"],
        }, ensure_ascii=False), flush=True)
        del children, parents, equity, flows, result
        gc.collect()

    summary = {
        "status": "complete", "config_id": config["id"],
        "account_roster_audit": account_audit,
        "capacity_source_audit": {
            key: value for key, value in capacity_audit.items()
            if key not in {"eligible_parent_ids", "entry_limits", "original_parent_ids",
                           "stress_funded", "stress_hourly"}
        },
        "entry_limit_matrix_audit": entry_matrix_audit,
        "funding_source_coverage": source_coverage,
        "funding_window_audit": coverage_audit,
        "zero_funding_cap10_reference_parity": parity,
        "frozen_zero_funding_baselines": baseline_metrics,
        "zero_funding_metrics": result0["metrics"],
        "zero_funding_audit": result0["audit"],
        "scenarios": scenarios,
        "limits": {
            "entry_turnover_fraction": ENTRY_FRACTION,
            "prior_window": "Exactly 60 complete native Binance USD-M 1m bars in [entry_ms-60min, entry_ms).",
            "per_contract_hourly_nav_trim_fraction": HOURLY_CAP,
            "unresolved_event_ids": sorted(EXPECTED_CAPACITY_UNKNOWN),
            "entry_events_not_replaced": True,
            "original_nine_slot_order_preserved": True,
            "funding cashflows affect subsequent shared cash and position size": True,
        },
    }
    _write_json(EXP / "summary.json", summary)
    return summary


def _preflight() -> dict:
    config = _json(CONFIG_PATH)
    _validate_config(config)
    prepared = _prepare_real_account(config)
    if (not prepared["reference_equity"].index.is_unique
            or prepared["entry_matrix"].shape != prepared["previous_close"].shape
            or np.isfinite(prepared["entry_matrix"]).sum() != 1583):
        raise ValueError("Actual funding and entry-cap inputs did not reach the native account grid")
    result = {
        "status": "preflight_pass", "capacity_rows": len(prepared["panel"]),
        "eligible_cap10_parents": len(prepared["reference"]),
        "hourly_rows": len(prepared["reference_equity"]),
        "funding_rows": len(prepared["tape"]), "source_months": len(prepared["months"]),
        "funding_coverage_complete": bool(prepared["source_coverage"].get("complete")),
        "covered_child_legs": prepared["coverage_audit"]["covered_child_legs"],
        "h1_grid": prepared["entry_matrix_audit"]["native_grid_shape"],
        "entry_capacity_cells": prepared["entry_matrix_audit"]["entry_capacity_cells"],
        "per_contract_hourly_trim_fraction": HOURLY_CAP,
        "skipped_original_event_ids": sorted(prepared["capacity_audit"]["unknown_parent_ids"]),
        "both_capacity_and_hourly_caps_retained": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--preflight", action="store_true", help="Validate frozen inputs, no account replay")
    commands.add_argument("--mini-check", action="store_true", help="Run VectorBT mini and capacity semantics check")
    commands.add_argument("--run", action="store_true", help="Run exact cap10 parity and funding scenarios once")
    args = parser.parse_args()
    if args.preflight:
        _preflight()
    elif args.mini_check:
        config = _json(CONFIG_PATH)
        _validate_config(config)
        relative._mini_check()
        _preflight()
    else:
        _run()


if __name__ == "__main__":
    main()
