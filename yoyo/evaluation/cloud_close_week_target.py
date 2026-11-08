"""Compare V1.7's weekly opposite 1.618 extension as a 75% resting exit.

The only changed trading variable is the 75% limit target geometry. All
candidate paths keep the frozen 12H-confirmation / next-1H-open entry, the
event's original stop, and the same cloud/stale/gap runner exit. Each position
keeps 25% in that unchanged runner. Controls inherit the parent signal's
opposite-extension-to-entry price ratio, known by the time the later control
opens; they do not use control-side future prices.

The fixed-2R candidate is reused from the frozen selected 9-slot account. New
target paths reuse cloud_close_reward_paths._simulate_symbol and are then
funded through the existing cash-sharing slot/scale-out adapter.
"""
from __future__ import annotations

import argparse
import gc
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_slots as capacity

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-week-target-20261109-v1"
FRACTIONS = (0.5, 1.0, 1.5, 2.0)
BASELINE_CANDIDATE = "fixed_2r"
PATH_FIELDS = [
    "event_id", "id", "parent_id", "source_row_number", "symbol", "week",
    "side", "tp_r", "is_control", "rank", "cohort", "is_current",
    "was_top20", "bar_source", "key_open_ms", "confirmation_ms",
    "confirmed_bjt", "confirmation_12h_bar_present", "entry_ms",
    "exit_bar_ms", "entry_price", "exit_price", "stop_price", "target_price",
    "risk_distance", "risk_fraction", "entry_fee_class", "entry_fee_rate",
    "entry_fee_amount", "exit_fee_class", "exit_fee_rate", "exit_fee_amount",
    "exit_reason", "exit_phase", "same_bar_ambiguous", "ambiguous_triggers",
    "gross_pnl", "net_pnl", "gross_r", "net_r",
    "entry_status", "control_match_status", "control_confirmation_ms",
    "control_12h_open_ms", "control_atr_relative_difference",
    "control_prior_atr_pct", "control_match_reason", "stop_basis",
    "source_path_12h", "source_path_1h", "path_upper_bound_reason",
]
PATH_NULLS = {
    "entry_ms": [""], "exit_bar_ms": [""], "entry_price": [""],
    "exit_price": [""], "stop_price": [""], "target_price": [""],
    "risk_distance": [""], "risk_fraction": [""], "entry_fee_rate": [""],
    "entry_fee_amount": [""], "exit_fee_rate": [""], "exit_fee_amount": [""],
    "gross_pnl": [""], "net_pnl": [""], "gross_r": [""], "net_r": [""],
}
EXIT_COMPARE_FIELDS = (
    "entry_ms", "entry_price", "stop_price", "target_price", "exit_bar_ms",
    "exit_price", "exit_reason", "exit_phase", "exit_fee_class",
    "exit_fee_rate", "same_bar_ambiguous", "ambiguous_triggers",
    "gross_pnl", "net_pnl", "gross_r", "net_r",
)


def candidate_name(fraction: float) -> str:
    token = f"{float(fraction):g}".replace(".", "p")
    return f"opposite_1p618_x_{token}"


def transfer_parent_extension(reference_price: float, parent_entry: float,
                              control_entry: float) -> float:
    """Scale the known parent reference by its entry-relative price ratio."""
    if not all(np.isfinite(x) and x > 0 for x in
               (reference_price, parent_entry, control_entry)):
        raise ValueError("Control extension transfer requires positive finite prices")
    return control_entry * (reference_price / parent_entry)


