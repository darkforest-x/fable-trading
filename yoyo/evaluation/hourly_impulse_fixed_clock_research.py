"""V24 frozen random background samples and OPEN-only fixed-clock labels.

Sampling uses only V23 saved mother IDs/own decision/direction/fold and known
admissible edges; original signal/ATR/risk fields remain unchanged. Canonical
connected components must be complete bipartite with at least three candidates
per mother. One Generator(PCG64(20260907)) stream permutes each sorted candidate
list once; sorted mothers receive successive triples. No outcomes, old exits,
future label availability, seed search or capacity-witness allocation is used.

The source reader first validates metadata and the entire physical timestamp
column, then hashes archive bytes, then materializes only open_time/open rows
strictly before 2025. It does not resample, drop invalid OHLC rows or inspect
HLC/volume. Labels use [E,E+H] complete 5m OPEN grids for H=1/4/12/24 hours;
only 4h is primary. These are future labels, not entry features or executable
returns. All original 251 mothers remain; unmatched mothers can have known own
labels while their paired excess remains unknown. Inference is not run here.

Official version-specific APIs (numpy 2.0.2, pandas 2.3.3):
https://numpy.org/doc/2.0/reference/random/bit_generators/pcg64.html
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.permutation.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_fixed_clock import build_fixed_clock_labels, HORIZONS_HOURS

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24"
EXPERIMENT = Path("experiments/active") / EXPERIMENT_ID
V23 = Path("experiments/active/exp-btcusdtp-1h-background-support-preholdout-20260907-v23/results")
MOTHERS = Path("experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz")
BASE = Path("experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json")
AUDIT = Path("data/kline_preholdout_okx_5m/archive_BTC_USDT_SWAP.json")
V23_HASHES = {
    "allocation.csv.gz": "fba2f167a0cdd0e9fe8e99bdf90442fba5a92d73497dd74d8acc6215b872752a",
    "assignments.csv.gz": "e97b433b51acb5386fe7a5d6afe63cc5bc893b675a7c172ec403ea7b36fbeec1",
    "component_capacity.csv.gz": "95b5589c12631d0ced49a4d58f9fe2c0ef7dae04e8f59ba36a8d015d07b7eb0e",
    "controls.csv.gz": "fae5176d36bd3882bf0fcd98487ef16e916fb5402387c0fa42345019df2974ba",
    "eligible_edges.csv.gz": "d045d5a05e71d93bde0af5402496292669bfbd9a71cc18c98f5b097d7d5ea1a8",
    "fold_coverage.csv.gz": "46f718f3320d491e7399cc1210cb0b4c847aff336a15bf821598fa39edc69210",
    "matching_frame.csv.gz": "4745cd147d571b2cc47ef6801f6868912910eccecd4c9a1e8e23bda3e2ac9023",
    "mother_support.csv.gz": "71886909385176088c2eec8f62ee2932ae4fc9d28f29a687aa08aa665f8dcd78",
    "original_mothers.csv.gz": "743a32c6fb91c87d4acf30eca50289db64c92627f75e3676c9498b6085d5985c",
    "stage_counts.csv.gz": "aa6e00b19ad0806e7271997c01608a0424837a500f2c165fffbe19fc2222a2c8",
    "started.json": "cd2226ef1316816dcae422c23ae13107b82ada8f8e44d8b3e77c11c2e03c5b5a",
    "summary.json": "e5455a429e7664d25e4ab91df0df7ef1fdbb9298f5423309c89f88e36de46384",
    "support_frozen.json": "83e7cf528566eafc81ac1ea70a250e88c44c797b90d913741f6c2bb2646bd790",
}
INPUTS = {**{str(V23 / name): sha for name, sha in V23_HASHES.items()},
          str(MOTHERS): "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
          str(BASE): "95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a",
          str(AUDIT): "c14aae71600d7f91b27068976656b9d4a8dc1bfb462df182a2e2a78ac85d5228"}
SOURCE = {"path": "data/kline_preholdout_okx_5m/okx_BTC_USDT_SWAP_5m_341567.csv",
          "audit": str(AUDIT), "sha256": "767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac",
          "end_exclusive": "2026-02-28T16:00:00Z"}
FOLDS = [("2023H1", "2023-01-01", "2023-07-01"), ("2023H2", "2023-07-01", "2024-01-01"),
         ("2024H1", "2024-01-01", "2024-07-01"), ("2024H2", "2024-07-01", "2025-01-01")]
SEED = 20260907
PHASE_END = pd.Timestamp("2025-01-01T00:00:00Z")
HOLDOUT = pd.Timestamp("2026-05-04T00:00:00Z")
META = ["request_kind", "mother_id", "control_slot", "mother_month", "matched_support"]
SOURCES = ["yoyo/evaluation/hourly_impulse_fixed_clock_research.py", "tests/test_hourly_impulse_fixed_clock_research.py",
           "yoyo/evaluation/hourly_impulse_fixed_clock_analysis.py", "tests/test_hourly_impulse_fixed_clock_analysis.py",
           "scripts/diagnose_hourly_impulse_failed_confirm_v18.py",
           "yoyo/evaluation/hourly_impulse_fixed_clock.py", "tests/test_hourly_impulse_fixed_clock.py",
           "yoyo/evaluation/hourly_impulse_fixed_clock_statistics.py", "tests/test_hourly_impulse_fixed_clock_statistics.py",
           str(EXPERIMENT / "PROJECT_PLAN.md"), str(EXPERIMENT / "config.json"), str(BASE)]


def frozen_config():
    return {"experiment_id": EXPERIMENT_ID, "inputs": INPUTS, "source": SOURCE,
            "folds": [list(row) for row in FOLDS], "phase_end_exclusive": str(PHASE_END),
            "population": {"mothers": 251, "matched_mothers": 248, "controls": 744, "unmatched_mothers": 3},
            "sampling": {"bit_generator": "PCG64", "seed": SEED, "streams": 1, "count": 3,
                         "component_order": "minimum_mother_id", "mother_order": "sorted_id",
                         "candidate_order_before_permutation": "sorted_UTC_ISO_id", "one_permutation_per_component": True,
                         "complete_bipartite_required": True, "required_supply": "3*mother_count",
                         "fallback": False, "resample_missing_labels": False},
            "embargo_hours": 72, "primary_horizon_hours": 4, "horizons_hours": [1, 4, 12, 24],
            "cost_threshold_fraction": .002, "execution_pnl": False, "old_exits_used": False,
            "inference_executed_by_runner": False, "holdout_consumed": False,
            "training_eligible": False, "production_eligible": False,
            "statistics_preregistration": {"primary_metrics": ["all_case_cost_threshold_markout", "complete_triplet_excess"],
                "calendar_months": 24, "weighting": "event_sum_over_count", "resamples": 9999,
                "bit_generator": "PCG64", "seed": SEED, "stream_independent_of_sampling": True,
                "interval": "95_percent_percentile", "p": "one_sided_monthly_sum_signflip_plus_one",
                "shared_month_indices_and_signs_across_horizons": True, "minimum_known_cases": 226,
                "minimum_complete_pairs": 226, "continue_requires": "both_means_positive_both_CI_lower_positive_both_p_lt_.01_and_four_fold_case_means_positive",
                "continue_is_profitability_acceptance": False}}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return str(value) if isinstance(value, (Path, pd.Timestamp)) else value


def write_json(path, value):
    Path(path).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_tables(directory, tables):
    hashes = {}
    for name, frame in tables.items():
        path = directory / (name + ".csv.gz")
        if path.exists():
            raise FileExistsError(str(path))
        frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        hashes[path.name] = digest(path)
    return hashes


def stamp(value):
    if isinstance(value, (bool, int, float, np.number)):
        raise ValueError("Explicit UTC clocks required")
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Explicit UTC clocks required")
    return result.tz_convert("UTC")


def _git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def committed_sources(root):
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    receipts = []
    for path in SOURCES:
        sha = hashlib.sha256(_git(root, "show", commit + ":" + path)).hexdigest()
        if digest(root / path) != sha:
            raise ValueError("Builder source is not committed: " + path)
        receipts.append({"path": path, "sha256": sha})
    return commit, receipts


def verify_inputs(root):
    """Byte/source-commit provenance only, before any input table is parsed."""
    for path, sha in INPUTS.items():
        if digest(root / path) != sha:
            raise ValueError("Input SHA mismatch: " + path)
    started, frozen, summary = [json.loads((root / V23 / name).read_text()) for name in
                                ("started.json", "support_frozen.json", "summary.json")]
    if not stamp(started["at"]) <= stamp(frozen["generated_at"]) <= stamp(summary["generated_at"]):
        raise ValueError("V23 checkpoint chronology mismatch")
    if summary["status"] != "background_support_passed" or not summary["coverage_gate_passed"] or summary["maximum_matched"] != 248 or summary["mothers"] != 251:
        raise ValueError("V23 support population failed")
    if summary["outcomes_read_or_computed"] or frozen["outcomes_read_or_computed"] or frozen["capacity_attempted"]:
        raise ValueError("V23 was not outcome-free before capacity")
    if started["sources"] != frozen["sources"] or started["sources"] != summary["sources"] or started["builder_commit"] != summary["builder_commit"]:
        raise ValueError("V23 source receipt disagreement")
    for name, sha in summary["output_hashes"].items():
        if V23_HASHES.get(name) != sha:
            raise ValueError("V23 output identity mismatch")
    for name, sha in frozen["output_hashes"].items():
        if summary["output_hashes"].get(name) != sha:
            raise ValueError("V23 frozen graph changed")
    for source in started["sources"]:
        if hashlib.sha256(_git(root, "show", started["builder_commit"] + ":" + source["path"])).hexdigest() != source["sha256"]:
            raise ValueError("V23 source differs from its builder commit")
    commit_time = pd.Timestamp(_git(root, "show", "-s", "--format=%cI", started["builder_commit"]).decode().strip())
    if commit_time > stamp(started["at"]):
        raise ValueError("V23 source commit followed materialization")
    lineage = frozen["source_receipt"]
    if lineage != summary["source_receipt"] or lineage["inputs"].get(str(MOTHERS)) != INPUTS[str(MOTHERS)]:
        raise ValueError("V4 mother lineage mismatch")
    prior = lineage["upstream_source_receipt"]
    if stamp(prior["phase_price_last_open"]) >= PHASE_END or prior["holdout_price_rows"] != 0:
        raise ValueError("V23 upstream phase reaches forbidden prices")
    base = json.loads((root / BASE).read_text())
    if base["source"] != SOURCE:
        raise ValueError("Frozen raw source contract changed")
    return {"inputs": dict(INPUTS), "parent_builder_commit": started["builder_commit"],
            "parent_source_pins_verified": len(started["sources"]), "support_only_parent": True}


def _requests(frame):
    required = {"event_id", "signal_time", "decision_time", "direction", "fold"}
    if not frame.columns.is_unique or not required.issubset(frame) or frame.event_id.isna().any() or frame.event_id.duplicated().any():
        raise ValueError("Invalid request identities/schema")
    if set(META) & set(frame):
        raise ValueError("Input request metadata would be overwritten")
    result = frame.copy(deep=True)
    limits = {fold: (pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") - pd.Timedelta(hours=72)) for fold, start, end in FOLDS}
    for column in ("signal_time", "decision_time"):
        result[column] = result[column].map(stamp)
    for row in result.itertuples():
        if not isinstance(row.event_id, str) or not row.event_id.strip() or row.fold not in limits:
            raise ValueError("Unknown event/fold")
        if isinstance(row.direction, (bool, np.bool_)) or row.direction not in (-1, 1):
            raise ValueError("Invalid own direction")
        if row.decision_time != row.decision_time.floor("h") or row.signal_time + pd.Timedelta(hours=1) != row.decision_time:
            raise ValueError("Own K1 clock mismatch")
        if not limits[row.fold][0] <= row.decision_time < limits[row.fold][1]:
            raise ValueError("Request breaches fold/72h embargo")
    return result


def _components(ids, edges):
    by_mother, by_candidate = {event: set() for event in ids}, {}
    for event, candidate in edges[["event_id", "candidate_id"]].itertuples(index=False, name=None):
        by_mother[event].add(candidate)
        by_candidate.setdefault(candidate, set()).add(event)
    seen, groups = set(), []
    for first in sorted(ids):
        if first in seen:
            continue
        pending, mothers, candidates = [first], set(), set()
        while pending:
            event = pending.pop()
            if event in mothers:
                continue
            mothers.add(event)
            candidates |= by_mother[event]
            for candidate in by_mother[event]:
                pending.extend(by_candidate[candidate] - mothers)
        seen |= mothers
        groups.append((sorted(mothers), sorted(candidates)))
    return groups, by_mother


def sample_controls(mothers, edges, support):
    """Pure pre-label allocation; never borrow the V23 MILP witness or retry.

    Only mother identity/time/direction and admissible edges affect sampling.
    Support rows provide unknown reasons. Same four clocks share this allocation.
    """
    m = _requests(mothers)
    if not {"event_id", "candidate_id", "candidate_time", "fold", "synthetic_stop", "mother_risk_atr"}.issubset(edges) or edges.duplicated(["event_id", "candidate_id"]).any():
        raise ValueError("Invalid admissible edges")
    if not {"event_id", "support_reason", "available_controls"}.issubset(support) or support.event_id.duplicated().any() or set(support.event_id) != set(m.event_id):
        raise ValueError("Every original mother needs support status")
    if not set(edges.event_id).issubset(m.event_id) or edges.candidate_id.isna().any():
        raise ValueError("Unknown edge identity")
    lookup, reasons = m.set_index("event_id"), support.set_index("event_id")
    e = edges.copy(deep=True)
    e["candidate_time"] = e.candidate_time.map(stamp)
    actual = set(m.decision_time)
    for row in e.itertuples():
        own = lookup.loc[row.event_id]
        if row.candidate_id != row.candidate_time.isoformat() or row.fold != own.fold or row.candidate_time in actual:
            raise ValueError("Candidate time/fold/actual-mother identity mismatch")
        if row.candidate_time.strftime("%Y-%m") != own.decision_time.strftime("%Y-%m") or row.candidate_time.hour // 6 != own.decision_time.hour // 6:
            raise ValueError("Candidate month/UTC-six-hour key mismatch")
        if not np.isfinite([row.synthetic_stop, row.mother_risk_atr]).all() or min(row.synthetic_stop, row.mother_risk_atr) <= 0:
            raise ValueError("Invalid saved transferred-risk edge")
    groups, by_mother = _components(m.event_id.tolist(), e)
    rng = np.random.Generator(np.random.PCG64(SEED))
    initial_state = rng.bit_generator.state
    allocations, components, controls, assignments = [], [], [], []
    for component, (ids, candidates) in enumerate(groups):
        if any(by_mother[event] != set(candidates) for event in ids):
            raise ValueError("Component is not complete bipartite; no alternate allocation permitted")
        if candidates and len(candidates) < 3 * len(ids):
            raise ValueError("Component supply is insufficient; no alternate seed permitted")
        for event in ids:
            record = reasons.loc[event]
            if candidates and (record.support_reason != "eligible" or record.available_controls != len(candidates)):
                raise ValueError("Edge and mother support disagree")
            if not candidates and record.support_reason == "eligible":
                raise ValueError("Known eligible mother unexpectedly has zero-edge support")
        permutation = rng.permutation(np.asarray(candidates, dtype=object)).tolist() if candidates else []
        components.append({"component_id": component, "minimum_mother_id": ids[0],
                           "mother_ids": json.dumps(ids), "candidate_ids": json.dumps(candidates),
                           "mother_count": len(ids), "candidate_count": len(candidates),
                           "selected_controls": 3 * len(ids) if candidates else 0,
                           "supply_surplus": len(candidates) - 3 * len(ids) if candidates else None,
                           "complete_bipartite": True})
        for ordinal, event in enumerate(ids):
            own = lookup.loc[event]
            assignments.append({"event_id": event, "matched_support": bool(candidates),
                                "assigned_controls": 3 if candidates else 0, "support_reason": reasons.loc[event].support_reason,
                                "component_id": component})
            for slot, candidate in enumerate(permutation[ordinal * 3:ordinal * 3 + 3]):
                time = stamp(candidate)
                control_id = event + "::fixed_clock_control" + str(slot)
                allocations.append({"event_id": event, "candidate_id": candidate, "candidate_time": time,
                                    "component_id": component, "control_slot": slot, "control_event_id": control_id})
                controls.append({"event_id": control_id, "signal_time": time - pd.Timedelta(hours=1),
                                 "decision_time": time, "direction": int(own.direction), "fold": own.fold,
                                 "request_kind": "control", "mother_id": event, "control_slot": slot,
                                 "mother_month": own.decision_time.strftime("%Y-%m"), "matched_support": True})
    assignment = pd.DataFrame(assignments, columns=["event_id", "matched_support", "assigned_controls", "support_reason", "component_id"])
    matched = set(assignment.loc[assignment.matched_support, "event_id"])
    cases = mothers.copy(deep=True)
    cases["request_kind"], cases["mother_id"], cases["control_slot"] = "case", cases.event_id, pd.NA
    cases["mother_month"] = m.decision_time.dt.strftime("%Y-%m")
    cases["matched_support"] = cases.event_id.isin(matched)
    control_frame = pd.DataFrame(controls, columns=["event_id", "signal_time", "decision_time", "direction", "fold"] + META)
    if not control_frame.empty:
        _requests(control_frame.drop(columns=META))
        if control_frame.decision_time.duplicated().any():
            raise ValueError("Control timestamps were reused")
    tables = {"original_mothers": mothers.copy(deep=True), "case_requests": cases,
              "control_requests": control_frame,
              "random_allocation": pd.DataFrame(allocations, columns=["event_id", "candidate_id", "candidate_time", "component_id", "control_slot", "control_event_id"]),
              "random_assignments": assignment.set_index("event_id").reindex(m.event_id).reset_index(),
              "sampling_components": pd.DataFrame(components)}
    return tables, {"seed": SEED, "bit_generator": "PCG64", "streams": 1,
                    "initial_rng_state": initial_state, "final_rng_state": rng.bit_generator.state,
                    "mothers": len(m), "matched_mothers": len(matched), "controls": len(controls),
                    "unmatched_mothers": len(m) - len(matched), "no_reuse": True, "fallback_used": False,
                    "outcomes_used": False, "selection_frozen_before_labels": True,
                    "random_sampling_is_random_treatment_assignment": False}


def load_open_source(root, source, audit_sha):
    """Timestamp preflight -> archive hash -> nrows OPEN-only 2023/24 prefix.

    Invalid opens remain untouched for unknown labels. The 2022 warmup prefix
    can remain in the saved source, but no post-2024 price is materialized.
    """
    audit_path, path = root / source["audit"], root / source["path"]
    if digest(audit_path) != audit_sha:
        raise ValueError("Source audit SHA mismatch")
    audit = json.loads(audit_path.read_text())
    if audit.get("status") != "complete" or audit.get("holdout_ohlcv_rows_materialized") != 0 or audit.get("output_sha256") != source["sha256"]:
        raise ValueError("Archive audit contract failed")
    source_end = stamp(source["end_exclusive"])
    if source_end > HOLDOUT or stamp(audit["last_time"]) >= min(source_end, HOLDOUT):
        raise ValueError("Archive audit reaches source boundary or holdout")
    time_frame = pd.read_csv(path, usecols=["open_time"], dtype=str, keep_default_na=False)
    times = pd.DatetimeIndex(time_frame.open_time.map(stamp))
    if not len(times) or not times.is_monotonic_increasing or not times.is_unique or (times.asi8 % (300 * 10**9)).any():
        raise ValueError("Physical timestamp uniqueness/order/grid failed")
    if times.max() >= min(source_end, HOLDOUT) or len(times) != audit["rows"] or times.min() != stamp(audit["first_time"]) or times.max() != stamp(audit["last_time"]):
        raise ValueError("Physical timestamp/audit boundary mismatch")
    archive_sha = digest(path)
    if archive_sha != source["sha256"]:
        raise ValueError("Archive SHA mismatch after timestamp preflight")
    count = int((times < PHASE_END).sum())
    raw = pd.read_csv(path, usecols=["open_time", "open"], nrows=count, dtype=str, keep_default_na=False)
    raw["open_time"] = raw.open_time.map(stamp)
    if len(raw) != count or not pd.DatetimeIndex(raw.open_time).equals(times[:count]) or not raw.open_time.lt(PHASE_END).all():
        raise ValueError("OPEN prefix changed during materialization")
    return raw, {"source": source, "audit_sha256": audit_sha, "sha256": archive_sha,
                 "physical_rows": len(times), "phase_rows": len(raw), "phase_price_last_open": raw.open_time.max(),
                 "physical_last_open": times.max(), "price_columns_materialized": ["open"],
                 "holdout_price_rows": 0, "post2024_price_rows": 0, "rows_dropped_for_hlc_or_open_validity": 0,
                 "timestamp_preflight_before_price_hash": True, "resampling_performed": False}


def label_requests(raw, requests):
    folds = {name: (pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")) for name, start, end in FOLDS}
    labels = build_fixed_clock_labels(raw, requests, folds)
    metadata = requests[["event_id"] + META]
    return labels.merge(metadata, on="event_id", how="left", validate="many_to_one", sort=False)


def paired_labels(case_labels, control_labels):
    """All mother/horizon rows, with three-known-controls-or-unknown excess."""
    groups = {(mother, hours): group for (mother, hours), group in control_labels.groupby(["mother_id", "horizon_hours"], sort=False)}
    rows = []
    for case in case_labels.to_dict("records"):
        controls = groups.get((case["event_id"], case["horizon_hours"]), control_labels.iloc[:0])
        if len(controls) not in (0, 3) or controls.event_id.duplicated().any():
            raise ValueError("Partial or duplicate control group")
        if bool(len(controls)) != bool(case["matched_support"]):
            raise ValueError("Frozen support differs from labeled controls")
        if len(controls) and (set(controls.control_slot) != {0, 1, 2} or not controls.fold.eq(case["fold"]).all() or not controls.direction.eq(case["direction"]).all() or not controls.mother_month.eq(case["mother_month"]).all()):
            raise ValueError("Own control linkage changed")
        known = controls.status.eq("known") & np.isfinite(controls.gross_markout) & np.isfinite(controls.cost_threshold_markout)
        ck = case["status"] == "known" and np.isfinite([case["gross_markout"], case["cost_threshold_markout"]]).all()
        complete = ck and len(controls) == 3 and bool(known.all())
        control_gross = controls.gross_markout.mean() if len(controls) == 3 and known.all() else np.nan
        control_cost = controls.cost_threshold_markout.mean() if len(controls) == 3 and known.all() else np.nan
        row = {key: case[key] for key in ["event_id", "mother_id", "fold", "direction", "decision_time", "mother_month", "horizon_hours", "role"]}
        row.update(case_status=case["status"], case_reason=case["reason"], case_known=bool(ck),
                   matched_support=case["matched_support"], n_controls_expected=3, n_controls_assigned=len(controls),
                   n_controls_known=int(known.sum()), pair_complete=bool(complete),
                   pair_reason="known" if complete else "unmatched_support" if not len(controls) else "unknown_case_label" if not ck else "unknown_control_label",
                   case_gross_markout=case["gross_markout"], case_cost_threshold_markout=case["cost_threshold_markout"],
                   control_mean_gross_markout=control_gross, control_mean_cost_threshold_markout=control_cost,
                   gross_excess_markout=case["gross_markout"] - control_gross if complete else np.nan,
                   cost_threshold_excess_markout=case["cost_threshold_markout"] - control_cost if complete else np.nan,
                   control_event_ids=json.dumps(controls.sort_values("control_slot").event_id.tolist()))
        rows.append(row)
    return pd.DataFrame(rows)


def descriptive_summary(cases, controls, pairs):
    rows = []
    for hours in HORIZONS_HOURS:
        c, p = cases.loc[cases.horizon_hours.eq(hours)], pairs.loc[pairs.horizon_hours.eq(hours)]
        k = c.loc[c.status.eq("known")]
        complete = p.loc[p.pair_complete]
        ctrl = controls.loc[controls.horizon_hours.eq(hours)]
        rows.append({"horizon_hours": hours, "role": "primary" if hours == 4 else "descriptive",
                     "all_cases": len(c), "known_cases": len(k), "unknown_cases": len(c)-len(k),
                     "case_mean_gross_bp": k.gross_markout.mean()*10000,
                     "case_mean_cost_threshold_bp": k.cost_threshold_markout.mean()*10000,
                     "matched_support_cases": int(c.matched_support.sum()), "complete_pairs": len(complete),
                     "paired_case_mean_cost_threshold_bp": complete.case_cost_threshold_markout.mean()*10000,
                     "control_triplet_mean_cost_threshold_bp": complete.control_mean_cost_threshold_markout.mean()*10000,
                     "mean_excess_bp": complete.cost_threshold_excess_markout.mean()*10000,
                     "all_controls": len(ctrl), "known_controls": int(ctrl.status.eq("known").sum()),
                     "unknown_controls": int(ctrl.status.ne("known").sum())})
    return rows


def run(root=ROOT):
    directory = root / EXPERIMENT / "results"
    if directory.exists():
        raise FileExistsError("Refusing existing result directory")
    if json.loads((root / EXPERIMENT / "config.json").read_text()) != frozen_config():
        raise ValueError("V24 frozen configuration changed")
    commit, sources = committed_sources(root)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "started.json", {"at": pd.Timestamp.now(tz="UTC"), "builder_commit": commit,
               "sources": sources, "inputs": INPUTS, "config_sha256": digest(root / EXPERIMENT / "config.json")})
    try:
        receipt = verify_inputs(root)
        mothers = pd.read_csv(root / V23 / "original_mothers.csv.gz")
        original = pd.read_csv(root / MOTHERS)
        pd.testing.assert_frame_equal(mothers, original, rtol=1e-12, atol=1e-12)
        if len(mothers) != 251 or mothers.groupby("fold").size().to_dict() != dict(zip([x[0] for x in FOLDS], [55, 66, 55, 75])):
            raise ValueError("Original mother population changed")
        edges = pd.read_csv(root / V23 / "eligible_edges.csv.gz")
        support = pd.read_csv(root / V23 / "mother_support.csv.gz")
        tables, sampling = sample_controls(mothers, edges, support)
        if sampling["matched_mothers"] != 248 or sampling["controls"] != 744 or len(edges) != 13192:
            raise ValueError("Frozen sampling population mismatch")
        hashes = write_tables(directory, tables)
        write_json(directory / "sampling_frozen.json", {"at": pd.Timestamp.now(tz="UTC"), "sampling": sampling,
                   "input_receipt": receipt, "output_hashes": dict(hashes), "before_any_raw_read": True,
                   "before_any_label": True, "sources": sources})
        raw, source_receipt = load_open_source(root, SOURCE, INPUTS[str(AUDIT)])
        write_json(directory / "source_receipt.json", source_receipt)
        # Preserve exactly the two columns passed to the label engine for an
        # independent saved-evidence audit; never open another price column.
        hashes.update(write_tables(directory, {"open_prefix": raw}))
        cases = label_requests(raw, tables["case_requests"])
        controls = label_requests(raw, tables["control_requests"])
        pairs = paired_labels(cases, controls)
        if len(cases) != 1004 or len(controls) != 2976 or len(pairs) != 1004:
            raise ValueError("Label denominator changed")
        hashes.update(write_tables(directory, {"case_labels": cases, "control_labels": controls, "paired_labels": pairs}))
        for name, sha in hashes.items():
            if digest(directory / name) != sha:
                raise ValueError("Frozen output changed: " + name)
        summary = {"experiment_id": EXPERIMENT_ID, "status": "labels_materialized_not_economic_acceptance",
                   "mothers": 251, "matched_mothers": 248, "controls": 744, "unmatched_mothers": 3,
                   "primary_horizon_hours": 4, "horizons_hours": list(HORIZONS_HOURS),
                   "labels": {"case": len(cases), "control": len(controls), "paired": len(pairs)},
                   "descriptive": descriptive_summary(cases, controls, pairs), "sampling": sampling,
                   "output_hashes": hashes, "input_receipt": receipt, "source_receipt": source_receipt,
                   "sources": sources, "builder_commit": commit, "generated_at": pd.Timestamp.now(tz="UTC"),
                   "inference_executed": False, "independent_sample_count_is_label_rows": False,
                   "executable_pnl": False, "production_eligible": False, "training_eligible": False,
                   "holdout_consumed": False, "old_exit_paths_read": False,
                   "limitation": "Random background sampling is not randomized K1 treatment; repeated horizons and overlapping labels are dependent. Cost-threshold markouts ignore stops, funding and execution."}
        write_json(directory / "summary.json", summary)
        return summary
    except Exception as exc:
        write_json(directory / "failure.json", {"status": "failed_not_label_evidence", "error_type": type(exc).__name__,
                   "message": str(exc), "sampling_frozen": (directory / "sampling_frozen.json").exists(),
                   "at": pd.Timestamp.now(tz="UTC")})
        raise


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    summary = run()
    print(json.dumps(clean({key: summary[key] for key in
          ("experiment_id", "status", "mothers", "matched_mothers", "controls", "labels", "descriptive", "inference_executed")}),
          ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
