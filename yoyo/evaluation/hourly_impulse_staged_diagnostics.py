"""All-six-arm V2 diagnosis and identical-control replay, development only.

This is a reporting completion of the registered six-arm experiment. It does
not create candidates, rank policies, change selection, or open transport or
repository holdout prices. Saved ledgers define the 111 fixed entry contracts.
The only OHLCV access is the existing ``Study(base, 'development')`` reader;
the registered matching routine then recomputes outcomes under each arm.
Control event IDs, decision times, direction, ATR and fixed stops must agree
across all arms before their matched results are reported together.

Outcome flags and takeover/partial-fill cohorts are descriptive future-path
labels, not prospective filters. Returns and realised amounts are fractions
of original event notional. Their sums may overlap and are not portfolio
returns. Funding and the existing 20bp cost convention are unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any, Dict, Mapping, Tuple

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_diagnostics import classify_trades
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, clean, digest, metrics, write_csv, write_json
from yoyo.evaluation.hourly_impulse_staged_research import EXPERIMENT, matched


EXPECTED_EVENT_COUNT = 111
EXPECTED_ARM_COUNT = 6
REFERENCE = "colour15"
DEFAULT_OUTPUT = ROOT / "analysis/output/btcusdtp_1h_staged_realisation_20260906_v2"
CONTRACT_COLUMNS = (
    "decision_time", "entry_time", "direction", "entry_price",
    "initial_stop", "signal_atr", "fold",
)
CONTROL_CONTRACT_COLUMNS = (
    "parent_event_id", "decision_time", "entry_time", "direction",
    "initial_stop", "signal_atr", "entry_price", "fold",
)


def _numbers(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce") if column in frame else pd.Series(default, index=frame.index, dtype=float)
    return values.where(np.isfinite(values))


def _booleans(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin(["true", "1", "1.0"])


def assert_common_contract(reference: pd.DataFrame, candidate: pd.DataFrame, *, controls: bool = False) -> None:
    """Fail on changed event sets, decision clocks, prices or risk contracts."""
    columns = CONTROL_CONTRACT_COLUMNS if controls else CONTRACT_COLUMNS
    for frame in (reference, candidate):
        if "event_id" not in frame or frame["event_id"].duplicated().any() or frame["event_id"].isna().any():
            raise ValueError("All arms require unique finite event IDs")
        missing = set(columns) - set(frame)
        if missing:
            raise ValueError("Missing entry contract columns: {}".format(sorted(missing)))
    left, right = reference.set_index("event_id").sort_index(), candidate.set_index("event_id").sort_index()
    if not left.index.equals(right.index):
        raise ValueError("Registered arms have different event IDs")
    numeric = {"direction", "entry_price", "initial_stop", "signal_atr"}
    temporal = {"decision_time", "entry_time"}
    for key in columns:
        if key in numeric:
            a, b = pd.to_numeric(left[key], errors="coerce"), pd.to_numeric(right[key], errors="coerce")
            valid = np.isfinite(a) & np.isfinite(b)
            equal = valid & np.isclose(a, b, rtol=1e-12, atol=1e-12)
        elif key in temporal:
            a, b = pd.to_datetime(left[key], utc=True, errors="coerce"), pd.to_datetime(right[key], utc=True, errors="coerce")
            equal = a.notna() & b.notna() & a.eq(b)
        else:
            equal = left[key].notna() & right[key].notna() & left[key].eq(right[key])
        if not bool(equal.all()):
            raise ValueError("Registered entry contract differs: {}".format(key))


def staged_path_summary(trades: pd.DataFrame, cost_fraction: float) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Summarise observed partial realisation, takeover and losing paths."""
    classified = classify_trades(trades, cost_fraction)
    closed = classified.loc[classified["diagnostic_eligible"]].copy()
    partial = _numbers(closed, "partial_fraction", 0.0).fillna(0.0)
    fills = _numbers(closed, "partial_fill_count", 0.0).fillna(0.0)
    realised_gross = _numbers(closed, "realised_partial_gross_return", 0.0).fillna(0.0)
    realised_net = _numbers(closed, "realised_partial_net_return")
    realised_net = realised_net.fillna(realised_gross - cost_fraction * partial)
    active = _booleans(closed, "takeover_active")
    triggered = pd.to_datetime(closed["takeover_trigger_time"], utc=True, errors="coerce").notna() if "takeover_trigger_time" in closed else pd.Series(False, index=closed.index)
    losers = closed.loc[closed["net_loser"]]
    net = closed["net_return"]
    summary = {
        "closed_events": len(closed), "net_losers": len(losers),
        "losers_mfe_ge1r": int(losers["giveback"].sum()),
        "losers_never_positive": int(losers["never_positive"].sum()),
        "losers_fee_flips": int(losers["fees_flip"].sum()),
        "losers_hard_stop": int(losers["hard_stop"].sum()),
        "losers_colour_exit": int(losers["colour_exit"].sum()),
        "losers_with_partial_realisation": int((closed["net_loser"] & partial.gt(0)).sum()),
        "partial_realised_events": int(partial.gt(0).sum()),
        "partial_fill_count": int(fills.sum()),
        "partial_original_fraction_mean": float(partial.mean()),
        "realised_partial_gross_sum_event_bp": float(realised_gross.sum() * 10000),
        "realised_partial_net_sum_event_bp": float(realised_net.sum() * 10000),
        "takeover_triggered_events": int(triggered.sum()),
        "takeover_active_events": int(active.sum()),
        "takeover_active_mean_net_bp": float(net.loc[active].mean() * 10000),
        "takeover_active_net_losers": int((active & net.lt(0)).sum()),
        "positive_return_sum_event_bp": float(net.clip(lower=0).sum() * 10000),
        "negative_return_sum_event_bp": float(net.clip(upper=0).sum() * 10000),
        "loss_taxonomy": losers["primary_loss_reason"].value_counts().sort_index().to_dict(),
        "cohort_warning": "Partial fills and takeover are post-entry outcomes, not causal entry filters; event sums are not portfolio returns.",
    }
    return classified, summary