def target_geometry(entry_price: float, stop_price: float, extension_price: float,
                    side: int, fraction: float) -> dict:
    """Map an extension fraction to a directional target or a fixed 2R fallback."""
    if side not in (-1, 1):
        raise ValueError("V1.7 target side must be -1 or +1")
    if fraction not in FRACTIONS:
        raise ValueError(f"Unexpected extension fraction: {fraction}")
    if not all(np.isfinite(x) and x > 0 for x in
               (entry_price, stop_price, extension_price)):
        raise ValueError("Target mapping requires positive finite prices")
    original_risk = abs(entry_price - stop_price)
    if original_risk <= 0:
        raise ValueError("Original V1.7 stop must define positive risk")
    remaining_distance = side * (extension_price - entry_price)
    already_passed = remaining_distance <= 0
    if already_passed:
        requested_distance = 2.0 * original_risk
        geometry = "fixed_2r_fallback_level_already_passed"
    else:
        requested_distance = float(fraction) * remaining_distance
        geometry = "opposite_weekly_1p618_extension"
    requested_price = entry_price + side * requested_distance
    no_resting_order = requested_price <= 0
    if no_resting_order:
        # VectorBT must see no finite target in this case. A short limit at a
        # zero/negative price cannot rest; the target leg therefore follows the
        # unchanged stop/cloud runner instead of creating an impossible fill.
        target_distance = None
        simulation_distance = 0.0
        target_price = None
        expected_r = None
        geometry = ("no_resting_order_nonpositive_2r_fallback" if already_passed
                    else "no_resting_order_nonpositive_extension_target")
        no_order_reason = "nonpositive_projected_limit_price"
    else:
        target_distance = requested_distance
        simulation_distance = target_distance
        target_price = float(requested_price)
        expected_r = float(target_distance / original_risk)
        no_order_reason = ""
    return {
        "target_price": target_price,
        "target_distance": None if target_distance is None else float(target_distance),
        "target_simulation_distance": float(simulation_distance),
        "target_requested_price": float(requested_price),
        "target_requested_distance": float(requested_distance),
        "original_risk_distance": float(original_risk),
        "target_expected_r": expected_r,
        "target_reference_price": float(extension_price),
        "target_reference_remaining_distance": float(remaining_distance),
        "target_relative_favorable_pct": float(remaining_distance / entry_price),
        "target_fraction": float(fraction),
        "target_geometry": geometry,
        "target_fallback_2r": bool(already_passed),
        "target_level_already_passed": bool(already_passed),
        "target_no_resting_order": bool(no_resting_order),
        "target_no_order_reason": no_order_reason,
        "target_fallback_reason": "level_already_passed_at_confirmation_entry" if already_passed else "",
    }


def check_target_mapping() -> dict:
    """Small deterministic assertions for directional mapping and 2R fallback."""
    long_half = target_geometry(100.0, 90.0, 120.0, 1, 0.5)
    short_double = target_geometry(100.0, 110.0, 80.0, -1, 2.0)
    long_fallback = target_geometry(100.0, 95.0, 99.0, 1, 1.5)
    short_unplaceable = target_geometry(100.0, 101.0, 20.0, -1, 2.0)
    assert np.isclose(long_half["target_price"], 110.0)
    assert np.isclose(short_double["target_price"], 60.0)
    assert long_fallback["target_fallback_2r"]
    assert np.isclose(long_fallback["target_price"], 110.0)
    assert short_unplaceable["target_no_resting_order"]
    assert short_unplaceable["target_price"] is None
    assert short_unplaceable["target_no_order_reason"] == "nonpositive_projected_limit_price"
    assert np.isclose(transfer_parent_extension(120.0, 100.0, 50.0), 60.0)
    assert np.isclose(transfer_parent_extension(80.0, 100.0, 200.0), 160.0)
    return {"directional_fraction_mapping": "passed",
            "passed_extension_fallback": "passed",
            "nonpositive_limit_becomes_no_order": "passed",
            "parent_relative_control_transfer": "passed"}


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _close(actual: float, expected: float, label: str) -> None:
    if not np.isclose(float(actual), float(expected), rtol=1e-10, atol=1e-7):
        raise ValueError(f"Frozen parent metric changed for {label}: {actual} != {expected}")


