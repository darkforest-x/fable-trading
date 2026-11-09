"""Add the fixed first-confirmed 12H low to the matched Binance hold_doubling clock."""
from __future__ import annotations

import gc
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_binance_capacity_funding as funding
from yoyo.evaluation import cloud_close_binance_doubled_stop_transfer as transfer
from yoyo.evaluation import cloud_close_binance_matched_target_clock as matched
from yoyo.evaluation import cloud_close_doubled_candle_low as stops

EXP = funding.EXP.parent / "exp-cloud-close-matched-doubling-floor-20261109-v1"
CONFIG_PATH = EXP / "config.json"
WEIGHTS = np.asarray([1 / 3, 1 / 3, 1 / 6, 1 / 6], dtype=np.float64)
BOUNDARIES = ("include_boundary", "exclude_boundary")
SLIPPAGES = (0.0, 0.0005)
BASELINE_SUMMARY = matched.EXP / "summary.json"
ACTIVATION_COLUMNS = [
    "event_id", "mode", "activation_ms", "activation_low", "changed",
    "exit_bar_ms", "exit_phase", "exit_price", "original_exit_ms", "original_exit_price",
]


def _assert_committed() -> None:
    """Require the builder and frozen inputs to be committed before any official run."""
    for path in (Path(__file__).resolve(), CONFIG_PATH.resolve()):
        rel = str(path.relative_to(funding.ROOT))
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel], cwd=funding.ROOT,
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=funding.ROOT,
                          check=False).returncode:
            raise RuntimeError(f"Commit the matched doubling-floor builder/input first: {rel}")


def _read_parent_ledger(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"event_id": str}, float_precision="round_trip")
    if "event_id" not in frame:
        frame = frame.rename(columns={frame.columns[0]: "event_id"})
    if frame.event_id.duplicated().any():
        raise ValueError(f"Parent ledger is not unique by event: {path}")
    return frame.set_index("event_id", drop=True).sort_index()


def _baseline_arm(boundary: str, slip: float) -> tuple[str, dict, pd.DataFrame]:
    if not BASELINE_SUMMARY.is_file():
        raise FileNotFoundError(f"Matched hold_doubling baseline is missing: {BASELINE_SUMMARY}")
    baseline_summary = funding._json(BASELINE_SUMMARY)
    suffix = "_stress" if slip else ""
    key = f"hold_doubling{suffix}_{boundary}"
    arm = baseline_summary.get("arms", {}).get(key)
    if not arm:
        raise ValueError(f"Registered matched-clock baseline has no {key} arm")
    folder = matched.EXP / key
    result_path = folder / "result.json"
    parent_path = folder / "parents.csv.gz"
    if not result_path.is_file() or not parent_path.is_file():
        raise FileNotFoundError(f"Matched-clock arm is missing its native funded evidence: {folder}")
    result = funding._json(result_path)
    parents = _read_parent_ledger(parent_path)
    if len(parents) != 1583 or not np.isclose(
            1000.0 + parents.cash_pnl.sum(), result["metrics"]["all"]["final_usdt"], atol=0.02, rtol=0.0):
        raise ValueError(f"Matched-clock baseline parent cash fails its terminal-NAV bridge: {key}")
    if arm["metrics"]["all"] != result["metrics"]["all"]:
        raise ValueError(f"Registered matched-clock summary differs from its raw arm result: {key}")
    return key, result, parents


def _path_rows(data: dict) -> pd.DataFrame:
    selected = data["selected"].copy()
    selected["child"] = selected.slot.astype(int) % 4
    # The last exiting surviving child bounds the parent's live no-sale path.
    # Earlier 2R fills remain closed and are not reopened by the candle stop.
    indices = selected.groupby("event_id", sort=False).exit_bar_ms.idxmax()
    rows = selected.loc[indices].sort_values("event_id").reset_index(drop=True)
    if rows.event_id.duplicated().any() or len(rows) != 1583:
        raise ValueError("No-sale maximum-life clock is not one row per funded original parent")
    return rows


def _validate_no_sale_route(data: dict) -> dict:
    selected = data["selected"].copy()
    if "child" not in selected:
        raise ValueError("No-sale route did not retain its original source-child identity")
    expected = selected.slot.astype(int).mod(4).map({0: 1, 1: 1, 2: 3, 3: 3})
    if not np.array_equal(selected.child.to_numpy(dtype=int), expected.to_numpy(dtype=int)):
        raise ValueError("The selected children include an absolute 2x sale instead of hold_doubling")
    if len(selected) != 6332 or not selected.event_id.nunique() == 1583:
        raise ValueError("Matched no-sale route changed the original funded four-child roster")
    return {
        "absolute_2x_sale_templates_selected": 0,
        "selected_source_children": {str(k): int(v) for k, v in selected.child.value_counts().sort_index().items()},
        "funded_parents": int(selected.event_id.nunique()),
        "funded_child_legs": int(len(selected)),
    }