def paired_against_colour15(reference: pd.DataFrame, candidate: pd.DataFrame, arm_id: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Compare identical entries closed in both arms, delta = candidate - base."""
    assert_common_contract(reference, candidate)
    a, b = reference.set_index("event_id"), candidate.set_index("event_id")
    eligible_a = _booleans(a, "closed") & _numbers(a, "net_return").notna()
    eligible_b = _booleans(b, "closed") & _numbers(b, "net_return").notna()
    ids = sorted(set(a.index[eligible_a]) & set(b.index[eligible_b]))
    old, new = a.loc[ids], b.loc[ids]
    old_net, new_net = _numbers(old, "net_return"), _numbers(new, "net_return")
    delta = new_net - old_net
    table = pd.DataFrame({
        "event_id": ids, "reference": REFERENCE, "candidate": arm_id,
        "entry_time": old["entry_time"].to_numpy(), "direction": old["direction"].to_numpy(), "fold": old["fold"].to_numpy(),
        "reference_net_return": old_net.to_numpy(), "candidate_net_return": new_net.to_numpy(),
        "delta_net_bp": delta.to_numpy() * 10000,
        "reference_outcome": old["outcome"].to_numpy(), "candidate_outcome": new["outcome"].to_numpy(),
        "reference_mfe_r": _numbers(old, "max_favourable_r").to_numpy(), "candidate_mfe_r": _numbers(new, "max_favourable_r").to_numpy(),
        "candidate_partial_fraction": _numbers(new, "partial_fraction", 0.0).to_numpy(),
        "candidate_realised_partial_gross_return": _numbers(new, "realised_partial_gross_return", 0.0).to_numpy(),
        "candidate_takeover_active": _booleans(new, "takeover_active").to_numpy(),
    })
    summary = {
        "reference": REFERENCE, "candidate": arm_id,
        "registered_same_entry_events": len(reference), "same_event_closed": len(ids),
        "not_closed_in_both": len(reference) - len(ids),
        "mean_delta_net_bp": float(delta.mean() * 10000),
        "median_delta_net_bp": float(delta.median() * 10000),
        "gained_delta_sum_event_bp": float(delta.clip(lower=0).sum() * 10000),
        "lost_delta_sum_event_bp": float(delta.clip(upper=0).sum() * 10000),
        "net_delta_sum_event_bp": float(delta.sum() * 10000),
        "improved_events": int(delta.gt(0).sum()), "worsened_events": int(delta.lt(0).sum()),
        "unchanged_events": int(delta.eq(0).sum()),
        "rescued_baseline_nonpositive_to_profit": int((old_net.le(0) & new_net.gt(0)).sum()),
        "baseline_profit_to_nonpositive": int((old_net.gt(0) & new_net.le(0)).sum()),
        "baseline_losers_improvement_sum_event_bp": float(delta.loc[old_net.lt(0)].clip(lower=0).sum() * 10000),
        "baseline_winners_trimmed_profit_sum_event_bp": float(delta.loc[old_net.gt(0)].clip(upper=0).sum() * 10000),
        "same_events_reference_mean_net_bp": float(old_net.mean() * 10000),
        "same_events_candidate_mean_net_bp": float(new_net.mean() * 10000),
    }
    return table, summary


def _require_committed(path: Path) -> Dict[str, str]:
    relative = str(path.resolve().relative_to(ROOT))
    frozen = subprocess.run(["git", "show", "HEAD:" + relative], cwd=ROOT, check=True, capture_output=True).stdout
    if frozen != path.read_bytes():
        raise RuntimeError("Commit exact diagnostic builder and inputs before running: " + relative)
    return {"path": relative, "sha256": digest(path)}


def build(output_dir: Path = DEFAULT_OUTPUT) -> Dict[str, Any]:
    """Complete descriptive analysis of the rejected V2 development experiment."""
    output_dir = Path(output_dir).resolve()
    results = EXPERIMENT / "results"
    if output_dir == results.resolve() or results.resolve() in output_dir.parents:
        raise ValueError("Diagnostic outputs must be separate from original experiment results")
    if (output_dir / "all_arm_summary.json").exists():
        raise RuntimeError("Preserve completed diagnostic output; no silent overwrite")
    config_path, selection_path = EXPERIMENT / "config.json", results / "selection.json"
    manifest = [_require_committed(Path(__file__)), _require_committed(config_path), _require_committed(selection_path)]
    for dependency in (
        "yoyo/evaluation/hourly_impulse_staged_research.py",
        "yoyo/evaluation/hourly_impulse_research.py",
        "yoyo/evaluation/hourly_impulse_diagnostics.py",
        "yoyo/layers/l3_backtest/hourly_impulse_staged.py",
        "yoyo/layers/l3_backtest/hourly_impulse.py",
        "yoyo/data/hourly_impulse.py",
    ):
        manifest.append(_require_committed(ROOT / dependency))
    config, selection = json.loads(config_path.read_text()), json.loads(selection_path.read_text())
    if selection["config_sha256"] != digest(config_path):
        raise RuntimeError("V2 frozen selection/configuration mismatch")
    if selection.get("go_to_transport") is not False:
        raise RuntimeError("This diagnostic completion is restricted to rejected development")
    base_path = ROOT / config["base_config"]
    manifest.append(_require_committed(base_path))
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Registered parent configuration changed")
    base = json.loads(base_path.read_text())
    expected_entry = {**base["baseline"], **config["entry_override"]}
    if selection["entry"] != expected_entry:
        raise RuntimeError("Saved entry differs from registered fixed V2 entry")
    if any(config[key] != base["execution"][key] for key in ("cost_fraction", "max_hours")):
        raise RuntimeError("Frozen economics mismatch")
    if config["cost_fraction"] != 0.002:
        raise RuntimeError("This completion retains the registered 20bp cost")
    policies = config["policies"]
    ids = [policy["id"] for policy in policies]
    if len(policies) != EXPECTED_ARM_COUNT or len(set(ids)) != EXPECTED_ARM_COUNT or REFERENCE not in ids:
        raise RuntimeError("Expected exactly the six registered staged policies")
    ledgers = {}
    for policy in policies:
        path = results / ("development_" + policy["id"] + "_trades.csv.gz")
        manifest.append(_require_committed(path))
        ledgers[policy["id"]] = pd.read_csv(path)
    reference = ledgers[REFERENCE]
    if len(reference) != EXPECTED_EVENT_COUNT:
        raise RuntimeError("Expected the registered 111-event development ledger")
    for arm_id, frame in ledgers.items():
        assert_common_contract(reference, frame)
        old = reference.set_index("event_id")["closed"].sort_index()
        new = frame.set_index("event_id")["closed"].sort_index()
        if not old.equals(new):
            raise RuntimeError("Closed-event sets differ; cannot claim identical random-control assignments")

    # This exact constructor cannot open an audit source prefix. The V1 reader
    # itself proves the physical pre-holdout boundary before price materialisation.
    study = Study(base, "development")
    output_dir.mkdir(parents=True, exist_ok=True)
    fold_names = [fold[0] for fold in study.folds]
    arm_rows, matched_rows, paired_rows, paired_event_frames = [], [], [], []
    details, control_reference = {}, None
    ordered_policies = sorted(policies, key=lambda policy: policy["id"] != REFERENCE)
    for policy in ordered_policies:
        arm_id = policy["id"]
        trades = ledgers[arm_id]
        controls, pairs, matched_info = matched(study, trades, policy, selection["entry"])
        if control_reference is None:
            control_reference = controls.copy()
        else:
            assert_common_contract(control_reference, controls, controls=True)
        classified, path_info = staged_path_summary(trades, config["cost_fraction"])
        info = metrics(trades, fold_names)
        paired_events, paired_info = paired_against_colour15(reference, trades, arm_id)
        details[arm_id] = {"policy": policy, "metrics": info, "path_diagnostics": path_info, "matched": matched_info, "paired_vs_colour15": paired_info}
        compact_info = {key: value for key, value in info.items() if not isinstance(value, (list, dict))}
        compact_path = {key: value for key, value in path_info.items() if not isinstance(value, (list, dict))}
        arm_rows.append({"arm": arm_id, **compact_info, **compact_path})
        matched_rows.append({"arm": arm_id, **matched_info, "identical_control_assignment_verified": True})
        paired_rows.append(paired_info)
        paired_event_frames.append(paired_events)
        write_csv(output_dir / (arm_id + "_controls.csv.gz"), controls)
        write_csv(output_dir / (arm_id + "_matched_pairs.csv"), pairs)
        write_csv(output_dir / (arm_id + "_classified.csv.gz"), classified)
        write_csv(output_dir / (arm_id + "_losing_trades.csv.gz"), classified.loc[classified["net_loser"]])
        print(json.dumps(clean({"completed_arm": arm_id, "closed_events": info["events"], "matched": matched_info, "same_events": paired_info["same_event_closed"]})), flush=True)

    write_csv(output_dir / "all_arm_metrics.csv", pd.DataFrame(arm_rows))
    write_csv(output_dir / "all_arm_matched_summary.csv", pd.DataFrame(matched_rows))
    write_csv(output_dir / "paired_vs_colour15_summary.csv", pd.DataFrame(paired_rows))
    write_csv(output_dir / "paired_vs_colour15_events.csv.gz", pd.concat(paired_event_frames, ignore_index=True))
    for source in manifest:
        if digest(ROOT / source["path"]) != source["sha256"]:
            raise RuntimeError("Original builder/input changed during diagnostic replay")
    summary = {
        "status": "descriptive_completion_no_selection_change_no_transport",
        "source_manifest": manifest, "source_receipt": study.source_receipt,
        "phase": "development", "registered_event_count": EXPECTED_EVENT_COUNT,
        "registered_arm_count": EXPECTED_ARM_COUNT, "reference": REFERENCE,
        "entry": selection["entry"], "original_selection_unchanged": selection,
        "identical_control_assignments_verified": True,
        "arms": details, "funding_modelled": False,
        "interpretation": "Observed failure paths and partial/takeover cohorts are descriptive, not causal or usable as future-entry filters. All sums are overlapping event-notional sums, not portfolio returns. No arm was selected or retuned by this helper.",
    }
    write_json(output_dir / "all_arm_summary.json", summary)
    return clean(summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = build(args.output_dir)
    print(json.dumps({"status": summary["status"], "arms": list(summary["arms"]), "output_dir": str(args.output_dir)}, sort_keys=True))


if __name__ == "__main__":
    main()
