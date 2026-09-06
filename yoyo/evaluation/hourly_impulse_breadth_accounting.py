"""Pure V21 cached-exit accounting for an independently frozen breadth gate.

The caller pins original V18 case/control EPISODES and all own entry contexts
before accessing outcomes. This module performs no I/O, market calculation,
exit replay, threshold selection, or structure-gate translation. The sole
change is participation; accepted old episode columns remain exactly equal.

Context contains all original request columns plus breadth_score (the frozen
four-asset mean of normalized ChartPrime rank50 scores), breadth_known,
breadth_gate_state, breadth_cutoff and breadth_available_at. All cutoffs equal
own signal_time (K1 OPEN), one hour before decision_time. Known scores are real
finite [-1,1] and become available at that cutoff; unknown scores are missing
and availability is NaT. Known direction*score>0 admits, including arbitrarily
small positive values; known zero and opposite signs abstain. Additional
breadth_* diagnostics are copied verbatim, never used to overwrite old fields.
The causal feature/verifier, NOT accounting, certifies four-asset membership,
rank windows, missingness, and the aggregate mean from underlying sources.

Generic V20 pure bookkeeping is reused without fabricating structure fields.
Known abstention is zero/no trade/no fee; unknown reserves mother+72h in serial
diagnostics and remains NaN, including an accepted unknown remainder. Costs
remain original20bp per completed whole episode, including weighted exits.

Locked pandas2.3.3: explicit UTC conversion before shared-clock comparison;
null IDs rejected before one-to-one joins; accepted old fields check_exact.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
"""
from __future__ import annotations

from numbers import Real

import numpy as np
import pandas as pd

from yoyo.evaluation import hourly_impulse_structure_accounting as bookkeeping


COST, STATES, FOLDS = bookkeeping.COST, bookkeeping.STATES, bookkeeping.FOLDS
IDENTITY = bookkeeping.IDENTITY
TIME_SUFFIXES = ("_time", "_deadline", "_until", "_available", "_available_at", "_bar_open", "_at")


def _check_context(episodes, context, population):
    part = context.loc[context.population.eq(population)].set_index("event_id")
    if set(part.index) != set(episodes.event_id):
        raise ValueError("Full own breadth-context population must remain")
    own = part.loc[episodes.event_id].reset_index(drop=True)
    old = episodes.reset_index(drop=True)
    for column in [c for c in own if c in old and not c.startswith("breadth_")]:
        a, b = old[column], own[column]
        time = column.endswith(TIME_SUFFIXES)
        if time:
            a, b = bookkeeping._times(a), bookkeeping._times(b)
        pd.testing.assert_series_equal(a, b, check_dtype=False, check_names=False,
            check_exact=time, rtol=1e-12, atol=1e-12)
    known = own.breadth_known
    if not known.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("Breadth known must be an explicit boolean")
    known = known.astype(bool)
    if not own.breadth_score.map(lambda value: pd.isna(value) or
            isinstance(value, Real) and not isinstance(value, (bool, np.bool_))).all():
        raise ValueError("Breadth scores must be real numbers, never strings/bools")
    score = pd.to_numeric(own.breadth_score, errors="raise")
    if (not (np.isfinite(score.loc[known].to_numpy(dtype=float)) &
             score.loc[known].between(-1., 1.).to_numpy(dtype=bool)).all()
            or score.loc[~known].notna().any()):
        raise ValueError("Known breadth score must be finite [-1,1]; unknown must be NaN")
    expected = pd.Series("unknown", index=own.index)
    expected.loc[known] = np.where(
        score.loc[known].astype(float).mul(own.loc[known, "direction"]).gt(0), "accepted", "abstain")
    if not own.breadth_gate_state.eq(expected).all():
        raise ValueError("Own direction times breadth score must determine the gate; zero abstains")
    signal = bookkeeping._times(own.signal_time)
    cutoff = bookkeeping._times(own.breadth_cutoff)
    available = bookkeeping._times(own.breadth_available_at)
    if cutoff.isna().any() or not cutoff.eq(signal).all():
        raise ValueError("Every breadth cutoff must equal own K1 open, one hour before entry")
    if (not available.loc[known].eq(signal.loc[known]).all()
            or available.loc[~known].notna().any()):
        raise ValueError("Known breadth availability must equal K1 open; unknown must be NaT")
    return own