def _attach_funding_inputs(data: dict, original: dict, prepared: dict) -> dict:
    no_settlement, coverage = funding.relative._check_covered_windows(
        data["selected"], prepared["tape"], prepared["months"],
    )
    for field in ("expected_positions", "expected_child_legs", "exposure_map"):
        data[field] = original[field]
    data["funding_no_settlement_windows"] = no_settlement
    data["funding_source_audit"] = coverage
    if coverage["covered_child_legs"] != 6332 or coverage["uncovered_symbol_months"]:
        raise ValueError("Changed held intervals lack verified historical Binance funding coverage")
    return no_settlement


def _compare_parents(candidate: pd.DataFrame, baseline: pd.DataFrame) -> dict:
    if not candidate.index.equals(baseline.index):
        raise ValueError("Candidate and matched baseline parent rosters differ")
    if not np.array_equal(candidate.entry_ms.to_numpy(), baseline.entry_ms.to_numpy()):
        raise ValueError("Candidate changed frozen native entry times")
    cash_delta = candidate.cash_pnl.to_numpy(dtype=float) - baseline.cash_pnl.to_numpy(dtype=float)
    return {
        "parent_ids_equal": True,
        "entry_times_equal": True,
        "parents_with_nonzero_cash_change": int(np.count_nonzero(np.abs(cash_delta) > 1e-8)),
        "sum_parent_cash_delta_usdt": float(cash_delta.sum()),
        "median_parent_cash_delta_usdt": float(np.median(cash_delta)),
        "p10_parent_cash_delta_usdt": float(np.quantile(cash_delta, 0.10)),
        "p90_parent_cash_delta_usdt": float(np.quantile(cash_delta, 0.90)),
        "max_abs_parent_cash_delta_usdt": float(np.abs(cash_delta).max(initial=0.0)),
    }


