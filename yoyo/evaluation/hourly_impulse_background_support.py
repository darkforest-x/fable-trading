"""V23 outcome-free whole-K1 background support, not a repair of V10.

Inputs are SHA-pinned V4 original requests and V10 saved matching features.
No raw archive, exit, return, MFE or MAE is read or calculated. The exact keys
are decision month, UTC six-hour bucket and causal ATR-fraction tercile.
The inherited tercile uses signal-hour ATR/close against previous 720 hours
(minimum 168), excluding the current hour; all source features are available
at the request decision (signal_time + 1h). Existing matching_support validity,
current/prior-hour body-cross exclusions, real-mother exclusions and positive
transferred-risk stops remain required. Controls may be later in the same
month: this is offline contemporaneous support, not online randomization.
Each fold requires start <= decision < end - 72h. No outcome is generated.

Pure build_support_graph reads only IDs/clocks/direction/stop/ATR, saved
matching keys, support flags, known entry open and own signal OHLC/MA fields.
allocate_support uses this graph only; one exact MILP per connected component
has a fixed 30-second budget. Partial triples and reused candidate times are
forbidden. Equal-capacity allocations need not identify the same mothers.

Sources: hourly_impulse_k2_matching.build_matching_frame (saved V10 lineage),
hourly_impulse_matching_capacity.maximum_complete_matching; pandas 2.3 null
join warning https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
and SciPy 1.13.1 MILP status/dual/gap contract
https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.milp.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_matching_capacity import maximum_complete_matching

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-background-support-preholdout-20260907-v23"
EXPERIMENT = Path("experiments/active") / EXPERIMENT_ID
V10 = Path("experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/results")
MOTHERS = Path("experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz")
INPUTS = {
    str(V10 / "matching_frame.csv.gz"): "0a6a0c8461b33d13a9ca28346023033b56d7a00af1b39a6c827a774deb7a3009",
    str(V10 / "summary.json"): "6279ce97ac051e168e632291218a697ffc7558db611bcf5e007f23b51bb55440",
    str(V10 / "support_frozen.json"): "da1c2a5b4454fde29fc50385a056315f6f716989daa7b37db4c63ade9845e2c7",
    str(V10 / "started.json"): "07292f7c64235b0709e1e3b9479f287286eb197176e1baee1fa0b66ed60d7e72",
    str(MOTHERS): "b3f442ad8b0959b19cb5ae58fd40bc6a3bf40b455b4be31f3758d53940eea3e6",
}
FOLDS = [
    ("2023H1", "2023-01-01", "2023-07-01"),
    ("2023H2", "2023-07-01", "2024-01-01"),
    ("2024H1", "2024-01-01", "2024-07-01"),
    ("2024H2", "2024-07-01", "2025-01-01"),
]
KEYS = ["month", "utc_6h_bucket", "vol_bucket"]
FLAGS = ["known_entry_open", "entry_source_continuous", "known_5m_valid",
         "known_hourly_valid", "raw_strict_body_cross", "current_or_prior_cross_excluded",
         "actual_mother_decision_excluded", "matching_support", "candidate_eligible"]
FRAME_FIELDS = ["open_time", "signal_time", "decision_time", "signal_atr", "entry_open",
                "source_segment_id", "entry_source_segment_id", "known_5m_available",
                "known_5m_colour", "known_hourly_colour", "unsigned_hourly_slope_sign",
                "ma", "open", "high", "low", "close"] + KEYS + FLAGS
MOTHER_FIELDS = ["event_id", "signal_time", "decision_time", "direction", "initial_stop",
                 "signal_atr", "fold"]
EDGE_FIELDS = ["event_id", "candidate_id", "candidate_time", "fold", "mother_risk_atr", "synthetic_stop"]
CONTROL_FIELDS = ["event_id", "parent_event_id", "matched_event_id", "source_mother_decision_time",
                  "signal_time", "decision_time", "direction", "initial_stop", "signal_atr",
                  "transferred_risk_atr", "entry_open", "fold", "signal_open", "signal_high",
                  "signal_low", "signal_close", "ma", "ma_side", "ma_slope_atr", "body_ratio",
                  "range_atr", "volume_ratio", "cross_count24", "efficiency24", "close_location",
                  "extension_atr", "vol_bucket", "known_5m_colour", "known_5m_available",
                  "known_hourly_colour", "signed_hourly_slope_sign", "source_segment_id",
                  "month", "utc_6h_bucket", "candidate_id", "component_id"]
SOURCES = ["yoyo/evaluation/hourly_impulse_background_support.py",
           "yoyo/evaluation/hourly_impulse_matching_capacity.py",
           "tests/test_hourly_impulse_background_support.py",
           "tests/test_hourly_impulse_matching_capacity.py",
           str(EXPERIMENT / "PROJECT_PLAN.md"), str(EXPERIMENT / "config.json")]


def frozen_config() -> Dict[str, Any]:
    return {"experiment_id": EXPERIMENT_ID, "question": "whole_K1_background_support",
            "symbol": "BTCUSDT.P", "inputs": dict(INPUTS), "folds": [list(x) for x in FOLDS],
            "matching_keys": list(KEYS), "direction": "mother_direction",
            "count_per_mother": 3, "expected_mothers": 251, "required_complete_mothers": 226,
            "embargo_hours": 72, "component_time_limit_seconds": 30.0,
            "allocation": "maximum_complete_matching_per_connected_component",
            "allocation_role": "capacity_witness_not_random_sample", "seed": None,
            "control_time_reuse": False, "fallback": False, "outcomes_read_or_computed": False,
            "raw_price_io": False, "holdout_consumed": False, "training_eligible": False,
            "production_eligible": False, "later_label_study": "separate_registration_required",
            "later_primary_hours": 4, "later_descriptive_hours": [1, 12, 24],
            "old_v10_reinterpreted": False}


def _times(values: pd.Series, name: str, *, nullable: bool = False) -> pd.Series:
    parsed = []
    for value in values:
        if pd.isna(value):
            if not nullable:
                raise ValueError(name + " must be known")
            parsed.append(pd.NaT)
            continue
        if isinstance(value, (int, float, bool, np.number)):
            raise ValueError(name + " must be an explicitly zoned timestamp")
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError(name + " must be explicitly UTC")
        if stamp != stamp.floor("h") and name != "known_5m_available":
            raise ValueError(name + " must be hourly")
        parsed.append(stamp)
    return pd.Series(pd.to_datetime(parsed, utc=True), index=values.index, name=values.name)


def _validate(mothers: pd.DataFrame, frame: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    for data, required, name in [(mothers, MOTHER_FIELDS, "mothers"), (frame, FRAME_FIELDS, "matching frame")]:
        if not data.columns.is_unique or not set(required).issubset(data):
            raise ValueError(name + " missing/duplicate columns")
        if any(any(word in c.lower() for word in ("outcome", "net_return", "gross_return", "mfe", "mae", "exit_price")) for c in data):
            raise ValueError("Outcome columns are forbidden in support input")
    m, h = mothers.copy(deep=True), frame.copy(deep=True)
    if m.event_id.isna().any() or m.event_id.duplicated().any() or not m.event_id.map(lambda x: isinstance(x, str) and bool(x)).all():
        raise ValueError("Unique nonempty mother IDs required")
    for column in ("signal_time", "decision_time"):
        m[column] = _times(m[column], column)
        h[column] = _times(h[column], column)
    h["open_time"] = _times(h.open_time, "open_time")
    h["known_5m_available"] = _times(h.known_5m_available, "known_5m_available", nullable=True)
    if h.decision_time.duplicated().any() or not h.decision_time.is_monotonic_increasing:
        raise ValueError("Matching decisions must be unique and sorted")
    if m.decision_time.duplicated().any():
        raise ValueError("Original mother decision times must be unique")
    if not (h.signal_time.eq(h.open_time) & h.decision_time.eq(h.signal_time + pd.Timedelta(hours=1))).all():
        raise ValueError("Matching signal/decision clock mismatch")
    if not m.decision_time.eq(m.signal_time + pd.Timedelta(hours=1)).all():
        raise ValueError("Mother signal/decision clock mismatch")
    if not h.open_time.lt(pd.Timestamp("2025-01-01", tz="UTC")).all():
        raise ValueError("Post-2024 source rows forbidden")
    if not m.fold.isin([x[0] for x in FOLDS]).all():
        raise ValueError("Unknown fold")
    if m.direction.map(lambda x: isinstance(x, (bool, np.bool_))).any() or not m.direction.isin([-1, 1]).all():
        raise ValueError("Mother direction must be signed one, not bool")
    for column in FLAGS:
        if h[column].isna().any() or not h[column].map(lambda x: isinstance(x, (bool, np.bool_))).all():
            raise ValueError("Support flags must be nonnull boolean: " + column)
    if not h.month.eq(h.decision_time.dt.strftime("%Y-%m")).all() or not h.utc_6h_bucket.eq(h.decision_time.dt.hour // 6).all():
        raise ValueError("Matching keys disagree with own decision clock")
    if not h.vol_bucket.dropna().isin([0, 1, 2]).all():
        raise ValueError("Invalid causal volatility bucket")
    cross = ((h.open.lt(h.ma) & h.close.gt(h.ma)) | (h.open.gt(h.ma) & h.close.lt(h.ma)))
    banned = set(h.loc[cross, "decision_time"])
    banned |= {x + pd.Timedelta(hours=1) for x in banned}
    if not cross.eq(h.raw_strict_body_cross).all() or not h.decision_time.isin(banned).eq(h.current_or_prior_cross_excluded).all():
        raise ValueError("Current/prior crossing flag mismatch")
    expected = (h.vol_bucket.notna() & np.isfinite(h.signal_atr) & h.signal_atr.gt(0)
                & h.known_entry_open & h.entry_source_continuous & h.known_5m_valid & h.known_hourly_valid)
    eligible = expected & ~h.current_or_prior_cross_excluded & ~h.actual_mother_decision_excluded
    if not expected.eq(h.matching_support).all() or not eligible.eq(h.candidate_eligible).all():
        raise ValueError("Inherited support eligibility mismatch")
    if not h.loc[h.known_entry_open, "entry_open"].map(lambda x: np.isfinite(x) and x > 0).all():
        raise ValueError("Invalid supported entry open")
    if not h.loc[h.entry_source_continuous, "source_segment_id"].notna().all() or not h.loc[h.entry_source_continuous, "source_segment_id"].eq(h.loc[h.entry_source_continuous, "entry_source_segment_id"]).all():
        raise ValueError("Source continuity mismatch")
    valid5 = h.loc[h.known_5m_valid]
    if not valid5.known_5m_colour.isin([-1, 1]).all() or not valid5.known_5m_available.eq(valid5.decision_time).all():
        raise ValueError("Known five-minute support mismatch")
    validhour = h.loc[h.known_hourly_valid]
    if not validhour.known_hourly_colour.isin([-1, 1]).all() or not np.isfinite(validhour.ma).all() or not validhour.unsigned_hourly_slope_sign.isin([-1, 0, 1]).all():
        raise ValueError("Known hourly support mismatch")
    return m, h


def build_support_graph(mothers: pd.DataFrame, matching_frame: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Build admissible edges without allocation, outcome access or file writes.

    All original columns/order/dtypes are retained in original_mothers and
    matching_frame. mother_support/stage_counts contain every mother, including
    invalid/warmup support. Stage counts are sequential opportunities per mother,
    not distinct control times or causal attributions of overlapping exclusions.
    """
    m, h = _validate(mothers, matching_frame)
    lookup = h.set_index("decision_time")
    actual = set(m.decision_time)
    audits, stages, edges = [], [], []
    fold_limits = {name: (pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") - pd.Timedelta(hours=72)) for name, start, end in FOLDS}
    for mother in m.to_dict("records"):
        time, event, direction = mother["decision_time"], mother["event_id"], int(mother["direction"])
        start, limit = fold_limits[mother["fold"]]
        record = {"event_id": event, "fold": mother["fold"], "decision_time": time,
                  "direction": direction, "support_reason": "eligible", "mother_risk_atr": np.nan,
                  "available_controls": np.nan, **{key: np.nan for key in KEYS}}
        counts = {"event_id": event, "fold": mother["fold"], **{key: np.nan for key in
                  ["same_keys", "within_fold_embargo", "valid_support", "after_cross_exclusion", "after_actual_exclusion", "valid_transferred_stop"]}}
        own = lookup.loc[time] if time in lookup.index else None
        if own is None:
            record["support_reason"] = "missing_mother_hourly_decision"
        elif not start <= time < limit:
            record["support_reason"] = "outside_fold_embargo"
        else:
            record.update({key: own[key] for key in KEYS})
            atr, stop = mother["signal_atr"], mother["initial_stop"]
            if not np.isfinite([atr, stop]).all() or min(atr, stop) <= 0:
                record["support_reason"] = "invalid_mother_risk"
            elif not own.known_entry_open or not own.entry_source_continuous:
                record["support_reason"] = "missing_or_gapped_mother_open"
            elif not np.isclose(atr, own.signal_atr, rtol=1e-9, atol=1e-12):
                record["support_reason"] = "mother_atr_mismatch"
            else:
                risk = direction * (own.entry_open - stop) / atr
                record["mother_risk_atr"] = risk
                if not np.isfinite(risk) or risk <= 0:
                    record["support_reason"] = "invalid_mother_risk"
                elif not own.matching_support or any(pd.isna(own[key]) for key in KEYS):
                    record["support_reason"] = "missing_causal_matching_support"
                else:
                    same = h.loc[h.month.eq(own.month) & h.utc_6h_bucket.eq(own.utc_6h_bucket) & h.vol_bucket.eq(own.vol_bucket)]
                    counts["same_keys"] = len(same)
                    same = same.loc[same.decision_time.ge(start) & same.decision_time.lt(limit)]
                    counts["within_fold_embargo"] = len(same)
                    same = same.loc[same.matching_support]
                    counts["valid_support"] = len(same)
                    same = same.loc[~same.current_or_prior_cross_excluded]
                    counts["after_cross_exclusion"] = len(same)
                    same = same.loc[~same.actual_mother_decision_excluded & ~same.decision_time.isin(actual)]
                    counts["after_actual_exclusion"] = len(same)
                    stops = same.entry_open - direction * risk * same.signal_atr
                    same = same.loc[np.isfinite(stops) & stops.gt(0)]
                    counts["valid_transferred_stop"] = len(same)
                    record["available_controls"] = len(same)
                    for candidate in same.to_dict("records"):
                        edges.append({"event_id": event, "candidate_id": candidate["decision_time"].isoformat(),
                                      "candidate_time": candidate["decision_time"], "fold": mother["fold"],
                                      "mother_risk_atr": risk,
                                      "synthetic_stop": candidate["entry_open"] - direction * risk * candidate["signal_atr"]})
        audits.append(record)
        stages.append(counts)
    return {"original_mothers": mothers.copy(deep=True), "matching_frame": matching_frame.copy(deep=True),
            "mother_support": pd.DataFrame(audits, columns=["event_id", "fold", "decision_time", "direction", "support_reason", "mother_risk_atr", "available_controls"] + KEYS),
            "stage_counts": pd.DataFrame(stages, columns=["event_id", "fold", "same_keys", "within_fold_embargo", "valid_support", "after_cross_exclusion", "after_actual_exclusion", "valid_transferred_stop"]),
            "eligible_edges": pd.DataFrame(edges, columns=EDGE_FIELDS)}


def _components(ids: list, edges: pd.DataFrame) -> list:
    by_mother = {event: set() for event in ids}
    by_candidate: Dict[str, set] = {}
    for event, candidate in edges[["event_id", "candidate_id"]].itertuples(index=False, name=None):
        by_mother[event].add(candidate)
        by_candidate.setdefault(candidate, set()).add(event)
    visited, result = set(), []
    for first in sorted(ids):
        if first in visited:
            continue
        queue, members = [first], set()
        while queue:
            event = queue.pop()
            if event in members:
                continue
            members.add(event)
            for candidate in by_mother[event]:
                queue.extend(by_candidate[candidate] - members)
        visited |= members
        result.append(sorted(members))
    return result


def allocate_support(graph: Dict[str, pd.DataFrame]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Exactly maximize complete triples per graph component; never fall back.

    Preserves graph tables, records every zero-edge mother, and emits control
    diagnostics from each control's own hour. Mother direction and risk/ATR are
    transferred, but colour, slope, MA and prices are not transferred.
    """
    tables = {key: value.copy(deep=True) for key, value in graph.items()}
    # The public allocation API never trusts externally edited edge attributes.
    # Rebuild all admissibility facts from the original, outcome-free inputs.
    rebuilt = build_support_graph(tables["original_mothers"], tables["matching_frame"])
    for key in ("mother_support", "stage_counts", "eligible_edges"):
        try:
            pd.testing.assert_frame_equal(tables[key], rebuilt[key], check_exact=True)
        except AssertionError as exc:
            raise ValueError("Frozen graph differs from independently rebuilt input eligibility: " + key) from exc
    mothers, h = _validate(tables["original_mothers"], tables["matching_frame"])
    edges = tables["eligible_edges"]
    if edges.duplicated(["event_id", "candidate_id"]).any() or not set(edges.event_id).issubset(mothers.event_id):
        raise ValueError("Invalid graph identities")
    components, allocations, certificates = _components(mothers.event_id.tolist(), edges), [], []
    for component_id, ids in enumerate(components):
        local = edges.loc[edges.event_id.isin(ids), ["event_id", "candidate_id"]]
        allocation, certificate = maximum_complete_matching(ids, local, count=3, time_limit=30.0)
        folds = mothers.loc[mothers.event_id.isin(ids), "fold"].unique()
        if len(folds) != 1:
            raise ValueError("A graph component crossed folds")
        certificate.update({"component_id": component_id, "fold": folds[0], "mother_ids": ids})
        certificates.append(certificate)
        allocation["component_id"], allocation["fold"] = component_id, folds[0]
        allocations.append(allocation)
    allocated = pd.concat(allocations, ignore_index=True) if allocations else pd.DataFrame(columns=["event_id", "candidate_id", "component_id", "fold"])
    if allocated.candidate_id.duplicated().any() or not allocated.groupby("event_id").size().eq(3).all():
        raise AssertionError("Global no-reuse/complete-group certificate failed")
    tables["allocation"] = allocated
    assignments = tables["mother_support"].copy()
    assignments["assigned_controls"] = assignments.event_id.map(allocated.groupby("event_id").size()).fillna(0).astype(int)
    assignments["match_status"] = assignments.support_reason
    good = assignments.support_reason.eq("eligible")
    assignments.loc[good & assignments.available_controls.lt(3), "match_status"] = "insufficient_background_controls"
    assignments.loc[good & assignments.available_controls.ge(3), "match_status"] = "shared_capacity_unmatched"
    assignments.loc[assignments.assigned_controls.eq(3), "match_status"] = "matched"
    tables["assignments"] = assignments
    lookup, ml = h.set_index("decision_time", drop=False), mothers.set_index("event_id")
    edge_lookup = edges.set_index(["event_id", "candidate_id"])
    controls = []
    for event, selected in allocated.groupby("event_id", sort=True):
        mother = ml.loc[event]
        direction = int(mother.direction)
        for ordinal, allocation in enumerate(selected.sort_values("candidate_id").to_dict("records")):
            candidate_id = allocation["candidate_id"]
            candidate = lookup.loc[pd.Timestamp(candidate_id)]
            edge = edge_lookup.loc[(event, candidate_id)]
            row = {"event_id": event + "::background_control" + str(ordinal),
                   "parent_event_id": event, "matched_event_id": event,
                   "source_mother_decision_time": mother.decision_time, "direction": direction,
                   "signal_time": candidate.signal_time, "decision_time": candidate.decision_time,
                   "initial_stop": edge.synthetic_stop, "signal_atr": candidate.signal_atr,
                   "transferred_risk_atr": edge.mother_risk_atr, "entry_open": candidate.entry_open,
                   "fold": mother.fold, "candidate_id": candidate_id, "component_id": allocation["component_id"],
                   "ma_slope_atr": direction * candidate.get("ma_slope_atr", np.nan),
                   "signed_hourly_slope_sign": direction * candidate.unsigned_hourly_slope_sign,
                   "extension_atr": direction * (candidate.close - candidate.ma) / candidate.signal_atr,
                   "close_location": candidate.get("long_close_location" if direction == 1 else "short_close_location", np.nan)}
            row.update({"signal_" + field: candidate[field] for field in ("open", "high", "low", "close")})
            for field in CONTROL_FIELDS:
                if field not in row:
                    row[field] = candidate.get(field, np.nan)
            controls.append(row)
    tables["controls"] = pd.DataFrame(controls, columns=CONTROL_FIELDS)
    tables["component_capacity"] = pd.DataFrame([{key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in x.items()} for x in certificates])
    fold_rows = []
    for fold, _, _ in FOLDS:
        group = assignments.loc[assignments.fold.eq(fold)]
        matched = int(group.assigned_controls.eq(3).sum())
        fold_rows.append({"fold": fold, "mothers": len(group), "matched_mothers": matched,
                          "controls": 3 * matched, "coverage": matched / len(group) if len(group) else np.nan})
    tables["fold_coverage"] = pd.DataFrame(fold_rows)
    matched = int(assignments.assigned_controls.eq(3).sum())
    gate = len(mothers) == 251 and matched >= 226
    summary = {"experiment_id": EXPERIMENT_ID, "status": "background_support_passed" if gate else "background_support_insufficient",
               "mothers": len(mothers), "maximum_matched": matched, "controls": len(controls),
               "unmatched_mothers": len(mothers) - matched, "coverage": matched / len(mothers) if len(mothers) else None,
               "required_complete_mothers": 226, "coverage_gate_passed": gate,
               "count_per_mother": 3, "matching_keys": KEYS, "matching_edges": len(edges),
               "unique_eligible_control_times": int(edges.candidate_id.nunique()),
               "components": certificates, "folds": fold_rows,
               "status_counts": assignments.match_status.value_counts().sort_index().to_dict(),
               "optimal": True, "solution_verified": True, "fallback_used": False,
               "graph_rebuilt_before_allocation": True,
               "control_time_reuse_allowed": False, "mother_rows_removed": 0,
               "outcomes_read_or_computed": False, "raw_price_io": False,
               "profitability_test": False, "holdout_consumed": False,
               "training_eligible": False, "production_eligible": False,
               "label_study_authorized_by_this_result": False,
               "allocation_role": "capacity_witness_not_random_sample", "seed_used": False,
               "limitation": "New offline whole-K1 background estimand; not a V10 repair, randomized treatment, causal shape effect or profitability evidence."}
    return tables, summary


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_bytes(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", commit + ":" + path], cwd=root)


def committed_sources(root: Path) -> Tuple[str, list]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sources = []
    for relative in SOURCES:
        expected = hashlib.sha256(_git_bytes(root, commit, relative)).hexdigest()
        if _sha(root / relative) != expected:
            raise ValueError("Source not committed at HEAD: " + relative)
        sources.append({"path": relative, "sha256": expected})
    return commit, sources


def verify_saved_inputs(root: Path) -> Dict[str, Any]:
    """Verify saved-only byte/source/cutoff lineage before parsing any CSV."""
    for relative, expected in INPUTS.items():
        if _sha(root / relative) != expected:
            raise ValueError("Pinned input hash mismatch: " + relative)
    started, frozen, summary = [json.loads((root / V10 / name).read_text()) for name in ("started.json", "support_frozen.json", "summary.json")]
    if not _utc_stamp(started["at"]) <= _utc_stamp(frozen["generated_at"]) <= _utc_stamp(summary["generated_at"]):
        raise ValueError("V10 checkpoint chronology mismatch")
    if not (frozen["historical_full_parity"] and frozen["original_assignment_feasible"] and frozen["mothers"] == summary["mothers"] == 251):
        raise ValueError("V10 population provenance mismatch")
    if summary["outcomes_read_or_computed"] or summary["holdout_consumed"] or frozen["capacity_attempted"]:
        raise ValueError("V10 is not a pre-capacity outcome-free checkpoint")
    receipt = frozen["source_receipt"]
    if receipt != summary["source_receipt"] or receipt["holdout_price_rows"] != 0 or not receipt["timestamp_preflight_before_price_hash"]:
        raise ValueError("V10 source cutoff receipt mismatch")
    if _utc_stamp(receipt["phase_price_last_open"]) >= pd.Timestamp("2025-01-01", tz="UTC"):
        raise ValueError("V10 materialized post-2024 prices")
    for name, relative in [("matching_frame.csv.gz", V10 / "matching_frame.csv.gz"), ("original_mothers.csv.gz", MOTHERS)]:
        if frozen["output_hashes"][name] != INPUTS[str(relative)] or summary["output_hashes"][name] != INPUTS[str(relative)]:
            raise ValueError("V10 saved input lineage mismatch")
    if started["inputs"]["original_mothers.csv.gz"] != INPUTS[str(MOTHERS)] or started["sources"] != frozen["source_receipts"] or started["sources"] != summary["source_receipts"]:
        raise ValueError("V10 source identities differ between checkpoints")
    for source in started["sources"]:
        if hashlib.sha256(_git_bytes(root, started["builder_commit"], source["path"])).hexdigest() != source["sha256"]:
            raise ValueError("V10 builder commit source mismatch")
    commit_at = subprocess.check_output(["git", "show", "-s", "--format=%cI", started["builder_commit"]], cwd=root, text=True).strip()
    if _utc_stamp(commit_at) > _utc_stamp(started["at"]):
        raise ValueError("V10 builder postdates its run")
    return {"inputs": dict(INPUTS), "upstream_builder_commit": started["builder_commit"],
            "upstream_source_receipt": receipt, "upstream_sources_verified_at_commit": len(started["sources"]),
            "raw_aggregation_independently_recomputed": False, "saved_only": True}


def _utc_stamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("Receipt clocks must be explicitly zoned and known")
    return stamp.tz_convert("UTC")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_json_safe(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _write_tables(directory: Path, tables: Dict[str, pd.DataFrame]) -> Dict[str, str]:
    hashes = {}
    for name, table in tables.items():
        path = directory / (name + ".csv.gz")
        if path.exists():
            raise FileExistsError(str(path))
        table.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        hashes[path.name] = _sha(path)
    return hashes


def run(root: Path = ROOT) -> Dict[str, Any]:
    """Saved-only CLI; refuse overwrite, freeze graph before any capacity solve."""
    directory = root / EXPERIMENT / "results"
    if directory.exists():
        raise FileExistsError("Existing results must not be overwritten: " + str(directory))
    config = json.loads((root / EXPERIMENT / "config.json").read_text())
    if config != frozen_config():
        raise ValueError("Frozen V23 configuration mismatch")
    commit, sources = committed_sources(root)
    directory.mkdir(parents=True, exist_ok=False)
    _write_json(directory / "started.json", {"at": str(pd.Timestamp.now(tz="UTC")), "builder_commit": commit,
                                             "sources": sources, "inputs": INPUTS,
                                             "config_sha256": _sha(root / EXPERIMENT / "config.json")})
    try:
        receipt = verify_saved_inputs(root)
        mothers = pd.read_csv(root / MOTHERS)
        matching = pd.read_csv(root / V10 / "matching_frame.csv.gz")
        if len(mothers) != 251 or mothers.groupby("fold").size().to_dict() != dict(zip([x[0] for x in FOLDS], [55, 66, 55, 75])):
            raise ValueError("Frozen original population changed")
        graph = build_support_graph(mothers, matching)
        hashes = _write_tables(directory, graph)
        _write_json(directory / "support_frozen.json", {"generated_at": str(pd.Timestamp.now(tz="UTC")),
                    "mothers": len(mothers), "matching_edges": len(graph["eligible_edges"]),
                    "capacity_attempted": False, "outcomes_read_or_computed": False,
                    "source_receipt": receipt, "output_hashes": hashes, "sources": sources})
        tables, summary = allocate_support(graph)
        hashes.update(_write_tables(directory, {key: value for key, value in tables.items() if key not in graph}))
        # Recheck frozen graph bytes after capacity; do not silently rewrite them.
        for name, expected in hashes.items():
            if _sha(directory / name) != expected:
                raise ValueError("Saved support output changed: " + name)
        summary.update({"generated_at": str(pd.Timestamp.now(tz="UTC")), "output_hashes": hashes,
                        "source_receipt": receipt, "sources": sources,
                        "builder_commit": commit, "config_sha256": _sha(root / EXPERIMENT / "config.json")})
        _write_json(directory / "summary.json", summary)
        return summary
    except Exception as exc:
        _write_json(directory / "failure.json", {"status": "failed_not_support_evidence", "error_type": type(exc).__name__,
                    "message": str(exc), "diagnostics": getattr(exc, "diagnostics", None),
                    "support_frozen": (directory / "support_frozen.json").exists(), "generated_at": str(pd.Timestamp.now(tz="UTC"))})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(_json_safe(run()), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
