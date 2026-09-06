"""Outcome-free supply and greedy-consumption audit of exact K1 controls.

Source contract: ``hourly_impulse_k2_matching.build_matching_frame`` and
``assign_controls``. One call covers one fold, with the original seed/order and
all-or-none control count. No file/network access, outcomes, returns, K2 waits,
or raw-price loading. This diagnostic does not change matching or execute trades.

Known columns/windows: each candidate's decision is its completed hourly open
+1h; ATR fraction uses that hour and shifted previous720, min168, same-segment
terciles. Entry open is known exactly at decision. 5m colour must have completed
exactly at decision; hourly colour/slope use that completed hour. Cross exclusion
uses the current hour or previous1h only, never a future cross. Every supplied
actual mother time is excluded, and candidate decision < fold end -72h (or the
explicit caller embargo). Risk transfer uses the real mother's known open,
K1 stop and ATR; each candidate's own open/ATR determines positive-stop validity.

Supply can include later same-month candidate decisions, as in the frozen
retrospective matching design; each candidate's FEATURES remain own-time causal.
It is not a live online control sampler or a claim of independent treatment
randomization. No new feature windows or relaxed keys are introduced.

Pandas2.3 joins match null keys to null keys, so stratum comparisons explicitly
require both keys nonnull. Only unique, nonnull mother IDs are merged with
``validate='one_to_one'``; grouping missing supply keys is descriptive only.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge.html
"""
from __future__ import annotations

import hashlib
from numbers import Number

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_k2_matching import assign_controls


MOTHER_COLUMNS = ["event_id", "decision_time", "direction", "initial_stop", "signal_atr", "fold"]
KEY_COLUMNS = ["month", "utc_6h_bucket", "vol_bucket", "known_5m_colour",
               "known_hourly_colour", "unsigned_hourly_slope_sign"]
SUPPORT_FLAGS = ["known_entry_open", "entry_source_continuous", "known_5m_valid", "known_hourly_valid"]
STAGES = ["same_month", "same_utc6h", "same_vol_bucket", "same_5m_colour", "same_hourly_colour", "same_slope",
          "fold_embargo", "vol_support", "atr_support", "entry_open_support", "entry_continuity_support",
          "five_minute_support", "hourly_support", "cross_exclusion", "actual_mother_exclusion",
          "positive_synthetic_stop", "unused_before"]
EDGE_COLUMNS = ["event_id", "candidate_id", "candidate_time", "fold", "synthetic_stop", "mother_risk_atr"]
GREEDY_EDGE_COLUMNS = EDGE_COLUMNS + ["used_before", "available_before", "preallocation_hash_rank", "hash_rank", "selected"]