def _parent_contract(config: dict) -> tuple[dict, pd.DataFrame, dict]:
    reward_config_path = ROOT / config["frozen_inputs"]["reward_paths_config"]
    reward_config = _load_json(reward_config_path)
    scaleout_exp = ROOT / config["frozen_inputs"]["scaleout_experiment"]
    slots_exp = ROOT / config["frozen_inputs"]["slots_experiment"]
    priority_exp = ROOT / config["frozen_inputs"]["priority_experiment"]
    scaleout_summary = _load_json(scaleout_exp / "summary.json")
    slots_summary = _load_json(slots_exp / "summary.json")
    priority_summary = _load_json(priority_exp / "summary.json")
    if float(scaleout_summary["pick"]) != float(config["account"]["partial_fraction"]):
        raise ValueError("Frozen parent partial fraction differs from this experiment")
    if int(slots_summary["pick"]) != int(config["account"]["slots"]):
        raise ValueError("Frozen parent slot count differs from this experiment")
    if (priority_summary["pick"] != config["account"]["priority_policy"]
            or int(priority_summary["slots"]) != int(config["account"]["slots"])):
        raise ValueError("Frozen parent priority selection differs from this experiment")
    if (float(reward_config["fees"]["taker_per_fill"]) != wallet.TAKER
            or float(reward_config["fees"]["resting_maker_per_fill"]) != engine.MAKER):
        raise ValueError("Frozen V1.7 fee schedule differs from the account engine")

    metrics_path = priority_exp / "account_metrics.csv"
    parent_metrics = pd.read_csv(metrics_path)
    baseline = parent_metrics[
        parent_metrics.policy.eq(config["account"]["priority_policy"])
    ].copy()
    expected_periods = {"all", "dev", "2025", "2026"}
    if set(baseline.period) != expected_periods:
        raise ValueError("Frozen selected parent baseline is missing a required period")
    expected = config["frozen_inputs"]["baseline_expected"]
    for key, value in expected.items():
        period, field = key.split(".", 1)
        row = baseline[baseline.period.eq(period)]
        if len(row) != 1:
            raise ValueError(f"Frozen parent baseline has no unique {period} row")
        _close(row.iloc[0][field], value, key)
    return reward_config, baseline, {
        "scaleout": scaleout_summary,
        "slots": slots_summary,
        "priority": priority_summary,
    }


