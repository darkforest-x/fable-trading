"""Pure exploratory V32 economics of the frozen V31 prior-volume-wave gate.

Inputs are V24 fixed-clock FUTURE LABELS (identity, own decision/endpoint,
direction, H=1/4/12/24, known/reason, gross and gross-minus-0.002 markouts),
their original three-control aggregates, and V31 own-clock volume-wave context.
The labels use open[E] through open[E+H]; these are diagnostic outcomes, NEVER
entry features. Context signal_time+1h and wave_available_at must equal each
request's OWN E. Prior wave evidence uses exactly T-1h/T-2h, available at T
and T-1h. Frozen defaults20/5 and first-value EMA history are established by
V31 source provenance, not recomputed from prices here. This validator checks
finite numeric wave context, its raw ratio/current EMA5 identity, exact prior
clocks/counts, strict directional change and100-bar older-lag research warmup.
Unknown V31 contexts are specifically warmup, not unknown future outcomes. Original 251 mothers / 744 controls and every horizon
are retained. This module reads no prices/files and performs no optimization.

The accepted-mother comparison retains ALL original control triples, not only
controls accepted by their own wave gate. A separate policy comparison applies
the frozen gate to each control at its own E. A known abstention pays exactly
zero even when its unused future label is unknown; an unknown gate stays
unknown. An accepted order with an unknown future label also stays unknown.

Only 4h is primary. Four prespecified series share frozen V24 24-month cluster
bootstrap indices/signs, PCG64(20260907), 9999 draws, percentile-linear 95% CI
and one-sided plus-one sign-flip p. Holm adjusts exactly these four p-values,
NOT the earlier research family. Month resampling allows within-month overlap
but assumes weak between-month dependence; sign flips additionally require
exchangeable monthly-sum signs under the null, not randomized entry assignment.
All reused development data and inferred results remain exploratory. Markout
minus a cost threshold is not executable PnL; no economic/live acceptance.

Frozen numerical contracts and methodology are inherited unchanged from
hourly_impulse_fixed_clock_statistics.py (NumPy2.0.2 / pandas2.3.3). Holm:
https://stat.ethz.ch/R-manual/R-devel/library/stats/html/p.adjust.html
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from yoyo.evaluation import hourly_impulse_fixed_clock_statistics as base
from yoyo.evaluation.hourly_impulse_fixed_clock_research import paired_labels as rebuild_pairs
from yoyo.evaluation.hourly_impulse_structure_event_support import (
    _boolean, _frame, _integer, _time,
)


GATE = "wave_gate_state"
STATES = ("accepted", "abstain", "unknown")
PRIMARY_SERIES = ("accepted_cost", "accepted_excess", "policy_excess", "policy_delta")
WAVE_TIME_COLUMNS = (
    "wave_available_at", "wave_previous_open_time", "wave_previous_available_at",
    "wave_previous2_open_time", "wave_previous2_available_at",
)
WAVE_INT_COLUMNS = ("wave_segment", "wave_count", "wave_previous_count", "wave_previous2_count")
WAVE_NUMERIC_COLUMNS = (
    "wave_buy_volume", "wave_sell_volume", "wave_total_input", "wave_ema_buy",
    "wave_ema_sell", "wave_ema_total", "wave_raw", "wave_value",
    "wave_previous", "wave_previous2", "wave_prior_delta",
)
WAVE_COLUMNS = (*WAVE_TIME_COLUMNS, *WAVE_INT_COLUMNS, *WAVE_NUMERIC_COLUMNS,
                "wave_known", "wave_reason")
CONTEXT_COLUMNS = (
    "event_id", "signal_time", "decision_time", "direction", "fold", "request_kind",
    "mother_id", "control_slot", "mother_month", "matched_support", GATE, *WAVE_COLUMNS,
)
PAIR_CONTEXT_COLUMNS = ("signal_time", GATE, *WAVE_COLUMNS)
LABEL_COLUMNS = (*base._CASE_COLUMNS, "control_slot", "endpoint_time")
COUNTS = {"case": {"accepted": 100, "abstain": 148, "unknown": 3},
          "control": {"accepted": 401, "abstain": 343, "unknown": 0}}


def _contexts(frame, kind):
    """Validate frozen V31 contexts before calculating any returns.

    Both prior values remain finite during warmup; they are unavailable for
    admission because their older count is below100, not because the numeric
    wave has no value. No price/history replay or caller-selected threshold.
    """
    _frame(frame, CONTEXT_COLUMNS, kind + " context")
    rows = frame.loc[:, list(CONTEXT_COLUMNS)].copy(deep=True).to_dict("records")
    lookup = {}
    for row in rows:
        event = row["event_id"]
        if not isinstance(event, str) or not event.strip() or event in lookup:
            raise ValueError("Context identities must be unique nonempty strings")
        if row["request_kind"] != kind:
            raise ValueError("Context request kind changed")
        for col in ("signal_time", "decision_time", *WAVE_TIME_COLUMNS):
            row[col] = _time(row[col])
        signal, decision = row["signal_time"], row["decision_time"]
        hour = pd.Timedelta(hours=1)
        expected_clocks = {
            "wave_available_at": decision, "wave_previous_open_time": signal-hour,
            "wave_previous_available_at": signal, "wave_previous2_open_time": signal-2*hour,
            "wave_previous2_available_at": signal-hour,
        }
        if decision != signal+hour or any(row[col] != value for col, value in expected_clocks.items()):
            raise ValueError("Wave requires exact own pre-K1 clocks and K1 available at E")
        direction = _integer(row["direction"])
        if direction not in (-1, 1):
            raise ValueError("Own direction must be +/-1")
        fold = row["fold"]
        if fold not in base.FOLD_BOUNDS:
            raise ValueError("Unknown context fold")
        start, end = map(_time, base.FOLD_BOUNDS[fold])
        if not start <= decision < end or row["mother_month"] != decision.strftime("%Y-%m"):
            raise ValueError("Own context clock/fold/month disagree")
        matched, known = _boolean(row["matched_support"]), _boolean(row["wave_known"])
        for col in WAVE_INT_COLUMNS:
            row[col] = _integer(row[col])
        if (row["wave_segment"] < 1 or row["wave_count"] < 3
                or row["wave_previous_count"] != row["wave_count"]-1
                or row["wave_previous2_count"] != row["wave_count"]-2):
            raise ValueError("Wave current and prior contiguous counts disagree")
        for col in WAVE_NUMERIC_COLUMNS:
            row[col] = base._value(row[col], True, col)
        nonnegative = ("wave_buy_volume", "wave_sell_volume", "wave_ema_buy", "wave_ema_sell")
        if (any(row[col] < 0 for col in nonnegative)
                or row["wave_total_input"] <= 0 or row["wave_ema_total"] <= 0
                or row["wave_buy_volume"] > 0 and row["wave_sell_volume"] > 0):
            raise ValueError("Wave direction-volume proxy/denominator invalid")
        assigned = row["wave_buy_volume"]+row["wave_sell_volume"]
        if assigned > 0:
            base._equal(row["wave_total_input"], assigned, "wave non-doji source volume")
        if row["wave_ema_buy"]+row["wave_ema_sell"] > row["wave_ema_total"]+1e-12*max(1., row["wave_ema_total"]):
            raise ValueError("Wave directional EMA volume exceeds total")
        base._equal(row["wave_raw"], (row["wave_ema_buy"]-row["wave_ema_sell"])/row["wave_ema_total"]*100.,
                    "wave raw relative delta")
        alpha5 = 2./6.
        base._equal(row["wave_value"], alpha5*row["wave_raw"]+(1.-alpha5)*row["wave_previous"],
                    "wave current EMA5 diagnostic")
        if any(abs(row[col]) > 100.+1e-10 for col in ("wave_raw", "wave_value", "wave_previous", "wave_previous2")):
            raise ValueError("Wave ratio outside its bounded range")
        change = row["wave_previous"]-row["wave_previous2"]
        base._equal(row["wave_prior_delta"], change, "wave prior delta")
        if known != (row["wave_previous2_count"] >= 100):
            raise ValueError("Wave known flag disagrees with older-lag100 warmup")
        if known:
            signed = direction*change
            expected = "accepted" if signed > 0 else "abstain"
            reason = "prior_improving" if signed > 0 else "flat" if signed == 0 else "opposite"
        else:
            expected, reason = "unknown", "warmup"
        if row[GATE] != expected or row["wave_reason"] != reason:
            raise ValueError("Wave prior change/known/gate/reason disagree")
        if kind == "case":
            if row["mother_id"] != event or not pd.isna(row["control_slot"]):
                raise ValueError("Case mother/slot identity changed")
            slot = None
        else:
            slot = _integer(row["control_slot"])
            if slot not in (0, 1, 2) or not matched or row["mother_id"] == event:
                raise ValueError("Control slot/support identity changed")
        row.update(direction=direction, control_slot=slot, matched_support=matched, wave_known=known)
        lookup[event] = row
    actual = {state: sum(row[GATE] == state for row in rows) for state in STATES}
    if actual != COUNTS[kind] or len(rows) != sum(COUNTS[kind].values()):
        raise ValueError("Frozen %s wave population changed" % kind)
    return lookup


def _labels(frame, contexts, kind):
    """Validate full own-clock label grid, projecting out all unused columns."""
    _frame(frame, LABEL_COLUMNS, kind + " labels")
    result, seen = [], set()
    for row in frame.loc[:, list(LABEL_COLUMNS)].copy(deep=True).to_dict("records"):
        event = row["event_id"]
        if event not in contexts:
            raise ValueError("Label identity missing from frozen context")
        context = contexts[event]
        hours = _integer(row["horizon_hours"])
        if hours not in base.HORIZONS_HOURS or (event, hours) in seen:
            raise ValueError("Duplicate or unexpected label horizon")
        seen.add((event, hours))
        if row["role"] != ("primary" if hours == 4 else "descriptive"):
            raise ValueError("Only 4h can be primary")
        decision, endpoint = _time(row["decision_time"]), _time(row["endpoint_time"])
        if decision != context["decision_time"] or endpoint != decision + pd.Timedelta(hours=hours):
            raise ValueError("Label own decision/endpoint clock changed")
        for field in ("fold", "mother_id", "mother_month", "request_kind"):
            if row[field] != context[field]:
                raise ValueError("Label/context identity differs: " + field)
        if _integer(row["direction"]) != context["direction"]:
            raise ValueError("Label/context direction differs")
        if _boolean(row["matched_support"]) != context["matched_support"]:
            raise ValueError("Label/context matched support differs")
        if kind == "control":
            if _integer(row["control_slot"]) != context["control_slot"]:
                raise ValueError("Label/context control slot differs")
        elif not pd.isna(row["control_slot"]):
            raise ValueError("Case label cannot have a control slot")
        known = row["status"] == "known"
        if row["status"] not in ("known", "unknown") or not isinstance(row["reason"], str) or not row["reason"] or (row["reason"] == "known") != known:
            raise ValueError("Invalid label status/reason")
        gross = base._value(row["gross_markout"], known, kind + " gross")
        net = base._value(row["cost_threshold_markout"], known, kind + " net")
        if known:
            base._equal(net, gross - .002, kind + " frozen 20bp threshold")
            if endpoint >= _time(base.FOLD_BOUNDS[row["fold"]][1]):
                raise ValueError("Known label crosses its frozen fold end")
        row.update(decision_time=decision, endpoint_time=endpoint,
                   direction=context["direction"], horizon_hours=hours,
                   control_slot=context["control_slot"], matched_support=context["matched_support"],
                   gross_markout=gross, cost_threshold_markout=net)
        row.update({col: context[col] for col in CONTEXT_COLUMNS if col not in row})
        row["policy_cost_threshold_markout"] = (0. if context[GATE] == "abstain"
                                                 else net if context[GATE] == "accepted" else np.nan)
        row["policy_known"] = bool(np.isfinite(row["policy_cost_threshold_markout"]))
        row["policy_reason"] = ("observed_abstention" if context[GATE] == "abstain" else
                                "unknown_gate" if context[GATE] == "unknown" else
                                "known_accepted_label" if known else "unknown_accepted_label")
        result.append(row)
    if seen != {(event, hours) for event in contexts for hours in base.HORIZONS_HOURS}:
        raise ValueError("Incomplete original identity/horizon grid")
    return pd.DataFrame(result).sort_values(["event_id", "horizon_hours"]).reset_index(drop=True)


def _validate_pairs(cases, controls, original):
    """Recompute all individual controls; aggregate equality alone is insufficient."""
    _, original_pairs = base._validate(cases, original, base.FOLD_BOUNDS)
    rebuilt = rebuild_pairs(cases, controls)
    _, actual_pairs = base._validate(cases, rebuilt, base.FOLD_BOUNDS)
    for key, expected in original_pairs.items():
        actual = actual_pairs[key]
        for field in base._PAIR_COLUMNS:
            left, right = expected[field], actual[field]
            if pd.isna(left) and pd.isna(right):
                continue
            if field.endswith("markout"):
                base._equal(left, right, "individual-control reconstruction " + field)
            elif left != right:
                raise ValueError("Original pair differs from individual controls: " + field)
    if "control_event_ids" in original:
        expected_ids = rebuilt.set_index(["event_id", "horizon_hours"]).control_event_ids
        for row in original.itertuples():
            try:
                ids = json.loads(row.control_event_ids)
                wanted = json.loads(expected_ids.loc[(row.event_id, row.horizon_hours)])
            except (ValueError, TypeError) as error:
                raise ValueError("Malformed original control IDs") from error
            if ids != wanted:
                raise ValueError("Original pair control IDs changed")
    return rebuilt


def _description(values):
    """Fraction-unit descriptive statistics; no outlier deletion or inference."""
    values = np.asarray(values, dtype=float)
    known = values[np.isfinite(values)]
    output = base._describe(values)
    output.update(sum=float(math.fsum(known)) if len(known) else None,
                  n_positive=int((known > 0).sum()), n_negative=int((known < 0).sum()),
                  n_zero=int((known == 0).sum()),
                  positive_rate=float((known > 0).mean()) if len(known) else None,
                  positive_sum=float(math.fsum(known[known > 0])),
                  negative_sum=float(math.fsum(known[known < 0])), q05=None, q95=None,
                  iqr_outlier_count=0, iqr_outliers_removed=0)
    if len(known):
        output["q05"], output["q95"] = map(float, np.quantile(known, [.05, .95], method="linear"))
        lo, hi = output["q25"] - 1.5*output["iqr"], output["q75"] + 1.5*output["iqr"]
        output["iqr_outlier_count"] = int(((known < lo) | (known > hi)).sum())
    return output


def _holm(pvalues):
    """Fixed four-hypothesis Holm family; unavailable p keeps the family closed."""
    ordered = sorted(pvalues, key=lambda key: (pvalues[key] is None,
                                               1. if pvalues[key] is None else pvalues[key], key))
    result, previous = {}, 0.
    for rank, key in enumerate(ordered):
        value = pvalues[key]
        if value is None:
            result[key] = None
        else:
            previous = max(previous, min(1., (len(ordered)-rank)*value))
            result[key] = previous
    return result


def analyze(case_labels, control_labels, paired_labels, case_context, control_context):
    """Return (tables, summary); validate frozen identities before calculations.

    Tables: case_ledger (1004), control_ledger (2976), mother_ledger (1004),
    descriptives (all/fold/month by population/gate/horizon/metric), and
    primary_monthly (96 rows = four series x 24 original mother-month blocks).
    Summary: contracts, original populations, four-horizon descriptions,
    exactly four primary inferences, continuation decision and explicit limits.
    No file I/O and no source data mutation; callers pin saved inputs externally.
    """
    case_ctx, control_ctx = _contexts(case_context, "case"), _contexts(control_context, "control")
    if set(case_ctx) & set(control_ctx):
        raise ValueError("Case/control IDs overlap")
    control_times = [row["decision_time"] for row in control_ctx.values()]
    case_times = {row["decision_time"] for row in case_ctx.values()}
    if len(set(control_times)) != len(control_times) or set(control_times) & case_times:
        raise ValueError("Original own control clocks must be unique and not case clocks")
    for row in control_ctx.values():
        mother = case_ctx.get(row["mother_id"])
        if mother is None or not mother["matched_support"]:
            raise ValueError("Control refers to unknown/unsupported mother")
        if any(row[col] != mother[col] for col in ("fold", "direction", "mother_month")):
            raise ValueError("Control mother matching changed")
    cases = _labels(case_labels, case_ctx, "case")
    controls = _labels(control_labels, control_ctx, "control")
    pairs = _validate_pairs(cases, controls, paired_labels)
    if not all(row["matched_support"] for row in case_ctx.values() if row[GATE] == "accepted"):
        raise ValueError("All 100 frozen accepted mothers require their original control triples")
    own_controls = {(mother, hours): group for (mother, hours), group in
                    controls.groupby(["mother_id", "horizon_hours"], sort=False)}
    pair_rows = []
    for row, pair in zip(cases.to_dict("records"), pairs.to_dict("records")):
        group = own_controls.get((row["event_id"], row["horizon_hours"]), controls.iloc[:0])
        values = group.policy_cost_threshold_markout.to_numpy(dtype=float)
        complete = len(values) == 3 and bool(np.isfinite(values).all())
        control_policy = float(values.mean()) if complete else np.nan
        policy = row["policy_cost_threshold_markout"]
        accepted = row[GATE] == "accepted"
        pair.update({col: row[col] for col in PAIR_CONTEXT_COLUMNS})
        pair.update(control_policy_known_count=int(np.isfinite(values).sum()),
                    control_policy_complete=complete, control_policy_mean=control_policy,
                    case_policy=policy, case_policy_reason=row["policy_reason"],
                    accepted_cost=row["cost_threshold_markout"] if accepted else np.nan,
                    accepted_excess=pair["cost_threshold_excess_markout"] if accepted else np.nan,
                    policy_excess=policy-control_policy,
                    policy_delta=policy-row["cost_threshold_markout"],
                    control_gate_accepted=int(group[GATE].eq("accepted").sum()),
                    control_gate_abstain=int(group[GATE].eq("abstain").sum()),
                    control_gate_unknown=int(group[GATE].eq("unknown").sum()))
        pair_rows.append(pair)
    mothers = pd.DataFrame(pair_rows)
    rng = np.random.Generator(np.random.PCG64(base.SEED))
    indices = rng.integers(0, 24, size=(base.DRAWS, 24), dtype=np.int64)
    signs = 2*rng.integers(0, 2, size=(base.DRAWS, 24), dtype=np.int64)-1
    summary = dict(contract=dict(exploratory=True, independent_validation=False, executable_pnl=False,
        horizons_hours=list(base.HORIZONS_HOURS), primary_horizon_hours=4, cost_threshold=.002,
        primary_family=list(PRIMARY_SERIES), multiple_testing="Holm within four prespecified 4h series only",
        many_prior_experiments_adjusted=False, outliers="all retained", original_mothers=251,
        original_controls=744, seed=base.SEED, draws=base.DRAWS, clusters=list(base.MONTHS),
        month_indices_sha256=hashlib.sha256(indices.astype("<i8").tobytes()).hexdigest(),
        month_signs_sha256=hashlib.sha256(signs.astype("<i8").tobytes()).hexdigest(),
        interval="95% percentile, linear interpolation", tail="greater monthly sums; plus-one correction",
        accepted_comparator="all three original own-clock controls, ungated",
        policy_comparator="each original control gate at its own decision time",
        unknown_gate="missing, not abstention", known_abstention="zero even if future label unknown",
        wave_length=20, smooth_length=5, older_lag_warmup=100,
        wave_gate="direction*(wave[T-1h]-wave[T-2h])>0",
        wave_prior_available_at="T; one hour before own E",
        numpy_version=np.__version__, pandas_version=pd.__version__),
        population={kind: dict(counts) for kind, counts in COUNTS.items()},
        horizons={}, primary={}, decision={})
    descriptive_rows = []
    specifications = (("case", cases, {"gross": "gross_markout", "net": "cost_threshold_markout", "policy_net": "policy_cost_threshold_markout"}),
                      ("control", controls, {"gross": "gross_markout", "net": "cost_threshold_markout", "policy_net": "policy_cost_threshold_markout"}),
                      ("mother", mothers, {"original_excess": "cost_threshold_excess_markout",
                       "case_policy": "case_policy", "control_policy": "control_policy_mean",
                       "policy_excess": "policy_excess", "policy_delta": "policy_delta"}))
    for hours in base.HORIZONS_HOURS:
        entry = dict(role="primary" if hours == 4 else "descriptive", groups={})
        for population, ledger, metrics in specifications:
            entry["groups"][population] = {}
            horizon = ledger.loc[ledger.horizon_hours.eq(hours)]
            for state in ("all", *STATES):
                subset = horizon if state == "all" else horizon.loc[horizon[GATE].eq(state)]
                entry["groups"][population][state] = {metric: _description(subset[column]) for metric, column in metrics.items()}
                for dimension, keys in (("all", ["all"]), ("fold", list(base.FOLD_BOUNDS)), ("month", list(base.MONTHS))):
                    for key in keys:
                        part = subset if dimension == "all" else subset.loc[subset["fold" if dimension == "fold" else "mother_month"].eq(key)]
                        for metric, column in metrics.items():
                            descriptive_rows.append(dict(population=population, horizon_hours=hours, group=state,
                                dimension=dimension, key=key, metric=metric, **_description(part[column])))
        summary["horizons"][str(hours)] = entry
    primary = mothers.loc[mothers.horizon_hours.eq(4)]
    monthly_rows = []
    for name in PRIMARY_SERIES:
        # Conditional groups use their actual100 mothers, not151 artificial missing rows.
        subset = primary.loc[primary[GATE].eq("accepted")] if name.startswith("accepted_") else primary
        values = subset[name].to_numpy(dtype=float)
        monthly = base._monthly(values, subset.mother_month.tolist(), indices, signs)
        summary["primary"][name] = dict(**_description(values), monthly_cluster=monthly)
        monthly_rows += [dict(series=name, **row) for row in monthly["months"]]
    adjusted = _holm({name: row["monthly_cluster"]["one_sided_p"] for name, row in summary["primary"].items()})
    for name in PRIMARY_SERIES:
        summary["primary"][name]["holm_p"] = adjusted[name]
    positive_halves = all(
        (lambda d: d["mean"] is not None and d["mean"] > 0)(
            _description(primary.loc[primary.fold.eq(fold) & primary[GATE].eq("accepted"), "accepted_cost"]))
        for fold in base.FOLD_BOUNDS)
    evidence = all(row["mean"] is not None and row["mean"] > 0 and
        row["monthly_cluster"]["ci95"] is not None and row["monthly_cluster"]["ci95"][0] > 0 and
        row["holm_p"] is not None and row["holm_p"] < .01 for row in summary["primary"].values())
    summary["decision"] = dict(all_four_primary_evidence_gates_passed=evidence,
        four_half_accepted_means_positive=positive_halves, exploratory_continue=evidence and positive_halves,
        status="exploratory_support_requires_execution_validation" if evidence and positive_halves else "not_supported",
        profitability_accepted=False, economic_acceptance=False, production_eligible=False)
    # Allow missing ledger cells, but never serialize nonfinite aggregate numbers.
    json.dumps(summary, allow_nan=False)
    return dict(case_ledger=cases, control_ledger=controls, mother_ledger=mothers,
                descriptives=pd.DataFrame(descriptive_rows), primary_monthly=pd.DataFrame(monthly_rows)), summary