def _run() -> None:
    _assert_committed()
    cfg = funding._json(CONFIG_PATH)
    if cfg.get("id") != EXP.name or cfg.get("venue") != "Binance USD-M":
        raise ValueError("Matched doubling-floor config identity or venue changed")
    funding_cfg = funding._json(funding.CONFIG_PATH)
    funding._validate_config(funding_cfg)
    if vbt.__version__ != "1.2.0":
        raise RuntimeError(f"Expected local VectorBT 1.2.0, found {vbt.__version__}")
    if (EXP / "summary.json").exists():
        raise FileExistsError("Completed matched Binance doubling-floor account")
    EXP.mkdir(parents=True, exist_ok=True)

    prepared = funding._prepare_real_account(funding_cfg)
    original = prepared["data"]
    base, expiry_audit = matched.twelve_hour_expiry(original, prepared["quotes"])
    no_sale, changed_double_parents, changed_double_children = stops.doubling.route(
        base, prepared["quotes"], [1, 1, 3, 3],
    )
    route_audit = _validate_no_sale_route(no_sale)
    path_rows = _path_rows(no_sale)
    findings = []
    for row in path_rows.itertuples(index=False):
        if row.side == 1:
            findings.extend(stops.stop_path(row, *prepared["quotes"](row.symbol)))
    events = pd.DataFrame(findings, columns=ACTIVATION_COLUMNS)
    if not events.empty and events.duplicated(["event_id", "mode"]).any():
        raise ValueError("A parent has more than one fixed first-confirmed-candle stop")
    fixed = events.loc[events["mode"].eq("fixed")].copy() if not events.empty else events.copy()
    fixed.to_csv(EXP / "fixed_stop_activations.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    changes = fixed.loc[fixed.changed].set_index("event_id", verify_integrity=True)
    if not changes.empty and (changes.exit_bar_ms < changes.activation_ms).any():
        raise ValueError("The fixed low stop filled before the completed double-confirmation candle")
    fixed_data, changed_children = stops.apply_stops(no_sale, prepared["quotes"], changes)
    no_settlement = _attach_funding_inputs(fixed_data, original, prepared)
    for field in ("expected_positions", "expected_child_legs", "exposure_map"):
        no_sale[field] = original[field]
    _attach_funding_inputs(no_sale, original, prepared)

    identity_fields = ["event_id", "slot", "entry_ms", "entry_price", "side", "stop_price", "tp_r"]
    before = no_sale["selected"].sort_values(["event_id", "slot"])[identity_fields].reset_index(drop=True)
    after = fixed_data["selected"].sort_values(["event_id", "slot"])[identity_fields].reset_index(drop=True)
    if not before.equals(after):
        raise ValueError("The candidate changed entry, slot, child target, direction, or original stop geometry")
    if fixed_data["gap_marks"] != 0:
        raise ValueError("Fixed-stop candidate has an unobserved held Binance H1 hour")

    summary_arms = {}
    comparisons = {}
    baseline_provenance = {}
    for slip in SLIPPAGES:
        suffix = "_stress" if slip else ""
        for boundary in BOUNDARIES:
            baseline_key, baseline_result, baseline_parents = _baseline_arm(boundary, slip)
            candidate_key = f"fixed_low{suffix}_{boundary}"
            candidate_result = transfer.scenario(
                prepared, fixed_data, no_settlement, candidate_key, boundary, slip,
                directory=EXP, child_weights=WEIGHTS,
            )
            candidate_parents = _read_parent_ledger(EXP / candidate_key / "parents.csv.gz")
            parent_delta = _compare_parents(candidate_parents, baseline_parents)
            terminal_delta = (
                candidate_result["metrics"]["all"]["final_usdt"]
                - baseline_result["metrics"]["all"]["final_usdt"]
            )
            if not np.isclose(parent_delta["sum_parent_cash_delta_usdt"], terminal_delta,
                              rtol=1e-9, atol=0.02):
                raise ValueError(f"Paired parent cash difference fails NAV bridge for {candidate_key}")
            if candidate_result["audit"]["positions"] != 1583 or not candidate_result["audit"]["all_positions_closed"]:
                raise ValueError(f"Candidate account did not fund and close the full matched roster: {candidate_key}")
            summary_arms[baseline_key] = baseline_result
            summary_arms[candidate_key] = candidate_result
            comparisons[candidate_key] = {
                "baseline_arm": baseline_key,
                "baseline_source": str((matched.EXP / baseline_key / "result.json").relative_to(funding.ROOT)),
                "candidate_arm": candidate_key,
                "terminal_nav_delta_usdt": float(terminal_delta),
                "multiple_delta": float(candidate_result["metrics"]["all"]["multiple"]
                                         - baseline_result["metrics"]["all"]["multiple"]),
                "drawdown_mark_delta_pp": float(candidate_result["metrics"]["all"]["dd_mark_pct"]
                                                 - baseline_result["metrics"]["all"]["dd_mark_pct"]),
                "drawdown_bound_delta_pp": float(candidate_result["metrics"]["all"]["dd_bound_pct"]
                                                  - baseline_result["metrics"]["all"]["dd_bound_pct"]),
                "parent_cash_delta": parent_delta,
            }
            baseline_provenance[baseline_key] = {
                "source_summary": str(BASELINE_SUMMARY.relative_to(funding.ROOT)),
                "source_arm_result": str((matched.EXP / baseline_key / "result.json").relative_to(funding.ROOT)),
                "funded_parent_rows": int(len(baseline_parents)),
                "terminal_cash_bridge_usdt": float(1000.0 + baseline_parents.cash_pnl.sum()),
            }
            gc.collect()
    open_gap_count = int((changes.exit_phase.eq("open") &
                          changes.exit_price.lt(changes.activation_low - 1e-12)).sum()) if not changes.empty else 0
    summary = {
        "experiment_id": EXP.name,
        "status": "completed_historical_matched_account",
        "vectorbt_version": vbt.__version__,
        "framework_command": cfg["execution"]["reproduction_command"],
        "baseline_experiment": matched.EXP.name,
        "longer_clock_stop_transfer_reference": transfer.EXP.name,
        "expiry": {**expiry_audit, "hours": 12,
                   "unfilled_original_2r_expires_after_12h": True,
                   "original_2r_fills_before_expiry_preserved": True},
        "no_sale_route": {**route_audit,
                          "changed_doubling_template_parents": len(changed_double_parents),
                          "changed_doubling_child_templates": int(changed_double_children)},
        "fixed_low_stop": {
            "confirmed_double_parents": int(fixed.event_id.nunique()) if not fixed.empty else 0,
            "confirmed_double_parents_with_stop_fill": int(changes.index.nunique()),
            "stop_fill_child_legs": int(changed_children),
            "gap_open_fills_below_stop": open_gap_count,
            "activation_rule": "First completed UTC 12H candle close >= 2x entry; stop begins at the next native H1 open.",
            "formation_bar_cannot_trigger": True,
            "gap_open_fill_rule": "Long stop fills at the actual open if open is below the fixed low; otherwise at the stop level.",
            "fixed_low_is_not_ratchet": True,
        },
        "account_invariants": {
            **route_audit,
            "entry_ids_match_registered_funded_parent_roster": True,
            "entry_times_unchanged": True,
            "slots_and_child_weights_unchanged": True,
            "original_stop_geometry_unchanged": True,
            "no_unquoted_holding_hours": int(fixed_data["gap_marks"]),
            "funding_source_coverage": fixed_data["funding_source_audit"],
            "both_historical_funding_boundary_conventions": list(BOUNDARIES),
            "market_fill_slippage_scenarios": list(SLIPPAGES),
        },
        "baseline_provenance": baseline_provenance,
        "arms": summary_arms,
        "paired_comparisons": comparisons,
    }
    funding._write_json(EXP / "summary.json", summary)


def main() -> None:
    _run()


if __name__ == "__main__":
    main()