def _time_series(values, *, errors):
    if len(values) and values.map(lambda value: isinstance(value, Number)).any():
        raise ValueError("Explicit UTC datetimes required; numeric epoch units are not guessed")
    return pd.to_datetime(values, utc=True, errors=errors, format="mixed")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _frame_stages(mothers, matching_frame, latest):
    required = set(KEY_COLUMNS + SUPPORT_FLAGS + ["decision_time", "signal_time", "signal_atr", "entry_open",
        "matching_support", "candidate_eligible", "current_or_prior_cross_excluded", "actual_mother_decision_excluded",
        "raw_strict_body_cross", "open", "high", "low", "close", "ma", "ma_slope_atr"])
    if not required.issubset(matching_frame) or matching_frame.columns.duplicated().any():
        raise ValueError("A complete build_matching_frame contract with unique columns is required")
    h = matching_frame.copy()
    h.attrs = {}
    h["decision_time"] = _time_series(h["decision_time"], errors="raise")
    if h.decision_time.isna().any() or h.decision_time.duplicated().any():
        raise ValueError("Matching candidate times must be finite and unique")
    if not h.decision_time.eq(h.decision_time.dt.floor("h")).all():
        raise ValueError("Matching candidates must use completed UTC hour decisions")
    flags = SUPPORT_FLAGS + ["matching_support", "candidate_eligible", "current_or_prior_cross_excluded",
                             "actual_mother_decision_excluded", "raw_strict_body_cross"]
    if any(h[column].isna().any() or not h[column].isin([True, False]).all() for column in flags):
        raise ValueError("Matching support/exclusion flags must be explicit booleans")
    numeric = lambda key: pd.to_numeric(h[key], errors="coerce").where(np.isfinite(pd.to_numeric(h[key], errors="coerce")))
    cross = ((numeric("open") < numeric("ma")) & (numeric("close") > numeric("ma"))) | ((numeric("open") > numeric("ma")) & (numeric("close") < numeric("ma")))
    cross_times = set(h.loc[cross, "decision_time"])
    cross_excluded = h.decision_time.isin(cross_times | {time+pd.Timedelta(hours=1) for time in cross_times})
    support = h.vol_bucket.notna() & numeric("signal_atr").gt(0)
    for flag in SUPPORT_FLAGS:
        support &= h[flag]
    candidate = support & ~cross_excluded & ~h.actual_mother_decision_excluded
    for actual, expected, label in [(h.raw_strict_body_cross, cross, "raw strict body cross"),
                                   (h.current_or_prior_cross_excluded, cross_excluded, "current/previous cross"),
                                   (h.matching_support, support, "causal support"),
                                   (h.candidate_eligible, candidate, "candidate eligibility")]:
        if not actual.astype(bool).equals(expected.astype(bool)):
            raise ValueError("Inconsistent matching-frame "+label)
    columns = ["decision_time"] + KEY_COLUMNS + SUPPORT_FLAGS + ["signal_atr", "entry_open",
        "matching_support", "candidate_eligible", "raw_strict_body_cross", "current_or_prior_cross_excluded",
        "actual_mother_decision_excluded"]
    if "audit_fold" in h:
        columns.append("audit_fold")
    columns += [key for key in ("atr_tercile_low", "atr_tercile_high") if key in h]
    stages = h[columns].copy()
    stages["candidate_id"] = stages.decision_time.map(lambda t: t.isoformat())
    stages["candidate_time"] = stages.decision_time
    stages["key_complete"] = stages[KEY_COLUMNS].notna().all(axis=1)
    stages["vol_bucket_missing"] = stages.vol_bucket.isna()
    stages["atr_support"] = numeric("signal_atr").gt(0)
    for field in SUPPORT_FLAGS:
        stages["invalid_"+field] = ~stages[field]
    stages["invalid_atr"] = ~stages.atr_support
    stages["within_fold_embargo"] = h.decision_time.lt(latest)
    stages["supplied_mother_excluded"] = h.decision_time.isin(set(mothers.decision_time.dropna()))
    stages["actual_mother_excluded"] = stages.actual_mother_decision_excluded | stages.supplied_mother_excluded
    stages["pool_eligible"] = candidate & stages.within_fold_embargo & ~stages.supplied_mother_excluded
    return h, stages


def _mother_status(mother, own, latest):
    """Independently preserve the frozen allocator's exact rejection precedence."""
    if pd.isna(mother["decision_time"]) or own is None:
        return "missing_mother_hourly_decision", np.nan
    if mother["decision_time"] >= latest:
        return "outside_fold_embargo", np.nan
    direction, stop, atr = map(_number, (mother["direction"], mother["initial_stop"], mother["signal_atr"]))
    if direction not in (-1, 1) or not np.isfinite([stop, atr]).all() or min(stop, atr) <= 0:
        return "invalid_mother_risk", np.nan
    if not own.known_entry_open or not own.entry_source_continuous:
        return "missing_or_gapped_mother_open", np.nan
    risk = direction * (_number(own.entry_open)-stop) / atr
    if not np.isfinite(risk) or risk <= 0:
        return "invalid_mother_risk", np.nan
    if not np.isclose(atr, own.signal_atr, rtol=1e-9, atol=1e-12):
        return "mother_atr_mismatch", risk
    if not own.matching_support:
        return "missing_causal_matching_support", risk
    return "ready", risk