def _candidate(old, own):
    result = old.copy(deep=True)
    for column in own:
        if column.startswith("breadth_"):
            result[column] = own[column].to_numpy()
    for state in ("abstain", "unknown"):
        mask = result.breadth_gate_state.eq(state)
        result.loc[mask, ["status", "episode_status"]] = "breadth_"+state
        result.loc[mask, ["executed", "completed_trade"]] = False
        result.loc[mask, "observed"] = state == "abstain"
        result.loc[mask, ["episode_net_return", "episode_gross_return", "policy_fee_fraction"]] = (
            0. if state == "abstain" else np.nan)
        result.loc[mask, ["entry_time", "exit_time"]] = pd.NaT
        result.loc[mask, "terminal_time"] = result.loc[mask, "mother_decision_time"]
        result.loc[mask, "occupied_until"] = result.loc[mask,
            "mother_decision_time" if state == "abstain" else "mother_deadline"]
    accepted = result.breadth_gate_state.eq("accepted")
    pd.testing.assert_frame_equal(old.loc[accepted], result.loc[accepted, old.columns],
        check_dtype=False, check_exact=True)
    return result


def _mechanics(before, after, population):
    result, _ = bookkeeping._paired(before, after)
    gate = after.set_index("event_id").loc[result.event_id]
    for column in gate:
        if column.startswith("breadth_") or column == "direction":
            result[column] = gate[column].to_numpy()
    if "breadth_gate_reason" not in result:
        result["breadth_gate_reason"] = result.get("breadth_reason", result.breadth_gate_state)
    result["population"] = population
    result["known_pair"] = result.difference.notna()
    abstain = result.breadth_gate_state.eq("abstain")
    result["avoided_net_loser"] = abstain & result.before.lt(0)
    result["missed_net_winner"] = abstain & result.before.gt(0)
    result["avoided_loss_event_bp"] = (-result.before*1e4).where(result.avoided_net_loser, 0.)
    result["missed_winner_event_bp"] = (result.before*1e4).where(result.missed_net_winner, 0.)
    groups = []
    for (state, reason), part in result.groupby(["breadth_gate_state", "breadth_gate_reason"], dropna=False, sort=True):
        known = part.known_pair
        groups.append(dict(population=population, breadth_gate_state=state,
            breadth_gate_reason=reason, opportunities=len(part), known_pairs=int(known.sum()),
            unknown_pairs=int((~known).sum()), old_mean_net_bp=part.loc[known, "before"].mean()*1e4,
            new_mean_net_bp=part.loc[known, "after"].mean()*1e4,
            mean_delta_bp=part.difference.mean()*1e4,
            sum_delta_event_bp=part.difference.sum(min_count=1)*1e4,
            avoided_net_losers=int(part.avoided_net_loser.sum()),
            missed_net_winners=int(part.missed_net_winner.sum()),
            avoided_loss_event_bp=part.avoided_loss_event_bp.sum(),
            missed_winner_event_bp=part.missed_winner_event_bp.sum()))
    return result, pd.DataFrame(groups)


