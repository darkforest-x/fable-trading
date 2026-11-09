"""Re-evaluate only membership changes from the repaired Spot H1 panel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_spot_lead as lead
from yoyo.evaluation import cloud_close_spot_reclaim as wallet


ROOT = wallet.admin.archive.native.wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-spot-repaired-wallet-20261109-v1"
CONFIG_PATH = EXP / "config.json"
ARM_NAMES = ("covered", "confirmed", "placebo")
PERIODS = ("all", "dev", "2025", "2026")


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _read_panel(path: Path, *, lead_decisions: bool = False) -> pd.DataFrame:
    if lead_decisions:
        panel = pd.read_csv(path, usecols=["event_id", "covered", "spot_leads", "signed_log_difference"],
                            dtype={"covered": "boolean", "spot_leads": "boolean"})
        panel = panel.set_index("event_id", verify_integrity=True)
        return panel
    panel = pd.read_csv(path, usecols=["event_id", "covered", "spot_reclaim"],
                        dtype={"covered": "boolean", "spot_reclaim": "boolean"})
    return panel.set_index("event_id", verify_integrity=True)


def _same_signal(left: pd.Series, right: pd.Series) -> bool:
    if not left.index.equals(right.index):
        right = right.reindex(left.index)
    if left.isna().any() or right.isna().any():
        return False
    return left.astype(bool).equals(right.astype(bool))


def _load_candidate_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["event_id", "entry_ms", "score"])
    frame = frame.set_index("event_id", verify_integrity=True)
    frame["side"] = frame.index.to_series().str.rsplit("|", n=1).str[-1].astype(int)
    frame["has_score"] = frame.score.notna()
    return frame[["entry_ms", "side", "has_score"]]


def _group_members(panel: pd.DataFrame, signal_column: str,
                   candidates: pd.DataFrame) -> tuple[dict[str, set[str]], int, pd.DataFrame]:
    observed = candidates.join(panel[["covered", signal_column]], how="left", validate="one_to_one")
    if observed["covered"].isna().any():
        raise ValueError("Filter panel does not provide covered/unknown state for every frozen event")
    observed = observed.loc[observed.covered.fillna(False)].copy()
    placebo, movable = wallet.sample_placebo(observed, signal_column)
    observed["placebo"] = placebo
    if int(observed.placebo.sum()) != int(observed[signal_column].sum()):
        raise ValueError("Seeded placebo did not preserve the signal cohort count")
    confirmed = set(observed.index[observed[signal_column].fillna(False)])
    members = {
        "covered": set(observed.index),
        "confirmed": confirmed,
        "placebo": set(observed.index[observed.placebo]),
    }
    return members, movable, observed


def _read_old_observed(directory: Path) -> pd.DataFrame:
    path = directory / "observed_candidates.csv.gz"
    frame = pd.read_csv(path, index_col=0)
    frame.index = frame.index.astype(str)
    frame.index.name = "event_id"
    if not frame.index.is_unique:
        raise ValueError(f"Duplicate old observed candidate IDs: {path}")
    return frame


def _membership_plan(directory: Path, panel: pd.DataFrame, signal_column: str,
                     candidates: pd.DataFrame) -> tuple[dict, dict[str, set[str]]]:
    old_observed = _read_old_observed(directory)
    new_members, movable, _ = _group_members(panel, signal_column, candidates)
    old_members = {
        "covered": set(old_observed.index),
        "confirmed": set(old_observed.index[old_observed[signal_column].fillna(False)]),
        "placebo": set(old_observed.index[old_observed.placebo.fillna(False)]),
    }
    detail = {}
    for name in ARM_NAMES:
        old, new = old_members[name], new_members[name]
        detail[name] = {
            "old_candidates": len(old), "repaired_candidates": len(new),
            "added_event_ids": sorted(new - old), "removed_event_ids": sorted(old - new),
            "added": len(new - old), "removed": len(old - new),
            "membership_changed": old != new,
        }
    return {"arms": detail, "placebo_movable_strata": movable}, old_members


def _validate_settlement_sources(config: dict) -> list[Path]:
    result = []
    for relative in config["design"]["settlement_sources"]:
        directory = ROOT / relative
        config_path = directory / "config.json"
        minute_path = directory / "settlement_index_1m.csv"
        if not config_path.is_file() or not minute_path.is_file():
            raise FileNotFoundError(f"Missing reusable native delivery input: {directory}")
        source_config = _read_json(config_path)
        if "additional_settlements" not in source_config:
            raise ValueError(f"Settlement source lacks additional_settlements: {config_path}")
        bars = pd.read_csv(minute_path)
        required = {"symbol", "low", "close", "high"}
        if not required.issubset(bars.columns):
            raise ValueError(f"Unexpected native settlement schema: {minute_path}")
        if not bars.empty and not np.isfinite(bars[["low", "close", "high"]].to_numpy(dtype=float)).all():
            raise ValueError(f"Non-finite settlement mark in {minute_path}")
        result.append(directory)
    if len(result) != len(set(result)):
        raise ValueError("Duplicate settlement source directory")
    return result


def _read_old_arm(directory: Path, name: str) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary = _read_json(directory / "summary.json")["results"][name]
    parents = pd.read_csv(directory / f"funded_{name}.csv.gz")
    if "event_id" not in parents:
        raise ValueError(f"Old funded file has no event_id: {directory}/{name}")
    parents = parents.set_index("event_id", verify_integrity=True)
    equity = pd.read_csv(directory / f"hourly_{name}.csv.gz", index_col=0, parse_dates=True)
    trims = pd.DataFrame({"event_id": [f"reused-{i}" for i in range(summary["trimmed_positions"])]})
    return summary["metrics"], parents, equity, trims


def _run_with_fee_capture(trim_account, args: tuple, kwargs: dict, arm_name: str) -> tuple:
    """Use the existing VectorBT callback once and retain its exact order fees."""
    portfolio_cls = wallet.hourly.tested.w.vbt.Portfolio
    descriptor = portfolio_cls.__dict__["from_order_func"]
    native_constructor = portfolio_cls.from_order_func
    captured: list[dict] = []

    def capture_constructor(cls, *constructor_args, **constructor_kwargs):
        portfolio = native_constructor(*constructor_args, **constructor_kwargs)
        records = portfolio.orders.records
        if "fees" not in records.columns:
            raise ValueError("VectorBT order records do not expose the executed fees field")
        fees = records["fees"].to_numpy(dtype=float)
        if not np.isfinite(fees).all() or (fees < 0).any():
            raise ValueError("Invalid executed order fee in VectorBT records")
        captured.append({"fees_usdt": float(fees.sum()), "order_count": int(len(records))})
        return portfolio

    setattr(portfolio_cls, "from_order_func", classmethod(capture_constructor))
    try:
        output = trim_account(*args, **kwargs)
    finally:
        setattr(portfolio_cls, "from_order_func", descriptor)
    if len(captured) != 1:
        raise ValueError(f"Expected one VectorBT portfolio for {arm_name}, got {len(captured)}")
    return output, captured[0]


def _selective_filter(panel: pd.DataFrame, signal_column: str, new_directory: Path,
                      old_directory: Path, settlement_sources: list[Path],
                      member_delta: dict) -> tuple[dict, dict]:
    expected_sets = {
        name: (set(member_delta["old_members"][name])
               | set(member_delta["arms"][name]["added_event_ids"]))
        - set(member_delta["arms"][name]["removed_event_ids"])
        for name in ARM_NAMES
    }
    membership_changed = {name: member_delta["arms"][name]["membership_changed"] for name in ARM_NAMES}
    old_arms = {name: _read_old_arm(old_directory, name) for name in ARM_NAMES}
    arm_index = 0
    fee_audit: dict[str, dict] = {}
    original_trim = wallet.hourly.tested.trim_account

    def trim_only_changed(data, quote_loader, exposure, limit, *args, **kwargs):
        nonlocal arm_index
        if arm_index >= len(ARM_NAMES):
            raise ValueError("wallet.compare_filter requested more than its three fixed account arms")
        name = ARM_NAMES[arm_index]
        arm_index += 1
        selected = data["selected"]
        selected_ids = set(selected.event_id.astype(str))
        if not membership_changed[name]:
            metrics, parents, equity, trims = old_arms[name]
            if selected_ids != set(parents.index.astype(str)):
                raise ValueError(f"Unchanged {name} candidates no longer produce the frozen funded roster")
            fee_audit[name] = {
                "status": "reused_unchanged_membership",
                "executed_fee_total_usdt": None,
                "baseline_fee_ledger_available": False,
                "note": "The frozen v1 parent ledger stores net cash PnL but not per-order fee totals.",
                "orders": None,
            }
            return metrics, parents, equity, trims
        (metrics, parents, equity, trims), captured = _run_with_fee_capture(
            original_trim, (data, quote_loader, exposure, limit, *args), kwargs, name)
        fee_audit[name] = {
            "status": "replayed_changed_membership",
            "executed_fee_total_usdt": captured["fees_usdt"],
            "baseline_fee_ledger_available": False,
            "baseline_fee_note": "The frozen v1 parent ledger stores net cash PnL but not per-order fee totals.",
            "orders": captured["order_count"],
            "fee_source": "VectorBT Portfolio.orders.records.fees",
        }
        return metrics, parents, equity, trims

    wallet.hourly.tested.trim_account = trim_only_changed
    try:
        wallet.compare_filter(panel, signal_column, new_directory, tuple(settlement_sources))
    finally:
        wallet.hourly.tested.trim_account = original_trim
    if arm_index != len(ARM_NAMES):
        raise ValueError(f"Expected wallet.compare_filter to process three arms, got {arm_index}")

    new_summary = _read_json(new_directory / "summary.json")
    new_observed = pd.read_csv(new_directory / "observed_candidates.csv.gz", index_col=0)
    new_observed.index = new_observed.index.astype(str)
    new_members = {
        "covered": set(new_observed.index),
        "confirmed": set(new_observed.index[new_observed[signal_column].fillna(False)]),
        "placebo": set(new_observed.index[new_observed.placebo.fillna(False)]),
    }
    if any(new_members[name] != expected_sets[name] for name in ARM_NAMES):
        raise ValueError("wallet.compare_filter changed the preregistered observed/seeded membership")

    comparison = {}
    for name in ARM_NAMES:
        previous_metrics, previous_parents, _, _ = old_arms[name]
        current = new_summary["results"][name]
        new_parents = pd.read_csv(new_directory / f"funded_{name}.csv.gz")
        new_parent_ids = set(new_parents.event_id.astype(str))
        old_parent_ids = set(previous_parents.index.astype(str))
        period_deltas = {}
        for period in PERIODS:
            old_metric = previous_metrics[period]
            new_metric = current["metrics"][period]
            period_deltas[period] = {
                "old_final_usdt": old_metric["final_usdt"],
                "repaired_final_usdt": new_metric["final_usdt"],
                "delta_final_usdt": new_metric["final_usdt"] - old_metric["final_usdt"],
                "old_trades": old_metric["trades"], "repaired_trades": new_metric["trades"],
                "old_win_pct": old_metric["win_pct"], "repaired_win_pct": new_metric["win_pct"],
                "old_dd_bound_pct": old_metric["dd_bound_pct"],
                "repaired_dd_bound_pct": new_metric["dd_bound_pct"],
            }
        comparison[name] = {
            "candidate_membership_changed": membership_changed[name],
            "account_replayed": fee_audit[name]["status"] == "replayed_changed_membership",
            "old_funded_events": len(old_parent_ids), "repaired_funded_events": len(new_parent_ids),
            "funded_added": len(new_parent_ids - old_parent_ids),
            "funded_removed": len(old_parent_ids - new_parent_ids),
            "funded_added_event_ids": sorted(new_parent_ids - old_parent_ids),
            "funded_removed_event_ids": sorted(old_parent_ids - new_parent_ids),
            "periods": period_deltas,
            "fees": fee_audit[name],
        }
    return {"account_comparisons": comparison, "new_metrics": new_summary["results"]}, new_members


def _build_filter_plan(panel: pd.DataFrame, signal_column: str, candidates: pd.DataFrame,
                       old_directory: Path) -> tuple[dict, pd.DataFrame]:
    delta, old_members = _membership_plan(old_directory, panel, signal_column, candidates)
    members, movable, observed = _group_members(panel, signal_column, candidates)
    for name in ARM_NAMES:
        reconstructed = ((old_members[name] | set(delta["arms"][name]["added_event_ids"]))
                         - set(delta["arms"][name]["removed_event_ids"]))
        if members[name] != reconstructed:
            raise ValueError("Membership delta did not reconstruct the current filter cohort")
    delta["placebo_movable_strata"] = movable
    delta["old_members"] = {name: sorted(ids) for name, ids in old_members.items()}
    return delta, observed


def run() -> dict:
    config = _read_json(CONFIG_PATH)
    if int(config["design"]["placebo_seed"]) != 1029:
        raise ValueError("Placebo seed must remain the original frozen 1029")
    repaired_path = ROOT / config["inputs"]["repaired_panel"]
    old_panel_path = ROOT / config["inputs"]["original_panel"]
    if not repaired_path.is_file() or not old_panel_path.is_file():
        raise FileNotFoundError("Original and repaired Spot event panels are required")
    output_paths = [ROOT / config["outputs"][name]
                    for name in ("membership_deltas", "summary", "source_receipt")]
    output_paths.extend([
        ROOT / config["outputs"]["reclaim_dir"] / name
        for name in ("membership_preview.csv.gz", "filter_decisions.csv.gz",
                     "observed_candidates.csv.gz", "selected_deliveries.csv", "summary.json",
                     *(f"funded_{arm}.csv.gz" for arm in ARM_NAMES),
                     *(f"hourly_{arm}.csv.gz" for arm in ARM_NAMES))
    ])
    output_paths.extend([
        ROOT / config["outputs"]["lead_dir"] / name
        for name in ("membership_preview.csv.gz", "decisions.csv.gz",
                     "observed_candidates.csv.gz", "selected_deliveries.csv", "summary.json",
                     *(f"funded_{arm}.csv.gz" for arm in ARM_NAMES),
                     *(f"hourly_{arm}.csv.gz" for arm in ARM_NAMES))
    ])
    existing = [str(path) for path in output_paths if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite repaired wallet outputs: {existing}")
    for arm in ("reclaim", "lead"):
        directory = ROOT / config["outputs"][f"{arm}_dir"]
        if (directory / "summary.json").exists():
            raise FileExistsError(f"Completed {arm} wallet comparison exists: {directory}")

    repaired_full = pd.read_csv(repaired_path).set_index("event_id", verify_integrity=True)
    old_spot = _read_panel(old_panel_path)
    new_spot = _read_panel(repaired_path)
    frozen = pd.read_csv(ROOT / config["inputs"]["frozen_candidate_file"],
                         usecols=["event_id", "entry_ms", "score"]).set_index("event_id", verify_integrity=True)
    if not (set(old_spot.index) == set(new_spot.index) == set(frozen.index)):
        raise ValueError("Old/repaired panels do not preserve the exact frozen event roster and ordering")
    if not old_spot.covered.equals(old_spot.spot_reclaim.notna().astype("boolean")):
        raise ValueError("Original reclaim panel has inconsistent covered and nullable signal states")
    if not new_spot.covered.equals(new_spot.spot_reclaim.notna().astype("boolean")):
        raise ValueError("Repaired reclaim panel has inconsistent covered and nullable signal states")
    old_known = old_spot.covered.fillna(False)
    old_known_ids = old_spot.index[old_known]
    if not new_spot.covered.reindex(old_known_ids).fillna(False).all():
        raise ValueError("A previously observed Spot event became unknown")
    if not _same_signal(old_spot.loc[old_known, "spot_reclaim"],
                        new_spot.reindex(old_known_ids)["spot_reclaim"]):
        raise ValueError("A previously observed reclaim signal changed")
    old_spot_known = int(old_known.sum())
    newly_observed_spot = int((~old_known & new_spot.covered.reindex(old_spot.index).fillna(False)).sum())

    # Use the existing lead observation function against the repaired Spot panel.
    repaired_full.index = repaired_full.index.astype(str)
    spread = lead.observations(repaired_full)
    lead_covered = spread.notna().astype("boolean")
    lead_signal = spread.gt(0).astype("boolean").where(lead_covered)
    new_lead = pd.DataFrame({"covered": lead_covered, "spot_leads": lead_signal,
                             "signed_log_difference": spread}, index=spread.index)
    old_lead = _read_panel(lead.EXP / "decisions.csv.gz", lead_decisions=True)
    if set(old_lead.index) != set(new_lead.index):
        raise ValueError("Old and repaired lead panels do not share the frozen event roster")
    old_lead = old_lead.reindex(new_lead.index)
    old_lead_known = old_lead.covered.fillna(False)
    old_lead_ids = old_lead.index[old_lead_known]
    if not new_lead.covered.reindex(old_lead_ids).fillna(False).all():
        raise ValueError("A previously measured lead observation became unknown")
    common_lead = old_lead.index[old_lead_known]
    old_spread = old_lead.loc[common_lead, "signed_log_difference"].to_numpy(dtype=float)
    new_spread = new_lead.loc[common_lead, "signed_log_difference"].to_numpy(dtype=float)
    if not np.allclose(old_spread, new_spread, rtol=1e-12, atol=1e-12):
        raise ValueError("A previously observed signed lead value changed")
    if not _same_signal(old_lead.loc[old_lead_known, "spot_leads"],
                        new_lead.loc[old_lead_known, "spot_leads"]):
        raise ValueError("A previously observed lead signal changed")

    candidates = _load_candidate_frame(ROOT / config["inputs"]["frozen_candidate_file"])
    reclaim_plan, reclaim_observed = _build_filter_plan(
        new_spot, "spot_reclaim", candidates, wallet.EXP)
    lead_plan, lead_observed = _build_filter_plan(
        new_lead[["covered", "spot_leads"]], "spot_leads", candidates, lead.EXP)
    refs = _validate_settlement_sources(config)

    reclaim_dir = ROOT / config["outputs"]["reclaim_dir"]
    lead_dir = ROOT / config["outputs"]["lead_dir"]
    reclaim_dir.mkdir(parents=True, exist_ok=True)
    lead_dir.mkdir(parents=True, exist_ok=True)
    reclaim_observed.to_csv(reclaim_dir / "membership_preview.csv.gz")
    lead_observed.to_csv(lead_dir / "membership_preview.csv.gz")
    pd.DataFrame({"covered": new_spot.covered, "spot_reclaim": new_spot.spot_reclaim}).to_csv(
        reclaim_dir / "filter_decisions.csv.gz")
    new_lead.to_csv(lead_dir / "decisions.csv.gz")

    # compare_filter keeps the exact existing admission, pricing and VectorBT path.
    reclaim_result, _ = _selective_filter(
        pd.DataFrame({"covered": new_spot.covered, "spot_reclaim": new_spot.spot_reclaim}),
        "spot_reclaim", reclaim_dir, wallet.EXP, refs,
        {**reclaim_plan, "old_members": reclaim_plan["old_members"]})
    lead_result, _ = _selective_filter(
        new_lead[["covered", "spot_leads"]], "spot_leads", lead_dir, lead.EXP, refs,
        {**lead_plan, "old_members": lead_plan["old_members"]})

    def public_delta(plan: dict) -> dict:
        return {"arms": plan["arms"], "placebo_movable_strata": plan["placebo_movable_strata"]}

    membership = {
        "seed": config["design"]["placebo_seed"],
        "reclaim": public_delta(reclaim_plan),
        "lead": public_delta(lead_plan),
        "old_known_reclaim_events": old_spot_known,
        "newly_observed_spot_events": newly_observed_spot,
        "old_known_lead_events": int(old_lead_known.sum()),
        "newly_observed_lead_events": int((~old_lead_known & new_lead.covered.fillna(False)).sum()),
        "old_known_reclaim_flips": 0,
        "old_known_lead_value_or_signal_flips": 0,
    }
    _atomic_json(ROOT / config["outputs"]["membership_deltas"], membership)
    summary = {
        "schema": 1,
        "experiment_id": config["id"],
        "source_panel": config["inputs"]["repaired_panel"],
        "baseline_panel": config["inputs"]["original_panel"],
        "source_candidate_count": len(candidates),
        "placebo_seed": config["design"]["placebo_seed"],
        "signal_stability": {
            "old_known_reclaim_checked": old_spot_known,
            "old_known_reclaim_flips": 0,
            "old_known_lead_checked": int(old_lead_known.sum()),
            "old_known_lead_value_or_signal_flips": 0,
        },
        "reclaim": reclaim_result,
        "lead": lead_result,
        "settlement_sources": [str(path.relative_to(ROOT)) for path in refs],
        "fee_scope": "Executed fees for changed arms come from native VectorBT order records; original v1 outputs do not retain an order-fee ledger.",
    }
    _atomic_json(ROOT / config["outputs"]["summary"], summary)
    receipt = {
        "schema": 1,
        "experiment_id": config["id"],
        "candidate_count": len(candidates),
        "repaired_panel_rows": len(repaired_full),
        "baseline_panel_rows": len(old_spot),
        "settlement_sources": [str(path.relative_to(ROOT)) for path in refs],
        "replays_only_when_filter_membership_changed": True,
        "old_signal_flip_count": 0,
        "account_replay_count": sum(
            comparison["account_replayed"]
            for result in (reclaim_result, lead_result)
            for comparison in result["account_comparisons"].values()
        ),
        "outputs": config["outputs"],
    }
    _atomic_json(ROOT / config["outputs"]["source_receipt"], receipt)
    print(json.dumps({"membership": membership, "replay_count": receipt["account_replay_count"]},
                     ensure_ascii=False, sort_keys=True), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True,
                        help="Run only changed filter memberships and write the new comparison")
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
