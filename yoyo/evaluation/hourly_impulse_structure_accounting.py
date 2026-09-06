"""Pure cached-exit accounting for an entry-known Market Break gate.

``evaluate_cached`` never reads files, prices, sources or configuration and
never simulates an exit. Its caller must first pin the original V18 episodes
and freeze every own case/control context before accessing cached outcomes.
The fixed policy is the original K1 entry/stop, 72h horizon and 0.002 cost;
only entry participation changes. No outcome can determine the supplied gate.

Inputs are full case/control EPISODE frames, plus one context row per event.
Context requires event_id, population (case/control), signal_time (K1 open),
decision_time (K1 close), direction, structure_gate_state, structure_state,
structure_known, structure_available_at and structure_signal_close. Additional
structure_* fields are copied without inventing formulae or rolling windows.
Any other shared input fields are checked, never used to overwrite episodes.
All context source windows/availability must already have been validated by
the causal feature builder; this module cannot prove the source OHLC itself.

Known abstention is zero/no fee. Unknown context or an accepted unknown exit
remains unknown, with a conservative mother+72h serial reservation. This is
not a claim that an unknown-context opportunity actually opened a position.
Completed-trade gross = original net + 0.002, including weighted partial exits;
unknown remainders are never completed from a known realised partial leg.

Pandas 2.3.3: reject null keys before one-to-one joins (null matches null),
and compare accepted old fields exactly, not only economic aggregates:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
Month-block inference is reused from the frozen pure bookkeeping helpers;
it is exploratory reused-development evidence, not independent confirmation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_k2_research import (
    describe, matched_episodes, single_pending_ledger,
)
from yoyo.evaluation.hourly_impulse_management_research import paired_effects


COST = 0.002
STATES = ("accepted", "abstain", "unknown")
FOLDS = (
    ("2023H1", "2023-01-01", "2023-07-01"),
    ("2023H2", "2023-07-01", "2024-01-01"),
    ("2024H1", "2024-01-01", "2024-07-01"),
    ("2024H2", "2024-07-01", "2025-01-01"),
)
IDENTITY = ("signal_time", "decision_time", "direction")
REQUIRED = (
    "event_id", *IDENTITY, "signal_close", "fold", "mother_signal_time", "mother_decision_time",
    "mother_deadline", "terminal_time", "occupied_until", "entry_time", "exit_time",
    "status", "episode_status", "episode_net_return", "observed", "executed",
    "completed_trade",
)


def _times(values):
    if pd.api.types.is_numeric_dtype(values.dtype) and len(values):
        raise ValueError("Numeric timestamps require explicit upstream normalization")
    if any(pd.Timestamp(value).tzinfo is None for value in values.loc[values.notna()]):
        raise ValueError("Timestamps must carry an explicit timezone")
    return pd.to_datetime(values, utc=True, errors="raise", format="mixed")


def _unique(frame, required):
    if not frame.columns.is_unique or not set(required).issubset(frame):
        raise ValueError("Required unique columns missing")
    if frame.event_id.isna().any() or not frame.event_id.is_unique:
        raise ValueError("Every event must have one non-null identity")


def _validate_episode(frame):
    _unique(frame, REQUIRED)
    if not frame.index.is_unique:
        raise ValueError("Episode index must be unique for serial bookkeeping")
    if any(c.startswith("structure_") for c in frame):
        raise ValueError("Old episodes must not contain a candidate structure gate")
    for name in ("observed", "executed", "completed_trade"):
        if not frame[name].map(lambda v: isinstance(v, (bool, np.bool_))).all():
            raise ValueError("Episode flags must be explicit booleans")
    signal, decision, deadline = (_times(frame[c]) for c in
        ("signal_time", "decision_time", "mother_deadline"))
    if signal.isna().any() or decision.isna().any() or deadline.isna().any():
        raise ValueError("Original request clock cannot be unknown")
    if not (decision.eq(decision.dt.floor("h")) &
            decision.eq(signal+pd.Timedelta(hours=1)) &
            decision.eq(_times(frame.mother_decision_time)) &
            signal.eq(_times(frame.mother_signal_time)) &
            deadline.eq(decision+pd.Timedelta(hours=72))).all():
        raise ValueError("Only original direct hourly requests with fixed72h are valid")
    if not frame.direction.isin((-1, 1)).all() or frame.direction.map(
            lambda v: isinstance(v, (bool, np.bool_))).any():
        raise ValueError("Direction must be the own real +/-1 direction")
    closes = pd.to_numeric(frame.signal_close, errors="raise")
    if not (np.isfinite(closes) & closes.gt(0)).all():
        raise ValueError("Own signal close must be finite and positive")
    if not frame.fold.isin([f[0] for f in FOLDS]).all():
        raise ValueError("Only the four frozen2023--2024 folds are permitted")
    for fold, start, end in FOLDS:
        d = decision.loc[frame.fold.eq(fold)]
        if not (d.ge(pd.Timestamp(start, tz="UTC")) &
                d.lt(pd.Timestamp(end, tz="UTC")-pd.Timedelta(hours=72))).all():
            raise ValueError("Request crosses a frozen fold/holding embargo")
    values = pd.to_numeric(frame.episode_net_return, errors="raise")
    if np.isinf(values.to_numpy(dtype=float)).any() or not frame.observed.eq(np.isfinite(values)).all():
        raise ValueError("Observed must exactly identify finite whole-episode returns")
    if (frame.completed_trade & ~(frame.executed & frame.observed)).any():
        raise ValueError("Completed trades must be executed and observed")
    nontrade = frame.observed & ~frame.completed_trade
    if (nontrade & (frame.executed | values.ne(0))).any():
        raise ValueError("Known non-trading opportunities must be zero")
    occupied = _times(frame.occupied_until)
    terminal = _times(frame.terminal_time)
    if terminal.isna().any() or not (terminal.ge(decision) & terminal.le(deadline)).all():
        raise ValueError("Original terminal decision outside the fixed horizon")
    if occupied.isna().any() or not (occupied.ge(decision) & occupied.le(deadline)).all():
        raise ValueError("Serial occupancy outside the fixed mother horizon")
    if not occupied.loc[~frame.observed].eq(deadline.loc[~frame.observed]).all():
        raise ValueError("Unknown old episodes must reserve the full72h horizon")
    complete = frame.completed_trade
    entry, exit_ = _times(frame.entry_time), _times(frame.exit_time)
    if not (entry.loc[complete].eq(decision.loc[complete]) &
            exit_.loc[complete].ge(entry.loc[complete]) &
            exit_.loc[complete].le(deadline.loc[complete]) &
            occupied.loc[complete].eq(exit_.loc[complete])).all():
        raise ValueError("Completed episode entry/exit clock inconsistent")


def _check_context(episodes, context, population):
    part = context.loc[context.population.eq(population)].set_index("event_id")
    if set(part.index) != set(episodes.event_id):
        raise ValueError("Full own-context population must remain")
    own = part.loc[episodes.event_id].reset_index(drop=True)
    old = episodes.reset_index(drop=True)
    shared = [c for c in own if c in old and not c.startswith("structure_")]
    for col in shared:
        a, b = old[col], own[col]
        time = col.endswith(("_time", "_deadline", "_until", "_available", "_available_at", "_bar_open"))
        if time:
            a, b = _times(a), _times(b)
        pd.testing.assert_series_equal(a, b, check_dtype=False, check_names=False,
            check_exact=time, rtol=1e-12, atol=1e-12)
    known = own.structure_known
    if not known.map(lambda v: isinstance(v, (bool, np.bool_))).all():
        raise ValueError("Structure known must be an explicit boolean")
    if (not own.loc[known, "structure_state"].isin((-1, 1)).all() or
            own.loc[known, "structure_state"].map(lambda v: isinstance(v, (bool, np.bool_))).any() or
            own.loc[~known, "structure_state"].notna().any()):
        raise ValueError("Known structure side must be +/-1; unknown must not become zero")
    expected = pd.Series("unknown", index=own.index)
    expected.loc[known] = np.where(own.loc[known, "structure_state"].astype(float).eq(own.loc[known, "direction"]),
        "accepted", "abstain")
    if not own.structure_gate_state.eq(expected).all():
        raise ValueError("Own structure direction must determine the supplied gate")
    available = _times(own.structure_available_at)
    if available.gt(_times(own.decision_time)).any() or not available.loc[known].eq(_times(own.decision_time).loc[known]).all():
        raise ValueError("Known structure must be available at own K1 close, never later")
    if "signal_close" not in episodes:
        raise ValueError("Own saved signal_close required for structure source parity")
    np.testing.assert_allclose(own.loc[known, "structure_signal_close"].astype(float),
        old.loc[known, "signal_close"].astype(float), rtol=1e-12, atol=1e-12, equal_nan=False)
    return own


def _baseline(frame):
    result = frame.copy(deep=True)
    fee = pd.Series(np.where(result.completed_trade, COST,
        np.where(result.observed, 0., np.nan)), index=result.index)
    gross = result.episode_net_return+fee
    for col, value in (("policy_fee_fraction", fee), ("episode_gross_return", gross)):
        if col in result:
            np.testing.assert_allclose(result[col], value, rtol=0, atol=1e-12, equal_nan=True)
        else:
            result[col] = value
    if "gross_return" in result:
        complete = result.completed_trade
        np.testing.assert_allclose(result.loc[complete, "gross_return"], gross.loc[complete],
            rtol=0, atol=1e-12, equal_nan=False)
    return result


def _candidate(old, own):
    result = old.copy(deep=True)
    for col in own:
        if col.startswith("structure_"):
            result[col] = own[col].to_numpy()
    for state in ("abstain", "unknown"):
        mask = result.structure_gate_state.eq(state)
        for name in ("status", "episode_status"):
            result.loc[mask, name] = "structure_"+state
        result.loc[mask, ["executed", "completed_trade"]] = False
        result.loc[mask, "observed"] = state == "abstain"
        result.loc[mask, ["episode_net_return", "episode_gross_return", "policy_fee_fraction"]] = (
            0. if state == "abstain" else np.nan)
        result.loc[mask, ["entry_time", "exit_time"]] = pd.NaT
        result.loc[mask, "terminal_time"] = result.loc[mask, "mother_decision_time"]
        result.loc[mask, "occupied_until"] = result.loc[mask,
            "mother_decision_time" if state == "abstain" else "mother_deadline"]
    accepted = result.structure_gate_state.eq("accepted")
    pd.testing.assert_frame_equal(old.loc[accepted], result.loc[accepted, old.columns],
        check_dtype=False, check_exact=True)
    return result


def _serial_accounting(serial):
    result = serial.copy(deep=True)
    skipped = ~result.portfolio_selected
    result.loc[skipped, ["executed", "completed_trade"]] = False
    result.loc[skipped, "observed"] = True
    result.loc[skipped, ["episode_net_return", "episode_gross_return", "policy_fee_fraction"]] = 0.
    return result


def _metrics(frame):
    complete = frame.completed_trade & frame.observed
    returns = frame.loc[complete, "episode_net_return"]
    gross = frame.loc[complete, "episode_gross_return"]
    positive, negative = returns.loc[returns.gt(0)].sum(), -returns.loc[returns.lt(0)].sum()
    pf = positive/negative if negative else (np.inf if positive else np.nan)
    return {"opportunities": len(frame), "known_opportunities": int(frame.observed.sum()),
        "unknown_opportunities": int((~frame.observed).sum()), "executed": int(frame.executed.sum()),
        "completed_trades": int(complete.sum()), "nontrading_known": int((frame.observed & ~frame.executed).sum()),
        "mean_gross_bp": gross.mean()*1e4, "mean_net_bp": returns.mean()*1e4,
        "profit_factor": pf, "win_rate": returns.gt(0).mean() if len(returns) else np.nan,
        "all_opportunity_mean_net_bp": frame.episode_net_return.mean()*1e4,
        "all_opportunity_sum_net_event_bp": frame.episode_net_return.sum(min_count=1)*1e4,
        "all_opportunity_mean_gross_bp": frame.episode_gross_return.mean()*1e4,
        "net_mean_denominator": int(complete.sum()),
        "opportunity_mean_denominator": int(frame.observed.sum())}


def _paired(before, after):
    keys = ["event_id", "mother_decision_time", "fold"]
    result = before[keys+["episode_net_return"]].rename(columns={"episode_net_return": "before"}).merge(
        after[keys+["episode_net_return"]].rename(columns={"episode_net_return": "after"}),
        on=keys, how="inner", validate="one_to_one", sort=False)
    if len(result) != len(before) or len(result) != len(after):
        raise ValueError("Paired opportunity identities changed")
    result["difference"] = result.after-result.before
    info = {**describe(result.difference, result.mother_decision_time), "total_pairs": len(result),
        "unknown_pairs": int(result.difference.isna().sum()),
        "improved": int(result.difference.gt(1e-12).sum()),
        "worsened": int(result.difference.lt(-1e-12).sum()),
        "unchanged": int(result.difference.abs().le(1e-12).sum())}
    return result, info


def _mechanics(before, after, population):
    result, _ = _paired(before, after)
    gate = after.set_index("event_id").loc[result.event_id]
    for col in gate:
        if col.startswith("structure_") or col == "direction":
            result[col] = gate[col].to_numpy()
    if "structure_gate_reason" not in result:
        result["structure_gate_reason"] = result.get("structure_reason", result.structure_gate_state)
    result["population"] = population
    result["known_pair"] = result.difference.notna()
    abstain = result.structure_gate_state.eq("abstain")
    result["avoided_net_loser"] = abstain & result.before.lt(0)
    result["missed_net_winner"] = abstain & result.before.gt(0)
    result["avoided_loss_event_bp"] = (-result.before*1e4).where(result.avoided_net_loser, 0.)
    result["missed_winner_event_bp"] = (result.before*1e4).where(result.missed_net_winner, 0.)
    groups = []
    for (state, reason), part in result.groupby(["structure_gate_state", "structure_gate_reason"], dropna=False, sort=True):
        known = part.known_pair
        groups.append({"population": population, "structure_gate_state": state,
            "structure_gate_reason": reason, "opportunities": len(part), "known_pairs": int(known.sum()),
            "unknown_pairs": int((~known).sum()), "old_mean_net_bp": part.loc[known, "before"].mean()*1e4,
            "new_mean_net_bp": part.loc[known, "after"].mean()*1e4,
            "mean_delta_bp": part.difference.mean()*1e4,
            "sum_delta_event_bp": part.difference.sum(min_count=1)*1e4,
            "avoided_net_losers": int(part.avoided_net_loser.sum()),
            "missed_net_winners": int(part.missed_net_winner.sum()),
            "avoided_loss_event_bp": part.avoided_loss_event_bp.sum(),
            "missed_winner_event_bp": part.missed_winner_event_bp.sum()})
    return result, pd.DataFrame(groups)


def evaluate_cached(cases, controls, context):
    """Return ``(tables, summary)`` without mutating inputs or using file I/O.

    Tables: baseline/candidate_{case,control}_episodes; both arms' matched,
    case_serial and control_serial; case_delta, excess_delta, serial_delta,
    control_delta, control_serial_delta, matched_control_delta; case/control
    mechanics, mechanism_groups and metrics (all/four zero-filled folds, both
    populations, independent/serial scopes). All original identities remain.
    Synthetic populations may be smaller; caller enforces frozen251/462/154/97.
    ``serial_*`` ledgers retain underlying episodes and explicit selection;
    metrics/deltas turn skipped opportunities into zero, never a fake trade.
    """
    for frame in (cases, controls):
        _validate_episode(frame)
    _unique(context, ("event_id", "population", *IDENTITY, "structure_gate_state",
        "structure_state", "structure_known", "structure_available_at", "structure_signal_close"))
    if not context.population.isin(("case", "control")).all() or not context.structure_gate_state.isin(STATES).all():
        raise ValueError("Every own context needs an explicit population and tri-state gate")
    if not context.direction.isin((-1, 1)).all() or context.direction.map(
            lambda v: isinstance(v, (bool, np.bool_))).any():
        raise ValueError("Context must preserve a real +/-1 direction")
    if set(cases.event_id) & set(controls.event_id):
        raise ValueError("Case/control identities must be globally disjoint")
    if len(context) != len(cases)+len(controls):
        raise ValueError("All original own contexts must remain")
    if "parent_event_id" not in controls or controls.parent_event_id.isna().any():
        raise ValueError("Every control must retain its original case parent")
    parents = controls.groupby("parent_event_id").size()
    if not parents.eq(3).all() or not set(parents.index).issubset(set(cases.event_id)):
        raise ValueError("Frozen controls must be exactly three or zero per case")
    case_index = cases.set_index("event_id")
    if not (controls.direction.eq(controls.parent_event_id.map(case_index.direction)) &
            controls.fold.eq(controls.parent_event_id.map(case_index.fold))).all():
        raise ValueError("Original controls must retain the parent's direction and fold")
    if _times(controls.decision_time).duplicated().any():
        raise ValueError("Own control decision times must not be reused")
    own = {label: _check_context(frame, context, label) for label, frame in (("case", cases), ("control", controls))}
    baseline = {label: _baseline(frame) for label, frame in (("case", cases), ("control", controls))}
    candidate = {label: _candidate(baseline[label], own[label]) for label in baseline}
    tables, arms, metric_rows = {}, {}, []
    for arm, frames in (("baseline", baseline), ("candidate", candidate)):
        match, info = matched_episodes(frames["case"], frames["control"])
        tables[arm+"_matched"] = match
        arms[arm] = {"matching": info, "case": {}, "control": {}}
        for population, episode in frames.items():
            serial = single_pending_ledger(episode)
            tables[f"{arm}_{population}_episodes"] = episode
            tables[f"{arm}_{population}_serial"] = serial
            for scope, ledger in (("independent", episode), ("serial", _serial_accounting(serial))):
                measures = {}
                for fold in ("all", *[f[0] for f in FOLDS]):
                    part = ledger if fold == "all" else ledger.loc[ledger.fold.eq(fold)]
                    m = _metrics(part)
                    m.update(arm=arm, population=population, scope=scope, fold=fold)
                    pm = match if fold == "all" else match.loc[match.fold.eq(fold)]
                    m["matched_excess_bp"] = pm.excess.mean()*1e4 if population == "case" and scope == "independent" else np.nan
                    m["matched_known_pairs"] = int(pm.excess.notna().sum()) if population == "case" and scope == "independent" else 0
                    if scope == "serial":
                        source = serial if fold == "all" else serial.loc[serial.fold.eq(fold)]
                        m["handled_opportunities"] = int(source.portfolio_selected.sum())
                    metric_rows.append(m)
                    measures[fold] = m
                arms[arm][population][scope] = measures
    deltas, effects = paired_effects(baseline["case"], candidate["case"],
        tables["baseline_matched"], tables["candidate_matched"],
        tables["baseline_case_serial"], tables["candidate_case_serial"])
    tables.update(deltas)
    for name, before, after in (
        ("control_delta", baseline["control"], candidate["control"]),
        ("control_serial_delta", _serial_accounting(tables["baseline_control_serial"]),
         _serial_accounting(tables["candidate_control_serial"]))):
        tables[name], effects[name] = _paired(before, after)
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
        table, group = _mechanics(baseline[population], candidate[population], population)
        tables[population+"_mechanics"] = table
        groups.append(group)
    tables["mechanism_groups"] = pd.concat(groups, ignore_index=True)
    tables["metrics"] = pd.DataFrame(metric_rows)
    return tables, {"population": {"cases": len(cases), "controls": len(controls),
        "matched_cases": len(parents), "unmatched_cases": len(cases)-len(parents)},
        "gate_counts": {label: {state: int(own[label].structure_gate_state.eq(state).sum()) for state in STATES} for label in own},
        "arms": arms, "effects": effects,
        "denominators": {"case_D": len(cases), "control_D": len(controls),
            "fixed_triplets": len(parents), "I_rows_including_unmatched_unknown": len(cases)},
        "accounting": {"cost_fraction": COST, "cached_exits_unchanged": True,
            "single_position_recomputed_each_arm": True, "control_serial_diagnostic_only": True,
            "known_abstention": "zero_no_trade_no_fee", "unknown": "NaN_full72h_serial_reservation",
            "gross_definition": "completed_episode_net_plus_original20bp",
            "all_opportunity_mean": "finite_episode_returns_including_known_abstention_zero; unknown_count_explicit",
            "raw_replay": False, "context_source_independently_verified": False},
        "interpretation": "Exploratory cached fixed-exit gate accounting on reused development; not independent validation or a profitability acceptance."}
