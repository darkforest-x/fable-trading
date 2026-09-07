"""Pure, preregistered V28 fixed-clock descriptive and monthly-cluster analysis.

Uses only supplied case/paired label identity, clocks, known flags and markout
columns. Every original mother and H=(1,4,12,24) must be present. No raw data,
prices, I/O, old strategy outcomes, outlier deletion or parameter selection is
used. Only 4h is primary; four observations per mother are not independent.
The 20bp-subtracted label is a cost-threshold markout, never executable PnL.

The fixed 24 calendar-month clusters preserve within-month overlap. Inference
assumes sufficiently weak between-month dependence; sign flipping additionally
assumes exchangeable signs of monthly sums under its zero-mean null. This is
not an exact randomized-treatment test: the observed K1 events were not randomly
assigned. Reused 2023--2024 development data remain exploratory.

NumPy 2.0 contracts, verified against the repository's NumPy 2.0.2:
https://numpy.org/doc/2.0/reference/random/bit_generators/pcg64.html
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.integers.html
https://numpy.org/doc/2.0/reference/generated/numpy.quantile.html
One independent PCG64(20260907) stream draws the (9999,24) bootstrap indices,
then the (9999,24) signs; these same arrays are shared by every series/horizon.
Percentile intervals use explicit linear interpolation. All values are fractions.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import hashlib
import math
from numbers import Number, Real

import numpy as np
import pandas as pd


HORIZONS_HOURS = (1, 4, 12, 24)
PRIMARY_HORIZON_HOURS = 4
SEED = 20260907
DRAWS = 9999
EXPECTED_MOTHERS = 288
EXPECTED_SUPPORTED = 284
MINIMUM_KNOWN = 260
MONTHS = tuple("%04d-%02d" % (year, month) for year in (2023, 2024) for month in range(1, 13))
FOLD_BOUNDS = {
    "2023H1": ("2023-01-01T00:00:00Z", "2023-07-01T00:00:00Z"),
    "2023H2": ("2023-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
    "2024H1": ("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
    "2024H2": ("2024-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
}
_FOLD_COUNTS = {"2023H1": 68, "2023H2": 74, "2024H1": 66, "2024H2": 80}
_IDENTITY = ("event_id", "mother_id", "fold", "direction", "decision_time", "mother_month", "horizon_hours", "role")
_CASE_COLUMNS = _IDENTITY + ("request_kind", "status", "reason", "gross_markout", "cost_threshold_markout", "matched_support")
_PAIR_COLUMNS = _IDENTITY + (
    "case_status", "case_reason", "case_known", "matched_support", "n_controls_expected",
    "n_controls_assigned", "n_controls_known", "pair_complete", "pair_reason",
    "case_gross_markout", "case_cost_threshold_markout", "control_mean_gross_markout",
    "control_mean_cost_threshold_markout", "gross_excess_markout", "cost_threshold_excess_markout",
)


def _time(value):
    if isinstance(value, (Number, np.bool_)) or not isinstance(value, (str, datetime, pd.Timestamp)):
        raise ValueError("clocks require explicit UTC timestamps")
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("clocks require explicit UTC timestamps")
    stamp = stamp.tz_convert("UTC").as_unit("ns")
    if stamp.value % (5 * 60 * 10**9):
        raise ValueError("clock is not aligned to five minutes")
    return stamp


def _boolean(value, name):
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError("%s must be boolean" % name)
    return bool(value)


def _integer(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(value) or value != int(value):
        raise ValueError("%s must be a finite integer" % name)
    return int(value)


def _value(value, known, name):
    if not known:
        if not pd.isna(value):
            raise ValueError("unknown %s must remain missing, never zero-filled" % name)
        return np.nan
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError("known %s must be finite numeric" % name)
    return float(value)


def _equal(left, right, name):
    if not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("inconsistent %s" % name)


def _prepare(frame, columns, bounds):
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any() or not set(columns) <= set(frame):
        raise ValueError("missing or duplicate label columns")
    rows = frame.loc[:, list(columns)].copy(deep=True).to_dict("records")
    seen, identities = set(), {}
    for row in rows:
        event = row["event_id"]
        if not isinstance(event, str) or not event.strip() or row["mother_id"] != event:
            raise ValueError("case/paired mother identity must equal event_id")
        hours = _integer(row["horizon_hours"], "horizon_hours")
        if hours not in HORIZONS_HOURS or row["role"] != ("primary" if hours == 4 else "descriptive"):
            raise ValueError("only 4h may be primary; all four fixed horizons are required")
        if (event, hours) in seen:
            raise ValueError("duplicate event/horizon")
        seen.add((event, hours))
        direction = _integer(row["direction"], "direction")
        if direction not in (-1, 1):
            raise ValueError("direction must be +1/-1")
        stamp = _time(row["decision_time"])
        fold, month = row["fold"], row["mother_month"]
        if fold not in bounds or not bounds[fold][0] <= stamp < bounds[fold][1]:
            raise ValueError("decision time is outside its named fold")
        if month != stamp.strftime("%Y-%m") or month not in MONTHS:
            raise ValueError("mother month is inconsistent with its clock/fold")
        matched = _boolean(row["matched_support"], "matched_support")
        identity = (fold, direction, stamp, month, matched)
        if event in identities and identities[event] != identity:
            raise ValueError("mother identity/support changes across horizons")
        identities[event] = identity
        row.update(decision_time=stamp, direction=direction, horizon_hours=hours, matched_support=matched)
    if len(identities) != EXPECTED_MOTHERS or len(rows) != EXPECTED_MOTHERS * 4:
        raise ValueError("all original 288 mothers and four horizons must be retained")
    if set(seen) != {(event, hours) for event in identities for hours in HORIZONS_HOURS}:
        raise ValueError("incomplete mother/horizon grid")
    if sum(item[4] for item in identities.values()) != EXPECTED_SUPPORTED:
        raise ValueError("the frozen support denominator is 284, not an outcome-selected subset")
    counts = {fold: sum(item[0] == fold for item in identities.values()) for fold in bounds}
    if counts != _FOLD_COUNTS:
        raise ValueError("original fold mother counts changed")
    return {(row["event_id"], row["horizon_hours"]): row for row in rows}, identities


def _validate(case_labels, paired_labels, fold_bounds):
    if not isinstance(fold_bounds, Mapping) or set(fold_bounds) != set(FOLD_BOUNDS):
        raise ValueError("the four original 2023/2024 folds are required")
    bounds = {}
    for fold, expected in FOLD_BOUNDS.items():
        values = fold_bounds[fold]
        if not isinstance(values, (tuple, list)) or len(values) != 2:
            raise ValueError("fold bounds must be explicit start/end pairs")
        bounds[fold] = tuple(_time(value) for value in values)
        if bounds[fold] != tuple(_time(value) for value in expected):
            raise ValueError("frozen fold boundaries cannot change")
    cases, case_ids = _prepare(case_labels, _CASE_COLUMNS, bounds)
    pairs, pair_ids = _prepare(paired_labels, _PAIR_COLUMNS, bounds)
    if case_ids != pair_ids:
        raise ValueError("case and paired mother identities disagree")
    for key in sorted(cases):
        case, pair = cases[key], pairs[key]
        if case["request_kind"] != "case" or case["status"] not in ("known", "unknown"):
            raise ValueError("invalid case kind/status")
        known = case["status"] == "known"
        if not isinstance(case["reason"], str) or not case["reason"] or (case["reason"] == "known") != known:
            raise ValueError("case status/reason disagree")
        gross = _value(case["gross_markout"], known, "case gross")
        net = _value(case["cost_threshold_markout"], known, "case cost threshold")
        case.update(gross_markout=gross, cost_threshold_markout=net)
        if known:
            _equal(net, gross - .002, "case 20bp threshold")
        if pair["case_status"] != case["status"] or pair["case_reason"] != case["reason"] or _boolean(pair["case_known"], "case_known") != known:
            raise ValueError("paired case status disagrees with case label")
        for field, actual in (("case_gross_markout", gross), ("case_cost_threshold_markout", net)):
            candidate = _value(pair[field], known, field)
            if known:
                _equal(candidate, actual, field)
        expected = _integer(pair["n_controls_expected"], "n_controls_expected")
        assigned = _integer(pair["n_controls_assigned"], "n_controls_assigned")
        control_known = _integer(pair["n_controls_known"], "n_controls_known")
        if expected != 3 or assigned != (3 if pair["matched_support"] else 0) or not 0 <= control_known <= assigned:
            raise ValueError("invalid three-control count contract")
        complete = _boolean(pair["pair_complete"], "pair_complete")
        if complete != (known and control_known == 3):
            raise ValueError("pair completeness disagrees with case/control availability")
        expected_reason = ("known" if complete else "unmatched_support" if not assigned
                           else "unknown_case_label" if not known else "unknown_control_label")
        if pair["pair_reason"] != expected_reason:
            raise ValueError("pair reason disagrees with support/case/control availability")
        cg = _value(pair["control_mean_gross_markout"], control_known == 3, "control mean gross")
        cn = _value(pair["control_mean_cost_threshold_markout"], control_known == 3, "control mean cost threshold")
        eg = _value(pair["gross_excess_markout"], complete, "gross excess")
        en = _value(pair["cost_threshold_excess_markout"], complete, "cost-threshold excess")
        pair.update(gross_excess_markout=eg, cost_threshold_excess_markout=en)
        if control_known == 3:
            _equal(cn, cg - .002, "control 20bp threshold")
        if complete:
            _equal(eg, gross - cg, "gross excess")
            _equal(en, net - cn, "cost-threshold excess")
            _equal(en, eg, "equal cost cancellation")
    return cases, pairs


def _describe(values):
    values = np.asarray(values, dtype=float)
    finite = values[np.isfinite(values)]
    result = dict(n_total=len(values), n_known=len(finite), n_unknown=len(values)-len(finite),
                  mean=None, sd=None, median=None, q25=None, q75=None, iqr=None, minimum=None, maximum=None)
    if len(finite):
        q25, median, q75 = np.quantile(finite, [.25, .5, .75], method="linear")
        result.update(mean=float(np.mean(finite)), sd=float(np.std(finite, ddof=1)) if len(finite)>1 else None,
                      median=float(median), q25=float(q25), q75=float(q75), iqr=float(q75-q25),
                      minimum=float(np.min(finite)), maximum=float(np.max(finite)))
    return result


def _monthly(values, months, indices, signs, infer=True):
    sums = np.array([math.fsum(value for value, month in zip(values, months) if month == target and math.isfinite(value)) for target in MONTHS])
    counts = np.array([sum(month == target and math.isfinite(value) for value, month in zip(values, months)) for target in MONTHS], dtype=np.int64)
    rows = [{"month": month, "n_known": int(n), "sum": float(total), "mean": float(total/n) if n else None}
            for month, n, total in zip(MONTHS, counts, sums)]
    if not infer:
        return {"months": rows, "inference_status": "descriptive_only"}
    result = dict(months=rows, ci95=None, one_sided_p=None, inference_status="no_known_values",
                  draws=DRAWS, clusters=24, bootstrap_zero_count_draws=None)
    if not counts.sum():
        return result
    denominator = counts[indices].sum(axis=1)
    empty = int((denominator == 0).sum())
    result["bootstrap_zero_count_draws"] = empty
    # No redrawing, dropping or imputing empty bootstrap replicates.
    if empty:
        result["inference_status"] = "bootstrap_contains_zero_known_count"
        return result
    replicate_means = sums[indices].sum(axis=1) / denominator
    ci = np.quantile(replicate_means, [.025, .975], method="linear")
    # Identical reduction to the sign-flipped rows preserves exact all-positive
    # ties; mixing math.fsum here with np.sum below can change the tail count.
    observed = sums.sum()
    flipped = (signs*sums).sum(axis=1)
    pvalue = (1 + int(np.count_nonzero(flipped >= observed))) / (DRAWS + 1)
    result.update(ci95=[float(ci[0]), float(ci[1])], one_sided_p=float(pvalue), inference_status="computed_exploratory")
    return result


def analyze_fixed_clock_statistics(case_labels, paired_labels, fold_bounds):
    """Analyze the frozen 288-mother V28 tables without mutating either input.

    Descriptions retain unknown counts for each horizon and fold. Only 4h exposes
    inferential statistics. The continuation flag requires both 4h series to have
    >=260 known mothers, mean>0, CI lower>0, p<.01 and positive case cost-threshold
    means in all four folds. It permits further entry research only, never live
    trading, profitability, or independent validation claims. Unknowns are not
    imputed and no winner/outlier filtering occurs. Caller must pin the source
    label tables separately: this function does not reconstruct prices or verify
    each of the underlying three individual control labels.
    """
    cases, pairs = _validate(case_labels, paired_labels, fold_bounds)
    rng = np.random.Generator(np.random.PCG64(SEED))
    indices = rng.integers(0, 24, size=(DRAWS, 24), dtype=np.int64)
    signs = 2*rng.integers(0, 2, size=(DRAWS, 24), dtype=np.int64)-1
    output = {
        "contract": {"label_only": True, "executable_pnl": False, "exploratory": True,
                     "independent_validation": False, "all_mothers": EXPECTED_MOTHERS,
                     "supported_mothers": EXPECTED_SUPPORTED, "primary_horizon_hours": 4,
                     "horizons_hours": list(HORIZONS_HOURS), "minimum_primary_known": MINIMUM_KNOWN,
                     "seed": SEED, "draws": DRAWS, "clusters": list(MONTHS),
                     "interval": "95% percentile, linear interpolation", "tail": "greater, monthly sums, +1 correction",
                     "weighting": "event-weighted sum/n after whole-month resampling",
                     "outliers": "all retained", "unknown": "no imputation; no control redraw",
                     "month_indices_sha256": hashlib.sha256(indices.astype("<i8").tobytes()).hexdigest(),
                     "month_signs_sha256": hashlib.sha256(signs.astype("<i8").tobytes()).hexdigest(),
                     "numpy_version": np.__version__, "pandas_version": pd.__version__},
        "horizons": {},
    }
    for hours in HORIZONS_HOURS:
        keys = sorted(key for key in cases if key[1] == hours)
        months = [cases[key]["mother_month"] for key in keys]
        folds = [cases[key]["fold"] for key in keys]
        case_values = [float(cases[key]["cost_threshold_markout"]) for key in keys]
        excess_values = [float(pairs[key]["cost_threshold_excess_markout"]) for key in keys]
        paired_case = [value if pairs[key]["pair_complete"] else np.nan for value, key in zip(case_values, keys)]
        entry = {"role": "primary" if hours == 4 else "descriptive", "all_case": _describe(case_values),
                 "paired_case": _describe(paired_case), "paired_excess": _describe(excess_values),
                 "folds": {}, "case_unknown_reasons": {}, "pair_unknown_reasons": {}}
        for key in keys:
            for name, known, reason in (("case_unknown_reasons", cases[key]["status"] == "known", cases[key]["reason"]),
                                        ("pair_unknown_reasons", pairs[key]["pair_complete"], pairs[key]["pair_reason"])):
                if not known:
                    entry[name][reason] = entry[name].get(reason, 0)+1
        for fold in FOLD_BOUNDS:
            entry["folds"][fold] = {
                "all_case": _describe([value for value, actual in zip(case_values, folds) if actual == fold]),
                "paired_excess": _describe([value for value, actual in zip(excess_values, folds) if actual == fold]),
            }
        # Every H uses the same fixed design arrays; descriptive H expose monthly
        # sums/counts only, with no p-value or interval that could become a winner.
        for name, values in (("all_case", case_values), ("paired_excess", excess_values)):
            statistics = _monthly(values, months, indices, signs, infer=hours == 4)
            entry[name]["monthly_cluster"] = statistics
        output["horizons"][str(hours)] = entry
    primary = output["horizons"]["4"]
    coverage = all(primary[name]["n_known"] >= MINIMUM_KNOWN for name in ("all_case", "paired_excess"))
    positive_folds = all(item["all_case"]["mean"] is not None and item["all_case"]["mean"] > 0 for item in primary["folds"].values())
    evidence = all(primary[name]["mean"] is not None and primary[name]["mean"] > 0
                   and primary[name]["monthly_cluster"]["ci95"] is not None
                   and primary[name]["monthly_cluster"]["ci95"][0] > 0
                   and primary[name]["monthly_cluster"]["one_sided_p"] < .01 for name in ("all_case", "paired_excess"))
    output["decision"] = {"primary_coverage_passed": coverage, "four_fold_case_means_positive": positive_folds,
                          "both_primary_evidence_gates_passed": evidence, "exploratory_continue": coverage and positive_folds and evidence,
                          "status": "inconclusive_coverage" if not coverage else ("exploratory_support" if positive_folds and evidence else "not_supported"),
                          "profitability_accepted": False, "production_eligible": False}
    def finite_tree(value):
        if isinstance(value, dict):
            return all(finite_tree(item) for item in value.values())
        if isinstance(value, list):
            return all(finite_tree(item) for item in value)
        return not isinstance(value, float) or math.isfinite(value)
    if not finite_tree(output):
        raise ValueError("nonfinite aggregate statistics; no silent overflow")
    return output
