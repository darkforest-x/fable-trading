"""Vary only the original Binance V1.7 account's initial 2R child fraction.

The replay uses the frozen capacity-funded Binance parent roster, original
four-child VectorBT paths and the archived source-timestamped funding tape.
The runner's historical 2/3 allocation is reused as baseline; this builder
replays only the preregistered 1/3 and 5/6 allocations.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_capacity_funding as capacity
from yoyo.evaluation import cloud_close_binance_relative_funding as relative

ROOT = relative.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-binance-target-fraction-20261109-v1"
CONFIG_PATH = EXP / "config.json"
BASE_CONFIG_PATH = capacity.CONFIG_PATH
BASE_EXP = capacity.EXP
SOURCE_ROOT = ROOT / "experiments/active/exp-signal-center-score-20261106-v1/results/symbols"
PRIORITY_PATH = ROOT / "experiments/active/exp-cloud-close-fvg-priority-20261109-v1/priority.csv.gz"
PRIORITY_FUNDED_PATH = ROOT / "experiments/active/exp-cloud-close-fvg-priority-20261109-v1/funded.csv.gz"
NATIVE_CHILDREN_PATH = ROOT / "experiments/active/exp-cloud-close-fvg-priority-funding-20261109-v1/child_windows.csv.gz"
EXPECTED_BASE_WEIGHTS = np.asarray([1 / 3, 1 / 3, 1 / 6, 1 / 6], dtype=np.float64)
TARGET_FRACTIONS = {"target_1_3": 1 / 3, "target_5_6": 5 / 6}
SCENARIOS = (
    (0.0, "include_boundary"),
    (0.0, "exclude_boundary"),
    (0.0005, "include_boundary"),
    (0.0005, "exclude_boundary"),
)
def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _weights(target_fraction: float) -> np.ndarray:
    value = float(target_fraction)
    if not 0.0 < value < 1.0:
        raise ValueError("2R target fraction must lie strictly between zero and one")
    result = np.asarray([value / 2.0, value / 2.0, (1.0 - value) / 2.0,
                         (1.0 - value) / 2.0], dtype=np.float64)
    if not np.isclose(result.sum(), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("Four child fractions do not sum to the original parent size")
    return result


def _validate_config(config: dict) -> None:
    if config.get("id") != EXP.name or config.get("venue") != "Binance USD-M":
        raise ValueError("Target-fraction experiment identity or venue changed")
    if config.get("status") != "preregistered_frozen_inputs":
        raise ValueError("Target-fraction configuration is not the frozen preregistration")
    arms = config.get("arms", {})
    if set(arms) != {"baseline_2_3_reused", "primary_1_3", "opposite_control_5_6"}:
        raise ValueError("Target-fraction arms changed")
    expected = {
        "baseline_2_3_reused": 2 / 3,
        "primary_1_3": 1 / 3,
        "opposite_control_5_6": 5 / 6,
    }
    for name, fraction in expected.items():
        arm = arms[name]
        if not np.isclose(float(arm.get("target_fraction", np.nan)), fraction, rtol=0, atol=1e-12):
            raise ValueError(f"Preregistered target fraction changed for {name}")
        weights = np.asarray(arm.get("child_weights", []), dtype=np.float64)
        if weights.shape != (4,) or not np.allclose(weights, _weights(fraction), rtol=0, atol=1e-12):
            raise ValueError(f"Four child weights do not encode the preregistered f for {name}")
    if arms["baseline_2_3_reused"].get("run_again") is not False:
        raise ValueError("The frozen 2/3 baseline must be reused instead of replayed")
    account = config.get("account", {})
    expected_account = {
        "initial_cash_usdt": 1000.0,
        "original_signal_parent_denominator": 1586,
        "capacity_eligible_positions": 1583,
        "capacity_unknown_positions_skipped_without_replacement": 3,
        "children_per_parent": 4,
        "expected_child_legs": 6332,
        "slots": 9,
        "entry_capacity_fraction": 0.1,
        "single_contract_hourly_nav_trim_fraction": 0.2,
    }
    for key, value in expected_account.items():
        if not np.isclose(float(account.get(key, np.nan)), float(value), rtol=0, atol=1e-12):
            raise ValueError(f"Frozen account parameter changed: {key}")
    if config.get("funding", {}).get("boundary_scenarios") != ["include_boundary", "exclude_boundary"]:
        raise ValueError("Funding boundary scenarios changed")
    if config.get("fees") != {
        "taker": 0.0005,
        "resting_maker": 0.0002,
        "additional_market_slippage_stress": 0.0005,
    }:
        raise ValueError("Fee or slippage stress assumptions changed")
    base_cfg = _json(BASE_CONFIG_PATH)
    capacity._validate_config(base_cfg)
    if (config.get("inputs", {}).get("base_capacity_funding_config")
            != str(BASE_CONFIG_PATH.relative_to(ROOT))):
        raise ValueError("Frozen capacity/funding runner input changed")
    baseline_config = base_cfg.get("account", {})
    if (baseline_config.get("child_weights") != EXPECTED_BASE_WEIGHTS.tolist()
            or not np.isclose(float(baseline_config.get("single_contract_hourly_cap_fraction", -1)), 0.2)
            or not np.isclose(float(baseline_config.get("entry_capacity_fraction", -1)), 0.1)):
        raise ValueError("The reused baseline does not preserve the frozen 2/3 split and both capacity rules")
    input_paths = config.get("inputs", {})
    for key, expected_path in {
        "base_capacity_funding_summary": BASE_EXP / "summary.json",
        "capacity_panel": capacity.CAPACITY_EXP / "entry_capacity.csv.gz",
        "capacity_funded_reference": capacity.CAPACITY_EXP / "funded_cap_10pct.csv.gz",
        "capacity_hourly_reference": capacity.CAPACITY_EXP / "hourly_cap_10pct.csv.gz",
        "native_original_children": NATIVE_CHILDREN_PATH,
        "frozen_priority_roster": PRIORITY_PATH,
        "raw_signal_archive": SOURCE_ROOT,
    }.items():
        if input_paths.get(key) != str(expected_path.relative_to(ROOT)):
            raise ValueError(f"Frozen input path changed: {key}")
    for key in ("funding_tape", "source_months", "source_coverage"):
        if input_paths.get(key) != base_cfg["inputs"][key]:
            raise ValueError(f"Target-fraction replay changed the frozen funding source: {key}")


def _assert_committed() -> None:
    paths = [
        Path(__file__).resolve(), CONFIG_PATH.resolve(),
        Path(relative.__file__).resolve(), Path(capacity.__file__).resolve(),
    ]
    for path in paths:
        rel = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT,
                          check=False).returncode:
            raise RuntimeError(f"Commit the target-fraction builder/config/weight adapter first: {rel}")


def _event_clock_audit(panel: pd.DataFrame, selected: pd.DataFrame,
                       original_reference: pd.DataFrame) -> dict:
    """Join exact frozen parents to the 12H source key and original native legs.

    The archived per-symbol signal CSVs carry the original `close_ns`, NY week,
    Monday-width and signal stop. Some retired-symbol signal files are absent;
    those rows remain in the frozen parent/path receipts and are counted as
    unresolved direct signal-source joins, never removed from the account.
    """
    panel_events = panel[["event_id", "symbol", "side", "entry_ms", "status"]].copy()
    panel_events["event_id"] = panel_events.event_id.astype(str)
    if len(panel_events) != 1586 or panel_events.event_id.duplicated().any():
        raise ValueError("Capacity panel no longer contains 1,586 unique signal event keys")

    roster = pd.read_csv(PRIORITY_PATH, usecols=["event_id", "entry_ms"], dtype={"event_id": str})
    if roster.event_id.duplicated().any():
        raise ValueError("Frozen priority source roster contains duplicate event keys")
    priority = roster.set_index("event_id").entry_ms
    original_ids = set(panel_events.event_id)
    if not original_ids.issubset(set(priority.index)):
        raise ValueError("Capacity parents are not a subset of the frozen 12H priority signal roster")
    if not np.array_equal(priority.reindex(panel_events.event_id).to_numpy(dtype=np.int64),
                          panel_events.entry_ms.to_numpy(dtype=np.int64)):
        raise ValueError("Frozen priority roster does not preserve parent signal confirmation clocks")

    funded_ids = set(original_reference.event_id.astype(str))
    if funded_ids != original_ids or len(original_reference) != 1586:
        raise ValueError("Frozen Binance native 1x/2x source reference changed its original 1,586 signal parents")

    original = pd.read_csv(NATIVE_CHILDREN_PATH, dtype={"event_id": str, "parent_id": str,
                                                        "symbol": str})
    required = {"event_id", "parent_id", "symbol", "side", "entry_ms", "entry_price",
                "stop_price", "tp_r", "slot"}
    if required - set(original):
        raise ValueError("Frozen original native child path schema changed")
    if (len(original) != 3172 or original.duplicated(["event_id", "tp_r"]).any()
            or not original.groupby("event_id").size().eq(2).all()
            or set(original.event_id.astype(str)) != original_ids):
        raise ValueError("Original native source is not the full two-leg path for all 1,586 signal parents")
    native_child_row_count = int(len(original))
    runner = original.loc[original.tp_r.eq(0)].set_index("event_id")
    if not np.array_equal(runner.entry_ms.reindex(panel_events.event_id).to_numpy(dtype=np.int64),
                          panel_events.entry_ms.to_numpy(dtype=np.int64)):
        raise ValueError("Original native path entry clocks differ from the original capacity signal keys")
    for row in panel_events.itertuples(index=False):
        parts = str(row.event_id).split("|")
        if (len(parts) != 4 or parts[0] != "12H" or parts[1] != str(row.symbol) + "USDT"
                or int(parts[2]) % 1_000_000 != 0
                or int(parts[2]) // 1_000_000 != int(row.entry_ms)
                or int(parts[3]) != int(row.side)):
            raise ValueError(f"Event identity does not encode its exact native 12H signal clock: {row.event_id}")
    original = original_reference.set_index("event_id", drop=False)
    if not np.array_equal(original.symbol.reindex(runner.index).astype(str).to_numpy(),
                          runner.symbol.astype(str).to_numpy()):
        raise ValueError("Frozen native account parent and source-child symbol labels differ")
    for field in ("parent_id", "side", "entry_ms"):
        left = original[field].reindex(runner.index).to_numpy()
        right = runner[field].to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f"Original source child and native account differ in {field}")
    if (len(selected) != 6332 or selected.duplicated(["event_id", "slot"]).any()
            or not selected.groupby("event_id").size().eq(4).all()):
        raise ValueError("Capacity-selected native account does not keep four original child legs")
    eligible_ids = set(selected.event_id.astype(str))
    eligible_runner = runner.loc[sorted(eligible_ids)]
    for field in ("parent_id", "symbol", "side", "entry_ms", "entry_price", "stop_price"):
        per_parent = selected.groupby("event_id", sort=False)[field].nunique(dropna=False)
        if not per_parent.eq(1).all():
            raise ValueError(f"Capacity-selected child rows disagree on original {field}")
        values = selected.drop_duplicates("event_id").set_index("event_id")[field].reindex(eligible_runner.index)
        if not np.array_equal(values.to_numpy(), eligible_runner[field].to_numpy()):
            raise ValueError(f"Capacity-selected child path changed original signal {field}")

    signal_parts = []
    files_missing: list[str] = []
    for symbol, group in panel_events.groupby("symbol", sort=False):
        path = SOURCE_ROOT / (str(symbol) + "USDT") / "signals.csv.gz"
        if not path.is_file():
            files_missing.append(str(symbol))
            continue
        frame = pd.read_csv(
            path,
            usecols=["signal_id", "close_ns", "side", "stop", "tf", "week",
                     "monday_width_pct", "hours_since_monday", "symbol"],
            dtype={"signal_id": str, "tf": str, "week": str, "symbol": str},
        )
        keep = set(group.event_id)
        frame = frame.loc[frame.signal_id.isin(keep)]
        if len(frame):
            signal_parts.append(frame)
    if signal_parts:
        signals = pd.concat(signal_parts, ignore_index=True)
    else:
        signals = pd.DataFrame(columns=["signal_id", "close_ns", "side", "stop", "tf", "week",
                                        "monday_width_pct", "hours_since_monday", "symbol"])
    if signals.signal_id.duplicated().any():
        raise ValueError("Original archived signal IDs are not unique across per-symbol source files")
    signal_rows = signals.set_index("signal_id", drop=False)
    direct_ids = set(signal_rows.index.astype(str))
    if not direct_ids.issubset(original_ids):
        raise ValueError("Raw signal source contains event keys outside the frozen capacity parent set")
    resolved = panel_events.loc[panel_events.event_id.isin(direct_ids)].set_index("event_id")
    signal_rows = signal_rows.reindex(resolved.index)
    src_close_ms = pd.to_numeric(signal_rows.close_ns, errors="raise").to_numpy(dtype=np.int64) // 1_000_000
    key_open_ny = pd.to_datetime(src_close_ms - 12 * 60 * 60 * 1000, unit="ms", utc=True).tz_convert(
        "America/New_York"
    )
    expected_weeks = (key_open_ny.normalize() - pd.to_timedelta(key_open_ny.day_of_week, unit="D")).strftime(
        "%Y-%m-%d"
    )
    if (not signal_rows.tf.eq("12H").all()
            or not np.array_equal(src_close_ms, resolved.entry_ms.to_numpy(dtype=np.int64))
            or not np.array_equal(pd.to_numeric(signal_rows.side).to_numpy(dtype=np.int64),
                                  resolved.side.to_numpy(dtype=np.int64))
            or not np.array_equal(signal_rows.symbol.astype(str).to_numpy(),
                                  (resolved.symbol.astype(str) + "USDT").to_numpy())
            or not np.array_equal(expected_weeks.to_numpy(), signal_rows.week.astype(str).to_numpy())):
        raise ValueError("Direct archived signal row is not the same 12H parent and confirmation clock")
    source_stop = pd.to_numeric(signal_rows.stop, errors="raise").to_numpy(dtype=np.float64)
    path_stop = runner.stop_price.reindex(resolved.index).to_numpy(dtype=np.float64)
    if not np.allclose(source_stop, path_stop, rtol=1e-12, atol=1e-12):
        raise ValueError("Direct source 1.618 protective stop differs from the frozen native path")
    if (signal_rows.week.isna().any() or signal_rows.monday_width_pct.isna().any()
            or signal_rows.hours_since_monday.isna().any()):
        raise ValueError("Resolved original signal rows lack their NY-week/Monday clock features")

    return {
        "source_event_key": "12H|symbolUSDT|confirmation_ms_as_ns|side",
        "signal_to_capacity_join": "event_id == parent_id == archived signal_id; event key milliseconds equal source close_ns/1e6 and native H1 entry_ms.",
        "original_parent_denominator": len(panel_events),
        "frozen_priority_roster_rows": int(len(roster)),
        "all_parent_ids_in_frozen_priority_roster": True,
        "native_original_two_leg_rows": native_child_row_count,
        "native_original_parent_count": int(original.event_id.nunique()),
        "eligible_four_child_path_count": int(selected.event_id.nunique()),
        "eligible_four_child_parent_clocks_match_native_signal_path": True,
        "source_capacity_parent_ids_match": True,
        "raw_signal_source_rows_directly_matched": int(len(direct_ids)),
        "raw_signal_source_rows_unresolved": int(len(original_ids - direct_ids)),
        "unresolved_direct_source_by_reason": {
            "per_symbol_signal_file_missing": int(sum(
                1 for event_id in original_ids - direct_ids
                if event_id.split("|")[1].removesuffix("USDT") in set(files_missing)
            )),
            "signal_id_not_present_in_existing_per_symbol_file": int(sum(
                1 for event_id in original_ids - direct_ids
                if event_id.split("|")[1].removesuffix("USDT") not in set(files_missing)
            )),
        },
        "symbols_with_missing_signal_file": sorted(files_missing),
        "direct_rows_all_12h": True,
        "direct_rows_confirmation_clock_equals_entry": True,
        "direct_rows_side_and_symbol_match": True,
        "direct_rows_1_618_stop_matches_native_path": True,
        "direct_rows_have_ny_week_monday_width_and_hours_since_monday": True,
        "direct_rows_ny_week_matches_key_open_week": True,
        "direct_signal_rows_are_source_audit_only_not_an_entry_filter": True,
        "qualification_uses_only_frozen_prior_minute_capacity_status": {
            str(k): int(v) for k, v in panel_events.status.value_counts().to_dict().items()
        },
    }


def _validate_base_arm(prepared: dict, config: dict) -> dict:
    summary_path = BASE_EXP / "summary.json"
    summary = _json(summary_path)
    if summary.get("status") != "complete" or summary.get("config_id") != BASE_EXP.name:
        raise ValueError("Frozen 2/3 capacity/funding baseline has no complete receipt")
    if summary.get("account_roster_audit", {}).get("positions") != 1583:
        raise ValueError("Frozen 2/3 capacity/funding baseline parent count changed")
    base_account = _json(BASE_CONFIG_PATH)["account"]
    if base_account.get("child_weights") != EXPECTED_BASE_WEIGHTS.tolist():
        raise ValueError("Reused baseline is not the 2/3 target fraction")
    baseline_scenarios = {}
    expected_names = {"include_boundary", "exclude_boundary", "stress_include_boundary", "stress_exclude_boundary"}
    if set(summary.get("scenarios", {})) != expected_names:
        raise ValueError("Frozen baseline does not contain all funding-boundary and slippage scenarios")
    for name in sorted(expected_names):
        folder = BASE_EXP / name
        required = ("funded_legs.csv.gz", "funded_positions.csv.gz", "applied_funding.csv.gz",
                    "hourly_equity.csv.gz", "metrics.json")
        if not all((folder / filename).is_file() for filename in required):
            raise FileNotFoundError(f"Frozen baseline output missing from {name}")
        old = capacity._resume_scenario(folder)
        if old is None:
            raise ValueError(f"Frozen baseline scenario is not independently auditable: {name}")
        legs = pd.read_csv(folder / "funded_legs.csv.gz", dtype={"event_id": str, "symbol": str})
        _assert_child_weights(legs, EXPECTED_BASE_WEIGHTS, expected_positions=1583)
        if set(legs.event_id.astype(str)) != set(prepared["capacity_audit"]["eligible_parent_ids"]):
            raise ValueError("Reused 2/3 baseline eligible IDs differ from the frozen capacity roster")
        baseline_scenarios[name] = old
    return {
        "experiment": str(BASE_EXP.relative_to(ROOT)),
        "child_weights": EXPECTED_BASE_WEIGHTS.tolist(),
        "target_fraction": 2 / 3,
        "rerun": False,
        "scenarios": baseline_scenarios,
        "parent_ids_match_new_runs": True,
        "all_baseline_child_entry_notional_fractions_match_weights": True,
    }


def _assert_child_weights(children: pd.DataFrame, weights: np.ndarray, *, expected_positions: int) -> dict:
    required = {"event_id", "parent_id", "symbol", "side", "slot", "child_leg", "child_role",
                "entry_notional_usdt", "entry_signed_size", "entry_fee_usdt", "funding_cashflow_usdt"}
    if required - set(children):
        raise ValueError("Native VectorBT child ledger is missing actual quantity and cash fields")
    if len(children) != expected_positions * 4 or children.duplicated(["event_id", "slot"]).any():
        raise ValueError("Actual child ledger does not contain four unique legs per parent")
    if not children.groupby("event_id").size().eq(4).all():
        raise ValueError("A native parent has lost one or more child legs")
    ordered = children.sort_values(["event_id", "child_leg"])
    observed = ordered.groupby("event_id", sort=False).entry_notional_usdt.transform(
        lambda values: values.to_numpy(dtype=np.float64) / values.sum()
    ).to_numpy(dtype=np.float64).reshape(expected_positions, 4)
    unit_observed = ordered.groupby("event_id", sort=False).entry_signed_size.transform(
        lambda values: values.abs().to_numpy(dtype=np.float64) / values.abs().sum()
    ).to_numpy(dtype=np.float64).reshape(expected_positions, 4)
    errors = np.abs(observed - weights.reshape(1, 4))
    unit_errors = np.abs(unit_observed - weights.reshape(1, 4))
    if (not np.all(np.isfinite(observed)) or not np.all(np.isfinite(unit_observed))
            or float(errors.max(initial=0.0)) > 1e-8
            or float(unit_errors.max(initial=0.0)) > 1e-8):
        raise ValueError("Actual entry notional/native units do not follow the configured child weights")
    child_order_ok = ordered.groupby("event_id", sort=False).child_leg.apply(
        lambda values: tuple(values.to_numpy(dtype=np.int64)) == (0, 1, 2, 3)
    )
    if not child_order_ok.all():
        raise ValueError("Child role order changed in the funded output")
    sums = ordered.groupby("event_id", sort=False).entry_notional_usdt.sum()
    if not np.isfinite(sums.to_numpy(dtype=np.float64)).all() or sums.le(0.0).any():
        raise ValueError("Funded native parent has nonpositive or invalid initial notional")
    return {
        "parent_count": int(len(sums)),
        "children_per_parent": 4,
        "configured_initial_native_notional_fractions": weights.tolist(),
        "configured_initial_native_unit_fractions": weights.tolist(),
        "max_absolute_child_fraction_error": float(errors.max(initial=0.0)),
        "max_absolute_native_unit_fraction_error": float(unit_errors.max(initial=0.0)),
        "all_native_child_entry_sizes_match_configured_weights": True,
    }


def _same_path_set(children: pd.DataFrame, selected: pd.DataFrame) -> None:
    old = selected.set_index(["event_id", "slot"]).sort_index()
    new = children.set_index(["event_id", "slot"]).sort_index()
    if not old.index.equals(new.index):
        raise ValueError("Changing target fraction altered event or child-slot admission")
    for field in ("parent_id", "symbol", "side", "entry_ms", "exit_bar_ms", "exit_phase", "exit_reason"):
        left = old[field].to_numpy()
        right = new[field].to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f"Changing target fraction altered frozen path identity or {field}")


def _prepare(config: dict) -> dict:
    base_config = _json(BASE_CONFIG_PATH)
    prepared = capacity._prepare_real_account(base_config)
    panel = prepared["panel"]
    original_reference = _read_original_reference(base_config)
    signal_audit = _event_clock_audit(panel, prepared["selected"], original_reference)
    if len(prepared["reference"]) != 1583 or len(prepared["selected"]) != 6332:
        raise ValueError("Prepared source no longer has 1,583 observed positions and four child legs")
    if prepared["coverage_audit"]["covered_child_legs"] != 6332:
        raise ValueError("Funding tape is not complete for every retained native child leg")
    if prepared["entry_matrix_audit"]["entry_capacity_parents"] != 1583:
        raise ValueError("Prior-minute entry limits did not reach every eligible native parent")
    baseline = _validate_base_arm(prepared, config)
    selected = prepared["selected"].copy()
    child_id = selected.slot.astype(int) % 4
    if (not selected.loc[child_id.lt(2), "tp_r"].eq(2.0).all()
            or not selected.loc[child_id.ge(2), "tp_r"].eq(0.0).all()
            or selected.groupby("event_id").side.nunique().gt(1).any()
            or selected.is_control.astype(bool).any()):
        raise ValueError("Frozen native V1.7 target and runner child roles changed")
    prepared["selected"] = selected
    prepared["signal_source_audit"] = signal_audit
    prepared["baseline_reuse"] = baseline
    return prepared


def _read_original_reference(config: dict) -> pd.DataFrame:
    path = ROOT / config["inputs"]["original_funded_parent_reference"]
    if not path.is_file():
        raise FileNotFoundError(f"Frozen original 1x/2x event reference is missing: {path}")
    frame = pd.read_csv(path, dtype={"event_id": str, "parent_id": str, "symbol": str})
    if len(frame) != 1586 or frame.event_id.duplicated().any():
        raise ValueError("Frozen original Binance reference must contain 1,586 unique source parents")
    return frame


def _audit_candidate_arm(prepared: dict, children: pd.DataFrame, parents: pd.DataFrame,
                         flows: pd.DataFrame, equity: pd.DataFrame,
                         result: dict, weights: np.ndarray) -> dict:
    selected = prepared["selected"]
    _same_path_set(children, selected)
    weight_audit = _assert_child_weights(children, weights, expected_positions=1583)
    if (len(parents) != 1583 or parents.index.duplicated().any()
            or set(parents.index.astype(str)) != prepared["capacity_audit"]["eligible_parent_ids"]):
        raise ValueError("Candidate arm changed the observed-capacity parent roster")
    limits = prepared["capacity_audit"]["entry_limits"]
    if not np.array_equal(parents.index.astype(str).to_numpy(), limits.reindex(parents.index).index.astype(str).to_numpy()):
        raise ValueError("Capacity limits do not align with funded parent rows")
    if (parents.notional.to_numpy(dtype=np.float64) > limits.reindex(parents.index).to_numpy(dtype=np.float64) + 1e-6).any():
        raise ValueError("Candidate arm exceeded the observed 10% prior-minute capacity cap")
    if (len(equity) != 58992 or not equity.index.is_unique
            or not equity.index.equals(prepared["reference_equity"].index)):
        raise ValueError("Candidate arm does not use the frozen native Binance H1 clock")
    if result["audit"]["hourly_single_contract_cap_fraction"] != 0.20:
        raise ValueError("Candidate arm did not preserve the 20% H1 single-contract trim")
    if not result["audit"]["entry_capacity_limit_active"]:
        raise ValueError("Candidate arm did not preserve prior-minute capacity limits")
    bridge = capacity._cash_bridge(parents, flows, equity)
    applied = flows.loc[flows.applied] if len(flows) else flows
    applied_funding = float(applied.funding_cashflow_usdt.sum()) if len(applied) else 0.0
    if not np.isclose(applied_funding, result["audit"]["funding_cashflow_usdt"], rtol=1e-9, atol=1e-7):
        raise ValueError("Applied funding ledger differs from native VectorBT account settlement cash")
    if (result["audit"]["child_legs"] != 6332 or bridge["account_bridge_error_usdt"] > 0.02
            or bridge["parent_ledger_bridge_error_usdt"] > 0.02):
        raise ValueError("Candidate arm failed child count or actual orders/funding cash reconciliation")
    result["audit"]["cash_bridge"] = bridge
    result["audit"]["target_fraction"] = float(weights[0] + weights[1])
    result["audit"]["child_weights"] = weights.tolist()
    result["audit"]["child_weight_quantity_audit"] = weight_audit
    result["audit"]["same_original_v17_event_and_exit_paths"] = True
    result["audit"]["source_parent_count"] = 1586
    result["audit"]["capacity_funded_parent_count"] = 1583
    return weight_audit


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def _write_frame(path: Path, frame: pd.DataFrame, *, index: bool = False,
                 index_label: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(temp, index=index, index_label=index_label, compression=compression)
    temp.replace(path)


def _resume(folder: Path, weights: np.ndarray, prepared: dict) -> dict | None:
    required = ("funded_legs.csv.gz", "funded_positions.csv.gz", "applied_funding.csv.gz",
                "hourly_equity.csv.gz", "orders.csv.gz", "metrics.json")
    if not all((folder / name).is_file() for name in required):
        return None
    saved = _json(folder / "metrics.json")
    audit = saved.get("audit", {})
    if (audit.get("child_weights") != weights.tolist()
            or not np.isclose(float(audit.get("target_fraction", np.nan)), float(weights[:2].sum()), atol=1e-12)):
        raise ValueError(f"Partial checkpoint has a different frozen target fraction: {folder}")
    children = pd.read_csv(folder / "funded_legs.csv.gz", dtype={"event_id": str})
    parents = relative._read_parent_reference(folder / "funded_positions.csv.gz")
    flows = pd.read_csv(folder / "applied_funding.csv.gz")
    if "applied" in flows:
        flows["applied"] = relative._bool_column(flows.applied, "saved_target_fraction.applied")
    equity = relative._read_equity_reference(folder / "hourly_equity.csv.gz")
    _audit_candidate_arm(prepared, children, parents, flows, equity,
                         {"metrics": saved["metrics"], "audit": audit}, weights)
    return saved


def _run() -> dict:
    if (EXP / "summary.json").exists():
        raise FileExistsError(f"Completed target-fraction study already exists: {EXP}")
    config = _json(CONFIG_PATH)
    _validate_config(config)
    _assert_committed()
    prepared = _prepare(config)
    EXP.mkdir(parents=True, exist_ok=True)
    arms: dict[str, dict] = {}
    for arm_name, fraction in TARGET_FRACTIONS.items():
        weights = _weights(fraction)
        arm: dict = {"target_fraction": fraction, "child_weights": weights.tolist(), "scenarios": {}}
        for slip, boundary in SCENARIOS:
            scenario_name = ("stress_" if slip else "") + boundary
            folder = EXP / arm_name / scenario_name
            saved = _resume(folder, weights, prepared)
            if saved is not None:
                arm["scenarios"][scenario_name] = saved
                print(arm_name, scenario_name, "reused verified checkpoint", json.dumps({
                    "final_usdt": saved["metrics"]["all"]["final_usdt"],
                    "dd_bound_pct": saved["metrics"]["all"]["dd_bound_pct"],
                    "funding_usdt": saved["audit"]["cash_bridge"]["applied_funding_cashflow_usdt"],
                }, ensure_ascii=False), flush=True)
                continue
            children, parents, equity, flows, result = relative._run_scenario(
                prepared["data"], prepared["tape"], boundary, slip, prepared["multipliers"],
                prepared["previous_close"], prepared["native_open"], prepared["directions"],
                prepared["no_settlement"], hourly_cap_fraction=0.20,
                entry_limits=prepared["entry_matrix"], child_weights=weights,
            )
            _audit_candidate_arm(prepared, children, parents, flows, equity, result, weights)
            _write_frame(folder / "funded_legs.csv.gz", children)
            _write_frame(folder / "funded_positions.csv.gz", parents.reset_index(names="event_id"))
            _write_frame(folder / "applied_funding.csv.gz", flows)
            _write_frame(folder / "hourly_equity.csv.gz", equity, index=True, index_label="hour_end_utc")
            _write_frame(folder / "orders.csv.gz", result["orders"])
            receipt = {"metrics": result["metrics"], "audit": result["audit"]}
            _write_json(folder / "metrics.json", receipt)
            arm["scenarios"][scenario_name] = receipt
            print(arm_name, scenario_name, json.dumps({
                "final_usdt": result["metrics"]["all"]["final_usdt"],
                "dd_bound_pct": result["metrics"]["all"]["dd_bound_pct"],
                "win_pct": result["metrics"]["all"]["win_pct"],
                "payoff_usdt": result["metrics"]["all"]["payoff_usdt"],
                "funding_usdt": result["audit"]["cash_bridge"]["applied_funding_cashflow_usdt"],
                "parents": result["metrics"]["all"]["trades"],
            }, ensure_ascii=False), flush=True)
            del children, parents, equity, flows, result
            gc.collect()
        arms[arm_name] = arm
    summary = {
        "status": "complete", "config_id": config["id"],
        "single_variable": config["single_variable"],
        "arm_order": ["baseline_2_3_reused", "target_1_3", "target_5_6"],
        "frozen_baseline_2_3": prepared["baseline_reuse"],
        "candidate_arms": arms,
        "signal_source_audit": prepared["signal_source_audit"],
        "capacity_source_audit": {
            key: value for key, value in prepared["capacity_audit"].items()
            if key not in {"eligible_parent_ids", "entry_limits", "original_parent_ids",
                           "stress_funded", "stress_hourly"}
        },
        "entry_limit_matrix_audit": prepared["entry_matrix_audit"],
        "funding_source_coverage": prepared["source_coverage"],
        "funding_window_audit": prepared["coverage_audit"],
        "account_roster_audit": prepared["account_audit"],
        "limitations": [
            "This is an independent Binance historical account transfer; it is not the same trade sample or an unconditional account comparison to OKX.",
            "The 2/3 baseline is reused from the previously committed 10% prior-minute-cap funding account, not replayed.",
            "The 10% prior-minute quote-turnover cap is a volume proxy, not order-book depth or measured market impact.",
            "All three capacity-unknown original parent signals remain skipped in their original slots without replacements.",
            "For 92 parent signals the per-symbol archived raw signals.csv.gz is absent or does not contain the event; all are retained through exact frozen parent/priority/native-path joins, and the unresolved direct feature-source count is explicit.",
            "2026 is partial through the frozen Binance history boundary; the source account ends flat at 2026-09-24 00:00 UTC.",
            "Research only; no production or real-money action is authorized.",
        ],
    }
    _write_json(EXP / "summary.json", summary)
    _write_json(EXP / "signal_source_audit.json", prepared["signal_source_audit"])
    return summary


def _preflight() -> dict:
    config = _json(CONFIG_PATH)
    _validate_config(config)
    prepared = _prepare(config)
    report = {
        "status": "preflight_pass",
        "vectorbt_version": relative.vbt.__version__,
        "original_parent_denominator": len(prepared["panel"]),
        "eligible_positions": len(prepared["reference"]),
        "native_child_legs": len(prepared["selected"]),
        "native_h1_rows": len(prepared["reference_equity"]),
        "funding_tape_rows": len(prepared["tape"]),
        "funding_source_months": len(prepared["months"]),
        "funding_coverage_complete": bool(prepared["source_coverage"].get("complete")),
        "covered_child_legs": prepared["coverage_audit"]["covered_child_legs"],
        "entry_capacity_cells": prepared["entry_matrix_audit"]["entry_capacity_cells"],
        "hourly_trim_fraction": 0.2,
        "signal_source_audit": prepared["signal_source_audit"],
        "reused_baseline_scenarios": sorted(prepared["baseline_reuse"]["scenarios"]),
        "candidate_child_weights": {name: _weights(value).tolist()
                                     for name, value in TARGET_FRACTIONS.items()},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight", action="store_true", help="Join frozen event, capacity and funding sources")
    modes.add_argument("--mini-check", action="store_true", help="Run the VectorBT weight/capacity/funding fixture")
    modes.add_argument("--run", action="store_true", help="Replay the two preregistered target fractions once")
    args = parser.parse_args()
    if args.preflight:
        _preflight()
    elif args.mini_check:
        _validate_config(_json(CONFIG_PATH))
        mini = relative._mini_check()
        preflight = _preflight()
        print(json.dumps({"status": "mini_and_preflight_pass", "weight_fixture": mini,
                          "preflight": {k: preflight[k] for k in (
                              "original_parent_denominator", "eligible_positions", "native_child_legs",
                              "native_h1_rows", "funding_tape_rows", "funding_coverage_complete",
                              "entry_capacity_cells")}}, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    else:
        _run()


if __name__ == "__main__":
    main()