def build_support_audit(mothers: pd.DataFrame, matching_frame: pd.DataFrame, *,
                        end_exclusive, count=3, seed=20260906, embargo_hours=72) -> dict:
    """Return original greedy outputs plus complete preallocation support graph.

    Keys: greedy_controls, greedy_assignments, greedy_diagnostics are unmodified
    assign_controls outputs. mother_audit preserves every input mother in caller
    order (only causal MOTHER_COLUMNS, not optional outcomes), with original
    match_status plus independent reconstructed_status, own stratum/support and
    risk, ordered stage counts and preallocation/used/available/selected counts.
    For mothers rejected before candidate search, availability stays unknown
    (NA), not a fabricated zero. Ready mothers retain exact0/1/2/... availability.

    eligible_edges: all valid (event_id,candidate_id) pairs BEFORE allocation;
    candidate_id is UTC ISO decision time. Positive synthetic stop is specific
    to the real mother's direction/risk, so equal strata need not imply equal
    graph neighbourhoods. greedy_edges includes the full same graph plus prior
    usage, SHA rank and selection; insufficient mothers consume ZERO controls.
    candidate_stages includes every supplied hourly state, explicitly marking
    the fold-embargo/exclusion filters. key_supply is an aggregate of those
    states (including labelled missing-key groups, never used for joining).
    stage_counts is long-form per-mother counts; receipt records consistency.

    No files are read/written. Additional caller outcome columns and attrs are
    ignored; future same-hour risk/exit information is never inspected. This is
    the original retrospective pool, NOT a restriction to candidate<mother.
    """
    if not set(MOTHER_COLUMNS).issubset(mothers) or mothers.columns.duplicated().any():
        raise ValueError("Unique mother columns and full causal identity/risk fields required")
    m = mothers[MOTHER_COLUMNS].copy()
    m.attrs = {}
    if m.event_id.isna().any() or m.event_id.duplicated().any() or not m.event_id.map(lambda v: isinstance(v, str) and bool(v)).all():
        raise ValueError("Mother IDs must be nonnull unique strings")
    m["decision_time"] = _time_series(m.decision_time, errors="coerce")
    if isinstance(end_exclusive, Number):
        raise ValueError("Fold end must be an explicit timestamp")
    end = pd.to_datetime(end_exclusive, utc=True)
    if pd.isna(end) or not np.isfinite(embargo_hours) or embargo_hours < 0:
        raise ValueError("Finite fold end and nonnegative embargo required")
    latest = end-pd.Timedelta(hours=embargo_hours)
    h, candidates = _frame_stages(m, matching_frame, latest)
    controls, assignments, diagnostics = assign_controls(m, h, count=count, seed=seed,
        end_exclusive=end, embargo_hours=embargo_hours)
    lookup = h.set_index("decision_time")
    audit_rows, stage_rows, edge_rows, greedy_rows = [], [], [], []
    used = set()
    ordered = m.sort_values(["decision_time", "event_id"], kind="mergesort")
    original_assignments = assignments.set_index("event_id")
    for order, mother in enumerate(ordered.to_dict("records")):
        time = mother["decision_time"]
        own = lookup.loc[time] if pd.notna(time) and time in lookup.index else None
        status, risk = _mother_status(mother, own, latest)
        row = {**mother, "greedy_order": order, "mother_risk_atr": risk,
               "mother_hourly_found": own is not None, "mother_search_reached": status == "ready",
               "preallocation_available": pd.NA, "used_before_count": pd.NA,
               "available_before_greedy": pd.NA, "selected_count": 0,
               "global_used_before_count": len(used)}
        for key in KEY_COLUMNS + SUPPORT_FLAGS + ["matching_support", "entry_open"]:
            row[key if key in KEY_COLUMNS else "mother_"+key] = own[key] if own is not None else pd.NA
        row["mother_vol_bucket_missing"] = own is None or pd.isna(own.vol_bucket)
        row["mother_key_complete"] = own is not None and own[KEY_COLUMNS].notna().all()
        row["matching_signal_atr"] = own.signal_atr if own is not None else np.nan
        row["mother_atr_support"] = own is not None and _number(own.signal_atr) > 0
        for key in KEY_COLUMNS:
            row["missing_key_"+key] = own is None or pd.isna(own[key])
        for key in ("atr_tercile_low", "atr_tercile_high"):
            if key in h:
                row["mother_"+key] = own[key] if own is not None else np.nan
        row["signed_hourly_slope_sign"] = _number(mother["direction"])*_number(own.unsigned_hourly_slope_sign) if own is not None else np.nan
        mask = pd.Series(True, index=h.index)
        for key, stage in zip(KEY_COLUMNS, STAGES[:6]):
            mask &= False if own is None or pd.isna(own[key]) else (h[key].notna() & h[key].eq(own[key])).fillna(False)
            value = int(mask.sum())
            row[stage+"_count"] = value
            stage_rows.append({"event_id": mother["event_id"], "stage": stage, "count": value})
        stage_masks = [candidates.within_fold_embargo, h.vol_bucket.notna(), candidates.atr_support,
            h.known_entry_open, h.entry_source_continuous, h.known_5m_valid, h.known_hourly_valid,
            ~h.current_or_prior_cross_excluded, ~candidates.actual_mother_excluded]
        for stage, step in zip(STAGES[6:15], stage_masks):
            mask &= step
            value = int(mask.sum())
            row[stage+"_count"] = value
            stage_rows.append({"event_id": mother["event_id"], "stage": stage, "count": value})
        if status == "ready":
            pool = h.loc[mask].copy()
            stop = pool.entry_open-_number(mother["direction"])*risk*pool.signal_atr
            pool = pool.loc[np.isfinite(stop) & stop.gt(0)].copy()
            pool["synthetic_stop"] = stop.loc[pool.index]
            records = pool.to_dict("records")
            records.sort(key=lambda c: hashlib.sha256(f"{seed}|{mother['event_id']}|{c['decision_time'].isoformat()}".encode()).hexdigest())
            available = [c for c in records if c["decision_time"] not in used]
            selected = available[:count] if len(available) >= count else []
            chosen_times = {c["decision_time"] for c in selected}
            row.update(preallocation_available=len(records), used_before_count=len(records)-len(available),
                       available_before_greedy=len(available), selected_count=len(selected))
            available_rank = {c["decision_time"]: i+1 for i, c in enumerate(available)}
            for i, candidate in enumerate(records):
                ct = candidate["decision_time"]
                edge = {"event_id": mother["event_id"], "candidate_id": ct.isoformat(), "candidate_time": ct,
                        "fold": mother["fold"], "synthetic_stop": candidate["synthetic_stop"], "mother_risk_atr": risk}
                edge_rows.append(edge)
                greedy_rows.append({**edge, "used_before": ct in used, "available_before": ct not in used,
                    "preallocation_hash_rank": i+1, "hash_rank": available_rank.get(ct, pd.NA), "selected": ct in chosen_times})
            status = "matched" if selected else "insufficient_exact_controls"
            saved = original_assignments.loc[mother["event_id"]]
            if saved.available_controls != len(available):
                raise AssertionError("Independent supply disagrees with original available_controls")
            actual_times = set(controls.loc[controls.parent_event_id.eq(mother["event_id"]), "decision_time"])
            if chosen_times != actual_times:
                raise AssertionError("Independent seeded selection disagrees with original controls")
            used.update(chosen_times)
        row["reconstructed_status"] = status
        saved = original_assignments.loc[mother["event_id"]]
        if status != saved.match_status:
            raise AssertionError("Independent mother rejection status disagrees with original allocator")
        if row["selected_count"] != saved.assigned_controls:
            raise AssertionError("Independent selected count disagrees with original allocator")
        if "mother_risk_atr" in saved and pd.notna(saved.mother_risk_atr):
            if not np.isclose(risk, saved.mother_risk_atr, rtol=1e-12, atol=1e-12):
                raise AssertionError("Independent mother risk disagrees with original allocator")
        for stage, field in [("positive_synthetic_stop", "preallocation_available"), ("unused_before", "available_before_greedy")]:
            stage_rows.append({"event_id": mother["event_id"], "stage": stage, "count": row[field]})
            row[stage+"_count"] = row[field]
        audit_rows.append(row)
    audit = pd.DataFrame(audit_rows)
    if len(audit):
        audit = m[["event_id"]].merge(audit, on="event_id", how="left", sort=False, validate="one_to_one")
        audit = audit.merge(assignments[["event_id", "match_status", "assigned_controls"]], on="event_id", how="left", sort=False, validate="one_to_one")
        assert audit.event_id.tolist() == m.event_id.tolist()
    else:
        audit = pd.DataFrame(columns=MOTHER_COLUMNS+["match_status", "assigned_controls", "reconstructed_status",
            "preallocation_available", "used_before_count", "available_before_greedy", "selected_count"])
    edges = pd.DataFrame(edge_rows, columns=EDGE_COLUMNS)
    greedy = pd.DataFrame(greedy_rows, columns=GREEDY_EDGE_COLUMNS)
    if edges.duplicated(["event_id", "candidate_id"]).any() or len(used) != len(controls):
        raise AssertionError("Duplicated graph edge or reused control time")
    group_keys = (["audit_fold"] if "audit_fold" in candidates else []) + KEY_COLUMNS
    supply = candidates.groupby(group_keys, dropna=False).agg(raw_candidates=("candidate_id", "size"),
        in_embargo_window=("within_fold_embargo", "sum"), causal_support=("matching_support", "sum"),
        original_pool=("pool_eligible", "sum"), vol_bucket_missing_count=("vol_bucket_missing", "sum"),
        invalid_atr_count=("invalid_atr", "sum"), invalid_entry_open_count=("invalid_known_entry_open", "sum"),
        gapped_entry_count=("invalid_entry_source_continuous", "sum"),
        invalid_5m_count=("invalid_known_5m_valid", "sum"), invalid_hourly_count=("invalid_known_hourly_valid", "sum"),
        cross_excluded_count=("current_or_prior_cross_excluded", "sum"),
        actual_mother_excluded_count=("actual_mother_excluded", "sum")).reset_index()
    supply["key_complete"] = supply[KEY_COLUMNS].notna().all(axis=1)
    receipt = {"mother_count": len(m), "candidate_rows": len(h), "eligible_edges": len(edges),
        "selected_edges": len(controls), "all_mothers_retained": len(audit) == len(m),
        "original_assignment_parity": True, "candidate_pool_count": int(candidates.pool_eligible.sum()),
        "outcomes_used": False, "future_cross_exclusion_used": False, "fallback_used": False,
        "seed": seed, "count": count, "end_exclusive": end.isoformat(), "embargo_hours": embargo_hours,
        "availability_missing_means": "mother rejected before exact candidate search; not zero supply",
        "graph_semantics": "all eligible mother-candidate edges before greedy usage; per-mother transferred risk",
        "support_failure_counts_overlap": True,
        "assignment_hash": diagnostics["assignment_hash"]}
    if receipt["candidate_pool_count"] != diagnostics["candidate_count_before_exact_keys"]:
        raise AssertionError("Independent global pool disagrees with original allocator")
    return {"greedy_controls": controls, "greedy_assignments": assignments, "greedy_diagnostics": diagnostics,
            "mother_audit": audit, "eligible_edges": edges, "greedy_edges": greedy,
            "candidate_stages": candidates, "key_supply": supply,
            "stage_counts": pd.DataFrame(stage_rows, columns=["event_id", "stage", "count"]), "receipt": receipt}