def _read_frozen_events(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    paths_path = ROOT / config["frozen_inputs"]["paths_csv_gz"]
    signals_path = ROOT / config["frozen_inputs"]["signals_csv"]
    # The existing reader validates the seven-arm event identity, fee classes,
    # and exact matched-control chronology before we use its frozen 0R runner.
    verified = wallet.read_paths()
    if len(verified) != int(config["frozen_inputs"]["expected_verified_path_rows"]):
        raise ValueError("Frozen reward-path population size changed")
    if not np.isclose(verified.tp_r.min(), 0.0) or not verified.tp_r.eq(0.0).any():
        raise ValueError("Frozen reward paths have no unchanged cloud runner")
    frozen = pd.read_csv(paths_path, usecols=PATH_FIELDS, low_memory=False,
                         float_precision="round_trip", dtype={"symbol": str},
                         na_values=PATH_NULLS)
    runner = frozen[frozen.tp_r.eq(0.0) & frozen.entry_status.eq("executed")].copy()
    if runner.duplicated("event_id").any():
        raise ValueError("Frozen runner has duplicate event identities")
    verified_runner = verified[verified.tp_r.eq(0.0)]
    if set(runner.event_id) != set(verified_runner.event_id):
        raise ValueError("Frozen runner event IDs disagree with the validated account reader")
    if len(runner) != int(config["frozen_inputs"]["expected_executable_runner_events"]):
        raise ValueError("Frozen runner count changed")
    fixed_2r = frozen[frozen.tp_r.eq(2.0) & frozen.entry_status.eq("executed")].copy()
    if set(fixed_2r.event_id) != set(runner.event_id):
        raise ValueError("Frozen fixed-2R paths do not cover the runner event set")

    signals = pd.read_csv(signals_path, low_memory=False, dtype={"symbol": str})
    if len(signals) != int(config["frozen_inputs"]["expected_signal_rows"]):
        raise ValueError("Frozen signal row count changed")
    signals["source_row_number"] = np.arange(1, len(signals) + 1, dtype=np.int64)
    signals = signals.set_index("source_row_number", drop=False)
    if signals.opposite_target.isna().any():
        raise ValueError("An original V1.7 signal has no opposite weekly 1.618 reference")
    if not runner.source_row_number.isin(signals.index).all():
        raise ValueError("A runner row has no source signal join")
    actual = runner[~runner.is_control].set_index("parent_id", drop=False)
    controls = runner[runner.is_control]
    if not controls.parent_id.isin(actual.index).all():
        raise ValueError("An executable control has no executable parent event")
    if not actual.index.is_unique:
        raise ValueError("Parent signal IDs are not unique in the frozen runner")
    return runner, fixed_2r, signals


def _construct_symbol_events(symbol: str, runner: pd.DataFrame,
                             signals: pd.DataFrame, reward_config: dict,
                             batch_size: int) -> tuple[list[dict], dict]:
    signal_rows = signals[signals.symbol.astype(str).eq(str(symbol))]
    if signal_rows.empty:
        raise ValueError(f"No frozen signal row for runner symbol {symbol}")
    p12, p1, _source_group = engine._choose_input(
        symbol, bool(signal_rows.iloc[0].was_top20), reward_config)
    expected_12h = p12.relative_to(ROOT).as_posix()
    expected_1h = p1.relative_to(ROOT).as_posix()
    bars12, bars1 = engine._load_bars(p12, "12h"), engine._load_bars(p1, "1h")
    q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
    if len(q) == 0:
        raise ValueError(f"Executable frozen events have empty bars for {symbol}")
    hour_map = {int(t): i for i, t in enumerate(bar_ms)}
    actual = runner[~runner.is_control].set_index("parent_id", drop=False)
    symbol_events: list[dict] = []
    variant_map: dict[str, dict] = {}
    records = runner[runner.symbol.astype(str).eq(str(symbol))].sort_values(
        ["entry_ms", "event_id"], kind="stable").to_dict("records")
    for row in records:
        if str(row["source_path_12h"]) != expected_12h or str(row["source_path_1h"]) != expected_1h:
            raise ValueError(f"Frozen input-path mapping changed for {row['event_id']}")
        entry_ms = int(row["entry_ms"])
        entry_i = hour_map.get(entry_ms)
        if entry_i is None:
            raise ValueError(f"Frozen confirmation fill is absent from hourly bars: {row['event_id']}")
        entry_price = float(row["entry_price"])
        stop_price = float(row["stop_price"])
        side = int(row["side"])
        risk = float(row["risk_distance"])
        if (not np.isclose(float(q[entry_i, 0]), entry_price, rtol=1e-9, atol=1e-12)
                or not np.isclose(abs(entry_price - stop_price), risk, rtol=1e-9, atol=1e-12)):
            raise ValueError(f"Entry or frozen stop/risk does not align: {row['event_id']}")
        end_i, end_reason = engine._safe_path_end(entry_i, side, q, bar_ms)
        if row.get("path_upper_bound_reason") and end_reason != row["path_upper_bound_reason"]:
            raise ValueError(f"Frozen runner path cap changed: {row['event_id']}")

        source_row = signals.loc[int(row["source_row_number"])]
        extension = float(source_row.opposite_target)
        parent_entry = entry_price
        if bool(row["is_control"]):
            parent_row = actual.loc[row["parent_id"]]
            parent_entry = float(parent_row.entry_price)
            extension = transfer_parent_extension(
                extension, parent_entry, entry_price)

        event = dict(row)
        event.update({
            "entry_i": int(entry_i), "end_i": int(end_i),
            "entry_price": entry_price, "stop_price": stop_price,
            "risk_distance": risk,
            "risk_fraction": risk / entry_price,
            "path_upper_bound_reason": end_reason,
            "source_path_12h": expected_12h,
            "source_path_1h": expected_1h,
        })
        for fraction in FRACTIONS:
            mapped = target_geometry(entry_price, stop_price, extension, side, fraction)
            suffix = candidate_name(fraction)
            engine_event_id = f"{row['event_id']}::target::{suffix}"
            clone = dict(event)
            # _simulate_symbol maps tp_r=1 to this synthetic distance. We restore
            # original stop-risk R fields after the VectorBT path is returned.
            clone["event_id"] = engine_event_id
            clone["risk_distance"] = mapped["target_simulation_distance"]
            symbol_events.append(clone)
            variant_map[engine_event_id] = {
                **mapped,
                "event_id": row["event_id"],
                "candidate": suffix,
                "parent_signal_entry_price": parent_entry,
                "target_reference_transfer": (
                    "parent_relative_extension_to_control_entry" if bool(row["is_control"])
                    else "original_opposite_weekly_1p618_price"
                ),
                "target_parent_reference_price": float(source_row.opposite_target),
                "target_parent_entry_price": parent_entry,
                "target_is_control": bool(row["is_control"]),
                "target_fallback_2r": bool(mapped["target_fallback_2r"]),
            }

    rows, vectorbt_counts = engine._simulate_symbol(
        symbol_events, q, bar_ms, (1.0,), batch_size)
    if vectorbt_counts["trades"] != len(symbol_events):
        raise AssertionError(f"Target variants did not close every event for {symbol}")
    for row in rows:
        meta = variant_map.get(row["event_id"])
        if meta is None:
            raise AssertionError(f"VectorBT returned an unknown event variant: {row['event_id']}")
        original_risk = float(meta["original_risk_distance"])
        actual_target = row["target_price"]
        expected_target = meta["target_price"]
        if expected_target is None:
            if actual_target is not None and np.isfinite(float(actual_target)):
                raise AssertionError(f"Unplaceable target produced a finite order price: {row['event_id']}")
        elif not np.isclose(float(actual_target), float(expected_target),
                            rtol=1e-10, atol=1e-9):
            raise AssertionError(f"Synthetic target price differs from mapped geometry: {row['event_id']}")
        row["event_id"] = meta["event_id"]
        row["id"] = meta["event_id"]
        row["tp_r"] = 2.0  # target-child sentinel required by the frozen scale-out adapter
        row["risk_distance"] = original_risk
        row["risk_fraction"] = original_risk / float(row["entry_price"])
        row["gross_r"] = float(row["gross_pnl"]) / original_risk
        row["net_r"] = float(row["net_pnl"]) / original_risk
        row["target_fraction"] = float(meta["target_fraction"])
        row["target_candidate"] = meta["candidate"]
        row["target_geometry"] = meta["target_geometry"]
        row["target_reference_price"] = meta["target_reference_price"]
        row["target_reference_remaining_distance"] = meta["target_reference_remaining_distance"]
        row["target_relative_favorable_pct"] = meta["target_relative_favorable_pct"]
        row["target_distance"] = meta["target_distance"]
        row["target_requested_distance"] = meta["target_requested_distance"]
        row["target_requested_price"] = meta["target_requested_price"]
        row["target_expected_r"] = meta["target_expected_r"]
        row["target_fallback_2r"] = meta["target_fallback_2r"]
        row["target_level_already_passed"] = meta["target_level_already_passed"]
        row["target_no_resting_order"] = meta["target_no_resting_order"]
        row["target_no_order_reason"] = meta["target_no_order_reason"]
        row["target_fallback_reason"] = meta["target_fallback_reason"]
        row["target_reference_transfer"] = meta["target_reference_transfer"]
        row["target_parent_reference_price"] = meta["target_parent_reference_price"]
        row["target_parent_entry_price"] = meta["target_parent_entry_price"]
        if bool(meta["target_is_control"]):
            row["target_control_transferred_price_ratio"] = (
                meta["target_reference_price"] / float(row["entry_price"]))
        else:
            row["target_control_transferred_price_ratio"] = None
    del bars12, bars1, prepared, q
    gc.collect()
    return rows, vectorbt_counts


def generate_target_paths(config: dict, reward_config: dict,
                          runner: pd.DataFrame, fixed_2r: pd.DataFrame,
                          signals: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    batch_size = int(config["execution"]["event_batch_size"])
    result_rows: list[dict] = []
    total = Counter()
    for i, symbol in enumerate(sorted(runner.symbol.astype(str).unique()), start=1):
        rows, counts = _construct_symbol_events(
            symbol, runner, signals, reward_config, batch_size)
        result_rows.extend(rows)
        total.update(counts)
        if i % 25 == 0 or i == runner.symbol.nunique():
            print(f"Completed target paths for {i}/{runner.symbol.nunique()} symbols", flush=True)
        del rows
        gc.collect()
    paths = pd.DataFrame(result_rows)
    expected_rows = len(runner) * len(FRACTIONS)
    if len(paths) != expected_rows:
        raise AssertionError(f"Expected {expected_rows} target path rows, found {len(paths)}")
    if paths.duplicated(["event_id", "target_fraction"]).any():
        raise AssertionError("Target path identity must be unique by event and fraction")
    for fraction in FRACTIONS:
        variant = paths[paths.target_fraction.eq(fraction)]
        if set(variant.event_id) != set(runner.event_id):
            raise AssertionError(f"Target variant {fraction} does not cover the runner event set")
        fallback_orders = variant[variant.target_fallback_2r & ~variant.target_no_resting_order]
        no_orders = variant[variant.target_no_resting_order]
        _assert_path_parity(fallback_orders, fixed_2r,
                            f"{fraction} passed-level fixed-2R fallback")
        _assert_path_parity(no_orders, runner,
                            f"{fraction} nonpositive-price no-order runner parity")

    fallback_counts = {}
    for fraction in FRACTIONS:
        variant = paths[paths.target_fraction.eq(fraction)]
        no_orders = variant[variant.target_no_resting_order]
        fallback = variant[variant.target_fallback_2r]
        fallback_counts[candidate_name(fraction)] = {
            "already_passed_extension_2r_fallback": int(variant.target_level_already_passed.sum()),
            "already_passed_signal": int((variant.target_level_already_passed & ~variant.is_control).sum()),
            "already_passed_control": int((variant.target_level_already_passed & variant.is_control).sum()),
            "orderable_2r_fallback": int((fallback & ~variant.target_no_resting_order).sum()),
            "unplaceable_no_order": int(variant.target_no_resting_order.sum()),
            "unplaceable_extension_target": int(variant.target_geometry.eq("no_resting_order_nonpositive_extension_target").sum()),
            "unplaceable_extension_signal": int((variant.target_geometry.eq("no_resting_order_nonpositive_extension_target") & ~variant.is_control).sum()),
            "unplaceable_extension_control": int((variant.target_geometry.eq("no_resting_order_nonpositive_extension_target") & variant.is_control).sum()),
            "unplaceable_2r_fallback": int(variant.target_geometry.eq("no_resting_order_nonpositive_2r_fallback").sum()),
            "no_order_signal": int((~no_orders.is_control).sum()),
            "no_order_control": int(no_orders.is_control.sum()),
            "events": int(len(variant)),
        }
    for candidate, expected in config.get("expected_geometry_audit", {}).items():
        actual = fallback_counts.get(candidate)
        if actual is None:
            raise ValueError(f"Expected geometry audit names an unknown candidate: {candidate}")
        for field, value in expected.items():
            if int(actual[field]) != int(value):
                raise ValueError(
                    f"Frozen target-geometry count changed for {candidate}.{field}: "
                    f"{actual[field]} != {value}"
                )
    audit = {
        "input_runner_events": int(len(runner)),
        "input_signal_events": int((~runner.is_control).sum()),
        "input_matched_control_events": int(runner.is_control.sum()),
        "target_path_rows": int(len(paths)),
        "target_fallback_counts": fallback_counts,
        "vectorbt_execution_totals": dict(total),
        "fallback_path_parity": "Orderable passed-level events reproduce frozen fixed-2R paths; nonpositive projected prices produce no finite target and reproduce frozen 0R runner paths. Both parity checks include fills, fees, time, PnL, and ambiguity flags.",
    }
    return paths, audit


def _rank_low_prior_atr(frame: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    prior = signals["prior_atr_pct"]
    out["entry_order"] = out.source_row_number.map(prior)  # ascending = low prior ATR first
    out["entry_order"] = out.entry_order.fillna(np.inf)
    return out


def _run_account(paths: pd.DataFrame, fraction: float, slots: int,
                 slip: float, signals: pd.DataFrame):
    ranked = _rank_low_prior_atr(paths, signals)
    return capacity.run(ranked, slots, fraction, slip)


def _assert_path_parity(events: pd.DataFrame, reference: pd.DataFrame, label: str) -> None:
    if events.empty:
        return
    if events.event_id.duplicated().any() or reference.event_id.duplicated().any():
        raise AssertionError(f"{label} parity requires unique event IDs")
    left = events.set_index("event_id", drop=False).sort_index()
    right_indexed = reference.set_index("event_id", drop=False)
    if not left.index.isin(right_indexed.index).all():
        raise AssertionError(f"{label} has events missing from its frozen reference")
    right = right_indexed.loc[left.index]
    for field in EXIT_COMPARE_FIELDS:
        a, b = left[field], right[field]
        if field in {"exit_reason", "exit_phase", "exit_fee_class", "ambiguous_triggers"}:
            same = a.fillna("").astype(str).equals(b.fillna("").astype(str))
        elif field == "same_bar_ambiguous":
            same = a.fillna(False).astype(bool).equals(b.fillna(False).astype(bool))
        else:
            same = np.allclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float),
                               rtol=1e-9, atol=1e-8, equal_nan=True)
        if not same:
            raise AssertionError(f"{label} path differs from frozen reference field {field}")


def _period_rows(candidate: str, fraction: float | None, metrics: dict,
                 slots: int, policy: str, fallback_counts: dict | None = None) -> list[dict]:
    output = []
    for period, values in metrics.items():
        output.append({
            "candidate": candidate,
            "target_fraction": fraction,
            "partial_fraction": 0.75,
            "slots": slots,
            "priority_policy": policy,
            "period": period,
            "fallback_events": (fallback_counts or {}).get("already_passed_extension_2r_fallback"),
            "no_order_events": (fallback_counts or {}).get("unplaceable_no_order"),
            **values,
        })
    return output


def _baseline_metrics(baseline: pd.DataFrame, config: dict) -> pd.DataFrame:
    out = baseline.copy()
    out = out.rename(columns={"policy": "priority_policy"})
    out["candidate"] = BASELINE_CANDIDATE
    out["target_fraction"] = np.nan
    out["partial_fraction"] = float(config["account"]["partial_fraction"])
    out["slots"] = int(config["account"]["slots"])
    out["fallback_events"] = 0
    out["no_order_events"] = 0
    return out


def _parent_checks(config: dict, parent_summaries: dict) -> dict:
    parent = parent_summaries["priority"]
    if config["account"]["priority_policy"] not in parent["checks"]:
        raise ValueError("Frozen parent does not contain its control/stress/paired checks")
    return parent["checks"][config["account"]["priority_policy"]]


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    parser.add_argument("--check-mapping", action="store_true",
                        help="run only deterministic target-mapping assertions")
    args = parser.parse_args(argv)
    if args.check_mapping:
        print(json.dumps(check_target_mapping(), ensure_ascii=False, indent=2))
        return
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    config = _load_json(config_path)
    if tuple(float(x) for x in config["target_fractions_of_remaining_extension"]) != FRACTIONS:
        raise ValueError("Registered one-factor target fractions differ from the builder")
    outputs = {key: ROOT / value for key, value in config["outputs"].items()}
    existing = [str(p) for p in outputs.values() if p.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite frozen outputs: {existing}")

    reward_config, baseline, parent_summaries = _parent_contract(config)
    runner, fixed_2r, signals = _read_frozen_events(config)
    print(f"Frozen runner events: {len(runner)}; generating four target geometries", flush=True)
    target_paths, path_audit = generate_target_paths(
        config, reward_config, runner, fixed_2r, signals)
    outputs["target_paths_csv_gz"].parent.mkdir(parents=True, exist_ok=True)
    target_paths.to_csv(outputs["target_paths_csv_gz"], index=False,
                        compression="gzip", float_format="%.12g")

    rows = _baseline_metrics(baseline, config).to_dict("records")
    curves: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    fallback_counts = path_audit["target_fallback_counts"]
    slots = int(config["account"]["slots"])
    partial = float(config["account"]["partial_fraction"])
    priority = str(config["account"]["priority_policy"])
    for fraction in FRACTIONS:
        name = candidate_name(fraction)
        targets = target_paths[target_paths.target_fraction.eq(fraction)]
        pool = pd.concat([runner, targets], ignore_index=True, sort=False)
        real = pool[~pool.is_control].copy()
        metrics, trades, equity = _run_account(real, partial, slots, 0.0, signals)
        rows.extend(_period_rows(name, fraction, metrics, slots, priority,
                                 fallback_counts[name]))
        curves[name] = (trades, equity)
        print(f"Accounted {name}", flush=True)
        gc.collect()

    frame = pd.DataFrame(rows)
    dev = frame[frame.period.eq("dev")].copy()
    dev = dev.sort_values(["multiple", "candidate"], ascending=[False, True], kind="stable")
    eligible = dev[dev.apply(wallet.qualifies, axis=1) & dev.trades.ge(100)]
    pick = str(eligible.candidate.iloc[0]) if len(eligible) else BASELINE_CANDIDATE
    best = str(dev.candidate.iloc[0])
    frame.to_csv(outputs["account_metrics_csv"], index=False)

    checks = {}
    for name in sorted({pick, best}):
        if name == BASELINE_CANDIDATE:
            checks[name] = _parent_checks(config, parent_summaries)
            continue
        fraction = next(f for f in FRACTIONS if candidate_name(f) == name)
        targets = target_paths[target_paths.target_fraction.eq(fraction)]
        pool = pd.concat([runner, targets], ignore_index=True, sort=False)
        real, control = pool[~pool.is_control], pool[pool.is_control]
        paired = real[real.parent_id.isin(control.parent_id)]
        checks[name] = {
            "control": _run_account(control, partial, slots, 0.0, signals)[0],
            "paired_signals": _run_account(paired, partial, slots, 0.0, signals)[0],
            "slippage_5bp": _run_account(real, partial, slots, 0.0005, signals)[0],
        }
        gc.collect()
        if name in curves:
            trades, equity = curves[name]
            trades.to_csv(EXP / f"funded_{name}.csv.gz", index=False, compression="gzip")
            equity.to_csv(EXP / f"hourly_{name}.csv.gz", index_label="hour_end_utc")

    baseline_rows = frame[(frame.candidate.eq(BASELINE_CANDIDATE)) & frame.period.eq("all")]
    summary = {
        "experiment_id": config["experiment_id"],
        "question": config["question"],
        "selection": {
            "dev_period": "entry and exit before 2025-01-01 UTC; parent wallet selection rules",
            "constraints": "win_pct >= 30, equal-notional payoff >= 2.5, conservative dd_bound_pct <= 35, trades >= 100",
            "eligible": eligible.candidate.tolist(),
            "pick": pick,
            "best_dev_growth": best,
            "baseline_fixed_2r": BASELINE_CANDIDATE,
        },
        "frozen_parent": {
            "partial_fraction": partial,
            "slots": slots,
            "priority_policy": priority,
            "baseline_all": baseline_rows.iloc[0].to_dict() if len(baseline_rows) else None,
            "fixed_2r_curves": "experiments/active/exp-cloud-close-priority-20261109-v1/funded_low_prior_atr.csv.gz and hourly_low_prior_atr.csv.gz",
        },
        "target_geometry": {
            "fractions_of_remaining_entry_to_extension": list(FRACTIONS),
            "passed_extension_fallback": "for that event, every candidate uses the frozen fixed 2R level when it is orderable; verified against the original VectorBT 2R path",
            "unplaceable_limit": "zero or negative projected price means no resting target order; the 75% leg follows the frozen 0R runner, verified against runner fills including controls",
            "control_transfer": "at control open, scale the parent signal's opposite-reference price by control_entry / parent_actual_confirmation_entry; then apply that event's target fraction",
            "partial_position": "75% closes at the candidate resting limit when its price is positive; a nonpositive limit leaves that 75% leg on the exact frozen 0R cloud runner, which also carries the remaining 25%",
            "target_path_tp_r_sentinel": "2.0 solely for the frozen pair/scale-out adapter; target_fraction, target_geometry, target_price, and target_distance contain the actual tested geometry",
        },
        "path_audit": path_audit,
        "checks": checks,
        "costs_and_limits": {
            "starting_equity_usdt": 1000,
            "market_entry_and_exit_fee_per_fill": 0.0005,
            "resting_limit_exit_fee_per_fill": 0.0002,
            "slippage_stress": "additional 5bp per taker fill; maker limit exits do not receive market slippage",
            "funding": "not modeled",
            "capacity": "shared 1000U, nine concurrent position slots, parent same-symbol and same-time ordering; no exchange depth or market-impact model",
            "fills": "OHLC target touches are treated as resting-limit fills at target price; real queue position and non-fill probability are not modeled",
            "liquidation": "no exchange-specific maintenance-margin tier/liquidation model; this experiment changes exit geometry only and does not imply leveraged liquidation safety",
            "ohlc_ordering": "inherits the frozen VectorBT engine's opening-trigger and intrabar priority, ambiguity flags, and exact stop/cloud semantics",
        },
        "outputs": {key: str(path.relative_to(ROOT)) for key, path in outputs.items()},
    }
    outputs["summary_json"].write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(frame[frame.period.eq("all")].to_string(index=False), flush=True)
    print(json.dumps({"pick": pick, "best_dev_growth": best,
                      "eligible": eligible.candidate.tolist(),
                      "fallbacks": path_audit["target_fallback_counts"]},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