def evaluate_cached(cases, controls, context):
    """Return V20-shaped tables/summary, with genuine breadth diagnostics only.

    No fixed identity is dropped. Tiny synthetic populations are allowed; the
    runner pins all251 cases,462 controls,154 triples and97 unmatched cases.
    Independent policy means include known abstention zero and exclude explicit
    unknown. Completed-trade means have their own reported denominator. All
    serial masks are recomputed; skipped opportunities are zero, not trades.
    """
    for frame in (cases, controls):
        if any(c.startswith(("structure_", "breadth_")) for c in frame):
            raise ValueError("Original episodes cannot contain old structure/breadth gates")
        bookkeeping._validate_episode(frame)
    bookkeeping._unique(context, ("event_id", "population", *IDENTITY, "signal_close",
        "breadth_score", "breadth_known", "breadth_gate_state", "breadth_available_at", "breadth_cutoff"))
    if any(c.startswith("structure_") for c in context):
        raise ValueError("Do not stack the old structure gate with breadth")
    if not context.population.isin(("case", "control")).all() or not context.breadth_gate_state.isin(STATES).all():
        raise ValueError("Every own context needs an explicit population and breadth gate")
    if not context.direction.isin((-1, 1)).all() or context.direction.map(
            lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError("Own context direction must be real +/-1")
    if set(cases.event_id) & set(controls.event_id) or len(context) != len(cases)+len(controls):
        raise ValueError("All globally disjoint original case/control identities must remain")
    if "parent_event_id" not in controls or controls.parent_event_id.isna().any():
        raise ValueError("Every control must retain its original case parent")
    parents = controls.groupby("parent_event_id").size()
    if not parents.eq(3).all() or not set(parents.index).issubset(set(cases.event_id)):
        raise ValueError("Frozen controls must be exactly three or zero per case")
    indexed = cases.set_index("event_id")
    if not (controls.direction.eq(controls.parent_event_id.map(indexed.direction)) &
            controls.fold.eq(controls.parent_event_id.map(indexed.fold))).all():
        raise ValueError("Original controls must retain parent's direction and fold")
    if bookkeeping._times(controls.decision_time).duplicated().any():
        raise ValueError("Own control times must not be reused")
    own = {label: _check_context(frame, context, label) for label, frame in (("case", cases), ("control", controls))}
    baseline = {label: bookkeeping._baseline(frame) for label, frame in (("case", cases), ("control", controls))}
    candidate = {label: _candidate(baseline[label], own[label]) for label in baseline}
    tables, arms, metric_rows = {}, {}, []
    for arm, frames in (("baseline", baseline), ("candidate", candidate)):
        match, info = bookkeeping.matched_episodes(frames["case"], frames["control"])
        tables[arm+"_matched"] = match
        arms[arm] = {"matching": info, "case": {}, "control": {}}
        for population, episode in frames.items():
            serial = bookkeeping.single_pending_ledger(episode)
            tables[f"{arm}_{population}_episodes"] = episode
            tables[f"{arm}_{population}_serial"] = serial
            for scope, ledger in (("independent", episode), ("serial", bookkeeping._serial_accounting(serial))):
                measures = {}
                for fold in ("all", *[f[0] for f in FOLDS]):
                    part = ledger if fold == "all" else ledger.loc[ledger.fold.eq(fold)]
                    metrics = bookkeeping._metrics(part)
                    metrics.update(arm=arm, population=population, scope=scope, fold=fold)
                    paired = match if fold == "all" else match.loc[match.fold.eq(fold)]
                    metrics["matched_excess_bp"] = paired.excess.mean()*1e4 if population == "case" and scope == "independent" else np.nan
                    metrics["matched_known_pairs"] = int(paired.excess.notna().sum()) if population == "case" and scope == "independent" else 0
                    if scope == "serial":
                        source = serial if fold == "all" else serial.loc[serial.fold.eq(fold)]
                        metrics["handled_opportunities"] = int(source.portfolio_selected.sum())
                    metric_rows.append(metrics)
                    measures[fold] = metrics
                arms[arm][population][scope] = measures
    deltas, effects = bookkeeping.paired_effects(baseline["case"], candidate["case"],
        tables["baseline_matched"], tables["candidate_matched"],
        tables["baseline_case_serial"], tables["candidate_case_serial"])
    tables.update(deltas)
    for name, before, after in (("control_delta", baseline["control"], candidate["control"]),
        ("control_serial_delta", bookkeeping._serial_accounting(tables["baseline_control_serial"]),
         bookkeeping._serial_accounting(tables["candidate_control_serial"]))):
        tables[name], effects[name] = bookkeeping._paired(before, after)
    matched_control = tables["baseline_matched"][["event_id", "mother_decision_time", "fold", "control_mean_return"]].rename(
        columns={"control_mean_return": "before"}).merge(
        tables["candidate_matched"][["event_id", "control_mean_return"]].rename(columns={"control_mean_return": "after"}),
        on="event_id", validate="one_to_one", sort=False)
    matched_control["difference"] = matched_control.after-matched_control.before
    tables["matched_control_delta"] = matched_control
    aligned = {name: tables[name].set_index("event_id").reindex(cases.event_id).difference
        for name in ("case_delta", "excess_delta", "matched_control_delta")}
    np.testing.assert_allclose(aligned["excess_delta"], aligned["case_delta"]-aligned["matched_control_delta"],
        rtol=0, atol=1e-12, equal_nan=True)
    groups = []
    for population in ("case", "control"):
        mechanics, group = _mechanics(baseline[population], candidate[population], population)
        tables[population+"_mechanics"] = mechanics
        groups.append(group)
    tables["mechanism_groups"] = pd.concat(groups, ignore_index=True)
    tables["metrics"] = pd.DataFrame(metric_rows)
    return tables, dict(population=dict(cases=len(cases), controls=len(controls),
        matched_cases=len(parents), unmatched_cases=len(cases)-len(parents)),
        gate_counts={label: {state: int(own[label].breadth_gate_state.eq(state).sum()) for state in STATES} for label in own},
        arms=arms, effects=effects,
        denominators=dict(case_D=len(cases), control_D=len(controls), fixed_triplets=len(parents),
            I_rows_including_unmatched_unknown=len(cases)),
        accounting=dict(cost_fraction=COST, cached_exits_unchanged=True,
            single_position_recomputed_each_arm=True, control_serial_diagnostic_only=True,
            known_abstention="zero_no_trade_no_fee", unknown="NaN_full72h_serial_reservation",
            gross_definition="completed_episode_net_plus_original20bp",
            all_opportunity_mean="finite_episode_returns_including_known_abstention_zero; unknown_count_explicit",
            raw_replay=False, context_source_independently_verified=False),
        interpretation="Exploratory cached fixed-exit breadth-gate accounting on reused development; not independent validation or a profitability acceptance.")
